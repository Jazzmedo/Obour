#!/usr/bin/python3
"""Draw assets/banner.svg for the README: the logo, the name and what Obour does,
as a pedestrian crossing (عبور) on asphalt.

Text is turned into outlines (Pango shapes the Arabic, cairo writes paths), so the
banner looks the same on every computer without the fonts installed.
Needs: Poppins and IBM Plex Sans Arabic fonts, PyGObject with Pango and Rsvg.
Run: assets/make-banner.py"""

import os

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("Rsvg", "2.0")
from gi.repository import Pango, PangoCairo, Rsvg  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
W, H, RADIUS = 1280, 340, 28
LOGO = os.path.join(HERE, "Logo", "trans.svg")
OUT = os.path.join(HERE, "banner.svg")


def rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


def text(cr, x, y, markup, font, rgba=(1, 1, 1, 1), rtl=False):
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string(font))
    layout.set_markup(markup, -1)
    if rtl:
        layout.set_auto_dir(True)
    cr.move_to(x, y)
    cr.set_source_rgba(*rgba)
    PangoCairo.layout_path(cr, layout)  # outlines, not <text>
    cr.fill()
    ink, logical = layout.get_pixel_extents()
    return logical.width, logical.height


# --- feature icons, drawn in a 20 x 20 box at (x, y)

def _icon_drag(cr, x, y):
    # a file, and a pointer dragging it
    rounded_rect(cr, x + 1, y + 1, 11, 14, 2)
    cr.stroke()
    cr.move_to(x + 10, y + 8)
    cr.line_to(x + 10, y + 20)
    cr.line_to(x + 13, y + 17)
    cr.line_to(x + 15.5, y + 21)
    cr.line_to(x + 17.5, y + 20)
    cr.line_to(x + 15, y + 16)
    cr.line_to(x + 19, y + 16)
    cr.close_path()
    cr.fill()


def _icon_clipboard(cr, x, y):
    rounded_rect(cr, x + 3, y + 3, 14, 17, 2.5)
    cr.stroke()
    rounded_rect(cr, x + 7, y + 0.5, 6, 5, 1.5)
    cr.fill()
    for row in (10, 14):
        cr.move_to(x + 6.5, y + row)
        cr.line_to(x + 13.5, y + row)
    cr.stroke()


def _icon_theme(cr, x, y):
    # a half-filled circle: light and dark, your colors
    cr.arc(x + 10, y + 10, 8.5, 0, 6.2832)
    cr.stroke()
    cr.arc(x + 10, y + 10, 8.5, 1.5708, 4.7124)
    cr.close_path()
    cr.fill()


def _icon_sound(cr, x, y):
    cr.move_to(x + 1, y + 7)
    cr.line_to(x + 5, y + 7)
    cr.line_to(x + 10, y + 2.5)
    cr.line_to(x + 10, y + 17.5)
    cr.line_to(x + 5, y + 13)
    cr.line_to(x + 1, y + 13)
    cr.close_path()
    cr.fill()
    for r in (4.5, 8):
        cr.arc(x + 11, y + 10, r, -0.9, 0.9)
        cr.stroke()


def _icon_auto(cr, x, y):
    # two arrows: Wayland or X11, switched for you
    for ay, sign in ((6, 1), (14, -1)):
        x0, x1 = (x + 2, x + 18) if sign > 0 else (x + 18, x + 2)
        cr.move_to(x0, y + ay)
        cr.line_to(x1, y + ay)
        cr.stroke()
        cr.move_to(x1, y + ay)
        cr.line_to(x1 - 4.5 * sign, y + ay - 3.5)
        cr.line_to(x1 - 4.5 * sign, y + ay + 3.5)
        cr.close_path()
        cr.fill()


def _icon_lock(cr, x, y):
    cr.arc(x + 10, y + 7.5, 4.5, 3.1416, 0)
    cr.move_to(x + 5.5, y + 7.5)
    cr.line_to(x + 5.5, y + 9)
    cr.move_to(x + 14.5, y + 7.5)
    cr.line_to(x + 14.5, y + 9)
    cr.stroke()
    rounded_rect(cr, x + 2.5, y + 9, 15, 11, 2.5)
    cr.fill()


