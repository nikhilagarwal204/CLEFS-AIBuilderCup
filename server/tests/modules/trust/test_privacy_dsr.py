"""Data subject requests: intake, queue, verification and executors (03 spec 5.3)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone

from clefs.core import consent, privacy
from clefs.core import time as ctime
from clefs.core.hotels import get_hotel
from clefs.files import get_file
from clefs.modules.trust import api as trust_api
from clefs.modules.trust import dsr

PHONE = "+971501234567"
EMAIL = "guest@example.com"


def _guest(cur, slug, *, room="101", other_room="102"):
    """A stay with contacts, consents, a transcript, a request, and a
    neighbour's rows that must never be touched."""
    hotel = get_hotel(cur, slug)
    cin, cout = date(2026, 9, 1), date(2026, 9, 3)
    cur.execute(
        "INSERT INTO reservations (hotel_slug, pms_res_id, guest_name, check_in, check_out, nights, room_number, currency) "
        "VALUES (%s, 'R-1', 'Sara Al Mansoori', %s, %s, 2, %s, 'AED') RETURNING id",
        (slug, cin, cout, room),
    )
    res_id = cur.fetchone()["id"]
    for kind, raw in (("phone", PHONE), ("email", EMAIL)):
        cur.execute(
            "INSERT INTO reservation_contacts (hotel_slug, reservation_id, kind, value_enc, value_hmac, provenance) "
            "VALUES (%s, %s, %s, %s, %s, 'pms_direct')",
            (slug, res_id, kind, privacy.seal(raw), privacy.contact_hmac(slug, kind, raw)),
        )
    during = ctime.to_ms(ctime.local_day_bounds(hotel, date(2026, 9, 2))[0] + timedelta(hours=10))
    for r in (room, other_room):
        cur.execute(
            "INSERT INTO conversation_logs (hotel_slug, room_number, session_id, role, message, timestamp) "
            "VALUES (%s, %s, 's', 'user', %s, %s)",
            (slug, r, f"towels please from {r}", during),
        )
        cur.execute(
            "INSERT INTO staff_requests (hotel_slug, room, type, description, timestamp) VALUES (%s, %s, 'Cleaning', %s, %s)",
            (slug, r, f"towels for {r}", during),
        )
    ref = privacy.contact_ref(slug, "phone", PHONE)
    for purpose in ("marketing", "ai_analytics", "recording"):
        consent.record_consent(cur, slug, ref, purpose, True, source="registration_card")
    return res_id, ref


def _file(cur, slug, type_, **ids):
    ids = ids or {"phone": PHONE, "email": EMAIL, "name": "Sara Al Mansoori"}
    return dsr.create_request(cur, slug, type=type_, identifiers=dsr.Identifiers(**ids), details="Please act",
                              channel="console")


# ─── Public intake ───


def test_public_form_files_a_sealed_request(cur, hotel_uae, client, outbox):
    slug = hotel_uae.slug
    ctx = client.get("/public/privacy/form", params={"hotel": slug})
    assert ctx.status_code == 200 and ctx.json()["hotel"]["name"] == "Test Hotel Dubai"
    r = client.post("/public/privacy/requests", json={"hotel": slug, "type": "access", "email": EMAIL,
                                                      "phone": "050 123 4567", "details": "What do you hold on me?"})
    assert r.status_code == 202 and r.json() == {"ok": True, "due_days": 30}
    cur.execute("SELECT * FROM trs_dsr_requests WHERE hotel_slug = %s", (slug,))
    row = cur.fetchone()
    assert row["channel"] == "public_form" and row["status"] == "received"
    # Nothing raw at rest: sealed blobs plus HMACs (the national number
    # hashes the same as its E.164 form).
    raw = json.dumps({k: (str(v) if not isinstance(v, (bytes, memoryview)) else "") for k, v in row.items()})
    assert EMAIL not in raw and "4567" not in raw.replace(row["identifiers_hint"]["phone"], "")
    assert privacy.contact_hmac(slug, "phone", PHONE).hex() in row["identifier_hmacs"]
    assert (row["due_at"] - row["received_at"]).days == 30
    cur.execute("SELECT channel, recipient FROM outbound_messages WHERE hotel_slug = %s AND purpose = 'trust.dsr_ack'", (slug,))
    ack = cur.fetchone()
    assert ack["channel"] == "email" and ack["recipient"] == EMAIL
    cur.execute("SELECT kind, status FROM signals WHERE hotel_slug = %s AND kind = 'trust.dsr_due'", (slug,))
    assert cur.fetchone()["status"] == "open"


