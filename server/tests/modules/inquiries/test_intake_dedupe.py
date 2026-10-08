"""Dedupe and the reopen rule (04 spec 4.2, 4.4, 4.12)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    hotel,
    inquiries,
    now,
    send_email,
    send_wa,
    thread,
)


def _close(cur, hotel, stage, **kw):
    from clefs.modules.inquiries import service

    r = inquiries(cur, hotel.slug)[0]
    return service.transition(cur, r, stage, hotel=hotel, **kw)


def test_lost_reopens_within_29_days(cur, hotel, now):
    send_wa("Hello", mid="w1")
    _close(cur, hotel, "lost", lost_reason="GUEST_UNRESPONSIVE")
    now.advance(days=29)
    send_wa("Back again, still interested", mid="w2")
    rows = inquiries(cur, hotel.slug)
    assert len(rows) == 1 and rows[0]["stage"] == "new" and rows[0]["lost_reason"] is None
    # A fresh first-response clock from the reopen.
    assert rows[0]["first_response_due_at"] == rows[0]["stage_changed_at"] + timedelta(minutes=15)
    cur.execute("SELECT to_stage, reason FROM inq_stage_events WHERE inquiry_id = %s ORDER BY id", (rows[0]["id"],))
    assert [e["to_stage"] for e in cur.fetchall()] == ["new", "lost", "new"]


def test_lost_after_31_days_creates_a_new_inquiry(cur, hotel, now):
    send_wa("Hello", mid="w1")
    _close(cur, hotel, "lost", lost_reason="GUEST_UNRESPONSIVE")
    now.advance(days=31)
    send_wa("Hi again", mid="w2")
    rows = inquiries(cur, hotel.slug)
    assert [r["stage"] for r in rows] == ["lost", "new"]


@pytest.mark.parametrize("stage,kw", [("spam", {}), ("turned_down", {"lost_reason": "NO_AVAILABILITY"})])
def test_spam_and_turned_down_never_reopen(cur, hotel, now, stage, kw):
    send_wa("Hello", mid="w1")
    _close(cur, hotel, stage, **kw)
    now.advance(days=1)
    send_wa("Hello again", mid="w2")
    assert [r["stage"] for r in inquiries(cur, hotel.slug)] == [stage, "new"]


def test_future_reopens_on_new_inbound(cur, hotel, now):
    from datetime import date

    send_wa("Hello", mid="w1")
    _close(cur, hotel, "future", remind_on=date(2026, 12, 1))
    now.advance(days=3)
    send_wa("We can come in December now", mid="w2")
    rows = inquiries(cur, hotel.slug)
    assert len(rows) == 1 and rows[0]["stage"] == "new" and rows[0]["future_remind_on"] is None


def test_same_email_on_another_channel_merges(cur, hotel, now):
    from clefs.modules.inquiries.intake import Inbound, ingest

    send_email(sender="sara@example.com", text="Two rooms please", message_id="m1")
    res = ingest(cur, hotel, Inbound(source="web_form", text="Also need airport pickup", email="SARA@example.com ",
                                     phone="+971501110000", consent_basis="express_web_form"))
    rows = inquiries(cur, hotel.slug)
    assert res.outcome == "appended" and len(rows) == 1
    # The phone the second channel carried is filled in.
    assert rows[0]["contact_phone_hmac"] is not None
    assert len([m for m in thread(cur, rows[0]["id"]) if m["direction"] == "in"]) == 2


def test_illegal_stage_move_is_refused(cur, hotel):
    from clefs.core.errors import ClefsError

    send_wa("Hello")
    _close(cur, hotel, "spam")
    with pytest.raises(ClefsError) as e:
        _close(cur, hotel, "quoted")
    assert e.value.code == "illegal_stage"


def test_same_name_overlapping_dates_without_contact_is_a_suggestion(client_as, cur, hotel, now):
    from datetime import date

    from clefs.modules.inquiries.intake import Inbound, ingest

    a = ingest(cur, hotel, Inbound(source="phone_desk", text="Caller, 2 rooms", name="Fatima Al Zahra",
                                   check_in=date(2026, 10, 12), check_out=date(2026, 10, 15), author_type="staff")).inquiry
    now.advance(hours=5)
    b = ingest(cur, hotel, Inbound(source="web_form", text="Rooms please", name="Fatima Al-Zahra",
                                   email="fatima@example.com", check_in=date(2026, 10, 13), check_out=date(2026, 10, 16),
                                   consent_basis="express_web_form")).inquiry
    c = client_as("gm", hotel.slug)
    detail = c.get(f"/hotels/{hotel.slug}/inquiries/{a['id']}").json()
    assert [s["id"] for s in detail["merge_suggestions"]] == [b["id"]]
    merged = c.post(f"/hotels/{hotel.slug}/inquiries/{a['id']}/merge", json={"into_id": b["id"]})
    assert merged.status_code == 200, merged.text
    rows = {r["id"]: r for r in inquiries(cur, hotel.slug)}
    assert rows[a["id"]]["duplicate_of"] == b["id"] and rows[a["id"]]["stage"] == "lost"
    assert len(thread(cur, b["id"])) == 2
