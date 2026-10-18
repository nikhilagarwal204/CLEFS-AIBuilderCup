"""Action verifiers (02 spec 6.3): pg.vcc_charge only verifies on the OTA's
or the bank's evidence dated after the decision; pg.config_fix measures
the next statement's lines; tax.td_remit reads the Tourism Dirham ledger."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest

from clefs.core import actions
from clefs.core.actions import ActionCtx
from clefs.modules.profit_guard import actions as pg_actions  # noqa: F401  (registers the types)
from clefs.modules.profit_guard import repo
from tests.modules.profit_guard.fixtures import pg_data as pg

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
DECIDED = NOW - timedelta(days=2)


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


def verify(cur, hotel, key, params, *, decided=DECIDED):
    t = actions.get_action_type(key)
    ctx = ActionCtx(cur=cur, hotel=hotel, action={"params": params, "decided_at": decided, "created_at": decided},
                    action_type=t)
    return t.verifier(ctx)


def report_batch(cur, slug, committed_at):
    """A committed VCC_REPORT import batch."""
    cur.execute(
        "INSERT INTO import_batches (hotel_slug, kind, sha256, status, committed_at, created_at) "
        "VALUES (%s, 'VCC_REPORT', %s, 'COMMITTED', %s, %s) RETURNING id",
        (slug, hashlib.sha256(str(committed_at).encode()).hexdigest(), committed_at, committed_at),
    )
    return cur.fetchone()["id"]


def card(cur, slug, *, status="AVAILABLE", balance=50000, initial=50000, batch=None, vcc_id="VC-1",
         deadline=date(2026, 10, 20)):
    cid = pg.vcc(cur, slug, vcc_id, balance=balance, deadline=deadline, status=status, ota_res_id="4000000020")
    cur.execute("UPDATE pfg_virtual_cards SET initial_minor = %s, last_seen_batch_id = %s, ota_res_id = %s "
                "WHERE id = %s", (initial, batch, "4000000020", cid))
    return cid


# ─── pg.vcc_charge ───


@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED", "NOT_LOADED"])
def test_vcc_cancelled_or_unloaded_card_with_zero_balance_is_not_verified(cur, hotel_uae, status):
    # Before the fix a zero balance verified first, whatever the status.
    b = report_batch(cur, hotel_uae.slug, NOW - timedelta(hours=3))
    cid = card(cur, hotel_uae.slug, status=status, balance=0, batch=b)
    out = verify(cur, hotel_uae, "pg.vcc_charge", {"card_id": cid})
    assert out.status == "not_verified"


def test_vcc_staff_mark_is_pending_until_a_report_confirms(cur, hotel_uae):
    s = hotel_uae.slug
    b = report_batch(cur, s, DECIDED - timedelta(days=1))
    cid = card(cur, s, batch=b)
    repo.mark_vcc_charged(cur, s, cid)                                 # a person's click, no evidence yet
    out = verify(cur, hotel_uae, "pg.vcc_charge", {"card_id": cid})
    assert out.status == "pending" and "next card report" in out.evidence[0].value
    # The OTA's next report shows the card fully charged: verified.
    b2 = report_batch(cur, s, NOW + timedelta(minutes=5))
    cur.execute("UPDATE pfg_virtual_cards SET last_seen_batch_id = %s, status = 'FULLY_CHARGED', "
                "current_balance_minor = 0 WHERE id = %s", (b2, cid))
    out = verify(cur, hotel_uae, "pg.vcc_charge", {"card_id": cid})
    assert (out.status, out.outcome_minor) == ("verified", 50000)


def test_vcc_report_older_than_the_decision_proves_nothing(cur, hotel_uae):
    b = report_batch(cur, hotel_uae.slug, DECIDED - timedelta(hours=1))
    cid = card(cur, hotel_uae.slug, status="FULLY_CHARGED", balance=0, batch=b)
    assert verify(cur, hotel_uae, "pg.vcc_charge", {"card_id": cid}).status == "pending"


def test_vcc_matched_bank_credit_verifies(cur, hotel_uae):
    s = hotel_uae.slug
    cid = card(cur, s)
    cur.execute(
        "INSERT INTO pfg_bank_credits (hotel_slug, line_key, value_date, currency, amount_minor, hotel_currency, "
        "reference) VALUES (%s, 'k1', %s, 'AED', 50000, 'AED', 'ACQ SETTLEMENT VC-1')", (s, date(2026, 10, 1)))
    out = verify(cur, hotel_uae, "pg.vcc_charge", {"card_id": cid})
    assert (out.status, out.outcome_minor) == ("verified", 50000)
    assert out.evidence[0].ref["table"] == "pfg_bank_credits"


# ─── pg.config_fix (L1b) ───

MF_TD = [{"type": "MUNICIPALITYFEE", "amount_minor": 7000}, {"type": "TOURISMFEE", "amount_minor": 4500}]


def l1b_lines(cur, slug, *, fixed, total=10, doc_date=date(2026, 10, 20)):
    """`total` stays after the fix; `fixed` of them have MF/TD out of the base."""
    lines = [{"ota_res_id": f"40000005{i:02d}", "gross_minor": 110000, "commission_rate": "0.15",
              "commissionable_minor": 98500 if i < fixed else 110000,
              "commission_minor": 14775 if i < fixed else 16500, "charges": MF_TD,
              "check_in": date(2026, 10, 5), "check_out": date(2026, 10, 8)} for i in range(total)]
    pg.statement(cur, slug, lines, doc_no="INV-NEXT", doc_date=doc_date)


def test_config_fix_setting_alone_is_not_verified(cur, hotel_uae):
    # Before the fix the channel_config flags a user typed in verified it.
    pg.channel_cfg(cur, hotel_uae.slug, mf=True, td=True)
    out = verify(cur, hotel_uae, "pg.config_fix", {"detector": "L1b", "ota_code": "BOOKING"})
    assert out.status == "pending"


def test_config_fix_verified_by_ninety_percent_of_next_month_lines(cur, hotel_uae):
    s = hotel_uae.slug
    pg.channel_cfg(cur, s, mf=True, td=True)
    l1b_lines(cur, s, fixed=9)
    out = verify(cur, hotel_uae, "pg.config_fix", {"detector": "L1b", "ota_code": "BOOKING"})
    # 9 lines x 15% x (7000 + 4500) no longer commissioned
    assert (out.status, out.outcome_minor, out.currency) == ("verified", 9 * 1725, "AED")


def test_config_fix_below_ninety_percent_fails(cur, hotel_uae):
    s = hotel_uae.slug
    pg.channel_cfg(cur, s, mf=True, td=True)
    l1b_lines(cur, s, fixed=8)
    assert verify(cur, hotel_uae, "pg.config_fix", {"detector": "L1b", "ota_code": "BOOKING"}).status == \
        "not_verified"


def test_config_fix_needs_the_setting_first(cur, hotel_uae):
    s = hotel_uae.slug
    pg.channel_cfg(cur, s, mf=False, td=True)
    l1b_lines(cur, s, fixed=10)
    assert verify(cur, hotel_uae, "pg.config_fix", {"detector": "L1b", "ota_code": "BOOKING"}).status == "pending"


def test_config_fix_ignores_statements_before_the_fix(cur, hotel_uae):
    s = hotel_uae.slug
    pg.channel_cfg(cur, s, mf=True, td=True)
    l1b_lines(cur, s, fixed=10, doc_date=date(2026, 9, 25))
    assert verify(cur, hotel_uae, "pg.config_fix", {"detector": "L1b", "ota_code": "BOOKING"}).status == "pending"
