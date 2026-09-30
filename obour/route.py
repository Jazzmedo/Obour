"""Pick the address to reach a host: its usual one, or its Tailscale address.

The host string stays the same everywhere (saved apps, ~/.ssh/config aliases,
known passwords); only ssh's HostName is overridden, so users, ports and keys
from ~/.ssh/config keep working over Tailscale."""

from __future__ import annotations

import socket
import subprocess
import threading
import time

from .config import host_options
from .i18n import _

CHECK_TIMEOUT_S = 2.5
CACHE_S = 60

_cache: dict[str, tuple[float, str | None]] = {}
_lock = threading.Lock()


def _ssh_target(host: str) -> tuple[str, int] | None:
    """(hostname, port) that ssh would connect to for host, after ~/.ssh/config."""
    try:
        out = subprocess.run(["ssh", "-G", host], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=5).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    values = dict(line.split(" ", 1) for line in out.splitlines() if " " in line)
    try:
        return values["hostname"], int(values.get("port", "22"))
    except (KeyError, ValueError):
        return None


def _reachable(host: str) -> bool:
    target = _ssh_target(host)
    if target is None:
        return True  # can't tell; let ssh try and report
    try:
        with socket.create_connection(target, timeout=CHECK_TIMEOUT_S):
            return True
    except OSError:
        return False


def tailscale_address(host: str) -> str | None:
    """The Tailscale address to use for host right now, or None for its usual address.
    May take a couple of seconds (reachability check), so call it off the UI thread."""
    opts = host_options(host)
    address = opts.get("tailscale", "").strip()
    if not address:
        return None
    if opts.get("force_tailscale"):
        return address
    with _lock:
        cached = _cache.get(host)
        if cached and time.monotonic() - cached[0] < CACHE_S:
            return cached[1]
    chosen = None if _reachable(host) else address
    with _lock:
        _cache[host] = (time.monotonic(), chosen)
    return chosen


def forget(host: str) -> None:
    with _lock:
        _cache.pop(host, None)


def ssh_route_opts(host: str) -> list[str]:
    address = tailscale_address(host)
    return ["-o", f"HostName={address}"] if address else []


def unreachable_hint(host: str) -> str:
    """Extra advice when host couldn't be reached through its Tailscale address."""
    address = tailscale_address(host)
    if not address:
        return ""
    try:
        out = subprocess.run(["tailscale", "status"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=5).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    if "stopped" in out.lower() or "logged out" in out.lower():
        return " " + _("Obour tried the Tailscale address {address}, but Tailscale is "
                       "stopped on this computer. Start it (`tailscale up`) and try "
                       "again.").format(address=address)
    return " " + _("Obour tried the Tailscale address {address}. Check that Tailscale is "
                   "connected on both computers.").format(address=address)


def describe(host: str) -> str:
    address = tailscale_address(host)
    if not address:
        return ""
    if host_options(host).get("force_tailscale"):
        return _("Connecting to {host} through Tailscale ({address}; always).").format(
            host=host, address=address)
    return _("Connecting to {host} through Tailscale ({address}; the usual address is "
             "unreachable).").format(host=host, address=address)
