"""Environment for programs Obour starts on this computer (ssh, waypipe, gdbus…).

Inside the AppImage, AppRun points GObject-introspection, gdk-pixbuf and GIO at
bundled files and keeps the user's original values in OBOUR_ORIG_<VAR>. Child
programs use the host's own libraries, so they must get the original values back."""

from __future__ import annotations

import os

BUNDLE_VARS = ("GI_TYPELIB_PATH", "GDK_PIXBUF_MODULE_FILE", "GIO_MODULE_DIR", "XDG_DATA_DIRS",
               "GTK_EXE_PREFIX", "GTK_DATA_PREFIX")


def in_appimage() -> bool:
    return "OBOUR_APPDIR" in os.environ


def host_env() -> dict[str, str] | None:
    """None (inherit) outside the AppImage; otherwise the user's original environment."""
    if not in_appimage():
        return None
    env = dict(os.environ)
    for var in BUNDLE_VARS:
        original = env.pop(f"OBOUR_ORIG_{var}", None)
        if original is None:
            env.pop(var, None)
        else:
            env[var] = original
    for key in [k for k in env if k.startswith("OBOUR_")]:
        del env[key]
    return env
