"""SSH plumbing: connection tests, host capabilities, remote app scanning, icons."""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import subprocess
import tarfile
from dataclasses import dataclass, field

from . import auth
from .config import CACHE_DIR
from .hostenv import host_env
from .i18n import _

CAPS_FILE = os.path.join(CACHE_DIR, "hosts.json")
AUTH_NEEDED = "The host needs a password"


def _auth_needed() -> str:
    return _("The host needs a password")


class RemoteError(Exception):
    """A (translated) message for the user; .code is a stable id to test against, e.g.
    "sudo-password" when the host's sudo password was wrong or missing."""

    def __init__(self, message: str = "", code: str = ""):
        super().__init__(message)
        self.code = code


def is_auth_error(message: str) -> bool:
    """True for messages from friendly_ssh_error about a missing password, in English
    or in the interface language."""
    return message.startswith(AUTH_NEEDED) or message.startswith(_auth_needed())


def ssh_argv(host: str, extra: list[str] | None = None, password: str | None = None) -> list[str]:
    return ["ssh", *auth.ssh_opts(host, password), *(extra or []), host]


def unreachable(host: str | None = None) -> str:
    msg = _("The host is not reachable from this network (connection timed out).")
    if host:
        from .route import unreachable_hint
        msg += unreachable_hint(host)
    return msg


# ssh's own wording ("user@host: Permission denied (publickey,password).") — not a
# "permission denied" printed by the app or by the remote shell's startup files.
_SSH_AUTH_FAILED = re.compile(r"permission denied \([a-z0-9@.,-]+\)|permission denied, please "
                              r"try again|too many authentication failures", re.I)


def ssh_auth_failed(output: str) -> bool:
    return bool(_SSH_AUTH_FAILED.search(output))


def friendly_ssh_error(stderr: str, host: str | None = None) -> str:
    s = stderr.strip()
    low = s.lower()
    if ssh_auth_failed(s):
        return _auth_needed() + " " + _("(no working SSH key). Log in once with the password "
                                        "and Obour can set up key login so you won't need it "
                                        "again.")
    if "could not resolve hostname" in low or "name or service not known" in low:
        return _("Host not found. Check the hostname, or add it to ~/.ssh/config.")
    if "connection refused" in low:
        return _("Connection refused. Is the SSH server running on the remote machine?")
    if "timed out" in low or "no route to host" in low or "network is unreachable" in low:
        return unreachable(host)
    if "remote host identification has changed" in low or "host key verification failed" in low:
        return _("The host's key has changed since your last connection. If that is "
                 "expected, remove the old key with `ssh-keygen -R <host>`.")
    lines = [l for l in s.splitlines() if l.strip()]
    return lines[-1] if lines else _("Unknown SSH error.")


def _run(argv: list[str], host: str, *, input_text: str | None = None,
         timeout: int = 30, password: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            input=input_text.encode() if input_text is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, env=auth.ssh_env(host, password),
        )
    except FileNotFoundError:
        raise RemoteError(_("`{program}` is not installed on this computer.").format(
            program=argv[0]))


# ---------------------------------------------------------------- host capabilities

def _load_caps() -> dict:
    try:
        with open(CAPS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def host_caps(host: str) -> dict | None:
    return _load_caps().get(host)


def _save_caps(host: str, caps: dict) -> None:
    data = _load_caps()
    data[host] = caps
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = CAPS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CAPS_FILE)


