"""Settings for all apps of one host (between Preferences and each app's own)."""

from __future__ import annotations

from gi.repository import Adw, GLib

from ..config import host_settings, resolve, set_gpu_broken, set_host_settings
from ..i18n import _
from . import TailscaleRows
from .settings_rows import SettingsForm


class HostSettingsDialog(Adw.PreferencesDialog):
    def __init__(self, window, host: str):
        super().__init__(title=_("Settings for {host}").format(host=host), search_enabled=False)
        self.window = window
        self.host = host
        page = Adw.PreferencesPage()
        self.add(page)

        page.add(Adw.PreferencesGroup(
            description=_("Apps from {host} use these settings unless they change them "
                          "(Edit…). “Inherit” uses Preferences.").format(host=host)))

        # connection
        self.tailscale = TailscaleRows(host)
        conn = Adw.PreferencesGroup(title="Tailscale",
                                    description=_("A second address, for when you're away from "
                                                  "this computer's network."))
        conn.add(self.tailscale.address)
        conn.add(self.tailscale.force)
        for row in (self.tailscale.address,):
            row.connect("changed", lambda *_args: self._save_tailscale())
        self.tailscale.force.connect("notify::active", lambda *_args: self._save_tailscale())
        page.add(conn)

        # everything that can be inherited: here the level below is Preferences only
        self.form = SettingsForm("host", host_settings(host), resolve("", None,
                                                                         window.settings),
                                 on_change=self._on_option)
        for group in self.form.groups():
            page.add(group)
        self.connect("closed", lambda *_args: window.refresh())
        GLib.idle_add(lambda: self.set_focus(None))   # don't select the Tailscale address

    def _save_tailscale(self) -> None:
        if self.tailscale.edited:
            self.tailscale.save(self.host)

    def _on_option(self, key: str, value) -> None:
        set_host_settings(self.host, self.form.values)
        if key == "gpu" and value:
            set_gpu_broken(self.host, False)   # give GPU sharing another try here
        if key == "menu_name":
            self.window.resync_menu_entries()
        self.add_toast(Adw.Toast(title=_("Saved"), timeout=1))
