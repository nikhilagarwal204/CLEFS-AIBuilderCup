"""The sideband worker (03 spec 3.4): leases, transcripts, the emergency
injection, sideband function tools, the socket lifecycle and the reaper."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from tests.modules.voice.conftest import incoming


@pytest.fixture
def accepted(post_event, voice_env, recorder, line, cur):
    """accepted(call_id, from_user='1501', transport='mcp') -> vc_calls row."""

    def _make(call_id: str, from_user: str = "1501", transport: str = "mcp") -> dict:
        cur.execute("UPDATE vc_lines SET tool_transport = %s WHERE id = %s", (transport, line[0]["id"]))
        assert post_event(incoming(call_id, from_user=from_user, line_secret=line[1])).json()["action"] == "accepted"
        cur.execute("SELECT * FROM vc_calls WHERE provider_call_id = %s", (call_id,))
        return dict(cur.fetchone())

    return _make


def _logs(cur, call_id):
    cur.execute("SELECT role, message, source, safety_level, room_number FROM conversation_logs WHERE call_id = %s ORDER BY id",
                (call_id,))
    return [dict(r) for r in cur.fetchall()]


# ─── Leases ───


def test_claim_leases_once_and_reaper_frees_expired(cur, accepted, clock):
    from clefs.core import clock as clock_mod
    from clefs.modules.voice import worker

    call = accepted("rtc_lease")
    [claimed] = worker.claim_calls(cur, "w1", 5)
    assert claimed["id"] == call["id"] and claimed["worker_id"] == "w1"
    assert worker.claim_calls(cur, "w2", 5) == []
    assert worker.renew_lease(cur, call["id"], "w1") and not worker.renew_lease(cur, call["id"], "w2")
    clock.freeze(clock_mod.now() + timedelta(minutes=5))
    assert worker.reap(cur)["leases_cleared"] == 1
    [again] = worker.claim_calls(cur, "w2", 5)
    assert again["worker_id"] == "w2"


def test_reaper_marks_stale_and_stuck_calls(cur, accepted, clock, line):
    from clefs.core import clock as clock_mod
    from clefs.modules.voice import worker

    accepted("rtc_stale")
    cur.execute("UPDATE vc_calls SET status = 'accepting' WHERE provider_call_id = %s", ("rtc_stale",))
    clock.freeze(clock_mod.now() + timedelta(minutes=10))
    assert worker.reap(cur)["stuck_failed"] == 1
    accepted("rtc_stale2", from_user="1502")
    clock.freeze(clock_mod.now() + timedelta(seconds=int(line[0]["max_call_s"]) + 600))
    assert worker.reap(cur)["stale"] == 1
    cur.execute("SELECT status, ended_at FROM vc_calls WHERE provider_call_id = 'rtc_stale2'")
    row = cur.fetchone()
    assert row["status"] == "stale" and row["ended_at"] is None


def test_receipts_purge_keeps_30_days(cur, post_event, voice_env, recorder, line, clock):
    from clefs.core import clock as clock_mod
    from clefs.modules.voice import worker

    post_event(incoming("rtc_old", line_secret=line[1]), webhook_id="msg_old")
    cur.execute("UPDATE vc_webhook_receipts SET received_at = %s WHERE webhook_id = 'msg_old'",
                (clock_mod.now() - timedelta(days=31),))
    assert worker.purge_receipts(cur)["deleted"] == 1


# ─── Events ───


def test_transcripts_go_to_conversation_logs(cur, accepted):
    from clefs.modules.voice import worker

    call = accepted("rtc_tx")
    s = worker.SidebandCall(call, use_llm_safety=False)
    assert s.handle(cur, {"type": "session.started"}) == []
    s.handle(cur, {"type": "input_transcription.done", "transcript": "Two towels please"})
    s.handle(cur, {"type": "output_transcription.done", "transcript": "Two towels for room 501, correct?"})
    s.handle(cur, {"type": "dtmf", "digit": "5"})
    logs = _logs(cur, "rtc_tx")
    assert [(r["role"], r["source"]) for r in logs] == [("user", "phone"), ("assistant", "phone"), ("system", "phone")]
    assert logs[0]["room_number"] == "501" and logs[0]["safety_level"] == "none"
    cur.execute("SELECT status FROM vc_calls WHERE id = %s", (call["id"],))
    assert cur.fetchone()["status"] == "active"


def test_emergency_is_escalated_once_and_injected(cur, accepted, fake_service):
    from clefs.modules.voice import worker

    call = accepted("rtc_fire")
    s = worker.SidebandCall(call, use_llm_safety=False)
    out = s.handle(cur, {"type": "input_transcription.done", "transcript": "There is a fire!"})
    [msg] = out
    assert msg["type"] == "inject"
    text = msg["instructions"]
    assert "dial 9 for an outside line" in text and "997" in text and "transfer" in text
    assert len(fake_service.requests) == 1 and fake_service.requests[0]["priority_hint"] == "L1"
    again = s.handle(cur, {"type": "input_transcription.done", "transcript": "fire fire"})
    assert again == [] and len(fake_service.requests) == 1
    assert _logs(cur, "rtc_fire")[0]["safety_level"] == "emergency"
    cur.execute("SELECT emergencies, requests_created FROM vc_calls WHERE id = %s", (call["id"],))
    assert dict(cur.fetchone()) == {"emergencies": 1, "requests_created": 1}


def test_sideband_function_call_runs_once(cur, accepted, fake_service):
    from clefs.modules.voice import worker

    call = accepted("rtc_fn", transport="sideband")
    s = worker.SidebandCall(call, use_llm_safety=False)
    ev = {"type": "tool_call", "call_id": "fc_1", "name": "notify_staff",
          "arguments": json.dumps({"request_type": "water", "description_en": "2 bottles", "room": "101"})}
    out = s.handle(cur, ev)
    assert [m["type"] for m in out] == ["tool_response"]
    result = out[0]["output"]
    assert out[0]["call_id"] == "fc_1" and result["status"] == "created"
    assert fake_service.requests[0]["room_number"] == "501"
    assert s.handle(cur, ev) == []
    replay = worker.SidebandCall(call, use_llm_safety=False).handle(cur, ev)
    assert replay[0]["output"]["deduped"] is True
    assert len(fake_service.requests) == 1


def test_mcp_line_runs_function_calls_too(cur, accepted, fake_service):
    """Gemini Live has no MCP client, so the worker runs an MCP line's tools as well."""
    from clefs.modules.voice import worker

    s = worker.SidebandCall(accepted("rtc_mcpfn"), use_llm_safety=False)
    ev = {"type": "tool_call", "call_id": "fc_x", "name": "notify_staff", "arguments": "{}"}
    [out] = s.handle(cur, ev)
    assert out["type"] == "tool_response" and out["call_id"] == "fc_x" and out["output"]["status"] == "created"


