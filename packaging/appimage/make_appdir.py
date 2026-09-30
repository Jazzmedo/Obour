#!/usr/bin/env python3
"""Assemble the Obour AppDir from the build container's system packages.

Bundles: Python (stdlib), PyGObject, GTK 4, libadwaita, their libraries,
typelibs, gdk-pixbuf loaders, GSettings schemas and the Adwaita icons.
Leaves to the host: glibc, graphics drivers (GL/EGL/DRM), X11/Wayland client
libraries, fonts stack, libstdc++ — these must match the running system."""

from __future__ import annotations

import compileall
import glob
import os
import re
import shutil
import subprocess
import sys
import sysconfig

APPDIR = os.path.abspath(sys.argv[1])
SRC = "/src"
USR = os.path.join(APPDIR, "usr")
LIB = os.path.join(USR, "lib")
SHARE = os.path.join(USR, "share")
APP = os.path.join(SHARE, "obour")
MULTIARCH = sysconfig.get_config_var("MULTIARCH")
SYSLIB = f"/usr/lib/{MULTIARCH}"
PYVER = f"{sys.version_info.major}.{sys.version_info.minor}"
APP_ID = "io.github.Jazzmedo.Obour"

# Provided by every desktop system; bundling them breaks drivers, fonts or libc.
EXCLUDE = re.compile(r"^(" + "|".join([
    r"ld-linux.*", r"libc\.so.*", r"libm\.so.*", r"libmvec\.so.*", r"libdl\.so.*",
    r"libpthread\.so.*", r"librt\.so.*", r"libresolv\.so.*", r"libutil\.so.*",
    r"libanl\.so.*", r"libnsl\.so.*", r"libBrokenLocale\.so.*", r"libthread_db\.so.*",
    r"libgcc_s\.so.*", r"libstdc\+\+\.so.*",
    r"libGL\.so.*", r"libGLX.*", r"libEGL\.so.*", r"libGLdispatch\.so.*", r"libOpenGL\.so.*",
    r"libGLES.*", r"libdrm.*", r"libgbm\.so.*",
    r"libwayland-.*", r"libX11.*", r"libxcb.*",
    r"libfontconfig\.so.*", r"libfreetype\.so.*", r"libharfbuzz\.so.*",
]) + r")$")

SKIP_STDLIB = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3",
               "pydoc_data", "__pycache__", "sitecustomize.py", "turtle.py"}


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def copy(src: str, dst: str) -> str:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=True)
    return dst


def set_rpath(path: str, rpath: str) -> None:
    subprocess.run(["patchelf", "--set-rpath", rpath, path], check=True)


def ldd_deps(path: str) -> set[str]:
    out = subprocess.run(["ldd", path], capture_output=True, text=True).stdout
    deps = set()
    for line in out.splitlines():
        m = re.match(r"\s*(\S+) => (/\S+)", line)
        if m and not EXCLUDE.match(m.group(1)):
            deps.add(os.path.realpath(m.group(2)))
            deps_name[os.path.realpath(m.group(2))] = m.group(1)
        elif "not found" in line:
            sys.exit(f"missing dependency for {path}: {line.strip()}")
    return deps


deps_name: dict[str, str] = {}


