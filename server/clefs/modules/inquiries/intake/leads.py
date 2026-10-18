"""
Ad lead forms (04 spec 4.3): Meta leadgen and Google Ads lead forms.

Both deliver a list of question/answer pairs. `lead_inbound` maps the
standard contact questions to the contact, recognisable stay questions
(arrival, departure, rooms, guests) to the structured fields, and keeps
every other answer in the message text so extraction and a person read it.

A lead is replied to by email, or on WhatsApp only when the form asked a
WhatsApp opt-in question (`whatsapp_optin` in ad_referral).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from clefs import jobs
from clefs.core import clock
from clefs.modules.inquiries import channels
from clefs.modules.inquiries.intake.core import Inbound, ingest

META_FETCH_TASK = "inquiries.meta_lead_fetch"
# Meta keeps leads for 90 days; after 72 h of failures a person is told
# while there is still time to download them by hand.
META_GIVE_UP = timedelta(hours=72)
_YES = {"yes", "y", "true", "1", "نعم", "ok", "sure"}

_NAME_KEYS = {"full_name", "fullname", "name"}
_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "email": ("email", "work_email", "e-mail"),
    "phone": ("phone_number", "phone", "mobile", "work_phone_number", "whatsapp_number"),
    "first_name": ("first_name",),
    "last_name": ("last_name",),
    "company": ("company_name", "company"),
    "check_in": ("check_in", "check-in", "arrival", "arrival_date", "check_in_date"),
    "check_out": ("check_out", "check-out", "departure", "departure_date", "check_out_date"),
    "rooms": ("rooms", "number_of_rooms", "no_of_rooms"),
    "adults": ("adults", "guests", "number_of_guests", "pax"),
}


def _norm(k: str) -> str:
    return re.sub(r"[\s?]+", "_", (k or "").strip().lower()).strip("_")


def _date(v: str) -> date | None:
    v = (v or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _int(v: str) -> int | None:
    m = re.search(r"\d{1,4}", v or "")
    return int(m.group(0)) if m else None


def lead_inbound(answers: list[tuple[str, str]], *, source: str, lead_id: str, meta: dict, is_test: bool = False,
                 gclid: str | None = None, language: str | None = None) -> Inbound:
    got: dict[str, str] = {}
    other: list[str] = []
    whatsapp_optin = False
    for key, value in answers:
        k, value = _norm(key), (value or "").strip()
        if not value:
            continue
        if "whatsapp" in k and value.lower() in _YES:
            whatsapp_optin = True
            continue
        if k in _NAME_KEYS:
            got["name"] = value
            continue
        field = next((f for f, names in _KEY_ALIASES.items() if k in names), None)
        if field and field not in got:
            got[field] = value
        else:
            other.append(f"{key}: {value}")
    name = got.get("name") or " ".join(p for p in (got.get("first_name"), got.get("last_name")) if p) or None
    ci, co = _date(got.get("check_in", "")), _date(got.get("check_out", ""))
    if ci and co and co <= ci:
        co = None
    text = "\n".join(other) or f"[{source.replace('_', ' ')} without questions]"
    return Inbound(
        source=source, text=text, channel=source, provider_message_id=f"lead:{lead_id}", name=name,
        phone=got.get("phone"), email=got.get("email"), company=got.get("company"), language=language,
        check_in=ci, check_out=co, rooms=_int(got.get("rooms", "")), adults=_int(got.get("adults", "")),
        external_lead_id=str(lead_id), ad_referral={**meta, "whatsapp_optin": whatsapp_optin},
        gclid=gclid, is_test=is_test, channel_ref=meta.get("form_id"),
    )


# ─── Meta: fetch the lead the webhook announced ───


def enqueue_meta_fetch(cur: Any, hotel_slug: str, change: dict) -> int | None:
    leadgen_id = str(change.get("leadgen_id") or "")
    if not leadgen_id:
        return None
    return jobs.enqueue(
        META_FETCH_TASK,
        {"leadgen_id": leadgen_id, "page_id": str(change.get("page_id") or ""), "form_id": change.get("form_id"),
         "ad_id": change.get("ad_id"), "first_seen": clock.now().isoformat()},
        hotel_slug=hotel_slug, dedupe_key=f"inq.meta_lead:{leadgen_id}", cur=cur,
    )


def fetch_meta_lead(cur: Any, hotel: Any, payload: dict) -> dict:
    """The task body: GET the lead with the page's sealed token, then ingest.
    Transient failures retry with a growing delay (jobs.Retry does not spend
    an attempt); after 72 h it raises inq.lead_fetch_failed and stops."""
    leadgen_id = payload["leadgen_id"]
    cur.execute(
        "SELECT id FROM inq_inquiries WHERE hotel_slug = %s AND source = 'meta_leadgen' AND external_lead_id = %s",
        (hotel.slug, leadgen_id),
    )
    if cur.fetchone():
        return {"skipped": "duplicate"}
    acct = channels.meta_account_for_page(cur, payload.get("page_id") or "")
    token = channels.secret_of(acct) if acct and acct["hotel_slug"] == hotel.slug else None
    if not token:
        return _fetch_failed(cur, hotel, payload, "page_not_connected", retry=False)
    try:
        with channels.graph_client() as c:
            r = c.get(f"/{channels.graph_version()}/{leadgen_id}", params={
                "fields": "field_data,created_time,ad_id,form_id,campaign_id", "access_token": token,
            })
        if r.status_code >= 400:
            return _fetch_failed(cur, hotel, payload, f"http_{r.status_code}", retry=r.status_code >= 429)
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        return _fetch_failed(cur, hotel, payload, type(exc).__name__, retry=True)
    answers = [(f.get("name") or "", ", ".join(str(v) for v in f.get("values") or []))
               for f in data.get("field_data") or []]
    meta = {"platform": "meta", "page_id": payload.get("page_id"), "form_id": data.get("form_id") or payload.get("form_id"),
            "ad_id": data.get("ad_id") or payload.get("ad_id"), "campaign_id": data.get("campaign_id"),
            "created_time": data.get("created_time")}
    res = ingest(cur, hotel, lead_inbound(answers, source="meta_leadgen", lead_id=leadgen_id, meta=meta))
    return {"outcome": res.outcome, "inquiry_id": res.inquiry["id"] if res.inquiry else None}


def _fetch_failed(cur: Any, hotel: Any, payload: dict, reason: str, *, retry: bool) -> dict:
    now = clock.now()
    try:
        first = datetime.fromisoformat(payload.get("first_seen") or now.isoformat())
    except ValueError:
        first = now
    elapsed = now - first
    if retry and elapsed < META_GIVE_UP:
        # 1 min, then half the time already waited, capped at 6 h: a
        # handful of tries in the first hour, a few a day after that.
        delay = min(max(elapsed / 2, timedelta(minutes=1)), timedelta(hours=6))
        raise jobs.Retry(now + delay, f"meta lead fetch: {reason}")
    from clefs.core.signals import Evidence, SignalIn, upsert_signal

    upsert_signal(cur, hotel.slug, SignalIn(
        module="inquiries", kind="inq.lead_fetch_failed", dedupe_key=f"inq.lead_fetch_failed:{payload['leadgen_id']}",
        title="A Facebook/Instagram ad lead could not be downloaded",
        summary="Meta keeps leads for 90 days: reconnect the page in Inquiries settings, or download the lead "
                "from Meta Business Suite.",
        confidence=1.0, owner_role="gm",
        evidence=[Evidence(kind="row", label=f"leadgen {payload['leadgen_id']} ({reason})",
                           value={"page_id": payload.get("page_id"), "form_id": payload.get("form_id")})],
        subject_type="meta_lead", subject_id=str(payload["leadgen_id"]),
    ))
    return {"failed": reason}
