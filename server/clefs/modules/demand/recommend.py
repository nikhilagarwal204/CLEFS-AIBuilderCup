"""
Rate recommendations (02 spec 4.3). Advice for the owner, never pushed to a
channel manager: an approved recommendation is a human task (the owner
changes the rate) and measure.py verifies it from later BAR evidence.

    d_raw = 0.10 (DI - 50) / 50 + 0.40 pace_gap + 0.5 comp_move   (comp_move capped +/-20%)
    d     = clip(d_raw, -10%, +30%), then within +/- max_step_pct (default 15)
    rec   = clamp(round_to_5(BAR x (1 + d)), floor, ceiling)
    emit only if |rec - BAR| >= max(AED 15, 3% of BAR)
    confidence = 0.4 event_conf + 0.3 data_coverage + 0.3 calibration_fit
                 (capped at 0.6 while the driving class runs on global priors)
    impact = min(capacity, OTB + expected_pickup) x (rec - BAR) x (1 - commission_share)
             an "estimated opportunity", never a promise

Levers: DI >= 80 with lead <= 21 days and OTB >= 70% closes discounted
plans, sets min-stay 2 for a multi-night event, trims OTA allotment by
ceil(10% of capacity) and pushes a direct offer; DI <= 35 suggests a
pre-arrival offer and reopening closed plans, never below the floor.

Lifecycle: PROPOSED -> APPROVED | ADJUSTED | REJECTED (owner), SUPERSEDED
(DI moved more than 15 points, or a driving event changed), EXPIRED (the
stay date arrived undecided), APPLIED (BAR evidence within +/- 2%).
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs import ai
from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.money import fmt_money, minor_units, money_str
from clefs.core.time import local_date
from clefs.db import Json
from clefs.modules.demand import bar as bar_mod
from clefs.modules.demand import demand_index as di_mod
from clefs.modules.demand import guardrails, relevance as rel

log = logging.getLogger("clefs.demand.recommend")

# ─── Constants ───

DI_WEIGHT, PACE_WEIGHT, COMP_WEIGHT = 0.10, 0.40, 0.5
COMP_MOVE_CAP = 0.20
DELTA_MIN, DELTA_MAX = -0.10, 0.30
MIN_MOVE_MAJOR = 15          # AED 15 (in the hotel's currency's major units)
MIN_MOVE_PCT = 0.03
SUPERSEDE_DI_POINTS = 15
PRIOR_CONF_CAP = rel.PRIOR_CONFIDENCE_CAP
DEFAULT_COMMISSION_SHARE = 0.12  # when neither the owner nor Profit Guard's channel economics say
HORIZON_DAYS = 90
MAX_LLM_PER_RUN = 20
OPEN = ("PROPOSED",)
DECIDED = ("APPROVED", "ADJUSTED")
TERMINAL = ("REJECTED", "SUPERSEDED", "EXPIRED", "APPLIED")
BANNED_PHRASES = ("will increase", "guaranteed", "guarantee", "will boost", "will raise", "will drive",
                  "will fill", "certain to", "definitely", "ensures", "will sell out", "will generate")

HIGH_DI, LOW_DI, HIGH_LEAD_DAYS, HIGH_OTB = 80, 35, 21, 0.70
EM_DASH = chr(0x2014)  # house style bans it in owner-facing text


# ─── Arithmetic (pure) ───


def delta(di: int, pace_gap: float | None, comp_move: float | None, *, max_step_pct: float) -> dict:
    cm = max(-COMP_MOVE_CAP, min(COMP_MOVE_CAP, comp_move or 0.0))
    raw = DI_WEIGHT * (di - 50) / 50 + PACE_WEIGHT * (pace_gap or 0.0) + COMP_WEIGHT * cm
    d = max(DELTA_MIN, min(DELTA_MAX, raw))
    step = float(max_step_pct) / 100
    capped = max(-step, min(step, d))
    return {"raw": round(raw, 4), "clipped": round(d, 4), "delta": round(capped, 4), "comp_move": round(cm, 4),
            "step_capped": capped != d}


def price(bar_minor: int, d: float, *, floor: int | None, ceiling: int | None, max_step_pct: float,
          currency: str) -> int:
    """clamp(round5(BAR x (1 + d)), floor, ceiling). Rounding may not carry
    the move past the step cap, so an over-rounded price steps back by 5;
    the floor and ceiling still win over the step."""
    unit = 5 * 10 ** minor_units(currency)
    rec = guardrails.round5(int((Decimal(bar_minor) * (1 + Decimal(str(d)))).to_integral_value(ROUND_HALF_UP)),
                            currency)
    limit = Decimal(bar_minor) * Decimal(str(max_step_pct)) / 100
    while rec - bar_minor > limit:
        rec -= unit
    while bar_minor - rec > limit:
        rec += unit
    return guardrails.clamp(rec, floor, ceiling)


def worth_emitting(bar_minor: int, rec_minor: int, currency: str) -> bool:
    threshold = max(MIN_MOVE_MAJOR * 10 ** minor_units(currency), int(math.ceil(bar_minor * MIN_MOVE_PCT)))
    return abs(rec_minor - bar_minor) >= threshold


def confidence(event_conf: float, coverage: float, calibration_fit: float, *, prior: bool) -> float:
    c = 0.4 * event_conf + 0.3 * coverage + 0.3 * calibration_fit
    return round(min(c, PRIOR_CONF_CAP) if prior else c, 3)


def impact(capacity: int, otb: int, expected_pickup: float, rec_minor: int, bar_minor: int,
           commission_share: float) -> int:
    rooms = min(capacity, otb + expected_pickup)
    return int(round(rooms * (rec_minor - bar_minor) * (1 - commission_share)))


def levers(di: int, *, lead_days: int, otb_share: float | None, event_nights: int, capacity: int) -> list[dict]:
    out: list[dict] = []
    if di >= HIGH_DI and lead_days <= HIGH_LEAD_DAYS and (otb_share or 0) >= HIGH_OTB:
        out.append({"code": "close_discounts", "label": "Close discounted plans (Early Booker, Mobile, deep Genius tiers)"})
        if event_nights >= 2:
            out.append({"code": "min_stay_2", "label": "Set a 2-night minimum stay"})
        out.append({"code": "reduce_ota_allotment", "label": f"Reduce OTA allotment by {math.ceil(0.1 * capacity)} rooms",
                    "rooms": math.ceil(0.1 * capacity)})
        out.append({"code": "direct_offer", "label": "Push a direct-booking offer"})
    elif di <= LOW_DI:
        out.append({"code": "pre_arrival_offer", "label": "Send a pre-arrival offer to booked guests"})
        out.append({"code": "open_closed_plans", "label": "Reopen closed rate plans (never below the floor)"})
    return out


# ─── "Why this matters" (structured facts in, one factual sentence out) ───

WHY_SYSTEM = (
    "You write one short sentence (max 45 words) for a hotel owner explaining why a rate suggestion for one night "
    "is worth a look. Use ONLY the facts given: events (name, distance), competitor price move, pace versus "
    "comparable nights, demand index. Describe what is observed, never predict outcomes. Never write causal or "
    "promissory claims such as 'will increase', 'will boost', 'will fill', 'guaranteed' or 'definitely'. "
    "Say 'indicative' for competitor prices. No emojis, no exclamation marks."
)


def lint_why(text: str) -> list[str]:
    """Banned causal phrases (02 spec 4.3). Returns the hits; empty = clean."""
    low = (text or "").lower()
    hits = [p for p in BANNED_PHRASES if p in low]
    if EM_DASH in (text or ""):
        hits.append("em dash")
    return hits


def facts_for(night: dict, *, currency: str, bar_minor: int, rec_minor: int) -> dict:
    comp = (night["components"].get("comp") or {})
    pace = (night["components"].get("pace") or {})
    return {
        "stay_date": night["stay_date"], "demand_index": night["demand_index"],
        "current_bar": money_str(bar_minor, currency), "suggested": money_str(rec_minor, currency), "currency": currency,
        "events": [{"name": e["title"], "category": e["category"],
                    "distance_km": e.get("distance_km")} for e in night["components"].get("events", [])[:3]],
        "market_holidays": night["components"].get("market_holidays", [])[:3],
        "comp_move_pct": None if comp.get("comp_move") is None else round(100 * comp["comp_move"], 1),
        "comps_sold_out": comp.get("sold_out"), "comps": comp.get("comps"),
        "pace_gap_pct": None if pace.get("pace_gap") is None else round(100 * pace["pace_gap"], 1),
    }


def template_why(f: dict) -> str:
    parts = []
    if f["events"]:
        e = f["events"][0]
        where = f" about {e['distance_km']:.0f} km away" if e.get("distance_km") is not None else ""
        parts.append(f"{e['name']} runs{where} on these dates")
    elif f["market_holidays"]:
        parts.append(f"{f['market_holidays'][0]} falls on this night")
    if f["comp_move_pct"] is not None and abs(f["comp_move_pct"]) >= 3:
        parts.append(f"indicative comp prices are {abs(f['comp_move_pct']):.0f}% "
                     f"{'above' if f['comp_move_pct'] > 0 else 'below'} comparable nights")
    if f.get("comps_sold_out"):
        parts.append(f"{f['comps_sold_out']} of {f['comps']} comps show sold out")
    if f["pace_gap_pct"] is not None and abs(f["pace_gap_pct"]) >= 2:
        parts.append(f"bookings are {abs(f['pace_gap_pct']):.0f} points of capacity "
                     f"{'ahead of' if f['pace_gap_pct'] > 0 else 'behind'} comparable nights")
    if not parts:
        parts.append(f"the demand index for this night reads {f['demand_index']} of 100")
    s = "; ".join(parts)
    return s[0].upper() + s[1:] + "."


def why_this_matters(f: dict, *, hotel_slug: str, use_llm: bool = True) -> tuple[str, str]:
    """(sentence, 'llm'|'template'). The LLM sees the structured facts only;
    anything that trips the lint (or any AI failure) falls back to the
    deterministic template, which is lint-clean by construction."""
    if use_llm:
        try:
            out = ai.text("demand.why_this_matters", system=WHY_SYSTEM, input=json.dumps(f, default=str),
                          hotel_slug=hotel_slug, max_output_tokens=200).strip()
            out = re.sub(r"\s+", " ", out)[:400]
            if out and not lint_why(out):
                return out, "llm"
            if out:
                log.warning("why-this-matters failed the lint: %s", lint_why(out))
        except Exception:  # noqa: BLE001  (AIDisabled, AIError or a transport error: the template stands in)
            pass
    return template_why(f), "template"


# ─── Context shared by one run ───


def commission_share(cur: Any, hotel: Any, *, today: date) -> tuple[float, str]:
    """The owner's figure, else Profit Guard's trailing-90-day commission
    over gross (channel_economics), else a conservative default."""
    s = rel.get_settings(cur, hotel.slug)
    if s.get("commission_share") is not None:
        return float(s["commission_share"]), "settings"
    from clefs.modules.demand.actions import safe_call
    from clefs.modules.profit_guard import api as pg_api

    rows = safe_call(cur, pg_api.channel_economics, cur, hotel.slug, start=today - timedelta(days=90),
                     end=today - timedelta(days=1), default=[]) or []
    try:
        gross = sum(Decimal(str(r.get("gross") or 0)) for r in rows)
        comm = sum(Decimal(str(r.get("commission") or 0)) for r in rows)
    except (ArithmeticError, ValueError, AttributeError):
        gross = comm = Decimal(0)
    if gross > 0:
        return float(min(Decimal("0.5"), comm / gross)), "channel_economics"
    return DEFAULT_COMMISSION_SHARE, "default"


def bounds_for(cur: Any, hotel_slug: str, stay_date: date, room_type: str, bar_minor: int,
               currency: str) -> dict:
    """The owner's guardrail row, else BAR -10% / +30% (the delta clip)."""
    row = guardrails.resolve(cur, hotel_slug, stay_date, room_type)
    if row is not None:
        return {"floor": int(row["floor_minor"]), "ceiling": int(row["ceiling_minor"]),
                "max_step_pct": float(row["max_step_pct"]), "source": "rate_guardrails", "guardrail_id": row["id"]}
    return {"floor": guardrails.round5(int(bar_minor * guardrails.BAR_ONLY_FLOOR), currency),
            "ceiling": guardrails.round5(int(bar_minor * guardrails.BAR_ONLY_CEILING), currency),
            "max_step_pct": float(guardrails.DEFAULT_STEP_PCT), "source": "bar_only", "guardrail_id": None}


