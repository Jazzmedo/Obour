"""The settings rows shared by Preferences, Host Settings and the Edit App dialog.

Each Option in config.OPTIONS can be set in Obour's Preferences, changed for all
apps of a host, and changed again for one app. In the host and app dialogs every
row starts with "Inherit", which shows the value that applies otherwise and where
it comes from (the priority is app → host → Preferences)."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from ..config import OPTIONS, Resolved
from ..i18n import _, ngettext


def _texts() -> dict:
    """key -> (title, subtitle, {value: label}). Built on use, after i18n.setup."""
    on_off = {True: _("On"), False: _("Off")}
    return {
        "protocol": (_("Display"), _("How windows are forwarded"),
                     {"auto": _("Automatic"), "wayland": _("Wayland (waypipe, X11 fallback)"),
                      "x11": _("X11 only (ssh -Y)")}),
        "style": (_("Light or Dark Style"),
                  _("Match This Computer follows your desktop's light or dark mode"),
                  {"system": _("Match This Computer"), "dark": _("Dark"), "light": _("Light"),
                   "off": _("Don't Change")}),
        "desktop_theme": (_("Use My Desktop Theme"),
                          _("Your colors, icons and fonts, copied to the host once"), on_off),
        "qt_version": (_("Qt Version"),
                       _("For theming Qt apps. Automatic checks the program (and VLC's "
                         "interface plugin)"),
                       {"auto": _("Automatic"), "qt5": "Qt 5", "qt6": "Qt 6"}),
        "match_cursor": (_("Use My Cursor"), _("Your cursor theme, copied to the host once"),
                         on_off),
        "audio": (_("Sound"), _("Play the app's audio on this computer"), on_off),
        "file_sharing": (_("Copy, Paste and Drag Files"),
                         _("Copy or drag a file from one computer to the other"),
                         {"allow": _("Always Allow"), "ask": _("Ask Every Time"),
                          "deny": _("Deny")}),
        "private_bus": (_("Separate Instance"),
                        _("Open a new copy instead of a window of one already running on the "
                          "host"), on_off),
        "gpu": (_("GPU Acceleration for Wayland Apps"),
                _("Faster for video and games, but some hosts freeze before the window "
                  "appears. If that happens, Obour turns it off for that host by itself."),
                on_off),
        "fallback_x11": (_("Try X11 When Wayland Fails"),
                         _("If a Wayland app shows no window, open it again with X11 and tell "
                           "you"), on_off),
        "waypipe_compress": (_("Compression for Wayland Apps"),
                             _("LZ4 suits home networks; Zstandard the internet or slow Wi-Fi; "
                               "H.264 video is smallest but needs GPU Acceleration"),
                             {"none": _("None"), "lz4": "LZ4", "zstd": "Zstandard",
                              "h264": _("H.264 video")}),
        "compress": (_("Compress X11 Apps and Sound"),
                     _("SSH compression (zlib). Helps on slow links, adds delay on fast ones"),
                     on_off),
        "extra_env": (_("Environment Variables"), _("KEY=value, separated by spaces"), {}),
        "menu_name": (_("App Menu Name"),
                      _("Fields: {name} {host} {user} {address}"), {}),
    }


# Rows per group, in order
GROUPS = (
    ("appearance", ("style", "desktop_theme", "match_cursor", "qt_version")),
    ("display", ("protocol", "fallback_x11", "gpu", "waypipe_compress", "compress")),
    ("files", ("file_sharing", "audio")),
    ("advanced", ("private_bus", "extra_env", "menu_name")),
)


def group_titles() -> dict[str, str]:
    return {"appearance": _("Appearance"), "display": _("Display and Performance"),
            "files": _("Files and Sound"), "advanced": _("Advanced")}


def source_label(source: str) -> str:
    return {"host": _("from the host"), "obour": _("from Preferences"),
            "app": _("from this app")}.get(source, "")


class SettingsForm:
    """Rows for all Options.

    scope "obour": values is the full {key: value} of Obour's settings; every row
    has a value. scope "host"/"app": values holds only what this host/app changes;
    inherited is what applies without it (config.Resolved of the level below)."""

    def __init__(self, scope: str, values: dict, inherited: Resolved | None = None,
                 on_change=None):
        self.scope = scope
        self.values = dict(values)
        self.inherited = inherited
        self.on_change = on_change
        self.texts = _texts()
        self.rows: dict[str, Gtk.Widget] = {}
        self._choices: dict[str, list] = {}
        self._updating = False
        for option in OPTIONS:
            self.rows[option.key] = self._make_row(option)
            if option.kind != "text":
                self._update_subtitle(option.key)

    # --- building

    def _inherited_text(self, key: str) -> str:
        """"Match This Computer, from Preferences" for the row's subtitle."""
        if self.inherited is None:
            return ""
        value = getattr(self.inherited.settings, key)
        labels = self.texts[key][2]
        shown = labels.get(value, str(value)) if labels else (value or _("none"))
        return _("{value}, {source}").format(
            value=shown, source=source_label(self.inherited.sources.get(key, "obour")))

    def _update_subtitle(self, key: str) -> None:
        """Below the description: what is inherited, or that this level changes it."""
        row = self.rows.get(key)
        if row is None or self.scope == "obour":
            return
        description = self.texts[key][1]
        inherited = self._inherited_text(key)
        if key in self.values:
            note = (_("Changed for this app (otherwise: {inherited})") if self.scope == "app"
                    else _("Changed for this host (otherwise: {inherited})")).format(
                        inherited=inherited)
        else:
            note = _("Inherited: {inherited}").format(inherited=inherited)
        row.set_subtitle(f"{description}\n{note}")

    def _make_row(self, option) -> Gtk.Widget:
        key = option.key
        title, subtitle, labels = self.texts[key]
        if option.kind == "text":
            return self._text_row(key, title, subtitle)
        values = [True, False] if option.kind == "bool" else list(option.choices)
        if self.scope == "obour" and option.kind == "bool":
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(self.values.get(key)))
            row.connect("notify::active", lambda r, _p: self._set(key, r.get_active()))
            return row
        names = [labels.get(v, str(v)) for v in values]
        if self.scope != "obour":
            values = [None] + values
            names = [_("Inherit")] + names
        model = Gtk.StringList.new(names)
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=model)
        current = self.values.get(key) if self.scope != "obour" or key in self.values else None
        row.set_selected(values.index(current) if current in values else 0)
        self._choices[key] = values
        row.connect("notify::selected", lambda r, _p: self._on_choice(key, r))
        return row

    def _text_row(self, key: str, title: str, subtitle: str) -> Gtk.Widget:
        row = Adw.EntryRow(title=title, text=self.values.get(key, "") or "",
                           show_apply_button=True)
        row.set_tooltip_text(subtitle)
        row.connect("apply", lambda r: self._set_text(key, r.get_text().strip()))
        if self.scope != "obour":
            self._update_text_title(key, row)
        return row

    def _update_text_title(self, key: str, row: Adw.EntryRow) -> None:
        title = self.texts[key][0]
        if self.inherited is None:
            return
        inherited = getattr(self.inherited.settings, key)
        if key == "extra_env":
            hint = (_("added to: {value}").format(value=inherited) if inherited
                    else _("added to the inherited ones"))
        else:
            hint = _("empty: {value}").format(value=inherited)
        row.set_title(f"{title} — {hint}")

    # --- changes

    def _on_choice(self, key: str, row: Adw.ComboRow) -> None:
        if self._updating:
            return
        value = self._choices[key][row.get_selected()]
        self._set(key, value)

    def _set_text(self, key: str, text: str) -> None:
        self._set(key, text if (text or self.scope == "obour") else None)

    def _set(self, key: str, value) -> None:
        if value is None:
            self.values.pop(key, None)
        else:
            self.values[key] = value
        self._update_subtitle(key)
        if self.on_change:
            self.on_change(key, value)

    def set_inherited(self, inherited: Resolved) -> None:
        """The level below changed (e.g. another host was typed): relabel "Inherit"."""
        self.inherited = inherited
        for option in OPTIONS:
            if option.kind == "text":
                self._update_text_title(option.key, self.rows[option.key])
            else:
                self._update_subtitle(option.key)

    def changed_count(self, keys) -> int:
        return sum(1 for k in keys if k in self.values)

    # --- layout

    def groups(self) -> list[Adw.PreferencesGroup]:
        """One PreferencesGroup per topic."""
        titles = group_titles()
        result = []
        for name, keys in GROUPS:
            group = Adw.PreferencesGroup(title=titles[name])
            for key in keys:
                group.add(self.rows[key])
            result.append(group)
        return result

    def expanders(self) -> list[Adw.ExpanderRow]:
        """One collapsible row per topic, with how many settings are changed here."""
        titles = group_titles()
        result = []
        for name, keys in GROUPS:
            expander = Adw.ExpanderRow(title=titles[name])
            for key in keys:
                expander.add_row(self.rows[key])
            self._update_expander(expander, keys)
            result.append((expander, keys))
        self._expanders = result
        previous = self.on_change

        def on_change(key, value):
            for expander, keys in self._expanders:
                self._update_expander(expander, keys)
            if previous:
                previous(key, value)
        self.on_change = on_change
        return [e for e, _k in result]

    def _update_expander(self, expander: Adw.ExpanderRow, keys) -> None:
        count = self.changed_count(keys)
        expander.set_subtitle(ngettext("{count} setting changed for this app",
                                       "{count} settings changed for this app",
                                       count).format(count=count)
                              if count else _("All inherited"))
