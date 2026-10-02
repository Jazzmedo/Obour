"""Build forwarding commands and manage running remote apps."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import uuid
from dataclasses import dataclass, field

from .config import WAYPIPE_COMPRESSION, Launcher, Settings, host_options, resolve_launcher
from .cursor import apply_x11_root_cursor, local_cursor, theme_dir
from .hostenv import host_env
from .i18n import _
from . import look, remote, route
from .auth import ssh_env, ssh_opts
from .remote import RemoteError, friendly_ssh_error


def strip_field_codes(exec_line: str) -> str:
    return re.sub(r"\s*%[fFuUdDnNickvm]", "", exec_line).replace("%%", "%").strip()


def app_command(exec_line: str, protocol: str) -> str:
    """The command to run on the host, with fixes for apps that break when forwarded.

    mpv over waypipe: without GPU sharing its Wayland contexts fail, it goes on to
    probe the X11 ones and dies on an assertion (`!vo->x11`) while exiting with 0.
    Keeping it to the Wayland contexts makes it use one that works (or wlshm)."""
    cmd = strip_field_codes(exec_line)
    if protocol == "wayland" and "--gpu-context" not in cmd:
        cmd = re.sub(r"^((?:\S*/)?mpv)(?=\s|$)", r"\1 --gpu-context=wayland,waylandvk",
                     cmd)
    return cmd


def local_pulse_socket() -> str | None:
    """The local Pulse-compatible socket (served by pipewire-pulse or PulseAudio)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    sock = os.path.join(runtime, "pulse", "native")
    return sock if os.path.exists(sock) else None


def local_is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"


def system_prefers_dark() -> bool:
    """Ask the desktop (portal first, then GNOME settings) for its color scheme."""
    try:
        proc = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.freedesktop.portal.Desktop",
             "--object-path", "/org/freedesktop/portal/desktop",
             "--method", "org.freedesktop.portal.Settings.ReadOne",
             "org.freedesktop.appearance", "color-scheme"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, env=host_env())
        m = re.search(rb"uint32 (\d)", proc.stdout)
        if m:
            return m.group(1) == b"1"
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, env=host_env())
        return b"prefer-dark" in proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


@dataclass
class LaunchPlan:
    argv: list[str]
    protocol: str
    audio: bool
    notes: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    fallback: bool = False   # watch for a missing Wayland window and retry
    gpu: bool = False        # Wayland with GPU sharing (retried without it first)
    share: bool = False      # files can be copied with the host while it runs


# A session that shows a window moves megabytes within seconds (window pictures, or
# X11 drawing for apps that picked X11); one stuck before its first window has moved
# only the SSH handshake, about 150 KiB (measured).
# an app that ends sooner than this after launch is still "starting"
STARTED_AFTER_S = 4
NO_WINDOW_AFTER_S = 15
NO_WINDOW_BELOW_BYTES = 400 * 1024


def session_traffic(pgid: int) -> int:
    """Bytes read + written so far by all processes of a launch (its process group)."""
    total = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            if int(fields[2]) != pgid:
                continue
            with open(f"/proc/{pid}/io") as f:
                io = dict(line.split(": ") for line in f.read().splitlines())
            total += int(io["rchar"]) + int(io["wchar"])
        except (OSError, ValueError, IndexError, KeyError):
            continue
    return total


def display_name(kind: str) -> str:
    """"Wayland" / "X11" for a protocol, or for what an app really uses."""
    return {"wayland": "Wayland", "x11": "X11", "both": _("Wayland and X11"),
            "auto": _("Automatic")}.get(kind, "")


def can_learn(launcher: Launcher, settings: Settings | None = None) -> bool:
    """Obour may pick X11 for this app by itself: the app doesn't set its own
    display mode (a host or Preferences choice of Wayland still allows X11 when
    Wayland fails), and "Try X11 When Wayland Fails" is on."""
    return ("protocol" not in launcher.overrides
            and resolve_launcher(launcher, settings).settings.fallback_x11)


def uses_learned_x11(launcher: Launcher, settings: Settings | None = None) -> bool:
    """Obour found the app doesn't work over Wayland, and may use X11 for it."""
    return launcher.learned_protocol == "x11" and can_learn(launcher, settings)


def expected_protocol(launcher: Launcher, settings: Settings | None = None) -> str:
    """What a launch will use (wayland | x11), or "auto" when that depends on the
    host and it hasn't been checked yet. Never contacts the host."""
    mode = resolve_launcher(launcher, settings).settings.protocol
    if mode == "x11" or uses_learned_x11(launcher, settings):
        return "x11"
    if mode == "wayland":
        return mode
    if not (local_is_wayland() and shutil.which("waypipe")):
        return "x11"
    caps = remote.host_caps(launcher.host)
    if caps is None:
        return "auto"
    return "wayland" if caps.get("waypipe") else "x11"


