"""Meta template sync and the WhatsApp-less hotel (04 spec 3.6, 3.10, 3.11):
guests.template_category_check maps Meta's category onto each version, a
RED or PAUSED template demotes its step, the pending-approval signal
follows the library, and a hotel with no WhatsApp number sends email only."""

from __future__ import annotations

from clefs.core import actions, signals
from clefs.modules.guests.journeys import dispatch, maintenance, templates

from tests.modules.guests.test_journeys_support import (
    EMAIL, PHONE, add_contact, approve_templates, at, event, grant, make_res, plans, sends,
)


def _fake_meta(by_name: dict[str, dict]):
    """brief.api.wa_template_info stand-in: Meta's view per template name."""
    def info(cur, hotel_slug, *, name, language):
        return by_name.get(name)
    return info


# ─── Category mapping and quality ───


def test_category_check_maps_meta_category_and_demotes_red_quality(cur, hotel_uae, clock, monkeypatch):
    s = hotel_uae.slug
    clock.freeze(at(2026, 10, 1, 5, 10))
    templates.ensure_library(cur, s)
    monkeypatch.setattr(templates.brief_api, "wa_template_info", _fake_meta({
        "gst_j2_planning_v1": {"status": "APPROVED", "category": "MARKETING", "quality": "GREEN"},
        "gst_j5_daycheck_v1": {"status": "PAUSED", "category": "UTILITY", "quality": "RED"},
        "gst_j1_confirm_v1": {"status": "APPROVED", "category": "UTILITY", "quality": "GREEN"},
    }))
    out = maintenance.check_templates(cur, s)
    # Both languages of J2 were recategorised; J1 matches its expected category.
    assert sorted(out["mismatched"]) == ["gst_j2_planning_v1/ar", "gst_j2_planning_v1/en"]
    assert out["demoted"] == ["guests.journey_j5_daycheck"]
    assert actions.get_autonomy(cur, s, "guests.journey_j5_daycheck")["level"] != "auto"
    assert actions.get_autonomy(cur, s, "guests.journey_j1_confirm")["level"] == "auto"
    # A recategorised utility step now follows the marketing rules at send time.
    j2 = next(t for t in templates.list_templates(cur, s) if t["meta_name"] == "gst_j2_planning_v1"
              and t["channel"] == "whatsapp" and t["language"] == "en")
    assert (j2["category_expected"], j2["category_meta"]) == ("UTILITY", "MARKETING")
    assert templates.effective_category(j2) == "MARKETING"
    # The demotion is once per 7 days, however often the job runs.
    assert maintenance.check_templates(cur, s)["demoted"] == []


def test_unknown_to_meta_keeps_whatsapp_unsendable(cur, hotel_uae):
    s = hotel_uae.slug
    approve_templates(cur, s, channels=("whatsapp",), meta=None)
    # The Brief stub returns None: no WhatsApp version counts as Meta-approved.
    assert templates.approved_template(cur, s, "j1", "whatsapp", "en") is None


def test_pending_approval_signal_follows_the_library(cur, hotel_uae):
    s = hotel_uae.slug
    templates.ensure_library(cur, s)
    assert maintenance.check_templates(cur, s)["pending"] > 0
    open_kinds = [x["kind"] for x in signals.list_signals(cur, s, modules=["guests"])]
    assert "guests.templates_pending_approval" in open_kinds
    cur.execute("UPDATE gst_journey_templates SET approved_at = now() WHERE hotel_slug = %s", (s,))
    assert maintenance.check_templates(cur, s)["pending"] == 0
    open_kinds = [x["kind"] for x in signals.list_signals(cur, s, modules=["guests"])]
    assert "guests.templates_pending_approval" not in open_kinds


# ─── A hotel without WhatsApp (3.11 edge case) ───


def test_hotel_without_whatsapp_sends_email_only(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    approve_templates(cur, s)  # WhatsApp versions Meta-approved too: the missing number still blocks them
    clock.freeze(at(2026, 9, 1, 10, 5))
    both = make_res(cur, s, booked_at=at(2026, 9, 1, 10))
    grant(cur, s, add_contact(cur, s, both, PHONE), "whatsapp_updates")
    add_contact(cur, s, both, EMAIL, kind="email")
    phone_only = make_res(cur, s, booked_at=at(2026, 9, 1, 10))
    grant(cur, s, add_contact(cur, s, phone_only, "+971509999999"), "whatsapp_updates")
    event(cur, s, both, "created")
    event(cur, s, phone_only, "created")
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, both)[("j1", 1)]["channel"] == "email"
    assert plans(cur, s, phone_only)[("j1", 1)]["skip_reason"] == "no_eligible_contact"
    assert sends(cur, s, channel="whatsapp") == []
    assert len(sends(cur, s, channel="email")) == 1


# ─── Metrics compile (3.10) ───

JOURNEY_METRICS = ("guests.journey_sends", "guests.journey_delivery_rate", "guests.journey_response_rate",
                   "guests.j5_negative_rate", "guests.feedback_completion_rate", "guests.review_link_clicks",
                   "guests.direct_contact_capture_rate", "guests.optout_rate", "guests.journey_cost")


def test_journey_metrics_compile_with_every_dimension(cur, hotel_uae, clock, outbox):
    from datetime import date

    from clefs.core import metrics

    registered = {m.key: m for m in metrics.registered_metrics()}
    for key in JOURNEY_METRICS:
        m = registered[key]
        for grain, group in (("total", []), ("day", [d.key for d in m.dimensions[:2]]), ("month", [d.key for d in m.dimensions[2:4]])):
            q = metrics.MetricQuery(metric=key, start=date(2026, 9, 1), end=date(2026, 9, 30), grain=grain, group_by=group)
            sql, params = metrics.compile_metric(hotel_uae, q)
            cur.execute("EXPLAIN " + sql, params)
            assert cur.fetchall(), key
    # One J1 email counts once in journey_sends for its month.
    s = hotel_uae.slug
    approve_templates(cur, s, channels=("email",))
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s, booked_at=at(2026, 9, 1, 10))
    add_contact(cur, s, rid, EMAIL, kind="email")
    event(cur, s, rid, "created")
    dispatch.dispatch_hotel(cur, s)
    sql, params = metrics.compile_metric(hotel_uae, metrics.MetricQuery(
        metric="guests.journey_sends", start=date(2026, 9, 1), end=date(2026, 9, 30), filters={"step": ["j1"]}))
    cur.execute(sql, params)
    assert [float(r["value"]) for r in cur.fetchall()] == [1.0]
