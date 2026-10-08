"""
Profit Guard storage helpers: owner policies, channel configuration,
programs, statement / payout / card / bank reads, rate evidence.

Every function joins the caller's transaction (CONTRACTS 3: pass `cur`
down, never open a nested db() block). Writes that change how money is
computed (policies, channel config, programs) are audited, because a
finding's amount must be explainable from the settings in force when it
was computed.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs import db
from clefs.audit import audit
from clefs.core import clock, money, stays
from clefs.core.errors import ClefsError
from clefs.modules.profit_guard.models import normalize_ota

# ─── Serialization (Canon 2: Decimal strings in APIs) ───


def _user_id(actor: Any) -> int | None:
    principal = getattr(actor, "principal", actor)
    return getattr(principal, "user_id", None) if principal is not None else None


def out(row: dict | None, *, money_fields: tuple[str, ...] = (), currency_field: str = "currency") -> dict | None:
    """A DB row for JSON: Decimals become strings, each `<x>_minor` money
    field also gets its `<x>` Decimal-string twin in the row's currency."""
    if row is None:
        return None
    res: dict[str, Any] = {}
    for k, v in dict(row).items():
        if isinstance(v, Decimal):
            res[k] = str(v)
        elif isinstance(v, (bytes, memoryview)):
            continue
        else:
            res[k] = str(v) if k.endswith("file_id") and v is not None else v
    ccy = res.get(currency_field)
    for f in money_fields:
        if f in res and f.endswith("_minor"):
            res[f.removesuffix("_minor")] = money.money_str(res[f], ccy) if res[f] is not None and ccy else None
    return res


# ─── Owner policies ───

POLICY_DEFAULTS: dict[str, Any] = {
    "max_total_discount_pct": Decimal("25"),
    "parity_tolerance_pct": Decimal("1.0"),
    "min_finding_minor": 2500,
    "confidence_floor": Decimal("0.5"),
    "treat_sc_as_revenue": True,
    "reference_ota": None,
}


def get_policies(cur: Any, hotel_slug: str) -> dict:
    """The hotel's policies with the 02 spec 3.1 defaults for a missing row
    (`stored` False then), so readers never branch on absence."""
    cur.execute("SELECT * FROM pfg_owner_policies WHERE hotel_slug = %s", (hotel_slug,))
    row = cur.fetchone()
    if row is None:
        return {"hotel_slug": hotel_slug, **POLICY_DEFAULTS, "stored": False, "updated_at": None, "updated_by": None}
    return {**dict(row), "stored": True}


def put_policies(cur: Any, hotel_slug: str, changes: dict, *, actor: Any = None) -> dict:
    """Partial update: keys left out (or None) keep their value."""
    before = get_policies(cur, hotel_slug)
    merged = {k: before.get(k) for k in POLICY_DEFAULTS}
    merged.update({k: v for k, v in changes.items() if k in POLICY_DEFAULTS and v is not None})
    if "reference_ota" in changes:
        merged["reference_ota"] = normalize_ota(changes["reference_ota"])
    cur.execute(
        """
        INSERT INTO pfg_owner_policies (hotel_slug, max_total_discount_pct, parity_tolerance_pct, min_finding_minor,
            confidence_floor, treat_sc_as_revenue, reference_ota, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug) DO UPDATE SET
            max_total_discount_pct = EXCLUDED.max_total_discount_pct,
            parity_tolerance_pct = EXCLUDED.parity_tolerance_pct, min_finding_minor = EXCLUDED.min_finding_minor,
            confidence_floor = EXCLUDED.confidence_floor, treat_sc_as_revenue = EXCLUDED.treat_sc_as_revenue,
            reference_ota = EXCLUDED.reference_ota, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        (hotel_slug, merged["max_total_discount_pct"], merged["parity_tolerance_pct"], merged["min_finding_minor"],
         merged["confidence_floor"], merged["treat_sc_as_revenue"], merged["reference_ota"], _user_id(actor),
         clock.now()),
    )
    after = {**dict(cur.fetchone()), "stored": True}
    audit(cur, actor, "profit.policies.update", "pfg_owner_policies", hotel_slug, hotel_slug=hotel_slug,
          before=out(before), after=out(after))
    return after


# ─── Channel configuration (date-effective) ───

# No row yet: the commission base follows the contract as written, which
# assumes the property configured MF and TD as charges (Canon K2). L1b is
# the detector that asks the owner to confirm; the default keeps expected
# commission (and "commission avoided") on the conservative, lower side.
CHANNEL_DEFAULTS: dict[str, Any] = {
    "mf_configured_as_charge": True,
    "td_configured_as_charge": True,
    "card_fee_pct": None,
    "payments_fee_pct": None,
    "booking_engine_fee_pct": None,
}


def list_channel_config(cur: Any, hotel_slug: str, *, ota_code: str | None = None) -> list[dict]:
    cur.execute(
        "SELECT * FROM pfg_channel_config WHERE hotel_slug = %s AND (%s::text IS NULL OR ota_code = %s) "
        "ORDER BY ota_code, configured_from DESC",
        (hotel_slug, ota_code, ota_code),
    )
    return [dict(r) for r in cur.fetchall()]


def channel_config_on(cur: Any, hotel_slug: str, ota_code: str, on: date) -> dict:
    """The configuration in force on `on` (newest `configured_from` on or
    before it), with the defaults for unset values; `stored` says whether a
    row decided it."""
    cur.execute(
        "SELECT * FROM pfg_channel_config WHERE hotel_slug = %s AND ota_code = %s AND configured_from <= %s "
        "ORDER BY configured_from DESC LIMIT 1",
        (hotel_slug, normalize_ota(ota_code), on),
    )
    row = cur.fetchone()
    res: dict[str, Any] = {"ota_code": normalize_ota(ota_code), "configured_from": None, "stored": row is not None}
    for k, dflt in CHANNEL_DEFAULTS.items():
        v = row.get(k) if row is not None else None
        res[k] = dflt if v is None else v
    if row is not None:
        res["configured_from"] = row["configured_from"]
    return res


def put_channel_config(cur: Any, hotel_slug: str, data: dict, *, actor: Any = None) -> dict:
    ota = normalize_ota(data["ota_code"])
    cur.execute(
        """
        INSERT INTO pfg_channel_config (hotel_slug, ota_code, configured_from, mf_configured_as_charge,
            td_configured_as_charge, card_fee_pct, payments_fee_pct, booking_engine_fee_pct, note, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, ota_code, configured_from) DO UPDATE SET
            mf_configured_as_charge = EXCLUDED.mf_configured_as_charge,
            td_configured_as_charge = EXCLUDED.td_configured_as_charge, card_fee_pct = EXCLUDED.card_fee_pct,
            payments_fee_pct = EXCLUDED.payments_fee_pct, booking_engine_fee_pct = EXCLUDED.booking_engine_fee_pct,
            note = EXCLUDED.note, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        (hotel_slug, ota, data["configured_from"], data.get("mf_configured_as_charge"),
         data.get("td_configured_as_charge"), data.get("card_fee_pct"), data.get("payments_fee_pct"),
         data.get("booking_engine_fee_pct"), data.get("note"), _user_id(actor), clock.now()),
    )
    row = dict(cur.fetchone())
    audit(cur, actor, "profit.channel_config.put", "pfg_channel_config", f"{ota}:{row['configured_from']}",
          hotel_slug=hotel_slug, after=out(row))
    return row


