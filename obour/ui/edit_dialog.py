"""Create / edit a launcher."""

from __future__ import annotations

import os
import shlex

from gi.repository import Adw, Gio, GLib, Gtk

from .. import remote
from ..config import Launcher, resolve
from ..i18n import _
from ..launch import strip_field_codes
from . import TailscaleRows, host_menu_button, icon_image, set_icon
from .settings_rows import SettingsForm


def default_name(command: str) -> str:
    try:
        first = shlex.split(strip_field_codes(command))[0]
    except (ValueError, IndexError):
        return ""
    return os.path.basename(first).replace("-", " ").replace("_", " ").title()


class EditDialog(Adw.Dialog):
    def __init__(self, window, launcher: Launcher | None = None):
        super().__init__(title=_("Edit App") if launcher else _("Add App"), content_width=480)
        self.window = window
        self.original = launcher
        src = launcher or window.settings.new_launcher(host="", exec="", name="")
        self._icon = src.icon
        self._app_id = src.app_id

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_args: self.close())
        self.save_button = Gtk.Button(label=_("Save") if launcher else _("Add"),
                                      css_classes=["suggested-action"])
        self.save_button.connect("clicked", self._on_save)
        header.pack_start(cancel)
        header.pack_end(self.save_button)
        view.add_top_bar(header)

        page = Adw.PreferencesPage()
        view.set_content(page)
        self.set_child(view)

        # --- host
        host_group = Adw.PreferencesGroup(title=_("Remote Machine"))
        self.host_group = host_group
        self.host_row = Adw.EntryRow(title=_("Host — user@hostname or SSH alias"), text=src.host)
        self.host_row.add_suffix(host_menu_button(window.store.known_hosts(), self.host_row.set_text))
        self.test_button = Gtk.Button(label=_("Test"), valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.test_button.connect("clicked", self._on_test)
        self.host_row.add_suffix(self.test_button)
        host_group.add(self.host_row)
        self.tailscale = TailscaleRows(src.host)
        host_group.add(self.tailscale.address)
        host_group.add(self.tailscale.force)
        host_settings = Adw.ActionRow(title=_("Settings for All Apps of This Host…"),
                                      activatable=True)
        rtl = Gtk.Widget.get_default_direction() == Gtk.TextDirection.RTL
        host_settings.add_suffix(Gtk.Image(icon_name="go-previous-symbolic" if rtl
                                           else "go-next-symbolic"))
        host_settings.connect("activated", lambda *_args: self._open_host_settings())
        host_group.add(host_settings)
        # show the chosen host's Tailscale options, unless the user already typed some
        self.host_row.connect("changed", lambda r: self.tailscale.edited
                              or self.tailscale.load(r.get_text().strip()))
        # "Inherit" shows what the chosen host (or Preferences) would use
        self.host_row.connect("changed", lambda r: self.form.set_inherited(
            resolve(r.get_text().strip(), None, window.settings)))
        page.add(host_group)

        # --- app
        app_group = Adw.PreferencesGroup(title=_("Application"))
        pick = Gtk.Button(label=_("Pick from Host…"), css_classes=["flat"], valign=Gtk.Align.CENTER)
        pick.connect("clicked", self._on_pick)
        app_group.set_header_suffix(pick)
        self.cmd_row = Adw.EntryRow(title=_("Command"), text=src.exec)
        self.name_row = Adw.EntryRow(title=_("Name"), text=src.name)
        self.icon_row = Adw.ActionRow(title=_("Icon"))
        self.icon_preview = icon_image(self._icon, 32)
        self.icon_row.add_prefix(self.icon_preview)
        choose = Gtk.Button(label=_("Choose…"), valign=Gtk.Align.CENTER, css_classes=["flat"])
        choose.connect("clicked", self._on_choose_icon)
        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                           css_classes=["flat"], tooltip_text=_("Use Default Icon"))
        reset.connect("clicked", lambda *_args: self._set_icon(""))
        self.icon_row.add_suffix(choose)
        self.icon_row.add_suffix(reset)
        for row in (self.cmd_row, self.name_row, self.icon_row):
            app_group.add(row)
        page.add(app_group)

        # --- Obour found this app doesn't work over Wayland
        self._learned = launcher.learned_protocol if launcher else ""
        if self._learned:
            learned = Adw.PreferencesGroup()
            row = Adw.ActionRow(title=_("Obour Uses X11 for This App"),
                                subtitle=_("It didn't work over Wayland (or it uses X11 "
                                           "anyway). Setting its own Display below "
                                           "overrides this."))
            again = Gtk.Button(label=_("Try Wayland Again"), valign=Gtk.Align.CENTER)

            def forget(_button):
                self._learned = ""
                window.store.set_learned_protocol(launcher.id, "")
                window.refresh()
                learned.set_visible(False)
                window.toast(_("Obour will try Wayland again next time"))
            again.connect("clicked", forget)
            row.add_suffix(again)
            learned.add(row)
            page.add(learned)

        # --- this app's settings
        opts = Adw.PreferencesGroup(
            title=_("Settings for This App"),
            description=_("“Inherit” uses the host's setting, or Preferences when the host "
                          "doesn't change it. Priority: this app → host → Preferences."))
        self.form = SettingsForm("app", src.overrides, resolve(src.host, None, window.settings))
        for expander in self.form.expanders():
            opts.add(expander)
        page.add(opts)

        for row in (self.host_row, self.cmd_row):
            row.connect("changed", lambda *_args: self._validate())
        self._validate()

    # ------------------------------------------------------------------

    def _open_host_settings(self) -> None:
        host = self.host_row.get_text().strip()
        if not host:
            self.host_row.grab_focus()
            return
        from .host_settings import HostSettingsDialog
        dialog = HostSettingsDialog(self.window, host)
        dialog.connect("closed", lambda *_args: self.form.set_inherited(
            resolve(host, None, self.window.settings)))
        dialog.present(self)

    def _validate(self) -> None:
        ok = bool(self.host_row.get_text().strip() and self.cmd_row.get_text().strip())
        self.save_button.set_sensitive(ok)
        self.test_button.set_sensitive(bool(self.host_row.get_text().strip()))

    def _set_icon(self, icon: str) -> None:
        self._icon = icon
        set_icon(self.icon_preview, icon)
        self.icon_row.set_subtitle(os.path.basename(icon) if icon else "")

    def _on_choose_icon(self, *_args) -> None:
        dialog = Gtk.FileDialog(title=_("Choose an Icon"))
        image_filter = Gtk.FileFilter(name=_("Images"))
        image_filter.add_mime_type("image/png")
        image_filter.add_mime_type("image/svg+xml")
        image_filter.add_mime_type("image/jpeg")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(image_filter)
        dialog.set_filters(filters)

        def done(d, result):
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return
            if file and file.get_path():
                self._set_icon(file.get_path())

        dialog.open(self.window, None, done)

    def _with_user(self, then) -> bool:
        """Make sure the host names a user; returns False (and calls then() once it
        does) if the user has to be asked first."""
        host = self.host_row.get_text().strip()
        if not remote.missing_user(host):
            return True
        self.window.confirm_user(host, lambda h: (self.host_row.set_text(h), then()))
        return False

    def _on_test(self, *_args) -> None:
        host = self.host_row.get_text().strip()
        if not host or not self._with_user(self._on_test):
            return
        if self.tailscale.edited:
            self.tailscale.save(host)
        self.test_button.set_sensitive(False)
        self.host_group.set_description(_("Testing…"))

        def result(report):
            self.test_button.set_sensitive(True)
            if report.ok:
                missing = [label for label, ok, _hint in report.checks if not ok]
                self.host_group.set_description(
                    _("✔ Connected — missing on host: {tools}").format(tools=", ".join(missing))
                    if missing else _("✔ Connected"))
            else:
                self.host_group.set_description("✘ " + report.error)

        self.window.test_host(host, on_result=result)

    def _on_pick(self, *_args) -> None:
        self.window.open_browser(host=self.host_row.get_text().strip(), on_pick=self._fill_from_app)

    def _fill_from_app(self, host: str, app) -> None:
        self.host_row.set_text(host)
        self.cmd_row.set_text(app.exec)
        self.name_row.set_text(app.name)
        self._app_id = app.id
        self._set_icon(app.icon_path or app.icon)

    def _on_save(self, *_args) -> None:
        if not self._with_user(self._on_save):
            return
        command = self.cmd_row.get_text().strip()
        launcher = Launcher(
            host=self.host_row.get_text().strip(),
            exec=command,
            name=self.name_row.get_text().strip() or default_name(command) or command,
            icon=self._icon,
            in_menu=self.original.in_menu if self.original else False,
            app_id=self._app_id,
            overrides=dict(self.form.values),
        )
        # a new display choice (or another app) starts over with Wayland
        if self.original and launcher.overrides.get("protocol") == \
                self.original.overrides.get("protocol") and launcher.host == \
                self.original.host and launcher.exec == self.original.exec:
            launcher.learned_protocol = self._learned
        if self.original:
            launcher.id = self.original.id
        new_host = launcher.host not in {l.host for l in self.window.store.launchers}
        if self.tailscale.edited:
            self.tailscale.save(launcher.host)
        self.window.save_launcher(launcher)
        self.window.toast(_("Saved {name}").format(name=launcher.name))
        self.close()
        if new_host:
            self.window.check_host(launcher.host, auto=True)
