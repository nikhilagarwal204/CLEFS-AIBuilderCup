"""
The one intake pipeline every adapter feeds (04 spec 4.2-4.4).

An adapter turns its channel's payload into an `Inbound` and calls
`ingest`. Ingest then applies one rule set, whatever the channel:

1. A message already stored (same channel + provider id) is a duplicate.
2. A lead id already stored for the source is a duplicate (Google retries,
   Meta re-deliveries).
3. Same phone or email hmac as an open inquiry active in the last 30 d:
   append (and fill any field the inquiry does not have yet).
4. Same contact on a lost / future inquiry active in the last 30 d: reopen
   it to `new`. spam, turned_down, rerouted and confirmed_verified never
   reopen (4.2).
5. Otherwise a new inquiry.

A new or reopened inquiry gets money at risk, the SLA timer and (channels
that are not staff-logged) the fixed acknowledgement. Every inbound queues
extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from clefs import events, jobs
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import Hotel
from clefs.modules.inquiries import risk, service, sla, store
from clefs.modules.inquiries.models import (
    ACK_SOURCES,
    APPEND_DAYS,
    CONSENT_BASIS_BY_SOURCE,
    OPEN_STAGES,
    SOURCES,
)

EXTRACT_TASK = "inquiries.extract"
# Who sees inquiry events on the console stream (inquiries.view roles).
EVENT_AUDIENCE = {"roles": ["owner", "gm", "revenue", "front_desk"]}


@dataclass
class Inbound:
    source: str
    text: str | None
    channel: str | None = None  # inq_messages.channel; defaults to the source
    provider_message_id: str | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    company: str | None = None
    language: str | None = None
    strict_contact: bool = False  # 400 on an invalid phone/email (forms) instead of dropping it
    # Structured values the channel itself carries (form fields, lead answers).
    check_in: date | None = None
    check_out: date | None = None
    rooms: int | None = None
    adults: int | None = None
    children: int | None = None
    # Channel metadata stored on a new inquiry.
    channel_ref: str | None = None
    external_lead_id: str | None = None
    ad_referral: dict | None = None
    ctwa_clid: str | None = None
    gclid: str | None = None
    utm: dict | None = None
    page_url: str | None = None
    is_test: bool = False
    consent_basis: str | None = None
    consent_ids: list[int] = field(default_factory=list)
    wa_window_expires_at: datetime | None = None
    attachments: list = field(default_factory=list)
    author_type: str = "guest"
    author_user_id: int | None = None


@dataclass
class IntakeResult:
    inquiry: dict | None
    outcome: str  # created | appended | reopened | duplicate
    message: dict | None = None

    @property
    def created(self) -> bool:
        return self.outcome == "created"


# ─── Structured fields ───


def _structured(inb: Inbound) -> dict:
    cols: dict[str, Any] = {}
    for k in ("check_in", "check_out", "rooms", "adults", "children", "company", "language"):
        v = getattr(inb, k)
        if v not in (None, ""):
            cols[k] = v
    if inb.check_in and inb.check_out:
        if inb.check_out <= inb.check_in:
            raise ClefsError(400, "check_out must be after check_in", "invalid_dates")
        cols["nights"] = (inb.check_out - inb.check_in).days
    if inb.name:
        cols["contact_name"] = inb.name.strip()[:200]
    if "language" not in cols and inb.text:
        # Script detection is enough to pick the ack's language (EN/AR)
        # before extraction has run; extraction may refine it later.
        from clefs.modules.inquiries.extraction import detect_language

        lang = detect_language(inb.text)
        if lang in ("en", "ar"):
            cols["language"] = lang
    return cols


def _fill(row: dict, cols: dict) -> dict:
    """Only what the inquiry does not say yet (4.4: "new fields are filled in")."""
    return {k: v for k, v in cols.items() if row.get(k) is None and v is not None}


# ─── Dedupe lookups ───


def _message_seen(cur: Any, hotel_slug: str, channel: str, provider_message_id: str | None) -> dict | None:
    if not provider_message_id:
        return None
    cur.execute(
        """
        SELECT i.* FROM inq_messages m JOIN inq_inquiries i ON i.id = m.inquiry_id
        WHERE m.hotel_slug = %s AND m.channel = %s AND m.provider_message_id = %s
        """,
        (hotel_slug, channel, provider_message_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _lead_seen(cur: Any, hotel_slug: str, source: str, external_lead_id: str | None) -> dict | None:
    if not external_lead_id:
        return None
    cur.execute(
        "SELECT * FROM inq_inquiries WHERE hotel_slug = %s AND source = %s AND external_lead_id = %s",
        (hotel_slug, source, external_lead_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ─── Ingest ───


def ingest(cur: Any, hotel: Hotel, inb: Inbound, *, actor: Any = None, thread: dict | None = None) -> IntakeResult:
    """Run the rule set above. `thread` is an inquiry the channel itself
    names (an email to <alias>+inq-<ref>@, a reply to one of our messages):
    it wins over the contact lookup, under the same append / reopen rule."""
    if inb.source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    channel = inb.channel or inb.source
    seen = _message_seen(cur, hotel.slug, channel, inb.provider_message_id) or _lead_seen(
        cur, hotel.slug, inb.source, inb.external_lead_id
    )
    if seen is not None:
        return IntakeResult(seen, "duplicate")
    contact = store.contact_columns(cur, hotel.slug, phone=inb.phone, email=inb.email, strict=inb.strict_contact)
    cols = _structured(inb)
    existing = thread if thread is not None else store.latest_by_contact(cur, hotel.slug, store.hmacs_of(contact))
    if existing is not None and existing["stage"] in OPEN_STAGES and store.within(existing["last_activity_at"], APPEND_DAYS):
        return _append(cur, hotel, existing, inb, channel, {**contact, **cols})
    if existing is not None and service.reopenable(existing):
        service.transition(cur, existing, "new", actor=actor, reason="reopen:new_inbound", hotel=hotel)
        res = _append(cur, hotel, existing, inb, channel, {**contact, **cols}, outcome="reopened")
        _start_clock(cur, hotel, res.inquiry, created_now=True)
        return res
    return _create(cur, hotel, inb, channel, contact, cols, actor=actor)


def _append(cur: Any, hotel: Hotel, row: dict, inb: Inbound, channel: str, cols: dict,
            *, outcome: str = "appended") -> IntakeResult:
    changes = _fill(row, cols)
    if inb.wa_window_expires_at:
        changes["wa_window_expires_at"] = inb.wa_window_expires_at
    for k in ("ad_referral", "ctwa_clid", "gclid"):
        if row.get(k) is None and getattr(inb, k):
            changes[k] = getattr(inb, k)
    if inb.consent_ids:
        changes["consent_ids"] = sorted(set(row.get("consent_ids") or []) | set(inb.consent_ids))
    if inb.consent_basis == "express_web_form" and row["consent_basis"] != "express_web_form":
        # The form collected express consent (and transfer_abroad): it
        # upgrades a thread that started on WhatsApp or email (4.3, 4.4).
        changes["consent_basis"] = "express_web_form"
    if row["stage"] == "quoted" and inb.author_type == "guest" and not row["guest_engaged"]:
        changes["guest_engaged"] = True  # quoted + guest reply: p 0.30 -> 0.50 (4.5)
    if changes:
        row.update(store.update_inquiry(cur, row["id"], changes))
    msg = _message(cur, row, inb, channel)
    if msg is None:
        return IntakeResult(row, "duplicate")
    if outcome == "appended":
        row.update(store.update_inquiry(cur, row["id"], risk.compute(cur, hotel, row)))
    _after(cur, hotel, row, msg, outcome)
    return IntakeResult(row, outcome, msg)


def _create(cur: Any, hotel: Hotel, inb: Inbound, channel: str, contact: dict, cols: dict, *, actor: Any) -> IntakeResult:
    basis = inb.consent_basis or CONSENT_BASIS_BY_SOURCE[inb.source]
    fields = {
        **contact, **cols,
        "source": inb.source, "channel_ref": inb.channel_ref, "external_lead_id": inb.external_lead_id,
        "ad_referral": inb.ad_referral, "ctwa_clid": inb.ctwa_clid, "gclid": inb.gclid, "utm": inb.utm,
        "page_url": (inb.page_url or "")[:2000] or None, "is_test": inb.is_test, "consent_basis": basis,
        "consent_ids": list(inb.consent_ids), "currency": hotel.currency,
        "wa_window_expires_at": inb.wa_window_expires_at, "stage": "new",
    }
    row = store.insert_inquiry(cur, hotel.slug, {k: v for k, v in fields.items() if v is not None})
    cur.execute(
        "INSERT INTO inq_stage_events (hotel_slug, inquiry_id, from_stage, to_stage, at, actor_type, reason) "
        "VALUES (%s, %s, NULL, 'new', %s, %s, %s)",
        (hotel.slug, row["id"], clock.now(), "user" if actor is not None else "system", f"intake:{inb.source}"),
    )
    msg = _message(cur, row, inb, channel)
    _start_clock(cur, hotel, row, created_now=True)
    if inb.source in ACK_SOURCES and not inb.is_test:
        _ack(cur, hotel, row)
    _after(cur, hotel, row, msg, "created")
    return IntakeResult(row, "created", msg)


# ─── Steps shared by create / append / reopen ───


def _message(cur: Any, row: dict, inb: Inbound, channel: str) -> dict | None:
    return store.add_message(
        cur, row, direction="in", channel=channel, body=inb.text, author_type=inb.author_type,
        provider_message_id=inb.provider_message_id, attachments=inb.attachments, author_user_id=inb.author_user_id,
    )


def _start_clock(cur: Any, hotel: Hotel, row: dict, *, created_now: bool) -> None:
    """Money at risk and the first-response timer. A phone-desk inquiry was
    answered live by the person who logged it: the call is its first
    response (new -> replied) and no timer runs."""
    row.update(store.update_inquiry(cur, row["id"], risk.compute(cur, hotel, row)))
    if row["source"] == "phone_desk":
        service.on_outbound(cur, row, by="phone_desk", at=row.get("stage_changed_at") or row["created_at"], hotel=hotel)
        return
    sla.arm(cur, hotel, row)


def _ack(cur: Any, hotel: Hotel, row: dict) -> None:
    from clefs.core.actions import SYSTEM, create_action
    from clefs.modules.inquiries.ack import ACK_TYPE

    settings = sla.settings_for(cur, hotel.slug)
    if not settings.ack_enabled:
        return
    create_action(
        cur, hotel.slug, type=ACK_TYPE, title=f"Acknowledge inquiry {row['ref'].upper()}",
        params={"inquiry_id": row["id"]}, owner_role="front_desk", dedupe_key=f"inq.ack:{row['id']}",
        actor=SYSTEM, via="system",
    )


def _after(cur: Any, hotel: Hotel, row: dict, msg: dict | None, outcome: str) -> None:
    if msg is not None and msg.get("body"):
        jobs.enqueue(
            EXTRACT_TASK, {"inquiry_id": row["id"], "message_id": msg["id"]}, hotel_slug=hotel.slug,
            dedupe_key=f"inq.extract:{row['id']}:{msg['id']}", cur=cur,
        )
    events.publish(
        hotel.slug, "inquiries.changed",
        {"id": row["id"], "ref": row["ref"], "stage": row["stage"], "outcome": outcome, "source": row["source"]},
        audience=EVENT_AUDIENCE, cur=cur,
    )
