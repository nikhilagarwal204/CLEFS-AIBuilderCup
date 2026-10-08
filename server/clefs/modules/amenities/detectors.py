"""
Amenity signals (02 spec 5.3) and the rules behind them.

- am.variance.spike (money_lost): all three of V7/E7 > 15%, V7 money > AED
  100 and z = (V7 - mu_8w) / sigma_8w > 2. Impact is the 7-day money; the
  monthly projection appears in the text only. The occupancy-unexplained
  regression check is evidence, not a fourth condition.
- am.request_logging_gap (service_risk): on-request items issued more than
  1.5 x the requests logged for them over 14 days (guests get kits nobody
  logged, so expected use and the request-rate learning both go blind).
- am.stockout_risk (service_risk): a counted location whose estimated on-hand
  is below what it needs until the next delivery. Weekly, except a signal
  with under one day of use left, which goes daily (Canon 18, F7).
- am.sup_compliance (compliance, never in the daily top 5): active items
  flagged single-use plastic still being issued.
- am.minibar_unbilled (money_lost): minibar refills beyond what the folio
  billed, when the hotel imports folio charges.

Wording is neutral: signals describe stock, never people.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs.core import signals
from clefs.core.money import D, money_str, to_minor
from clefs.modules.amenities import catalog, stock, variance
from clefs.modules.amenities.constants import (
    GAP_DAYS,
    GAP_MIN_ISSUED,
    GAP_RATIO,
    HISTORY_WINDOWS,
    MIN_HISTORY_WINDOWS,
    MINIBAR_MIN_UNBILLED_MAJOR,
    MINIBAR_MIN_UNBILLED_SHARE,
    REGRESSION_DAYS,
    SIGMA_FLOOR_UNITS,
    SPIKE_MIN_MAJOR,
    SPIKE_RATIO,
    SPIKE_Z,
)

SPIKE = "am.variance.spike"
GAP = "am.request_logging_gap"
STOCKOUT = "am.stockout_risk"
SUP = "am.sup_compliance"
MINIBAR = "am.minibar_unbilled"
INVESTIGATE = "am.investigate_variance"
MIN_COVERED_DAYS = 4                      # a window with fewer occupancy days is not judged


# ─── Signal kinds ───

# Canon 18: the daily brief is money at risk with a deadline (and service
# L1) only. A spike is a backward-looking 7-day loss and stock running low is
# a service risk, so both kinds are weekly. One stockout that bites within a
# day is the exception and overrides per signal (F7, see detect_stockout).
signals.register_signal_kind(SPIKE, label="Amenity use above expected", default_owner_role="dept_head",
                             section="money_lost", cadence="weekly", icon="package")
signals.register_signal_kind(GAP, label="Amenity requests not being logged", default_owner_role="dept_head",
                             section="service_risk", cadence="weekly", icon="clipboard")
signals.register_signal_kind(STOCKOUT, label="Amenity stock running low", default_owner_role="dept_head",
                             section="service_risk", cadence="weekly", icon="package")
signals.register_signal_kind(SUP, label="Single-use plastic items in use", default_owner_role="gm",
                             section="compliance", cadence="monthly", icon="leaf")
signals.register_signal_kind(MINIBAR, label="Minibar refills not billed", default_owner_role="gm",
                             section="money_lost", cadence="weekly", icon="receipt")


# ─── Spike rule ───


def _mean_sd(xs: list[Decimal]) -> tuple[Decimal, Decimal]:
    n = Decimal(len(xs))
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / (n - 1) if len(xs) > 1 else Decimal(0)
    return mu, var.sqrt()


def evaluate_spike(current: dict, history: list[Decimal], *, min_minor: int) -> dict:
    """The three-condition rule on one item's 7-day window. `history` is the
    V7 (units) of up to 8 earlier windows. Pure, so tests can name cases."""
    e7, v7, v7m = current["expected"], current["variance"], current["variance_minor"]
    ratio = (v7 / e7) if e7 > 0 else (Decimal("Infinity") if v7 > 0 else Decimal(0))
    out = {"expected": e7, "issued": current["issued"], "variance": v7, "variance_minor": int(v7m),
           "ratio": ratio, "history_windows": len(history), "z": None, "mu": None, "sigma": None}
    ratio_ok = ratio > SPIKE_RATIO
    money_ok = v7m > min_minor
    z_ok = False
    if len(history) >= MIN_HISTORY_WINDOWS:
        mu, sd = _mean_sd(history)
        sigma = max(sd, SIGMA_FLOOR_UNITS)
        z = (v7 - mu) / sigma
        out.update(z=z, mu=mu, sigma=sigma)
        z_ok = z > SPIKE_Z
    out.update(ratio_ok=ratio_ok, money_ok=money_ok, z_ok=z_ok, fires=ratio_ok and money_ok and z_ok)
    return out


def _fmt(x: Decimal | None, places: int = 1) -> str | None:
    if x is None:
        return None
    if x == Decimal("Infinity"):
        return "inf"
    return str(round(x, places))


def detect_spikes(cur: Any, hotel: Any, end: date) -> dict:
    slug, ccy = hotel.slug, hotel.currency
    min_minor = to_minor(SPIKE_MIN_MAJOR, ccy)
    wins = variance.item_windows(cur, slug, end, count=HISTORY_WINDOWS + 1)
    cov = variance.window_coverage(cur, slug, end, count=HISTORY_WINDOWS + 1)
    seen: list[str] = []
    fired = []
    if cov.get(0, 0) < MIN_COVERED_DAYS:
        return {"fired": 0, "reason": "current_window_not_covered"}
    items = {i["id"]: i for i in catalog.list_items(cur, slug)}
    zero = {"expected": Decimal(0), "issued": Decimal(0), "variance": Decimal(0), "variance_minor": Decimal(0)}
    for iid, per_k in wins.items():
        item = items.get(iid)
        if not item or 0 not in per_k:
            continue
        hist = [per_k.get(k, zero)["variance"] for k in range(1, HISTORY_WINDOWS + 1) if cov.get(k, 0) >= MIN_COVERED_DAYS]
        ev = evaluate_spike(per_k[0], hist, min_minor=min_minor)
        if not ev["fires"]:
            continue
        fired.append(iid)
        seen.append(_upsert_spike(cur, hotel, item, ev, end))
    resolved = signals.resolve_missing(cur, slug, "amenities", SPIKE, seen)
    return {"fired": len(fired), "resolved": resolved}


# ─── Spike signal ───


def _floor_split(cur: Any, slug: str, item_id: int, start: date, end: date) -> list[dict]:
    cur.execute(
        "SELECT floor, SUM(expected_total) AS e, SUM(issued) AS i, SUM(variance_minor) AS vm FROM amn_variance_daily "
        "WHERE hotel_slug = %s AND item_id = %s AND business_date BETWEEN %s AND %s GROUP BY floor "
        "ORDER BY SUM(variance_minor) DESC LIMIT 5", (slug, item_id, start, end))
    return list(cur.fetchall())


def _upsert_spike(cur: Any, hotel: Any, item: dict, ev: dict, end: date) -> str:
    slug, ccy = hotel.slug, hotel.currency
    start = end - timedelta(days=6)
    key = f"{SPIKE}:{item['id']}"
    v7m = int(ev["variance_minor"])
    monthly = int(Decimal(v7m) * 30 / 7)
    pct = "n/a" if ev["ratio"] == Decimal("Infinity") else f"{round(ev['ratio'] * 100)}%"
    series = variance.daily_series(cur, slug, item["id"], end - timedelta(days=REGRESSION_DAYS - 1), end)
    resid = variance.occupancy_residual(series)
    floors = _floor_split(cur, slug, item["id"], start, end)
    evidence = [
        {"kind": "calc", "label": "Issued (7 days)", "value": _fmt(ev["issued"])},
        {"kind": "calc", "label": "Expected (7 days)", "value": _fmt(ev["expected"])},
        {"kind": "calc", "label": "Above expected", "value": pct},
        {"kind": "calc", "label": "z against the last 8 weeks", "value": _fmt(ev["z"], 2),
         "ref": {"mu": _fmt(ev["mu"]), "sigma": _fmt(ev["sigma"]), "windows": ev["history_windows"]}},
    ]
    if resid:
        evidence.append({"kind": "calc", "label": "Not explained by occupancy",
                         "value": "yes" if resid["unexplained"] else "no",
                         "ref": {k: str(v) if isinstance(v, Decimal) else v for k, v in resid.items()}})
    # The verifier judges the floor that leaked most. '' is "not
    # floor-tagged", so it leaves the check hotel-wide.
    top_floor = next((f["floor"] for f in floors if D(f["vm"]) > 0 and f["floor"]), None)
    for f in floors:
        if D(f["vm"]) > 0:
            evidence.append({"kind": "row", "label": f"Floor {f['floor'] or 'not recorded'}",
                             "value": f"{money_str(int(f['vm']), ccy)} {ccy} above expected",
                             "ref": {"floor": f["floor"], "issued": _fmt(D(f["i"])), "expected": _fmt(D(f["e"]))}})
    signals.upsert_signal(cur, slug, signals.SignalIn(
        module="amenities", kind=SPIKE, dedupe_key=key,
        title=f"{item['name_en']}: {_fmt(ev['variance'], 0)} more issued than expected in 7 days",
        summary=(f"{pct} above expected use for occupancy and logged requests, {ccy} {money_str(v7m, ccy)} this week "
                 f"(about {ccy} {money_str(monthly, ccy)} a month if it continues). Count the floor closets and "
                 "compare with the issuance log."),
        severity=3 if resid and resid["unexplained"] else 2, impact_minor=v7m, currency=ccy,
        confidence=0.8 if resid and resid["unexplained"] else 0.65, evidence=evidence,
        recommended_action={"type": INVESTIGATE, "label": "Count stock and review this week's issuance",
                            "params": {"item_id": item["id"], "window_end": end.isoformat(), "v7_minor": v7m,
                                       **({"floor": top_floor} if top_floor else {})},
                            "owner_role": "dept_head", "due_in_hours": 48},
        owner_role="dept_head", subject_type="amn_item", subject_id=str(item["id"]),
    ))
    return key


# ─── Logging gap ───


def logging_gap(cur: Any, hotel_slug: str, end: date, *, days: int = GAP_DAYS) -> dict:
    """Issued vs logged requests for ON_REQUEST items over `days`."""
    start = end - timedelta(days=days - 1)
    cur.execute(
        """
        SELECT v.item_id, i.name_en, SUM(v.issued) AS issued, SUM(v.requested) AS requested
        FROM amn_variance_daily v JOIN amn_items i ON i.id = v.item_id
        WHERE v.hotel_slug = %s AND v.business_date BETWEEN %s AND %s AND i.basis = 'ON_REQUEST'
          AND i.category <> 'MINIBAR'
        GROUP BY v.item_id, i.name_en ORDER BY SUM(v.issued) - SUM(v.requested) DESC
        """,
        (hotel_slug, start, end),
    )
    rows = cur.fetchall()
    issued = sum((D(r["issued"]) for r in rows), Decimal(0))
    logged = sum((D(r["requested"]) for r in rows), Decimal(0))
    fires = issued >= GAP_MIN_ISSUED and issued > GAP_RATIO * logged
    return {"issued": issued, "logged": logged, "fires": fires, "items": rows[:5], "start": start, "end": end}


def detect_logging_gap(cur: Any, hotel: Any, end: date) -> dict:
    g = logging_gap(cur, hotel.slug, end)
    seen = []
    if g["fires"]:
        key = GAP
        seen.append(key)
        signals.upsert_signal(cur, hotel.slug, signals.SignalIn(
            module="amenities", kind=GAP, dedupe_key=key,
            title=f"{_fmt(g['issued'], 0)} on-request items issued, {_fmt(g['logged'], 0)} requests logged (14 days)",
            summary=("Kits handed out without a logged request hide real demand and make leakage invisible. "
                     "Ask the team to log each guest request in the service queue or the issuance entry."),
            severity=2, confidence=0.7,
            evidence=[{"kind": "row", "label": r["name_en"], "value": f"issued {_fmt(D(r['issued']), 0)}, "
                       f"logged {_fmt(D(r['requested']), 0)}", "ref": {"item_id": r["item_id"]}} for r in g["items"]],
            owner_role="dept_head",
        ))
    resolved = signals.resolve_missing(cur, hotel.slug, "amenities", GAP, seen)
    return {"fired": len(seen), "resolved": resolved}


# ─── Stockout risk ───


def detect_stockout(cur: Any, hotel: Any, end: date) -> dict:
    """Counted locations only: on hand (last count + received - sent out)
    below daily use x lead days, or below the safety stock."""
    seen = []
    for p in stock.list_par(cur, hotel.slug):
        oh = stock.on_hand(cur, hotel.slug, p["location_id"], p["item_id"])
        if oh is None:
            continue
        comp = p.get("computed") or {}
        daily = D(comp.get("daily_usage") or 0) or (D(p["par_qty"]) / D(p["days_of_supply"]) if D(p["par_qty"]) else 0)
        need = max(D(p["safety_stock"]), D(daily) * D(p["lead_days"]))
        if need <= 0 or oh["qty"] >= need:
            continue
        key = f"{STOCKOUT}:{p['location_id']}:{p['item_id']}"
        seen.append(key)
        # Less than one day of use left (use > 0 means rooms are occupied):
        # it bites before the next brief, so this one signal goes daily.
        urgent = D(daily) > 0 and oh["qty"] < D(daily)
        signals.upsert_signal(cur, hotel.slug, signals.SignalIn(
            module="amenities", kind=STOCKOUT, dedupe_key=key,
            title=f"{p['item_name']} low at {p['location_name']}: about {_fmt(oh['qty'], 0)} left",
            summary=(f"Needs about {_fmt(need, 0)} to cover {catalog.qty_str(p['lead_days'])} day(s) until the next "
                     f"delivery. Restock to par ({catalog.qty_str(p['par_qty'])})."),
            severity=3 if oh["qty"] <= 0 else 2, confidence=0.75, cadence="daily" if urgent else None,
            evidence=[{"kind": "calc", "label": "Last count", "value": catalog.qty_str(oh["counted_qty"]),
                       "ref": {"counted_at": oh["counted_at"].isoformat()}},
                      {"kind": "calc", "label": "Received since", "value": catalog.qty_str(oh["received"])},
                      {"kind": "calc", "label": "Issued out since", "value": catalog.qty_str(oh["sent"])}],
            owner_role="dept_head", subject_type="amn_location", subject_id=str(p["location_id"]),
        ))
    resolved = signals.resolve_missing(cur, hotel.slug, "amenities", STOCKOUT, seen)
    return {"fired": len(seen), "resolved": resolved}


# ─── Single-use plastic ───


def detect_sup(cur: Any, hotel: Any, end: date) -> dict:
    """Active single-use plastic items issued in the last 30 days (or on a
    standard basis, so issued daily by definition)."""
    cur.execute(
        """
        SELECT i.id, i.name_en, i.sup_ban_ref, i.basis, COALESCE(SUM(s.qty), 0) AS issued
        FROM amn_items i LEFT JOIN amn_issuance s ON s.item_id = i.id AND s.voided_at IS NULL
             AND s.business_date BETWEEN %s AND %s
        WHERE i.hotel_slug = %s AND i.active AND i.is_single_use_plastic
        GROUP BY i.id HAVING COALESCE(SUM(s.qty), 0) > 0 OR i.basis <> 'ON_REQUEST'
        ORDER BY i.sup_ban_ref NULLS LAST, i.name_en
        """,
        (end - timedelta(days=29), end, hotel.slug),
    )
    rows = cur.fetchall()
    seen = []
    if rows:
        seen.append(SUP)
        where = "Dubai's" if (hotel.emirate or "").lower() == "dubai" else "UAE"
        banned = [r for r in rows if r["sup_ban_ref"]]
        signals.upsert_signal(cur, hotel.slug, signals.SignalIn(
            module="amenities", kind=SUP, dedupe_key=SUP,
            title=f"{len(rows)} single-use plastic amenity item(s) still issued",
            summary=(f"{where} single-use plastic rules are tightening. Review replacements (bamboo, paper, refillable "
                     "dispensers) with your supplier." + (f" {len(banned)} item(s) fall under a ban phase." if banned
                                                          else "")),
            severity=3 if banned else 1, confidence=0.9,
            evidence=[{"kind": "row", "label": r["name_en"], "value": f"{_fmt(D(r['issued']), 0)} issued in 30 days",
                       "ref": {"item_id": r["id"], "ban": r["sup_ban_ref"]}} for r in rows[:10]]
            + [{"kind": "link", "label": "Dubai single-use plastic ban, final phase 2026",
                "value": "https://www.khaleejtimes.com/uae/dubai-single-use-plastic-ban-final-phase-january-2026-awareness-guide"}],
            recommended_action={"type": "am.replace_sup_item", "label": "Agree replacements with the supplier",
                                "owner_role": "gm", "due_in_hours": 24 * 14},
            owner_role="gm",
        ))
    resolved = signals.resolve_missing(cur, hotel.slug, "amenities", SUP, seen)
    return {"fired": len(seen), "resolved": resolved}


# ─── Minibar unbilled ───


def detect_minibar(cur: Any, hotel: Any, end: date) -> dict:
    """7-day refills against MINIBAR folio quantity. Silent when the hotel
    imports no folio (has_folio false): an absent feed is not a loss."""
    from clefs.modules.amenities import reports

    mb = reports.minibar(cur, hotel, end - timedelta(days=6), end)
    ccy = hotel.currency
    seen = []
    refills = D(mb["refills"])
    if mb["has_folio"] and refills > 0:
        unbilled, value = D(mb["unbilled"]), int(mb["unbilled_minor"] or 0)
        if unbilled / refills > MINIBAR_MIN_UNBILLED_SHARE and value > to_minor(MINIBAR_MIN_UNBILLED_MAJOR, ccy):
            seen.append(MINIBAR)
            signals.upsert_signal(cur, hotel.slug, signals.SignalIn(
                module="amenities", kind=MINIBAR, dedupe_key=MINIBAR,
                title=f"{mb['unbilled']} minibar refills not on a folio this week",
                summary=(f"{mb['refills']} items refilled, {mb['billed']} billed: about {ccy} {mb['unbilled_value']} "
                         "at menu prices. Check the minibar postings against the refill sheets before guests "
                         "check out."),
                severity=2, impact_minor=value, currency=ccy, confidence=0.6,
                evidence=[{"kind": "calc", "label": "Refilled (7 days)", "value": mb["refills"]},
                          {"kind": "calc", "label": "Billed on folios (7 days)", "value": mb["billed"]},
                          {"kind": "calc", "label": "Unbilled share", "value": f"{mb['unbilled_share_pct']}%"}],
                recommended_action={"type": "am.post_minibar", "label": "Post the missing minibar charges",
                                    "owner_role": "front_desk", "due_in_hours": 24},
                owner_role="gm",
            ))
    resolved = signals.resolve_missing(cur, hotel.slug, "amenities", MINIBAR, seen)
    return {"fired": len(seen), "resolved": resolved}


def run_all(cur: Any, hotel: Any, end: date) -> dict:
    """Every detector for business date `end` (the nightly job)."""
    return {"spike": detect_spikes(cur, hotel, end), "logging_gap": detect_logging_gap(cur, hotel, end),
            "stockout": detect_stockout(cur, hotel, end), "sup": detect_sup(cur, hotel, end),
            "minibar": detect_minibar(cur, hotel, end)}
