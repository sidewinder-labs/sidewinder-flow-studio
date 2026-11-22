"""Password and token hashing, with the standard library only.

Passwords: scrypt (memory-hard, OWASP-recommended alongside Argon2 and
bcrypt), N=2^15, r=8, p=1 — about 32 MB and ~0.1 s per check. Stored as
`scrypt$N$r$p$salt$hash` so the parameters can be raised later and old
hashes still verify.

Session ids and API tokens are long random strings, so a plain SHA-256 is
enough to store them: there is nothing to brute-force.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P, _LEN = 2 ** 15, 8, 1, 32
_MAXMEM = 64 * 1024 * 1024

MIN_PASSWORD = 8


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_LEN, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(h)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        want = _unb64(h)
        got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(want), maxmem=_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def check_new_password(password: str) -> None:
    """Raise ValueError when a new password is too weak to accept."""
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Use at least {MIN_PASSWORD} characters.")
    if password.strip() != password:
        raise ValueError("The password can't start or end with a space.")


def new_secret(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()
