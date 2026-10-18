"""
Review storage: sources and the one ingest path every source writes through.

Why one path: the uniqueness key (hotel, platform, external_id), the /10 to /5
normalisation, the 30-day raw expiry and the owner-response merge must be the
same whether a review came from SerpApi, GBP, Tripadvisor, a CSV or an email.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.db import Json

# 03 spec 4.1: raw text and the author's name live 30 days (GBP policy; the
# same rule for every platform keeps the privacy story one sentence long).
RAW_TTL = timedelta(days=30)
SOURCE_KINDS = ("serpapi_google", "gbp", "tripadvisor", "csv", "email")
KIND_PLATFORM = {"serpapi_google": "google", "gbp": "google", "tripadvisor": "tripadvisor",
                 "csv": "booking", "email": "email"}
PLATFORMS = ("google", "tripadvisor", "booking", "agoda", "expedia", "email", "other")
MAX_TEXT = 8000


# ─── Normalisation ───


def norm_5(rating: Any, scale: int) -> Decimal | None:
    """/10 scores (Booking.com, Agoda) halve to /5, one decimal, half up."""
    if rating is None or rating == "":
        return None
    r = Decimal(str(rating))
    if scale == 10:
        r = r / 2
    r = max(Decimal("0"), min(Decimal("5"), r))
    return r.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def synthetic_id(*parts: Any) -> str:
    """Stable id for a review whose platform gives none (CSV rows, emails)."""
    raw = "|".join("" if p is None else str(p).strip().lower() for p in parts)
    return "h:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


@dataclass
class ReviewIn:
    platform: str
    external_id: str
    rating: Any = None
    rating_scale: int = 5
    author_display: str | None = None
    title: str | None = None
    text: str | None = None
    language: str | None = None
    posted_at: datetime | None = None
    has_owner_response: bool = False
    owner_response_at: datetime | None = None
    platform_ref: str | None = None  # e.g. the GBP review name, for replies
    stay_date_guess: Any = None


# ─── Sources ───

_SOURCE_COLS = ("id, hotel_slug, kind, platform, label, config, status, auto_reply_consent, auto_reply_consent_by, "
                "auto_reply_consent_at, last_synced_at, last_error, created_at, updated_at")


def list_sources(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(f"SELECT {_SOURCE_COLS} FROM rev_sources WHERE hotel_slug = %s ORDER BY id", (hotel_slug,))
    return [dict(r) for r in cur.fetchall()]


def get_source(cur: Any, hotel_slug: str, source_id: int) -> dict:
    cur.execute(f"SELECT {_SOURCE_COLS} FROM rev_sources WHERE hotel_slug = %s AND id = %s", (hotel_slug, source_id))
    row = cur.fetchone()
    if not row:
        raise ClefsError(404, "Review source not found", "source_not_found")
    return dict(row)


def find_source(cur: Any, hotel_slug: str, kind: str) -> dict | None:
    cur.execute(f"SELECT {_SOURCE_COLS} FROM rev_sources WHERE hotel_slug = %s AND kind = %s ORDER BY id LIMIT 1",
                (hotel_slug, kind))
    row = cur.fetchone()
    return dict(row) if row else None


def create_source(cur: Any, hotel_slug: str, kind: str, *, config: dict | None = None, label: str | None = None,
                  platform: str | None = None, created_by: int | None = None) -> dict:
    if kind not in SOURCE_KINDS:
        raise ClefsError(400, f"Unknown source kind {kind!r}", "invalid_source_kind")
    platform = platform or KIND_PLATFORM[kind]
    if platform not in PLATFORMS:
        raise ClefsError(400, f"Unknown platform {platform!r}", "invalid_platform")
    if kind != "csv" and find_source(cur, hotel_slug, kind):
        raise ClefsError(409, "This source is already connected", "source_exists")
    cur.execute(
        f"""INSERT INTO rev_sources (hotel_slug, kind, platform, label, config, created_by)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_SOURCE_COLS}""",
        (hotel_slug, kind, platform, label, Json(config or {}), created_by),
    )
    return dict(cur.fetchone())


def ensure_source(cur: Any, hotel_slug: str, kind: str, **kw: Any) -> dict:
    return find_source(cur, hotel_slug, kind) or create_source(cur, hotel_slug, kind, **kw)


def update_source(cur: Any, hotel_slug: str, source_id: int, **changes: Any) -> dict:
    allowed = {"label", "config", "status", "last_synced_at", "last_error", "platform",
               "auto_reply_consent", "auto_reply_consent_by", "auto_reply_consent_at"}
    sets, params = [], []
    for k, v in changes.items():
        if k not in allowed:
            raise ValueError(k)
        sets.append(f"{k} = %s")
        params.append(Json(v) if k == "config" else v)
    if not sets:
        return get_source(cur, hotel_slug, source_id)
    cur.execute(
        f"UPDATE rev_sources SET {', '.join(sets)}, updated_at = now() WHERE hotel_slug = %s AND id = %s "
        f"RETURNING {_SOURCE_COLS}",
        (*params, hotel_slug, source_id),
    )
    row = cur.fetchone()
    if not row:
        raise ClefsError(404, "Review source not found", "source_not_found")
    return dict(row)


# ─── Ingest ───


def _clip(s: str | None, n: int) -> str | None:
    s = (s or "").replace("\x00", "").strip()
    return s[:n] or None


def ingest(cur: Any, hotel_slug: str, source_id: int | None, r: ReviewIn) -> tuple[dict, bool]:
    """Insert or refresh one review; returns (row, created).

    A known review only gains facts: an owner response, an edited rating or
    text (which queues it for analysis again). Raw text is never written back
    once the row is past raw_expires_at, so a re-sync cannot undo the 30-day
    purge.

    WHY the guard is expiry and not raw_purged_at alone: Trust's
    trust.retention_purge also redacts rev_reviews (registered in jobs.py) at
    the same 30-day mark, an hour before reviews.raw_purge, and it does not
    stamp raw_purged_at. Keyed on the flag, the daily sync (GBP's first page,
    Tripadvisor's latest) wrote the reviewer's words straight back and queued
    them for the AI again."""
    from clefs.modules.reviews.taxonomy import looks_arabic, room_hint

    if r.rating_scale not in (5, 10):
        raise ClefsError(400, "rating_scale must be 5 or 10", "invalid_rating_scale")
    platform = r.platform if r.platform in PLATFORMS else "other"
    now = clock.now()
    text = _clip(r.text, MAX_TEXT)
    rating = None if r.rating in (None, "") else Decimal(str(r.rating))
    language = (r.language or ("ar" if looks_arabic(text) else ("en" if text else None)))
    cur.execute(
        """
        INSERT INTO rev_reviews (hotel_slug, source_id, platform, external_id, author_display, title, rating,
                                 rating_scale, rating_norm_5, language, posted_at, raw_text, raw_expires_at,
                                 has_owner_response, owner_response_at, room_hint, stay_date_guess, platform_ref,
                                 ingested_at)
        VALUES (%(h)s, %(src)s, %(p)s, %(x)s, %(a)s, %(t)s, %(r)s, %(sc)s, %(n5)s, %(lang)s, %(at)s, %(txt)s,
                %(exp)s, %(resp)s, %(resp_at)s, %(room)s, %(stay)s, %(ref)s, %(now)s)
        ON CONFLICT (hotel_slug, platform, external_id) DO UPDATE SET
            has_owner_response = rev_reviews.has_owner_response OR EXCLUDED.has_owner_response,
            owner_response_at = COALESCE(rev_reviews.owner_response_at, EXCLUDED.owner_response_at),
            platform_ref = COALESCE(EXCLUDED.platform_ref, rev_reviews.platform_ref),
            rating = COALESCE(EXCLUDED.rating, rev_reviews.rating),
            rating_norm_5 = COALESCE(EXCLUDED.rating_norm_5, rev_reviews.rating_norm_5),
            raw_text = CASE WHEN rev_reviews.raw_purged_at IS NULL AND rev_reviews.raw_expires_at > %(now)s
                                 AND EXCLUDED.raw_text IS NOT NULL
                            THEN EXCLUDED.raw_text ELSE rev_reviews.raw_text END,
            analyzed_at = CASE WHEN rev_reviews.raw_purged_at IS NULL AND rev_reviews.raw_expires_at > %(now)s
                                    AND EXCLUDED.raw_text IS NOT NULL
                                    AND EXCLUDED.raw_text IS DISTINCT FROM rev_reviews.raw_text
                               THEN NULL ELSE rev_reviews.analyzed_at END
        RETURNING *, (xmax = 0) AS created
        """,
        {"h": hotel_slug, "src": source_id, "p": platform, "x": _clip(r.external_id, 300) or synthetic_id(text),
         "a": _clip(r.author_display, 120), "t": _clip(r.title, 300), "r": rating, "sc": r.rating_scale,
         "n5": norm_5(rating, r.rating_scale), "lang": _clip(language, 12), "at": r.posted_at, "txt": text,
         "exp": now + RAW_TTL, "resp": bool(r.has_owner_response), "resp_at": r.owner_response_at,
         "room": room_hint(f"{r.title or ''} {text or ''}"), "stay": r.stay_date_guess,
         "ref": _clip(r.platform_ref, 300), "now": now},
    )
    row = dict(cur.fetchone())
    created = bool(row.pop("created"))
    return row, created


def known_external_ids(cur: Any, hotel_slug: str, platform: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    cur.execute("SELECT external_id FROM rev_reviews WHERE hotel_slug = %s AND platform = %s AND external_id = ANY(%s)",
                (hotel_slug, platform, ids))
    return {r["external_id"] for r in cur.fetchall()}


def get_review(cur: Any, hotel_slug: str, review_id: int) -> dict | None:
    cur.execute("SELECT * FROM rev_reviews WHERE hotel_slug = %s AND id = %s", (hotel_slug, review_id))
    row = cur.fetchone()
    return dict(row) if row else None


def purge_raw(cur: Any, *, now: datetime | None = None, hotel_slug: str | None = None) -> int:
    """Null raw_text, author_display and title past raw_expires_at
    (reviews.raw_purge). rev_derived, the rating and the dates stay.

    Every expired row ends up with raw_purged_at set, including one whose
    columns Trust's retention job already nulled (it runs first and never
    stamps the flag): the flag is what the console reports as purged."""
    now = now or clock.now()
    cur.execute(
        """UPDATE rev_reviews SET raw_text = NULL, author_display = NULL, title = NULL,
                  raw_purged_at = COALESCE(raw_purged_at, %s)
           WHERE raw_expires_at <= %s
             AND (raw_purged_at IS NULL OR raw_text IS NOT NULL OR author_display IS NOT NULL OR title IS NOT NULL)
             AND (%s::text IS NULL OR hotel_slug = %s)""",
        (now, now, hotel_slug, hotel_slug),
    )
    return cur.rowcount
