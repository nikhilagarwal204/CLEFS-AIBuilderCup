"""
Verification: inquiry -> PMS reservation (04 spec 4.9).

`verify_inquiry` scores direct-channel reservations booked between the
inquiry's arrival (-1 d) and its anchor + 14 d (A7):

    identity hmac (pms_direct contact) 0.50, name >= 0.88 0.20,
    check-in exact 0.20 (+-1 d 0.10), nights equal 0.05, amount within 5% 0.05

0.75+ verifies; 0.55-0.75 opens a match review. Paths: `payment_and_pms`
(a paid link plus a match) and `stay_completed` (no paid link; the stay
reached IN_HOUSE or CHECKED_OUT).

Ledger: exactly one action carries the outcome (the latest executed
send_payment_link, else send_quote, else a system `inquiries.direct_booking`),
every other send action of the inquiry verifies at 0 and not counted, so
verified profit is never double-counted. outcome = commission avoided (the
reference OTA's expected commission on the reservation) minus payment fees.
It counts (attribution `caused`) only when Clefs sent a reply, quote or link.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from clefs.core import clock
from clefs.core.hotels import Hotel
from clefs.core.time import local_date
from clefs.db import Json
from clefs.modules.inquiries import store

DIRECT_CHANNELS = ("DIRECT_WEB", "DIRECT_PHONE", "WALKIN", "CORPORATE")
LIVE_STATUSES = ("CONFIRMED", "IN_HOUSE", "CHECKED_OUT")
AUTO_VERIFY = Decimal("0.75")
REVIEW_BAND = Decimal("0.55")
NAME_THRESHOLD = 0.88
BOOKED_BEFORE = timedelta(days=1)
BOOKED_AFTER = timedelta(days=14)  # A7
SEND_TYPES = ("inquiries.send_payment_link", "inquiries.send_quote")
CARRIER_TYPES = ("inquiries.send_payment_link", "inquiries.send_quote", "inquiries.direct_booking")
EXECUTED = ("done", "verified", "not_verified")
VERIFY_TASK = "inquiries.verify_inquiry"

# ─── Anchor and candidates ───


def paid_links(cur: Any, inquiry_id: int) -> list[dict]:
    cur.execute("SELECT * FROM inq_payment_links WHERE inquiry_id = %s AND status IN ('paid', 'partially_paid') "
                "ORDER BY paid_at DESC NULLS LAST, id DESC", (inquiry_id,))
    return [dict(r) for r in cur.fetchall()]


def anchor(cur: Any, inquiry: dict) -> datetime:
    """The payment time, else the quote-sent time, else the last outbound,
    else the arrival."""
    paid = paid_links(cur, inquiry["id"])
    if paid and paid[0].get("paid_at"):
        return paid[0]["paid_at"]
    cur.execute("SELECT MAX(sent_at) AS at FROM inq_quotes WHERE inquiry_id = %s", (inquiry["id"],))
    sent = cur.fetchone()["at"]
    return sent or inquiry.get("last_outbound_at") or inquiry["created_at"]


def candidates(cur: Any, hotel: Hotel, inquiry: dict) -> list[dict]:
    """Direct reservations booked in the window that no OTHER inquiry has
    already verified against. WHY the exclusion: one guest often sends two
    unlinked inquiries (email then WhatsApp, or a repeat days later); both
    score the same booking at >= 0.75, and without it each would carry the
    full commission avoided, so one booking would count twice in verified
    profit. First verified wins; migration 0954 backs this with a partial
    unique index."""
    lo = inquiry["created_at"] - BOOKED_BEFORE
    hi = anchor(cur, inquiry) + BOOKED_AFTER
    cur.execute(
        """
        SELECT r.* FROM reservations r WHERE r.hotel_slug = %s AND r.channel_code = ANY(%s) AND r.status = ANY(%s)
          AND r.booked_at BETWEEN %s AND %s
          AND NOT EXISTS (SELECT 1 FROM inq_outcomes o WHERE o.verified_reservation_id = r.id
                            AND o.outcome = 'verified_direct' AND o.inquiry_id <> %s)
        ORDER BY r.booked_at, r.id LIMIT 200
        """,
        (hotel.slug, list(DIRECT_CHANNELS), list(LIVE_STATUSES), lo, hi, inquiry["id"]),
    )
    return [dict(r) for r in cur.fetchall()]


def claimed_by_other(cur: Any, reservation_id: int, inquiry_id: int) -> bool:
    """Lock the reservation row, then check whether another inquiry has
    verified against it. WHY the lock: two verify jobs for sibling inquiries
    can run at once; both passed candidates() before either wrote. The row
    lock serialises them, so the second sees the first's outcome here
    instead of tripping the unique index."""
    cur.execute("SELECT id FROM reservations WHERE id = %s FOR UPDATE", (reservation_id,))
    cur.execute("SELECT 1 FROM inq_outcomes WHERE verified_reservation_id = %s AND outcome = 'verified_direct' "
                "AND inquiry_id <> %s LIMIT 1", (reservation_id, inquiry_id))
    return cur.fetchone() is not None


# ─── Score ───


def _quote_amounts(cur: Any, inquiry_id: int) -> list[int]:
    cur.execute("SELECT total_minor FROM inq_quotes WHERE inquiry_id = %s AND status IN ('sent', 'accepted', 'expired')",
                (inquiry_id,))
    out = [int(r["total_minor"]) for r in cur.fetchall()]
    out += [int(p["paid_amount_minor"] or p["amount_minor"]) for p in paid_links(cur, inquiry_id)]
    return out


def score(cur: Any, inquiry: dict, res: dict, *, amounts: list[int] | None = None) -> tuple[Decimal, dict]:
    """(score 0..1, evidence). Identity counts only against pms_direct
    contacts: an OTA alias or OTA-provided contact is never evidence of a
    direct booking."""
    from clefs.core.stays import name_similarity

    ev: dict[str, Any] = {}
    s = Decimal(0)
    hmacs = store.hmacs_of(inquiry)
    if hmacs:
        cur.execute(
            "SELECT kind FROM reservation_contacts WHERE reservation_id = %s AND provenance = 'pms_direct' "
            "AND value_hmac = ANY(%s) AND purged_at IS NULL LIMIT 1",
            (res["id"], hmacs),
        )
        hit = cur.fetchone()
        if hit:
            s += Decimal("0.50")
            ev["identity"] = hit["kind"]
    if inquiry.get("contact_name") and res.get("guest_name"):
        sim = name_similarity(inquiry["contact_name"], res["guest_name"])
        ev["name_similarity"] = round(float(sim), 3)
        if sim >= NAME_THRESHOLD:
            s += Decimal("0.20")
    if inquiry.get("check_in"):
        days = abs((res["check_in"] - inquiry["check_in"]).days)
        ev["check_in_delta_days"] = days
        s += Decimal("0.20") if days == 0 else (Decimal("0.10") if days == 1 else 0)
    want_nights = inquiry.get("nights") or (
        (inquiry["check_out"] - inquiry["check_in"]).days if inquiry.get("check_in") and inquiry.get("check_out") else None)
    if want_nights and res.get("nights") == want_nights:
        s += Decimal("0.05")
        ev["nights_equal"] = True
    gross = res.get("gross_minor")
    amounts = amounts if amounts is not None else _quote_amounts(cur, inquiry["id"])
    if gross and any(a and abs(int(gross) - a) <= Decimal(a) * Decimal("0.05") for a in amounts):
        s += Decimal("0.05")
        ev["amount_within_5pct"] = True
    return min(s, Decimal(1)).quantize(Decimal("0.001")), ev


# ─── Attribution and money ───


def clefs_sent(cur: Any, inquiry_id: int) -> bool:
    """A Clefs-sent reply, quote or payment link exists (4.9 counting rule).
    The fixed ack does not qualify: it is author_type 'system'."""
    cur.execute(
        "SELECT 1 FROM inq_messages WHERE inquiry_id = %s AND direction = 'out' AND author_type IN ('staff', 'ai') LIMIT 1",
        (inquiry_id,),
    )
    if cur.fetchone():
        return True
    cur.execute(
        """
        SELECT 1 FROM actions WHERE type = ANY(%s) AND (params->>'inquiry_id')::bigint = %s
          AND status IN ('done', 'verified', 'not_verified') LIMIT 1
        """,
        (["inquiries.send_quote", "inquiries.send_payment_link", "inquiries.respond"], inquiry_id),
    )
    return cur.fetchone() is not None


def commission_avoided(cur: Any, hotel: Hotel, res: dict) -> tuple[int | None, dict]:
    """The reference OTA's expected commission on this reservation's own
    breakdown (Booking: room + SC + VAT, Canon K2). None when Profit Guard has
    no reference OTA or no terms."""
    from clefs.core.stays import reservation_breakdown
    from clefs.modules.profit_guard import api as pg_api

    from clefs.modules.inquiries.quotes import guarded

    ref = guarded(cur, lambda: pg_api.reference_ota(cur, hotel.slug, as_of=local_date(hotel)))
    if not ref:
        return None, {"reason": "no_reference_ota"}
    ota_code = ref[0]
    breakdown = reservation_breakdown(cur, res["id"])
    got = guarded(cur, lambda: pg_api.expected_commission(cur, hotel.slug, ota_code=ota_code, breakdown=breakdown,
                                                          on=res["check_in"]))
    if not got or got.get("commission_minor") is None:
        return None, {"reason": "no_terms", "ota_code": ota_code}
    basis = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in got.items()
             if k in ("base_minor", "pct", "basis", "base_rule", "base_components", "contract_id", "currency")}
    return int(got["commission_minor"]), {"ota_code": ota_code, **basis}


def payment_fees(cur: Any, inquiry_id: int) -> int:
    return sum(int(p.get("fee_minor") or 0) for p in paid_links(cur, inquiry_id))


# ─── Outcome ───


def get_outcome(cur: Any, inquiry_id: int) -> dict | None:
    cur.execute("SELECT * FROM inq_outcomes WHERE inquiry_id = %s", (inquiry_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _mark_verified_stage(cur: Any, hotel: Hotel, inquiry: dict, *, reason: str) -> None:
    """quoted / payment_pending -> confirmed_verified through the stage
    machine. An inquiry verified from another stage (the guest booked
    direct after a reply, or with no Clefs outbound at all) is moved here
    with the same stage-event log line, since service.TRANSITIONS has no such
    edge (cross-part request: allow new|replied|lost|future ->
    confirmed_verified)."""
    from clefs.modules.inquiries import service

    if inquiry["stage"] == "confirmed_verified":
        return
    if service.can_transition(inquiry["stage"], "confirmed_verified"):
        service.transition(cur, inquiry, "confirmed_verified", reason=reason, hotel=hotel)
        return
    frm = inquiry["stage"]
    now = clock.now()
    store.update_inquiry(cur, inquiry["id"], {"stage": "confirmed_verified", "stage_changed_at": now,
                                              "money_at_risk_minor": 0, "lost_reason": None})
    cur.execute("INSERT INTO inq_stage_events (hotel_slug, inquiry_id, from_stage, to_stage, at, actor_type, reason, data) "
                "VALUES (%s, %s, %s, 'confirmed_verified', %s, 'system', %s, %s)",
                (hotel.slug, inquiry["id"], frm, now, reason, Json({"direct_edge": True})))
    from clefs.modules.inquiries import sla

    sla.resolve_unanswered(cur, hotel.slug, inquiry["id"], resolution="stage:confirmed_verified")


def inquiry_actions(cur: Any, inquiry_id: int, types: tuple[str, ...] = CARRIER_TYPES) -> list[dict]:
    cur.execute(
        "SELECT * FROM actions WHERE type = ANY(%s) AND (params->>'inquiry_id')::bigint = %s ORDER BY executed_at DESC NULLS LAST, id DESC",
        (list(types), inquiry_id),
    )
    return [dict(r) for r in cur.fetchall()]


def carrier(cur: Any, inquiry_id: int) -> dict | None:
    """The one action that carries the outcome: the latest executed
    send_payment_link, else the latest executed send_quote, else the
    inquiry's direct_booking record."""
    acts = inquiry_actions(cur, inquiry_id)
    for t in CARRIER_TYPES:
        for a in acts:
            if a["type"] == t and (a["status"] in EXECUTED or (t == "inquiries.direct_booking" and a["status"] not in (
                    "cancelled", "failed", "expired", "rejected"))):
                return a
    return None


