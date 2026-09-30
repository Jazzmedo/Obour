"""wlproxy: file lists in drags are rewritten both ways, everything else passes.

    python3 -m unittest discover tests

A fake compositor and a fake client (standing in for waypipe) talk through the
proxy with raw wire messages."""

import os
import socket
import struct
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from obour import wlproxy  # noqa: E402
from obour.wayland import _pack_string  # noqa: E402


def message(oid: int, opcode: int, payload: bytes = b"") -> bytes:
    return struct.pack("=II", oid, ((8 + len(payload)) << 16) | opcode) + payload


def read_message(sock: socket.socket):
    """One message: exactly its bytes, and the fds that came with them."""
    data, fds = b"", []
    size = 8
    while len(data) < size:
        chunk, new, _flags, _addr = socket.recv_fds(sock, size - len(data), 8)
        if not chunk:
            raise EOFError
        data += chunk
        fds += new
        if len(data) >= 8:
            size = struct.unpack_from("=II", data)[1] >> 16
    oid, word = struct.unpack_from("=II", data)
    return oid, word & 0xFFFF, data[8:word >> 16], fds


def pipe_with(data: bytes | None = None):
    """(read end, write end); data is written and the write end closed."""
    r, w = os.pipe()
    if data is not None:
        os.write(w, data)
        os.close(w)
        w = -1
    return r, w


def read_fd(fd: int) -> bytes:
    out = b""
    while chunk := os.read(fd, 4096):
        out += chunk
    os.close(fd)
    return out


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        compositor_path = os.path.join(self.dir, "compositor")
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(compositor_path)
        self.listener.listen(1)
        self.rewritten = []

        def rewrite(paths):
            self.rewritten.append(paths)
            return ["/tmp/obour-x" + p for p in paths]

        self.proxy = wlproxy.Proxy(compositor_path, self.dir, rewrite, log=lambda _t: None)
        self.client = socket.socket(socket.AF_UNIX)
        self.client.connect(self.proxy.start())
        self.server, _ = self.listener.accept()
        for sock in (self.client, self.server):
            sock.settimeout(5)
        # registry 2, data device manager 3, data device 4 (for seat 9)
        self.client.sendall(message(1, 1, struct.pack("=I", 2))
                            + message(2, 0, struct.pack("=I", 7)
                                      + _pack_string("wl_data_device_manager")
                                      + struct.pack("=II", 3, 3))
                            + message(3, 1, struct.pack("=II", 4, 9)))
        for _ in range(3):
            read_message(self.server)

    def tearDown(self):
        self.proxy.stop()
        for sock in (self.client, self.server, self.listener):
            sock.close()

    def drop_into_app(self, mime: str, data: bytes) -> bytes:
        """The compositor offers a drag; the app asks for mime; what it gets."""
        offer = 0xFF000001
        self.server.sendall(message(4, 0, struct.pack("=I", offer)))      # data_offer
        read_message(self.client)
        app_r, app_w = pipe_with()
        socket.send_fds(self.client, [message(offer, 1, _pack_string(mime))], [app_w])
        os.close(app_w)
        oid, opcode, body, fds = read_message(self.server)
        self.assertEqual((oid, opcode, len(fds)), (offer, 1, 1))
        os.write(fds[0], data)                    # the drag source answers
        os.close(fds[0])
        return read_fd(app_r)

    def drag_out_of_app(self, mime: str, data: bytes) -> bytes:
        """The app is dragging a data source; the compositor asks for mime."""
        source = 5
        self.client.sendall(message(3, 0, struct.pack("=I", source)))    # create_data_source
        read_message(self.server)
        target_r, target_w = pipe_with()
        socket.send_fds(self.server, [message(source, 1, _pack_string(mime))], [target_w])
        os.close(target_w)
        oid, opcode, body, fds = read_message(self.client)
        self.assertEqual((oid, opcode, len(fds)), (source, 1, 1))
        os.write(fds[0], data)                    # the app answers
        os.close(fds[0])
        return read_fd(target_r)

    def test_drop_rewrites_file_list(self):
        got = self.drop_into_app("text/uri-list", b"file:///home/me/a%20b.mkv\r\n")
        self.assertEqual(got, b"file:///tmp/obour-x/home/me/a%20b.mkv\r\n")
        self.assertEqual(self.rewritten, [["/home/me/a b.mkv"]])

    def test_drag_out_rewrites_file_list(self):
        got = self.drag_out_of_app("text/uri-list", b"file:///home/them/c.txt\n")
        self.assertEqual(got, b"file:///tmp/obour-x/home/them/c.txt\r\n")

    def test_other_types_pass_unchanged(self):
        got = self.drop_into_app("text/plain", b"file:///home/me/a")
        self.assertEqual(got, b"file:///home/me/a")
        self.assertEqual(self.rewritten, [])

    def test_portal_file_lists_are_hidden(self):
        # into the app: offer events of the compositor's offer
        offer = 0xFF000002
        self.server.sendall(message(4, 0, struct.pack("=I", offer))
                            + message(offer, 0, _pack_string("application/vnd.portal.filetransfer"))
                            + message(offer, 0, _pack_string("text/uri-list")))
        read_message(self.client)                                        # data_offer
        _oid, _op, body, _fds = read_message(self.client)
        self.assertIn(b"text/uri-list", body)
        # out of the app: offer requests of its data source
        self.client.sendall(message(3, 0, struct.pack("=I", 5))
                            + message(5, 0, _pack_string("application/vnd.portal.files"))
                            + message(5, 0, _pack_string("text/uri-list")))
        read_message(self.server)                                        # create_data_source
        _oid, _op, body, _fds = read_message(self.server)
        self.assertIn(b"text/uri-list", body)

    def test_web_links_pass_unchanged(self):
        got = self.drop_into_app("text/uri-list", b"https://example.org/\r\n")
        self.assertEqual(got, b"https://example.org/\r\n")


if __name__ == "__main__":
    unittest.main()
