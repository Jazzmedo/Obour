"""Copy and paste files between this computer and hosts.

While an app with file sharing runs, a helper process (`obour share-daemon`, one per
desktop session) keeps two read-only mounts per host, at the same path on both
computers so a copied path works on either side:

  /tmp/obour-<user>-<id>      this computer's copied files. On the host: an sshfs
                              mount served by sftp.py, which shows only the files
                              copied to the clipboard. Here: a link to /.
  /tmp/obour-<user>-h<host>   the host's files. Here: an sshfs mount of the host
                              (needs sshfs here). On the host: a link to /.

clipboard.Bridge rewrites copied file paths to those folders. Apps (GUI or menu
launches) hold a host through the helper's socket for as long as they run; the
first hold mounts, the last release unmounts. The helper leaves when nothing is
held and it no longer owns the clipboard. A helper from another version of Obour
(after an update) leaves as soon as nothing is held, and a new one takes over."""

from __future__ import annotations

import fcntl
import getpass
import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time

from . import auth, sftp
from .hostenv import host_env
from .i18n import _
from .look import local_id

RUNTIME = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/obour-run-{os.getuid()}",
                       "obour")
SOCKET = os.path.join(RUNTIME, "share.sock")
LOCK = os.path.join(RUNTIME, "share.lock")
LOG = os.path.join(RUNTIME, "share.log")
IDLE_EXIT_S = 20
_BUILD: str | None = None


def build() -> str:
    """Identifies this copy of Obour's code (the helper and the app must match)."""
    global _BUILD
    if _BUILD is None:
        digest = hashlib.sha256()
        here = os.path.dirname(os.path.abspath(__file__))
        for root, dirs, files in os.walk(here):
            dirs.sort()
            for name in sorted(files):
                if name.endswith((".py", ".json")):
                    with open(os.path.join(root, name), "rb") as f:
                        digest.update(name.encode() + f.read())
        _BUILD = digest.hexdigest()[:16]
    return _BUILD


_USER = "".join(c for c in getpass.getuser() if c.isalnum() or c in "-_") or "user"


def local_share_path() -> str:
    return f"/tmp/obour-{_USER}-{local_id()}"


def host_share_path(host: str) -> str:
    return f"/tmp/obour-{_USER}-h{hashlib.sha256(host.encode()).hexdigest()[:8]}"


def _log(text: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + text + "\n")
    except OSError:
        pass


def _ensure_link(path: str) -> None:
    """path -> / on this computer (so shared paths also work here)."""
    if os.path.islink(path):
        if os.readlink(path) == "/":
            return
        os.unlink(path)
    elif os.path.isdir(path):
        try:
            os.rmdir(path)           # an empty leftover folder
        except OSError:
            return
    try:
        os.symlink("/", path)
    except FileExistsError:
        pass


def _unmount(path: str) -> None:
    if os.path.ismount(path):
        subprocess.run(["fusermount3", "-uz", path], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=host_env())


# On the host: link the host path, then serve our files with sshfs over this
# connection until it closes. stdout is the SFTP stream, so nothing else prints there.
_REMOTE_MOUNT = r'''P=$1; H=$2
command -v sshfs >/dev/null 2>&1 || { echo "obour: sshfs is not installed" >&2; exit 3; }
if [ -L "$H" ] || [ ! -e "$H" ]; then rm -f "$H"; ln -s / "$H"; fi
fusermount3 -uz "$P" 2>/dev/null || fusermount -uz "$P" 2>/dev/null
[ -L "$P" ] && rm -f "$P"
mkdir -p "$P" && chmod 700 "$P" || exit 4
exec 4<&0   # a background job would otherwise get /dev/null as its input
sshfs -f -o passive,ro,follow_symlinks,cache=no obour:/ "$P" <&4 4<&- &
fs=$!; exec 4<&-; i=0
until mountpoint -q "$P" 2>/dev/null || [ $i -ge 40 ] || ! kill -0 $fs 2>/dev/null; do
    sleep 0.2; i=$((i+1)); done
if mountpoint -q "$P" 2>/dev/null; then echo "obour: mounted" >&2; else echo "obour: mount failed" >&2; fi
wait $fs
fusermount3 -uz "$P" 2>/dev/null || fusermount -uz "$P" 2>/dev/null
rmdir "$P" 2>/dev/null; rm -f "$H"
'''
# On the host: its SFTP server, read-only, for our mount of the host
_REMOTE_SFTP = ("for p in /usr/lib/openssh/sftp-server /usr/libexec/openssh/sftp-server "
                "/usr/lib/ssh/sftp-server /usr/libexec/sftp-server /usr/lib/sftp-server; do "
                '[ -x "$p" ] && exec "$p" -R; done; echo "obour: no sftp-server" >&2; exit 127')


