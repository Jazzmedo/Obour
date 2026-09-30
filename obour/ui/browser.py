"""Browse a remote machine's installed apps and add them in one click."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .. import remote
from ..i18n import _, ngettext
from . import host_menu_button, icon_image, run_async, set_icon, spinner


class BrowserDialog(Adw.Dialog):
    def __init__(self, window, host: str = "", on_pick=None):
        """on_pick(host, RemoteApp): if given, the dialog selects one app instead of adding."""
        super().__init__(title=_("Browse Remote Apps"), content_width=520, content_height=640)
        self.window = window
        self.on_pick = on_pick
        self._host = ""
        self._apps: list[remote.RemoteApp] = []
        self._images: dict[str, list[Gtk.Image]] = {}
        self._generation = 0

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.toasts = Adw.ToastOverlay(child=view)
        self.set_child(self.toasts)

        self.host_entry = Gtk.Entry(placeholder_text=_("user@host or SSH alias"), hexpand=True,
                                    text=host)
        self.host_entry.connect("activate", lambda *_args: self.connect_host())
        self.connect_button = Gtk.Button(label=_("Connect"), css_classes=["suggested-action"])
        self.connect_button.connect("clicked", lambda *_args: self.connect_host())
        bar = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        bar.append(self.host_entry)
        bar.append(host_menu_button(window.store.known_hosts(), self._select_host))
        bar.append(self.connect_button)

        self.search = Gtk.SearchEntry(placeholder_text=_("Search apps"), visible=False,
                                      margin_start=12, margin_end=12, margin_bottom=6)
        self.search.connect("search-changed", lambda *_args: self.listbox.invalidate_filter())

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                   css_classes=["boxed-list"], valign=Gtk.Align.START)
        self.listbox.set_filter_func(self._filter)
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=12, margin_end=12,
                           margin_top=6, margin_bottom=18)
        list_box.append(self.listbox)
        list_page = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                       child=Adw.Clamp(maximum_size=600, child=list_box))

        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        loading.append(spinner(48))
        self.loading_label = Gtk.Label(css_classes=["dim-label"])
        loading.append(self.loading_label)

        self.error_page = Adw.StatusPage(icon_name="network-offline-symbolic",
                                         title=_("Couldn't Browse This Host"))
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Adw.StatusPage(
            icon_name="network-server-symbolic", title=_("Browse a Remote Machine"),
            description=_("Enter a host to see its installed apps.")), "idle")
        self.stack.add_named(loading, "loading")
        self.stack.add_named(self.error_page, "error")
        self.stack.add_named(Adw.StatusPage(icon_name="system-search-symbolic",
                                            title=_("No Apps Found")), "empty")
        self.stack.add_named(list_page, "list")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(bar)
        content.append(self.search)
        content.append(self.stack)
        view.set_content(content)

        if host:
            self.connect_host()

    # ------------------------------------------------------------------

    def _select_host(self, host: str) -> None:
        self.host_entry.set_text(host)
        self.connect_host()

    def connect_host(self) -> None:
        host = self.host_entry.get_text().strip()
        if not host:
            self.host_entry.grab_focus()
            return
        self.window.confirm_user(host, self._connect)

    def _connect(self, host: str) -> None:
        self.host_entry.set_text(host)
        self._host = host
        self._generation += 1
        generation = self._generation
        self.loading_label.set_text(_("Looking for apps on {host}…").format(host=host))
        self.stack.set_visible_child_name("loading")
        self.search.set_visible(False)
        self.connect_button.set_sensitive(False)
        self.window.set_status(_("Scanning apps on {host}…").format(host=host), "busy")
        self.window.log(_("Scanning installed apps on {host}…").format(host=host))
        run_async(remote.scan_apps, host,
                  on_done=lambda apps: self._on_apps(generation, host, apps),
                  on_error=lambda e: self._on_error(generation, host, e))

    def _on_error(self, generation: int, host: str, error: Exception) -> None:
        if generation != self._generation:
            return
        self.connect_button.set_sensitive(True)
        self.error_page.set_description(str(error))
        self.stack.set_visible_child_name("error")
        if remote.is_auth_error(str(error)):
            self.window.request_login(host, retry=self.connect_host)
            return
        self.window.report_error(None, _("Couldn't browse {host}: {error}").format(host=host, error=error))

    def _on_apps(self, generation: int, host: str, apps: list[remote.RemoteApp]) -> None:
        if generation != self._generation:
            return
        self.connect_button.set_sensitive(True)
        self._apps = apps
        self._images.clear()
        while (child := self.listbox.get_first_child()) is not None:
            self.listbox.remove(child)
        existing = {(l.host, l.exec) for l in self.window.store.launchers}
        for app in apps:
            self.listbox.append(self._make_row(app, (host, app.exec) in existing))
        found = ngettext("Found {count} app on {host}", "Found {count} apps on {host}",
                         len(apps)).format(count=len(apps), host=host)
        self.window.log(found + ".", "ok")
        self.window.set_status(found, "ok")
        if not apps:
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("list")
        self.search.set_visible(True)
        self.search.grab_focus()
        run_async(remote.fetch_icons, host, [a.icon for a in apps],
                  on_done=lambda icons: self._on_icons(generation, host, icons))

    def _make_row(self, app: remote.RemoteApp, added: bool) -> Adw.ActionRow:
        row = Adw.ActionRow(use_markup=False, subtitle_lines=1)
        row.set_title(app.name)
        row.set_subtitle(app.comment or app.exec)
        row._app = app
        image = icon_image(app.icon, 40)
        self._images.setdefault(app.icon, []).append(image)
        row.add_prefix(image)
        if self.on_pick:
            button = Gtk.Button(label=_("Select"), valign=Gtk.Align.CENTER, css_classes=["flat"])
            button.connect("clicked", lambda *_args: self._pick(app))
        else:
            button = Gtk.Button(valign=Gtk.Align.CENTER)
            if added:
                self._mark_added(button)
            else:
                button.set_label(_("Add"))
                button.add_css_class("suggested-action")
                button.connect("clicked", lambda b: self._add(app, b))
        row.add_suffix(button)
        row.set_activatable_widget(button)
        return row

    @staticmethod
    def _mark_added(button: Gtk.Button) -> None:
        button.set_icon_name("object-select-symbolic")
        button.set_tooltip_text(_("Already added"))
        button.remove_css_class("suggested-action")
        button.add_css_class("flat")
        button.set_sensitive(False)

    def _filter(self, row: Gtk.ListBoxRow) -> bool:
        query = self.search.get_text().strip().lower()
        if not query:
            return True
        app = getattr(row, "_app", None)
        return app is None or query in f"{app.name} {app.comment} {app.exec}".lower()

    def _on_icons(self, generation: int, host: str, icons: dict[str, str]) -> None:
        if generation != self._generation:
            return
        for app in self._apps:
            path = icons.get(app.icon)
            if path:
                app.icon_path = path
        for name, images in self._images.items():
            if name in icons:
                for image in images:
                    set_icon(image, icons[name])
        # launchers added before their icon arrived
        for launcher in list(self.window.store.launchers):
            if launcher.host == host and launcher.icon in icons:
                launcher.icon = icons[launcher.icon]
                self.window.save_launcher(launcher)

    def _add(self, app: remote.RemoteApp, button: Gtk.Button) -> None:
        launcher = self.window.settings.new_launcher(host=self._host, exec=app.exec, name=app.name,
                                                     icon=app.icon_path or app.icon, app_id=app.id)
        new_host = self._host not in {l.host for l in self.window.store.launchers}
        self.window.save_launcher(launcher)
        if new_host:
            self.window.check_host(self._host, auto=True)
        self._mark_added(button)
        toast = Adw.Toast(title=GLib.markup_escape_text(_("Added {name}").format(name=app.name)),
                          timeout=5, button_label=_("Add to App Menu"))
        toast.connect("button-clicked",
                      lambda *_args: self.window.toggle_menu_entry(launcher.id))
        self.toasts.add_toast(toast)
        self.window.log(_("Added {name} from {host}.").format(name=app.name, host=self._host), "ok")

    def _pick(self, app: remote.RemoteApp) -> None:
        self.on_pick(self._host, app)
        self.close()
