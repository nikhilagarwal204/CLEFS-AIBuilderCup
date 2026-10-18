"""KB console routes, legacy import and the unknowns queue (03 spec 2.5)."""

from __future__ import annotations

from clefs.modules.concierge import api, kb

BASE = "/hotels/test-dubai/concierge"


def test_console_crud_and_approval_flow(client_as, hotel_uae):
    gm = client_as("gm")
    r = gm.post(f"{BASE}/kb/entries", json={"section_key": "pool", "question_en": "Pool hours?", "answer_en": "7:00 to 21:00."})
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    assert r.json()["status"] == "pending"
    assert gm.post(f"{BASE}/kb/entries/{eid}/approve").json()["status"] == "approved"
    r = gm.patch(f"{BASE}/kb/entries/{eid}", json={"answer_ar": "من 7:00 إلى 21:00."})
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["version"] == 2
    r = gm.patch(f"{BASE}/kb/entries/{eid}", json={"answer_en": "7:00 to 22:00.", "approve": True})
    assert r.json()["status"] == "approved"
    revs = gm.get(f"{BASE}/kb/entries/{eid}/revisions").json()
    assert [x["change"] for x in revs][-1] == "approved"
    overview = gm.get(f"{BASE}/kb").json()
    assert "pool" not in overview["missing_required"] and "wifi" in overview["missing_required"]


def test_front_desk_can_read_but_not_approve(client_as, hotel_uae):
    fd = client_as("front_desk")
    assert fd.get(f"{BASE}/kb/entries").status_code == 200
    assert fd.post(f"{BASE}/kb/entries", json={"answer_en": "x"}).status_code == 403


def test_anonymous_gets_401(client, hotel_uae):
    assert client.get(f"{BASE}/kb").status_code == 401


def test_prompt_preview_is_manage_only(client_as, hotel_uae):
    assert client_as("front_desk").get(f"{BASE}/prompt-preview").status_code == 403
    body = client_as("gm").get(f"{BASE}/prompt-preview", params={"surface": "phone", "language": "ar"}).json()
    assert body["surface"] == "phone" and body["prompt_version"] and "998" in body["instructions"]


def test_legacy_import_splits_then_approval_flips_mode(client_as, cur, hotel_uae, run_jobs):
    cur.execute(
        "UPDATE hotels SET knowledge_base = %s WHERE slug = %s",
        ("Wi-Fi password is sunrise.\n\nBreakfast is 7 to 11 in Saffron.\n\nNo smoking indoors.", hotel_uae.slug),
    )
    assert kb.kb_mode(cur, hotel_uae.slug) == "legacy_text"
    gm = client_as("gm")
    r = gm.post(f"{BASE}/kb/imports", json={"source": "legacy_text"})
    assert r.status_code == 202, r.text
    run_jobs(only=["concierge.kb_import"])
    imp = gm.get(f"{BASE}/kb/imports").json()[0]
    assert imp["status"] == "done" and imp["entries_created"] == 3 and imp["method"] == "paragraphs"
    pending = gm.get(f"{BASE}/kb/entries", params={"status": "pending"}).json()
    assert {e["answer_en"] for e in pending} >= {"No smoking indoors."}
    sections = {s["id"]: s["key"] for s in gm.get(f"{BASE}/kb/sections").json()}
    assert {sections[e["section_id"]] for e in pending} == {"wifi", "breakfast", "smoking"}
    r = gm.post(f"{BASE}/kb/imports/{imp['id']}/approve")
    assert r.status_code == 200 and r.json()["approved_entries"] == 3
    assert kb.kb_mode(cur, hotel_uae.slug) == "structured"
    assert len(api.kb_facts(cur, hotel_uae.slug)) == 3