def record_outcome(cur: Any, hotel: Hotel, inquiry: dict, res: dict, *, match_score: Decimal, evidence: dict,
                   path: str) -> dict:
    """Write inq_outcomes (verified_direct), move the inquiry to
    confirmed_verified and attach the outcome to exactly one action."""
    from clefs.core import actions, money

    revenue = int(res.get("room_revenue_minor") or 0)
    if res.get("currency") and res["currency"] != hotel.currency and revenue:
        revenue = int(money.convert_minor(cur, revenue, res["currency"], hotel.currency, res["check_in"]))
    commission, basis = commission_avoided(cur, hotel, res)
    fees = payment_fees(cur, inquiry["id"])
    net = (commission or 0) - fees
    caused = clefs_sent(cur, inquiry["id"])
    paid = paid_links(cur, inquiry["id"])
    act = carrier(cur, inquiry["id"])
    if act is None:
        act = actions.create_action(
            cur, hotel.slug, type="inquiries.direct_booking", title=f"Direct booking from inquiry {inquiry['ref'].upper()}",
            params={"inquiry_id": inquiry["id"], "reservation_id": res["id"]}, expected_minor=net,
            currency=hotel.currency, owner_role="gm", dedupe_key=f"inq.direct_booking:{inquiry['id']}",
            actor=actions.SYSTEM,
        )
    cur.execute(
        """
        INSERT INTO inq_outcomes (inquiry_id, hotel_slug, outcome, verification_path, attribution,
          counts_toward_verified_profit, verified_reservation_id, match_score, match_evidence, payment_link_id,
          verified_revenue_minor, commission_avoided_minor, payment_fee_minor, outcome_net_minor, commission_basis,
          currency, decided_at, action_id)
        VALUES (%s, %s, 'verified_direct', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (inquiry_id) DO UPDATE SET outcome = 'verified_direct', verification_path = EXCLUDED.verification_path,
          attribution = EXCLUDED.attribution, counts_toward_verified_profit = EXCLUDED.counts_toward_verified_profit,
          verified_reservation_id = EXCLUDED.verified_reservation_id, match_score = EXCLUDED.match_score,
          match_evidence = EXCLUDED.match_evidence, payment_link_id = EXCLUDED.payment_link_id,
          verified_revenue_minor = EXCLUDED.verified_revenue_minor,
          commission_avoided_minor = EXCLUDED.commission_avoided_minor, payment_fee_minor = EXCLUDED.payment_fee_minor,
          outcome_net_minor = EXCLUDED.outcome_net_minor, commission_basis = EXCLUDED.commission_basis,
          currency = EXCLUDED.currency, decided_at = EXCLUDED.decided_at, action_id = EXCLUDED.action_id
        RETURNING *
        """,
        (inquiry["id"], hotel.slug, path, "caused" if caused else "influenced", caused, res["id"], match_score,
         Json(evidence), paid[0]["id"] if paid else None, revenue, commission, fees, net, Json(basis), hotel.currency,
         clock.now(), act.get("id")),
    )
    outcome = dict(cur.fetchone())
    _mark_verified_stage(cur, hotel, inquiry, reason=f"verified:{path}")
    cur.execute("UPDATE inq_match_reviews SET status = 'superseded' WHERE inquiry_id = %s AND kind = 'verify' "
                "AND status = 'open'", (inquiry["id"],))
    from clefs.modules.inquiries import signals

    signals.resolve_paid_not_in_pms(cur, hotel.slug, inquiry["id"])
    attach(cur, hotel, inquiry["id"])
    return outcome


