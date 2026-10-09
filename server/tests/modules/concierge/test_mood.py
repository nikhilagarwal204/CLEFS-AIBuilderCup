"""Guest mood and frustration per turn (05 spec decision 2): scored beside
the reply, stored on the session, passed to every job, and an upset guest
opens one recovery case for the duty manager per session."""

from __future__ import annotations

import pytest

from tests.modules.concierge.test_tools import call, fake_service, mint  # noqa: F401 (fixture)
from tests.modules.concierge.test_turns import chat, fn_call, said, turn

ANGRY = {"mood": "angry", "frustration": 0.85, "urgency": "high", "reason": "Towels asked for an hour ago never came"}
CALM = {"mood": "calm", "frustration": 0.05, "urgency": "normal", "reason": ""}


def by_task(answers: dict[str, list]) -> list:
    """fake_ai queue entries that answer per task, whichever thread asks
    first (the mood score runs beside the reply model)."""
    queues = {task: list(items) for task, items in answers.items()}

    def answer(req):
        return queues[req.task].pop(0)

    return [answer] * sum(len(v) for v in queues.values())


def _session_mood(cur, sid):
    cur.execute("SELECT last_mood, last_urgency, max_frustration, frustration_flagged_at FROM cc_sessions WHERE id = %s",
                (sid,))
    return dict(cur.fetchone())


# ─── The scorer ───


def test_score_uses_the_fast_model_with_a_short_budget(mood_scoring, fake_ai):
    from clefs.settings import get_settings

    fake_ai.push({**ANGRY, "frustration": 1.7})
    out = mood_scoring.score("Where are my towels?! I asked an hour ago.", language="en", hotel_slug="test-dubai",
                             recent=["Can I get two towels?"])
    assert out == {**ANGRY, "frustration": 1.0}, "clamped to 0..1"
    [req] = fake_ai.calls
    assert req.task == "concierge.mood" and req.model == get_settings().AI_MODEL_FAST and req.timeout_s == 3.0
    assert "<message>" in req.input and "<earlier>Can I get two towels?</earlier>" in req.input


def test_score_never_raises(mood_scoring, fake_ai):
    from clefs import ai

    fake_ai.push(ai.AIError("Gemini call failed: 503"), {"mood": "furious", "frustration": 1, "urgency": "high"})
    assert mood_scoring.score("This is ridiculous", language="en", hotel_slug=None) is None
    assert mood_scoring.score("This is ridiculous", language="en", hotel_slug=None) is None, "not a known mood"
    assert mood_scoring.score("!!! ...", language="en", hotel_slug=None) is None, "no words, no call"
    assert len(fake_ai.calls) == 2


def test_no_ai_means_no_thread_and_no_score(mood_scoring):
    assert mood_scoring.start("Hello", language="en", hotel_slug=None) is None
    assert mood_scoring.score("Hello", language="en", hotel_slug=None) is None


# ─── Voice and typed-over-Live turns (POST .../turns) ───


def test_turn_stores_the_mood_on_the_session(client, cur, hotel_uae, fake_service, mood_scoring, fake_ai):
    s = mint(client, hotel_uae.slug)
    fake_ai.push(CALM)
    out = turn(client, s, "user", "What time is breakfast?").json()
    assert out == {"ok": True, "stored": True, "safety": {"level": "none", "kind": None}, "escalated": False}
    assert _session_mood(cur, s["session_id"]) == {"last_mood": "calm", "last_urgency": "normal",
                                                    "max_frustration": pytest.approx(0.05), "frustration_flagged_at": None}
    assert fake_service.recoveries == []
    turn(client, s, "assistant", "Breakfast is from 7.")
    assert [c.task for c in fake_ai.calls] == ["concierge.mood"], "only the guest's words are scored"


def test_an_upset_guest_flags_the_duty_manager_once_per_session(client, cur, hotel_uae, fake_service, mood_scoring,
                                                               fake_ai):
    s = mint(client, hotel_uae.slug)
    call(client, s, "notify_staff", {"request_type": "towels", "description_en": "Two towels", "quantity": 2})
    fake_ai.push(ANGRY, {**ANGRY, "frustration": 0.95}, CALM)
    turn(client, s, "user", "My towels never came, this is ridiculous")
    turn(client, s, "user", "Still nothing. I want a manager.")
    turn(client, s, "user", "Thanks, they are here now")
    [case] = fake_service.recoveries
    assert case["kind"] == "frustration" and case["room"] == "101" and case["request_id"] == 1001
    assert case["session_id"] == s["session_id"] and case["guest_language"] == "en"
    assert "angry" in case["summary"] and "never came" in case["summary"]
    row = _session_mood(cur, s["session_id"])
    assert row["last_mood"] == "calm" and row["max_frustration"] == pytest.approx(0.95), "the peak is kept"
    assert row["frustration_flagged_at"] is not None
    cur.execute("SELECT ref, auto FROM cc_session_events WHERE session_id = %s AND kind = 'recovery'", (s["session_id"],))
    [ev] = cur.fetchall()
    assert ev["auto"] is True and ev["ref"]["kind"] == "frustration" and ev["ref"]["case_id"] == 71


