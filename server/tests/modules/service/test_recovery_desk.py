"""Recovery before the review (05 spec): flag_recovery opens or reuses a
case with a gesture Gemini suggests (a default when AI is off) and alerts the
duty manager; "not fixed" from the guest reopens the job and opens a case;
the Dashboard approves (the gesture reaches the guest in their language),
edits or dismisses it."""

from __future__ import annotations

import pytest

from clefs.core.errors import ClefsError
from clefs.modules.service import api, config_api, recovery, state
from clefs.settings import get_settings
from tests.modules.service.desk_helpers import answer_by_task, events, roster, staff, tasks

GESTURE = {"gesture": "We are so sorry for the wait. Dessert from our kitchen is on us tonight.",
           "kind": "fnb_credit", "value": 40, "reason": "Two asks for the same towels."}
ARABIC = "نعتذر عن الانتظار. الحلوى من مطبخنا على حسابنا الليلة."


@pytest.fixture(autouse=True)
def _ops_routes(app):
    config_api.mount_ops(app)


@pytest.fixture
def live_whatsapp(cur, hotel_uae, monkeypatch):
    from clefs.settings import reload_settings

    monkeypatch.setenv("WA_PLATFORM_PHONE_NUMBER_ID", "PNID-PLATFORM")
    reload_settings()
    cur.execute("INSERT INTO hotel_module_settings (hotel_slug, module, enabled, settings) "
                "VALUES (%s, 'service', TRUE, '{\"escalation_mode\": \"live\"}') "
                "ON CONFLICT (hotel_slug, module) DO UPDATE SET settings = EXCLUDED.settings", (hotel_uae.slug,))
    yield
    monkeypatch.undo()
    reload_settings()


def _duty_manager(cur, slug):
    dm = staff(cur, slug, "Omar Khalid", "Front Desk", phone="+971500000002")
    roster(cur, slug, dm, role="duty_manager", dept=None)
    return dm


def _job(cur, slug, *, session="sess-9", lang="ar", room="204", rtype="towels", key=None):
    maria = staff(cur, slug, f"Maria {key or 'S'}")
    roster(cur, slug, maria, floors=("2",))
    return api.create_job(cur, slug, room=room, request_type=rtype, quantity=2, description_en="Two bath towels",
                          original_text="منشفتان", guest_language=lang, session_id=session, idempotency_key=key)


def test_frustration_opens_one_case_per_conversation_and_alerts_the_duty_manager(cur, hotel_uae, fake_ai, outbox,
                                                                                    live_whatsapp):
    s = hotel_uae.slug
    _duty_manager(cur, s)
    job = _job(cur, s)
    outbox.reset()
    answer_by_task(fake_ai, {"service.recovery_gesture": GESTURE})
    out = api.flag_recovery(cur, s, kind="frustration", room="204", summary="Guest is upset: asked twice for towels",
                            session_id="sess-9", guest_language="ar")
    assert out["created"] is True and out["kind"] == "frustration" and out["approval_status"] == "pending"
    assert out["request_id"] == job["request_id"]  # the conversation's job, found by its session
    assert (out["suggested_gesture"], out["suggested_kind"]) == (GESTURE["gesture"], "fnb_credit")
    assert (out["suggested_value_minor"], out["suggested_value"], out["suggestion_source"]) == (4000, "40.00", "ai")
    call = next(c for c in fake_ai.calls if c.task == "service.recovery_gesture")
    assert call.model == get_settings().AI_MODEL_FAST and call.timeout_s <= 3
    sent = outbox.captured()
    assert len(sent) == 1 and "Needs your approval: room 204" in str(sent[0]["payload"])
    ev = events(cur, job["request_id"], "recovery")[0]["payload"]
    assert ev["to"] == "duty_manager" and ev["recipients"] == ["Omar Khalid"] and ev["case_id"] == out["case_id"]
    again = api.flag_recovery(cur, s, kind="frustration", room="204", summary="Still upset", session_id="sess-9")
    assert again["case_id"] == out["case_id"] and again["created"] is False
    assert tasks(fake_ai).count("service.recovery_gesture") == 1 and len(outbox.captured()) == 1  # asked and told once
    case = recovery.get_case(cur, s, out["case_id"])
    assert case["summary"].endswith("Still upset") and case["guest_language"] == "ar"


