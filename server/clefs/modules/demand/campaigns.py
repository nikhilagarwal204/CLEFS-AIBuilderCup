"""
Campaign drafts from an event (02 spec 4.3; Canon 10, 19, WhatsApp rule).

Export-only: Demand never sends. A draft holds, per channel (WHATSAPP,
INSTAGRAM, EMAIL):
- copy from a structured LLM call over the event facts (falls back to a
  template; both pass the causal-claim lint of recommend.lint_why);
- the audience from guests.api.marketing_audience (consented direct
  contacts only: OTA alias and OTA-provenance contacts never appear,
  Canon 19), narrowed to the event's source-market countries where the
  guests module supports it (segment "countries"); Instagram is an organic
  post and has no list;
- suggested_send_at clamped into 09:00-18:00 Asia/Dubai (Canon: marketing
  sends only in that window).
The CSV export lists contact refs (hmac hex, never an address), channel,
locale, the copy and, for email, the one-click unsubscribe path.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from clefs import ai
from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.modules.demand import actions as dr_actions
from clefs.modules.demand import markets
from clefs.modules.demand.recommend import lint_why

log = logging.getLogger("clefs.demand.campaigns")

CHANNELS = ("WHATSAPP", "INSTAGRAM", "EMAIL")
DUBAI = ZoneInfo("Asia/Dubai")
SEND_WINDOW = (time(9, 0), time(18, 0))  # [09:00, 18:00) Asia/Dubai
DEFAULT_LEAD_DAYS = 14
DEFAULT_SEND_TIME = time(10, 0)
AUDIENCE_CHANNEL = {"WHATSAPP": "whatsapp", "EMAIL": "email"}


# ─── Send window ───


def clamp_send_window(dt: datetime) -> datetime:
    """The first instant at or after dt inside [09:00, 18:00) Dubai time."""
    local = dt.astimezone(DUBAI)
    start, end = SEND_WINDOW
    if start <= local.time() < end:
        return dt
    day = local.date() if local.time() < start else local.date() + timedelta(days=1)
    return datetime.combine(day, start, tzinfo=DUBAI)


def suggested_send_at(event_start: date, *, now: datetime) -> datetime:
    """Two weeks before the event at 10:00 Dubai time, never in the past,
    always inside the send window."""
    want = datetime.combine(event_start - timedelta(days=DEFAULT_LEAD_DAYS), DEFAULT_SEND_TIME, tzinfo=DUBAI)
    return clamp_send_window(max(want, now))


# ─── Copy ───


class ChannelCopy(BaseModel):
    whatsapp: str = Field(description="WhatsApp marketing message, max 600 characters, no links")
    instagram: str = Field(description="Instagram caption, max 600 characters, up to 5 hashtags")
    email_subject: str = Field(description="Email subject line, max 80 characters")
    email_body: str = Field(description="Plain-text email body, max 1200 characters")


COPY_SYSTEM = (
    "You write short direct-booking marketing copy for a hotel around one event. Use ONLY the facts given. "
    "Invite guests to book direct; do not promise availability or prices, do not claim the event causes "
    "anything, never write 'guaranteed', 'will increase' or 'definitely'. No emojis in email. "
    "Mention that replies go to the hotel team."
)


def template_copy(hotel: Any, ev: dict) -> dict:
    dates = f"{ev['start_date']:%d %b} to {ev['end_date']:%d %b}"
    name = hotel.name
    return {
        "whatsapp": (f"Coming to Dubai for {ev['title']} ({dates})? Book direct with {name} for our best "
                     f"available rate and flexible check-in. Reply to this message and our team will help."),
        "instagram": (f"{ev['title']} is on {dates}. Stay close and book direct with {name} for our best available "
                      f"rate. Link in bio. #Dubai #{''.join(w for w in ev['title'].split()[:2] if w.isalnum())}"),
        "email_subject": f"Your stay for {ev['title']}, {dates}",
        "email_body": (f"Hello,\n\n{ev['title']} runs {dates}. If you are planning to be in town, book direct with "
                       f"{name} for our best available rate and flexible check-in.\n\nReply to this email and our "
                       f"team will help with your dates.\n\n{name}"),
    }


def generate_copy(hotel: Any, ev: dict, *, use_llm: bool = True) -> tuple[dict, str]:
    if use_llm:
        facts = {"hotel": hotel.name, "city": hotel.city, "event": ev["title"], "category": ev["category"],
                 "start": ev["start_date"].isoformat(), "end": ev["end_date"].isoformat()}
        try:
            out = ai.structured("demand.campaign_copy", system=COPY_SYSTEM, input=json.dumps(facts),
                                schema=ChannelCopy, hotel_slug=hotel.slug, max_output_tokens=900).model_dump()
            if not any(lint_why(v) for v in out.values()):
                return {k: v.strip()[:1200] for k, v in out.items()}, "llm"
            log.warning("campaign copy failed the lint")
        except Exception:  # noqa: BLE001  (AI off or failing: the template stands in)
            pass
    return template_copy(hotel, ev), "template"


# ─── Audience ───


def market_countries(cur: Any, hotel_slug: str) -> list[str]:
    """ISO2 countries of the hotel's source markets (owner shares, else the
    DET default mix)."""
    out: list[str] = []
    for m in markets.get_shares(cur, hotel_slug)["markets"]:
        out += [c for c in m["countries"] if c not in out]
    return out


def audience_for(cur: Any, hotel_slug: str, channel: str, countries: list[str]) -> dict:
    if channel not in AUDIENCE_CHANNEL:
        return {"kind": "organic_post", "count": 0, "contacts": [], "countries": countries}
    from clefs.modules.guests import api as guests_api

    rows = dr_actions.safe_call(cur, guests_api.marketing_audience, cur, hotel_slug, purpose="marketing",
                                channel=AUDIENCE_CHANNEL[channel], segment={"countries": countries}, default=[]) or []
    contacts = [{"contact_ref": r.get("contact_ref"), "locale": r.get("locale")} for r in rows if r.get("contact_ref")]
    return {"kind": "consented_contacts", "count": len(contacts), "contacts": contacts, "countries": countries,
            "consent": f"marketing_{AUDIENCE_CHANNEL[channel]}",
            "note": "Direct contacts with marketing consent only; OTA-sourced contacts are never included."}


def still_consented(cur: Any, hotel_slug: str, channel: str, aud: dict) -> tuple[list[dict], int, bool]:
    """(contacts to export, dropped count, recheck ok) for a stored draft
    audience, re-checked against Guests NOW.

    WHY: a draft freezes its audience at creation, but the export can come
    weeks later (the suggested send is 14 days before the event and drafts
    can be made months ahead). Consent is honoured at send time (Canon 10:
    audiences come from Guests), so a guest who unsubscribed, withdrew
    consent or was suppressed since must not be on the list. We re-run the
    same audience query (same channel, same source-market segment) and keep
    the stored contacts that are still in it: the approved list can only
    shrink, never grow past what the owner reviewed. If Guests cannot answer
    (disabled, failing) we fail closed and export no contacts."""
    stored = [c for c in (aud.get("contacts") or []) if c.get("contact_ref")]
    if channel not in AUDIENCE_CHANNEL or not stored:
        return stored, 0, True
    from clefs.modules.guests import api as guests_api

    seg = {"countries": aud["countries"]} if aud.get("countries") else None
    rows = dr_actions.safe_call(cur, guests_api.marketing_audience, cur, hotel_slug, purpose="marketing",
                                channel=AUDIENCE_CHANNEL[channel], segment=seg, default=None)
    if rows is None:
        return [], len(stored), False
    live = {r.get("contact_ref") for r in rows if r.get("contact_ref")}
    keep = [c for c in stored if c["contact_ref"] in live]
    return keep, len(stored) - len(keep), True


# ─── Drafts ───


def serialize(r: dict, *, with_contacts: bool = False) -> dict:
    aud = dict(r["audience"] or {})
    if not with_contacts:
        aud.pop("contacts", None)
    return {"id": r["id"], "event_id": str(r["event_id"]) if r["event_id"] else None, "channel": r["channel"],
            "audience": aud, "copy": r["copy"], "copy_source": r["copy_source"],
            "suggested_send_at": r["suggested_send_at"].astimezone(DUBAI).isoformat() if r["suggested_send_at"] else None,
            "status": r["status"], "action_id": r["action_id"], "created_at": r["created_at"].isoformat(),
            "exported_at": r["exported_at"].isoformat() if r["exported_at"] else None, "export_only": True}


def _event(cur: Any, hotel_slug: str, event_id: str) -> dict:
    from clefs.modules.demand import events

    ev = clefs_db.fetch_one("SELECT * FROM dr_events WHERE id = %s AND (hotel_slug IS NULL OR hotel_slug = %s) "
                            "AND merged_into IS NULL", (event_id, hotel_slug), cur=cur) \
        if events._is_uuid(event_id) else None
    if ev is None:
        raise ClefsError(404, "No such event", "event_not_found")
    return ev


def create_drafts(cur: Any, hotel: Any, *, event_id: str, channels: list[str] | None = None,
                  send_at: datetime | None = None, user_id: int | None = None, use_llm: bool = True) -> list[dict]:
    """One draft per channel for the event, one approvable dr.campaign
    action for the set. A requested send time is clamped into the window."""
    chans = [c.upper() for c in (channels or CHANNELS)]
    bad = [c for c in chans if c not in CHANNELS]
    if bad or not chans:
        raise ClefsError(400, f"channels are {', '.join(CHANNELS)}", "invalid_channel")
    ev = _event(cur, hotel.slug, event_id)
    now = clock.now()
    if send_at is not None and send_at.tzinfo is None:
        raise ClefsError(400, "send_at needs a timezone", "invalid_send_at")
    when = clamp_send_window(max(send_at, now)) if send_at else suggested_send_at(ev["start_date"], now=now)
    copy, src = generate_copy(hotel, ev, use_llm=use_llm)
    countries = market_countries(cur, hotel.slug)
    rows = []
    for ch in dict.fromkeys(chans):
        per = {"WHATSAPP": {"text": copy["whatsapp"]}, "INSTAGRAM": {"caption": copy["instagram"]},
               "EMAIL": {"subject": copy["email_subject"], "body": copy["email_body"]}}[ch]
        rows.append(clefs_db.fetch_one(
            "INSERT INTO dr_campaign_drafts (hotel_slug, event_id, channel, audience, copy, copy_source, "
            "suggested_send_at, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (hotel.slug, str(ev["id"]), ch, Json(audience_for(cur, hotel.slug, ch, countries)), Json(per), src, when,
             user_id), cur=cur))
    act = dr_actions.create(
        cur, hotel.slug, type=dr_actions.CAMPAIGN, title=f"Campaign drafts for {ev['title']} (export only)",
        params={"event_id": str(ev["id"]), "draft_ids": [r["id"] for r in rows],
                "suggested_send_at": when.isoformat()}, owner_role="revenue",
        dedupe_key=f"dr.campaign:{ev['id']}:{rows[0]['id']}")
    if act.get("id"):
        clefs_db.execute("UPDATE dr_campaign_drafts SET action_id = %s WHERE id = ANY(%s)",
                         (act["id"], [r["id"] for r in rows]), cur=cur)
    return [serialize({**r, "action_id": act.get("id")}) for r in rows]


def get_draft(cur: Any, hotel_slug: str, draft_id: int) -> dict:
    r = clefs_db.fetch_one("SELECT * FROM dr_campaign_drafts WHERE id = %s AND hotel_slug = %s",
                           (draft_id, hotel_slug), cur=cur)
    if r is None:
        raise ClefsError(404, "No such campaign draft", "campaign_not_found")
    return r


def list_drafts(cur: Any, hotel_slug: str, *, event_id: str | None = None, limit: int = 200) -> list[dict]:
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_campaign_drafts WHERE hotel_slug = %s AND (%s::uuid IS NULL OR event_id = %s::uuid) "
        "AND status <> 'ARCHIVED' ORDER BY created_at DESC, id DESC LIMIT %s",
        (hotel_slug, event_id, event_id, limit), cur=cur)
    return [serialize(r) for r in rows]


def update_draft(cur: Any, hotel_slug: str, draft_id: int, *, copy: dict | None = None,
                 send_at: datetime | None = None, status: str | None = None) -> dict:
    """Edit the copy or the send time (clamped again), approve or archive.
    An exported draft is final."""
    r = get_draft(cur, hotel_slug, draft_id)
    if r["status"] == "EXPORTED" and (copy or send_at):
        raise ClefsError(409, "An exported draft is final; create a new one", "campaign_exported")
    new_copy = dict(r["copy"] or {})
    if copy:
        allowed = {"WHATSAPP": {"text"}, "INSTAGRAM": {"caption"}, "EMAIL": {"subject", "body"}}[r["channel"]]
        for k, v in copy.items():
            if k not in allowed or not isinstance(v, str):
                raise ClefsError(400, f"{r['channel']} copy fields are {', '.join(sorted(allowed))}", "invalid_copy")
            if lint_why(v):
                raise ClefsError(400, f"Remove causal claims: {', '.join(lint_why(v))}", "copy_lint")
            new_copy[k] = v.strip()[:2000]
    when = r["suggested_send_at"]
    if send_at is not None:
        if send_at.tzinfo is None:
            raise ClefsError(400, "send_at needs a timezone", "invalid_send_at")
        when = clamp_send_window(send_at)
    if status is not None and status not in ("DRAFT", "APPROVED", "ARCHIVED"):
        raise ClefsError(400, "status is DRAFT, APPROVED or ARCHIVED", "invalid_status")
    row = clefs_db.fetch_one(
        "UPDATE dr_campaign_drafts SET copy = %s, suggested_send_at = %s, status = COALESCE(%s, status), "
        "updated_at = %s WHERE id = %s RETURNING *", (Json(new_copy), when, status, clock.now(), draft_id), cur=cur)
    return serialize(row)


def export_csv(cur: Any, hotel_slug: str, draft_id: int) -> tuple[str, str]:
    """(filename, CSV text). One row per contact (or one row for an
    Instagram post). Marks the draft EXPORTED; nothing is sent. Contacts are
    re-checked against current consent first (still_consented); the kept
    and dropped counts are stored on the draft's audience for audit."""
    r = get_draft(cur, hotel_slug, draft_id)
    aud = dict(r["audience"] or {})
    now = clock.now()
    contacts, dropped, ok = still_consented(cur, hotel_slug, r["channel"], aud)
    aud["export_check"] = {"at": now.isoformat(), "exported_count": len(contacts), "dropped_count": dropped,
                           "recheck": "ok" if ok else "guests_unavailable"}
    copy = r["copy"] or {}
    when = r["suggested_send_at"].astimezone(DUBAI).isoformat() if r["suggested_send_at"] else ""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["contact_ref", "channel", "locale", "suggested_send_at_dubai", "subject", "message",
                "unsubscribe_path"])
    msg = copy.get("text") or copy.get("caption") or copy.get("body") or ""
    if not contacts:
        w.writerow(["", r["channel"].lower(), "", when, copy.get("subject", ""), msg, ""])
    for c in contacts:
        unsub = ""
        if r["channel"] == "EMAIL":
            from clefs.modules.guests import api as guests_api

            unsub = dr_actions.safe_call(cur, guests_api.unsubscribe_path, hotel_slug,
                                         identity_hmac=bytes.fromhex(c["contact_ref"]), channel="email",
                                         default="") or ""
        w.writerow([c["contact_ref"], r["channel"].lower(), c.get("locale") or "", when, copy.get("subject", ""),
                    msg, unsub])
    # The stored contacts list stays as approved (a later export re-checks
    # it again); only the latest check's counts are recorded.
    clefs_db.execute("UPDATE dr_campaign_drafts SET status = 'EXPORTED', exported_at = COALESCE(exported_at, %s), "
                     "audience = %s, updated_at = %s WHERE id = %s", (now, Json(aud), now, draft_id), cur=cur)
    return f"campaign-{r['id']}-{r['channel'].lower()}.csv", buf.getvalue()


def _ref_finder(cur: Any, event_id: str) -> list[dict]:
    rows = clefs_db.fetch_all("SELECT id, hotel_slug, status FROM dr_campaign_drafts WHERE event_id = %s "
                              "AND status IN ('DRAFT', 'APPROVED') AND action_id IS NULL", (event_id,), cur=cur)
    return [{"hotel_slug": r["hotel_slug"], "ref_type": "campaign", "ref_id": r["id"],
             "approved": r["status"] == "APPROVED"} for r in rows]


def _register() -> None:
    from clefs.modules.demand import events

    events.register_ref_finder(_ref_finder)


_register()
