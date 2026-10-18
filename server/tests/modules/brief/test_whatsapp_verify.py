"""A WhatsApp number is proven, never typed in (security-privacy-1).

users.phone_e164 is a global identity: magic links are minted by it and
inbound WhatsApp senders map to a user by it. Brief must never store one on
a manager's say-so (a GM at hotel A could otherwise bind their own phone to
the owner's account and sign in as them at hotel B), and a member's own
number is stored only after "CLEFS <code>" arrives from that WhatsApp."""

from __future__ import annotations

import itertools

import pytest

from clefs import notify
from clefs.modules.brief import whatsapp

PLATFORM = "PNID-PLATFORM"
MINE = "+971501234567"
OTHER = "+971509999999"
_ids = itertools.count(1)


@pytest.fixture
def wa_on(monkeypatch, outbox):
    from clefs.settings import reload_settings

    monkeypatch.setenv("WA_PLATFORM_PHONE_NUMBER_ID", PLATFORM)
    monkeypatch.setenv("NOTIFY_MODE", "capture")
    reload_settings()
    yield outbox
    monkeypatch.undo()
    reload_settings()


def text_from(sender: str, body: str) -> dict:
    """One inbound text through the foundation's real webhook dispatch."""
    from clefs.core import clock

    m = {"from": sender.lstrip("+"), "id": f"wamid.verify.{next(_ids)}",
         "timestamp": str(int(clock.now().timestamp())), "type": "text", "text": {"body": body}}
    return notify.process_whatsapp({"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{
        "field": "messages", "value": {"messaging_product": "whatsapp", "metadata": {"phone_number_id": PLATFORM},
                                       "messages": [m]}}]}]})


def phone_of(cur, user_id: int) -> str | None:
    cur.execute("SELECT phone_e164 FROM users WHERE id = %s", (user_id,))
    return cur.fetchone()["phone_e164"]


# ─── A manager never binds a number ───


def test_manager_cannot_attach_a_phone_to_another_members_account(cur, client_as, hotel_uae, make_user):
    base = f"/hotels/{hotel_uae.slug}/brief/wa"
    owner = make_user("owner", hotel_uae.slug)
    gm = client_as("gm")
    r = gm.post(f"{base}/contacts", json={"user_id": owner["id"], "phone": OTHER, "opted_in": True})
    assert r.status_code == 409 and r.json()["code"] == "no_phone"
    assert phone_of(cur, owner["id"]) is None
    # set_opt_in itself never writes the global column, whoever calls it.
    with pytest.raises(Exception) as err:
        whatsapp.set_opt_in(cur, hotel_uae.slug, owner["id"], opted_in=True, method="console", actor=None, phone=OTHER)
    assert getattr(err.value, "code", None) == "phone_unverified"
    assert phone_of(cur, owner["id"]) is None
    cur.execute("SELECT count(*) AS n FROM brf_wa_contacts WHERE user_id = %s", (owner["id"],))
    assert cur.fetchone()["n"] == 0
    # A manager may still record an opt-out for them (no number involved).
    r = gm.post(f"{base}/contacts", json={"user_id": owner["id"], "opted_in": False})
    assert r.status_code == 200 and r.json()["state"] == "opted_out"


def test_verify_is_only_for_the_callers_own_account(cur, client_as, hotel_uae, make_user):
    base = f"/hotels/{hotel_uae.slug}/brief/wa"
    owner = make_user("owner", hotel_uae.slug)
    gm = client_as("gm")
    # The body has no user_id: an extra one is ignored, the code is for the caller.
    r = gm.post(f"{base}/contacts/verify", json={"phone": OTHER, "user_id": owner["id"]})
    assert r.status_code == 200, r.text
    cur.execute("SELECT user_id FROM brf_phone_verifications ORDER BY id DESC LIMIT 1")
    assert cur.fetchone()["user_id"] == gm.user["id"]
    assert phone_of(cur, owner["id"]) is None


# ─── The member proves their own number ───


