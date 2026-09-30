"""Entry point: `obour` opens the GUI, `obour launch <id>` starts one app headlessly."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections import deque

from . import APP_ID, APP_NAME, __version__
from .hostenv import host_env
from .i18n import _, ngettext

USAGE = f"""\
{APP_NAME} {__version__} — run apps from remote Linux machines as local windows.

usage:
  obour                 open the window
  obour launch <id>     start a saved app (used by app-menu entries)
  obour list            print saved apps and their ids
  obour backup <file.zip> [--with-ssh-key]
                        save apps, hosts, settings and app-menu entries
  obour restore <file.zip>
                        replace them with a backup's (the current ones are kept
                        in ~/.local/share/obour/backups)
"""


def _notify(title: str, body: str) -> None:
    print(f"{title}: {body}", file=sys.stderr)
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "--app-name", APP_NAME, "--icon", APP_ID, title, body],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=host_env())


def headless_launch(launcher_id: str) -> int:
    from .config import Store

    launcher = Store().get(launcher_id)
    if launcher is None:
        _notify(APP_NAME, _("This app is no longer saved in Obour."))
        return 1
    from .config import Settings, file_sharing_for, set_gpu_broken

    sharing = file_sharing_for(launcher)
    share = sharing == "allow" or (sharing == "ask" and _ask_share(launcher))
    if share:
        from .remote import host_caps
        caps = host_caps(launcher.host) or {}
        if "sshfs" in caps and not caps["sshfs"]:
            _notify(_("Install sshfs on {host} to paste your files there").format(
                        host=launcher.host),
                    _("Open Obour, then the host's ⋮ menu → Set Up Host…"))
    from .config import resolve_launcher
    from .launch import NO_WINDOW_AFTER_S
    from .launch import can_learn
    automatic = can_learn(launcher)
    run = _run_session(launcher, None, share)
    if run.why == NO_WINDOW and run.gpu:
        set_gpu_broken(launcher.host)
        _notify(_("{name}: GPU Acceleration turned off for {host}").format(
                    name=launcher.name, host=launcher.host),
                _("No window appeared with GPU Acceleration, which freezes waypipe on some "
                  "hosts. Trying Wayland again without it."))
        run = _run_session(launcher, "wayland", share)
    if run.display == "x11" and automatic:
        Store().set_learned_protocol(launcher.id, "x11")   # it doesn't use Wayland anyway
    if run.why and resolve_launcher(launcher).settings.fallback_x11:
        reason = (_("no Wayland window appeared within {seconds} seconds").format(
                      seconds=NO_WINDOW_AFTER_S) if run.why == NO_WINDOW else run.why)
        _notify(_("{name}: Wayland failed, using X11").format(name=launcher.name),
                _("{name} couldn't open over Wayland ({reason}), so Obour is opening it "
                  "with X11 instead. If that works, Obour remembers it for this app. To "
                  "stop this, turn off “Try X11 When Wayland Fails” in "
                  "Preferences.").format(name=launcher.name, reason=reason))
        run = _run_session(launcher, "x11", share)
        if run.started and automatic:
            Store().set_learned_protocol(launcher.id, "x11")
    return run.rc


def _ask_share(launcher) -> bool:
    """Ask with a notification (menu launches have no window). No answer means no."""
    if not shutil.which("notify-send"):
        return False
    try:
        proc = subprocess.run(
            ["notify-send", "--app-name", APP_NAME, "--icon", APP_ID, "--wait",
             "--expire-time", "60000", "--action", "allow=" + _("Allow"),
             "--action", "deny=" + _("Don't Allow"),
             _("Copy Files With {host}?").format(host=launcher.host),
             _("While {name} runs, you can copy and paste files between this computer "
               "and {host}. The host can read the files you copy (read-only) during that "
                   "time.")
             .format(name=launcher.name, host=launcher.host)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=70, env=host_env())
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.stdout.decode(errors="replace").strip() == "allow"


NO_WINDOW = "no-window"


class _Run:
    """How one launch went: exit code; why Wayland failed (NO_WINDOW, a reason from
    launch.wayland_failure, or ""); whether it shared the GPU; whether it got past
    starting; what the host saw it use ("wayland", "x11", "both" or "")."""

    def __init__(self, rc: int, why: str = "", gpu: bool = False, started: bool = False,
                 display: str = ""):
        self.rc, self.why, self.gpu, self.started, self.display = rc, why, gpu, started, display


def _run_session(launcher, protocol: str | None, share: bool = False) -> _Run:
    """Run one launch until it ends."""
    import signal
    import threading
    from .config import CACHE_DIR
    from .launch import (LOOK_OUTDATED, NO_WINDOW_AFTER_S, NO_WINDOW_BELOW_BYTES, STARTED_AFTER_S,
                         build_plan, crash_line, diagnose, session_traffic, start_sharing,
                         wayland_failure)
    from .remote import is_auth_error

    try:
        plan = build_plan(launcher, protocol=protocol)
    except RuntimeError as e:
        message = str(e)
        if is_auth_error(message):
            message = _needs_password(launcher.host)
        _notify(_("Couldn't start {name}").format(name=launcher.name), message)
        return _Run(1)

    tail: deque[str] = deque(maxlen=60)
    tail.extend(plan.notes)
    hold = start_sharing(launcher, share, tail.append, plan)
    started = time.monotonic()
    # stdin stays open while we wait: the remote side ends the app when it closes.
    proc = subprocess.Popen(plan.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.PIPE, env=plan.env, start_new_session=True)
    no_window = threading.Event()

    def check_window() -> None:
        if proc.poll() is None and session_traffic(proc.pid) < NO_WINDOW_BELOW_BYTES:
            no_window.set()
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    if plan.fallback:
        timer = threading.Timer(NO_WINDOW_AFTER_S, check_window)
        timer.daemon = True
        timer.start()
    display = ""
    for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line.startswith("obour: display "):
            display = line.split()[-1]
        elif line == LOOK_OUTDATED:
            from .look import forget
            forget(launcher.host)                # copied again at the next launch
        elif line:
            tail.append(line)
            print(line, file=sys.stderr)
    rc = proc.wait()
    lasted = time.monotonic() - started
    if hold:
        hold.release()

    log_dir = os.path.join(CACHE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, f"{launcher.id}.log"), "w", encoding="utf-8") as f:
        f.write("$ " + " ".join(plan.argv) + "\n" + "\n".join(tail) + f"\n[exit {rc}]\n")

    crashed = bool(crash_line(list(tail)))
    result = _Run(rc, gpu=plan.gpu, started=lasted >= STARTED_AFTER_S and not crashed,
                  display=display)
    if no_window.is_set():
        result.why = NO_WINDOW
        return result
    if plan.protocol == "wayland" and plan.fallback and lasted < STARTED_AFTER_S:
        result.why = wayland_failure(list(tail), rc, launcher.host)
        if result.why:
            return result                       # the caller tries X11
    if rc != 0 and not (rc in (-15, 143, 129) and lasted > 5) or rc == 0 and crashed:
        message = diagnose(list(tail), rc, plan.protocol, launcher.host)
        if is_auth_error(message):
            message = _needs_password(launcher.host)
        _notify(_("{name} stopped").format(name=launcher.name), message)
    return result


def _needs_password(host: str) -> str:
    return _("{host} needs a password. Open Obour and launch it once to log in.").format(
        host=host)


def list_launchers() -> int:
    from .config import Store
    for l in Store().launchers:
        print(f"{l.id}  {l.name}  ({l.host}: {l.exec})")
    return 0


def run_gui(argv: list[str]) -> int:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, Gtk

    from . import i18n
    from .ui.window import MainWindow

    class ObourApp(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        def do_startup(self):
            Adw.Application.do_startup(self)
            # The AppImage's GTK can't read the desktop's settings, and would fall
            # back to the default cursor; set the desktop's one explicitly.
            from .cursor import local_cursor
            theme, size = local_cursor()
            settings = Gtk.Settings.get_default()
            if theme and settings is not None:
                settings.props.gtk_cursor_theme_name = theme
                settings.props.gtk_cursor_theme_size = size

        def do_activate(self):
            window = self.props.active_window or MainWindow(self)
            window.present()

    Gtk.Window.set_default_icon_name(APP_ID)
    if i18n.is_rtl():
        Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)
    return ObourApp().run(argv[:1])


def backup_cli(path: str, include_key: bool) -> int:
    from . import backup
    try:
        summary = backup.create(path, include_key=include_key)
    except OSError as e:
        print(f"{path}: {e.strerror or e}", file=sys.stderr)
        return 1
    print(ngettext("Backed up {count} app to {path}", "Backed up {count} apps to {path}",
                   summary.apps).format(count=summary.apps, path=path))
    return 0


def restore_cli(path: str) -> int:
    from . import backup, desktop
    from .config import Store
    try:
        summary, notes = backup.restore(path)
    except (OSError, backup.BackupError) as e:
        print(f"{path}: {getattr(e, 'strerror', None) or e}", file=sys.stderr)
        return 1
    store = Store()
    for launcher in store.launchers:
        try:
            desktop.sync(launcher)
        except OSError:
            pass
    desktop.prune({l.id for l in store.launchers})
    for note in notes:
        print(note)
    print(ngettext("Restored {count} app from {date}", "Restored {count} apps from {date}",
                   summary.apps).format(count=summary.apps, date=summary.created))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = argv[1:]
    # Before anything that shows text: some modules translate labels at import time.
    from . import i18n
    from .config import Settings
    i18n.setup(Settings.load().language)
    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if args and args[0] in ("-v", "--version"):
        print(__version__)
        return 0
    if args and args[0] == "launch":
        if len(args) != 2:
            print(USAGE, file=sys.stderr)
            return 2
        return headless_launch(args[1])
    if args and args[0] == "list":
        return list_launchers()
    if args and args[0] == "backup" and len(args) in (2, 3) and args[2:] in ([], ["--with-ssh-key"]):
        return backup_cli(args[1], include_key=len(args) == 3)
    if args and args[0] == "restore" and len(args) == 2:
        return restore_cli(args[1])
    if args and args[0] == "share-daemon":
        from .share import Daemon
        return Daemon().serve()
    if args:
        print(USAGE, file=sys.stderr)
        return 2
    return run_gui(argv)