# ─── Ledger attachment ───


def verify_result_for(action: dict, outcome: dict | None) -> dict:
    """The VerifyResult an inquiry action gets from the outcome. The carrier
    takes the net value; every other send action 0 and not counted."""
    if outcome is None:
        return {"status": "pending"}
    if outcome["outcome"] != "verified_direct":
        return {"status": "not_verified", "evidence": [{"kind": "row", "label": "Outcome", "value": outcome["outcome"]}]}
    ev = [{"kind": "calc", "label": "Commission avoided", "value": str(outcome.get("commission_avoided_minor"))},
          {"kind": "calc", "label": "Payment fees", "value": str(outcome.get("payment_fee_minor"))},
          {"kind": "row", "label": "Reservation", "value": str(outcome.get("verified_reservation_id")),
           "ref": {"reservation_id": outcome.get("verified_reservation_id")}}]
    if outcome.get("action_id") == action["id"]:
        return {"status": "verified", "outcome_minor": int(outcome["outcome_net_minor"] or 0),
                "currency": outcome["currency"], "attribution": outcome["attribution"],
                "counts_toward_verified_profit": bool(outcome["counts_toward_verified_profit"]), "evidence": ev,
                "confidence": float(outcome.get("match_score") or 1)}
    return {"status": "verified", "outcome_minor": 0, "currency": outcome["currency"], "attribution": "influenced",
            "counts_toward_verified_profit": False, "evidence": ev}


