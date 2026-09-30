#!/usr/bin/env python3
"""Build obour/wlproto.json, the message signatures wlproxy.py needs, from Wayland
protocol XML files.

    packaging/wayland/gen-protocols.py [extra.xml ...]

Reads /usr/share/wayland/wayland.xml and /usr/share/wayland-protocols, plus the
files given (waypipe also forwards wlr-protocols, gtk-primary-selection, wl_drm and
KDE's server-decoration, whose signatures are in server-decoration.xml here:
https://gitlab.freedesktop.org/wlroots/wlr-protocols, gtk's
gdk/wayland/protocol/gtk-primary-selection.xml, mesa's wayland-drm.xml).

Output: {interface: [requests, events]}, each message a string of argument codes:
i u f o (numbers and objects), s a (strings, arrays), h (fd), n:<interface> (a new
object of that interface), N (a new object named by the request, as in bind)."""

import glob
import json
import os
import sys
import xml.etree.ElementTree as ET

CODES = {"int": "i", "uint": "u", "fixed": "f", "object": "o", "string": "s",
         "array": "a", "fd": "h"}


def signature(message) -> str:
    args = []
    for arg in message.findall("arg"):
        kind = arg.get("type")
        if kind == "new_id":
            args.append(f"n:{arg.get('interface')}" if arg.get("interface") else "N")
        else:
            args.append(CODES[kind])
    return " ".join(args)


def main() -> None:
    # Later files win: old unstable protocols reuse names (xdg-shell v5 has an
    # xdg_surface of its own), so the stable ones are read last.
    base = "/usr/share/wayland-protocols"
    files = [*sys.argv[1:],
             *(path for kind in ("unstable", "staging", "stable")
               for path in sorted(glob.glob(f"{base}/{kind}/**/*.xml", recursive=True))),
             "/usr/share/wayland/wayland.xml"]
    table = {}
    for path in files:
        for interface in ET.parse(path).getroot().findall("interface"):
            table[interface.get("name")] = [
                [signature(m) for m in interface.findall("request")],
                [signature(m) for m in interface.findall("event")]]
    out = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "obour", "wlproto.json")
    with open(out, "w") as f:
        json.dump(table, f, separators=(",", ":"), sort_keys=True)
        f.write("\n")
    print(f"{len(table)} interfaces -> {os.path.normpath(out)}")


if __name__ == "__main__":
    main()
