"""Shared GTK helpers."""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ..i18n import _  # noqa: E402

FALLBACK_ICONS = ("application-x-executable", "application-x-executable-symbolic")


def spinner(size: int = 0, visible: bool = True) -> Gtk.Widget:
    """Adw.Spinner needs libadwaita 1.6; older systems get Gtk.Spinner."""
    widget = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
    widget.set_visible(visible)
    if size:
        widget.set_size_request(size, size)
    return widget


def run_async(fn, *args, on_done=None, on_error=None):
    """Run fn(*args) on a worker thread; deliver the result on the main loop."""
    def deliver(cb, value):
        cb(value)
        return False

    def worker():
        try:
            result = fn(*args)
        except Exception as e:  # noqa: BLE001 — surfaced to the user by on_error
            if on_error:
                GLib.idle_add(deliver, on_error, e)
            return
        if on_done:
            GLib.idle_add(deliver, on_done, result)

    threading.Thread(target=worker, daemon=True).start()


def labelled(widget: Gtk.Widget, label: str | None = None) -> Gtk.Widget:
    """Give an icon-only control an accessible name (defaults to its tooltip)."""
    text = label or (widget.get_tooltip_text() or "").split(" (Ctrl")[0]
    if text:
        widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    return widget


def set_icon(image: Gtk.Image, icon: str) -> None:
    if icon and os.path.isabs(icon) and os.path.isfile(icon):
        image.set_from_file(icon)
        return
    display = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display) if display else None
    for name in ((icon,) if icon else ()) + FALLBACK_ICONS:
        if theme is None or theme.has_icon(name):
            image.set_from_icon_name(name)
            return
    image.set_from_icon_name(FALLBACK_ICONS[-1])


def icon_image(icon: str, size: int = 32) -> Gtk.Image:
    image = Gtk.Image(pixel_size=size)
    set_icon(image, icon)
    return image


def host_menu_button(hosts: list[str], on_select) -> Gtk.MenuButton:
    """A small drop-down listing known hosts (saved launchers + ~/.ssh/config)."""
    button = Gtk.MenuButton(icon_name="pan-down-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=_("Known hosts"), css_classes=["flat"])
    if not hosts:
        button.set_sensitive(False)
        return button
    popover = Gtk.Popover()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    for host in hosts:
        item = Gtk.Button(css_classes=["flat"])
        item.set_child(Gtk.Label(label=host, xalign=0))

        def clicked(_btn, h=host):
            popover.popdown()
            on_select(h)

        item.connect("clicked", clicked)
        box.append(item)
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                propagate_natural_height=True,
                                propagate_natural_width=True,
                                max_content_height=320)
    scroll.set_child(box)
    popover.set_child(scroll)
    button.set_popover(popover)
    return button


class TailscaleRows:
    """"Tailscale address" entry and "Force Tailscale" switch for one host."""

    def __init__(self, host: str = ""):
        self.address = Adw.EntryRow(title=_("Tailscale Address — IP or name (optional)"))
        self.force = Adw.SwitchRow(
            title=_("Force Tailscale"),
            subtitle=_("Always connect through the Tailscale address. When off, it's used "
                       "only if the usual address can't be reached."))
        self.edited = False
        self._loading = False
        self.address.connect("changed", self._on_changed)
        self.force.connect("notify::active", lambda *_args: self._loading or self._mark())
        self.load(host)

    def _mark(self) -> None:
        self.edited = True

    def _on_changed(self, *_args) -> None:
        if not self._loading:
            self.edited = True
        self.force.set_sensitive(bool(self.address.get_text().strip()))

    def load(self, host: str) -> None:
        from ..config import host_options
        opts = host_options(host) if host else {}
        self._loading = True
        self.address.set_text(opts.get("tailscale", ""))
        self.force.set_active(bool(opts.get("force_tailscale")))
        self.force.set_sensitive(bool(opts.get("tailscale")))
        self._loading = False

    def save(self, host: str) -> None:
        from .. import route
        from ..config import set_host_options
        set_host_options(host, self.address.get_text(), self.force.get_active())
        route.forget(host)
