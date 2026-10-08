"""
Quote builder (04 spec 4.6, 4.7): nightly rates inside Demand's guardrails,
Dubai tax lines through core.tax.compose_stay, validity, deposit,
cancellation and group terms, and the auto-send rule.

A quote is a numbered version of an inquiry (inq_quotes). Building one never
sends anything: `request_send` creates the `inquiries.send_quote` action and
the autonomy dial decides whether it goes out on its own (only at or above
BAR with no discount, 4.7) or waits for approval.

Money is minor units of the hotel's currency throughout; the API shape adds
Decimal strings (`*_value`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from clefs.core import clock, money
from clefs.core.errors import ClefsError, not_found
from clefs.core.hotels import Hotel
from clefs.db import Json
from clefs.modules.inquiries import store

# ─── Constants (4.6, 4.7; assumptions A11, A12) ───

VALIDITY = {
    "fit": timedelta(hours=48),
    "unknown": timedelta(hours=48),
    "corporate": timedelta(hours=72),
    "long_stay": timedelta(hours=72),
    "group": timedelta(days=7),
    "event": timedelta(days=7),
}
HIGH_DEMAND_INDEX = 80  # A11: any night this hot shortens validity to 24 h
HIGH_DEMAND_VALIDITY = timedelta(hours=24)
OPEN_QUOTE_STATUSES = ("draft", "awaiting_approval", "approved", "sent")
SENDABLE_STATUSES = ("draft", "awaiting_approval", "approved")
AUTO_SEND_MIN_CONFIDENCE = Decimal("0.8")

TD_DEPOSIT_NOTE = {
    "en": "Tourism Dirham fee: refundable if you do not check in.",
    "ar": "رسوم درهم السياحة: تُسترد إذا لم تسجل الوصول.",
}
DEFAULT_CANCELLATION = {
    "en": "Free cancellation until 48 hours before arrival. Later cancellations and no-shows are charged the "
          "first night.",
    "ar": "إلغاء مجاني حتى 48 ساعة قبل الوصول. يُحتسب ثمن الليلة الأولى عند الإلغاء المتأخر أو عدم الحضور.",
}


# ─── Calls into other modules ───


def guarded(cur: Any, fn: Any, default: Any = None) -> Any:
    """Run another module's api call inside a savepoint: a lane that is off,
    unmigrated or broken degrades to `default` (no guardrails, no comp
    snapshot, no commission) instead of aborting the caller's transaction."""
    import logging

    cur.execute("SAVEPOINT inq_guard")
    try:
        out = fn()
    except ClefsError:
        cur.execute("ROLLBACK TO SAVEPOINT inq_guard")
        raise
    except Exception:  # noqa: BLE001 - any failure of a foreign module degrades
        cur.execute("ROLLBACK TO SAVEPOINT inq_guard")
        logging.getLogger(__name__).warning("inquiries: foreign api call failed", exc_info=True)
        return default
    cur.execute("RELEASE SAVEPOINT inq_guard")
    return out


# ─── Settings (hotel_module_settings['inquiries'].settings['quotes']) ───


class DepositMilestone(BaseModel):
    pct: Decimal = Field(gt=0, le=100)  # cumulative share of the total
    days_before_arrival: int | None = None  # None = at signing


class QuoteSettings(BaseModel):
    """Quote policy. Group and long-stay discounts make a quote below BAR, so
    it always needs approval (4.7). The group terms and deposit milestones
    are "Dubai norms UNVERIFIED" (A12): the owner confirms them."""

    group_min_rooms: int = Field(9, ge=2)
    group_discount_pct: Decimal = Field(Decimal("10"), ge=0, le=50)
    long_stay_min_nights: int = Field(30, ge=7)
    long_stay_discount_pct: Decimal = Field(Decimal("15"), ge=0, le=50)
    group_deposits: list[DepositMilestone] = Field(default_factory=lambda: [
        DepositMilestone(pct=Decimal("25")),
        DepositMilestone(pct=Decimal("50"), days_before_arrival=60),
        DepositMilestone(pct=Decimal("75"), days_before_arrival=30),
    ])
    group_cutoff_days: int = Field(30, ge=0, le=365)
    group_attrition_pct: Decimal = Field(Decimal("80"), ge=0, le=100)
    group_norms_label: str = "Dubai norms UNVERIFIED"
    cancellation: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_CANCELLATION))
    inclusions: dict[str, str] = Field(default_factory=dict)  # {"en": "Breakfast included", "ar": ...}
    # Empty = the deployment's own web origin (settings.web_base_url), so a
    # local or staging stack never mails links to production (QA OWN-09).
    public_base_url: str = ""


def quote_settings(cur: Any, hotel_slug: str) -> QuoteSettings:
    """Stored under the `quotes` key of the module's settings row, read raw
    (InquirySettings.quotes keeps it as a plain dict across PUT /settings)."""
    cur.execute(
        "SELECT settings->'quotes' AS q FROM hotel_module_settings WHERE hotel_slug = %s AND module = 'inquiries'",
        (hotel_slug,),
    )
    row = cur.fetchone()
    try:
        return QuoteSettings.model_validate((row or {}).get("q") or {})
    except ValidationError:
        return QuoteSettings()


def save_quote_settings(cur: Any, hotel_slug: str, patch: dict, *, actor: Any) -> QuoteSettings:
    """Merge `patch` into the stored quote settings (422 invalid_settings)."""
    from clefs.audit import audit

    merged = {**quote_settings(cur, hotel_slug).model_dump(mode="json"), **(patch or {})}
    try:
        clean = QuoteSettings.model_validate(merged)
    except ValidationError as exc:
        raise ClefsError(422, str(exc.errors()[0].get("msg")), "invalid_settings") from exc
    data = clean.model_dump(mode="json")
    cur.execute(
        """
        INSERT INTO hotel_module_settings (hotel_slug, module, enabled, settings, updated_at)
        VALUES (%s, 'inquiries', TRUE, jsonb_build_object('quotes', %s::jsonb), NOW())
        ON CONFLICT (hotel_slug, module) DO UPDATE
          SET settings = COALESCE(hotel_module_settings.settings, '{}'::jsonb) || jsonb_build_object('quotes', %s::jsonb),
              updated_at = NOW()
        """,
        (hotel_slug, json.dumps(data), json.dumps(data)),
    )
    audit(cur, actor, "inquiries.quote_settings", "hotel", hotel_slug, after=data, hotel_slug=hotel_slug)
    return clean


class SendQuoteLimits(BaseModel):
    """The dial limits of `inquiries.send_quote` (4.7). Auto-send also needs
    every night at or above a fresh BAR and no discount, whatever these say."""

    segments: list[str] = Field(default_factory=lambda: ["fit", "corporate"])
    max_rooms: int = Field(2, ge=1)
    max_nights: int = Field(7, ge=1)
    max_total_minor: int = Field(500000, ge=0)
    max_bar_age_days: int = Field(7, ge=0)


# ─── Nightly rates (4.6 step 1) ───


@dataclass(frozen=True)
class RoomRequest:
    room_type_code: str | None
    rooms: int


def stay_dates(check_in: date, check_out: date) -> list[date]:
    return [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]


def _to_minor(cur: Any, hotel: Hotel, value: Any, ccy: str | None) -> int | None:
    """A guardrail value (Decimal string) in the hotel's currency."""
    if value is None or value == "":
        return None
    src = (ccy or hotel.currency).upper()
    minor = money.to_minor(Decimal(str(value)), src)
    if src != hotel.currency:
        from clefs.core.time import local_date

        minor = money.convert_minor(cur, minor, src, hotel.currency, local_date(hotel))
    return int(minor)


def _pct_off(amount: int, pct: Decimal) -> int:
    return int((Decimal(amount) * (Decimal(100) - pct) / Decimal(100)).quantize(Decimal(1), ROUND_HALF_UP))


