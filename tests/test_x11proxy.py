"""x11proxy: file lists in X11 selections are rewritten both ways, all else passes.

    python3 -m unittest discover tests

A fake X server and client (standing in for ssh's X11 forwarding) talk through
the proxy with raw protocol messages, in both byte orders."""

import os
import socket
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from obour import x11proxy  # noqa: E402

URI_ATOM = 300


def pad(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % 4)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


class Base:
    ORDER = "<"

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old_dir, self.old_cookie = x11proxy.SOCKET_DIR, x11proxy._cookie
        x11proxy.SOCKET_DIR = self.dir
        x11proxy._cookie = lambda _display: (b"MIT-MAGIC-COOKIE-1", b"\x01" * 16)
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(os.path.join(self.dir, "X0"))
        self.listener.listen(1)
        self.rewritten = []

        def rewrite(paths):
            self.rewritten.append(paths)
            return ["/tmp/obour-x" + p for p in paths]

        self.proxy = x11proxy.Proxy(":0", rewrite, log=lambda _t: None)
        display = self.proxy.start()
        self.client = socket.socket(socket.AF_UNIX)
        self.client.connect(os.path.join(self.dir, "X" + display[1:]))
        self.server = None
        for sock in (self.client,):
            sock.settimeout(5)
        self.handshake()

    def tearDown(self):
        self.proxy.stop()
        x11proxy.SOCKET_DIR, x11proxy._cookie = self.old_dir, self.old_cookie
        for sock in (self.client, self.server, self.listener):
            if sock is not None:
                sock.close()

    def p(self, fmt, *values):
        return struct.pack(self.ORDER + fmt, *values)

    def u(self, fmt, data, pos=0):
        return struct.unpack_from(self.ORDER + fmt, data, pos)

    def handshake(self):
        order = b"l" if self.ORDER == "<" else b"B"
        fake = b"\xAA" * 16
        self.client.sendall(order + b"\0" + self.p("HHHHH", 11, 0, 18, 16, 0)
                            + pad(b"MIT-MAGIC-COOKIE-1") + fake)
        self.server, _ = self.listener.accept()
        self.server.settimeout(5)
        setup = recv_exact(self.server, 12)
        n, d = self.u("HH", setup, 6)
        auth = recv_exact(self.server, len(pad(b"x" * n)) + len(pad(b"x" * d)))
        self.cookie_seen = auth[len(pad(b"x" * n)):][:d]
        reply = bytes([1, 0]) + self.p("HHH", 11, 0, 2) + b"\0" * 8     # 8 extra bytes
        self.server.sendall(reply)
        recv_exact(self.client, len(reply))

    # --- helpers for requests and replies

    def request(self, opcode: int, data: int, body: bytes) -> bytes:
        body = pad(body)
        return bytes([opcode, data]) + self.p("H", 1 + len(body) // 4) + body

    def intern(self, name: str, seq: int, atom: int) -> None:
        self.client.sendall(self.request(16, 0, self.p("HH", len(name), 0) + name.encode()))
        recv_exact(self.server, 4 + len(pad(self.p("HH", 0, 0) + name.encode())))
        self.server.sendall(bytes([1, 0]) + self.p("HII", seq, 0, atom) + b"\0" * 20)
        recv_exact(self.client, 32)

    def change_property(self, prop_type: int, data: bytes) -> bytes:
        body = self.p("IIIB3xI", 5, 6, prop_type, 8, len(data)) + data
        self.client.sendall(self.request(18, 0, body))
        head = recv_exact(self.server, 4)
        rest = recv_exact(self.server, self.u("H", head, 2)[0] * 4 - 4)
        count = self.u("I", rest, 16)[0]
        return rest[20:20 + count]

    def get_property_reply(self, seq: int, prop_type: int, data: bytes) -> bytes:
        self.client.sendall(self.request(20, 0, self.p("IIIII", 5, 6, 0, 0, 1000)))
        recv_exact(self.server, 24)
        body = pad(data)
        self.server.sendall(bytes([1, 8]) + self.p("HIIII", seq, len(body) // 4, prop_type,
                                                   0, len(data)) + b"\0" * 12 + body)
        head = recv_exact(self.client, 32)
        rest = recv_exact(self.client, self.u("I", head, 4)[0] * 4)
        return rest[:self.u("I", head, 16)[0]]

    # --- tests

    def test_real_cookie_replaces_the_made_up_one(self):
        self.assertEqual(self.cookie_seen, b"\x01" * 16)

    def test_drag_out_of_app_rewrites_file_list(self):
        self.intern("text/uri-list", 1, URI_ATOM)
        got = self.change_property(URI_ATOM, b"file:///home/them/c.txt\r\n")
        self.assertEqual(got, b"file:///tmp/obour-x/home/them/c.txt\r\n")

    def test_drop_into_app_rewrites_file_list(self):
        self.intern("text/uri-list", 1, URI_ATOM)
        got = self.get_property_reply(2, URI_ATOM, b"file:///home/me/a%20b.mkv\r\n")
        self.assertEqual(got, b"file:///tmp/obour-x/home/me/a%20b.mkv\r\n")
        self.assertEqual(self.rewritten, [["/home/me/a b.mkv"]])

    def test_other_properties_pass_unchanged(self):
        self.intern("text/uri-list", 1, URI_ATOM)
        self.assertEqual(self.change_property(31, b"file:///home/me/a"), b"file:///home/me/a")
        self.assertEqual(self.get_property_reply(3, 31, b"file:///x"), b"file:///x")
        self.assertEqual(self.rewritten, [])

    def test_unknown_atom_passes_unchanged(self):
        # nobody interned text/uri-list through the proxy: the atom isn't known
        self.assertEqual(self.change_property(URI_ATOM, b"file:///home/me/a\r\n"),
                         b"file:///home/me/a\r\n")

    def query_extension(self, name: str, seq: int) -> bytes:
        self.client.sendall(self.request(98, 0, self.p("HH", len(name), 0) + name.encode()))
        recv_exact(self.server, 4 + len(pad(self.p("HH", 0, 0) + name.encode())))
        self.server.sendall(bytes([1, 0]) + self.p("HI", seq, 0) + bytes([1, 150, 0, 0])
                            + b"\0" * 20)
        return recv_exact(self.client, 32)

    def test_dri3_is_hidden(self):
        # its replies carry file descriptors, which can't reach a remote app
        self.assertEqual(self.query_extension("DRI3", 1)[8:12], b"\0\0\0\0")
        self.assertEqual(self.query_extension("RANDR", 2)[8:10], bytes([1, 150]))

    def test_big_request_passes(self):
        body = b"\x07" * 1000
        message = bytes([72, 0]) + self.p("HI", 0, 2 + len(body) // 4) + body   # PutImage
        self.client.sendall(message)
        self.assertEqual(recv_exact(self.server, len(message)), message)
        # the sequence count stays right after it
        self.intern("text/uri-list", 2, URI_ATOM)
        got = self.get_property_reply(3, URI_ATOM, b"file:///home/me/b\r\n")
        self.assertEqual(got, b"file:///tmp/obour-x/home/me/b\r\n")


class LittleEndian(Base, unittest.TestCase):
    ORDER = "<"


class BigEndian(Base, unittest.TestCase):
    ORDER = ">"


if __name__ == "__main__":
    unittest.main()
