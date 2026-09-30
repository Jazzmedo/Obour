"""Makes copied files pasteable on the other computer.

When files are copied (in any app, here or in a remote app), the clipboard holds
their paths, which don't exist on the other computer. The bridge watches the
clipboard; when it holds file paths, it replaces them with the same files under
the share folders (see share.py), which have the same path on both computers:

    /home/me/a.txt              ->  /tmp/obour-me-<id>/home/me/a.txt
    /home/them/b.txt (remote)   ->  /tmp/obour-me-h<id>/home/them/b.txt

Cut files are left alone (moving between computers isn't supported), so cut and
paste on this computer keeps working."""

from __future__ import annotations

import os
import select
import struct
import threading
import time
from urllib.parse import quote, unquote, urlparse

from .wayland import MANAGERS, Connection, WaylandError, _pack_string

GNOME_FILES = "x-special/gnome-copied-files"
URI_LIST = "text/uri-list"
KDE_CUT = "application/x-kde-cutselection"
MARKER = "application/x-obour-clipboard"
TEXT_TYPES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "STRING", "TEXT")
READ_TIMEOUT_S = 3


def parse_uris(data: bytes) -> list[str]:
    """Local paths from text/uri-list or the file lines of gnome-copied-files; empty
    if any entry isn't a local file."""
    paths = []
    for line in data.decode(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line in ("copy", "cut"):
            continue
        uri = urlparse(line)
        if uri.scheme != "file" or uri.netloc not in ("", "localhost"):
            return []
        paths.append(unquote(uri.path))
    return paths


def to_uri(path: str) -> str:
    return "file://" + quote(path)


def clipboard_data(paths: list[str]) -> dict[str, bytes]:
    uris = [to_uri(p) for p in paths]
    text = "\n".join(paths).encode()
    data = {URI_LIST: ("\r\n".join(uris) + "\r\n").encode(),
            GNOME_FILES: ("copy\n" + "\n".join(uris)).encode(),
            KDE_CUT: b"0", MARKER: b"1"}
    data.update({t: text for t in TEXT_TYPES})
    return data


class Bridge:
    """Runs on its own thread. rewrite(paths) returns the new paths, or None to leave
    the clipboard as it is. on_log(text) reports problems."""

    def __init__(self, rewrite, on_log=print):
        self.rewrite = rewrite
        self.on_log = on_log
        self.owns_selection = False
        self._stop_r, self._stop_w = os.pipe()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    # --- control

    def start(self) -> bool:
        """Connect and start watching. False (and .error) when the desktop has no
        data-control protocol (GNOME) or isn't a Wayland session."""
        try:
            self._setup()
        except WaylandError as e:
            self.error = str(e)
            return False
        self._thread = threading.Thread(target=self._loop, name="obour-clipboard", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        os.write(self._stop_w, b"x")
        if self._thread:
            self._thread.join(3)

    # --- setup

    def _setup(self) -> None:
        self.conn = conn = Connection()
        globals_: dict[str, tuple[int, int]] = {}

        def on_global(_oid, opcode, args):
            if opcode == 0:
                globals_.setdefault(args[1], (args[0], args[2]))

        registry = conn.get_registry(on_global)
        conn.roundtrip()
        for interface, version in MANAGERS:
            if interface in globals_:
                name, have = globals_[interface]
                self.manager = conn.bind(registry, name, interface, min(version, have), "manager")
                break
        else:
            conn.close()
            raise WaylandError("this desktop doesn't let apps manage the clipboard "
                               "(no data-control protocol)")
        if "wl_seat" not in globals_:
            conn.close()
            raise WaylandError("no seat")
        seat = conn.bind(registry, globals_["wl_seat"][0], "wl_seat", 1, "wl_seat")
        self.offers: dict[int, list[str]] = {}
        self.current: int | None = None
        self.source: int | None = None
        self.source_data: dict[str, bytes] = {}
        self.reading: dict[int, tuple[int, str, list[str], bytearray, float, dict]] = {}
        self.device = conn.new("device", self._on_device)
        conn.send(self.manager, 1, struct.pack("=II", self.device, seat))   # get_data_device
        conn.roundtrip()

    # --- events

    def _on_device(self, _oid, opcode, args) -> None:
        if opcode == 0:                      # data_offer: a new offer, its types follow
            self.offers[args[0]] = []
            self.conn.objects[args[0]] = ("offer", self._on_offer)
        elif opcode == 1:                    # selection changed
            self._selection(args[0])
        elif opcode == 3 and args[0] and args[0] != self.current:
            self._destroy_offer(args[0])     # primary selection: not used
        elif opcode == 2:                    # finished
            raise WaylandError("the clipboard device was taken away")

    def _on_offer(self, oid, _opcode, args) -> None:
        self.offers.setdefault(oid, []).append(args[0])

    def _on_source(self, oid, opcode, args) -> None:
        if opcode == 0:                      # send(mime, fd)
            mime, fd = args
            if fd < 0:
                return
            try:
                if oid == self.source:
                    os.write(fd, self.source_data.get(mime, b""))
            except OSError:
                pass
            finally:
                os.close(fd)
        elif opcode == 1:                    # cancelled: someone else copied
            self.conn.send(oid, 1)           # destroy
            self.conn.objects.pop(oid, None)
            if oid == self.source:
                self.source = None
                self.owns_selection = False

    def _destroy_offer(self, oid: int) -> None:
        if oid in self.offers:
            self.offers.pop(oid, None)
            self.conn.send(oid, 1)           # destroy
            self.conn.objects.pop(oid, None)

    def _selection(self, oid: int) -> None:
        if self.current is not None and self.current != oid:
            self._destroy_offer(self.current)
        self.current = oid or None
        if not oid:
            return
        types = self.offers.get(oid, [])
        if MARKER in types:
            return                           # our own
        if GNOME_FILES in types:
            wanted = [GNOME_FILES]
        elif URI_LIST in types:
            wanted = [URI_LIST]
        else:
            return
        if KDE_CUT in types:
            wanted.append(KDE_CUT)
        self._receive(oid, wanted, {})

    def _receive(self, oid: int, wanted: list[str], got: dict) -> None:
        mime = wanted[0]
        r, w = os.pipe()
        os.set_blocking(r, False)
        self.conn.send(oid, 0, _pack_string(mime), [w])   # receive(mime, fd)
        os.close(w)
        self.reading[r] = (oid, mime, wanted[1:], bytearray(), time.monotonic(), got)

    def _read_ready(self, r: int) -> None:
        oid, mime, rest, buf, started, got = self.reading[r]
        try:
            chunk = os.read(r, 65536)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if chunk:
            buf += chunk
            if len(buf) < 4 * 1024 * 1024:
                return
        del self.reading[r]
        os.close(r)
        got[mime] = bytes(buf)
        if oid != self.current:
            return                           # the clipboard changed meanwhile
        if rest:
            self._receive(oid, rest, got)
        else:
            self._rewrite(got)

    def _rewrite(self, got: dict[str, bytes]) -> None:
        if got.get(KDE_CUT, b"0").strip() == b"1":
            return
        files = got.get(GNOME_FILES)
        if files is not None and files.split(b"\n", 1)[0].strip() == b"cut":
            return
        paths = parse_uris(files if files is not None else got.get(URI_LIST, b""))
        if not paths:
            return
        try:
            new = self.rewrite(paths)
        except Exception as e:               # never let a bad path stop the bridge
            self.on_log(f"clipboard: {e}")
            return
        if new and new != paths:
            self._offer(new)

    def _offer(self, paths: list[str]) -> None:
        conn = self.conn
        source = conn.new("source", self._on_source)
        conn.send(self.manager, 0, struct.pack("=I", source))    # create_data_source
        self.source_data = clipboard_data(paths)
        for mime in self.source_data:
            conn.send(source, 0, _pack_string(mime))              # offer
        old = self.source
        self.source = source
        conn.send(self.device, 0, struct.pack("=I", source))     # set_selection
        self.owns_selection = True
        if old is not None:
            conn.send(old, 1)
            conn.objects.pop(old, None)

    # --- loop

    def _loop(self) -> None:
        try:
            while True:
                fds = [self.conn.fileno(), self._stop_r, *self.reading]
                ready, _w, _x = select.select(fds, [], [], 1.0)
                if self._stop_r in ready:
                    break
                if self.conn.fileno() in ready:
                    self.conn.dispatch()
                for r in list(self.reading):
                    if r in ready:
                        self._read_ready(r)
                    elif time.monotonic() - self.reading[r][4] > READ_TIMEOUT_S:
                        del self.reading[r]
                        os.close(r)
        except (WaylandError, OSError) as e:
            self.error = str(e)
            self.on_log(f"clipboard: {e}")
        finally:
            for r in self.reading:
                os.close(r)
            self.reading.clear()
            self.owns_selection = False
            self.conn.close()
