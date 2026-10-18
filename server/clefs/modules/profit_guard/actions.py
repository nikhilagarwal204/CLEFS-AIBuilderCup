"""Profit Guard action types and their verifiers (02 spec 3.7, 3.9; Canon 7
and the autonomy dial).

| Type | Who does it | Verified when |
|---|---|---|
| pg.dispute | owner/finance approve, then submit the packet | the OTA credits it (statement, credit note, payout) |
| pg.mark_no_show | front desk reports it in the extranet before the deadline | the statement shows no commission for it |
| pg.vcc_charge | finance charges the virtual card | a later card report shows FULLY_CHARGED, or a payment matches |
| pg.config_fix | revenue changes an extranet setting | L1b: 90% of next month's lines exclude MF/TD (value: saving) |
| pg.payout_query | finance asks the OTA about a short payout | the shortfall is credited |
| tax.td_remit | finance pays the month's Tourism Dirham | td_ledger remitted = collected +- AED 1, with a reference |

Every type is a human task (no executor): Clefs never touches an OTA
extranet or a card terminal. Disputes and configuration fixes are OTA
changes, so they always need approval (Canon autonomy defaults); so do
no-show reports and card charges (folio and OTA changes). Money outcomes
count toward verified profit; a configuration fix prevents future leaks and
carries no amount of its own.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs.core import actions, clock
from clefs.core import time as htime
from clefs.core.actions import ActionType, VerifyResult
from clefs.core.hotels import get_hotel

PERM = "profit.manage"
MODULE = "profit_guard"


def _ev(label: str, value: Any = None, kind: str = "row", ref: dict | None = None) -> dict:
    return {"kind": kind, "label": label, "value": value, "ref": ref}


def _finding_ids(ctx: Any) -> list[int]:
    ids = ctx.params.get("finding_ids") or ([ctx.params["finding_id"]] if ctx.params.get("finding_id") else [])
    return [int(i) for i in ids if str(i).isdigit()]


def _findings(ctx: Any) -> list[dict]:
    ids = _finding_ids(ctx)
    if not ids:
        return []
    ctx.cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND id = ANY(%s) ORDER BY id",
                    (ctx.hotel_slug, ids))
    return [dict(r) for r in ctx.cur.fetchall()]


def _since(ctx: Any):
    a = ctx.action
    return a.get("executed_at") or a.get("done_at") or a.get("decided_at") or a.get("created_at")


# ─── pg.dispute ───


def _dispute_verify(ctx: Any) -> VerifyResult:
    from clefs.modules.profit_guard import disputes

    pid = ctx.params.get("packet_id")
    if pid is None and ctx.action_id is not None:
        # A proposal raised from a signal group carries finding_ids only;
        # its packet is built on the brief's Dispute verb and linked by
        # action_id (disputes.packet_for_action).
        ctx.cur.execute("SELECT id FROM pfg_dispute_packets WHERE hotel_slug = %s AND action_id = %s "
                        "ORDER BY id DESC LIMIT 1", (ctx.hotel_slug, ctx.action_id))
        got = ctx.cur.fetchone()
        pid = got["id"] if got else None
    if pid is None:
        return _dispute_verify_findings(ctx)
    ctx.cur.execute("SELECT * FROM pfg_dispute_packets WHERE hotel_slug = %s AND id = %s", (ctx.hotel_slug, pid))
    packet = ctx.cur.fetchone()
    if packet is None:
        return VerifyResult(status="not_verified", evidence=[_ev("packet", "missing")])
    packet = dict(packet)
    ctx.cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND id = ANY(%s)",
                    (ctx.hotel_slug, list(packet["finding_ids"])))
    return disputes.verify_result(packet, [dict(r) for r in ctx.cur.fetchall()], now=clock.now())


def _dispute_verify_findings(ctx: Any) -> VerifyResult:
    """No packet: the findings' own ledger decides (credited, rejected or
    still open). Findings that went into ANOTHER action's packet are that
    action's to count: never twice in verified value."""
    from clefs.modules.profit_guard import disputes

    rows = _findings(ctx)
    if not rows:
        return VerifyResult(status="not_verified", evidence=[_ev("findings", "none")])
    held = sorted({r["action_id"] for r in rows if r.get("action_id") and r["action_id"] != ctx.action_id})
    if held:
        return VerifyResult(status="not_verified", evidence=[_ev("counted on action", held)])
    stamp = max((r.get("updated_at") for r in rows if r.get("updated_at")), default=None)
    pseudo = {"id": None, "currency": rows[0]["currency"], "updated_at": stamp}
    return disputes.verify_result(pseudo, rows, now=clock.now())


