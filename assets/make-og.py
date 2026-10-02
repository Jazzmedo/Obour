#!/usr/bin/python3
"""Draw docs/img/og.png, the picture shown when the website's link is shared
(WhatsApp, Discord, X, Facebook...). 1200 x 630, centered so a square crop keeps
the logo and the name. Uses the banner's drawing helpers and colors.
Run: assets/make-og.py"""

import importlib.util
import os

import cairo

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("banner", os.path.join(HERE, "make-banner.py"))
banner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(banner)  # also picks the Pango/Rsvg versions
from gi.repository import Pango, PangoCairo, Rsvg  # noqa: E402

W, H = 1200, 630
OUT = os.path.join(HERE, os.pardir, "docs", "img", "og.png")


def centered(cr, y, markup, font, rgba):
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string(font))
    layout.set_markup(markup, -1)
    _ink, logical = layout.get_pixel_extents()
    banner.text(cr, (W - logical.width) / 2, y, markup, font, rgba)


surface = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
cr = cairo.Context(surface)
cr.set_source_rgb(*banner.ASPHALT)
cr.paint()

logo = Rsvg.Handle.new_from_file(banner.LOGO)
box = Rsvg.Rectangle()
box.x, box.y, box.width, box.height = (W - 250) / 2, 60, 250, 250
logo.render_document(cr, box)

centered(cr, 330, "Obour", "Poppins SemiBold 92px", (*banner.TEXT, 1))
centered(cr, 470, "Open apps from your other Linux machines as native windows.",
         "Poppins 32px", (*banner.MUTED, 1))
centered(cr, 530, "Sound, files and your theme cross over too.",
         "Poppins 26px", (*banner.AMBER, 1))

surface.write_to_png(OUT)
print(os.path.normpath(OUT))
