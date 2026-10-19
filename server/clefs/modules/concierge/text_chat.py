"""
Concierge text chat: the Responses API fallback when WebRTC fails, the
WhatsApp chat surface and Trust's evals (03 spec 2.1 surface text_fallback,
2.4, 5.4).

The same server-built prompt and the same tool executor as the voice
session: the model runs through clefs.ai.responses_raw (logged in ai_calls
with its cost, store=False), and every function call it makes is executed
here by tools.execute, then fed back as a function_call_output until the
model answers in words (at most MAX_TOOL_ROUNDS rounds).

respond_text(spec, messages) is api.respond_text's body. A dry-run spec
(eval) uses a synthetic session, so tools answer realistically and nothing
is filed; any other spec gets a real cc_sessions row for its tool calls.
chat_turn(session, message) is one guest message on a stored session (the
HTTP chat route and WhatsApp): turn log and safety double check, the mood
score, the model, the reply turn, and the session's short working memory
(chat_history).

Database work happens in short db() blocks between model calls, so no
pooled connection is held while Gemini thinks. On the HTTP route the guest
waits for the whole turn, so the two side checks that call a model (the
safety model's second opinion and the mood score) run on worker threads
beside the reply model instead of in front of it.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import Future
from contextlib import contextmanager
from typing import Any, Callable

from clefs import ai, db
from clefs.db import Json
from clefs.modules.concierge import tools

log = logging.getLogger("clefs.concierge.text_chat")

TASK = "concierge.text"
HISTORY_MAX = 20            # messages of working memory per text session
MESSAGE_MAX = 4000
MAX_TOOL_ROUNDS = 4
MODEL_TIMEOUT_S = 20.0
# How long a finished reply waits for the side checks. The safety model has
# its own short budget (Service settings, 1.5 s by default) and has had the
# whole reply to run; the mood score never holds the reply up for long.
SAFETY_WAIT_S = 2.5
MOOD_TOOL_WAIT_S = 1.0      # before the turn's first job: so it carries this turn's mood
MOOD_REPLY_WAIT_S = 0.3     # after the reply; a later score is stored when it lands
HANDOFF_TEXT = "Let me connect you with our front desk team, who can help with that."
ERROR_TEXT = "Sorry, I could not answer just now. Please try again, or call the front desk if it is urgent."


# ─── Model plumbing ───


def responses_tools(schemas: list[dict]) -> list[dict]:
    """Realtime schemas in Responses form. strict=False: the optional
    arguments (quantity, scheduled_for) must stay optional."""
    return [{**t, "strict": False} for t in schemas if t.get("type") == "function"]


def parse_output(body: dict) -> tuple[str, list[dict]]:
    """(text, [{name, arguments, call_id}]) from a Responses body."""
    texts: list[str] = []
    calls: list[dict] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            raw = item.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except ValueError:
                args = {}
            calls.append({"name": item.get("name") or "", "arguments": args if isinstance(args, dict) else {},
                          "call_id": item.get("call_id") or item.get("id") or "", "raw_arguments": raw})
        elif item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                    texts.append(part.get("text") or "")
    text = "".join(texts).strip() or str(body.get("output_text") or "").strip()
    return text, calls


def _clean_text(text: str) -> str:
    from clefs.modules.concierge.prompt import sanitize

    return sanitize(text or "").strip()[:MESSAGE_MAX]


def history_messages(messages: list[dict]) -> list[dict]:
    """Guest and assistant text only, trimmed; anything else (a client that
    sends system messages) is dropped: the prompt is the server's."""
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = {"user": "user", "guest": "user", "assistant": "assistant"}.get(str(m.get("role") or "").lower())
        content = m.get("content") if isinstance(m.get("content"), str) else m.get("text")
        if role and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content.strip()[:MESSAGE_MAX]})
    return out[-HISTORY_MAX:]


Executor = Callable[[str, dict, str], dict]


def responses_model(model: str | None) -> str | None:
    """The model id to send to ai.responses_raw. A Gemini Live id
    (gemini-live-*, *native-audio*) is served only by the Live API, so it is
    never forwarded: None falls back to the concierge.text task model."""
    m = (model or "").strip()
    return None if not m or is_live_model(m) else m