def test_an_angry_mood_flags_even_at_lower_frustration(client, hotel_uae, fake_service, mood_scoring, fake_ai):
    s = mint(client, hotel_uae.slug)
    fake_ai.push({"mood": "distressed", "frustration": 0.4, "urgency": "high", "reason": "Lost her passport"})
    turn(client, s, "user", "I can't find my passport anywhere, please help me")
    assert [c["kind"] for c in fake_service.recoveries] == ["frustration"]


def test_an_emergency_is_not_frustration(client, hotel_uae, fake_service, mood_scoring, fake_ai):
    s = mint(client, hotel_uae.slug)
    fake_ai.push({"mood": "distressed", "frustration": 0.9, "urgency": "high", "reason": "Fire"})
    out = turn(client, s, "user", "There is a fire in my room!").json()
    assert out["escalated"] is True and fake_service.recoveries == [], "the duty manager is paged for the fire already"


def test_a_refused_flag_is_tried_again_on_the_next_upset_turn(client, cur, hotel_uae, fake_service, mood_scoring,
                                                             fake_ai, monkeypatch):
    from clefs.core.errors import ClefsError
    from clefs.modules.service import api as service_api

    s = mint(client, hotel_uae.slug)
    real = fake_service.flag_recovery

    def down(*a, **kw):
        raise ClefsError(503, "Service is busy", "busy")

    monkeypatch.setattr(service_api, "flag_recovery", down)
    fake_ai.push(ANGRY, ANGRY)
    assert turn(client, s, "user", "Nobody came!").status_code == 200, "a refusal never costs the turn"
    assert _session_mood(cur, s["session_id"])["frustration_flagged_at"] is None
    monkeypatch.setattr(service_api, "flag_recovery", real)
    turn(client, s, "user", "Still nobody came!")
    assert len(fake_service.recoveries) == 1


def test_mood_failure_never_touches_the_turn(client, cur, hotel_uae, fake_service, mood_scoring, fake_ai):
    from clefs import ai

    s = mint(client, hotel_uae.slug)
    fake_ai.push(ai.AIError("timeout"))
    assert turn(client, s, "user", "Hello").json()["ok"] is True
    assert _session_mood(cur, s["session_id"])["last_mood"] is None


# ─── HTTP chat: scored beside the reply, carried by this turn's jobs ───


def test_chat_scores_beside_the_reply_and_the_job_carries_it(client, cur, hotel_uae, fake_service, mood_scoring,
                                                            fake_ai):
    s = mint(client, hotel_uae.slug)
    fake_ai.push(*by_task({
        "concierge.mood": [ANGRY],
        "concierge.text": [fn_call("notify_staff", {"request_type": "towels", "description_en": "Towels", "quantity": 2}),
                           said("I am so sorry. Housekeeping has been notified. Maria is on the way, about 5 minutes.")],
    }))
    body = chat(client, s, "I asked for towels an hour ago and nothing came!").json()
    assert body["text"].startswith("I am so sorry") and body["tool_calls"][0]["ui"]["card"]["staff_first_name"] == "Maria"
    [job] = fake_service.requests
    assert job["mood"] == "angry" and job["frustration"] == pytest.approx(0.85), "this turn's mood rides with its job"
    assert [c["kind"] for c in fake_service.recoveries] == ["frustration"]
    assert sorted(c.task for c in fake_ai.calls) == ["concierge.mood", "concierge.text", "concierge.text"]
    mood_input = next(c for c in fake_ai.calls if c.task == "concierge.mood").input
    assert "nothing came" in mood_input


def test_chat_reply_stands_when_the_mood_call_fails(client, cur, hotel_uae, fake_service, mood_scoring, fake_ai):
    from clefs import ai

    s = mint(client, hotel_uae.slug)
    fake_ai.push(*by_task({"concierge.mood": [ai.AIError("Gemini call failed: 429")],
                           "concierge.text": [said("Breakfast is from 7 to 10:30.")]}))
    body = chat(client, s, "When is breakfast?").json()
    assert body["text"] == "Breakfast is from 7 to 10:30." and body["handoff"] is False
    assert _session_mood(cur, s["session_id"])["last_mood"] is None


def test_a_late_score_is_stored_when_it_lands(cur, hotel_uae, mood_scoring, fake_service, client, monkeypatch, fake_ai):
    """A score still running when the reply is ready never holds it up: it
    is stored in its own transaction once it lands."""
    import threading

    from clefs.modules.concierge import text_chat

    gate, landed = threading.Event(), threading.Event()

    def slow_score(*a, **kw):
        gate.wait(5)
        return ANGRY

    monkeypatch.setattr(mood_scoring, "score", slow_score)
    real_store = text_chat._TurnMood._store_when_in

    def store_then_signal(self, job):
        try:
            real_store(self, job)
        finally:
            landed.set()

    monkeypatch.setattr(text_chat._TurnMood, "_store_when_in", store_then_signal)
    monkeypatch.setattr(text_chat, "MOOD_REPLY_WAIT_S", 0.01)
    s = mint(client, hotel_uae.slug)
    fake_ai.push(said("Hello! How can I help?"))
    assert chat(client, s, "Hi").json()["text"] == "Hello! How can I help?"
    assert _session_mood(cur, s["session_id"])["last_mood"] is None, "the reply did not wait for the score"
    gate.set()
    assert landed.wait(5)
    assert _session_mood(cur, s["session_id"])["last_mood"] == "angry"
