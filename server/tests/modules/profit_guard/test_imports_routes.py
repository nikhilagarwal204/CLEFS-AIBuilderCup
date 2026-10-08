"""Profit Guard data routes under /hotels/{slug}/profit-guard: the import
wrappers over core/ingest, statement / payout / bank / card reads, and the
profit.view / profit.manage gates."""

from __future__ import annotations

import pytest

from clefs import files
from clefs.modules.profit_guard import imports

STATEMENT = (
    "Book number,Guest name(s),Check-in,Check-out,Status,Price,Commission %,Commission amount,Currency\n"
    "4000012345,AL MANSOURI/AHMED,01/10/2026,03/10/2026,ok,1000.00,15%,150.00,AED\n"
    "4000012346,Omar Ali,02/10/2026,04/10/2026,no_show,500.00,15%,75.00,AED\n"
    "Total,,,,,1500.00,,225.00,\n"
)
VCC = (
    "VCC ID,Booking number,Activation date,Expiration date,Initial amount,Current balance,Currency\n"
    "VC-9,4000012345,01/10/2026,01/12/2026,500.00,500.00,AED\n"
)


@pytest.fixture(autouse=True)
def handlers():
    imports.register()


def stored(cur, slug, text, name):
    return str(files.put_file(cur, slug, text.encode(), filename=name, content_type="text/csv",
                              purpose="ingest.upload").id)


def test_import_flow_through_profit_guard_routes(cur, client_as, hotel_uae):
    s = hotel_uae.slug
    owner = client_as("owner")
    fid = stored(cur, s, STATEMENT, "stmt.csv")
    r = owner.post(f"/hotels/{s}/profit-guard/imports", json={"file_id": fid, "source_code": "booking_res_statement"})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["kind"] == "OTA_STATEMENT" and b["status"] == "REVIEW" and b["gate_status"] == "ok"
    assert "ERROR" not in b["row_counts"] and sum(b["row_counts"].values()) == 2
    again = owner.post(f"/hotels/{s}/profit-guard/imports", json={"file_id": fid, "kind": "OTA_STATEMENT"})
    assert again.status_code == 200 and again.json()["id"] == b["id"]
    assert [x["id"] for x in owner.get(f"/hotels/{s}/profit-guard/imports").json()] == [b["id"]]
    c = owner.post(f"/hotels/{s}/profit-guard/imports/{b['id']}/commit", json={})
    assert c.status_code == 200, c.text
    result = c.json()["commit_result"]
    assert c.json()["status"] == "COMMITTED" and result["lines"] == 2
    st = owner.get(f"/hotels/{s}/profit-guard/statements").json()
    assert len(st) == 1 and st[0]["total_minor"] == 22500 and st[0]["total"] == "225.00"
    one = owner.get(f"/hotels/{s}/profit-guard/statements/{st[0]['id']}").json()
    assert {ln["guest_name"] for ln in one["lines"]} == {"AL MANSOURI/AHMED", "Omar Ali"}
    assert one["lines"][0]["commission"] == "150.00" and one["lines"][0]["match_status"] == "UNMATCHED"
    assert owner.post(f"/hotels/{s}/profit-guard/imports/{b['id']}/commit").json()["code"] == "already_committed"


def test_review_edits_and_non_profit_kinds(cur, client_as, hotel_uae):
    s = hotel_uae.slug
    owner = client_as("owner")
    fid = stored(cur, s, STATEMENT.replace("225.00", "999.00"), "stmt2.csv")
    b = owner.post(f"/hotels/{s}/profit-guard/imports", json={"file_id": fid, "source_code": "booking_res_statement"})
    assert b.json()["gate_status"] == "mismatch" and b.json()["gate_blocking"] is True
    blocked = owner.post(f"/hotels/{s}/profit-guard/imports/{b.json()['id']}/commit")
    assert blocked.status_code == 409 and blocked.json()["code"] == "gate_blocked"
    rows = owner.get(f"/hotels/{s}/ingest/batches/{b.json()['id']}/rows").json()["rows"]
    edit = owner.post(f"/hotels/{s}/profit-guard/imports/{b.json()['id']}/review",
                      json={"edits": [{"row_id": rows[0]["id"], "values": {"commission_minor": "924.00"}}]})
    assert edit.status_code == 200 and edit.json()["gate_status"] == "ok", edit.text
    pms = stored(cur, s, "Reservation ID,Arrival,Departure\nR1,01/10/2026,02/10/2026\n", "pms.csv")
    wrong = owner.post(f"/hotels/{s}/profit-guard/imports", json={"file_id": pms, "source_code": "pms_reservations"})
    assert wrong.status_code == 400 and wrong.json()["code"] == "source_kind_mismatch"


def test_vcc_routes_and_permissions(cur, client_as, hotel_uae):
    s = hotel_uae.slug
    fin = client_as("finance")
    fid = stored(cur, s, VCC, "vcc.csv")
    b = fin.post(f"/hotels/{s}/profit-guard/imports", json={"file_id": fid, "kind": "VCC_REPORT", "ota_code": "BOOKING"})
    assert b.status_code == 201, b.text
    assert fin.post(f"/hotels/{s}/profit-guard/imports/{b.json()['id']}/commit").status_code == 200
    cards = fin.get(f"/hotels/{s}/profit-guard/vcc?open=true").json()
    assert len(cards) == 1 and cards[0]["current_balance"] == "500.00" and cards[0]["charge_deadline"] == "2026-12-01"
    assert isinstance(cards[0]["days_left"], int)
    part = fin.post(f"/hotels/{s}/profit-guard/vcc/{cards[0]['id']}/charged", json={"amount": "200.00"})
    assert part.json()["status"] == "PARTIALLY_CHARGED" and part.json()["current_balance_minor"] == 30000
    assert fin.post(f"/hotels/{s}/profit-guard/vcc/{cards[0]['id']}/charged", json={"amount": "900"}).status_code == 400
    assert fin.get(f"/hotels/{s}/profit-guard/payouts").json() == []
    assert fin.get(f"/hotels/{s}/profit-guard/bank-credits?from=2026-01-01").json() == []
    desk = client_as("front_desk")
    assert desk.get(f"/hotels/{s}/profit-guard/statements").status_code == 403
    rev = client_as("revenue")
    assert rev.post(f"/hotels/{s}/profit-guard/imports", json={"file_id": fid, "kind": "BANK"}).status_code == 403
    assert fin.get(f"/hotels/{s}/profit-guard/imports/999999").status_code == 404
