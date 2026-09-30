"""Launcher storage: one JSON file under ~/.config/obour/."""

from __future__ import annotations

import json
import os
import re
import shlex
import uuid
from dataclasses import dataclass, asdict, field, fields

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "obour"
)
CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "obour"
)
DATA_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "obour"
)
LAUNCHERS_FILE = os.path.join(CONFIG_DIR, "launchers.json")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
HOSTS_FILE = os.path.join(CONFIG_DIR, "hosts.json")

PROTOCOLS = ("auto", "wayland", "x11")
# light/dark style of remote apps; "off" leaves the host's style alone
STYLES = ("system", "dark", "light", "off")
WAYPIPE_COMPRESSION = ("none", "lz4", "zstd", "h264")
FILE_SHARING = ("allow", "ask", "deny")
QT_VERSIONS = ("auto", "qt5", "qt6")


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


@dataclass(frozen=True)
class Option:
    """A setting that hosts and apps can change for themselves (see resolve)."""
    key: str
    kind: str                        # bool | choice | text
    choices: tuple[str, ...] = ()


# In the order the settings dialogs show them.
OPTIONS = (
    Option("protocol", "choice", PROTOCOLS),
    Option("style", "choice", STYLES),
    Option("desktop_theme", "bool"),
    Option("qt_version", "choice", QT_VERSIONS),
    Option("match_cursor", "bool"),
    Option("audio", "bool"),
    Option("file_sharing", "choice", FILE_SHARING),
    Option("private_bus", "bool"),
    Option("gpu", "bool"),
    Option("fallback_x11", "bool"),
    Option("waypipe_compress", "choice", WAYPIPE_COMPRESSION),
    Option("compress", "bool"),
    Option("extra_env", "text"),
    Option("menu_name", "text"),
)
OPTION_KEYS = tuple(o.key for o in OPTIONS)


def valid_value(key: str, value) -> bool:
    option = next((o for o in OPTIONS if o.key == key), None)
    if option is None:
        return False
    if option.kind == "bool":
        return isinstance(value, bool)
    if option.kind == "choice":
        return value in option.choices
    return isinstance(value, str)


def clean_overrides(values) -> dict:
    return {k: v for k, v in (values or {}).items() if valid_value(k, v)} \
        if isinstance(values, dict) else {}


def parse_env(text: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    try:
        words = shlex.split(text or "")
    except ValueError:
        return pairs
    for word in words:
        key, sep, value = word.partition("=")
        if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            pairs[key] = value
    return pairs


@dataclass
class Settings:
    """Obour's settings. Every Option can be changed per host and per app; the
    rest (language, check_hosts) apply everywhere."""
    style: str = "system"        # light/dark style of remote apps (STYLES)
    desktop_theme: bool = True   # copy this desktop's colors, icons and fonts (look.py)
    qt_version: str = "auto"     # which Qt the app uses, when ldd can't tell
    match_cursor: bool = True    # use (and copy to hosts) this computer's cursor theme
    gpu: bool = False            # waypipe GPU buffer sharing; freezes on some hosts
    compress: bool = False       # ssh -C (zlib): X11 apps and sound
    waypipe_compress: str = "lz4"  # Wayland apps: none | lz4 | zstd | h264
    check_hosts: bool = True     # look for missing tools when a new host is added
    fallback_x11: bool = True    # retry with X11 (and say so) when Wayland shows no window
    extra_env: str = ""          # KEY=VALUE … given to remote apps
    menu_name: str = "{name} ({host})"  # app-menu entry name; see desktop.menu_name
    language: str = "system"     # interface language: system | en | ar (see i18n.py)
    file_sharing: str = "ask"    # copy/paste files with hosts: allow | ask | deny
    protocol: str = "auto"       # auto | wayland | x11
    audio: bool = True
    private_bus: bool = True     # own D-Bus session, so apps open as a separate instance

    @classmethod
    def load(cls, path: str = SETTINGS_FILE) -> "Settings":
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            known = {f.name for f in fields(cls)}
            settings = cls(**{k: v for k, v in data.items() if k in known})
            if data.get("match_theme") is False and "style" not in data:
                settings.style = "off"          # from before "Don't Change" existed
            return settings
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: str = SETTINGS_FILE) -> None:
        _write_json(path, asdict(self))

    def extra_env_pairs(self) -> dict[str, str]:
        return parse_env(self.extra_env)

    def new_launcher(self, **kwargs) -> "Launcher":
        return Launcher(**kwargs)


