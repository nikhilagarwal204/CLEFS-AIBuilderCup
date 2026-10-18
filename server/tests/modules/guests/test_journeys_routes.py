"""Journeys HTTP API: console routes and the /g page data (04 spec 3.10)."""

from __future__ import annotations

from clefs.modules.guests.journeys import tokens as jtokens

from tests.modules.guests.test_journeys_support import add_contact, at, event, make_res

ORIGIN = {"Origin": "http://localhost:3000"}


def test_overview_empty_state_and_permissions(cur, hotel_uae, client_as):
    s = hotel_uae.slug
    staff = client_as("staff")
    assert staff.get(f"/hotels/{s}/guests/journeys/overview").status_code == 403
    fd = client_as("front_desk")
    r = fd.get(f"/hotels/{s}/guests/journeys/overview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["all_observing"] is True and set(body["funnel"]) >= {"j1", "j8"}
    assert body["dial"]["j3"]["max_level"] == "auto_within_limits"
    assert fd.post(f"/hotels/{s}/guests/journeys/templates/1/approve", headers=ORIGIN).status_code == 403


def test_owner_approves_a_template_and_it_becomes_sendable_on_email(cur, hotel_uae, client_as):
    s = hotel_uae.slug
    owner = client_as("owner")
    rows = owner.get(f"/hotels/{s}/guests/journeys/templates").json()
    assert len(rows) == 28 and {r["dir"] for r in rows} == {"ltr", "rtl"}
    email_j1 = next(r for r in rows if r["step"] == "j1" and r["channel"] == "email" and r["language"] == "en")
    r = owner.post(f"/hotels/{s}/guests/journeys/templates/{email_j1['id']}/approve", headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert r.json()["sendable"] is True
    wa = next(r for r in rows if r["step"] == "j1" and r["channel"] == "whatsapp" and r["language"] == "en")
    r = owner.post(f"/hotels/{s}/guests/journeys/templates/{wa['id']}/approve", headers=ORIGIN).json()
    assert r["sendable"] is False and r["submission"]["error"] == "not_connected"  # Meta has not approved it


def test_offers_crud_and_approval(cur, hotel_uae, client_as):
    s = hotel_uae.slug
    owner = client_as("owner")
    body = {"step": "j8", "title_en": "10% off your next stay", "discount_pct": "10", "code_prefix": "back",
            "valid_from": "2026-10-01", "valid_to": "2027-03-31"}
    r = owner.post(f"/hotels/{s}/guests/journeys/offers", json=body, headers=ORIGIN)
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    assert r.json()["code_prefix"] == "BACK" and r.json()["approved_at"] is None
    bad = owner.post(f"/hotels/{s}/guests/journeys/offers", json={**body, "title_en": "Leave a review for 10% off"},
                     headers=ORIGIN)
    assert bad.status_code == 422
    assert owner.post(f"/hotels/{s}/guests/journeys/offers/{oid}/approve", headers=ORIGIN).json()["approved_at"]
    edited = owner.put(f"/hotels/{s}/guests/journeys/offers/{oid}", json={**body, "discount_pct": "15"}, headers=ORIGIN)
    assert edited.json()["approved_at"] is None  # an edit needs a fresh approval
    assert owner.delete(f"/hotels/{s}/guests/journeys/offers/{oid}", headers=ORIGIN).json() == {"ok": True}


def test_settings_narrow_the_marketing_window_only(cur, hotel_uae, client_as):
    s = hotel_uae.slug
    owner = client_as("owner")
    url = f"/hotels/{s}/guests/journeys/settings"
    assert owner.get(url).json()["marketing_window"] == {"start": "09:00", "end": "18:00"}
    assert owner.put(url, json={"marketing_window": {"start": "10:00", "end": "17:00"}}, headers=ORIGIN).status_code == 200
    r = owner.put(url, json={"marketing_window": {"start": "08:00", "end": "18:00"}}, headers=ORIGIN)
    assert r.status_code == 422
    r = owner.put(url, json={"review_links": {"google_url": "https://g.page.evil.example/x"}}, headers=ORIGIN)
    assert r.status_code == 422
    r = owner.put(url, json={"wa_billing_attested": True}, headers=ORIGIN)
    assert r.json()["wa_billing_attested_at"] is not None


def test_timeline_lists_steps_and_prefs(cur, hotel_uae, clock, client_as):
    s = hotel_uae.slug
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s)
    event(cur, s, rid, "created")
    cur.execute("INSERT INTO gst_stay_prefs (hotel_slug, reservation_id, requests, purge_after) VALUES (%s, %s, %s, now())",
                (s, rid, "anniversary cake"))
    body = client_as("owner").get(f"/hotels/{s}/guests/journeys/reservations/{rid}/timeline").json()
    assert {p["step"] for p in body["plans"]} >= {"j1", "j6_pre"} and body["prefs"]["requests"] == "anniversary cake"


def test_j1_page_for_an_ota_booking_has_no_marketing_boxes(cur, hotel_uae, clock, client):
    s = hotel_uae.slug
    clock.freeze(at(2026, 9, 1, 10, 5))
    ota = make_res(cur, s, code="BOOKING")
    add_contact(cur, s, ota, "zz@guest.booking.com", kind="email", provenance="ota_alias", alias_domain="guest.booking.com")
    event(cur, s, ota, "created")
    direct = make_res(cur, s)
    event(cur, s, direct, "created")
    t_ota = jtokens.mint(cur, s, ota, "prefs", expires_at=at(2026, 10, 20, 23, 59))
    t_dir = jtokens.mint(cur, s, direct, "prefs", expires_at=at(2026, 10, 20, 23, 59))
    page = client.get(f"/public/journeys/{t_ota}").json()
    assert page["marketing_boxes"] is False and set(page["consent_texts"]) == {"transfer_abroad", "whatsapp_updates"}
    assert "Test Hotel Dubai" in page["consent_texts"]["whatsapp_updates"]["body"]
    assert client.get(f"/public/journeys/{t_dir}").json()["marketing_boxes"] is True
    r = client.post(f"/public/journeys/{t_ota}/prefs", json={
        "arrival_time": "22:00", "contact": {"kind": "email", "value": "own@example.com"},
        "consents": {"transfer_abroad": True, "marketing_email": True}})
    assert r.status_code == 422 and r.json()["code"] == "consent_not_allowed"
    r = client.post(f"/public/journeys/{t_ota}/prefs", json={
        "arrival_time": "22:00", "contact": {"kind": "email", "value": "own@example.com"},
        "consents": {"transfer_abroad": True, "whatsapp_updates": True}})
    assert r.status_code == 200 and r.json() == {"ok": True, "contact_saved": True}
    cur.execute("SELECT provenance FROM gst_contact_points WHERE hotel_slug = %s AND capture_source = 'j1_form'", (s,))
    assert cur.fetchone()["provenance"] == "guest_stay"
    feedback_token = jtokens.mint(cur, s, ota, "feedback", expires_at=at(2026, 11, 1))
    assert client.post(f"/public/journeys/{feedback_token}/prefs", json={}).status_code == 404  # purpose enforced
    assert client.get("/public/journeys/nonsense").status_code == 404