def policy_discount(settings: QuoteSettings, *, rooms: int, nights: int, segment: str) -> tuple[Decimal, str | None]:
    """(pct, policy) off BAR: group first (9+ rooms), then long stay (30+
    nights). A discount always means approval (below_bar, 4.7)."""
    if rooms >= settings.group_min_rooms and settings.group_discount_pct > 0:
        return settings.group_discount_pct, "group"
    if nights >= settings.long_stay_min_nights and settings.long_stay_discount_pct > 0:
        return settings.long_stay_discount_pct, "long_stay"
    return Decimal(0), None


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))


def price_lines(
    cur: Any, hotel: Hotel, *, check_in: date, check_out: date, rooms: list[RoomRequest], discount_pct: Decimal,
) -> tuple[list[dict], dict]:
    """One line per night and room type, priced from demand.api.price_guardrails:
    recommended (else BAR), less any policy discount off BAR, clamped to
    [floor, ceiling]. A night Demand has no view of is left empty with
    source='manual' (staff enter it; no clamp; sending needs approval).
    Returns (lines, guardrails snapshot keyed "date:type")."""
    from clefs.modules.demand import api as demand_api

    lines: list[dict] = []
    snapshot: dict[str, Any] = {}
    for d in stay_dates(check_in, check_out):
        for req in rooms:
            g = guarded(cur, lambda d=d, req=req: demand_api.price_guardrails(cur, hotel.slug, d, req.room_type_code))
            snapshot[f"{d.isoformat()}:{req.room_type_code or ''}"] = _jsonable(g) if g else None
            line = {"stay_date": d.isoformat(), "room_type_code": req.room_type_code, "rooms": int(req.rooms),
                    "rate_minor": None, "bar_minor": None, "floor_minor": None, "ceiling_minor": None,
                    "discount_pct": "0", "source": "manual", "demand_index": None, "bar_captured_at": None,
                    "guardrails_source": None}
            if g:
                ccy = g.get("currency")
                bar = _to_minor(cur, hotel, g.get("bar"), ccy)
                floor = _to_minor(cur, hotel, g.get("floor"), ccy)
                ceiling = _to_minor(cur, hotel, g.get("ceiling"), ccy)
                rec = _to_minor(cur, hotel, g.get("recommended"), ccy)
                rate = rec if rec is not None else bar
                if rate is not None and discount_pct > 0 and bar is not None:
                    rate = _pct_off(bar, discount_pct)
                if rate is not None:
                    rate = clamp(rate, floor, ceiling)
                line.update(rate_minor=rate, bar_minor=bar, floor_minor=floor, ceiling_minor=ceiling,
                            discount_pct=str(discount_pct if discount_pct > 0 and bar is not None else 0),
                            source="guardrails" if rate is not None else "manual",
                            demand_index=g.get("demand_index"), bar_captured_at=_iso(g.get("bar_captured_at")),
                            guardrails_source=g.get("source"))
            lines.append(line)
    return lines, snapshot


def clamp(rate: int, floor: int | None, ceiling: int | None) -> int:
    if floor is not None and rate < floor:
        rate = floor
    if ceiling is not None and rate > ceiling:
        rate = ceiling
    return rate


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


# ─── Staff edits (4.6 step 1) ───


def apply_edits(lines: list[dict], edits: list[dict], *, can_approve: bool, reason: str | None) -> list[dict]:
    """Staff rates, each {stay_date, room_type_code?, rate_minor}. Bounded to
    [floor, ceiling]: above the ceiling is refused; below the floor needs
    inquiries.approve plus a reason. A night without guardrails takes any
    positive rate (it stays source='manual')."""
    by_key = {(ln["stay_date"], ln["room_type_code"]): ln for ln in lines}
    for e in edits or []:
        key = (str(e.get("stay_date")), e.get("room_type_code"))
        if key not in by_key and len({k[1] for k in by_key}) == 1:
            key = (key[0], next(iter(by_key))[1])  # one room type: the code may be omitted
        ln = by_key.get(key)
        if ln is None:
            raise ClefsError(400, f"no quote line for {key[0]} {key[1] or ''}".strip(), "unknown_line")
        rate = int(e["rate_minor"])
        if rate <= 0:
            raise ClefsError(400, "a rate must be positive", "invalid_rate")
        if ln["ceiling_minor"] is not None and rate > ln["ceiling_minor"]:
            raise ClefsError(400, "rate above the Demand ceiling", "above_ceiling")
        if ln["floor_minor"] is not None and rate < ln["floor_minor"]:
            if not can_approve:
                raise ClefsError(403, "a rate below the floor needs inquiries.approve", "below_floor_needs_approval")
            if not (reason or "").strip():
                raise ClefsError(400, "a rate below the floor needs a reason", "reason_required")
            ln["below_floor_reason"] = reason.strip()[:500]
        ln["rate_minor"] = rate
        if ln["source"] != "manual":
            ln["source"] = "staff"
        bar = ln.get("bar_minor")
        ln["discount_pct"] = str(_discount_of(rate, bar)) if bar else ln.get("discount_pct", "0")
    return lines


def _discount_of(rate: int, bar: int) -> Decimal:
    if rate >= bar:
        return Decimal(0)
    return ((Decimal(bar - rate) * 100) / Decimal(bar)).quantize(Decimal("0.01"), ROUND_HALF_UP)


# ─── Taxes (4.6 step 2) ───


def compose(cur: Any, hotel_slug: str, lines: list[dict]):
    """core.tax.compose_stay over every line in ONE call (a mixed group is
    one stay, 4.6 golden). A night still missing its rate counts as 0 here;
    the quote is marked rates_missing and cannot be sent."""
    from clefs.core.tax import StayLine, compose_stay

    stay = [StayLine(date.fromisoformat(ln["stay_date"]), ln["room_type_code"], int(ln["rooms"]),
                     int(ln["rate_minor"] or 0)) for ln in lines]
    return compose_stay(cur, hotel_slug, lines=stay)


_APPLIED = "(applied at the owner's request)"


def stay_notices(cur: Any, hotel_slug: str, lines: list[dict], notices: list[str] | tuple[str, ...]) -> list[str]:
    """compose_stay's banner lines, reworded to the nights actually quoted.
    WHY: core.tax words an announced rule "applied at the owner's request"
    whenever the hotel has an override row, even when the override's own
    window misses the stay (a one-night September exemption printed on a
    December quote that charges the Tourism Dirham, QA OWN-09). The rule's
    own active() test, per quoted night, decides what the guest reads."""
    from clefs.core.tax import load_context

    nights = sorted({date.fromisoformat(ln["stay_date"]) for ln in lines})
    out = list(notices)
    if not nights or not any(_APPLIED in n for n in out):
        return out
    ctx = load_context(cur, hotel_slug, nights[0], nights[-1])
    for r in ctx.unverified_intersecting(nights[0], nights[-1]):
        if r.get("ov_apply") is not True:
            continue
        on = sum(1 for d in nights if ctx.active(r, d, ctx.category(d)))
        if on == len(nights):
            continue
        state = "(not applied to these dates)" if on == 0 else \
            f"(applied at the owner's request on {on} of {len(nights)} nights)"
        prefix = f"{r['label']}: announced, not yet verified {_APPLIED}"
        out = [n.replace(_APPLIED, state, 1) if n.startswith(prefix) else n for n in out]
    return out


def first_nights(lines: list[dict], n: int) -> list[dict]:
    dates = sorted({ln["stay_date"] for ln in lines})[:n]
    return [ln for ln in lines if ln["stay_date"] in dates]


# ─── Validity (4.6 step 3) ───


