"""Commit handlers core registers itself: the PMS kinds go to core/stays
(04 spec D1, D4), and BOOKING_ENGINE_ATTRIBUTION to its attribution writer
(D2). Modules register the others (Finance: PL_STATEMENT, TRIAL_BALANCE;
Profit Guard: the OTA kinds) through register_commit_handler.

A row core/stays refuses (a check-out before its check-in) is marked ERROR
on its import row inside a savepoint; the rest of the file still commits.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from clefs.core.errors import ClefsError
from clefs.core.ingest.kinds import register_commit_handler

RES_FIELDS = (
    "channel_code", "ota_res_id", "guest_name", "guest_country", "guest_language", "booked_at", "check_in",
    "check_out", "rooms_count", "room_type_code", "room_number", "rate_plan_code", "status", "cancelled_at",
    "checked_in_at", "checked_out_at", "gross_minor", "room_revenue_minor", "currency", "segment", "adults",
    "children", "collect_model", "payment_method", "promo_code",
)
# Raw channel wording -> channels.code (stays creates unknown codes itself).
CHANNEL_WORDS: tuple[tuple[str, str], ...] = (
    ("booking engine", "DIRECT_WEB"), ("booking com", "BOOKING"), ("booking", "BOOKING"), ("bdc", "BOOKING"), ("agoda", "AGODA"),
    ("expedia", "EXPEDIA"), ("hotels com", "EXPEDIA"), ("walk in", "WALKIN"), ("walkin", "WALKIN"),
    ("website", "DIRECT_WEB"), ("web", "DIRECT_WEB"), ("direct", "DIRECT_WEB"),
    ("phone", "DIRECT_PHONE"), ("telephone", "DIRECT_PHONE"), ("email", "DIRECT_PHONE"),
    ("corporate", "CORPORATE"), ("company", "CORPORATE"), ("gds", "GDS"),
)
SEGMENTS = ("LEISURE", "CORPORATE", "MICE", "GROUP", "CREW", "LONG_STAY")
PAYMENT_WORDS: tuple[tuple[str, str], ...] = (
    ("virtual", "VCC"), ("vcc", "VCC"), ("upc", "UPC"), ("card", "CARD"), ("credit", "CARD"), ("visa", "CARD"),
    ("master", "CARD"), ("amex", "CARD"), ("cash", "CASH"), ("bank", "BANK"), ("transfer", "BANK"),
)
# Folio transaction wording -> folio_charges.code.
FOLIO_WORDS: tuple[tuple[str, str], ...] = (
    ("refund", "REFUND"), ("no show", "NOSHOW_FEE"), ("noshow", "NOSHOW_FEE"), ("cancel", "CXL_FEE"),
    ("tourism dirham", "TD"), ("tourism fee", "TD"), ("tourism", "TD"), ("municipality", "MF"),
    ("service charge", "SC"), ("vat", "VAT"), ("value added", "VAT"), ("minibar", "MINIBAR"), ("mini bar", "MINIBAR"),
    ("food", "FNB"), ("beverage", "FNB"), ("restaurant", "FNB"), ("room service", "FNB"), ("f b", "FNB"),
    ("bar", "FNB"), ("breakfast", "FNB"), ("accommodation", "ROOM"), ("room", "ROOM"), ("lodging", "ROOM"),
)
FOLIO_CODES = ("ROOM", "SC", "MF", "TF", "VAT", "TD", "FNB", "MINIBAR", "NOSHOW_FEE", "CXL_FEE", "REFUND", "OTHER")


def _words(v: Any) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip() + " "


def _first(v: Any, table: tuple[tuple[str, str], ...]) -> str | None:
    w = _words(v)
    return next((code for word, code in table if f" {word} " in w), None)


def channel_code(v: Any) -> str | None:
    if v is None:
        return None
    hit = _first(v, CHANNEL_WORDS)
    if hit:
        return hit
    code = re.sub(r"[^A-Z0-9]+", "_", str(v).upper()).strip("_")[:30]
    return code or None


def collect_model(v: Any) -> str | None:
    w = _words(v)
    if any(f" {x} " in w for x in ("hotel collect", "property collect", "to be paid by guest", "pay at hotel",
                                    "hotel")):
        return "HOTEL_COLLECT"
    if any(f" {x} " in w for x in ("ota collect", "expedia collect", "agoda collect", "prepaid", "ota",
                                    "virtual card", "vcc")):
        return "OTA_COLLECT"
    return None


def folio_code(code: Any, description: Any = None) -> str:
    raw = str(code or "").strip().upper()
    if raw in FOLIO_CODES:
        return raw
    return _first(code, FOLIO_WORDS) or _first(description, FOLIO_WORDS) or "OTHER"


def _mark_error(cur: Any, row_id: int, message: str) -> None:
    cur.execute(
        "UPDATE import_rows SET status = 'ERROR', messages = array_append(messages, %s) WHERE id = %s",
        (message[:500], row_id),
    )


# ─── Handlers ───


def _savepoint(cur: Any, fn: Any, row: dict) -> Any:
    """Run one row's write in a savepoint: a refused row becomes ERROR and
    the rest of the batch goes on."""
    cur.execute("SAVEPOINT clefs_ingest_row")
    try:
        out = fn()
        cur.execute("RELEASE SAVEPOINT clefs_ingest_row")
        return out
    except (ClefsError, ValueError) as exc:
        cur.execute("ROLLBACK TO SAVEPOINT clefs_ingest_row")
        cur.execute("RELEASE SAVEPOINT clefs_ingest_row")
        _mark_error(cur, row["_row_id"], f"not written: {getattr(exc, 'detail', None) or exc}")
        return None


def reservation_data(row: dict) -> tuple[dict, list[dict]]:
    """(upsert data, contacts) for one canonical PMS_RESERVATIONS row."""
    data = {k: row[k] for k in RES_FIELDS if row.get(k) is not None}
    data["pms_res_id"] = row["pms_res_id"]
    if "channel_code" in data:
        data["channel_code"] = channel_code(data["channel_code"])
    if "collect_model" in data:
        data["collect_model"] = collect_model(data["collect_model"])
    if "payment_method" in data:
        data["payment_method"] = _first(data["payment_method"], PAYMENT_WORDS) or "OTHER"
    if "segment" in data:
        seg = re.sub(r"[^A-Z]+", "_", str(data["segment"]).upper()).strip("_")
        data["segment"] = seg if seg in SEGMENTS else None
    data = {k: v for k, v in data.items() if v is not None}
    contacts = [{"kind": kind, "value": row[f]} for f, kind in (("guest_email", "email"), ("guest_phone", "phone"))
                if row.get(f)]
    return data, contacts


def commit_pms_reservations(cur: Any, batch: dict, rows: list[dict]) -> dict:
    from clefs.core import stays

    hotel = batch["hotel_slug"]
    created = updated = failed = 0
    for row in rows:
        data, contacts = reservation_data(row)
        res = _savepoint(cur, lambda: stays.upsert_reservation(
            cur, hotel, data, source_system="PMS", contacts=contacts or None, import_batch_id=batch["id"],
        ), row)
        if res is None:
            failed += 1
        elif res["created"]:
            created += 1
        else:
            updated += 1
    # New PMS truth can settle OTA claims that were waiting for it, and
    # folio lines imported before their reservation.
    rematched = stays.rematch_open(cur, hotel)
    linked = stays.link_orphans(cur, hotel)
    return {"created": created, "updated": updated, "failed": failed, "rematched": rematched,
            "folio_linked": linked}


def commit_pms_folio(cur: Any, batch: dict, rows: list[dict]) -> dict:
    from clefs.core import stays

    written = linked = failed = 0
    for row in rows:
        line = {k: row.get(k) for k in ("pms_txn_id", "posted_date", "amount_minor", "pms_res_id", "stay_date",
                                        "description", "currency", "room_number")}
        line["code"] = folio_code(row.get("code"), row.get("description"))
        if row.get("qty") is not None:
            line["qty"] = row["qty"]
        out = _savepoint(cur, lambda: stays.upsert_folio_charges(cur, batch["hotel_slug"], [line],
                                                                 import_batch_id=batch["id"]), row)
        if out is None:
            failed += 1
        else:
            written += out["written"]
            linked += out["linked"]
    return {"written": written, "linked": linked, "failed": failed}


def commit_occupancy(cur: Any, batch: dict, rows: list[dict]) -> dict:
    from clefs.core import stays

    written = refused = failed = 0
    cols = ("rooms_occupied", "rooms_available", "rooms_ooo", "arrivals", "departures", "room_revenue_minor")
    for row in rows:
        values = {k: row[k] for k in cols if row.get(k) is not None}
        day = date.fromisoformat(row["date"])
        out = _savepoint(cur, lambda: stays.set_occupancy(cur, batch["hotel_slug"], day, values, source="PMS",
                                                          import_batch_id=batch["id"]) or "refused", row)
        if out is None:
            failed += 1
        elif out == "refused":
            refused += 1  # a manual figure for that day outranks the PMS
        else:
            written += 1
    return {"written": written, "kept_manual": refused, "failed": failed}


def commit_attribution(cur: Any, batch: dict, rows: list[dict]) -> dict:
    from clefs.core import stays

    keys = ("booking_ref", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "promo_code",
            "landing_url")
    clean = [{k: r[k] for k in keys if r.get(k) is not None} for r in rows]
    return stays.apply_attribution(cur, batch["hotel_slug"], clean)


def install() -> None:
    register_commit_handler("PMS_RESERVATIONS", commit_pms_reservations)
    register_commit_handler("PMS_FOLIO", commit_pms_folio)
    register_commit_handler("OCCUPANCY", commit_occupancy)
    register_commit_handler("BOOKING_ENGINE_ATTRIBUTION", commit_attribution)


install()
