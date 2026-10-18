"""
AI calls (01 spec E7, 04 spec F8): the kill switch, the fake backend,
per-task models, strict schemas, invalid output, citations, raw calls, and
ai_calls logging with content only when AI_LOG_CONTENT is on.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from clefs import ai


class Mapping(BaseModel):
    canonical: str
    header: str
    confidence: float
    note: str | None = None


class Answer(BaseModel):
    hotels: list[str]


def _calls(cur):
    cur.execute("SELECT task, kind, model, backend, status, tokens_in, input, output, error FROM ai_calls ORDER BY id")
    return [dict(r) for r in cur.fetchall()]


def test_kill_switch(cur, env):
    ai.reset_backend()
    env(AI_ENABLED="false")
    with pytest.raises(ai.AIDisabled) as exc:
        ai.text("reviews.draft_reply", system="s", input="x")
    assert exc.value.status == 409 and exc.value.code == "ai_disabled" and not ai.enabled()
    env(AI_ENABLED="true", GEMINI_API_KEY=None, GOOGLE_API_KEY=None, GOOGLE_GENAI_USE_VERTEXAI="false")
    with pytest.raises(ai.AIDisabled):
        ai.text("reviews.draft_reply", system="s", input="x")
    assert [c["status"] for c in _calls(cur)] == ["disabled", "disabled"]


def test_structured_with_the_fake_backend(cur, fake_ai, env):
    env(AI_MODEL_DEFAULT="model-default", AI_MODEL_INGEST_MAP_COLUMNS="model-mapper")
    fake_ai.push({"canonical": "ota_res_id", "header": "Book number", "confidence": 0.93, "note": None})
    out = ai.structured("ingest.map_columns", system="Map headers", input="Book number,Arrival", schema=Mapping, hotel_slug="hotel-nandan")
    assert out == Mapping(canonical="ota_res_id", header="Book number", confidence=0.93)
    [req] = fake_ai.calls
    assert req.model == "model-mapper" and req.kind == "structured" and req.store is False
    assert req.schema["additionalProperties"] is False and req.schema["required"] == ["canonical", "header", "confidence", "note"]
    assert "default" not in str(req.schema.get("properties", {}).get("note"))
    fake_ai.push("A warm reply")
    assert ai.text("reviews.draft_reply", system="s", input="Great stay") == "A warm reply"
    assert fake_ai.calls[-1].model == "model-default"
    [c1, c2] = _calls(cur)
    assert (c1["task"], c1["status"], c1["backend"], c1["model"]) == ("ingest.map_columns", "ok", "fake", "model-mapper")
    assert c1["input"] is None and c1["output"] is None  # no content unless AI_LOG_CONTENT


def test_content_is_logged_only_when_asked(cur, fake_ai, env):
    env(AI_LOG_CONTENT="true")
    fake_ai.push("hi")
    ai.text("core.test", system="sys", input="question")
    [c] = _calls(cur)
    assert c["input"] == {"system": "sys", "input": "question"} and c["output"] == {"text": "hi"}


def test_invalid_output_is_an_error(cur, fake_ai):
    fake_ai.push('{"canonical": "x"}')
    with pytest.raises(ai.AIError) as exc:
        ai.structured("ingest.map_columns", system="s", input="x", schema=Mapping)
    assert exc.value.code == "ai_invalid_output" and exc.value.status == 502
    assert [c["status"] for c in _calls(cur)] == ["invalid_output"]


def test_tool_calls_web_search_and_raw(cur, fake_ai):
    fake_ai.push([{"name": "query_metric", "arguments": {"metric": "core.signals_raised"}}])
    [call] = ai.tool_call("ask.answer", system="s", input="How many?", tools=[{"type": "function", "name": "query_metric"}])
    assert call.name == "query_metric" and call.arguments == {"metric": "core.signals_raised"}
    fake_ai.push(ai.AIResponse(output_text='{"hotels": ["Hotel A"]}', citations=[ai.Citation(url="https://a.example", title="A")]))
    found = ai.web_search("visibility.ai_answer", query="best hotel in Deira", schema=Answer)
    assert found.data.hotels == ["Hotel A"] and found.citations[0].url == "https://a.example"
    assert fake_ai.calls[-1].tools == [{"type": "web_search"}]
    fake_ai.push({"id": "resp_1", "output": []})
    assert ai.responses_raw("visibility.raw", input="q", tools=[{"type": "web_search"}])["id"] == "resp_1"


def test_fake_backend_errors(cur, fake_ai):
    with pytest.raises(ai.AIError):
        ai.text("core.test", system="s", input="nothing queued")
    fake_ai.push(ai.AIError("rate limited"))
    with pytest.raises(ai.AIError):
        ai.text("core.test", system="s", input="x")
    fake_ai.push(lambda req: f"echo {req.input}")
    assert ai.text("core.test", system="s", input="ping") == "echo ping"
    assert [c["status"] for c in _calls(cur)] == ["error", "error", "ok"]


def test_gemini_response_parsing():
    raw = {
        "response_id": "resp_9",
        "model_version": "gemini-2.5-flash",
        "candidates": [{
            "content": {"role": "model", "parts": [
                {"text": "thinking...", "thought": True},
                {"text": "Hotel A is popular."},
                {"function_call": {"name": "lookup", "args": {"q": 1}, "id": "call_1"}},
            ]},
            "grounding_metadata": {
                "web_search_queries": ["popular hotel"],
                "grounding_chunks": [
                    {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x", "title": "a.example"}},
                ],
                "grounding_supports": [{"segment": {"start_index": 0, "end_index": 7}, "grounding_chunk_indices": [0]}],
            },
        }],
        "usage_metadata": {"prompt_token_count": 120, "candidates_token_count": 25, "thoughts_token_count": 5, "cached_content_token_count": 20},
    }
    resp = SimpleNamespace(model_dump=lambda **_: raw)
    out = ai._parse_gemini(resp)
    assert out.response_id == "resp_9" and (out.tokens_in, out.tokens_out, out.cached_tokens_in) == (120, 30, 20)
    assert out.output_text == "Hotel A is popular." and out.web_search_calls == 1 and out.model == "gemini-2.5-flash"
    assert out.tool_calls == [ai.ToolCall(name="lookup", arguments={"q": 1}, call_id="call_1")]
    assert [(c.url, c.start_index, c.end_index) for c in out.citations] == [("https://a.example/", 0, 7)]
    assert [i["type"] for i in out.raw["output"]] == ["web_search_call", "message", "function_call"]


def test_gemini_schema_inlines_refs():
    class Inner(BaseModel):
        a: int = 1

    class Outer(BaseModel):
        items: list[Inner]

    g = ai._gemini_schema(ai.strict_schema(Outer))
    assert "$defs" not in g and g["properties"]["items"]["items"]["required"] == ["a"]
    assert "additionalProperties" not in g["properties"]["items"]["items"]


def test_strict_schema_handles_nested_models():
    class Inner(BaseModel):
        a: int = 1

    class Outer(BaseModel):
        items: list[Inner]
        extra: dict[str, int] | None = None

    s = ai.strict_schema(Outer)
    inner = s["$defs"]["Inner"]
    assert inner["additionalProperties"] is False and inner["required"] == ["a"] and "default" not in inner["properties"]["a"]
