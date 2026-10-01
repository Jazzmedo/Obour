#!/bin/sh
# Install (or with --uninstall, remove) Obour for the current user.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
BIN_DIR=$HOME/.local/bin
APP_DIR=$DATA_HOME/applications
ICON_DIR=$DATA_HOME/icons/hicolor/scalable/apps
MAN_DIR=$DATA_HOME/man/man1   # man finds it via ~/.local/bin in PATH
APP_ID=io.github.Jazzmedo.Obour

OLD_ID=io.github.obour.Obour   # before 0.1.0 was published
rm -f "$APP_DIR/$OLD_ID.desktop" "$ICON_DIR/$OLD_ID.svg"

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$BIN_DIR/obour" "$APP_DIR/$APP_ID.desktop" "$ICON_DIR/$APP_ID.svg" "$MAN_DIR/obour.1"
    rm -f "$APP_DIR"/obour-*.desktop
    echo "Obour removed. Your saved apps are kept in ~/.config/obour/."
    exit 0
fi

if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null; then
    echo "Missing GTK 4 / libadwaita Python bindings. On Debian/Ubuntu:" >&2
    echo "    sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" >&2
    exit 1
fi
command -v waypipe >/dev/null 2>&1 || echo "Note: waypipe is not installed; Wayland forwarding will fall back to X11."

mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR" "$MAN_DIR"
cp "$HERE/data/obour.1" "$MAN_DIR/"
chmod +x "$HERE/bin/obour"
ln -sf "$HERE/bin/obour" "$BIN_DIR/obour"
cp "$HERE/data/$APP_ID.svg" "$ICON_DIR/"
sed "s|^Exec=.*|Exec=\"$HERE/bin/obour\"|" "$HERE/data/$APP_ID.desktop" > "$APP_DIR/$APP_ID.desktop"
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" 2>/dev/null || true

echo "Obour installed. Start it from your app menu or run: obour"
