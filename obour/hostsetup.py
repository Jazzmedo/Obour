"""What a host needs for Obour, and installing what's missing with its package manager."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass

from . import auth
from .i18n import _
from .remote import RemoteError, friendly_ssh_error, ssh_argv

APT, DNF, PACMAN = "apt", "dnf", "pacman"


@dataclass(frozen=True)
class Requirement:
    key: str                         # capability flag from remote.probe_caps
    title: str
    purpose: str
    packages: dict[str, tuple[str, ...]]
    needed_if: str = ""              # only needed when this flag is set (e.g. Qt 5 present)
    alternative: str = ""            # another flag that satisfies it just as well
    # Only asked for when this computer's desktop (look.Look.tags) or file sharing
    # needs it; "gtk-qt" rows are also used when the desktop is unknown.
    tag: str = ""


REQUIREMENTS = (
    # Translated when this module is first imported; i18n.setup() runs before that.
    Requirement("waypipe", "waypipe", _("Shows Wayland apps (Firefox, GTK 4, Qt 6…)"),
                {APT: ("waypipe",), DNF: ("waypipe",), PACMAN: ("waypipe",)}),
    Requirement("xauth", "xauth", _("Shows X11 apps"),
                {APT: ("xauth",), DNF: ("xorg-x11-xauth",), PACMAN: ("xorg-xauth",)}),
    Requirement("libpulse", _("PulseAudio client library"),
                _("Plays sound here. Works with PipeWire or PulseAudio on this computer"),
                {APT: ("libpulse0",), DNF: ("pulseaudio-libs",), PACMAN: ("libpulse",)}),
    Requirement("alsa-pulse", _("ALSA sound plugin"),
                _("Plays sound here from apps that use ALSA directly"),
                {APT: ("libasound2-plugins",), DNF: ("alsa-plugins-pulseaudio",),
                 PACMAN: ("alsa-plugins",)}, tag="audio"),
    Requirement("dbus-run-session", _("D-Bus session tool"),
                _("Opens apps as separate windows instead of reusing a copy running on the "
                  "host"),
                {APT: ("dbus",), DNF: ("dbus-daemon",), PACMAN: ("dbus",)}),
    Requirement("python3", "Python 3", _("Lists the host's apps in the app browser"),
                {APT: ("python3",), DNF: ("python3",), PACMAN: ("python",)}),
    Requirement("qt5-kvantum", _("Dark style for Qt 5 apps"),
                _("Dark mode for Qt 5 apps such as VLC"),
                # adwaita-qt is gone from Fedora 44 and Arch; Kvantum is in both
                {APT: ("adwaita-qt",), DNF: ("kvantum-qt5",), PACMAN: ("kvantum-qt5",)},
                needed_if="qt5", alternative="qt5-adwaita", tag="gtk-qt"),
    Requirement("qt6-gtk", _("GTK theme for Qt 6 apps"), _("Light/dark style for Qt 6 apps"),
                {APT: ("qt6-gtk-platformtheme",), DNF: ("qt6-qtbase-gui",), PACMAN: ("qt6-base",)},
                needed_if="qt6", tag="gtk-qt"),
    Requirement("qt5-gtk", _("GTK theme for Qt 5 apps"),
                _("Fonts and file dialogs for Qt 5 apps"),
                {APT: ("qt5-gtk-platformtheme",), DNF: ("qt5-qtbase-gui",), PACMAN: ("qt5-base",)},
                needed_if="qt5", tag="gtk-qt"),
    # Desktop-specific: only the rows for this computer's desktop are shown.
    Requirement("qt6ct", "qt6ct", _("Your colors, icons and fonts in Qt 6 apps"),
                {APT: ("qt6ct",), DNF: ("qt6ct",), PACMAN: ("qt6ct",)},
                needed_if="qt6", tag="qt6ct"),
    Requirement("qt5ct", "qt5ct", _("Your colors, icons and fonts in Qt 5 apps"),
                {APT: ("qt5ct",), DNF: ("qt5ct",), PACMAN: ("qt5ct",)},
                needed_if="qt5", tag="qt5ct"),
    Requirement("qt-kde", _("KDE Plasma integration"), _("Your KDE colors and fonts in Qt apps"),
                {APT: ("plasma-integration",), DNF: ("plasma-integration",),
                 PACMAN: ("plasma-integration",)},
                needed_if="qt6", tag="kde"),
    Requirement("breeze", "Breeze", _("The Breeze style for Qt apps"),
                {APT: ("breeze",), DNF: ("plasma-breeze",), PACMAN: ("breeze",)},
                needed_if="qt6", tag="breeze"),
    Requirement("qt-lxqt", _("LXQt Qt plugin"), _("Your LXQt colors and icons in Qt apps"),
                {APT: ("lxqt-qtplugin",), DNF: ("lxqt-qtplugin",), PACMAN: ("lxqt-qtplugin",)},
                needed_if="qt6", tag="lxqt"),
    Requirement("kvantum", "Kvantum", _("Your Kvantum style in Qt apps"),
                {APT: ("qt-style-kvantum",), DNF: ("kvantum",), PACMAN: ("kvantum",)},
                needed_if="qt6", tag="kvantum"),
    Requirement("breeze-icons", _("Breeze icons"),
                _("Icons your icon theme borrows from Breeze"),
                {APT: ("breeze-icon-theme",), DNF: ("breeze-icon-theme",), PACMAN: ("breeze-icons",)},
                tag="breeze-icons"),
    Requirement("dconf", _("dconf tools"), _("Your GTK settings on hosts without dconf"),
                {APT: ("dconf-cli",), DNF: ("dconf",), PACMAN: ("dconf",)}, tag="dconf"),
    Requirement("sshfs", "sshfs", _("Copy and paste files between the computers"),
                {APT: ("sshfs",), DNF: ("fuse-sshfs",), PACMAN: ("sshfs",)}, tag="sshfs"),
)

_FAMILIES = (
    (APT, ("debian", "ubuntu", "linuxmint", "pop", "elementary", "zorin", "kali", "raspbian")),
    (DNF, ("fedora", "rhel", "centos", "rocky", "almalinux", "nobara")),
    (PACMAN, ("arch", "manjaro", "endeavouros", "cachyos", "garuda")),
)

_INSTALL = {
    APT: "export DEBIAN_FRONTEND=noninteractive; apt-get update -q && "
         "apt-get install -y -q --no-install-recommends {pkgs}",
    DNF: "dnf install -y {pkgs}",
    PACMAN: "pacman -S --needed --noconfirm {pkgs}",
}


def package_manager(caps: dict) -> str | None:
    ids = [caps.get("os-id", "")] + caps.get("os-like", "").split()
    for os_id in ids:
        for manager, names in _FAMILIES:
            if os_id in names:
                return manager
    return None


@dataclass
class Check:
    requirement: Requirement
    ok: bool
    packages: tuple[str, ...]        # empty when ok, or when no package is known


def local_tags(host: str | None = None, lk=None) -> set[str]:
    """What this computer's desktop needs on hosts (lk: a look.Look), plus "sshfs"
    when files may be shared with host. Uses host's settings (see config.resolve)."""
    from . import look
    from .config import resolve
    settings = resolve(host or "").settings
    tags = {"gtk-qt"}           # light/dark: the GTK bridge and a dark Qt 5 style
    if settings.desktop_theme:
        try:
            tags = (lk or look.collect()).tags()
        except Exception:
            pass
        if not shutil.which("dconf"):
            tags.add("dconf")   # the look's dconf database is then compiled on the host
    if settings.file_sharing != "deny":
        tags.add("sshfs")
    if settings.audio:
        tags.add("audio")
    return tags


