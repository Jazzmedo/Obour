#!/bin/sh
# Runs inside the build container (see build.sh). /src is the project checkout.
set -eu
export DEBIAN_FRONTEND=noninteractive

echo "==> Installing build dependencies"
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-1 libadwaita-1-0 \
    librsvg2-common libgdk-pixbuf2.0-bin adwaita-icon-theme hicolor-icon-theme shared-mime-info \
    libglib2.0-bin dconf-gsettings-backend gsettings-desktop-schemas patchelf file curl ca-certificates \
    desktop-file-utils appstream zsync >/dev/null

echo "==> Assembling AppDir"
rm -rf /build && mkdir -p /build
python3 /src/packaging/appimage/make_appdir.py /build/AppDir

echo "==> Fetching appimagetool"
curl -fsSL -o /build/appimagetool \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x /build/appimagetool

VERSION=$(python3 -c 'import re; print(re.search(r"__version__ = \"(.+?)\"", open("/src/obour/__init__.py").read()).group(1))')
NAME=Obour-$VERSION-x86_64.AppImage
OUT=/src/dist/$NAME
# Lets Gear Lever and AppImageUpdate find new versions in the GitHub releases.
UPDATE_INFO=${OBOUR_UPDATE_INFO:-"gh-releases-zsync|Jazzmedo|Obour|latest|Obour-*x86_64.AppImage.zsync"}

echo "==> Building $OUT"
# Build in a temporary folder and rename into place: a running copy of the old
# AppImage keeps reading its (now unlinked) file instead of a half-written one.
mkdir -p /build/out
cd /build/out
ARCH=x86_64 VERSION=$VERSION APPIMAGE_EXTRACT_AND_RUN=1 \
    /build/appimagetool -u "$UPDATE_INFO" /build/AppDir "$NAME"

cp "$NAME.zsync" "$OUT.zsync.new" && mv -f "$OUT.zsync.new" "$OUT.zsync"
cp "$NAME" "$OUT.new" && mv -f "$OUT.new" "$OUT"
if [ -n "${HOST_UID:-}" ]; then
    chown "$HOST_UID:${HOST_GID:-$HOST_UID}" /src/dist "$OUT" "$OUT.zsync"
fi