def is_live_model(model: str) -> bool:
    """A Gemini Live API (realtime voice) model id."""
    m = model.lower()
    return "live" in m or "native-audio" in m


def run_model(spec: Any, history: list[dict], execute: Executor, *, server_note: str | None = None,
              raise_ai_errors: bool = False):
    """The tool loop. Returns api.TextTurn (text, tool_calls[{name,
    call_id, arguments, result}], handoff). A guest gets a hand-off
    sentence when the model is off or failing; raise_ai_errors=True lets an
    eval runner tell "runner unavailable" from a bad answer instead."""
    from clefs.modules.concierge.api import TextTurn

    input_items: list[dict] = [{"role": "system", "content": spec.instructions}]
    if server_note:
        input_items.append({"role": "system", "content": server_note})
    input_items += history_messages(history)
    schemas = responses_tools(spec.tools or [])
    log_calls: list[dict] = []
    handoff = False
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            body = ai.responses_raw(TASK, input=list(input_items), tools=schemas, model=responses_model(spec.model),
                                    hotel_slug=spec.hotel_slug, timeout_s=MODEL_TIMEOUT_S)
        except ai.AIDisabled:
            if raise_ai_errors:
                raise
            return TextTurn(text=HANDOFF_TEXT, tool_calls=log_calls, handoff=True)
        except Exception as exc:  # AIError, a timeout: never a 500 mid-conversation
            if raise_ai_errors:
                raise
            log.warning("concierge text model failed for %s: %s", spec.hotel_slug, getattr(exc, "detail", None) or type(exc).__name__)
            return TextTurn(text=ERROR_TEXT, tool_calls=log_calls, handoff=True)
        text, calls = parse_output(body)
        if not calls:
            return TextTurn(text=_clean_text(text) or HANDOFF_TEXT, tool_calls=log_calls, handoff=handoff or not text)
        for call in calls:
            out = execute(call["name"], call["arguments"], call["call_id"])
            log_calls.append({"name": call["name"], "call_id": call["call_id"], "arguments": call["arguments"], "result": out})
            handoff = handoff or (call["name"] == "escalate_to_human" and out.get("status") == "escalated")
            raw_args = call["raw_arguments"] if isinstance(call["raw_arguments"], str) else json.dumps(call["arguments"])
            # Replayed without the item id: with store=False nothing is kept
            # server side, so the call is resent in full.
            input_items.append({"type": "function_call", "call_id": call["call_id"], "name": call["name"], "arguments": raw_args})
            input_items.append({"type": "function_call_output", "call_id": call["call_id"],
                                "output": json.dumps(out, default=str)})
    return TextTurn(text=HANDOFF_TEXT, tool_calls=log_calls, handoff=True)


def last_ui(turn: Any) -> dict | None:
    """The UI hint of the most important tool result: an emergency card
    beats an order, which beats the request tracker."""
    rank = {"emergency": 0, "order": 1, "request": 2, "handoff": 3, "tracker": 4}
    uis = [c["result"]["ui"] for c in turn.tool_calls if isinstance(c.get("result"), dict) and c["result"].get("ui")]
    return min(uis, key=lambda u: rank.get(u.get("kind"), 9)) if uis else None


def call_summary(turn: Any) -> list[dict]:
    """What the client may see of the tool calls (no arguments)."""
    return [{"name": c["name"], "status": (c.get("result") or {}).get("status"),
             "ui": (c.get("result") or {}).get("ui")} for c in turn.tool_calls]


# ─── Sessions ───

# A voice surface's session falls back to text on the same token: the text
# model and prompt variant are text_fallback's, the room binding stays.
TEXT_SURFACE = {"web_room": "text_fallback", "web_location": "text_fallback", "phone": "text_fallback"}


def spec_for_session(cur: Any, session: dict, language: str | None = None) -> Any:
    from clefs.modules.concierge import api, sessions

    guest = None
    if session.get("verified") and session.get("room"):
        guest = sessions.guest_context(cur, session["hotel_slug"], session["room"])
    surface = TEXT_SURFACE.get(session.get("surface") or "", session.get("surface") or "text_fallback")
    return api.build_surface_session(
        session["hotel_slug"], surface=surface, room=session.get("room"), location=session.get("location_slug"),
        language=language or session.get("language"), verified=bool(session.get("verified")), guest=guest, cur=cur,
    )


