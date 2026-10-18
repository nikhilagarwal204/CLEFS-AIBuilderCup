"""
Demand Radar BAR calendar: the hotel's own best available rate per night and
room type (02 spec 4.1 bar_calendar), with every change kept.

Three sources:
- MANUAL: the console grid (PUT /bar).
- CSV: a channel-manager or PMS rate export (POST /bar/import), long
  (date, room type, rate) or wide (a date column then one column per room
  type code).
- RATE_EVIDENCE: a price seen elsewhere (for example the hotel's official
  price on Google). Evidence never overwrites a MANUAL or CSV value set in
  the last BAR_FRESH_DAYS days: the owner's own figure wins.

Inquiry Guard reads BAR only through demand.api.price_guardrails (bar and
bar_captured_at; an auto-sent quote needs a BAR at most 7 days old).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.money import money_str, to_minor
from clefs.modules.demand.adapters.csv_rates import parse_cell, parse_date
from clefs.modules.demand.comp_rates import get_rate_settings, normalize_code

SOURCES = ("MANUAL", "CSV", "RATE_EVIDENCE")
BAR_FRESH_DAYS = 7
MAX_ROWS = 5000
_ROOM_HEADERS = {"room type", "room_type", "room type code", "room_type_code", "room", "roomtype", "code"}
_RATE_HEADERS = {"bar", "rate", "price", "amount", "best available rate"}
_DATE_HEADERS = {"date", "stay date", "stay_date", "night", "arrival"}


# ─── Reads ───


def _out(r: dict) -> dict:
    return {"stay_date": r["stay_date"].isoformat(), "room_type_code": r["room_type_code"],
            "bar": money_str(r["bar_minor"], r["currency"]), "bar_minor": r["bar_minor"],
            "currency": r["currency"], "source": r["source"], "captured_at": r["captured_at"].isoformat(),
            "note": r.get("note")}


def get_bar(cur: Any, hotel_slug: str, stay_date: date, room_type_code: str) -> dict | None:
    r = clefs_db.fetch_one(
        "SELECT * FROM dr_bar_calendar WHERE hotel_slug = %s AND stay_date = %s AND room_type_code = %s",
        (hotel_slug, stay_date, room_type_code), cur=cur)
    return _out(r) if r else None


def list_bar(cur: Any, hotel_slug: str, start: date, end: date, room_type_code: str | None = None) -> list[dict]:
    rows = clefs_db.fetch_all(
        """SELECT * FROM dr_bar_calendar WHERE hotel_slug = %s AND stay_date BETWEEN %s AND %s
              AND (%s::text IS NULL OR room_type_code = %s) ORDER BY stay_date, room_type_code""",
        (hotel_slug, start, end, room_type_code, room_type_code), cur=cur)
    return [_out(r) for r in rows]


def changes(cur: Any, hotel_slug: str, stay_date: date, room_type_code: str | None = None) -> list[dict]:
    rows = clefs_db.fetch_all(
        """SELECT * FROM dr_bar_changes WHERE hotel_slug = %s AND stay_date = %s
              AND (%s::text IS NULL OR room_type_code = %s) ORDER BY at, id""",
        (hotel_slug, stay_date, room_type_code, room_type_code), cur=cur)
    return [{"room_type_code": r["room_type_code"], "old": money_str(r["old_minor"], r["currency"]),
             "new": money_str(r["new_minor"], r["currency"]), "currency": r["currency"], "source": r["source"],
             "actor": r["actor"], "at": r["at"].isoformat()} for r in rows]


def reference_room_type(cur: Any, hotel_slug: str) -> str | None:
    """The room type a NULL room_type_code means: the owner's setting, else
    the active type with the most rooms, else the only BAR type on file."""
    ref = get_rate_settings(cur, hotel_slug)["reference_room_type"]
    if ref:
        return ref
    r = clefs_db.fetch_one(
        """SELECT rt.code FROM room_types rt
             LEFT JOIN hotel_rooms hr ON hr.hotel_slug = rt.hotel_slug AND hr.room_type_code = rt.code
            WHERE rt.hotel_slug = %s AND rt.is_active
            GROUP BY rt.code, rt.room_count
            ORDER BY COALESCE(rt.room_count, count(hr.*)) DESC, rt.code LIMIT 1""",
        (hotel_slug,), cur=cur)
    if r:
        return r["code"]
    r = clefs_db.fetch_one("SELECT min(room_type_code) AS code FROM dr_bar_calendar WHERE hotel_slug = %s",
                           (hotel_slug,), cur=cur)
    return r["code"] if r and r["code"] else None


# ─── Writes ───


def set_bar(cur: Any, hotel: Any, *, stay_date: date, room_type_code: str, bar_minor: int, source: str,
            user_id: int | None = None, note: str | None = None) -> tuple[dict, bool]:
    """Upsert one night. Returns (row, changed). An unchanged amount only
    refreshes captured_at (a re-import confirms the BAR is current)."""
    if source not in SOURCES:
        raise ValueError(f"unknown BAR source {source!r}")
    if bar_minor <= 0:
        raise ClefsError(400, "A BAR must be above zero", "invalid_bar")
    code = normalize_code(room_type_code)
    ccy = str(hotel.currency).upper()
    prev = clefs_db.fetch_one(
        "SELECT * FROM dr_bar_calendar WHERE hotel_slug = %s AND stay_date = %s AND room_type_code = %s FOR UPDATE",
        (hotel.slug, stay_date, code), cur=cur)
    now = clock.now()
    if source == "RATE_EVIDENCE" and prev and prev["source"] != "RATE_EVIDENCE" \
            and prev["captured_at"] >= now - timedelta(days=BAR_FRESH_DAYS):
        return _out(prev), False
    changed = prev is None or prev["bar_minor"] != bar_minor
    cur.execute(
        """INSERT INTO dr_bar_calendar (hotel_slug, stay_date, room_type_code, bar_minor, currency, source,
                                        captured_at, updated_by, note)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (hotel_slug, stay_date, room_type_code) DO UPDATE SET
               bar_minor = EXCLUDED.bar_minor, currency = EXCLUDED.currency, source = EXCLUDED.source,
               captured_at = EXCLUDED.captured_at, updated_by = EXCLUDED.updated_by, note = EXCLUDED.note""",
        (hotel.slug, stay_date, code, bar_minor, ccy, source, now, user_id, (note or None) and note[:300]))
    if changed:
        cur.execute(
            """INSERT INTO dr_bar_changes (hotel_slug, stay_date, room_type_code, old_minor, new_minor, currency,
                                           source, actor, at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (hotel.slug, stay_date, code, prev["bar_minor"] if prev else None, bar_minor, ccy, source, user_id, now))
    return get_bar(cur, hotel.slug, stay_date, code), changed


