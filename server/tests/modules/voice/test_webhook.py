"""POST /webhooks/voice/incoming (03 spec 3.2): signatures, 486s, extension
mapping, the spoken-room path and idempotent retries."""

from __future__ import annotations

import json
import time

from tests.modules.voice.conftest import incoming, sign


def _call(cur, call_id):
    cur.execute("SELECT * FROM vc_calls WHERE provider_call_id = %s", (call_id,))
    return cur.fetchone()


# ─── Signatures ───


def test_valid_signature_accepts_and_records(post_event, voice_env, recorder, line, cur):
    ev = incoming("rtc_ok", line_secret=line[1])
    r = post_event(ev)
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "accepted"
    [(op, call_id, body)] = recorder.ops("accept")
    assert call_id == "rtc_ok" and body["type"] == "gemini_live" and body["model"]
    mcp = body["mcp"]
    assert mcp["server_label"] == "clefs" and mcp["server_url"] and mcp["authorization"]
    assert "transfer_to_front_desk" in mcp["allowed_tools"]
    row = _call(cur, "rtc_ok")
    assert row["status"] == "accepted" and row["room"] == "501" and row["room_confirmed_by"] == "extension"
    assert row["sip_headers_redacted"]["x-clefs-line"] == ["[present]"]


def test_invalid_signature_is_400(post_event, voice_env, recorder, line):
    ev = incoming("rtc_bad", line_secret=line[1])
    raw = json.dumps(ev).encode()
    hdrs = sign(raw, webhook_id="msg_bad")
    hdrs["x-clefs-signature"] = "sha256=" + "0" * 64
    r = post_event(ev, headers=hdrs, body=raw)
    assert r.status_code == 400 and r.json()["code"] == "invalid_signature"
    assert recorder.calls == []


def test_tampered_body_and_missing_headers_are_400(post_event, voice_env, recorder, line):
    ev = incoming("rtc_tamper", line_secret=line[1])
    raw = json.dumps(ev).encode()
    hdrs = sign(raw, webhook_id="msg_t")
    assert post_event(ev, headers=hdrs, body=raw.replace(b"rtc_tamper", b"rtc_tampex")).status_code == 400
    assert post_event(ev, headers={"content-type": "application/json"}, body=raw).status_code == 400
    assert recorder.calls == []


def test_stale_timestamp_is_400(post_event, voice_env, recorder, line):
    """A replay of a captured delivery outside the 5 minute signing window."""
    ev = incoming("rtc_old", line_secret=line[1])
    raw = json.dumps(ev).encode()
    r = post_event(ev, headers=sign(raw, webhook_id="msg_old", ts=int(time.time()) - 3600), body=raw)
    assert r.status_code == 400 and r.json()["code"] == "invalid_signature"


def test_wrong_secret_is_400(post_event, voice_env, recorder, line):
    import base64

    ev = incoming("rtc_other", line_secret=line[1])
    raw = json.dumps(ev).encode()
    other = "whsec_" + base64.b64encode(b"x" * 32).decode()
    assert post_event(ev, headers=sign(raw, webhook_id="msg_o", secret=other), body=raw).status_code == 400


def test_no_secret_configured_is_503(post_event, monkeypatch, recorder, line):
    from clefs.settings import reload_settings

    monkeypatch.delenv("VOICE_WEBHOOK_SECRET", raising=False)
    reload_settings()
    try:
        r = post_event(incoming("rtc_ns"))
        assert r.status_code == 503 and r.json()["code"] == "not_configured"
    finally:
        monkeypatch.undo()
        reload_settings()


# ─── Idempotency ───


def test_replayed_webhook_id_is_answered_once(post_event, voice_env, recorder, line, cur):
    ev = incoming("rtc_dup", line_secret=line[1])
    raw = json.dumps(ev).encode()
    hdrs = sign(raw, webhook_id="msg_same")
    assert post_event(ev, headers=hdrs, body=raw).json()["action"] == "accepted"
    again = post_event(ev, headers=hdrs, body=raw)
    assert again.status_code == 200 and again.json()["action"] == "duplicate"
    assert len(recorder.ops("accept")) == 1
    cur.execute("SELECT count(*) AS n FROM vc_calls WHERE provider_call_id = 'rtc_dup'")
    assert cur.fetchone()["n"] == 1