def test_bad_arguments_are_answered_not_raised(cur, accepted):
    from clefs.modules.voice import worker

    s = worker.SidebandCall(accepted("rtc_badargs", transport="sideband"), use_llm_safety=False)
    out = s.handle(cur, {"type": "tool_call", "call_id": "fc_b", "name": "notify_staff",
                         "arguments": "{not json"})
    assert out[0]["output"]["status"] == "invalid_arguments"


# ─── Socket lifecycle ───


class FakeSocket:
    def __init__(self, events):
        self.events = [json.dumps(e) for e in events]
        self.sent: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.events:
            raise StopAsyncIteration
        return self.events.pop(0)

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def close(self):
        self.events = []


class FakeLive:
    """A Gemini Live session: replays server messages, records what the worker sends."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.client_content: list = []
        self.tool_responses: list = []
        self.drained = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def receive(self):
        while self.messages:
            yield self.messages.pop(0)
        self.drained.set()
        await asyncio.sleep(3600)  # the session stays open until the call ends

    async def send_client_content(self, **kwargs):
        self.client_content.append(kwargs)

    async def send_tool_response(self, *, function_responses):
        self.tool_responses.extend(function_responses)

    async def send_realtime_input(self, **_):
        pass


class BridgeSocket(FakeSocket):
    """The bridge's media socket: hangs up once the Gemini side is drained."""

    def __init__(self, live):
        super().__init__([])
        self.live = live
        self.stopped = False

    async def __anext__(self):
        if self.stopped:
            raise StopAsyncIteration
        await self.live.drained.wait()
        self.stopped = True
        return json.dumps({"event": "stop"})