# $1 is the local cursor theme's name (may be empty). Library paths cover
# Debian/Ubuntu (/usr/lib/<triplet>), Fedora (/usr/lib64) and Arch (/usr/lib/qt).
_PROBE = r'''echo __obour_ok__
. /etc/os-release 2>/dev/null
echo "os-id=$ID"; echo "os-like=$ID_LIKE"; echo "os-name=${PRETTY_NAME:-$NAME}"
echo "uid=$(id -u)"
for c in waypipe python3 xauth dbus-run-session sudo sshfs fusermount3 dconf fc-cache; do
    command -v $c >/dev/null 2>&1 && echo $c=1 || echo $c=0
done
if [ "$(id -u)" = 0 ] || sudo -n true 2>/dev/null; then echo sudo-nopass=1; else echo sudo-nopass=0; fi
has() { for f in "$@"; do [ -e "$f" ] && { echo 1; return; }; done; echo 0; }
L="/usr/lib/*-linux-gnu* /usr/lib64 /usr/lib"
lib() { for d in $L; do for f in $d/$1; do [ -e "$f" ] && { echo 1; return; }; done; done; echo 0; }
echo libpulse=$(lib libpulse.so.0)
echo alsa-pulse=$(lib alsa-lib/libasound_module_pcm_pulse.so)
echo qt5=$(lib libQt5Core.so.5)
echo qt6=$(lib libQt6Core.so.6)
q=$(lib "qt5/plugins/platformthemes/libqgtk3.so")
[ $q = 1 ] || q=$(has /usr/lib/qt/plugins/platformthemes/libqgtk3.so)
echo qt5-gtk=$q
echo qt6-gtk=$(lib "qt6/plugins/platformthemes/libqgtk3.so")
a=$(lib "qt5/plugins/styles/adwaita.so"); [ $a = 1 ] || a=$(has /usr/lib/qt/plugins/styles/adwaita.so)
echo qt5-adwaita=$a
k=$(lib "qt5/plugins/styles/libkvantum.so"); [ $k = 1 ] || k=$(has /usr/lib/qt/plugins/styles/libkvantum.so)
echo qt5-kvantum=$k
q=$(lib "qt5/plugins/platformthemes/libqt5ct.so"); [ $q = 1 ] || q=$(has /usr/lib/qt/plugins/platformthemes/libqt5ct.so)
echo qt5ct=$q
echo qt6ct=$(lib "qt6/plugins/platformthemes/libqt6ct.so")
echo qt-kde=$(lib "qt6/plugins/platformthemes/KDEPlasmaPlatformTheme6.so")
echo breeze=$(lib "qt6/plugins/styles/breeze6.so")
echo qt-lxqt=$(lib "qt6/plugins/platformthemes/libqtlxqt.so")
echo kvantum=$(lib "qt6/plugins/styles/libkvantum.so")
echo breeze-icons=$(has /usr/share/icons/breeze/index.theme "$HOME/.local/share/icons/breeze/index.theme")
c=0
if [ -n "$1" ]; then
    for d in "$HOME/.local/share/icons" "$HOME/.icons" /usr/local/share/icons /usr/share/icons; do
        [ -d "$d/$1/cursors" ] && c=1
    done
fi
echo cursor=$c
'''

CAP_FLAGS = ("waypipe", "python3", "xauth", "dbus-run-session", "sudo", "sudo-nopass",
             "libpulse", "alsa-pulse", "qt5", "qt6", "qt5-gtk", "qt6-gtk", "qt5-adwaita", "qt5-kvantum",
             "cursor", "sshfs", "fusermount3", "dconf", "fc-cache", "qt5ct", "qt6ct", "qt-kde",
             "breeze", "qt-lxqt", "kvantum", "breeze-icons")