def attach(cur: Any, hotel: Hotel, inquiry_id: int) -> int:
    """Record the verification on every inquiry action waiting for it
    (status done). Idempotent: an action already verified is left alone."""
    from clefs.core import actions

    outcome = get_outcome(cur, inquiry_id)
    if outcome is None:
        return 0
    n = 0
    for a in inquiry_actions(cur, inquiry_id):
        if a["status"] != "done":
            continue
        vr = verify_result_for(a, outcome)
        if vr["status"] == "pending":
            continue
        actions.record_verification(cur, a["id"], result=vr)
        n += 1
    return n


def verifier(ctx: Any) -> dict:
    """ActionType.verifier of the send and direct-booking types, called by
    core.actions.verify: the same answer attach() records."""
    inquiry_id = ctx.params.get("inquiry_id")
    if not inquiry_id:
        return {"status": "not_verified"}
    return verify_result_for(ctx.action, get_outcome(ctx.cur, int(inquiry_id)))


# ─── The job ───


def _reviews(cur: Any, inquiry_id: int) -> dict[int, str]:
    cur.execute("SELECT reservation_id, status FROM inq_match_reviews WHERE inquiry_id = %s AND kind = 'verify'",
                (inquiry_id,))
    return {int(r["reservation_id"]): r["status"] for r in cur.fetchall()}