def _room_types(cur: Any, hotel_slug: str, start: date, end: date) -> list[str]:
    ref = None
    try:
        ref = bar_mod.reference_room_type(cur, hotel_slug)
    except ClefsError:
        ref = None
    rows = clefs_db.fetch_all("SELECT DISTINCT room_type_code FROM dr_bar_calendar WHERE hotel_slug = %s "
                              "AND stay_date BETWEEN %s AND %s", (hotel_slug, start, end), cur=cur)
    codes = sorted({r["room_type_code"] for r in rows})
    return ([ref] if ref and ref in codes else []) + [c for c in codes if c != ref]


def _event_conf(night: dict) -> tuple[float, float, bool]:
    """(event confidence, calibration fit, on priors) weighted by each
    event's night score; a night without events is neutral (0.5 / 0.3)."""
    evs = night["components"].get("events") or []
    tot = sum(e["night_score"] for e in evs)
    if not tot:
        return 0.5, 0.3, False
    conf = sum(e["confidence"] * e["night_score"] for e in evs) / tot
    fit = sum(float(e.get("calibration_fit") or 0.3) * e["night_score"] for e in evs) / tot
    prior = any(e.get("k_source") != "own" for e in evs)
    return conf, fit, prior


def expected_pickup(night: dict, capacity: int) -> float:
    """Room nights the calibrated model attaches to the night's events
    (k x night score x capacity), an estimate for the impact figure."""
    return sum(float(e.get("k") or 0.05) * e["night_score"] * capacity
               for e in night["components"].get("events") or [])