class HostShare:
    def __init__(self, host: str, allowed: sftp.Allowed):
        self.host = host
        self.allowed = allowed
        self.procs: list[subprocess.Popen] = []
        self.here = False            # the host's files are mounted here
        self.there = False           # our files are mounted on the host
        self._mounted = threading.Event()   # the host said so (or gave up)
        self.problems: list[str] = []

    def _ssh(self, command: str) -> subprocess.Popen:
        from .remote import ssh_argv
        return subprocess.Popen(ssh_argv(self.host) + [command], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=auth.ssh_env(self.host), start_new_session=True)

    def start(self) -> None:
        _ensure_link(local_share_path())
        # our files, mounted on the host
        proc = self._ssh("sh -c " + shlex.quote(_REMOTE_MOUNT) + " obour "
                         + shlex.quote(local_share_path()) + " "
                         + shlex.quote(host_share_path(self.host)))
        self.procs.append(proc)
        threading.Thread(target=sftp.serve, daemon=True,
                         args=(proc.stdout.fileno(), proc.stdin.fileno(), self.allowed)).start()
        threading.Thread(target=self._watch_stderr, args=(proc, "host"), daemon=True).start()
        # the host's files, mounted here
        path = host_share_path(self.host)
        if not shutil.which("sshfs"):
            self.problems.append(_("sshfs isn't installed on this computer, so files copied "
                                   "on {host} can't be pasted here.").format(host=self.host))
        else:
            _unmount(path)
            if os.path.islink(path):
                os.unlink(path)
            os.makedirs(path, mode=0o700, exist_ok=True)
            server = self._ssh("sh -c " + shlex.quote(_REMOTE_SFTP))
            fs = subprocess.Popen(["sshfs", "-f", "-o", "passive,ro,follow_symlinks", "obour:/",
                                   path], stdin=server.stdout, stdout=server.stdin,
                                  stderr=subprocess.PIPE, env=host_env(), start_new_session=True)
            server.stdout.close()
            server.stdin.close()
            self.procs += [server, fs]
            threading.Thread(target=self._watch_stderr, args=(server, "sftp"), daemon=True).start()
            threading.Thread(target=self._watch_stderr, args=(fs, "sshfs"), daemon=True).start()
        self._mounted.wait(12)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and shutil.which("sshfs"):
            self.here = os.path.ismount(path)
            if self.here:
                break
            time.sleep(0.2)
        _log(f"{self.host}: host mount {'up' if self.there else 'failed'}, "
             f"local mount {'up' if self.here else 'not available'}")

    def _watch_stderr(self, proc: subprocess.Popen, what: str) -> None:
        try:
            self._read_stderr(proc, what)
        finally:
            if what == "host":
                self.there = False
                self._mounted.set()

    def _read_stderr(self, proc: subprocess.Popen, what: str) -> None:
        for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            _log(f"{self.host} [{what}]: {line}")
            if line == "obour: mounted":
                self.there = True
                self._mounted.set()
            elif line == "obour: mount failed":
                self._mounted.set()
            if "sshfs is not installed" in line:
                self.problems.append(_("sshfs isn't installed on {host}, so your files can't "
                                       "be pasted there (⋮ → Set Up Host…).").format(host=self.host))

    def stop(self) -> None:
        _unmount(host_share_path(self.host))
        for proc in self.procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, 15)
                except ProcessLookupError:
                    pass
        for proc in self.procs:
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            os.rmdir(host_share_path(self.host))
        except OSError:
            pass
        _log(f"{self.host}: stopped")


