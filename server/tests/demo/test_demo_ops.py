"""Demo seed, operations part: every planted case surfaces as the signal (or
row) it should, and no decoy fires (02 spec 9, 04 spec 7.4, 00-PLAN "Demo
dataset"). One seed per test (a few seconds at scale 0.1), so the
assertions are grouped per module."""

from __future__ import annotations

from datetime import date

import pytest

from clefs.seed import demo_uae
from clefs.seed.demo_uae import ops

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-ops"
PARTS = ["core", "core_catalog", "core_stays", "ops"]


@pytest.fixture
def seeded(cur):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.1, password="demo-pass-123")


def _plants(seeded: dict, module: str) -> list[dict]:
    return [p for p in seeded["plants"] if p["module"] == module]


def _check(cur, plants: list[dict]) -> None:
    missing = [(p["detector"], p["key"]) for p in plants if p["klass"] != "DECOY" and not ops.surfaced(cur, SLUG, p)]
    fired = [(p["detector"], p["key"]) for p in plants if p["klass"] == "DECOY" and ops.surfaced(cur, SLUG, p)]
    assert missing == [] and fired == []


def test_service_history_friday_cluster_l1_and_recovery(cur, seeded):
    plants = _plants(seeded, "service")
    assert {p["detector"] for p in plants} >= {"service.sla_breach_cluster", "service.l1_incident",
                                                "service.repeat_fault", "service.recovery_case"}
    assert sum(p["detector"] == "service.l1_incident" for p in plants) == 3
    _check(cur, plants)

    # Volume, channels and languages look like a real hotel.
    cur.execute("SELECT count(*) AS n, count(DISTINCT source) AS src, count(DISTINCT language) AS lang, "
                "count(*) FILTER (WHERE translated) AS tr FROM staff_requests WHERE hotel_slug = %s", (SLUG,))
    r = cur.fetchone()
    assert r["n"] >= 250 and r["src"] >= 6 and r["lang"] >= 3 and r["tr"] > 0

    # The pattern: Friday evenings breach far more often than the rest.
    cur.execute(
        """SELECT (EXTRACT(ISODOW FROM t) = 5 AND EXTRACT(HOUR FROM t) >= 18) AS fri,
                  avg((sla_ack_breached OR sla_resolve_breached)::int) AS rate
           FROM (SELECT *, to_timestamp("timestamp" / 1000.0) AT TIME ZONE 'Asia/Dubai' AS t FROM staff_requests
                 WHERE hotel_slug = %s AND intake_version IS NOT NULL) x GROUP BY 1""", (SLUG,))
    rate = {row["fri"]: float(row["rate"]) for row in cur.fetchall()}
    assert rate[True] >= 4 * rate[False], rate

    # Incidents: two closed with a note, one still open on the anchor.
    cur.execute("SELECT status, count(*) AS n FROM svc_incidents WHERE hotel_slug = %s GROUP BY 1", (SLUG,))
    inc = {row["status"]: row["n"] for row in cur.fetchall()}
    assert inc.get("closed", 0) >= 2 and inc.get("open", 0) >= 1

    # Routing found people: the roster covers every live episode.
    cur.execute("SELECT count(*) AS n FROM signals WHERE hotel_slug = %s AND kind = 'service.understaffed' "
                "AND dedupe_key NOT LIKE 'service.understaffed:security:%%'", (SLUG,))
    assert cur.fetchone()["n"] == 0


def test_amenity_plants_and_the_closet_fill_decoy(cur, seeded):
    plants = _plants(seeded, "amenities")
    assert {(p["detector"], p["klass"]) for p in plants} >= {
        ("am.variance.spike", "SIGNAL"), ("am.request_logging_gap", "SIGNAL"), ("am.sup_compliance", "SIGNAL"),
        ("am.variance.spike", "DECOY")}
    _check(cur, plants)
    # Only the two planted items spike (the Monday fill never does).
    cur.execute("SELECT i.key FROM signals s JOIN amn_items i ON s.dedupe_key = 'am.variance.spike:' || i.id "
                "WHERE s.hotel_slug = %s AND s.kind = 'am.variance.spike'", (SLUG,))
    assert sorted(r["key"] for r in cur.fetchall()) == ["DENTAL_KIT", "SLIPPERS"]
    # The issuance log came through core.ingest (one committed ISSUANCE batch).
    cur.execute("SELECT count(*) AS n FROM import_batches WHERE hotel_slug = %s AND kind = 'ISSUANCE' "
                "AND status = 'COMMITTED'", (SLUG,))
    assert cur.fetchone()["n"] == 1