# ─── One candidate (pure over a DI night) ───


def candidate(night: dict, *, bar_minor: int, currency: str, bounds: dict, capacity: int, lead_days: int,
              comm_share: float) -> dict | None:
    """The recommendation for one night and room type, or None when the
    move is under max(AED 15, 3%)."""
    comps = night["components"]
    pace = comps.get("pace") or {}
    comp = comps.get("comp") or {}
    d = delta(night["demand_index"], pace.get("pace_gap"), comp.get("comp_move"), max_step_pct=bounds["max_step_pct"])
    rec = price(bar_minor, d["delta"], floor=bounds["floor"], ceiling=bounds["ceiling"],
                max_step_pct=bounds["max_step_pct"], currency=currency)
    if not worth_emitting(bar_minor, rec, currency):
        return None
    ev_conf, fit, prior = _event_conf(night)
    conf = confidence(ev_conf, night["coverage"], fit, prior=prior)
    otb = int(pace.get("otb") or 0)
    pickup = expected_pickup(night, capacity)
    imp = impact(capacity, otb, pickup, rec, bar_minor, comm_share)
    ev_nights = max([e.get("event_nights") or 0 for e in comps.get("events") or []] or [0])
    lv = levers(night["demand_index"], lead_days=lead_days, otb_share=(otb / capacity) if capacity else None,
                event_nights=ev_nights, capacity=capacity)
    return {
        "recommended_minor": rec, "change_pct": round(100 * (rec - bar_minor) / bar_minor, 2), "delta": d,
        "confidence": conf, "impact_minor": imp, "levers": lv, "expected_pickup": round(pickup, 2), "otb": otb,
        "driver_event_ids": [e["event_id"] for e in comps.get("events") or [] if e["night_score"] > 0][:5],
        "baseline": {"pace": pace or None, "comp": comp or None, "season": comps.get("season"),
                     "commission_share": comm_share, "capacity": capacity, "lead_days": lead_days},
    }