def probe_caps(host: str, timeout: int = 20, cursor_theme: str | None = None) -> dict:
    """Connect once and record the host's OS and which helper tools it has.
    Raises RemoteError."""
    if cursor_theme is None:
        from .cursor import local_cursor
        cursor_theme = local_cursor()[0]
    argv = ssh_argv(host) + ["sh", "-c", shlex.quote(_PROBE), "obour", shlex.quote(cursor_theme)]
    try:
        proc = _run(argv, host, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RemoteError(unreachable(host))
    out = proc.stdout.decode(errors="replace")
    if proc.returncode != 0 or "__obour_ok__" not in out:
        raise RemoteError(friendly_ssh_error(proc.stderr.decode(errors="replace"), host))
    found = dict(re.findall(r"^([\w-]+)=(.*)$", out, re.M))
    caps: dict = {k: found.get(k) == "1" for k in CAP_FLAGS}
    caps.update({k: found.get(k, "").strip() for k in ("os-id", "os-like", "os-name")})
    caps["root"] = found.get("uid") == "0"
    caps["cursor-theme"] = cursor_theme
    old = host_caps(host) or {}
    caps["setup-seen"] = old.get("setup-seen", False)
    _save_caps(host, caps)
    return caps


def mark_setup_seen(host: str) -> None:
    caps = host_caps(host)
    if caps is not None:
        caps["setup-seen"] = True
        _save_caps(host, caps)


def set_cap(host: str, key: str, value) -> None:
    caps = host_caps(host)
    if caps is not None:
        caps[key] = value
        _save_caps(host, caps)


# ---------------------------------------------------------------- cursor theme

def upload_cursor_theme(host: str, theme: str, path: str) -> None:
    """Copy a local cursor theme to ~/.local/share/icons/<theme> on host."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(path, arcname=theme)
    script = ('d="$HOME/.local/share/icons"; mkdir -p "$d" && '
              'tar xzf - --no-same-owner -C "$d"')
    try:
        proc = subprocess.run(ssh_argv(host) + ["sh", "-c", shlex.quote(script)],
                              input=buf.getvalue(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=180, env=auth.ssh_env(host))
    except subprocess.TimeoutExpired:
        raise RemoteError(_("Timed out while copying the cursor theme."))
    except FileNotFoundError:
        raise RemoteError(_("`{program}` is not installed on this computer.").format(
            program="ssh"))
    if proc.returncode != 0:
        raise RemoteError(friendly_ssh_error(proc.stderr.decode(errors="replace"), host))
    set_cap(host, "cursor", True)


# ---------------------------------------------------------------- connection test

@dataclass
class TestReport:
    host: str
    ok: bool
    error: str = ""
    checks: list[tuple[str, bool, str]] = field(default_factory=list)  # label, ok, hint
    caps: dict = field(default_factory=dict)
    via: str = ""  # set when the connection goes through Tailscale

    def lines(self) -> list[str]:
        if not self.ok:
            return ["✘ " + _("SSH connection — {error}").format(error=self.error)]
        os_name = self.caps.get("os-name")
        out = ["✔ " + (_("SSH connection ({os})").format(os=os_name) if os_name
                       else _("SSH connection"))]
        if self.via:
            out.append("✔ " + self.via)
        for label, ok, hint in self.checks:
            out.append(("✔ " if ok else "✘ ") + label + ("" if ok else f" — {hint}"))
        return out


def test_connection(host: str) -> TestReport:
    try:
        caps = probe_caps(host)
    except RemoteError as e:
        return TestReport(host, ok=False, error=str(e))
    from . import hostsetup
    from .route import describe
    report = TestReport(host, ok=True, caps=caps, via=describe(host))
    for c in hostsetup.check(caps, hostsetup.local_tags(host)):
        hint = (_("install {packages} on the host (⋮ → Set Up Host…)").format(
                    packages=", ".join(c.packages)) if c.packages
                else _("not available from this host's package manager"))
        report.checks.append((_("{title} ({purpose})").format(
            title=c.requirement.title, purpose=c.requirement.purpose), c.ok, hint))
    return report


# ---------------------------------------------------------------- password & key login


# Reads one public key on stdin and adds it to authorized_keys once, like ssh-copy-id.
_INSTALL_KEY = r'''umask 077
mkdir -p "$HOME/.ssh" || exit 1
f="$HOME/.ssh/authorized_keys"
k=$(cat)
touch "$f" || exit 1
if ! grep -qxF "$k" "$f"; then
    if [ -s "$f" ] && [ -n "$(tail -c1 "$f")" ]; then echo >> "$f"; fi
    printf '%s\n' "$k" >> "$f" || exit 1
fi
command -v restorecon >/dev/null 2>&1 && restorecon -F "$HOME/.ssh" "$f" >/dev/null 2>&1
exit 0'''


def _password_error(proc: subprocess.CompletedProcess) -> RemoteError:
    err = proc.stderr.decode(errors="replace")
    if ssh_auth_failed(err):
        return RemoteError(_("Wrong password, or the host doesn't allow password login."))
    return RemoteError(friendly_ssh_error(err))


def _login_user(host: str) -> str:
    try:
        out = subprocess.run(["ssh", "-G", host], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=5).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r"^user (\S+)$", out, re.M)
    return m.group(1) if m else ""


def missing_user(host: str) -> bool:
    """True when host names no user (“192.168.1.5”) and ~/.ssh/config doesn't set one,
    so ssh would silently log in with this computer's user name."""
    import getpass
    bare = host.removeprefix("ssh://")
    if "@" in bare or not bare:
        return False
    user = _login_user(host)
    return not user or user == getpass.getuser()


def with_user(host: str, user: str) -> str:
    if host.startswith("ssh://"):
        return f"ssh://{user}@{host.removeprefix('ssh://')}"
    return f"{user}@{host}"


def key_login_check(host: str) -> tuple[str, str]:
    """Try key login only (never a password, even one remembered for host).
    Returns ("ok", ""), ("denied", why the key wasn't accepted) or ("error", message)."""
    argv = ["ssh", "-v", *auth.ssh_opts(host, key_only=True), host, "true"]
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=25, env=host_env())
    except subprocess.TimeoutExpired:
        return "error", unreachable(host)
    except FileNotFoundError:
        return "error", _("`{program}` is not installed on this computer.").format(
            program="ssh")
    err = proc.stderr.decode(errors="replace")
    if proc.returncode == 0:
        return "ok", ""
    if not ssh_auth_failed(err):
        return "error", friendly_ssh_error(err, host)

    user = _login_user(host)
    methods = re.findall(r"Authentications that can continue: (\S+)", err)
    offered = re.findall(r"Offering public key: (\S+)", err)
    if methods and "publickey" not in methods[-1].split(","):
        if user:
            return "denied", _("The host doesn't allow key login as “{user}”, only a "
                               "password.").format(user=user)
        return "denied", _("The host doesn't allow key login, only a password.")
    if not offered:
        return "denied", _("No SSH key on this computer was offered. Obour can create one and "
                           "copy it to the host.")
    key = os.path.basename(offered[-1])
    if user:
        why = _("The host didn't accept your key ({key}) when logging in as “{user}”.").format(
            key=key, user=user)
    else:
        why = _("The host didn't accept your key ({key}).").format(key=key)
    why += " " + _("It isn't in that user's ~/.ssh/authorized_keys, or the host ignores it "
                   "because the permissions of ~/.ssh on the host are too open.")
    if user and "@" not in host:
        why += " " + _("If you copied it for a different user, write the host as "
                       "that user@{host}.").format(host=host)
    return "denied", why