def resolve_protocol(launcher: Launcher, notes: list[str], mode: str | None = None) -> str:
    """mode: the app's display setting (auto | wayland | x11)."""
    mode = mode or resolve_launcher(launcher).settings.protocol
    if mode == "x11":
        return "x11"
    if uses_learned_x11(launcher):
        notes.append(_("Using X11 for {name}: it didn't work over Wayland before (Edit… → "
                       "Try Wayland Again).").format(name=launcher.name))
        return "x11"
    if mode == "wayland":
        if not local_is_wayland():
            raise RuntimeError(_("Wayland forwarding needs a Wayland session on this "
                                 "computer. Set this app's display mode to X11 or Automatic."))
        if not shutil.which("waypipe"):
            raise RuntimeError(_("waypipe is not installed on this computer; it is needed "
                                 "for Wayland forwarding."))
        return "wayland"

    # automatic
    if not (local_is_wayland() and shutil.which("waypipe")):
        return "x11"
    caps = remote.host_caps(launcher.host)
    if caps is None:
        try:
            caps = remote.probe_caps(launcher.host)
        except RemoteError as e:
            raise RuntimeError(str(e))
    if caps.get("waypipe"):
        return "wayland"
    notes.append(_("waypipe isn't installed on {host}; using X11 forwarding instead.").format(
        host=launcher.host))
    return "x11"


def _caps_for(host: str) -> dict:
    """The host's cached capabilities, probing once if they're missing or from an
    older Obour. Connection problems are left for the launch itself to report."""
    caps = remote.host_caps(host)
    if caps is None or "os-id" not in caps:
        try:
            caps = remote.probe_caps(host)
        except RemoteError:
            caps = caps or {}
    return caps


def _sync_cursor(host: str, theme: str, caps: dict, notes: list[str]) -> None:
    """Copy this computer's cursor theme to the host the first time it's needed:
    X11 apps (and Wayland apps without cursor-shape support) draw cursors themselves."""
    if caps.get("cursor") and caps.get("cursor-theme") == theme:
        return
    path = theme_dir(theme)
    if path is None:
        return
    try:
        remote.upload_cursor_theme(host, theme, path)
        remote.set_cap(host, "cursor-theme", theme)
        notes.append(_("Copied your cursor theme ({theme}) to {host}.").format(
            theme=theme, host=host))
    except RemoteError as e:
        notes.append(_("Couldn't copy your cursor theme to {host}: {error}").format(
            host=host, error=e))


# Qt platform theme plugins, and the host capability that says the plugin is there.
_QT_PLUGIN_CAP = {"qt6ct": "qt6ct", "qt5ct": "qt5ct", "kde": "qt-kde", "lxqt": "qt-lxqt"}


def _sync_look(host: str, notes: list[str], on_line) -> look.Look | None:
    """This desktop's look, copied to the host when it changed. None if it couldn't be."""
    try:
        lk = look.collect()
    except Exception as e:     # reading local settings must never stop a launch
        notes.append(_("Couldn't read your desktop's look: {error}").format(error=e))
        return None
    try:
        look.sync(host, lk, on_line=on_line)
    except RemoteError as e:
        notes.append(_("Couldn't copy your desktop's look to {host}: {error}").format(
            host=host, error=e))
        return None
    return lk


def _qt_env(value: str, qt: str, caps: dict, dark: bool) -> tuple[str, str]:
    """(QT_QPA_PLATFORMTHEME, QT_STYLE_OVERRIDE) for Qt 5 or 6 apps: this desktop's
    plugin when the host has it, else the GTK bridge (plus a dark style for Qt 5)."""
    cap = _QT_PLUGIN_CAP.get(value)
    if cap and caps.get(cap):
        return value, ""
    style = ""
    if dark and qt == "qt5":
        style = ("Adwaita-Dark" if caps.get("qt5-adwaita")
                 else "kvantum-dark" if caps.get("qt5-kvantum") else "")
    return "gtk3", style


# Where VLC keeps its Qt interface: /usr/bin/vlc itself doesn't link Qt.
_VLC_QT = ("/usr/lib/*/vlc/plugins/gui/libqt_plugin.so /usr/lib64/vlc/plugins/gui/libqt_plugin.so "
           "/usr/lib/vlc/plugins/gui/libqt_plugin.so")


def _qt_script(q5: tuple[str, str], q6: tuple[str, str], qt_version: str, caps: dict) -> str:
    """Shell code that gives the app the Qt theme settings for its Qt version:
    q5/q6 are (QT_QPA_PLATFORMTHEME, QT_STYLE_OVERRIDE). The version comes from
    qt_version, or from what the program (or VLC's interface plugin) links against."""
    def export(values: tuple[str, str]) -> str:
        theme, style = values
        return (f"export QT_QPA_PLATFORMTHEME={theme}; "
                + (f"export QT_STYLE_OVERRIDE={style}; " if style else "unset QT_STYLE_OVERRIDE; "))

    fallback = "qt6" if caps.get("qt6") or not caps.get("qt5") else "qt5"
    return (
        f"qv={qt_version if qt_version in ('qt5', 'qt6') else 'auto'}; "
        'if [ "$qv" = auto ]; then '
        'qb=$(command -v "${cmd%% *}" 2>/dev/null); '
        'case "$(ldd "$qb" 2>/dev/null)" in *libQt5Core*) qv=qt5;; *libQt6Core*) qv=qt6;; '
        f'*libvlc*) for f in {_VLC_QT}; do [ -e "$f" ] || continue; '
        'case "$(ldd "$f" 2>/dev/null)" in *libQt5Core*) qv=qt5;; *libQt6Core*) qv=qt6;; esac; '
        'done;; esac; fi; '
        f'[ "$qv" != auto ] || qv={fallback}; '
        f'if [ "$qv" = qt5 ]; then {export(q5)}else {export(q6)}fi; '
    )