def test_same_call_new_webhook_id_is_not_accepted_twice(post_event, voice_env, recorder, line):
    ev = incoming("rtc_dup2", line_secret=line[1])
    assert post_event(ev, webhook_id="msg_a").json()["action"] == "accepted"
    assert post_event(ev, webhook_id="msg_b").json()["action"] == "duplicate"
    assert len(recorder.ops("accept")) == 1


# ─── 486 ───


def _rejected(recorder, call_id):
    return [c for c in recorder.ops("reject") if c[1] == call_id]


def test_unidentified_call_gets_486(post_event, voice_env, recorder, line, cur):
    r = post_event(incoming("rtc_anon", from_host="evil.example.com"))
    assert r.json()["action"] == "rejected" and r.json()["reason"] == "unidentified"
    [(_, _, body)] = _rejected(recorder, "rtc_anon")
    assert body == {"status_code": 486}
    assert recorder.ops("accept") == []
    assert _call(cur, "rtc_anon")["reject_code"] == 486


def test_wrong_line_secret_gets_486_even_from_allowed_source(post_event, voice_env, recorder, line):
    """A header that matches no line is refused, not silently ignored."""
    r = post_event(incoming("rtc_wrong", line_secret="vcl_not-the-secret"))
    assert r.json()["reason"] == "bad_line_secret"
    assert _rejected(recorder, "rtc_wrong")


def test_allowed_source_identifies_line_without_header(post_event, voice_env, recorder, line, cur):
    """Served, but the From extension (501 is in the allowlist) is not
    trusted without the line secret: nothing is bound, the prompt asks."""
    r = post_event(incoming("rtc_src"))
    assert r.json()["action"] == "accepted"
    claims, body = _claims(recorder, "rtc_src")
    assert claims["room"] is None and "did not tell us the room" in body["instructions"]
    row = _call(cur, "rtc_src")
    assert row["room"] is None and row["room_confirmed_by"] == "none" and row["extension_matched"]


# ─── Spoofed sources (review: security-voice-line-source-spoofable) ───


def _receipt(cur, call_id):
    cur.execute("SELECT outcome, detail FROM vc_webhook_receipts WHERE call_id = %s ORDER BY received_at DESC LIMIT 1",
                (call_id,))
    return cur.fetchone()


def test_spoofed_from_extension_is_never_a_verified_room(post_event, voice_env, recorder, line, cur):
    """An attacker who can reach the SIP URI writes From sip:1502@<the PBX
    domain> (and a matching Via). Every host matches allowed_sources, but
    without X-Clefs-Line the extension must not become verified room 502:
    not in the JWT, not on the call row, not in the prompt."""
    contact = {"name": "Contact", "value": "<sip:1502@pbx.test-dubai.ae:5061;transport=tls>"}
    assert post_event(incoming("rtc_spoof", from_user="1502", extra=[contact])).json()["action"] == "accepted"
    claims, body = _claims(recorder, "rtc_spoof")
    assert claims["room"] is None and "room 502" not in body["instructions"]
    row = _call(cur, "rtc_spoof")
    assert row["room"] is None and row["room_confirmed_by"] == "none" and row["location_slug"] is None
    detail = _receipt(cur, "rtc_spoof")["detail"]
    assert detail["identified_by"] == "source" and detail["extension_trusted"] is False and detail["room"] is None


def test_spoofed_room_cannot_file_bound_requests(post_event, voice_env, recorder, line, cur):
    """The executor binds a room only from the JWT or an extension-confirmed
    call row: a spoofed call is on the spoken (unverified) path."""
    from clefs.modules.voice import calls, executor

    post_event(incoming("rtc_spoof2", from_user="1501"))
    claims, _ = _claims(recorder, "rtc_spoof2")
    call = calls.get_call(cur, claims["call"])
    args, refusal = executor.resolve_room(cur, call, "place_order", {"items": []}, claims_room=claims["room"])
    assert refusal is not None and refusal["status"] == "room_required"


def test_source_match_requires_the_dialled_project(post_event, voice_env, recorder, line, cur):
    """Hosts match the line but the call was placed to another the telephony bridge
    project: refused, never accepted on the host match alone."""
    r = post_event(incoming("rtc_otherproj", project="proj_someone_else"))
    assert r.json()["action"] == "rejected" and r.json()["reason"] == "unidentified"
    assert recorder.ops("accept") == []


