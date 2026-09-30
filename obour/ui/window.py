"""Main window: saved apps, one-click launch, status bar and log."""

from __future__ import annotations

import time

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import APP_ID, HOMEPAGE, __version__
from .. import auth, backup, desktop, look, remote
from ..config import (Launcher, Settings, Store, file_sharing_for, host_options, resolve_launcher,
                      host_shares_files, set_gpu_broken, set_host_options,
                      set_host_shares_files)
from ..i18n import _, ngettext
from ..launch import (LOOK_OUTDATED, NO_WINDOW_AFTER_S, STARTED_AFTER_S, LaunchManager, crash_line, diagnose,
                      can_learn, display_name, expected_protocol, uses_learned_x11,
                      wayland_failure)
from . import icon_image, labelled, run_async, spinner



class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title=_("Obour"),
                         default_width=560, default_height=640)
        self.set_size_request(360, 420)
        self.store = Store()
        self.settings = Settings.load()
        self.manager = LaunchManager(
            on_line=lambda l, t: GLib.idle_add(self._on_app_line, l, t),
            on_exit=lambda l, rc: GLib.idle_add(self._on_app_exit, l, rc),
            on_no_window=lambda l: GLib.idle_add(self._on_no_window, l),
        )
        self._rows: dict[str, tuple[Adw.ActionRow, Gtk.Button]] = {}
        self._output: dict[str, list[str]] = {}
        self._protocol: dict[str, str] = {}
        self._display: dict[str, str] = {}   # what a running app really uses (see launch)
        self._learn_x11: set[str] = set()    # retried with X11: remember it if that works
        self._starting: dict[str, float] = {}
        self._stopping: set[str] = set()
        self._asking: set[str] = set()
        self._key_retried: dict[str, float] = {}
        self._falling_back: dict[str, str] = {}   # launcher id -> why Wayland failed
        self._gpu: dict[str, bool] = {}   # launcher id -> Wayland session shares the GPU
        self._shared: dict[str, bool] = {}  # launcher id -> files may be copied with host
        self._force_close = False

        self._install_actions()
        self._build()
        self.refresh()
        self.resync_menu_entries()
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView(bottom_bar_style=Adw.ToolbarStyle.RAISED_BORDER)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        add_menu = Gio.Menu()
        add_menu.append(_("Add Apps from a Computer…"), "win.browse")
        add_menu.append(_("Add an App by Its Command…"), "win.add")
        header.pack_start(labelled(Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=add_menu,
                                                  tooltip_text=_("Add Apps (Ctrl+N)"))))
        menu = Gio.Menu()
        menu.append(_("Add Apps from a Computer…"), "win.browse")
        menu.append(_("Add an App by Its Command…"), "win.add")
        menu.append(_("Show Log"), "win.toggle-log")
        backups = Gio.Menu()
        backups.append(_("Back Up…"), "win.backup")
        backups.append(_("Restore From a Backup…"), "win.restore")
        menu.append_section(None, backups)
        section = Gio.Menu()
        section.append(_("Preferences"), "win.preferences")
        section.append(_("About Obour"), "win.about")
        menu.append_section(None, section)
        header.pack_end(labelled(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                                primary=True, tooltip_text=_("Main Menu"))))
        view.add_top_bar(header)

        # empty state
        empty = Adw.StatusPage(
            icon_name="network-server-symbolic",
            title=_("No Remote Apps Yet"),
            description=_("Pick apps from another Linux machine and run them here "
                          "as normal windows, sound included."),
        )
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          halign=Gtk.Align.CENTER)
        buttons.append(Gtk.Button(label=_("Add Apps from a Computer"), action_name="win.browse",
                                  css_classes=["pill", "suggested-action"]))
        buttons.append(Gtk.Button(label=_("Add an App by Its Command"), action_name="win.add",
                                  css_classes=["pill"]))
        buttons.append(Gtk.Button(label=_("Restore From a Backup…"), action_name="win.restore",
                                  css_classes=["flat"]))
        empty.set_child(buttons)

        # list: one group per host, then a way to add another one
        self.groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        another = Gtk.Button(child=Adw.ButtonContent(icon_name="list-add-symbolic",
                                                     label=_("Add Apps from Another Computer")),
                             action_name="win.browse", halign=Gtk.Align.CENTER,
                             css_classes=["pill"], margin_top=6)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18,
                      margin_bottom=18, margin_start=12, margin_end=12)
        box.append(self.groups_box)
        box.append(another)
        clamp = Adw.Clamp(maximum_size=640, child=box)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(scroll, "list")
        view.set_content(self.stack)

        view.add_bottom_bar(self._build_bottom())

    def _build_bottom(self) -> Gtk.Widget:
        self.log_buffer = Gtk.TextBuffer()
        for name, props in {
            "time": {"foreground": "#888888"},
            "cmd": {"foreground": "#888888", "scale": 0.9},
            "ok": {"foreground": "#26a269", "weight": 700},
            "error": {"foreground": "#e01b24", "weight": 700},
            "app": {"weight": 700},
        }.items():
            self.log_buffer.create_tag(name, **props)
        self.log_view = Gtk.TextView(buffer=self.log_buffer, editable=False, cursor_visible=False,
                                     monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                     top_margin=8, bottom_margin=8, left_margin=12,
                                     right_margin=12)
        self.log_scroll = Gtk.ScrolledWindow(min_content_height=170, max_content_height=170,
                                             child=self.log_view)
        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        log_box.append(self.log_scroll)
        log_box.append(Gtk.Separator())
        self.log_revealer = Gtk.Revealer(child=log_box,
                                         transition_type=Gtk.RevealerTransitionType.SLIDE_UP)

        self.status_spinner = spinner(visible=False)
        self.status_icon = Gtk.Image(icon_name="dialog-information-symbolic")
        self.status_label = Gtk.Label(label=_("Ready"), xalign=0, hexpand=True,
                                      ellipsize=Pango.EllipsizeMode.END)
        self.log_toggle = labelled(Gtk.ToggleButton(icon_name="utilities-terminal-symbolic",
                                                    tooltip_text=_("Show Log (Ctrl+L)"),
                                                    css_classes=["flat"]))
        self.log_toggle.connect("toggled", lambda b: self.log_revealer.set_reveal_child(b.get_active()))
        clear = labelled(Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text=_("Clear Log"),
                                    css_classes=["flat"]))
        clear.connect("clicked", lambda *_args: self.log_buffer.set_text(""))
        status = Gtk.Box(spacing=8, margin_start=12, margin_end=6, margin_top=4, margin_bottom=4)
        status.append(self.status_spinner)
        status.append(self.status_icon)
        status.append(self.status_label)
        status.append(clear)
        status.append(self.log_toggle)

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(self.log_revealer)
        bottom.append(status)
        return bottom

    # ------------------------------------------------------------------ actions

    def _install_actions(self) -> None:
        for name, cb, accels in (
            ("browse", self.open_browser, ["<Control>n", "<Control>f"]),
            ("add", self.open_editor, ["<Control><Shift>n"]),
            ("toggle-log", lambda: self.log_toggle.set_active(not self.log_toggle.get_active()),
             ["<Control>l"]),
            ("preferences", self.open_preferences, ["<Control>comma"]),
            ("close", self.close, ["<Control>q", "<Control>w"]),
            ("about", self._show_about, []),
            ("backup", self.back_up, []),
            ("restore", self.restore, []),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(action)
            if accels:
                self.get_application().set_accels_for_action(f"win.{name}", accels)
        for name, cb in (("launch", self.toggle_launch), ("edit", self._edit_by_id),
                         ("menu-toggle", self.toggle_menu_entry),
                         ("remove", self._confirm_remove),
                         # these take a host
                         ("browse-host", lambda h: self.open_browser(host=h)),
                         ("test-host", self.test_host),
                         ("setup-host", self.check_host),
                         ("tailscale-host", self.edit_tailscale),
                         ("share-host", self.toggle_host_sharing),
                         ("host-settings", self.open_host_settings),
                         ("key-login", self.set_up_key_login)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, p, cb=cb: cb(p.get_string()))
            self.add_action(action)

    def open_browser(self, host: str = "", on_pick=None) -> None:
        from .browser import BrowserDialog
        BrowserDialog(self, host=host, on_pick=on_pick).present(self)

    def open_editor(self, launcher: Launcher | None = None) -> None:
        from .edit_dialog import EditDialog
        EditDialog(self, launcher).present(self)

    def open_host_settings(self, host: str) -> None:
        from .host_settings import HostSettingsDialog
        HostSettingsDialog(self, host).present(self)

    def open_preferences(self) -> None:
        from .preferences import PreferencesDialog
        PreferencesDialog(self).present(self)

    def _edit_by_id(self, launcher_id: str) -> None:
        launcher = self.store.get(launcher_id)
        if launcher:
            self.open_editor(launcher)

    def _show_about(self) -> None:
        Adw.AboutDialog(
            application_name=_("Obour"), application_icon=APP_ID, version=__version__,
            comments=_("Obour (عبور) means “crossing”. Run apps from other Linux machines "
                       "as native windows here, with sound."),
            website=HOMEPAGE, issue_url=f"{HOMEPAGE}/issues",
            license_type=Gtk.License.MIT_X11, developer_name="7anafi",
        ).present(self)

    # ------------------------------------------------------------------ launchers

    def refresh(self) -> None:
        while (child := self.groups_box.get_first_child()) is not None:
            self.groups_box.remove(child)
        self._rows.clear()
        hosts: dict[str, list[Launcher]] = {}
        for launcher in self.store.launchers:
            hosts.setdefault(launcher.host, []).append(launcher)
        for host, launchers in hosts.items():
            group = self._make_host_group(host)
            for launcher in launchers:
                group.add(self._make_row(launcher))
            self.groups_box.append(group)
        self.stack.set_visible_child_name("list" if self.store.launchers else "empty")

    @staticmethod
    def _menu(items, target: str) -> Gio.Menu:
        menu = Gio.Menu()
        for label, action in items:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(action, GLib.Variant.new_string(target))
            menu.append_item(item)
        return menu

    def _make_host_group(self, host: str) -> Adw.PreferencesGroup:
        caps = remote.host_caps(host) or {}
        details = [caps.get("os-name", "")]
        ts = host_options(host)
        if ts.get("tailscale"):
            details.append((_("Tailscale {address} (forced)") if ts.get("force_tailscale")
                            else _("Tailscale {address}")).format(address=ts["tailscale"]))
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(host),
                                     description=GLib.markup_escape_text(
                                         " · ".join(d for d in details if d)))
        add = labelled(Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                                  css_classes=["flat"], tooltip_text=_("Add Apps from {host}").format(host=host),
                                  action_name="win.browse-host",
                                  action_target=GLib.Variant.new_string(host)))
        menu = self._menu(((_("Add Apps…"), "win.browse-host"),
                           (_("Host Settings…"), "win.host-settings"),
                           (_("Set Up Host…"), "win.setup-host"),
                           (_("Tailscale…"), "win.tailscale-host"),
                           (_("Stop Sharing Files") if host_shares_files(host)
                            else _("Share Files With This Host"), "win.share-host"),
                           (_("Test Connection"), "win.test-host"),
                           (_("Set Up Key Login…"), "win.key-login")), host)
        more = labelled(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                                       valign=Gtk.Align.CENTER, css_classes=["flat"],
                                       tooltip_text=_("Options for {host}").format(host=host)))
        suffix = Gtk.Box(spacing=2)
        suffix.append(add)
        suffix.append(more)
        group.set_header_suffix(suffix)
        return group

    def _make_row(self, launcher: Launcher) -> Adw.ActionRow:
        row = Adw.ActionRow(use_markup=False, activatable=True)
        row.set_title(launcher.name)
        row.add_prefix(icon_image(launcher.icon, 40))

        button = Gtk.Button(valign=Gtk.Align.CENTER, css_classes=["circular"])
        button.set_action_name("win.launch")
        button.set_action_target_value(GLib.Variant.new_string(launcher.id))

        menu = self._menu(((_("Edit…"), "win.edit"),
                           (_("Remove from App Menu") if launcher.in_menu else _("Add to App Menu"),
                            "win.menu-toggle"),
                           (_("Remove"), "win.remove")), launcher.id)
        more = labelled(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                                       valign=Gtk.Align.CENTER, css_classes=["flat"],
                                       tooltip_text=_("More")),
                        _("More options for {name}").format(name=launcher.name))
        row.add_suffix(button)
        row.add_suffix(more)
        row.connect("activated", lambda _r: self.launch(launcher.id))
        self._rows[launcher.id] = (row, button)
        self._update_row(launcher.id)
        return row

    def _update_row(self, launcher_id: str) -> None:
        entry = self._rows.get(launcher_id)
        launcher = self.store.get(launcher_id)
        if not entry or not launcher:
            return
        row, button = entry
        running = self.manager.is_running(launcher_id)
        starting = launcher_id in self._starting
        if running:
            button.set_icon_name("media-playback-stop-symbolic")
            button.set_tooltip_text(_("Stop"))
            button.remove_css_class("suggested-action")
            shown = self._display.get(launcher_id) or self._protocol.get(launcher_id, "")
            state = _("Starting…") if starting else _("Running")
            row.set_subtitle(" · ".join(filter(None, (state, display_name(shown)))))
        else:
            button.set_icon_name("media-playback-start-symbolic")
            button.set_tooltip_text(_("Launch"))
            button.add_css_class("suggested-action")
            shown = (_("X11 (chosen automatically)") if uses_learned_x11(launcher, self.settings)
                     else display_name(expected_protocol(launcher, self.settings)))
            parts = (shown, _("In app menu") if launcher.in_menu else "")
            row.set_subtitle(" · ".join(filter(None, parts)))
        labelled(button, f"{button.get_tooltip_text()} {launcher.name}")

    def resync_menu_entries(self) -> int:
        """Rewrite menu entries so they point at wherever Obour runs from now (an
        AppImage may have been moved) and use the current name template; drop entries
        of apps that are gone. Returns how many apps are in the menu."""
        count = 0
        for launcher in self.store.launchers:
            try:
                desktop.sync(launcher)
                count += launcher.in_menu
            except OSError:
                pass
        desktop.prune({l.id for l in self.store.launchers})
        return count

    def toggle_host_sharing(self, host: str) -> None:
        on = not host_shares_files(host)
        set_host_shares_files(host, on)
        self.refresh()
        self.toast((_("Files can be copied and pasted with {host}") if on else
                    _("Files won't be shared with {host}")).format(host=host))

    def toggle_menu_entry(self, launcher_id: str) -> None:
        launcher = self.store.get(launcher_id)
        if not launcher:
            return
        launcher.in_menu = not launcher.in_menu
        self.save_launcher(launcher)
        if launcher.in_menu:
            self.toast(_("Added “{entry}” to the app menu").format(
                entry=desktop.menu_name(launcher)))
        else:
            self.toast(_("Removed {name} from the app menu").format(name=launcher.name))

    def save_launcher(self, launcher: Launcher) -> None:
        launcher.icon = desktop.persist_icon(launcher)
        self.store.upsert(launcher)
        try:
            desktop.sync(launcher)
        except OSError as e:
            self.log(_("Couldn't update the app menu entry: {error}").format(error=e),
                     "error", launcher)
        self.refresh()

    def _confirm_remove(self, launcher_id: str) -> None:
        launcher = self.store.get(launcher_id)
        if not launcher:
            return
        dialog = Adw.AlertDialog(heading=_("Remove {name}?").format(name=launcher.name),
                                 body=_("It will also disappear from your app menu. "
                                        "Nothing is changed on the remote machine."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response != "remove":
                return
            desktop.remove(launcher_id)
            desktop.remove_icon(launcher_id)
            self.store.remove(launcher_id)
            self.refresh()
            self.toast(_("Removed {name}").format(name=launcher.name))

        dialog.connect("response", on_response)
        dialog.present(self)

    # ------------------------------------------------------------------ backups

    @staticmethod
    def _zip_dialog(title: str) -> Gtk.FileDialog:
        zips = Gtk.FileFilter(name=_("Obour Backups"))
        zips.add_pattern("*.zip")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(zips)
        return Gtk.FileDialog(title=title, modal=True, filters=filters, default_filter=zips)

    def back_up(self) -> None:
        key = auth.local_public_key()
        dialog = Adw.AlertDialog(
            heading=_("Back Up Obour"),
            body=_("Saves your apps (and which ones are in your app menu), hosts, settings, "
                   "icons and what Obour learned about each host to one .zip file. "
                   "Passwords are never saved."))
        dialog.add_response("cancel", _("Cancel"))
        if key:
            dialog.set_body(dialog.get_body() + "\n\n" + _(
                "With your SSH key, the backup can log in to your hosts by itself on a new "
                "computer, so keep it private."))
            dialog.add_response("key", _("Include SSH Key"))
        dialog.add_response("save", _("Back Up"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        def on_response(_d, response):
            if response in ("save", "key"):
                self._save_backup(response == "key")

        dialog.connect("response", on_response)
        dialog.present(self)

    def _save_backup(self, include_key: bool) -> None:
        chooser = self._zip_dialog(_("Save Backup"))
        chooser.set_initial_name(backup.default_name())

        def on_file(d, result):
            try:
                path = d.save_finish(result).get_path()
            except GLib.Error:
                return                                  # cancelled
            if not path.endswith(".zip"):
                path += ".zip"
            try:
                summary = backup.create(path, include_key=include_key)
            except OSError as e:
                self.toast(_("Couldn't save the backup: {error}").format(error=e.strerror or e))
                return
            self.toast(ngettext("Backed up {count} app", "Backed up {count} apps",
                                summary.apps).format(count=summary.apps))

        chooser.save(self, None, on_file)

    def restore(self) -> None:
        if self._starting or any(self.manager.is_running(l.id) for l in self.store.launchers):
            self.toast(_("Close the running apps first"))
            return

        def on_file(d, result):
            try:
                path = d.open_finish(result).get_path()
            except GLib.Error:
                return
            try:
                summary = backup.read_summary(path)
            except backup.BackupError as e:
                self.toast(str(e))
                return
            self._confirm_restore(path, summary)

        self._zip_dialog(_("Restore From a Backup")).open(self, None, on_file)

    def _confirm_restore(self, path: str, summary) -> None:
        apps = ngettext("{count} app", "{count} apps", summary.apps).format(count=summary.apps)
        hosts = ngettext("{count} computer", "{count} computers",
                         summary.hosts).format(count=summary.hosts)
        body = _("Backup from {date}: {apps} on {hosts}, {menu} in the app menu.").format(
            date=summary.created, apps=apps, hosts=hosts, menu=summary.in_menu)
        if summary.ssh_key:
            body += " " + _("It includes an SSH key.")
        body += "\n\n" + _("Your current apps and settings are replaced; Obour keeps a copy "
                            "of them first.")
        dialog = Adw.AlertDialog(heading=_("Restore This Backup?"), body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response == "restore":
                self._restore(path)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _restore(self, path: str) -> None:
        language = self.settings.language
        try:
            _summary, notes = backup.restore(path)
        except (OSError, backup.BackupError) as e:
            self.toast(_("Couldn't restore the backup: {error}").format(
                error=getattr(e, "strerror", None) or e))
            return
        self.store.load()
        for name, value in vars(Settings.load()).items():
            setattr(self.settings, name, value)
        self.resync_menu_entries()
        self.refresh()
        for note in notes:
            self.log(note)
        self.toast(_("Restored") if self.settings.language == language else
                   _("Restored. Restart Obour to change the language."), timeout=5)

    # ------------------------------------------------------------------ launching

    def toggle_launch(self, launcher_id: str) -> None:
        if self.manager.is_running(launcher_id):
            self._stopping.add(launcher_id)
            self.manager.stop(launcher_id)
        else:
            self.launch(launcher_id)

    def launch(self, launcher_id: str, protocol: str | None = None,
               share: bool | None = None) -> None:
        """share: whether files may be copied with the host (None: work it out, asking
        if the settings say so)."""
        launcher = self.store.get(launcher_id)
        if not launcher or launcher_id in self._starting or self.manager.is_running(launcher_id):
            return
        if protocol is None and remote.missing_user(launcher.host):
            self.confirm_user(launcher.host,
                              lambda h: (self._rename_host(launcher.host, h), self.launch(launcher_id)))
            return
        sharing = file_sharing_for(launcher, self.settings)
        if sharing == "ask" and share is None:
            self._ask_share(launcher, lambda allowed: self.launch(launcher_id, protocol, allowed))
            return
        share = sharing == "allow" if share is None else share
        if share:
            self._warn_missing_sshfs(launcher.host)
        token = time.monotonic()
        self._starting[launcher_id] = token
        self._output[launcher_id] = []
        self._stopping.discard(launcher_id)
        self.set_status(_("Starting {name} from {host}…").format(name=launcher.name,
                                                                  host=launcher.host), "busy")
        dark = Adw.StyleManager.get_default().get_dark()
        run_async(self.manager.launch, launcher, dark, protocol, share,
                  on_done=lambda plan: self._on_launched(launcher, plan, token),
                  on_error=lambda e: self._on_launch_error(launcher, e))

    def _warn_missing_sshfs(self, host: str) -> None:
        """Files can't be pasted on host without sshfs there; say so before launching."""
        caps = remote.host_caps(host) or {}
        if "sshfs" not in caps or caps.get("sshfs"):
            return
        text = _("Install sshfs on {host} to paste your files there").format(host=host)
        self.log(text + ".", "error")
        toast = Adw.Toast(title=GLib.markup_escape_text(text), timeout=10)
        toast.set_button_label(_("Set Up Host…"))
        toast.connect("button-clicked", lambda *_args: self.check_host(host))
        self.toasts.add_toast(toast)

    def _ask_share(self, launcher: Launcher, then) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Copy Files With {host}?").format(host=launcher.host),
            body=_("While {name} runs, you can copy and paste files between this computer "
                   "and {host}. The host can read the files you copy (read-only) during that "
                   "time.")
            .format(name=launcher.name, host=launcher.host))
        dialog.add_response("deny", _("Don't Allow"))
        dialog.add_response("allow", _("Allow"))
        dialog.set_response_appearance("allow", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("allow")
        dialog.set_close_response("deny")
        dialog.connect("response", lambda _d, response: then(response == "allow"))
        dialog.present(self)

    def _on_launched(self, launcher: Launcher, plan, token: float) -> None:
        self._protocol[launcher.id] = plan.protocol
        self._display.pop(launcher.id, None)
        self._shared[launcher.id] = plan.share
        self._gpu[launcher.id] = plan.gpu
        self._update_row(launcher.id)
        GLib.timeout_add_seconds(STARTED_AFTER_S, self._check_started, launcher, token)

    def _check_started(self, launcher: Launcher, token: float) -> bool:
        if self._starting.get(launcher.id) == token:
            del self._starting[launcher.id]
            if self.manager.is_running(launcher.id):
                self.log(_("Connected; the app is running."), "ok", launcher)
                if launcher.id in self._learn_x11 and self._protocol.get(launcher.id) == "x11":
                    self._learn(launcher, _("{name} works with X11, so Obour will open it "
                                            "with X11 from now on."))
                self.set_status(_("{name} is running from {host}").format(
                    name=launcher.name, host=launcher.host), "ok")
            self._update_row(launcher.id)
        return False

    def _on_launch_error(self, launcher: Launcher, error: Exception) -> None:
        self._starting.pop(launcher.id, None)
        self._update_row(launcher.id)
        if remote.is_auth_error(str(error)):
            self.request_login(launcher.host, retry=lambda: self.launch(launcher.id))
            return
        self.report_error(launcher, str(error))

    def _on_app_line(self, launcher: Launcher, text: str) -> None:
        if text.startswith("obour: display "):
            self._on_display(launcher, text.split()[-1])
            return
        if text == LOOK_OUTDATED:
            look.forget(launcher.host)
            self.log(_("Your desktop look on {host} isn't the one Obour copied there; it's "
                       "copied again at the next launch.").format(host=launcher.host),
                     "info", launcher)
            return
        buf = self._output.setdefault(launcher.id, [])
        buf.append(text)
        del buf[:-60]
        self.log(text, "cmd" if text.startswith("  $ ") else "info", launcher)

    def _on_display(self, launcher: Launcher, kind: str) -> None:
        """The host reported what the app is connected to (Wayland mode only)."""
        if self._display.get(launcher.id) == kind:
            return
        self._display[launcher.id] = kind
        self.log({"wayland": _("{name} is using Wayland."),
                  "x11": _("{name} doesn't support Wayland and is using X11 instead "
                           "(through ssh -Y)."),
                  "both": _("{name} is using both Wayland and X11.")}.get(
                      kind, "{name}: " + kind).format(name=launcher.name), "info", launcher)
        if kind == "x11":
            # it doesn't use Wayland anyway: next time skip waypipe
            self._learn(launcher, _("Obour will open {name} with X11 from now on."))
        self._update_row(launcher.id)

    def _learn(self, launcher: Launcher, message: str) -> None:
        """Remember that launcher works with X11 (only used on Automatic display)."""
        self._learn_x11.discard(launcher.id)
        if not can_learn(launcher, self.settings):
            return
        if self.store.set_learned_protocol(launcher.id, "x11"):
            self.log(message.format(name=launcher.name) + " " + _(
                "To try Wayland again: Edit… → Try Wayland Again."), "info", launcher)
            self._update_row(launcher.id)

    def _on_no_window(self, launcher: Launcher) -> None:
        self._falling_back[launcher.id] = _(
            "no Wayland window appeared within {seconds} seconds").format(
                seconds=NO_WINDOW_AFTER_S)

    def _retry_without_gpu(self, launcher: Launcher) -> None:
        set_gpu_broken(launcher.host)
        self.log(_("{name} showed no window within {seconds} seconds with GPU Acceleration, "
                   "which freezes waypipe on some hosts. Obour turned it off for {host} and "
                   "is trying Wayland again without it. (Turning GPU Acceleration off and on "
                   "in Preferences tries it again.)").format(
                     name=launcher.name, seconds=NO_WINDOW_AFTER_S, host=launcher.host),
                 "error", launcher)
        self.set_status(_("{name}: retrying Wayland without GPU").format(name=launcher.name),
                        "busy")
        toast = Adw.Toast(title=GLib.markup_escape_text(
            _("GPU Acceleration doesn't work on {host} — turned off there").format(
                host=launcher.host)), timeout=8)
        toast.set_button_label(_("Details"))
        toast.connect("button-clicked", lambda *_args: self.log_toggle.set_active(True))
        self.toasts.add_toast(toast)
        self.launch(launcher.id, protocol="wayland", share=self._shared.get(launcher.id, False))

    def _fall_back_to_x11(self, launcher: Launcher, why: str) -> None:
        message = _("{name} couldn't open over Wayland ({reason}), so Obour is opening it "
                    "with X11 instead. If that works, Obour remembers it for this app. To "
                    "stop this, turn off “Try X11 When Wayland Fails” in "
                    "Preferences.").format(name=launcher.name, reason=why)
        self._learn_x11.add(launcher.id)
        self.log(message, "error", launcher)
        self.set_status(_("{name}: Wayland failed, using X11").format(name=launcher.name),
                        "error")
        toast = Adw.Toast(title=GLib.markup_escape_text(
            _("{name} didn't open over Wayland — using X11").format(name=launcher.name)),
            timeout=8)
        toast.set_button_label(_("Details"))
        toast.connect("button-clicked", lambda *_args: self.log_toggle.set_active(True))
        self.toasts.add_toast(toast)
        self.launch(launcher.id, protocol="x11", share=self._shared.get(launcher.id, False))

    def _on_app_exit(self, launcher: Launcher, returncode: int) -> None:
        self._display.pop(launcher.id, None)
        protocol = self._protocol.get(launcher.id, "x11")
        was_starting = self._starting.pop(launcher.id, None) is not None
        stopped = launcher.id in self._stopping
        self._stopping.discard(launcher.id)
        self._update_row(launcher.id)
        why = self._falling_back.pop(launcher.id, None)
        if why and not stopped:
            if self._gpu.get(launcher.id):
                self._retry_without_gpu(launcher)
            elif resolve_launcher(launcher, self.settings).settings.fallback_x11:
                self._fall_back_to_x11(launcher, why)
            else:
                self.report_error(launcher, _(
                    "{name} showed no window over Wayland within {seconds} seconds.").format(
                        name=launcher.name, seconds=NO_WINDOW_AFTER_S))
            return
        output = self._output.get(launcher.id, [])
        if was_starting:
            self._learn_x11.discard(launcher.id)     # the X11 try didn't get going either
        if (protocol == "wayland" and was_starting and not stopped
                and resolve_launcher(launcher, self.settings).settings.fallback_x11):
            reason = wayland_failure(output, returncode, launcher.host)
            if reason:
                self._fall_back_to_x11(launcher, reason)
                return
        if returncode == 0 and not stopped and crash_line(output):
            self.report_error(launcher, diagnose(output, returncode, protocol, launcher.host))
            return
        if returncode == 0 and was_starting and not stopped:
            self.log(_("The app exited right after starting. If no window appeared, "
                       "check the messages above."), "error", launcher)
            self.set_status(_("{name} exited right after starting").format(name=launcher.name),
                            "error")
            self.log_toggle.set_active(True)
            return
        if stopped or returncode == 0:
            self.log(_("Closed (exit code {code}).").format(code=returncode), "info", launcher)
            self.set_status(_("{name} closed").format(name=launcher.name), "info")
            return
        message = diagnose(self._output.get(launcher.id, []), returncode,
                           self._protocol.get(launcher.id, "x11"), launcher.host)
        if not was_starting and returncode in (-15, 143, 129, -1):
            self.log(_("Session ended (exit code {code}).").format(code=returncode), "info",
                     launcher)
            self.set_status(_("{name} closed").format(name=launcher.name), "info")
            return
        if remote.is_auth_error(message):
            self.request_login(launcher.host, retry=lambda: self.launch(launcher.id))
            return
        self.report_error(launcher, message)

    # ------------------------------------------------------------------ password login

    def request_login(self, host: str, retry=None, error: str = "",
                      key_checked: bool = False) -> None:
        """Ask for host's password (once at a time per host), then set up key login or
        keep the password for this session, and run retry() on success.
        Key login is always tried first: the dialog only appears if it really fails."""
        from .login_dialog import ask_login
        if host in self._asking:
            return
        self._asking.add(host)

        if not key_checked:
            self.set_status(_("Trying key login to {host}…").format(host=host), "busy")

            def checked(result: tuple[str, str]) -> None:
                state, message = result
                self._asking.discard(host)
                if state == "ok":
                    now = time.monotonic()
                    if now - self._key_retried.get(host, -60.0) < 30:
                        # the key works, yet the retried action failed again
                        self.report_error(None, _(
                            "Key login to {host} works, but the connection still failed. "
                            "See the log.").format(host=host))
                        return
                    self._key_retried[host] = now
                    self.log(_("Key login to {host} works; no password needed.").format(
                        host=host), "ok")
                    self.set_status(_("Connected to {host} with your key").format(host=host),
                                    "ok")
                    if retry:
                        retry()
                elif state == "error":
                    self.report_error(None, message)
                else:
                    self.log(message, "error")
                    self.request_login(host, retry, error=error or message, key_checked=True)

            run_async(remote.key_login_check, host, on_done=checked,
                      on_error=lambda e: checked(("error", str(e))))
            return

        self.set_status(_("{host} needs a password").format(host=host), "info")

        def login(password: str, set_up_key: bool) -> None:
            self.set_status(_("Logging in to {host}…").format(host=host), "busy")
            self.log((_("Logging in to {host} with a password and setting up key login…")
                      if set_up_key else _("Logging in to {host} with a password…"))
                     .format(host=host))
            action = remote.setup_key_login if set_up_key else remote.login_with_password
            run_async(action, host, password,
                      on_done=lambda summary: succeeded(summary),
                      on_error=lambda e: failed(str(e)))

        def succeeded(summary) -> None:
            self._asking.discard(host)
            text = summary or _("Logged in to {host}. Obour keeps the password until it "
                                "quits.").format(host=host)
            self.log(text, "ok")
            self.set_status(text, "ok")
            self.toast(text.split(". ")[0] + ".", timeout=5)
            if retry:
                retry()

        def failed(message: str) -> None:
            self._asking.discard(host)
            if auth.has_password(host):
                # key copied but the host still wants the password: carry on with it
                self.log(message, "error")
                self.toast(message.split(". ")[0] + ".", timeout=6)
                if retry:
                    retry()
                return
            self.log(message, "error")
            self.request_login(host, retry, error=message, key_checked=True)

        def cancelled() -> None:
            self._asking.discard(host)
            self.set_status(_("Login to {host} cancelled").format(host=host), "info")

        ask_login(self, host, login, cancelled, error=error)

    def set_up_key_login(self, host: str) -> None:
        self.set_status(_("Checking key login for {host}…").format(host=host), "busy")

        def checked(result: tuple[str, str]) -> None:
            state, message = result
            if state == "ok":
                self.set_status(_("Key login already works for {host}").format(host=host),
                                "ok")
                self.toast(_("Key login already works for {host}").format(host=host))
            elif state == "error":
                self.report_error(None, message)
            else:
                self.request_login(host, error=message, key_checked=True)

        run_async(remote.key_login_check, host, on_done=checked)

    def report_error(self, launcher: Launcher | None, message: str) -> None:
        self.log(message, "error", launcher)
        self.set_status(message, "error")
        self.log_toggle.set_active(True)
        name = launcher.name if launcher else "Obour"
        self.toast(f"{name}: {message.splitlines()[0]}", timeout=6)

    def _rename_host(self, old: str, new: str) -> None:
        """Point every saved app of host old at new (e.g. after adding the user name)."""
        for launcher in list(self.store.launchers):
            if launcher.host == old:
                launcher.host = new
                self.save_launcher(launcher)
        opts = host_options(old)
        if opts.get("tailscale"):
            set_host_options(new, opts["tailscale"], bool(opts.get("force_tailscale")))
            set_host_options(old, "", False)

    def confirm_user(self, host: str, then) -> None:
        """If host has no user name, ask which account to log in as, then call
        then(host) with the user filled in (“user@host”). Otherwise call it right away."""
        import getpass
        if not remote.missing_user(host):
            then(host)
            return
        local = getpass.getuser()
        dialog = Adw.AlertDialog(
            heading=_("Which User?"),
            body=_("You entered {host} without a user name, so Obour would log in as "
                   "“{user}”, your user on this computer. If your account on {host} has a "
                   "different name, enter it here.").format(host=host, user=local))
        user_row = Adw.EntryRow(title=_("User name on {host}").format(host=host), text=local,
                                activates_default=True)
        group = Adw.PreferencesGroup()
        group.add(user_row)
        dialog.set_extra_child(group)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("continue", _("Continue"))
        dialog.set_response_appearance("continue", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("continue")
        dialog.set_close_response("cancel")
        user_row.connect("changed", lambda r: dialog.set_response_enabled(
            "continue", bool(r.get_text().strip()) and "@" not in r.get_text()
            and " " not in r.get_text().strip()))

        def on_response(_d, response):
            if response == "continue":
                then(remote.with_user(host, user_row.get_text().strip()))

        dialog.connect("response", on_response)
        dialog.present(self)
        user_row.grab_focus()

    def edit_tailscale(self, host: str) -> None:
        from . import TailscaleRows
        rows = TailscaleRows(host)
        group = Adw.PreferencesGroup()
        group.add(rows.address)
        group.add(rows.force)
        dialog = Adw.AlertDialog(heading="Tailscale",
                                 body=_("A second address for {host}, for when you're away "
                                        "from its network. It applies to all apps from this "
                                        "host.").format(host=host))
        dialog.set_extra_child(group)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        def on_response(_d, response):
            if response == "save":
                rows.save(host)
                self.refresh()
                self.toast(_("Saved Tailscale settings for {host}").format(host=host))

        dialog.connect("response", on_response)
        dialog.present(self)

    # ------------------------------------------------------------------ host setup

    def check_host(self, host: str, auto: bool = False) -> None:
        """Look at what host has installed and offer to install what's missing.
        With auto (a host was just added), only speak up once per host, and only
        when something is missing."""
        from .host_setup import HostSetupDialog
        if auto and (not self.settings.check_hosts
                     or (remote.host_caps(host) or {}).get("setup-seen")):
            return
        if not auto:
            self.set_status(_("Checking what {host} has installed…").format(host=host), "busy")

        def checked(caps: dict) -> None:
            dialog = HostSetupDialog(self, host, caps)
            if auto and not dialog.packages and not dialog.cursor_theme:
                remote.mark_setup_seen(host)
                return
            remote.mark_setup_seen(host)
            self.refresh()
            if not auto:
                self.set_status(_("Checked {host}").format(host=host), "ok")
            dialog.present(self)

        def failed(error: Exception) -> None:
            if remote.is_auth_error(str(error)):
                self.request_login(host, retry=lambda: self.check_host(host, auto))
            elif not auto:
                self.report_error(None, _("Couldn't check {host}: {error}").format(
                    host=host, error=error))

        run_async(remote.probe_caps, host, on_done=checked, on_error=failed)

    # ------------------------------------------------------------------ connection test

    def test_host(self, host: str, on_result=None) -> None:
        self.set_status(_("Testing connection to {host}…").format(host=host), "busy")
        self.log(_("Testing connection to {host}…").format(host=host))

        def done(report: remote.TestReport):
            for line in report.lines():
                self.log("  " + line, "ok" if line.startswith("✔") else "error")
            if report.ok:
                missing = [c for c in report.checks if not c[1]]
                self.set_status((ngettext("Connection to {host} works ({count} optional tool "
                                          "missing)",
                                          "Connection to {host} works ({count} optional tools "
                                          "missing)", len(missing)) if missing
                                 else _("Connection to {host} works")).format(
                                     host=host, count=len(missing)), "ok")
                self.toast(_("Connection to {host} works").format(host=host))
            elif remote.is_auth_error(report.error):
                self.request_login(host, retry=lambda: self.test_host(host, on_result))
            else:
                self.set_status(report.error, "error")
                self.log_toggle.set_active(True)
                self.toast(_("Can't connect to {host}").format(host=host), timeout=5)
            if on_result:
                on_result(report)

        run_async(remote.test_connection, host, on_done=done,
                  on_error=lambda e: done(remote.TestReport(host, ok=False, error=str(e))))

    # ------------------------------------------------------------------ status & log

    def set_status(self, text: str, kind: str = "info") -> None:
        self.status_label.set_text(text.splitlines()[0] if text else "")
        self.status_label.set_tooltip_text(text)
        busy = kind == "busy"
        self.status_spinner.set_visible(busy)
        self.status_icon.set_visible(not busy)
        self.status_icon.set_from_icon_name({
            "ok": "object-select-symbolic",
            "error": "dialog-error-symbolic",
        }.get(kind, "dialog-information-symbolic"))
        for cls in ("success", "error"):
            self.status_label.remove_css_class(cls)
            self.status_icon.remove_css_class(cls)
        if kind in ("ok", "error"):
            cls = "success" if kind == "ok" else "error"
            self.status_label.add_css_class(cls)
            self.status_icon.add_css_class(cls)
            self.announce(text, Gtk.AccessibleAnnouncementPriority.HIGH if kind == "error"
                          else Gtk.AccessibleAnnouncementPriority.MEDIUM)

    def log(self, text: str, kind: str = "info", launcher: Launcher | None = None) -> None:
        buf = self.log_buffer
        end = buf.get_end_iter()
        buf.insert_with_tags_by_name(end, time.strftime("%H:%M:%S "), "time")
        if launcher:
            buf.insert_with_tags_by_name(buf.get_end_iter(), f"[{launcher.name}] ", "app")
        if kind in ("ok", "error", "cmd"):
            buf.insert_with_tags_by_name(buf.get_end_iter(), text + "\n", kind)
        else:
            buf.insert(buf.get_end_iter(), text + "\n")
        GLib.idle_add(self._scroll_log_to_end)

    def _scroll_log_to_end(self) -> bool:
        adj = self.log_scroll.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def toast(self, text: str, timeout: int = 3) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout))

    # ------------------------------------------------------------------ closing

    def _on_close_request(self, *_args) -> bool:
        count = self.manager.running_count()
        if count == 0 or self._force_close:
            return False
        dialog = Adw.AlertDialog(
            heading=_("Close Running Apps?"),
            body=ngettext("{count} remote app is still running. Closing Obour will close it "
                          "too.",
                          "{count} remote apps are still running. Closing Obour will close "
                          "them too.", count).format(count=count))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("close", _("Close All"))
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response == "close":
                self._stopping.update(l.id for l in self.store.launchers)
                self.manager.stop_all()
                self._force_close = True
                self.close()

        dialog.connect("response", on_response)
        dialog.present(self)
        return True