def valid_until(hotel: Hotel, *, segment: str, check_in: date, lines: list[dict], now: datetime | None = None) -> datetime:
    """FIT 48 h, corporate 72 h, group 7 d; 24 h when any night's demand
    index is 80+ (A11); never later than the end of the day before arrival
    (hotel time). A same-day quote still gets one hour to be read."""
    now = now or clock.now()
    until = now + VALIDITY.get(segment, VALIDITY["unknown"])
    if any((ln.get("demand_index") or 0) >= HIGH_DEMAND_INDEX for ln in lines):
        until = min(until, now + HIGH_DEMAND_VALIDITY)
    cap = datetime.combine(check_in, datetime.min.time(), tzinfo=hotel.tz)  # 00:00 on arrival day
    until = min(until, cap)
    return max(until, now + timedelta(hours=1))


# ─── Deposit schedule (4.6 step 4) ───


def deposit_schedule(
    cur: Any, hotel: Hotel, *, segment: str, lines: list[dict], total_minor: int, td_minor: int,
    check_in: date, settings: QuoteSettings, is_group: bool,
) -> list[dict]:
    """Milestones in order, each {seq, purpose, label, due_on, amount_minor,
    includes_td_minor, td_note}. The last one is the balance (due at
    arrival). A deposit that includes TD says it is refundable on a no-show:
    a Hotel Room Night starts at check-in, so a no-show owes no TD."""
    out: list[dict] = []
    if is_group:
        paid = 0
        paid_td = 0
        for m in settings.group_deposits:
            cum = int((Decimal(total_minor) * m.pct / 100).quantize(Decimal(1), ROUND_HALF_UP))
            cum_td = int((Decimal(td_minor) * m.pct / 100).quantize(Decimal(1), ROUND_HALF_UP))
            if cum <= paid:
                continue
            due = None if m.days_before_arrival is None else (check_in - timedelta(days=m.days_before_arrival))
            if due is not None and due <= clock.now().astimezone(hotel.tz).date():
                due = None  # already inside the window: due at signing
            out.append({"purpose": "deposit", "label": f"{pct_str(m.pct)}% deposit",
                        "due_on": due.isoformat() if due else None, "amount_minor": cum - paid,
                        "includes_td_minor": cum_td - paid_td, "cumulative_pct": pct_str(m.pct)})
            paid, paid_td = cum, cum_td
    else:
        n = 30 if segment == "long_stay" else 1
        part = compose(cur, hotel.slug, first_nights(lines, n))
        if part.total < total_minor:
            label = "First 30 nights" if n == 30 else "First night"
            out.append({"purpose": "deposit", "label": label, "due_on": None, "amount_minor": part.total,
                        "includes_td_minor": part.td})
        paid, paid_td = (part.total, part.td) if part.total < total_minor else (0, 0)
    if not out:
        out.append({"purpose": "full", "label": "Full payment", "due_on": None, "amount_minor": total_minor,
                    "includes_td_minor": td_minor})
    elif total_minor - paid > 0:
        out.append({"purpose": "balance", "label": "Balance", "due_on": check_in.isoformat(),
                    "amount_minor": total_minor - paid, "includes_td_minor": td_minor - paid_td})
    for i, m in enumerate(out, start=1):
        m["seq"] = i
        m["td_note"] = TD_DEPOSIT_NOTE if m["includes_td_minor"] and m["purpose"] != "balance" else None
    return out


def milestones(q: dict) -> list[dict]:
    """The stored schedule in the shape deposit_schedule() writes, whatever
    the row holds. WHY: a row written by hand (the demo seed once stored
    [{"pct": 30, "due": "on_confirmation"}] and [{"pct": "first_night"}])
    has no amount_minor, and to_api indexed it: GET /quotes 500'd and the
    Quote tab died. A numeric pct becomes that share of the total;
    "first_night" is total / nights (exact for a flat rate, a display
    fallback otherwise); anything else is the full total."""
    total, td = int(q.get("total_minor") or 0), int(q.get("td_minor") or 0)
    nights = max(len({ln.get("stay_date") for ln in (q.get("lines") or [])}), 1)
    out: list[dict] = []
    for i, raw in enumerate(q.get("deposit_schedule") or [], start=1):
        m = dict(raw) if isinstance(raw, dict) else {}
        pct = m.get("pct") if m.get("pct") is not None else m.get("cumulative_pct")
        try:
            share = Decimal(str(pct)) / 100 if pct is not None else None
        except (ArithmeticError, ValueError):
            share = None
        if share is None:
            share = Decimal(1) / nights if str(pct) == "first_night" else Decimal(1)
        if m.get("amount_minor") is None:
            m["amount_minor"] = int((Decimal(total) * share).quantize(Decimal(1), ROUND_HALF_UP))
        if m.get("includes_td_minor") is None:
            m["includes_td_minor"] = int((Decimal(td) * share).quantize(Decimal(1), ROUND_HALF_UP))
        m.setdefault("seq", i)
        m.setdefault("purpose", "deposit")
        m.setdefault("label", "First night" if str(pct) == "first_night" else
                     f"{pct_str(share * 100)}% deposit")
        m.setdefault("due_on", None)
        m.setdefault("td_note", None)
        out.append(m)
    return out


# ─── Group terms (4.6 step 6) ───


def group_terms(settings: QuoteSettings, *, check_in: date, rooms: int) -> dict:
    cutoff = check_in - timedelta(days=settings.group_cutoff_days)
    return {
        "cutoff_date": cutoff.isoformat(),
        "attrition_pct": pct_str(settings.group_attrition_pct),
        "min_rooms_after_attrition": int((Decimal(rooms) * settings.group_attrition_pct / 100).to_integral_value(
            rounding="ROUND_CEILING")),
        "rooming_list_due": cutoff.isoformat(),
        "label": settings.group_norms_label,
    }


def stored_group_terms(q: dict) -> dict | None:
    """The stored group terms when they have group_terms()'s shape, else
    None: both pages format cutoff_date and rooming_list_due as dates, and a
    hand-written row without them broke the render."""
    g = q.get("group_terms")
    if not isinstance(g, dict) or not all(g.get(k) for k in ("cutoff_date", "rooming_list_due")):
        return None
    return g


# ─── Auto-send rule (4.7) ───


def send_limits(cur: Any, hotel_slug: str) -> SendQuoteLimits:
    """The hotel's dial limits for inquiries.send_quote, else the defaults."""
    from clefs.core import actions

    if actions.get_action_type("inquiries.send_quote") is None:
        return SendQuoteLimits()
    raw = (actions.get_autonomy(cur, hotel_slug, "inquiries.send_quote") or {}).get("limits")
    try:
        return SendQuoteLimits.model_validate(raw or {})
    except ValidationError:
        return SendQuoteLimits()