def verify_inquiry(cur: Any, hotel: Hotel, inquiry_id: int) -> dict:
    """Score, then verify, open reviews, or wait. A confirmed review counts
    as a full match; a rejected one is never proposed again."""
    inquiry = store.get_inquiry(cur, hotel.slug, inquiry_id, lock=True)
    done = get_outcome(cur, inquiry_id)
    if done and done["outcome"] in ("verified_direct", "leaked_to_ota", "cancelled_after_confirm"):
        attach(cur, hotel, inquiry_id)
        return {"status": done["outcome"]}
    if inquiry["stage"] in ("spam", "rerouted", "turned_down") or inquiry.get("duplicate_of"):
        return {"status": "skipped"}
    reviews = _reviews(cur, inquiry_id)
    amounts = _quote_amounts(cur, inquiry_id)
    scored: list[tuple[Decimal, dict, dict]] = []
    for res in candidates(cur, hotel, inquiry):
        state = reviews.get(int(res["id"]))
        if state == "rejected":
            continue
        s, ev = score(cur, inquiry, res, amounts=amounts)
        if state == "confirmed":
            s, ev = Decimal(1), {**ev, "confirmed_by_staff": True}
        scored.append((s, ev, res))
    scored.sort(key=lambda t: (t[0], t[2]["id"]), reverse=True)
    paid = paid_links(cur, inquiry_id)
    # A sibling inquiry may have claimed the top match since candidates()
    # ran (concurrent job): drop it and fall through to the next best.
    while scored and scored[0][0] >= AUTO_VERIFY and claimed_by_other(cur, int(scored[0][2]["id"]), inquiry_id):
        scored.pop(0)
    if scored and scored[0][0] >= AUTO_VERIFY:
        s, ev, res = scored[0]
        if paid:
            path = "payment_and_pms"
        elif res["status"] in ("IN_HOUSE", "CHECKED_OUT"):
            path = "stay_completed"
        else:
            return {"status": "pending", "reservation_id": res["id"], "reason": "awaiting_payment_or_stay"}
        out = record_outcome(cur, hotel, inquiry, res, match_score=s, evidence=ev, path=path)
        return {"status": "verified", "path": path, "reservation_id": res["id"], "outcome_net_minor": out["outcome_net_minor"]}
    opened = 0
    for s, ev, res in scored:
        if REVIEW_BAND <= s < AUTO_VERIFY and int(res["id"]) not in reviews:
            cur.execute("INSERT INTO inq_match_reviews (hotel_slug, inquiry_id, reservation_id, kind, score, evidence) "
                        "VALUES (%s, %s, %s, 'verify', %s, %s) ON CONFLICT DO NOTHING",
                        (hotel.slug, inquiry_id, res["id"], s, Json(ev)))
            opened += cur.rowcount
    if paid and not any(s >= REVIEW_BAND for s, _, _ in scored):
        paid_not_in_pms(cur, hotel, inquiry, paid)
    return {"status": "review" if opened else "unmatched", "reviews_opened": opened}


