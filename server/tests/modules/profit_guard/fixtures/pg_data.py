"""Small deterministic builders for the detector tests: each writes exactly
the rows a case needs (straight into the pfg_* tables, bypassing ingest,
whose own tests cover parsing) and returns ids."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs import db
from clefs.core import stays
from clefs.core.hotels import get_hotel
from clefs.modules.profit_guard import repo
from clefs.modules.profit_guard.detectors import REGISTRY, store
from clefs.modules.profit_guard.detectors.base import Scope, make_ctx

CI = date(2026, 9, 1)


# ─── PMS ───


def reservation(cur: Any, slug: str, pms_id: str, *, channel: str = "BOOKING", ota_id: str | None = None,
                status: str = "CHECKED_OUT", check_in: date = CI, nights: int = 3, rate: int = 35000,
                name: str = "Ahmed Al Mansouri", **extra: Any) -> int:
    ns = [{"stay_date": check_in + timedelta(days=i), "rate_minor": rate} for i in range(nights)]
    data = {"pms_res_id": pms_id, "channel_code": channel, "ota_res_id": ota_id, "guest_name": name,
            "check_in": check_in, "check_out": check_in + timedelta(days=nights), "status": status, "currency": "AED",
            "room_type_code": "DLX", **extra}
    return stays.upsert_reservation(cur, slug, data, nights=ns)["id"]


def folio(cur: Any, slug: str, reservation_id: int, code: str, amount: int, *, txn: str | None = None) -> None:
    cur.execute(
        "INSERT INTO folio_charges (hotel_slug, reservation_id, posted_date, code, amount_minor, currency, pms_txn_id) "
        "VALUES (%s, %s, %s, %s, %s, 'AED', %s)",
        (slug, reservation_id, CI, code, amount, txn or f"t-{reservation_id}-{code}"),
    )


# ─── OTA claims ───


def statement(cur: Any, slug: str, lines: list[dict], *, ota: str = "BOOKING", doc_no: str = "INV-1",
              doc_date: date = date(2026, 10, 1), doc_kind: str = "COMMISSION_INVOICE",
              batch_id: int | None = None) -> list[int]:
    cur.execute(
        "INSERT INTO pfg_ota_statements (hotel_slug, ota_code, doc_kind, doc_no, doc_date, currency, batch_id) "
        "VALUES (%s, %s, %s, %s, %s, 'AED', %s) RETURNING id",
        (slug, ota, doc_kind, doc_no, doc_date, batch_id),
    )
    sid = cur.fetchone()["id"]
    ids = []
    for i, ln in enumerate(lines):
        row = {"line_key": f"k{i}", "ota_code": ota, "check_in": CI, "check_out": CI + timedelta(days=3),
               "room_nights": 3,
               "rooms": 1, "line_status": "OK", "currency": "AED", "charges": [], "programs": [], **ln}
        rid = row.get("ota_res_id")
        row.setdefault("ota_res_id_norm", stays.normalize_ota_res_id(ota, rid) if rid else None)
        row["charges"] = db.Json(row["charges"])
        cols = list(row)
        cur.execute(
            f"INSERT INTO pfg_ota_statement_lines (statement_id, hotel_slug, batch_id, {', '.join(cols)}) "
            f"VALUES (%s, %s, %s, {', '.join(['%s'] * len(cols))}) RETURNING id",
            (sid, slug, batch_id, *row.values()),
        )
        ids.append(cur.fetchone()["id"])
    return ids


def payout(cur: Any, slug: str, ref: str, lines: list[dict], *, ota: str = "BOOKING",
           payout_date: date = date(2026, 10, 5),
           currency: str = "AED", amount: int | None = None) -> int:
    total = sum(ln.get("partner_payout_minor", 0) for ln in lines) if amount is None else amount
    cur.execute(
        "INSERT INTO pfg_ota_payouts (hotel_slug, ota_code, payout_ref, payout_date, currency, amount_minor) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (slug, ota, ref, payout_date, currency, total),
    )
    pid = cur.fetchone()["id"]
    for i, ln in enumerate(lines):
        row = {"line_key": f"p{i}", "ota_code": ota, "currency": currency, "line_kind": "BOOKING", **ln}
        rid = row.get("ota_res_id")
        row.setdefault("ota_res_id_norm", stays.normalize_ota_res_id(ota, rid) if rid else None)
        cols = list(row)
        cur.execute(
            f"INSERT INTO pfg_ota_payout_lines (payout_id, hotel_slug, {', '.join(cols)}) "
            f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))})",
            (pid, slug, *row.values()),
        )
    return pid


def bank(cur: Any, slug: str, amount: int, *, value_date: date = date(2026, 10, 6), currency: str = "AED",
         payout_id: int | None = None, key: str = "b1") -> int:
    cur.execute(
        "INSERT INTO pfg_bank_credits (hotel_slug, line_key, value_date, currency, amount_minor, hotel_currency, "
        "matched_payout_id) VALUES (%s, %s, %s, %s, %s, 'AED', %s) RETURNING id",
        (slug, key, value_date, currency, amount, payout_id),
    )
    return cur.fetchone()["id"]


def vcc(cur: Any, slug: str, vcc_id: str, *, balance: int = 50000, deadline: date, status: str = "AVAILABLE",
        ota: str = "BOOKING", activation: date | None = None, ota_res_id: str | None = None) -> int:
    cur.execute(
        "INSERT INTO pfg_virtual_cards (hotel_slug, ota_code, vcc_id, ota_res_id_norm, activation_date, "
        "expiration_date, "
        "charge_deadline, current_balance_minor, currency, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'AED', %s) "
        "RETURNING id",
        (slug, ota, vcc_id, ota_res_id, activation, deadline, deadline, balance, status),
    )
    return cur.fetchone()["id"]


def evidence(cur: Any, slug: str, channel: str, price: int, *, stay_date: date, login: str = "LOGGED_OUT",
             device: str = "DESKTOP", screenshot: bool = False, emit_signal: bool = True) -> int:
    hotel = get_hotel(cur, slug)
    shot = None
    if screenshot:
        from clefs import files

        shot = files.put_file(cur, slug, b"\x89PNG fake", filename="s.png", content_type="image/png",
                              purpose="profit.rate_evidence").id
    row = repo.insert_rate_evidence(cur, slug, hotel.currency, {
        "channel": channel, "stay_date": stay_date, "price_minor": price, "currency": "AED", "device": device,
        "login_state": login, "room_label": "Deluxe King", "screenshot_file_id": shot, "taxes_included": True})
    if not emit_signal:
        cur.execute("UPDATE pfg_rate_evidence SET emit_signal = FALSE WHERE id = %s", (row["id"],))
    return row["id"]


def contract(cur: Any, slug: str, *, ota: str = "BOOKING", pct: str = "15", entity_country: str | None = None,
             effective_from: date = date(2026, 1, 1), rule: str = "ROOM_INCL_VAT",
             excluded: tuple[str, ...] = ("MUNICIPALITYFEE", "TOURISMFEE"), dispute_rules: list | None = None) -> int:
    cur.execute(
        """
        INSERT INTO pfg_ota_contracts (hotel_slug, ota_code, entity_name, entity_country, effective_from,
               base_commission_pct, commission_base_rule, excluded_charge_types, dispute_rules, status, confirmed_at)
        VALUES (%s, %s, 'Entity', %s, %s, %s, %s, %s, %s, 'CONFIRMED', NOW()) RETURNING id
        """,
        (slug, ota, entity_country, effective_from, Decimal(pct), rule, list(excluded),
         db.Json(dispute_rules if dispute_rules is not None else [{"kind": "HOURS_AFTER_CHECKOUT", "hours": 48}])),
    )
    return cur.fetchone()["id"]


def program(cur: Any, slug: str, program: str, pct: str, *, kind: str = "DISCOUNT", ota: str = "BOOKING",
            countries: tuple[str, ...] = ()) -> int:
    cur.execute(
        "INSERT INTO pfg_ota_programs (hotel_slug, ota_code, program, kind, pct, booker_countries) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (slug, ota, program, kind, Decimal(pct), list(countries)),
    )
    return cur.fetchone()["id"]


def channel_cfg(cur: Any, slug: str, *, mf: bool, td: bool, ota: str = "BOOKING",
                since: date = date(2026, 1, 1)) -> None:
    cur.execute(
        "INSERT INTO pfg_channel_config (hotel_slug, ota_code, configured_from, mf_configured_as_charge, "
        "td_configured_as_charge) VALUES (%s, %s, %s, %s, %s)",
        (slug, ota, since, mf, td),
    )


# ─── Running one detector ───


def drafts(cur: Any, slug: str, name: str, *, scope: Scope | None = None) -> list:
    """The drafts one detector returns, without storing anything."""
    ctx = make_ctx(cur, get_hotel(cur, slug), repo.get_policies(cur, slug), "test")
    store.load_tuning(ctx)
    out = REGISTRY[name](ctx, scope or Scope(kind="manual"))
    drafts.last_ctx = ctx  # type: ignore[attr-defined]
    return out


def only(ds: list, detector: str) -> list:
    return [d for d in ds if d.detector == detector]
