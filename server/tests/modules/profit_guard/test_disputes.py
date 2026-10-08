"""Dispute packets end to end (02 spec 3.7): create, the four outputs,
owner/finance approval, submission with the invoiced-commission snapshot,
auto-verified credit closing the packet and its pg.dispute action, manual
outcomes, the follow-up job and the brief's packet door."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from clefs.core import actions
from clefs.modules.profit_guard import disputes, economics, ledger, tax_checks
from tests.foundation.route_walk import iter_routes
from tests.modules.profit_guard.fixtures import pg_data as pg
from tests.modules.profit_guard.test_ledger import credit_note, finding, get

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
ROUTERS = (ledger.router, disputes.router, tax_checks.router, economics.router)


@pytest.fixture(scope="module")
def app():
    from clefs.app import create_app

    a = create_app()
    have = {getattr(r, "path", "") for r in iter_routes(a.routes)}
    for r in ROUTERS:
        # Mounted here until module.py lists these routers (lane FOUNDATION-REQUEST).
        if not any(getattr(x, "path", "") in have for x in iter_routes(r.routes)):
            a.include_router(r)
    return a


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


def setup_findings(cur, slug, n=2, *, ota="BOOKING"):
    ids = []
    for i in range(n):
        ota_id = f"40000000{20 + i}"
        [line] = pg.statement(cur, slug, [{"ota_res_id": ota_id, "commission_minor": 8470, "guest_name": "AL MANSOURI/AHMED"}],
                              doc_no=f"INV-{i}", ota=ota)
        ids.append(finding(cur, slug, ota_id, amount=8470, doc=f"INV-{i}", line_id=line, ota=ota,
                           deadline=NOW + timedelta(hours=40 + i)))
    return ids


def packet_row(cur, pid):
    cur.execute("SELECT * FROM pfg_dispute_packets WHERE id = %s", (pid,))
    return dict(cur.fetchone())


def action_row(cur, aid):
    cur.execute("SELECT * FROM actions WHERE id = %s", (aid,))
    return dict(cur.fetchone())


# ─── Create and render ───


def test_create_packet_outputs_signal_and_action(cur, hotel_uae):
    s = hotel_uae.slug
    ids = setup_findings(cur, s)
    p = disputes.create_packet(cur, s, ids)
    assert p["status"] == "DRAFT" and p["total_minor"] == 16940 and sorted(p["finding_ids"]) == ids
    assert len(p["extranet_text"]) <= 1000 and "Reservation 4000000020" in p["extranet_text"]
    assert p["extranet_text"].endswith("please issue a credit note.")
    assert p["email_subject"].startswith("Commission dispute Test Hotel Dubai") and "AED 169.40" in p["email_subject"]
    assert p["html_file_id"] and p["pdf_file_id"]
    cur.execute("SELECT content_type FROM files WHERE id = %s", (p["pdf_file_id"],))
    assert cur.fetchone()["content_type"] == "application/pdf"
    cur.execute("SELECT * FROM signals WHERE hotel_slug = %s AND dedupe_key = %s", (s, f"pg:dispute:{p['id']}:approve"))
    sig = dict(cur.fetchone())
    assert sig["kind"] == "pg.dispute.ready_for_approval" and sig["section"] == "money_available"
    assert sig["due_at"] == NOW + timedelta(hours=40)
    act = action_row(cur, p["action_id"])
    assert act["type"] == "pg.dispute" and act["status"] == "proposed" and act["signal_id"] == sig["id"]
    assert {get(cur, i)["dispute_packet_id"] for i in ids} == {p["id"]}


def test_items_carry_clause_calc_and_guest(cur, hotel_uae):
    s = hotel_uae.slug
    [fid] = setup_findings(cur, s, 1)
    items = disputes.packet_items(cur, hotel_uae, [fid])
    it = items[0]
    assert it["booking_ref"] == "4000000020" and it["guest_name"] == "AL MANSOURI/AHMED"
    assert it["clause_ref"] == "GDT v2601 2.3.2(ii)" and it["calc"][0]["label"] == "Commission"
    html = disputes.render_html(hotel_uae, {"id": 1, "ota_code": "BOOKING", "currency": "AED", "total_minor": 8470,
                                            "status": "DRAFT", "extranet_text": "x"}, items)
    assert "awaiting owner or finance approval" in html and "<script" not in html


def test_create_refuses_bad_selections(cur, hotel_uae):
    s = hotel_uae.slug
    a = setup_findings(cur, s, 1)
    b = setup_findings(cur, s, 1, ota="AGODA")
    c = finding(cur, s, "4000000099", klass="PREVENTABLE", detector="L6", doc="2026-09")
    for ids, code in ((a + b, "mixed_ota"), ([c], "not_recoverable"), ([], "no_findings")):
        with pytest.raises(Exception) as e:
            disputes.create_packet(cur, s, ids)
        assert getattr(e.value, "code", None) == code
    disputes.create_packet(cur, s, a)
    with pytest.raises(Exception) as e:
        disputes.create_packet(cur, s, a)
    assert e.value.code == "already_in_packet"


def test_extranet_text_stays_under_the_limit_for_many_bookings(cur, hotel_uae):
    items = [{"id": i, "detector": "L3", "booking_ref": f"40000{i:05d}", "stay_date": None, "clause_ref": "GDT v2601",
              "amount_minor": 8470, "currency": "AED"} for i in range(60)]
    text = disputes.extranet_text(hotel_uae, "AGODA", items, 60 * 8470, "AED")
    assert len(text) <= 1000 and "more bookings" in text and text.endswith("correct it on the next invoice.")


# ─── Lifecycle over HTTP ───


def test_approve_submit_credit_verifies_the_action(cur, client_as, hotel_uae, clock):
    s = hotel_uae.slug
    ids = setup_findings(cur, s)
    gm, owner = client_as("gm"), client_as("owner")
    r = gm.post(f"/hotels/{s}/profit-guard/disputes", json={"finding_ids": ids})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["can_approve"] is False and r.json()["items"][0]["amount"] == "84.70"
    r = gm.post(f"/hotels/{s}/profit-guard/disputes/{pid}/approve")
    assert r.status_code == 403 and r.json()["code"] == "approval_role_required"
    r = owner.post(f"/hotels/{s}/profit-guard/disputes/{pid}/submitted", json={})
    assert r.status_code == 409 and r.json()["code"] == "not_approved"
    r = owner.post(f"/hotels/{s}/profit-guard/disputes/{pid}/approve")
    assert r.status_code == 200 and r.json()["status"] == "APPROVED" and r.json()["approved_by_name"]
    assert {get(cur, i)["status"] for i in ids} == {"ACCEPTED"}
    p = packet_row(cur, pid)
    act = action_row(cur, p["action_id"])
    assert act["status"] == "approved" and act["subtype"] == "dispute"
    r = owner.post(f"/hotels/{s}/profit-guard/disputes/{pid}/submitted", json={"channel": "extranet",
                                                                               "reference": "BK-778"})
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED"
    assert {get(cur, i)["status"] for i in ids} == {"DISPUTED"}
    assert ledger._snapshot(cur, [ids[0]]) == 8470          # what the OTA invoiced when the dispute went out
    assert action_row(cur, p["action_id"])["status"] == "awaiting_counterparty"
    assert packet_row(cur, pid)["follow_up_at"] > NOW + timedelta(days=13)
    for i, ota_id in enumerate(("4000000020", "4000000021")):
        credit_note(cur, s, ota_id, 8470, doc_no=f"CN-{i}")
    out = ledger.auto_verify(cur, s)
    assert sorted(out["recovered"]) == ids and out["packets"] == [pid]
    assert packet_row(cur, pid)["status"] == "CREDITED"
    act = action_row(cur, p["action_id"])
    assert act["status"] == "verified" and act["outcome_minor"] == 16940


def test_manual_partial_then_rejected_outcomes(cur, client_as, hotel_uae):
    s = hotel_uae.slug
    ids = setup_findings(cur, s)
    fin = client_as("finance")
    pid = fin.post(f"/hotels/{s}/profit-guard/disputes", json={"finding_ids": ids}).json()["id"]
    assert fin.post(f"/hotels/{s}/profit-guard/disputes/{pid}/approve").status_code == 200
    assert fin.post(f"/hotels/{s}/profit-guard/disputes/{pid}/submitted", json={}).status_code == 200
    r = fin.post(f"/hotels/{s}/profit-guard/disputes/{pid}/outcome", json={"outcome": "PARTIAL", "amount": "100.00"})
    assert r.status_code == 200 and r.json()["status"] == "PARTIAL", r.text
    first, second = get(cur, ids[0]), get(cur, ids[1])
    assert first["status"] == "RECOVERED" and second["recovered_minor"] == 10000 - 8470
    r = fin.post(f"/hotels/{s}/profit-guard/disputes/{pid}/outcome", json={"outcome": "REJECTED"})
    assert r.status_code == 200 and get(cur, ids[1])["status"] == "REJECTED"
    assert r.json()["status"] == "PARTIAL"                     # one finding was credited


def test_rejected_packet_moves_the_action(cur, client_as, hotel_uae):
    s = hotel_uae.slug
    ids = setup_findings(cur, s, 1)
    owner = client_as("owner")
    pid = owner.post(f"/hotels/{s}/profit-guard/disputes", json={"finding_ids": ids}).json()["id"]
    owner.post(f"/hotels/{s}/profit-guard/disputes/{pid}/approve")
    owner.post(f"/hotels/{s}/profit-guard/disputes/{pid}/submitted", json={})
    r = owner.post(f"/hotels/{s}/profit-guard/disputes/{pid}/outcome", json={"outcome": "REJECTED", "note": "no"})
    assert r.json()["status"] == "REJECTED"
    assert action_row(cur, packet_row(cur, pid)["action_id"])["status"] == "rejected_by_counterparty"


def test_print_pdf_and_list_routes(cur, client_as, hotel_uae):
    s = hotel_uae.slug
    ids = setup_findings(cur, s, 1)
    fin = client_as("finance")
    pid = fin.post(f"/hotels/{s}/profit-guard/disputes", json={"finding_ids": ids}).json()["id"]
    r = fin.get(f"/hotels/{s}/profit-guard/disputes/{pid}/print")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and "@media print" in r.text
    r = fin.get(f"/hotels/{s}/profit-guard/disputes/{pid}.pdf")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    lst = fin.get(f"/hotels/{s}/profit-guard/disputes?status=draft").json()
    assert [p["id"] for p in lst] == [pid]
    assert client_as("revenue").get(f"/hotels/{s}/profit-guard/disputes/{pid}").status_code == 403


# ─── Jobs and the brief's door ───


def test_follow_up_reminds_every_ten_business_days(cur, hotel_uae, client_as, clock):
    s = hotel_uae.slug
    ids = setup_findings(cur, s, 1)
    owner = client_as("owner")
    pid = owner.post(f"/hotels/{s}/profit-guard/disputes", json={"finding_ids": ids}).json()["id"]
    owner.post(f"/hotels/{s}/profit-guard/disputes/{pid}/approve")
    owner.post(f"/hotels/{s}/profit-guard/disputes/{pid}/submitted", json={})
    assert disputes.follow_up(cur, hotel_uae)["reminded"] == []
    clock.freeze(NOW + timedelta(days=15))
    assert disputes.follow_up(cur, hotel_uae)["reminded"] == [pid]
    cur.execute("SELECT kind FROM signals WHERE hotel_slug = %s AND dedupe_key = %s", (s, f"pg:dispute:{pid}:follow_up"))
    assert cur.fetchone()["kind"] == "pg.dispute.follow_up"
    assert disputes.follow_up(cur, hotel_uae)["reminded"] == []       # the next date moved on
    assert action_row(cur, packet_row(cur, pid)["action_id"])["reminders_sent"] == 1


def test_brief_approval_is_adopted_and_packet_door(cur, hotel_uae, make_user):
    s = hotel_uae.slug
    ids = setup_findings(cur, s, 1)
    p = disputes.create_packet(cur, s, ids)
    user = make_user("finance", s)
    cur.execute("UPDATE actions SET status = 'approved', subtype = 'dispute', decided_by = %s, decided_at = %s "
                "WHERE id = %s", (user["id"], NOW, p["action_id"]))
    assert disputes.follow_up(cur, hotel_uae)["adopted"] == [p["id"]]
    assert packet_row(cur, p["id"])["status"] == "APPROVED"
    door = disputes.packet_for_action(cur, s, action_id=p["action_id"])
    assert door["file_id"] == str(packet_row(cur, p["id"])["pdf_file_id"]) and door["filename"].endswith(".pdf")
    assert disputes.packet_for_action(cur, s, action_id=999999) is None


def test_dispute_verifier_reads_the_packet(cur, hotel_uae):
    s = hotel_uae.slug
    ids = setup_findings(cur, s, 1)
    p = disputes.create_packet(cur, s, ids)
    t = actions.get_action_type("pg.dispute")
    assert t.always_approve and t.ceiling == "approve"
    rows = [get(cur, ids[0])]
    assert disputes.verify_result(p, rows, now=NOW).status == "pending"
    cur.execute("UPDATE pfg_findings SET status = 'RECOVERED', recovered_minor = amount_minor WHERE id = %s", (ids[0],))
    v = disputes.verify_result(p, [get(cur, ids[0])], now=NOW)
    assert v.status == "verified" and v.outcome_minor == 8470


def test_submit_by_email_sends_the_packet(cur, client_as, hotel_uae, outbox):
    s = hotel_uae.slug
    ids = setup_findings(cur, s, 1)
    owner = client_as("owner")
    pid = owner.post(f"/hotels/{s}/profit-guard/disputes", json={"finding_ids": ids}).json()["id"]
    owner.post(f"/hotels/{s}/profit-guard/disputes/{pid}/approve")
    r = owner.post(f"/hotels/{s}/profit-guard/disputes/{pid}/submitted", json={"send_to": "partner@example.com"})
    assert r.status_code == 200, r.text
    sent = outbox.captured("email")
    assert len(sent) == 1 and "Commission dispute" in str(sent[0])
    cur.execute("SELECT note FROM pfg_finding_events WHERE finding_id = %s ORDER BY id DESC LIMIT 1", (ids[0],))
    assert "via email" in cur.fetchone()["note"]
