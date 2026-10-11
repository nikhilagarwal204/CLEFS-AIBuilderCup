"""
Consent ledger (Canon 4: foundation-owned; the PDPL has no legitimate-interest
basis, so consent is the default and every purpose is separate).

    ref = privacy.contact_ref(slug, "phone", "+971501234567")
    record_consent(cur, slug, ref, "marketing", True, source="registration_card",
                   evidence={"file_id": "..."}, text_version="2026-10-01")
    has_consent(cur, slug, ref, "marketing")            # True
    withdraw(cur, slug, ref, "marketing", source="whatsapp_stop")

Append-only on purpose: the latest row per (hotel, subject, purpose) is the
state, and the full history is the evidence a regulator or a guest can ask
for. A withdrawal is a new row with granted = false, never an UPDATE.

subject_ref is a hashed identity (core.privacy.contact_ref) or an internal id
("user:12"); raw phone numbers and emails never land in this table.

Purposes (separate by law, Canon PDPL note):
  recording        call recording / transcripts
  ai_processing    guest content processed by an AI model
  transfer_abroad  processing outside the guest's country (storage in Google Cloud's
                   India region, Gemini models that may run in other countries)
  marketing        promotional messages on any channel
  ai_analytics     aggregate AI analytics over guest content
  profile_memory   remembering preferences across stays
  auto_reply       AI replying without a person in the loop
  whatsapp_updates utility updates about the stay on WhatsApp
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from typing import Any

from clefs.audit import actor_fields, audit
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.db import Json

CONSENT_PURPOSES: tuple[str, ...] = (
    "recording",
    "ai_processing",
    "transfer_abroad",
    "marketing",
    "ai_analytics",
    "profile_memory",
    "auto_reply",
    "whatsapp_updates",
)
SUBJECT_TYPES = frozenset({"guest", "contact", "lead", "user", "staff"})

_extra_purposes: set[str] = set()
_lock = threading.Lock()


def register_consent_purpose(purpose: str) -> None:
    """A module that needs a new purpose registers it (no migration needed)."""
    if not purpose.replace("_", "").isalnum() or purpose != purpose.lower():
        raise ValueError(f"bad consent purpose {purpose!r}")
    with _lock:
        _extra_purposes.add(purpose)


def purposes() -> tuple[str, ...]:
    with _lock:
        return CONSENT_PURPOSES + tuple(sorted(_extra_purposes - set(CONSENT_PURPOSES)))


def _check_purpose(purpose: str) -> None:
    if purpose not in purposes():
        raise ClefsError(400, f"unknown consent purpose {purpose!r}", "invalid_consent_purpose")


def _ref(subject_ref: str | bytes) -> str:
    if isinstance(subject_ref, (bytes, bytearray, memoryview)):
        return bytes(subject_ref).hex()
    ref = (subject_ref or "").strip()
    if not ref:
        raise ClefsError(400, "subject_ref is required", "invalid_subject")
    return ref


# ─── Consent texts ───


def record_consent_text(
    cur: Any, purpose: str, version: str, body: str, *, language: str = "en", hotel_slug: str | None = None
) -> dict:
    """Store (idempotently) the exact wording a guest was shown. Re-recording
    the same version with different wording is refused: evidence must not
    change after the fact."""
    _check_purpose(purpose)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    cur.execute(
        """
        SELECT id, hotel_slug, purpose, version, language, body, sha256, created_at, retired_at FROM consent_texts
        WHERE COALESCE(hotel_slug, '') = COALESCE(%s, '') AND purpose = %s AND version = %s AND language = %s
        """,
        (hotel_slug, purpose, version, language),
    )
    row = cur.fetchone()
    if row is not None:
        if row["sha256"] != digest:
            raise ClefsError(409, f"consent text {purpose}@{version} already exists with other wording", "consent_text_immutable")
        return dict(row)
    cur.execute(
        """
        INSERT INTO consent_texts (hotel_slug, purpose, version, language, body, sha256)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, hotel_slug, purpose, version, language, body, sha256, created_at, retired_at
        """,
        (hotel_slug, purpose, version, language, body, digest),
    )
    return dict(cur.fetchone())


def current_consent_text(cur: Any, purpose: str, *, language: str = "en", hotel_slug: str | None = None) -> dict | None:
    """The newest un-retired wording: the hotel's own first, else the platform's."""
    cur.execute(
        """
        SELECT id, hotel_slug, purpose, version, language, body, sha256, created_at FROM consent_texts
        WHERE purpose = %s AND language = %s AND retired_at IS NULL AND (hotel_slug = %s OR hotel_slug IS NULL)
        ORDER BY (hotel_slug IS NULL), created_at DESC, id DESC LIMIT 1
        """,
        (purpose, language, hotel_slug),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ─── Ledger ───


def record_consent(
    cur: Any,
    hotel_slug: str,
    subject_ref: str | bytes,
    purpose: str,
    granted: bool,
    *,
    source: str,
    evidence: dict | None = None,
    subject_type: str = "guest",
    text_version: str | None = None,
    language: str = "en",
    expires_at: datetime | None = None,
    actor: Any = None,
) -> dict:
    """Append one consent decision. `text_version` links it to the wording
    shown (consent_texts); the hotel's text wins over the platform default."""
    _check_purpose(purpose)
    if subject_type not in SUBJECT_TYPES:
        raise ClefsError(400, f"subject_type must be one of {sorted(SUBJECT_TYPES)}", "invalid_subject")
    if not source:
        raise ClefsError(400, "source is required", "invalid_source")
    ref = _ref(subject_ref)
    text_id = None
    if text_version:
        cur.execute(
            """
            SELECT id FROM consent_texts
            WHERE purpose = %s AND version = %s AND language = %s AND (hotel_slug = %s OR hotel_slug IS NULL)
            ORDER BY (hotel_slug IS NULL) LIMIT 1
            """,
            (purpose, text_version, language, hotel_slug),
        )
        t = cur.fetchone()
        if t is None:
            raise ClefsError(400, f"no consent text {purpose}@{text_version} ({language})", "unknown_consent_text")
        text_id = t["id"]
    actor_type, actor_id, _ = actor_fields(actor)
    cur.execute(
        """
        INSERT INTO consents (hotel_slug, subject_type, subject_ref, purpose, granted, source, text_id, text_version,
                              evidence, actor_type, actor_id, created_at, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, hotel_slug, subject_type, subject_ref, purpose, granted, source, text_id, text_version,
                  evidence, created_at, expires_at
        """,
        (
            hotel_slug,
            subject_type,
            ref,
            purpose,
            bool(granted),
            source,
            text_id,
            text_version,
            Json(evidence or {}),
            actor_type,
            actor_id,
            clock.now(),
            expires_at,
        ),
    )
    row = dict(cur.fetchone())
    audit(
        cur,
        actor,
        "core.consent.grant" if granted else "core.consent.withdraw",
        "consent",
        row["id"],
        after={"subject_type": subject_type, "purpose": purpose, "granted": bool(granted), "source": source},
        hotel_slug=hotel_slug,
    )
    return row


