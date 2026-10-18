"""
AI radar statistics (04 spec 5.5): Wilson intervals, the minimum-sample
gate, share of voice, source mix, Jensen-Shannon divergence and the
"CI moved outside the prior CI for 2+ windows" trend rule.

Pure functions (no cursor) except `rollup`, so the goldens run without a
database. The same half-width formula lives in SQL (vis_wilson_halfwidth,
migration 0904) because the vis.ai_mention_rate metric applies the gate in
SQL: the Python and SQL versions are tested against each other.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal

# ─── Wilson interval and the gate ───

Z = 1.96  # 95%
MIN_RUNS = 30  # below this a percentage is not shown at all
MAX_HALFWIDTH = 0.15  # "rate +/- 15 points" is the widest band worth showing


def wilson(k: int, n: int) -> tuple[float, float] | None:
    """Wilson 95% interval for k successes in n trials, clamped to [0, 1].
    Golden: k=12, n=40 -> (0.1807, 0.4543); k=0, n=30 -> (0.0, 0.1135)."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / denom
    half = halfwidth(k, n) or 0.0
    return max(0.0, centre - half), min(1.0, centre + half)


def halfwidth(k: int, n: int) -> float | None:
    """The same expression as SQL vis_wilson_halfwidth(k, n)."""
    if n <= 0:
        return None
    p = k / n
    return (Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))) / (1 + Z * Z / n)


def sufficient(k: int, n: int) -> bool:
    """The minimum-sample gate the UI, Ask and the metric SQL share:
    n >= 30 and half-width <= 0.15. n=30, k=6 passes (0.139); n=30, k=15
    fails (0.168); n=29 never passes."""
    hw = halfwidth(k, n)
    return n >= MIN_RUNS and hw is not None and hw <= MAX_HALFWIDTH


def q4(x: float | None) -> Decimal | None:
    """4-dp Decimal for the NUMERIC(5,4) columns (half up, like money)."""
    return None if x is None else Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def insufficient_label(n: int) -> str:
    return f"insufficient sample (n={n})"


def cell(k: int, n: int) -> dict:
    """{runs, mentions, rate, ci_low, ci_high, sufficient, display} for one
    cell. The rate and CI are always computed (they are stored); `display`
    is what a person sees, so an insufficient cell never shows a number."""
    ci = wilson(k, n)
    ok = sufficient(k, n)
    rate = (k / n) if n else None
    display = (f"{rate * 100:.0f}% ({ci[0] * 100:.0f}-{ci[1] * 100:.0f}%)" if ok and ci and rate is not None
               else insufficient_label(n))
    return {"runs": n, "mentions": k, "rate": q4(rate), "ci_low": q4(ci[0]) if ci else None,
            "ci_high": q4(ci[1]) if ci else None, "sufficient": ok, "display": display}


# ─── Share of voice and source mix ───


def share_of_voice(mentions: dict[str, int]) -> dict[str, dict]:
    """SoV per entity = its mentions / all mentions of the hotel, its comp
    set and 'other' hotels, with a Wilson interval over that total."""
    total = sum(max(0, int(v)) for v in mentions.values())
    out: dict[str, dict] = {}
    for entity, k in mentions.items():
        ci = wilson(int(k), total) if total else None
        out[entity] = {"mentions": int(k), "total": total, "sov": q4(k / total) if total else None,
                       "ci_low": q4(ci[0]) if ci else None, "ci_high": q4(ci[1]) if ci else None}
    return out


def source_mix(domain_classes: Iterable[str], *, top: int = 5) -> dict[str, int]:
    """Counts per cited-domain class, at most `top` slices (the rest are
    folded into 'other') so the console Donut stays readable."""
    counts: dict[str, int] = {}
    for c in domain_classes:
        counts[c] = counts.get(c, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) <= top:
        return dict(ranked)
    head, tail = ranked[: top - 1], ranked[top - 1:]
    return {**dict(head), "other": sum(v for _, v in tail)}


# ─── Drift and trends ───

JSD_SHIFT = 0.35  # AI Mode has no model id: a cited-domain shift above this suggests a new baseline


