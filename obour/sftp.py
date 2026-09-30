"""A small read-only SFTP (version 3) server that only shows chosen files.

The host mounts this computer with `sshfs -o passive`, talking to serve() through
the SSH connection. Only the files and folders in `allowed` (the ones copied to the
clipboard) can be read; the folders leading to them can be looked up, but list only
the way to them. Nothing can be written, renamed or deleted."""

from __future__ import annotations

import os
import stat
import struct
import threading

# packet types
INIT, VERSION = 1, 2
OPEN, CLOSE, READ, WRITE, LSTAT, FSTAT, SETSTAT, FSETSTAT = 3, 4, 5, 6, 7, 8, 9, 10
OPENDIR, READDIR, REMOVE, MKDIR, RMDIR, REALPATH, STAT, RENAME, READLINK, SYMLINK = range(11, 21)
EXTENDED = 200
STATUS, HANDLE, DATA, NAME, ATTRS = 101, 102, 103, 104, 105
# status codes
OK, EOF, NO_SUCH_FILE, PERMISSION_DENIED, FAILURE, BAD_MESSAGE, OP_UNSUPPORTED = 0, 1, 2, 3, 4, 5, 8
# open flags that would change something
_WRITE_FLAGS = 0x02 | 0x04 | 0x08 | 0x10 | 0x20   # WRITE APPEND CREAT TRUNC EXCL
MAX_READ = 256 * 1024


class Allowed:
    """The shared paths; thread-safe. Paths are absolute, as this computer sees them."""

    def __init__(self):
        self._roots: set[str] = set()
        self._lock = threading.Lock()

    def add(self, path: str) -> None:
        path = os.path.normpath(path)
        with self._lock:
            self._roots.add(path)
            self._roots.add(os.path.realpath(path))

    def clear(self) -> None:
        with self._lock:
            self._roots.clear()

    def roots(self) -> list[str]:
        with self._lock:
            return list(self._roots)

    def readable(self, path: str) -> bool:
        """path (after following links) is a shared file/folder or inside one."""
        real = os.path.realpath(path)
        return any(real == r or real.startswith(r.rstrip("/") + "/") for r in self.roots())

    def on_the_way(self, path: str) -> bool:
        """path is a folder leading to a shared path. The root always is: the host
        mounts it before anything is copied."""
        if path == "/":
            return True
        prefix = path.rstrip("/") + "/"
        return any(r.startswith(prefix) for r in self.roots())

    def children_on_the_way(self, path: str) -> set[str]:
        prefix = path.rstrip("/") + "/"
        return {r[len(prefix):].split("/", 1)[0] for r in self.roots() if r.startswith(prefix)}


def _string(data: bytes | str) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogateescape")
    return struct.pack(">I", len(data)) + data


def _attrs(st: os.stat_result | None) -> bytes:
    if st is None:
        return struct.pack(">I", 0)
    mode = st.st_mode
    if stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        mode &= ~0o222   # read-only
    return struct.pack(">IQIIIII", 0x1 | 0x2 | 0x4 | 0x8, st.st_size, st.st_uid, st.st_gid,
                       mode, int(st.st_atime) & 0xFFFFFFFF, int(st.st_mtime) & 0xFFFFFFFF)


def _dir_stat(st: os.stat_result) -> os.stat_result:
    """A folder on the way to a shared path, shown as browsable but read-only."""
    return os.stat_result((stat.S_IFDIR | 0o555, *st[1:]))


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def u32(self) -> int:
        (v,) = struct.unpack_from(">I", self.data, self.pos)
        self.pos += 4
        return v

    def u64(self) -> int:
        (v,) = struct.unpack_from(">Q", self.data, self.pos)
        self.pos += 8
        return v

    def string(self) -> bytes:
        n = self.u32()
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def path(self) -> str:
        p = self.string().decode("utf-8", "surrogateescape") or "/"
        return os.path.normpath("/" + p) if not p.startswith("/") else os.path.normpath(p)