def _icon_backup(cr, x, y):
    # a box with a lid: everything packed in one file
    rounded_rect(cr, x + 1.5, y + 2.5, 17, 5, 1.5)
    cr.stroke()
    rounded_rect(cr, x + 3, y + 7.5, 14, 11, 2)
    cr.stroke()
    cr.move_to(x + 7.5, y + 11.5)
    cr.line_to(x + 12.5, y + 11.5)
    cr.stroke()


FEATURES = [
    [("Drag & drop files", _icon_drag), ("Copy & paste files", _icon_clipboard),
     ("Your theme & colors", _icon_theme)],
    [("Sound", _icon_sound), ("Wayland or X11, chosen for you", _icon_auto),
     ("Just SSH", _icon_lock), ("Backup & restore", _icon_backup)],
]


# Colors: an asphalt road and its markings. "Obour" (عبور) is the word painted on
# pedestrian crossings, so the banner is a crossing: one warm accent, no gradients.
ASPHALT = (0x1b / 255, 0x1d / 255, 0x20 / 255)
STRIPE = (0xe8 / 255, 0xe4 / 255, 0xda / 255)     # road paint, off-white
AMBER = (0xf2 / 255, 0xb1 / 255, 0x34 / 255)      # road-marking yellow
TEXT = (0xf1 / 255, 0xee / 255, 0xe7 / 255)
MUTED = (0xb8 / 255, 0xb3 / 255, 0xa8 / 255)


def feature(cr, x, y, label, icon):
    """An icon in the accent color and its label; returns the width used."""
    cr.set_source_rgb(*AMBER)
    cr.set_line_width(1.8)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    icon(cr, x, y + 3)
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string("Poppins 19px"))
    layout.set_text(label, -1)
    _ink, logical = layout.get_pixel_extents()
    cr.move_to(x + 30, y + 13 - logical.height / 2)
    cr.set_source_rgb(*TEXT)
    PangoCairo.layout_path(cr, layout)
    cr.fill()
    return 30 + logical.width


def crossing(cr, x0, x1, top, bottom):
    """Zebra-crossing bars, seen from above, worn at the ends."""
    width, gap = 34, 30
    x = x0
    while x + width <= x1:
        cr.rectangle(x, top, width, bottom - top)
        x += width + gap
    cr.set_source_rgba(*STRIPE, 0.07)
    cr.fill()


def main():
    surface = cairo.SVGSurface(OUT, W, H)
    surface.set_document_unit(cairo.SVGUnit.PX)
    cr = cairo.Context(surface)

    rounded_rect(cr, 0, 0, W, H, RADIUS)
    cr.set_source_rgb(*ASPHALT)
    cr.fill_preserve()
    cr.clip()

    # the crossing runs under the logo: from one computer to the other, like its arrow
    crossing(cr, 30, 340, 0, H)

    # logo, as vectors
    cy = H / 2
    logo = Rsvg.Handle.new_from_file(LOGO)
    viewport = Rsvg.Rectangle()
    viewport.x, viewport.y, viewport.width, viewport.height = 55, cy - 130, 260, 260
    logo.render_document(cr, viewport)

    # name, Arabic name and tagline
    x = 372
    width, _h = text(cr, x, 30, "Obour", "Poppins SemiBold 76px", rgba=(*TEXT, 1))
    text(cr, x + width + 26, 50, "عبور", "IBM Plex Sans Arabic Bold 52px",
         rgba=(*AMBER, 1), rtl=True)
    text(cr, x + 4, 140, "Open apps from your other Linux machines as native windows.",
         "Poppins 25px", rgba=(*MUTED, 1))

    # what it does: two quiet rows, no boxes
    y = 204
    for row in FEATURES:
        cx = x + 4
        for label, icon in row:
            cx += feature(cr, cx, y, label, icon) + 34
        y += 46

    surface.finish()
    print(OUT)


if __name__ == "__main__":
    main()
