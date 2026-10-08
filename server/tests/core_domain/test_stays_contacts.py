"""Reservation contacts: sealed and HMAC'd, provenance and lifetimes, audited
unsealing, the purge, and no plaintext contact column anywhere in core
(04 spec D1, 3.4; Canon 19)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from clefs.core import privacy, stays
from clefs.core.stays import contacts as c_mod

NOW = datetime(2025, 10, 20, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def slug(cur, hotel_uae, clock):
    clock.freeze(NOW)
    return hotel_uae.slug


def book(cur, slug, pms_id, channel, contacts, **kw):
    data = {"pms_res_id": pms_id, "check_in": "2025-10-10", "check_out": "2025-10-12", "channel_code": channel,
            "room_number": "101", "guest_name": "Jane Doe"} | kw
    return stays.upsert_reservation(cur, slug, data, contacts=contacts)


def test_values_are_sealed_hashed_and_classified(cur, slug):
    rid = book(cur, slug, "K1", "BOOKING", [
        {"kind": "email", "value": "jdoe.123@guest.booking.com"},
        {"kind": "phone", "value": "050 123 4567"},
    ])["id"]
    rows = stays.contacts_for(cur, slug, rid)
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["email"]["provenance"] == "ota_alias" and by_kind["email"]["alias_domain"] == "guest.booking.com"
    assert by_kind["phone"]["provenance"] == "pms_ota"
    assert bytes(by_kind["phone"]["value_hmac"]) == privacy.contact_hmac(slug, "phone", "+971501234567")
    cur.execute("SELECT value_enc FROM reservation_contacts WHERE id = %s", (by_kind["phone"]["id"],))
    assert b"4567" not in bytes(cur.fetchone()["value_enc"])  # sealed, never plaintext
    # Booking alias and Booking-provided phone: check_out + 30 d (Dubai midnight).
    end = datetime(2025, 11, 11, 0, 0, tzinfo=timezone(timedelta(hours=4)))
    assert by_kind["email"]["usable_until"] == end == by_kind["phone"]["purge_after"]
    direct = book(cur, slug, "K2", "DIRECT_WEB", [{"kind": "email", "value": "Jane@Example.com"}])["id"]
    [d] = stays.contacts_for(cur, slug, direct)
    assert d["provenance"] == "pms_direct"
    assert d["usable_until"].date() == (datetime(2025, 10, 12) + timedelta(days=c_mod.PMS_DIRECT_RETENTION_DAYS)).date()
    expedia = book(cur, slug, "K3", "EXPEDIA", [{"kind": "email", "value": "a1@m.expediapartnercentral.com"}])["id"]
    [e] = stays.contacts_for(cur, slug, expedia)
    assert e["usable_until"].date().isoformat() == "2025-10-12"  # Expedia alias: check_out (A2)
    assert e["purge_after"].date().isoformat() == "2025-11-11"
    agoda = book(cur, slug, "K4", "AGODA", [{"kind": "email", "value": "x@agoda-messaging.com"}])["id"]
    assert stays.contacts_for(cur, slug, agoda)[0]["provenance"] == "ota_alias"  # Agoda heuristic


def test_contact_value_unseals_with_an_audit_row_until_usable_until(cur, slug, clock):
    rid = book(cur, slug, "K5", "BOOKING", [{"kind": "phone", "value": "+971 50 765 4321"}])["id"]
    [row] = stays.contacts_for(cur, slug, rid)
    assert stays.contact_value(cur, row["id"], purpose="tests.read") == "+971507654321"
    cur.execute("SELECT action, meta FROM audit_log WHERE entity_type = 'reservation_contact' AND entity_id = %s",
                (str(row["id"]),))
    audit = cur.fetchone()
    assert audit["action"] == "stays.contact_unseal" and audit["meta"]["purpose"] == "tests.read"
    assert "+971" not in str(audit["meta"])
    clock.freeze(row["usable_until"] + timedelta(seconds=1))
    assert stays.contact_value(cur, row["id"]) is None
    assert stays.contact_value(cur, 10**9) is None


def test_contact_changes_emit_ids_only_and_replace_purges(cur, slug):
    rid = book(cur, slug, "K6", "DIRECT_PHONE", [{"kind": "phone", "value": "+971501111111"}])["id"]
    first = stays.contacts_for(cur, slug, rid)[0]["id"]
    names = [e["event"] for e in stays.events_for(cur, slug, rid)]
    assert names == ["created", "contact_changed"]  # [] -> [first]
    stays.upsert_reservation(cur, slug, {"pms_res_id": "K6"}, contacts=[{"kind": "phone", "value": "0501111111"}])
    assert [e["event"] for e in stays.events_for(cur, slug, rid)] == names  # same number, national form
    stays.upsert_reservation(cur, slug, {"pms_res_id": "K6"}, contacts=[{"kind": "phone", "value": "+971502222222"},
                                                                         {"kind": "email", "value": "not-an-email"}])
    evs = [e for e in stays.events_for(cur, slug, rid) if e["event"] == "contact_changed"]
    second = stays.contacts_for(cur, slug, rid)[0]["id"]
    assert evs[-1]["changes"] == {"reservation_contact_ids": [[first], [second]]}
    assert "+97150" not in str([e["changes"] for e in evs])
    [old] = [r for r in stays.contacts_for(cur, slug, rid, include_purged=True) if r["id"] == first]
    assert old["purged_at"] is not None and old["value_hmac"] is None and not old["has_value"]


def test_purge_drops_ota_hmacs_and_keeps_direct_hmacs(cur, slug):
    ota = book(cur, slug, "K7", "BOOKING", [{"kind": "phone", "value": "+971503333333"}])["id"]
    direct = book(cur, slug, "K8", "WALKIN", [{"kind": "phone", "value": "+971504444444"}])["id"]
    at = datetime(2025, 11, 12, tzinfo=timezone.utc)
    assert stays.purge_contacts(cur, now=at) == {"purged": 1, "ota": 1, "direct": 0}
    [o] = stays.contacts_for(cur, slug, ota, include_purged=True)
    assert o["value_hmac"] is None and o["purged_at"] is not None and not o["has_value"]
    later = datetime(2028, 1, 1, tzinfo=timezone.utc)
    assert stays.purge_contacts(cur, now=later)["direct"] == 1
    [d] = stays.contacts_for(cur, slug, direct, include_purged=True)
    assert d["value_hmac"] is not None and not d["has_value"]
    # 04 spec D1: no OTA-provenance hmac exists after purge_after.
    cur.execute("""SELECT count(*) AS n FROM reservation_contacts WHERE provenance IN ('pms_ota', 'ota_alias')
                   AND purge_after <= %s AND value_hmac IS NOT NULL""", (at,))
    assert cur.fetchone()["n"] == 0


def test_moving_check_out_moves_the_lifetime(cur, slug):
    rid = book(cur, slug, "K9", "BOOKING", [{"kind": "email", "value": "z@guest.booking.com"}])["id"]
    stays.upsert_reservation(cur, slug, {"pms_res_id": "K9", "check_out": "2025-10-15"})
    [row] = stays.contacts_for(cur, slug, rid)
    assert row["purge_after"].date().isoformat() == "2025-11-14"


def test_no_core_table_holds_a_plaintext_phone_or_email_column(cur):
    """04 spec D1: 02's plaintext guest_contacts table is not built."""
    cur.execute("SELECT to_regclass('guest_contacts') AS t")
    assert cur.fetchone()["t"] is None
    core = ("reservations", "reservation_nights", "folio_charges", "occupancy_daily", "otb_snapshots",
            "reservation_matches", "reservation_contacts", "stay_events")
    cur.execute(
        """SELECT table_name, column_name FROM information_schema.columns
           WHERE table_schema = current_schema() AND table_name = ANY(%s)
             AND (column_name ~ '(phone|email|e164)' OR column_name = 'value')""",
        (list(core),),
    )
    assert cur.fetchall() == []