def test_public_form_validation_rate_limit_and_honeypot(cur, hotel_uae, client):
    slug = hotel_uae.slug
    assert client.post("/public/privacy/requests", json={"hotel": slug, "type": "access"}).json()["code"] == "identifier_required"
    assert client.post("/public/privacy/requests", json={"hotel": slug, "type": "nope", "email": EMAIL}).status_code == 400
    assert client.post("/public/privacy/requests", json={"hotel": "no-such", "type": "access", "email": EMAIL}).status_code == 404
    bot = client.post("/public/privacy/requests", json={"hotel": slug, "type": "erase", "email": EMAIL, "website": "x"})
    assert bot.status_code == 202
    cur.execute("SELECT count(*) AS n FROM trs_dsr_requests WHERE hotel_slug = %s", (slug,))
    assert cur.fetchone()["n"] == 0
    codes = [client.post("/public/privacy/requests", json={"hotel": slug, "type": "access", "email": f"g{i}@x.test"}).status_code
             for i in range(dsr_ip_limit() + 1)]
    assert codes[-1] == 429 and set(codes[:-1]) == {202}


def _post(client, slug, email, type_="access"):
    return client.post("/public/privacy/requests", json={"hotel": slug, "type": type_, "email": email})


def test_hotel_threshold_flags_instead_of_refusing(cur, hotel_uae, client, client_as, outbox, monkeypatch):
    """Regression (security-privacy-8): past the hotel's daily threshold a
    genuine request is still filed (flagged), never answered 429; flagged
    rows send no acknowledgement and share one aggregate signal."""
    from clefs.modules.trust import public

    slug = hotel_uae.slug
    monkeypatch.setattr(public, "PER_IP_PER_HOUR", 100)
    monkeypatch.setattr(public, "PER_HOTEL_PER_DAY", 2)
    codes = [_post(client, slug, f"g{i}@x.test").status_code for i in range(4)]
    assert codes == [202, 202, 202, 202]
    cur.execute("SELECT id, suspected_flood FROM trs_dsr_requests WHERE hotel_slug = %s ORDER BY id", (slug,))
    rows = [dict(r) for r in cur.fetchall()]
    assert [r["suspected_flood"] for r in rows] == [False, False, True, True]
    cur.execute("SELECT count(*) AS n FROM outbound_messages WHERE hotel_slug = %s AND purpose = 'trust.dsr_ack'", (slug,))
    assert cur.fetchone()["n"] == 2  # flagged requests are not acknowledged
    cur.execute("SELECT dedupe_key, title, status FROM signals WHERE hotel_slug = %s AND kind = 'trust.dsr_due' "
                "ORDER BY dedupe_key", (slug,))
    sigs = {r["dedupe_key"]: dict(r) for r in cur.fetchall()}
    assert len(sigs) == 3 and sigs[f"trust.dsr_due:{slug}:flood"]["title"].startswith("2 privacy requests")
    # The owner sees the flag in the queue, and triaging the last flagged
    # request resolves the aggregate signal.
    owner = client_as("owner")
    queue = owner.get(f"/hotels/{slug}/trust/dsr").json()
    assert sorted(i["suspected_flood"] for i in queue) == [False, False, True, True]
    for r in rows[2:]:
        c = owner.post(f"/hotels/{slug}/trust/dsr/{r['id']}/close", json={"status": "rejected", "note": "junk"})
        assert c.status_code == 200, c.text
    cur.execute("SELECT status FROM signals WHERE hotel_slug = %s AND dedupe_key = %s", (slug, f"trust.dsr_due:{slug}:flood"))
    assert cur.fetchone()["status"] == "resolved"