def rationale(night: dict, cand: dict, bounds: dict) -> str:
    """The arithmetic in one line (for the audit trail and the card)."""
    d = cand["delta"]
    return (f"DI {night['demand_index']}, pace gap {((night['components'].get('pace') or {}).get('pace_gap') or 0):+.1%}, "
            f"comp move {d['comp_move']:+.1%} -> raw {d['raw']:+.1%}, clipped {d['clipped']:+.1%}, "
            f"step-capped {d['delta']:+.1%} ({bounds['source']}, step {bounds['max_step_pct']:.0f}%)")


# ─── Persisted recommendations ───


def _latest(cur: Any, hotel_slug: str, stay_date: date, room_type: str) -> dict | None:
    return clefs_db.fetch_one(
        "SELECT * FROM dr_rate_recommendations WHERE hotel_slug = %s AND stay_date = %s AND room_type_code = %s "
        "AND status NOT IN ('SUPERSEDED', 'EXPIRED') ORDER BY created_at DESC, id DESC LIMIT 1",
        (hotel_slug, stay_date, room_type), cur=cur)


def _set_status(cur: Any, rec: dict, status: str, reason: str | None, *, cancel_action: bool = True) -> None:
    clefs_db.execute("UPDATE dr_rate_recommendations SET status = %s, status_reason = %s, updated_at = %s "
                     "WHERE id = %s", (status, reason, clock.now(), rec["id"]), cur=cur)
    if cancel_action and rec.get("action_id"):
        from clefs.core import actions as core_actions

        try:
            core_actions.cancel(cur, rec["action_id"], actor=core_actions.SYSTEM,
                                reason="expired" if status == "EXPIRED" else "superseded")
        except ClefsError:
            pass  # already terminal (expired by core, decided): nothing to cancel


def supersede(cur: Any, rec: dict, reason: str) -> None:
    _set_status(cur, rec, "SUPERSEDED", reason)


def expire_due(cur: Any, hotel_slug: str, *, today: date) -> int:
    rows = clefs_db.fetch_all("SELECT * FROM dr_rate_recommendations WHERE hotel_slug = %s AND status = 'PROPOSED' "
                              "AND stay_date < %s", (hotel_slug, today), cur=cur)
    for r in rows:
        _set_status(cur, r, "EXPIRED", "stay_date_reached")
    return len(rows)


