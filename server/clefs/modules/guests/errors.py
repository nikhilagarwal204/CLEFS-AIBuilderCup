"""
Guests errors. ConsentNotAllowed is part of the frozen api surface
(FROZEN_API_NAMES); it is defined here so internals can raise it without
importing api.py (which imports them), and api.py re-exports the same class.
"""

from __future__ import annotations

from clefs.core.errors import ClefsError


class ConsentNotAllowed(ClefsError):
    """record_own_contact refused: the capture lacks the consent the rules
    need (transfer_abroad with a text version on every capture, PDPL; no
    guest_direct point or marketing consent from j1_form on an OTA stay)."""

    def __init__(self, detail: str, *, reason: str):
        super().__init__(422, detail, "consent_not_allowed")
        self.reason = reason
