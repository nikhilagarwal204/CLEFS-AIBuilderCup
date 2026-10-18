"""
Phone numbers are stored in E.164 (+971501234567): WhatsApp, Twilio and the
users.phone_e164 CHECK constraint all expect it.
"""

from __future__ import annotations

import re

from clefs.core.errors import ClefsError

E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone(phone: str | None) -> str | None:
    """'+971 50-123 4567' or '00971501234567' -> '+971501234567'; blank -> None;
    anything else -> 400 invalid_phone. National formats are refused on purpose:
    a UAE and an Indian number can look alike without the country code."""
    if phone is None or not phone.strip():
        return None
    p = re.sub(r"[\s\-().]", "", phone.strip())
    if p.startswith("00"):
        p = "+" + p[2:]
    if not E164_RE.match(p):
        raise ClefsError(400, "phone must be in international format, e.g. +971501234567", "invalid_phone")
    return p
