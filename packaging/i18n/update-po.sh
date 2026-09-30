#!/bin/sh
# Refresh the translation template and every obour/locale/*.po after strings change.
# New or changed strings show up in the .po files with an empty (or "fuzzy") msgstr;
# translate them there (a text editor, Poedit or GNOME's Translation Editor all work).
# Needs gettext (xgettext, msgmerge).
set -eu
cd "$(dirname "$0")/../.."

POT=obour/locale/obour.pot
xgettext --language=Python --from-code=UTF-8 --keyword=_ --keyword=ngettext:1,2 \
    --add-comments=TRANSLATORS: --package-name=Obour \
    --msgid-bugs-address=https://github.com/Jazzmedo/Obour/issues \
    --sort-by-file -o "$POT" obour/*.py obour/ui/*.py
echo "Updated $POT ($(grep -c '^msgid ' "$POT") strings)"

for po in obour/locale/*.po; do
    msgmerge --quiet --update --backup=none --no-wrap "$po" "$POT"
    printf '%s: ' "$po"
    msgfmt --statistics -o /dev/null "$po"
done
