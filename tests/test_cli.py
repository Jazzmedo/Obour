"""CLI: add, find by id or name, remove; in a throwaway config folder.

    python3 -m unittest discover tests"""

import os
import subprocess
import sys
import tempfile
import unittest

OBOUR = os.path.join(os.path.dirname(__file__), os.pardir, "bin", "obour")


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, **{k: os.path.join(self.tmp.name, k) for k in
                                       ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME")})

    def tearDown(self):
        self.tmp.cleanup()

    def obour(self, *args):
        r = subprocess.run([sys.executable, OBOUR, *args], env=self.env,
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_add_list_remove(self):
        code, out = self.obour("add", "me@pc", "gimp -n", "--menu")
        self.assertEqual(code, 0)
        app_id = out.strip()
        menu = os.path.join(self.tmp.name, "XDG_DATA_HOME", "applications", f"obour-{app_id}.desktop")
        self.assertTrue(os.path.exists(menu))
        self.assertIn(f"{app_id}  gimp  (me@pc: gimp -n)  [menu]", self.obour("list")[1])
        self.obour("add", "me@laptop", "gimp")
        self.assertEqual(self.obour("remove", "GIMP")[0], 1)    # two apps have this name
        self.assertEqual(self.obour("remove", app_id)[0], 0)
        self.assertFalse(os.path.exists(menu))
        self.assertEqual(self.obour("remove", "gimp")[0], 0)    # now unique
        self.assertEqual(self.obour("list"), (0, ""))

    def test_help_and_errors(self):
        self.assertEqual(self.obour("help", "add")[0], 0)
        self.assertEqual(self.obour("add")[0], 2)
        self.assertEqual(self.obour("remove", "nope")[0], 1)


if __name__ == "__main__":
    unittest.main()