def withdraw(
    cur: Any, hotel_slug: str, subject_ref: str | bytes, purpose: str, *, source: str, evidence: dict | None = None, actor: Any = None
) -> dict:
    return record_consent(cur, hotel_slug, subject_ref, purpose, False, source=source, evidence=evidence, actor=actor)


def latest(cur: Any, hotel_slug: str, subject_ref: str | bytes, purpose: str) -> dict | None:
    cur.execute(
        """
        SELECT id, granted, source, text_version, created_at, expires_at FROM consents
        WHERE hotel_slug = %s AND subject_ref = %s AND purpose = %s
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (hotel_slug, _ref(subject_ref), purpose),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def has_consent(cur: Any, hotel_slug: str, subject_ref: str | bytes, purpose: str, *, at: datetime | None = None) -> bool:
    """True only if the latest decision is a grant that has not expired.
    No record means no consent (PDPL default)."""
    _check_purpose(purpose)
    row = latest(cur, hotel_slug, subject_ref, purpose)
    if row is None or not row["granted"]:
        return False
    when = at or clock.now()
    return row["expires_at"] is None or row["expires_at"] > when


def consent_state(cur: Any, hotel_slug: str, subject_ref: str | bytes) -> dict[str, dict]:
    """{purpose: {granted, source, text_version, at, expires_at}} for every purpose on record."""
    cur.execute(
        """
        SELECT DISTINCT ON (purpose) purpose, granted, source, text_version, created_at, expires_at FROM consents
        WHERE hotel_slug = %s AND subject_ref = %s
        ORDER BY purpose, created_at DESC, id DESC
        """,
        (hotel_slug, _ref(subject_ref)),
    )
    now = clock.now()
    return {
        r["purpose"]: {
            "granted": bool(r["granted"]) and (r["expires_at"] is None or r["expires_at"] > now),
            "source": r["source"],
            "text_version": r["text_version"],
            "at": r["created_at"],
            "expires_at": r["expires_at"],
        }
        for r in cur.fetchall()
    }
