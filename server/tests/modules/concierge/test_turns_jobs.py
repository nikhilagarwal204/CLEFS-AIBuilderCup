"""Concierge jobs, signals and metrics of the tools-turns part (03 spec 2.8)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from clefs.core import clock
from clefs.db import Json
from clefs.modules.concierge import turns


def _unknown(cur, slug, key, title, n):
    cur.execute(
        "INSERT INTO cc_kb_unknowns (hotel_slug, cluster_key, title, samples, languages, count) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (slug, key, title, Json([title]), ["en"], n),
    )
    uid = cur.fetchone()["id"]
    for _ in range(n):
        cur.execute("INSERT INTO cc_kb_unknown_events (hotel_slug, unknown_id, text, text_norm, language) "
                    "VALUES (%s, %s, %s, %s, 'en')", (slug, uid, title, key))
    return uid


def _session(cur, slug, **counters):
    sid = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO cc_sessions (id, hotel_slug, surface, channel, room, verified, transport, expires_at, turn_count, "
        "requests_created, handoffs, unknowns, orders) VALUES (%s, %s, 'web_room', 'web', '101', TRUE, 'live', %s, "
        "%s, %s, %s, %s, %s)",
        (sid, slug, clock.now() + timedelta(hours=2), counters.get("turns", 2), counters.get("requests", 0),
         counters.get("handoffs", 0), counters.get("unknowns", 0), counters.get("orders", 0)),
    )
    return sid


def test_jobs_signals_and_metrics_are_registered():
    from clefs import jobs
    from clefs.core import metrics, signals

    names = {j.name for j in jobs.registered_jobs()} | {t.name for t in jobs.registered_tasks()}
    assert {"concierge.unknowns_cluster", "concierge.session_gc", "concierge.chat_history_gc",
            "concierge.upsell_performance", "concierge.whatsapp_reply"} <= names
    keys = {m.key for m in metrics.registered_metrics()}
    assert {"concierge.containment_rate", "concierge.unknown_rate", "concierge.handoff_rate",
            "concierge.order_revenue", "concierge.orders", "concierge.sessions"} <= keys
    assert "concierge.upsell_performance" in {k.key for k in signals.signal_kinds()}


def test_cluster_merges_fuzzy_duplicates_and_raises_the_gap(cur, hotel_uae):
    a = _unknown(cur, hotel_uae.slug, "breakfast served time", "What time is breakfast served?", 3)
    b = _unknown(cur, hotel_uae.slug, "breakfast time", "Breakfast time?", 2)
    c = _unknown(cur, hotel_uae.slug, "camel ride", "Can I ride a camel?", 1)
    out = turns.cluster_unknowns(cur, hotel_uae.slug, use_ai=False)
    assert out == {"open": 3, "merged": 1}
    cur.execute("SELECT id, status, merged_into, count FROM cc_kb_unknowns WHERE id = ANY(%s) ORDER BY id", ([a, b, c],))
    rows = {r["id"]: dict(r) for r in cur.fetchall()}
    assert rows[a]["count"] == 5 and rows[a]["status"] == "open"
    assert rows[b]["status"] == "merged" and rows[b]["merged_into"] == a and rows[c]["status"] == "open"
    cur.execute("SELECT COUNT(*) AS n FROM cc_kb_unknown_events WHERE unknown_id = %s", (a,))
    assert cur.fetchone()["n"] == 5
    cur.execute("SELECT status FROM signals WHERE hotel_slug = %s AND dedupe_key = %s",
                (hotel_uae.slug, f"concierge.kb_gap:{a}"))
    assert cur.fetchone()["status"] == "open"


def test_cluster_uses_the_ai_for_different_wording(cur, hotel_uae, fake_ai):
    a = _unknown(cur, hotel_uae.slug, "airport shuttle", "Is there an airport shuttle?", 2)
    b = _unknown(cur, hotel_uae.slug, "get dxb", "How do I get to DXB?", 1)
    fake_ai.push({"groups": [[a, b, 999999]]})
    out = turns.cluster_unknowns(cur, hotel_uae.slug)
    assert out["merged"] == 1
    cur.execute("SELECT merged_into FROM cc_kb_unknowns WHERE id = %s", (b,))
    assert cur.fetchone()["merged_into"] == a
    assert fake_ai.calls[0].task == "concierge.unknowns_cluster"


def test_containment_and_handoff_metrics_compile_and_run(cur, hotel_uae):
    from clefs.core.hotels import get_hotel
    from clefs.core.metrics import MetricQuery, compile_metric

    _session(cur, hotel_uae.slug)                       # contained
    _session(cur, hotel_uae.slug, requests=1)           # a ticket
    _session(cur, hotel_uae.slug, handoffs=1, unknowns=1)
    _session(cur, hotel_uae.slug, turns=0)              # never engaged: not counted
    today = clock.now().date()
    hotel = get_hotel(cur, hotel_uae.slug)
    values = {}
    for key in ("concierge.containment_rate", "concierge.handoff_rate", "concierge.unknown_rate", "concierge.sessions"):
        sql, params = compile_metric(hotel, MetricQuery(metric=key, start=today - timedelta(days=1), end=today + timedelta(days=1)))
        cur.execute(sql, params)
        values[key] = float(list(cur.fetchone().values())[-1])
    assert values["concierge.sessions"] == 3
    assert round(values["concierge.containment_rate"]) == 33
    assert round(values["concierge.handoff_rate"]) == 33 and round(values["concierge.unknown_rate"]) == 33


def test_upsell_performance_signal_when_pairings_are_ignored(cur, hotel_uae):
    for i in range(turns.UPSELL_MIN_SHOWN):
        sid = _session(cur, hotel_uae.slug)
        cur.execute("INSERT INTO cc_session_events (session_id, hotel_slug, kind, ref) VALUES (%s, %s, 'quote', %s)",
                    (sid, hotel_uae.slug, Json({"suggested": [50 + i]})))
        cur.execute("INSERT INTO cc_session_events (session_id, hotel_slug, kind, ref) VALUES (%s, %s, 'order', %s)",
                    (sid, hotel_uae.slug, Json({"item_ids": [1], "total_minor": 5000, "currency": "AED"})))
    stats = turns.upsell_signal(cur, hotel_uae.slug)
    assert stats["shown"] == 20 and stats["accepted"] == 0 and stats["orders"] == 20 and stats["signal"] is True
    cur.execute("SELECT section, title FROM signals WHERE hotel_slug = %s AND kind = %s",
                (hotel_uae.slug, turns.UPSELL_KIND))
    row = cur.fetchone()
    assert row["section"] == "money_available" and "0 of 20" in row["title"]


def test_upsell_week_counts_a_taken_pairing(cur, hotel_uae):
    sid = _session(cur, hotel_uae.slug)
    cur.execute("INSERT INTO cc_session_events (session_id, hotel_slug, kind, ref) VALUES (%s, %s, 'quote', %s)",
                (sid, hotel_uae.slug, Json({"suggested": [12]})))
    cur.execute("INSERT INTO cc_session_events (session_id, hotel_slug, kind, ref) VALUES (%s, %s, 'order', %s)",
                (sid, hotel_uae.slug, Json({"item_ids": [11, 12], "total_minor": 9000, "currency": "AED"})))
    stats = turns.upsell_week(cur, hotel_uae.slug)
    assert (stats["shown"], stats["accepted"], stats["rate"], stats["order_minor"]) == (1, 1, 1.0, 9000)
    assert turns.upsell_signal(cur, hotel_uae.slug)["signal"] is False


def test_chat_history_is_cleared_once_the_session_ends(cur, hotel_uae):
    sid = _session(cur, hotel_uae.slug)
    live = _session(cur, hotel_uae.slug)
    cur.execute("UPDATE cc_sessions SET chat_history = %s WHERE id = ANY(%s::uuid[])",
                (Json([{"role": "user", "content": "hi"}]), [sid, live]))
    cur.execute("UPDATE cc_sessions SET ended_at = NOW() WHERE id = %s", (sid,))
    assert turns.clear_chat_history(cur) == 1
    cur.execute("SELECT id, chat_history FROM cc_sessions WHERE id = ANY(%s::uuid[])", ([sid, live],))
    assert {str(r["id"]): r["chat_history"] is None for r in cur.fetchall()} == {sid: True, live: False}