def test_guest_journeys_consent_and_the_j5_ticket(cur, seeded):
    plants = _plants(seeded, "guests")
    assert {p["detector"] for p in plants} >= {
        "guests.j5_negative_ticket", "guests.registration_card_consent", "guests.j1_guest_stay", "guests.optout",
        "guests.meta_131049", "guests.j7_feedback"}
    _check(cur, plants)
    # Sends for recent stays, Expedia's J7 skipped as alias_expired.
    cur.execute("SELECT step, count(*) AS n FROM gst_journey_plans WHERE hotel_slug = %s GROUP BY 1", (SLUG,))
    steps = {r["step"]: r["n"] for r in cur.fetchall()}
    assert {"j1", "j4", "j5", "j7", "j8"} <= set(steps)
    cur.execute("SELECT count(*) AS n FROM gst_journey_plans p JOIN reservations r ON r.id = p.reservation_id "
                "WHERE p.hotel_slug = %s AND p.step = 'j7' AND r.channel_code = 'EXPEDIA' "
                "AND p.status NOT IN ('skipped', 'planned')", (SLUG,))
    assert cur.fetchone()["n"] == 0


def test_reviews_themes_link_and_drafts(cur, seeded):
    plants = _plants(seeded, "reviews")
    assert {p["detector"] for p in plants} >= {"reviews.linked_request", "reviews.negative_review",
                                                "reviews.drafted_replies"}
    _check(cur, plants)
    cur.execute("SELECT platform, language, count(*) AS n FROM rev_reviews WHERE hotel_slug = %s GROUP BY 1, 2",
                (SLUG,))
    got = {(r["platform"], r["language"]) for r in cur.fetchall()}
    assert {p for p, _l in got} == {"google", "tripadvisor", "booking"}
    assert {lang for _p, lang in got} >= {"en", "ar", "ru"}
    cur.execute("SELECT count(*) AS n FROM rev_derived WHERE hotel_slug = %s AND themes <> '[]'::jsonb", (SLUG,))
    assert cur.fetchone()["n"] >= 30


def test_upsell_funnel_orders_and_the_live_order(cur, seeded):
    plants = _plants(seeded, "upsell")
    assert [p["detector"] for p in plants] == ["upsell.funnel"]
    _check(cur, plants)
    funnel = plants[0]["subjects"][0]
    assert funnel["delivered"] > 0 and funnel["abandoned"] > 0
    # Every delivered order's F&B request was completed (no stale open work).
    cur.execute("SELECT count(*) AS n FROM ups_orders o JOIN staff_requests r ON r.order_id = o.id "
                "WHERE o.hotel_slug = %s AND o.status = 'delivered' AND r.state <> 'completed'", (SLUG,))
    assert cur.fetchone()["n"] == 0


def test_inquiry_plants_and_decoys(cur, seeded):
    plants = _plants(seeded, "inquiries")
    assert {(p["detector"], p["klass"]) for p in plants} >= {
        ("inq.unanswered", "SIGNAL"), ("inq.quote_awaiting_approval", "SIGNAL"), ("inq.leaked_to_ota", "SIGNAL"),
        ("inq.leaked_to_ota", "DECOY"), ("inq.verified_direct", "ROW"), ("inq.google_duplicate", "ROW")}
    _check(cur, plants)
    cur.execute("SELECT source, count(*) AS n FROM inq_inquiries WHERE hotel_slug = %s GROUP BY 1", (SLUG,))
    by_source = {r["source"]: r["n"] for r in cur.fetchall()}
    assert by_source == {"whatsapp": 22, "email": 12, "web_form": 14, "meta_leadgen": 5, "google_leadform": 4,
                         "phone_desk": 3}
    # The brief line: exactly the two planted inquiries are unanswered > 2 h.
    from clefs.core import clock
    from clefs.modules.inquiries import api as inq_api

    with clock.frozen(demo_uae.DemoCtx(cur=cur, slug=SLUG, anchor=ANCHOR).now):
        brief = inq_api.brief_summary(cur, SLUG, day=ANCHOR)
    assert brief["unanswered_over_2h"] == 2 and brief["money_at_risk_minor"] > 0, brief
