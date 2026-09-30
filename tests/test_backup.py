"""backup: everything comes back, nothing outside Obour's folders is touched.

    python3 -m unittest discover tests"""

import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from obour import auth, backup  # noqa: E402

LAUNCHERS = {"launchers": [{"id": "a1", "host": "me@pc", "in_menu": True},
                           {"id": "b2", "host": "me@pc", "in_menu": False},
                           {"id": "c3", "host": "me@laptop", "in_menu": True}]}


def write(path: str, data: bytes | str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data.encode() if isinstance(data, str) else data)


def read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.saved = (backup.CONFIG_DIR, backup.DATA_DIR, backup.CACHE_DIR, auth.SSH_DIR)
        backup.CONFIG_DIR = os.path.join(self.root, "config")
        backup.DATA_DIR = os.path.join(self.root, "data")
        backup.CACHE_DIR = os.path.join(self.root, "cache")
        auth.SSH_DIR = os.path.join(self.root, "ssh")
        write(f"{backup.CONFIG_DIR}/launchers.json", json.dumps(LAUNCHERS))
        write(f"{backup.CONFIG_DIR}/hosts.json", '{"me@pc": {}}')
        write(f"{backup.CONFIG_DIR}/settings.json", '{"language": "ar"}')
        write(f"{backup.DATA_DIR}/icons/a1.svg", "<svg/>")
        write(f"{backup.CACHE_DIR}/hosts.json", "{}")
        write(f"{backup.CACHE_DIR}/icons/me_pc/firefox.png", b"\x89PNG")
        write(f"{backup.CACHE_DIR}/logs/a1.log", "log")
        write(f"{auth.SSH_DIR}/known_hosts", "pc ssh-ed25519 AAAA\n")
        write(f"{auth.SSH_DIR}/id_ed25519", "PRIVATE")
        write(f"{auth.SSH_DIR}/id_ed25519.pub", "ssh-ed25519 PUB")
        self.zip = os.path.join(self.root, "out", "b.zip")

    def tearDown(self):
        backup.CONFIG_DIR, backup.DATA_DIR, backup.CACHE_DIR, auth.SSH_DIR = self.saved

    def test_summary(self):
        s = backup.create(self.zip)
        self.assertEqual((s.apps, s.in_menu, s.hosts, s.ssh_key), (3, 2, 2, False))
        self.assertEqual(backup.read_summary(self.zip), s)

    def test_round_trip_restores_everything(self):
        backup.create(self.zip)
        # the setup changes after the backup
        write(f"{backup.CONFIG_DIR}/launchers.json", '{"launchers": []}')
        write(f"{backup.CONFIG_DIR}/extra.json", "{}")
        os.remove(f"{backup.DATA_DIR}/icons/a1.svg")
        backup.restore(self.zip)
        self.assertEqual(json.loads(read(f"{backup.CONFIG_DIR}/launchers.json")), LAUNCHERS)
        self.assertFalse(os.path.exists(f"{backup.CONFIG_DIR}/extra.json"))
        self.assertEqual(read(f"{backup.DATA_DIR}/icons/a1.svg"), b"<svg/>")
        self.assertEqual(read(f"{backup.CACHE_DIR}/icons/me_pc/firefox.png"), b"\x89PNG")
        # logs aren't backed up, and aren't removed either
        self.assertEqual(read(f"{backup.CACHE_DIR}/logs/a1.log"), b"log")
        # the setup before the restore was kept
        self.assertEqual(len(os.listdir(backup.safety_dir())), 1)

    def test_paths_follow_the_new_home(self):
        icon = f"{backup.DATA_DIR}/icons/a1.svg"
        write(f"{backup.CONFIG_DIR}/launchers.json", json.dumps({"launchers": [{"icon": icon}]}))
        backup.create(self.zip)
        old_data = backup.DATA_DIR
        backup.DATA_DIR = os.path.join(self.root, "elsewhere")
        try:
            backup.restore(self.zip)
            got = json.loads(read(f"{backup.CONFIG_DIR}/launchers.json"))["launchers"][0]["icon"]
            self.assertEqual(got, f"{backup.DATA_DIR}/icons/a1.svg")
            self.assertTrue(os.path.isfile(got))
        finally:
            backup.DATA_DIR = old_data

    def test_key_only_when_asked(self):
        with zipfile.ZipFile(backup.create(self.zip) and self.zip) as z:
            self.assertNotIn("ssh/id_ed25519", z.namelist())
            self.assertIn("ssh/known_hosts", z.namelist())
        self.assertTrue(backup.create(self.zip, include_key=True).ssh_key)
        self.assertEqual(os.stat(self.zip).st_mode & 0o777, 0o600)

    def test_key_restored_but_never_overwritten(self):
        backup.create(self.zip, include_key=True)
        os.remove(f"{auth.SSH_DIR}/id_ed25519")
        os.remove(f"{auth.SSH_DIR}/id_ed25519.pub")
        backup.restore(self.zip)
        self.assertEqual(read(f"{auth.SSH_DIR}/id_ed25519"), b"PRIVATE")
        self.assertEqual(os.stat(f"{auth.SSH_DIR}/id_ed25519").st_mode & 0o777, 0o600)
        write(f"{auth.SSH_DIR}/id_ed25519", "OTHER")
        backup.restore(self.zip)
        self.assertEqual(read(f"{auth.SSH_DIR}/id_ed25519"), b"OTHER")
        self.assertEqual(read(f"{auth.SSH_DIR}/id_ed25519.obour-backup"), b"PRIVATE")

    def test_known_hosts_are_added_to(self):
        backup.create(self.zip)
        write(f"{auth.SSH_DIR}/known_hosts", "other ssh-rsa BBBB\n")
        backup.restore(self.zip)
        self.assertEqual(read(f"{auth.SSH_DIR}/known_hosts").splitlines(),
                         [b"other ssh-rsa BBBB", b"pc ssh-ed25519 AAAA"])

    def test_unsafe_or_foreign_zip_is_refused(self):
        bad = os.path.join(self.root, "bad.zip")
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("manifest.json", json.dumps({"app": "obour", "format": 1}))
            z.writestr("config/../../evil", "x")
        self.assertRaises(backup.BackupError, backup.restore, bad)
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("hello.txt", "x")
        self.assertRaises(backup.BackupError, backup.read_summary, bad)
        self.assertEqual(json.loads(read(f"{backup.CONFIG_DIR}/launchers.json")), LAUNCHERS)


if __name__ == "__main__":
    unittest.main()