def test_acknowledgements_are_capped_per_address(cur, hotel_uae, client, outbox, monkeypatch):
    from clefs.modules.trust import public

    slug = hotel_uae.slug
    monkeypatch.setattr(public, "PER_IP_PER_HOUR", 100)
    for _ in range(public.ACKS_PER_ADDRESS_PER_DAY + 2):
        assert _post(client, slug, "victim@x.test").status_code == 202
    cur.execute("SELECT count(*) AS n FROM trs_dsr_requests WHERE hotel_slug = %s", (slug,))
    assert cur.fetchone()["n"] == public.ACKS_PER_ADDRESS_PER_DAY + 2  # every request is filed
    cur.execute("SELECT count(*) AS n FROM outbound_messages WHERE hotel_slug = %s AND purpose = 'trust.dsr_ack'", (slug,))
    assert cur.fetchone()["n"] == public.ACKS_PER_ADDRESS_PER_DAY
    cur.execute("SELECT key FROM rate_limits WHERE key LIKE 'trust:dsr:ack:%%'")
    assert all("victim" not in r["key"] for r in cur.fetchall())  # hashed, never the address


def dsr_ip_limit():
    from clefs.modules.trust import public

    return public.PER_IP_PER_HOUR


# ─── Queue and verification ───


def test_queue_is_owner_gm_only_and_unmasked_for_them(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    row = _file(cur, slug, "access")
    gm = client_as("gm")
    items = gm.get(f"/hotels/{slug}/trust/dsr").json()
    assert items[0]["id"] == row["id"] and items[0]["identifiers"]["email"] == EMAIL
    assert items[0]["days_left"] in (29, 30) and items[0]["overdue"] is False
    assert "identifiers_enc" not in items[0] and "identifier_hmacs" not in items[0]
    for role in ("finance", "front_desk", "dept_head", "staff", "revenue"):
        assert client_as(role).get(f"/hotels/{slug}/trust/dsr").status_code == 403, role


def test_execute_requires_verification(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    row = _file(cur, slug, "access")
    owner = client_as("owner")
    r = owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={})
    assert r.status_code == 409 and r.json()["code"] == "dsr_not_verified"
    assert owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/verify", json={"method": "guess"}).status_code == 400
    v = owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/verify", json={"method": "id_document", "note": "passport seen"})
    assert v.status_code == 200 and v.json()["status"] == "verified"
    assert owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/verify", json={"method": "id_document"}).status_code == 409


# ─── Executors ───


def _verified(cur, slug, type_, client, **ids):
    row = _file(cur, slug, type_, **ids)
    assert client.post(f"/hotels/{slug}/trust/dsr/{row['id']}/verify", json={"method": "matched_reservation"}).status_code == 200
    return row


def test_access_bundle_holds_only_the_subjects_rows(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    _guest(cur, slug)
    owner = client_as("owner")
    row = _verified(cur, slug, "access", owner)
    r = owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed" and body["result_upload_id"]
    assert body["result"]["sections"]["transcripts"] == 1 and body["result"]["sections"]["consents"] == 3
    _, blob = get_file(cur, slug, body["result_upload_id"])
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert {"subject.json", "stays.csv", "contacts.csv", "consents.csv", "transcripts.csv", "requests.csv", "README.txt"} <= set(z.namelist())
    doc = json.loads(z.read("subject.json"))
    assert doc["sections"]["transcripts"][0]["message"] == "towels please from 101"
    assert doc["sections"]["stays"][0]["guest_name"] == "Sara Al Mansoori"
    assert {c["value"] for c in doc["sections"]["contacts"]} == {PHONE, EMAIL}
    assert "102" not in json.dumps(doc["sections"]["requests"])
    link = owner.get(f"/hotels/{slug}/trust/dsr/{row['id']}/download").json()
    assert link["url"].startswith(f"/hotels/{slug}/files/{body['result_upload_id']}?t=")
    cur.execute("SELECT status FROM signals WHERE hotel_slug = %s AND kind = 'trust.dsr_due'", (slug,))
    assert cur.fetchone()["status"] == "resolved"


def test_erase_anonymizes_and_withdraws(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    res_id, ref = _guest(cur, slug)
    owner = client_as("owner")
    row = _verified(cur, slug, "erase", owner)
    r = owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={})
    assert r.status_code == 400 and r.json()["code"] == "confirm_required"
    r = owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={"confirm": True})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["erased"]["transcripts"] == 1 and result["erased"]["requests"] == 1
    assert result["erased"]["contacts"] == 2 and result["consents_withdrawn"] == 3
    cur.execute("SELECT room_number, message FROM conversation_logs WHERE hotel_slug = %s ORDER BY room_number", (slug,))
    assert [tuple(x.values()) for x in cur.fetchall()] == [("101", "[erased]"), ("102", "towels please from 102")]
    cur.execute("SELECT description FROM staff_requests WHERE hotel_slug = %s AND room = '101'", (slug,))
    assert cur.fetchone()["description"] == "[erased]"
    cur.execute("SELECT guest_name, nights FROM reservations WHERE id = %s", (res_id,))
    assert dict(cur.fetchone()) == {"guest_name": "[erased]", "nights": 2}  # the stay itself is an accounting record
    cur.execute("SELECT count(*) AS n FROM reservation_contacts WHERE reservation_id = %s AND value_hmac IS NOT NULL", (res_id,))
    assert cur.fetchone()["n"] == 0
    assert not consent.has_consent(cur, slug, ref, "marketing")
    assert trust_api.is_restricted(cur, slug, ref, scope="marketing") and trust_api.is_restricted(cur, slug, ref, scope="profile_memory")


def test_erase_takes_the_guests_own_words_too(cur, hotel_uae, client_as):
    """Regression (spec-erase-retention-leave-guest-words): description,
    original_text (the guest's own language) and guest_comment (the tracker
    "not resolved" note) all read [erased]; the neighbour's row is untouched."""
    slug = hotel_uae.slug
    # Service adds these columns (0100_service_requests_v2.sql); a trust-only
    # run lacks them, so add them here (rolled back with the test).
    cur.execute("ALTER TABLE staff_requests ADD COLUMN IF NOT EXISTS original_text TEXT, "
                "ADD COLUMN IF NOT EXISTS guest_comment TEXT")
    _guest(cur, slug)
    cur.execute("UPDATE staff_requests SET original_text = 'مناشف من فضلك ' || room, "
                "guest_comment = 'still waiting in ' || room WHERE hotel_slug = %s", (slug,))
    owner = client_as("owner")
    row = _verified(cur, slug, "erase", owner)
    r = owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={"confirm": True})
    assert r.status_code == 200, r.text
    cur.execute("SELECT room, description, original_text, guest_comment FROM staff_requests "
                "WHERE hotel_slug = %s ORDER BY room", (slug,))
    got = [tuple(x.values()) for x in cur.fetchall()]
    assert got == [
        ("101", "[erased]", "[erased]", "[erased]"),
        ("102", "towels for 102", "مناشف من فضلك 102", "still waiting in 102"),
    ]