# ─── pg.mark_no_show ───


def _no_show_verify(ctx: Any) -> VerifyResult:
    """The report worked when the OTA's statement for the booking shows the
    no-show (or a net commission of zero or less); it failed when a no-show
    commission finding (L3) was raised for it after all."""
    rows = _findings(ctx)
    ota = ctx.params.get("ota_code") or next((r["ota_code"] for r in rows), None)
    norms = {str(r.get("subject_ref") or "").partition("|")[2] for r in rows} - {""}
    if ctx.params.get("ota_res_id_norm"):
        norms.add(str(ctx.params["ota_res_id_norm"]))
    if not ota or not norms:
        return VerifyResult(status="not_verified", evidence=[_ev("booking", "unknown")])
    ccy = next((r["currency"] for r in rows), None)
    ctx.cur.execute(
        "SELECT ota_res_id_norm, SUM(commission_minor) AS net, BOOL_OR(line_status IN ('NO_SHOW', 'CANCELLED')) "
        "AS marked FROM pfg_ota_statement_lines WHERE hotel_slug = %s AND ota_code = %s AND ota_res_id_norm = ANY(%s) "
        "GROUP BY ota_res_id_norm", (ctx.hotel_slug, ota, list(norms)))
    seen = {r["ota_res_id_norm"]: dict(r) for r in ctx.cur.fetchall()}
    ctx.cur.execute("SELECT id FROM pfg_findings WHERE hotel_slug = %s AND detector = 'L3' AND ota_code = %s "
                    "AND split_part(COALESCE(subject_ref, ''), '|', 2) = ANY(%s) AND status <> 'RECOVERED'",
                    (ctx.hotel_slug, ota, list(norms)))
    charged = [r["id"] for r in ctx.cur.fetchall()]
    if charged:
        return VerifyResult(status="not_verified", evidence=[_ev("commission charged anyway", charged)])
    if norms and all(n in seen and (seen[n]["marked"] or int(seen[n]["net"] or 0) <= 0) for n in norms):
        avoided = sum(int(r["amount_minor"]) for r in rows)
        return VerifyResult(status="verified", outcome_minor=avoided, currency=ccy, attribution="caused",
                            evidence=[_ev("statement", {n: str(seen[n]["net"]) for n in norms})], confidence=0.9)
    return VerifyResult(status="pending", evidence=[_ev("statement", "not arrived yet")])


# ─── pg.vcc_charge ───


def _card(ctx: Any) -> dict | None:
    cid = ctx.params.get("card_id")
    if cid is None:
        for r in _findings(ctx):
            for e in r.get("evidence") or []:
                ref = e.get("ref") if isinstance(e, dict) else None
                if isinstance(ref, dict) and ref.get("table") == "pfg_virtual_cards":
                    cid = ref.get("id")
                    break
    if cid is None:
        return None
    ctx.cur.execute("SELECT * FROM pfg_virtual_cards WHERE hotel_slug = %s AND id = %s", (ctx.hotel_slug, cid))
    row = ctx.cur.fetchone()
    return dict(row) if row else None


def _decided(ctx: Any):
    """When the action was decided (the lower bound for evidence that it
    worked): a card report or a payment older than this proves nothing."""
    a = ctx.action
    return a.get("decided_at") or a.get("created_at")


def _report_at(ctx: Any, card: dict):
    """committed_at of the VCC report that last wrote this card, or None."""
    if not card.get("last_seen_batch_id"):
        return None
    ctx.cur.execute("SELECT committed_at, created_at FROM import_batches WHERE id = %s AND kind = 'VCC_REPORT'",
                    (card["last_seen_batch_id"],))
    row = ctx.cur.fetchone()
    return (row["committed_at"] or row["created_at"]) if row else None


