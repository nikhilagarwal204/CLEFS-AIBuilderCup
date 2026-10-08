"""Against the REAL registry (whatever modules are loaded), not the demo one:
the generated tools stay valid strict JSON schemas and a real core metric
answers end to end."""

from __future__ import annotations

from clefs.modules.ask import catalog, engine, tools

from .conftest import call, grounded_answer, q


def _walk_strict(node, path="$"):
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties") or {}
            assert node.get("additionalProperties") is False, path
            assert set(node.get("required") or []) == set(props), path
        if "enum" in node:
            assert node["enum"], f"{path}: empty enum"
        for k, v in node.items():
            _walk_strict(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_strict(v, f"{path}[{i}]")


def test_generated_tools_are_strict_for_every_role(cur, hotel_uae, ctx_for):
    for role in ("owner", "gm", "finance", "revenue", "dept_head"):
        ctx, _ = ctx_for(role)
        defs = tools.build_tools(catalog.entries(ctx))
        assert [t["name"] for t in defs] == list(tools.TOOL_NAMES)
        for t in defs:
            assert t["strict"] is True
            _walk_strict(t["parameters"], t["name"])


def test_a_core_metric_answers_end_to_end(cur, hotel_uae, ctx_for, fake_ai, frozen):
    ctx, _ = ctx_for("owner")
    keys = {e["key"] for e in catalog.entries(ctx)}
    assert {"core.signals_raised", "core.verified_value"} <= keys
    fake_ai.push([call("query_metric", **q("core.signals_raised", dims=["section"], preset="last_month"))],
                 grounded_answer)
    out = engine.ask(cur, ctx, "How many issues did Clefs find last month, by section?")
    assert out["status"] == "answered", out
    assert out["answer_text"] == "Signals raised for 1-31 Aug 2026 was 0.00."
    assert out["trace"]["metric"] == "core.signals_raised" and out["trace"]["dims"] == ["section"]