class Daemon:
    def __init__(self):
        self.allowed = sftp.Allowed()
        self.holds: dict[str, int] = {}
        self.shares: dict[str, HostShare] = {}
        self.lock = threading.Lock()
        self.bridge = None
        self.last_active = time.monotonic()
        self.outdated = False        # a newer Obour asked: leave once nothing is held

    # --- clipboard

    def rewrite(self, paths: list[str]) -> list[str] | None:
        with self.lock:
            shares = [s for s in self.shares.values()]
        if not shares:
            return None
        mine = local_share_path()
        out = []
        for path in paths:
            if path.startswith("/tmp/obour-"):
                return None                   # already rewritten
            if os.path.lexists(path):
                if not any(s.there for s in shares):
                    return None               # no host can see our files
                self.allowed.add(path)
                out.append(mine + path)
                continue
            for share in shares:
                if share.here and os.path.lexists(host_share_path(share.host) + path):
                    out.append(host_share_path(share.host) + path)
                    break
            else:
                return None                   # not a file we know
        return out

    def _start_bridge(self) -> str:
        if self.bridge is not None and self.bridge.error is None:
            return ""
        from .clipboard import Bridge
        self.bridge = Bridge(self.rewrite, on_log=_log)
        if not self.bridge.start():
            _log(f"clipboard: {self.bridge.error}")
            return _("Files can't be copied through the clipboard on this desktop "
                     "({reason}).").format(reason=self.bridge.error)
        return ""

    # --- holds

    def hold(self, host: str) -> list[str]:
        with self.lock:
            self.holds[host] = self.holds.get(host, 0) + 1
            first = self.holds[host] == 1
            if first:
                share = self.shares[host] = HostShare(host, self.allowed)
        notes = []
        if first:
            share.start()
        else:
            share = self.shares.get(host)
        note = self._start_bridge()
        if note:
            notes.append(note)
        if share is not None:
            if not share.there and not share.problems:
                notes.append(_("Couldn't share your files with {host}; see {log}.").format(
                    host=host, log=LOG))
            notes += share.problems
        return notes

    def release(self, host: str) -> None:
        share = None
        with self.lock:
            self.holds[host] = self.holds.get(host, 1) - 1
            if self.holds[host] <= 0:
                self.holds.pop(host, None)
                share = self.shares.pop(host, None)
            if not self.holds:
                self.allowed.clear()
                self.last_active = time.monotonic()
        if share is not None:
            share.stop()

    def _proxy(self, compositor: str):
        """A Wayland socket for the app's waypipe, where drags of files are rewritten."""
        from .wlproxy import Proxy
        proxy = Proxy(compositor, RUNTIME, self.rewrite, _log)
        try:
            proxy.start()
        except OSError as e:
            _log(f"drag and drop: {e}")
            proxy.stop()
            return None
        return proxy

    def _x11_proxy(self, display: str):
        """An X display for the app's ssh -Y, where file lists in drags and the
        clipboard are rewritten."""
        from .x11proxy import Proxy
        proxy = Proxy(display, self.rewrite, _log)
        try:
            proxy.start()
        except OSError as e:
            _log(f"x11: {e}")
            proxy.stop()
            return None
        return proxy

    def _client(self, conn: socket.socket) -> None:
        host = proxy = x11 = None
        try:
            f = conn.makefile("rwb", buffering=0)
            request = json.loads(f.readline() or b"{}")
            host = request.get("host")
            if not host:
                return
            if request.get("build") not in (None, build()):
                with self.lock:
                    self.outdated = True
                    idle = not self.holds
                if idle:
                    _log("another version of Obour: making way for its helper")
                    f.write(json.dumps({"restart": True}).encode() + b"\n")
                    host = None
                    return
                _log("another version of Obour: this helper leaves when its apps close")
            if request.get("password"):
                auth.remember_password(host, request["password"])
            notes = self.hold(host)
            reply = {"ok": True, "notes": notes}
            if request.get("wayland"):
                proxy = self._proxy(request["wayland"])
                if proxy is not None:
                    reply["wayland_display"] = proxy.path
            if request.get("x11"):
                x11 = self._x11_proxy(request["x11"])
                if x11 is not None:
                    reply["x11_display"] = x11.display
            f.write(json.dumps(reply).encode() + b"\n")
            while f.read(1024):      # until the app ends (the client closes)
                pass
        except (OSError, ValueError):
            pass
        finally:
            conn.close()
            for p in (proxy, x11):
                if p is not None:
                    p.stop()
            if host:
                self.release(host)

    def serve(self) -> int:
        os.makedirs(RUNTIME, mode=0o700, exist_ok=True)
        lock = open(LOCK, "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0                 # already running
        try:
            os.unlink(SOCKET)
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET)
        os.chmod(SOCKET, 0o600)
        server.listen(16)
        server.settimeout(0.5)
        _log("started")
        try:
            while True:
                try:
                    conn, _addr = server.accept()
                except socket.timeout:
                    with self.lock:
                        idle = not self.holds
                    owns = self.bridge is not None and self.bridge.owns_selection
                    if idle and (self.outdated or not owns and
                                 time.monotonic() - self.last_active > IDLE_EXIT_S):
                        break
                    continue
                self.last_active = time.monotonic()
                threading.Thread(target=self._client, args=(conn,), daemon=True).start()
        finally:
            server.close()
            try:
                os.unlink(SOCKET)
            except OSError:
                pass
            for host in list(self.shares):
                self.shares.pop(host).stop()
            if self.bridge is not None:
                self.bridge.stop()
            _log("stopped")
        return 0


