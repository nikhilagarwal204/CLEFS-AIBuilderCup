"""
Lighthouse and RateGain rate-shop CSVs (02 spec 4.2: CsvLighthouse and
CsvRateGain via ingest), plus the LighthouseApi stub.

Two doors, one writer (comp_rates.record_rates):
- core ingest kind COMP_RATES: the staged canonical rows (comp_name,
  stay_date, los, adults, price_minor, currency, channel, captured_at) are
  handed to commit_comp_rates() on commit. A row whose price does not parse
  (for example "Sold out") is an ERROR row in ingest and never reaches the
  handler, so sold-out cells need the direct route.
- POST /hotels/{slug}/demand/comp-rates/import: the console posts the file
  text; parse_csv() reads both shapes rate shops export:
  - long: one row per hotel and night (hotel, date, rate[, channel, los]);
  - wide: a date column then one column per hotel (the Lighthouse and
    RateGain grid). Cells "sold out", "SO", "closed" are sold out; empty,
    "-" and "n/a" are no data.

Unknown hotel names become comps (origin 'csv') while the set has room, so a
rate-shop export can seed the comp set. The hotel's own column is skipped
when it matches the hotel's name.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from clefs.core import clock
from clefs.core.errors import ClefsError, NotConfigured
from clefs.core.hotels import get_hotel
from clefs.core.money import from_minor, to_minor
from clefs.modules.demand import comp_rates as cr

SOURCES = {"lighthouse": "CSV_LIGHTHOUSE", "rategain": "CSV_RATEGAIN"}
MAX_ROWS = 20_000
_SOLD_OUT = {"sold out", "soldout", "so", "closed", "no availability", "not available", "unavailable", "full"}
_NO_DATA = {"", "-", "--", "n/a", "na", "none", "null", "?"}
_DATE_HEADERS = {"date", "stay date", "arrival", "arrival date", "check in", "check-in", "checkin", "night"}
_NAME_HEADERS = {"hotel", "hotel name", "competitor", "property", "property name", "comp", "comp name"}
_PRICE_HEADERS = {"rate", "price", "lowest rate", "best available rate", "bar", "amount", "lowest price"}
_SKIP_HEADERS = {"day", "weekday", "dow", "day of week", "currency", "los", "nights", "channel", "source",
                 "adults", "room", "room type", "notes", "event", "events", "demand", "market demand"}


# ─── Source and value parsing ───


def source_for(hint: Any) -> str:
    """CSV_RATEGAIN when the source hint names RateGain, else Lighthouse."""
    return SOURCES["rategain"] if "rategain" in str(hint or "").lower().replace(" ", "") else SOURCES["lighthouse"]


def parse_cell(raw: Any) -> tuple[str, Decimal | None]:
    """('price', amount) | ('sold_out', None) | ('none', None)."""
    s = " ".join(str(raw or "").split()).lower()
    if s in _NO_DATA:
        return "none", None
    if s in _SOLD_OUT:
        return "sold_out", None
    num = re.sub(r"[^\d.,\-]", "", s)
    if num.count(",") and num.count("."):
        num = num.replace(",", "")
    elif num.count(",") == 1 and len(num.split(",")[1]) == 2:
        num = num.replace(",", ".")  # 450,00
    else:
        num = num.replace(",", "")
    try:
        d = Decimal(num)
    except (InvalidOperation, ValueError):
        return "none", None
    return ("price", d) if d > 0 else ("none", None)


def parse_date(raw: Any) -> date | None:
    """ISO, 29/09/2026 (day first, the UAE and Lighthouse default),
    "Tue 29 Sep 2026", "29 Sep 2026", "Sep 29, 2026"."""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = " ".join(str(raw or "").split())
    s = re.sub(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+", "", s, flags=re.I)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%d-%b-%Y",
                "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s[:30], fmt).date()
        except ValueError:
            continue
    return None


# ─── Direct import (long and wide) ───


def _norm_header(h: Any) -> str:
    return " ".join(re.sub(r"[_\s]+", " ", str(h or "")).lower().split())


def parse_csv(text: str) -> list[dict]:
    """Rows of {name, stay_date, kind: price|sold_out, amount, channel, los}.
    400 unreadable_file when neither shape is recognised."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    # Rate-shop exports often open with title lines: the header is the first
    # row that has a date column.
    head_idx = next((i for i, r in enumerate(rows[:15]) if any(_norm_header(c) in _DATE_HEADERS for c in r)), None)
    if head_idx is None:
        raise ClefsError(400, "No date column found", "unreadable_file")
    headers = [_norm_header(h) for h in rows[head_idx]]
    body = [r for r in rows[head_idx + 1:] if any(str(c).strip() for c in r)][:MAX_ROWS]
    date_i = next(i for i, h in enumerate(headers) if h in _DATE_HEADERS)
    name_i = next((i for i, h in enumerate(headers) if h in _NAME_HEADERS), None)
    price_i = next((i for i, h in enumerate(headers) if h in _PRICE_HEADERS), None)
    chan_i = next((i for i, h in enumerate(headers) if h in ("channel", "source", "website")), None)
    los_i = next((i for i, h in enumerate(headers) if h in ("los", "nights", "length of stay")), None)
    out: list[dict] = []

    def cell(r: list, i: int | None) -> str:
        return r[i] if i is not None and i < len(r) else ""

    if name_i is not None and price_i is not None:  # long shape
        for r in body:
            d = parse_date(cell(r, date_i))
            kind, amt = parse_cell(cell(r, price_i))
            name = " ".join(cell(r, name_i).split())
            if d and name and kind != "none":
                los = int(cell(r, los_i)) if cell(r, los_i).strip().isdigit() else 1
                out.append({"name": name, "stay_date": d, "kind": kind, "amount": amt,
                            "channel": cell(r, chan_i).strip() or None, "los": max(1, min(30, los))})
        return out
    comp_cols = [(i, rows[head_idx][i].strip()) for i, h in enumerate(headers)
                 if i != date_i and h and h not in _SKIP_HEADERS and h not in _DATE_HEADERS]
    if not comp_cols:
        raise ClefsError(400, "No hotel columns found", "unreadable_file")
    for r in body:
        d = parse_date(cell(r, date_i))
        if not d:
            continue
        for i, name in comp_cols:
            kind, amt = parse_cell(cell(r, i))
            if kind != "none":
                out.append({"name": name, "stay_date": d, "kind": kind, "amount": amt, "channel": None, "los": 1})
    return out