def delete_channel_config(cur: Any, hotel_slug: str, ota_code: str, configured_from: date, *, actor: Any = None) -> None:
    cur.execute(
        "DELETE FROM pfg_channel_config WHERE hotel_slug = %s AND ota_code = %s AND configured_from = %s RETURNING *",
        (hotel_slug, normalize_ota(ota_code), configured_from),
    )
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "No channel configuration from that date", "channel_config_not_found")
    audit(cur, actor, "profit.channel_config.delete", "pfg_channel_config", f"{row['ota_code']}:{configured_from}",
          hotel_slug=hotel_slug, before=out(dict(row)))


# ─── Programs ───

PROGRAM_COLS = ("ota_code", "program", "kind", "pct", "fixed_minor", "currency", "booker_countries", "stay_from",
                "stay_to", "rate_plans", "stackable", "source", "label", "is_active")


def _program_values(hotel_currency: str, data: dict) -> dict:
    vals = {k: data.get(k) for k in PROGRAM_COLS if k in data}
    vals["ota_code"] = normalize_ota(data["ota_code"])
    ccy = (data.get("currency") or hotel_currency).upper()
    vals["currency"] = ccy if data.get("fixed") is not None else data.get("currency")
    if data.get("fixed") is not None:
        vals["fixed_minor"] = money.to_minor(data["fixed"], ccy)
    vals.pop("fixed", None)
    vals["booker_countries"] = sorted({c.strip().upper() for c in data.get("booker_countries") or [] if c.strip()})
    vals["rate_plans"] = [p.strip() for p in data.get("rate_plans") or [] if p.strip()]
    if vals.get("pct") is None and vals.get("fixed_minor") is None:
        raise ClefsError(400, "A program needs a percentage or a fixed amount", "program_amount_required")
    if data.get("stay_from") and data.get("stay_to") and data["stay_to"] < data["stay_from"]:
        raise ClefsError(400, "stay_to is before stay_from", "invalid_range")
    return vals


def list_programs(cur: Any, hotel_slug: str, *, ota_code: str | None = None, active_only: bool = False) -> list[dict]:
    cur.execute(
        "SELECT * FROM pfg_ota_programs WHERE hotel_slug = %s AND (%s::text IS NULL OR ota_code = %s) "
        "AND (NOT %s OR is_active) ORDER BY ota_code, program, id",
        (hotel_slug, ota_code, ota_code, active_only),
    )
    return [dict(r) for r in cur.fetchall()]


def get_program(cur: Any, hotel_slug: str, program_id: int) -> dict:
    cur.execute("SELECT * FROM pfg_ota_programs WHERE hotel_slug = %s AND id = %s", (hotel_slug, program_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Program not found", "program_not_found")
    return dict(row)


def create_program(cur: Any, hotel_slug: str, hotel_currency: str, data: dict, *, actor: Any = None) -> dict:
    vals = _program_values(hotel_currency, data)
    cols = list(vals)
    cur.execute(
        f"INSERT INTO pfg_ota_programs (hotel_slug, created_by, {', '.join(cols)}) "
        f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))}) RETURNING *",
        (hotel_slug, _user_id(actor), *vals.values()),
    )
    row = dict(cur.fetchone())
    audit(cur, actor, "profit.program.create", "pfg_ota_program", row["id"], hotel_slug=hotel_slug, after=out(row))
    return row