@dataclass
class Launcher:
    host: str
    exec: str
    name: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    icon: str = ""            # absolute path to an image, or a themed icon name
    in_menu: bool = False     # added to the app menu only on request ("Add to App Menu")
    app_id: str = ""          # remote .desktop id, used as StartupWMClass
    # settings this app changes for itself: {Option key: value}; see resolve()
    overrides: dict = field(default_factory=dict)
    # "x11" once Obour found the app doesn't work over Wayland (it failed to start,
    # or it uses X11 anyway). Used when the display setting is Automatic.
    learned_protocol: str = ""

    @classmethod
    def from_dict(cls, d: dict, defaults: "Settings | None" = None) -> "Launcher":
        known = {f.name for f in fields(cls)}
        launcher = cls(**{k: v for k, v in d.items() if k in known})
        launcher.overrides = clean_overrides(launcher.overrides)
        # Older versions stored every option in each app. Values that match the
        # general setting become "inherit", so host settings apply to them.
        defaults = defaults or Settings()
        legacy = {k: d[k] for k in ("protocol", "audio", "private_bus", "file_sharing")
                  if k in d}
        if d.get("match_theme") is False:
            legacy["style"] = "off"
        for key, value in legacy.items():
            if key not in launcher.overrides and valid_value(key, value) \
                    and value != getattr(defaults, key):
                launcher.overrides[key] = value
        return launcher


class Store:
    def __init__(self, path: str = LAUNCHERS_FILE):
        self.path = path
        self.launchers: list[Launcher] = []
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            defaults = Settings.load()
            self.launchers = [Launcher.from_dict(d, defaults) for d in data.get("launchers", [])]
        except (OSError, ValueError, TypeError):
            self.launchers = []

    def save(self) -> None:
        _write_json(self.path, {"launchers": [asdict(l) for l in self.launchers]})

    def get(self, launcher_id: str) -> Launcher | None:
        return next((l for l in self.launchers if l.id == launcher_id), None)

    def upsert(self, launcher: Launcher) -> None:
        self.load()  # another Obour (window or menu launch) may have changed the file
        for i, l in enumerate(self.launchers):
            if l.id == launcher.id:
                self.launchers[i] = launcher
                break
        else:
            self.launchers.append(launcher)
        self.save()

    def set_learned_protocol(self, launcher_id: str, value: str) -> bool:
        """Remember (or forget, with "") what works for an app; True if it changed."""
        self.load()
        launcher = self.get(launcher_id)
        if launcher is None or launcher.learned_protocol == value:
            return False
        launcher.learned_protocol = value
        self.save()
        return True

    def remove(self, launcher_id: str) -> None:
        self.load()
        self.launchers = [l for l in self.launchers if l.id != launcher_id]
        self.save()

    def known_hosts(self) -> list[str]:
        hosts = list(dict.fromkeys(l.host for l in self.launchers))
        for h in ssh_config_hosts():
            if h not in hosts:
                hosts.append(h)
        return hosts


