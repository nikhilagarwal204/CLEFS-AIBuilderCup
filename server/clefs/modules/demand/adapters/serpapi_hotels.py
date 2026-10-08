"""
SerpApi Google Hotels comp-rate adapter (02 spec 4.2, 04 spec F5, Canon K3).

- One area query per night: clefs.core.serpapi.search('google_hotels',
  google_hotels_area_params(hotel, q=..., check_in, check_out),
  purpose='demand.comp_rates'). The shared builder means Visibility's
  identical daily request is served from the same cached, billed call.
- Comps missing from the area page get a per-property_token call, only for
  nights within the fallback horizon and at most `fallback_max` per run.
- BudgetExhausted stops the adapter for the run (a skipped fetch, not an
  error). NotConfigured (no SERPAPI_KEY) stops it too. A SerpApiError skips
  that night; three in a row stop the run (the vendor is down).

Prices are `rate_per_night.extracted_lowest` in the requested currency (the
hotel's). A comp on the page with no price at all is sold out for the night.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from clefs.core import clock, serpapi
from clefs.core.errors import NotConfigured
from clefs.core.money import to_minor
from clefs.core.time import local_date
from clefs.modules.demand.comp_rates import CompRate, match_comp

log = logging.getLogger("clefs.demand.serpapi")

PURPOSE = "demand.comp_rates"
SOURCE = "SERPAPI"
MAX_CONSECUTIVE_ERRORS = 3


# ─── Parsing (pure; tested against fixture JSON) ───


def _amount(block: Any) -> Decimal | None:
    """`extracted_lowest` of a rate block, else None. Zero or negative is
    never a price."""
    if not isinstance(block, dict):
        return None
    v = block.get("extracted_lowest")
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def parse_property(prop: dict, *, currency: str) -> tuple[int | None, list[dict]]:
    """(lowest per-night minor units | None, by_source rows) of one property."""
    by_source: list[dict] = []
    for p in prop.get("prices") or prop.get("featured_prices") or []:
        amt = _amount(p.get("rate_per_night"))
        if amt is not None and p.get("source"):
            row = {"source": str(p["source"])[:80], "minor": to_minor(amt, currency)}
            if p.get("official"):
                row["official"] = True
            by_source.append(row)
    amt = _amount(prop.get("rate_per_night"))
    lowest = to_minor(amt, currency) if amt is not None else None
    if lowest is None and by_source:
        lowest = min(s["minor"] for s in by_source)
    return lowest, sorted(by_source, key=lambda s: s["minor"])


def parse_area(body: dict, comps: list[dict], *, stay_date: date, los: int, adults: int, currency: str,
               query: str | None, self_token: str | None = None) -> tuple[list[CompRate], set[str]]:
    """Map an area page to CompRates for the comps it lists. Returns (rates,
    comp_ids seen). The hotel's own token is skipped; a property listed
    twice (an ad and an organic row) keeps its lowest price."""
    now = clock.now()
    found: dict[str, CompRate] = {}
    for prop in body.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        token = prop.get("property_token")
        if self_token and token == self_token:
            continue
        comp = match_comp(comps, token=token, name=prop.get("name"))
        if comp is None:
            continue
        lowest, by_source = parse_property(prop, currency=currency)
        rate = CompRate(comp_id=comp["comp_id"], stay_date=stay_date, los=los, adults=adults,
                        lowest_minor=lowest, currency=currency, source=SOURCE, captured_at=now,
                        sold_out=lowest is None, by_source=by_source, query=query)
        prev = found.get(comp["comp_id"])
        if prev is None or (lowest is not None and (prev.lowest_minor is None or lowest < prev.lowest_minor)):
            found[comp["comp_id"]] = rate
    return list(found.values()), set(found)


def parse_property_page(body: dict, comp: dict, *, stay_date: date, los: int, adults: int,
                        currency: str) -> CompRate | None:
    """A per-property_token answer is the property itself at the top level.
    No name and no price means the page did not resolve: no row."""
    if not isinstance(body, dict) or not (body.get("name") or body.get("prices")):
        return None
    lowest, by_source = parse_property(body, currency=currency)
    return CompRate(comp_id=comp["comp_id"], stay_date=stay_date, los=los, adults=adults, lowest_minor=lowest,
                    currency=currency, source=SOURCE, captured_at=clock.now(), sold_out=lowest is None,
                    by_source=by_source, query=f"property_token:{comp['property_token']}")


# ─── Adapter ───


class SerpApiGoogleHotels:
    """RateFeedAdapter over clefs.core.serpapi. `stopped` names why the run
    ended early (budget_exhausted, not_configured, vendor_down); `report`
    counts calls for the job result."""

    source = SOURCE

    def __init__(self, comps: list[dict], *, queries: list[str], self_token: str | None = None,
                 fallback_max: int = 0, fallback_horizon: int = 14) -> None:
        self.comps = comps
        self.queries = queries
        self.self_token = self_token
        self.fallback_left = max(0, int(fallback_max))
        self.fallback_horizon = fallback_horizon
        self.stopped: str | None = None if queries else "no_area_query"
        self.report: dict = {"area_calls": 0, "fallback_calls": 0, "errors": 0, "stopped": self.stopped}
        self._consecutive_errors = 0

    def _search(self, params: dict, hotel: Any) -> dict | None:
        try:
            body = serpapi.search("google_hotels", params, hotel_slug=hotel.slug, purpose=PURPOSE)
        except serpapi.BudgetExhausted:
            self._stop("budget_exhausted")
            return None
        except NotConfigured:
            self._stop("not_configured")
            return None
        except serpapi.SerpApiError as exc:
            self.report["errors"] += 1
            self._consecutive_errors += 1
            log.warning("comp rates: SerpApi error for %s: %s", hotel.slug, exc.detail)
            if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                self._stop("vendor_down")
            return None
        self._consecutive_errors = 0
        return body

    def _stop(self, reason: str) -> None:
        self.stopped = self.report["stopped"] = reason

    def fetch(self, hotel: Any, comp_ids: list[str], dates: list[date], los: int = 1, adults: int = 2,
              currency: str = "AED") -> list[CompRate]:
        wanted = [c for c in self.comps if c["comp_id"] in set(comp_ids)]
        out: list[CompRate] = []
        today = local_date(hotel)  # the fallback horizon counts hotel-local nights
        for d in dates:
            seen: set[str] = set()
            for q in self.queries:
                if self.stopped:
                    return out
                params = serpapi.google_hotels_area_params(hotel, q=q, check_in=d, check_out=d + timedelta(days=los))
                if adults != 2:
                    params["adults"] = adults
                self.report["area_calls"] += 1
                body = self._search(params, hotel)
                if body is None:
                    continue
                rates, ids = parse_area(body, [c for c in wanted if c["comp_id"] not in seen], stay_date=d,
                                        los=los, adults=adults, currency=currency, query=q,
                                        self_token=self.self_token)
                out += rates
                seen |= ids
            out += self._fallback(hotel, wanted, seen, d, los, adults, currency, today)
        return out

    def _fallback(self, hotel: Any, wanted: list[dict], seen: set[str], d: date, los: int, adults: int,
                  currency: str, today: date) -> list[CompRate]:
        """Per-property calls for comps the area pages missed (budgeted)."""
        out: list[CompRate] = []
        if (d - today).days > self.fallback_horizon or not self.queries:
            return out
        for c in wanted:
            if self.stopped or self.fallback_left <= 0:
                break
            if c["comp_id"] in seen or not c.get("property_token"):
                continue
            params = serpapi.google_hotels_area_params(hotel, q=c["name"] or self.queries[0], check_in=d,
                                                       check_out=d + timedelta(days=los))
            params["property_token"] = c["property_token"]
            if adults != 2:
                params["adults"] = adults
            self.fallback_left -= 1
            self.report["fallback_calls"] += 1
            body = self._search(params, hotel)
            rate = parse_property_page(body, c, stay_date=d, los=los, adults=adults,
                                       currency=currency) if body else None
            if rate is not None:
                out.append(rate)
        return out


# ─── Comp-set discovery ───


def candidates(body: dict, comps: list[dict], *, currency: str, self_token: str | None = None) -> list[dict]:
    """Hotels on an area page as comp-set candidates (sponsored rows and
    vacation rentals left out). Pure, for the discover route."""
    out: list[dict] = []
    seen: set[str] = set()
    for p in body.get("properties") or []:
        if not isinstance(p, dict) or p.get("sponsored") or (p.get("type") or "hotel") != "hotel":
            continue
        tok = p.get("property_token")
        if not tok or tok in seen:
            continue
        seen.add(tok)
        lowest, _ = parse_property(p, currency=currency)
        gps = p.get("gps_coordinates") or {}
        match = match_comp(comps, token=tok)
        out.append({
            "name": p.get("name"), "property_token": tok,
            "stars": p.get("extracted_hotel_class"), "lat": gps.get("latitude"), "lng": gps.get("longitude"),
            "rate_minor": lowest, "overall_rating": p.get("overall_rating"), "reviews": p.get("reviews"),
            "is_self": bool(self_token and tok == self_token),
            "comp_id": match["comp_id"] if match else None,
        })
    return out


def discover(hotel: Any, *, q: str, check_in: date, comps: list[dict], self_token: str | None = None) -> list[dict]:
    """One area search (the same cached call the nightly fetch makes for that
    night). Raises BudgetExhausted / NotConfigured / SerpApiError."""
    params = serpapi.google_hotels_area_params(hotel, q=q, check_in=check_in, check_out=check_in + timedelta(days=1))
    body = serpapi.search("google_hotels", params, hotel_slug=hotel.slug, purpose=PURPOSE)
    return candidates(body, comps, currency=str(hotel.currency), self_token=self_token)
