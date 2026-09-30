#!/bin/sh
# Build dist/obour_<version>_all.deb in a Debian container.
# The package is architecture-independent: it uses the distro's Python, GTK 4 and libadwaita.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
IMAGE=${OBOUR_DEB_IMAGE:-docker.io/library/debian:13}
ENGINE=$(command -v podman || command -v docker || true)
[ -n "$ENGINE" ] || { echo "podman or docker is required to build the .deb." >&2; exit 1; }
mkdir -p "$ROOT/dist"
# Rootless podman already maps container root to the calling user; docker needs a chown.
OWNER=""
case "$(basename "$ENGINE")" in
    docker) OWNER="-e HOST_UID=$(id -u) -e HOST_GID=$(id -g)" ;;
esac

# shellcheck disable=SC2086
"$ENGINE" run --rm -v "$ROOT:/src:Z" $OWNER "$IMAGE" sh -euc '
VERSION=$(sed -n "s/^__version__ = \"\(.*\)\"/\1/p" /src/obour/__init__.py)
PKG=/build/obour_${VERSION}_all
rm -rf /build && mkdir -p "$PKG/DEBIAN"
sh /src/packaging/linux/stage.sh /src "$PKG"
cat > "$PKG/DEBIAN/control" <<EOF
Package: obour
Version: $VERSION
Architecture: all
Maintainer: 7anafi <https://github.com/Jazzmedo/Obour>
Section: net
Priority: optional
Homepage: https://github.com/Jazzmedo/Obour
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5), openssh-client
Recommends: waypipe, x11-xserver-utils, libnotify-bin, sshfs, dconf-cli
Installed-Size: $(du -sk "$PKG" | cut -f1)
Description: Run apps from remote Linux machines as local windows
 Obour opens individual apps from other Linux computers over SSH so they
 appear as normal windows on your desktop, with sound. Wayland apps are
 forwarded with waypipe and X11 apps with SSH X11 forwarding.
EOF
OUT=/src/dist/obour_${VERSION}_all.deb
dpkg-deb --root-owner-group --build "$PKG" "$OUT" >/dev/null
[ -z "${HOST_UID:-}" ] || chown "$HOST_UID:$HOST_GID" "$OUT"
echo "$OUT"
'
ls -lh "$ROOT"/dist/*.deb