# ---------------------------------------------------------------- client side

def _spawn_daemon() -> None:
    from .desktop import OBOUR_BIN
    argv = [OBOUR_BIN] if os.access(OBOUR_BIN, os.X_OK) or shutil.which(OBOUR_BIN) else [
        sys.executable, os.path.join(os.path.dirname(__file__), os.pardir, "bin", "obour")]
    os.makedirs(RUNTIME, mode=0o700, exist_ok=True)
    with open(LOG, "a") as log:
        subprocess.Popen(argv + ["share-daemon"], stdin=subprocess.DEVNULL, stdout=log,
                         stderr=log, start_new_session=True, close_fds=True)


class Hold:
    """Keeps file sharing with a host going until release() (or this process ends)."""

    def __init__(self, host: str, wayland: str | None = None, x11: str | None = None):
        """wayland: this session's compositor socket, for an app forwarded with
        waypipe; x11: this session's X display, for ssh -Y. The daemon then gives
        a socket for waypipe (wayland_display) and a display for ssh (x11_display)
        where file lists in drags and the clipboard are rewritten."""
        self.host = host
        self.wayland = wayland
        self.x11 = x11
        self.wayland_display: str | None = None
        self.x11_display: str | None = None
        self.sock: socket.socket | None = None
        self.notes: list[str] = []

    def acquire(self, timeout: float = 20) -> bool:
        deadline = time.monotonic() + timeout
        failures = 0
        while time.monotonic() < deadline:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
            try:
                sock.connect(SOCKET)
            except OSError:
                sock.close()
                # again now and then: an old helper may still be leaving
                if failures % 10 == 0:
                    _spawn_daemon()
                failures += 1
                time.sleep(0.2)
                continue
            request = {"host": self.host, "password": auth.password_for(self.host)
                       if auth.has_password(self.host) else None, "wayland": self.wayland,
                       "x11": self.x11, "build": build()}
            try:
                sock.sendall(json.dumps(request).encode() + b"\n")
                sock.settimeout(max(deadline - time.monotonic(), 1))
                reply = b""
                while not reply.endswith(b"\n"):
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise OSError("closed")
                    reply += chunk
                sock.settimeout(None)
                reply = json.loads(reply)
                if reply.get("restart"):         # an older helper is leaving
                    sock.close()
                    time.sleep(0.3)
                    continue
                self.notes = reply.get("notes", [])
                self.wayland_display = reply.get("wayland_display")
                self.x11_display = reply.get("x11_display")
            except (OSError, ValueError):
                sock.close()
                self.notes = [_("File sharing didn't start; see {log}.").format(log=LOG)]
                return False
            self.sock = sock
            return True
        self.notes = [_("File sharing didn't start; see {log}.").format(log=LOG)]
        return False

    def release(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None
