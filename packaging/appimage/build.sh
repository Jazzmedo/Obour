#!/bin/sh
# Build dist/Obour-<version>-x86_64.AppImage inside an Ubuntu 24.04 container.
# The oldest supported glibc/GTK comes from the build image, so the AppImage runs on
# Ubuntu 24.04+, Debian 13+, Fedora 40+, Arch and similar.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
IMAGE=${OBOUR_BUILD_IMAGE:-docker.io/library/ubuntu:24.04}
ENGINE=$(command -v podman || command -v docker || true)
if [ -z "$ENGINE" ]; then
    echo "podman or docker is required to build the AppImage." >&2
    exit 1
fi

mkdir -p "$ROOT/dist"
# Rootless podman already maps container root to the calling user; docker needs a chown.
OWNER=""
case "$(basename "$ENGINE")" in
    docker) OWNER="-e HOST_UID=$(id -u) -e HOST_GID=$(id -g)" ;;
esac
# shellcheck disable=SC2086
"$ENGINE" run --rm -v "$ROOT:/src:Z" $OWNER \
    "$IMAGE" sh /src/packaging/appimage/build-in-container.sh

ls -lh "$ROOT"/dist/*.AppImage
