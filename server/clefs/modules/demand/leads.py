"""
Public demand-preview lead (03 spec 6: "/demand-preview is a lead form";
Canon 13 `/public/`, 23: it is a request for a person, not a third
automated lead magnet).

POST /public/leads/demand-preview takes the marketing form's fields, checks
both processing consents and a contact consent on a channel that was filled
in, rate-limits by IP (3 a day) and in total (50 a day), seals the contacts
(core.privacy.seal), tells the founder by email (FOUNDER_NOTIFY_EMAIL,
read through env(); no contact details in the email) and answers 202 with a
small 30-day preview built from the curated event calendar near the area
given, when the area can be placed. The founder prepares the real view by
hand. A filled honeypot (`website`) gets the same 202 and stores nothing.
Rows expire after 90 days (dr.leads_purge).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import re
import uuid
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.modules.demand import dedup, relevance as rel
from clefs.settings import env, get_settings

DUBAI = ZoneInfo("Asia/Dubai")
IP_DAILY_LIMIT = 3
GLOBAL_DAILY_LIMIT = 50
RETENTION = timedelta(days=90)
PREVIEW_DAYS = 30
PREVIEW_EVENTS = 8
PREVIEW_NOTE = ("Indicative: built from the public event and holiday calendar near your area. It is not a forecast; "
                "we will send the full view for your hotel by hand.")

# Area names owners type, placed approximately (for the preview's distance
# decay only; the real view uses the hotel's own coordinates).
AREAS = {
    "deira": (25.2697, 55.3095, "dubai"), "bur dubai": (25.2532, 55.2972, "dubai"),
    "karama": (25.2459, 55.3033, "dubai"), "trade centre": (25.2285, 55.2866, "dubai"),
    "sheikh zayed road": (25.2167, 55.2780, "dubai"), "szr": (25.2167, 55.2780, "dubai"),
    "downtown": (25.1972, 55.2744, "dubai"), "business bay": (25.1850, 55.2650, "dubai"),
    "difc": (25.2110, 55.2820, "dubai"), "al barsha": (25.1110, 55.2000, "dubai"),
    "barsha": (25.1110, 55.2000, "dubai"), "dubai marina": (25.0800, 55.1400, "dubai"),
    "marina": (25.0800, 55.1400, "dubai"), "jbr": (25.0780, 55.1330, "dubai"), "jlt": (25.0690, 55.1440, "dubai"),
    "palm": (25.1124, 55.1390, "dubai"), "jumeirah": (25.2000, 55.2400, "dubai"),
    "al qusais": (25.2840, 55.3790, "dubai"), "airport": (25.2528, 55.3644, "dubai"),
    "dubai south": (24.9000, 55.1600, "dubai"), "expo city": (24.9630, 55.1470, "dubai"),
    "yas": (24.4900, 54.6070, "abu_dhabi"), "yas island": (24.4900, 54.6070, "abu_dhabi"),
    "saadiyat": (24.5400, 54.4300, "abu_dhabi"), "abu dhabi": (24.4539, 54.3773, "abu_dhabi"),
    "sharjah": (25.3463, 55.4209, "sharjah"), "ajman": (25.4052, 55.5136, "ajman"),
    "ras al khaimah": (25.8007, 55.9762, "ras_al_khaimah"), "dubai": (25.2048, 55.2708, "dubai"),
}

CITY_KEYS = ("dubai", "abu dhabi", "sharjah", "ajman", "ras al khaimah")


class LeadIn(BaseModel):
    hotel_name: str | None = None
    city: str | None = None
    area: str | None = None
    rooms: int | None = None
    name: str | None = None
    role: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    consent_processing: bool = False
    consent_transfer_abroad: bool = False
    consent_contact_email: bool = False
    consent_contact_whatsapp: bool = False
    website: str | None = None  # honeypot: people never see it


def _invalid(detail: str, code: str = "invalid_input") -> ClefsError:
    return ClefsError(422, detail, code)


def _text(v: str | None, *, field: str, lo: int, hi: int) -> str | None:
    t = " ".join((v or "").split())
    if not t and lo == 0:
        return None
    if not lo <= len(t) <= hi:
        raise _invalid(f"{field} must be {lo}-{hi} characters")
    return t


def _ip_hash(ip: str | None) -> bytes:
    key = get_settings().SECRET_KEY.encode()
    return hmac.new(key, f"demand.lead|{ip or 'unknown'}".encode(), hashlib.sha256).digest()


def _client_ip() -> str | None:
    from clefs.core.context import request_info

    return request_info().ip


# ─── Preview (curated calendar near the area) ───


def place(area: str | None, city: str | None) -> dict | None:
    """The longest known area name inside the text, else the city."""
    for text in (area, city):
        t = " ".join(re.sub(r"[^a-z ]", " ", (text or "").lower()).split())
        if not t:
            continue
        hits = [k for k in AREAS if re.search(rf"\b{re.escape(k)}\b", t)]
        if hits:
            k = max(hits, key=lambda h: (h not in CITY_KEYS, len(h)))  # a district beats its city
            lat, lon, emirate = AREAS[k]
            return {"label": k.title() if k not in ("szr", "jbr", "jlt", "difc") else k.upper(), "lat": lat,
                    "lon": lon, "emirate": emirate}
    return None


def _level(score: float) -> str:
    return "high" if score >= 0.6 else "medium" if score >= 0.3 else "low"


def preview(cur: Any, *, area: str | None, city: str | None, today: date) -> dict:
    """Up to 8 events in the next 30 days, strongest first, and the nights
    they press on. Global calendar rows only (never a hotel's own); AI-only
    discoveries are left out, as everywhere else."""
    where = place(area, city)
    end = today + timedelta(days=PREVIEW_DAYS - 1)
    rows = clefs_db.fetch_all(
        "SELECT e.*, EXISTS (SELECT 1 FROM dr_event_sources s WHERE s.event_id = e.id AND s.source <> 'AI_WEB') "
        "AS non_ai FROM dr_events e WHERE e.hotel_slug IS NULL AND e.merged_into IS NULL AND e.status <> 'CANCELLED' "
        "AND e.end_date >= %s AND e.start_date <= %s ORDER BY e.start_date", (today, end), cur=cur)
    venues = {v["code"]: v for v in clefs_db.fetch_all("SELECT * FROM dr_venues", cur=cur)}
    scored, nights = [], {}
    for ev in rows:
        if not rel.actionable(ev):
            continue
        dist = None
        if where:
            pts = [(float(v["lat"]), float(v["lon"])) for c in ev["venue_codes"] or []
                   if (v := venues.get(c)) and v.get("lat") is not None]
            if not pts and ev["lat"] is not None:
                pts = [(float(ev["lat"]), float(ev["lon"]))]
            if pts:
                dist = min(dedup.haversine_km(where["lat"], where["lon"], a, b) for a, b in pts)
        dec = rel.decay(dist, ev["category"], ev["attendance_est"])
        if ev["category"] == "CITYWIDE" and where and (ev["emirate"] or "").lower() == where["emirate"]:
            dec = max(dec, 0.6)
        score = dec * rel.scale(ev["category"], ev["attendance_est"], ev["phq_rank"])
        if score < 0.1:
            continue
        scored.append((score, ev, dist))
        for d, w in rel.nights_affected(ev["category"], ev["start_date"], ev["end_date"],
                                        uncertainty_days=ev["date_uncertainty_days"] or 0,
                                        moon_dependent=ev["moon_dependent"]).items():
            if today <= d <= end:
                nights[d] = nights.get(d, 0.0) + score * w
    scored.sort(key=lambda x: (-x[0], x[1]["start_date"]))
    return {
        "area": where, "from": today.isoformat(), "to": end.isoformat(), "note": PREVIEW_NOTE,
        "events": [{"title": ev["title"], "category": ev["category"], "start": ev["start_date"].isoformat(),
                    "end": ev["end_date"].isoformat(), "venue": (ev["venue_codes"] or [None])[0],
                    "distance_km": None if dist is None else round(dist, 1), "relevance": _level(s),
                    "verification": ev["verification"]} for s, ev, dist in scored[:PREVIEW_EVENTS]],
        "busy_nights": [{"date": d.isoformat(), "level": _level(v)}
                        for d, v in sorted(nights.items()) if v >= 0.3][:PREVIEW_DAYS],
    }


# ─── Submit ───


def submit(cur: Any, body: LeadIn) -> dict:
    from clefs import ratelimit
    from clefs.core import privacy
    from clefs.core.phone import normalize_phone

    if (body.website or "").strip():
        return {"ok": True, "lead_id": None, "preview": None}  # a bot filled the hidden field
    hotel_name = _text(body.hotel_name, field="hotel_name", lo=1, hi=120)
    city = _text(body.city, field="city", lo=1, hi=80)
    area = _text(body.area, field="area", lo=0, hi=80)
    name = _text(body.name, field="name", lo=0, hi=120)
    role = _text(body.role, field="role", lo=0, hi=60)
    if body.rooms is not None and not 1 <= int(body.rooms) <= 5000:
        raise _invalid("rooms must be between 1 and 5000")
    if not (body.consent_processing and body.consent_transfer_abroad):
        raise _invalid("Both processing consents are needed to prepare the preview", "consent_required")
    email = (body.email or "").strip().lower()[:200] or None
    wa = (body.whatsapp or "").strip()[:40] or None
    if not email and not wa:
        raise _invalid("An email or a WhatsApp number is required", "contact_required")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise _invalid("That email address does not look right")
    if wa:
        try:
            wa = normalize_phone(wa)
        except ClefsError as exc:
            raise _invalid("Enter the WhatsApp number with its country code, e.g. +971501234567") from exc
    c_email = bool(body.consent_contact_email and email)
    c_wa = bool(body.consent_contact_whatsapp and wa)
    if not (c_email or c_wa):
        raise _invalid("Tick how Clefs may contact you, on a channel you filled in", "contact_consent_required")
    ip_h = _ip_hash(_client_ip())
    if not ratelimit.hit(cur, f"dr:lead:ip:{ip_h.hex()[:32]}", limit=IP_DAILY_LIMIT, window_s=86400):
        raise ClefsError(429, "Too many requests today; try again tomorrow", "rate_limited")
    if not ratelimit.hit(cur, "dr:lead:global", limit=GLOBAL_DAILY_LIMIT, window_s=86400):
        raise ClefsError(429, "Too many requests today; try again tomorrow", "rate_limited")
    now = clock.now()
    today = now.astimezone(DUBAI).date()
    pv = preview(cur, area=area, city=city, today=today)
    lead_id = str(uuid.uuid4())
    clefs_db.execute(
        """INSERT INTO dr_leads (id, hotel_name, city, area, rooms, contact_name, contact_role, email_enc, whatsapp_enc,
               consent_processing, consent_transfer_abroad, consent_contact_email, consent_contact_whatsapp, ip_hash,
               preview, created_at, expires_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, TRUE, %s, %s, %s, %s, %s, %s)""",
        (lead_id, hotel_name, city, area, body.rooms, name, role,
         privacy.seal(email) if email else None, privacy.seal(wa) if wa else None, c_email, c_wa, ip_h,
         Json(pv), now, now + RETENTION), cur=cur)
    _notify_founder(cur, lead_id, hotel_name=hotel_name, city=city, area=area, rooms=body.rooms, pv=pv,
                    channels=[c for c, ok in (("email", c_email), ("WhatsApp", c_wa)) if ok])
    return {"ok": True, "lead_id": lead_id, "preview": pv}


def _notify_founder(cur: Any, lead_id: str, *, hotel_name: str, city: str, area: str | None, rooms: int | None,
                    pv: dict, channels: list[str]) -> None:
    """An email to FOUNDER_NOTIFY_EMAIL (unmodelled, read through env()),
    with the hotel and the preview headline only: contacts stay sealed in
    the row and are read in the console."""
    to = env("FOUNDER_NOTIFY_EMAIL", None)
    if not to:
        return
    from clefs import notify

    top = ", ".join(e["title"] for e in pv["events"][:3]) or "no curated events placed"
    e = html.escape
    notify.send_email(
        to, f"Demand preview request: {hotel_name} ({city})",
        f"<p><b>{e(hotel_name)}</b>, {e(area or '-')}, {e(city)}; rooms: {e(str(rooms or '-'))}.</p>"
        f"<p>Contact allowed by: {e(', '.join(channels))}. Lead {e(lead_id)}.</p>"
        f"<p>Next 30 days near the area: {e(top)}.</p>",
        purpose="demand.public_lead", category="utility", idempotency_key=f"demand.public_lead:{lead_id}", cur=cur)