def js_divergence(p: dict[str, float], q: dict[str, float]) -> float:
    """Jensen-Shannon divergence (log base 2, so it lies in [0, 1]) between two
    count or probability maps. Empty on either side is 0 (nothing to compare)."""
    sp, sq = sum(p.values()), sum(q.values())
    if sp <= 0 or sq <= 0:
        return 0.0
    keys = set(p) | set(q)
    pn = {k: p.get(k, 0) / sp for k in keys}
    qn = {k: q.get(k, 0) / sq for k in keys}
    m = {k: (pn[k] + qn[k]) / 2 for k in keys}

    def kl(a: dict[str, float]) -> float:
        return sum(a[k] * math.log2(a[k] / m[k]) for k in keys if a[k] > 0)

    return max(0.0, min(1.0, (kl(pn) + kl(qn)) / 2))


def trend_change(windows: Sequence[tuple[float, float]], *, consecutive: int = 2) -> str | None:
    """'up' / 'down' when each of the last `consecutive` intervals lies
    entirely outside the one before the run (the baseline window), else None.
    `windows` is oldest first: [(ci_low, ci_high), ...]. A single noisy week
    never surfaces a change."""
    if len(windows) < consecutive + 1:
        return None
    base_lo, base_hi = windows[-consecutive - 1]
    recent = windows[-consecutive:]
    if all(lo > base_hi for lo, _ in recent):
        return "up"
    if all(hi < base_lo for _, hi in recent):
        return "down"
    return None


# ─── Weekly rollup (visibility.ai_weekly_rollup) ───

DISCOVERY = ("generic", "persona", "event")
FACT_CHUNK = 6  # fact extractions per task invocation (each is one AI call)


def _window_runs(cur, hotel_slug: str, week, window: int) -> list[dict]:
    from datetime import timedelta

    cur.execute(
        """
        SELECT r.engine, r.market, r.language, r.baseline_epoch, r.entities, r.citations
        FROM vis_ai_runs r JOIN vis_ai_batches b ON b.id = r.batch_id
        WHERE r.hotel_slug = %s AND b.kind = 'weekly' AND b.week BETWEEN %s AND %s
          AND r.status IN ('ok', 'empty') AND r.prompt_intent = ANY(%s)
        """,
        (hotel_slug, week - timedelta(days=7 * (window - 1)), week, list(DISCOVERY)),
    )
    return [dict(r) for r in cur.fetchall()]


def cells_from_runs(runs: list[dict], comp_ids: list[str]) -> dict[tuple, dict]:
    """{(engine, market, language): {epoch, n, entities{key: {k, pos[]}}, other, mix}}
    over the latest epoch of each engine (a window spanning an epoch change is
    truncated to the new epoch). Pure, so the goldens run without SQL."""
    latest: dict[str, int] = {}
    for r in runs:
        latest[r["engine"]] = max(latest.get(r["engine"], 0), int(r["baseline_epoch"] or 1))
    out: dict[tuple, dict] = {}
    for r in runs:
        if int(r["baseline_epoch"] or 1) != latest[r["engine"]]:
            continue
        key = (r["engine"], str(r["market"]).strip().upper(), r["language"])
        c = out.setdefault(key, {"epoch": latest[r["engine"]], "n": 0, "other": 0,
                                 "entities": {k: {"k": 0, "pos": []} for k in ["self", *comp_ids]},
                                 "self_wins": [], "comp_wins": [], "all": []})
        c["n"] += 1
        ents = r.get("entities") or []
        seen: set[str] = set()
        for e in ents:
            k = e.get("key")
            if k == "other":
                c["other"] += 1
            elif k in c["entities"] and k not in seen:
                seen.add(k)
                c["entities"][k]["k"] += 1
                c["entities"][k]["pos"].append(int(e.get("position") or 0))
        classes = [ci.get("cls") or "press_other" for ci in (r.get("citations") or [])]
        c["all"] += classes
        first = next((e.get("key") for e in sorted(ents, key=lambda e: e.get("position") or 99)), None)
        if first == "self":
            c["self_wins"] += classes
        elif first and first != "other":
            c["comp_wins"] += classes
    return out