def paid_not_in_pms(cur: Any, hotel: Hotel, inquiry: dict, paid: list[dict]) -> dict | None:
    """48 h after a payment with no PMS reservation: a front-desk task and
    the inq.paid_not_in_pms signal (impact = the paid amount)."""
    from clefs.core import actions
    from clefs.modules.inquiries import signals

    first = min((p["paid_at"] for p in paid if p.get("paid_at")), default=None)
    if first is None or clock.now() - first < timedelta(hours=48):
        return None
    total = sum(int(p.get("paid_amount_minor") or p["amount_minor"]) for p in paid)
    act = actions.create_action(
        cur, hotel.slug, type="inquiries.create_pms_reservation",
        title=f"Create the PMS reservation for inquiry {inquiry['ref'].upper()} (paid)",
        params={"inquiry_id": inquiry["id"], "paid_minor": total}, owner_role="front_desk",
        dedupe_key=f"inq.create_pms_reservation:{inquiry['id']}", actor=actions.SYSTEM,
    )
    signals.upsert_paid_not_in_pms(cur, hotel, inquiry, paid_minor=total, currency=paid[0]["currency"],
                                   action_id=act.get("id"))
    return act


def due_for_verification(cur: Any, hotel: Hotel, *, limit: int = 500) -> list[int]:
    """Inquiries worth scoring this hour: not verified or leaked yet, not
    junk, arrived in the last 180 days and not long past their stay."""
    cur.execute(
        """
        SELECT i.id FROM inq_inquiries i LEFT JOIN inq_outcomes o ON o.inquiry_id = i.id
        WHERE i.hotel_slug = %s AND i.duplicate_of IS NULL AND NOT i.is_test
          AND i.stage NOT IN ('spam', 'rerouted', 'turned_down', 'confirmed_verified')
          AND (o.inquiry_id IS NULL OR o.outcome IN ('lost', 'turned_down'))
          AND i.created_at >= %s AND (i.check_out IS NULL OR i.check_out >= %s)
        ORDER BY i.id LIMIT %s
        """,
        (hotel.slug, clock.now() - timedelta(days=180), local_date(hotel) - timedelta(days=14), limit),
    )
    return [int(r["id"]) for r in cur.fetchall()]


