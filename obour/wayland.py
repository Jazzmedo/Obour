"""Just enough of the Wayland wire protocol to use the clipboard through the
data-control protocol (ext-data-control-v1, or wlr-data-control on older desktops).

It is small and dependency-free: a message is the object id, then the size and
opcode, then the arguments (32-bit numbers, and strings with their length); file
descriptors travel beside the bytes (SCM_RIGHTS)."""

from __future__ import annotations

import os
import socket
import struct
from collections import deque

# event signatures per interface: u = uint/int/object, n = new object, s = string, h = fd
EVENTS = {
    "wl_display": {0: "uus", 1: "u"},                 # error, delete_id
    "wl_registry": {0: "usu", 1: "u"},                # global, global_remove
    "wl_callback": {0: "u"},                          # done
    "wl_seat": {0: "u", 1: "s"},                      # capabilities, name
    "manager": {},
    "device": {0: "n", 1: "u", 2: "", 3: "u"},        # data_offer, selection, finished, primary
    "source": {0: "sh", 1: ""},                       # send, cancelled
    "offer": {0: "s"},                                # offer
}
# the data-control managers in order of preference, and the versions used
MANAGERS = (("ext_data_control_manager_v1", 1), ("zwlr_data_control_manager_v1", 1))


class WaylandError(Exception):
    pass


def _pack_string(text: str) -> bytes:
    data = text.encode() + b"\0"
    return struct.pack("=I", len(data)) + data + b"\0" * (-len(data) % 4)


class Connection:
    def __init__(self, display: str | None = None):
        display = display or os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        if not display.startswith("/"):
            runtime = os.environ.get("XDG_RUNTIME_DIR")
            if not runtime:
                raise WaylandError("XDG_RUNTIME_DIR is not set")
            display = os.path.join(runtime, display)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        try:
            self.sock.connect(display)
        except OSError as e:
            self.sock.close()
            raise WaylandError(f"can't connect to {display}: {e}") from e
        self._next_id = 2
        self.objects: dict[int, tuple[str, object]] = {1: ("wl_display", None)}
        self._in = b""
        self._fds: deque[int] = deque()

    def fileno(self) -> int:
        return self.sock.fileno()

    def close(self) -> None:
        for fd in self._fds:
            os.close(fd)
        self._fds.clear()
        self.sock.close()

    def new(self, kind: str, handler=None) -> int:
        oid = self._next_id
        self._next_id += 1
        self.objects[oid] = (kind, handler)
        return oid

    def send(self, oid: int, opcode: int, payload: bytes = b"", fds: list[int] = ()) -> None:
        msg = struct.pack("=II", oid, ((8 + len(payload)) << 16) | opcode) + payload
        if fds:
            socket.send_fds(self.sock, [msg], list(fds))
        else:
            self.sock.sendall(msg)

    # --- the few core requests needed

    def get_registry(self, handler) -> int:
        registry = self.new("wl_registry", handler)
        self.send(1, 1, struct.pack("=I", registry))
        return registry

    def bind(self, registry: int, name: int, interface: str, version: int, kind: str,
             handler=None) -> int:
        oid = self.new(kind, handler)
        self.send(registry, 0, struct.pack("=I", name) + _pack_string(interface)
                  + struct.pack("=II", version, oid))
        return oid

    def roundtrip(self) -> None:
        """Wait until the compositor has handled everything sent so far."""
        done = []
        callback = self.new("wl_callback", lambda _oid, _op, _args: done.append(True))
        self.send(1, 0, struct.pack("=I", callback))
        while not done:
            self.dispatch()

    # --- events

    def dispatch(self) -> None:
        """Read what's available (blocking until something is) and call handlers."""
        data, fds, _flags, _addr = socket.recv_fds(self.sock, 65536, 28)
        if not data:
            raise WaylandError("the compositor closed the connection")
        self._fds.extend(fds)
        self._in += data
        while len(self._in) >= 8:
            oid, word = struct.unpack_from("=II", self._in)
            size, opcode = word >> 16, word & 0xFFFF
            if size < 8 or len(self._in) < size:
                break
            body, self._in = self._in[8:size], self._in[size:]
            self._event(oid, opcode, body)

    def _event(self, oid: int, opcode: int, body: bytes) -> None:
        kind, handler = self.objects.get(oid, (None, None))
        if kind is None:
            return
        signature = EVENTS.get(kind, {}).get(opcode)
        if signature is None:
            return
        args: list = []
        pos = 0
        for code in signature:
            if code in "un":
                (value,) = struct.unpack_from("=I", body, pos)
                pos += 4
                args.append(value)
            elif code == "s":
                (length,) = struct.unpack_from("=I", body, pos)
                pos += 4
                args.append(body[pos:pos + max(length - 1, 0)].decode(errors="replace"))
                pos += length + (-length % 4)
            elif code == "h":
                args.append(self._fds.popleft() if self._fds else -1)
        if kind == "wl_display":
            if opcode == 0:
                raise WaylandError(f"protocol error {args[1]} on object {args[0]}: {args[2]}")
            self.objects.pop(args[0], None)       # delete_id
            return
        if handler is not None:
            handler(oid, opcode, args)
        elif kind == "source" and opcode == 0 and args[1] >= 0:
            os.close(args[1])
