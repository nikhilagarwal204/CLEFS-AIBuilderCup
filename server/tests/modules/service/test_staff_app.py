"""The staff app (05 spec, decision 10 and section 3): who is working and Go
off duty, the job list and detail in the worker's language (translated once
per request and language and cached; English when the model fails), and
replies to the guest, typed or by voice, in the guest's language."""

from __future__ import annotations

import base64
import uuid

import pytest

from clefs.modules.service import api, config_api, staff_lang
from tests.modules.service.desk_helpers import answer_by_task, events, h, login, roster, staff, tasks

TAGALOG = {"title": "2 x Tuwalya", "text": "Dalawang tuwalyang pampaligo para sa kuwarto 204"}


@pytest.fixture(autouse=True)
def _ops_routes(app):
    config_api.mount_ops(app)


def _maria(cur, slug):
    maria = staff(cur, slug, "Maria Santos", languages=("fil", "en"), pin=True)
    roster(cur, slug, maria, floors=("1", "2", "3", "4"))
    return maria


def _job(cur, slug, *, lang="ar", room="204", rtype="towels", key=None):
    return api.create_job(cur, slug, room=room, request_type=rtype, quantity=2,
                          description_en="Two bath towels for room 204", original_text="أريد منشفتين",
                          guest_language=lang, idempotency_key=key)


# ─── The job in the worker's language ───


