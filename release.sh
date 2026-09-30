#!/usr/bin/env bash
# Build Obour's AppImage, .deb and .rpm and publish them as a GitHub release.
#
#   ./release.sh            asks for the tag, confirms, builds, tags, pushes, uploads
#   ./release.sh --dry-run  only builds the current version (no commit, tag, push or upload)
#
# Needs: git, gh (logged in: gh auth login), podman or docker.
set -euo pipefail
cd "$(dirname "$0")"

REPO=Jazzmedo/Obour
BRANCH=main
APP_ID=io.github.Jazzmedo.Obour
DRY_RUN=0
[[ ${1:-} == --dry-run ]] && DRY_RUN=1

if [[ -t 1 ]]; then
    B=$'\e[1m' DIM=$'\e[2m' RED=$'\e[31m' GRN=$'\e[32m' YLW=$'\e[33m' BLU=$'\e[34m' R=$'\e[0m'
else
    B='' DIM='' RED='' GRN='' YLW='' BLU='' R=''
fi
step() { printf '\n%s==>%s %s%s%s\n' "$BLU" "$R" "$B" "$*" "$R"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$R" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$R" "$*"; }
die()  { printf '%s✗ %s%s\n' "$RED" "$*" "$R" >&2; exit 1; }

current_version() { sed -n 's/^__version__ = "\(.*\)"/\1/p' obour/__init__.py; }

# ---------------------------------------------------------------- checks
step "Checking"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Not a git repository."
command -v podman >/dev/null || command -v docker >/dev/null || die "podman or docker is needed to build."
branch=$(git symbolic-ref --short HEAD 2>/dev/null || true)
if (( DRY_RUN )); then
    [[ -z $(git status --porcelain) ]] || warn "Uncommitted changes will be included in this test build."
else
    [[ $branch == "$BRANCH" ]] || die "Switch to '$BRANCH' first (you are on '${branch:-a detached HEAD}')."
    [[ -z $(git status --porcelain) ]] || die "Commit or stash your changes first (git status)."
    command -v gh >/dev/null || die "Install the GitHub CLI (gh)."
    gh auth status >/dev/null 2>&1 || die "Log in to GitHub first: gh auth login"
    git fetch --quiet --tags origin "$BRANCH" || die "Couldn't reach origin."
    behind=$(git rev-list --count "HEAD..origin/$BRANCH")
    (( behind == 0 )) || die "Your '$BRANCH' is $behind commit(s) behind origin. Run: git pull"
fi
ok "Ready"

version=$(current_version)
last_tag=$(git describe --tags --abbrev=0 2>/dev/null || echo "none yet")
printf '  Version in the code: %s%s%s\n  Last release tag:    %s%s%s\n' "$B" "$version" "$R" "$B" "$last_tag" "$R"

# ---------------------------------------------------------------- questions
if (( DRY_RUN )); then
    tag="v$version"
    notes=""
else
    while true; do
        read -rp "${B}Release tag${R} (e.g. v$version): " tag
        tag=${tag// /}
        [[ $tag == [0-9]* ]] && tag="v$tag"
        if [[ ! $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]; then
            warn "Use the form v1.2.3 (or v1.2.3-beta.1)."
        elif git rev-parse -q --verify "refs/tags/$tag" >/dev/null \
                || git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null 2>&1; then
            warn "$tag already exists."
        else
            break
        fi
    done
    read -rp "${B}Release notes${R} (one line; empty = list of commits since $last_tag): " notes
fi
new_version=${tag#v}
prerelease=0
[[ $new_version == *-* ]] && prerelease=1

appimage="dist/Obour-$new_version-x86_64.AppImage"
files=("$appimage" "$appimage.zsync" "dist/obour_${new_version}_all.deb"
       "dist/obour-${new_version}-1.noarch.rpm" "dist/SHA256SUMS")

printf '\n%sSummary%s\n' "$B" "$R"
printf '  Repository:  https://github.com/%s\n' "$REPO"
printf '  Tag:         %s%s%s%s\n' "$B" "$tag" "$R" "$( (( prerelease )) && echo ' (pre-release)')"
printf '  Version:     %s -> %s\n' "$version" "$new_version"
printf '  Notes:       %s\n' "${notes:-generated from commits}"
printf '  Files:\n'; printf '    %s\n' "${files[@]}"
if (( DRY_RUN )); then
    printf '  %sDry run: builds only; nothing is committed, tagged, pushed or uploaded.%s\n' "$YLW" "$R"
else
    printf '  This commits "Release %s", pushes %s and the tag, and publishes the release.\n' "$tag" "$BRANCH"
    read -rp "${B}Are you sure? [y/N]${R} " answer
    [[ $answer =~ ^[Yy]([Ee][Ss])?$ ]] || die "Cancelled. Nothing was changed."
fi

# ---------------------------------------------------------------- version bump
bumped=0
restore() {
    if (( bumped )); then
        git checkout -- obour/__init__.py "data/$APP_ID.metainfo.xml"
        warn "Build failed; the version change was undone. Nothing was pushed."
    fi
}
trap restore ERR

if [[ $new_version != "$version" ]]; then
    step "Setting version $new_version"
    sed -i "s/^__version__ = \".*\"/__version__ = \"$new_version\"/" obour/__init__.py
    bumped=1
fi
if (( ! DRY_RUN )) && ! grep -q "<release version=\"$new_version\"" "data/$APP_ID.metainfo.xml"; then
    sed -i "s|  <releases>|  <releases>\n    <release version=\"$new_version\" date=\"$(date +%F)\"/>|" \
        "data/$APP_ID.metainfo.xml"
    bumped=1
fi

# ---------------------------------------------------------------- build
step "Building the AppImage"
packaging/appimage/build.sh
step "Building the .deb"
packaging/linux/build-deb.sh
step "Building the .rpm"
packaging/linux/build-rpm.sh

for f in "${files[@]:0:4}"; do
    [[ -s $f ]] || die "Missing build output: $f"
done
(cd dist && sha256sum "$(basename "$appimage")" "$(basename "$appimage").zsync" \
    "obour_${new_version}_all.deb" "obour-${new_version}-1.noarch.rpm" > SHA256SUMS)
ok "Built:"
ls -lh "${files[@]}" | awk '{print "    " $5 "  " $NF}'
trap - ERR

if (( DRY_RUN )); then
    printf '\n%sDry run finished.%s The files are in dist/.\n' "$GRN" "$R"
    exit 0
fi

# ---------------------------------------------------------------- publish
step "Tagging $tag and pushing"
if (( bumped )); then
    git add obour/__init__.py "data/$APP_ID.metainfo.xml"
    git commit -q -m "Release $tag"
fi
git tag -a "$tag" -m "Obour $tag"
git push --quiet origin "$BRANCH"
git push --quiet origin "$tag"
ok "Pushed $BRANCH and $tag"

step "Creating the GitHub release"
args=(--repo "$REPO" --title "Obour $tag" --verify-tag)
if [[ -n $notes ]]; then args+=(--notes "$notes"); else args+=(--generate-notes); fi
(( prerelease )) && args+=(--prerelease)
gh release create "$tag" "${files[@]}" "${args[@]}"

printf '\n%s✓ Released %s%s  https://github.com/%s/releases/tag/%s\n' "$GRN$B" "$tag" "$R" "$REPO" "$tag"
printf '%sGear Lever users get it from "Check for updates"; the .zsync lets them download only what changed.%s\n' "$DIM" "$R"