def auto_send_reasons(
    inquiry: dict, lines: list[dict], *, total_minor: int, limits: SendQuoteLimits, now: datetime | None = None,
) -> list[str]:
    """Every reason the quote may NOT go out on its own; [] = eligible.

    - every night at or above a BAR at most max_bar_age_days old;
    - no discount line; guardrails present and not `fallback`;
    - segment, rooms, nights and total within the dial's limits;
    - extraction confidence >= 0.8 and nothing missing."""
    now = now or clock.now()
    reasons: list[str] = []
    if any(ln["rate_minor"] is None for ln in lines):
        reasons.append("rates_missing")
    if any(ln["source"] == "manual" for ln in lines):
        reasons.append("no_guardrails")
    if any(ln.get("guardrails_source") == "fallback" for ln in lines):
        reasons.append("fallback_guardrails")
    if any(ln["bar_minor"] is not None and ln["rate_minor"] is not None and ln["rate_minor"] < ln["bar_minor"]
           for ln in lines):
        reasons.append("below_bar")
    if any(ln["bar_minor"] is None for ln in lines) and "no_guardrails" not in reasons:
        reasons.append("no_bar")
    if any(Decimal(str(ln.get("discount_pct") or 0)) > 0 for ln in lines):
        reasons.append("discount")
    oldest = now - timedelta(days=limits.max_bar_age_days)
    for ln in lines:
        at = ln.get("bar_captured_at")
        if ln["bar_minor"] is not None and (not at or datetime.fromisoformat(at) < oldest):
            reasons.append("bar_stale")
            break
    rooms = sum(ln["rooms"] for ln in lines if ln["stay_date"] == lines[0]["stay_date"]) if lines else 0
    nights = len({ln["stay_date"] for ln in lines})
    if (inquiry.get("segment") or "unknown") not in limits.segments:
        reasons.append("segment_not_allowed")
    if rooms > limits.max_rooms:
        reasons.append("too_many_rooms")
    if nights > limits.max_nights:
        reasons.append("too_many_nights")
    if total_minor > limits.max_total_minor:
        reasons.append("total_over_limit")
    conf = inquiry.get("extraction_confidence")
    if conf is None or Decimal(str(conf)) < AUTO_SEND_MIN_CONFIDENCE:
        reasons.append("low_confidence")
    if ((inquiry.get("extraction") or {}).get("missing") or []):
        reasons.append("missing_fields")
    return reasons


def below_bar(lines: list[dict]) -> bool:
    """Unknown BAR counts as below (no guardrails, 4.6)."""
    return any(ln["bar_minor"] is None or ln["rate_minor"] is None or ln["rate_minor"] < ln["bar_minor"]
               or Decimal(str(ln.get("discount_pct") or 0)) > 0 for ln in lines)


def max_discount(lines: list[dict]) -> Decimal:
    best = Decimal(0)
    for ln in lines:
        best = max(best, Decimal(str(ln.get("discount_pct") or 0)))
        if ln["bar_minor"] and ln["rate_minor"] is not None:
            best = max(best, _discount_of(ln["rate_minor"], ln["bar_minor"]))
    return best


# ─── Assembly and storage ───

_JSON_COLS = ("lines", "fee_notices", "deposit_schedule", "cancellation_terms", "group_terms", "inclusions",
              "pricing_trace", "guardrails_snapshot", "comp_snapshot")


def _assemble(cur: Any, hotel: Hotel, inquiry: dict, lines: list[dict], *, settings: QuoteSettings,
              policy: str | None, snapshot: dict, language: str) -> dict:
    """Every derived column of a quote from its lines."""
    from clefs.modules.demand import api as demand_api

    b = compose(cur, hotel.slug, lines)
    check_in = date.fromisoformat(lines[0]["stay_date"])
    check_out = date.fromisoformat(max(ln["stay_date"] for ln in lines)) + timedelta(days=1)
    rooms = sum(ln["rooms"] for ln in lines if ln["stay_date"] == lines[0]["stay_date"])
    segment = inquiry.get("segment") or "unknown"
    is_group = segment == "group" or rooms >= settings.group_min_rooms
    dates = stay_dates(check_in, check_out)
    comp = guarded(cur, lambda: demand_api.comp_rates_snapshot(cur, hotel.slug, stay_dates=dates), {})
    reasons = auto_send_reasons(inquiry, lines, total_minor=b.total, limits=send_limits(cur, hotel.slug))
    trace = {"policy": policy, "nights": len(dates), "rooms": rooms, "breakdown": b.as_dict(),
             "manual_nights": sorted({ln["stay_date"] for ln in lines if ln["source"] == "manual"}),
             "computed_at": clock.now().isoformat()}
    return {
        "lines": lines, "room_minor": b.room, "sc_minor": b.sc, "mf_minor": b.mf, "vat_minor": b.vat,
        "td_minor": b.td, "td_nights_charged": b.td_nights_charged, "td_units": b.td_units,
        "total_minor": b.total, "currency": b.currency or hotel.currency, "tax_convention": b.convention,
        "tax_rule_ids": [int(r["id"]) for r in b.fee_rules if r.get("id") is not None],
        "fee_notices": stay_notices(cur, hotel.slug, lines, b.notices), "below_bar": below_bar(lines),
        "max_discount_pct": max_discount(lines),
        "valid_until": valid_until(hotel, segment=segment, check_in=check_in, lines=lines),
        "deposit_schedule": deposit_schedule(cur, hotel, segment=segment, lines=lines, total_minor=b.total,
                                             td_minor=b.td, check_in=check_in, settings=settings, is_group=is_group),
        "cancellation_terms": dict(settings.cancellation),
        "group_terms": group_terms(settings, check_in=check_in, rooms=rooms) if is_group else None,
        "inclusions": dict(settings.inclusions) or None, "language": language,
        "pricing_trace": _jsonable(trace), "guardrails_snapshot": snapshot, "comp_snapshot": _jsonable(comp) or None,
        "auto_send_eligible": not reasons, "auto_send_reasons": reasons,
    }


def _values(cols: dict) -> list[Any]:
    return [Json(_jsonable(v)) if k in _JSON_COLS and v is not None else v for k, v in cols.items()]


def build(
    cur: Any, hotel: Hotel, inquiry: dict, *, rooms: list[RoomRequest] | None = None, edits: list[dict] | None = None,
    reason: str | None = None, can_approve: bool = False, language: str | None = None, actor: Any = None,
) -> dict:
    """Price, tax and store a new draft version of the inquiry's quote. Older
    open versions are superseded (their pending send action cancelled)."""
    if inquiry["stage"] in ("confirmed_verified", "turned_down", "spam", "rerouted"):
        raise ClefsError(409, f"cannot quote an inquiry that is {inquiry['stage']}", "illegal_stage")
    check_in, check_out = inquiry.get("check_in"), inquiry.get("check_out")
    if not check_in or not check_out or check_out <= check_in:
        raise ClefsError(400, "set check-in and check-out before quoting", "dates_required")
    rooms = rooms or [RoomRequest(inquiry.get("room_type_code"), int(inquiry.get("rooms") or 1))]
    if any(r.rooms < 1 for r in rooms):
        raise ClefsError(400, "rooms must be at least 1", "invalid_rooms")
    settings = quote_settings(cur, hotel.slug)
    nights = (check_out - check_in).days
    pct, policy = policy_discount(settings, rooms=sum(r.rooms for r in rooms), nights=nights,
                                  segment=inquiry.get("segment") or "unknown")
    lines, snapshot = price_lines(cur, hotel, check_in=check_in, check_out=check_out, rooms=rooms, discount_pct=pct)
    lines = apply_edits(lines, edits or [], can_approve=can_approve, reason=reason)
    lang = language or ("ar" if inquiry.get("language") == "ar" else "en")
    cols = _assemble(cur, hotel, inquiry, lines, settings=settings, policy=policy, snapshot=snapshot, language=lang)
    cur.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM inq_quotes WHERE inquiry_id = %s", (inquiry["id"],))
    version = int(cur.fetchone()["v"])
    supersede_open(cur, inquiry["id"], reason=f"v{version}")
    user_id = getattr(getattr(actor, "principal", actor), "user_id", None)
    cols = {"hotel_slug": hotel.slug, "inquiry_id": inquiry["id"], "version": version, "status": "draft",
            "created_by": user_id, "created_at": clock.now(), **cols}
    cur.execute(
        f"INSERT INTO inq_quotes ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING *",
        _values(cols),
    )
    return dict(cur.fetchone())


def _cancel_action(cur: Any, action_id: int | None, reason: str) -> None:
    """Cancel a still-open send action (a superseded or edited quote must
    never go out); a finished one is left alone."""
    from clefs.core import actions

    if not action_id:
        return
    cur.execute("SELECT status FROM actions WHERE id = %s", (action_id,))
    row = cur.fetchone()
    if row and row["status"] in ("proposed", "approved", "assigned", "in_progress", "snoozed"):
        actions.cancel(cur, int(action_id), reason=reason)