def test_ai_import_path(cur, hotel_uae, fake_ai):
    fake_ai.push(
        {
            "entries": [
                {"section_key": "checkout", "kind": "fact", "question_en": "When is check-in?", "answer_en": "Check-in is from 3 pm.",
                 "question_ar": None, "answer_ar": None, "fact_key": "checkin_time", "fact_value": "3 pm"},
                {"section_key": "parking", "kind": "fact", "question_en": "Parking?", "answer_en": "Parking is free.",
                 "question_ar": None, "answer_ar": None, "fact_key": "parking", "fact_value": "sometimes"},
            ]
        }
    )
    imp = kb.start_import(cur, hotel_uae.slug, source="text", text="Check-in 3 pm. Parking free.")
    out = kb.run_import(cur, hotel_uae.slug, imp["id"], text="Check-in 3 pm. Parking free.")
    assert out["method"] == "ai" and out["entries_created"] == 2
    rows = kb.list_entries(cur, hotel_uae.slug, status="pending")
    by_q = {r["question_en"]: r for r in rows}
    assert by_q["When is check-in?"]["fact_value"] == "15:00"
    assert by_q["Parking?"]["fact_key"] is None, "a bad fact value is kept as plain text"


def test_import_needs_text(client_as, hotel_uae):
    gm = client_as("gm")
    assert gm.post(f"{BASE}/kb/imports", json={"source": "legacy_text"}).status_code == 409
    assert gm.post(f"{BASE}/kb/imports", json={"source": "text", "text": "  "}).status_code == 400


def test_set_mode_keeps_other_settings(client_as, cur, hotel_uae):
    from clefs.core.hotels import set_module_settings

    set_module_settings(cur, hotel_uae.slug, "concierge", {"legacy_qr_personalization": True}, actor=None)
    r = client_as("owner").put(f"{BASE}/kb/mode", json={"kb_mode": "legacy_text"})
    assert r.json() == {"kb_mode": "legacy_text"}
    s = kb.settings(cur, hotel_uae.slug)
    assert s.kb_mode == "legacy_text" and s.legacy_qr_personalization is True


# ─── Unknowns ───


def test_unknowns_cluster_raise_gap_and_answer(client_as, cur, hotel_uae):
    for text in ("Do you have a helipad?", "Is there a helipad?", "helipad??", "Do you have a helipad", "Where is the helipad?"):
        kb.record_unknown(cur, hotel_uae.slug, text=text, language="en")
    kb.record_unknown(cur, hotel_uae.slug, text="Can I bring my falcon?", language="en")
    gm = client_as("gm")
    rows = gm.get(f"{BASE}/kb/unknowns").json()
    assert rows[0]["count"] == 5 and len(rows) == 2
    cur.execute("SELECT status, title FROM signals WHERE hotel_slug = %s AND kind = 'concierge.kb_gap'", (hotel_uae.slug,))
    sig = cur.fetchone()
    assert sig and sig["status"] == "open" and "helipad" in sig["title"].lower()
    r = gm.post(f"{BASE}/kb/unknowns/{rows[0]['id']}/answer", json={"answer_en": "We have no helipad.", "approve": True})
    assert r.status_code == 201 and r.json()["status"] == "approved"
    assert len(gm.get(f"{BASE}/kb/unknowns").json()) == 1
    cur.execute("SELECT status FROM signals WHERE hotel_slug = %s AND kind = 'concierge.kb_gap'", (hotel_uae.slug,))
    assert cur.fetchone()["status"] == "resolved"
    assert gm.post(f"{BASE}/kb/unknowns/{rows[1]['id']}/dismiss").json()["status"] == "dismissed"


def test_propose_and_status_via_api(cur, hotel_uae):
    eid = api.propose_kb_entry(
        cur, hotel_uae.slug, question_en="Check-out time?", answer_en="12:00", fact_key="checkout_time", source="visibility", actor=None
    )
    assert api.kb_entry_status(cur, hotel_uae.slug, eid)["status"] == "pending"
    kb.approve_entry(cur, hotel_uae.slug, eid, actor=None)
    st = api.kb_entry_status(cur, hotel_uae.slug, eid)
    assert st["status"] == "approved" and st["approved_at"] is not None
    assert api.kb_facts(cur, hotel_uae.slug, keyed_only=True)[0]["value"] == "12:00"
