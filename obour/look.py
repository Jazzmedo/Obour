"""This computer's desktop look — colors, GTK and Qt settings, icons, fonts — so
remote apps can use a private copy of it on the host.

collect() gathers it; sync() copies it to a host. Nothing on the host changes except
~/.local/share/obour/look/<id> (settings) and additions to ~/.local/share/{icons,
themes,fonts}: apps started by Obour point XDG_CONFIG_HOME at an overlay of the
host's ~/.config with the look's entries on top (see launch.look_wrapper)."""

from __future__ import annotations

import configparser
import getpass
import glob
import hashlib
import io
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field

from . import auth
from .config import host_options, _update_host
from .cursor import _icon_dirs, local_cursor
from .hostenv import host_env
from .i18n import _

HOME = os.path.expanduser("~")
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
# Replaced on the host with the look folder's absolute path.
PLACEHOLDER = "@OBOUR_LOOK@"

DESKTOP_NAMES = {
    "dms": "DankMaterialShell", "noctalia": "Noctalia", "quickshell": "Quickshell",
    "kde": "KDE Plasma", "gnome": "GNOME", "cinnamon": "Cinnamon", "xfce": "Xfce",
    "lxqt": "LXQt", "generic": "",
}
_COMPOSITORS = {"hyprland": "Hyprland", "niri": "niri", "sway": "sway", "kwin_wayland": "KWin",
                "mutter": "Mutter", "labwc": "labwc", "river": "river", "wayfire": "Wayfire",
                "cosmic-comp": "COSMIC"}
_PLAIN_GTK_THEMES = {"", "Adwaita", "Adwaita-dark", "Default", "HighContrast",
                     "HighContrastInverse"}
_MAX_CSS_BYTES = 4 * 1024 * 1024
_MAX_FONT_FAMILY_BYTES = 30 * 1024 * 1024


def _run(argv: list[str], timeout: float = 4) -> str:
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout, env=host_env())
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.decode(errors="replace").strip() if proc.returncode == 0 else ""


def _processes() -> list[str]:
    """Command lines of this user's processes."""
    uid = os.getuid()
    result = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if args and args[0]:
            result.append(" ".join(a.decode(errors="replace") for a in args if a))
    return result


def _names(procs: list[str]) -> set[str]:
    return {os.path.basename(p.split(" ", 1)[0]).lower() for p in procs}


def detect_desktop(procs: list[str] | None = None) -> str:
    """dms | noctalia | quickshell | kde | gnome | cinnamon | xfce | lxqt | generic.
    Running shells come first: DMS and others set XDG_CURRENT_DESKTOP to KDE or GNOME
    so that Qt and portals behave."""
    procs = _processes() if procs is None else procs
    names = _names(procs)
    joined = "\n".join(procs).lower()
    if "dms" in names or "dankmaterialshell" in joined or "danklinux-shell" in joined:
        return "dms"
    if "noctalia" in joined:
        return "noctalia"
    if names & {"qs", "quickshell"}:
        return "quickshell"
    if "plasmashell" in names:
        return "kde"
    if "gnome-shell" in names:
        return "gnome"
    if names & {"cinnamon", "cinnamon-session"}:
        return "cinnamon"
    if names & {"xfce4-session", "xfce4-panel", "xfwm4"}:
        return "xfce"
    if names & {"lxqt-session", "lxqt-panel"}:
        return "lxqt"
    current = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":"
               + os.environ.get("XDG_SESSION_DESKTOP", "")).lower()
    for key, marks in (("kde", ("kde", "plasma")), ("gnome", ("gnome", "unity")),
                       ("cinnamon", ("cinnamon", "x-cinnamon")), ("xfce", ("xfce",)),
                       ("lxqt", ("lxqt",))):
        if any(m in current for m in marks):
            return key
    return "generic"


def compositor(procs: list[str] | None = None) -> str:
    names = _names(_processes() if procs is None else procs)
    for key, label in _COMPOSITORS.items():
        if key in names:
            return label
    return ""


