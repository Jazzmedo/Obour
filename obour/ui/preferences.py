"""Obour's settings. Hosts and apps can change each of them for themselves."""

from __future__ import annotations

from dataclasses import asdict

from gi.repository import Adw, Gtk

from .. import look
from ..config import OPTION_KEYS, Launcher, clear_gpu_broken
from ..cursor import local_cursor
from ..desktop import menu_name
from ..i18n import LANGUAGES, _, ngettext
from .settings_rows import SettingsForm


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, window):
        super().__init__(title=_("Preferences"), search_enabled=False)
        self.window = window
        s = window.settings
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        self.add(page)

        page.add(Adw.PreferencesGroup(
            description=_("These settings apply to every app. A host can change them for its "
                          "apps (its ⋮ menu → Host Settings…), and an app can change them "
                          "for itself (Edit…). The app's own setting wins, then the host's, "
                          "then these.")))

        values = {k: v for k, v in asdict(s).items() if k in OPTION_KEYS}
        self.form = SettingsForm("obour", values, on_change=self._on_option)
        rows = self.form.rows
        rows["desktop_theme"].set_subtitle(
            _("Colors, icons and fonts of your desktop ({desktop}), copied to each host "
              "once").format(desktop=look.describe_desktop()))
        theme = local_cursor()[0]
        if theme:
            rows["match_cursor"].set_subtitle(
                _("Show your cursor theme ({theme}) in remote apps. It's copied to each host "
                  "once.").format(theme=theme))
        groups = self.form.groups()
        # the app-menu name gets a live preview
        self.preview_row = Adw.ActionRow(title=_("App Menu Preview"), use_markup=False)
        rows["menu_name"].connect("changed", lambda *_args: self._update_preview())
        groups[-1].add(self.preview_row)
        self._update_preview()
        for group in groups:
            page.add(group)

        # --- only for Obour itself
        own = Adw.PreferencesGroup(title=_("Obour"),
                                   description=_("These apply to Obour itself, not per host "
                                                 "or app."))
        self._languages = ["system", *LANGUAGES]
        # Language names are shown in their own language, so anyone can find theirs.
        language = Adw.ComboRow(
            title=_("Language"), subtitle=_("Takes effect when Obour is opened again"),
            model=Gtk.StringList.new([_("System Default"), *LANGUAGES.values()]))
        language.set_selected(self._languages.index(s.language)
                              if s.language in self._languages else 0)
        language.connect("notify::selected", self._on_language)
        check = Adw.SwitchRow(title=_("Check New Hosts"),
                              subtitle=_("When you add apps from a new host, look for missing "
                                         "tools and offer to install them"),
                              active=s.check_hosts)
        check.connect("notify::active", lambda r, _p: self._set("check_hosts", r.get_active()))
        own.add(language)
        own.add(check)
        page.add(own)

    # ------------------------------------------------------------------

    def _on_option(self, key: str, value) -> None:
        if key == "menu_name":
            value = value or "{name} ({host})"
            self.form.rows["menu_name"].set_text(value)
        self._set(key, value)
        if key == "gpu" and value:
            clear_gpu_broken()   # hosts where it failed get another try
        elif key == "menu_name":
            count = self.window.resync_menu_entries()
            self.add_toast(Adw.Toast(title=ngettext("Renamed {count} app menu entry",
                                                    "Renamed {count} app menu entries",
                                                    count).format(count=count)
                                     if count else _("Saved")))
        elif key == "extra_env":
            ignored = [w for w in (value or "").split() if "=" not in w]
            self.add_toast(Adw.Toast(title=_("Ignored (no “=”): {words}").format(
                words=" ".join(ignored)) if ignored else _("Saved")))

    def _preview_launcher(self):
        saved = self.window.store.launchers
        return saved[0] if saved else Launcher(host="user@my-computer", exec="", name="Firefox")

    def _update_preview(self) -> None:
        template = self.form.rows["menu_name"].get_text() or "{name} ({host})"
        self.preview_row.set_subtitle(menu_name(self._preview_launcher(), template))

    def _on_language(self, row: Adw.ComboRow, *_args) -> None:
        choice = self._languages[row.get_selected()]
        if choice != self.window.settings.language:
            self._set("language", choice)
            self.add_toast(Adw.Toast(title=_("Restart Obour to change the language")))

    def _set(self, attr: str, value) -> None:
        setattr(self.window.settings, attr, value)
        self.window.settings.save()