def delete_bar(cur: Any, hotel: Any, stay_date: date, room_type_code: str, *, user_id: int | None) -> bool:
    code = normalize_code(room_type_code)
    cur.execute("DELETE FROM dr_bar_calendar WHERE hotel_slug = %s AND stay_date = %s AND room_type_code = %s "
                "RETURNING bar_minor, currency", (hotel.slug, stay_date, code))
    gone = cur.fetchone()
    if gone:
        cur.execute(
            """INSERT INTO dr_bar_changes (hotel_slug, stay_date, room_type_code, old_minor, new_minor, currency,
                                           source, actor) VALUES (%s, %s, %s, %s, NULL, %s, 'MANUAL', %s)""",
            (hotel.slug, stay_date, code, gone["bar_minor"], gone["currency"], user_id))
    return bool(gone)


def put_rows(cur: Any, hotel: Any, rows: list[dict], *, source: str, user_id: int | None) -> dict:
    """Many nights at once: [{stay_date, room_type_code, bar (major units,
    Decimal string or number)}]. Validates everything before writing, so a
    bad row rejects the whole request (400 invalid_bar with its index)."""
    if len(rows) > MAX_ROWS:
        raise ClefsError(400, f"At most {MAX_ROWS} rows", "invalid_bar")
    parsed: list[tuple[date, str, int, str | None]] = []
    for i, r in enumerate(rows):
        d = parse_date(r.get("stay_date"))
        try:
            amount = Decimal(str(r.get("bar")))
            code = normalize_code(r.get("room_type_code"))
        except Exception as exc:
            raise ClefsError(400, f"Row {i}: {getattr(exc, 'detail', 'invalid value')}", "invalid_bar") from exc
        if d is None or amount <= 0 or code == "*":
            raise ClefsError(400, f"Row {i}: needs a date, a room type code and a BAR above zero", "invalid_bar")
        parsed.append((d, code, to_minor(amount, hotel.currency), r.get("note")))
    written = changed = 0
    for d, code, minor, note in parsed:
        _, ch = set_bar(cur, hotel, stay_date=d, room_type_code=code, bar_minor=minor, source=source,
                        user_id=user_id, note=note)
        written += 1
        changed += int(ch)
    return {"written": written, "changed": changed,
            "nights": len({p[0] for p in parsed}), "room_types": sorted({p[1] for p in parsed})}