def describe_desktop() -> str:
    """For the host setup dialog: "DankMaterialShell (Hyprland)"."""
    procs = _processes()
    name = DESKTOP_NAMES.get(detect_desktop(procs)) or _("Unknown desktop")
    comp = compositor(procs)
    return f"{name} ({comp})" if comp and comp not in name else name


# ---------------------------------------------------------------- reading settings

def _gsettings(schema: str, key: str) -> str:
    value = _run(["gsettings", "get", schema, key])
    return value.removeprefix("int32 ").strip("'\"") if value else ""


def _ini(path: str, section: str, key: str) -> str:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
        return parser.get(section, key, fallback="").strip()
    except (configparser.Error, OSError, UnicodeDecodeError):
        return ""


def _xfconf(prop: str) -> str:
    return _run(["xfconf-query", "-c", "xsettings", "-p", prop])


def _kde_app_colors(data: bytes) -> bytes:
    """kdeglobals, with its colors also chosen for KDE apps (Kate, Dolphin…).

    Outside Plasma they pick their colors from [UiSettings] ColorScheme, and use
    light ones when it's empty; set, they take kdeglobals' [Colors:*] (the named
    scheme's file isn't needed)."""
    text = data.decode("utf-8", "replace")
    if "[Colors:Window]" not in text or re.search(
            r"^\[UiSettings\][^\[]*^ColorScheme=\S", text, re.M | re.S):
        return data
    m = re.search(r"^\[General\][^\[]*^ColorScheme=([^\n]+)", text, re.M | re.S)
    name = m.group(1).strip() if m else "Obour"
    return (text.rstrip("\n") + f"\n\n[UiSettings]\nColorScheme={name}\n").encode()


def _kde_font(value: str) -> str:
    """kdeglobals "Noto Sans,10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1" -> "Noto Sans 10"."""
    parts = value.split(",")
    return f"{parts[0]} {parts[1]}" if len(parts) > 1 and parts[1].strip() else parts[0]


def session_env(name: str) -> str:
    """A variable from the desktop session (systemd user environment first: the
    AppImage or a menu launch may not carry it)."""
    for line in _run(["systemctl", "--user", "show-environment"]).splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip("$'\"")
    return (host_env() or os.environ).get(name, "")


@dataclass
class Look:
    desktop: str
    gtk_theme: str = ""
    icon_theme: str = ""
    font: str = ""
    mono_font: str = ""
    color_scheme: str = ""          # prefer-dark | prefer-light | default
    accent: str = ""                # GNOME 47+ accent-color name
    cursor: str = ""
    cursor_size: int = 24
    qt5: str = ""                   # QT_QPA_PLATFORMTHEME for Qt 5 / Qt 6 apps
    qt6: str = ""
    qt_style: str = ""              # "kvantum" when Kvantum is the Qt style
    # relative path under the look's config/ -> bytes
    files: dict[str, bytes] = field(default_factory=dict)
    icon_dirs: dict[str, str] = field(default_factory=dict)    # theme name -> folder
    gtk_theme_dirs: dict[str, str] = field(default_factory=dict)
    font_files: dict[str, list[str]] = field(default_factory=dict)  # family -> files

    @property
    def dark(self) -> bool:
        return self.color_scheme == "prefer-dark" or "dark" in self.gtk_theme.lower()

    def tags(self) -> set[str]:
        """What the host needs to show this look (see hostsetup.requirements)."""
        tags = {t for t in (self.qt5, self.qt6) if t in ("qt5ct", "qt6ct", "kde", "lxqt")}
        if self.qt_style == "kvantum":
            tags.add("kvantum")
        if not tags & {"kde", "lxqt", "qt6ct"}:
            tags.add("gtk-qt")
        if self.qt6 == "kde":
            tags.add("breeze")
        for path in self.icon_dirs.values():
            inherits = _ini(os.path.join(path, "index.theme"), "Icon Theme", "Inherits")
            if "breeze" in [t.strip().lower() for t in inherits.split(",")]:
                tags.add("breeze-icons")
        return tags

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for key in ("desktop", "gtk_theme", "icon_theme", "font", "mono_font", "color_scheme",
                    "accent", "cursor", "qt5", "qt6", "qt_style"):
            h.update(f"{key}={getattr(self, key)}\n".encode())
        for name in sorted(self.files):
            h.update(name.encode() + b"\0" + hashlib.sha256(self.files[name]).digest())
        return h.hexdigest()[:16]

    def data_items(self) -> list[tuple[str, str, str]]:
        """(kind, name, local path) of the big items: icons, themes, fonts."""
        items = [("icons", n, p) for n, p in self.icon_dirs.items()]
        items += [("themes", n, p) for n, p in self.gtk_theme_dirs.items()]
        items += [("fonts", n, "\n".join(f)) for n, f in self.font_files.items()]
        return items


