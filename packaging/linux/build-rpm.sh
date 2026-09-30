#!/bin/sh
# Build dist/obour-<version>-1.noarch.rpm in a Fedora container.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
IMAGE=${OBOUR_RPM_IMAGE:-registry.fedoraproject.org/fedora:latest}
ENGINE=$(command -v podman || command -v docker || true)
[ -n "$ENGINE" ] || { echo "podman or docker is required to build the .rpm." >&2; exit 1; }
mkdir -p "$ROOT/dist"
# Rootless podman already maps container root to the calling user; docker needs a chown.
OWNER=""
case "$(basename "$ENGINE")" in
    docker) OWNER="-e HOST_UID=$(id -u) -e HOST_GID=$(id -g)" ;;
esac

# shellcheck disable=SC2086
"$ENGINE" run --rm -v "$ROOT:/src:Z" $OWNER "$IMAGE" sh -euc '
dnf -q -y install rpm-build >/dev/null 2>&1 || dnf -y install rpm-build
VERSION=$(sed -n "s/^__version__ = \"\(.*\)\"/\1/p" /src/obour/__init__.py)
# Without %{dist}: one package for every Fedora/openSUSE/RHEL release.
rpmbuild -bb --quiet --define "_topdir /build" --define "source_date_epoch_from_changelog 0" --define "dist %{nil}" \
    --define "obour_version $VERSION" --define "obour_src /src" \
    /src/packaging/linux/obour.spec
for f in /build/RPMS/noarch/*.rpm; do
    cp "$f" /src/dist/
    [ -z "${HOST_UID:-}" ] || chown "$HOST_UID:$HOST_GID" "/src/dist/$(basename "$f")"
done
'
ls -lh "$ROOT"/dist/*.rpm