def test_restrict_and_object_flag_and_withdraw(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    _, ref = _guest(cur, slug)
    owner = client_as("owner")
    row = _verified(cur, slug, "restrict", owner, phone=PHONE)
    assert owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={}).status_code == 200
    assert not consent.has_consent(cur, slug, ref, "marketing") and not consent.has_consent(cur, slug, ref, "ai_analytics")
    assert consent.has_consent(cur, slug, ref, "recording")  # restriction is scoped, not an erasure
    assert trust_api.is_restricted(cur, slug, ref) and not trust_api.is_restricted(cur, slug, ref, scope="profile_memory")
    row2 = _verified(cur, slug, "object", owner, phone=PHONE)
    assert owner.post(f"/hotels/{slug}/trust/dsr/{row2['id']}/execute", json={}).status_code == 200
    assert trust_api.is_restricted(cur, slug, ref, scope="profile_memory")
    assert not trust_api.is_restricted(cur, slug, "someone-else")


CHANNEL_PURPOSES = ("marketing_whatsapp", "marketing_email")


def _channel_consents(cur, slug, ref):
    # Guests registers these on import; a trust-only run registers them here
    # (idempotent), the way any module adding a channel would.
    for p in CHANNEL_PURPOSES:
        consent.register_consent_purpose(p)
        consent.record_consent(cur, slug, ref, p, True, source="registration_card")