# printed by a launch whose host has another look than the one Obour sent there
LOOK_OUTDATED = "obour: look outdated"


def _look_script(lk: look.Look) -> tuple[str, str]:
    """Shell code run before starting the app (and before its D-Bus session, which
    inherits it) and after it ended. It points XDG_CONFIG_HOME at an overlay: the
    host's ~/.config entries as links, with the look's entries on top; folders that
    exist on both sides are merged one level deep. New files the app writes there are
    moved back to ~/.config afterwards."""
    runtime = '${XDG_RUNTIME_DIR:-$HOME/.cache}'
    # obour_restore DIR: move back what the app created there (real files where links
    # were, new entries in merged folders; links are left alone), then remove DIR.
    restore = (
        'obour_restore() { _d=$1; [ -n "$_d" ] && [ -d "$_d" ] || return 0; '
        'for e in "$_d"/* "$_d"/.[!.]*; do [ -e "$e" ] || continue; [ -L "$e" ] && continue; '
        'n=${e##*/}; [ "$n" = .obour-dconf-profile ] && continue; '
        'if [ -d "$e" ] && [ -e "$L/config/$n" ]; then '
        'for f in "$e"/* "$e"/.[!.]*; do [ -e "$f" ] && [ ! -L "$f" ] || continue; '
        '[ -e "$L/config/$n/${f##*/}" ] && continue; '
        'rm -rf "$base/$n/${f##*/}"; mv -f "$f" "$base/$n/" 2>/dev/null; done; '
        # another app's overlay may have created the same folder meanwhile: merge
        'elif [ -d "$e" ] && [ -d "$base/$n" ]; then cp -Rn "$e/." "$base/$n/" 2>/dev/null; '
        'else rm -f "$base/$n"; mv -f "$e" "$base/" 2>/dev/null; fi; done; '
        'rm -rf "$_d"; }; '
    )
    pre = (
        f'L="$HOME/{look.remote_dir()}"; o=; '
        f'[ "$(cat "$L/fingerprint" 2>/dev/null)" = {lk.fingerprint()} ] || '
        f'echo "{LOOK_OUTDATED}"; '
        'if [ -d "$L/config" ]; then '
        'base="${XDG_CONFIG_HOME:-$HOME/.config}"; mkdir -p "$base"; '
        + restore +
        # overlays of launches that were killed before they could clean up
        f'for d in "{runtime}"/obour-xdg-{look.local_id()}-*; do [ -d "$d" ] || continue; '
        'kill -0 "${d##*-}" 2>/dev/null || obour_restore "$d"; done; '
        f'o="{runtime}/obour-xdg-{look.local_id()}-$$"; '
        'rm -rf "$o"; mkdir -p "$o"; '
        # The connection going away signals this script, possibly with SIGKILL: clean up
        # on a signal, and from a detached helper that waits for this script to end.
        "trap 'obour_restore \"$o\"; exit 143' TERM; trap 'obour_restore \"$o\"; exit 129' HUP; "
        "! command -v setsid >/dev/null 2>&1 || setsid sh -c 'L=$3; base=$4; " + restore
        + "while kill -0 \"$1\" 2>/dev/null; do sleep 2; done; obour_restore \"$2\"' "
        'obour-cleanup "$$" "$o" "$L" "$base" </dev/null >/dev/null 2>&1 & '
        'for e in "$base"/* "$base"/.[!.]*; do [ -e "$e" ] || [ -L "$e" ] || continue; '
        'n=${e##*/}; [ -e "$L/config/$n" ] || ln -s "$e" "$o/$n"; done; '
        'for e in "$L/config"/*; do n=${e##*/}; '
        'if [ -d "$e" ] && [ -d "$base/$n" ]; then mkdir "$o/$n"; '
        'for f in "$base/$n"/* "$base/$n"/.[!.]*; do [ -e "$f" ] || [ -L "$f" ] || continue; '
        '[ -e "$e/${f##*/}" ] || ln -s "$f" "$o/$n/${f##*/}"; done; '
        'for f in "$e"/*; do ln -s "$f" "$o/$n/${f##*/}"; done; '
        'else ln -s "$e" "$o/$n"; fi; done; '
        'export XDG_CONFIG_HOME="$o"; '
        'if [ -f "$L/dconf.db" ]; then '
        'printf "user-db:user\\nfile-db:%s\\n" "$L/dconf.db" > "$o/.obour-dconf-profile"; '
        'export DCONF_PROFILE="$o/.obour-dconf-profile"; fi; '
        'fi; '
    )
    post = '[ -z "$o" ] || { obour_restore "$o"; trap - TERM HUP; }; '
    return pre, post


