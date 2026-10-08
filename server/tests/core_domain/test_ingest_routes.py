"""core/ingest HTTP routes (permissions, masking by role, shapes) and the
inbound-email path through the Resend webhook and the ingest.process task."""

from __future__ import annotations

import base64

import pytest

from tests.core_domain.conftest import swap_commit_handler
from tests.foundation import resend_mail

from clefs import files, notify
from clefs.core.ingest import inbound, kinds
from clefs.settings import reload_settings

BASE = "/hotels/test-dubai/ingest"
STATEMENT = (
    "Book number,Guest name(s),Check-in,Status,Price,Commission amount,Email\n"
    "5001,Jane Doe,01/10/2025,ok,1000.00,150.00,jd@guest.booking.com\n"
    "Total,,,,1000.00,150.00,\n"
)


@pytest.fixture
def env(monkeypatch):
    def _set(**values: str) -> None:
        for k, v in values.items():
            monkeypatch.setenv(k, v)
        reload_settings()

    yield _set
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def file_id(cur, hotel_uae):
    return files.put_file(cur, hotel_uae.slug, STATEMENT.encode(), filename="s.csv", content_type="text/csv",
                          purpose="ingest.upload").id


@pytest.fixture
def handler():
    with swap_commit_handler("OTA_STATEMENT", lambda cur, batch, rows: {"lines": len(rows)}):
        yield


def test_upload_review_and_commit_by_role(client_as, file_id, handler):
    owner = client_as("owner")
    r = owner.post(f"{BASE}/batches", json={"file_id": file_id, "source_code": "booking_res_statement"})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "REVIEW" and b["row_counts"] == {"OK": 1} and b["gate_status"] == "ok"
    assert b["gate_blocking"] is False and "mapping_suggestion" not in b
    assert owner.post(f"{BASE}/batches", json={"file_id": file_id}).status_code == 200  # same bytes
    assert client_as("front_desk").get(f"{BASE}/batches").status_code == 403
    dept = client_as("dept_head")
    assert dept.get(f"{BASE}/batches").json()[0]["id"] == b["id"]
    assert dept.post(f"{BASE}/batches/{b['id']}/commit").status_code == 403
    done = client_as("finance").post(f"{BASE}/batches/{b['id']}/commit", json={})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "COMMITTED" and done.json()["commit_result"] == {"lines": 1}
    assert client_as("gm").post(f"{BASE}/batches/{b['id']}/commit").json()["code"] == "already_committed"


def test_rows_and_samples_are_masked_by_role(client_as, file_id):
    owner = client_as("owner")
    b = owner.post(f"{BASE}/batches", json={"file_id": file_id, "kind": "OTA_STATEMENT"}).json()
    full = owner.get(f"{BASE}/batches/{b['id']}/rows").json()
    assert full["total"] == 1 and full["rows"][0]["canonical"]["guest_name"] == "Jane Doe"
    assert full["rows"][0]["raw"]["Email"] == "jd@guest.booking.com"  # uncommitted: still staged
    rev = client_as("revenue").get(f"{BASE}/batches/{b['id']}/rows").json()["rows"][0]
    assert rev["canonical"]["guest_name"] is None and rev["raw"]["Guest name(s)"] is None
    assert rev["raw"]["Email"] is None
    fin = client_as("finance").get(f"{BASE}/batches/{b['id']}/rows").json()["rows"][0]
    assert fin["raw"]["Guest name(s)"] == "Jane Doe" and fin["raw"]["Email"] != "jd@guest.booking.com"
    m = client_as("revenue").get(f"{BASE}/batches/{b['id']}/mapping").json()
    assert m["samples"][0]["Guest name(s)"] is None and m["mapping"]["ota_res_id"] == "Book number"
    assert {"name": "ota_res_id", "type": "text", "required": True} in m["fields"]