def test_restrict_withdraws_every_marketing_channel_purpose(cur, hotel_uae, client_as):
    """Regression (spec-dsr-restrict-misses-channel-marketing): guests split
    'marketing' per channel, and journeys check those purposes. A restrict
    must withdraw them all, and the flag must answer for a channel scope."""
    slug = hotel_uae.slug
    _, ref = _guest(cur, slug)
    _channel_consents(cur, slug, ref)
    assert trust_api.is_restricted(cur, slug, ref, scope="marketing_whatsapp") is False
    owner = client_as("owner")
    row = _verified(cur, slug, "restrict", owner, phone=PHONE)
    r = owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["consents_withdrawn"] == 4  # marketing, ai_analytics, both channels
    for p in ("marketing", *CHANNEL_PURPOSES):
        assert not consent.has_consent(cur, slug, ref, p), p
    assert consent.has_consent(cur, slug, ref, "recording")
    for scope in ("marketing", *CHANNEL_PURPOSES, "marketing_sms"):
        assert trust_api.is_restricted(cur, slug, ref, scope=scope), scope
    assert not trust_api.is_restricted(cur, slug, ref, scope="profile_memory")


def test_object_withdraws_channel_purposes_too(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    _, ref = _guest(cur, slug)
    _channel_consents(cur, slug, ref)
    owner = client_as("owner")
    row = _verified(cur, slug, "object", owner, phone=PHONE)
    assert owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={}).status_code == 200
    assert not any(consent.has_consent(cur, slug, ref, p) for p in CHANNEL_PURPOSES)
    assert set(dsr.expand_scopes(dsr.OBJECT_SCOPES)) >= {"marketing", *CHANNEL_PURPOSES, "profile_memory"}


def test_restrict_suppresses_guest_marketing_when_guests_is_loaded(cur, hotel_uae, client_as):
    """Second guard: journeys (J3/J8) and campaigns ask guests'
    can_send_marketing / marketing_audience, which refuse a suppressed
    identity even if a later opt-in re-grants the consent."""
    import pytest

    from clefs.registry import enabled_modules

    if "guests" not in enabled_modules():
        pytest.skip("guests not loaded (run with CLEFS_ONLY_MODULES=trust,guests)")
    from clefs.modules.guests import api as guests_api

    slug = hotel_uae.slug
    _, ref = _guest(cur, slug)
    _channel_consents(cur, slug, ref)
    h = privacy.contact_hmac(slug, "phone", PHONE)
    at = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    ok, why = guests_api.can_send_marketing(cur, slug, identity_hmacs=[h], channel="whatsapp", at=at, phase="post_stay")
    assert why != "suppressed"
    owner = client_as("owner")
    row = _verified(cur, slug, "restrict", owner, phone=PHONE, email=EMAIL)
    r = owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={})
    assert r.status_code == 200 and r.json()["result"]["marketing_suppressed"] == 2
    for channel, kind, raw in (("whatsapp", "phone", PHONE), ("email", "email", EMAIL)):
        hh = privacy.contact_hmac(slug, kind, raw)
        ok, why = guests_api.can_send_marketing(cur, slug, identity_hmacs=[hh], channel=channel, at=at, phase="post_stay")
        assert (ok, why) == (False, "suppressed"), channel
        assert ref not in {a["contact_ref"] for a in guests_api.marketing_audience(cur, slug, channel=channel)}