def _sound_cleanup(sock: str) -> str:
    """Shell code: remove this launch's sound socket once the script ends (even when
    it's killed), and sockets of earlier launches that nothing listens on anymore."""
    quoted = shlex.quote(sock)
    return (
        "if command -v ss >/dev/null 2>&1; then live=$(ss -xl 2>/dev/null); "
        'for s in /tmp/obour-pulse-*.sock; do [ -S "$s" ] && [ -O "$s" ] || continue; '
        'case "$live" in *"$s"*) ;; *) rm -f "$s";; esac; done; fi; '
        "! command -v setsid >/dev/null 2>&1 || setsid sh -c "
        "'while kill -0 \"$1\" 2>/dev/null; do sleep 2; done; rm -f \"$2\"' "
        f'obour-cleanup "$$" {quoted} </dev/null >/dev/null 2>&1 & '
    )


# In Wayland mode an app may still use X11 (through ssh -Y) when it can't do
# Wayland (VLC 3). This finds what the app's processes are connected to: the X11
# display's TCP port, or waypipe's Wayland socket (matched by socket inode), and
# prints "obour: display wayland|x11|both" when that changes (checked at 3, 8, 20 s).
# obour_display prints which displays the app's processes are connected to:
# "wayland", "x11", "both" or nothing (only with ss).
_DISPLAY_FN = (
    "obour_display() { "
    'p=; [ -z "$sess" ] || p=$(pgrep -s "$pid" 2>/dev/null | paste -sd"|" -); '
    '[ -n "$p" ] || p=$pid; x=; w=; '
    'if [ -n "${DISPLAY:-}" ]; then n=${DISPLAY#*:}; n=${n%%.*}; '
    'ss -tnp 2>/dev/null | grep -E "[:.]$((6000+n))[[:space:]]" | grep -qE "pid=($p)," '
    "&& x=1; fi; "
    'if [ -n "${WAYLAND_DISPLAY:-}" ]; then '
    "peers=$(ss -xp 2>/dev/null | grep -E \"pid=($p),\" | awk '{print $8}' | paste -sd'|' -); "
    '[ -n "$peers" ] && ss -x 2>/dev/null | awk -v d="$WAYLAND_DISPLAY" '
    "'$5 ~ d\"$\" {print $6}' | grep -qxE \"$peers\" && w=1; fi; "
    'now=; [ -n "$w" ] && now=wayland; if [ -n "$x" ]; then now=${now:+both}; now=${now:-x11}; fi; '
    'printf %s "$now"; }; '
)

_DISPLAY_CHECK = (
    "chk=; if command -v ss >/dev/null 2>&1; then ( last=; t=0; "
    "while [ $t -lt 20 ]; do sleep 1; t=$((t+1)); "
    'case $t in 3|8|20) ;; *) continue ;; esac; '
    "now=$(obour_display); "
    '[ -z "$now" ] || [ "$now" = "$last" ] || echo "obour: display $now"; '
    '[ -z "$now" ] || last=$now; done ) & chk=$!; fi; '
)

# After the app's own process ends, its session is waited for: a self-restarting
# app hands over to a new process there (LibreOffice, Firefox). Helpers it started
# can stay forever, though (GTK's glycin image loaders, for one), so once no process
# of the session has a window connection for 4 s, and the app had 30 s to show one,
# the rest are stopped and the app counts as closed.
_WAIT_SESSION = (
    'if [ -n "$sess" ] && command -v pgrep >/dev/null 2>&1; then '
    'idle=0; while pgrep -s "$pid" >/dev/null 2>&1; do sleep 2; '
    "command -v ss >/dev/null 2>&1 || continue; "
    'if [ -n "$(obour_display)" ]; then idle=0; continue; fi; '
    '[ $(( $(date +%s) - t0 )) -ge 30 ] || continue; '
    "idle=$((idle+1)); [ $idle -ge 2 ] || continue; "
    'kill -TERM -"$pid" 2>/dev/null; sleep 2; kill -KILL -"$pid" 2>/dev/null; break; done; fi; '
)


# An ALSA configuration for this session: the system's, with the default device
# going to PulseAudio (PULSE_SERVER, forwarded here).
_ALSA_PRE = (
    'alsa="${XDG_RUNTIME_DIR:-/tmp}/obour-alsa-$$.conf"; '
    "printf '%s\\n' '</usr/share/alsa/alsa.conf>' 'pcm.!default { type pulse }' "
    "'ctl.!default { type pulse }' > \"$alsa\" && export ALSA_CONFIG_PATH=\"$alsa\"; "
)


