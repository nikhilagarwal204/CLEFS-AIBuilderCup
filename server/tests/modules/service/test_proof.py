"""Proof before closure (05 spec, decisions 8 and 9): which jobs need a
photo, Mark as Done with a photo that Gemini's vision checks inside the same
call (a match completes the job; a mismatch keeps it open, says why in the
worker's language and alerts the supervisor; no check leaves it for a
person), the photo link for the console, storage in Postgres or a Cloud
Storage bucket, and deletion after 7 days."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from clefs.core import clock as clock_mod
from clefs.modules.service import api, config_api, proof, state
from clefs.settings import get_settings
from tests.modules.service.desk_helpers import JPEG, PNG, answer_by_task, events, h, login, roster, staff, tasks

MATCH = {"match": True, "confidence": 0.93, "reason": "Two towels are on the bed.", "observed": "two white towels"}
MISMATCH = {"match": False, "confidence": 0.88, "reason": "The photo shows an empty bed, no towels.",
            "observed": "an empty bed"}


@pytest.fixture(autouse=True)
def _ops_routes(app):
    config_api.mount_ops(app)


@pytest.fixture
def db_store():
    proof.set_store(None)
    yield
    proof.set_store(None)


def _worker(cur, slug, device_client, *, languages=("en",)):
    maria = staff(cur, slug, "Maria Santos", languages=languages, pin=True)
    roster(cur, slug, maria, floors=("1", "2"))
    dev = device_client()
    login(dev, maria)
    return maria, dev


def _job(cur, slug, rtype="towels", room="204", key=None):
    return api.create_job(cur, slug, room=room, request_type=rtype, quantity=2, description_en="Two bath towels",
                          original_text=None, guest_language="ar", idempotency_key=key)


def _upload(dev, staff_id, request_id, data=JPEG, *, ctype="image/jpeg", cid=None):
    form = {"client_event_id": cid} if cid else {}
    return dev.post(f"/device/service/requests/{request_id}/proof", headers=h(staff_id),
                    files={"photo": ("done.jpg", data, ctype)}, data=form)


def test_which_jobs_need_a_photo(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    for rtype, needed in (("towels", True), ("water", True), ("make_up_room", True), ("turndown", True),
                          ("maintenance", True), ("amenities", True), ("wake_up", False), ("front_desk", False),
                          ("concierge", False)):
        row = api.create_request(cur, s, room="", type=rtype, description=rtype.replace("_", " "), source="console")
        assert row["needs_proof"] is needed, rtype
    r = client_as("gm").put(f"/hotels/{s}/service/config/request-types", json={"request_types": [
        {"key": "towels", "label_en": "Towels", "department_key": "housekeeping", "needs_proof": False},
        {"key": "minibar", "label_en": "Minibar", "department_key": "housekeeping"}]})
    assert r.status_code == 200, r.text
    listed = {t["key"]: t["needs_proof"] for t in r.json()}
    assert listed["towels"] is False and listed["minibar"] is True and listed["wake_up"] is False
    assert api.create_request(cur, s, room="", type="towels", description="towels", source="console")["needs_proof"] is False


def test_a_matching_photo_completes_the_job(device_client, client, client_as, cur, hotel_uae, fake_ai, db_store):
    s = hotel_uae.slug
    maria, dev = _worker(cur, s, device_client)
    job = _job(cur, s)
    answer_by_task(fake_ai, {"service.verify_proof": MATCH})
    cid = str(uuid.uuid4())
    r = _upload(dev, maria, job["request_id"], cid=cid)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["verified"] is True and out["completed"] is True and out["proof_status"] == "verified"
    assert out["reason"] == MATCH["reason"] and out["replayed"] is False
    req = out["request"]
    assert req["state"] == "completed" and req["proof_status"] == "verified" and req["resolution_code"] == "photo_verified"
    assert req["display_title"] == "2 x Towels" and req["proof_photo_url"]
    check = next(c for c in fake_ai.calls if c.task == "service.verify_proof")
    assert check.media == [(JPEG, "image/jpeg")] and check.model == get_settings().AI_MODEL_DEFAULT
    assert '"job": "2 x Towels"' in check.input and check.schema_name == "ProofCheck"
    cur.execute("SELECT storage, data, status, confidence, sha256 FROM svc_request_proofs WHERE request_id = %s",
                (job["request_id"],))
    p = cur.fetchone()
    assert p["storage"] == "db" and bytes(p["data"]) == JPEG and p["status"] == "verified" and p["confidence"] > 0.9
    # the console's photo link works without a session for 5 minutes
    photo = client.get(req["proof_photo_url"])
    assert photo.status_code == 200 and photo.content == JPEG and photo.headers["content-type"] == "image/jpeg"
    bare = req["proof_photo_url"].split("?")[0]
    assert client.get(bare).status_code == 401 and client.get(bare + "?t=forged").status_code == 401
    assert client_as("dept_head").get(bare).content == JPEG
    other = req["proof_photo_url"].replace(f"/requests/{job['request_id']}/", f"/requests/{job['request_id'] + 1}/")
    assert client.get(other).status_code == 401  # a link is for its own request
    # the guest is asked now; the same upload again changes nothing and calls nothing
    view = client.get(f"/public/r/{job['tracking_token']}").json()
    assert view["proof_status"] == "verified" and view["needs_guest_confirmation"] is True
    n = len(fake_ai.calls)
    again = _upload(dev, maria, job["request_id"], cid=cid)
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["verified"] is True
    assert len(fake_ai.calls) == n


def test_a_mismatch_keeps_the_job_open_and_alerts_the_supervisor(device_client, cur, hotel_uae, fake_ai, db_store,
                                                                  outbox, monkeypatch):
    from clefs.settings import reload_settings

    s = hotel_uae.slug
    monkeypatch.setenv("WA_PLATFORM_PHONE_NUMBER_ID", "PNID-PLATFORM")
    reload_settings()
    try:
        sup = staff(cur, s, "Sara Sup", phone="+971500000001")
        roster(cur, s, sup, role="supervisor")
        cur.execute("INSERT INTO hotel_module_settings (hotel_slug, module, enabled, settings) "
                    "VALUES (%s, 'service', TRUE, '{\"escalation_mode\": \"live\"}') "
                    "ON CONFLICT (hotel_slug, module) DO UPDATE SET settings = EXCLUDED.settings", (s,))
        maria, dev = _worker(cur, s, device_client, languages=("fil", "en"))
        job = _job(cur, s)
        outbox.reset()
        answer_by_task(fake_ai, {"service.verify_proof": MISMATCH,
                                 "service.translate_line": "Walang tuwalya sa larawan, kama lang."})
        r = _upload(dev, maria, job["request_id"], PNG, ctype="image/png")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["verified"] is False and out["completed"] is False and out["proof_status"] == "rejected"
        assert out["reason"] == "Walang tuwalya sa larawan, kama lang." and out["reason_en"] == MISMATCH["reason"]
        row = state.load(cur, s, job["request_id"])
        assert row["state"] == "assigned" and row["proof_status"] == "rejected" and row["proof_reason"] == MISMATCH["reason"]
        ev = events(cur, job["request_id"], "proof_rejected")[0]["payload"]
        assert ev["to"] == "supervisor" and ev["recipients"] == ["Sara Sup"] and ev["shadow"] is False
        sent = outbox.captured()
        assert len(sent) == 1 and "Photo check failed: room 204" in str(sent[0]["payload"])
        # a "match" Gemini is unsure of is not proof either
        answer_by_task(fake_ai, {"service.verify_proof": {**MATCH, "confidence": 0.4}})
        unsure = _upload(dev, maria, job["request_id"]).json()
        assert unsure["proof_status"] == "rejected" and unsure["completed"] is False
        assert tasks(fake_ai).count("service.verify_proof") == 2
    finally:
        monkeypatch.undo()
        reload_settings()


def test_no_check_leaves_the_job_open_for_a_person(device_client, cur, hotel_uae, db_store):
    s = hotel_uae.slug
    maria, dev = _worker(cur, s, device_client)
    job = _job(cur, s)
    out = _upload(dev, maria, job["request_id"]).json()  # AI is off in tests without fake_ai
    assert out["proof_status"] == "unverified" and out["completed"] is False and out["verified"] is False
    assert "supervisor" in out["reason"]
    assert state.load(cur, s, job["request_id"])["state"] == "assigned"
    assert events(cur, job["request_id"], "proof_unverified")


def test_bad_uploads_are_refused(device_client, cur, hotel_uae, fake_ai, db_store):
    s = hotel_uae.slug
    maria, dev = _worker(cur, s, device_client)
    job = _job(cur, s)
    assert _upload(dev, maria, job["request_id"], JPEG + b"\x00" * (3 * 1024 * 1024)).status_code == 413
    assert _upload(dev, maria, job["request_id"], b"%PDF-1.7 not a photo", ctype="image/jpeg").status_code == 415
    assert dev.post(f"/device/service/requests/{job['request_id']}/proof",
                    files={"photo": ("p.jpg", JPEG, "image/jpeg")}).status_code == 400  # nobody named
    assert _upload(dev, maria, 999999).status_code == 404
    state.transition(cur, s, job["request_id"], "complete", state.SYSTEM)
    late = _upload(dev, maria, job["request_id"])
    assert late.status_code == 409 and late.json()["code"] == "already_closed" and late.json()["conflict"]["state"] == "completed"
    assert tasks(fake_ai) == []


class _Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def upload_from_string(self, data, content_type=None):
        self.bucket.objects[self.name] = (data, content_type)

    def download_as_bytes(self):
        if self.name not in self.bucket.objects:
            raise type("NotFound", (Exception,), {})("gone")
        return self.bucket.objects[self.name][0]

    def delete(self):
        if self.name not in self.bucket.objects:
            raise type("NotFound", (Exception,), {})("gone")
        del self.bucket.objects[self.name]


class _Bucket:
    def __init__(self):
        self.objects = {}

    def blob(self, name):
        return _Blob(self, name)


class _Client:
    def __init__(self):
        self.buckets = {}

    def bucket(self, name):
        return self.buckets.setdefault(name, _Bucket())


def test_photos_go_to_the_bucket_when_one_is_set_and_are_deleted_after_7_days(device_client, client, cur, hotel_uae,
                                                                              fake_ai):
    s = hotel_uae.slug
    gcs = _Client()
    proof.set_store(proof.GcsProofStore("clefs-proof-test", client=gcs))
    try:
        maria, dev = _worker(cur, s, device_client)
        job = _job(cur, s)
        answer_by_task(fake_ai, {"service.verify_proof": MATCH})
        out = _upload(dev, maria, job["request_id"]).json()
        assert out["completed"] is True
        cur.execute("SELECT storage, object_key, data FROM svc_request_proofs WHERE request_id = %s", (job["request_id"],))
        p = cur.fetchone()
        assert p["storage"] == "gcs" and p["data"] is None and p["object_key"].startswith(f"gs://clefs-proof-test/{s}/")
        key = p["object_key"].split("/", 3)[3]
        assert gcs.bucket("clefs-proof-test").objects[key] == (JPEG, "image/jpeg")
        assert client.get(out["request"]["proof_photo_url"]).content == JPEG
        assert proof.purge(cur, s) == 0  # not 7 days yet
        clock_mod.freeze(clock_mod.now() + timedelta(days=7, minutes=1))
        assert proof.purge(cur, s) == 1 and gcs.bucket("clefs-proof-test").objects == {}
        assert client.get(out["request"]["proof_photo_url"]).status_code == 410
    finally:
        proof.set_store(None)
        clock_mod.unfreeze()


def test_postgres_photos_are_dropped_after_7_days(device_client, cur, hotel_uae, fake_ai, db_store):
    from clefs import jobs

    assert "service.proof_retention" in {j.name for j in jobs.registered_jobs()}
    s = hotel_uae.slug
    maria, dev = _worker(cur, s, device_client)
    job = _job(cur, s)
    answer_by_task(fake_ai, {"service.verify_proof": MISMATCH})
    _upload(dev, maria, job["request_id"])
    clock_mod.freeze(clock_mod.now() + timedelta(days=8))
    try:
        assert proof.purge(cur, s) == 1
    finally:
        clock_mod.unfreeze()
    cur.execute("SELECT data, deleted_at, status, reason FROM svc_request_proofs WHERE request_id = %s", (job["request_id"],))
    p = cur.fetchone()
    assert p["data"] is None and p["deleted_at"] is not None and p["status"] == "rejected" and p["reason"]
    with pytest.raises(Exception):
        proof.photo_row(cur, s, job["request_id"])