def test_job_list_and_detail_read_in_the_workers_language_once(device_client, cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    maria = _maria(cur, s)
    dev = device_client(departments=["Housekeeping"])
    login(dev, maria)
    job = _job(cur, s)
    assert job["staff_id"] == maria
    cur.execute("SELECT payload FROM job_queue WHERE name = 'service.translate_job'")
    assert cur.fetchone()["payload"] == {"request_id": job["request_id"], "lang": "fil"}  # queued at assignment
    answer_by_task(fake_ai, {"service.translate_job": TAGALOG})
    r = dev.get("/device/service/requests", headers=h(maria))
    assert r.status_code == 200, r.text
    (item,) = [x for x in r.json() if x["id"] == job["request_id"]]
    assert (item["display_title"], item["display_text"]) == (TAGALOG["title"], TAGALOG["text"])
    assert item["display_language"] == "fil" and item["display_translated"] is True
    assert item["original_text"] == "أريد منشفتين" and item["guest_language"] == "ar" and item["needs_proof"] is True
    assert item["proof_status"] is None and item["eta_minutes"] == 5 and item["accepted_at"] is None
    assert item["priority"] in ("medium", "low", "high") and item["urgent"] is (item["priority"] == "high")
    call = next(c for c in fake_ai.calls if c.task == "service.translate_job")
    assert "Filipino" in call.system and "Two bath towels for room 204" in call.input
    detail = dev.get(f"/device/service/requests/{job['request_id']}", headers=h(maria))
    assert detail.status_code == 200 and detail.json()["request"]["display_title"] == TAGALOG["title"]
    assert dev.get("/device/service/requests", params={"mine": True}, headers=h(maria)).json()[0]["id"] == job["request_id"]
    assert tasks(fake_ai).count("service.translate_job") == 1  # cached per (request, language)


def test_an_english_reader_costs_no_model_call_and_a_failure_shows_english(device_client, cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    eng = staff(cur, s, "Ella English", languages=("en",), pin=True)
    roster(cur, s, eng, floors=("2",))
    dev = device_client()
    login(dev, eng)
    job = _job(cur, s)
    item = dev.get("/device/service/requests", headers=h(eng)).json()[0]
    assert item["display_title"] == "2 x Towels" and item["display_text"] == "Two bath towels for room 204"
    assert item["display_language"] == "en" and tasks(fake_ai) == []
    row = {"id": job["request_id"], "subtype": None, "request_type_key": "towels", "quantity": 2,
           "description": "Two bath towels for room 204"}
    answer_by_task(fake_ai, {"service.translate_job": TimeoutError("slow")})
    out = staff_lang.translate_job(s, row, "hi")
    assert out["display_translated"] is False and out["display_text"] == "Two bath towels for room 204"
    cur.execute("SELECT COUNT(*) AS n FROM svc_translations WHERE request_id = %s", (job["request_id"],))
    assert cur.fetchone()["n"] == 0  # a failure is never cached


def test_the_queued_translation_fills_the_cache(cur, hotel_uae, fake_ai, run_jobs):
    s = hotel_uae.slug
    _maria(cur, s)
    job = _job(cur, s)
    answer_by_task(fake_ai, {"service.translate_job": TAGALOG})
    run_jobs("service.translate_job")
    cur.execute("SELECT title, text, lang FROM svc_translations WHERE request_id = %s", (job["request_id"],))
    assert dict(cur.fetchone()) == {"title": TAGALOG["title"], "text": TAGALOG["text"], "lang": "fil"}
    row = {"id": job["request_id"], "subtype": None, "request_type_key": "towels", "quantity": 2,
           "description": "Two bath towels for room 204"}
    before = len(fake_ai.calls)
    assert staff_lang.display_fields(s, [row], "fil")[job["request_id"]]["display_title"] == TAGALOG["title"]
    assert len(fake_ai.calls) == before


def test_the_console_queue_and_detail_carry_the_same_fields_for_a_device(device_client, client_as, cur, hotel_uae,
                                                                          fake_ai):
    s = hotel_uae.slug
    maria = _maria(cur, s)
    dev = device_client()
    login(dev, maria)
    job = _job(cur, s)
    answer_by_task(fake_ai, {"service.translate_job": TAGALOG})
    queue = dev.get(f"/hotels/{s}/service/queue", headers=h(maria)).json()
    assert queue[0]["display_title"] == TAGALOG["title"] and queue[0]["display_language"] == "fil"
    one = dev.get(f"/hotels/{s}/service/requests/{job['request_id']}", headers=h(maria)).json()["request"]
    assert one["display_text"] == TAGALOG["text"]
    # a stale X-Clefs-Staff reads English instead of failing the list; a console user gets the plain card
    assert dev.get(f"/hotels/{s}/service/queue", headers=h(999999)).json()[0]["display_language"] == "en"
    assert "display_title" not in client_as("dept_head").get(f"/hotels/{s}/service/queue").json()[0]
    assert tasks(fake_ai).count("service.translate_job") == 1


# ─── Who is working, and Go off duty ───


def test_me_counters_and_going_off_duty_takes_the_worker_out_of_routing(device_client, cur, hotel_uae):
    s = hotel_uae.slug
    maria = _maria(cur, s)
    priya = staff(cur, s, "Priya Sharma", languages=("hi", "en"))
    roster(cur, s, priya, floors=("5",))
    dev = device_client(departments=["Housekeeping"])
    login(dev, maria)
    me = dev.get("/device/service/me", headers=h(maria)).json()
    assert me["staff"]["first_name"] == "Maria" and me["staff"]["reading_language"] == "fil"
    assert me["on_duty"] is True and me["counts"] == {"active": 0, "done_today": 0}
    assert me["shift"]["floors"] == ["1", "2", "3", "4"] and me["shift"]["shift_role"] == "attendant"
    first = _job(cur, s, key="a")
    assert first["staff_id"] == maria and dev.get("/device/service/me", headers=h(maria)).json()["counts"]["active"] == 1
    off = dev.post("/device/service/duty", json={"on_duty": False}, headers=h(maria))
    assert off.status_code == 200 and off.json()["on_duty"] is False and off.json()["off_duty_at"]
    assert dev.get("/device/service/me", headers=h(maria)).json()["on_duty"] is False
    second = _job(cur, s, room="203", rtype="linen", key="b")
    assert second["staff_id"] == priya  # Maria is off duty
    assert dev.post("/device/service/duty", json={"on_duty": True}, headers=h(maria)).json()["on_duty"] is True
    third = _job(cur, s, room="202", rtype="water", key="c")
    assert third["staff_id"] == maria
    done = dev.post("/device/service/actions", headers=h(maria), json={"actions": [
        {"client_event_id": str(uuid.uuid4()), "action": "complete", "request_id": third["request_id"]}]})
    assert done.json()["results"][0]["ok"] is True
    assert dev.get("/device/service/me", headers=h(maria)).json()["counts"] == {"active": 1, "done_today": 1}
    assert dev.post("/device/service/duty", json={"on_duty": False}).status_code == 400  # who? (no PIN header)


# ─── Replies to the guest ───


def test_a_typed_reply_reaches_the_guest_in_their_language(device_client, client, cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    maria = _maria(cur, s)
    dev = device_client()
    login(dev, maria)
    job = _job(cur, s, lang="ar")
    answer_by_task(fake_ai, {"service.translate_reply": "سأكون عندك خلال خمس دقائق"})
    r = dev.post(f"/device/service/requests/{job['request_id']}/reply",
                 json={"text": "Darating ako sa loob ng limang minuto"}, headers=h(maria))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["text_original"] == "Darating ako sa loob ng limang minuto"
    assert out["text_for_guest"] == "سأكون عندك خلال خمس دقائق" and out["language"] == "ar"
    assert out["translated"] is True and out["via"] == "typed"
    call = next(c for c in fake_ai.calls if c.task == "service.translate_reply")
    assert "Arabic" in call.system and "surname" in call.system
    msgs = api.guest_messages(cur, s, job["tracking_token"])
    assert [(m["kind"], m["text"], m["staff_first_name"]) for m in msgs] == [
        ("staff_reply", "سأكون عندك خلال خمس دقائق", "Maria")]
    assert isinstance(msgs[0]["at"], int)
    view = client.get(f"/public/r/{job['tracking_token']}").json()
    assert view["messages"][0]["text"] == "سأكون عندك خلال خمس دقائق" and "Santos" not in str(view)
    ev = events(cur, job["request_id"], "guest_message")[0]["payload"]
    assert ev["text_original"] == out["text_original"] and ev["via"] == "typed"


def test_a_voice_reply_is_transcribed_by_gemini_then_translated(device_client, cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    maria = _maria(cur, s)
    dev = device_client()
    login(dev, maria)
    job = _job(cur, s, lang="en")
    audio = b"\x1aE\xdf\xa3" + b"\x00" * 512  # a WebM header and silence
    answer_by_task(fake_ai, {"service.staff_voice": "Papunta na ako, limang minuto",
                             "service.translate_reply": "I am on my way, five minutes."})
    r = dev.post(f"/device/service/requests/{job['request_id']}/reply", headers=h(maria),
                 json={"audio_b64": base64.b64encode(audio).decode(), "mime": "audio/webm;codecs=opus"})
    assert r.status_code == 200, r.text
    assert r.json()["via"] == "voice" and r.json()["text_original"] == "Papunta na ako, limang minuto"
    assert r.json()["text_for_guest"] == "I am on my way, five minutes." and r.json()["language"] == "en"
    voice = next(c for c in fake_ai.calls if c.task == "service.staff_voice")
    assert voice.media == [(audio, "audio/webm")]


def test_reply_validation_and_outages(device_client, cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    maria = _maria(cur, s)
    dev = device_client()
    login(dev, maria)
    job = _job(cur, s, lang="ar")
    url = f"/device/service/requests/{job['request_id']}/reply"
    assert dev.post(url, json={"text": "hi"}).status_code == 400  # nobody named (X-Clefs-Staff)
    assert dev.post(url, json={}, headers=h(maria)).status_code == 400
    assert dev.post(url, json={"audio_b64": "AAAA", "mime": "video/mp4"}, headers=h(maria)).status_code == 415
    assert dev.post(url, json={"audio_b64": "not base64!", "mime": "audio/ogg"}, headers=h(maria)).status_code == 400
    assert dev.post("/device/service/requests/999999/reply", json={"text": "x"}, headers=h(maria)).status_code == 404
    answer_by_task(fake_ai, {})  # the model is down
    voice = dev.post(url, json={"audio_b64": base64.b64encode(b"OggS" + b"\x00" * 64).decode(), "mime": "audio/ogg"},
                     headers=h(maria))
    assert voice.status_code == 503 and voice.json()["code"] == "transcription_failed"
    typed = dev.post(url, json={"text": "Coming now"}, headers=h(maria))
    assert typed.status_code == 200 and typed.json()["translated"] is False
    assert typed.json()["text_for_guest"] == "Coming now"  # the worker's own words, never lost


def test_a_note_for_staff_is_translated_plainly_and_cached_apart(cur, hotel_uae, fake_ai):
    """The photo check's reason is a note to the worker: never softened into
    a guest apology, and cached apart from guest lines (migration 0135)."""
    from clefs.modules.service import staff_lang

    answer_by_task(fake_ai, {"service.translate_line": "Hindi tugma ang larawan."})
    text, translated = staff_lang.english_to(hotel_uae.slug, "The photo does not match the request.", "fil",
                                             cur=cur, audience="staff")
    assert (text, translated) == ("Hindi tugma ang larawan.", True)
    call = next(c for c in fake_ai.calls if c.task == "service.translate_line")
    assert "staff member" in call.system and "Do not apologise" in call.system
    cur.execute("SELECT purpose FROM svc_translations WHERE hotel_slug = %s AND lang = 'fil'", (hotel_uae.slug,))
    assert [r["purpose"] for r in cur.fetchall()] == ["staff_note"]
