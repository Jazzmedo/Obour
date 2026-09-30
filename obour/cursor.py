"""This computer's mouse cursor theme, so Obour and remote apps can use it too."""

from __future__ import annotations

import configparser
import os
import subprocess

from .hostenv import host_env


def _gsetting(key: str) -> str:
    try:
        proc = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", key],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=3, env=host_env())
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.decode(errors="replace").strip().strip("'")


def _kde_setting(key: str) -> str:
    path = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                        "kcminputrc")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
        return parser.get("Mouse", key, fallback="").strip()
    except (configparser.Error, OSError, UnicodeDecodeError):
        return ""


def local_cursor() -> tuple[str, int]:
    """(theme name, size) of this desktop's cursor; the name is "" if unknown."""
    theme = (os.environ.get("XCURSOR_THEME") or _kde_setting("cursorTheme")
             or _gsetting("cursor-theme"))
    size_text = (os.environ.get("XCURSOR_SIZE") or _kde_setting("cursorSize")
                 or _gsetting("cursor-size").removeprefix("int32 "))
    try:
        size = int(size_text)
    except ValueError:
        size = 24
    return theme, size if 8 <= size <= 256 else 24


def _icon_dirs() -> list[str]:
    home = os.path.expanduser("~")
    data_home = os.environ.get("XDG_DATA_HOME", os.path.join(home, ".local", "share"))
    env = host_env() or os.environ
    data_dirs = (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs = [os.path.join(data_home, "icons"), os.path.join(home, ".icons")]
    dirs += [os.path.join(d, "icons") for d in data_dirs if d]
    dirs += ["/usr/share/icons", "/usr/share/pixmaps"]
    return list(dict.fromkeys(dirs))


def theme_dir(theme: str) -> str | None:
    """The folder holding theme's cursors on this computer."""
    if not theme or "/" in theme or theme.startswith("."):
        return None
    for base in _icon_dirs():
        path = os.path.join(base, theme)
        if os.path.isdir(os.path.join(path, "cursors")):
            return path
    return None


_root_cursor_set: tuple[str, int] | None = None


def apply_x11_root_cursor(theme: str, size: int) -> bool:
    """Give the local X server's root window this cursor theme.

    Qt 5 (VLC 3…) sets no cursor of its own for the normal arrow, so its windows show
    the root window's cursor; under Wayland that's Xwayland's plain default unless
    someone sets it. Runs once per theme/size and Obour process."""
    global _root_cursor_set
    if not theme or _root_cursor_set == (theme, size):
        return True
    env = dict(host_env() or os.environ)
    if not env.get("DISPLAY"):
        return False
    env.update(XCURSOR_THEME=theme, XCURSOR_SIZE=str(size))
    try:
        ok = subprocess.run(["xsetroot", "-cursor_name", "left_ptr"], env=env, timeout=5,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    if ok:
        _root_cursor_set = (theme, size)
    return ok