def supersede_open(cur: Any, inquiry_id: int, *, reason: str) -> int:
    cur.execute(
        """
        UPDATE inq_quotes SET status = 'superseded' WHERE inquiry_id = %s AND status = ANY(%s)
        RETURNING id, action_id
        """,
        (inquiry_id, list(OPEN_QUOTE_STATUSES)),
    )
    rows = cur.fetchall()
    for r in rows:
        _cancel_action(cur, r["action_id"], f"quote_superseded:{reason}")
    return len(rows)


def get_quote(cur: Any, hotel_slug: str, inquiry_id: int, version: int, *, lock: bool = False) -> dict:
    cur.execute(
        f"SELECT * FROM inq_quotes WHERE hotel_slug = %s AND inquiry_id = %s AND version = %s{' FOR UPDATE' if lock else ''}",
        (hotel_slug, inquiry_id, version),
    )
    row = cur.fetchone()
    if row is None:
        raise not_found("quote not found")
    return dict(row)


def quote_by_id(cur: Any, quote_id: int, *, lock: bool = False) -> dict:
    cur.execute(f"SELECT * FROM inq_quotes WHERE id = %s{' FOR UPDATE' if lock else ''}", (quote_id,))
    row = cur.fetchone()
    if row is None:
        raise not_found("quote not found")
    return dict(row)


def list_quotes(cur: Any, inquiry_id: int) -> list[dict]:
    cur.execute("SELECT * FROM inq_quotes WHERE inquiry_id = %s ORDER BY version DESC", (inquiry_id,))
    return [dict(r) for r in cur.fetchall()]


def revise(
    cur: Any, hotel: Hotel, quote: dict, *, edits: list[dict] | None, reason: str | None, can_approve: bool,
    language: str | None = None,
) -> dict:
    """Edit a quote that has not gone out (draft, awaiting_approval, approved):
    the totals and the auto-send reasons are recomputed and a pending send
    action is cancelled (an edited quote needs a fresh decision). A sent
    quote is locked: build a new version instead (409 quote_locked)."""
    if quote["status"] not in SENDABLE_STATUSES:
        raise ClefsError(409, "this quote was sent or closed; create a new version", "quote_locked")
    inquiry = store.get_inquiry(cur, hotel.slug, quote["inquiry_id"])
    lines = apply_edits([dict(ln) for ln in quote["lines"]], edits or [], can_approve=can_approve, reason=reason)
    policy = (quote.get("pricing_trace") or {}).get("policy")
    cols = _assemble(cur, hotel, inquiry, lines, settings=quote_settings(cur, hotel.slug), policy=policy,
                     snapshot=quote["guardrails_snapshot"], language=language or quote["language"])
    _cancel_action(cur, quote.get("action_id"), "quote_edited")
    cols.update(status="draft", action_id=None)
    sets = ", ".join(f"{k} = %s" for k in cols)
    cur.execute(f"UPDATE inq_quotes SET {sets} WHERE id = %s RETURNING *", (*_values(cols), quote["id"]))
    return dict(cur.fetchone())


# ─── API shape ───

_MONEY_COLS = ("room_minor", "sc_minor", "mf_minor", "vat_minor", "td_minor", "total_minor")


def to_api(q: dict) -> dict:
    """Row -> JSON: money as Decimal strings next to the minor units; the
    token hash never leaves the server."""
    out = {k: v for k, v in q.items() if k != "public_token_hash"}
    ccy = q["currency"]
    for k in _MONEY_COLS:
        out[k.replace("_minor", "_value")] = money.money_str(q[k], ccy)
    out["max_discount_pct"] = str(q.get("max_discount_pct") or "0")
    out["deposit_schedule"] = milestones(q)
    out["group_terms"] = stored_group_terms(q)
    for m in out["deposit_schedule"]:
        m["amount_value"] = money.money_str(m["amount_minor"], ccy)
    for ln in out.get("lines") or []:
        for k in ("rate_minor", "bar_minor", "floor_minor", "ceiling_minor"):
            ln[k.replace("_minor", "_value")] = money.money_str(ln[k], ccy) if ln.get(k) is not None else None
    out["has_public_link"] = q.get("public_token_hash") is not None
    return out


def pct_str(d: Decimal) -> str:
    """80 not 8E+1: normalize() alone switches to exponent form."""
    return format(Decimal(d).normalize(), "f")


# ─── Requesting a send (4.7) ───

SEND_TYPE = "inquiries.send_quote"


def _check_sendable(q: dict) -> None:
    if q["status"] not in SENDABLE_STATUSES:
        raise ClefsError(409, f"this quote is {q['status']}", "quote_locked")
    if any(ln["rate_minor"] is None for ln in q["lines"]):
        raise ClefsError(409, "enter every night's rate first", "rates_missing")
    if q["valid_until"] <= clock.now():
        raise ClefsError(409, "the quote's validity has passed; create a new version", "quote_expired")


def verify_until_for(hotel: Hotel, inquiry: dict) -> datetime:
    """check_out + 7 d (4.9): a stay_completed match months later still
    finds a live action."""
    end = (inquiry.get("check_out") or clock.now().astimezone(hotel.tz).date()) + timedelta(days=7)
    return datetime.combine(end, datetime.max.time().replace(microsecond=0), tzinfo=hotel.tz)


def request_send(cur: Any, hotel: Hotel, quote: dict, *, actor: Any, via: str = "console",
                 approve_now: bool = False) -> dict:
    """Create the `inquiries.send_quote` action; the dial decides (4.7). A
    person holding inquiries.approve who presses Send (`approve_now`)
    approves it at once, which is a human decision. Returns the action."""
    from clefs.core import actions

    _check_sendable(quote)
    inquiry = store.get_inquiry(cur, hotel.slug, quote["inquiry_id"])
    nights = len({ln["stay_date"] for ln in quote["lines"]})
    rooms = sum(ln["rooms"] for ln in quote["lines"] if ln["stay_date"] == quote["lines"][0]["stay_date"])
    params = {"inquiry_id": inquiry["id"], "quote_id": quote["id"], "version": quote["version"],
              "segment": inquiry.get("segment"), "rooms": rooms, "nights": nights,
              "total_minor": quote["total_minor"], "below_bar": quote["below_bar"],
              "auto_send_eligible": quote["auto_send_eligible"], "reasons": list(quote["auto_send_reasons"])}
    title = f"Send quote {inquiry['ref'].upper()}-v{quote['version']}: {money.fmt_money(money.from_minor(quote['total_minor'], quote['currency']), quote['currency'])}"
    stamp = (quote.get("pricing_trace") or {}).get("computed_at") or ""
    act = actions.create_action(
        cur, hotel.slug, type=SEND_TYPE, title=title, params=params, expected_minor=quote["total_minor"],
        currency=quote["currency"], owner_role="revenue", dedupe_key=f"inq.quote:{quote['id']}:{stamp}",
        actor=actor, via=via if via in ("console", "whatsapp", "email", "api", "system") else "console",
        expires_at=quote["valid_until"], verify_until=verify_until_for(hotel, inquiry),
    )
    if act.get("id") is None:
        raise ClefsError(409, "quote sending is switched off for this hotel", "dial_off")
    if act["status"] == "proposed" and approve_now:
        act = actions.decide(cur, act["id"], "approve", actor=actor, via="console", hotel_slug=hotel.slug)
    status = {"proposed": "awaiting_approval", "approved": "approved"}.get(act["status"], quote["status"])
    if quote["status"] in SENDABLE_STATUSES:
        cur.execute("UPDATE inq_quotes SET action_id = %s, status = CASE WHEN status = 'sent' THEN status ELSE %s END "
                    "WHERE id = %s", (act["id"], status, quote["id"]))
    if status == "awaiting_approval":
        from clefs.modules.inquiries import signals

        signals.upsert_awaiting_approval(cur, hotel, inquiry, quote, action_id=act["id"])
    return act