def update_program(cur: Any, hotel_slug: str, hotel_currency: str, program_id: int, data: dict, *,
                   actor: Any = None) -> dict:
    before = get_program(cur, hotel_slug, program_id)
    vals = _program_values(hotel_currency, data)
    sets = ", ".join(f"{k} = %s" for k in vals)
    cur.execute(f"UPDATE pfg_ota_programs SET {sets}, updated_at = %s WHERE id = %s RETURNING *",
                (*vals.values(), clock.now(), program_id))
    row = dict(cur.fetchone())
    audit(cur, actor, "profit.program.update", "pfg_ota_program", program_id, hotel_slug=hotel_slug,
          before=out(before), after=out(row))
    return row


def delete_program(cur: Any, hotel_slug: str, program_id: int, *, actor: Any = None) -> None:
    before = get_program(cur, hotel_slug, program_id)
    cur.execute("DELETE FROM pfg_ota_programs WHERE id = %s", (program_id,))
    audit(cur, actor, "profit.program.delete", "pfg_ota_program", program_id, hotel_slug=hotel_slug,
          before=out(before))


def active_programs(cur: Any, hotel_slug: str, ota_code: str, on: date, *, booker_country: str | None = None,
                    rate_plan: str | None = None) -> list[dict]:
    """Programs that apply to one stay: active, the stay date inside the
    window, the booker country listed (or none listed) and the rate plan
    listed (or none listed). An unknown booker country matches only
    programs without a country list; the caller lowers its confidence for
    country-scoped uplifts (02 spec 3.3 L2)."""
    rows = list_programs(cur, hotel_slug, ota_code=normalize_ota(ota_code), active_only=True)
    res = []
    for p in rows:
        if p["stay_from"] and on < p["stay_from"] or p["stay_to"] and on > p["stay_to"]:
            continue
        if p["booker_countries"] and (booker_country or "").upper() not in p["booker_countries"]:
            continue
        if p["rate_plans"] and rate_plan not in p["rate_plans"]:
            continue
        res.append(p)
    return res


# ─── Statements, payouts, cards, bank credits (reads) ───

LINE_MONEY = ("gross_minor", "commissionable_minor", "commission_minor")
PAYOUT_LINE_MONEY = ("total_amount_paid_minor", "commissionable_minor", "commission_minor", "charges_minor",
                     "partner_payout_minor")


