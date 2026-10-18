"""Stay linking, the returning-guest badge, known senders and the hourly
guests.link_stays job (03 spec 2.7)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from clefs.core import privacy
from clefs.modules.guests import api, identity, linking, stays_port

from .test_identity import PHONE, add_contact, grant, make_res

TODAY = date(2026, 10, 5)
NOON = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)  # 12:00 Dubai


def _consented_profile(cur, slug, *, vip=False, allergies=("nuts",)):
    h = identity.hmac_for(cur, slug, "phone", PHONE)
    grant(cur, slug, h, "transfer_abroad", "profile_memory")
    return identity.upsert_profile(
        cur, slug, phone_hmac=h, first_name="Aisha", vip=vip,
        preferences={"tags": ["extra pillows"], "health": {"allergies": list(allergies)}},
    )


def _two_stays(cur, slug, **kw):
    """A completed earlier stay and one arriving today, both with the
    guest's own PMS phone (pms_direct), then linked by the job logic."""
    prof = _consented_profile(cur, slug, **kw)
    old = make_res(cur, slug, check_in=date(2026, 3, 1), check_out=date(2026, 3, 4), status="CHECKED_OUT", room="202", pms="R0")
    new = make_res(cur, slug, check_in=TODAY, check_out=TODAY + timedelta(days=3), status="CONFIRMED", room="101", pms="R1")
    for rid in (old, new):
        add_contact(cur, slug, rid, PHONE)
        linking.link_reservation(cur, slug, rid)
    return prof, old, new