def test_suggestions_stay_within_the_front_desks_authority_and_default_without_ai(cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    answer_by_task(fake_ai, {"service.recovery_gesture": {**GESTURE, "value": 900, "kind": "fnb_credit"}})
    big = api.flag_recovery(cur, s, kind="never_arrived", room="105", summary="The water never came")
    assert big["suggested_value_minor"] == 10000  # AED 100: the staff role's own limit
    fake_ai.calls.clear()
    answer_by_task(fake_ai, {"service.recovery_gesture": TimeoutError("slow")})
    slow = api.flag_recovery(cur, s, kind="not_fixed", room="103", summary="Still broken")
    assert slow["suggestion_source"] == "default" and slow["suggested_gesture"].startswith("We are sorry")
    assert slow["suggested_kind"] == "amenity" and slow["suggested_value_minor"] == 5000
    # the same room and kind within the hour joins the open case
    assert api.flag_recovery(cur, s, kind="not_fixed", room="103", summary="Really")["case_id"] == slow["case_id"]
    with pytest.raises(ClefsError):
        api.flag_recovery(cur, s, kind="angry", room="103", summary="x")


def test_not_fixed_reopens_the_job_opens_a_case_and_tells_the_guest(client, cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    job = _job(cur, s)
    with pytest.raises(ClefsError) as early:
        api.guest_feedback(cur, s, job["tracking_token"], fixed=False)
    assert early.value.code == "cannot_confirm"  # not done yet: nothing to answer
    state.transition(cur, s, job["request_id"], "complete", state.SYSTEM)
    answer_by_task(fake_ai, {"service.recovery_gesture": GESTURE,
                             "service.translate_line": "نأسف لأن المشكلة لم تُحل بعد. أعدنا الطلب إلى الفريق وأبلغنا مدير المناوبة."})
    view = api.guest_feedback(cur, s, job["tracking_token"], fixed=False, comment="still no towels")
    assert view["state"] in ("assigned", "received") and view["guest_confirmation"] == "no"
    assert view["needs_guest_confirmation"] is False
    row = state.load(cur, s, job["request_id"])
    assert row["reopen_count"] == 1 and row["guest_comment"] == "still no towels"
    (case,) = recovery.list_cases(cur, s)
    assert case["request_id"] == job["request_id"] and case["reason"] == "not_resolved"
    assert case["approval_status"] == "pending" and case["summary"] == "still no towels"
    msgs = api.guest_messages(cur, s, job["tracking_token"])
    assert [m["kind"] for m in msgs] == ["status"] and msgs[0]["text"].startswith("نأسف")
    with pytest.raises(ClefsError):
        api.guest_messages(cur, s, "no-such-token")


def test_fixed_confirms_and_closes(client, cur, hotel_uae):
    s = hotel_uae.slug
    job = _job(cur, s)
    state.transition(cur, s, job["request_id"], "complete", state.SYSTEM)
    r = client.post(f"/public/r/{job['tracking_token']}/feedback", json={"fixed": True, "comment": "perfect"})
    assert r.status_code == 200, r.text
    assert r.json()["guest_confirmation"] == "yes" and r.json()["closed_at"] is not None
    row = state.load(cur, s, job["request_id"])
    assert row["closure_reason"] == "guest_confirmed" and row["guest_comment"] == "perfect"
    assert client.post(f"/public/r/{job['tracking_token']}/feedback", json={"fixed": False}).status_code == 409
    assert recovery.list_cases(cur, s) == []


def test_approving_sends_the_gesture_to_the_guest_in_their_language(client_as, client, cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    job = _job(cur, s)
    answer_by_task(fake_ai, {"service.recovery_gesture": GESTURE, "service.translate_line": ARABIC})
    flagged = api.flag_recovery(cur, s, kind="frustration", room="204", summary="Upset", session_id="sess-9")
    manager = client_as("dept_head")
    listed = manager.get(f"/hotels/{s}/service/recovery-cases", params={"status": "open"}).json()
    (item,) = [c for c in listed if c["id"] == flagged["case_id"]]
    assert item["needs_approval"] is True and item["kind"] == "frustration" and item["suggested_value"] == "40.00"
    assert item["request"]["item_label"] == "Towels" and item["request"]["staff_first_name"] == "Maria"
    pending = manager.get(f"/hotels/{s}/service/recovery-cases", params={"status": "open", "approval": "pending"}).json()
    assert [c["id"] for c in pending] == [flagged["case_id"]]
    edited = "We are so sorry. A dessert and a drink are on us tonight."
    r = manager.post(f"/hotels/{s}/service/recovery-cases/{flagged['case_id']}/decide",
                     json={"action": "approve", "gesture": edited})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "resolved" and body["approval_status"] == "approved" and body["approved_gesture"] == edited
    assert [(g["status"], g["value_minor"], g["kind"]) for g in body["gestures"]] == [("given", 4000, "fnb_credit")]
    msgs = api.guest_messages(cur, s, job["tracking_token"])
    assert [(m["kind"], m["text"]) for m in msgs] == [("gesture", ARABIC)]
    line = next(c for c in fake_ai.calls if c.task == "service.translate_line")
    assert line.input == edited and "Arabic" in line.system
    assert client.get(f"/public/r/{job['tracking_token']}").json()["messages"][0]["kind"] == "gesture"
    assert manager.get(f"/hotels/{s}/service/recovery-cases", params={"status": "open"}).json() == []
    again = manager.post(f"/hotels/{s}/service/recovery-cases/{flagged['case_id']}/decide", json={"action": "dismiss"})
    assert again.status_code == 409 and again.json()["code"] == "already_decided"
    assert events(cur, job["request_id"], "guest_message")[0]["payload"]["kind"] == "gesture"


def test_dismiss_authority_and_a_gesture_that_costs_nothing(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    job = _job(cur, s, lang="en")
    a = api.flag_recovery(cur, s, kind="not_fixed", room="204", summary="x", request_id=job["request_id"])
    desk = client_as("front_desk")
    url = f"/hotels/{s}/service/recovery-cases/{a['case_id']}/decide"
    above = desk.post(url, json={"action": "approve", "value_minor": 50000})  # AED 500 is above the desk's AED 100
    assert above.status_code == 403 and above.json()["code"] == "above_authority"
    assert client_as("staff").post(url, json={"action": "dismiss"}).status_code == 403  # needs service.manage
    d = desk.post(url, json={"action": "dismiss"})
    assert d.status_code == 200 and d.json()["status"] == "closed" and d.json()["approval_status"] == "dismissed"
    assert api.guest_messages(cur, s, job["tracking_token"]) == []
    b = api.flag_recovery(cur, s, kind="frustration", room="204", summary="y", session_id="sess-9")
    assert b["request_id"] == job["request_id"] and b["case_id"] != a["case_id"]  # the dismissed case is closed
    free = desk.post(f"/hotels/{s}/service/recovery-cases/{b['case_id']}/decide",
                     json={"action": "approve", "gesture": "The duty manager will call you in five minutes.",
                           "kind": "other", "value_minor": 0})
    assert free.status_code == 200, free.text
    assert [(g["status"], g["value_minor"]) for g in free.json()["gestures"]] == [("given", 0)]
    msgs = api.guest_messages(cur, s, job["tracking_token"])
    assert [m["text"] for m in msgs] == ["The duty manager will call you in five minutes."]  # English guest: no call