@contextmanager
def _tx(cur: Any = None):
    """The caller's cursor (a worker task, the WhatsApp webhook: one
    transaction, which a session row created in it needs), else a short
    db() block of our own."""
    if cur is not None:
        yield cur
        return
    with db.db() as own:
        yield own


def executor_for(session: dict, *, dry_run: bool | None = None, cur: Any = None,
                 before: Callable[[], None] | None = None) -> Executor:
    """Each tool call in its own short transaction (see the module note),
    or on `cur` when the caller has one. `before` runs ahead of each call,
    outside any transaction (the chat turn settles its mood score there)."""

    def run(name: str, args: dict, call_id: str) -> dict:
        if before is not None:
            before()
        with _tx(cur) as c:
            return tools.execute(c, session, name, args, call_id=call_id or None, dry_run=dry_run)

    return run


def load_history(cur: Any, session: dict) -> list[dict]:
    if session.get("synthetic"):
        return []
    cur.execute("SELECT chat_history FROM cc_sessions WHERE id = %s", (session["id"],))
    row = cur.fetchone()
    return history_messages((row or {}).get("chat_history") or [])


def save_history(cur: Any, session: dict, history: list[dict]) -> None:
    if session.get("synthetic"):
        return
    cur.execute("UPDATE cc_sessions SET chat_history = %s WHERE id = %s", (Json(history_messages(history)), session["id"]))


# ─── Entry points ───


def respond_text(spec: Any, messages: list[dict]):
    """api.respond_text: one reply to `messages` ([{role, content}]). A dry-run
    spec (Trust's evals) runs on a synthetic session and files nothing;
    otherwise a cc_sessions row (transport none) holds the tool calls. The
    caller keeps its own transcript; this logs no turns. AI errors
    (AIDisabled, AIError) propagate: Trust's runner reports them as
    "runner unavailable" rather than grading a hand-off sentence."""
    from clefs.modules.concierge import api, sessions

    if spec.dry_run:
        session = tools.synthetic_session(spec)
    else:
        with db.db() as cur:
            opened = api.open_session(cur, spec, key_status="api", transport="none")
            session = sessions.get_session(cur, opened["session_id"]) or {}
            session.pop("token_hash", None)
    return run_model(spec, messages, executor_for(session, dry_run=spec.dry_run), raise_ai_errors=True)


EMERGENCY_NOTE = "SERVER NOTE: the hotel's emergency team has already been alerted for this guest. "


class _TurnMood:
    """This turn's mood score (mood.py), stored exactly once. On the HTTP
    route it is scored on a worker thread beside the reply model; inside a
    caller's transaction (WhatsApp's worker) it is scored in order, there."""

    def __init__(self, session: dict, message: str, language: str | None, *, earlier: list[str], cur: Any,
                 emergency: bool) -> None:
        from clefs.modules.concierge import mood

        self.session, self.message, self.language, self.cur, self.emergency = session, message, language, cur, emergency
        self.done = self.waited = False
        recent = earlier[-mood.RECENT_MAX:]
        self.verdict: dict | None = None
        self.job: Future | None = None
        if cur is None:
            self.job = mood.start(message, language=language, hotel_slug=session["hotel_slug"], recent=recent)
        else:
            self.verdict = mood.score(message, language=language, hotel_slug=session["hotel_slug"], recent=recent)

    def before_tool(self) -> None:
        """Ahead of a tool call: the turn's first job waits a moment for this
        turn's score, so it carries it; later calls do not wait again."""
        wait, self.waited = (0.0 if self.waited else MOOD_TOOL_WAIT_S), True
        self.settle(wait)

    def settle(self, wait: float, *, emergency: bool = False, later: bool = False) -> None:
        """Store the score once it is in, waiting at most `wait` seconds. With
        later=True a score still running is stored when it lands instead."""
        from clefs.modules.concierge import mood

        if self.done:
            return
        self.emergency = self.emergency or emergency
        verdict = self.verdict
        if self.job is not None:
            verdict = mood.collect(self.job, timeout=wait)
            if verdict is None and not self.job.done():
                if later:
                    self.done = True
                    self.job.add_done_callback(self._store_when_in)
                return
        self.done = True
        if verdict:
            with _tx(self.cur) as c:
                mood.apply(c, self.session, verdict, text=self.message, language=self.language, emergency=self.emergency)

    def _store_when_in(self, job: Future) -> None:
        """A score that landed after the reply went out, in its own transaction."""
        from clefs.modules.concierge import mood

        verdict = mood.collect(job, timeout=0)
        if not verdict:
            return
        try:
            with db.db() as c:
                mood.apply(c, self.session, verdict, text=self.message, language=self.language, emergency=self.emergency)
        except Exception:  # noqa: BLE001 - a lost score never reaches the guest
            log.exception("late mood score not stored for %s", self.session.get("hotel_slug"))


