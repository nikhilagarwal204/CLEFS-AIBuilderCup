"""OTA-leak matching (04 spec 4.9, Canon 19): the 30-day window, purged
contacts, OTA alias emails, name-plus-dates review, the golden leak and no
outbound message of any kind."""

from __future__ import annotations

from datetime import timedelta

from clefs.core.actions import SYSTEM
from clefs.modules.inquiries import leak, store, verify
from tests.modules.inquiries.test_payments_support import pg, reservation  # noqa: F401
from tests.modules.inquiries.test_quotes_support import CHECK_IN, NOW, hotel, make_inquiry, now  # noqa: F401


def booking(cur, hotel, **kw):
    kw.setdefault("channel", "BOOKING")
    kw.setdefault("nights", 3)
    kw.setdefault("rate_aed", 350)
    return reservation(cur, hotel, **kw)


def test_identity_match_records_the_leak_internally(cur, hotel, pg, outbox):
    inq = make_inquiry(cur, hotel, nights=3, stage="quoted")
    booking(cur, hotel, name="Different Name")
    outbox.reset()
    assert leak.run(cur, hotel) == {"leaked": 1, "reviews": 0}
    out = verify.get_outcome(cur, inq["id"])
    assert out["outcome"] == "leaked_to_ota" and out["leaked_channel"] == "BOOKING"
    assert out["leaked_commission_minor"] == 18252  # 15% of 1,216.79 (room + SC + VAT)
    row = store.get_row(cur, inq["id"])
    assert (row["stage"], row["lost_reason"]) == ("lost", "BOOKED_VIA_OTA")
    (call,) = pg.leaks
    assert call["commission_minor"] is None and call["currency"] is None and call["inquiry_ref"] == inq["ref"]
    assert outbox.captured() == []  # never a word to the guest
    cur.execute("SELECT status FROM signals WHERE kind = 'inq.leaked_to_ota'")
    assert cur.fetchone()["status"] == "open"


def test_booking_after_31_days_is_not_matched(cur, hotel, pg):
    make_inquiry(cur, hotel, nights=3)
    booking(cur, hotel, booked_at=NOW + timedelta(days=31))
    assert leak.run(cur, hotel) == {"leaked": 0, "reviews": 0}


def test_purged_contact_is_not_matched(cur, hotel, pg):
    make_inquiry(cur, hotel, nights=3)
    res = booking(cur, hotel)
    cur.execute("UPDATE reservation_contacts SET purge_after = %s WHERE reservation_id = %s",
                (NOW - timedelta(minutes=1), res["id"]))
    assert leak.run(cur, hotel)["leaked"] == 0


def test_alias_email_is_never_an_identity(cur, hotel, pg):
    alias = "sara.k.123@guest.booking.com"
    make_inquiry(cur, hotel, nights=3, phone=None, email=alias)
    res = booking(cur, hotel, phone=None, email=alias, name="Other Person")
    cur.execute("SELECT provenance FROM reservation_contacts WHERE reservation_id = %s", (res["id"],))
    assert cur.fetchone()["provenance"] == "ota_alias"
    assert leak.run(cur, hotel) == {"leaked": 0, "reviews": 0}


def test_name_and_exact_dates_go_to_review(cur, hotel, pg):
    inq = make_inquiry(cur, hotel, nights=3, phone="+971509999999", email=None)
    res = booking(cur, hotel, phone="+971501111111")
    assert leak.run(cur, hotel) == {"leaked": 0, "reviews": 1}
    (rv,) = verify.list_reviews(cur, hotel.slug, kind="leak")
    assert rv["reservation_id"] == res["id"]
    got = verify.decide_review(cur, hotel, rv["id"], confirm=True, actor=SYSTEM)
    assert got["leak"]["outcome"] == "leaked_to_ota"
    assert store.get_row(cur, inq["id"])["lost_reason"] == "BOOKED_VIA_OTA"


def test_old_inquiries_and_far_dates_are_out_of_window(cur, hotel, pg, now):
    make_inquiry(cur, hotel, nights=3)
    booking(cur, hotel, check_in=CHECK_IN + timedelta(days=4))  # +-3 d only
    assert leak.run(cur, hotel)["leaked"] == 0
    now.advance(days=31)
    booking(cur, hotel, pms="R2", booked_at=NOW + timedelta(days=30, hours=23))
    assert leak.run(cur, hotel)["leaked"] == 0  # the inquiry is older than 30 d


# ─── Statement currency (review money-correctness-leak-currency-label) ───


def test_statement_commission_is_converted_to_hotel_currency(cur, hotel, pg, monkeypatch):
    """Profit Guard keeps an Expedia statement's USD: USD 50.00 is stored
    and signalled as about AED 183.63 (peg 3.6725), never as AED 50.00."""
    from clefs.modules.profit_guard import api as pg_api

    monkeypatch.setattr(pg_api, "record_ota_leak", lambda cur, slug, **kw: {
        "finding_ref": "F-1", "commission_minor": 5000, "currency": "USD", "basis": "statement"})
    inq = make_inquiry(cur, hotel, nights=3, stage="quoted")
    booking(cur, hotel, name="Different Name")
    assert leak.run(cur, hotel)["leaked"] == 1
    out = verify.get_outcome(cur, inq["id"])
    assert (out["leaked_commission_minor"], out["currency"].strip()) == (18363, "AED")
    cur.execute("SELECT impact_minor, currency FROM signals WHERE kind = 'inq.leaked_to_ota'")
    sig = cur.fetchone()
    assert (sig["impact_minor"], sig["currency"].strip()) == (18363, "AED")


def test_commission_without_rate_keeps_its_own_currency(cur, hotel, pg, monkeypatch):
    from clefs.modules.profit_guard import api as pg_api

    monkeypatch.setattr(pg_api, "record_ota_leak", lambda cur, slug, **kw: {
        "finding_ref": "F-2", "commission_minor": 700000, "currency": "JPY", "basis": "statement"})
    inq = make_inquiry(cur, hotel, nights=3, stage="quoted")
    booking(cur, hotel, name="Different Name")
    leak.run(cur, hotel)
    out = verify.get_outcome(cur, inq["id"])
    # No JPY rate: the label stays true (JPY) rather than calling it AED.
    assert (out["leaked_commission_minor"], out["currency"].strip()) == (700000, "JPY")
