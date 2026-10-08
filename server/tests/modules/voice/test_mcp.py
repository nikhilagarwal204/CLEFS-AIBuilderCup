"""The phone's MCP server (03 spec 3.3): JWT auth and room binding, the
spoken-room flow, sip-only REFER and idempotent side-effect retries."""

from __future__ import annotations

import pytest

from tests.modules.voice.conftest import FRONT_DESK, incoming


@pytest.fixture
def start_call(post_event, voice_env, recorder, line):
    """start_call(call_id, from_user) -> the per-call JWT from the accept."""

    def _start(call_id: str, from_user: str = "1501") -> str:
        r = post_event(incoming(call_id, from_user=from_user, line_secret=line[1]))
        assert r.json()["action"] == "accepted", r.text
        [(_, _, body)] = [c for c in recorder.ops("accept") if c[1] == call_id]
        return body["mcp"]["authorization"]

    return _start


def rpc(client, token, method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
    return client.post("/webhooks/voice/mcp", json=body, headers={"Authorization": f"Bearer {token}"})


def tool(client, token, name, args):
    r = rpc(client, token, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.text
    return r.json()["result"]["structuredContent"]


@pytest.fixture
def svc(fake_service):
    return fake_service


def _requests(fake):
    return fake.requests


# ─── Auth and protocol ───


def test_no_or_bad_token_is_401(client, start_call):
    start_call("rtc_auth")
    assert client.post("/webhooks/voice/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    r = rpc(client, "not-a-jwt", "ping")
    assert r.status_code == 401 and r.json()["code"] == "invalid_call_token"


def test_initialize_and_tools_list(client, start_call):
    token = start_call("rtc_init")
    r = rpc(client, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "bridge", "version": "1"}})
    assert r.json()["result"]["protocolVersion"] == "2025-06-18"
    names = {t["name"] for t in rpc(client, token, "tools/list").json()["result"]["tools"]}
    assert {"create_ticket", "get_hotel_info", "get_request_status", "place_order", "transfer_to_front_desk"} <= names
    assert client.post("/webhooks/voice/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                       headers={"Authorization": f"Bearer {token}"}).status_code == 202
    assert client.get("/webhooks/voice/mcp").status_code == 405


def test_expired_token_is_401(client, start_call, clock):
    from datetime import timedelta

    from clefs.core import clock as clock_mod

    token = start_call("rtc_exp")
    clock.freeze(clock_mod.now() + timedelta(hours=3))
    assert rpc(client, token, "ping").status_code == 401


# ─── Room binding ───


def test_jwt_room_cannot_be_overridden_by_arguments(client, start_call, cur, svc):
    token = start_call("rtc_bound", from_user="1501")
    out = tool(client, token, "create_ticket", {"request_type": "towels", "description_en": "2 bath towels", "room": "101"})
    assert out["status"] in ("created", "merged"), out
    rooms = [r["room_number"] for r in _requests(svc)]
    assert rooms == ["501"]


def test_forged_token_for_another_room_is_401(client, start_call):
    import jwt

    token = start_call("rtc_forge")
    claims = jwt.decode(token, options={"verify_signature": False})
    forged = jwt.encode({**claims, "room": "101"}, "guessed-key-guessed-key-guessed-key!", algorithm="HS256")
    assert rpc(client, forged, "ping").status_code == 401


def test_token_of_one_call_cannot_act_on_another(client, start_call, cur, svc):
    from clefs.modules.voice import calls

    token = start_call("rtc_one")
    start_call("rtc_two", from_user="1502")
    claims = calls.verify_call_token(token)
    cur.execute("SELECT id FROM vc_calls WHERE provider_call_id = 'rtc_two'")
    other = cur.fetchone()["id"]
    cur.execute("SELECT * FROM vc_calls WHERE id = %s", (claims["call"],))
    mixed = calls.mint_call_token({**dict(cur.fetchone()), "id": other}, max_call_s=600)
    r = rpc(client, mixed, "tools/call", {"name": "create_ticket", "arguments": {"request_type": "water", "description_en": "water"}})
    assert "call_mismatch" in r.json()["error"]["message"]
    assert _requests(svc) == []


def test_spoken_room_flow_is_sticky(client, start_call, cur, svc):
    token = start_call("rtc_spoken", from_user="777")
    assert tool(client, token, "create_ticket", {"request_type": "water", "description_en": "water"})["status"] == "room_required"
    assert tool(client, token, "create_ticket", {"request_type": "water", "description_en": "water", "room": "999"})["status"] == "room_required"
    out = tool(client, token, "create_ticket", {"request_type": "water", "description_en": "2 bottles of water", "room": "503"})
    assert out["status"] == "created" and out.get("verified_room") is False
    cur.execute("SELECT room, room_confirmed_by FROM vc_calls WHERE provider_call_id = 'rtc_spoken'")
    assert dict(cur.fetchone()) == {"room": "503", "room_confirmed_by": "spoken"}
    tool(client, token, "create_ticket", {"request_type": "ac", "description_en": "AC too warm", "room": "101"})
    rows = _requests(svc)
    assert [r["room_number"] for r in rows] == ["503", "503"]
    assert "confirm at the door" in rows[0]["description"]


# ─── Idempotency ───


def test_retried_create_ticket_files_once(client, start_call, cur, svc):
    token = start_call("rtc_retry")
    args = {"request_type": "towels", "description_en": "2 towels"}
    first = tool(client, token, "create_ticket", args)
    second = tool(client, token, "create_ticket", args)
    assert second.get("deduped") is True and second["request_ref"] == first["request_ref"]
    assert len(_requests(svc)) == 1
    cur.execute("SELECT requests_created FROM vc_calls WHERE provider_call_id = 'rtc_retry'")
    assert cur.fetchone()["requests_created"] == 1


# ─── REFER ───


def test_transfer_refers_to_sip_front_desk(client, start_call, recorder, cur):
    token = start_call("rtc_refer")
    out = tool(client, token, "transfer_to_front_desk", {"reason": "wants a person"})
    assert out["status"] == "transferred"
    [(_, call_id, body)] = recorder.ops("refer")
    assert call_id == "rtc_refer" and body == {"target_uri": FRONT_DESK}
    cur.execute("SELECT transferred FROM vc_calls WHERE provider_call_id = 'rtc_refer'")
    assert cur.fetchone()["transferred"] is True


def test_tel_front_desk_is_never_referred(client, start_call, recorder, cur, line):
    """tel: is refused by the console validator and by the table CHECK; a
    line with no usable target files a callback instead of a REFER."""
    import psycopg2

    from clefs.core.errors import ClefsError
    from clefs.modules.voice import lines

    with pytest.raises(ClefsError) as exc:
        lines.update_line(cur, "test-dubai", line[0]["id"], {"front_desk_uri": "tel:+97140000000"})
    assert exc.value.code == "invalid_front_desk_uri"
    cur.execute("SAVEPOINT tel")
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute("UPDATE vc_lines SET front_desk_uri = 'tel:+97140000000' WHERE id = %s", (line[0]["id"],))
    cur.execute("ROLLBACK TO SAVEPOINT tel")
    assert not lines.valid_refer_target("tel:100") and lines.valid_refer_target("sips:fd@pbx.ae")
    cur.execute("UPDATE vc_lines SET front_desk_uri = NULL WHERE id = %s", (line[0]["id"],))
    token = start_call("rtc_notarget")
    out = tool(client, token, "transfer_to_front_desk", {})
    assert out["status"] == "callback_requested" and recorder.ops("refer") == []


def test_failed_refer_files_a_callback(client, start_call, recorder, cur, svc):
    token = start_call("rtc_referfail")
    recorder.fail_next("refer")
    out = tool(client, token, "transfer_to_front_desk", {"reason": "complaint"})
    assert out["status"] == "callback_requested"
    rows = _requests(svc)
    assert len(rows) == 1 and rows[0]["room_number"] == "501" and "call room 501 back" in rows[0]["description"]
