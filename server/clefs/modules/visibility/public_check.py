"""
Public Google check, the secondary lead magnet (04 spec 5.9, Canon 23; the
UI is lane H's /google-check page).

Flow: POST /public/visibility/google-check {hotel_name, city, both consent
ticks} searches Google Hotels through core.serpapi (purpose 'public_check',
capped by PUBLIC_SERPAPI_MONTHLY_CAP; exhausted -> 503) and matches the
hotel by name (>= 0.85), else offers up to 5 candidates to pick. A matched
hotel's property page is read for the two stay-date pairs. The teaser says
only "Official site price on Google: yes/no"; the parity table, the organic
rank and the note unlock after POST .../{token}/lead. Lead contacts are
sealed (core.privacy.seal) and cleared at 30 days; the row goes at
expires_at. Limits: 3 checks per IP per day and PUBLIC_CHECK_DAILY_CAP in
total, plus 3 picks per IP per day. A pick claims its row ('picking', a
5-minute lease) before its 3 searches, so parallel picks on one token cost
one set of searches, not N (409 pick_in_progress for the rest). The token is
shown once; only its sha256 is stored.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel
from rapidfuzz import fuzz

from clefs import db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.settings import env, get_settings

PURPOSE = "public_check"
RETENTION = timedelta(days=30)
IP_DAILY_LIMIT = 3
# A pick spends 3 searches too. One pick per started check is all an honest
# prospect needs, so picks get the same per-IP budget as starts (own key).
PICK_IP_DAILY_LIMIT = IP_DAILY_LIMIT
# How long a pick's claim holds before another request may take it over
# (a worker that died mid-search). Three SerpApi calls finish well inside it.
PICK_LEASE = timedelta(minutes=5)
MATCH_MIN = 85  # name similarity >= 0.85 (5.9)
MAX_CANDIDATES = 5
CURRENCY, GL, HL = "AED", "ae", "en"  # the magnet is for UAE hotels (Canon 23)
DUBAI = ZoneInfo("Asia/Dubai")
OUTREACH_WINDOW = (time(9, 0), time(18, 0))  # [09:00, 18:00) Asia/Dubai
NOTE_CLAIM_IDS = ["google_third_party_rates_unavailable_2026_09_30", "triptease_free_booking_links_unaffected"]
LABEL = "Google Hotels data via SerpApi (third-party); indicative"

router = APIRouter(prefix="/public/visibility/google-check", tags=["visibility-public"])

# ─── Bodies ───


# Every field is optional at the schema level and checked by hand, so a
# missing field is the contract's 422 invalid_input ({detail, code}), not
# FastAPI's list-shaped validation error (lane H's page reads `code`).


class CheckIn(BaseModel):
    hotel_name: str | None = None
    city: str | None = None
    consent_processing: bool = False
    consent_transfer_abroad: bool = False


class PickIn(BaseModel):
    property_token: str | None = None


class LeadIn(BaseModel):
    name: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    consent_contact_email: bool = False
    consent_contact_whatsapp: bool = False


def _invalid(detail: str) -> ClefsError:
    return ClefsError(422, detail, "invalid_input")


def _text(v: str | None, *, field: str, lo: int, hi: int) -> str:
    t = " ".join((v or "").split())
    if not lo <= len(t) <= hi:
        raise _invalid(f"{field} must be {lo}-{hi} characters")
    return t


# ─── Tokens and identity ───


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _ip_hash(ip: str | None) -> bytes:
    key = get_settings().SECRET_KEY.encode()
    return hmac.new(key, f"vis.public_check|{ip or 'unknown'}".encode(), hashlib.sha256).digest()


def _client_ip() -> str | None:
    from clefs.core.context import request_info

    return request_info().ip


# ─── SerpApi (purpose public_check, no hotel) ───


def _search(params: dict) -> dict:
    """core.serpapi.search, mapped to the endpoint's errors: an exhausted
    public cap (or the global budget) fails closed with 503."""
    from clefs.core import serpapi
    from clefs.core.errors import NotConfigured

    try:
        return serpapi.search("google_hotels", params, hotel_slug=None, purpose=PURPOSE)
    except serpapi.BudgetExhausted as exc:
        raise ClefsError(503, "The free check is paused for this month", "budget_exhausted") from exc
    except NotConfigured as exc:
        raise ClefsError(503, "The free check is not available right now", "budget_exhausted") from exc
    except serpapi.SerpApiError as exc:
        raise ClefsError(502, "Google Hotels did not answer; please retry", "serpapi_error") from exc


def _base(q: str, ci: date, co: date) -> dict:
    return {"engine": "google_hotels", "q": q, "check_in_date": ci.isoformat(), "check_out_date": co.isoformat(),
            "currency": CURRENCY, "gl": GL, "hl": HL, "adults": 2}


def _similar(a: str, b: str) -> int:
    from clefs.modules.visibility.parsers import normalise

    na, nb = normalise(a), normalise(b)
    return int(fuzz.token_sort_ratio(na, nb)) if na and nb else 0


def match(page: dict, hotel_name: str) -> tuple[dict | None, list[dict]]:
    """(the matched property or None, up to 5 candidates). SerpApi answers a
    precise name with the property page itself (type 'hotel' at the top)."""
    if page.get("type") == "hotel" and page.get("name") and _similar(page["name"], hotel_name) >= MATCH_MIN:
        return {"name": page["name"], "property_token": page.get("property_token"), "page": page}, []
    props = [p for p in (page.get("properties") or []) if isinstance(p, dict) and p.get("property_token")]
    organic = [p for p in props if not p.get("sponsored")]
    scored = sorted(((_similar(p.get("name") or "", hotel_name), i, p) for i, p in enumerate(organic)),
                    key=lambda t: (-t[0], t[1]))
    if scored and scored[0][0] >= MATCH_MIN:
        return scored[0][2], []
    cands = [{"property_token": p["property_token"], "name": p.get("name"),
              "address": p.get("address") or p.get("description")} for _, _, p in scored[:MAX_CANDIDATES]]
    return None, cands


def analyse(hotel_name: str, city: str, token: str) -> dict:
    """Property details for the two stay-date pairs, the teaser and the
    locked parity table, plus the organic rank in "hotels in {city}"."""
    from clefs.modules.visibility import google_hotels

    today = clock.now().astimezone(DUBAI).date()
    pairs = google_hotels.date_pairs(today)
    per_pair = []
    for ci, co in pairs:
        details = _search({**_base(f"{hotel_name} {city}", ci, co), "property_token": token})
        per_pair.append((ci, co, google_hotels.analyse_prices(details, CURRENCY)))
    ci, co = pairs[0]
    area = _search(_base(f"hotels in {city}", ci, co))
    organic, _ = google_hotels.organic_properties(area)
    rank = google_hotels.rank_in(organic, token)
    first = per_pair[0][2]
    gaps = [a for _, _, a in per_pair if a.get("parity_gap_minor") is not None]
    worst = max(gaps, key=lambda a: a["parity_gap_minor"], default=None)
    return {
        "teaser": {"official_present": any(a["official_present"] for _, _, a in per_pair)},
        "unlocked": {
            "parity": first["prices"], "gap_minor": worst["parity_gap_minor"] if worst else None,
            "gap_pct": str(google_hotels.q3(worst["parity_gap_pct"])) if worst else None,
            "organic_rank": rank, "checked_at": clock.now().isoformat(),
            "stay_dates": [[a.isoformat(), b.isoformat()] for a, b, _ in per_pair],
            "note_claim_ids": NOTE_CLAIM_IDS, "currency": CURRENCY, "label": LABEL,
        },
    }


# ─── Rows and views ───


def _row(cur: Any, token: str) -> dict:
    cur.execute("SELECT * FROM vis_public_checks WHERE token_hash = %s AND expires_at > %s FOR UPDATE",
                (_token_hash(token), clock.now()))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "This check has expired or does not exist", "not_found")
    return dict(row)


def view(row: dict, token: str) -> dict:
    """The response shape for a row: `candidates` only for needs_pick,
    `teaser` only for ready, `unlocked` only after the lead step."""
    result = row.get("result") or {}
    # 'picking' is an internal claim state (a pick is spending its searches);
    # the public wire keeps its three statuses, and lane H's page would read
    # an unknown one as a blank teaser. A second pick meanwhile gets 409.
    status = "needs_pick" if row["status"] == "picking" else row["status"]
    out: dict[str, Any] = {"token": token, "status": status, "candidates": [], "teaser": None}
    if status == "needs_pick":
        out["candidates"] = row.get("candidates") or []
    if row["status"] == "ready":
        out["teaser"] = result.get("teaser")
        if row.get("lead_at"):
            out["unlocked"] = result.get("unlocked")
    return out


def _resolve(hotel_name: str, city: str, token_prop: str | None) -> tuple[str, dict | None]:
    if not token_prop:
        return "no_match", None
    return "ready", analyse(hotel_name, city, token_prop)


# ─── Routes ───


@router.post("")
def start_check(body: CheckIn) -> dict:
    if not (body.consent_processing and body.consent_transfer_abroad):
        raise _invalid("Both consent boxes must be ticked")
    hotel_name = _text(body.hotel_name, field="hotel_name", lo=2, hi=120)
    city = _text(body.city, field="city", lo=2, hi=80)
    s = get_settings()
    from clefs import ratelimit

    ip_h = _ip_hash(_client_ip())
    with db.db() as cur:
        if not ratelimit.hit(cur, f"vis:gcheck:ip:{ip_h.hex()[:32]}", limit=IP_DAILY_LIMIT, window_s=86400):
            raise ClefsError(429, "You have run 3 checks today; please try again tomorrow", "rate_limited")
        if not ratelimit.hit(cur, "vis:gcheck:global", limit=max(0, int(s.PUBLIC_CHECK_DAILY_CAP)), window_s=86400):
            raise ClefsError(429, "Today's free checks are used up; please try again tomorrow", "rate_limited")
    # SerpApi runs outside any transaction (core.serpapi opens its own).
    from clefs.modules.visibility import google_hotels

    today = clock.now().astimezone(DUBAI).date()
    ci, co = google_hotels.date_pairs(today)[0]
    page = _search(_base(f"{hotel_name} {city}", ci, co))
    matched, cands = match(page, hotel_name)
    if matched:
        status, result = _resolve(hotel_name, city, matched.get("property_token"))
    else:
        status, result = ("needs_pick", None) if cands else ("no_match", None)
    token = secrets.token_urlsafe(24)
    with db.db() as cur:
        from clefs.db import Json

        now = clock.now()
        cur.execute(
            """
            INSERT INTO vis_public_checks (id, token_hash, ip_hash, hotel_query, city, property_token, candidates,
                result, consent_processing, consent_transfer_abroad, status, created_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, TRUE, %s, %s, %s)
            """,
            (str(uuid.uuid4()), _token_hash(token), ip_h, hotel_name, city,
             (matched or {}).get("property_token"), Json(cands), Json(result) if result else None, status, now,
             now + RETENTION),
        )
        cur.execute("SELECT * FROM vis_public_checks WHERE token_hash = %s", (_token_hash(token),))
        return view(dict(cur.fetchone()), token)


def _claim_pick(cur: Any, row: dict, prop: str) -> bool:
    """Move the row to 'picking' in the caller's transaction. WHY: the row
    lock from _row() ends at commit, while the 3 paid searches run after it;
    without a persisted claim every parallel pick on one needs_pick token
    passed the status check and spent its own 3 searches (security-privacy-6).
    The conditional UPDATE is the single gate: a row already 'picking' under
    a live lease is refused; a lease older than PICK_LEASE (a crashed worker)
    may be taken again so the prospect is never stuck."""
    now = clock.now()
    cur.execute(
        """
        UPDATE vis_public_checks SET status = 'picking', property_token = %s, picking_at = %s
        WHERE id = %s AND (status = 'needs_pick' OR (status = 'picking' AND picking_at < %s))
        RETURNING id
        """,
        (prop, now, row["id"], now - PICK_LEASE),
    )
    return cur.fetchone() is not None


def _release_pick(row_id: Any) -> None:
    """Undo a claim whose searches failed (budget, SerpApi error), so the
    prospect can pick again once the cause clears."""
    with db.db() as cur:
        cur.execute("UPDATE vis_public_checks SET status = 'needs_pick', property_token = NULL, picking_at = NULL "
                    "WHERE id = %s AND status = 'picking'", (row_id,))


@router.post("/{token}/pick")
def pick(token: str, body: PickIn) -> dict:
    from clefs import ratelimit
    from clefs.db import Json

    with db.db() as cur:
        row = _row(cur, token)
        allowed = {c.get("property_token") for c in (row.get("candidates") or [])}
        if row["status"] not in ("needs_pick", "picking") or not body.property_token \
                or body.property_token not in allowed:
            raise _invalid("Pick one of the offered hotels")
        # The IP limit sits after validation (a typo costs nothing) and shares
        # the claim's transaction: a refused claim (409) rolls the attempt
        # back, so only a pick that reaches SerpApi counts against the IP.
        if not ratelimit.hit(cur, f"vis:gcheck:pick:ip:{_ip_hash(_client_ip()).hex()[:32]}",
                             limit=PICK_IP_DAILY_LIMIT, window_s=86400):
            raise ClefsError(429, "You have run 3 checks today; please try again tomorrow", "rate_limited")
        if not _claim_pick(cur, row, body.property_token):
            raise ClefsError(409, "This check is already running; refresh in a moment", "pick_in_progress")
    try:
        status, result = _resolve(row["hotel_query"], row["city"], body.property_token)
    except BaseException:
        _release_pick(row["id"])
        raise
    with db.db() as cur:
        cur.execute("UPDATE vis_public_checks SET status = %s, property_token = %s, result = %s, picking_at = NULL "
                    "WHERE id = %s", (status, body.property_token, Json(result) if result else None, row["id"]))
        return view({**row, "status": status, "result": result}, token)


@router.get("/{token}")
def get_check(token: str) -> dict:
    with db.db() as cur:
        return view(_row(cur, token), token)


@router.post("/{token}/lead")
def lead(token: str, body: LeadIn) -> dict:
    """Unlock the parity table. The contacts are sealed; each contact
    consent names Clefs and the channel, and a WhatsApp follow-up is queued
    only with consent_contact_whatsapp (inside 09:00-18:00 Dubai time)."""
    from clefs.core import privacy
    from clefs.core.phone import normalize_phone

    name = _text(body.name, field="name", lo=1, hi=120)
    email = (body.email or "").strip().lower()[:200] or None
    wa = (body.whatsapp or "").strip()[:40] or None
    if not email and not wa:
        raise _invalid("An email or a WhatsApp number is required")
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        raise _invalid("That email address does not look right")
    if wa:
        try:
            wa = normalize_phone(wa)  # E.164 only: a national number is ambiguous without a country
        except ClefsError as exc:
            raise _invalid("Enter the WhatsApp number with its country code, e.g. +971501234567") from exc
    with db.db() as cur:
        row = _row(cur, token)
        if row["status"] != "ready":
            raise _invalid("Finish the check first")
        now = clock.now()
        cur.execute(
            """
            UPDATE vis_public_checks SET lead_name = %s, lead_email_enc = %s, lead_whatsapp_enc = %s,
                   consent_contact_email = %s, consent_contact_whatsapp = %s, lead_at = COALESCE(lead_at, %s)
            WHERE id = %s
            """,
            (name, privacy.seal(email) if email else None, privacy.seal(wa) if wa else None,
             bool(body.consent_contact_email and email), bool(body.consent_contact_whatsapp and wa), now, row["id"]),
        )
        _notify_founder(cur, row, name=name)
        if body.consent_contact_whatsapp and wa and not row.get("lead_at"):
            from clefs import jobs

            jobs.enqueue("visibility.public_lead_followup", {"check_id": str(row["id"])},
                         run_at=outreach_at(now), dedupe_key=f"visibility.public_lead_followup:{row['id']}", cur=cur)
        return {"unlocked": (row.get("result") or {}).get("unlocked")}


def outreach_at(now: datetime) -> datetime:
    """The next moment inside [09:00, 18:00) Asia/Dubai (now if inside)."""
    local = now.astimezone(DUBAI)
    start, end = OUTREACH_WINDOW
    if start <= local.time() < end:
        return now
    day = local.date() if local.time() < start else local.date() + timedelta(days=1)
    return datetime.combine(day, start, tzinfo=DUBAI)


def _notify_founder(cur: Any, row: dict, *, name: str) -> None:
    """An email to FOUNDER_NOTIFY_EMAIL (unmodelled, read through env()); no
    contact details in it, only the hotel and the teaser answer."""
    # FOUNDATION-REQUEST: model FOUNDER_NOTIFY_EMAIL and VIS_LEAD_WA_TEMPLATE in
    # Settings and list them in .env.example (read through env() until then).
    to = env("FOUNDER_NOTIFY_EMAIL", None)
    if not to:
        return
    from clefs import notify

    teaser = ((row.get("result") or {}).get("teaser") or {})
    notify.send_email(to, f"Google check lead: {row['hotel_query']} ({row['city']})",
                      f"<p>{_esc(name)} unlocked the Google check for <b>{_esc(row['hotel_query'])}</b>, "
                      f"{_esc(row['city'])}. Official price on Google: "
                      f"{'yes' if teaser.get('official_present') else 'no'}.</p>",
                      purpose="visibility.public_lead", category="utility",
                      idempotency_key=f"visibility.public_lead:{row['id']}", cur=cur)


def _esc(s: str | None) -> str:
    import html

    return html.escape(s or "")


def purge_expired(cur: Any) -> dict:
    cur.execute("DELETE FROM vis_public_checks WHERE expires_at <= %s", (clock.now(),))
    return {"deleted": cur.rowcount}


def whatsapp_followup(cur: Any, check_id: str) -> dict:
    """The founder's WhatsApp follow-up (task visibility.public_lead_followup):
    only with consent_contact_whatsapp, only inside 09:00-18:00 Dubai time
    (a late retry is re-queued to the next window), from the platform number
    with an approved marketing template (VIS_LEAD_WA_TEMPLATE)."""
    from clefs import jobs, notify
    from clefs.core import privacy

    cur.execute("SELECT * FROM vis_public_checks WHERE id = %s", (check_id,))
    row = cur.fetchone()
    if row is None or not row["consent_contact_whatsapp"] or not row["lead_whatsapp_enc"]:
        return {"skipped": "no_consent"}
    now = clock.now()
    when = outreach_at(now)
    if when > now:
        raise jobs.Retry(when, "outside the 09:00-18:00 Dubai window")
    to = privacy.unseal(bytes(row["lead_whatsapp_enc"]))
    res = notify.send_whatsapp_template(
        to, env("VIS_LEAD_WA_TEMPLATE", "clefs_google_check_followup"), "en",
        [row["lead_name"] or "there", row["hotel_query"] or "your hotel"], hotel_slug=None,
        purpose="visibility.public_lead", sender="platform", category="marketing",
        idempotency_key=f"visibility.public_lead_wa:{row['id']}", cur=cur)
    return {"ok": res.ok, "status": res.status}