def run(cur: Any, hotel: Hotel) -> dict:
    counts: dict[str, int] = {}
    for iid in due_for_verification(cur, hotel):
        st = verify_inquiry(cur, hotel, iid)["status"]
        counts[st] = counts.get(st, 0) + 1
    cur.execute("SELECT DISTINCT o.inquiry_id FROM inq_outcomes o JOIN actions a ON a.status = 'done' "
                "AND (a.params->>'inquiry_id')::bigint = o.inquiry_id AND a.type = ANY(%s) WHERE o.hotel_slug = %s",
                (list(CARRIER_TYPES), hotel.slug))
    attached = sum(attach(cur, hotel, int(r["inquiry_id"])) for r in cur.fetchall())
    return {**counts, "attached": attached}


# ─── Reversals (4.9: a refund or a PMS cancellation) ───


def reverse(cur: Any, hotel: Hotel, inquiry_id: int, *, reason: str) -> bool:
    """outcome -> cancelled_after_confirm; the carrier's verified value is
    reversed (core reverse_verification: value 0, status stays verified).
    The stage is unchanged (4.2)."""
    from clefs.core import actions

    out = get_outcome(cur, inquiry_id)
    if out is None or out["outcome"] != "verified_direct":
        return False
    cur.execute("UPDATE inq_outcomes SET outcome = 'cancelled_after_confirm', counts_toward_verified_profit = FALSE, "
                "decided_at = %s WHERE inquiry_id = %s", (clock.now(), inquiry_id))
    if out.get("action_id"):
        cur.execute("SELECT status, reversed_at FROM actions WHERE id = %s", (out["action_id"],))
        a = cur.fetchone()
        if a and a["status"] == "verified" and a["reversed_at"] is None:
            actions.reverse_verification(cur, int(out["action_id"]), reason=reason,
                                         evidence=[{"kind": "row", "label": "Reason", "value": reason}])
        elif a and a["status"] == "done":
            actions.record_verification(cur, int(out["action_id"]), status="not_verified",
                                        evidence=[{"kind": "row", "label": "Reason", "value": reason}])
    return True


def on_refund(cur: Any, hotel: Hotel, link: dict) -> bool:
    return reverse(cur, hotel, int(link["inquiry_id"]), reason=f"refund:link:{link['id']}")


# ─── Match reviews ───


def decide_review(cur: Any, hotel: Hotel, review_id: int, *, confirm: bool, actor: Any) -> dict:
    """A person settles a 0.55-0.75 match. Verify: a confirmation counts as a
    full match and the inquiry is re-scored now. Leak: a confirmation records
    the leak (internal only)."""
    from clefs.audit import audit
    from clefs.core.errors import ClefsError, not_found

    cur.execute("SELECT * FROM inq_match_reviews WHERE hotel_slug = %s AND id = %s FOR UPDATE", (hotel.slug, review_id))
    row = cur.fetchone()
    if row is None:
        raise not_found("match review not found")
    row = dict(row)
    if row["status"] != "open":
        raise ClefsError(409, f"this review is {row['status']}", "review_closed")
    user_id = getattr(getattr(actor, "principal", actor), "user_id", None)
    status = "confirmed" if confirm else "rejected"
    cur.execute("UPDATE inq_match_reviews SET status = %s, decided_by = %s, decided_at = %s WHERE id = %s",
                (status, user_id, clock.now(), review_id))
    audit(cur, actor, f"inquiries.match_{status}", "inq_match_review", review_id,
          after={"kind": row["kind"], "reservation_id": row["reservation_id"]}, hotel_slug=hotel.slug)
    result: dict[str, Any] = {"id": review_id, "status": status, "kind": row["kind"]}
    if confirm and row["kind"] == "verify":
        result["verification"] = verify_inquiry(cur, hotel, int(row["inquiry_id"]))
    elif confirm and row["kind"] == "leak":
        from clefs.modules.inquiries import leak

        result["leak"] = leak.record_leak(cur, hotel, int(row["inquiry_id"]), int(row["reservation_id"]),
                                          score=Decimal(str(row["score"] or 1)), evidence=row.get("evidence") or {},
                                          method="review")
    return result


