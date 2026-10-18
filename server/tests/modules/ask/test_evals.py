"""The Ask Clefs eval set (evals/ask_clefs.yaml, about 60 items) on the fake
AI: the scripted model writes each item's tool call the way a model would
("Booking.com", "water bottles", "gte 5"); the server must turn it into
exactly the expected governed query, every answered number must be
grounded, and every unsupported question must be refused or declined.

ASK_EVAL_LIVE=1 (with a real GEMINI_API_KEY) runs the same items against
the live planner instead, scoring only the targets."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest
import yaml

from clefs import ratelimit
from clefs.modules.ask import engine, grounding

from .conftest import call, grounded_answer, q

EVALS = yaml.safe_load((Path(__file__).parent / "evals" / "ask_clefs.yaml").read_text())
TARGET_ARGS, TARGET_GROUNDING, TARGET_UNSUPPORTED = 0.90, 1.0, 0.95


def _s(v) -> str | None:
    return v.isoformat() if isinstance(v, date) else (None if v is None else str(v))


def to_args(c: dict) -> dict:
    p = c.get("p", "mtd")
    explicit = isinstance(p, list)
    return q(c["m"], dims=c.get("dims", ()), filters=[tuple(x) for x in c.get("f", [])],
             preset=None if explicit else p, frm=_s(p[0]) if explicit else None, to=_s(p[1]) if explicit else None,
             grain=c.get("grain", "total"), compare=c.get("cmp", "none"), sort=c.get("sort"), limit=c.get("limit", 20))


def seed_eval(f):
    """Enough demo data that most items return rows (empty answers are fine too)."""
    from .conftest import seed_standard

    seed_standard(f)
    for d, ch, v in (("2026-07-14", "BOOKING", 800_000), ("2026-09-05", "EXPEDIA", 210_000),
                     ("2025-06-01", "BOOKING", 640_000), ("2026-02-02", "DIRECT", 0)):
        f.add("pg.commission_aed", d, v, channel=ch)
    for d, ch, v in (("2026-03-10", "DIRECT", 5_500_000), ("2026-08-20", "BOOKING", 7_700_000)):
        f.add("pg.net_revenue_aed", d, v, channel=ch)
    for d, am, dept, n in (("2026-09-22", "Water bottle", "Housekeeping", 140), ("2026-09-15", "Slippers",
                                                                                   "Housekeeping", 60)):
        f.add("am.issued_units", d, n, amenity=am, department=dept, floor="2")
    for d, ch in (("2026-09-28", "BOOKING"), ("2026-09-28", "DIRECT"), ("2026-09-25", "EXPEDIA")):
        f.add("stays.bookings", d, 1, channel=ch, guest_name="Should Never Show")
    for d, v in (("2026-08-03", 1), ("2026-08-19", 1), ("2026-08-27", 1)):
        f.add("reviews.count", d, v, channel="GOOGLE")
    for d, v in (("2026-02-10", 9), ("2026-05-10", 8)):
        f.add("reviews.avg_rating", d, v, channel="GOOGLE")
    for d, ch in (("2026-09-23", "WHATSAPP"), ("2026-09-24", "EMAIL"), ("2025-09-10", "EMAIL")):
        f.add("inq.inquiries_count", d, 1, channel=ch)
    f.add("upsell.order_revenue", "2026-08-09", 4_550_000, department="IRD")
    f.add("dr.comp_median_rate_aed", "2026-10-12", 95_000, room_type="DLX")
    f.add("dr.comp_median_rate_aed", "2026-11-02", 72_500, room_type="DLX")
    f.add("service.sla_breaches", "2026-09-10", 2, department="Housekeeping")
    f.add("pg.findings_open", "2026-04-02", 3, channel="BOOKING")
    for d, n in (("2026-10-13", 11), ("2025-10-13", 9)):
        f.add("stays.room_nights", d, n, channel="DIRECT", room_type="DLX")


def _push(fake_ai, item: dict) -> None:
    if item.get("unsupported") == "guardrail":
        return  # refused before any model call: nothing may be consumed
    rounds = item.get("calls") or ([item["call"]] if item.get("call") else [])
    for c in rounds:
        fake_ai.push([call("query_metric", **to_args(c))])
    if item.get("unsupported") == "model":
        fake_ai.push([call("answer_unsupported", reason="Clefs has no metric for that.",
                           suggestions=["Occupancy next 30 days"])])
        return
    mode = item.get("answer", "grounded")
    if mode in ("hallucinate_once", "hallucinate_twice"):
        fake_ai.push({"answer_text": "It was AED 123,456,789, a record."})
    if mode == "hallucinate_twice":
        fake_ai.push({"answer_text": "Roughly AED 98,765 more than usual."})
        return  # two strikes: the table-only fallback, no third model call
    fake_ai.push(grounded_answer)


def _matches(out: dict, exp: dict) -> bool:
    tr = out.get("trace")
    if not tr:
        return False
    got = {
        "m": tr["metric"], "dims": tr["dims"], "f": {f["field"]: sorted(f["values"]) for f in tr["filters"]},
        "from": tr["period"]["from"], "to": tr["period"]["to"], "grain": tr["grain"], "cmp": tr["compare"],
    }
    want = {
        "m": exp["m"], "dims": list(exp.get("dims", [])),
        "f": {k: sorted(str(x) for x in v) for k, v in (exp.get("f") or {}).items()},
        "from": _s(exp["from"]), "to": _s(exp["to"]), "grain": exp.get("grain", "total"), "cmp": exp.get("cmp"),
    }
    if "limit" in exp and tr["limit"] != exp["limit"]:
        return False
    return got == want


def _score(cur, ctx_for, fake_ai, *, live: bool) -> dict:
    ctxs: dict[str, object] = {}
    arg_hits = arg_total = grounded_ok = grounded_total = unsup_hits = unsup_total = false_refusals = 0
    misses: list[str] = []
    for item in EVALS:
        role = item.get("role", "owner")
        if role not in ctxs:
            ctxs[role] = ctx_for(role)[0]
        ctx = ctxs[role]
        ratelimit.reset(cur, f"ask:q:{ctx.principal.user_id}")
        before = len(fake_ai.calls) if fake_ai else 0
        if not live:
            _push(fake_ai, item)
        out = engine.ask(cur, ctx, item["question"])
        if item.get("unsupported"):
            unsup_total += 1
            if out["status"] in ("refused", "unsupported"):
                unsup_hits += 1
            else:
                misses.append(f"{item['id']}: not declined ({out['status']} {out['reason']})")
            if not live:
                if item["unsupported"] == "guardrail":
                    assert len(fake_ai.calls) == before, f"{item['id']}: a guardrail refusal called the model"
                assert fake_ai.pending == 0, f"{item['id']}: scripted outputs left over"
            continue
        if out["status"] in ("refused", "unsupported"):
            false_refusals += 1
        arg_total += 1
        if _matches(out, item["expect"]):
            arg_hits += 1
        else:
            misses.append(f"{item['id']}: got {out.get('trace')}")
        if out["status"] in ("answered", "table_only"):
            grounded_total += 1
            bag = grounding.allowed([_res_of(b) for b in out["results"]], question=item["question"])
            if grounding.check(out["answer_text"], bag).ok:
                grounded_ok += 1
            else:
                misses.append(f"{item['id']}: ungrounded {out['answer_text']!r}")
        if not live:
            assert fake_ai.pending == 0, f"{item['id']}: scripted outputs left over"
    return {
        "items": len(EVALS), "args": arg_hits / max(1, arg_total), "grounding": grounded_ok / max(1, grounded_total),
        "unsupported": unsup_hits / max(1, unsup_total), "false_refusals": false_refusals, "misses": misses,
        "answered": grounded_total,
    }


def _res_of(block: dict) -> dict:
    """Rebuild the registry-shaped result a block was made from (for the
    independent grounding re-check)."""
    tab, tr = block["table"], block["trace"]
    keys = [c["key"] for c in tab["columns"]]
    rows = []
    for r in tab["rows"]:
        cell = dict(zip(keys, r))
        rows.append({"bucket": cell.get("bucket"), "group": {d: cell.get(d) for d in tr["dims"]},
                     "value": cell.get("value"), "compare_value": cell.get("compare_value")})
    return {"unit": tr["unit"], "rows": rows, "total": tab["total"], "compare_total": tab["compare_total"],
            "row_count": tab["row_count"], "start": tr["period"]["from"], "end": tr["period"]["to"],
            "limit": tr["limit"]}


def test_eval_set_size_and_shape():
    assert 55 <= len(EVALS) <= 70
    ids = [i["id"] for i in EVALS]
    assert len(ids) == len(set(ids))
    assert sum(1 for i in EVALS if i.get("unsupported")) >= 10


def test_eval_set_on_the_fake_ai(cur, demo, ctx_for, fake_ai):
    seed_eval(demo)
    s = _score(cur, ctx_for, fake_ai, live=False)
    assert s["args"] >= TARGET_ARGS, s["misses"]
    assert s["grounding"] >= TARGET_GROUNDING, s["misses"]
    assert s["unsupported"] >= TARGET_UNSUPPORTED, s["misses"]
    assert s["false_refusals"] == 0, s["misses"]
    # The scripted planner is exact, so anything short of 100% is a server bug.
    assert s["args"] == 1.0 and s["misses"] == [], s["misses"]
    assert s["answered"] == sum(1 for i in EVALS if not i.get("unsupported"))  # every supported item answered


@pytest.mark.skipif(os.environ.get("ASK_EVAL_LIVE") != "1", reason="live planner eval: set ASK_EVAL_LIVE=1")
def test_eval_set_live(cur, demo, ctx_for):  # pragma: no cover - needs a real model
    seed_eval(demo)
    s = _score(cur, ctx_for, None, live=True)
    print({k: v for k, v in s.items() if k != "misses"}, *s["misses"], sep="\n")
    assert s["args"] >= TARGET_ARGS and s["grounding"] >= TARGET_GROUNDING and s["unsupported"] >= TARGET_UNSUPPORTED
