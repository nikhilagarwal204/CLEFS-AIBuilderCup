"""POST /hotels/{slug}/concierge/kb/test (Wave 1 integration): one dry-run
answer on the eval surface for the knowledge console. Cites [KB:n] tags,
strips them from the text, says whether an entry was in the prompt, files
nothing, and is for managers only."""

from __future__ import annotations

from clefs.modules.concierge import kb
from tests.modules.concierge.test_turns import said


def test_kb_test_answers_from_approved_entries(client_as, cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    e = kb.create_entry(cur, s, {"answer_en": "Wi-Fi is free. Network Clefs-Guest.", "section_key": "wifi"})
    kb.approve_entry(cur, s, e["id"], actor=None)
    pending = kb.create_entry(cur, s, {"answer_en": "Parking is free.", "section_key": "parking"})
    fake_ai.push(said(f"The Wi-Fi is free [KB:{e['id']}]."))
    owner = client_as("owner")
    r = owner.post(f"/hotels/{s}/concierge/kb/test", json={"question": "Is the wifi free?", "entry_id": e["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "The Wi-Fi is free."
    assert "[KB:" not in body["text"] and body["cited"] == [e["id"]]
    assert e["id"] in body["kb_entry_ids"] and body["entry_included"] is True
    fake_ai.push(said("I will check with the front desk."))
    r2 = owner.post(f"/hotels/{s}/concierge/kb/test", json={"question": "Parking?", "entry_id": pending["id"]})
    assert r2.json()["entry_included"] is False
    assert client_as("front_desk").post(f"/hotels/{s}/concierge/kb/test", json={"question": "x"}).status_code == 403