def test_mapping_edit_gate_override_templates_and_discard(client_as, cur, hotel_uae):
    owner = client_as("owner")
    odd = "Ref,Arr,Gross,Comm\nA1,01/10/2025,100,15\nTotal,,100,20\n"
    fid = files.put_file(cur, hotel_uae.slug, odd.encode(), filename="o.csv", content_type="text/csv",
                         purpose="ingest.upload").id
    b = owner.post(f"{BASE}/batches", json={"file_id": fid, "kind": "OTA_STATEMENT"}).json()
    # "Gross" is a canonical name; "Ref"/"Arr"/"Comm" match no alias, and the
    # AI is off in tests, so a person has to map the rest.
    assert b["status"] == "MAPPING_REQUIRED" and b["mapping"]["gross_minor"] == "Gross"
    put = owner.put(f"{BASE}/batches/{b['id']}/mapping",
                    json={"mapping": {"ota_res_id": "Ref", "check_in": "Arr", "gross_minor": "Gross",
                                      "commission_minor": "Comm"}})
    assert put.status_code == 200 and put.json()["mapping_source"] == "manual"
    assert put.json()["status"] == "REVIEW"
    assert put.json()["gate_status"] == "mismatch" and put.json()["gate_blocking"] is True
    ov = owner.post(f"{BASE}/batches/{b['id']}/gate-override", json={"reason": "OTA credit note"})
    assert ov.status_code == 200 and ov.json()["gate_blocking"] is False
    rows = owner.get(f"{BASE}/batches/{b['id']}/rows").json()["rows"]
    r = owner.patch(f"{BASE}/batches/{b['id']}/rows/{rows[0]['id']}", json={"values": {"commission_minor": "20"}})
    assert r.status_code == 200 and r.json()["row"]["canonical"]["commission_minor"] == 2000
    assert r.json()["batch"]["gate_status"] == "ok"
    assert owner.patch(f"{BASE}/batches/{b['id']}/rows/{rows[0]['id']}", json={}).status_code == 400
    tpls = owner.get(f"{BASE}/templates").json()
    assert len(tpls) == 1 and tpls[0]["origin"] == "manual"
    assert owner.delete(f"{BASE}/templates/{tpls[0]['id']}").json() == {"ok": True}
    assert owner.get(f"{BASE}/templates").json() == []
    assert owner.delete(f"{BASE}/batches/{b['id']}").status_code == 204
    assert owner.get(f"{BASE}/batches/{b['id']}").status_code == 404


def test_sources_and_inbound_address(client_as, cur, hotel_uae):
    body = client_as("finance").get(f"{BASE}/sources").json()
    codes = {s["source_code"] for s in body["sources"]}
    assert "booking_res_statement" in codes and "generic" not in codes
    pms = next(k for k in body["kinds"] if k["kind"] == "PMS_RESERVATIONS")
    assert pms["committable"] is True
    assert client_as("owner").get(f"{BASE}/inbound-address").json()["address"] is None
    cur.execute("UPDATE hotels SET inbound_email_alias = 'testdubai' WHERE slug = %s", (hotel_uae.slug,))
    assert client_as("owner").get(f"{BASE}/inbound-address").json()["address"] == "testdubai@in.clefs.world"


# ─── Inbound email ───


def _mail(to: str, attachments: list, message_id: str = "m-ingest-1", sender: str = "owner@hotel.ae") -> dict:
    return {"From": sender, "FromFull": {"Email": sender, "Name": "Owner"}, "To": to,
            "ToFull": [{"Email": to, "Name": "", "MailboxHash": ""}], "Subject": "October statement",
            "MessageID": message_id, "TextBody": "attached", "HtmlBody": "", "StrippedTextReply": "",
            "Headers": [], "Attachments": attachments}


def _att(name: str, text: str, ctype: str = "text/csv") -> dict:
    data = text.encode()
    return {"Name": name, "Content": base64.b64encode(data).decode(), "ContentType": ctype,
            "ContentLength": len(data)}


def test_forwarded_statement_becomes_a_processed_batch(client, cur, hotel_uae, env, run_jobs):
    env(INBOUND_EMAIL_ENABLED="true", RESEND_WEBHOOK_SECRET=resend_mail.SECRET, INBOUND_EMAIL_DOMAIN="in.clefs.world")
    cur.execute("UPDATE hotels SET inbound_email_alias = 'testdubai' WHERE slug = %s", (hotel_uae.slug,))
    body = _mail("testdubai+ingest@in.clefs.world", [_att("stmt.csv", STATEMENT), _att("logo.png", "x", "image/png")])
    r = resend_mail.post(client, body)
    assert r.status_code == 200 and r.json()["status"] == "handled", r.text
    cur.execute("SELECT * FROM import_batches WHERE hotel_slug = %s", (hotel_uae.slug,))
    [b] = cur.fetchall()
    assert (b["via"], b["status"], b["kind"], b["source_code"]) == ("EMAIL", "UPLOADED", "OTA_STATEMENT",
                                                                   "booking_res_statement")
    # owner@hotel.ae is not a member and the mail carries no SPF/DKIM
    # result: the batch is made, but a person must commit it.
    assert b["inbound_message_id"] is not None and b["auto_commit"] is False
    run_jobs("ingest.process")
    cur.execute("SELECT status, gate_status FROM import_batches WHERE id = %s", (b["id"],))
    assert dict(cur.fetchone()) == {"status": "REVIEW", "gate_status": "ok"}  # alias map: a person reviews
    hint = _mail("testdubai+ingest-pms_reservations@in.clefs.world",
                 [_att("res.csv", "Res No,Arrival,Departure\nR1,2025-10-01,2025-10-02\n")], message_id="m-ingest-2")
    assert resend_mail.post(client, hint).json()["status"] == "handled"
    cur.execute("SELECT kind FROM import_batches WHERE hotel_slug = %s ORDER BY id DESC LIMIT 1", (hotel_uae.slug,))
    assert cur.fetchone()["kind"] == "PMS_RESERVATIONS"
    junk = _mail("testdubai+ingest@in.clefs.world", [_att("notes.csv", "a,b\n1,2\n")], message_id="m-ingest-3")
    assert resend_mail.post(client, junk).json()["status"] == "unhandled"


def test_address_hints_and_ota_senders():
    assert inbound.hint_labels("booking_res_statement") == ("OTA_STATEMENT", "booking_res_statement")
    # A hint naming an alias map is a source (its kind follows); a bare kind
    # with no same-named map is a kind only.
    assert inbound.hint_labels("PMS_FOLIO") == ("PMS_FOLIO", "pms_folio")
    assert inbound.hint_labels("occupancy") == ("OCCUPANCY", None)
    assert inbound.hint_labels("generic") == (None, None) and inbound.hint_labels(None) == (None, None)
    assert inbound.sender_ota("Finance <noreply@finance.booking.com>") == "BOOKING"
    assert inbound.sender_ota("someone@notbooking.com") is None


# ─── Sender trust: who may auto-commit mailed imports ───


def _spf(addr: str) -> dict:
    return {"Name": "Received-SPF", "Value": f'Pass (sender SPF authorized) identity=mailfrom; client-ip=192.0.2.1; '
                                             f'envelope-from="{addr}"; receiver=inbound.resend.com'}


def _dkim(domain: str) -> dict:
    return {"Name": "Authentication-Results", "Value": f"mx.in.clefs.world; dkim=pass header.d={domain} header.s=s1"}


def _email(sender: str, headers: dict[str, str], cur=None) -> notify.InboundEmail:
    return notify.InboundEmail(cur=cur, inbound_id=1, hotel_slug="test-dubai", purpose="ingest", purpose_token=None,
                               ref=None, alias="testdubai", sender=sender, sender_name=None, to="x", subject=None,
                               text=None, html=None, stripped_reply=None, message_id="m", headers=headers)


def _hdrs(*hs: dict) -> dict[str, str]:
    return {h["Name"]: h["Value"] for h in hs}


def test_sender_authentication_needs_a_pass_aligned_with_from(env):
    env(INBOUND_EMAIL_AUTHSERV_IDS="mx.in.clefs.world")  # a relay that stamps every message
    ok = inbound.sender_authenticated
    assert ok(_email("gm@hotel.ae", _hdrs(_spf("bounce@hotel.ae"))))
    assert ok(_email("gm@hotel.ae", _hdrs(_spf("bounce@mail.hotel.ae"))))  # relaxed alignment
    assert ok(_email("Reports <gm@hotel.ae>", _hdrs(_dkim("hotel.ae"))))
    assert ok(_email("gm@hotel.ae", {"Authentication-Results": "mx.in.clefs.world; dmarc=pass header.from=hotel.ae"}))
    # No result, a failing result, or a pass for someone else's domain proves
    # nothing about From: anyone can sign their own mail and forge From.
    assert not ok(_email("gm@hotel.ae", {}))
    assert not ok(_email("gm@hotel.ae", _hdrs(_spf("x@attacker.test"))))
    assert not ok(_email("gm@hotel.ae", _hdrs(_dkim("attacker.test"))))
    assert not ok(_email("gm@hotel.ae", {"Received-SPF": 'Fail identity=mailfrom; envelope-from="gm@hotel.ae"'}))
    assert not ok(_email("gm@hotel.ae", {"Authentication-Results": "mx; dmarc=pass header.from=attacker.test"}))
    assert not ok(_email("gm@hotel.ae", _hdrs(_dkim("hotel.ae.attacker.test"))))
    assert not ok(_email("not-an-address", _hdrs(_spf("bounce@hotel.ae"))))


