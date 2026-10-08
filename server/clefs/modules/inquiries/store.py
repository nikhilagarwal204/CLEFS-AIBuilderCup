"""
Inquiry rows and messages (04 spec 4.1): the only file that writes
inq_inquiries / inq_messages columns directly, so sealing, hashing and the
API shape stay in one place.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from clefs.core import clock, pii, privacy
from clefs.core.errors import ClefsError, not_found
from clefs.db import Json

# Lowercase and free of look-alikes: the ref rides in email addresses
# (<alias>+inq-<ref>@...), where notify compares refs lowercased.
_REF_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

CONTACT_FIELD_CLASSES = {
    "contact_name": pii.GUEST_NAME,
    "contact_phone": pii.GUEST_CONTACT,
    "contact_email": pii.GUEST_CONTACT,
}
# Thread bodies are the sales request itself, not a stay transcript: every
# role with inquiries.view reads them (04 spec 7.1 masks contacts only).


# ─── Contacts ───


def contact_columns(cur: Any, hotel_slug: str, *, phone: str | None, email: str | None, strict: bool = False) -> dict:
    """Sealed value + hmac per kind. A value that does not normalise is
    dropped (channel metadata is sometimes junk) unless `strict`, where the
    caller wants the 400 (the web form, the console)."""
    out: dict[str, Any] = {}
    for kind, raw in (("phone", phone), ("email", email)):
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            h = privacy.contact_hmac(hotel_slug, kind, raw, cur=cur)
            norm = privacy.normalize_contact(kind, raw, default_country_code=_cc(cur, hotel_slug, kind, raw))
        except ClefsError:
            if strict:
                raise
            continue
        out[f"contact_{kind}_hmac"] = h
        out[f"contact_{kind}_enc"] = privacy.seal(norm)
    return out


def _cc(cur: Any, hotel_slug: str, kind: str, raw: str) -> str | None:
    if kind != "phone" or raw.startswith(("+", "00")):
        return None
    return privacy.hotel_calling_code(hotel_slug, cur=cur)


def hmacs_of(row_or_cols: dict) -> list[bytes]:
    return [bytes(h) for h in (row_or_cols.get("contact_phone_hmac"), row_or_cols.get("contact_email_hmac")) if h]


def unsealed(row: dict) -> dict:
    """{phone, email} in clear, for sending replies (never for the API
    without masking)."""
    return {
        "phone": privacy.unseal(row.get("contact_phone_enc")),
        "email": privacy.unseal(row.get("contact_email_enc")),
    }


# ─── Rows ───


def new_ref(cur: Any, hotel_slug: str) -> str:
    for _ in range(20):
        ref = "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))
        cur.execute("SELECT 1 FROM inq_inquiries WHERE hotel_slug = %s AND ref = %s", (hotel_slug, ref))
        if cur.fetchone() is None:
            return ref
    raise ClefsError(503, "could not allocate an inquiry ref", "ref_exhausted")


_JSON_COLS = ("ad_referral", "utm", "extraction")


def insert_inquiry(cur: Any, hotel_slug: str, fields: dict) -> dict:
    now = clock.now()
    cols = {"hotel_slug": hotel_slug, "ref": new_ref(cur, hotel_slug), "created_at": now,
            "updated_at": now, "stage_changed_at": now, "last_activity_at": now, **fields}
    names = list(cols)
    values = [Json(cols[n]) if n in _JSON_COLS and cols[n] is not None else cols[n] for n in names]
    cur.execute(
        f"INSERT INTO inq_inquiries ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))}) RETURNING *",
        values,
    )
    return dict(cur.fetchone())


def update_inquiry(cur: Any, inquiry_id: int, changes: dict) -> dict:
    if not changes:
        return get_row(cur, inquiry_id)
    changes = {**changes, "updated_at": clock.now()}
    sets = ", ".join(f"{k} = %s" for k in changes)
    values = [Json(v) if k in _JSON_COLS and v is not None else v for k, v in changes.items()]
    cur.execute(f"UPDATE inq_inquiries SET {sets} WHERE id = %s RETURNING *", (*values, inquiry_id))
    return dict(cur.fetchone())


def get_row(cur: Any, inquiry_id: int, *, lock: bool = False) -> dict:
    cur.execute(f"SELECT * FROM inq_inquiries WHERE id = %s{' FOR UPDATE' if lock else ''}", (inquiry_id,))
    row = cur.fetchone()
    if row is None:
        raise not_found("inquiry not found")
    return dict(row)


def get_inquiry(cur: Any, hotel_slug: str, inquiry_id: int, *, lock: bool = False) -> dict:
    cur.execute(
        f"SELECT * FROM inq_inquiries WHERE hotel_slug = %s AND id = %s{' FOR UPDATE' if lock else ''}",
        (hotel_slug, inquiry_id),
    )
    row = cur.fetchone()
    if row is None:
        raise not_found("inquiry not found")
    return dict(row)


def by_ref(cur: Any, hotel_slug: str, ref: str) -> dict | None:
    cur.execute("SELECT * FROM inq_inquiries WHERE hotel_slug = %s AND ref = %s", (hotel_slug, (ref or "").lower()))
    row = cur.fetchone()
    return dict(row) if row else None


def latest_by_contact(cur: Any, hotel_slug: str, hmacs: list[bytes]) -> dict | None:
    """The most recently active inquiry sharing a phone or email hmac,
    whatever its stage: the caller applies the append / reopen rule (4.2)."""
    if not hmacs:
        return None
    cur.execute(
        """
        SELECT * FROM inq_inquiries
        WHERE hotel_slug = %s AND duplicate_of IS NULL
          AND (contact_phone_hmac = ANY(%s) OR contact_email_hmac = ANY(%s))
        ORDER BY last_activity_at DESC, id DESC LIMIT 1 FOR UPDATE
        """,
        (hotel_slug, hmacs, hmacs),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def within(ts: datetime | None, days: int) -> bool:
    return ts is not None and clock.now() - ts <= timedelta(days=days)


# ─── Messages ───


def add_message(
    cur: Any,
    row: dict,
    *,
    direction: str,
    channel: str,
    body: str | None,
    author_type: str,
    provider_message_id: str | None = None,
    attachments: list | None = None,
    author_user_id: int | None = None,
    outbound_message_id: int | None = None,
    template_name: str | None = None,
    action_id: int | None = None,
) -> dict | None:
    """Append to the thread. None when (channel, provider_message_id) was
    already stored: a re-delivered webhook adds nothing."""
    now = clock.now()
    cur.execute(
        """
        INSERT INTO inq_messages (hotel_slug, inquiry_id, direction, channel, provider_message_id, body, attachments,
                                  author_type, author_user_id, outbound_message_id, template_name, action_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, channel, provider_message_id) DO NOTHING
        RETURNING *
        """,
        (row["hotel_slug"], row["id"], direction, channel, provider_message_id, (body or "")[:20000],
         Json(attachments or []), author_type, author_user_id, outbound_message_id, template_name, action_id, now),
    )
    msg = cur.fetchone()
    if msg is None:
        return None
    col = "last_inbound_at" if direction == "in" else "last_outbound_at"
    cur.execute(
        f"UPDATE inq_inquiries SET {col} = %s, last_activity_at = %s, updated_at = %s WHERE id = %s",
        (now, now, now, row["id"]),
    )
    row[col] = row["last_activity_at"] = now
    return dict(msg)


def messages(cur: Any, inquiry_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT id, direction, channel, body, attachments, author_type, author_user_id, template_name, action_id, created_at
        FROM inq_messages WHERE inquiry_id = %s ORDER BY created_at, id
        """,
        (inquiry_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def inbound_text(cur: Any, inquiry_id: int, *, limit: int = 6) -> str:
    """The guest's own words, oldest first (what extraction reads)."""
    cur.execute(
        """
        SELECT body FROM (
          SELECT body, created_at, id FROM inq_messages
          WHERE inquiry_id = %s AND direction = 'in' AND body IS NOT NULL
          ORDER BY created_at DESC, id DESC LIMIT %s) t
        ORDER BY created_at, id
        """,
        (inquiry_id, limit),
    )
    return "\n\n".join(r["body"] for r in cur.fetchall())


# ─── API shape ───

_HIDDEN = ("contact_phone_enc", "contact_phone_hmac", "contact_email_enc", "contact_email_hmac")
_MONEY = ("money_at_risk_minor", "expected_adr_minor", "budget_minor")


def to_api(row: dict, ctx: Any = None, *, with_thread: list[dict] | None = None) -> dict:
    """Row -> JSON, contacts unsealed then masked by the viewer's role (the
    Trust matrix: revenue sees no contact, front desk sees it in full). No
    ctx means system code (api.py callers): contacts stay hidden."""
    from clefs.core import money

    out = {k: v for k, v in row.items() if k not in _HIDDEN}
    for k in ("contact_phone_hmac", "contact_email_hmac"):
        out[f"has_{k.split('_')[1]}"] = row.get(k) is not None
    clear = unsealed(row)
    out["contact_phone"], out["contact_email"] = clear["phone"], clear["email"]
    for k in _MONEY:
        ccy = row.get("budget_currency") if k == "budget_minor" else row.get("currency")
        out[k.replace("_minor", "_value")] = (
            money.money_str(row[k], ccy) if row.get(k) is not None and ccy else None
        )
    if with_thread is not None:
        out["messages"] = with_thread
    if ctx is None:
        for k in CONTACT_FIELD_CLASSES:
            out[k] = None
        return out
    return pii.mask_for_ctx(out, ctx, field_classes=CONTACT_FIELD_CLASSES)
