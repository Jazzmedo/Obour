"""SSH authentication: key login by default, an in-memory password as fallback.

Passwords are never written to disk. When one is known for a host, ssh gets it
through SSH_ASKPASS (askpass.sh), which reads it from the environment of that
ssh process only."""

from __future__ import annotations

import getpass
import os
import socket
import subprocess

from .hostenv import host_env

ASKPASS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "askpass.sh")
SSH_DIR = os.path.expanduser("~/.ssh")
KEY_NAMES = ("id_ed25519", "id_ecdsa", "id_rsa")

_passwords: dict[str, str] = {}   # used for SSH logins
_typed: dict[str, str] = {}       # last password typed per host, to prefill sudo prompts


def remember_password(host: str, password: str) -> None:
    _passwords[host] = password
    _typed[host] = password


def forget_password(host: str) -> None:
    _passwords.pop(host, None)


def has_password(host: str) -> bool:
    return host in _passwords


def password_for(host: str) -> str | None:
    """The password typed for host during this Obour session, if any (memory only)."""
    return _typed.get(host)


def ssh_opts(host: str, password: str | None = None, key_only: bool = False) -> list[str]:
    """key_only: never fall back to a password, even one remembered for host."""
    use_password = not key_only and (password is not None or host in _passwords)
    opts = [
        "-o", f"BatchMode={'no' if use_password else 'yes'}",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "StreamLocalBindUnlink=yes",
        "-o", "StreamLocalBindMask=0177",
    ]
    if use_password:
        opts += ["-o", "NumberOfPasswordPrompts=1"]
    from .route import ssh_route_opts
    return opts + ssh_route_opts(host)


def ssh_env(host: str, password: str | None = None) -> dict[str, str] | None:
    """Environment for an ssh process to host (None means inherit unchanged)."""
    password = password if password is not None else _passwords.get(host)
    base = host_env()
    if password is None:
        return base
    env = dict(base if base is not None else os.environ)
    env.update(SSH_ASKPASS=ASKPASS, SSH_ASKPASS_REQUIRE="force", OBOUR_SSH_PASSWORD=password)
    return env


def local_public_key() -> str | None:
    for name in KEY_NAMES:
        private = os.path.join(SSH_DIR, name)
        if os.path.isfile(private) and os.path.isfile(private + ".pub"):
            return private + ".pub"
    return None


def ensure_key() -> tuple[str, bool]:
    """Return (public key path, created now). Creates ~/.ssh/id_ed25519 if no key exists."""
    existing = local_public_key()
    if existing:
        return existing, False
    os.makedirs(SSH_DIR, mode=0o700, exist_ok=True)
    private = os.path.join(SSH_DIR, "id_ed25519")
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", private,
         "-C", f"{getpass.getuser()}@{socket.gethostname()}"],
        check=True, stdin=subprocess.DEVNULL, capture_output=True, env=host_env())
    return private + ".pub", True