def test_source_match_skips_lines_without_a_project(post_event, voice_env, recorder, line, cur):
    cur.execute("UPDATE vc_lines SET trunk_id = NULL WHERE id = %s", (line[0]["id"],))
    r = post_event(incoming("rtc_noproj"))
    assert r.json()["reason"] == "unidentified"
    # The line secret still identifies it (the project is not needed then).
    assert post_event(incoming("rtc_noproj_hdr", line_secret=line[1])).json()["action"] == "accepted"


def test_spoofed_calls_do_not_rate_limit_the_real_handset(post_event, voice_env, recorder, line, cur):
    """Ten spoofed INVITEs from 1502 fill their own bucket; room 502's real
    phone (with the line secret) still gets through."""
    from clefs.modules.voice import constants as C

    cur.execute("UPDATE vc_lines SET max_concurrent = 50 WHERE id = %s", (line[0]["id"],))
    for i in range(C.EXTENSION_CALLS_PER_HOUR):
        assert post_event(incoming(f"rtc_flood{i}", from_user="1502")).json()["action"] == "accepted"
    assert post_event(incoming("rtc_flood_x", from_user="1502")).json()["reason"] == "extension_rate_limited"
    assert post_event(incoming("rtc_real502", from_user="1502", line_secret=line[1])).json()["action"] == "accepted"
    assert _call(cur, "rtc_real502")["room"] == "502"


def test_disabled_line_and_voice_off_get_486(post_event, voice_env, recorder, line, cur, monkeypatch):
    from clefs.settings import reload_settings

    cur.execute("UPDATE vc_lines SET enabled = FALSE WHERE id = %s", (line[0]["id"],))
    assert post_event(incoming("rtc_dis", line_secret=line[1])).json()["reason"] == "line_disabled"
    cur.execute("UPDATE vc_lines SET enabled = TRUE WHERE id = %s", (line[0]["id"],))
    monkeypatch.setenv("VOICE_ENABLED", "false")
    reload_settings()
    assert post_event(incoming("rtc_off", line_secret=line[1])).json()["reason"] == "voice_disabled"
    assert recorder.ops("accept") == []


def test_over_capacity_gets_486(post_event, voice_env, recorder, line, cur):
    cur.execute("UPDATE vc_lines SET max_concurrent = 1 WHERE id = %s", (line[0]["id"],))
    assert post_event(incoming("rtc_c1", line_secret=line[1])).json()["action"] == "accepted"
    r = post_event(incoming("rtc_c2", line_secret=line[1], from_user="1502"))
    assert r.json()["reason"] == "over_capacity"


def test_after_hours_gets_486(post_event, voice_env, recorder, line, cur, clock):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    cur.execute("""UPDATE vc_lines SET after_hours = '{"enabled": true, "start": "23:00", "end": "06:00"}' WHERE id = %s""",
                (line[0]["id"],))
    clock.freeze(datetime(2026, 9, 29, 2, 30, tzinfo=ZoneInfo("Asia/Dubai")))
    assert post_event(incoming("rtc_night", line_secret=line[1])).json()["reason"] == "after_hours"


def test_accept_failure_rejects_and_marks_failed(post_event, voice_env, recorder, line, cur):
    recorder.fail_next("accept")
    r = post_event(incoming("rtc_fail", line_secret=line[1]))
    assert r.status_code == 200 and r.json()["action"] == "accept_failed"
    assert _rejected(recorder, "rtc_fail")
    assert _call(cur, "rtc_fail")["status"] == "failed"


# ─── Extension mapping ───


def _claims(recorder, call_id):
    from clefs.modules.voice import calls

    [(_, _, body)] = [c for c in recorder.ops("accept") if c[1] == call_id]
    return calls.verify_call_token(body["mcp"]["authorization"]), body


def test_mapped_extension_binds_room_in_token_and_prompt(post_event, voice_env, recorder, line, cur):
    post_event(incoming("rtc_map", from_user="1502", line_secret=line[1]))
    claims, body = _claims(recorder, "rtc_map")
    assert claims["room"] == "502" and claims["hotel"] == "test-dubai" and claims["call_id"] == "rtc_map"
    assert "room 502" in body["instructions"]
    row = _call(cur, "rtc_map")
    assert row["extension_matched"] and row["room_confirmed_by"] == "extension"


