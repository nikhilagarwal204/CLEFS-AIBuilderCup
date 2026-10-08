"""AI answer fact errors: comparators, extraction over a batch, signals and the OTA-fix verifier (04 spec 5.5, 5.7)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from clefs.modules.visibility import facts

NOW = datetime(2026, 9, 28, 9, 0, tzinfo=ZoneInfo("Asia/Dubai"))
KB = [{"fact_key": "checkin_time", "value": "14:00"}, {"fact_key": "parking", "value": "paid"}]

# ─── Comparators ───


@pytest.mark.parametrize(("key", "claimed", "truth", "err"), [
    ("checkin_time", "12:00", {"checkin_time": "14:00"}, "wrong_value"),
    ("checkin_time", "14:15", {"checkin_time": "14:00"}, None),  # under 30 minutes
    ("checkin_time", "14:30", {"checkin_time": "14:00"}, "wrong_value"),
    ("parking", "no", {"parking": "paid"}, "false_absent"),
    ("parking", "free", {"parking": "paid"}, "wrong_value"),
    ("parking", "yes", {"parking": "paid"}, None),
    ("pool", "yes", {}, "unverifiable"),
    ("pool", "closed", {"pool": "true"}, "false_closed"),
    ("star_rating", "5", {"star_rating": "4"}, "wrong_star"),
    ("price_band", "AED 900", {"price_band": "AED 500.00"}, "price_band_off"),
    ("price_band", "AED 650", {"price_band": "AED 500.00"}, None),
    ("price_band", "USD 900", {"price_band": "AED 500.00"}, None),  # another currency: not compared
])
def test_comparators(key, claimed, truth, err):
    assert facts.compare(key, facts.norm_claim(key, claimed), truth, currency="AED") == err


def test_time_normalisation():
    assert facts.norm_claim("checkin_time", "2 pm") == "14:00"
    assert facts.norm_claim("checkin_time", "noon") == "12:00"
    assert facts.norm_claim("checkin_time", "12pm") == "12:00"
    assert facts.norm_claim("star_rating", "a 4-star hotel") == "4"


# ─── Over a batch ───


@pytest.fixture
def world(cur, hotel_uae, clock, fake_ai, monkeypatch):
    from clefs.modules.concierge import api as concierge_api
    from clefs.modules.visibility import store

    clock.freeze(NOW)
    monkeypatch.setattr(concierge_api, "kb_facts", lambda cur, slug, **kw: KB)
    store.update_profile(cur, hotel_uae.slug, {"website_url": "https://testhoteldubai.example"}, user_id=None)
    cur.execute("INSERT INTO vis_ai_batches (hotel_slug, kind, week, status) VALUES (%s, 'weekly', '2026-09-28', "
                "'ready') RETURNING id", (hotel_uae.slug,))
    return {"hotel": hotel_uae, "batch": cur.fetchone()["id"], "ai": fake_ai}


def _run(cur, slug, batch, *, answer, intent="generic", mentioned=True, engine="gemini_web_search",
         domains=("booking.com",), key="ae.en.generic.1"):
    cur.execute(
        """
        INSERT INTO vis_ai_runs (batch_id, batch_kind, hotel_slug, prompt_key, prompt_intent, engine, run_at, status,
            answer_text, self_mentioned, cited_domains)
        VALUES (%s, 'weekly', %s, %s, %s, %s, NOW(), 'ok', %s, %s, %s) RETURNING id
        """,
        (batch, slug, key, intent, engine, answer, mentioned, list(domains)),
    )
    return cur.fetchone()["id"]


def test_detector_over_a_batch(cur, world):
    from clefs.modules.visibility.facts import FactClaim, FactClaims

    slug, batch = world["hotel"].slug, world["batch"]
    r1 = _run(cur, slug, batch, answer="Test Hotel Dubai is lovely. Check-in at Test Hotel Dubai is at noon. "
                                       "Other hotels are fine.")
    _run(cur, slug, batch, answer="Try the Palm Vista.", mentioned=False)
    r3 = _run(cur, slug, batch, answer="There is no parking and a pool.", intent="fact_check",
              engine="serpapi_ai_mode", key="ae.en.fact.parking")
    world["ai"].push(
        FactClaims(facts=[FactClaim(fact_key="checkin_time", value="12:00", quote="Check-in ... is at noon.")]),
        FactClaims(facts=[FactClaim(fact_key="parking", value="no", quote="There is no parking"),
                          FactClaim(fact_key="pool", value="yes", quote="and a pool")]),
    )
    assert facts.check_pending(cur, world["hotel"], batch_id=batch, limit=6) == 2
    # only the hotel's sentences are sent for a discovery answer
    sent = world["ai"].calls[0].input
    assert "noon" in sent and "Other hotels" not in sent
    cur.execute("SELECT fact_key, error_type, claimed_value, truth_value, run_ids, engines FROM vis_ai_facts "
                "WHERE hotel_slug = %s ORDER BY fact_key", (slug,))
    got = {r["fact_key"]: dict(r) for r in cur.fetchall()}
    assert got["checkin_time"]["error_type"] == "wrong_value" and got["checkin_time"]["run_ids"] == [r1]
    assert got["parking"]["error_type"] == "false_absent" and got["parking"]["engines"] == ["serpapi_ai_mode"]
    assert got["pool"]["error_type"] == "unverifiable" and got["parking"]["run_ids"] == [r3]
    assert facts.check_pending(cur, world["hotel"], batch_id=batch, limit=6) == 0  # everything is checked


def test_occurrences_accumulate_by_fingerprint_and_fixed_recurs(cur, world):
    slug = world["hotel"].slug
    kw = dict(fact_key="checkin_time", claimed="12:00", quote="q", truth_value="14:00", error_type="wrong_value",
              cited_domains=["booking.com"])
    facts.record_fact(cur, slug, engine="gemini_web_search", run_id=1, **kw)
    row = facts.record_fact(cur, slug, engine="serpapi_ai_mode", run_id=2, **kw)
    assert row["occurrences"] == 2 and sorted(row["engines"]) == ["gemini_web_search", "serpapi_ai_mode"]
    cur.execute("UPDATE vis_ai_facts SET status = 'fixed' WHERE id = %s", (row["id"],))
    assert facts.record_fact(cur, slug, engine="gemini_web_search", run_id=3, **kw)["status"] == "recurred"


def test_signal_routes_the_fix_by_cited_domain(cur, world):
    from clefs.core import signals as core_signals
    from clefs.modules.visibility import api, signals

    slug = world["hotel"].slug
    facts.record_fact(cur, slug, fact_key="checkin_time", claimed="12:00", quote="q", truth_value="14:00",
                      error_type="wrong_value", engine="gemini_web_search", run_id=1, cited_domains=["booking.com"])
    facts.record_fact(cur, slug, fact_key="parking", claimed="no", quote="q", truth_value="paid",
                      error_type="false_absent", engine="gemini_web_search", run_id=1,
                      cited_domains=["testhoteldubai.example"])
    facts.record_fact(cur, slug, fact_key="pool", claimed="yes", quote="q", truth_value=None,
                      error_type="unverifiable", engine="gemini_web_search", run_id=1, cited_domains=[])
    assert signals.ai_fact_errors(cur, world["hotel"]) == 3
    rows = core_signals.list_signals(cur, slug, modules=["visibility"])
    types = sorted(r["recommended_action"]["type"] for r in rows if r["kind"] == "vis.ai_fact_error")
    assert types == ["visibility.add_kb_faq", "visibility.ota_listing_fix", "visibility.site_fact_fix"]
    assert all(r["cadence"] == "weekly" for r in rows)
    errs = [e for e in api.open_fact_errors(cur, slug) if e.get("source") == "ai_answers"]
    assert {e["fact_key"] for e in errs} == {"checkin_time", "parking"}  # unverifiable is a KB gap, not an error


def test_ota_listing_fix_verifier_needs_8_clean_answers(cur, world):
    from types import SimpleNamespace

    from clefs.modules.visibility import ai_verify

    slug, batch = world["hotel"].slug, world["batch"]
    row = facts.record_fact(cur, slug, fact_key="parking", claimed="no", quote="q", truth_value="paid",
                            error_type="false_absent", engine="gemini_web_search", run_id=None,
                            cited_domains=["booking.com"])
    fixed_at = datetime(2026, 9, 1, tzinfo=ZoneInfo("UTC"))
    ctx = SimpleNamespace(cur=cur, hotel_slug=slug, action={"executed_at": fixed_at},
                          params={"fact_key": "parking", "fingerprint": row["fingerprint"],
                                  "engines": ["gemini_web_search"]})

    def answers(n):
        ids = [_run(cur, slug, batch, answer="Parking is paid.", intent="fact_check", key="ae.en.fact.parking")
               for _ in range(n)]
        cur.execute("UPDATE vis_ai_runs SET facts_checked_at = NOW() WHERE id = ANY(%s)", (ids,))
        return ids

    answers(7)
    assert ai_verify.verify_ota_listing_fix(ctx).status == "pending"
    wrong = answers(1)
    cur.execute("UPDATE vis_ai_facts SET run_ids = run_ids || %s::bigint[] WHERE id = %s", (wrong, row["id"]))
    assert ai_verify.verify_ota_listing_fix(ctx).status == "not_verified"
    cur.execute("UPDATE vis_ai_facts SET run_ids = '{}' WHERE id = %s", (row["id"],))
    assert ai_verify.verify_ota_listing_fix(ctx).status == "verified"
    cur.execute("SELECT status FROM vis_ai_facts WHERE id = %s", (row["id"],))
    assert cur.fetchone()["status"] == "fixed"


def test_price_band_truth_is_the_trailing_30_day_adr(cur, world):
    hotel = world["hotel"]
    cur.execute(
        """
        INSERT INTO reservations (hotel_slug, source_system, pms_res_id, check_in, check_out, nights, currency, status)
        VALUES (%s, 'MANUAL', 'r1', '2026-09-10', '2026-09-12', 2, 'AED', 'CHECKED_OUT') RETURNING id
        """,
        (hotel.slug,),
    )
    rid = cur.fetchone()["id"]
    for d, rate in (("2026-09-10", 40000), ("2026-09-11", 60000)):
        cur.execute("INSERT INTO reservation_nights (hotel_slug, reservation_id, stay_date, rate_minor, currency, "
                    "stayed) VALUES (%s, %s, %s, %s, 'AED', TRUE)", (hotel.slug, rid, d, rate))
    truth = facts.ai_truth(cur, hotel)
    assert truth["price_band"] == "AED 500.00" and truth["star_rating"] == "4" and truth["checkin_time"] == "14:00"
