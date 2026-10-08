"""The template registry: definitions (UTILITY, promo-free, within Meta's
limits), the 1,024 body budget, template_info lookup order, submit, the daily
sync with its once-per-change alerts, and the brief.api doors."""

from __future__ import annotations

import pytest

from clefs import notify
from clefs.core.errors import ClefsError
from clefs.modules.brief import api as brief_api
from clefs.modules.brief import templates


@pytest.fixture
def graph():
    """A fake Graph API: graph.templates is what GET message_templates returns,
    graph.calls records every request."""

    class _G:
        templates: list[dict] = []
        calls: list[tuple] = []

        def __call__(self, method, path, params, json):
            self.calls.append((method, path, params, json))
            if method == "GET":
                return {"data": list(self.templates)}
            return {"id": "9001", "status": "PENDING", "category": json["category"]}

    g = _G()
    g.templates, g.calls = [], []
    templates.set_graph(g)
    yield g
    templates.set_graph(None)


# ─── Definitions and variables ───


def test_brief_definitions_are_utility_promo_free_and_valid():
    for name, d in templates.definitions().items():
        assert d["category"] == "UTILITY"
        assert templates.lint_definition(d["components"]) == [], name
        notify.validate_template_definition(d["components"])
    body = next(c for c in templates.definitions()["owner_brief_v1"]["components"] if c["type"] == "BODY")["text"]
    assert body.count("{{") == 7
    assert templates.lint_definition([{"type": "BODY", "text": "Special offer: 20% off this deal"}])


def _run(n_items: int, *, title: str = "x" * 400, verified: str = "Verified: AED 99,999 this month " * 3) -> dict:
    items = [{"item_no": i, "title": title, "impact_minor": 99_999_999, "currency": "AED", "confidence": 0.9,
              "urgency": 1.0, "deadline_at": "2026-10-14T10:00:00+00:00", "responsible_label": "Finance team"}
             for i in range(1, n_items + 1)]
    return {"brief_date": "2026-10-01", "currency": "AED", "at_stake_minor": 999_999_999_99, "items": items,
            "overdue": [], "verified": {"text": verified}}


@pytest.mark.parametrize("n", [0, 2, 5, 7])
def test_worst_case_body_fits_1024_and_has_seven_clean_params(hotel_uae, n):
    v = templates.owner_brief_variables(_run(n), hotel_uae)
    params = v["params"]
    assert len(params) == 7 and sum(len(p) for p in params) <= 1024
    assert all(p and "\n" not in p and "    " not in p for p in params)
    assert len(v["header"]) <= 60


def test_verified_line_takes_the_first_free_slot_and_titles_are_linted(hotel_uae):
    run = _run(2, title="Close discount plans for Friday", verified="Verified: AED 3,050 this month")
    params = templates.owner_brief_variables(run, hotel_uae)["params"]
    assert params[0] == "2" and "restrict rate plans" in params[2]
    assert params[4] == "Verified: AED 3,050 this month" and params[5:] == ["-", "-"]
    assert "discount" not in " ".join(params).lower()


def test_fit_shortens_the_longest_parameter():
    out = templates.fit(["a" * 600, "b" * 600, "c"], limit=1024)
    assert sum(len(x) for x in out) <= 1024 and out[2] == "c"


# ─── Registry and doors ───


def test_template_info_prefers_the_hotel_row_then_the_platform(cur, hotel_uae):
    assert brief_api.wa_template_info(cur, hotel_uae.slug, name="owner_brief_v1", language="en") is None
    templates.upsert(cur, None, "owner_brief_v1", "en", category="UTILITY", status="APPROVED")
    info = brief_api.wa_template_info(cur, hotel_uae.slug, name="owner_brief_v1", language="en")
    assert info["status"] == "APPROVED" and info["category"] == "UTILITY" and info["scope"] == "platform"
    templates.upsert(cur, hotel_uae.slug, "owner_brief_v1", "en", category="MARKETING", status="PAUSED")
    info = brief_api.wa_template_info(cur, hotel_uae.slug, name="owner_brief_v1", language="en")
    assert info["status"] == "PAUSED" and info["scope"] == "hotel"
    assert not templates.is_sendable(info)


def test_submit_needs_a_connected_waba_and_refuses_promo_words(cur, hotel_uae, graph):
    comps = [{"type": "BODY", "text": "Your stay at {{1}} is confirmed.", "example": {"body_text": [["Clefs"]]}}]
    with pytest.raises(ClefsError) as e:
        brief_api.submit_template(cur, hotel_uae.slug, name="stay_confirm", language="en", category="UTILITY",
                                  components=comps)
    assert e.value.code == "not_connected"
    cur.execute("INSERT INTO brf_wa_accounts (hotel_slug, waba_id) VALUES (%s, '123456')", (hotel_uae.slug,))
    with pytest.raises(ClefsError) as e:
        brief_api.submit_template(cur, hotel_uae.slug, name="stay_confirm", language="en", category="UTILITY",
                                  components=[{"type": "BODY", "text": "A special offer for you"}])
    assert e.value.code == "promo_words" and not graph.calls
    out = brief_api.submit_template(cur, hotel_uae.slug, name="stay_confirm", language="en", category="UTILITY",
                                    components=comps)
    assert out == {"name": "stay_confirm", "language": "en", "status": "PENDING", "meta_template_id": "9001"}
    assert graph.calls[-1][:2] == ("POST", "123456/message_templates")
    with pytest.raises(ClefsError) as e:
        brief_api.submit_template(cur, hotel_uae.slug, name="Bad Name", language="en", category="UTILITY",
                                  components=comps)
    assert e.value.code == "invalid_template"


def test_submit_outside_live_mode_records_pending_without_calling(cur, hotel_uae):
    cur.execute("INSERT INTO brf_wa_accounts (hotel_slug, waba_id) VALUES (%s, '123456')", (hotel_uae.slug,))
    out = brief_api.submit_template(cur, hotel_uae.slug, name="stay_confirm", language="en", category="MARKETING",
                                    components=[{"type": "BODY", "text": "Summer offer"}])
    assert out["status"] == "PENDING" and out["meta_template_id"] is None


def test_sync_stores_meta_view_and_alerts_once_per_change(cur, hotel_uae, graph):
    from clefs.core import signals

    cur.execute("INSERT INTO brf_wa_accounts (hotel_slug, waba_id) VALUES (%s, '123456')", (hotel_uae.slug,))
    templates.upsert(cur, hotel_uae.slug, "inq_quote", "en", category_expected="UTILITY", category="UTILITY",
                     status="PENDING")
    graph.templates = [{"id": "1", "name": "inq_quote", "language": "en", "status": "APPROVED", "category": "UTILITY",
                        "quality_score": {"score": "GREEN"}}]
    assert templates.sync(cur, hotel_uae.slug)["alerts"] == []
    assert brief_api.wa_template_info(cur, hotel_uae.slug, name="inq_quote", language="en")["quality"] == "GREEN"
    graph.templates[0].update(category="MARKETING")
    alerts = templates.sync(cur, hotel_uae.slug)["alerts"]
    assert [a["category"] for a in alerts] == ["MARKETING"]
    assert templates.sync(cur, hotel_uae.slug)["alerts"] == []  # same state: no second alert
    graph.templates[0].update(status="PAUSED")
    assert [a["status"] for a in templates.sync(cur, hotel_uae.slug)["alerts"]] == ["PAUSED"]
    open_alerts = [s for s in signals.list_signals(cur, hotel_uae.slug) if s["kind"] == "wa.template_alert"]
    assert len(open_alerts) == 2
    info = brief_api.wa_template_info(cur, hotel_uae.slug, name="inq_quote", language="en")
    assert info["category_expected"] == "UTILITY"  # a sync never rewrites what we built it as


def test_sync_skips_without_live_mode_or_waba(cur, hotel_uae):
    assert templates.sync(cur, hotel_uae.slug)["skipped"] == "not_connected"
    cur.execute("INSERT INTO brf_wa_accounts (hotel_slug, waba_id) VALUES (%s, '123456')", (hotel_uae.slug,))
    assert templates.sync(cur, hotel_uae.slug)["skipped"] == "not_live"


def test_template_sync_job_registers_platform_templates(cur, run_jobs, clock, graph):
    from datetime import datetime, timezone

    clock.freeze(datetime(2026, 10, 1, 4, 31, tzinfo=timezone.utc))
    run_jobs(["wa.template_sync"], scheduled=True)
    names = {t["name"] for t in templates.list_templates(cur, None)}
    assert {"owner_brief_v1", "weekly_report_v1"} <= names


def test_wa_billing_status(cur, hotel_uae):
    assert brief_api.wa_billing_status(cur, hotel_uae.slug)["ready"] is None
    cur.execute("INSERT INTO brf_wa_accounts (hotel_slug, waba_id, billing_ready, billing_checked_at) "
                "VALUES (%s, '1', TRUE, NOW())", (hotel_uae.slug,))
    assert brief_api.wa_billing_status(cur, hotel_uae.slug)["ready"] is True


def test_service_message_metric_compiles(cur, hotel_uae):
    from datetime import date

    from clefs.core import metrics

    for grain, group in (("total", []), ("day", ["kind"])):
        q = metrics.MetricQuery(metric="wa.service_msgs_month", start=date(2026, 10, 1), end=date(2026, 10, 31),
                                grain=grain, group_by=group)
        sql, params = metrics.compile_metric(hotel_uae, q)
        cur.execute("EXPLAIN " + sql, params)
        assert cur.fetchall()
