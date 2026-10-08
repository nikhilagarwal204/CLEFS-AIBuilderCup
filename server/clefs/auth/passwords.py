"""
Password and PIN hashing with hashlib.scrypt (standard library, no bcrypt dep).

Encoding: scrypt$<n>$<r>$<p>$<b64 salt>$<b64 hash>, compared in constant time.

PINs (4-6 digits) use the same hash. A PIN on its own is weak; its real
protection is that it only works on a paired device of that hotel, and 5 wrong
tries lock that staff member on that device for 5 minutes (auth/devices.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
DKLEN = 64
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8
PIN_RE = re.compile(r"^\d{4,6}$")

_dummy_hash: str | None = None


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _derive(secret: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=DKLEN, maxmem=64 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = os.urandom(SALT_BYTES)
    dk = _derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algo, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        got = _derive(password, base64.b64decode(salt_b64), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def needs_rehash(encoded: str) -> bool:
    try:
        _, n, r, p, _, _ = encoded.split("$")
        return (int(n), int(r), int(p)) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)
    except ValueError:
        return True


def burn_time(password: str) -> None:
    """Spend one verification's worth of CPU so an unknown email takes as long
    as a wrong password (no account enumeration by timing)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("clefs-dummy-password")
    verify_password(password, _dummy_hash)


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def validate_pin(pin: str) -> None:
    if not PIN_RE.match(pin or ""):
        raise ValueError("PIN must be 4 to 6 digits")


def hash_pin(pin: str) -> str:
    validate_pin(pin)
    return hash_password(pin)


def verify_pin(pin: str, encoded: str | None) -> bool:
    return bool(PIN_RE.match(pin or "")) and verify_password(pin, encoded)