def build_plan(launcher: Launcher, dark: bool | None = None,
               settings: Settings | None = None, protocol: str | None = None,
               on_line=None) -> LaunchPlan:
    """Work out the full local command line. May contact the host the first time
    (to learn what it has installed, and to copy the cursor theme and desktop look;
    on_line(text) reports that progress).
    protocol overrides the launcher's display mode (used for the X11 fallback)."""
    if not shutil.which("ssh"):
        raise RuntimeError(_("`{program}` is not installed on this computer.").format(
            program="ssh"))
    resolved = resolve_launcher(launcher, settings)
    settings = resolved.settings        # this app's settings: app, then host, then Obour
    notes: list[str] = []
    via = route.describe(launcher.host)
    if via:
        notes.append(via)
    protocol = protocol or resolve_protocol(launcher, notes, settings.protocol)
    caps = _caps_for(launcher.host)
    gpu_off = bool(host_options(launcher.host).get("gpu_broken"))

    # Java (AWT/Swing) always uses X11, and draws an empty window under window managers
    # that don't frame X11 windows (Hyprland, sway, niri…) unless told so.
    # ponytail: set for every desktop; framing ones (GNOME, KDE) ignore it in practice.
    env: dict[str, str] = {"_JAVA_AWT_WM_NONREPARENTING": "1"}
    forwards: list[str] = []
    if settings.compress:
        forwards.append("-C")

    if protocol == "wayland":
        # No QT_QPA_PLATFORM/GDK_BACKEND: GTK and Qt 6 already prefer Wayland when it is
        # available, and forcing it hangs Qt 5 apps (VLC 3) — directly, or through the
        # GTK3 theme bridge that dark mode uses.
        env.update(XDG_SESSION_TYPE="wayland", MOZ_ENABLE_WAYLAND="1",
                   ELECTRON_OZONE_PLATFORM_HINT="auto", SDL_VIDEODRIVER="wayland,x11")
    else:
        env.update(GDK_BACKEND="x11", QT_QPA_PLATFORM="xcb")
    # The other computer's GPU only helps when waypipe shares its buffers with this one;
    # otherwise each frame comes back through the CPU anyway, and old drivers can hang
    # in it (radeon with Chromium/Electron apps: VSCodium never drew its window).
    if not (protocol == "wayland" and settings.gpu and not gpu_off):
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"

    remote_sock = ""
    audio = False
    alsa_pre = alsa_post = ""
    if settings.audio:
        sock = local_pulse_socket()
        if sock:
            remote_sock = f"/tmp/obour-pulse-{uuid.uuid4().hex[:12]}.sock"
            forwards += ["-R", f"{remote_sock}:{sock}"]
            env["PULSE_SERVER"] = f"unix:{remote_sock}"
            # Apps that speak PipeWire directly (mpv) would play on the host's own
            # PipeWire; without it they fall back to Pulse, which is forwarded here.
            env["PIPEWIRE_REMOTE"] = "obour-no-pipewire"
            audio = True
            # Apps that use ALSA directly go to the host's default device (usually its
            # PipeWire); the Pulse ALSA plugin sends them to PULSE_SERVER instead.
            if caps.get("alsa-pulse"):
                alsa_pre, alsa_post = _ALSA_PRE, 'rm -f "$alsa"; '
            elif caps.get("alsa-pulse") is not None:
                notes.append(_("Apps that use ALSA directly have no sound until the ALSA "
                               "sound plugin is installed on {host} (⋮ → Set Up Host…).")
                             .format(host=launcher.host))
        else:
            notes.append(_("No local PipeWire/PulseAudio socket found; launching without "
                           "sound."))

    # Light/dark and the desktop theme are separate settings. dark is None when the
    # style is left alone ("Don't Change").
    if settings.style == "off":
        dark = None
    elif settings.style != "system":
        dark = settings.style == "dark"
    elif dark is None:
        dark = system_prefers_dark()
    look_pre = look_post = qt = ""
    lk = _sync_look(launcher.host, notes, on_line) if settings.desktop_theme else None
    if lk is not None:
        look_pre, look_post = _look_script(lk)
        # A light/dark choice opposite to the desktop's colors wins: plain Adwaita in
        # that style, and the GTK bridge for Qt instead of the desktop's palette.
        matches = dark is None or dark == lk.dark
        shade = lk.dark if dark is None else dark
        env["ADW_DEBUG_COLOR_SCHEME"] = "prefer-dark" if shade else "prefer-light"
        if not matches or (shade and not lk.gtk_theme_dirs
                           and lk.gtk_theme in ("", "Adwaita", "Default")):
            env["GTK_THEME"] = "Adwaita:dark" if shade else "Adwaita"
        qt = _qt_script(_qt_env(lk.qt5 if matches else "", "qt5", caps, shade),
                        _qt_env(lk.qt6 if matches else "", "qt6", caps, shade),
                        settings.qt_version, caps)
    elif dark is not None:
        env["GTK_THEME"] = "Adwaita:dark" if dark else "Adwaita"
        env["ADW_DEBUG_COLOR_SCHEME"] = "prefer-dark" if dark else "prefer-light"
        # Qt 6 takes its palette from GTK_THEME through the gtk3 plugin. Qt 5's version
        # of it only borrows fonts and dialogs, so Qt 5 apps (VLC 3) need a dark style.
        qt = _qt_script(_qt_env("", "qt5", caps, dark), _qt_env("", "qt6", caps, dark),
                        settings.qt_version, caps)
        if dark and caps.get("qt5") and not (caps.get("qt5-adwaita") or caps.get("qt5-kvantum")):
            notes.append(_("Qt 5 apps on {host} stay light until a dark Qt style is "
                           "installed there (⋮ → Set Up Host…).").format(host=launcher.host))

    if settings.match_cursor:
        theme, size = local_cursor()
        if theme:
            _sync_cursor(launcher.host, theme, caps, notes)
            if not apply_x11_root_cursor(theme, size) and shutil.which("xsetroot") is None:
                notes.append(_("Install `x11-xserver-utils` (xsetroot) on this computer so "
                               "Qt 5 apps like VLC show your cursor."))
            env["XCURSOR_THEME"] = theme
            env["XCURSOR_SIZE"] = str(size)
            # Java (and other apps using X11's built-in cursors) only get the theme with this
            env["XCURSOR_THEME_CORE"] = "1"

    env.update(settings.extra_env_pairs())

    # A private D-Bus session keeps single-instance apps (gedit, Files, terminals…)
    # from handing the request to an instance already running on the remote desktop.
    # The app gets its own session; when the SSH connection goes away (Stop, Obour
    # quitting or crashing) stdin hits EOF and the watcher ends the whole group —
    # sshd itself does not signal commands that run without a terminal.
    # (Background jobs get /dev/null as stdin, so the connection is kept on fd 3.)
    # ssh forwards LANG and LC_*; a locale the host lacks makes apps warn and fall
    # back to plain ASCII, so drop those (LANG becomes C.UTF-8).
    script = (
        "exec 3<&0; "
        "if command -v locale >/dev/null 2>&1; then "
        "have=$(locale -a 2>/dev/null | tr A-Z a-z | tr -d -); "
        "for v in LANG LC_ALL LC_CTYPE LC_NUMERIC LC_TIME LC_COLLATE LC_MONETARY LC_MESSAGES "
        "LC_PAPER LC_NAME LC_ADDRESS LC_TELEPHONE LC_MEASUREMENT LC_IDENTIFICATION; do "
        'eval "x=\\${$v:-}"; [ -n "$x" ] || continue; '
        'y=$(printf %s "$x" | tr A-Z a-z | tr -d -); '
        "printf '%s\\n' \"$have\" | grep -qxF \"$y\" && continue; "
        'if [ "$v" = LANG ]; then export LANG=C.UTF-8; else unset "$v"; fi; '
        "done; fi; "
        f"cmd={shlex.quote(app_command(launcher.exec, protocol))}; "
        + (_sound_cleanup(remote_sock) if remote_sock else "")
        + alsa_pre
        + look_pre + qt +
        'bus=; set -- sh -c "$cmd"; '
        # The bus lives as long as this script, not just the first process: apps that
        # restart themselves (Firefox after an update) would otherwise lose it.
        + ('if command -v dbus-daemon >/dev/null 2>&1 && b=$(dbus-daemon --session --fork '
           '--nopidfile --print-address=1 --print-pid=1 </dev/null 2>/dev/null); then '
           'bus=$(printf %s "$b" | tail -n 1); '
           'export DBUS_SESSION_BUS_ADDRESS="$(printf %s "$b" | head -n 1)"; '
           'elif command -v dbus-run-session >/dev/null 2>&1; then '
           'set -- dbus-run-session -- sh -c "$cmd"; fi; '
           if settings.private_bus else "")
        + _DISPLAY_FN + 't0=$(date +%s); '
        'sess=; if command -v setsid >/dev/null 2>&1; then setsid "$@" </dev/null 3<&- & '
        'sess=1; else "$@" </dev/null 3<&- & fi; pid=$!; '
        '( cat <&3 >/dev/null 2>&1; kill -TERM -"$pid" "$pid" 2>/dev/null; sleep 3; '
        'kill -KILL -"$pid" "$pid" 2>/dev/null; [ -z "$bus" ] || kill "$bus" 2>/dev/null ) '
        ">/dev/null 2>&1 & "
        "exec 3<&-; "
        + (_DISPLAY_CHECK if protocol == "wayland" else "")
        + 'wait "$pid"; rc=$?; '
        + _WAIT_SESSION
        + ('[ -z "$chk" ] || kill "$chk" 2>/dev/null; ' if protocol == "wayland" else "") +
        '[ -z "$bus" ] || kill "$bus" 2>/dev/null; '
        + look_post
        + (f"rm -f {shlex.quote(remote_sock)}; " if remote_sock else "")
        + alsa_post
        + "exit $rc"
    )
    remote_cmd = ["env", *(shlex.quote(f"{k}={v}") for k, v in env.items()),
                  "sh", "-c", shlex.quote(script)]

    if protocol == "wayland":
        # -Y as well: X11-only apps (e.g. VLC 3) fall back to the forwarded X display,
        # while Wayland-capable toolkits prefer waypipe via the backend variables above.
        # --no-gpu: sharing GPU buffers freezes waypipe on some hosts before the first
        # window appears; plain shared memory works everywhere.
        method = settings.waypipe_compress
        if method not in WAYPIPE_COMPRESSION:
            method = "lz4"
        gpu = settings.gpu and not gpu_off
        if settings.gpu and gpu_off:
            notes.append(_("GPU Acceleration is off for {host}: Wayland apps showed "
                           "no window with it there.").format(host=launcher.host))
        wp_opts = [] if gpu else ["--no-gpu"]
        if method == "h264":
            if gpu:
                wp_opts += ["--video=h264", "--compress=lz4"]
            else:
                notes.append(_("H.264 video needs GPU Acceleration (Preferences); "
                               "using LZ4 compression instead."))
                wp_opts.append("--compress=lz4")
        else:
            wp_opts.append(f"--compress={method}")
        argv = ["waypipe", *wp_opts, "ssh", "-Y",
                *ssh_opts(launcher.host), *forwards, launcher.host, *remote_cmd]
    else:
        argv = ["ssh", "-Y", *ssh_opts(launcher.host), *forwards, launcher.host, *remote_cmd]
    gpu_used = protocol == "wayland" and settings.gpu and not gpu_off
    return LaunchPlan(argv=argv, protocol=protocol, audio=audio, notes=notes,
                      env=ssh_env(launcher.host), gpu=gpu_used,
                      # with GPU sharing on, a missing window is retried without it first
                      fallback=protocol == "wayland" and (settings.fallback_x11 or gpu_used))