def list_reviews(cur: Any, hotel_slug: str, *, status: str = "open", kind: str | None = None) -> list[dict]:
    cur.execute(
        """
        SELECT m.*, i.ref, r.check_in AS res_check_in, r.check_out AS res_check_out, r.channel_code, r.pms_res_id,
               r.guest_name AS res_guest_name
        FROM inq_match_reviews m JOIN inq_inquiries i ON i.id = m.inquiry_id JOIN reservations r ON r.id = m.reservation_id
        WHERE m.hotel_slug = %s AND m.status = %s AND (%s::text IS NULL OR m.kind = %s) ORDER BY m.id DESC LIMIT 200
        """,
        (hotel_slug, status, kind, kind),
    )
    return [dict(r) for r in cur.fetchall()]


# ─── Stay events (inquiries.verify_on_stay) ───


def on_stay_event(cur: Any, ev: dict) -> None:
    """created (direct channel), checked_in / checked_out: re-score the
    inquiries this stay could belong to. cancelled: a verified outcome on
    this reservation is reversed (cancelled_after_confirm)."""
    from clefs import jobs
    from clefs.core.hotels import find_hotel, module_enabled

    hotel = find_hotel(cur, ev["hotel_slug"])
    if hotel is None or not module_enabled(cur, hotel.slug, "inquiries"):
        return
    cur.execute("SELECT * FROM reservations WHERE id = %s", (ev["reservation_id"],))
    res = cur.fetchone()
    if res is None:
        return
    if ev["event"] in ("cancelled", "no_show"):
        cur.execute("SELECT inquiry_id FROM inq_outcomes WHERE verified_reservation_id = %s AND outcome = 'verified_direct'",
                    (res["id"],))
        for r in cur.fetchall():
            reverse(cur, hotel, int(r["inquiry_id"]), reason=f"pms_{ev['event']}:{res['id']}")
        return
    if ev["event"] not in ("created", "checked_in", "checked_out") or res["channel_code"] not in DIRECT_CHANNELS:
        return
    cur.execute(
        """
        SELECT id FROM inq_inquiries WHERE hotel_slug = %s AND duplicate_of IS NULL
          AND stage NOT IN ('spam', 'rerouted', 'turned_down', 'confirmed_verified')
          AND check_in BETWEEN %s AND %s
        """,
        (hotel.slug, res["check_in"] - timedelta(days=1), res["check_in"] + timedelta(days=1)),
    )
    for r in cur.fetchall():
        jobs.enqueue(VERIFY_TASK, {"inquiry_id": r["id"]}, hotel_slug=hotel.slug,
                     dedupe_key=f"inq.verify:{r['id']}:stay:{ev['id']}", cur=cur)


# ─── For api.verified_outcomes (frozen signature; the api owner delegates here) ───


def verified_outcomes(cur: Any, hotel_slug: str, *, start: Any, end: Any) -> list[dict]:
    """Inquiries verified as direct bookings with decided_at in the hotel-local
    [start, end]: [{inquiry_ref, action_id, outcome_minor, currency,
    attribution, verified_at}]. outcome_minor is the net commission avoided
    (0 once reversed, which also drops the row)."""
    from clefs.core.hotels import get_hotel
    from clefs.core.time import local_day_bounds

    hotel = get_hotel(cur, hotel_slug)
    t0 = local_day_bounds(hotel, start)[0]
    t1 = local_day_bounds(hotel, end)[1]
    cur.execute(
        """
        SELECT i.ref AS inquiry_ref, o.action_id, o.outcome_net_minor AS outcome_minor, o.currency, o.attribution,
               o.decided_at AS verified_at
        FROM inq_outcomes o JOIN inq_inquiries i ON i.id = o.inquiry_id
        WHERE o.hotel_slug = %s AND o.outcome = 'verified_direct' AND o.decided_at >= %s AND o.decided_at < %s
        ORDER BY o.decided_at
        """,
        (hotel_slug, t0, t1),
    )
    return [dict(r) for r in cur.fetchall()]
