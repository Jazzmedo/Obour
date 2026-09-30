"""share: a helper from another version of Obour makes way for the new one.

    python3 -m unittest discover tests"""

import json
import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from obour import share  # noqa: E402


class HandOverTest(unittest.TestCase):
    def setUp(self):
        self.daemon = share.Daemon()
        self.held = []
        self.daemon.hold = lambda host: self.held.append(host) or []
        self.daemon.release = lambda host: None
        self.old_log, share._log = share._log, lambda _t: None

    def tearDown(self):
        share._log = self.old_log

    def ask(self, request: dict) -> dict:
        ours, theirs = socket.socketpair()
        thread = threading.Thread(target=self.daemon._client, args=(theirs,))
        thread.start()
        ours.sendall(json.dumps(request).encode() + b"\n")
        reply = json.loads(ours.makefile("rb").readline())
        ours.close()
        thread.join(5)
        return reply

    def test_same_version_is_served(self):
        reply = self.ask({"host": "h", "build": share.build()})
        self.assertTrue(reply["ok"])
        self.assertFalse(self.daemon.outdated)

    def test_idle_helper_makes_way(self):
        reply = self.ask({"host": "h", "build": "other"})
        self.assertTrue(reply.get("restart"))
        self.assertTrue(self.daemon.outdated)
        self.assertEqual(self.held, [])

    def test_busy_helper_serves_then_leaves(self):
        self.daemon.holds["h"] = 1                  # an app is running
        reply = self.ask({"host": "h", "build": "other"})
        self.assertTrue(reply["ok"])
        self.assertTrue(self.daemon.outdated)


if __name__ == "__main__":
    unittest.main()