def key_login_works(host: str) -> bool:
    return key_login_check(host)[0] == "ok"


def login_with_password(host: str, password: str) -> None:
    """Check the password and keep it in memory for this Obour session."""
    try:
        proc = _run(ssh_argv(host, password=password) + ["true"], host,
                    password=password, timeout=30)
    except subprocess.TimeoutExpired:
        raise RemoteError(unreachable(host))
    if proc.returncode != 0:
        raise _password_error(proc)
    auth.remember_password(host, password)


def setup_key_login(host: str, password: str) -> str:
    """Copy the local public key to host using password, then confirm key login works.
    Returns a short summary for the user."""
    pub, created = auth.ensure_key()
    with open(pub, encoding="utf-8") as f:
        key = f.read().strip()
    try:
        proc = _run(ssh_argv(host, password=password) + ["sh", "-c", shlex.quote(_INSTALL_KEY)],
                    host, input_text=key + "\n", password=password, timeout=40)
    except subprocess.TimeoutExpired:
        raise RemoteError(unreachable(host))
    if proc.returncode != 0:
        raise _password_error(proc)

    auth.remember_password(host, password)  # keeps it as the sudo prefill
    auth.forget_password(host)              # …but SSH itself must now use the key
    if key_login_works(host):
        return ((_("Created a new SSH key and set up key login for {host}.") if created
                 else _("Key login set up for {host}.")).format(host=host)
                + " " + _("No password needed next time."))
    auth.remember_password(host, password)
    raise RemoteError(_("Your key was copied, but {host} still asks for a password (it may not "
                        "allow key login). Obour will use your password until it quits.").format(
                            host=host))


# ---------------------------------------------------------------- remote app scan

_SCAN_SCRIPT = r"""
import glob, json, os, re, shutil, sys
dirs = [os.path.expanduser("~/.local/share/applications"),
        "/usr/local/share/applications", "/usr/share/applications",
        os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
        "/var/lib/flatpak/exports/share/applications",
        "/var/lib/snapd/desktop/applications"]
seen = {}
for d in dirs:
    for p in sorted(glob.glob(os.path.join(d, "*.desktop"))):
        appid = os.path.basename(p)[:-8]
        if appid in seen:
            continue
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        m = re.search(r"\[Desktop Entry\](.*?)(?:\n\[|\Z)", txt, re.S)
        if not m:
            continue
        sec = m.group(1)
        def get(k):
            mm = re.search(r"^%s\s*=\s*(.*)$" % re.escape(k), sec, re.M)
            return mm.group(1).strip() if mm else ""
        if get("Type") != "Application":
            continue
        if "true" in (get("NoDisplay").lower(), get("Hidden").lower(), get("Terminal").lower()):
            continue
        tryexec = get("TryExec")
        if tryexec and not shutil.which(tryexec):
            continue
        cmd = get("Exec")
        if not cmd:
            continue
        seen[appid] = {"id": appid, "name": get("Name") or appid, "exec": cmd,
                       "icon": get("Icon"), "comment": get("Comment")}
json.dump(sorted(seen.values(), key=lambda a: a["name"].lower()), sys.stdout)
"""