def compute_cells(cur, hotel_slug: str, week) -> int:
    """Upsert vis_ai_weekly for both windows (1 and 4 weeks). The console
    reads window 1 for the home market in English (n=40 per engine) and
    window 4 for Arabic (n=80) and the source markets (n=32)."""
    from clefs.db import Json
    from clefs.modules.visibility import store

    comp_ids = [str(c["comp_id"]) for c in store.competitors(cur, hotel_slug)]
    written = 0
    for window in (1, 4):
        for (engine, market, language), c in cells_from_runs(_window_runs(cur, hotel_slug, week, window),
                                                             comp_ids).items():
            ks = {k: v["k"] for k, v in c["entities"].items()}
            # SoV over the hotel plus its comp set; with no comp set (Demand
            # not configured) "other hotels" join the denominator, or SoV
            # would read 100% whenever the hotel is named at all.
            sov_basis = dict(ks) if comp_ids else {**ks, "other": c["other"]}
            sov = share_of_voice(sov_basis)
            mix = {"self_wins": source_mix(c["self_wins"]), "comp_wins": source_mix(c["comp_wins"]),
                   "all": source_mix(c["all"])}
            rows = [(k, c["entities"][k]["k"], c["entities"][k]["pos"]) for k in ks] + [("other", c["other"], [])]
            for entity, k, pos in rows:
                cl = cell(min(k, c["n"]), c["n"]) if entity != "other" else {
                    "rate": None, "ci_low": None, "ci_high": None, "sufficient": None}
                sv = sov.get(entity) or {}
                cur.execute(
                    """
                    INSERT INTO vis_ai_weekly (hotel_slug, week, window_weeks, engine, market, language, entity,
                        baseline_epoch, runs, mentions, rate, ci_low, ci_high, avg_position, sov, sov_ci_low,
                        sov_ci_high, sufficient, source_mix, computed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (hotel_slug, week, window_weeks, engine, market, language, entity) DO UPDATE SET
                        baseline_epoch = EXCLUDED.baseline_epoch, runs = EXCLUDED.runs, mentions = EXCLUDED.mentions,
                        rate = EXCLUDED.rate, ci_low = EXCLUDED.ci_low, ci_high = EXCLUDED.ci_high,
                        avg_position = EXCLUDED.avg_position, sov = EXCLUDED.sov, sov_ci_low = EXCLUDED.sov_ci_low,
                        sov_ci_high = EXCLUDED.sov_ci_high, sufficient = EXCLUDED.sufficient,
                        source_mix = EXCLUDED.source_mix, computed_at = NOW()
                    """,
                    (hotel_slug, week, window, engine, market, language, entity, c["epoch"], c["n"], k, cl["rate"],
                     cl["ci_low"], cl["ci_high"], round(sum(pos) / len(pos), 2) if pos else None, sv.get("sov"),
                     sv.get("ci_low"), sv.get("ci_high"), cl["sufficient"], Json(mix) if entity == "self" else None),
                )
                written += 1
    return written


def source_shift(cur, hotel_slug: str, week) -> dict | None:
    """AI Mode has no model id: when its cited-domain distribution moves by a
    Jensen-Shannon divergence above 0.35 week over week, record a 'suggested'
    baseline reset (a person accepts it; nothing resets on its own)."""
    from datetime import timedelta

    def dist(w) -> dict[str, int]:
        cur.execute(
            """
            SELECT d, COUNT(*) AS n FROM vis_ai_runs r JOIN vis_ai_batches b ON b.id = r.batch_id,
                 LATERAL unnest(r.cited_domains) AS d
            WHERE r.hotel_slug = %s AND b.kind = 'weekly' AND b.week = %s AND r.engine = 'serpapi_ai_mode'
              AND r.status = 'ok'
            GROUP BY d
            """,
            (hotel_slug, w),
        )
        return {r["d"]: int(r["n"]) for r in cur.fetchall()}

    now, prev = dist(week), dist(week - timedelta(days=7))
    jsd = js_divergence(now, prev)
    if not now or not prev or jsd <= JSD_SHIFT:
        return None
    from clefs.core import clock

    cur.execute(
        """
        INSERT INTO vis_engine_versions (hotel_slug, engine, version_key, baseline_epoch, first_seen_at, reason, status)
        SELECT %s, 'serpapi_ai_mode', %s, COALESCE(MAX(baseline_epoch), 1) + 1, %s, %s, 'suggested'
        FROM vis_engine_versions WHERE hotel_slug = %s AND engine = 'serpapi_ai_mode' AND status = 'active'
        ON CONFLICT (hotel_slug, engine, version_key) DO NOTHING
        """,
        (hotel_slug, f"source_shift:{week.isoformat()}", clock.now(), f"possible source shift (JSD {jsd:.2f})",
         hotel_slug),
    )
    return {"jsd": round(jsd, 3), "week": week.isoformat()}