def _load_host_file() -> dict:
    try:
        with open(HOSTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def host_options(host: str) -> dict:
    """Per-host options: {"tailscale": address, "force_tailscale": bool,
    "gpu_broken": bool (GPU sharing left Wayland apps without a window there),
    "settings": {Option key: value} (see host_settings), "look_fp"/"look_data": what
    look.sync sent}."""
    opts = _load_host_file().get(host)
    return opts if isinstance(opts, dict) else {}


def _update_host(host: str, **values) -> None:
    data = _load_host_file()
    opts = data.get(host) if isinstance(data.get(host), dict) else {}
    opts.update(values)
    opts = {k: v for k, v in opts.items() if v not in ("", False, None)}
    if opts:
        data[host] = opts
    else:
        data.pop(host, None)
    _write_json(HOSTS_FILE, data)


def set_host_options(host: str, tailscale: str, force_tailscale: bool) -> None:
    tailscale = tailscale.strip().rpartition("@")[2]
    _update_host(host, tailscale=tailscale, force_tailscale=bool(tailscale) and force_tailscale)


def host_settings(host: str) -> dict:
    """The settings host changes for its apps: {Option key: value}."""
    opts = host_options(host)
    values = clean_overrides(opts.get("settings"))
    if opts.get("no_file_sharing") and "file_sharing" not in values:
        values["file_sharing"] = "deny"         # from before host settings existed
    return values


def set_host_settings(host: str, values: dict) -> None:
    _update_host(host, settings=clean_overrides(values) or None, no_file_sharing=False)


def host_shares_files(host: str) -> bool:
    """False when file sharing is turned off for host (its ⋮ menu)."""
    return host_settings(host).get("file_sharing") != "deny"


def set_host_shares_files(host: str, on: bool) -> None:
    values = host_settings(host)
    if on:
        values.pop("file_sharing", None)
    else:
        values["file_sharing"] = "deny"
    set_host_settings(host, values)


@dataclass
class Resolved:
    settings: Settings           # the values in effect
    sources: dict[str, str]      # Option key -> "app" | "host" | "obour"


def resolve(host: str, overrides: dict | None = None,
            settings: Settings | None = None) -> Resolved:
    """The settings for an app on host: the app's own choices (overrides) first, then
    the host's, then Obour's Preferences. Environment variables are merged the same
    way, variable by variable."""
    base = settings or Settings.load()
    layers = (("app", clean_overrides(overrides)),
              ("host", host_settings(host) if host else {}))
    values = {k: getattr(base, k) for k in OPTION_KEYS}
    sources = {k: "obour" for k in OPTION_KEYS}
    for name, layer in reversed(layers):
        for key, value in layer.items():
            if key == "extra_env":
                continue
            values[key] = value
            sources[key] = name
    env = parse_env(base.extra_env)
    for name, layer in reversed(layers):
        if "extra_env" in layer:
            env.update(parse_env(layer["extra_env"]))
            sources["extra_env"] = name
    values["extra_env"] = " ".join(shlex.quote(f"{k}={v}") for k, v in env.items())
    merged = Settings(**{**asdict(base), **values})
    return Resolved(merged, sources)


def resolve_launcher(launcher: "Launcher", settings: Settings | None = None) -> Resolved:
    return resolve(launcher.host, launcher.overrides, settings)


def file_sharing_for(launcher: "Launcher", settings: Settings | None = None) -> str:
    """allow | ask | deny for this app (app, then host, then Preferences)."""
    return resolve_launcher(launcher, settings).settings.file_sharing


def set_gpu_broken(host: str, broken: bool = True) -> None:
    _update_host(host, gpu_broken=broken)


def clear_gpu_broken() -> None:
    """Try GPU sharing on every host again."""
    for host, opts in _load_host_file().items():
        if isinstance(opts, dict) and opts.get("gpu_broken"):
            set_gpu_broken(host, False)


def ssh_config_hosts() -> list[str]:
    """Host aliases from ~/.ssh/config, to offer as suggestions."""
    hosts: list[str] = []
    try:
        with open(os.path.expanduser("~/.ssh/config"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^\s*Host\s+(.+)$", line, re.I)
                if not m:
                    continue
                for name in m.group(1).split():
                    if not any(c in name for c in "*?!"):
                        hosts.append(name)
    except OSError:
        pass
    return hosts
