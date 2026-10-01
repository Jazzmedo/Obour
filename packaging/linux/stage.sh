#!/bin/sh
# Lay out Obour for a system package: stage.sh <source dir> <destination root>
# Used by the .deb and .rpm builds (and usable for any distro package).
set -eu

SRC=$1
DEST=$2
APP_ID=io.github.Jazzmedo.Obour
SHARE=$DEST/usr/share

install -d "$SHARE/obour/bin" "$DEST/usr/bin" "$SHARE/applications" \
    "$SHARE/icons/hicolor/scalable/apps" "$SHARE/metainfo" "$SHARE/doc/obour" "$SHARE/man/man1"

# The Python package with its helper script and translations (read as .po, no
# compile step), without caches. Executable files keep their mode.
(cd "$SRC" && find obour -type f ! -path '*__pycache__*' ! -name '*.pyc') | while read -r f; do
    if [ -x "$SRC/$f" ]; then mode=755; else mode=644; fi
    install -D -m "$mode" "$SRC/$f" "$SHARE/obour/$f"
done
install -m 755 "$SRC/bin/obour" "$SHARE/obour/bin/obour"

# OBOUR_BIN: app-menu entries created by Obour run this stable path.
cat > "$DEST/usr/bin/obour" <<'EOF'
#!/bin/sh
export OBOUR_BIN=/usr/bin/obour
exec /usr/bin/python3 /usr/share/obour/bin/obour "$@"
EOF
chmod 755 "$DEST/usr/bin/obour"

install -m 644 "$SRC/data/$APP_ID.desktop" "$SHARE/applications/$APP_ID.desktop"
install -m 644 "$SRC/data/$APP_ID.svg" "$SHARE/icons/hicolor/scalable/apps/$APP_ID.svg"
install -m 644 "$SRC/data/$APP_ID.metainfo.xml" "$SHARE/metainfo/$APP_ID.metainfo.xml"
install -m 644 "$SRC/data/obour.1" "$SHARE/man/man1/obour.1"
install -m 644 "$SRC/README.md" "$SHARE/doc/obour/README.md"
[ ! -f "$SRC/LICENSE" ] || install -m 644 "$SRC/LICENSE" "$SHARE/doc/obour/LICENSE"
