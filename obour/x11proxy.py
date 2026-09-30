"""Drag and drop and copy/paste of files for X11 apps on a host.

X11 apps (ssh -Y) are shown by this computer's X server (Xwayland on a Wayland
desktop). The file lists they get from drags and the clipboard hold paths of the
computer the files are on, which the other computer doesn't have. ssh connects
to this proxy's display instead of the real one; the proxy passes everything
through and rewrites only file lists (see share.py for the paths):

  into a remote app   the reply to GetProperty, with a property of type
                      text/uri-list (or gnome-copied-files) set by a local source
  out of a remote app ChangeProperty of that type: the remote app answering a
                      local app's request for its selection

Only message headers are read; everything else is forwarded as it comes."""

from __future__ import annotations

import os
import re
import socket
import struct
import subprocess
import threading

from .clipboard import GNOME_FILES, URI_LIST, clipboard_data, parse_uris
from .hostenv import host_env

FILE_TYPES = (URI_LIST, GNOME_FILES)
SOCKET_DIR = "/tmp/.X11-unix"
FIRST_DISPLAY = 200
# requests
INTERN_ATOM, GET_ATOM_NAME, CHANGE_PROPERTY, GET_PROPERTY = 16, 17, 18, 20
QUERY_EXTENSION = 98
# Extensions whose replies carry file descriptors, which can't reach a remote app:
# it would wait for them forever (mpv hangs on DRI3Open). Remote apps are told
# they're missing and use what works over the network.
HIDDEN_EXTENSIONS = {"DRI3"}
MAX_REWRITE = 1 << 20        # file lists bigger than this pass unchanged


def _pad(n: int) -> int:
    return n + (-n % 4)


def display_socket(display: str) -> str | None:
    """The socket path of a local display like ":0" or ":0.0" (None for TCP ones)."""
    m = re.fullmatch(r"(?:unix)?:(\d+)(?:\.\d+)?", display or "")
    return f"{SOCKET_DIR}/X{m.group(1)}" if m else None


def _connect(path: str) -> socket.socket:
    """Connect to an X server's socket, trying the abstract one first (as Xlib does)."""
    for address in ("\0" + path, path):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        try:
            sock.connect(address)
            return sock
        except OSError:
            sock.close()
    raise OSError(f"can't connect to {path}")


def _cookie(display: str) -> tuple[bytes, bytes]:
    """(auth name, data) for the real display, from xauth; empty when it has none."""
    try:
        out = subprocess.run(["xauth", "list", display], capture_output=True, text=True,
                             timeout=5, env=host_env()).stdout
    except (OSError, subprocess.TimeoutExpired):
        return b"", b""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "MIT-MAGIC-COOKIE-1":
            try:
                return b"MIT-MAGIC-COOKIE-1", bytes.fromhex(parts[2])
            except ValueError:
                pass
    return b"", b""


class _Atoms:
    """The atoms of file-list types, learned from what clients ask (shared by all
    connections: atoms belong to the X server)."""

    def __init__(self):
        self.names: dict[int, str] = {}

    def learn(self, atom: int, name: str) -> None:
        if atom and name in FILE_TYPES:
            self.names[atom] = name