def _vcc_paid(ctx: Any, card: dict, since: Any) -> dict | None:
    """A payment matched to the card after the decision: a bank credit
    whose reference names the card or its booking and covers the balance
    that was due, or an OTA payout line for the card's booking."""
    hotel = get_hotel(ctx.cur, ctx.hotel_slug)
    on = htime.local_date(hotel, since) if since else None
    due = int(card["initial_minor"] or 0) or int(card["current_balance_minor"] or 0)
    keys = [k for k in (card.get("vcc_id"), card.get("ota_res_id")) if k]
    if keys and due > 0:
        ctx.cur.execute(
            """
            SELECT id, amount_minor, reference, value_date FROM pfg_bank_credits
             WHERE hotel_slug = %s AND currency = %s AND amount_minor >= %s AND (%s::date IS NULL OR value_date >= %s)
               AND EXISTS (SELECT 1 FROM unnest(%s::text[]) k WHERE (COALESCE(reference, '') || ' ' ||
                           COALESCE(description, '')) ILIKE ('%%' || k || '%%'))
             ORDER BY value_date, id LIMIT 1
            """,
            (ctx.hotel_slug, card["currency"], due - max(100, due // 100), on, on, keys),
        )
        row = ctx.cur.fetchone()
        if row:
            return {"table": "pfg_bank_credits", "id": row["id"], "amount_minor": int(row["amount_minor"])}
    if card.get("ota_res_id_norm"):
        ctx.cur.execute(
            """
            SELECT l.id, l.partner_payout_minor FROM pfg_ota_payout_lines l JOIN pfg_ota_payouts p ON p.id = l.payout_id
             WHERE l.hotel_slug = %s AND l.ota_code = %s AND l.ota_res_id_norm = %s AND l.currency = %s
               AND l.line_kind = 'BOOKING' AND l.partner_payout_minor > 0
               AND (%s::date IS NULL OR COALESCE(l.event_date, p.payout_date) >= %s)
             ORDER BY l.id LIMIT 1
            """,
            (ctx.hotel_slug, card["ota_code"], card["ota_res_id_norm"], card["currency"], on, on),
        )
        row = ctx.cur.fetchone()
        if row:
            return {"table": "pfg_ota_payout_lines", "id": row["id"], "amount_minor": int(row["partner_payout_minor"])}
    return None


def _vcc_verify(ctx: Any) -> VerifyResult:
    """02 spec 6.3: VERIFIED only on the OTA's or the bank's evidence, dated
    after the decision: a VCC report showing FULLY_CHARGED, or a matched
    payout or bank credit. A card the OTA cancelled, let expire or never
    loaded fails first, whatever its balance reads (a zero balance on such
    a card is not a charge). A staff click on "charged" (repo.
    mark_vcc_charged) is a claim, not evidence: it stays pending until a
    report or a payment confirms it.

    A proposal from a signal group can cover several cards (card_ids): each
    is judged alone, the action stays pending while any card is, and it is
    verified with the sum of the cards that were charged (review
    completeness-01)."""
    cards = _cards(ctx)
    if not cards:
        return VerifyResult(status="not_verified", evidence=[_ev("card", "unknown")])
    if len(cards) == 1:
        return _vcc_verify_card(ctx, cards[0])
    results = [_vcc_verify_card(ctx, c) for c in cards]
    evidence = [e for r in results for e in r.evidence]
    if any(r.status == "pending" for r in results):
        return VerifyResult(status="pending", evidence=evidence)
    ok = [r for r in results if r.status == "verified"]
    if not ok:
        return VerifyResult(status="not_verified", evidence=evidence)
    ccy = ok[0].currency
    got = sum(int(r.outcome_minor or 0) for r in ok if r.currency == ccy)
    return VerifyResult(status="verified", outcome_minor=got, currency=ccy, attribution="caused", evidence=evidence)


def _cards(ctx: Any) -> list[dict]:
    ids = [int(i) for i in ctx.params.get("card_ids") or [] if str(i).isdigit()]
    if not ids:
        one = _card(ctx)
        return [one] if one is not None else []
    ctx.cur.execute("SELECT * FROM pfg_virtual_cards WHERE hotel_slug = %s AND id = ANY(%s) ORDER BY id",
                    (ctx.hotel_slug, ids))
    return [dict(r) for r in ctx.cur.fetchall()]


def _vcc_verify_card(ctx: Any, card: dict) -> VerifyResult:
    ref = {"table": "pfg_virtual_cards", "id": card["id"]}
    initial, balance = int(card["initial_minor"] or 0), int(card["current_balance_minor"] or 0)
    charged = max(0, initial - balance)
    since, report_at = _decided(ctx), _report_at(ctx, card)
    # The row's status is the report's unless a person marked it after it.
    manual = card.get("charged_at") is not None and (report_at is None or card["charged_at"] > report_at)
    if not manual and card["status"] in ("CANCELLED", "EXPIRED", "NOT_LOADED"):
        return VerifyResult(status="not_verified", outcome_minor=charged, currency=card["currency"],
                            evidence=[_ev("card report", card["status"], ref=ref)])
    fresh = report_at is not None and (since is None or report_at > since)
    if fresh and not manual and card["status"] == "FULLY_CHARGED" and initial > 0:
        return VerifyResult(status="verified", outcome_minor=charged or initial, currency=card["currency"],
                            attribution="caused", evidence=[_ev("card report", card["status"], ref=ref)])
    paid = _vcc_paid(ctx, card, since)
    if paid is not None:
        return VerifyResult(status="verified", outcome_minor=min(paid["amount_minor"], initial or paid["amount_minor"]),
                            currency=card["currency"], attribution="caused",
                            evidence=[_ev("payment matched", paid["amount_minor"],
                                          ref={"table": paid["table"], "id": paid["id"]}), _ev("card", None, ref=ref)])
    today = htime.local_date(get_hotel(ctx.cur, ctx.hotel_slug), clock.now())
    if card["charge_deadline"] and card["charge_deadline"] < today and not manual:
        return VerifyResult(status="not_verified", outcome_minor=charged, currency=card["currency"],
                            evidence=[_ev("card", card["status"], ref=ref)])
    label = "marked charged in Clefs; the next card report confirms it" if manual else card["status"]
    return VerifyResult(status="pending", evidence=[_ev("card", label, ref=ref)])


# ─── pg.config_fix ───


def _config_verify(ctx: Any) -> VerifyResult:
    """L1b: the fees configured as charges is the prerequisite; the next
    month's statement lines decide (_l1b_measure). Anything else (stacking
    caps, payout currency): no new finding of that detector for the OTA in
    the 30 days after the fix."""
    from clefs.modules.profit_guard import repo

    rows = _findings(ctx)
    det = ctx.params.get("detector") or next((r["detector"] for r in rows), None)
    ota = ctx.params.get("ota_code") or next((r["ota_code"] for r in rows), None)
    if det == "L1b" and ota:
        today = htime.local_date(get_hotel(ctx.cur, ctx.hotel_slug), clock.now())
        cfg = repo.channel_config_on(ctx.cur, ctx.hotel_slug, ota, today)
        if not (cfg.get("mf_configured_as_charge") and cfg.get("td_configured_as_charge")):
            return VerifyResult(status="pending",
                                evidence=[_ev("channel config", "fees not configured as charges yet")])
        return _l1b_measure(ctx, ota)
    since = _since(ctx)
    ctx.cur.execute("SELECT COUNT(*) AS n FROM pfg_findings WHERE hotel_slug = %s AND detector = %s "
                    "AND ota_code IS NOT DISTINCT FROM %s AND created_at > %s AND NOT (id = ANY(%s))",
                    (ctx.hotel_slug, det, ota, since, [r["id"] for r in rows] or [0]))
    n = int(ctx.cur.fetchone()["n"])
    if n:
        return VerifyResult(status="not_verified", evidence=[_ev("new findings since the fix", n)])
    if since is not None and clock.now() - since >= timedelta(days=30):
        return VerifyResult(status="verified", attribution="caused", evidence=[_ev("no new findings", 30, "metric")])
    return VerifyResult(status="pending", evidence=[_ev("watching", det)])


L1B_SHARE = Decimal("0.9")   # 02 spec 6.3: at least 90% of the next month's lines


def _l1b_lines(ctx: Any, ota: str, fix_on: date) -> list[dict]:
    """The next month's lines after the fix: every claim line on the OTA's
    documents dated in the first month that has one after the fix date,
    for stays that began on or after it (older stays kept their base)."""
    ctx.cur.execute(
        "SELECT doc_date FROM pfg_ota_statements WHERE hotel_slug = %s AND ota_code = %s AND doc_date > %s "
        "AND doc_kind IN ('COMMISSION_INVOICE', 'RES_STATEMENT', 'PAYOUT_STATEMENT') ORDER BY doc_date LIMIT 1",
        (ctx.hotel_slug, ota, fix_on))
    first = ctx.cur.fetchone()
    if first is None:
        return []
    m0 = first["doc_date"].replace(day=1)
    m1 = (m0 + timedelta(days=32)).replace(day=1)
    ctx.cur.execute(
        """
        SELECT l.* FROM pfg_ota_statement_lines l JOIN pfg_ota_statements s ON s.id = l.statement_id
         WHERE l.hotel_slug = %s AND l.ota_code = %s AND s.doc_date > %s AND s.doc_date >= %s AND s.doc_date < %s
           AND s.doc_kind IN ('COMMISSION_INVOICE', 'RES_STATEMENT', 'PAYOUT_STATEMENT')
           AND l.commission_minor > 0 AND l.gross_minor > 0 AND l.commissionable_minor IS NOT NULL
           AND (l.check_in IS NULL OR l.check_in >= %s)
         ORDER BY l.id
        """,
        (ctx.hotel_slug, ota, fix_on, m0, m1, fix_on))
    return [dict(r) for r in ctx.cur.fetchall()]


def _l1b_measure(ctx: Any, ota: str) -> VerifyResult:
    """02 spec 6.3: the channel setting is only the prerequisite. VERIFIED
    when the next month's lines exclude MF and TD from the commission base
    for at least 90% of them (reported base <= gross - MF - TD within
    tol); the value is the commission those fees no longer carry that
    month (pct x MF+TD over the lines that exclude them)."""
    from clefs.modules.profit_guard import repo
    from clefs.modules.profit_guard.detectors import data
    from clefs.modules.profit_guard.detectors import l01_commission_base as l1
    from clefs.modules.profit_guard.detectors.base import make_ctx, pct_amount, tol

    hotel = get_hotel(ctx.cur, ctx.hotel_slug)
    fixed_at = _since(ctx)
    fix_on = htime.local_date(hotel, fixed_at) if fixed_at else htime.local_date(hotel, clock.now())
    lines = _l1b_lines(ctx, ota, fix_on)
    dctx = make_ctx(ctx.cur, hotel, repo.get_policies(ctx.cur, ctx.hotel_slug), "verify:pg.config_fix")
    judged = ok = saving = 0
    ccy = None
    for ln in lines:
        fee, _inferred = l1.fees(dctx, ln, l1.MF_TD)
        if not fee:
            continue    # no MF/TD on the line (or no dates to infer them): nothing to judge
        judged += 1
        expected = int(ln["gross_minor"]) - int(fee)
        if int(ln["commissionable_minor"]) <= expected + tol(expected):
            ok += 1
            saving += pct_amount(int(fee), data.line_pct(dctx, ln) or Decimal(0))
            ccy = ccy or ln["currency"]
    if not judged:
        return VerifyResult(status="pending", evidence=[_ev("statement", "no lines after the fix yet")])
    share = Decimal(ok) / Decimal(judged)
    ev = [_ev("lines excluding MF/TD", {"ok": ok, "judged": judged, "share": str(share.quantize(Decimal("0.001")))},
              "metric")]
    if share >= L1B_SHARE:
        return VerifyResult(status="verified", outcome_minor=saving, currency=ccy or hotel.currency,
                            attribution="caused", evidence=ev, confidence=float(share))
    return VerifyResult(status="not_verified", evidence=ev)


# ─── pg.payout_query ───


def _payout_verify(ctx: Any) -> VerifyResult:
    rows = _findings(ctx)
    if not rows:
        return VerifyResult(status="not_verified", evidence=[_ev("findings", "none")])
    got = sum(int(r["recovered_minor"] or 0) for r in rows)
    ccy = rows[0]["currency"]
    if all(r["status"] == "RECOVERED" for r in rows):
        return VerifyResult(status="verified", outcome_minor=got, currency=ccy, attribution="caused",
                            evidence=[_ev("recovered", got)])
    if all(r["status"] in ("REJECTED", "UNRESOLVED", "DISMISSED") for r in rows):
        return VerifyResult(status="not_verified", outcome_minor=got, currency=ccy, evidence=[_ev("closed", got)])
    return VerifyResult(status="pending", evidence=[_ev("recovered so far", got)])


# ─── tax.td_remit ───


def _td_remit_verify(ctx: Any) -> VerifyResult:
    """02 spec 6.3: VERIFIED when the month's td_ledger row shows a
    remittance within AED 1 of what the folio collected, with a
    reference. Compliance: no money value. A remittance off by more than
    AED 1 fails (T7 raises the gap); no remittance yet, or one without its
    reference, stays pending."""
    from clefs.modules.profit_guard import tax_checks

    try:
        month = date.fromisoformat(str(ctx.params.get("month") or "")[:10]).replace(day=1)
    except ValueError:
        return VerifyResult(status="not_verified", evidence=[_ev("month", "unknown")])
    ctx.cur.execute("SELECT * FROM pfg_td_ledger WHERE hotel_slug = %s AND month = %s", (ctx.hotel_slug, month))
    row = ctx.cur.fetchone()
    ref = {"table": "pfg_td_ledger", "month": month.isoformat()}
    if row is None:
        return VerifyResult(status="pending", evidence=[_ev("TD ledger", "month not rolled up yet", ref=ref)])
    collected, remitted = int(row["td_collected_minor"] or 0), int(row["td_remitted_minor"] or 0)
    ev = [_ev("TD ledger", {"collected_minor": collected, "remitted_minor": remitted,
                            "reference": row["remit_reference"]}, ref=ref)]
    if row["remitted_on"] is None:
        return VerifyResult(status="pending", evidence=ev)
    if abs(collected - remitted) > tax_checks.LEDGER_TOL_MINOR:
        return VerifyResult(status="not_verified", evidence=ev)
    if not (row["remit_reference"] or "").strip():
        return VerifyResult(status="pending", evidence=[*ev, _ev("reference", "missing")])
    return VerifyResult(status="verified", attribution="caused", counts_toward_verified_profit=False, evidence=ev)


# ─── Registration ───

TYPES = (
    ActionType(
        key="pg.dispute", module=MODULE, label="Dispute OTA commission", verifier=_dispute_verify,
        verify_after=timedelta(days=1), verify_every=timedelta(days=1), verify_until=timedelta(days=120),
        default_owner_role="owner", default_level="approve", max_level="approve", always_approve=True,
        decide_perm=PERM, counts_toward_verified_profit=True,
        description="Approve the dispute packet (owner or finance), then paste the extranet text or email it; "
                    "Clefs verifies the credit on the OTA's next credit note, statement or payout."),
    ActionType(
        key="pg.mark_no_show", module=MODULE, label="Report a no-show or change to the OTA",
        verifier=_no_show_verify, verify_after=timedelta(days=2), verify_every=timedelta(days=1),
        verify_until=timedelta(days=75), default_owner_role="front_desk", default_level="approve",
        max_level="approve", always_approve=True, decide_perm=PERM, counts_toward_verified_profit=True,
        description="Mark the no-show, early departure or change in the extranet before the deadline (Booking.com: "
                    "48 hours after check-out) so no commission is invoiced."),
    ActionType(
        key="pg.vcc_charge", module=MODULE, label="Charge the virtual card", verifier=_vcc_verify,
        verify_after=timedelta(hours=12), verify_every=timedelta(days=1), verify_until=timedelta(days=60),
        default_owner_role="finance", default_level="approve", max_level="approve", always_approve=True,
        decide_perm=PERM, counts_toward_verified_profit=True,
        description="Charge the OTA's virtual card before its deadline; verified on the next card report."),
    ActionType(
        key="pg.config_fix", module=MODULE, label="Fix an OTA channel setting", verifier=_config_verify,
        verify_after=timedelta(days=1), verify_every=timedelta(days=1), verify_until=timedelta(days=45),
        default_owner_role="revenue", default_level="approve", max_level="approve", always_approve=True,
        decide_perm=PERM,
        description="Change the extranet setting behind a preventable leak (fees as charges, discount stacking, "
                    "payout currency); verified when the setting holds or the leak stops."),
    ActionType(
        key="pg.payout_query", module=MODULE, label="Query a short OTA payout", verifier=_payout_verify,
        verify_after=timedelta(days=3), verify_every=timedelta(days=1), verify_until=timedelta(days=90),
        default_owner_role="finance", default_level="approve", max_level="approve", decide_perm=PERM,
        counts_toward_verified_profit=True,
        description="Ask the OTA's finance support about the gap between the statement and the payout received."),
    ActionType(
        key="tax.td_remit", module=MODULE, label="Remit the Tourism Dirham", verifier=_td_remit_verify,
        verify_after=timedelta(hours=12), verify_every=timedelta(days=1), verify_until=timedelta(days=45),
        default_owner_role="finance", default_level="approve", max_level="approve", decide_perm=PERM,
        counts_toward_verified_profit=False,
        description="Pay the month's Tourism Dirham to DTCM before the 15th and record it with its reference; "
                    "verified when the ledger shows remitted = collected within AED 1."),
)


def register() -> None:
    """Idempotent (the test app imports the module more than once)."""
    for t in TYPES:
        if actions.get_action_type(t.key) is None:
            actions.register_action_type(t)


register()