def _resolve(cur: Any, hotel: Any, names: set[str]) -> tuple[dict[str, str], list[str]]:
    """{name: comp_id} for every name, creating comps while the set has
    room. The hotel's own column maps to nothing. Returns (map, unmatched)."""
    comps = cr.list_comps(cur, hotel.slug, include_inactive=True)
    own = cr.norm_name(hotel.name)
    out: dict[str, str] = {}
    unmatched: list[str] = []
    for name in sorted(names):
        if cr.norm_name(name) == own:
            continue
        c = cr.match_comp(comps, name=name)
        if c is None:
            try:
                c, _ = cr.upsert_comp(cur, hotel.slug, {"name": name}, origin="csv")
                comps.append(c)
            except ClefsError:
                unmatched.append(name)
                continue
        out[name] = c["comp_id"]
    return out, unmatched


def _to_rates(cur: Any, hotel: Any, records: list[dict], *, source: str, currency: str,
              captured_at: datetime, batch_id: int | None = None) -> tuple[list[cr.CompRate], list[str]]:
    ids, unmatched = _resolve(cur, hotel, {r["name"] for r in records})
    merged: dict[tuple, cr.CompRate] = {}
    for r in records:
        cid = ids.get(r["name"])
        if cid is None:
            continue
        minor = to_minor(r["amount"], currency) if r["kind"] == "price" else None
        key = (cid, r["stay_date"], r["los"])
        prev = merged.get(key)
        by_source = [{"source": r["channel"], "minor": minor}] if r.get("channel") and minor else []
        if prev is not None:
            # Several channels for one comp and night: keep the lowest price
            # and every channel row; a price beats a sold-out cell.
            prev.by_source += by_source
            if minor is not None and (prev.lowest_minor is None or minor < prev.lowest_minor):
                prev.lowest_minor, prev.sold_out = minor, False
            continue
        merged[key] = cr.CompRate(comp_id=cid, stay_date=r["stay_date"], los=r["los"], lowest_minor=minor,
                                  currency=currency, source=source, captured_at=captured_at,
                                  sold_out=minor is None, by_source=by_source, query=f"csv:{source.lower()}",
                                  import_batch_id=batch_id)
    return list(merged.values()), unmatched


