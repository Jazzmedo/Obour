"""Local application-menu entries for saved launchers."""

from __future__ import annotations

import glob
import os
import re
import shutil

from .config import DATA_DIR, Launcher
from .i18n import _

APPLICATIONS_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "applications"
)
ICON_DIR = os.path.join(DATA_DIR, "icons")
# Inside an AppImage, $APPIMAGE is the .AppImage file itself; the mount point changes per run.
# Packages set OBOUR_BIN to a path that survives updates (/usr/bin/obour, or plain
# "obour" on Nix, whose store paths change with every version).
OBOUR_BIN = (os.environ.get("APPIMAGE") or os.environ.get("OBOUR_BIN") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "bin", "obour")))


def entry_path(launcher_id: str) -> str:
    return os.path.join(APPLICATIONS_DIR, f"obour-{launcher_id}.desktop")


def _value(s: str) -> str:
    return " ".join(s.split())


def _exec_arg(arg: str) -> str:
    if not any(c in arg for c in ' \t"\'\\><~|&;$*?#()`'):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'.replace("\\", "\\\\")


def short_host(host: str) -> str:
    return host.rsplit("@", 1)[-1]


MENU_NAME_FIELDS = {
    "name": "the app's name",
    "host": "the computer (without the user)",
    "user": "the user name on it",
    "address": "user@computer, as saved",
}


def menu_name(launcher: Launcher, template: str | None = None) -> str:
    """The app-menu name for launcher, from a template such as "{name} ({host})"
    (by default the app's own, else its host's, else Obour's). Unknown {fields} are
    kept as typed; brackets left empty are dropped."""
    if template is None:
        from .config import resolve_launcher
        template = resolve_launcher(launcher).settings.menu_name
    user, _at, host = launcher.host.removeprefix("ssh://").rpartition("@")
    values = {"name": launcher.name, "host": host, "user": user, "address": launcher.host}
    text = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    return " ".join(text.split()) or launcher.name


def persist_icon(launcher: Launcher) -> str:
    """Copy a file-based icon into Obour's data dir so cache cleanups don't break it."""
    icon = launcher.icon
    if not (icon and os.path.isabs(icon) and os.path.isfile(icon)):
        return icon
    if os.path.dirname(icon) == ICON_DIR:
        return icon
    os.makedirs(ICON_DIR, exist_ok=True)
    remove_icon(launcher.id)
    dest = os.path.join(ICON_DIR, launcher.id + os.path.splitext(icon)[1].lower())
    shutil.copyfile(icon, dest)
    return dest


def remove_icon(launcher_id: str) -> None:
    for p in glob.glob(os.path.join(ICON_DIR, launcher_id + ".*")):
        try:
            os.remove(p)
        except OSError:
            pass


def install(launcher: Launcher, template: str | None = None) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={_value(menu_name(launcher, template))}",
        "Comment=" + _value(_("Runs on {host} via Obour").format(host=launcher.host)),
        f"Exec={_exec_arg(OBOUR_BIN)} launch {launcher.id}",
        f"Icon={_value(launcher.icon) or 'application-x-executable'}",
        "Terminal=false",
        "Categories=Network;RemoteAccess;",
        f"Keywords=remote;obour;{_value(short_host(launcher.host))};",
        f"X-Obour-Id={launcher.id}",
    ]
    if launcher.app_id:
        lines.append(f"StartupWMClass={_value(launcher.app_id)}")
    os.makedirs(APPLICATIONS_DIR, exist_ok=True)
    path = entry_path(launcher.id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    return path


def remove(launcher_id: str) -> None:
    try:
        os.remove(entry_path(launcher_id))
    except FileNotFoundError:
        pass


def prune(saved_ids: set[str]) -> list[str]:
    """Remove menu entries (and their icons) made by Obour for apps that are no longer
    saved. Returns the removed ids."""
    removed = []
    for path in glob.glob(os.path.join(APPLICATIONS_DIR, "obour-*.desktop")):
        try:
            with open(path, encoding="utf-8") as f:
                m = re.search(r"^X-Obour-Id=(\S+)$", f.read(), re.M)
        except OSError:
            continue
        if m and m.group(1) not in saved_ids:
            remove(m.group(1))
            remove_icon(m.group(1))
            removed.append(m.group(1))
    return removed


def sync(launcher: Launcher, template: str | None = None) -> None:
    if launcher.in_menu:
        install(launcher, template)
    else:
        remove(launcher.id)