def test_unmapped_extension_asks_for_the_room(post_event, voice_env, recorder, line, cur):
    """The From user is untrusted text: 504 looks like a room but is not in
    the allowlist, so nothing is bound and the prompt asks."""
    post_event(incoming("rtc_unmapped", from_user="504", line_secret=line[1]))
    claims, body = _claims(recorder, "rtc_unmapped")
    assert claims["room"] is None
    assert "did not tell us the room" in body["instructions"]
    row = _call(cur, "rtc_unmapped")
    assert row["room"] is None and row["room_confirmed_by"] == "none" and not row["extension_matched"]


def test_caller_id_mode_none_ignores_from(post_event, voice_env, recorder, line, cur):
    cur.execute("UPDATE vc_lines SET caller_id_mode = 'none' WHERE id = %s", (line[0]["id"],))
    post_event(incoming("rtc_fxo", from_user="1501", line_secret=line[1]))
    claims, _ = _claims(recorder, "rtc_fxo")
    assert claims["room"] is None


def test_extension_rate_limit(post_event, voice_env, recorder, line, cur):
    from clefs.modules.voice import constants as C

    cur.execute("UPDATE vc_lines SET max_concurrent = 50 WHERE id = %s", (line[0]["id"],))
    for i in range(C.EXTENSION_CALLS_PER_HOUR):
        assert post_event(incoming(f"rtc_rl{i}", from_user="1501", line_secret=line[1])).json()["action"] == "accepted"
    r = post_event(incoming("rtc_rl_over", from_user="1501", line_secret=line[1]))
    assert r.json()["reason"] == "extension_rate_limited"


def test_sideband_line_gets_function_tools(post_event, voice_env, recorder, line, cur):
    cur.execute("UPDATE vc_lines SET tool_transport = 'sideband' WHERE id = %s", (line[0]["id"],))
    post_event(incoming("rtc_sb", line_secret=line[1]))
    [(_, _, body)] = recorder.ops("accept")
    names = {t.get("name") for t in body["tools"]}
    assert all(t["type"] == "function" for t in body["tools"])
    assert "transfer_to_front_desk" in names


def test_call_end_event_closes_the_call(post_event, voice_env, recorder, line, cur):
    post_event(incoming("rtc_end", line_secret=line[1]))
    ev = {"id": "evt_end", "object": "event", "type": "call.ended", "created_at": int(time.time()),
          "data": {"call_id": "rtc_end"}}
    assert post_event(ev).json()["action"] == "recorded"
    row = _call(cur, "rtc_end")
    assert row["status"] == "ended" and row["ended_at"] is not None and row["duration_s"] is not None


def test_receipts_are_logged_for_the_console(post_event, voice_env, recorder, line, cur):
    post_event(incoming("rtc_log", line_secret=line[1]), webhook_id="msg_log")
    post_event(incoming("rtc_log_anon", from_host="unknown.example"), webhook_id="msg_anon")
    cur.execute("SELECT webhook_id, hotel_slug, project_id, outcome FROM vc_webhook_receipts ORDER BY webhook_id")
    rows = {r["webhook_id"]: r for r in cur.fetchall()}
    assert rows["msg_log"]["outcome"] == "accepted" and rows["msg_log"]["hotel_slug"] == "test-dubai"
    assert rows["msg_anon"]["outcome"] == "rejected" and rows["msg_anon"]["project_id"] == "proj_testvoice"


def test_concierge_not_live_gets_486(post_event, voice_env, recorder, line, cur, hotel_uae):
    """The readiness gate (concierge live.py): until the concierge is
    switched live, the phone fails over to the operator."""
    cur.execute("UPDATE cc_live SET live = FALSE WHERE hotel_slug = %s", (hotel_uae.slug,))
    r = post_event(incoming("rtc_notlive", line_secret=line[1]))
    assert r.json()["action"] == "rejected" and r.json()["reason"] == "concierge_not_live"
    assert _rejected(recorder, "rtc_notlive") and recorder.ops("accept") == []
