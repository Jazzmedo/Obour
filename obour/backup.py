"""Back up and restore everything Obour keeps, as one .zip file.

    config/   ~/.config/obour: apps (with which ones are in the app menu), hosts,
              settings
    data/     ~/.local/share/obour: the apps' icons (used by app-menu entries)
    cache/    ~/.cache/obour: what Obour learned about hosts and their app icons
              (not the logs)
    ssh/      known_hosts, so hosts aren't asked about again; and, only when
              chosen, the SSH key Obour logs in with

Passwords are never in it: Obour keeps them in memory only. A restore replaces
Obour's files with the backup's, after saving the current ones to
~/.local/share/obour/backups/; app-menu entries are then made again (desktop.sync)."""

from __future__ import annotations

import io
import json
import os
import time
import zipfile
from dataclasses import dataclass

from . import __version__, auth
from .config import CACHE_DIR, CONFIG_DIR, DATA_DIR
from .i18n import _

FORMAT = 1
MANIFEST = "manifest.json"
SAFETY_DIR_NAME = "backups"            # in DATA_DIR; not itself backed up
MAX_FILE = 64 * 1024 * 1024            # nothing Obour keeps is near this
SKIP_CACHE = ("logs",)


class BackupError(Exception):
    pass


@dataclass
class Summary:
    created: str
    version: str
    apps: int
    in_menu: int
    hosts: int
    ssh_key: bool


def _folders() -> dict[str, str]:
    return {"config": CONFIG_DIR, "data": DATA_DIR, "cache": CACHE_DIR}


def _skipped(part: str, rel: str) -> bool:
    top = rel.split("/", 1)[0]
    return (part == "data" and top == SAFETY_DIR_NAME) or (part == "cache" and top in SKIP_CACHE)