# ─── CSV import and rate evidence ───


def parse_bar_csv(text: str) -> list[dict]:
    """Long (date, room type, rate) or wide (date, then one column per room
    type code) -> [{stay_date, room_type_code, bar}]. Empty and sold-out
    cells are skipped: BAR is a price, never a status."""
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        raise ClefsError(400, "The file is empty", "unreadable_file")
    heads = [" ".join(re.sub(r"[_\s]+", " ", h).lower().split()) for h in rows[0]]
    date_i = next((i for i, h in enumerate(heads) if h in _DATE_HEADERS), None)
    if date_i is None:
        raise ClefsError(400, "No date column found", "unreadable_file")
    room_i = next((i for i, h in enumerate(heads) if h in _ROOM_HEADERS), None)
    rate_i = next((i for i, h in enumerate(heads) if h in _RATE_HEADERS), None)
    out: list[dict] = []
    for r in rows[1:MAX_ROWS + 1]:
        d = parse_date(r[date_i] if date_i < len(r) else "")
        if d is None:
            continue
        cells = [(r[room_i], r[rate_i])] if room_i is not None and rate_i is not None and max(room_i, rate_i) < len(r) \
            else [(rows[0][i], r[i]) for i in range(len(r)) if i != date_i and i < len(rows[0]) and heads[i]]
        for code, raw in cells:
            kind, amount = parse_cell(raw)
            if kind == "price" and str(code).strip():
                out.append({"stay_date": d.isoformat(), "room_type_code": str(code).strip(), "bar": str(amount)})
    return out


def import_csv(cur: Any, hotel: Any, text: str, *, user_id: int | None) -> dict:
    rows = parse_bar_csv(text)
    if not rows:
        raise ClefsError(400, "No BAR values found in the file", "no_rates")
    return put_rows(cur, hotel, rows, source="CSV", user_id=user_id)


def record_rate_evidence(cur: Any, hotel: Any, *, stay_date: date, room_type_code: str | None,
                         amount_minor: int, currency: str) -> dict | None:
    """A BAR observed elsewhere (for example the official-site price on
    Google). Only in the hotel's currency; None when it did not apply."""
    if str(currency).upper() != str(hotel.currency).upper() or amount_minor <= 0:
        return None
    code = room_type_code or reference_room_type(cur, hotel.slug)
    if not code:
        return None
    row, _ = set_bar(cur, hotel, stay_date=stay_date, room_type_code=code, bar_minor=int(amount_minor),
                     source="RATE_EVIDENCE")
    return row