def _create(cur: Any, hotel: Any, *, night: dict, room_type: str, bar_minor: int, currency: str, bounds: dict,
            cand: dict, why: str, why_source: str, facts: dict) -> dict:
    from clefs.core.time import local_day_bounds
    from clefs.modules.demand import actions as dr_actions

    stay = date.fromisoformat(night["stay_date"])
    row = clefs_db.fetch_one(
        """INSERT INTO dr_rate_recommendations (hotel_slug, stay_date, room_type_code, currency, current_bar_minor,
               recommended_minor, floor_minor, ceiling_minor, change_pct, levers, rationale, why, why_source, facts,
               driver_event_ids, demand_index, confidence, impact_minor, baseline)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid[], %s, %s, %s, %s) RETURNING *""",
        (hotel.slug, stay, room_type, currency, bar_minor, cand["recommended_minor"], bounds["floor"],
         bounds["ceiling"], cand["change_pct"], Json(cand["levers"]), rationale(night, cand, bounds), why, why_source,
         Json(facts), cand["driver_event_ids"], night["demand_index"], cand["confidence"], cand["impact_minor"],
         Json(cand["baseline"])), cur=cur)
    up = cand["recommended_minor"] > bar_minor
    # fmt_money groups thousands ("AED 1,360.00", QA OWN-13 read "AED 1360.00").
    title = (f"{'Raise' if up else 'Lower'} {room_type} BAR for {stay:%a %d %b} to "
             f"{fmt_money(cand['recommended_minor'], currency, getattr(hotel, 'locale', None), minor=True)}")
    # params.adjust names the ONE editable field. Without it every Adjust
    # surface (console brief, WhatsApp, magic brief) guessed "the first
    # *_minor key with bounds", and Postgres JSONB orders keys by length then
    # bytes, so the guess was current_bar_minor: the owner "adjusted" a fact
    # and the suggested rate never moved (QA OWN-01).
    act = dr_actions.create(
        cur, hotel.slug, type=dr_actions.RATE_CHANGE, title=title,
        params={"recommendation_id": row["id"], "stay_date": stay.isoformat(), "room_type_code": room_type,
                "current_bar_minor": bar_minor, "recommended_minor": cand["recommended_minor"],
                "floor_minor": bounds["floor"], "ceiling_minor": bounds["ceiling"], "currency": currency,
                "driver_event_ids": cand["driver_event_ids"], "levers": [lv["code"] for lv in cand["levers"]],
                "why": why, "adjust": dr_actions.rate_adjust_spec(bounds["floor"], bounds["ceiling"])},
        expected_minor=max(0, cand["impact_minor"]) or None, currency=currency if cand["impact_minor"] > 0 else None,
        owner_role="revenue", dedupe_key=f"dr.rate_change:{row['id']}",
        expires_at=local_day_bounds(hotel, stay)[1])
    if act.get("id"):
        clefs_db.execute("UPDATE dr_rate_recommendations SET action_id = %s WHERE id = %s", (act["id"], row["id"]),
                         cur=cur)
        row["action_id"] = act["id"]
    return row


def run(cur: Any, hotel: Any, *, today: date | None = None, days: int = HORIZON_DAYS,
        llm_budget: int = MAX_LLM_PER_RUN, nights: list[dict] | None = None) -> dict:
    """The dr.recommend pass for one hotel: expire, supersede on a DI move
    of more than 15 points, and propose where the move clears the minimum.
    `nights` are demand_index.night() dicts (computed when not given)."""
    today = today or local_date(hotel)
    ccy = str(hotel.currency).upper()
    # A PROPOSED row whose action was decided elsewhere must not be expired
    # or superseded (that would cancel the owner's approved action).
    sync_from_actions(cur, hotel.slug)
    expired = expire_due(cur, hotel.slug, today=today)
    if nights is None:
        nights = di_mod.compute(cur, hotel, start=today, days=days, today=today)
    end = today + timedelta(days=days)
    capacity = rel.capacity(cur, hotel)
    comm, _src = commission_share(cur, hotel, today=today)
    room_types = _room_types(cur, hotel.slug, today, end)
    report = {"expired": expired, "created": 0, "superseded": 0, "kept": 0, "skipped": 0, "llm": 0}
    for night in nights:
        stay = date.fromisoformat(night["stay_date"])
        for rt in room_types:
            bar = bar_mod.get_bar(cur, hotel.slug, stay, rt)
            if not bar or bar["currency"] != ccy:
                continue
            prev = _latest(cur, hotel.slug, stay, rt)
            if prev and prev["demand_index"] is not None \
                    and abs(night["demand_index"] - prev["demand_index"]) <= SUPERSEDE_DI_POINTS:
                report["kept"] += 1
                continue
            if prev and prev["status"] == "PROPOSED":
                supersede(cur, prev, "demand_index_moved")
                report["superseded"] += 1
            bounds = bounds_for(cur, hotel.slug, stay, rt, bar["bar_minor"], ccy)
            cand = candidate(night, bar_minor=bar["bar_minor"], currency=ccy, bounds=bounds, capacity=capacity,
                             lead_days=(stay - today).days, comm_share=comm)
            if cand is None:
                report["skipped"] += 1
                continue
            facts = facts_for(night, currency=ccy, bar_minor=bar["bar_minor"], rec_minor=cand["recommended_minor"])
            use_llm = report["llm"] < llm_budget
            why, src = why_this_matters(facts, hotel_slug=hotel.slug, use_llm=use_llm)
            report["llm"] += 1 if use_llm else 0
            _create(cur, hotel, night=night, room_type=rt, bar_minor=bar["bar_minor"], currency=ccy, bounds=bounds,
                    cand=cand, why=why, why_source=src, facts=facts)
            report["created"] += 1
    return report


