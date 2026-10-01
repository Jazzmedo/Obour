"""Entry point: `obour` opens the GUI; subcommands (see `obour --help`) work without it."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import deque

from . import APP_ID, APP_NAME, __version__
from .hostenv import host_env
from .i18n import _, ngettext

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


def find_launcher(store, key: str):
    """An app by id, or by name when the name is unique (case doesn't matter)."""
    launcher = store.get(key)
    if launcher:
        return launcher
    matches = [l for l in store.launchers if l.name.casefold() == key.casefold()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        print(f"{key}: more than one app has this name; use its id:", file=sys.stderr)
        for l in matches:
            print(f"  {l.id}  {l.host}", file=sys.stderr)
    else:
        print(f"{key}: no saved app has this id or name (see: obour list)", file=sys.stderr)
    return None


def list_launchers(as_json: bool) -> int:
    from dataclasses import asdict
    from .config import Store
    launchers = Store().launchers
    if as_json:
        print(json.dumps([asdict(l) for l in launchers], indent=2, ensure_ascii=False))
    for l in [] if as_json else launchers:
        print(f"{l.id}  {l.name}  ({l.host}: {l.exec}){'  [menu]' if l.in_menu else ''}")
    return 0


def list_hosts() -> int:
    from collections import Counter
    from .config import Store
    for host, n in Counter(l.host for l in Store().launchers).items():
        print(f"{host}  ({n} app{'s' if n != 1 else ''})")
    return 0


def add_cli(host: str, command: str, name: str | None, menu: bool) -> int:
    from . import desktop
    from .config import Launcher, Store
    if not command.strip():
        print("The command is empty.", file=sys.stderr)
        return 2
    name = name or os.path.basename(command.split()[0])
    launcher = Launcher(host=host, exec=command, name=name, in_menu=menu)
    Store().upsert(launcher)
    try:
        desktop.sync(launcher)
    except OSError as e:
        print(f"Saved, but the app menu entry couldn't be written: {e}", file=sys.stderr)
    print(launcher.id)
    return 0


def remove_cli(key: str) -> int:
    from . import desktop
    from .config import Store
    store = Store()
    launcher = find_launcher(store, key)
    if launcher is None:
        return 1
    desktop.remove(launcher.id)
    desktop.remove_icon(launcher.id)
    store.remove(launcher.id)
    print(f"Removed {launcher.name} ({launcher.host})")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="obour",
        description=f"{APP_NAME} {__version__}: run apps from other Linux computers as "
                    "windows on this desktop, over SSH. Without a command, opens the window.",
        epilog="Apps are found by id or by name. More: man obour")
    parser.add_argument("-v", "--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", metavar="<command>")
    p = sub.add_parser("list", help="print saved apps and their ids",
                       description="Print each saved app: id, name, computer and command.")
    p.add_argument("--json", action="store_true", help="print every field, as JSON")
    sub.add_parser("hosts", help="print the computers you saved apps for",
                   description="Print each computer that has saved apps, and how many.")
    p = sub.add_parser("launch", help="start a saved app",
                       description="Start a saved app without opening the window, as app-menu "
                                   "entries do. Problems are shown as notifications.")
    p.add_argument("app", help="the app's id or name")
    p = sub.add_parser("add", help="save a new app",
                       description="Save an app that runs COMMAND on HOST. Settings start as "
                                   "\"inherit\"; change them in the window. Prints the new id.")
    p.add_argument("host", help="the other computer: user@host or a name from ~/.ssh/config")
    p.add_argument("command", help="the command to run there; quote it if it has spaces")
    p.add_argument("--name", help="the name to show (default: the command's first word)")
    p.add_argument("--menu", action="store_true", help="also add it to this desktop's app menu")
    p = sub.add_parser("remove", help="delete a saved app",
                       description="Delete a saved app and its app-menu entry.")
    p.add_argument("app", help="the app's id or name")
    p = sub.add_parser("backup", help="save apps, computers and settings to a .zip",
                       description="Save apps, computers, settings and app-menu entries to a "
                                   ".zip file. Passwords are never saved.")
    p.add_argument("file", help="the .zip file to write")
    p.add_argument("--with-ssh-key", action="store_true",
                   help="include your SSH key from ~/.ssh; keep that file private")
    p = sub.add_parser("restore", help="replace apps, computers and settings with a backup's",
                       description="Replace apps, computers and settings with a backup's. The "
                                   "current ones are kept in ~/.local/share/obour/backups.")
    p.add_argument("file", help="the .zip file made by obour backup")
    p = sub.add_parser("help", help="show help for a command",
                       description="Show help for a command, or this list.")
    p.add_argument("command", nargs="?", help="the command to explain")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    # Before anything that shows text: some modules translate labels at import time.
    from . import i18n
    from .config import Settings
    i18n.setup(Settings.load().language)
    if argv[1:] == ["share-daemon"]:  # internal: started by file sharing, not listed
        from .share import Daemon
        return Daemon().serve()
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    if args.cmd == "help":
        if args.command is None:
            parser.print_help()
            return 0
        return main(argv[:1] + [args.command, "--help"])
    if args.cmd == "list":
        return list_launchers(args.json)
    if args.cmd == "hosts":
        return list_hosts()
    if args.cmd == "launch":
        from .config import Store
        store = Store()
        launcher = find_launcher(store, args.app)
        if launcher is None and not any(l.name.casefold() == args.app.casefold()
                                        for l in store.launchers):
            return headless_launch(args.app)  # notifies, for a menu entry of a deleted app
        return headless_launch(launcher.id) if launcher else 1
    if args.cmd == "add":
        return add_cli(args.host, args.command, args.name, args.menu)
    if args.cmd == "remove":
        return remove_cli(args.app)
    if args.cmd == "backup":
        return backup_cli(args.file, include_key=args.with_ssh_key)
    if args.cmd == "restore":
        return restore_cli(args.file)
    return run_gui(argv)
