"""
AI radar signal kinds and emitters (04 spec 5.11), registered at import.

Both are weekly (Canon 18: nothing here is money at risk today):
- vis.ai_sov_change: the hotel's mention-rate interval moved outside the
  prior window's interval for 2+ consecutive weeks (stats.trend_change), on
  a sufficient sample, within one baseline epoch. One noisy week never
  raises it, and a model change never looks like a trend.
- vis.ai_fact_error: one signal per open wrong fact (by fingerprint); its
  recommended action is routed by the domain the answers cite
  (action_types.route_fact_error). Unverifiable claims ask for a KB answer.
"""

from __future__ import annotations

from typing import Any

from clefs.core import signals
from clefs.core.signals import Evidence, RecommendedAction, SignalIn

MODULE = "visibility"

signals.register_signal_kind("vis.ai_sov_change", label="AI answers mention you more or less",
                             default_owner_role="gm", section="money_available", cadence="weekly")
signals.register_signal_kind("vis.ai_fact_error", label="AI answers state a wrong hotel fact",
                             default_owner_role="gm", section="money_available", cadence="weekly")

FACT_LABELS = {
    "checkin_time": "check-in time", "checkout_time": "check-out time", "parking": "parking", "pool": "pool",
    "breakfast": "breakfast", "star_rating": "star rating", "pets": "pets policy", "wifi": "Wi-Fi",
    "price_band": "price level",
}

# ─── Share of voice ───


def ai_sov_change(cur: Any, hotel: Any, week: Any) -> list[dict]:
    """Checks each engine's home-market English cell (window 1, the 'self'
    row) over the last 3 weeks of the current epoch."""
    from clefs.modules.visibility import ai_radar, stats

    home = (hotel.country or "AE").upper()[:2]
    cur.execute(
        """
        SELECT engine, week, baseline_epoch, ci_low, ci_high, rate, runs, sufficient FROM vis_ai_weekly
        WHERE hotel_slug = %s AND window_weeks = 1 AND market = %s AND language = 'en' AND entity = 'self'
          AND week <= %s ORDER BY engine, week
        """,
        (hotel.slug, home, week),
    )
    by_engine: dict[str, list[dict]] = {}
    for r in cur.fetchall():
        by_engine.setdefault(r["engine"], []).append(dict(r))
    seen: list[str] = []
    out: list[dict] = []
    for engine, rows in by_engine.items():
        epoch = rows[-1]["baseline_epoch"]
        series = [r for r in rows if r["baseline_epoch"] == epoch and r["sufficient"]][-3:]
        direction = stats.trend_change([(float(r["ci_low"]), float(r["ci_high"])) for r in series])
        if not direction:
            continue
        key = f"vis.ai_sov_change:{engine}"
        seen.append(key)
        last = series[-1]
        verb = "more" if direction == "up" else "less"
        out.append(signals.upsert_signal(cur, hotel.slug, SignalIn(
            module=MODULE, kind="vis.ai_sov_change", dedupe_key=key,
            title=f"{ai_radar.ENGINES[engine].label} mentions you {verb} often than before",
            summary=(f"Mentioned in {float(last['rate']) * 100:.0f}% of discovery answers this week "
                     f"(n={last['runs']}); the interval has moved {direction} for 2 weeks in a row. "
                     f"{ai_radar.label_for(engine)}."),
            severity=2 if direction == "down" else 1, confidence=0.8,
            evidence=[Evidence(kind="series", label="Mention rate (95% interval)",
                               value=[{"week": r["week"].isoformat(), "rate": str(r["rate"]),
                                       "ci": [str(r["ci_low"]), str(r["ci_high"])]} for r in series])],
            recommended_action=RecommendedAction(type="core.task", label="Review AI answers and cited sources",
                                                 owner_role="gm", params={"link": "/visibility/ai"}),
        )))
    signals.resolve_missing(cur, hotel.slug, MODULE, "vis.ai_sov_change", seen)
    return out


# ─── Fact errors ───


def fact_title(f: dict) -> str:
    label = FACT_LABELS.get(f["fact_key"], f["fact_key"])
    if f["error_type"] == "unverifiable":
        return f"AI answers state your {label} ('{f['claimed_value']}') but your knowledge base has no answer"
    if f["error_type"] == "false_closed":
        return "AI answers say your hotel is closed"
    return f"AI answers get your {label} wrong: '{f['claimed_value']}' (yours: {f['truth_value']})"


def action_for(cur: Any, hotel_slug: str, f: dict) -> tuple[str, dict]:
    """The action type and params that fix this error (5.7 routing)."""
    from clefs.modules.visibility import store
    from clefs.modules.visibility.action_types import route_fact_error
    from clefs.modules.visibility.parsers import domain_of

    if f["error_type"] == "unverifiable":
        return "visibility.add_kb_faq", {
            "fact_key": f["fact_key"], "question_en": _question(f["fact_key"]), "answer_en": "", "fact_id": f["id"]}
    host = domain_of(store.get_profile(cur, hotel_slug).get("website_url")) or None
    kind = route_fact_error(list(f.get("cited_domains") or []), website_host=host)
    params = {"fact_id": f["id"], "fact_key": f["fact_key"], "claimed": f["claimed_value"],
              "truth": f["truth_value"], "fingerprint": f["fingerprint"],
              "domains": list(f.get("cited_domains") or [])[:5], "engines": list(f.get("engines") or [])}
    if kind == "visibility.site_fact_fix":
        params["facts"] = [f["fact_key"]]
    if kind == "visibility.add_kb_faq":
        params.update(question_en=_question(f["fact_key"]), answer_en=f["truth_value"] or "")
    return kind, params


def _question(fact_key: str) -> str:
    return {
        "checkin_time": "What time is check-in?", "checkout_time": "What time is check-out?",
        "parking": "Is there parking, and is it free?", "pool": "Is there a swimming pool?",
        "breakfast": "Is breakfast included?", "star_rating": "How many stars is the hotel?",
        "pets": "Are pets allowed?", "wifi": "Is there Wi-Fi, and is it free?",
        "price_band": "What is the typical nightly rate?",
    }.get(fact_key, f"What is the hotel's {fact_key}?")


def ai_fact_errors(cur: Any, hotel: Any) -> int:
    from clefs.modules.visibility import ai_radar

    cur.execute("SELECT * FROM vis_ai_facts WHERE hotel_slug = %s AND status IN ('open', 'recurred')", (hotel.slug,))
    seen: list[str] = []
    for f in [dict(r) for r in cur.fetchall()]:
        key = f"vis.ai_fact_error:{f['fingerprint']}"
        seen.append(key)
        kind, params = action_for(cur, hotel.slug, f)
        signals.upsert_signal(cur, hotel.slug, SignalIn(
            module=MODULE, kind="vis.ai_fact_error", dedupe_key=key, title=fact_title(f),
            summary=(f"Seen {f['occurrences']} time(s) across {', '.join(f['engines'] or [])}. "
                     + " ".join(sorted({ai_radar.label_for(e) + "." for e in f["engines"] or []}))).strip(),
            severity=int(f["severity"] or 3), confidence=0.7, subject_type="vis_ai_fact", subject_id=str(f["id"]),
            evidence=[Evidence(kind="quote", label="What the answer said", value=f["claimed_quote"]),
                      Evidence(kind="row", label="Cited sources", value=list(f["cited_domains"] or []))],
            recommended_action=RecommendedAction(type=kind, label="Create the fix", owner_role="gm", params=params),
        ))
    signals.resolve_missing(cur, hotel.slug, MODULE, "vis.ai_fact_error", seen)
    return len(seen)