def rollup_task(cur, batch_id: int, *, phase: str = "cells", n: int = 0) -> dict:
    """Cells, source shift and the SoV signal first; then fact extraction in
    chunks of FACT_CHUNK runs, re-enqueued until every answer is checked."""
    from clefs import jobs
    from clefs.core.hotels import get_hotel
    from clefs.modules.visibility import ai_radar, facts, signals

    batch = ai_radar.get_batch(cur, batch_id)
    if batch is None or batch["kind"] != "weekly":
        return {"skipped": "not_weekly"}
    hotel = get_hotel(cur, batch["hotel_slug"])
    out: dict = {"batch_id": batch_id, "phase": phase}
    if phase == "cells":
        out["cells"] = compute_cells(cur, hotel.slug, batch["week"])
        out["source_shift"] = source_shift(cur, hotel.slug, batch["week"])
        out["sov_signal"] = signals.ai_sov_change(cur, hotel, batch["week"])
    checked = facts.check_pending(cur, hotel, batch_id=batch_id, limit=FACT_CHUNK)
    out["facts_checked"] = checked
    if checked >= FACT_CHUNK:
        jobs.enqueue("visibility.ai_weekly_rollup", {"batch_id": batch_id, "phase": "facts", "n": n + 1},
                     hotel_slug=hotel.slug, dedupe_key=f"visibility.ai_weekly_rollup:{batch_id}:facts:{n + 1}", cur=cur)
    else:
        out["fact_signal"] = signals.ai_fact_errors(cur, hotel)
    return out


# ─── Console read model (GET /visibility/ai) ───


def _window_for(market: str, language: str, home: str) -> int:
    return 1 if market == home and language == "en" else 4


def _epoch_note(r: dict) -> str | None:
    if r["reason"] in (None, "first_seen"):
        return None
    when = r["first_seen_at"].strftime("%-d %b") if r.get("first_seen_at") else "recently"
    what = {"parser_changed": "Parser updated", "model_setting_changed": "Model setting changed"}.get(
        r["reason"], "Model changed")
    return f"{what} {when}; baseline reset"