# ─── Owner decisions ───

VERBS = ("approve", "adjust", "reject")


def get(cur: Any, hotel_slug: str, rec_id: int) -> dict:
    r = clefs_db.fetch_one("SELECT * FROM dr_rate_recommendations WHERE id = %s AND hotel_slug = %s",
                           (rec_id, hotel_slug), cur=cur)
    if r is None:
        raise ClefsError(404, "No such recommendation", "recommendation_not_found")
    return r


def decide(cur: Any, ctx: Any, rec_id: int, verb: str, *, recommended_minor: int | None = None,
           note: str | None = None) -> dict:
    """approve | adjust (a rate inside [floor, ceiling]) | reject. The linked
    core action moves with it (approve / adjust / ignore); an approval or
    adjustment teaches the driving events' classes (useful + 1)."""
    from clefs.core import actions as core_actions

    if verb not in VERBS:
        raise ClefsError(400, f"verb is one of {', '.join(VERBS)}", "invalid_verb")
    hotel_slug = ctx.hotel.slug
    sync_from_actions(cur, hotel_slug)  # decided in the brief or WhatsApp: 409 here, not a second decision
    rec = get(cur, hotel_slug, rec_id)
    if rec["status"] != "PROPOSED":
        raise ClefsError(409, f"This recommendation is {rec['status'].lower()}", "recommendation_closed")
    final = int(rec["recommended_minor"])
    if verb == "adjust":
        if recommended_minor is None:
            raise ClefsError(400, "An adjustment needs a rate", "invalid_rate")
        final = int(recommended_minor)
        if (rec["floor_minor"] is not None and final < rec["floor_minor"]) or \
                (rec["ceiling_minor"] is not None and final > rec["ceiling_minor"]):
            raise ClefsError(400, "The rate must stay between the floor and the ceiling", "rate_out_of_bounds")
    if rec["action_id"]:
        decision = {"approve": "approve", "adjust": "adjust", "reject": "ignore"}[verb]
        patch = {"recommended_minor": final} if verb == "adjust" else None
        core_actions.decide(cur, rec["action_id"], decision, actor=ctx, params_patch=patch, note=note,
                            hotel_slug=hotel_slug)
    status = {"approve": "APPROVED", "adjust": "ADJUSTED", "reject": "REJECTED"}[verb]
    now = clock.now()
    clefs_db.execute(
        "UPDATE dr_rate_recommendations SET status = %s, decided_minor = %s, decided_at = %s, decided_by = %s, "
        "status_reason = %s, updated_at = %s WHERE id = %s",
        (status, final if verb != "reject" else None, now, getattr(getattr(ctx, "principal", None), "user_id", None),
         (note or "")[:300] or None, now, rec_id), cur=cur)
    if verb != "reject":
        rel.mark_useful(cur, hotel_slug, rel.uuid_list(rec["driver_event_ids"]))
    return serialize(get(cur, hotel_slug, rec_id))


def sync_from_actions(cur: Any, hotel_slug: str) -> int:
    """A decision taken on the core action (the brief, WhatsApp, the
    actions API) reaches the recommendation row: an adjust (the action's
    recommended_minor moved) is ADJUSTED at the owner's rate.

    Core has no per-type decide hook, so this runs lazily before every
    read and write of the rows (list, night, guardrails, ADR, the
    verifier, the recommend pass, the Demand page's own verbs). It also
    re-syncs an APPROVED / ADJUSTED row whose action was adjusted again
    after approval (core allows approved -> adjust until it executes, and
    dr.rate_change never executes). APPLIED rows are history: untouched."""
    n = clefs_db.execute(
        """UPDATE dr_rate_recommendations r SET
               status = CASE WHEN a.status IN ('rejected') THEN 'REJECTED'
                             WHEN a.status = 'expired' THEN 'EXPIRED'
                             WHEN a.status = 'cancelled' THEN 'SUPERSEDED'
                             WHEN (a.params->>'recommended_minor')::bigint <> r.recommended_minor THEN 'ADJUSTED'
                             ELSE 'APPROVED' END,
               decided_minor = CASE WHEN a.status IN ('rejected', 'expired', 'cancelled') THEN NULL
                                    ELSE (a.params->>'recommended_minor')::bigint END,
               decided_at = COALESCE(a.decided_at, r.decided_at, now()),
               decided_by = COALESCE(a.decided_by, r.decided_by), updated_at = now()
             FROM actions a
            WHERE a.id = r.action_id AND r.hotel_slug = %s
              AND ((r.status = 'PROPOSED'
                    AND a.status IN ('approved', 'assigned', 'in_progress', 'done', 'verified', 'rejected', 'expired',
                                     'cancelled'))
                OR (r.status IN ('APPROVED', 'ADJUSTED')
                    AND a.status IN ('approved', 'assigned', 'in_progress', 'done', 'verified')
                    AND (a.params->>'recommended_minor')::bigint IS DISTINCT FROM r.decided_minor))""",
        (hotel_slug,), cur=cur)
    return n