def test_code_sent_from_the_number_stores_it_and_opts_in(cur, client_as, hotel_uae, wa_on, frozen):
    base = f"/hotels/{hotel_uae.slug}/brief/wa"
    me = client_as("finance", name="Fatima Finance")
    r = me.post(f"{base}/contacts", json={"phone": MINE, "opted_in": True})
    assert r.status_code == 409 and r.json()["code"] == "phone_unverified"
    assert phone_of(cur, me.user["id"]) is None
    r = me.post(f"{base}/contacts/verify", json={"phone": "+971 50 123 4567"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending" and body["message"] == f"CLEFS {body['code']}" and len(body["code"]) == 6
    assert body["phone_masked"].endswith("567") and "1234" not in body["phone_masked"]
    cur.execute("SELECT code_hash FROM brf_phone_verifications WHERE user_id = %s", (me.user["id"],))
    assert body["code"] not in cur.fetchone()["code_hash"]  # only the hash is stored
    assert me.get(f"{base}/contacts/verify").json()["status"] == "pending"
    assert "code" not in me.get(f"{base}/contacts/verify").json()

    # The code from ANOTHER phone proves nothing and binds nothing.
    text_from(OTHER, f"CLEFS {body['code']}")
    assert phone_of(cur, me.user["id"]) is None
    # From the number itself it does.
    text_from(MINE, f"clefs {body['code']}")
    assert phone_of(cur, me.user["id"]) == MINE
    assert me.get(f"{base}/contacts/verify").json()["status"] == "verified"
    row = next(c for c in me.get(f"{base}/contacts").json() if c["user_id"] == me.user["id"])
    assert row["state"] == "opted_in" and row["opt_in_method"] == "whatsapp_verify"
    cur.execute("SELECT action FROM audit_log WHERE action LIKE 'brief.wa.verify_%%' ORDER BY id")
    assert [r["action"] for r in cur.fetchall()] == ["brief.wa.verify_start", "brief.wa.verify_confirm"]
    # Now the number on the account confirms, a different one is a mismatch.
    assert me.post(f"{base}/contacts", json={"phone": MINE, "opted_in": True}).status_code == 200
    r = me.post(f"{base}/contacts", json={"phone": OTHER})
    assert r.status_code == 409 and r.json()["code"] == "phone_mismatch"
    r = me.post(f"{base}/contacts/verify", json={"phone": MINE})
    assert r.status_code == 409 and r.json()["code"] == "already_verified"


def test_wrong_codes_count_and_five_end_the_request(cur, client_as, hotel_uae, wa_on, frozen):
    base = f"/hotels/{hotel_uae.slug}/brief/wa"
    me = client_as("owner")
    code = me.post(f"{base}/contacts/verify", json={"phone": MINE}).json()["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(whatsapp.VERIFY_MAX_ATTEMPTS):
        text_from(MINE, f"CLEFS {wrong}")
    text_from(MINE, f"CLEFS {code}")  # too late: the request is over
    assert phone_of(cur, me.user["id"]) is None
    assert me.get(f"{base}/contacts/verify").json()["status"] == "cancelled"


def test_taken_numbers_and_expiry(cur, client_as, hotel_uae, make_user, wa_on, frozen):
    base = f"/hotels/{hotel_uae.slug}/brief/wa"
    holder = make_user("gm", hotel_uae.slug)
    cur.execute("UPDATE users SET phone_e164 = %s WHERE id = %s", (OTHER, holder["id"]))
    me = client_as("owner")
    r = me.post(f"{base}/contacts/verify", json={"phone": OTHER})
    assert r.status_code == 409 and r.json()["code"] == "phone_taken"
    code = me.post(f"{base}/contacts/verify", json={"phone": MINE}).json()["code"]
    frozen.advance(minutes=31)  # VERIFY_TTL is 30 minutes
    text_from(MINE, f"CLEFS {code}")
    assert phone_of(cur, me.user["id"]) is None
    assert me.get(f"{base}/contacts/verify").json()["status"] == "expired"