def _no_tools(name: str, args: dict, call_id: str) -> dict:
    return tools.result("unknown_tool", "No tools in this reply. Carry on without them.")


def _rewrite_for_emergency(spec: Any, history: list[dict], turn: Any, late: dict) -> Any:
    """The reply was written before the safety model caught an emergency in
    the guest's words: write it again around the alert. No tools this time:
    the turn's jobs are filed and the emergency team is already paged."""
    from clefs.modules.concierge.api import TextTurn

    again = run_model(spec.model_copy(update={"tools": []}), history, _no_tools,
                      server_note=EMERGENCY_NOTE + (late.get("followup_instructions") or ""))
    if again.handoff and again.text in (ERROR_TEXT, HANDOFF_TEXT):
        return turn  # failed again: the first reply stands, and the emergency card says the rest
    return TextTurn(text=again.text, tool_calls=turn.tool_calls, handoff=turn.handoff)


def chat_turn(session: dict, message: str, *, language: str | None = None, cur: Any = None) -> dict:
    """One guest message on a stored session (HTTP chat, WhatsApp). Pass
    `cur` to run every step in the caller's transaction (worker tasks), in
    order.

    Without `cur` (the HTTP route, where the guest waits for the whole turn)
    the side checks run beside the reply model: the lexicons still run first
    and an emergency they find reaches the model as a SERVER NOTE, while the
    safety model's second opinion and the mood score run on worker threads.
    An emergency only the safety model caught is escalated when it lands and
    the reply is written again around it.
    -> {text, handoff, tool_calls[{name, status, ui}], ui, safety, prompt_version}"""
    from clefs.core.errors import bad_request
    from clefs.modules.concierge import turns

    message = (message or "").strip()[:MESSAGE_MAX]
    if not message:
        raise bad_request("message is required", "message_required")
    lang = (language or session.get("language") or "")[:8] or None
    beside = cur is None
    with _tx(cur) as c:
        checked = turns.record_turn(c, session, role="user", text=message, language=lang, safety_llm=not beside)
        history = load_history(c, session)
        spec = spec_for_session(c, session, lang)
    earlier = [m["content"] for m in history if m.get("role") == "user"]
    history.append({"role": "user", "content": message})
    emergency = (checked.get("ui") or {}).get("kind") == "emergency"
    safety_job = turns.start_safety_check(session, message, lang) if beside and not emergency else None
    feeling = _TurnMood(session, message, lang, earlier=earlier, cur=cur, emergency=emergency)
    note = EMERGENCY_NOTE + (checked.get("followup_instructions") or "") if emergency else None
    turn = run_model(spec, history, executor_for(session, cur=cur, before=feeling.before_tool), server_note=note)
    ui = checked.get("ui") if emergency else last_ui(turn)
    safety = checked.get("safety")
    late_verdict = turns.collect_safety_check(safety_job, wait=SAFETY_WAIT_S)
    if late_verdict:
        with _tx(cur) as c:
            late = turns.finish_safety_check(c, session, message, late_verdict)
        if late:
            safety = late["safety"]
            if late.get("ui"):
                ui, emergency = late["ui"], True
                if late.get("escalated"):
                    turn = _rewrite_for_emergency(spec, history, turn, late)
    feeling.settle(MOOD_REPLY_WAIT_S, emergency=emergency, later=beside)
    history.append({"role": "assistant", "content": turn.text})
    with _tx(cur) as c:
        turns.record_turn(c, session, role="assistant", text=turn.text, language=lang)
        save_history(c, session, history)
    return {
        "text": turn.text,
        "handoff": bool(turn.handoff),
        "tool_calls": call_summary(turn),
        "ui": ui,
        "safety": safety,
        "prompt_version": spec.prompt_version,
    }