def test_pms_stays_link_to_a_consented_profile_and_count(cur, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    prof, old, new = _two_stays(cur, s)
    cur.execute("SELECT reservation_id, guest_profile_id FROM gst_stays WHERE hotel_slug = %s ORDER BY check_in", (s,))
    assert [(r["reservation_id"], r["guest_profile_id"]) for r in cur.fetchall()] == [(old, prof["id"]), (new, prof["id"])]
    p = identity.get_profile(cur, s, prof["id"])
    assert p["stays_count"] == 2 and p["first_seen"] == date(2026, 3, 1)


def test_ota_provided_identifiers_never_link_a_profile(cur, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    _consented_profile(cur, s)
    rid = make_res(cur, s, code="BOOKING", check_in=TODAY, check_out=TODAY + timedelta(days=2))
    add_contact(cur, s, rid, PHONE, provenance="pms_ota")
    stay = linking.link_reservation(cur, s, rid)
    assert stay["guest_profile_id"] is None


def test_returning_badge_is_masked_per_viewer(cur, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    _two_stays(cur, s)
    fd = api.returning_badge(cur, s, room="101", role="front_desk")
    assert fd["stay_number"] == 2 and fd["first_name"] == "Aisha"
    assert fd["label"] == "2nd stay · extra pillows · no nuts"
    hk = api.returning_badge(cur, s, room="101", role="device", departments=("Housekeeping",))
    assert hk["health"] is None and hk["label"] == "2nd stay · extra pillows" and hk["first_name"] == "Aisha"
    fnb = api.returning_badge(cur, s, room="101", role="dept_head", departments=("Food & Beverage",))
    assert fnb["health"]["allergies"] == ["nuts"] and "no nuts" in fnb["label"]
    assert api.returning_badge(cur, s, room="101", role=None)["first_name"] is None  # unknown role sees nothing
    assert api.returning_badge(cur, s, room="202", role="owner") is None  # nobody in 202 today
    arrivals = api.returning_arrivals(cur, s, day=TODAY, role="gm")
    assert [a["room"] for a in arrivals] == ["101"]


def test_badges_can_be_switched_off(cur, hotel_uae, clock):
    from clefs.core.hotels import set_module_settings

    clock.freeze(NOON)
    s = hotel_uae.slug
    _two_stays(cur, s)
    set_module_settings(cur, s, "guests", {"returning_badge": False}, actor=None)
    assert api.returning_badge(cur, s, room="101", role="owner") is None
    assert api.returning_arrivals(cur, s, day=TODAY, role="owner") == []
    assert api.returning_badges(cur, s, rooms=["101", "202"], role="owner") == {}


def test_returning_badges_batch_matches_single_room(cur, hotel_uae, clock):
    """The queue and device serializers badge a page of cards in one call;
    each entry is exactly what returning_badge gives for that room."""
    clock.freeze(NOON)
    s = hotel_uae.slug
    _two_stays(cur, s)
    # A first-time guest in 303 today: a live stay but not a returning one.
    make_res(cur, s, check_in=TODAY, check_out=TODAY + timedelta(days=1), status="CONFIRMED", room="303", pms="R3")
    for role, depts in (("front_desk", ()), ("device", ("Housekeeping",)), ("dept_head", ("Food & Beverage",))):
        got = api.returning_badges(cur, s, rooms=["101", "202", "303", "", "101"], role=role, departments=depts)
        assert list(got) == ["101"]
        assert got["101"] == api.returning_badge(cur, s, room="101", role=role, departments=depts)
    assert api.returning_badges(cur, s, rooms=[], role="gm") == {}


def test_guest_context_says_welcome_back_without_profile_details(cur, hotel_uae, clock):
    from clefs.db import Json

    clock.freeze(NOON)
    s = hotel_uae.slug
    _two_stays(cur, s)
    cur.execute("INSERT INTO guest_rooms (hotel_slug, room_number, data) VALUES (%s, '101', %s)",
                (s, Json({"stayType": "leisure", "internalNotes": "secret"})))
    ctx = api.guest_context_for_room(cur, s, "101")
    assert ctx["returningGuest"] == {"stayNumber": 2} and "internalNotes" not in ctx
    assert "health" not in repr(ctx) and "nuts" not in repr(ctx)


def test_link_job_raises_a_vip_arrival_signal_without_pii(cur, hotel_uae, clock, run_jobs):
    from clefs import jobs

    clock.freeze(NOON)
    s = hotel_uae.slug
    _two_stays(cur, s, vip=True)
    out = linking.link_stays_job(jobs.JobCtx(cur=cur, job="guests.link_stays", hotel_slug=s))
    assert out["vip_signals"] == 1 and out["pms_linked"] >= 1
    cur.execute("SELECT kind, title, summary FROM signals WHERE hotel_slug = %s AND kind = 'guests.returning_guest_arrival'", (s,))
    sig = cur.fetchone()
    assert "101" in sig["title"] and "2nd stay" in sig["title"]
    assert "Aisha" not in sig["title"] + (sig["summary"] or "") and "nuts" not in sig["title"] + (sig["summary"] or "")


def test_known_guest_sender(cur, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    rid = make_res(cur, s, check_in=TODAY + timedelta(days=1), check_out=TODAY + timedelta(days=3))
    h = add_contact(cur, s, rid, PHONE)
    assert api.is_known_guest_sender(cur, s, phone_hmac=h) is True  # CONFIRMED, check-in tomorrow
    far = make_res(cur, s, check_in=TODAY + timedelta(days=9), check_out=TODAY + timedelta(days=10), pms="R9")
    h2 = add_contact(cur, s, far, "+971502222222")
    assert api.is_known_guest_sender(cur, s, phone_hmac=h2) is False
    assert api.is_known_guest_sender(cur, s, phone_hmac=b"x" * 32) is False
    assert api.is_known_guest_sender(cur, s) is False
    # a roster guest captured at the desk, in house today
    linking.capture_room_identity(cur, s, "105", phone="+971503333333", consents={"transfer_abroad": True})
    h3 = identity.hmac_for(cur, s, "phone", "+971503333333")
    assert api.is_known_guest_sender(cur, s, phone_hmac=h3) is True


def test_roster_contacts_are_hashed_then_scrubbed(cur, hotel_uae, clock):
    from clefs.db import Json

    clock.freeze(NOON)
    s = hotel_uae.slug
    cur.execute(
        "INSERT INTO guest_rooms (hotel_slug, room_number, data) VALUES (%s, '101', %s), (%s, '102', %s)",
        (s, Json({"phone": PHONE, "consents": {"transfer_abroad": True, "profile_memory": True}, "checkIn": "2026-10-05"}),
         s, Json({"email": "nope@example.com"})),
    )
    assert linking.scrub_roster(cur, s) == 2
    cur.execute("SELECT room_number, data FROM guest_rooms WHERE hotel_slug = %s ORDER BY room_number", (s,))
    rows = {r["room_number"]: r["data"] for r in cur.fetchall()}
    assert "phone" not in rows["101"] and PHONE not in repr(rows["101"])
    assert rows["101"]["contact"]["status"] == "linked" and rows["101"]["contact"]["phone"] == privacy.mask_phone(PHONE)
    assert rows["102"]["contact"]["status"] == "not_stored:transfer_abroad_required" and "email" not in rows["102"]
    cur.execute("SELECT COUNT(*) AS n FROM gst_profiles WHERE hotel_slug = %s", (s,))
    assert cur.fetchone()["n"] == 1


def test_reservations_since_pages_instead_of_truncating(cur, hotel_uae, monkeypatch):
    s = hotel_uae.slug
    monkeypatch.setattr(stays_port, "PAGE_SIZE", 2)
    ids = [make_res(cur, s, pms=f"P{i}", check_in=TODAY, check_out=TODAY + timedelta(days=1)) for i in range(5)]
    got = stays_port.SqlStaysBackend().reservations_since(cur, s, check_out_from=TODAY)
    assert got == ids


def test_direct_codes_issue_and_redeem_once(cur, hotel_uae):
    s = hotel_uae.slug
    c = identity.issue_direct_code(cur, s, valid_to=TODAY + timedelta(days=30), discount_pct=10, prefix="back")
    assert c["code"].startswith("BACK-") and len(c["code"]) == 11
    assert identity.redeem_direct_code(cur, s, c["code"].lower(), reservation_id=7, on=TODAY)["redeemed_reservation_id"] == 7
    assert identity.redeem_direct_code(cur, s, c["code"], reservation_id=8, on=TODAY) is None
    old = identity.issue_direct_code(cur, s, valid_to=TODAY - timedelta(days=1))
    assert identity.redeem_direct_code(cur, s, old["code"], reservation_id=9, on=TODAY) is None


def test_identity_metrics_compile_and_count(cur, hotel_uae, clock):
    from clefs.core.metrics import MetricQuery, compile_metric
    from clefs.modules.guests import module  # noqa: F401  (registers the metrics, as app start does)

    clock.freeze(NOON)
    s = hotel_uae.slug
    old = make_res(cur, s, check_in=date(2026, 3, 1), check_out=date(2026, 3, 4), status="CHECKED_OUT", pms="A0")
    back = make_res(cur, s, check_in=date(2026, 9, 1), check_out=date(2026, 9, 3), status="CHECKED_OUT", pms="A1")
    new = make_res(cur, s, check_in=date(2026, 9, 10), check_out=date(2026, 9, 12), status="CHECKED_OUT", pms="A2")
    add_contact(cur, s, old, PHONE)
    add_contact(cur, s, back, PHONE)
    add_contact(cur, s, new, "+971504444444")
    sql, params = compile_metric(hotel_uae, MetricQuery(metric="guests.returning_share", start=date(2026, 9, 1), end=date(2026, 9, 30)))
    cur.execute(sql, params)
    assert float(cur.fetchall()[0]["value"]) == 50.0
    c = identity.issue_direct_code(cur, s, valid_to=date(2026, 12, 31))
    identity.redeem_direct_code(cur, s, c["code"], reservation_id=new, on=date(2026, 10, 5))
    sql, params = compile_metric(
        hotel_uae, MetricQuery(metric="guests.direct_rebook_conversions", start=date(2026, 10, 1), end=date(2026, 10, 31), group_by=["basis"])
    )
    cur.execute(sql, params)
    rows = cur.fetchall()
    assert [(r["value"]) for r in rows] == [1]