def _read_exact(fd: int, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def serve(rfd: int, wfd: int, allowed: Allowed) -> None:
    """Answer SFTP requests from rfd on wfd until the other side closes."""
    handles: dict[bytes, tuple[str, object]] = {}
    counter = 0

    def reply(kind: int, rid: int, payload: bytes = b"") -> None:
        body = struct.pack(">BI", kind, rid) + payload
        _write_all(wfd, struct.pack(">I", len(body)) + body)

    def status(rid: int, code: int, message: str = "") -> None:
        reply(STATUS, rid, struct.pack(">I", code) + _string(message) + _string(""))

    def error(rid: int, e: OSError) -> None:
        import errno
        code = {errno.ENOENT: NO_SUCH_FILE, errno.ENOTDIR: NO_SUCH_FILE,
                errno.EACCES: PERMISSION_DENIED, errno.EPERM: PERMISSION_DENIED}.get(
                    e.errno, FAILURE)
        status(rid, code, e.strerror or "")

    def new_handle(kind: str, obj) -> bytes:
        nonlocal counter
        counter += 1
        h = str(counter).encode()
        handles[h] = (kind, obj)
        return h

    def lookup(path: str, follow: bool) -> os.stat_result | None:
        """stat for a visible path, None if hidden."""
        if allowed.readable(path):
            return os.stat(path) if follow else os.lstat(path)
        if allowed.on_the_way(path):
            return _dir_stat(os.stat(path))
        return None

    try:
        while True:
            head = _read_exact(rfd, 4)
            if head is None:
                return
            (length,) = struct.unpack(">I", head)
            if length > 1024 * 1024:
                return
            packet = _read_exact(rfd, length)
            if packet is None:
                return
            kind = packet[0]
            r = _Reader(packet[1:])
            if kind == INIT:
                body = struct.pack(">BI", VERSION, 3)
                _write_all(wfd, struct.pack(">I", len(body)) + body)
                continue
            rid = r.u32()
            try:
                if kind in (STAT, LSTAT):
                    path = r.path()
                    st = lookup(path, kind == STAT)
                    if st is None:
                        status(rid, NO_SUCH_FILE, "Not shared")
                    else:
                        reply(ATTRS, rid, _attrs(st))
                elif kind == REALPATH:
                    path = r.path()
                    reply(NAME, rid, struct.pack(">I", 1) + _string(path) + _string(path)
                          + _attrs(None))
                elif kind == READLINK:
                    path = r.path()
                    if not allowed.readable(os.path.dirname(path)) and not allowed.readable(path):
                        status(rid, NO_SUCH_FILE, "Not shared")
                    else:
                        target = os.readlink(path)
                        reply(NAME, rid, struct.pack(">I", 1) + _string(target) + _string(target)
                              + _attrs(None))
                elif kind == OPEN:
                    path = r.path()
                    pflags = r.u32()
                    if pflags & _WRITE_FLAGS:
                        status(rid, PERMISSION_DENIED, "Read-only")
                    elif not allowed.readable(path):
                        status(rid, NO_SUCH_FILE, "Not shared")
                    else:
                        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
                        reply(HANDLE, rid, _string(new_handle("file", fd)))
                elif kind == READ:
                    h = r.string()
                    offset, size = r.u64(), r.u32()
                    entry = handles.get(h)
                    if not entry or entry[0] != "file":
                        status(rid, FAILURE, "Bad handle")
                    else:
                        data = os.pread(entry[1], min(size, MAX_READ), offset)
                        if data:
                            reply(DATA, rid, _string(data))
                        else:
                            status(rid, EOF)
                elif kind == FSTAT:
                    entry = handles.get(r.string())
                    if not entry or entry[0] != "file":
                        status(rid, FAILURE, "Bad handle")
                    else:
                        reply(ATTRS, rid, _attrs(os.fstat(entry[1])))
                elif kind == OPENDIR:
                    path = r.path()
                    if allowed.readable(path):
                        names = os.listdir(path)
                    elif allowed.on_the_way(path):
                        names = [n for n in allowed.children_on_the_way(path)
                                 if os.path.lexists(os.path.join(path, n))]
                    else:
                        status(rid, NO_SUCH_FILE, "Not shared")
                        continue
                    reply(HANDLE, rid, _string(new_handle("dir", (path, [".", ".."] + names))))
                elif kind == READDIR:
                    entry = handles.get(r.string())
                    if not entry or entry[0] != "dir":
                        status(rid, FAILURE, "Bad handle")
                        continue
                    path, names = entry[1]
                    if not names:
                        status(rid, EOF)
                        continue
                    batch = names[:100]
                    del names[:100]
                    out = b""
                    count = 0
                    for n in batch:
                        full = os.path.normpath(os.path.join(path, n))
                        try:
                            st = (os.lstat(full) if allowed.readable(full) or n in (".", "..")
                                  else _dir_stat(os.stat(full)))
                        except OSError:
                            continue
                        out += _string(n) + _string(n) + _attrs(st)
                        count += 1
                    reply(NAME, rid, struct.pack(">I", count) + out)
                elif kind == CLOSE:
                    entry = handles.pop(r.string(), None)
                    if entry and entry[0] == "file":
                        os.close(entry[1])
                    status(rid, OK)
                elif kind in (WRITE, SETSTAT, FSETSTAT, REMOVE, MKDIR, RMDIR, RENAME, SYMLINK):
                    status(rid, PERMISSION_DENIED, "Read-only")
                else:
                    status(rid, OP_UNSUPPORTED)
            except OSError as e:
                error(rid, e)
            except (struct.error, IndexError):
                status(rid, BAD_MESSAGE)
    except OSError:
        return
    finally:
        for kind, obj in handles.values():
            if kind == "file":
                try:
                    os.close(obj)
                except OSError:
                    pass
