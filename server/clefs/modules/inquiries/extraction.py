"""
Inquiry extraction (04 spec 4.4): dates, pax, segment, language, intent.

Two readers share one output shape (models.InquiryExtraction):

- the LLM (`ai.structured(store=False)`), only when the inquiry's consent
  allows it: `express_web_form` always (the form collected transfer_abroad);
  every other basis only when InquirySettings.ai_on_unconsented_inbound is
  switched on (false until counsel signs off, 7.7);
- a deterministic date and pax parser for everything else, which a person
  then reads (confidence stays low).

Phones and emails are regex-redacted before either reader sees the text:
contacts come from channel metadata, never from the model.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

# ─── Redaction ───

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 7+ digits with the usual separators, optionally +country: long enough not
# to eat dates ("12-14") or room counts.
_PHONE_RE = re.compile(r"(?<![\w/])(?:\+|00)?\d[\d\s().\-]{6,}\d(?![\w/])")
_DATE_SHAPE_RE = re.compile(r"(?:20\d\d-\d{1,2}-\d{1,2}|\d{1,2}[.\-]\d{1,2}[.\-](?:20)?\d\d)")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def redact(text: str) -> str:
    t = _EMAIL_RE.sub("[email]", text or "")

    def _phone(m: re.Match) -> str:
        span = m.group(0).strip()
        if _DATE_SHAPE_RE.fullmatch(span):
            return m.group(0)  # 2026-10-05 or 05.10.2026 is a stay date, not a phone
        digits = re.sub(r"\D", "", span)
        return "[phone]" if len(digits) >= 7 else m.group(0)

    return _PHONE_RE.sub(_phone, t)


def normalise_digits(text: str) -> str:
    return (text or "").translate(_AR_DIGITS)


def detect_language(text: str) -> str:
    letters = [c for c in text or "" if c.isalpha()]
    if not letters:
        return "other"
    arabic = sum(1 for c in letters if "؀" <= c <= "ۿ" or "ݐ" <= c <= "ݿ")
    if arabic / len(letters) >= 0.3:
        return "ar"
    latin = sum(1 for c in letters if c.isascii())
    return "en" if latin / len(letters) >= 0.6 else "other"


# ─── Deterministic parser ───

_MONTHS = {
    m: i for i, names in enumerate((
        ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
        ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
        ("oct", "october"), ("nov", "november"), ("dec", "december"),
    ), start=1) for m in names
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ISO_RE = re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d\d|\d\d)\b")
_DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*(?:-|to|until|till)\s*(\d{{1,2}})(?:st|nd|rd|th)?)?\s+({_MONTH_RE})\b", re.I)
_MONTH_DAY_RE = re.compile(rf"\b({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*(?:-|to|until|till)\s*(\d{{1,2}})(?:st|nd|rd|th)?)?\b", re.I)
_COUNT = r"(\d{1,3})\s*"
_ROOMS_RE = re.compile(_COUNT + r"(?:x\s*)?(?:rooms?|غرف|غرفة|غرفه)", re.I)
_ADULTS_RE = re.compile(_COUNT + r"(?:adults?|people|persons?|guests?|pax|أشخاص|اشخاص|شخص|بالغين|بالغ)", re.I)
_CHILDREN_RE = re.compile(_COUNT + r"(?:children|child|kids?|أطفال|اطفال|طفل)", re.I)
_NIGHTS_RE = re.compile(_COUNT + r"(?:nights?|ليالي|ليلة|ليله)", re.I)
_RELATIVE = (
    (re.compile(r"\b(tonight)\b|الليلة", re.I), 0),
    (re.compile(r"\b(tomorrow)\b|غدا|غداً|بكرة", re.I), 1),
)
_FLEX_RE = re.compile(r"\b(flexible|around|approximately|sometime|after eid|before eid)\b|مرن|بعد العيد|قبل العيد", re.I)
_EID_RE = re.compile(r"\beid\b|العيد|رمضان|ramadan", re.I)


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _future_year(today: date, m: int, d: int) -> date | None:
    """A day-month without a year means the next such date."""
    got = _mk(today.year, m, d)
    if got is not None and got < today - timedelta(days=1):
        got = _mk(today.year + 1, m, d)
    return got


def parse_dates(text: str, today: date) -> tuple[date | None, date | None, str | None]:
    """(check_in, check_out, the matched text) from the first date mention(s)."""
    t = normalise_digits(text)
    found: list[tuple[int, date, str]] = []
    for m in _ISO_RE.finditer(t):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d, m.group(0)))
    for m in _DMY_RE.finditer(t):
        y = int(m.group(3))
        d = _mk(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))  # UAE writes day first
        if d:
            found.append((m.start(), d, m.group(0)))
    for m in _DAY_MONTH_RE.finditer(t):
        mon = _MONTHS[m.group(3).lower()]
        a = _future_year(today, mon, int(m.group(1)))
        if a:
            found.append((m.start(), a, m.group(0)))
            if m.group(2):
                b = _future_year(a, mon, int(m.group(2)))
                if b:
                    found.append((m.start() + 1, b, m.group(0)))
    for m in _MONTH_DAY_RE.finditer(t):
        mon = _MONTHS[m.group(1).lower()]
        a = _future_year(today, mon, int(m.group(2)))
        if a:
            found.append((m.start(), a, m.group(0)))
            if m.group(3):
                b = _future_year(a, mon, int(m.group(3)))
                if b:
                    found.append((m.start() + 1, b, m.group(0)))
    if not found:
        for rx, offset in _RELATIVE:
            m = rx.search(t)
            if m:
                return today + timedelta(days=offset), None, m.group(0)
        return None, None, None
    found.sort(key=lambda f: f[0])
    dates = sorted({f[1] for f in found})
    text_span = found[0][2]
    return dates[0], (dates[1] if len(dates) > 1 else None), text_span


def _count(rx: re.Pattern, text: str) -> int | None:
    m = rx.search(text)
    return int(m.group(1)) if m else None


def deterministic(text: str, today: date) -> "InquiryExtraction":
    """The no-AI reader: dates, counts and language only. Intent stays
    `other` and confidence low, so a person reads every such inquiry."""
    from clefs.modules.inquiries.models import FieldQuote, InquiryExtraction

    t = normalise_digits(redact(text))
    check_in, check_out, date_text = parse_dates(t, today)
    nights = _count(_NIGHTS_RE, t)
    if check_in and not check_out and nights:
        check_out = check_in + timedelta(days=nights)
    rooms, adults, children = _count(_ROOMS_RE, t), _count(_ADULTS_RE, t), _count(_CHILDREN_RE, t)
    evidence = []
    if date_text:
        evidence.append(FieldQuote(field="check_in", quote=date_text))
    for field, rx in (("rooms", _ROOMS_RE), ("adults", _ADULTS_RE), ("children", _CHILDREN_RE), ("nights", _NIGHTS_RE)):
        m = rx.search(t)
        if m:
            evidence.append(FieldQuote(field=field, quote=m.group(0)))
    missing = [k for k, v in (("dates", check_in), ("guests", adults), ("rooms", rooms)) if not v]
    lang = detect_language(text)
    return InquiryExtraction(
        language=lang,
        intent="other",
        segment="unknown",
        is_rfp=bool(re.search(r"\b(rfp|request for proposal|tender)\b", t, re.I)),
        check_in=check_in,
        check_out=check_out,
        nights=nights,
        dates_flexible=bool(_FLEX_RE.search(t) or _EID_RE.search(t)),
        date_text=date_text,
        rooms=rooms,
        adults=adults,
        children=children,
        room_type_hint=None,
        budget_amount=None,
        budget_currency=None,
        budget_per="unknown",
        company=None,
        event_name=None,
        special_requests=[],
        missing=missing,
        confidence=0.3,
        evidence=evidence,
    )


# ─── LLM reader ───

SYSTEM_PROMPT = """You read one hotel booking inquiry and extract structured fields.
Rules:
- Resolve relative dates ("next Friday", "this weekend") against TODAY, given in the input, in the hotel's timezone.
- check_out is the departure date. If only nights are given, compute check_out from check_in.
- Never invent a value: use null when the text does not say it. Fill `missing` with what is absent.
- `evidence` must quote the exact words (verbatim) that support every field you fill.
- Contact details were removed and appear as [phone] / [email]; ignore them.
- intent: existing_booking or complaint when the writer already has a reservation or reports a problem;
  vendor when they sell something to the hotel; spam for unrelated content.