def _files(part: str, root: str):
    """(relative path, absolute path) of the files to back up under root."""
    for here, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(here, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if os.path.isfile(path) and not os.path.islink(path) and not _skipped(part, rel):
                yield rel, path


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _ssh_key() -> str | None:
    """The private key Obour logs in with (see auth.local_public_key)."""
    public = auth.local_public_key()
    return public[:-len(".pub")] if public else None


def _launchers(data: bytes) -> list[dict]:
    try:
        value = json.loads(data)
    except ValueError:
        return []
    items = value.get("launchers", value) if isinstance(value, dict) else value
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def create(path: str, include_key: bool = False) -> Summary:
    """Write a backup to path."""
    buffer = io.BytesIO()
    launchers: list[dict] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for part, root in _folders().items():
            for rel, full in _files(part, root):
                with open(full, "rb") as f:
                    data = f.read()
                if part == "config" and rel == "launchers.json":
                    launchers = _launchers(data)
                z.writestr(f"{part}/{rel}", data)
        known = os.path.join(auth.SSH_DIR, "known_hosts")
        if os.path.isfile(known):
            z.write(known, "ssh/known_hosts")
        key = _ssh_key() if include_key else None
        if key:
            name = os.path.basename(key)
            z.write(key, f"ssh/{name}")
            z.write(key + ".pub", f"ssh/{name}.pub")
        manifest = {
            "app": "obour", "format": FORMAT, "version": __version__,
            "created": time.strftime("%Y-%m-%d %H:%M"),
            "apps": len(launchers),
            "in_menu": sum(1 for l in launchers if l.get("in_menu")),
            "hosts": len({l.get("host") for l in launchers if l.get("host")}),
            "ssh_key": bool(key),
            # saved files name these folders (icons); they may differ where it's restored
            "paths": _folders(),
        }
        z.writestr(MANIFEST, json.dumps(manifest, indent=2))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(buffer.getvalue())
    if key:
        os.chmod(tmp, 0o600)           # it holds a private key
    os.replace(tmp, path)
    return _summary(manifest)


def _summary(m: dict) -> Summary:
    return Summary(created=str(m.get("created", "")), version=str(m.get("version", "")),
                   apps=int(m.get("apps", 0)), in_menu=int(m.get("in_menu", 0)),
                   hosts=int(m.get("hosts", 0)), ssh_key=bool(m.get("ssh_key")))


def _open(path: str) -> tuple[zipfile.ZipFile, dict]:
    try:
        z = zipfile.ZipFile(path)
        manifest = json.loads(z.read(MANIFEST))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as e:
        raise BackupError(_("This isn’t an Obour backup.")) from e
    if manifest.get("app") != "obour":
        z.close()
        raise BackupError(_("This isn’t an Obour backup."))
    if int(manifest.get("format", 0)) > FORMAT:
        z.close()
        raise BackupError(_("This backup was made by a newer Obour. Update Obour to restore it."))
    for info in z.infolist():
        name = info.filename
        if (name.startswith("/") or "\\" in name or ".." in name.split("/")
                or info.file_size > MAX_FILE):
            z.close()
            raise BackupError(_("This backup has an unsafe file in it: {name}").format(name=name))
    return z, manifest


def read_summary(path: str) -> Summary:
    z, manifest = _open(path)
    z.close()
    return _summary(manifest)


def _clear(part: str, root: str) -> None:
    for rel, full in list(_files(part, root)):
        os.remove(full)
    for here, dirs, _names in os.walk(root, topdown=False):
        for d in dirs:
            path = os.path.join(here, d)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if not _skipped(part, rel) and not os.path.islink(path):
                try:
                    os.rmdir(path)           # only empty ones
                except OSError:
                    pass


def _write(path: str, data: bytes, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def restore(path: str) -> tuple[Summary, list[str]]:
    """Replace Obour's files with the backup's. Returns its summary and notes for the
    user. The caller makes the app-menu entries again (desktop.sync)."""
    z, manifest = _open(path)
    notes = []
    with z:
        safety = os.path.join(DATA_DIR, SAFETY_DIR_NAME,
                              time.strftime("before-restore-%Y%m%d-%H%M%S.zip"))
        create(safety)
        remove_safety_copies()
        notes.append(_("Your previous setup was saved to {path}").format(path=safety))
        folders = _folders()
        moved = [(old.encode(), folders[part].encode())
                 for part, old in (manifest.get("paths") or {}).items()
                 if part in folders and isinstance(old, str) and old and old != folders[part]]
        for part, root in folders.items():
            _clear(part, root)
        for info in z.infolist():
            part, _sep, rel = info.filename.partition("/")
            if info.is_dir() or not rel:
                continue
            if part in folders and not _skipped(part, rel):
                data = z.read(info)
                if rel.endswith(".json"):
                    for old, new in moved:
                        data = data.replace(old, new)
                _write(os.path.join(folders[part], rel), data)
        notes += _restore_ssh(z)
    return _summary(manifest), notes


def _restore_ssh(z: zipfile.ZipFile) -> list[str]:
    notes = []
    names = [n[len("ssh/"):] for n in z.namelist() if n.startswith("ssh/") and n != "ssh/"]
    if not names:
        return notes
    os.makedirs(auth.SSH_DIR, mode=0o700, exist_ok=True)
    if "known_hosts" in names:
        # added to, never replaced: the lines here stay
        path = os.path.join(auth.SSH_DIR, "known_hosts")
        have = _read(path).splitlines() if os.path.isfile(path) else []
        new = [l for l in z.read("ssh/known_hosts").splitlines() if l.strip() and l not in have]
        if new:
            _write(path, b"\n".join(have + new) + b"\n", 0o600)
    for name in names:
        if name == "known_hosts" or name.endswith(".pub") or name not in auth.KEY_NAMES:
            continue
        private, public = z.read(f"ssh/{name}"), z.read(f"ssh/{name}.pub")
        path = os.path.join(auth.SSH_DIR, name)
        if not os.path.exists(path):
            _write(path, private, 0o600)
            _write(path + ".pub", public, 0o644)
            notes.append(_("Restored your SSH key {path}").format(path=path))
        elif _read(path) != private:
            # never overwrite a key: keep the backup's next to it
            kept = os.path.join(auth.SSH_DIR, f"{name}.obour-backup")
            _write(kept, private, 0o600)
            _write(kept + ".pub", public, 0o644)
            notes.append(_("You already have a different SSH key in {path}; the backup’s "
                           "key was saved as {kept}").format(path=path, kept=kept))
    return notes


def default_name() -> str:
    return time.strftime("obour-backup-%Y-%m-%d.zip")


def safety_dir() -> str:
    return os.path.join(DATA_DIR, SAFETY_DIR_NAME)


def remove_safety_copies(keep: int = 5) -> None:
    """Keep only the newest automatic copies made before restores."""
    folder = safety_dir()
    try:
        files = sorted(f for f in os.listdir(folder) if f.startswith("before-restore-"))
    except OSError:
        return
    for name in files[:-keep]:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass

