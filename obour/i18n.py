"""Interface translations.

Strings are marked with _() / ngettext() and translated in obour/locale/<lang>.po.
The .po files are read directly (no compile step), so every way of installing
Obour — AppImage, .deb, .rpm, Nix or a source checkout — carries them as plain files.
setup() must run before the UI modules are imported: some of them translate
module-level labels."""

from __future__ import annotations

import ast
import gettext
import os

LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")
# language code -> its own name, shown in Preferences
LANGUAGES = {"en": "English", "ar": "العربية"}
RTL_LANGUAGES = {"ar", "fa", "he", "ur"}

_catalog: dict[str, str | list[str]] = {}
_plural = gettext.c2py("n != 1")
_language = "en"


def _unquote(text: str) -> str:
    return ast.literal_eval(text) if text.startswith('"') else ""


def _parse_po(path: str) -> tuple[dict[str, str | list[str]], str]:
    """{msgid: msgstr or [plural forms]} and the Plural-Forms expression."""
    entries: list[dict[str, str]] = []
    entry: dict[str, str] = {}
    key = ""
    fuzzy = False   # a "#, fuzzy" flag comes before the entry it marks
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                fuzzy = fuzzy or (line.startswith("#,") and "fuzzy" in line)
                continue
            if line.startswith('"'):
                if key:
                    entry[key] += _unquote(line)
                continue
            key, _sep, value = line.partition(" ")
            if key == "msgid":
                if entry:
                    entries.append(entry)
                entry = {"fuzzy": "1"} if fuzzy else {}
                fuzzy = False
            entry[key] = _unquote(value)
    if entry:
        entries.append(entry)

    catalog: dict[str, str | list[str]] = {}
    plural_forms = "nplurals=2; plural=n != 1;"
    for e in entries:
        msgid = e.get("msgid", "")
        if not msgid:
            for header in e.get("msgstr", "").splitlines():
                if header.lower().startswith("plural-forms:"):
                    plural_forms = header.split(":", 1)[1]
            continue
        if e.get("fuzzy"):
            continue
        if "msgid_plural" in e:
            forms = [e[k] for k in sorted((k for k in e if k.startswith("msgstr[")),
                                           key=lambda k: int(k[7:-1]))]
            if forms and all(forms):
                catalog[msgid] = forms
        elif e.get("msgstr"):
            catalog[msgid] = e["msgstr"]
    expression = plural_forms.split("plural=", 1)[-1].strip().rstrip(";")
    return catalog, expression


def system_language() -> str:
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value and value not in ("C", "POSIX") and not value.startswith("C."):
            return value.split(":")[0].split("_")[0].split(".")[0].lower()
    return "en"


def setup(choice: str = "system") -> str:
    """Load the translation for choice ("system" or a language code); returns the
    language in use."""
    global _catalog, _plural, _language
    language = system_language() if choice in ("", "system") else choice
    _catalog, _plural, _language = {}, gettext.c2py("n != 1"), "en"
    path = os.path.join(LOCALE_DIR, f"{language}.po")
    if language != "en" and os.path.isfile(path):
        try:
            _catalog, expression = _parse_po(path)
            _plural = gettext.c2py(expression)
            _language = language
        except (OSError, ValueError, SyntaxError):
            _catalog = {}
    return _language


def language() -> str:
    return _language


def is_rtl() -> bool:
    return _language in RTL_LANGUAGES


def _(message: str) -> str:
    translated = _catalog.get(message)
    return translated if isinstance(translated, str) else message


def ngettext(singular: str, plural: str, n: int) -> str:
    forms = _catalog.get(singular)
    if isinstance(forms, list):
        index = _plural(n)
        if 0 <= index < len(forms):
            return forms[index]
    return singular if n == 1 else plural