def test_run_call_records_end_on_close(cur, accepted, fake_service):
    from types import SimpleNamespace as NS

    from clefs.modules.voice import worker

    call = accepted("rtc_ws", transport="sideband")
    [leased] = worker.claim_calls(cur, "wtest", 1)
    live = FakeLive([
        NS(server_content=NS(input_transcription=NS(text="someone is not breathing"), turn_complete=True), tool_call=None),
        NS(server_content=None, tool_call=NS(function_calls=[NS(id="fc_ws", name="transfer_to_front_desk", args={})])),
    ])
    sock = BridgeSocket(live)
    reason = asyncio.run(worker.run_call(leased, connect=lambda _cid: sock, live=lambda _call: live, worker_id="wtest",
                                         use_llm_safety=False))
    assert reason == "socket_closed"
    assert live.client_content and live.client_content[0]["turns"].parts[0].text == worker.GREETING_CUE
    assert any(r.id == "fc_ws" for r in live.tool_responses)
    cur.execute("SELECT status, ended_at, duration_s, end_reason, transferred, worker_lease_until FROM vc_calls WHERE id = %s",
                (call["id"],))
    row = cur.fetchone()
    assert row["status"] == "ended" and row["ended_at"] and row["end_reason"] == "socket_closed"
    assert row["transferred"] is True and row["worker_lease_until"] is None


def test_connect_failure_hands_the_call_back_then_gives_up(cur, accepted):
    from clefs.modules.voice import worker

    call = accepted("rtc_noconn")

    def boom(_cid):
        raise OSError("refused")

    for _ in range(worker.CONNECT_ATTEMPTS - 1):
        [leased] = worker.claim_calls(cur, "wt", 1)
        assert asyncio.run(worker.run_call(leased, connect=boom, worker_id="wt")) == "retry"
    [leased] = worker.claim_calls(cur, "wt", 1)
    assert asyncio.run(worker.run_call(leased, connect=boom, worker_id="wt")) == "sideband_unreachable"
    cur.execute("SELECT status FROM vc_calls WHERE id = %s", (call["id"],))
    assert cur.fetchone()["status"] == "ended"


def test_ws_url_follows_bridge_url(monkeypatch):
    from clefs.modules.voice import worker

    monkeypatch.setenv("VOICE_BRIDGE_URL", "https://bridge.example/v1")
    assert worker.realtime_ws_url("rtc_1") == "wss://bridge.example/v1/calls/rtc_1/media"
    monkeypatch.setenv("VOICE_BRIDGE_URL", "http://127.0.0.1:9911/v1")
    assert worker.realtime_ws_url("rtc/2") == "ws://127.0.0.1:9911/v1/calls/rtc%2F2/media"


def test_service_idles_when_voice_is_off(monkeypatch):
    """No VOICE_ENABLED: the service sleeps instead of polling the DB."""
    from clefs.modules.voice import worker
    from clefs.settings import reload_settings

    monkeypatch.setenv("VOICE_ENABLED", "false")
    reload_settings()
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)
        raise asyncio.CancelledError

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(worker.voice_sideband())
    finally:
        monkeypatch.undo()
        reload_settings()
    assert slept == [worker.IDLE_SLEEP_S]


def test_module_manifest_registers_service_and_jobs():
    from clefs import jobs
    from clefs.modules.voice import module, worker

    assert worker.voice_sideband in module.MODULE.worker_services
    names = {getattr(j, "name", j) for j in jobs.registered_jobs()}
    assert {"voice.lease_reaper", "voice.receipts_purge"} <= names
