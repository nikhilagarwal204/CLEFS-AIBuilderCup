"""
Demand Radar public API (frozen signatures; other modules import only this file).

Canon 12: Demand owns dr_weather (Open-Meteo) and exposes outdoor_temp_c;
Service escalates AC requests to L1 above 40 C and falls back to its own
June-September 11:00-17:00 heuristic when this returns None.

04 spec 7.2: the functions below ship as working stubs with the documented
safe defaults, and every caller handles them (Inquiry Guard, Visibility,
Finance). The Demand lane replaces the bodies, never the signatures; a
signature change goes through a FOUNDATION-REQUEST (FROZEN_API).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from clefs.core.hotels import find_hotel


def price_guardrails(cur: Any, hotel_slug: str, stay_date: date, room_type_code: str | None = None) -> dict | None:
    """{floor, ceiling, recommended, currency, confidence, reasons[], bar,
    bar_captured_at, source: 'rate_guardrails'|'bar_only'|'fallback',
    demand_index} for one night (and room type, None = the hotel's reference
    type), or None when Demand has no view: Inquiry Guard then takes manual
    rate entry and never auto-sends (04 spec 4.6). Rules in guardrails.py."""
    from clefs.modules.demand import guardrails

    return guardrails.price_guardrails(cur, hotel_slug, stay_date, room_type_code)


def outdoor_temp_c(hotel: Any, at: datetime) -> float | None:
    """Forecast/observed outdoor temperature at the hotel at `at` (Hotel or
    slug). None when unknown. Reads the Open-Meteo cache only (weather.py):
    it runs on Service's request intake, so it never calls the network."""
    from clefs.modules.demand import weather

    return weather.outdoor_temp_c(hotel, at)


def comp_rates(cur: Any, hotel_slug: str, stay_date: date) -> list[dict]:
    """[{competitor, channel, rate, currency, fetched_at}] for one night
    (plus comp_id, sold_out, source, indicative). Every rate is indicative."""
    from clefs.modules.demand import comp_rates as cr

    return cr.rates_for_night(cur, hotel_slug, stay_date)


def expected_adr(
    cur: Any, hotel_slug: str, *, check_in: date, check_out: date, room_type_code: str | None = None
) -> dict | None:
    """{amount, currency, source, confidence} (amount a Decimal string) for
    the stay, or None: Inquiry Guard then uses the trailing-28-day
    same-weekday ADR from core/stays (04 spec 4.5). The mean of each night's
    decided recommendation, else its BAR; None when a night has neither
    (recommend.expected_adr)."""
    from clefs.modules.demand import recommend

    return recommend.expected_adr(cur, hotel_slug, check_in=check_in, check_out=check_out,
                                  room_type_code=room_type_code)


def comp_set(cur: Any, hotel_slug: str) -> list[dict]:
    """[{comp_id, name, property_token, place_id, lat, lng, stars, aliases}].
    Empty until the owner sets a comp set: Visibility then shows the hotel
    against "other hotels" only."""
    from clefs.modules.demand import comp_rates as cr

    keys = ("comp_id", "name", "property_token", "place_id", "lat", "lng", "stars", "aliases")
    return [{k: c[k] for k in keys} for c in cr.list_comps(cur, hotel_slug)]


def area_queries(cur: Any, hotel_slug: str) -> list[str]:
    """The single source of Google Hotels `q` strings (Demand and Visibility
    share one billed SerpApi call per query, 04 spec F5): the owner's
    queries (rates settings), else one generic query on the hotel's city;
    [] when there is neither (a query on nothing would bill a useless call)."""
    from clefs.modules.demand import comp_rates as cr

    hotel = find_hotel(cur, hotel_slug)
    return cr.area_queries(cur, hotel) if hotel else []


def comp_rates_snapshot(cur: Any, hotel_slug: str, *, stay_dates: list[date]) -> dict:
    """{stay_date (ISO): [{competitor, rate, currency, fetched_at}]} for the
    dates asked (plus comp_id, sold_out, source); nights without data are
    left out, so {} means no comp snapshot on the quote."""
    from clefs.modules.demand import comp_rates as cr

    return cr.snapshot(cur, hotel_slug, list(stay_dates or []))


def upcoming_events(cur: Any, hotel_slug: str, *, days: int = 60, min_score: float = 0.3) -> list[dict]:
    """[{event_id, name, start, end, score, source}] in the next `days` with
    demand score >= min_score (plus category, status, verification). The
    score is the relevance model's when it has run, else a distance-and-size
    estimate (events.quick_score). [] for an unknown hotel."""
    from clefs.core.time import local_date
    from clefs.modules.demand import events

    hotel = find_hotel(cur, hotel_slug)
    if hotel is None:
        return []
    return events.upcoming(cur, hotel, days=max(0, int(days)), min_score=float(min_score), today=local_date(hotel))


def find_event(cur: Any, hotel_slug: str, *, text: str) -> dict | None:
    """The event a guest names ("GITEX week") as {event_id, name, start, end},
    or None: the inquiry's dates then stay flexible (04 spec 4.4)."""
    from clefs.core.time import local_date
    from clefs.modules.demand import events

    hotel = find_hotel(cur, hotel_slug)
    if hotel is None or not (text or "").strip():
        return None
    return events.find(cur, hotel_slug, text=text[:200], today=local_date(hotel))


def demand_index(cur: Any, hotel_slug: str, *, stay_date: date) -> int | None:
    """0-100 demand pressure for one night, or None: standard quote validity
    (a value >= 80 shortens it to 24 h, 04 spec 4.6). The stored index
    (dr.demand_index job), else a live one-night computation; None when no
    input (history, pace, comp rates, events) has data (demand_index.py)."""
    from clefs.modules.demand import demand_index as di

    return di.value_for(cur, hotel_slug, stay_date)


# ─── Demo seed door ───
# WHY: the UAE demo seed (clefs.seed.demo_uae) is foundation code, which may
# never import a module's internals (tests/foundation/test_module_contracts).
# It loads this api by name and drives Demand through the same functions the
# console routes and the vendor adapters call, so the demo data carries every
# rule those paths enforce. Nothing else calls this; other modules use the
# functions above.


def demo_seed_door() -> Any:
    """The functions the demo seed drives: manual events (the Events tab),
    the PredictHQ adapters with fixture payloads (their documented results=
    / payload= inputs), the rate-shop CSV import, and the BAR and guardrail
    writers the PUT routes call."""
    from types import SimpleNamespace

    from clefs.modules.demand import bar, events, guardrails
    from clefs.modules.demand.adapters import csv_rates, phq_events, phq_features

    return SimpleNamespace(
        create_manual_event=events.create_manual,
        sync_phq_events=phq_events.sync_hotel,
        sync_phq_features=phq_features.sync_hotel,
        import_rate_csv=csv_rates.import_csv,
        put_bar_rows=bar.put_rows,
        upsert_guardrails=guardrails.upsert_rows,
    )