def check(caps: dict, tags: set[str] | None = None) -> list[Check]:
    """The requirements that apply to this host (and this computer's desktop, tags),
    with their status."""
    manager = package_manager(caps)
    tags = {"gtk-qt"} if tags is None else tags
    result = []
    for req in REQUIREMENTS:
        if req.needed_if and not caps.get(req.needed_if):
            continue
        if req.tag and req.tag not in tags:
            continue
        ok = bool(caps.get(req.key) or (req.alternative and caps.get(req.alternative)))
        packages = () if ok else req.packages.get(manager, ())
        result.append(Check(req, ok, packages))
    return result


def missing_packages(checks: list[Check]) -> list[str]:
    return list(dict.fromkeys(p for c in checks if not c.ok for p in c.packages))


def install_command(caps: dict, packages: list[str]) -> str:
    """The command shown to the user and run (as root) on the host."""
    manager = package_manager(caps)
    if manager is None:
        raise RemoteError(_("Obour doesn't know this host's package manager. "
                            "Install the missing tools on the host yourself."))
    return _INSTALL[manager].format(pkgs=" ".join(shlex.quote(p) for p in packages))


def needs_sudo_password(caps: dict) -> bool:
    return not (caps.get("root") or caps.get("sudo-nopass"))


def install(host: str, caps: dict, packages: list[str], sudo_password: str | None,
            on_line, cancel: threading.Event | None = None) -> None:
    """Install packages on host, streaming output to on_line(text). Raises RemoteError."""
    command = install_command(caps, packages)
    if caps.get("root"):
        remote = ["sh", "-c", shlex.quote(command)]
        stdin = b""
    elif not caps.get("sudo"):
        raise RemoteError(_("sudo isn't installed on the host, so Obour can't install "
                            "packages. Log in as root, or install them yourself."))
    elif sudo_password is None:
        remote = ["sudo", "-n", "sh", "-c", shlex.quote(command)]
        stdin = b""
    else:
        # -S reads the password from stdin; the empty prompt keeps it out of the log.
        remote = ["sudo", "-S", "-p", "''", "sh", "-c", shlex.quote(command)]
        stdin = sudo_password.encode() + b"\n"

    on_line("  $ " + command)
    try:
        proc = subprocess.Popen(ssh_argv(host) + remote, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=auth.ssh_env(host), start_new_session=True)
    except FileNotFoundError:
        raise RemoteError(_("`{program}` is not installed on this computer.").format(
            program="ssh"))
    proc.stdin.write(stdin)
    proc.stdin.close()
    if cancel is not None:
        threading.Thread(target=lambda: cancel.wait() and proc.poll() is None and proc.terminate(),
                         daemon=True).start()
    tail: list[str] = []
    for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line:
            tail = (tail + [line])[-20:]
            on_line(line)
    rc = proc.wait()
    if cancel is not None:
        cancel.set()
    if rc == 0:
        return
    text = "\n".join(tail).lower()
    if "incorrect password" in text or "sorry, try again" in text or "no password was provided" in text:
        raise RemoteError(_("Wrong password for sudo on the host."), code="sudo-password")
    if "not in the sudoers" in text or "is not allowed to" in text:
        raise RemoteError(_("Your user on the host isn't allowed to use sudo. Ask an "
                            "administrator to install the packages."), code="sudo-denied")
    if "a password is required" in text:
        raise RemoteError(_("sudo on the host needs your password."), code="sudo-password")
    if rc == 255:
        raise RemoteError(friendly_ssh_error("\n".join(tail), host))
    if "could not get lock" in text or "waiting for cache lock" in text:
        raise RemoteError(_("Another program on the host is installing software. Try again "
                            "later."))
    raise RemoteError(_("Installing failed (exit code {code}). See the log for details.").format(
        code=rc))
