"""
Demand Radar rate guardrails and demand.api.price_guardrails (02 spec 4.1,
4.3; 04 spec 4.6-4.7).

Owner rows in dr_rate_guardrails set a floor and a ceiling per room type
('*' = all), month and ISO weekday (NULL = any), plus max_step_pct (default
15) for recommendations. The most specific row wins: an exact room type
beats '*', then month beats no month, then weekday beats no weekday.

price_guardrails(cur, hotel_slug, stay_date, room_type_code=None) answers
Inquiry Guard's quote builder for one night:
- source 'rate_guardrails': an owner row applies (the BAR, when known, is
  reported alongside, with bar_below_floor / bar_above_ceiling reasons);
- source 'bar_only': no row, but a BAR: floor BAR -10%, ceiling BAR +30%
  (the recommendation clip of 02 spec 4.3), rounded to 5;
- source 'fallback': neither, but indicative comp rates: floor 80% and
  ceiling 130% of the comp median. Never auto-send eligible (04 spec 4.7);
- None: nothing to go on, so the quote rate is entered by hand.
Values are Decimal strings in the hotel's currency.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import find_hotel
from clefs.core.money import minor_units, money_str, to_minor
from clefs.modules.demand import bar as bar_mod
from clefs.modules.demand import comp_rates
from clefs.modules.demand.comp_rates import normalize_code

log = logging.getLogger("clefs.demand.guardrails")

DEFAULT_STEP_PCT = Decimal("15")
BAR_ONLY_FLOOR = Decimal("0.90")
BAR_ONLY_CEILING = Decimal("1.30")
FALLBACK_FLOOR = Decimal("0.80")
FALLBACK_CEILING = Decimal("1.30")
BAR_MAX_AGE_DAYS = 7

# A recommender answers {recommended_minor, id?, status?} for a night, or
# None. The recommendation engine registers one (02 spec 4.3); until then
# `recommended` is None and callers use the BAR.
Recommender = Callable[[Any, str, date, str | None], dict | None]
_recommenders: list[Recommender] = []


def register_recommender(fn: Recommender) -> None:
    if fn not in _recommenders:
        _recommenders.append(fn)


# ─── Arithmetic (02 spec 4.3: clamp, round5, step cap) ───


def round5(minor: int, currency: str) -> int:
    """Round to the nearest 5 major units (AED 447.50 -> 450), half up."""
    step = 5 * 10 ** minor_units(currency)
    return int((Decimal(minor) / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step)


def clamp(minor: int, floor: int | None, ceiling: int | None) -> int:
    if floor is not None and minor < floor:
        return floor
    if ceiling is not None and minor > ceiling:
        return ceiling
    return minor


def pct_str(v: Any) -> str:
    """15.00 -> "15", 12.50 -> "12.5" (never "2E+1")."""
    return format(Decimal(str(v)).normalize(), "f")


def step_cap(current: int, proposed: int, max_step_pct: Decimal | float | int) -> int:
    """A single decision moves the rate at most max_step_pct from current."""
    limit = Decimal(current) * Decimal(str(max_step_pct)) / 100
    lo, hi = int((Decimal(current) - limit).to_integral_value(ROUND_HALF_UP)), \
        int((Decimal(current) + limit).to_integral_value(ROUND_HALF_UP))
    return max(lo, min(hi, proposed))


# ─── Guardrail rows ───


def _row_out(r: dict) -> dict:
    return {"id": r["id"], "room_type_code": r["room_type_code"], "month": r["month"], "dow": r["dow"],
            "floor": money_str(r["floor_minor"], r["currency"]),
            "ceiling": money_str(r["ceiling_minor"], r["currency"]),
            "currency": r["currency"], "max_step_pct": pct_str(r["max_step_pct"]), "note": r["note"],
            "updated_at": r["updated_at"].isoformat()}


def list_rows(cur: Any, hotel_slug: str) -> list[dict]:
    rows = clefs_db.fetch_all(
        """SELECT * FROM dr_rate_guardrails WHERE hotel_slug = %s
            ORDER BY room_type_code, COALESCE(month, 0), COALESCE(dow, 0)""", (hotel_slug,), cur=cur)
    return [_row_out(r) for r in rows]


def _parse_row(hotel: Any, i: int, r: dict) -> tuple:
    def bad(msg: str) -> ClefsError:
        return ClefsError(400, f"Row {i}: {msg}", "invalid_guardrail")

    try:
        code = normalize_code(r.get("room_type_code") or "*")
        floor = to_minor(Decimal(str(r["floor"])), hotel.currency)
        ceiling = to_minor(Decimal(str(r["ceiling"])), hotel.currency)
        step = Decimal(str(r.get("max_step_pct") if r.get("max_step_pct") is not None else DEFAULT_STEP_PCT))
    except ClefsError as exc:
        raise bad(exc.detail) from exc
    except Exception as exc:
        raise bad("floor and ceiling must be amounts") from exc
    month, dow = r.get("month"), r.get("dow")
    if month is not None and not (isinstance(month, int) and 1 <= month <= 12):
        raise bad("month is 1-12 or null")
    if dow is not None and not (isinstance(dow, int) and 1 <= dow <= 7):
        raise bad("dow is an ISO weekday 1-7 or null")
    if floor <= 0 or ceiling < floor:
        raise bad("the floor must be above zero and at most the ceiling")
    if not Decimal(0) < step <= 100:
        raise bad("max_step_pct is above 0 and at most 100")
    ccy = str(r.get("currency") or hotel.currency).upper()
    if ccy != str(hotel.currency).upper():
        raise bad(f"amounts are in {hotel.currency}")
    note = (str(r.get("note") or "").strip()[:300]) or None
    return code, month, dow, floor, ceiling, ccy, step, note


def upsert_rows(cur: Any, hotel: Any, rows: list[dict], *, user_id: int | None) -> list[dict]:
    """Insert or replace rows by scope (room type, month, weekday). Every
    row is validated before any is written."""
    if not rows or len(rows) > 200:
        raise ClefsError(400, "Send 1-200 guardrail rows", "invalid_guardrail")
    parsed = [_parse_row(hotel, i, r) for i, r in enumerate(rows)]
    for code, month, dow, floor, ceiling, ccy, step, note in parsed:
        cur.execute(
            """INSERT INTO dr_rate_guardrails (hotel_slug, room_type_code, month, dow, floor_minor, ceiling_minor,
                                               currency, max_step_pct, note, updated_by, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
               ON CONFLICT (hotel_slug, room_type_code, (COALESCE(month, 0)), (COALESCE(dow, 0))) DO UPDATE SET
                   floor_minor = EXCLUDED.floor_minor, ceiling_minor = EXCLUDED.ceiling_minor,
                   currency = EXCLUDED.currency, max_step_pct = EXCLUDED.max_step_pct, note = EXCLUDED.note,
                   updated_by = EXCLUDED.updated_by, updated_at = now()""",
            (hotel.slug, code, month, dow, floor, ceiling, ccy, step, note, user_id))
    return list_rows(cur, hotel.slug)


def delete_row(cur: Any, hotel_slug: str, row_id: int) -> dict:
    cur.execute("DELETE FROM dr_rate_guardrails WHERE hotel_slug = %s AND id = %s RETURNING *", (hotel_slug, row_id))
    r = cur.fetchone()
    if r is None:
        raise ClefsError(404, "No such guardrail", "guardrail_not_found")
    return _row_out(r)


def resolve(cur: Any, hotel_slug: str, stay_date: date, room_type_code: str | None) -> dict | None:
    """The most specific row for the night (room type > month > weekday)."""
    return clefs_db.fetch_one(
        """SELECT * FROM dr_rate_guardrails
            WHERE hotel_slug = %s AND (room_type_code = '*' OR room_type_code = %s)
              AND (month IS NULL OR month = %s) AND (dow IS NULL OR dow = %s)
            ORDER BY (room_type_code <> '*') DESC, (month IS NOT NULL) DESC, (dow IS NOT NULL) DESC
            LIMIT 1""",
        (hotel_slug, room_type_code or "*", stay_date.month, stay_date.isoweekday()), cur=cur)


# ─── price_guardrails ───


def _demand_index(cur: Any, hotel_slug: str, stay_date: date) -> int | None:
    from clefs.modules.demand import api as demand_api

    try:
        return demand_api.demand_index(cur, hotel_slug, stay_date=stay_date)
    except Exception:  # a scoring failure must not take the quote's guardrails with it
        log.exception("demand_index failed for %s %s", hotel_slug, stay_date)
        return None


def _recommended(cur: Any, hotel_slug: str, stay_date: date, code: str | None) -> dict | None:
    for fn in _recommenders:
        try:
            rec = fn(cur, hotel_slug, stay_date, code)
        except Exception:
            log.exception("recommender failed for %s %s", hotel_slug, stay_date)
            continue
        if rec and rec.get("recommended_minor"):
            return rec
    return None


def _comp_median(cur: Any, hotel_slug: str, stay_date: date) -> tuple[int | None, dict]:
    rows = comp_rates.latest_rows(cur, hotel_slug, stay_date, stay_date)
    s = comp_rates.night_summary(rows)
    return s["median_minor"], {"median": s["median"], "priced": s["priced"], "sold_out": s["sold_out"],
                               "indicative": True}


def price_guardrails(cur: Any, hotel_slug: str, stay_date: date, room_type_code: str | None = None) -> dict | None:
    hotel = find_hotel(cur, hotel_slug)
    if hotel is None:
        return None
    ccy = str(hotel.currency).upper()
    try:
        code = normalize_code(room_type_code) if room_type_code else bar_mod.reference_room_type(cur, hotel_slug)
    except ClefsError:
        return None
    row = resolve(cur, hotel_slug, stay_date, code)
    bar = bar_mod.get_bar(cur, hotel_slug, stay_date, code) if code else None
    bar_minor = bar["bar_minor"] if bar else None
    median, comp = _comp_median(cur, hotel_slug, stay_date)
    reasons: list[str] = []
    step = DEFAULT_STEP_PCT
    if row is not None:
        source, floor, ceiling, step = "rate_guardrails", row["floor_minor"], row["ceiling_minor"], row["max_step_pct"]
        confidence = Decimal("0.9") if bar else Decimal("0.6")
    elif bar_minor:
        source, confidence = "bar_only", Decimal("0.6")
        floor = round5(int(bar_minor * BAR_ONLY_FLOOR), ccy)
        ceiling = round5(int(bar_minor * BAR_ONLY_CEILING), ccy)
        reasons.append("no_guardrails_configured")
    elif median:
        source, confidence = "fallback", Decimal("0.3")
        floor = round5(int(median * FALLBACK_FLOOR), ccy)
        ceiling = max(floor, round5(int(median * FALLBACK_CEILING), ccy))
        reasons += ["no_guardrails_configured", "from_indicative_comp_rates"]
    else:
        return None
    if bar is None:
        reasons.append("no_bar")
    else:
        age = clock.now() - datetime.fromisoformat(bar["captured_at"])
        if age > timedelta(days=BAR_MAX_AGE_DAYS):
            reasons.append("bar_stale")
            confidence -= Decimal("0.2")
        if bar_minor < floor:
            reasons.append("bar_below_floor")
        elif bar_minor > ceiling:
            reasons.append("bar_above_ceiling")
    rec = _recommended(cur, hotel_slug, stay_date, code)
    recommended = clamp(int(rec["recommended_minor"]), floor, ceiling) if rec else None
    return {
        "stay_date": stay_date.isoformat(), "room_type_code": code,
        "floor": money_str(floor, ccy), "ceiling": money_str(ceiling, ccy),
        "recommended": money_str(recommended, ccy), "recommendation_id": (rec or {}).get("id"),
        "currency": ccy, "confidence": float(max(Decimal("0.1"), confidence)), "reasons": reasons,
        "bar": money_str(bar_minor, ccy), "bar_captured_at": bar["captured_at"] if bar else None,
        "bar_source": bar["source"] if bar else None, "source": source,
        "demand_index": _demand_index(cur, hotel_slug, stay_date),
        "max_step_pct": pct_str(step), "guardrail_id": row["id"] if row else None,
        "comp": comp,
    }
