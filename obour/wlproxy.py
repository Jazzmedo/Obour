"""Drag and drop of files between this computer and Wayland apps on a host.

A dragged file arrives as its path on the computer it was dragged from, which the
other computer doesn't have. waypipe (the local end) connects to this proxy
instead of the compositor; the proxy passes every message through unchanged,
except the file lists of drags and pastes, which it rewrites like
clipboard.Bridge does (see share.py):

  a drop into a remote app      wl_data_offer.receive: the list the compositor
                                sends is rewritten on its way to the app
  a drag out of a remote app    wl_data_source.send: the list the app sends is
                                rewritten on its way to the compositor

To pass file descriptors on with the right messages (and to know which objects
are data offers and sources) the proxy follows each message's signature, from
wlproto.json (made by packaging/wayland/gen-protocols.py)."""

from __future__ import annotations

import array
import json
import os
import select
import socket
import struct
import threading
import time
import uuid
from collections import deque

from .clipboard import GNOME_FILES, URI_LIST, clipboard_data, parse_uris

FILE_TYPES = (URI_LIST, GNOME_FILES)
MAX_FDS = 28                 # what libwayland reads with one message batch
READ_TIMEOUT_S = 10
# (interface, opcode) whose "s h" arguments are a file list to rewrite
RECEIVE = ("wl_data_offer", 1)          # request: receive(mime_type, fd)
SEND = ("wl_data_source", 1)            # event: send(mime_type, fd)
# File lists through the desktop portal: a key that only this computer's portal
# knows. GTK 4 and KDE apps prefer them, and the drop fails on the other side, so
# they're hidden and apps use text/uri-list instead.
PORTAL_TYPES = (b"application/vnd.portal.filetransfer", b"application/vnd.portal.files")
OFFERS = {("wl_data_offer", 1, 0),                       # event: offer(mime_type)
          ("zwp_primary_selection_offer_v1", 1, 0),
          ("wl_data_source", 0, 0),                      # request: offer(mime_type)
          ("zwp_primary_selection_source_v1", 0, 0)}

_TABLE: dict | None = None


def _table() -> dict:
    global _TABLE
    if _TABLE is None:
        with open(os.path.join(os.path.dirname(__file__), "wlproto.json")) as f:
            _TABLE = {name: ([m.split() for m in requests], [m.split() for m in events])
                      for name, (requests, events) in json.load(f).items()}
    return _TABLE


def _parse(signature: list[str], body: bytes):
    """(number of fds, [(new object id, interface)], [strings]) of one message."""
    fds, new, strings = 0, [], []
    pos = 0
    try:
        for code in signature:
            if code == "h":
                fds += 1
            elif code in ("s", "a"):
                (length,) = struct.unpack_from("=I", body, pos)
                pos += 4
                if code == "s":
                    strings.append(body[pos:pos + max(length - 1, 0)].decode(errors="replace"))
                pos += length + (-length % 4)
            elif code == "N":            # bind: interface name, version, id
                (length,) = struct.unpack_from("=I", body, pos)
                name = body[pos + 4:pos + 4 + max(length - 1, 0)].decode(errors="replace")
                pos += 4 + length + (-length % 4) + 4
                (oid,) = struct.unpack_from("=I", body, pos)
                pos += 4
                new.append((oid, name))
            elif code.startswith("n:"):
                (oid,) = struct.unpack_from("=I", body, pos)
                pos += 4
                new.append((oid, code[2:]))
            else:
                pos += 4
    except struct.error:
        pass
    return fds, new, strings


def _recv(sock: socket.socket):
    return socket.recv_fds(sock, 65536, 253, socket.MSG_CMSG_CLOEXEC)[:2]


def _send(sock: socket.socket, data: bytes, fds: list[int]) -> None:
    sent = sock.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                  array.array("i", fds))] if fds else [])
    if sent < len(data):
        sock.sendall(data[sent:])


def _close(fds) -> None:
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass


class _Session:
    """One client (waypipe) and its connection to the compositor."""

    def __init__(self, client: socket.socket, server: socket.socket, rewrite, log):
        self.client, self.server = client, server
        self.rewrite, self.log = rewrite, log
        self.objects: dict[int, str] = {1: "wl_display"}
        self.unknown: set[str] = set()

    def run(self) -> None:
        threading.Thread(target=self._pump, args=(self.client, self.server, 0),
                         daemon=True).start()
        threading.Thread(target=self._pump, args=(self.server, self.client, 1),
                         daemon=True).start()

    def _end(self) -> None:
        for sock in (self.client, self.server):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _pump(self, src: socket.socket, dst: socket.socket, side: int) -> None:
        """side 0: requests (client -> compositor), 1: events."""
        data, fds = b"", deque()
        try:
            while True:
                chunk, new_fds = _recv(src)
                if not chunk:
                    break
                data += chunk
                fds.extend(new_fds)
                out, out_fds = [], []
                while len(data) >= 8:
                    oid, word = struct.unpack_from("=II", data)
                    size, opcode = word >> 16, word & 0xFFFF
                    if size < 8 or len(data) < size:
                        break
                    body = data[8:size]
                    count = self._message(oid, opcode, body, side)
                    if count > len(fds):
                        break                     # its fds haven't arrived yet
                    msg_fds = [fds.popleft() for _ in range(count)]
                    message = data[:size]
                    data = data[size:]
                    if self._hidden(oid, opcode, body, side):
                        _close(msg_fds)
                        continue
                    msg_fds = self._intercept(oid, opcode, body, side, msg_fds)
                    if len(out_fds) + len(msg_fds) > MAX_FDS:
                        self._flush(dst, out, out_fds)
                    out.append(message)
                    out_fds += msg_fds
                self._flush(dst, out, out_fds)
        except OSError:
            pass
        finally:
            _close(fds)
            self._end()

    def _flush(self, dst: socket.socket, out: list[bytes], fds: list[int]) -> None:
        if out:
            try:
                _send(dst, b"".join(out), fds)
            finally:
                _close(fds)
                out.clear()
                fds.clear()

    def _message(self, oid: int, opcode: int, body: bytes, side: int) -> int:
        """Follow new objects; the number of fds the message carries."""
        interface = self.objects.get(oid)
        messages = _table().get(interface)
        if messages is None or opcode >= len(messages[side]):
            if interface not in self.unknown:
                self.unknown.add(interface)
                self.log(f"drag and drop: unknown interface {interface} (object {oid})")
            return 0
        if interface == "wl_display" and side == 1 and opcode == 1:
            self.objects.pop(struct.unpack_from("=I", body)[0], None)    # delete_id
            return 0
        count, new, _strings = _parse(messages[side][opcode], body)
        for new_id, name in new:
            self.objects[new_id] = name
        return count

    def _hidden(self, oid: int, opcode: int, body: bytes, side: int) -> bool:
        """An offer of a portal file list (see PORTAL_TYPES)."""
        if (self.objects.get(oid), side, opcode) not in OFFERS:
            return False
        _count, _new, strings = _parse(["s"], body)
        return bool(strings) and strings[0].encode() in PORTAL_TYPES

    def _intercept(self, oid: int, opcode: int, body: bytes, side: int,
                   fds: list[int]) -> list[int]:
        """For file lists: hand the sender a pipe, and pass on what arrives there
        rewritten. Returns the fds to send with the message."""
        key = (self.objects.get(oid), opcode)
        if key != (RECEIVE if side == 0 else SEND) or len(fds) != 1:
            return fds
        _count, _new, strings = _parse(["s", "h"], body)
        if not strings or strings[0] not in FILE_TYPES:
            return fds
        read_end, write_end = os.pipe()
        threading.Thread(target=self._relay, args=(strings[0], read_end, fds[0]),
                         daemon=True).start()
        return [write_end]

    def _relay(self, mime: str, read_end: int, target: int) -> None:
        try:
            data = _read_all(read_end)
            if data and not data.startswith(b"cut"):
                paths = parse_uris(data)
                new = self.rewrite(paths) if paths else None
                if new:
                    self.log(f"drag and drop: {len(new)} file(s) -> {new[0]}…")
                    data = clipboard_data(new)[mime]
            view = memoryview(data)
            while view:
                view = view[os.write(target, view):]
        except OSError:
            pass
        finally:
            _close((read_end, target))


def _read_all(fd: int) -> bytes:
    data = b""
    deadline = time.monotonic() + READ_TIMEOUT_S
    while (left := deadline - time.monotonic()) > 0:
        if not select.select([fd], [], [], left)[0]:
            break
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        data += chunk
    return data


class Proxy:
    """A Wayland socket for waypipe. rewrite(paths) is share.Daemon.rewrite."""

    def __init__(self, upstream: str, directory: str, rewrite, log=print):
        self.upstream = upstream
        self.path = os.path.join(directory, f"wl-{uuid.uuid4().hex[:10]}.sock")
        self.rewrite, self.log = rewrite, log
        self.listener: socket.socket | None = None

    def start(self) -> str:
        """The socket path to use as WAYLAND_DISPLAY."""
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        self.listener.bind(self.path)
        os.chmod(self.path, 0o600)
        self.listener.listen(4)
        threading.Thread(target=self._accept, daemon=True).start()
        return self.path

    def _accept(self) -> None:
        listener = self.listener
        while True:
            try:
                client, _addr = listener.accept()
            except OSError:
                return
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
            try:
                server.connect(self.upstream)
            except OSError as e:
                self.log(f"drag and drop: can't connect to {self.upstream}: {e}")
                client.close()
                server.close()
                continue
            _Session(client, server, self.rewrite, self.log).run()

    def stop(self) -> None:
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        try:
            os.unlink(self.path)
        except OSError:
            pass


def display_path() -> str | None:
    """This session's compositor socket, as an absolute path."""
    display = os.environ.get("WAYLAND_DISPLAY")
    if not display:
        return None
    if display.startswith("/"):
        return display
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return os.path.join(runtime, display) if runtime else None