def list_statements(cur: Any, hotel_slug: str, *, ota_code: str | None = None, doc_kind: str | None = None,
                    limit: int = 100) -> list[dict]:
    cur.execute(
        """
        SELECT * FROM pfg_ota_statements WHERE hotel_slug = %s AND (%s::text IS NULL OR ota_code = %s)
           AND (%s::text IS NULL OR doc_kind = %s) ORDER BY COALESCE(period_end, doc_date) DESC NULLS LAST, id DESC
         LIMIT %s
        """,
        (hotel_slug, ota_code, ota_code, doc_kind, doc_kind, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def get_statement(cur: Any, hotel_slug: str, statement_id: int) -> dict:
    cur.execute("SELECT * FROM pfg_ota_statements WHERE hotel_slug = %s AND id = %s", (hotel_slug, statement_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Statement not found", "statement_not_found")
    cur.execute("SELECT * FROM pfg_ota_statement_lines WHERE statement_id = %s ORDER BY id", (statement_id,))
    return {**dict(row), "lines": [dict(r) for r in cur.fetchall()]}


def list_payouts(cur: Any, hotel_slug: str, *, ota_code: str | None = None, limit: int = 100) -> list[dict]:
    cur.execute(
        "SELECT * FROM pfg_ota_payouts WHERE hotel_slug = %s AND (%s::text IS NULL OR ota_code = %s) "
        "ORDER BY payout_date DESC NULLS LAST, id DESC LIMIT %s",
        (hotel_slug, ota_code, ota_code, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def get_payout(cur: Any, hotel_slug: str, payout_id: int) -> dict:
    cur.execute("SELECT * FROM pfg_ota_payouts WHERE hotel_slug = %s AND id = %s", (hotel_slug, payout_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Payout not found", "payout_not_found")
    cur.execute("SELECT * FROM pfg_ota_payout_lines WHERE payout_id = %s ORDER BY id", (payout_id,))
    return {**dict(row), "lines": [dict(r) for r in cur.fetchall()]}


def list_bank_credits(cur: Any, hotel_slug: str, *, start: date | None = None, end: date | None = None,
                      limit: int = 200) -> list[dict]:
    cur.execute(
        """
        SELECT * FROM pfg_bank_credits WHERE hotel_slug = %s AND (%s::date IS NULL OR value_date >= %s)
           AND (%s::date IS NULL OR value_date <= %s) ORDER BY value_date DESC, id DESC LIMIT %s
        """,
        (hotel_slug, start, start, end, end, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def list_vcc(cur: Any, hotel_slug: str, *, status: str | None = None, open_only: bool = False,
             limit: int = 200) -> list[dict]:
    """Cards sorted by charge deadline (the VCC page's countdowns);
    `open_only` keeps cards with money still on them."""
    cur.execute(
        """
        SELECT * FROM pfg_virtual_cards WHERE hotel_slug = %s AND (%s::text IS NULL OR status = %s)
           AND (NOT %s OR (current_balance_minor > 0 AND status NOT IN ('FULLY_CHARGED', 'CANCELLED')))
         ORDER BY charge_deadline, id LIMIT %s
        """,
        (hotel_slug, status, status, open_only, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def mark_vcc_charged(cur: Any, hotel_slug: str, card_id: int, *, amount_minor: int | None = None,
                     actor: Any = None) -> dict:
    """A person charged the card (the extranet is the source of truth; the
    next VCC report confirms). Without an amount the whole balance counts."""
    cur.execute("SELECT * FROM pfg_virtual_cards WHERE hotel_slug = %s AND id = %s FOR UPDATE", (hotel_slug, card_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Virtual card not found", "vcc_not_found")
    before = dict(row)
    charged = before["current_balance_minor"] if amount_minor is None else amount_minor
    if charged <= 0 or charged > before["current_balance_minor"]:
        raise ClefsError(400, "The amount must be above 0 and at most the card's balance", "invalid_amount")
    left = before["current_balance_minor"] - charged
    cur.execute(
        "UPDATE pfg_virtual_cards SET current_balance_minor = %s, status = %s, charged_at = %s, charged_by = %s, "
        "updated_at = %s WHERE id = %s RETURNING *",
        (left, "FULLY_CHARGED" if left == 0 else "PARTIALLY_CHARGED", clock.now(), _user_id(actor), clock.now(),
         card_id),
    )
    after = dict(cur.fetchone())
    audit(cur, actor, "profit.vcc.charged", "pfg_virtual_card", card_id, hotel_slug=hotel_slug,
          before=out(before), after=out(after), meta={"charged_minor": charged})
    return after


# ─── Rate evidence ───

RATE_SOURCES = ("MANUAL", "SERPAPI", "FEED")
DEVICES = ("DESKTOP", "MOBILE")
LOGIN_STATES = ("LOGGED_OUT", "LOGGED_IN", "MEMBER")


def room_key(label: str | None) -> str:
    """Room labels compare loosely ("Deluxe King Room" = "deluxe-king"):
    lower-case words without the filler "room"."""
    words = [w for w in re.findall(r"[a-z0-9]+", (label or "").lower()) if w not in ("room", "rooms", "the")]
    return "-".join(words) or "any"


def comparable_key(*, stay_date: date, los: int, adults: int, room_label: str | None, device: str,
                   login_state: str) -> str:
    """date|los|adults|room|device|login (02 spec 3.1): only observations
    with the same key are compared by L7, so a member price never meets a
    logged-out one."""
    return f"{stay_date.isoformat()}|{los}|{adults}|{room_key(room_label)}|{device}|{login_state}"


def insert_rate_evidence(cur: Any, hotel_slug: str, hotel_currency: str, ev: dict, *, actor: Any = None) -> dict:
    """Validate and store one observation. `ev` takes `price_minor` or
    `price` (Decimal string), `stay_date` (date or ISO) and `channel`;
    everything else has a default. Raises 400 `invalid_evidence`."""
    try:
        stay = ev["stay_date"] if isinstance(ev.get("stay_date"), date) else date.fromisoformat(str(ev["stay_date"]))
        channel = (normalize_ota(ev.get("channel")) or "").strip()
        ccy = money.ccy(ev.get("currency") or hotel_currency)
        if ev.get("price_minor") is not None:
            price = int(ev["price_minor"])
        else:
            price = money.to_minor(ev["price"], ccy)
        los, adults = int(ev.get("los") or 1), int(ev.get("adults") or 2)
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ClefsError(400, "Rate evidence needs channel, stay_date and a price", "invalid_evidence") from exc
    device = str(ev.get("device") or "DESKTOP").upper()
    login = str(ev.get("login_state") or "LOGGED_OUT").upper()
    source = str(ev.get("source") or "MANUAL").upper()
    if not channel or price < 0 or los < 1 or adults < 1 or device not in DEVICES or login not in LOGIN_STATES \
            or source not in RATE_SOURCES:
        raise ClefsError(400, "Rate evidence has an invalid value", "invalid_evidence")
    captured = ev.get("captured_at") or clock.now()
    if isinstance(captured, str):
        captured = datetime.fromisoformat(captured)
    key = comparable_key(stay_date=stay, los=los, adults=adults, room_label=ev.get("room_label"), device=device,
                         login_state=login)
    cur.execute(
        """
        INSERT INTO pfg_rate_evidence (hotel_slug, captured_at, channel, stay_date, los, adults, room_label,
            plan_label, price_minor, currency, taxes_included, device, login_state, booker_country, search_url,
            screenshot_file_id, source, source_module, comparable_key, emit_signal, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (hotel_slug, captured, channel, stay, los, adults, ev.get("room_label"), ev.get("plan_label"), price, ccy,
         ev.get("taxes_included"), device, login, (ev.get("booker_country") or None), ev.get("search_url"),
         ev.get("screenshot_file_id") or None, source, ev.get("source_module"), key,
         bool(ev.get("emit_signal", True)), _user_id(actor)),
    )
    return dict(cur.fetchone())


def list_rate_evidence(cur: Any, hotel_slug: str, *, start: date | None = None, end: date | None = None,
                       channel: str | None = None, limit: int = 500) -> list[dict]:
    cur.execute(
        """
        SELECT * FROM pfg_rate_evidence WHERE hotel_slug = %s AND (%s::date IS NULL OR stay_date >= %s)
           AND (%s::date IS NULL OR stay_date <= %s) AND (%s::text IS NULL OR channel = %s)
         ORDER BY stay_date, comparable_key, captured_at DESC LIMIT %s
        """,
        (hotel_slug, start, start, end, end, channel, channel, limit),
    )
    return [dict(r) for r in cur.fetchall()]


# ─── Commission credits of one booking (the one credit measure) ───
# The detectors net this off a finding's amount and the ledger counts what
# arrives above it as recovery, so both read the same lines the same way.


def credit_lines(cur: Any, hotel_slug: str, ota: str, res_norm: str, currency: str) -> dict:
    """{statement: [...], payout: [...]}: the booking's negative-commission
    statement lines (credit notes and adjustments, any document) and its
    negative-commission REFUND/ADJUSTMENT payout lines, as positive
    amounts."""
    cur.execute(
        """
        SELECT l.id, -l.commission_minor AS credit_minor, s.doc_no, s.doc_kind FROM pfg_ota_statement_lines l
          JOIN pfg_ota_statements s ON s.id = l.statement_id
         WHERE l.hotel_slug = %s AND l.ota_code = %s AND l.ota_res_id_norm = %s AND l.currency = %s
           AND l.commission_minor < 0 ORDER BY l.id
        """,
        (hotel_slug, ota, res_norm, currency),
    )
    stmt = [dict(r) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT l.id, -l.commission_minor AS credit_minor, p.payout_ref FROM pfg_ota_payout_lines l
          JOIN pfg_ota_payouts p ON p.id = l.payout_id
         WHERE l.hotel_slug = %s AND l.ota_code = %s AND l.ota_res_id_norm = %s AND l.currency = %s
           AND l.line_kind IN ('REFUND', 'ADJUSTMENT') AND l.commission_minor < 0 ORDER BY l.id
        """,
        (hotel_slug, ota, res_norm, currency),
    )
    return {"statement": stmt, "payout": [dict(r) for r in cur.fetchall()]}


def booking_credit(cur: Any, hotel_slug: str, ota: str | None, res_norm: str | None, currency: str, *,
                   payouts: bool = True) -> int:
    """Commission the OTA has credited back for one booking: the larger of
    the statement credits and the payout credits (one credit often shows
    in both the credit note and the next payout, so never the sum).
    `payouts=False` reads statements only (L12 counts its own refund
    payout lines separately)."""
    if not ota or not res_norm:
        return 0
    got = credit_lines(cur, hotel_slug, ota, res_norm, currency)
    stmt = sum(int(r["credit_minor"]) for r in got["statement"])
    pay = sum(int(r["credit_minor"]) for r in got["payout"]) if payouts else 0
    return max(stmt, pay)


# ─── Findings (storage; the detectors and the ledger build on this) ───

FINDING_COLS = ("detector", "ota_code", "reservation_id", "statement_line_id", "subject_ref", "class", "amount_minor",
                "currency", "confidence", "calc", "evidence", "deadline_at", "internal_only", "source",
                "credit_baseline_minor")
# Findings that claim commission back on a booking: the only ones the
# ledger's credit verification may credit (a VCC balance, a payout
# shortfall or a chargeback is not settled by a commission credit note).
COMMISSION_DETECTORS = ("L1", "L2", "L3", "L4", "L5", "L12")
# Once a claim went out (or money came back against it) its amount and
# trace are the claim of record: a detector re-run never rewrites them.
FROZEN_STATUSES = ("DISPUTED", "RECOVERED", "REJECTED", "UNRESOLVED")


def upsert_finding(cur: Any, hotel_slug: str, f: dict, *, run_id: str | None = None,
                   actor: str = "system") -> tuple[dict, bool]:
    """Insert or refresh one finding by (hotel, fingerprint). A re-run
    updates amount, confidence, trace and evidence but never the status a
    person or the ledger set; a new finding gets its DETECTED event.

    One credit accounting (02 spec 3.5): `credit_baseline_minor` is the
    booking credit already netted into `amount_minor` (a commission
    finding without one is measured here). The baseline is set once, when
    the finding is first stored. A re-run keeps it, and its amount is the
    gross claim less that first baseline: credit that arrived after
    detection is the ledger's to count as recovery, never netted again. A
    frozen finding (claim sent, or money already credited against it)
    keeps its amount and trace as they were. Returns (row, created)."""
    vals = {k: f.get(k) for k in FINDING_COLS}
    vals["calc"] = db.Json(f.get("calc") or [])
    vals["evidence"] = db.Json(f.get("evidence") or [])
    vals["internal_only"] = bool(f.get("internal_only"))
    vals["source"] = f.get("source") or "detector"
    if vals["credit_baseline_minor"] is None:
        vals["credit_baseline_minor"] = (
            booking_credit(cur, hotel_slug, f.get("ota_code"), str(f.get("subject_ref") or "").partition("|")[2],
                           f.get("currency") or "")
            if f.get("detector") in COMMISSION_DETECTORS and f.get("class") == "RECOVERABLE" else 0)
    vals["credit_baseline_minor"] = max(0, int(vals["credit_baseline_minor"]))
    cols = list(vals)
    frozen = "(pfg_findings.status = ANY(%s) OR pfg_findings.recovered_minor > 0)"
    special = {
        # gross (the draft's amount plus the credit it netted) less the
        # baseline netted when the finding was first stored
        "amount_minor": f"CASE WHEN {frozen} THEN pfg_findings.amount_minor ELSE GREATEST(0, EXCLUDED.amount_minor "
                        "+ EXCLUDED.credit_baseline_minor - pfg_findings.credit_baseline_minor) END",
        "calc": f"CASE WHEN {frozen} THEN pfg_findings.calc ELSE EXCLUDED.calc END",
        "credit_baseline_minor": "pfg_findings.credit_baseline_minor",
    }
    sets = [f"{c} = {special.get(c, f'EXCLUDED.{c}')}" for c in cols if c not in ("detector", "class")]
    cur.execute(
        f"""
        INSERT INTO pfg_findings (hotel_slug, fingerprint, first_run_id, last_run_id, {', '.join(cols)})
        VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(cols))})
        ON CONFLICT (hotel_slug, fingerprint) DO UPDATE SET
            {', '.join(sets)},
            last_run_id = EXCLUDED.last_run_id, updated_at = NOW()
        RETURNING *, (xmax = 0) AS created
        """,
        (hotel_slug, f["fingerprint"], run_id, run_id, *vals.values(), list(FROZEN_STATUSES),
         list(FROZEN_STATUSES)),
    )
    row = dict(cur.fetchone())
    created = bool(row.pop("created"))
    if created:
        cur.execute(
            "INSERT INTO pfg_finding_events (finding_id, hotel_slug, from_status, to_status, actor, amount_minor) "
            "VALUES (%s, %s, NULL, 'DETECTED', %s, %s)",
            (row["id"], hotel_slug, actor, row["amount_minor"]),
        )
    return row, created


def get_finding(cur: Any, hotel_slug: str, finding_id: int) -> dict | None:
    cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND id = %s", (hotel_slug, finding_id))
    row = cur.fetchone()
    return dict(row) if row else None


# ─── Reservations (reads for the money API) ───


def find_reservation_ref(cur: Any, hotel_slug: str, ref: Any, *, ota_code: str | None = None) -> dict | None:
    """A reservation named the way another module knows it: the core id,
    the PMS id, or the OTA's booking id (normalised for that OTA)."""
    if ref is None or str(ref).strip() == "":
        return None
    text = str(ref).strip()
    if text.isdigit():
        cur.execute("SELECT * FROM reservations WHERE hotel_slug = %s AND id = %s", (hotel_slug, int(text)))
        row = cur.fetchone()
        if row:
            return dict(row)
    cur.execute("SELECT * FROM reservations WHERE hotel_slug = %s AND pms_res_id = %s ORDER BY id LIMIT 1",
                (hotel_slug, text))
    row = cur.fetchone()
    if row:
        return dict(row)
    norm = stays.normalize_ota_res_id(ota_code, text)
    cur.execute("SELECT * FROM reservations WHERE hotel_slug = %s AND ota_res_id_norm = %s ORDER BY id LIMIT 1",
                (hotel_slug, norm))
    row = cur.fetchone()
    return dict(row) if row else None


def statement_commission(cur: Any, hotel_slug: str, reservation_id: int) -> tuple[int, str, list[int]] | None:
    """(net commission, currency, line ids) the OTA invoiced for one stay
    across every document (credit notes net off), None when no line is
    linked to it."""
    cur.execute(
        """
        SELECT l.id, l.commission_minor, l.currency FROM pfg_ota_statement_lines l
         WHERE l.hotel_slug = %s AND l.reservation_id = %s AND l.commission_minor IS NOT NULL ORDER BY l.id
        """,
        (hotel_slug, reservation_id),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return None
    ccy = rows[0]["currency"]
    same = [r for r in rows if r["currency"] == ccy]
    return max(0, sum(r["commission_minor"] for r in same)), ccy, [r["id"] for r in same]


# ─── Distribution cost of a stay (02 spec 3.6; 04 spec 7.2) ───

OTA_FALLBACK = ("BOOKING", "AGODA", "EXPEDIA")


def channel_kinds(cur: Any, hotel_slug: str) -> dict[str, str]:
    cur.execute("SELECT code, kind FROM channels WHERE hotel_slug = %s", (hotel_slug,))
    return {r["code"]: r["kind"] for r in cur.fetchall()}


def is_ota_channel(kinds: dict[str, str], code: str | None) -> bool:
    c = (code or "").upper()
    return kinds.get(c) == "ota" or normalize_ota(c) in OTA_FALLBACK


def pct_of(amount_minor: int, pct: Decimal) -> int:
    """pct percent of an amount, rounded once half up to minor units."""
    return int((Decimal(amount_minor) * pct / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _allocate(total: int, weights: list[int]) -> list[int]:
    if not weights:
        return []
    if total == 0:
        return [0] * len(weights)
    return money.allocate(total, weights if any(weights) else [1] * len(weights))


def stay_costs(cur: Any, hotel_slug: str, res: dict, *, kinds: dict[str, str] | None = None) -> dict:
    """What one reservation cost to distribute, allocated by night.

    Commission: what the OTA invoiced for the stay (statement lines, credit
    notes netted), else the expected commission from the contract or the
    default terms; a cancelled or no-show stay costs only what was invoiced.
    Payment cost: (card fee + payments fee) x gross, plus the booking-engine
    fee on direct channels, from the channel configuration in force at
    check-in. Everything is spread over the nights by gross (exact sums)."""
    from clefs.modules.profit_guard import contracts  # contracts imports repo

    kinds = kinds if kinds is not None else channel_kinds(cur, hotel_slug)
    channel = (res.get("channel_code") or "OTHER").upper()
    ota = is_ota_channel(kinds, channel)
    cur.execute(
        """
        SELECT stay_date, SUM(rooms) AS room_nights, SUM(rate_minor * rooms) AS room_minor, SUM(sc_minor) AS sc_minor,
               SUM(mf_minor + tf_minor + vat_minor + td_minor) AS pass_through_minor, BOOL_OR(stayed) AS stayed,
               SUM(rate_minor * rooms + sc_minor + mf_minor + tf_minor + vat_minor + td_minor) AS gross_minor
          FROM reservation_nights WHERE reservation_id = %s GROUP BY stay_date ORDER BY stay_date
        """,
        (res["id"],),
    )
    nights = [{k: (int(v) if isinstance(v, Decimal) else v) for k, v in dict(r).items()} for r in cur.fetchall()]
    live = res.get("status") not in ("CANCELLED", "NO_SHOW")
    commission, basis = 0, "none"
    if ota:
        actual = statement_commission(cur, hotel_slug, res["id"])
        if actual is not None:
            commission, basis = actual[0], "statement"
        elif live and nights:
            ec = contracts.expected_commission(cur, hotel_slug, ota_code=channel,
                                               breakdown=stays.reservation_breakdown(cur, res["id"]),
                                               on=res["check_in"])
            if ec is not None:
                commission, basis = ec["commission_minor"], "expected"
    gross = sum(n["gross_minor"] for n in nights)
    cfg = channel_config_on(cur, hotel_slug, channel, res["check_in"])
    pay_pct = Decimal(str(cfg.get("card_fee_pct") or 0)) + Decimal(str(cfg.get("payments_fee_pct") or 0))
    be_pct = Decimal(0) if ota else Decimal(str(cfg.get("booking_engine_fee_pct") or 0))
    pay = pct_of(gross, pay_pct) if live else 0
    be = pct_of(gross, be_pct) if live else 0
    weights = [n["gross_minor"] for n in nights]
    for n, c, p, b in zip(nights, _allocate(commission, weights), _allocate(pay, weights), _allocate(be, weights)):
        n.update(commission_minor=c, payment_fee_minor=p, booking_engine_fee_minor=b)
    return {"reservation_id": res["id"], "channel_code": channel, "is_ota": ota, "status": res.get("status"),
            "currency": res.get("currency"), "commission_minor": commission, "commission_basis": basis,
            "payment_fee_minor": pay, "booking_engine_fee_minor": be, "gross_minor": gross, "nights": nights}


def _stayed_reservations(cur: Any, hotel_slug: str, start: date, end: date) -> list[dict]:
    cur.execute(
        """
        SELECT r.* FROM reservations r
         WHERE r.hotel_slug = %s AND r.status NOT IN ('CANCELLED', 'NO_SHOW')
           AND EXISTS (SELECT 1 FROM reservation_nights n WHERE n.reservation_id = r.id AND n.stayed
                          AND n.stay_date BETWEEN %s AND %s)
         ORDER BY r.id
        """,
        (hotel_slug, start, end),
    )
    return [dict(r) for r in cur.fetchall()]


def top_ota(cur: Any, hotel_slug: str, *, as_of: date, days: int = 90) -> str | None:
    """The OTA with the most booked room nights over the trailing window
    (cancellations excluded; ties go to the alphabetically first code)."""
    kinds = channel_kinds(cur, hotel_slug)
    cur.execute(
        """
        SELECT r.channel_code, SUM(n.rooms) AS rn FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
         WHERE r.hotel_slug = %s AND r.status NOT IN ('CANCELLED', 'NO_SHOW') AND n.stay_date > %s AND n.stay_date <= %s
         GROUP BY r.channel_code ORDER BY rn DESC, r.channel_code
        """,
        (hotel_slug, as_of - timedelta(days=days), as_of),
    )
    return next((normalize_ota(r["channel_code"]) for r in cur.fetchall() if is_ota_channel(kinds, r["channel_code"])),
                None)


def distribution_by_night(cur: Any, hotel_slug: str, *, start: date, end: date) -> list[dict]:
    kinds = channel_kinds(cur, hotel_slug)
    acc: dict[tuple, dict] = {}
    for res in _stayed_reservations(cur, hotel_slug, start, end):
        c = stay_costs(cur, hotel_slug, res, kinds=kinds)
        for n in c["nights"]:
            if not n["stayed"] or not start <= n["stay_date"] <= end:
                continue
            k = (n["stay_date"], c["channel_code"], c["currency"])
            row = acc.setdefault(k, {"stay_date": k[0], "channel_code": k[1], "is_ota": c["is_ota"],
                                     "commission_minor": 0, "payment_fee_minor": 0, "booking_engine_fee_minor": 0,
                                     "currency": k[2]})
            for f in ("commission_minor", "payment_fee_minor", "booking_engine_fee_minor"):
                row[f] += n[f]
    return [acc[k] for k in sorted(acc)]


def cancellation_costs(cur: Any, hotel: Any, *, start: date, end: date) -> list[dict]:
    """Invoiced commission on cancelled and no-show stays, by the hotel-local
    cancellation date (a no-show's is its check-in)."""
    cur.execute(
        """
        SELECT r.id, r.channel_code, r.status, r.check_in, r.cancelled_at, l.currency,
               SUM(l.commission_minor) AS cost
          FROM reservations r JOIN pfg_ota_statement_lines l ON l.reservation_id = r.id
         WHERE r.hotel_slug = %s AND r.status IN ('CANCELLED', 'NO_SHOW') AND l.commission_minor IS NOT NULL
         GROUP BY r.id, r.channel_code, r.status, r.check_in, r.cancelled_at, l.currency
        """,
        (hotel.slug,),
    )
    acc: dict[tuple, int] = defaultdict(int)
    for r in cur.fetchall():
        day = r["check_in"] if r["status"] == "NO_SHOW" or r["cancelled_at"] is None else \
            r["cancelled_at"].astimezone(hotel.tz).date()
        if start <= day <= end and int(r["cost"] or 0) > 0:
            acc[(day, (r["channel_code"] or "OTHER").upper(), r["currency"])] += int(r["cost"])
    return [{"cancel_date": k[0], "channel_code": k[1], "cost_minor": v, "currency": k[2]} for k, v in sorted(acc.items())]


def channel_economics(cur: Any, hotel_slug: str, *, start: date, end: date, sc_as_revenue: bool = True) -> list[dict]:
    """02 spec 3.6 per channel over stayed nights in [start, end]:
    RoomRev = R (+ SC when the owner counts it), C = invoiced else expected
    commission, PayCost = card/payments (+ booking engine) fees, CxlCost =
    commission invoiced on stays cancelled in the range, Net = RoomRev - C -
    PayCost - CxlCost, NetADR = Net / RN, Retention = Net / Gross."""
    from clefs.core.hotels import get_hotel

    hotel = get_hotel(cur, hotel_slug)
    kinds = channel_kinds(cur, hotel_slug)
    acc: dict[tuple, dict] = {}
    fields = ("room_nights", "gross_minor", "room_rev_minor", "sc_minor", "pass_through_tax_minor", "commission_minor",
              "payment_cost_minor", "cancellation_cost_minor")

    def slot(channel: str, ccy: str, is_ota: bool) -> dict:
        return acc.setdefault((channel, ccy), {"channel_code": channel, "is_ota": is_ota, "currency": ccy,
                                               "bases": set(), **{f: 0 for f in fields}})

    for res in _stayed_reservations(cur, hotel_slug, start, end):
        c = stay_costs(cur, hotel_slug, res, kinds=kinds)
        row = slot(c["channel_code"], c["currency"] or hotel.currency, c["is_ota"])
        row["bases"].add(c["commission_basis"])
        for n in c["nights"]:
            if not n["stayed"] or not start <= n["stay_date"] <= end:
                continue
            row["room_nights"] += n["room_nights"]
            row["gross_minor"] += n["gross_minor"]
            row["room_rev_minor"] += n["room_minor"] + (n["sc_minor"] if sc_as_revenue else 0)
            row["sc_minor"] += n["sc_minor"]
            row["pass_through_tax_minor"] += n["pass_through_minor"]
            row["commission_minor"] += n["commission_minor"]
            row["payment_cost_minor"] += n["payment_fee_minor"] + n["booking_engine_fee_minor"]
    for cx in cancellation_costs(cur, hotel, start=start, end=end):
        slot(cx["channel_code"], cx["currency"], is_ota_channel(kinds, cx["channel_code"]))["cancellation_cost_minor"] \
            += cx["cost_minor"]
    out_rows = []
    for (channel, ccy), row in sorted(acc.items()):
        bases = row.pop("bases") - {"none"}
        net = row["room_rev_minor"] - row["commission_minor"] - row["payment_cost_minor"] - row["cancellation_cost_minor"]
        rn = row["room_nights"]
        net_adr = (Decimal(net) / rn).quantize(Decimal(1), rounding=ROUND_HALF_UP) if rn else None
        out_rows.append({
            **row, "channel": channel, "net_minor": net, "net_adr_minor": int(net_adr) if net_adr is not None else None,
            "commission_basis": "none" if not bases else (bases.pop() if len(bases) == 1 else "mixed"),
            "gross": money.money_str(row["gross_minor"], ccy), "room_rev": money.money_str(row["room_rev_minor"], ccy),
            "commission": money.money_str(row["commission_minor"], ccy), "net": money.money_str(net, ccy),
            "net_adr": money.money_str(int(net_adr), ccy) if net_adr is not None else None,
            "retention": str((Decimal(net) / row["gross_minor"]).quantize(Decimal("0.0001")))
            if row["gross_minor"] else None,
            # Finance check 3 (04 spec 7.2) reads these names.
            "room_rev_fils": row["room_rev_minor"], "commission_fils": row["commission_minor"],
        })
    return out_rows