# ─── Delivery (4.6 step 7) ───


def public_token(quote: dict) -> str:
    """The quote page token: HMAC(SECRET_KEY, hotel:id:created_at), so a
    follow-up or an email fallback can link the same page again, while only
    its sha256 is stored (public_token_hash) for the lookup."""
    import base64
    import hashlib
    import hmac

    from clefs.settings import get_settings

    msg = f"inq-quote:{quote['hotel_slug']}:{quote['id']}:{quote['created_at'].isoformat()}".encode()
    mac = hmac.new(get_settings().SECRET_KEY.encode(), msg, hashlib.sha256).digest()[:24]
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def mint_token(cur: Any, quote: dict) -> str:
    from clefs.auth.tokens import hash_token

    token = public_token(quote)
    cur.execute("UPDATE inq_quotes SET public_token_hash = %s WHERE id = %s", (hash_token(token), quote["id"]))
    return token


def quote_url(cur: Any, hotel_slug: str, token: str) -> str:
    """The guest's /q link: the hotel's own base when set, else this
    deployment's web origin (WEB_ORIGINS[0]; www.clefs.world in production)."""
    from clefs.settings import get_settings

    base = quote_settings(cur, hotel_slug).public_base_url or get_settings().web_base_url
    return f"{base.rstrip('/')}/q/{token}"


def stay_label(quote: dict, lang: str) -> str:
    dates = sorted({ln["stay_date"] for ln in quote["lines"]})
    first = date.fromisoformat(dates[0])
    last = date.fromisoformat(dates[-1]) + timedelta(days=1)
    rooms = sum(ln["rooms"] for ln in quote["lines"] if ln["stay_date"] == dates[0])
    if lang == "ar":
        return f"{first.isoformat()} إلى {last.isoformat()}، {rooms} غرفة"
    return f"{first.strftime('%d %b')} to {last.strftime('%d %b %Y')}, {rooms} room{'s' if rooms != 1 else ''}"


def money_label(minor: int, ccy: str) -> str:
    return money.fmt_money(money.from_minor(minor, ccy), ccy)


@dataclass(frozen=True)
class Delivery:
    ok: bool
    channel: str | None = None
    outbox_id: int | None = None
    error: str | None = None
    template: str | None = None


def deliver(
    cur: Any, hotel: Hotel, inquiry: dict, *, purpose: str, template: str, params: list[str], token: str,
    text: str, subject: str, action_id: int | None, key: str, deadline: datetime | None,
) -> Delivery:
    """One guest message about a quote: free-form WhatsApp inside the 24 h
    window, else the template through the category gate (templates.plan_send),
    else email. Returns where it went; ok=False with `template_missing` when
    no channel can carry it (4.6)."""
    import html as _html

    from clefs import notify
    from clefs.modules.inquiries import templates

    clear = store.unsealed(inquiry)
    phone, email = clear["phone"], clear["email"]
    lang = "ar" if inquiry.get("language") == "ar" else "en"
    ph = inquiry.get("contact_phone_hmac")
    if phone and ph and notify.wa_window_open_until(cur, hotel.slug, sender_hmac=bytes(ph)):
        r = notify.send_whatsapp_text(phone, text, hotel_slug=hotel.slug, purpose=purpose, sender="hotel",
                                      preview_url=True, idempotency_key=f"{key}:wa", action_id=action_id, cur=cur)
        if r.ok:
            return Delivery(True, "whatsapp", r.outbox_id)
    plan = templates.plan_send(cur, hotel.slug, name=template, language=lang, phone=phone, email=email,
                               deadline=deadline)
    if plan.channel == "whatsapp":
        windowed = plan.category != "UTILITY" or template == templates.FOLLOWUP
        r = notify.send_whatsapp_template(
            phone, template, lang, params, hotel_slug=hotel.slug, purpose=purpose, sender="hotel",
            buttons=[{"type": "url", "text": token}], category="marketing" if windowed else "utility",
            idempotency_key=f"{key}:tpl", action_id=action_id, cur=cur,
        )
        if r.ok:
            return Delivery(True, "whatsapp", r.outbox_id, template=template)
    if email:
        reply_to = notify.hotel_inbound_address(cur, hotel.slug, "inquiries", inquiry["ref"])
        body = _html.escape(text).replace("\n", "<br>")
        r = notify.send_email(email, subject, f"<p>{body}</p>", text=text, reply_to=reply_to, hotel_slug=hotel.slug,
                              purpose=purpose, category="utility", idempotency_key=f"{key}:email",
                              action_id=action_id, cur=cur)
        if r.ok:
            return Delivery(True, "email", r.outbox_id)
    return Delivery(False, error=plan.reason or "no_reachable_channel")


SUBJECT = {"en": "Your quote {ref} from {hotel}", "ar": "عرض السعر {ref} من {hotel}"}
VIEW_LINE = {"en": "View it here: {url}", "ar": "اطلع عليه هنا: {url}"}


def valid_label(hotel: Hotel, at: datetime) -> str:
    return at.astimezone(hotel.tz).strftime("%d %b %Y %H:%M")


def quote_message(cur: Any, hotel: Hotel, inquiry: dict, quote: dict, token: str, *, name: str) -> tuple[list[str], str, str]:
    """(template params, free text, email subject) for a quote-ready or
    follow-up message, in the quote's language."""
    from clefs.modules.inquiries import templates

    lang = "ar" if quote["language"] == "ar" else "en"
    ref = f"{inquiry['ref'].upper()}-v{quote['version']}"
    stay = stay_label(quote, lang)
    until = valid_label(hotel, quote["valid_until"])
    if name == templates.QUOTE_READY:
        params = [ref, stay, money_label(quote["total_minor"], quote["currency"]), until]
    else:
        params = [ref, stay, until]
    text = templates.render(name, lang, params) + "\n" + VIEW_LINE[lang].format(url=quote_url(cur, hotel.slug, token))
    return params, text, SUBJECT[lang].format(ref=ref, hotel=hotel.name)


def _decided_by_person(ctx: Any) -> bool:
    return ctx.action.get("decided_via") not in (None, "dial") and ctx.action.get("decided_by") is not None


def execute_send(ctx: Any) -> Any:
    """Executor of inquiries.send_quote: re-checks the quote, delivers it,
    marks it sent and moves the inquiry to `quoted` (a quote is also a first
    response: service.on_outbound)."""
    from clefs.core.actions import ExecResult
    from clefs.modules.inquiries import service, templates

    cur, hotel = ctx.cur, ctx.hotel
    q = quote_by_id(cur, int(ctx.params["quote_id"]), lock=True)
    if q["status"] not in SENDABLE_STATUSES:
        return ExecResult(ok=False, error=f"quote_{q['status']}")
    if q["valid_until"] <= clock.now():
        return ExecResult(ok=False, error="quote_expired")
    inquiry = store.get_inquiry(cur, hotel.slug, q["inquiry_id"], lock=True)
    token = mint_token(cur, q)
    params, text, subject = quote_message(cur, hotel, inquiry, q, token, name=templates.QUOTE_READY)
    d = deliver(cur, hotel, inquiry, purpose="inquiries.quote_ready", template=templates.QUOTE_READY, params=params,
                token=token, text=text, subject=subject, action_id=ctx.action_id,
                key=f"inq.quote:{hotel.slug}:{q['id']}:{ctx.action_id}", deadline=q["valid_until"])
    if not d.ok:
        return ExecResult(ok=False, error=d.error or "template_missing")
    now = clock.now()
    cur.execute("UPDATE inq_quotes SET status = 'sent', sent_at = %s, action_id = %s WHERE id = %s",
                (now, ctx.action_id, q["id"]))
    by = "staff" if _decided_by_person(ctx) else "ai"
    store.add_message(cur, inquiry, direction="out", channel=d.channel, body=text, author_type=by,
                      provider_message_id=f"quote:{d.outbox_id}", outbound_message_id=d.outbox_id,
                      template_name=d.template, action_id=ctx.action_id,
                      author_user_id=ctx.action.get("decided_by") if by == "staff" else None)
    service.on_outbound(cur, inquiry, by=by, at=now, hotel=hotel)
    if inquiry["stage"] in ("new", "replied", "payment_pending"):
        service.transition(cur, inquiry, "quoted", reason=f"quote_sent:v{q['version']}", hotel=hotel,
                           data={"quote_id": q["id"], "action_id": ctx.action_id})
    from clefs.modules.inquiries import signals

    signals.resolve_awaiting_approval(cur, hotel.slug, q["id"])
    tpl = templates.latest(cur, hotel.slug, templates.QUOTE_READY, q["language"]) if d.template else None
    return ExecResult(ok=True, data={"channel": d.channel, "outbox_id": d.outbox_id, "quote_id": q["id"]},
                      template_id=f"{d.template}@{tpl['version']}" if tpl else None,
                      template_category="MARKETING" if d.template else None,
                      consent_basis=f"inquiry:{inquiry['consent_basis']}")