- segment: group for 9+ rooms, long_stay for 30+ nights, corporate for company travel, event for meetings or
  weddings, fit for individual leisure travel; unknown when unclear.
- Hijri-relative dates ("after Eid") are flexible: set dates_flexible true and keep the dates null unless the
  text gives a Gregorian date.
- confidence is your overall confidence from 0 to 1."""


def llm(text: str, *, today: date, hotel_slug: str, hotel_name: str, timezone: str) -> "InquiryExtraction":
    from clefs import ai
    from clefs.modules.inquiries.models import InquiryExtraction

    payload = f"TODAY: {today.isoformat()} ({timezone})\nHOTEL: {hotel_name}\n\nINQUIRY:\n{redact(text)[:8000]}"
    return ai.structured(
        "inquiries.extract", system=SYSTEM_PROMPT, input=payload, schema=InquiryExtraction,
        hotel_slug=hotel_slug, store=False, max_output_tokens=1500,
    )


def ai_allowed(row: dict, settings: Any) -> bool:
    """4.4: only express_web_form reaches the model by default."""
    return row["consent_basis"] == "express_web_form" or bool(settings.ai_on_unconsented_inbound)


# ─── Validation (4.4) ───


def validate(cur: Any, hotel: Any, ext: "InquiryExtraction") -> tuple[dict, list[str]]:
    """Extraction -> candidate column values, plus the warnings that explain
    every value it refused."""
    from decimal import Decimal

    from clefs.core import money
    from clefs.core.time import local_date
    from clefs.modules.demand import api as demand_api
    from clefs.modules.inquiries.models import GROUP_ROOMS, LONG_STAY_NIGHTS, MAX_NIGHTS, MAX_ROOMS

    warnings: list[str] = []
    ci, co, nights, flexible = ext.check_in, ext.check_out, ext.nights, ext.dates_flexible
    if ext.event_name and ci is None:
        ev = demand_api.find_event(cur, hotel.slug, text=ext.event_name)
        if ev and ev.get("start"):
            ci = date.fromisoformat(str(ev["start"]))
            co = date.fromisoformat(str(ev["end"])) if ev.get("end") else None
            flexible = True
            warnings.append(f"dates from event {ev.get('name') or ext.event_name}")
        else:
            flexible = True
    if ci and co and co <= ci:
        warnings.append("check_out not after check_in: dropped")
        co = None
    if ci and co:
        nights = (co - ci).days
    elif ci and nights and not co:
        co = ci + timedelta(days=nights)
    if nights is not None and not 0 < nights <= MAX_NIGHTS:
        warnings.append(f"nights {nights} out of range: dropped")
        nights, co = None, (None if ci is None else None)
    rooms = ext.rooms
    if rooms is not None and not 0 < rooms <= MAX_ROOMS:
        warnings.append(f"rooms {rooms} out of range: dropped")
        rooms = None
    segment = ext.segment
    if nights and nights >= LONG_STAY_NIGHTS:
        segment = "long_stay"
    elif rooms and rooms >= GROUP_ROOMS:
        segment = "group"
    cols: dict[str, Any] = {
        "check_in": ci, "check_out": co, "nights": nights, "rooms": rooms,
        "adults": ext.adults if ext.adults is None or 0 < ext.adults <= 2000 else None,
        "children": ext.children if ext.children is None or 0 <= ext.children <= 2000 else None,
        "dates_flexible": flexible, "segment": segment, "is_rfp": ext.is_rfp, "intent": ext.intent,
        "language": ext.language if ext.language != "other" else None, "company": ext.company,
    }
    if ext.budget_amount is not None and ext.budget_amount > 0:
        src = (ext.budget_currency or hotel.currency).upper()
        try:
            minor = money.to_minor(Decimal(ext.budget_amount), src)
            if src != hotel.currency:  # a USD budget goes through the 3.6725 peg
                minor = money.convert_minor(cur, minor, src, hotel.currency, local_date(hotel))
            cols.update(budget_minor=minor, budget_currency=hotel.currency)
        except Exception:  # noqa: BLE001 - an unknown currency code drops the budget, not the inquiry
            warnings.append(f"budget currency {src} not convertible: dropped")
    return cols, warnings


# Filled only when the inquiry does not already say it: form fields, lead
# answers and staff edits are authoritative over a reader's guess.
_FILL_ONLY = ("check_in", "check_out", "nights", "rooms", "adults", "children", "company", "language",
              "budget_minor", "budget_currency", "dates_flexible")


def merge_columns(row: dict, cols: dict) -> dict:
    """The column changes for `row` (4.4 dedupe also uses this: "new fields
    are filled in")."""
    changes: dict[str, Any] = {}
    for k in _FILL_ONLY:
        if row.get(k) is None and cols.get(k) is not None:
            changes[k] = cols[k]
    if row.get("check_in") is not None and "check_out" in changes and changes["check_out"] <= row["check_in"]:
        changes.pop("check_out")
    ci = changes.get("check_in", row.get("check_in"))
    co = changes.get("check_out", row.get("check_out"))
    if ci and co and row.get("nights") is None:
        changes["nights"] = (co - ci).days
    # Segment: the reader's value replaces 'unknown' or a previous reader's
    # value, never a person's choice (extraction.segment_by == 'staff').
    by_staff = (row.get("extraction") or {}).get("segment_by") == "staff"
    if cols.get("segment") and cols["segment"] != "unknown" and not by_staff:
        rn_rooms = changes.get("rooms", row.get("rooms"))
        rn_nights = changes.get("nights", row.get("nights"))
        seg = cols["segment"]
        if rn_nights and rn_nights >= 30:
            seg = "long_stay"
        elif rn_rooms and rn_rooms >= 9:
            seg = "group"
        if seg != row.get("segment"):
            changes["segment"] = seg
    if cols.get("is_rfp") and not row.get("is_rfp"):
        changes["is_rfp"] = True
    if cols.get("intent") and cols["intent"] != row.get("intent"):
        changes["intent"] = cols["intent"]
    return changes


# ─── Routing by intent (4.4) ───


def _in_house_room(cur: Any, hotel_slug: str, hmacs: list[bytes]) -> str | None:
    from clefs.core import stays

    for h in hmacs:
        for rid in stays.reservations_for_contact(cur, hotel_slug, h):
            r = stays.get_reservation(cur, rid)
            if r and r.get("status") == "IN_HOUSE" and r.get("room_number"):
                return str(r["room_number"])
    return None


def reroute(cur: Any, hotel: Any, row: dict, text: str) -> dict:
    """existing_booking / complaint: a guest matter, not a sale. In-house
    senders become a Service request on their room, everyone else a
    front-desk follow-up; the inquiry moves to `rerouted` either way."""
    from clefs.core import clock
    from clefs.modules.inquiries import service, store
    from clefs.modules.service import api as service_api

    room = _in_house_room(cur, hotel.slug, store.hmacs_of(row))
    summary = f"Inquiry {row['ref'].upper()} ({row['source']}): {redact(text)[:1500]}"
    if room:
        req_type = getattr(service_api, "REQUEST_TYPE_FOR_DEPARTMENT", {}).get("front_office", "front_desk")
        got = service_api.create_request(
            cur, hotel.slug, room=room, type=req_type, description=summary, source="inquiries",
            language=row.get("language"),
        )
        data = {"routed_to": "service_request", "request_id": got.get("id"), "room": room}
    else:
        got = service_api.create_followup(
            cur, hotel.slug, kind="guest_contact", summary=summary, guest_ref=row["ref"], owner_role="front_desk",
            due_at=clock.now() + timedelta(hours=1), source="inquiries", language=row.get("language"),
        )
        data = {"routed_to": "followup", "followup": {k: got.get(k) for k in ("id", "kind") if k in got}}
    return service.transition(cur, row, "rerouted", reason=row.get("intent"), data=data, hotel=hotel)


# ─── The task body ───


def run(cur: Any, hotel: Any, inquiry_id: int) -> dict:
    """Read the thread, fill the inquiry, route by intent, then refresh
    money at risk and the SLA timer (the segment may have changed it)."""
    from clefs.core import clock
    from clefs.core.time import local_date
    from clefs.modules.inquiries import risk, service, sla, store

    row = store.get_inquiry(cur, hotel.slug, inquiry_id, lock=True)
    text = store.inbound_text(cur, inquiry_id)
    if not text.strip():
        return {"skipped": "no_text"}
    settings = sla.settings_for(cur, hotel.slug)
    today = local_date(hotel)
    reader = "deterministic"
    ext = None
    if ai_allowed(row, settings):
        from clefs import ai

        try:
            ext = llm(text, today=today, hotel_slug=hotel.slug, hotel_name=hotel.name, timezone=str(hotel.timezone))
            reader = "llm"
        except (ai.AIDisabled, ai.AIError):
            ext = None  # degrade to the parser; a person reads it
    if ext is None:
        ext = deterministic(text, today)
    cols, warnings = validate(cur, hotel, ext)
    changes = merge_columns(row, cols)
    prev = row.get("extraction") or {}
    changes["extraction"] = {
        **ext.model_dump(mode="json"), "reader": reader, "warnings": warnings,
        "segment_by": prev.get("segment_by") or reader, "extracted_at": clock.now().isoformat(),
    }
    changes["extraction_confidence"] = round(min(max(ext.confidence, 0.0), 1.0), 2)
    row = store.update_inquiry(cur, inquiry_id, changes)
    row = store.update_inquiry(cur, inquiry_id, risk.compute(cur, hotel, row))
    intent = row.get("intent")
    if row["stage"] == "new" and intent in ("existing_booking", "complaint"):
        cur.execute("SAVEPOINT inq_reroute")
        try:
            row = reroute(cur, hotel, row, text)
            cur.execute("RELEASE SAVEPOINT inq_reroute")
        except Exception as exc:  # noqa: BLE001 - Service down must not lose the inquiry: a person routes it
            cur.execute("ROLLBACK TO SAVEPOINT inq_reroute")
            cur.execute("RELEASE SAVEPOINT inq_reroute")
            warnings.append(f"reroute failed: {type(exc).__name__}")
    elif row["stage"] in ("new", "replied") and intent in ("vendor", "spam"):
        row = service.transition(cur, row, "spam", reason=f"intent:{intent}", hotel=hotel)
    if row["stage"] == "new":
        sla.arm(cur, hotel, row, settings=settings)
    return {"reader": reader, "intent": intent, "segment": row["segment"], "stage": row["stage"]}