def import_csv(cur: Any, hotel: Any, text: str, *, source_hint: str | None = None,
               currency: str | None = None) -> dict:
    """The direct route. Amounts are read in `currency` (default the
    hotel's); only the hotel's currency is stored (Canon 2)."""
    ccy = (currency or hotel.currency or "").upper()
    if ccy != str(hotel.currency).upper():
        raise ClefsError(400, f"Rates must be in {hotel.currency}", "invalid_currency")
    records = parse_csv(text)
    if not records:
        raise ClefsError(400, "No rates found in the file", "no_rates")
    source = source_for(source_hint)
    rates, unmatched = _to_rates(cur, hotel, records, source=source, currency=ccy, captured_at=clock.now())
    res = cr.record_rates(cur, hotel, rates)
    return {"source": source, "rows": len(records), "rates": res["written"], "skipped": res["skipped"],
            "sold_out": sum(1 for r in rates if r.sold_out), "unmatched": unmatched,
            "nights": len({r.stay_date for r in rates}), "indicative": True}


# ─── core ingest commit handler (kind COMP_RATES) ───


def commit_comp_rates(cur: Any, batch: dict, rows: list[dict]) -> dict:
    """Staged canonical rows -> comp rates. price_minor is already integer
    minor units of the row (or batch) currency."""
    if not batch.get("hotel_slug"):
        raise ClefsError(400, "A comp-rate batch needs a hotel", "invalid_batch")
    hotel = get_hotel(cur, batch["hotel_slug"])
    ccy = str(hotel.currency).upper()
    source = source_for(" ".join(str(batch.get(k) or "") for k in ("source_code", "filename", "options")))
    records: list[dict] = []
    wrong_ccy = 0
    # The upload time: a rate shop's own capture stamp is not reliably mapped.
    captured_at = clock.now()
    for r in rows:
        d = parse_date(r.get("stay_date"))
        minor = r.get("price_minor")
        if not d or not r.get("comp_name") or not minor or int(minor) <= 0:
            continue
        if str(r.get("currency") or batch.get("currency") or ccy).upper() != ccy:
            wrong_ccy += 1
            continue
        records.append({"name": " ".join(str(r["comp_name"]).split()), "stay_date": d, "kind": "price",
                        "amount": from_minor(int(minor), ccy),
                        "channel": (str(r.get("channel") or "").strip() or None),
                        "los": max(1, min(30, int(r.get("los") or 1)))})
    rates, unmatched = _to_rates(cur, hotel, records, source=source, currency=ccy, captured_at=captured_at,
                                 batch_id=batch.get("id"))
    res = cr.record_rates(cur, hotel, rates)
    return {"source": source, "rates": res["written"], "skipped": res["skipped"] + wrong_ccy,
            "wrong_currency": wrong_ccy, "unmatched": unmatched}


# ─── LighthouseApi (stub) ───


class LighthouseApi:
    """Lighthouse Integration API v3 (X-Oi-Authorization; 20 requests per
    API per subscription per day). Needs the hotel's own subscription or a
    Clefs partnership, so it is a stub: rate-shop data arrives as CSV."""

    source = SOURCES["lighthouse"]

    def fetch(self, hotel: Any, comp_ids: list[str], dates: list[date], los: int = 1, adults: int = 2,
              currency: str = "AED") -> list[cr.CompRate]:
        raise NotConfigured("Lighthouse Integration API (partner access)")


# ─── Registration (at import; module.py imports this through the router) ───

try:
    from clefs.core.ingest import register_commit_handler

    register_commit_handler("COMP_RATES", commit_comp_rates)
except ImportError:  # the ingest package is optional to the app (jobs.OPTIONAL_REGISTRANTS)
    pass