# ─── Reads ───


def serialize(r: dict) -> dict:
    ccy = r["currency"]
    return {
        "id": r["id"], "stay_date": r["stay_date"].isoformat(), "room_type_code": r["room_type_code"],
        "currency": ccy, "current_bar": money_str(r["current_bar_minor"], ccy),
        "recommended": money_str(r["recommended_minor"], ccy), "floor": money_str(r["floor_minor"], ccy),
        "ceiling": money_str(r["ceiling_minor"], ccy), "decided": money_str(r["decided_minor"], ccy),
        "change_pct": format(Decimal(str(r["change_pct"])).normalize(), "f"), "levers": r["levers"] or [],
        "rationale": r["rationale"], "why": r["why"], "why_source": r["why_source"],
        "driver_event_ids": rel.uuid_list(r["driver_event_ids"]), "demand_index": r["demand_index"],
        "confidence": float(r["confidence"]), "impact": money_str(r["impact_minor"], ccy),
        "impact_minor": r["impact_minor"], "impact_label": "Estimated opportunity",
        "status": r["status"], "status_reason": r["status_reason"], "action_id": r["action_id"],
        "outcome": r["outcome"], "outcome_label": "Observed, not causal" if r["outcome"] else None,
        "created_at": r["created_at"].isoformat(),
        "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
        "applied_at": r["applied_at"].isoformat() if r["applied_at"] else None,
    }


def list_recs(cur: Any, hotel_slug: str, *, statuses: list[str] | None = None, start: date | None = None,
              end: date | None = None, limit: int = 200) -> list[dict]:
    sync_from_actions(cur, hotel_slug)
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_rate_recommendations WHERE hotel_slug = %s AND (%s OR status = ANY(%s)) "
        "AND (%s::date IS NULL OR stay_date >= %s::date) AND (%s::date IS NULL OR stay_date <= %s::date) "
        "ORDER BY stay_date, room_type_code, created_at DESC, id DESC LIMIT %s",
        (hotel_slug, not statuses, statuses or [], start, start, end, end, limit), cur=cur)
    return [serialize(r) for r in rows]


def open_for_night(cur: Any, hotel_slug: str, stay_date: date) -> list[dict]:
    sync_from_actions(cur, hotel_slug)
    return [serialize(r) for r in clefs_db.fetch_all(
        "SELECT * FROM dr_rate_recommendations WHERE hotel_slug = %s AND stay_date = %s "
        "AND status NOT IN ('SUPERSEDED', 'EXPIRED') ORDER BY created_at DESC, id DESC", (hotel_slug, stay_date), cur=cur)]


# ─── Hooks: guardrails, event changes ───


def _recommender(cur: Any, hotel_slug: str, stay_date: date, code: str | None) -> dict | None:
    """price_guardrails' `recommended`: the decided rate, else the open one."""
    sync_from_actions(cur, hotel_slug)
    r = clefs_db.fetch_one(
        "SELECT * FROM dr_rate_recommendations WHERE hotel_slug = %s AND stay_date = %s "
        "AND (%s::text IS NULL OR room_type_code = %s) AND status IN ('PROPOSED', 'APPROVED', 'ADJUSTED', 'APPLIED') "
        "ORDER BY (status <> 'PROPOSED') DESC, created_at DESC LIMIT 1", (hotel_slug, stay_date, code, code), cur=cur)
    if r is None:
        return None
    return {"recommended_minor": r["decided_minor"] or r["recommended_minor"], "id": r["id"], "status": r["status"]}


guardrails.register_recommender(_recommender)


def on_event_changed(cur: Any, ev: dict, changes: list[dict], refs: list[dict]) -> None:
    """A driving event moved, was postponed or cancelled: open proposals on
    it are superseded (the next dr.recommend re-proposes on the new dates).
    Decided ones stay; events.py's dr.event.changed signal (money section
    when approved) is the owner's prompt to revert."""
    ids = [str(ev["id"])]
    q = "SELECT * FROM dr_rate_recommendations WHERE status = 'PROPOSED' AND driver_event_ids && %s::uuid[]"
    # Sync first: superseding a row the owner already decided elsewhere would
    # cancel their approved action.
    for slug in {r["hotel_slug"] for r in clefs_db.fetch_all(q, (ids,), cur=cur)}:
        sync_from_actions(cur, slug)
    rows = clefs_db.fetch_all(q, (ids,), cur=cur)
    for r in rows:
        if ev.get("hotel_slug") is None or ev["hotel_slug"] == r["hotel_slug"]:
            supersede(cur, r, "event_changed")