def start_sharing(launcher: Launcher, share: bool, on_line, plan: LaunchPlan | None = None):
    """Start copy/paste of files with the launcher's host; returns a share.Hold to
    release when the app ends, or None. With a plan, waypipe and ssh -Y are pointed
    at the helper's proxies so files can be dragged too, and pasted where the
    clipboard bridge can't work (plan.env changes)."""
    if not share:
        return None
    from .share import Hold
    from .wlproxy import display_path
    wayland = plan is not None and plan.protocol == "wayland"
    hold = Hold(launcher.host, display_path() if wayland else None,
                os.environ.get("DISPLAY") if plan is not None else None)
    ok = hold.acquire()
    for note in hold.notes:
        on_line(note)
    if not ok:
        return None
    changes = {}
    if hold.wayland_display:
        changes["WAYLAND_DISPLAY"] = hold.wayland_display
    if hold.x11_display:
        changes["DISPLAY"] = hold.x11_display
    if changes:
        plan.env = {**(plan.env if plan.env is not None else os.environ), **changes}
    if hold.wayland_display if wayland else hold.x11_display:
        on_line(_("Files can be copied, pasted and dragged between this computer and "
                  "{name} while it runs.").format(name=launcher.name))
    else:
        on_line(_("Files can be copied and pasted with {host} while {name} runs.").format(
            host=launcher.host, name=launcher.name))
    return hold


