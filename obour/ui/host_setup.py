"""Show what a host is missing and install it with the host's package manager."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .. import auth, hostsetup, look, remote
from ..config import resolve
from ..cursor import local_cursor, theme_dir
from ..i18n import _
from . import run_async, spinner


class HostSetupDialog(Adw.Dialog):
    def __init__(self, window, host: str, caps: dict):
        super().__init__(title=_("Set Up {host}").format(host=host), content_width=500, content_height=620)
        self.window = window
        self.host = host
        self.caps = caps
        self._busy = False
        # what this computer's desktop needs on the host
        try:
            self.look = look.collect() if resolve(host).settings.desktop_theme else None
        except Exception:
            self.look = None
        self.tags = hostsetup.local_tags(host, self.look)

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        self.close_button = Gtk.Button(label=_("Not Now"))
        self.close_button.connect("clicked", lambda *_args: self.close())
        self.install_button = Gtk.Button(label=_("Install"), css_classes=["suggested-action"])
        self.install_button.connect("clicked", self._on_install)
        self.spinner = spinner(visible=False)
        header.pack_start(self.close_button)
        header.pack_end(self.install_button)
        header.pack_end(self.spinner)
        view.add_top_bar(header)
        self.page = Adw.PreferencesPage()
        view.set_content(self.page)
        self.set_child(view)
        self._populate()

    # ------------------------------------------------------------------

    def _cursor_missing(self) -> str | None:
        """The local cursor theme's name when it should be copied to the host."""
        if not resolve(self.host).settings.match_cursor:
            return None
        theme = local_cursor()[0]
        if theme and theme_dir(theme) and not (self.caps.get("cursor")
                                               and self.caps.get("cursor-theme") == theme):
            return theme
        return None

    def _populate(self) -> None:
        for group in list(getattr(self, "_groups", [])):
            self.page.remove(group)
        self._groups = []
        checks = hostsetup.check(self.caps, self.tags)
        self.packages = hostsetup.missing_packages(checks)
        self.cursor_theme = self._cursor_missing()
        self.copy_look = self.look is not None and look.needs_sync(self.host, self.look)
        manual = [c for c in checks if not c.ok and not c.packages]

        lines = [self.caps.get("os-name") or ""]
        lines.append(_("Your desktop: {desktop}").format(desktop=look.describe_desktop()))
        status = Adw.PreferencesGroup(title=_("What the Host Has"),
                                      description="\n".join(l for l in lines if l))
        for c in checks:
            row = Adw.ActionRow(title=c.requirement.title, subtitle=c.requirement.purpose,
                                use_markup=False)
            icon = Gtk.Image(icon_name="object-select-symbolic" if c.ok else "dialog-warning-symbolic",
                             css_classes=["success" if c.ok else "warning"],
                             tooltip_text=_("Installed") if c.ok else _("Missing"))
            row.add_prefix(icon)
            if not c.ok:
                row.set_subtitle(_("{purpose}. Missing: {packages}").format(
                    purpose=c.requirement.purpose,
                    packages=", ".join(c.packages) or _("no package available")))
            status.add(row)
        if self.cursor_theme:
            row = Adw.ActionRow(title=_("Your Cursor Theme"),
                                subtitle=_("{theme}, copied to your home folder on the host "
                                           "(no password needed)").format(theme=self.cursor_theme),
                                use_markup=False)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
            status.add(row)
        if self.copy_look:
            size = look.pending_size(look.pending_data(self.host, self.look))
            row = Adw.ActionRow(title=_("Your Desktop Look"),
                                subtitle=(_("Colors, icons and fonts ({size}), copied to your home "
                                            "folder on the host once (no password needed)")
                                          .format(size=GLib.format_size(size)) if size else
                                          _("Colors and settings changed since the last copy")),
                                use_markup=False)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
            status.add(row)
        self._add(status)

        if not self.packages and not self.cursor_theme and not self.copy_look:
            self.install_button.set_visible(False)
            self.close_button.set_label(_("Close"))
            done = Adw.PreferencesGroup(
                description=_("Everything Obour can use is installed.") if not manual else
                _("Nothing more can be installed automatically."))
            self._add(done)
        else:
            self.install_button.set_visible(True)
            self.install_button.set_label(_("Install") if self.packages else _("Copy"))
            self.close_button.set_label(_("Not Now"))

        self.password_row = None
        if self.packages:
            try:
                command = hostsetup.install_command(self.caps, self.packages)
            except remote.RemoteError as e:
                command = ""
                self.packages = []
                self._add(Adw.PreferencesGroup(title=_("Install Missing Packages"),
                                               description=str(e)))
            if command:
                self.install_group = Adw.PreferencesGroup(
                    title=_("Install Missing Packages"),
                    description=_("Obour will run this on {host} as administrator:").format(
                        host=self.host))
                cmd = Gtk.Label(label=command, selectable=True, wrap=True, xalign=0,
                                css_classes=["monospace"], margin_top=8, margin_bottom=8,
                                margin_start=10, margin_end=10)
                self.install_group.add(Gtk.Frame(child=cmd))
                self._add(self.install_group)
                if hostsetup.needs_sudo_password(self.caps):
                    self.password_row = Adw.PasswordEntryRow(
                        title=_("Your password on {host} (for sudo)").format(host=self.host),
                        text=auth.password_for(self.host) or "")
                    self.password_row.connect("entry-activated", self._on_install)
                    pw_group = Adw.PreferencesGroup()
                    pw_group.add(self.password_row)
                    self._add(pw_group)

        if manual:
            names = ", ".join(c.requirement.title for c in manual)
            hint = (_("This host's package manager isn't supported, so install these "
                      "yourself: {names}")
                    if hostsetup.package_manager(self.caps) is None else
                    _("Not in this host's official repositories: {names}")).format(names=names)
            if hostsetup.package_manager(self.caps) == hostsetup.PACMAN:
                hint += " " + _("(Qt 5 dark style: adwaita-qt5 or kvantum from the AUR)")
            self._add(Adw.PreferencesGroup(description=hint))

        self.error_group = Adw.PreferencesGroup(visible=False)
        self.error_label = Gtk.Label(wrap=True, xalign=0, css_classes=["error"])
        self.error_group.add(self.error_label)
        self._add(self.error_group)
        self._update_sensitivity()

    def _add(self, group: Adw.PreferencesGroup) -> None:
        self.page.add(group)
        self._groups.append(group)

    def _update_sensitivity(self) -> None:
        self.install_button.set_sensitive(not self._busy)
        self.close_button.set_sensitive(not self._busy)
        self.spinner.set_visible(self._busy)
        self.set_can_close(not self._busy)
        if self.password_row is not None:
            self.password_row.set_sensitive(not self._busy)

    def _show_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_group.set_visible(True)

    # ------------------------------------------------------------------

    def _on_install(self, *_args) -> None:
        if self._busy:
            return
        password = None
        if self.packages and self.password_row is not None:
            password = self.password_row.get_text()
            if not password:
                self.password_row.grab_focus()
                self._show_error(_("Enter your password for sudo on the host."))
                return
        self._busy = True
        self.error_group.set_visible(False)
        self._update_sensitivity()
        what = (_("Installing {packages} on {host}…").format(
                    packages=", ".join(self.packages), host=self.host)
                if self.packages else
                _("Copying your look to {host}…").format(host=self.host))
        self.window.set_status(what, "busy")
        self.window.log(what)
        self.window.log_toggle.set_active(True)
        run_async(self._work, password, on_done=self._on_done, on_error=self._on_error)

    def _work(self, password: str | None) -> dict:
        """Runs on a worker thread."""
        log = lambda text: GLib.idle_add(self.window.log, text, "cmd" if text.startswith("  $ ")
                                         else "info")
        if self.packages:
            hostsetup.install(self.host, self.caps, self.packages, password, log)
        if self.cursor_theme:
            remote.upload_cursor_theme(self.host, self.cursor_theme, theme_dir(self.cursor_theme))
            remote.set_cap(self.host, "cursor-theme", self.cursor_theme)
        if self.copy_look:
            look.sync(self.host, self.look, on_line=log)
        return remote.probe_caps(self.host)

    def _on_done(self, caps: dict) -> None:
        self._busy = False
        self.caps = caps
        remaining = hostsetup.missing_packages(hostsetup.check(caps, self.tags))
        text = (_("{host} is set up").format(host=self.host) if not remaining else
                _("Finished, but {host} still lacks: {packages}").format(
                    host=self.host, packages=", ".join(remaining)))
        self.window.log(text + ".", "ok" if not remaining else "error")
        self.window.set_status(text, "ok" if not remaining else "error")
        self.window.toast(text)
        self.window.refresh()
        self._populate()

    def _on_error(self, error: Exception) -> None:
        self._busy = False
        self._update_sensitivity()
        message = str(error)
        self.window.log(message, "error")
        self.window.set_status(message, "error")
        self._show_error(message)
        if self.password_row is not None and (getattr(error, "code", "") == "sudo-password"
                                              or "password" in message.lower()):
            self.password_row.set_text("")
            self.password_row.grab_focus()
