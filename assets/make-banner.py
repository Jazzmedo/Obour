#!/usr/bin/python3
"""Draw assets/banner.svg for the README: the logo, the name and what Obour does
on a gradient.

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


def chip(cr, x, y, label, icon):
    """A rounded pill with an icon and a label; returns its width."""
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string("Poppins Medium 18px"))
    layout.set_text(label, -1)
    _ink, logical = layout.get_pixel_extents()
    h = 44
    w = 16 + 20 + 10 + logical.width + 18
    rounded_rect(cr, x, y, w, h, h / 2)
    cr.set_source_rgba(1, 1, 1, 0.14)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.22)
    cr.set_line_width(1)
    cr.stroke()
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.set_line_width(1.8)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    icon(cr, x + 16, y + (h - 20) / 2)
    cr.move_to(x + 46, y + (h - logical.height) / 2)
    PangoCairo.layout_path(cr, layout)
    cr.fill()
    return w


def main():
    surface = cairo.SVGSurface(OUT, W, H)
    surface.set_document_unit(cairo.SVGUnit.PX)
    cr = cairo.Context(surface)

    # background: deep indigo -> violet -> pink
    grad = cairo.LinearGradient(0, 0, W, H)
    grad.add_color_stop_rgb(0.0, 0x1e / 255, 0x1b / 255, 0x4b / 255)
    grad.add_color_stop_rgb(0.45, 0x5b / 255, 0x21 / 255, 0xb6 / 255)
    grad.add_color_stop_rgb(1.0, 0xdb / 255, 0x27 / 255, 0x77 / 255)
    rounded_rect(cr, 0, 0, W, H, RADIUS)
    cr.set_source(grad)
    cr.fill()

    # soft glow behind the logo
    cy = H / 2
    glow = cairo.RadialGradient(190, cy, 10, 190, cy, 200)
    glow.add_color_stop_rgba(0, 1, 1, 1, 0.15)
    glow.add_color_stop_rgba(1, 1, 1, 1, 0)
    cr.set_source(glow)
    cr.arc(190, cy, 200, 0, 6.2832)
    cr.fill()

    # logo, as vectors
    logo = Rsvg.Handle.new_from_file(LOGO)
    viewport = Rsvg.Rectangle()
    viewport.x, viewport.y, viewport.width, viewport.height = 55, cy - 135, 270, 270
    logo.render_document(cr, viewport)

    # name, Arabic name and tagline
    x = 350
    width, _h = text(cr, x, 34, "Obour", "Poppins SemiBold 76px")
    text(cr, x + width + 28, 54, "عبور", "IBM Plex Sans Arabic Bold 52px",
         rgba=(1, 1, 1, 0.8), rtl=True)
    text(cr, x + 4, 146, "Open apps from your other Linux machines as native windows.",
         "Poppins 26px", rgba=(1, 1, 1, 0.92))

    # what it does
    y = 206
    for row in FEATURES:
        cx = x + 4
        for label, icon in row:
            cx += chip(cr, cx, y, label, icon) + 12
        y += 56

    surface.finish()
    print(OUT)


if __name__ == "__main__":
    main()