def overview(cur, hotel) -> dict:
    from clefs.modules.visibility import ai_radar, store

    profile = store.get_profile(cur, hotel.slug)
    status = ai_radar.engine_status(profile)
    home = (hotel.country or "AE").upper()[:2]
    cur.execute("SELECT MAX(week) AS w FROM vis_ai_weekly WHERE hotel_slug = %s", (hotel.slug,))
    week = (cur.fetchone() or {}).get("w")
    cells: list[dict] = []
    sov: list[dict] = []
    mix: dict[str, dict] = {}
    if week:
        cur.execute("SELECT * FROM vis_ai_weekly WHERE hotel_slug = %s AND week = %s ORDER BY engine, market, "
                    "language, entity", (hotel.slug, week))
        rows = [dict(r) for r in cur.fetchall()]
        comp_names = {str(c["comp_id"]): c.get("name") for c in store.competitors(cur, hotel.slug)}
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            if r["window_weeks"] == _window_for(r["market"], r["language"], home):
                groups.setdefault((r["engine"], r["market"], r["language"]), []).append(r)
        for (engine, market, language), rs in groups.items():
            me = next((r for r in rs if r["entity"] == "self"), None)
            if me is None:
                continue
            c = cell(int(me["mentions"] or 0), int(me["runs"] or 0))
            cells.append({"engine": engine, "market": market, "language": language,
                          "window_weeks": me["window_weeks"], "epoch": me["baseline_epoch"],
                          "label": ai_radar.label_for(engine), "avg_position": _s(me["avg_position"]),
                          **{k: _s(v) for k, v in c.items()}})
            sov.append({"engine": engine, "market": market, "language": language, "sufficient": c["sufficient"],
                        "entities": [{"entity": r["entity"], "name": hotel.name if r["entity"] == "self"
                                      else comp_names.get(r["entity"], "Other hotels" if r["entity"] == "other"
                                                          else r["entity"]),
                                      "mentions": r["mentions"], "sov": _s(r["sov"]), "ci_low": _s(r["sov_ci_low"]),
                                      "ci_high": _s(r["sov_ci_high"])} for r in rs]})
            if market == home and language == "en":
                mix[engine] = me.get("source_mix") or {}
    cur.execute("SELECT engine, version_key, baseline_epoch, first_seen_at, reason, status FROM vis_engine_versions "
                "WHERE hotel_slug = %s ORDER BY engine, baseline_epoch", (hotel.slug,))
    versions = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT id, week, status, planned, done, cost_micros, plan, created_at, finished_at "
                "FROM vis_ai_batches WHERE hotel_slug = %s AND kind = 'weekly' ORDER BY created_at DESC LIMIT 1",
                (hotel.slug,))
    batch = cur.fetchone()
    setup = sorted({v for v in status.values() if v not in ("ok", "disabled")})
    if not any(v == "ok" for v in status.values()):
        setup = setup or ["engines"]
    return {
        "enabled": any(v == "ok" for v in status.values()), "setup_missing": setup,
        "engines": [{"engine": k, "label": ai_radar.ENGINES[k].label, "status": v, "disclaimer": ai_radar.label_for(k)}
                    for k, v in status.items()],
        "week": week.isoformat() if week else None, "cells": cells, "sov": sov, "source_mix": mix,
        "facts": list_facts(cur, hotel.slug), "min_sample": {"runs": MIN_RUNS, "max_halfwidth": str(MAX_HALFWIDTH)},
        "epochs": [{"engine": v["engine"], "epoch": v["baseline_epoch"], "status": v["status"],
                    "version_key": v["version_key"], "reason": v["reason"],
                    "since": v["first_seen_at"].isoformat() if v["first_seen_at"] else None,
                    "note": _epoch_note(v) if v["status"] == "active" else None} for v in versions],
        "suggested_resets": [v["version_key"] for v in versions if v["status"] == "suggested"],
        "latest_batch": ({**{k: batch[k] for k in ("id", "status", "planned", "done", "cost_micros")},
                          "week": batch["week"].isoformat() if batch["week"] else None,
                          "steps": (batch["plan"] or {}).get("steps") or []} if batch else None),
        "budget": store.budget_state(cur, hotel.slug, profile=profile),
        "labels": {"simulation": ai_radar.LABEL_SIMULATION, "third_party": ai_radar.LABEL_THIRD_PARTY},
        "method_note": ai_radar.METHOD_NOTE,
    }


def _s(v):
    """Decimal -> string (the API's number contract); everything else as is."""
    from decimal import Decimal

    return str(v) if isinstance(v, Decimal) else v


def list_facts(cur, hotel_slug: str, *, statuses: tuple[str, ...] = ("open", "recurred", "fixing")) -> list[dict]:
    from clefs.modules.visibility.signals import FACT_LABELS

    cur.execute("SELECT * FROM vis_ai_facts WHERE hotel_slug = %s AND status = ANY(%s) ORDER BY severity, "
                "last_seen DESC LIMIT 100", (hotel_slug, list(statuses)))
    return [{"id": r["id"], "fact_key": r["fact_key"], "label": FACT_LABELS.get(r["fact_key"], r["fact_key"]),
             "claimed_value": r["claimed_value"], "claimed_quote": r["claimed_quote"], "truth_value": r["truth_value"],
             "truth_source": r["truth_source"], "error_type": r["error_type"], "severity": r["severity"],
             "engines": list(r["engines"] or []), "cited_domains": list(r["cited_domains"] or []),
             "occurrences": r["occurrences"], "run_ids": list(r["run_ids"] or [])[-10:], "status": r["status"],
             "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
             "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None, "action_id": r["action_id"]}
            for r in cur.fetchall()]
