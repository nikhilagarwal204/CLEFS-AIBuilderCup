"""OTA claim to PMS stay matching: exact id, fuzzy score with the 0.85 /
0.65 thresholds, the review queue and human decisions (02 spec 2.1)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from clefs.core import stays
from clefs.core.stays import matching as m

D = date.fromisoformat


@pytest.fixture
def slug(cur, hotel_uae, clock):
    clock.freeze(datetime(2025, 10, 20, 8, tzinfo=timezone.utc))
    return hotel_uae.slug


def book(cur, slug, pms_id, *, name="Jane Doe", ci="2025-10-10", co="2025-10-13", channel="BOOKING", ota=None,
         gross=150000, room="101"):
    data = {"pms_res_id": pms_id, "guest_name": name, "check_in": ci, "check_out": co, "channel_code": channel,
            "ota_res_id": ota, "gross_minor": gross, "room_number": room, "status": "CHECKED_OUT"}
    return stays.upsert_reservation(cur, slug, data)["id"]


def test_score_weights_match_the_spec():
    claim = m.Claim("BOOKING", None, "DOE/JANE", D("2025-10-10"), 3, 150000, "AED")
    res = {"guest_name_norm": "doe jane", "check_in": D("2025-10-10"), "nights": 3, "gross_minor": 150000,
           "currency": "AED"}
    assert m.score(claim, res)[0] == Decimal("1.000")
    s, parts = m.score(claim, res | {"check_in": D("2025-10-11"), "nights": 2, "gross_minor": 200000})
    assert parts == {"name": 1.0, "check_in": 0.6, "nights": 0.0, "amount": 0.0}
    assert s == Decimal("0.600")
    assert m.score(claim, res | {"gross_minor": 157000})[1]["amount"] == 1.0  # within 5%
    assert m.score(claim, res | {"gross_minor": 158000})[1]["amount"] == 0.0
    assert m.score(claim, res | {"currency": "USD"})[1]["amount"] == 0.0
    # Two different OTA ids: never above the auto-link line on the other fields.
    claim_id = m.Claim("BOOKING", "111", "Jane Doe", D("2025-10-10"), 3, 150000, "AED")
    assert m.score(claim_id, res | {"ota_res_id_norm": "222"})[0] == Decimal("0.840")
    assert [m.status_for(Decimal(x)) for x in ("0.85", "0.849", "0.65", "0.649")] == [
        "LINKED", "REVIEW", "REVIEW", "UNMATCHED"]


def test_exact_id_links_every_split_row(cur, slug):
    a = book(cur, slug, "M1", ota="4123456789", room="101")
    b = book(cur, slug, "M2", ota="BDC 4123-456-789", room="102")
    book(cur, slug, "M3", ota="999", room="103")
    row = stays.match_claim(cur, slug, statement_line_id=1, ota_code="booking", ota_res_id="#4123456789",
                            guest_name="Someone Else", check_in=D("2025-10-10"), nights=3)
    assert (row["method"], row["status"], row["reservation_id"]) == ("EXACT", "LINKED", a)
    assert row["reservation_ids"] == [a, b]


def test_fuzzy_links_reviews_or_leaves_unmatched(cur, slug):
    good = book(cur, slug, "F1", name="Jane Doe")
    row = stays.match_claim(cur, slug, statement_line_id=2, ota_code="BOOKING", guest_name="DOE, JANE MS",
                            check_in=D("2025-10-10"), check_out=D("2025-10-13"), amount_minor=151000,
                            currency="AED")
    assert (row["method"], row["status"], row["reservation_id"]) == ("FUZZY", "LINKED", good)
    assert Decimal(row["score"]) == Decimal("1.000")
    # Right name, a day off, other nights, other amount: 0.45 + 0.15 = 0.60 -> UNMATCHED.
    row = stays.match_claim(cur, slug, statement_line_id=3, ota_code="BOOKING", guest_name="Jane Doe",
                            check_in=D("2025-10-11"), nights=5, amount_minor=900000, currency="AED")
    assert row["status"] == "UNMATCHED" and row["reservation_id"] is None and row["candidates"]
    # Right name and date, other nights and amount: 0.70 -> REVIEW.
    row = stays.match_claim(cur, slug, statement_line_id=4, ota_code="BOOKING", guest_name="Jane Doe",
                            check_in=D("2025-10-10"), nights=5, amount_minor=900000, currency="AED")
    assert row["status"] == "REVIEW" and row["reservation_id"] is None
    assert row["candidates"][0]["reservation_id"] == good
    # A direct booking or another OTA's stay is never a candidate.
    direct = book(cur, slug, "F2", name="Omar Khan", channel="DIRECT_WEB", room="102")
    agoda = book(cur, slug, "F3", name="Omar Khan", channel="AGODA", room="103")
    row = stays.match_claim(cur, slug, statement_line_id=5, ota_code="BOOKING", guest_name="Omar Khan",
                            check_in=D("2025-10-10"), nights=3, amount_minor=150000, currency="AED")
    assert {c["reservation_id"] for c in row["candidates"]} == {good}
    assert direct not in row["reservation_ids"] and agoda not in row["reservation_ids"]


def test_two_strong_candidates_go_to_review(cur, slug):
    book(cur, slug, "T1", name="Ali Hassan", room="101", channel="AGODA")
    book(cur, slug, "T2", name="Ali Hassan", room="102", channel=None)
    row = stays.match_claim(cur, slug, statement_line_id=6, ota_code="AGODA", guest_name="Ali Hassan",
                            check_in=D("2025-10-10"), nights=3, amount_minor=150000, currency="AED", )
    assert row["status"] == "REVIEW" and len(row["candidates"]) == 2


def test_review_queue_and_human_decisions_survive_reruns(cur, slug):
    good = book(cur, slug, "H1", name="Jane Doe")
    row = stays.match_claim(cur, slug, statement_line_id=7, ota_code="BOOKING", guest_name="Jane Doe",
                            check_in=D("2025-10-10"), nights=5, amount_minor=900000, currency="AED")
    queue = stays.review_queue(cur, slug)
    assert [q["id"] for q in queue] == [row["id"]]
    assert queue[0]["candidates"][0]["reservation"]["pms_res_id"] == "H1"
    assert queue[0]["query"]["guest_name"] == "doe jane"  # stored normalised only
    done = stays.confirm_match(cur, slug, row["id"], reservation_ids=[good])
    assert (done["method"], done["status"], done["reservation_ids"]) == ("MANUAL", "LINKED", [good])
    again = stays.match_claim(cur, slug, statement_line_id=7, ota_code="BOOKING", guest_name="Nobody",
                              check_in=D("2025-12-01"))
    assert again["method"] == "MANUAL" and again["reservation_id"] == good
    other = stays.match_claim(cur, slug, statement_line_id=8, ota_code="BOOKING", guest_name="Jane Doe",
                              check_in=D("2025-10-10"), nights=5, amount_minor=900000, currency="AED")
    rejected = stays.reject_match(cur, slug, other["id"])
    assert rejected["status"] == "REJECTED"
    cur.execute("SELECT action FROM audit_log WHERE entity_type = 'reservation_match' ORDER BY id")
    assert [r["action"] for r in cur.fetchall()] == ["stays.match_confirm", "stays.match_reject"]
    from clefs.core.errors import ClefsError

    with pytest.raises(ClefsError):
        stays.confirm_match(cur, slug, other["id"], reservation_ids=[10**9])


def test_rematch_upgrades_open_lines_after_a_new_import(cur, slug):
    row = stays.match_claim(cur, slug, statement_line_id=9, ota_code="EXPEDIA", ota_res_id="EXP-7214 5530",
                            guest_name="Mei Lin", check_in=D("2025-10-10"), nights=3)
    assert row["status"] == "UNMATCHED"
    rid = book(cur, slug, "R1", name="Lin Mei", channel="EXPEDIA", ota="72145530")
    assert stays.rematch_open(cur, slug) == {"LINKED": 1}
    assert stays.match_for_line(cur, slug, 9)["reservation_id"] == rid
