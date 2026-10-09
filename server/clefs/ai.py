"""
Server-side AI calls through the Google Gemini API, google-genai SDK (01 spec E7, 04 spec F8).

    class Mapping(BaseModel): canonical: str; header: str; confidence: float
    out = structured("ingest.map_columns", system="...", input=headers_text, schema=Mapping, hotel_slug=slug)
    reply = text("reviews.draft_reply", system="...", input=review_text, hotel_slug=slug)
    calls = tool_call("ask.answer", system="...", input=question, tools=[...])
    found = web_search("visibility.ai_answer", query="best hotel in Deira", schema=Answer)
    found.data, found.citations

Model: the `model` argument, else AI_MODEL_<TASK> (task "ingest.map_columns"
reads AI_MODEL_INGEST_MAP_COLUMNS), else AI_MODEL_DEFAULT.

Backend: the Gemini Developer API with GEMINI_API_KEY, or Vertex AI when
GOOGLE_GENAI_USE_VERTEXAI=true (GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION),
which on Cloud Run authenticates as the service's own identity, no key.

Kill switch: AI_ENABLED=false (or neither GEMINI_API_KEY nor Vertex AI) makes
every call raise AIDisabled (409 ai_disabled), which callers catch to degrade:
a person reads the statement, the mapping falls back to aliases. Tests install
a FakeBackend with set_backend(), which works regardless of the switch.

Logging: every call writes one ai_calls row (task, model, tokens, latency,
status, cost). Prompts and outputs are stored only when AI_LOG_CONTENT=true,
because they carry guest and financial data. `store` (default False) stays in
the contract: generateContent keeps no conversation state on Google's side,
no caller overrides it (04 spec F8), and an override is logged as a warning.

Reply shape: every Gemini response is normalized (_normalize) into one
provider-neutral body: "output" items of type "message" (output_text with
url_citation annotations from Google Search grounding), "function_call" and
"web_search_call" (one per Google Search query), "usage" (input_tokens,
output_tokens, input_tokens_details.cached_tokens), "grounding" (queries and
sources) and the untouched Gemini JSON under "gemini". responses_raw()
returns this body, so callers parse one shape whatever the model.

Cost (Canon K3): vendor costs are sub-cent, so ai_calls.cost_micros holds
micro-USD (1 USD = 1,000,000 micros; 1 cent = 10,000). A price in USD per 1M
tokens is exactly micro-USD per token, so cost = tokens x price with no
scaling, plus WEB_SEARCH_CALL_MICROS per Google Search query the grounding ran.
Prices live in MODEL_PRICES and can be overridden per model with
AI_PRICE_<MODEL> = "in,out" or "in,cached_in,out" (USD per 1M tokens). An
unpriced model logs NULL, never a made-up 0. responses_raw() hands the cost
back under the reply's "clefs" key, which Visibility copies into its ledger.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time as _time
from collections import deque
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Generic, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from clefs import db
from clefs.core.errors import ClefsError
from clefs.settings import ai_model_for, env, get_settings

log = logging.getLogger("clefs.ai")

T = TypeVar("T", bound=BaseModel)


class AIDisabled(ClefsError):
    def __init__(self, reason: str = "AI is switched off"):
        super().__init__(409, reason, "ai_disabled")


class AIError(ClefsError):
    def __init__(self, detail: str, code: str = "ai_error"):
        super().__init__(502, detail, code)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict
    call_id: str | None = None


class Citation(BaseModel):
    url: str
    title: str | None = None
    start_index: int | None = None
    end_index: int | None = None


@dataclass
class SearchResult(Generic[T]):
    data: T
    citations: list[Citation]
    text: str | None = None


@dataclass
class AIRequest:
    task: str
    kind: str  # structured | text | tool_call | web_search | raw
    model: str
    system: str | None
    input: Any
    schema: dict | None = None
    schema_name: str | None = None
    tools: list[dict] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    max_output_tokens: int | None = None
    hotel_slug: str | None = None
    file_ids: list[str] = field(default_factory=list)
    store: bool = False
    timeout_s: float = 30.0
    # None: the model's default. Sent only when set (an eval judge asks for 0).
    temperature: float | None = None


@dataclass
class AIResponse:
    output_text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    tokens_in: int | None = None
    tokens_out: int | None = None
    response_id: str | None = None
    raw: dict | None = None
    cached_tokens_in: int | None = None  # usage_metadata.cached_content_token_count (billed cheaper)
    web_search_calls: int = 0  # Google Search queries the grounding ran (billed per query)
    model: str | None = None  # the model version the API says answered
    # Filled in by the ai_calls logger:
    cost_micros: int | None = None
    ai_call_id: int | None = None


# ─── Cost (Canon K3) ───

COST_CURRENCY = "USD"
# USD per 1M tokens: (input, cached input, output). Public list prices; a
# price change is an AI_PRICE_<MODEL> env var, not a deploy.
MODEL_PRICES: dict[str, tuple[str, str | None, str]] = {
    "gemini-3-pro-preview": ("2.00", "0.20", "12.00"),
    "gemini-3-flash-preview": ("0.50", "0.05", "3.00"),
    "gemini-2.5-pro": ("1.25", "0.125", "10.00"),
    "gemini-2.5-flash": ("0.30", "0.03", "2.50"),
    "gemini-2.5-flash-lite": ("0.10", "0.01", "0.40"),
    "gemini-2.0-flash": ("0.10", "0.025", "0.40"),
    "gemini-2.0-flash-lite": ("0.075", None, "0.30"),
}
# Grounding with Google Search: $14 per 1,000 search queries (04 spec 5.10),
# i.e. 1.4 cents a query.
WEB_SEARCH_CALL_MICROS = 14_000
# Version suffixes the API reports ("gemini-2.5-flash-preview-09-2025",
# "gemini-2.0-flash-001") price as their base model.
_SNAPSHOT_RE = re.compile(r"(-preview)?-\d{2}-\d{4}$|-\d{3}$")
_unpriced_warned: set[str] = set()


def _price_env_key(model: str) -> str:
    return "AI_PRICE_" + "".join(ch if ch.isalnum() else "_" for ch in model).upper()


def model_price(model: str | None) -> tuple[Decimal, Decimal, Decimal] | None:
    """(input, cached input, output) in micro-USD per token for `model` or its
    base ("gemini-2.5-flash-preview-09-2025" -> "gemini-2.5-flash"); None if
    unpriced. AI_PRICE_<MODEL> (either name) overrides the table."""
    if not model:
        return None
    name = model.strip().lower().removeprefix("models/")
    base = _SNAPSHOT_RE.sub("", name)
    for candidate in (name, base):
        raw = env(_price_env_key(candidate))
        if raw:
            try:
                parts = [Decimal(p.strip()) for p in raw.split(",")]
            except InvalidOperation:
                log.warning("ignoring %s=%r: expected 'in,out' or 'in,cached,out'", _price_env_key(candidate), raw)
                continue
            if len(parts) == 2:
                return parts[0], parts[0], parts[1]
            if len(parts) == 3:
                return parts[0], parts[1], parts[2]
            log.warning("ignoring %s=%r: expected 'in,out' or 'in,cached,out'", _price_env_key(candidate), raw)
    for candidate in (name, base):
        if candidate in MODEL_PRICES:
            i, c, o = MODEL_PRICES[candidate]
            return Decimal(i), Decimal(c if c is not None else i), Decimal(o)
    return None


def cost_micros(
    model: str | None,
    *,
    tokens_in: int | None,
    tokens_out: int | None,
    cached_tokens_in: int | None = 0,
    web_search_calls: int = 0,
) -> int | None:
    """What one call cost in micro-USD, rounded half up; None when the model
    is not priced (a NULL cost is honest, a 0 would under-count budgets)."""
    price = model_price(model)
    if price is None:
        if model and model not in _unpriced_warned:
            _unpriced_warned.add(model)
            log.warning("no price for AI model %s: ai_calls.cost_micros stays NULL (set %s)", model, _price_env_key(model))
        return None
    if tokens_in is None and tokens_out is None and not web_search_calls:
        return None
    p_in, p_cached, p_out = price
    t_in = max(int(tokens_in or 0), 0)
    cached = min(max(int(cached_tokens_in or 0), 0), t_in)
    total = (
        Decimal(t_in - cached) * p_in
        + Decimal(cached) * p_cached
        + Decimal(max(int(tokens_out or 0), 0)) * p_out
        + Decimal(max(int(web_search_calls or 0), 0) * WEB_SEARCH_CALL_MICROS)
    )
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _usage_fields(raw: dict) -> dict:
    """Token counts, web-search calls and the answering model from a
    normalized body (_normalize's, or one a FakeBackend test queued)."""
    usage = raw.get("usage") or {}
    details = usage.get("input_tokens_details") or {}
    return {
        "tokens_in": usage.get("input_tokens"),
        "tokens_out": usage.get("output_tokens"),
        "cached_tokens_in": details.get("cached_tokens"),
        "web_search_calls": sum(1 for item in raw.get("output") or [] if isinstance(item, dict) and item.get("type") == "web_search_call"),
        "model": raw.get("model"),
    }


# ─── Backends ───


_client_cache: dict[tuple, Any] = {}
_client_lock = threading.Lock()


def genai_client(*, api_version: str | None = None) -> Any:
    """A google-genai Client for this deployment, cached per configuration:
    Vertex AI when GOOGLE_GENAI_USE_VERTEXAI is on (on Cloud Run, the
    service's own identity), else the Gemini Developer API with
    GEMINI_API_KEY. Live API ephemeral tokens need the Developer API."""
    from google import genai

    s = get_settings()
    key = (s.GOOGLE_GENAI_USE_VERTEXAI, s.GOOGLE_CLOUD_PROJECT, s.GOOGLE_CLOUD_LOCATION, s.GEMINI_API_KEY, api_version)
    with _client_lock:
        client = _client_cache.get(key)
        if client is None:
            http_options = {"api_version": api_version} if api_version else None
            if s.GOOGLE_GENAI_USE_VERTEXAI:
                client = genai.Client(
                    vertexai=True, project=s.GOOGLE_CLOUD_PROJECT, location=s.GOOGLE_CLOUD_LOCATION, http_options=http_options
                )
            else:
                # vertexai=False: the SDK reads GOOGLE_GENAI_USE_VERTEXAI from the
                # environment on its own and would otherwise switch modes.
                client = genai.Client(vertexai=False, api_key=s.GEMINI_API_KEY, http_options=http_options)
            _client_cache[key] = client
    return client


class GeminiBackend:
    name = "gemini"

    def __init__(self) -> None:
        self._client = genai_client()

    def respond(self, req: AIRequest) -> AIResponse:
        system, contents = _contents(req)
        tools = _tools(req.tools)
        grounded = any("google_search" in t for t in tools)
        config: dict[str, Any] = {"http_options": {"timeout": int(req.timeout_s * 1000)}}
        if req.schema is not None:
            if grounded:
                # A pure-JSON reply comes back without grounding sources (and
                # Gemini 2.5 refuses a JSON format next to Google Search), so a
                # grounded answer is prose first, then the JSON, which
                # _extract_json cuts out; the prose carries the citations.
                system = (system or "") + _schema_instruction(req.schema)
            else:
                config["response_mime_type"] = "application/json"
                config["response_json_schema"] = _gemini_schema(req.schema)
        if system:
            config["system_instruction"] = system
        if tools:
            config["tools"] = tools
            config["automatic_function_calling"] = {"disable": True}
        if req.max_output_tokens:
            config["max_output_tokens"] = req.max_output_tokens
        if req.temperature is not None:
            config["temperature"] = req.temperature
        thinking = _thinking_config(req.model)
        if thinking:
            config["thinking_config"] = thinking
        try:
            resp = self._client.models.generate_content(model=req.model, contents=contents, config=config)
        except Exception as exc:  # network, quota, 4xx/5xx
            raise AIError(f"Gemini call failed: {type(exc).__name__}") from exc
        out = _parse_gemini(resp)
        if req.schema is not None and grounded and out.output_text:
            out.output_text = _extract_json(out.output_text)
        return out


def _is_gemini_3(model: str | None) -> bool:
    return (model or "").lower().removeprefix("models/").startswith("gemini-3")


def _thinking_config(model: str | None) -> dict | None:
    """Thinking tokens count against max_output_tokens, so Gemini 2.5 Flash and
    Flash-Lite run without them, Gemini 3 Flash-Lite at its minimal level (on
    "low" it can spend a small output budget on thoughts and cut the JSON
    short) and other Gemini 3 models at their low level (they reject
    "minimal"); AI_THINKING_BUDGET (an int, -1 = dynamic) overrides for every
    model."""
    override = env("AI_THINKING_BUDGET")
    if override:
        try:
            return {"thinking_budget": int(override)}
        except ValueError:
            log.warning("ignoring AI_THINKING_BUDGET=%r: expected an integer", override)
    m = (model or "").lower()
    if "2.5-flash" in m:
        return {"thinking_budget": 0}
    if _is_gemini_3(m):
        return {"thinking_level": "minimal" if "flash-lite" in m else "low"}
    return None


# Gemini 3 rejects a replayed function call without its thought signature; a
# call rebuilt without one carries Google's documented bypass value instead.
_SKIP_SIGNATURE = b"skip_thought_signature_validator"


def _b64decode(value: str) -> bytes:
    """A thought signature from a reply body: the SDK writes bytes as URL-safe
    base64, sometimes unpadded; standard base64 is read too."""
    s = value.replace("+", "-").replace("/", "_")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text") or "" if isinstance(p, dict) else str(p) for p in content)
    return json.dumps(content, default=str)


def _file_part(file_id: str) -> dict:
    """A file the Gemini Files API (or Cloud Storage) holds: "<uri>" or
    "<uri>|<mime type>" (PDF when the type is left out)."""
    uri, _, mime = file_id.partition("|")
    return {"file_data": {"file_uri": uri, "mime_type": mime or "application/pdf"}}


def _contents(req: AIRequest) -> tuple[str | None, list[dict]]:
    """(system instruction, Gemini contents) for the request's input: a str,
    or a list of message / function_call / function_call_output items (the
    normalized reply shape). System and developer messages join the system
    instruction; function outputs are matched to their call's name."""
    system = [req.system] if req.system else []
    contents: list[dict] = []
    names: dict[str, str] = {}

    def add(role: str, part: dict) -> None:
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append(part)
        else:
            contents.append({"role": role, "parts": [part]})

    items = req.input if isinstance(req.input, list) else [req.input]
    for item in items:
        if not isinstance(item, dict):
            add("user", {"text": item if isinstance(item, str) else json.dumps(item, default=str)})
            continue
        kind = item.get("type")
        if kind == "function_call":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {"_raw": item.get("arguments")}
            name = item.get("name", "")
            names[str(item.get("call_id") or item.get("id"))] = name
            part: dict = {"function_call": {"name": name, "args": args if isinstance(args, dict) else {"value": args}}}
            if item.get("thought_signature"):
                part["thought_signature"] = _b64decode(item["thought_signature"])
            elif _is_gemini_3(req.model):
                part["thought_signature"] = _SKIP_SIGNATURE
            add("model", part)
        elif kind == "function_call_output":
            output = item.get("output")
            try:
                response = json.loads(output) if isinstance(output, str) else output
            except json.JSONDecodeError:
                response = output
            name = names.get(str(item.get("call_id")), item.get("name", ""))
            add("user", {"function_response": {"name": name, "response": response if isinstance(response, dict) else {"output": response}}})
        elif kind in ("web_search_call", "reasoning"):
            continue
        else:  # a message
            role = item.get("role", "user")
            text = _text_of(item.get("content"))
            if role in ("system", "developer"):
                system.append(text)
            elif text:
                add("model" if role == "assistant" else "user", {"text": text})
    if req.file_ids:
        if not contents or contents[-1]["role"] != "user":
            contents.append({"role": "user", "parts": []})
        contents[-1]["parts"] += [_file_part(f) for f in req.file_ids]
    return ("\n\n".join(system) or None), contents


def _tools(tools: list[dict]) -> list[dict]:
    """Function tools ({"type": "function", "name", "description",
    "parameters"}) become one Gemini function_declarations tool, and
    {"type": "web_search"} becomes Grounding with Google Search. Tools already
    in Gemini form pass through."""
    out: list[dict] = []
    decls: list[dict] = []
    for t in tools or []:
        kind = t.get("type")
        if kind in ("web_search", "web_search_preview", "google_search"):
            out.append({"google_search": {}})
        elif kind == "function":
            fn = t.get("function") or t
            decl: dict = {"name": fn["name"], "description": fn.get("description") or ""}
            if fn.get("parameters"):
                decl["parameters_json_schema"] = _gemini_schema(fn["parameters"])
            decls.append(decl)
        elif kind is None:
            out.append(t)
    if decls:
        out.append({"function_declarations": decls})
    return out


_DROP_KEYS = {"$defs", "definitions", "default", "additionalProperties", "$schema", "discriminator", "strict"}


def _gemini_schema(schema: dict) -> dict:
    """A JSON schema Gemini accepts for response_json_schema and function
    parameters: $refs inlined, defaults and additionalProperties dropped,
    const as a one-value enum."""
    defs = dict(schema.get("$defs") or schema.get("definitions") or {})

    def walk(node: Any, depth: int = 0) -> Any:
        if isinstance(node, list):
            return [walk(v, depth) for v in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and depth < 32 and ref.rsplit("/", 1)[-1] in defs:
            merged = {**defs[ref.rsplit("/", 1)[-1]], **{k: v for k, v in node.items() if k != "$ref"}}
            return walk(merged, depth + 1)
        out: dict = {}
        for k, v in node.items():
            if k in _DROP_KEYS:
                continue
            if k == "const":
                out.setdefault("enum", [v])
            elif k == "properties" and isinstance(v, dict):
                out[k] = {name: walk(sub, depth) for name, sub in v.items()}
            else:
                out[k] = walk(v, depth)
        return out

    return walk(schema)


def _schema_instruction(schema: dict) -> str:
    return (
        "\n\nFirst answer in a few sentences of plain prose from the search results. Then, on its own line, give "
        "one JSON object (no code fences) that validates against this JSON schema:\n"
        + json.dumps(_gemini_schema(schema), separators=(",", ":"))
    )


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def _extract_json(text: str) -> str:
    """The last JSON object in a grounded reply (prose and fences cut away);
    the text unchanged when there is none, so validation reports it."""
    s = text.strip()
    end = s.rfind("}")
    start = s.rfind("{", 0, end + 1)
    while start != -1 and end != -1:
        candidate = s[start : end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            start = s.rfind("{", 0, start)
    return _FENCE_RE.sub("", s)


_GOOGLE_REDIRECT_HOSTS = {"vertexaisearch.cloud.google.com"}
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$", re.IGNORECASE)


def _source(chunk: dict) -> dict:
    """One grounding chunk as {"url", "title", "domain"}. Google Search
    grounding links go through a redirect host, so the url becomes the
    source's own site (its domain is in the title) and the redirect is kept."""
    web = chunk.get("web") or chunk.get("retrieved_context") or {}
    uri = web.get("uri") or ""
    title = web.get("title") or None
    host = (urlparse(uri).hostname or "").lower()
    domain = web.get("domain") or (title if title and _DOMAIN_RE.match(title) else None)
    if host in _GOOGLE_REDIRECT_HOSTS and domain:
        return {"url": f"https://{domain}/", "title": title, "domain": domain, "redirect_url": uri}
    return {"url": uri, "title": title, "domain": domain or host.removeprefix("www.") or None}


def _char_index(text: str, byte_index: int | None) -> int | None:
    """Grounding segments count UTF-8 bytes; citations count characters."""
    if byte_index is None:
        return None
    return len(text.encode("utf-8")[:byte_index].decode("utf-8", errors="ignore"))


def _normalize(raw: dict) -> dict:
    """The provider-neutral body for one Gemini response (module docstring)."""
    cand = (raw.get("candidates") or [{}])[0] or {}
    parts = (cand.get("content") or {}).get("parts") or []
    meta = cand.get("grounding_metadata") or {}
    sources = [_source(c) for c in meta.get("grounding_chunks") or []]
    queries = list(meta.get("web_search_queries") or [])
    output: list[dict] = [
        {"type": "web_search_call", "status": "completed", "action": {"type": "search", "query": q}} for q in queries
    ]
    text, offsets, calls = "", {}, []
    for i, part in enumerate(parts):
        if part.get("thought"):
            continue
        fc = part.get("function_call")
        if fc:
            name = fc.get("name") or ""
            call = {
                "type": "function_call", "status": "completed", "name": name,
                "arguments": json.dumps(fc.get("args") or {}, default=str),
                "call_id": fc.get("id") or f"call_{len(calls)}_{name}",
            }
            if part.get("thought_signature"):
                call["thought_signature"] = part["thought_signature"]
            calls.append(call)
        elif part.get("text"):
            offsets[i] = (len(text), part["text"])
            text += part["text"]
    annotations: list[dict] = []
    for support in meta.get("grounding_supports") or []:
        seg = support.get("segment") or {}
        base, part_text = offsets.get(seg.get("part_index") or 0, (0, text))
        start, end = _char_index(part_text, seg.get("start_index") or 0), _char_index(part_text, seg.get("end_index"))
        for ci in support.get("grounding_chunk_indices") or []:
            if 0 <= ci < len(sources):
                src = sources[ci]
                annotations.append({
                    "type": "url_citation", "url": src["url"], "title": src["title"],
                    "start_index": base + start if start is not None else None,
                    "end_index": base + end if end is not None else None,
                })
    if not annotations:
        annotations = [{"type": "url_citation", "url": s["url"], "title": s["title"], "start_index": None, "end_index": None} for s in sources if s["url"]]
    if text or not calls:
        output.append({
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": annotations}],
        })
    output.extend(calls)
    usage = raw.get("usage_metadata") or {}
    t_in = usage.get("prompt_token_count")
    if usage.get("tool_use_prompt_token_count"):
        t_in = (t_in or 0) + usage["tool_use_prompt_token_count"]
    t_out = None
    if usage.get("candidates_token_count") is not None or usage.get("thoughts_token_count") is not None:
        t_out = (usage.get("candidates_token_count") or 0) + (usage.get("thoughts_token_count") or 0)
    finish = cand.get("finish_reason")
    return {
        "id": raw.get("response_id"),
        "provider": "google",
        "model": raw.get("model_version"),
        "status": "incomplete" if finish == "MAX_TOKENS" else "completed",
        "output_text": text,
        "output": output,
        "usage": {
            "input_tokens": t_in,
            "output_tokens": t_out,
            "total_tokens": usage.get("total_token_count"),
            "input_tokens_details": {"cached_tokens": usage.get("cached_content_token_count") or 0},
            "output_tokens_details": {"reasoning_tokens": usage.get("thoughts_token_count") or 0},
        },
        "grounding": {
            "web_search_queries": queries,
            "sources": sources,
            "search_entry_point_html": (meta.get("search_entry_point") or {}).get("rendered_content"),
        },
        "gemini": raw,
    }


def _parse_gemini(resp: Any) -> AIResponse:
    raw = resp.model_dump(mode="json", exclude_none=True) if hasattr(resp, "model_dump") else dict(resp)
    body = _normalize(raw)
    calls: list[ToolCall] = []
    cites: list[Citation] = []
    for item in body["output"]:
        if item["type"] == "function_call":
            try:
                args = json.loads(item["arguments"])
            except json.JSONDecodeError:
                args = {"_raw": item["arguments"]}
            calls.append(ToolCall(name=item["name"], arguments=args, call_id=item["call_id"]))
        elif item["type"] == "message":
            for part in item["content"]:
                for ann in part["annotations"]:
                    cites.append(Citation(url=ann["url"], title=ann.get("title"), start_index=ann.get("start_index"), end_index=ann.get("end_index")))
    return AIResponse(
        output_text=body["output_text"] or None,
        tool_calls=calls,
        citations=cites,
        response_id=body.get("id"),
        raw=body,
        **_usage_fields(body),
    )


class FakeBackend:
    """Test backend: records every AIRequest in .calls and answers from a queue.

    Queue entries: a str (text), a dict or BaseModel (structured / raw JSON),
    a list of ToolCall or {"name", "arguments"} dicts (tool_call), an
    AIResponse (anything, including citations), an exception (raised), or a
    callable(req) returning one of those."""

    name = "fake"

    def __init__(self, *outputs: Any) -> None:
        self.calls: list[AIRequest] = []
        self._queue: deque = deque(outputs)
        self._lock = threading.Lock()

    def push(self, *outputs: Any) -> "FakeBackend":
        with self._lock:
            self._queue.extend(outputs)
        return self

    @property
    def pending(self) -> int:
        return len(self._queue)

    def respond(self, req: AIRequest) -> AIResponse:
        with self._lock:
            self.calls.append(req)
            if not self._queue:
                raise AIError(f"FakeBackend: no output queued for {req.task}")
            out = self._queue.popleft()
        if callable(out) and not isinstance(out, (BaseModel, type)):
            out = out(req)
        if isinstance(out, BaseException):
            raise out
        return _coerce(out, req)


def _coerce(out: Any, req: AIRequest) -> AIResponse:
    if isinstance(out, AIResponse):
        return out
    if isinstance(out, BaseModel):
        return AIResponse(output_text=out.model_dump_json(), tokens_in=10, tokens_out=10)
    if isinstance(out, str):
        return AIResponse(output_text=out, tokens_in=10, tokens_out=10)
    if isinstance(out, list):
        calls = [c if isinstance(c, ToolCall) else ToolCall(name=c["name"], arguments=c.get("arguments", {}), call_id=c.get("call_id")) for c in out]
        return AIResponse(tool_calls=calls, tokens_in=10, tokens_out=10)
    if isinstance(out, dict):
        resp = AIResponse(output_text=json.dumps(out, default=str), raw=out, tokens_in=10, tokens_out=10)
        if req.kind == "raw":
            # A queued normalized body: take its usage and web_search_call
            # items, so tests see the cost the live API would produce.
            fields = _usage_fields(out)
            resp.output_text = out.get("output_text", resp.output_text)
            resp.response_id = out.get("id")
            resp.cached_tokens_in = fields["cached_tokens_in"]
            resp.web_search_calls = fields["web_search_calls"]
            resp.model = fields["model"]
            if fields["tokens_in"] is not None or fields["tokens_out"] is not None:
                resp.tokens_in, resp.tokens_out = fields["tokens_in"], fields["tokens_out"]
        return resp
    raise TypeError(f"FakeBackend cannot answer with {type(out).__name__}")


_backend_override: Any = None
_backend_lock = threading.Lock()


def set_backend(backend: Any) -> Any:
    """Install a backend (tests: FakeBackend()). Returns it."""
    global _backend_override
    with _backend_lock:
        _backend_override = backend
    return backend


def reset_backend() -> None:
    set_backend(None)


def get_backend() -> Any:
    with _backend_lock:
        override = _backend_override
    if override is not None:
        return override
    s = get_settings()
    if not s.AI_ENABLED:
        raise AIDisabled("AI is switched off (AI_ENABLED=false)")
    if not (s.GEMINI_API_KEY or s.GOOGLE_GENAI_USE_VERTEXAI):
        raise AIDisabled("AI is not configured (no GEMINI_API_KEY, and Vertex AI is off)")
    return GeminiBackend()


def enabled() -> bool:
    try:
        get_backend()
    except AIDisabled:
        return False
    return True


# ─── Logging ───


def _log_call(req: AIRequest, backend_name: str, resp: AIResponse | None, status: str, t0: float, error: str | None) -> None:
    """One ai_calls row per call. A response's cost (micro-USD) is computed
    here and also stored on `resp` (cost_micros, ai_call_id) for callers
    that keep their own vendor ledger."""
    s = get_settings()
    content = s.AI_LOG_CONTENT
    cost = None
    if resp is not None:
        cost = cost_micros(
            resp.model or req.model,
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
            cached_tokens_in=resp.cached_tokens_in,
            web_search_calls=resp.web_search_calls,
        )
        resp.cost_micros = cost
    try:
        with db.db() as cur:
            cur.execute(
                """
                INSERT INTO ai_calls (hotel_slug, task, kind, model, backend, status, tokens_in, tokens_out, latency_ms,
                                      cost_micros, cost_currency, response_id, error, input, output)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    req.hotel_slug,
                    req.task,
                    req.kind,
                    req.model,
                    backend_name,
                    status,
                    resp.tokens_in if resp else None,
                    resp.tokens_out if resp else None,
                    int((_time.monotonic() - t0) * 1000),
                    cost,
                    COST_CURRENCY,
                    resp.response_id if resp else None,
                    (error or "")[:1000] or None,
                    db.Json(json.loads(json.dumps({"system": req.system, "input": req.input}, default=str))) if content else None,
                    db.Json({"text": resp.output_text}) if (content and resp is not None) else None,
                ),
            )
            row = cur.fetchone()
            if resp is not None and row is not None:
                resp.ai_call_id = row["id"]
    except Exception:  # logging must never fail the caller
        log.exception("could not log ai call for %s", req.task)


def _call(req: AIRequest, schema: type[BaseModel] | None = None) -> tuple[AIResponse, Any]:
    """Run one request through the backend, parse it against `schema` when
    given, and log exactly one ai_calls row for it."""
    t0 = _time.monotonic()
    if req.store:
        log.warning("ai task %s passed store=True: ignored, Gemini keeps no stored responses (04 spec F8 keeps it False)", req.task)
    try:
        backend = get_backend()
    except AIDisabled as exc:
        _log_call(req, "disabled", None, "disabled", t0, exc.detail)
        raise
    try:
        resp = backend.respond(req)
    except AIError as exc:
        _log_call(req, backend.name, None, "error", t0, exc.detail)
        raise
    except Exception as exc:
        _log_call(req, backend.name, None, "error", t0, f"{type(exc).__name__}: {exc}")
        raise AIError(f"AI call failed: {type(exc).__name__}") from exc
    parsed = None
    if schema is not None:
        try:
            parsed = schema.model_validate_json(resp.output_text or "")
        except ValidationError as exc:
            _log_call(req, backend.name, resp, "invalid_output", t0, str(exc)[:500])
            raise AIError("The AI answer did not match the expected format", "ai_invalid_output") from exc
    _log_call(req, backend.name, resp, "ok", t0, None)
    return resp, parsed


# ─── Strict JSON schema ───


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema made strict for Gemini structured output: every
    object lists all its properties as required and forbids extras; defaults
    are dropped (optional fields stay nullable through anyOf)."""
    schema = model.model_json_schema()

    def fix(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" or "properties" in node:
                props = node.get("properties") or {}
                node["required"] = list(props)
                node["additionalProperties"] = False
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(schema)
    return schema


# ─── Public API ───


def structured(
    task: str,
    *,
    system: str,
    input: Any,  # noqa: A002 - the contract's name
    schema: type[T],
    hotel_slug: str | None = None,
    model: str | None = None,
    file_ids: list[str] | tuple[str, ...] = (),
    max_output_tokens: int = 4000,
    store: bool = False,
    timeout_s: float = 30.0,
    temperature: float | None = None,
) -> T:
    req = AIRequest(
        task=task, kind="structured", model=model or ai_model_for(task), system=system, input=input,
        schema=strict_schema(schema), schema_name=schema.__name__, hotel_slug=hotel_slug, file_ids=list(file_ids),
        max_output_tokens=max_output_tokens, store=store, timeout_s=timeout_s, temperature=temperature,
    )
    return _call(req, schema)[1]


def text(
    task: str,
    *,
    system: str,
    input: Any,  # noqa: A002
    hotel_slug: str | None = None,
    model: str | None = None,
    max_output_tokens: int = 2000,
    store: bool = False,
    timeout_s: float = 30.0,
) -> str:
    req = AIRequest(
        task=task, kind="text", model=model or ai_model_for(task), system=system, input=input, hotel_slug=hotel_slug,
        max_output_tokens=max_output_tokens, store=store, timeout_s=timeout_s,
    )
    return _call(req)[0].output_text or ""


def tool_call(
    task: str,
    *,
    system: str,
    input: Any,  # noqa: A002
    tools: list[dict],
    hotel_slug: str | None = None,
    model: str | None = None,
    store: bool = False,
    timeout_s: float = 30.0,
) -> list[ToolCall]:
    """Function tools as {"type": "function", "name", "description", "parameters"}, sent as Gemini function declarations."""
    req = AIRequest(
        task=task, kind="tool_call", model=model or ai_model_for(task), system=system, input=input, tools=list(tools),
        hotel_slug=hotel_slug, store=store, timeout_s=timeout_s,
    )
    return _call(req)[0].tool_calls


def web_search(
    task: str,
    *,
    query: str,
    schema: type[T],
    hotel_slug: str | None = None,
    model: str | None = None,
    system: str | None = None,
    store: bool = False,
    timeout_s: float = 60.0,
) -> SearchResult[T]:
    """Grounding with Google Search plus a structured answer, with the grounding
    sources returned as citations (Visibility requires at least one)."""
    req = AIRequest(
        task=task, kind="web_search", model=model or ai_model_for(task),
        system=system or "Answer from current web results. Cite sources.", input=query,
        schema=strict_schema(schema), schema_name=schema.__name__, tools=[{"type": "web_search"}],
        hotel_slug=hotel_slug, store=store, timeout_s=timeout_s,
    )
    resp, parsed = _call(req, schema)
    return SearchResult(data=parsed, citations=resp.citations, text=resp.output_text)


def responses_raw(
    task: str,
    *,
    input: Any,  # noqa: A002
    tools: list[dict],
    include: tuple[str, ...] | list[str] = (),
    model: str | None = None,
    hotel_slug: str | None = None,
    timeout_s: float = 15.0,
    store: bool = False,
) -> dict:
    """The normalized reply body (Visibility parses citations and sources itself),
    logged in ai_calls with its cost like every other call.

    The reply is a copy of the body with one added key, "clefs":
    {"ai_call_id", "cost_micros" (micro-USD, None when the model is unpriced),
    "cost_currency": "USD"}, so a caller with its own vendor ledger
    (vis_api_costs, Canon K3) records the same number ai_calls holds."""
    req = AIRequest(
        task=task, kind="raw", model=model or ai_model_for(task), system=None, input=input, tools=list(tools),
        include=list(include), hotel_slug=hotel_slug, store=store, timeout_s=timeout_s,
    )
    resp = _call(req)[0]
    body = dict(resp.raw) if resp.raw else {"output_text": resp.output_text}
    body["clefs"] = {"ai_call_id": resp.ai_call_id, "cost_micros": resp.cost_micros, "cost_currency": COST_CURRENCY}
    return body