def _import_events_hook() -> None:
    from clefs.modules.demand import events

    events.on_event_changed(on_event_changed)


_import_events_hook()


# ─── dr.pace.behind (nights well behind comparable pickup) ───

PACE_BEHIND_Z = -1.5
PACE_BEHIND_LEAD = 45


def raise_pace_signals(cur: Any, hotel: Any, nights: list[dict], *, today: date) -> dict:
    """One signal per night whose pace_z is at or below -1.5 within 45 days.
    The impact is the room gap priced at the reference BAR net of
    commission: an estimated opportunity, not a loss anyone caused."""
    from clefs.core.signals import Evidence, RecommendedAction, resolve_missing
    from clefs.modules.demand import signals as sig

    ccy = str(hotel.currency).upper()
    ref = None
    try:
        ref = bar_mod.reference_room_type(cur, hotel.slug)
    except ClefsError:
        ref = None
    comm, _ = commission_share(cur, hotel, today=today)
    seen = []
    for n in nights:
        stay = date.fromisoformat(n["stay_date"])
        pace = n["components"].get("pace") or {}
        if pace.get("base") is None or n["components"]["pace_z"] > PACE_BEHIND_Z \
                or (stay - today).days > PACE_BEHIND_LEAD:
            continue
        gap_rooms = max(0.0, float(pace["base"]) - float(pace["otb"]))
        b = bar_mod.get_bar(cur, hotel.slug, stay, ref) if ref else None
        imp = int(round(gap_rooms * b["bar_minor"] * (1 - comm))) if b and b["currency"] == ccy else None
        key = stay.isoformat()
        seen.append(f"{sig.PACE_BEHIND}:{key}")
        sig.raise_signal(
            cur, hotel.slug, kind=sig.PACE_BEHIND, dedupe_key=key,
            title=f"{stay:%a %d %b}: {pace['otb']} rooms on the books vs {pace['base']:.0f} on comparable nights",
            summary="Pickup is behind comparable nights at the same lead time (observed). Consider a direct offer "
                    "or reopening closed plans, never below the floor.",
            impact_minor=imp, currency=ccy, confidence=0.4 + 0.3 * n["coverage"],
            evidence=[Evidence(kind="metric", label="Pace z", value=str(n["components"]["pace_z"])),
                      Evidence(kind="metric", label="Lead days", value=str(pace.get("lead")))],
            action=RecommendedAction(type="dr.pre_arrival_offer", label="Draft a direct offer",
                                     params={"stay_date": key}, owner_role="revenue"),
            subject_type="dr_night", subject_id=key)
    resolved = resolve_missing(cur, hotel.slug, "demand", sig.PACE_BEHIND, seen)
    return {"behind": len(seen), "resolved": resolved}


# ─── demand.api.expected_adr ───


def expected_adr(cur: Any, hotel_slug: str, *, check_in: date, check_out: date,
                 room_type_code: str | None = None) -> dict | None:
    """Mean nightly rate over [check_in, check_out): a decided (approved,
    adjusted or applied) recommendation where there is one, else the BAR on
    file. None when any night has neither (Inquiry Guard then uses its own
    trailing-28-day ADR)."""
    if check_out <= check_in or (check_out - check_in).days > 60:
        return None
    try:
        code = room_type_code.strip().upper() if room_type_code else bar_mod.reference_room_type(cur, hotel_slug)
    except ClefsError:
        return None
    if not code:
        return None
    sync_from_actions(cur, hotel_slug)  # an adjust in the brief is the rate a quote should price
    total, ccy, sources = 0, None, set()
    d = check_in
    while d < check_out:
        r = clefs_db.fetch_one(
            "SELECT COALESCE(decided_minor, recommended_minor) AS m, currency FROM dr_rate_recommendations "
            "WHERE hotel_slug = %s AND stay_date = %s AND room_type_code = %s "
            "AND status IN ('APPROVED', 'ADJUSTED', 'APPLIED') ORDER BY decided_at DESC NULLS LAST LIMIT 1",
            (hotel_slug, d, code), cur=cur)
        if r:
            amount, c, src = int(r["m"]), r["currency"], "recommendation"
        else:
            b = bar_mod.get_bar(cur, hotel_slug, d, code)
            if not b:
                return None
            amount, c, src = int(b["bar_minor"]), b["currency"], "bar"
        if ccy and c != ccy:
            return None
        ccy = c
        total += amount
        sources.add(src)
        d += timedelta(days=1)
    nights = (check_out - check_in).days
    mean = int((Decimal(total) / nights).to_integral_value(ROUND_HALF_UP))
    return {"amount": money_str(mean, ccy), "currency": ccy, "room_type_code": code,
            "source": "recommendation" if sources == {"recommendation"} else "bar" if sources == {"bar"} else "mixed",
            "confidence": 0.8 if "recommendation" in sources else 0.7}
