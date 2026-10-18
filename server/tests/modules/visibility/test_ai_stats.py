"""AI radar statistics: Wilson goldens, the sample gate, rollups, SoV and epochs (04 spec 5.5, 5.12)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from clefs.modules.visibility import stats

NOW = datetime(2026, 9, 28, 9, 0, tzinfo=ZoneInfo("Asia/Dubai"))
WEEK = date(2026, 9, 28)

# ─── Pure goldens ───


def test_wilson_goldens():
    assert [stats.q4(x) for x in stats.wilson(12, 40)] == [Decimal("0.1807"), Decimal("0.4543")]
    assert [stats.q4(x) for x in stats.wilson(0, 30)] == [Decimal("0.0000"), Decimal("0.1135")]
    assert stats.wilson(0, 0) is None


@pytest.mark.parametrize(("k", "n", "shown"), [(6, 29, False), (6, 30, True), (15, 30, False)])
def test_gate(k, n, shown):
    assert stats.sufficient(k, n) is shown
    c = stats.cell(k, n)
    assert c["sufficient"] is shown
    assert (c["display"] == f"insufficient sample (n={n})") is (not shown)


def test_halfwidths_match_the_spec():
    assert round(stats.halfwidth(6, 30), 3) == 0.139
    assert round(stats.halfwidth(15, 30), 3) == 0.168


def test_sql_function_agrees_with_python(cur):
    for k, n in [(12, 40), (0, 30), (6, 30), (15, 30), (40, 80)]:
        cur.execute("SELECT vis_wilson_halfwidth(%s, %s) AS h", (k, n))
        assert abs(float(cur.fetchone()["h"]) - stats.halfwidth(k, n)) < 1e-9


def test_share_of_voice_and_source_mix():
    sov = stats.share_of_voice({"self": 10, "c1": 20, "c2": 10})
    assert sov["self"]["sov"] == Decimal("0.2500") and sov["self"]["total"] == 40
    assert sov["self"]["ci_low"] < Decimal("0.25") < sov["self"]["ci_high"]
    mix = stats.source_mix(["ota"] * 4 + ["review"] * 3 + ["google", "ugc", "wiki", "press_other"])
    assert len(mix) == 5 and mix["ota"] == 4 and mix["other"] == 2


def test_trend_needs_two_consecutive_windows():
    base = (0.20, 0.40)
    assert stats.trend_change([base, (0.45, 0.60)]) is None
    assert stats.trend_change([base, (0.45, 0.60), (0.42, 0.55)]) == "up"
    assert stats.trend_change([base, (0.45, 0.60), (0.30, 0.50)]) is None
    assert stats.trend_change([base, (0.01, 0.15), (0.02, 0.18)]) == "down"


def test_js_divergence_bounds():
    assert stats.js_divergence({"a.com": 5}, {"a.com": 9}) == 0.0
    assert stats.js_divergence({"a.com": 5}, {"b.com": 5}) == 1.0
    assert stats.js_divergence({"a.com": 5, "b.com": 5}, {"a.com": 5, "c.com": 5}) == pytest.approx(0.5)


# ─── Rollup over stored runs ───


def _batch(cur, slug, week):
    cur.execute("SELECT id FROM vis_ai_batches WHERE hotel_slug = %s AND week = %s", (slug, week))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute("INSERT INTO vis_ai_batches (hotel_slug, kind, week, status) VALUES (%s, 'weekly', %s, 'ready') "
                "RETURNING id", (slug, week))
    return cur.fetchone()["id"]


def seed(cur, slug, week, *, n, k, engine="gemini_web_search", market="AE", language="en", intent="generic",
         epoch=1, comp_k=0, other=0, cls="ota"):
    """n runs, the first k name the hotel (position 1), the first comp_k name comp c1."""
    from clefs.db import Json

    b = _batch(cur, slug, week)
    for i in range(n):
        ents = []
        if i < k:
            ents.append({"key": "self", "name": "Test Hotel Dubai", "position": 1, "via": "name"})
        if i < comp_k:
            ents.append({"key": "c1", "name": "Palm Vista", "position": len(ents) + 1, "via": "name"})
        if i < other:
            ents.append({"key": "other", "name": f"Other {i}", "position": len(ents) + 1, "via": "name"})
        cur.execute(
            """
            INSERT INTO vis_ai_runs (batch_id, batch_kind, hotel_slug, prompt_key, prompt_intent, prompt_text, market,
                language, engine, baseline_epoch, run_at, status, entities, citations, self_mentioned)
            VALUES (%s, 'weekly', %s, %s, %s, 'p', %s, %s, %s, %s, NOW(), 'ok', %s, %s, %s)
            """,
            (b, slug, f"{market.lower()}.{language}.{intent}.{i % 10}", intent, market, language, engine, epoch,
             Json(ents), Json([{"url": "https://x", "domain": "x", "cls": cls}]), i < k),
        )
    return b


@pytest.fixture
def hotel(cur, hotel_uae, clock):
    clock.freeze(NOW)
    cur.execute("INSERT INTO vis_competitors (hotel_slug, comp_id, name) VALUES (%s, 'c1', 'Palm Vista')",
                (hotel_uae.slug,))
    return hotel_uae


def _row(cur, slug, *, window, market="AE", language="en", entity="self", engine="gemini_web_search"):
    cur.execute("SELECT * FROM vis_ai_weekly WHERE hotel_slug = %s AND week = %s AND window_weeks = %s AND engine = %s "
                "AND market = %s AND language = %s AND entity = %s", (slug, WEEK, window, engine, market, language,
                                                                      entity))
    return cur.fetchone()


def test_brand_and_fact_check_runs_do_not_count(cur, hotel):
    seed(cur, hotel.slug, WEEK, n=40, k=12)
    seed(cur, hotel.slug, WEEK, n=8, k=8, intent="brand")
    seed(cur, hotel.slug, WEEK, n=16, k=16, intent="fact_check")
    stats.compute_cells(cur, hotel.slug, WEEK)
    r = _row(cur, hotel.slug, window=1)
    assert (r["runs"], r["mentions"], r["rate"]) == (40, 12, Decimal("0.3000"))
    assert (r["ci_low"], r["ci_high"], r["sufficient"]) == (Decimal("0.1807"), Decimal("0.4543"), True)


def test_ar_four_week_rollup_reaches_80(cur, hotel):
    for w in range(4):
        seed(cur, hotel.slug, WEEK - timedelta(days=7 * w), n=20, k=5, language="ar")
    stats.compute_cells(cur, hotel.slug, WEEK)
    assert _row(cur, hotel.slug, window=1, language="ar")["runs"] == 20
    r4 = _row(cur, hotel.slug, window=4, language="ar")
    assert (r4["runs"], r4["mentions"]) == (80, 20)


def test_window_is_truncated_to_the_new_epoch(cur, hotel):
    seed(cur, hotel.slug, WEEK - timedelta(days=14), n=20, k=20, language="ar", epoch=1)
    seed(cur, hotel.slug, WEEK, n=20, k=2, language="ar", epoch=2)
    stats.compute_cells(cur, hotel.slug, WEEK)
    r4 = _row(cur, hotel.slug, window=4, language="ar")
    assert (r4["runs"], r4["mentions"], r4["baseline_epoch"]) == (20, 2, 2)


def test_sov_against_the_comp_set_with_other_hotels_apart(cur, hotel):
    seed(cur, hotel.slug, WEEK, n=40, k=10, comp_k=30, other=40)
    stats.compute_cells(cur, hotel.slug, WEEK)
    me, comp = _row(cur, hotel.slug, window=1), _row(cur, hotel.slug, window=1, entity="c1")
    other = _row(cur, hotel.slug, window=1, entity="other")
    assert me["sov"] == Decimal("0.2500") and comp["sov"] == Decimal("0.7500")
    assert other["mentions"] == 40 and other["sov"] is None and other["rate"] is None
    assert me["source_mix"]["self_wins"] == {"ota": 10}


def test_metric_sql_applies_the_same_gate_as_the_ui(cur, hotel):
    from clefs.core import metrics
    from clefs.core.hotels import get_hotel
    from clefs.modules.visibility import metrics as _registered  # noqa: F401  (registers vis.ai_*)

    seed(cur, hotel.slug, WEEK, n=30, k=6)  # half-width 0.139: shown
    seed(cur, hotel.slug, WEEK, n=30, k=15, engine="serpapi_ai_mode")  # 0.168: hidden
    stats.compute_cells(cur, hotel.slug, WEEK)
    q = metrics.MetricQuery(metric="vis.ai_mention_rate", start=WEEK, end=WEEK, grain="total", group_by=["engine"])
    sql, params = metrics.compile_metric(get_hotel(cur, hotel.slug), q)
    cur.execute(sql, params)
    sql_values = {r["g_engine"]: r["value"] for r in cur.fetchall()}
    ui = {c["engine"]: c for c in stats.overview(cur, get_hotel(cur, hotel.slug))["cells"]}
    # the metric hides exactly the cells the UI hides, and agrees on the value it shows
    for engine, c in ui.items():
        assert (sql_values[engine] is not None) is c["sufficient"]
    assert sql_values["gemini_web_search"] == Decimal("0.2") and ui["gemini_web_search"]["rate"] == "0.2000"
    assert ui["serpapi_ai_mode"]["display"] == "insufficient sample (n=30)"
    assert ui["serpapi_ai_mode"]["label"].startswith("Google AI Mode via SerpApi (third-party)")
    assert ui["gemini_web_search"]["label"] == "API simulation: consumer app answers vary"


def test_sov_change_signal_after_two_windows_outside_the_prior_ci(cur, hotel):
    from clefs.core import signals as core_signals
    from clefs.modules.visibility import signals

    for w, k in ((14, 8), (7, 30), (0, 32)):  # 20% -> 75% -> 80% of n=40
        seed(cur, hotel.slug, WEEK - timedelta(days=w), n=40, k=k)
        stats.compute_cells(cur, hotel.slug, WEEK - timedelta(days=w))
    assert len(signals.ai_sov_change(cur, hotel, WEEK)) == 1
    row = [s for s in core_signals.list_signals(cur, hotel.slug) if s["kind"] == "vis.ai_sov_change"][0]
    assert row["cadence"] == "weekly" and "more often" in row["title"]
