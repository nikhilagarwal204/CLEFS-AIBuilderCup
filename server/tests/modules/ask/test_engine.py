"""The Ask pipeline end to end on the fake AI: strict registry tools, the
3-call budget, validation feedback, grounding with one regeneration and the
table-only fallback, output shape and the log row."""

from __future__ import annotations

import pytest

from clefs.modules.ask import engine, tools
from clefs.modules.ask.constants import MAX_TOOL_CALLS

from .conftest import call, grounded_answer, q


def test_answer_has_text_table_chart_and_trace(cur, seeded, ctx_for, fake_ai):
    ctx, user = ctx_for("owner")
    fake_ai.push([call("query_metric", **q("pg.commission_on_tax_aed", filters=[("channel", "eq", "Booking.com")],
                                            preset="last_month"))], grounded_answer)
    out = engine.ask(cur, ctx, "How much commission did Booking charge on taxes last month?")
    assert out["status"] == "answered", out
    assert out["grounded"] is True and out["regenerated"] is False
    tr = out["trace"]
    assert tr["metric"] == "pg.commission_on_tax_aed"
    assert tr["filters"] == [{"field": "channel", "values": ["BOOKING"]}]  # matched to the distinct values
    assert tr["period"]["from"] == "2026-08-01" and tr["period"]["to"] == "2026-08-31"
    assert out["chart_spec"]["type"] == "kpi"
    assert out["chart_spec"]["data"][0]["value"] == 385.0
    assert out["table"]["columns"][-1]["currency"] == "AED"
    assert "385.00" in out["answer_text"]
    cur.execute("SELECT * FROM ask_logs WHERE id = %s", (out["log_id"],))
    log = cur.fetchone()
    assert log["user_id"] == user["id"] and log["channel"] == "WEB" and log["status"] == "answered"
    assert log["tool_calls"][0]["name"] == "query_metric" and log["tool_calls"][0]["ok"] is True
    assert log["tokens"] == 40  # 2 fake calls x (10 in + 10 out), from ai_calls


def test_tools_are_strict_and_enums_follow_the_role(cur, seeded, ctx_for, fake_ai):
    owner, _ = ctx_for("owner")
    head, _ = ctx_for("dept_head")
    fake_ai.push([call("answer_unsupported", reason="n/a", suggestions=[])])
    engine.ask(cur, owner, "anything at all")
    qm = next(t for t in fake_ai.calls[0].tools if t["name"] == "query_metric")
    assert qm["strict"] is True and qm["parameters"]["additionalProperties"] is False
    assert set(qm["parameters"]["required"]) == set(qm["parameters"]["properties"])
    keys = qm["parameters"]["properties"]["metric"]["enum"]
    assert "fin.gop_aed" in keys and "pg.commission_aed" in keys and "ask.occupancy" in keys
    dims = qm["parameters"]["properties"]["dimensions"]["items"]["enum"]
    assert "guest_name" not in dims and "channel" in dims  # no guest-level field is ever offered

    fake_ai.push([call("answer_unsupported", reason="n/a", suggestions=[])])
    engine.ask(cur, head, "anything at all")
    keys = next(t for t in fake_ai.calls[1].tools if t["name"] == "query_metric")["parameters"]["properties"][
        "metric"]["enum"]
    assert "fin.gop_aed" not in keys and "pg.commission_aed" not in keys  # finance metrics hidden
    assert "am.leakage_aed" in keys


def test_a_bad_argument_goes_back_to_the_model_then_succeeds(cur, seeded, ctx_for, fake_ai):
    ctx, _ = ctx_for("owner")
    fake_ai.push(
        [call("query_metric", **q("pg.commission_aed", filters=[("channel", "eq", "Agoda")], preset="last_month"))],
        [call("query_metric", **q("pg.commission_aed", dims=["channel"], preset="last_month"))],
        grounded_answer,
    )
    out = engine.ask(cur, ctx, "Commission by channel last month")
    assert out["status"] == "answered"
    assert out["tool_calls"] == 2
    second = fake_ai.calls[1].input
    err = [i for i in second if i.get("type") == "function_call_output"][0]
    assert "not a known 'channel' value" in err["output"]


def test_at_most_three_tool_calls(cur, seeded, ctx_for, fake_ai):
    ctx, _ = ctx_for("owner")
    bad = [call("query_metric", **q("pg.commission_aed", dims=["amenity"]))]
    fake_ai.push(bad, bad, bad, bad)
    out = engine.ask(cur, ctx, "Commission by amenity")
    assert out["status"] == "error" and out["reason"] == "tool_call_budget"
    assert len(fake_ai.calls) == MAX_TOOL_CALLS
    assert out["tool_calls"] == MAX_TOOL_CALLS


def test_extra_calls_in_one_round_are_not_run(cur, seeded, ctx_for, fake_ai):
    ctx, _ = ctx_for("owner")
    many = [call("query_metric", **q("service.requests")) for _ in range(5)]
    fake_ai.push(many, grounded_answer)
    out = engine.ask(cur, ctx, "Requests this month")
    assert len(out["results"]) == 3
    cur.execute("SELECT tool_calls FROM ask_logs WHERE id = %s", (out["log_id"],))
    log = cur.fetchone()["tool_calls"]
    assert [c.get("error") for c in log[3:]] == ["tool_call_budget"] * 2


def test_ungrounded_answer_is_regenerated_once(cur, seeded, ctx_for, fake_ai):
    ctx, _ = ctx_for("owner")
    fake_ai.push([call("query_metric", **q("pg.commission_aed", preset="last_month"))],
                 {"answer_text": "Commission was AED 99,999 last month."}, grounded_answer)
    out = engine.ask(cur, ctx, "Commission last month")
    assert out["status"] == "answered" and out["regenerated"] is True and out["grounded"] is True
    retry = fake_ai.calls[2].input
    assert "99,999" in retry and "correction" in retry


def test_twice_ungrounded_falls_back_to_the_table(cur, seeded, ctx_for, fake_ai):
    ctx, _ = ctx_for("owner")
    fake_ai.push([call("query_metric", **q("pg.commission_aed", preset="last_month"))],
                 {"answer_text": "About AED 20,000."}, {"answer_text": "Roughly AED 15,000."})
    out = engine.ask(cur, ctx, "Commission last month")
    assert out["status"] == "table_only" and out["grounded"] is False and out["reason"] == "ungrounded"
    assert out["ungrounded"] == ["15,000"]
    assert out["answer_text"] == "OTA commission paid, 1-31 Aug 2026: AED 14,700.00."
    assert out["table"]["rows"] == [[14700.0]]


def test_model_declines(cur, seeded, ctx_for, fake_ai):
    ctx, _ = ctx_for("owner")
    fake_ai.push([call("answer_unsupported", reason="Clefs has no weather data.",
                       suggestions=["Occupancy next 30 days"])])
    out = engine.ask(cur, ctx, "Will it rain on Friday?")
    assert out["status"] == "unsupported" and out["unsupported"] is True
    assert out["suggestions"] == ["Occupancy next 30 days"] and out["table"] is None


def test_ai_switched_off_is_an_error_answer(cur, seeded, ctx_for):
    from clefs import ai

    ai.reset_backend()
    ctx, _ = ctx_for("owner")
    out = engine.ask(cur, ctx, "Commission last month")
    assert out["status"] == "error" and out["reason"] == "ai_disabled"


def test_validate_query_refuses_what_the_schema_forbids(cur, seeded, ctx_for):
    from clefs.modules.ask import catalog

    ctx, _ = ctx_for("owner")
    cat = catalog.by_key(ctx)
    for args, msg in (
        ({**q("stays.bookings", dims=["guest_name"])}, "guest-level"),
        ({**q("pg.commission_aed", dims=["channel", "room_type", "amenity"])}, "at most 2"),
        ({**q("nope.metric")}, "unknown metric"),
        ({**q("pg.commission_aed", frm="2020-01-01", to="2026-01-01")}, "3 years"),
        ({**q("pg.commission_aed", frm="2026-02-01", to="2026-01-01")}, "ends before"),
        ({**q("pg.commission_aed", filters=[("channel", "eq", ["BOOKING", "EXPEDIA"])])}, "exactly one"),
    ):
        with pytest.raises(tools.ToolArgError, match=msg):
            tools.validate_query(ctx, args, cat)


def test_a_broken_metric_is_one_failed_call_not_a_500(cur, seeded, ctx_for, fake_ai):
    from clefs.core import metrics
    from clefs.core.metrics import Metric

    metrics.register_metric(Metric(key="am.broken", label="Broken", description="x", unit="count",
                                   source="FROM ask_no_such_table t", hotel_column="t.hotel_slug",
                                   time_column="t.day", time_kind="date", measure="COUNT(*)"))
    ctx, _ = ctx_for("owner")
    fake_ai.push([call("query_metric", **q("am.broken"))],
                 [call("answer_unsupported", reason="That number is unavailable right now.", suggestions=[])])
    out = engine.ask(cur, ctx, "Broken count this month")
    assert out["status"] == "unsupported"
    cur.execute("SELECT tool_calls FROM ask_logs WHERE id = %s", (out["log_id"],))
    assert cur.fetchone()["tool_calls"][0]["error"] == "metric_failed"
