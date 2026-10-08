"""Console routes under /hotels/{slug}/brief/wa: opt-in capture, the template
list and submit, and the hotel's WhatsApp Business account."""

from __future__ import annotations


def test_contacts_opt_in_capture(client_as, cur, hotel_uae, frozen):
    owner = client_as("owner", name="Omar Owner")
    base = f"/hotels/{hotel_uae.slug}/brief/wa"
    rows = owner.get(f"{base}/contacts").json()
    me = next(r for r in rows if r["user_id"] == owner.user["id"])
    assert me["state"] == "no_phone" and me["phone_masked"] is None
    r = owner.post(f"{base}/contacts", json={"opted_in": True})
    assert r.status_code == 400 and r.json()["code"] == "no_phone"
    # A typed number is never stored (test_whatsapp_verify proves one from WhatsApp).
    r = owner.post(f"{base}/contacts", json={"phone": "+971 50 123 4567", "opted_in": True})
    assert r.status_code == 409 and r.json()["code"] == "phone_unverified"
    cur.execute("UPDATE users SET phone_e164 = %s WHERE id = %s", ("+971501234567", owner.user["id"]))
    r = owner.post(f"{base}/contacts", json={"phone": "+971 50 123 4567", "opted_in": True})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "opted_in" and r.json()["opt_in_method"] == "console"
    assert r.json()["phone_masked"].endswith("567") and "1234" not in r.json()["phone_masked"]
    r = owner.post(f"{base}/contacts", json={"phone": "+971509999999"})
    assert r.status_code == 409 and r.json()["code"] == "phone_mismatch"
    r = owner.post(f"{base}/contacts", json={"opted_in": False})
    assert r.json()["state"] == "opted_out"


def test_only_managers_record_someone_elses_choice(client_as, hotel_uae, make_user):
    base = f"/hotels/{hotel_uae.slug}/brief/wa"
    fin = client_as("finance")
    other = make_user("gm", hotel_uae.slug)
    assert fin.post(f"{base}/contacts", json={"user_id": other["id"], "opted_in": False}).status_code == 403
    gm = client_as("gm")
    r = gm.post(f"{base}/contacts", json={"user_id": other["id"], "opted_in": False})
    assert r.status_code == 200 and r.json()["state"] == "opted_out"
    assert client_as("front_desk").get(f"{base}/contacts").status_code == 403


def test_templates_and_account(client_as, hotel_uae):
    owner = client_as("owner")
    base = f"/hotels/{hotel_uae.slug}/brief/wa"
    body = owner.get(f"{base}/templates").json()
    assert {t["name"] for t in body["brief_templates"]} == {"owner_brief_v1", "weekly_report_v1"}
    r = owner.post(f"{base}/templates/submit", json={"name": "owner_brief_v1", "scope": "platform"})
    assert r.status_code == 403  # platform templates are Clefs's
    r = owner.post(f"{base}/templates/submit", json={"name": "stay_note", "components": [
        {"type": "BODY", "text": "Your room is ready."}]})
    assert r.status_code == 409 and r.json()["code"] == "not_connected"
    assert owner.get(f"{base}/account").json() == {"account": None}
    r = owner.put(f"{base}/account", json={"waba_id": "123456", "phone_number_id": "777", "billing_ready": True})
    assert r.status_code == 200 and r.json()["account"]["waba_id"] == "123456"
    r = owner.post(f"{base}/templates/submit", json={"name": "stay_note", "components": [
        {"type": "BODY", "text": "Your room is ready."}]})
    assert r.status_code == 200 and r.json()["status"] == "PENDING"
    names = {t["name"]: t["scope"] for t in owner.get(f"{base}/templates").json()["templates"]}
    assert names["stay_note"] == "hotel"
    assert client_as("finance").put(f"{base}/account", json={"waba_id": "1"}).status_code == 403


def test_platform_submit_by_clefs_admin(client_as, hotel_uae):
    admin = client_as("owner", admin=True)
    r = admin.post(f"/hotels/{hotel_uae.slug}/brief/wa/templates/submit",
                   json={"name": "owner_brief_v1", "scope": "platform"})
    # No platform WABA configured in tests: the door says so instead of guessing.
    assert r.status_code == 409 and r.json()["code"] == "not_configured"
