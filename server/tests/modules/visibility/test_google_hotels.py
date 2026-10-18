"""Google Hotels: organic rank, official price and parity (04 spec 5.2, 5.12)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))  # Tuesday
TOKEN = "ChoIself"


def _prop(name, token, sponsored=False):
    p = {"type": "hotel", "name": name, "property_token": token}
    if sponsored:
        p["sponsored"] = True
    return p


PAGE1 = {
    "properties": [_prop("Ad Hotel", "ad1", sponsored=True)] + [_prop(f"H{i}", f"t{i}") for i in range(1, 19)],
    "ads": [{"name": "Ad 2"}, {"name": "Ad 3"}],
    "serpapi_pagination": {"next_page_token": "PAGE2"},
}
PAGE1_WITH_SELF = {
    "properties": [_prop("Ad", "ad1", sponsored=True), _prop("H1", "t1"), _prop("Self", TOKEN)],
    "ads": [{"name": "Ad 2"}],
}
PAGE2 = {"properties": [_prop("H19", "t19"), _prop("Sp", "sp", sponsored=True), _prop("Self", TOKEN)]}


def _details(official, others, basis="total_rate"):
    prices = []
    if official is not None:
        prices.append({"source": "Test Hotel Dubai", "official": True, basis: {"extracted_lowest": official}})
    for src, amount in others:
        prices.append({"source": src, basis: {"extracted_lowest": amount}})
    return {"type": "hotel", "name": "Self", "prices": prices}


@pytest.fixture
def env(monkeypatch):
    from clefs.settings import reload_settings

    def _set(**values):
        for k, v in values.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, str(v))
        reload_settings()

    yield _set
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def serp(env):
    from clefs.core import serpapi

    env(SERPAPI_KEY="test-key", SERPAPI_MONTHLY_BUDGET_USD=None)
    fake = serpapi.use_fake()
    yield fake
    serpapi.set_transport(None)


@pytest.fixture
def hotel(cur, hotel_uae, clock):
    clock.freeze(NOW)
    from clefs.modules.visibility import store

    store.update_profile(cur, hotel_uae.slug, {"gh_property_token": TOKEN}, user_id=None)
    return hotel_uae


def _router(details_by_date, page1=PAGE1, page2=PAGE2):
    def answer(params):
        if params.get("property_token"):
            return details_by_date.get(params["check_in_date"], _details(None, []))
        if params.get("next_page_token"):
            return page2
        return page1
    return answer


# ─── Pure parts ───


def test_date_pairs_next_friday_and_plus_30():
    from clefs.modules.visibility.google_hotels import date_pairs

    assert date_pairs(date(2026, 9, 29)) == [(date(2026, 10, 2), date(2026, 10, 3)),
                                             (date(2026, 10, 29), date(2026, 10, 30))]
    # A Friday looks a week ahead.
    assert date_pairs(date(2026, 10, 2))[0][0] == date(2026, 10, 9)


def test_organic_rank_ignores_sponsored_and_ads():
    from clefs.modules.visibility.google_hotels import organic_properties, rank_in

    organic, sponsored = organic_properties(PAGE1_WITH_SELF)
    assert sponsored == 2 and rank_in(organic, TOKEN) == 2


@pytest.mark.parametrize("official,other,gap,pct,breach", [
    (450, 412, 3800, Decimal("8.444"), True),
    (400, 396, 400, Decimal("1.000"), False),     # exactly 1.00%: parity
    (404, 400, 400, Decimal("0.990"), False),
    (450, 446.50, 350, Decimal("0.778"), False),
])
def test_parity_goldens(official, other, gap, pct, breach):
    from clefs.modules.visibility.google_hotels import analyse_prices, is_breach, q3

    a = analyse_prices(_details(official, [("Booking.com", other), ("Agoda", other + 30)]), "AED")
    assert a["official_present"] and a["parity_gap_minor"] == gap
    assert q3(a["parity_gap_pct"]) == pct
    assert is_breach(a["parity_gap_pct"], Decimal("1.0")) is breach
    assert a["min_other_source"] == "Booking.com"


def test_like_for_like_basis_uses_the_official_entrys_field():
    from clefs.modules.visibility.google_hotels import analyse_prices

    d = {"prices": [
        {"source": "Self", "official": True, "rate_per_night": {"extracted_lowest": 450}},
        {"source": "Booking.com", "total_rate": {"extracted_lowest": 300}, "rate_per_night": {"extracted_lowest": 420}},
    ]}
    a = analyse_prices(d, "AED")
    assert a["price_basis"] == "rate_per_night" and a["min_other_price_minor"] == 42000


def test_no_official_entry():
    from clefs.modules.visibility.google_hotels import analyse_prices

    a = analyse_prices(_details(None, [("Booking.com", 400)]), "AED")
    assert a["official_present"] is False and a["parity_gap_minor"] is None


# ─── The daily run ───


@pytest.fixture
def evidence_calls(monkeypatch):
    from clefs.modules.profit_guard import api as pg_api

    calls = []

    def _record(cur, hotel_slug, *, evidence, emit_signal=False):
        calls.append({"evidence": evidence, "emit_signal": emit_signal})
        return len(calls)

    monkeypatch.setattr(pg_api, "record_rate_evidence", _record)
    return calls


@pytest.fixture
def booking_otb(cur, hotel):
    cur.execute("INSERT INTO channels (hotel_slug, code, name, kind) VALUES (%s, 'BOOKING', 'Booking.com', 'ota'), "
                "(%s, 'AGODA', 'Agoda', 'ota') ON CONFLICT DO NOTHING", (hotel.slug, hotel.slug))
    cur.execute(
        "INSERT INTO otb_snapshots (hotel_slug, snapshot_date, stay_date, rooms_otb, currency, by_channel) "
        "VALUES (%s, '2026-09-28', '2026-10-02', 20, 'AED', '{\"BOOKING\": 14, \"AGODA\": 3}')", (hotel.slug,))


def test_daily_run_stores_rank_parity_and_signals(cur, hotel, serp, evidence_calls, booking_otb):
    from clefs.core import signals
    from clefs.modules.visibility import google_hotels

    serp.default = None
    serp.queue = [_router({"2026-10-02": _details(450, [("Booking.com", 412), ("Agoda", 430)]),
                           "2026-10-29": _details(400, [("Booking.com", 396)])})] * 6
    report = google_hotels.run_daily(cur, hotel)
    assert report["observations"] == 2 and report["breaches"] == 1 and report["errors"] == 0

    cur.execute("SELECT * FROM vis_google_hotels_obs WHERE hotel_slug = %s ORDER BY check_in", (hotel.slug,))
    rows = cur.fetchall()
    first = rows[0]
    # Page 1: 18 organic (the sponsored one and 2 ads dropped); page 2: H19, sponsored, Self.
    assert (first["organic_rank"], first["organic_total"], first["sponsored_count"], first["pages_fetched"]) == \
        (20, 20, 4, 2)
    assert first["official_present"] and first["parity_gap_minor"] == 3800
    assert first["parity_gap_pct"] == Decimal("8.444") and first["min_other_channel"] == "BOOKING"

    # Profit Guard gets the evidence without a signal of its own (no double L7).
    assert len(evidence_calls) == 2 and all(c["emit_signal"] is False for c in evidence_calls)
    assert evidence_calls[0]["evidence"]["source"] == "SERPAPI"

    open_ = {s["dedupe_key"]: s for s in signals.list_signals(cur, hotel.slug, modules=["visibility"])}
    s = open_["vis.parity_gap_google:2026-10-02"]
    assert s["impact_minor"] == 38 * 14 * 100 and s["currency"] == "AED" and s["cadence"] == "daily"
    assert "vis.parity_gap_google:2026-10-29" not in open_          # 1.00% exactly: parity
    assert "vis.official_price_missing" not in open_


def test_official_missing_raises_weekly_signal(cur, hotel, serp, evidence_calls):
    from clefs.core import signals
    from clefs.modules.visibility import google_hotels

    serp.queue = [_router({}, page1=PAGE1_WITH_SELF)] * 4
    google_hotels.run_daily(cur, hotel)
    keys = {s["dedupe_key"]: s for s in signals.list_signals(cur, hotel.slug, modules=["visibility"])}
    assert keys["vis.official_price_missing"]["cadence"] == "weekly"


def test_serpapi_disabled_skips_everything(cur, hotel, serp):
    from clefs.modules.visibility import google_hotels, store

    store.update_profile(cur, hotel.slug, {"serpapi_enabled": False}, user_id=None)
    assert google_hotels.run_daily(cur, hotel) == {"skipped": "serpapi_disabled"}
    assert serp.calls == 0


def test_page1_is_one_billed_call_shared_with_demand(cur, hotel, serp, evidence_calls):
    """Demand's 04:00 fetch and Visibility's 05:30 job build identical params
    through google_hotels_area_params: page 1 is billed once."""
    from clefs.core import serpapi
    from clefs.modules.visibility import google_hotels

    serp.queue = [_router({}, page1=PAGE1_WITH_SELF)] * 10
    for ci, co in google_hotels.date_pairs(date(2026, 9, 29)):
        params = serpapi.google_hotels_area_params(hotel, q="hotels in Dubai", check_in=ci, check_out=co)
        serpapi.search("google_hotels", params, hotel_slug=hotel.slug, purpose="demand.comp_rates")
    assert serp.calls == 2
    google_hotels.run_daily(cur, hotel)
    page1_requests = [r for r in serp.requests if "property_token" not in r and "next_page_token" not in r]
    assert len(page1_requests) == 2                     # no second billed page-1 call
    cur.execute("SELECT count(*) AS n FROM serpapi_calls WHERE purpose = 'visibility.google_hotels' AND cache_hit")
    assert cur.fetchone()["n"] == 2


def test_parity_status_api(cur, hotel, serp, evidence_calls, booking_otb):
    from clefs.modules.visibility import api, google_hotels

    assert api.parity_status(cur, hotel.slug, stay_date=date(2026, 10, 2)) is None
    serp.queue = [_router({"2026-10-02": _details(450, [("Booking.com", 412)])})] * 6
    google_hotels.run_daily(cur, hotel)
    st = api.parity_status(cur, hotel.slug, stay_date=date(2026, 10, 2))
    assert st["official_present"] and st["official_minor"] == 45000 and st["lowest_ota_minor"] == 41200
    assert st["gap_minor"] == 3800 and st["gap_pct"] == "8.444" and st["currency"] == "AED"


def test_parity_fix_verifier(cur, hotel, serp, clock):
    from clefs.core import actions
    from clefs.modules.visibility import action_types  # noqa: F401  (registers the types)

    t = actions.get_action_type("visibility.parity_fix")
    assert t.executor is None and t.decide_perm == "visibility.act"
    act = {"id": None, "params": {"check_in": "2026-10-02", "check_out": "2026-10-03"}}

    def verify():
        return t.verifier(actions.ActionCtx(cur=cur, hotel=hotel, action=act, action_type=t, actor=None))

    serp.queue = [_details(450, [("Booking.com", 412)])]
    assert verify().status == "pending"                        # still 8.44%: re-checked daily until 7 d
    clock.advance(hours=2)                                      # past the 1 h freshness window
    serp.queue = [_details(450, [("Booking.com", 447)])]
    v = verify()
    assert v.status == "verified" and v.attribution == "caused"
    assert serp.requests[-1]["property_token"] == TOKEN and serp.requests[-1]["check_in_date"] == "2026-10-02"
    clock.advance(days=5)
    assert verify().status == "not_verified"                    # the night passed first