def _gtk_values(desktop: str) -> dict[str, str]:
    ini3 = os.path.join(CONFIG_HOME, "gtk-3.0", "settings.ini")
    values = {"theme": "", "icons": "", "font": "", "mono": "", "scheme": "", "accent": ""}
    if desktop == "cinnamon":
        schema = "org.cinnamon.desktop.interface"
        values.update(theme=_gsettings(schema, "gtk-theme"), icons=_gsettings(schema, "icon-theme"),
                      font=_gsettings(schema, "font-name"))
    elif desktop == "xfce":
        values.update(theme=_xfconf("/Net/ThemeName"), icons=_xfconf("/Net/IconThemeName"),
                      font=_xfconf("/Gtk/FontName"), mono=_xfconf("/Gtk/MonospaceFontName"))
    elif desktop == "kde":
        kdeglobals = os.path.join(CONFIG_HOME, "kdeglobals")
        values.update(icons=_ini(kdeglobals, "Icons", "Theme"),
                      font=_kde_font(_ini(kdeglobals, "General", "font")),
                      mono=_kde_font(_ini(kdeglobals, "General", "fixed")))
    elif desktop == "lxqt":
        lxqt = os.path.join(CONFIG_HOME, "lxqt", "lxqt.conf")
        values.update(icons=_ini(lxqt, "General", "icon_theme"))
    schema = "org.gnome.desktop.interface"
    for key, gkey in (("theme", "gtk-theme"), ("icons", "icon-theme"), ("font", "font-name"),
                      ("mono", "monospace-font-name"), ("scheme", "color-scheme"),
                      ("accent", "accent-color")):
        if not values[key] and desktop not in ("kde", "lxqt", "xfce"):
            values[key] = _gsettings(schema, gkey)
    for key, ikey in (("theme", "gtk-theme-name"), ("icons", "gtk-icon-theme-name"),
                      ("font", "gtk-font-name")):
        if not values[key]:
            values[key] = _ini(ini3, "Settings", ikey)
    if not values["scheme"] or values["scheme"] == "default":
        prefer = _ini(ini3, "Settings", "gtk-application-prefer-dark-theme").lower()
        if prefer in ("1", "true") or "dark" in values["theme"].lower():
            values["scheme"] = "prefer-dark"
        elif not values["scheme"]:
            values["scheme"] = "default"
    return values


def _css_bundle(folder: str, prefix: str, files: dict[str, bytes]) -> None:
    """gtk.css and the local files it @imports (plus assets/), as regular files."""
    base = os.path.join(CONFIG_HOME, folder)
    total = 0
    todo = ["gtk.css"]
    seen: set[str] = set()
    while todo:
        name = todo.pop()
        path = os.path.normpath(os.path.join(base, name))
        if name in seen or not path.startswith(base + os.sep) or not os.path.isfile(path):
            continue
        seen.add(name)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue
        total += len(data)
        if total > _MAX_CSS_BYTES:
            break
        files[f"{prefix}/{name}"] = data
        for ref in re.findall(rb"""@import\s+(?:url\()?\s*["']([^"')]+)["']""", data):
            ref = ref.decode(errors="replace")
            if ref.startswith("file://"):
                ref = ref[7:]
            if os.path.isabs(ref):
                if not ref.startswith(base + os.sep):
                    continue
                ref = os.path.relpath(ref, base)
            todo.append(ref)
    assets = os.path.join(base, "assets")
    if files.get(f"{prefix}/gtk.css") is not None and os.path.isdir(assets):
        for root, _dirs, names in os.walk(assets):
            for n in names:
                p = os.path.join(root, n)
                try:
                    if os.path.getsize(p) > 512 * 1024:
                        continue
                    with open(p, "rb") as f:
                        files[f"{prefix}/{os.path.relpath(p, base)}"] = f.read()
                except OSError:
                    continue