class _Session:
    def __init__(self, client: socket.socket, server: socket.socket, proxy: "Proxy"):
        self.client, self.server, self.proxy = client, server, proxy
        self.order = "<"                         # byte order, from the setup request
        self.ready = threading.Event()           # setup request seen
        self.pending: dict[int, tuple] = {}      # sequence -> what its reply tells
        self.seq = 0
        self.setup_done = False                  # the server's setup reply passed

    def run(self) -> None:
        threading.Thread(target=self._requests, daemon=True).start()
        threading.Thread(target=self._replies, daemon=True).start()

    def _end(self) -> None:
        self.ready.set()
        for sock in (self.client, self.server):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # --- plumbing: a buffer that parses what it can and forwards the rest

    def _pump(self, src: socket.socket, dst: socket.socket, parse) -> None:
        # Images for X11 apps are tens of MB/s: what isn't parsed is sent straight
        # from what was received, without copying.
        rest = b""                               # an incomplete message
        skip = 0                                 # bytes of a message to forward as is
        try:
            while True:
                chunk = src.recv(262144)
                if not chunk:
                    break
                data = rest + chunk if rest else chunk
                rest = b""
                view = memoryview(data)
                pos, out = 0, []
                while pos < len(data):
                    if skip:
                        n = min(skip, len(data) - pos)
                        out.append(view[pos:pos + n])
                        pos += n
                        skip -= n
                        continue
                    result = parse(view[pos:])
                    if result is None:
                        rest = bytes(view[pos:])     # need more bytes
                        break
                    message, used, skip = result
                    if message:
                        out.append(message)
                    pos += used
                if len(out) == 1:
                    dst.sendall(out[0])
                elif out:
                    dst.sendall(b"".join(out))
        except OSError:
            pass
        finally:
            self._end()

    def _u16(self, data, pos: int) -> int:
        return struct.unpack_from(self.order + "H", data, pos)[0]

    def _u32(self, data, pos: int) -> int:
        return struct.unpack_from(self.order + "I", data, pos)[0]

    # --- client -> server

    def _requests(self) -> None:
        self._pump(self.client, self.server, self._parse_request)

    def _parse_request(self, buf: bytearray):
        """(bytes to send now, bytes used from buf, bytes after them to pass as is),
        or None to wait for more."""
        if not self.ready.is_set():
            return self._setup(buf)
        if len(buf) < 4:
            return None
        opcode = buf[0]
        length = self._u16(buf, 2)
        head = 4
        if length == 0:                          # BIG-REQUESTS: a 32-bit length follows
            if len(buf) < 8:
                return None
            length, head = self._u32(buf, 4), 8
        size = length * 4
        self.seq = (self.seq + 1) & 0xFFFF
        if opcode in (INTERN_ATOM, GET_ATOM_NAME, CHANGE_PROPERTY, QUERY_EXTENSION):
            # small, or (ChangeProperty) only rewritten when small: read it whole
            if size > MAX_REWRITE + 64:
                return b"", 0, size
            if len(buf) < size:
                self.seq = (self.seq - 1) & 0xFFFF   # counted again when it's complete
                return None
            message = bytes(buf[:size])
            if opcode == INTERN_ATOM:
                n = self._u16(message, head)
                name = message[head + 4:head + 4 + n].decode(errors="replace")
                if name in FILE_TYPES:
                    self.pending[self.seq] = ("atom", name)
            elif opcode == QUERY_EXTENSION:
                n = self._u16(message, head)
                if message[head + 4:head + 4 + n].decode(errors="replace") in HIDDEN_EXTENSIONS:
                    self.pending[self.seq] = ("hide",)
            elif opcode == GET_ATOM_NAME:
                self.pending[self.seq] = ("name", self._u32(message, head))
            else:
                message = self._change_property(message, head)
            return message, size, 0
        if opcode == GET_PROPERTY:
            self.pending[self.seq] = ("property",)
        return b"", 0, size

    def _setup(self, buf: bytearray):
        if len(buf) < 12:
            return None
        self.order = ">" if buf[0] == 0x42 else "<"
        n, d = self._u16(buf, 6), self._u16(buf, 8)
        size = 12 + _pad(n) + _pad(d)
        if len(buf) < size:
            return None
        message = bytes(buf[:size])
        name, data = self.proxy.cookie
        if name:
            # ssh has no cookie for our display and sends a made-up one: use the real one
            message = (message[:6] + struct.pack(self.order + "HH", len(name), len(data))
                       + message[10:12] + name + b"\0" * (-len(name) % 4)
                       + data + b"\0" * (-len(data) % 4))
        self.ready.set()
        return message, size, 0

    def _change_property(self, message: bytes, head: int) -> bytes:
        base = head - 4                          # fields move 4 bytes in the big form
        prop_type = self._u32(message, base + 12)
        mime = self.proxy.atoms.names.get(prop_type)
        if mime is None or message[base + 16] != 8:
            return message
        count = self._u32(message, base + 20)
        data = self.proxy.rewrite_data(mime, message[base + 24:base + 24 + count])
        if data is None:
            return message
        body = bytearray(message[:base + 24]) + data + b"\0" * (-len(data) % 4)
        struct.pack_into(self.order + "I", body, base + 20, len(data))
        words = len(body) // 4
        if head == 8:
            struct.pack_into(self.order + "I", body, 4, words)
        elif words > 0xFFFF:
            return message
        else:
            struct.pack_into(self.order + "H", body, 2, words)
        return bytes(body)

    # --- server -> client

    def _replies(self) -> None:
        self.ready.wait()
        self._pump(self.server, self.client, self._parse_reply)

    def _parse_reply(self, buf: bytearray):
        if not self.setup_done:
            if len(buf) < 8:
                return None
            self.setup_done = True
            return b"", 0, 8 + self._u16(buf, 6) * 4
        if len(buf) < 32:
            return None
        kind = buf[0] & 0x7F
        if kind == 0:                            # error: its request gets no reply
            self.pending.pop(self._u16(buf, 2), None)
            return bytes(buf[:32]), 32, 0
        if kind == 1:
            size = 32 + self._u32(buf, 4) * 4
            what = self.pending.pop(self._u16(buf, 2), None)
            if what is None:
                return b"", 0, size
            if size > MAX_REWRITE + 64:
                return b"", 0, size
            if len(buf) < size:
                self.pending[self._u16(buf, 2)] = what   # wait for the rest
                return None
            reply = bytes(buf[:size])
            if what[0] == "atom":
                self.proxy.atoms.learn(self._u32(reply, 8), what[1])
            elif what[0] == "name":
                n = self._u16(reply, 8)
                self.proxy.atoms.learn(what[1], reply[32:32 + n].decode(errors="replace"))
            elif what[0] == "property":
                reply = self._property_reply(reply)
            elif what[0] == "hide":
                reply = reply[:8] + b"\0\0\0\0" + reply[12:]   # present, opcode, event, error
            return reply, size, 0
        if kind == 35:                           # GenericEvent
            return b"", 0, 32 + self._u32(buf, 4) * 4
        return bytes(buf[:32]), 32, 0

    def _property_reply(self, reply: bytes) -> bytes:
        mime = self.proxy.atoms.names.get(self._u32(reply, 8))
        if mime is None or reply[1] != 8 or self._u32(reply, 12) != 0:
            return reply                         # not a file list, or only part of it
        count = self._u32(reply, 16)
        data = self.proxy.rewrite_data(mime, reply[32:32 + count])
        if data is None:
            return reply
        fixed = bytearray(reply[:32])
        struct.pack_into(self.order + "I", fixed, 4, _pad(len(data)) // 4)
        struct.pack_into(self.order + "I", fixed, 16, len(data))
        return bytes(fixed) + data + b"\0" * (-len(data) % 4)


class Proxy:
    """A display for ssh -Y. rewrite(paths) is share.Daemon.rewrite."""

    def __init__(self, upstream_display: str, rewrite, log=print):
        self.upstream = display_socket(upstream_display)
        self.upstream_display = upstream_display
        self.rewrite, self.log = rewrite, log
        self.atoms = _Atoms()
        self.cookie = (b"", b"")
        self.listener: socket.socket | None = None
        self.path = ""
        self.display = ""

    def start(self) -> str:
        """The DISPLAY for ssh (":N"). Raises OSError when it can't be set up."""
        if self.upstream is None:
            raise OSError(f"not a local display: {self.upstream_display}")
        self.cookie = _cookie(self.upstream_display)
        os.makedirs(SOCKET_DIR, exist_ok=True)
        for number in range(FIRST_DISPLAY, FIRST_DISPLAY + 100):
            path = f"{SOCKET_DIR}/X{number}"
            if os.path.lexists(path) or os.path.exists(f"/tmp/.X{number}-lock"):
                continue
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
            try:
                sock.bind(path)
            except OSError:
                sock.close()
                continue
            os.chmod(path, 0o600)
            sock.listen(8)
            self.listener, self.path, self.display = sock, path, f":{number}"
            threading.Thread(target=self._accept, daemon=True).start()
            return self.display
        raise OSError("no free X display number")

    def _accept(self) -> None:
        listener = self.listener
        while True:
            try:
                client, _addr = listener.accept()
            except OSError:
                return
            try:
                server = _connect(self.upstream)
            except OSError as e:
                self.log(f"x11: {e}")
                client.close()
                continue
            _Session(client, server, self).run()

    def rewrite_data(self, mime: str, data: bytes) -> bytes | None:
        """The file list with rewritten paths, or None to leave it."""
        if not data or data.startswith(b"cut"):
            return None
        paths = parse_uris(data)
        new = self.rewrite(paths) if paths else None
        if not new:
            return None
        self.log(f"x11: {len(new)} file(s) -> {new[0]}…")
        return clipboard_data(new)[mime]

    def stop(self) -> None:
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass
