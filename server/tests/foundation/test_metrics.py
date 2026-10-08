"""
Metrics registry (01 spec E3): registration refuses unsafe fragments, a
query only chooses registry keys, values are parameters, the hotel filter is
always there, buckets use the hotel's timezone, comparison periods line up,
permissions and the 5 s timeout hold, and the HTTP routes work.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from clefs.core import actions, metrics, signals
from clefs.core.errors import ClefsError
from clefs.core.metrics import Dimension, Metric, MetricQuery
from clefs.core.signals import SignalIn
from clefs.core.time import to_ms


def _metric(**kw) -> Metric:
    base = dict(
        key="core.test_requests",
        label="Requests",
        description="Legacy requests",
        unit="count",
        source="FROM staff_requests r",
        hotel_column="r.hotel_slug",
        time_column="r.timestamp",
        time_kind="epoch_ms",
        measure="COUNT(*)",
        dimensions=(Dimension("category", "Category", "r.category"), Dimension("room", "Room", "r.room")),
    )
    base.update(kw)
    return Metric(**base)


@pytest.fixture
def requests_metric():
    m = metrics.register_metric(_metric())
    yield m
    metrics.unregister_metric(m.key)


def _request(cur, slug: str, at: datetime, *, category: str = "Housekeeping", room: str = "101") -> None:
    cur.execute(
        "INSERT INTO staff_requests (hotel_slug, room, type, category, timestamp) VALUES (%s, %s, 'Towels', %s, %s)",
        (slug, room, category, to_ms(at)),
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"source": "FROM staff_requests r; DROP TABLE hotels"},
        {"measure": "COUNT(*) -- sneaky"},
        {"where": "r.room = '1' /* x */"},
        {"where": "r.room = %(evil)s"},
        {"measure": "100 % 3"},
        {"source": "staff_requests r"},
        {"unit": "furlongs"},
        {"dimensions": (Dimension("Bad Key", "x", "r.room"),)},
        {"dimensions": (Dimension("value", "x", "r.room"),)},
        {"dimensions": (Dimension("a", "x", "r.room; DELETE FROM hotels"),)},
    ],
)
def test_registration_refuses_unsafe_or_malformed_fragments(bad):
    with pytest.raises(ValueError):
        metrics.register_metric(_metric(key="core.test_bad", **bad))
    assert metrics.metric_origin("core.test_bad") is None


def test_percent_signs_are_allowed_when_escaped_or_named():
    m = metrics.register_metric(_metric(key="core.test_pct", measure="100.0 * COUNT(*) FILTER (WHERE r.category LIKE 'H%%') / NULLIF(COUNT(*), 0)"))
    metrics.unregister_metric(m.key)


def test_queries_choose_keys_only_and_values_stay_parameters(cur, hotel_uae, owner_ctx, requests_metric):
    with pytest.raises(ClefsError) as exc:
        metrics.compile_metric(hotel_uae, MetricQuery(metric="core.test_requests; DROP TABLE hotels", start=date(2026, 9, 1), end=date(2026, 9, 2)))
    assert exc.value.status == 404
    with pytest.raises(ClefsError) as exc:
        metrics.compile_metric(hotel_uae, MetricQuery(metric="core.test_requests", start=date(2026, 9, 1), end=date(2026, 9, 2), group_by=["r.category"]))
    assert exc.value.code == "unknown_dimension"
    with pytest.raises(ClefsError) as exc:
        metrics.compile_metric(hotel_uae, MetricQuery(metric="core.test_requests", start=date(2026, 9, 1), end=date(2026, 9, 2), filters={"1=1 OR room": ["x"]}))
    assert exc.value.code == "unknown_dimension"
    with pytest.raises(ClefsError) as exc:
        metrics.compile_metric(hotel_uae, MetricQuery(metric="core.test_requests", start=date(2026, 9, 2), end=date(2026, 9, 1)))
    assert exc.value.code == "invalid_range"

    evil = "x') OR 1=1; DROP TABLE hotels; --"
    q = MetricQuery(metric="core.test_requests", start=date(2026, 9, 1), end=date(2026, 9, 30), filters={"category": [evil]})
    sql, params = metrics.compile_metric(hotel_uae, q)
    assert evil not in sql and params["f0"] == [evil]
    assert "(r.hotel_slug) = %(hotel)s" in sql and params["hotel"] == hotel_uae.slug
    _request(cur, hotel_uae.slug, datetime(2026, 9, 10, 8, tzinfo=timezone.utc))
    out = metrics.run_metric(owner_ctx, q)
    assert out["rows"] == [{"bucket": None, "group": {}, "value": 0, "compare_value": None}]
    cur.execute("SELECT COUNT(*) AS n FROM hotels")
    assert cur.fetchone()["n"] > 0


def test_the_hotel_filter_isolates_tenants(cur, hotel_uae, owner_ctx, requests_metric):
    at = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)
    _request(cur, hotel_uae.slug, at)
    _request(cur, "hotel-nandan", at)
    _request(cur, "hotel-nandan", at)
    out = metrics.run_metric(owner_ctx, MetricQuery(metric="core.test_requests", start=date(2026, 9, 1), end=date(2026, 9, 30)))
    assert out["total"] == 1


def test_day_buckets_follow_the_hotel_timezone(cur, hotel_uae, owner_ctx, requests_metric):
    # 19:00 UTC on the 30th is 23:00 in Dubai (the 30th); 21:30 UTC is 01:30 on the 1st.
    _request(cur, hotel_uae.slug, datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc))
    _request(cur, hotel_uae.slug, datetime(2026, 9, 30, 21, 30, tzinfo=timezone.utc), category="Maintenance")
    _request(cur, hotel_uae.slug, datetime(2026, 9, 29, 20, 30, tzinfo=timezone.utc))  # 00:30 on the 30th in Dubai
    _request(cur, hotel_uae.slug, datetime(2026, 9, 29, 19, 59, tzinfo=timezone.utc))  # 23:59 on the 29th: outside the range
    q = MetricQuery(metric="core.test_requests", start=date(2026, 9, 30), end=date(2026, 10, 1), grain="day")
    out = metrics.run_metric(owner_ctx, q)
    assert [(r["bucket"], r["value"]) for r in out["rows"]] == [("2026-09-30", 2), ("2026-10-01", 1)]
    assert out["total"] == 3
    grouped = metrics.run_metric(owner_ctx, q.model_copy(update={"group_by": ["category"]}))
    assert [(r["bucket"], r["group"]["category"], r["value"]) for r in grouped["rows"]] == [
        ("2026-09-30", "Housekeeping", 2),
        ("2026-10-01", "Maintenance", 1),
    ]
    filtered = metrics.run_metric(owner_ctx, q.model_copy(update={"filters": {"category": ["Maintenance"]}, "grain": "total"}))
    assert filtered["total"] == 1

    # The same instants through a TIMESTAMPTZ metric bucket identically.
    for i, at in enumerate([datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc), datetime(2026, 9, 30, 21, 30, tzinfo=timezone.utc)]):
        s = signals.upsert_signal(cur, hotel_uae.slug, SignalIn(module="core", kind="core.x", dedupe_key=f"t{i}", title="S",
                                                                confidence=0.5, section="money_lost", cadence="daily"))
        cur.execute("UPDATE signals SET first_seen_at = %s WHERE id = %s", (at, s["id"]))
    sig = metrics.run_metric(owner_ctx, MetricQuery(metric="core.signals_raised", start=date(2026, 9, 30), end=date(2026, 10, 1), grain="day"))
    assert [(r["bucket"], r["value"]) for r in sig["rows"]] == [("2026-09-30", 1), ("2026-10-01", 1)]


def test_compare_previous_period_and_previous_year(cur, hotel_uae, owner_ctx, requests_metric):
    for d in (date(2026, 9, 3), date(2026, 9, 3), date(2026, 9, 5)):
        _request(cur, hotel_uae.slug, datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc))
    _request(cur, hotel_uae.slug, datetime(2026, 8, 31, 8, tzinfo=timezone.utc))  # previous 7-day period
    _request(cur, hotel_uae.slug, datetime(2025, 9, 3, 8, tzinfo=timezone.utc))  # a year earlier
    q = MetricQuery(metric="core.test_requests", start=date(2026, 9, 1), end=date(2026, 9, 7), compare="prev_period")
    out = metrics.run_metric(owner_ctx, q)
    assert (out["total"], out["compare_total"]) == (3, 1)
    daily = metrics.run_metric(owner_ctx, q.model_copy(update={"grain": "day", "compare": "prev_year"}))
    assert {r["bucket"]: (r["value"], r["compare_value"]) for r in daily["rows"]} == {"2026-09-03": (2, 1), "2026-09-05": (1, None)}


def test_week_and_month_comparisons_line_up_by_position(cur, hotel_uae, owner_ctx, requests_metric):
    """Week/month buckets are date_trunc'd, so the comparison buckets pair
    with the current ones by position; shifting them by the period length
    used to land between bucket keys and leave compare_value always null."""
    def at(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc)

    for d in (date(2026, 9, 8), date(2026, 9, 8), date(2026, 9, 15)):  # weeks of Mon 7 and Mon 14 Sep
        _request(cur, hotel_uae.slug, at(d))
    for d in (date(2026, 8, 4), date(2026, 8, 11), date(2026, 8, 11), date(2026, 8, 11)):  # weeks of 3 and 10 Aug
        _request(cur, hotel_uae.slug, at(d))
    _request(cur, hotel_uae.slug, at(date(2025, 9, 20)))  # September a year earlier
    # 1-30 Sep (30 days, weeks from Mon 31 Aug) against 2-31 Aug (weeks from Mon 27 Jul):
    # a 30-day shift of a Monday is a Wednesday, which is no bucket at all.
    weekly = metrics.run_metric(owner_ctx, MetricQuery(
        metric="core.test_requests", start=date(2026, 9, 1), end=date(2026, 9, 30), grain="week", compare="prev_period",
    ))
    assert [(r["bucket"], r["value"], r["compare_value"]) for r in weekly["rows"]] == [
        ("2026-09-07", 2, 1),
        ("2026-09-14", 1, 3),
    ]
    monthly = metrics.run_metric(owner_ctx, MetricQuery(
        metric="core.test_requests", start=date(2026, 9, 1), end=date(2026, 9, 30), grain="month", compare="prev_year",
    ))
    assert [(r["bucket"], r["value"], r["compare_value"]) for r in monthly["rows"]] == [("2026-09-01", 3, 1)]
    prev_month = metrics.run_metric(owner_ctx, MetricQuery(
        metric="core.test_requests", start=date(2026, 9, 1), end=date(2026, 9, 30), grain="month", compare="prev_period",
    ))
    assert [(r["bucket"], r["value"], r["compare_value"]) for r in prev_month["rows"]] == [("2026-09-01", 3, 4)]


def test_money_averages_round_half_up_instead_of_truncating(cur, hotel_uae, owner_ctx):
    m = metrics.register_metric(_metric(key="core.test_avg_money", unit="money", measure="AVG(2.5)", currency="hotel"))
    try:
        _request(cur, hotel_uae.slug, datetime(2026, 9, 10, 8, tzinfo=timezone.utc))
        out = metrics.run_metric(owner_ctx, MetricQuery(metric=m.key, start=date(2026, 9, 1), end=date(2026, 9, 30)))
    finally:
        metrics.unregister_metric(m.key)
    assert out["total"] == 0.03  # 2.5 fils -> 3 fils (ROUND_HALF_UP), never truncated to 2


def test_verified_value_never_adds_minor_units_of_another_currency(cur, hotel_uae, owner_ctx):
    t = actions.ActionType(key="core.test_money_ccy", module="core", label="Money", counts_toward_verified_profit=True)
    actions.register_action_type(t)
    try:
        ids = []
        for minor in (10000, 5000):
            a = actions.create_action(cur, hotel_uae.slug, type=t.key, title="x", actor=None)
            actions.decide(cur, a["id"], "approve", actor=owner_ctx)
            actions.mark_done(cur, a["id"], actor=owner_ctx)
            actions.record_verification(cur, a["id"], verified=True, outcome_minor=minor)
            ids.append(a["id"])
        # A row written in another currency (older code, a direct insert) is left out, not added as fils.
        cur.execute("UPDATE actions SET currency = 'USD' WHERE id = %s", (ids[1],))
        today = datetime.now(timezone.utc).date()
        out = metrics.run_metric(owner_ctx, MetricQuery(metric="core.verified_value", start=date(2026, 1, 1), end=max(today, date(2026, 12, 31))))
    finally:
        actions.unregister_action_type(t.key)
    assert out["total"] == 100.0


def test_money_metrics_return_major_units_in_the_hotel_currency(cur, hotel_uae, owner_ctx):
    t = actions.ActionType(key="core.test_money", module="core", label="Money", counts_toward_verified_profit=True)
    actions.register_action_type(t)
    try:
        a = actions.create_action(cur, hotel_uae.slug, type=t.key, title="x", actor=None)
        actions.decide(cur, a["id"], "approve", actor=owner_ctx)
        actions.mark_done(cur, a["id"], actor=owner_ctx)
        actions.record_verification(cur, a["id"], verified=True, outcome_minor=123456)
        today = datetime.now(timezone.utc).date()
        out = metrics.run_metric(owner_ctx, MetricQuery(metric="core.verified_value", start=date(2026, 1, 1), end=max(today, date(2026, 12, 31))))
    finally:
        actions.unregister_action_type(t.key)
    assert out["unit"] == "money" and out["currency"] == "AED" and out["total"] == 1234.56


def test_metric_permissions(cur, hotel_uae, ctx_for, requests_metric):
    staff_ctx, _ = ctx_for("staff")
    assert "core.signals_raised" not in {m["key"] for m in metrics.list_metrics(staff_ctx)}
    assert "core.test_requests" in {m["key"] for m in metrics.list_metrics(staff_ctx)}  # no permission set
    with pytest.raises(ClefsError) as exc:
        metrics.run_metric(staff_ctx, MetricQuery(metric="core.signals_raised", start=date(2026, 9, 1), end=date(2026, 9, 2)))
    assert exc.value.status == 403


def test_a_slow_metric_hits_the_statement_timeout(cur, hotel_uae, owner_ctx, monkeypatch):
    m = metrics.register_metric(_metric(key="core.test_slow", where="(SELECT COUNT(*) FROM pg_sleep(0.3)) = 1"))
    monkeypatch.setattr(metrics, "STATEMENT_TIMEOUT_MS", 50)
    try:
        _request(cur, hotel_uae.slug, datetime(2026, 9, 10, 8, tzinfo=timezone.utc))
        with pytest.raises(ClefsError) as exc:
            metrics.run_metric(owner_ctx, MetricQuery(metric=m.key, start=date(2026, 9, 1), end=date(2026, 9, 30)))
        assert exc.value.code == "metric_timeout"
    finally:
        metrics.unregister_metric(m.key)
    cur.execute("SELECT 1 AS ok")  # the test transaction survived the timeout
    assert cur.fetchone()["ok"] == 1


def test_row_cap_marks_truncated(cur, hotel_uae, owner_ctx, requests_metric, monkeypatch):
    monkeypatch.setattr(metrics, "MAX_ROWS", 2)
    for room in ("101", "102", "103"):
        _request(cur, hotel_uae.slug, datetime(2026, 9, 10, 8, tzinfo=timezone.utc), room=room)
    out = metrics.run_metric(owner_ctx, MetricQuery(metric="core.test_requests", start=date(2026, 9, 1), end=date(2026, 9, 30), group_by=["room"]))
    assert out["truncated"] and len(out["rows"]) == 2


def test_every_registered_metric_compiles_and_explains(cur, hotel_uae, requests_metric):
    for m in metrics.registered_metrics():
        q = MetricQuery(
            metric=m.key, start=date(2026, 9, 1), end=date(2026, 9, 30), grain="week",
            group_by=[m.dimensions[0].key] if m.dimensions else [],
        )
        sql, params = metrics.compile_metric(hotel_uae, q)
        cur.execute("EXPLAIN " + sql, params)
        assert cur.fetchall(), m.key


def test_metric_routes(cur, client, client_as, hotel_uae, requests_metric):
    owner = client_as("owner")
    _request(cur, hotel_uae.slug, datetime(2026, 9, 10, 8, tzinfo=timezone.utc))
    listed = owner.get(f"/hotels/{hotel_uae.slug}/metrics")
    assert listed.status_code == 200
    keys = {m["key"] for m in listed.json()}
    assert {"core.signals_raised", "core.verified_value", "core.test_requests"} <= keys
    vv = next(m for m in listed.json() if m["key"] == "core.verified_value")
    assert vv["currency"] == "AED" and vv["unit"] == "money"
    r = owner.post(
        f"/hotels/{hotel_uae.slug}/metrics/query",
        json={"metric": "core.test_requests", "start": "2026-09-01", "end": "2026-09-30", "grain": "day", "group_by": ["category"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"] == [{"bucket": "2026-09-10", "group": {"category": "Housekeeping"}, "value": 1, "compare_value": None}]
    assert set(body) >= {"metric", "label", "unit", "currency", "grain", "start", "end", "group_by", "rows", "total", "compare", "truncated"}
    assert owner.post(f"/hotels/{hotel_uae.slug}/metrics/query", json={"metric": "core.nope", "start": "2026-09-01", "end": "2026-09-02"}).status_code == 404
    too_many = owner.post(
        f"/hotels/{hotel_uae.slug}/metrics/query",
        json={"metric": "core.test_requests", "start": "2026-09-01", "end": "2026-09-02", "group_by": ["a", "b", "c"]},
    )
    assert too_many.status_code == 422
    assert client.get(f"/hotels/{hotel_uae.slug}/metrics").status_code == 401