def test_rectify_needs_a_note_and_close_paths(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    owner = client_as("owner")
    row = _verified(cur, slug, "rectify", owner)
    assert owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={}).json()["code"] == "note_required"
    done = owner.post(f"/hotels/{slug}/trust/dsr/{row['id']}/execute", json={"note": "Name spelling fixed in PMS"})
    assert done.json()["status"] == "completed"
    other = _file(cur, slug, "erase")
    r = owner.post(f"/hotels/{slug}/trust/dsr/{other['id']}/close", json={"status": "rejected", "note": "Could not verify"})
    assert r.json()["status"] == "rejected"
    assert owner.post(f"/hotels/{slug}/trust/dsr/{other['id']}/close", json={"status": "cancelled"}).status_code == 409
    assert [x["id"] for x in owner.get(f"/hotels/{slug}/trust/dsr", params={"status": "all"}).json()]


def test_due_reminder_marks_overdue(cur, hotel_uae, clock, run_jobs):
    slug = hotel_uae.slug
    clock.freeze(datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc))
    row = _file(cur, slug, "access")
    clock.freeze(datetime(2026, 11, 2, 5, 1, tzinfo=timezone.utc))  # 09:01 Dubai, past due
    run_jobs(["trust.dsr_due_reminder"], scheduled=True)
    cur.execute("SELECT title, severity FROM signals WHERE hotel_slug = %s AND subject_id = %s AND kind = 'trust.dsr_due'",
                (slug, str(row["id"])))
    sig = cur.fetchone()
    assert "overdue" in sig["title"] and sig["severity"] == 4


def test_registry_tables_match_by_hmac_array_and_room_window(cur, hotel_uae, client_as, monkeypatch):
    """A module's registered tables are covered without Trust importing it:
    an array of contact HMACs matches on overlap, a room-keyed table matches
    inside the subject's stay window only."""
    slug = hotel_uae.slug
    _guest(cur, slug)
    cur.execute("CREATE TABLE trs_t_stays (id SERIAL, hotel_slug TEXT, hmacs BYTEA[], note TEXT, updated_at TIMESTAMPTZ DEFAULT now())")
    cur.execute("CREATE TABLE trs_t_orders (id SERIAL, hotel_slug TEXT, room TEXT, notes TEXT, created_at TIMESTAMPTZ)")
    monkeypatch.setitem(privacy._registry, ("t", "trs_t_stays"),
                        privacy.PiiSpec("t", "trs_t_stays", {"hmacs": "hmac"}, "updated_at", 730, "delete"))
    monkeypatch.setitem(privacy._registry, ("t", "trs_t_orders"),
                        privacy.PiiSpec("t", "trs_t_orders", {"room": "room", "notes": "free_text"}, "created_at", 730,
                                        "redact", ("notes",)))
    mine = privacy.contact_hmac(slug, "phone", PHONE)
    cur.execute("INSERT INTO trs_t_stays (hotel_slug, hmacs, note) VALUES (%s, %s, 'mine'), (%s, %s, 'theirs')",
                (slug, [mine], slug, [b"\x01" * 32]))
    during = datetime(2026, 9, 2, 8, tzinfo=timezone.utc)
    cur.execute(
        "INSERT INTO trs_t_orders (hotel_slug, room, notes, created_at) VALUES (%s, '101', 'no nuts', %s), (%s, '101', 'later guest', %s)",
        (slug, during, slug, during + timedelta(days=20)),
    )
    owner = client_as("owner")
    access = _verified(cur, slug, "access", owner)
    sections = owner.post(f"/hotels/{slug}/trust/dsr/{access['id']}/execute", json={}).json()["result"]["sections"]
    assert sections["t.trs_t_stays"] == 1 and sections["t.trs_t_orders"] == 1
    erase = _verified(cur, slug, "erase", owner)
    result = owner.post(f"/hotels/{slug}/trust/dsr/{erase['id']}/execute", json={"confirm": True}).json()["result"]
    assert result["erased"]["t.trs_t_stays"] == 1 and result["erased"]["t.trs_t_orders"] == 1
    cur.execute("SELECT note FROM trs_t_stays")
    assert [r["note"] for r in cur.fetchall()] == ["theirs"]
    cur.execute("SELECT room, notes FROM trs_t_orders ORDER BY created_at")
    assert [tuple(r.values()) for r in cur.fetchall()] == [("101", "[erased]"), ("101", "later guest")]