# ─── Follow-ups (4.10) ───

FOLLOWUP_AFTER = (timedelta(hours=24), timedelta(hours=72))


def propose_follow_ups(cur: Any, hotel: Hotel) -> int:
    """Propose `inquiries.follow_up` at sent + 24 h and + 72 h while the quote
    is still valid and the guest has not written since it went out."""
    from clefs.core import actions

    now = clock.now()
    cur.execute(
        """
        SELECT q.*, i.ref, i.last_inbound_at FROM inq_quotes q JOIN inq_inquiries i ON i.id = q.inquiry_id
        WHERE q.hotel_slug = %s AND q.status = 'sent' AND q.valid_until > %s AND i.stage = 'quoted'
          AND q.sent_at <= %s AND (i.last_inbound_at IS NULL OR i.last_inbound_at < q.sent_at)
        """,
        (hotel.slug, now, now - FOLLOWUP_AFTER[0]),
    )
    n = 0
    for q in [dict(r) for r in cur.fetchall()]:
        for i, after in enumerate(FOLLOWUP_AFTER, start=1):
            if q["sent_at"] + after > now or q["sent_at"] + after >= q["valid_until"]:
                continue
            act = actions.create_action(
                cur, hotel.slug, type="inquiries.follow_up",
                title=f"Follow up quote {q['ref'].upper()}-v{q['version']} ({i} of 2)",
                params={"inquiry_id": q["inquiry_id"], "quote_id": q["id"], "n": i}, owner_role="revenue",
                dedupe_key=f"inq.follow_up:{q['id']}:{i}", expires_at=q["valid_until"],
            )
            n += 1 if act.get("created") else 0
    return n


def execute_follow_up(ctx: Any) -> Any:
    """Free text inside the window, else inq_quote_followup_v1, always timed
    inside [09:00, 18:00) Dubai, with the 4.6 category gate."""
    from clefs.core.actions import ExecResult
    from clefs.modules.inquiries import templates

    cur, hotel = ctx.cur, ctx.hotel
    q = quote_by_id(cur, int(ctx.params["quote_id"]))
    if q["status"] != "sent" or q["valid_until"] <= clock.now():
        return ExecResult(ok=False, error=f"quote_{q['status'] if q['status'] != 'sent' else 'expired'}")
    inquiry = store.get_inquiry(cur, hotel.slug, q["inquiry_id"], lock=True)
    if inquiry.get("last_inbound_at") and q["sent_at"] and inquiry["last_inbound_at"] > q["sent_at"]:
        return ExecResult(ok=True, data={"skipped": "guest_replied"})
    token = public_token(q)
    params, text, subject = quote_message(cur, hotel, inquiry, q, token, name=templates.FOLLOWUP)
    d = deliver(cur, hotel, inquiry, purpose="inquiries.follow_up", template=templates.FOLLOWUP, params=params,
                token=token, text=text, subject=subject, action_id=ctx.action_id,
                key=f"inq.follow_up:{hotel.slug}:{q['id']}:{ctx.params.get('n')}", deadline=q["valid_until"])
    if not d.ok:
        return ExecResult(ok=False, error=d.error or "template_missing")
    by = "staff" if _decided_by_person(ctx) else "ai"
    store.add_message(cur, inquiry, direction="out", channel=d.channel, body=text, author_type=by,
                      provider_message_id=f"followup:{d.outbox_id}", outbound_message_id=d.outbox_id,
                      template_name=d.template, action_id=ctx.action_id)
    return ExecResult(ok=True, data={"channel": d.channel, "outbox_id": d.outbox_id, "n": ctx.params.get("n")},
                      template_category="MARKETING" if d.template else None)


def email_fallback_for_outbox(cur: Any, row: dict, *, reason: str) -> bool:
    """Re-send a failed quote or follow-up template by email (131049 after
    its one retry). The outbox row's action tells which quote it was."""
    from clefs import notify
    from clefs.core.hotels import get_hotel
    from clefs.modules.inquiries import templates

    if not row.get("action_id"):
        return False
    cur.execute("SELECT params, hotel_slug FROM actions WHERE id = %s", (row["action_id"],))
    act = cur.fetchone()
    if not act or not (act["params"] or {}).get("quote_id"):
        return False
    hotel = get_hotel(cur, act["hotel_slug"])
    q = quote_by_id(cur, int(act["params"]["quote_id"]))
    inquiry = store.get_inquiry(cur, hotel.slug, q["inquiry_id"])
    email = store.unsealed(inquiry)["email"]
    if not email:
        return False
    name = templates.FOLLOWUP if row.get("template_name") == templates.FOLLOWUP else templates.QUOTE_READY
    _params, text, subject = quote_message(cur, hotel, inquiry, q, public_token(q), name=name)
    import html as _html

    r = notify.send_email(email, subject, f"<p>{_html.escape(text)}</p>", text=text, hotel_slug=hotel.slug,
                          purpose=row["purpose"],
                          category="utility", idempotency_key=f"inq.fallback:{row['id']}:{reason}",
                          action_id=row["action_id"], cur=cur)
    return bool(r.ok)


# ─── Public quote page (/q/[token], Canon K4) ───


def by_token(cur: Any, token: str, *, lock: bool = False) -> dict:
    from clefs.auth.tokens import hash_token

    if not token or len(token) > 100:
        raise not_found("quote not found")
    cur.execute(f"SELECT * FROM inq_quotes WHERE public_token_hash = %s{' FOR UPDATE' if lock else ''}",
                (hash_token(token),))
    row = cur.fetchone()
    if row is None:
        raise not_found("quote not found")
    return dict(row)


def _room_type_names(cur: Any, hotel_slug: str) -> dict[str, str]:
    cur.execute("SELECT code, name FROM room_types WHERE hotel_slug = %s", (hotel_slug,))
    return {r["code"]: r["name"] for r in cur.fetchall()}