def main() -> None:
    shutil.rmtree(APPDIR, ignore_errors=True)
    elf_roots: list[tuple[str, str]] = []  # (bundled path, rpath)

    log("python")
    python = copy(f"/usr/bin/python{PYVER}", os.path.join(USR, "bin", f"python{PYVER}"))
    os.symlink(f"python{PYVER}", os.path.join(USR, "bin", "python3"))
    elf_roots.append((python, "$ORIGIN/../lib"))

    stdlib_src = f"/usr/lib/python{PYVER}"
    stdlib_dst = os.path.join(LIB, f"python{PYVER}")
    shutil.copytree(stdlib_src, stdlib_dst, symlinks=False,
                    ignore=lambda d, names: [n for n in names
                                             if n in SKIP_STDLIB or n.startswith("config-")
                                             or n.startswith("_tkinter")])
    for so in glob.glob(os.path.join(stdlib_dst, "lib-dynload", "*.so")):
        elf_roots.append((so, "$ORIGIN/../.."))

    log("obour + PyGObject")
    shutil.copytree(os.path.join(SRC, "obour"), os.path.join(APP, "obour"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    copy(os.path.join(SRC, "bin", "obour"), os.path.join(APP, "bin", "obour"))
    shutil.copytree("/usr/lib/python3/dist-packages/gi", os.path.join(APP, "gi"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    for so in glob.glob(os.path.join(APP, "gi", "*.so")):
        elf_roots.append((so, "$ORIGIN/../../../lib"))

    log("GTK, libadwaita and typelibs")
    for name in ("libgtk-4.so.1", "libadwaita-1.so.0", "libgirepository-1.0.so.1"):
        elf_roots.append((copy(os.path.join(SYSLIB, name), os.path.join(LIB, name)), "$ORIGIN"))
    typelib_dir = os.path.join(LIB, "girepository-1.0")
    for t in glob.glob(os.path.join(SYSLIB, "girepository-1.0", "*.typelib")):
        copy(t, os.path.join(typelib_dir, os.path.basename(t)))
    # dconf backend: lets GSettings read the desktop's settings (cursor, fonts…)
    # instead of built-in defaults.
    dconf = os.path.join(SYSLIB, "gio", "modules", "libdconfsettings.so")
    elf_roots.append((copy(dconf, os.path.join(LIB, "gio", "modules", "libdconfsettings.so")),
                      "$ORIGIN/../.."))

    log("gdk-pixbuf loaders")
    pixbuf_dst = os.path.join(LIB, "gdk-pixbuf-2.0", "2.10.0")
    for so in glob.glob(os.path.join(SYSLIB, "gdk-pixbuf-2.0", "2.10.0", "loaders", "*.so")):
        elf_roots.append((copy(so, os.path.join(pixbuf_dst, "loaders", os.path.basename(so))),
                          "$ORIGIN/../../.."))

    log("shared libraries")
    needed: set[str] = set()
    for path, _ in elf_roots:
        needed |= ldd_deps(path)
    bundled = {os.path.basename(p) for p, _ in elf_roots}
    for real in sorted(needed):
        soname = deps_name[real]
        if soname in bundled:
            continue
        dst = copy(real, os.path.join(LIB, soname))
        bundled.add(soname)
        set_rpath(dst, "$ORIGIN")
    for path, rpath in elf_roots:
        set_rpath(path, rpath)
    log(f"{len(needed)} libraries bundled")

    loaders = subprocess.run(
        [os.path.join(SYSLIB, "gdk-pixbuf-2.0", "gdk-pixbuf-query-loaders"),
         *sorted(glob.glob(os.path.join(pixbuf_dst, "loaders", "*.so")))],
        capture_output=True, text=True, check=True).stdout
    with open(os.path.join(pixbuf_dst, "loaders.cache.in"), "w") as f:
        f.write(loaders.replace(APPDIR, "@APPDIR@"))

    log("MIME database (gdk-pixbuf needs it to recognise SVG icons)")
    mime_dst = os.path.join(SHARE, "mime")
    for f in glob.glob("/usr/share/mime/*"):
        if os.path.isfile(f):
            copy(f, os.path.join(mime_dst, os.path.basename(f)))

    log("schemas and icons")
    schemas = os.path.join(SHARE, "glib-2.0", "schemas")
    for xml in glob.glob("/usr/share/glib-2.0/schemas/*.gschema.xml"):
        copy(xml, os.path.join(schemas, os.path.basename(xml)))
    subprocess.run(["glib-compile-schemas", schemas], check=True)
    shutil.copytree("/usr/share/icons/Adwaita", os.path.join(SHARE, "icons", "Adwaita"),
                    ignore=shutil.ignore_patterns("cursors"))
    copy("/usr/share/icons/hicolor/index.theme",
         os.path.join(SHARE, "icons", "hicolor", "index.theme"))
    icon = os.path.join(SRC, "data", f"{APP_ID}.svg")
    copy(icon, os.path.join(SHARE, "icons", "hicolor", "scalable", "apps", f"{APP_ID}.svg"))
    copy(icon, os.path.join(APPDIR, f"{APP_ID}.svg"))
    os.symlink(f"{APP_ID}.svg", os.path.join(APPDIR, ".DirIcon"))

    log("desktop entry, AppStream metadata and AppRun")
    with open(os.path.join(SRC, "data", f"{APP_ID}.desktop")) as f:
        entry = f.read()
    for dst in (os.path.join(APPDIR, f"{APP_ID}.desktop"),
                os.path.join(SHARE, "applications", f"{APP_ID}.desktop")):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w") as f:
            f.write(entry)
    copy(os.path.join(SRC, "data", f"{APP_ID}.metainfo.xml"),
         os.path.join(SHARE, "metainfo", f"{APP_ID}.appdata.xml"))
    apprun = copy(os.path.join(SRC, "packaging", "appimage", "AppRun"),
                  os.path.join(APPDIR, "AppRun"))
    os.chmod(apprun, 0o755)

    log("precompiling Python")
    for d in (stdlib_dst, APP):
        compileall.compile_dir(d, quiet=1, workers=0)

    subprocess.run(["du", "-sh", APPDIR])


if __name__ == "__main__":
    main()
