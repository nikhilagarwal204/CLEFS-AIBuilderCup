"""
Xotelo comp-rate adapter: demo and prototype only (02 spec 4.2).

Xotelo (https://data.xotelo.com/api) is free, derives from TripAdvisor, has
no SLA and quotes no AED (research events-demand.md). So:
- it runs only for a demo hotel (hotels.status 'demo') or when
  DEMAND_XOTELO_ENABLED is set, and only for comps with a `xotelo_key`;
- `GET /rates?hotel_key&chk_in&chk_out&currency=USD` answers per-OTA USD
  rates for the stay; the lowest `rate` becomes the per-night price;
- USD is converted to the hotel's currency with clefs.core.money (fx_rates,
  else the 3.6725 AED peg) and the row is flagged fx_converted with the
  vendor amount kept in original_minor / original_currency.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.money import convert_minor, to_minor
from clefs.modules.demand.adapters import VendorError, vendor_json
from clefs.modules.demand.comp_rates import CompRate
from clefs.settings import env

log = logging.getLogger("clefs.demand.xotelo")

SOURCE = "XOTELO"
API = "https://data.xotelo.com/api/rates"
VENDOR_CCY = "USD"


def enabled_for(hotel: Any) -> bool:
    flag = (env("DEMAND_XOTELO_ENABLED", "") or "").strip().lower() in ("1", "true", "yes", "on")
    return flag or getattr(hotel, "status", None) == "demo"


def parse_rates(body: dict, *, nights: int) -> tuple[int | None, list[dict]]:
    """(lowest per-night USD cents | None, by_source in USD cents). Xotelo's
    `rate` is for the whole stay, so it is divided by the nights."""
    result = body.get("result") if isinstance(body, dict) else None
    if body.get("error") or not isinstance(result, dict):
        return None, []
    rows: list[dict] = []
    for r in result.get("rates") or []:
        try:
            amt = Decimal(str(r.get("rate")))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if amt > 0:
            rows.append({"source": str(r.get("name") or r.get("code") or "?")[:80],
                         "minor": to_minor(amt / max(1, nights), VENDOR_CCY)})
    rows.sort(key=lambda s: s["minor"])
    return (rows[0]["minor"] if rows else None), rows


class Xotelo:
    source = SOURCE

    def __init__(self, comps: list[dict]) -> None:
        self.comps = [c for c in comps if c.get("xotelo_key")]
        self.stopped: str | None = None
        self.report: dict = {"calls": 0, "errors": 0, "stopped": None}

    def fetch(self, hotel: Any, comp_ids: list[str], dates: list[date], los: int = 1, adults: int = 2,
              currency: str = "AED") -> list[CompRate]:
        out: list[CompRate] = []
        for d in dates:
            for c in self.comps:
                if c["comp_id"] not in comp_ids or self.stopped:
                    continue
                self.report["calls"] += 1
                try:
                    body = vendor_json("xotelo", "GET", API, params={
                        "hotel_key": c["xotelo_key"], "chk_in": d.isoformat(),
                        "chk_out": (d + timedelta(days=los)).isoformat(), "currency": VENDOR_CCY})
                except VendorError as exc:
                    self.report["errors"] += 1
                    if not exc.transient:
                        self.stopped = self.report["stopped"] = "vendor_error"
                    continue
                usd, by_source = parse_rates(body, nights=los)
                if usd is None:
                    continue  # no quote is not sold out on Xotelo: it often has gaps
                with clefs_db.db(readonly=True) as cur:
                    local = convert_minor(cur, usd, VENDOR_CCY, currency, d)
                    by_local = [{**s, "minor": convert_minor(cur, s["minor"], VENDOR_CCY, currency, d)}
                                for s in by_source]
                out.append(CompRate(comp_id=c["comp_id"], stay_date=d, los=los, adults=adults, lowest_minor=local,
                                    currency=currency, source=SOURCE, captured_at=clock.now(),
                                    by_source=by_local, fx_converted=True, original_minor=usd,
                                    original_currency=VENDOR_CCY, query=f"xotelo:{c['xotelo_key']}"))
        return out