class Diagnosis(str):
    """A (translated) explanation from diagnose(), with a stable .code to test against:
    "no-waypipe", "x11-refused", "wayland-window", "x11-display", "ssh", "running",
    "not-found", "crash" or "exit"."""

    code: str = ""

    def __new__(cls, text: str, code: str) -> "Diagnosis":
        obj = super().__new__(cls, text)
        obj.code = code
        return obj

    def __contains__(self, item: object) -> bool:
        # Callers written before translations test `"Wayland window" in message`.
        if item == "Wayland window":
            return self.code == "wayland-window"
        return super().__contains__(item)


def is_wayland_window_error(message: str) -> bool:
    """True when diagnose() found that the app couldn't open a Wayland window."""
    return getattr(message, "code", "") == "wayland-window"


_CRASH = re.compile(r"Assertion .* failed|Aborted \(core dumped\)|Segmentation fault|"
                    r"core dumped|terminate called after throwing")


def crash_line(output: list[str]) -> str:
    """The line saying the app crashed, if any. Some apps (mpv) still exit with 0
    after an assertion, so the exit code alone doesn't tell."""
    hits = [l.strip() for l in output if _CRASH.search(l)]
    # the reason (the assertion) is more useful than "Aborted (core dumped)"
    return next((l for l in hits if "core dumped" not in l), hits[0] if hits else "")


def wayland_failure(output: list[str], returncode: int, host: str | None = None) -> str:
    """Why a Wayland launch that ended while starting looks like a Wayland problem
    (so X11 is worth a try), or "" when it doesn't: a normal exit, or a problem X11
    wouldn't fix (login, a wrong command, an app already running)."""
    if crash_line(output):
        return _("it crashed")
    if returncode in (0, -15, 143, 129, -1):
        return ""
    message = diagnose(output, returncode, "wayland", host)
    if message.code == "wayland-window":
        return _("it couldn't connect to the Wayland display")
    if message.code in ("ssh", "not-found", "running", "no-waypipe", "x11-refused"):
        return ""
    return _("it stopped with exit code {code}").format(code=returncode)