def _email_list(sender: str, header_list: list[tuple[str, str]]) -> notify.InboundEmail:
    return notify.InboundEmail(cur=None, inbound_id=1, hotel_slug="test-dubai", purpose="ingest", purpose_token=None,
                               ref=None, alias="testdubai", sender=sender, sender_name=None, to="x", subject=None,
                               text=None, html=None, stripped_reply=None, message_id="m", header_list=header_list)


def test_sender_verdicts_the_sender_wrote_prove_nothing(env):
    """Review finding: the attacker appends their own verdict headers below
    the real trace headers. Only a single copy stamped by our verifier counts."""
    ok = inbound.sender_authenticated
    forged_ar = ("Authentication-Results", "x; dmarc=pass header.from=hotel.ae")
    real_fail = ("Received-SPF", "Fail identity=mailfrom; envelope-from=\"x@attacker.test\"; "
                                 "receiver=inbound.resend.com")
    forged_spf = ("Received-SPF", "Pass identity=mailfrom; envelope-from=\"gm@hotel.ae\"; "
                                  "receiver=inbound.resend.com")
    # An authserv-id other than our verifier's is untrusted.
    assert not ok(_email_list("gm@hotel.ae", [real_fail, forged_ar]))
    env(INBOUND_EMAIL_AUTHSERV_IDS="mx.in.clefs.world")
    assert not ok(_email_list("gm@hotel.ae", [real_fail, forged_ar]))  # "x" is not our authserv-id
    # A forged SPF pass beside our verifier's real stamp voids both, in either order.
    assert not ok(_email_list("gm@hotel.ae", [real_fail, forged_spf]))
    assert not ok(_email_list("gm@hotel.ae", [forged_spf, real_fail]))
    # Claiming our authserv-id beside the relay's real stamp voids it too.
    real_ar = ("Authentication-Results", "mx.in.clefs.world; dmarc=fail header.from=hotel.ae")
    fake_ar = ("Authentication-Results", "mx.in.clefs.world; dmarc=pass header.from=hotel.ae")
    assert not ok(_email_list("gm@hotel.ae", [real_ar, fake_ar]))
    assert not ok(_email_list("gm@hotel.ae", [fake_ar, real_ar]))
    # A Received-SPF from another hop (Gmail forwarding) is ignored, not counted.
    other_hop = ("Received-SPF", "pass (google.com: domain of gm@hotel.ae) receiver=mx.google.com; "
                                 "envelope-from=gm@hotel.ae")
    assert ok(_email_list("gm@hotel.ae", [other_hop, ("Received-SPF", forged_spf[1])]))  # one real stamp: counts
    assert not ok(_email_list("gm@hotel.ae", [other_hop]))


def test_trusted_email_senders_are_kept_by_managers(client_as, cur, hotel_uae):
    owner = client_as("owner")
    assert owner.get(f"{BASE}/email-senders").json() == {"email_senders": []}
    r = owner.put(f"{BASE}/email-senders", json={"email_senders": [" Reports@PMS.example ", "@pms.example",
                                                                   "reports@pms.example"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"email_senders": ["reports@pms.example", "@pms.example"]}
    assert owner.get(f"{BASE}/email-senders").json()["email_senders"] == ["reports@pms.example", "@pms.example"]
    assert inbound.sender_vouched(cur, hotel_uae.slug, "Mailer <x@pms.example>")
    assert not inbound.sender_vouched(cur, hotel_uae.slug, "x@pms.example.attacker.test")
    bad = owner.put(f"{BASE}/email-senders", json={"email_senders": ["not an address"]})
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_sender"
    # A trust boundary: finance may run imports but not widen who commits them.
    assert client_as("finance").put(f"{BASE}/email-senders", json={"email_senders": []}).status_code == 403
    cur.execute("SELECT count(*) AS n FROM audit_log WHERE action = 'core.module_settings.update' "
                "AND entity_id = %s", (f"{hotel_uae.slug}:ingest",))
    assert cur.fetchone()["n"] == 1


def test_inactive_members_do_not_vouch(cur, hotel_uae, make_user):
    u = make_user("finance", hotel_uae.slug, email="left@hotel.ae")
    assert inbound.sender_vouched(cur, hotel_uae.slug, "left@hotel.ae")
    cur.execute("UPDATE hotel_memberships SET is_active = FALSE WHERE id = %s", (u["membership_id"],))
    assert not inbound.sender_vouched(cur, hotel_uae.slug, "left@hotel.ae")
    assert not inbound.sender_vouched(cur, hotel_uae.slug, None)