def public_view(cur: Any, token: str, *, language: str | None = None) -> dict:
    """What the guest sees: the stay, nightly rates, the tax lines, the
    terms. Never a contact, never BAR, floor, ceiling or the pricing trace."""
    from clefs.core.hotels import get_hotel
    from clefs.core.tax import TD_LABEL

    q = by_token(cur, token)
    hotel = get_hotel(cur, q["hotel_slug"])
    inquiry = store.get_row(cur, q["inquiry_id"])
    lang = "ar" if (language or q["language"]) == "ar" else "en"
    ccy = q["currency"]
    names = _room_type_names(cur, hotel.slug)
    dates = sorted({ln["stay_date"] for ln in q["lines"]})
    status = q["status"]
    if status == "sent" and q["valid_until"] <= clock.now():
        status = "expired"
    return {
        # timezone: /q shows valid_until in the hotel's clock (the email and
        # console do), never the viewer's (QA OWN-09).
        "hotel": {"slug": hotel.slug, "name": hotel.name, "city": hotel.city, "star_rating": hotel.star_rating,
                  "locale": hotel.locale, "timezone": hotel.timezone},
        "ref": f"{inquiry['ref'].upper()}-v{q['version']}", "version": q["version"], "status": status,
        "language": lang, "valid_until": q["valid_until"].isoformat(), "currency": ccy,
        "stay": {"check_in": dates[0], "check_out": (date.fromisoformat(dates[-1]) + timedelta(days=1)).isoformat(),
                 "nights": len(dates),
                 "rooms": sum(ln["rooms"] for ln in q["lines"] if ln["stay_date"] == dates[0])},
        "lines": [{"stay_date": ln["stay_date"], "room_type_code": ln["room_type_code"],
                   "room_type_name": names.get(ln["room_type_code"] or "", ln["room_type_code"]),
                   "rooms": ln["rooms"], "rate_value": money.money_str(ln["rate_minor"] or 0, ccy)}
                  for ln in q["lines"]],
        "taxes": {"room": money.money_str(q["room_minor"], ccy), "service_charge": money.money_str(q["sc_minor"], ccy),
                  "municipality_fee": money.money_str(q["mf_minor"], ccy), "vat": money.money_str(q["vat_minor"], ccy),
                  "tourism_dirham": money.money_str(q["td_minor"], ccy), "tourism_dirham_label": TD_LABEL,
                  "td_nights_charged": q["td_nights_charged"], "total": money.money_str(q["total_minor"], ccy)},
        "fee_notices": list(q["fee_notices"] or []),
        "deposit_schedule": [{"seq": m["seq"], "purpose": m["purpose"], "label": m["label"], "due_on": m["due_on"],
                              "amount_value": money.money_str(m["amount_minor"], ccy),
                              "includes_td_value": money.money_str(m["includes_td_minor"], ccy),
                              "td_note": (m.get("td_note") or {}).get(lang)} for m in milestones(q)],
        "cancellation": (q["cancellation_terms"] or {}).get(lang) or (q["cancellation_terms"] or {}).get("en"),
        "group_terms": stored_group_terms(q), "inclusions": (q["inclusions"] or {}).get(lang),
        "can_accept": status == "sent", "accepted": status == "accepted", "noindex": True,
    }


def accept(cur: Any, token: str) -> dict:
    """The guest accepts (4.6 step 8): quote accepted, guest engaged, and an
    `inquiries.send_payment_link` action for the first deposit milestone
    (the dial decides whether it goes out). The stage moves to
    payment_pending only when a link is actually sent (4.2)."""
    from clefs.core.actions import SYSTEM
    from clefs.core.hotels import get_hotel
    from clefs.modules.inquiries import risk
    from clefs.modules.inquiries.payments import service as pay

    q = by_token(cur, token, lock=True)
    if q["status"] == "accepted":
        return {"ok": True, "status": "accepted", "replayed": True}
    if q["status"] != "sent":
        raise ClefsError(409, f"this quote is {q['status']}", "quote_not_open")
    if q["valid_until"] <= clock.now():
        raise ClefsError(409, "this quote has expired", "quote_expired")
    hotel = get_hotel(cur, q["hotel_slug"])
    cur.execute("UPDATE inq_quotes SET status = 'accepted' WHERE id = %s", (q["id"],))
    inquiry = store.get_inquiry(cur, hotel.slug, q["inquiry_id"], lock=True)
    inquiry = store.update_inquiry(cur, inquiry["id"], {"guest_engaged": True, "last_activity_at": clock.now()})
    store.update_inquiry(cur, inquiry["id"], risk.compute(cur, hotel, inquiry))
    first = (milestones(q) or [{}])[0]
    try:
        act = pay.request_link(cur, hotel, {**q, "status": "accepted"}, purpose=first.get("purpose") or "deposit",
                               amount_minor=int(first.get("amount_minor") or q["total_minor"]), actor=SYSTEM,
                               via="system", milestone=int(first.get("seq") or 1))
    except ClefsError as exc:
        # No provider connected (or the dial is off): the acceptance stands and
        # staff send a manual link from the drawer.
        return {"ok": True, "status": "accepted", "payment_action_id": None, "payment_action_status": None,
                "payment_reason": exc.code}
    return {"ok": True, "status": "accepted", "payment_action_id": act.get("id"),
            "payment_action_status": act.get("status")}


# ─── Expiry and the lost-reason automation (4.10) ───

UNRESPONSIVE_AFTER_EXPIRY = timedelta(days=7)


def expire_due(cur: Any, hotel: Hotel) -> dict:
    """quote_expiry (every 15 min): quotes past valid_until expire; follow-ups
    are proposed; unresponsive guests are closed as lost.

    GUEST_UNRESPONSIVE is set when a quote expired after 2 follow-ups with no
    inbound, or 7 d after expiry with no inbound when fewer went out. It
    reads SLOW_RESPONSE instead when the first-response SLA was breached."""
    now = clock.now()
    cur.execute(
        """
        UPDATE inq_quotes SET status = 'expired'
        WHERE hotel_slug = %s AND status = ANY(%s) AND valid_until <= %s RETURNING id, action_id, status
        """,
        (hotel.slug, list(OPEN_QUOTE_STATUSES), now),
    )
    expired = [dict(r) for r in cur.fetchall()]
    for r in expired:
        _cancel_action(cur, r["action_id"], "quote_expired")
    proposed = propose_follow_ups(cur, hotel)
    closed = close_unresponsive(cur, hotel)
    return {"expired": len(expired), "follow_ups": proposed, "lost": closed}


def _follow_ups_sent(cur: Any, quote_id: int) -> int:
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM actions WHERE type = 'inquiries.follow_up' AND (params->>'quote_id')::bigint = %s
          AND status IN ('done', 'verified', 'not_verified')
        """,
        (quote_id,),
    )
    return int(cur.fetchone()["n"])


def close_unresponsive(cur: Any, hotel: Hotel) -> int:
    from clefs.modules.inquiries import service

    now = clock.now()
    cur.execute(
        """
        SELECT DISTINCT ON (i.id) i.*, q.id AS q_id, q.valid_until AS q_valid_until, q.sent_at AS q_sent_at
        FROM inq_inquiries i JOIN inq_quotes q ON q.inquiry_id = i.id
        WHERE i.hotel_slug = %s AND i.stage = 'quoted' AND q.status = 'expired' AND q.sent_at IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM inq_quotes o WHERE o.inquiry_id = i.id AND o.status = ANY(%s))
        ORDER BY i.id, q.version DESC
        """,
        (hotel.slug, list(OPEN_QUOTE_STATUSES) + ["accepted"]),
    )
    n = 0
    for row in [dict(r) for r in cur.fetchall()]:
        if row.get("last_inbound_at") and row["last_inbound_at"] > row["q_sent_at"]:
            continue  # the guest wrote back: a person decides
        followed = _follow_ups_sent(cur, row["q_id"])
        if followed < 2 and now < row["q_valid_until"] + UNRESPONSIVE_AFTER_EXPIRY:
            continue
        reason = "SLOW_RESPONSE" if row.get("sla_breached") else "GUEST_UNRESPONSIVE"
        inq = {k: v for k, v in row.items() if not k.startswith("q_")}
        service.transition(cur, inq, "lost", reason="auto:quote_expired", lost_reason=reason, hotel=hotel,
                           data={"quote_id": row["q_id"], "follow_ups": followed})
        n += 1
    return n