def _settings_ini(folder: str, look: Look) -> bytes:
    """The user's settings.ini without local-only modules, with the look's values."""
    path = os.path.join(CONFIG_HOME, folder, "settings.ini")
    lines: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = [l.rstrip("\n") for l in f]
    except OSError:
        lines = ["[Settings]"]
    wanted = {"gtk-theme-name": look.gtk_theme, "gtk-icon-theme-name": look.icon_theme,
              "gtk-font-name": look.font, "gtk-cursor-theme-name": look.cursor,
              "gtk-application-prefer-dark-theme": "true" if look.dark else "false"}
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key == "gtk-modules":           # local modules aren't on the host
            continue
        if key in wanted:
            if wanted[key]:
                out.append(f"{key}={wanted.pop(key)}")
            else:
                wanted.pop(key)
                out.append(line)
            continue
        out.append(line)
    if not any(l.strip() == "[Settings]" for l in out):
        out.insert(0, "[Settings]")
    out += [f"{k}={v}" for k, v in wanted.items() if v]
    return ("\n".join(out) + "\n").encode()


def _qtct(name: str, look: Look) -> None:
    """qt5ct/qt6ct settings; the color scheme and style sheets are copied along and
    their paths pointed at the look folder on the host."""
    base = os.path.join(CONFIG_HOME, name)
    conf = os.path.join(base, f"{name}.conf")
    try:
        with open(conf, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return

    def relocate(path: str, sub: str) -> str:
        path = os.path.expanduser(path.strip().strip('"'))
        if not os.path.isfile(path):
            return path
        try:
            with open(path, "rb") as f:
                look.files[f"{name}/{sub}/{os.path.basename(path)}"] = f.read()
        except OSError:
            return path
        return f"{PLACEHOLDER}/config/{name}/{sub}/{os.path.basename(path)}"

    text = re.sub(r"^(color_scheme_path=)(.*)$",
                  lambda m: m.group(1) + relocate(m.group(2), "colors"), text, flags=re.M)
    text = re.sub(r"^(stylesheets=)(.*)$",
                  lambda m: m.group(1) + ", ".join(relocate(p, "qss") for p in m.group(2).split(",")
                                                   if p.strip() and "@Invalid" not in p)
                  if "@Invalid" not in m.group(2) else m.group(0), text, flags=re.M)
    look.files[f"{name}/{name}.conf"] = text.encode()


def _find_dir(kind: str, name: str) -> str | None:
    if not name or "/" in name or name.startswith("."):
        return None
    if kind == "icons":
        bases = [os.path.join(d) for d in _icon_dirs()]
    else:
        bases = [os.path.join(HOME, ".local", "share", "themes"), os.path.join(HOME, ".themes"),
                 "/usr/local/share/themes", "/usr/share/themes"]
    for base in bases:
        path = os.path.join(base, name)
        marker = "index.theme" if kind == "icons" else ""
        if os.path.isdir(path) and (not marker or os.path.isfile(os.path.join(path, marker))):
            return path
    return None


def _font_family(spec: str) -> str:
    """"Poppins Light 9" -> "Poppins" (the family fontconfig knows)."""
    spec = re.sub(r"\s+\d+(\.\d+)?$", "", spec.strip())
    if not spec:
        return ""
    family = _run(["fc-match", "-f", "%{family[0]}", spec])
    words = spec.split()
    # fc-match always answers; accept it only if it's (a prefix of) what was asked for
    for n in range(len(words), 0, -1):
        candidate = " ".join(words[:n])
        if family.lower() == candidate.lower():
            return family
    return ""


def _font_files(family: str) -> list[str]:
    files: dict[str, str] = {}
    for path in _run(["fc-list", "-f", "%{file}\n", family]).splitlines():
        name = os.path.basename(path)
        if path and name not in files and os.path.isfile(path):
            files[name] = path
    total = sum(os.path.getsize(p) for p in files.values())
    return sorted(files.values()) if 0 < total <= _MAX_FONT_FAMILY_BYTES else []


def collect() -> Look:
    procs = _processes()
    desktop = detect_desktop(procs)
    values = _gtk_values(desktop)
    cursor, size = local_cursor()
    look = Look(desktop=desktop, gtk_theme=values["theme"], icon_theme=values["icons"],
                font=values["font"], mono_font=values["mono"], color_scheme=values["scheme"],
                accent=values["accent"], cursor=cursor, cursor_size=size)

    # Qt: what this session tells Qt apps to use
    platform = session_env("QT_QPA_PLATFORMTHEME").split(";")[0].split(":")[0].lower()
    has5 = os.path.isfile(os.path.join(CONFIG_HOME, "qt5ct", "qt5ct.conf"))
    has6 = os.path.isfile(os.path.join(CONFIG_HOME, "qt6ct", "qt6ct.conf"))
    if desktop == "kde" or platform == "kde":
        look.qt6 = "kde"
    elif desktop == "lxqt" or platform == "lxqt":
        look.qt6 = "lxqt"
    elif platform in ("qt5ct", "qt6ct") or (desktop in ("dms", "noctalia", "quickshell")
                                            and (has5 or has6)):
        look.qt6 = "qt6ct" if has6 else ""
        look.qt5 = "qt5ct" if has5 else ""
    else:
        look.qt5 = look.qt6 = "gtk3"
    style = session_env("QT_STYLE_OVERRIDE").lower()
    kvantum_conf = os.path.join(CONFIG_HOME, "Kvantum", "kvantum.kvconfig")
    if "kvantum" in style or any("style=kvantum" in _ini_text(os.path.join(CONFIG_HOME, n, f"{n}.conf"))
                                 for n in ("qt5ct", "qt6ct")):
        look.qt_style = "kvantum"

    # config entries that replace the host's in the overlay
    for folder in ("gtk-3.0", "gtk-4.0"):
        _css_bundle(folder, folder, look.files)
        if os.path.isdir(os.path.join(CONFIG_HOME, folder)) or folder == "gtk-3.0":
            look.files[f"{folder}/settings.ini"] = _settings_ini(folder, look)
    if look.qt5 == "qt5ct" or look.qt6 == "qt6ct":
        for name in ("qt5ct", "qt6ct"):
            _qtct(name, look)
    path = os.path.join(CONFIG_HOME, "kdeglobals")
    if os.path.isfile(path):
        with open(path, "rb") as f:
            look.files["kdeglobals"] = _kde_app_colors(f.read())
    if look.qt6 == "lxqt":
        path = os.path.join(CONFIG_HOME, "lxqt", "lxqt.conf")
        if os.path.isfile(path):
            with open(path, "rb") as f:
                look.files["lxqt/lxqt.conf"] = f.read()
    if look.qt_style == "kvantum" and os.path.isfile(kvantum_conf):
        with open(kvantum_conf, "rb") as f:
            look.files["Kvantum/kvantum.kvconfig"] = f.read()
        theme = _ini(kvantum_conf, "General", "theme")
        folder = os.path.join(CONFIG_HOME, "Kvantum", theme) if theme else ""
        if folder and os.path.isdir(folder):
            for n in os.listdir(folder):
                p = os.path.join(folder, n)
                if os.path.isfile(p) and os.path.getsize(p) < 4 * 1024 * 1024:
                    with open(p, "rb") as f:
                        look.files[f"Kvantum/{theme}/{n}"] = f.read()

    # data: icons, a non-default GTK theme, fonts
    path = _find_dir("icons", look.icon_theme)
    if path and not path.startswith("/usr/share/icons/Adwaita"):
        look.icon_dirs[look.icon_theme] = path
    if look.gtk_theme not in _PLAIN_GTK_THEMES:
        path = _find_dir("themes", look.gtk_theme)
        if path:
            look.gtk_theme_dirs[look.gtk_theme] = path
    for spec in (look.font, look.mono_font):
        family = _font_family(spec)
        if family and family not in look.font_files:
            files = _font_files(family)
            if files:
                look.font_files[family] = files
    return look


def _ini_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().lower().replace(" ", "")
    except OSError:
        return ""


# ---------------------------------------------------------------- copying to a host

def local_id() -> str:
    """Names this computer (and user) on hosts; several computers can share a host."""
    try:
        with open("/etc/machine-id", encoding="ascii") as f:
            machine = f.read().strip()
    except OSError:
        machine = os.uname().nodename
    return hashlib.sha256(f"{machine}:{getpass.getuser()}".encode()).hexdigest()[:10]


def remote_dir() -> str:
    """Relative to the host user's home."""
    return f".local/share/obour/look/{local_id()}"


def _dconf_keyfile(look: Look) -> str:
    def quoted(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    keys = {"gtk-theme": look.gtk_theme, "icon-theme": look.icon_theme, "font-name": look.font,
            "monospace-font-name": look.mono_font, "color-scheme": look.color_scheme,
            "cursor-theme": look.cursor, "accent-color": look.accent}
    lines = ["[org/gnome/desktop/interface]"]
    lines += [f"{k}={quoted(v)}" for k, v in keys.items() if v]
    if look.cursor:
        lines.append(f"cursor-size={look.cursor_size}")
    return "\n".join(lines) + "\n"


def _dconf_locks(keyfile: str) -> str:
    """Locks make the values win over the host user's own (user-db) settings."""
    return "".join(f"/org/gnome/desktop/interface/{line.split('=', 1)[0]}\n"
                   for line in keyfile.splitlines() if "=" in line)


def _compile_dconf(keyfile: str) -> bytes | None:
    """The binary dconf database, compiled here when dconf is installed."""
    if not shutil.which("dconf"):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "keyfiles")
        os.makedirs(os.path.join(src, "locks"))
        with open(os.path.join(src, "00-obour"), "w", encoding="utf-8") as f:
            f.write(keyfile)
        with open(os.path.join(src, "locks", "00-obour"), "w", encoding="utf-8") as f:
            f.write(_dconf_locks(keyfile))
        out = os.path.join(tmp, "db")
        try:
            subprocess.run(["dconf", "compile", out, src], check=True, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=host_env())
            with open(out, "rb") as f:
                return f.read()
        except (OSError, subprocess.SubprocessError):
            return None


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    tar.addfile(info, io.BytesIO(data))


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


def pending_data(host: str, look: Look) -> list[tuple[str, str, str]]:
    """The icons/themes/fonts not copied to host yet."""
    sent = set(host_options(host).get("look_data", []))
    return [item for item in look.data_items() if f"{item[0]}/{item[1]}" not in sent]


def pending_size(items: list[tuple[str, str, str]]) -> int:
    total = 0
    for kind, _name, path in items:
        if kind == "fonts":
            total += sum(os.path.getsize(p) for p in path.split("\n") if os.path.isfile(p))
        else:
            total += _dir_size(path)
    return total


def needs_sync(host: str, look: Look) -> bool:
    opts = host_options(host)
    return opts.get("look_fp") != look.fingerprint() or bool(pending_data(host, look))


# On the host: unpack into a temporary folder, then move the settings into place in
# one step and add the data to ~/.local/share. Existing data folders are kept.
_UNPACK = r'''set -e
L="$HOME/$1"; T="$L.new.$$"
rm -rf "$T"; mkdir -p "$T"
tar xzf - --no-same-owner -C "$T"
if [ -d "$T/look" ]; then
    find "$T/look/config" -type f \( -name '*.conf' \) -exec sed -i "s|@OBOUR_LOOK@|$L|g" {} + 2>/dev/null || true
    if [ ! -f "$T/look/dconf.db" ] && [ -f "$T/look/dconf.keyfile" ] && command -v dconf >/dev/null 2>&1; then
        mkdir -p "$T/kf/locks" && cp "$T/look/dconf.keyfile" "$T/kf/00-obour" && cp "$T/look/dconf.locks" "$T/kf/locks/00-obour" && dconf compile "$T/look/dconf.db" "$T/kf" || true
    fi
    mkdir -p "$(dirname "$L")"
    rm -rf "$L.old"; [ ! -e "$L" ] || mv "$L" "$L.old"
    mv "$T/look" "$L"; rm -rf "$L.old"
fi
for kind in icons themes fonts; do
    [ -d "$T/data/$kind" ] || continue
    mkdir -p "$HOME/.local/share/$kind"
    for d in "$T/data/$kind"/*; do
        [ -e "$d" ] || continue
        n=$(basename "$d"); dest="$HOME/.local/share/$kind/$n"
        [ "$kind" = fonts ] && dest="$HOME/.local/share/fonts/obour/$n" && mkdir -p "$HOME/.local/share/fonts/obour"
        rm -rf "$dest"; mv "$d" "$dest"
    done
done
[ ! -d "$T/data/fonts" ] || ! command -v fc-cache >/dev/null 2>&1 || fc-cache -f "$HOME/.local/share/fonts/obour" >/dev/null 2>&1 || true
rm -rf "$T"
echo __obour_look_ok__
'''


def sync(host: str, look: Look, on_line=None) -> None:
    """Copy the look to host: settings when they changed, icons/themes/fonts the first
    time. Raises remote.RemoteError."""
    from . import remote
    fingerprint = look.fingerprint()
    opts = host_options(host)
    items = pending_data(host, look)
    send_settings = opts.get("look_fp") != fingerprint or not opts.get("look_fp")
    if not send_settings and not items:
        return
    if items and on_line:
        size = pending_size(items) / (1024 * 1024)
        on_line(_("Copying your desktop look to {host} (first time, {size:.0f} MB)…").format(
            host=host, size=max(size, 1)))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        if send_settings:
            for name, data in sorted(look.files.items()):
                _add_bytes(tar, f"look/config/{name}", data)
            keyfile = _dconf_keyfile(look)
            _add_bytes(tar, "look/dconf.keyfile", keyfile.encode())
            _add_bytes(tar, "look/dconf.locks", _dconf_locks(keyfile).encode())
            db = _compile_dconf(keyfile)
            if db:
                _add_bytes(tar, "look/dconf.db", db)
            env = {"OBOUR_QT5": look.qt5, "OBOUR_QT6": look.qt6, "OBOUR_DESKTOP": look.desktop}
            _add_bytes(tar, "look/env", "".join(f"{k}={shlex.quote(v)}\n"
                                                 for k, v in env.items()).encode())
            # launches check it, so a profile that changed on the host is sent again
            _add_bytes(tar, "look/fingerprint", fingerprint.encode())
        for kind, name, path in items:
            if kind == "fonts":
                for f in path.split("\n"):
                    tar.add(f, arcname=f"data/fonts/{name}/{os.path.basename(f)}")
            else:
                tar.add(path, arcname=f"data/{kind}/{name}")
    argv = remote.ssh_argv(host) + ["sh", "-c", shlex.quote(_UNPACK), "obour",
                                    shlex.quote(remote_dir())]
    try:
        proc = subprocess.run(argv, input=buf.getvalue(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=600, env=auth.ssh_env(host))
    except subprocess.TimeoutExpired:
        raise remote.RemoteError(_("Timed out while copying your desktop look."))
    except FileNotFoundError:
        raise remote.RemoteError(_("`{program}` is not installed on this computer.").format(
            program="ssh"))
    if proc.returncode != 0 or b"__obour_look_ok__" not in proc.stdout:
        raise remote.RemoteError(remote.friendly_ssh_error(
            proc.stderr.decode(errors="replace"), host))
    sent = set(opts.get("look_data", [])) | {f"{k}/{n}" for k, n, _p in items}
    _update_host(host, look_fp=fingerprint, look_data=sorted(sent))


def forget(host: str) -> None:
    """Send everything again next time (e.g. after the host was reinstalled)."""
    _update_host(host, look_fp="", look_data=[])