@dataclass
class RemoteApp:
    id: str
    name: str
    exec: str
    icon: str = ""       # icon name (or path) from the remote .desktop file
    comment: str = ""
    icon_path: str = ""  # local cached copy, filled in by fetch_icons()


def scan_apps(host: str) -> list[RemoteApp]:
    try:
        proc = _run(ssh_argv(host) + ["python3", "-"], host, input_text=_SCAN_SCRIPT, timeout=45)
    except subprocess.TimeoutExpired:
        raise RemoteError(_("Timed out while scanning applications on the host."))
    if proc.returncode == 127:
        raise RemoteError(_("python3 is not installed on the host, and the app browser needs "
                            "it. You can still add apps by hand."))
    if proc.returncode != 0:
        raise RemoteError(friendly_ssh_error(proc.stderr.decode(errors="replace"), host))
    try:
        data = json.loads(proc.stdout.decode(errors="replace"))
    except ValueError:
        raise RemoteError(_("Could not read the app list sent by the host."))
    return [RemoteApp(**d) for d in data]


# ---------------------------------------------------------------- icon fetching

_ICON_SCRIPT = r"""
import io, os, re, sys, tarfile
wanted = set(sys.argv[1:])
found = {}
def score(path):
    if path.endswith(".svg"):
        return 1000
    m = re.search(r"/(\d+)x\d+(?:@\d)?/", path)
    if m:
        n = int(m.group(1))
        return n if n <= 256 else 512 - n
    return 48
def consider(name, path):
    if name in wanted and (name not in found or score(path) > score(found[name])):
        found[name] = path
for n in list(wanted):
    if n.startswith("/") and os.path.isfile(n):
        found[n] = n
roots = []
for base in ["/usr/share/icons", "/usr/local/share/icons",
             os.path.expanduser("~/.local/share/icons"),
             "/var/lib/flatpak/exports/share/icons",
             os.path.expanduser("~/.local/share/flatpak/exports/share/icons")]:
    for theme in ("hicolor", "Adwaita", "breeze", "Papirus", "Yaru"):
        roots.append(os.path.join(base, theme))
for root in roots:
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            stem, ext = os.path.splitext(fn)
            if ext in (".png", ".svg"):
                consider(stem, os.path.join(dirpath, fn))
for d in ("/usr/share/pixmaps",):
    if os.path.isdir(d):
        for fn in os.listdir(d):
            stem, ext = os.path.splitext(fn)
            if ext in (".png", ".svg") and stem not in found:
                consider(stem, os.path.join(d, fn))
tar = tarfile.open(fileobj=sys.stdout.buffer, mode="w|")
for name, path in found.items():
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        continue
    if data.startswith(b"\x89PNG"):
        ext = ".png"
    elif b"<svg" in data[:4096]:
        ext = ".svg"
    else:
        continue
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    info = tarfile.TarInfo(safe + ext)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))
tar.close()
"""


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def icon_cache_dir(host: str) -> str:
    return os.path.join(CACHE_DIR, "icons", _safe_name(host))


def _cached_icon(host: str, name: str) -> str | None:
    d = icon_cache_dir(host)
    for ext in (".svg", ".png"):
        p = os.path.join(d, _safe_name(name) + ext)
        if os.path.exists(p):
            return p
    return None


def fetch_icons(host: str, names: list[str]) -> dict[str, str]:
    """Return {icon name: local file}, downloading icons not yet cached."""
    result: dict[str, str] = {}
    missing: list[str] = []
    for name in dict.fromkeys(n for n in names if n):
        cached = _cached_icon(host, name)
        if cached:
            result[name] = cached
        else:
            missing.append(name)
    if not missing:
        return result

    argv = ssh_argv(host) + ["python3", "-"] + [shlex.quote(n) for n in missing]
    try:
        proc = _run(argv, host, input_text=_ICON_SCRIPT, timeout=120)
    except (subprocess.TimeoutExpired, RemoteError):
        return result
    if proc.returncode != 0 or not proc.stdout:
        return result

    dest = icon_cache_dir(host)
    os.makedirs(dest, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r|") as tar:
            for member in tar:
                if (not member.isfile() or "/" in member.name
                        or member.name.startswith(".")):
                    continue
                data = tar.extractfile(member).read()
                with open(os.path.join(dest, member.name), "wb") as f:
                    f.write(data)
    except tarfile.TarError:
        return result

    for name in missing:
        cached = _cached_icon(host, name)
        if cached:
            result[name] = cached
    return result