def diagnose(output: list[str], returncode: int, protocol: str,
             host: str | None = None) -> Diagnosis:
    """Turn a failed session's output into a helpful message."""
    text = "\n".join(output)
    low = text.lower()
    if "waypipe" in low and ("not found" in low or "no such file" in low):
        return Diagnosis(_("waypipe isn't installed on the host. Install it there, or set "
                           "this app's display mode to X11."), "no-waypipe")
    if "x11 forwarding request failed" in low:
        return Diagnosis(_("The host refused X11 forwarding. Set `X11Forwarding yes` in "
                           "/etc/ssh/sshd_config on the host and make sure `xauth` is "
                           "installed there."), "x11-refused")
    if any(s in low for s in ("cannot open display", "can't open display",
                              "could not connect to display", "failed to open display",
                              "unable to open display", "cannot connect to x server")):
        if protocol == "wayland":
            return Diagnosis(_("The app couldn't open a Wayland window. It may only support "
                               "X11; try setting its display mode to X11."), "wayland-window")
        return Diagnosis(_("The app couldn't open the forwarded X11 display. Make sure "
                           "`xauth` is installed on the host."), "x11-display")
    if returncode == 255 or remote.ssh_auth_failed(text) or any(
            s in low for s in ("could not resolve", "connection refused", "timed out",
                               "host key verification")):
        return Diagnosis(friendly_ssh_error(text, host), "ssh")
    if "is already running" in low:
        return Diagnosis(_("The app is already running on the host and refused to start a "
                           "second copy (for example Firefox with the same profile). Close it "
                           "there, or turn off “Separate Instance” in this app's settings to "
                           "open a window of the running copy."), "running")
    if returncode == 127 or "command not found" in low:
        return Diagnosis(_("The command wasn't found on the host. Check the command in this "
                           "app's settings."), "not-found")
    crash = crash_line(output)
    if crash:
        return Diagnosis(_("The app crashed on the host: {line}").format(line=crash), "crash")
    last = next((l for l in reversed(output) if l.strip() and not l.startswith("  $ ")), "")
    message = _("The app stopped with exit code {code}.").format(code=returncode)
    if last:
        message += " " + _("Last message: {line}").format(line=last)
    return Diagnosis(message, "exit")


class LaunchManager:
    """Start remote apps and stream their output to callbacks.

    on_line(launcher, text), on_exit(launcher, returncode) and on_no_window(launcher)
    run on worker threads; the UI marshals them to the main loop itself.
    on_no_window is called (before the session is ended) when a Wayland launch shows
    no window in time; on_exit follows."""

    def __init__(self, on_line, on_exit, on_no_window=None):
        self.on_line = on_line
        self.on_exit = on_exit
        self.on_no_window = on_no_window
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def launch(self, launcher: Launcher, dark: bool | None = None,
               protocol: str | None = None, share: bool = False) -> LaunchPlan:
        plan = build_plan(launcher, dark, protocol=protocol,
                          on_line=lambda text: self.on_line(launcher, text))
        for note in plan.notes:
            self.on_line(launcher, note)
        hold = start_sharing(launcher, share, lambda text: self.on_line(launcher, text), plan)
        plan.share = hold is not None
        self.on_line(launcher, (_("Connecting to {host} ({protocol}, with sound)…") if plan.audio
                                else _("Connecting to {host} ({protocol})…")).format(
                                    host=launcher.host, protocol=plan.protocol))
        self.on_line(launcher, "  $ " + shlex.join(plan.argv))
        # stdin stays open for the app's lifetime: the remote watcher ends the app on EOF.
        try:
            proc = subprocess.Popen(plan.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.PIPE, start_new_session=True,
                                    env=plan.env)
        except OSError:
            if hold:
                hold.release()
            raise
        with self._lock:
            self._procs[launcher.id] = proc
        threading.Thread(target=self._reader, args=(launcher, proc, hold), daemon=True).start()
        if plan.fallback and self.on_no_window:
            timer = threading.Timer(NO_WINDOW_AFTER_S, self._check_window, (launcher, proc))
            timer.daemon = True
            timer.start()
        return plan

    def _check_window(self, launcher: Launcher, proc: subprocess.Popen) -> None:
        with self._lock:
            current = self._procs.get(launcher.id) is proc
        if not current or proc.poll() is not None:
            return
        if session_traffic(proc.pid) >= NO_WINDOW_BELOW_BYTES:
            return
        self.on_no_window(launcher)
        self._terminate(proc)

    def is_running(self, launcher_id: str) -> bool:
        with self._lock:
            proc = self._procs.get(launcher_id)
        return proc is not None and proc.poll() is None

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for p in self._procs.values() if p.poll() is None)

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        """End waypipe/ssh and everything they started (they share the process group)."""
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def stop(self, launcher_id: str) -> None:
        with self._lock:
            proc = self._procs.get(launcher_id)
        if proc:
            self._terminate(proc)

    def stop_all(self) -> None:
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            self._terminate(proc)

    def _reader(self, launcher: Launcher, proc: subprocess.Popen, hold=None) -> None:
        for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                self.on_line(launcher, line)
        proc.wait()
        if hold:
            hold.release()
        with self._lock:
            if self._procs.get(launcher.id) is proc:
                del self._procs[launcher.id]
        self.on_exit(launcher, proc.returncode)
