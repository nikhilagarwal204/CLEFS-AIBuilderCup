"""
Inquiry-to-OTA leak matching (04 spec 4.9 `inquiries.leak_match`, Canon 19).

Daily 05:00, INTERNAL ONLY: a direct inquiry that later booked through an OTA
is recorded for the owner's numbers and reported to Profit Guard
(`profit_guard.api.record_ota_leak`), and nothing else:

- never a message to the guest (this file imports no notify);
- never exported, never an audience, never a public audit;
- masked for roles without guests.view wherever it is shown.

Window (Booking.com GDT 2.2.6: OTA contact data lives 30 days): inquiries
created in the last 30 d that are open, lost or future, against OTA
reservations booked within [inquiry.created_at, +30 d], arriving within
+-3 d of the requested check-in, whose contacts are still inside their
purge_after. Matching is by identity hmac only against contacts whose
provenance is not `ota_alias` (an alias email can never equal a real one),
or by name >= 0.90 plus exact dates, which goes to review.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from clefs.core import clock, money
from clefs.core.errors import ClefsError
from clefs.core.hotels import Hotel
from clefs.db import Json
from clefs.modules.inquiries import store

WINDOW = timedelta(days=30)
DATE_TOLERANCE_DAYS = 3
NAME_THRESHOLD = 0.90
LEAK_STAGES = ("new", "replied", "quoted", "payment_pending", "lost", "future")


def _inquiries(cur: Any, hotel: Hotel) -> list[dict]:
    cur.execute(
        """
        SELECT i.* FROM inq_inquiries i LEFT JOIN inq_outcomes o ON o.inquiry_id = i.id
        WHERE i.hotel_slug = %s AND i.stage = ANY(%s) AND i.created_at >= %s AND i.duplicate_of IS NULL
          AND NOT i.is_test AND i.check_in IS NOT NULL AND o.inquiry_id IS NULL
        ORDER BY i.id
        """,
        (hotel.slug, list(LEAK_STAGES), clock.now() - WINDOW),
    )
    return [dict(r) for r in cur.fetchall()]


def candidates(cur: Any, hotel: Hotel, inquiry: dict) -> list[dict]:
    """OTA reservations inside the window whose contacts have not passed
    purge_after (a purged contact is never matched)."""
    now = clock.now()
    cur.execute(
        """
        SELECT r.* FROM reservations r JOIN channels c ON c.hotel_slug = r.hotel_slug AND c.code = r.channel_code
        WHERE r.hotel_slug = %s AND c.kind = 'ota' AND r.status <> 'CANCELLED'
          AND r.booked_at BETWEEN %s AND %s AND r.check_in BETWEEN %s AND %s
          AND EXISTS (SELECT 1 FROM reservation_contacts rc WHERE rc.reservation_id = r.id AND rc.purged_at IS NULL
                      AND (rc.purge_after IS NULL OR rc.purge_after >= %s))
        ORDER BY r.booked_at, r.id
        """,
        (hotel.slug, inquiry["created_at"], inquiry["created_at"] + WINDOW,
         inquiry["check_in"] - timedelta(days=DATE_TOLERANCE_DAYS), inquiry["check_in"] + timedelta(days=DATE_TOLERANCE_DAYS),
         now),
    )
    return [dict(r) for r in cur.fetchall()]


def match(cur: Any, inquiry: dict, res: dict) -> tuple[str | None, Decimal, dict]:
    """('identity' | 'review' | None, score, evidence)."""
    from clefs.core.stays import name_similarity

    hmacs = store.hmacs_of(inquiry)
    if hmacs:
        cur.execute(
            """
            SELECT kind, provenance FROM reservation_contacts WHERE reservation_id = %s AND provenance <> 'ota_alias'
              AND purged_at IS NULL AND (purge_after IS NULL OR purge_after >= %s) AND value_hmac = ANY(%s) LIMIT 1
            """,
            (res["id"], clock.now(), hmacs),
        )
        hit = cur.fetchone()
        if hit:
            return "identity", Decimal(1), {"identity": hit["kind"], "provenance": hit["provenance"]}
    if inquiry.get("contact_name") and res.get("guest_name") and inquiry.get("check_out"):
        sim = name_similarity(inquiry["contact_name"], res["guest_name"])
        if sim >= NAME_THRESHOLD and res["check_in"] == inquiry["check_in"] and res["check_out"] == inquiry["check_out"]:
            return "review", Decimal(str(round(sim, 3))), {"name_similarity": round(float(sim), 3), "dates_exact": True}
    return None, Decimal(0), {}


def run(cur: Any, hotel: Hotel) -> dict:
    leaked = reviews = 0
    for inquiry in _inquiries(cur, hotel):
        for res in candidates(cur, hotel, inquiry):
            how, s, ev = match(cur, inquiry, res)
            if how == "identity":
                record_leak(cur, hotel, inquiry["id"], res["id"], score=s, evidence=ev, method="identity")
                leaked += 1
                break
            if how == "review":
                cur.execute("INSERT INTO inq_match_reviews (hotel_slug, inquiry_id, reservation_id, kind, score, evidence) "
                            "VALUES (%s, %s, %s, 'leak', %s, %s) ON CONFLICT DO NOTHING",
                            (hotel.slug, inquiry["id"], res["id"], s, Json(ev)))
                reviews += cur.rowcount
    return {"leaked": leaked, "reviews": reviews}


def _own_commission(cur: Any, hotel: Hotel, res: dict) -> tuple[int | None, dict]:
    """Inquiry Guard's own expected commission for the internal record, used
    only when Profit Guard returns nothing."""
    from clefs.core.stays import reservation_breakdown
    from clefs.modules.profit_guard import api as pg_api

    from clefs.modules.inquiries.quotes import guarded

    breakdown = reservation_breakdown(cur, res["id"])
    got = guarded(cur, lambda: pg_api.expected_commission(cur, hotel.slug, ota_code=res["channel_code"],
                                                          breakdown=breakdown, on=res["check_in"]))
    if not got or got.get("commission_minor") is None:
        return None, {"basis": "none"}
    return int(got["commission_minor"]), {"basis": "expected", "base_minor": got.get("base_minor"),
                                          "pct": str(got.get("pct")) if got.get("pct") is not None else None,
                                          "currency": got.get("currency")}


def to_hotel_currency(cur: Any, hotel: Hotel, minor: int | None, ccy: str | None, *, on: Any) -> tuple[int | None, str]:
    """(minor, currency) in the hotel's currency. WHY: Profit Guard keeps the
    statement's currency (an Expedia statement in USD), and the leak record
    and the money-lost signal are read as hotel money; USD 50.00 stored as
    AED 50.00 understated the loss about 3.7x. Converted at the check-in
    date's rate; with no rate the amount keeps its own currency label
    (right currency, not converted) rather than a wrong one."""
    ccy = (ccy or hotel.currency).upper()
    if minor is None or ccy == hotel.currency.upper():
        return minor, hotel.currency
    try:
        return money.convert_minor(cur, int(minor), ccy, hotel.currency, on), hotel.currency
    except ClefsError:
        return int(minor), ccy


def record_leak(cur: Any, hotel: Hotel, inquiry_id: int, reservation_id: int, *, score: Decimal, evidence: dict,
                method: str) -> dict:
    """outcome leaked_to_ota, lost_reason BOOKED_VIA_OTA, Profit Guard
    informed (it fills the commission from its own statement line, else its
    expected commission). No outbound message of any kind."""
    from clefs.modules.inquiries import service, signals
    from clefs.modules.profit_guard import api as pg_api

    inquiry = store.get_inquiry(cur, hotel.slug, inquiry_id, lock=True)
    cur.execute("SELECT * FROM reservations WHERE hotel_slug = %s AND id = %s", (hotel.slug, reservation_id))
    res = dict(cur.fetchone())
    from clefs.modules.inquiries.quotes import guarded

    pg = guarded(cur, lambda: pg_api.record_ota_leak(
        cur, hotel.slug, source="inquiries", inquiry_ref=inquiry["ref"], reservation_ref=res.get("pms_res_id"),
        channel_code=res["channel_code"], evidence={"method": method, "score": str(score), **evidence},
        commission_minor=None, currency=None,
    ))
    if pg and pg.get("commission_minor") is not None:
        commission, basis, ccy = int(pg["commission_minor"]), pg.get("basis"), pg.get("currency")
    else:
        commission, own = _own_commission(cur, hotel, res)
        basis, ccy = own["basis"], own.get("currency")
    commission, ccy = to_hotel_currency(cur, hotel, commission, ccy, on=res["check_in"])
    cur.execute(
        """
        INSERT INTO inq_outcomes (inquiry_id, hotel_slug, outcome, match_score, match_evidence, leaked_reservation_id,
          leaked_channel, leaked_commission_minor, profit_guard_ref, commission_basis, currency, decided_at)
        VALUES (%s, %s, 'leaked_to_ota', %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (inquiry_id) DO UPDATE SET outcome = 'leaked_to_ota', match_score = EXCLUDED.match_score,
          match_evidence = EXCLUDED.match_evidence, leaked_reservation_id = EXCLUDED.leaked_reservation_id,
          leaked_channel = EXCLUDED.leaked_channel, leaked_commission_minor = EXCLUDED.leaked_commission_minor,
          profit_guard_ref = EXCLUDED.profit_guard_ref, commission_basis = EXCLUDED.commission_basis,
          decided_at = EXCLUDED.decided_at
        RETURNING *
        """,
        (inquiry_id, hotel.slug, score, Json({"method": method, **evidence}), reservation_id, res["channel_code"],
         commission, (pg or {}).get("finding_ref"), Json({"basis": basis}), ccy, clock.now()),
    )
    out = dict(cur.fetchone())
    if inquiry["stage"] == "lost":
        store.update_inquiry(cur, inquiry_id, {"lost_reason": "BOOKED_VIA_OTA"})
    elif service.can_transition(inquiry["stage"], "lost"):
        service.transition(cur, inquiry, "lost", reason="auto:leak_match", lost_reason="BOOKED_VIA_OTA", hotel=hotel)
    cur.execute("UPDATE inq_match_reviews SET status = 'superseded' WHERE inquiry_id = %s AND kind = 'leak' "
                "AND status = 'open' AND reservation_id <> %s", (inquiry_id, reservation_id))
    signals.upsert_leaked(cur, hotel, inquiry, channel=res["channel_code"], commission_minor=commission, currency=ccy)
    return out
