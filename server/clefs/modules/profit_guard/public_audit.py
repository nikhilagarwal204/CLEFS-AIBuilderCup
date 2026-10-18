"""
Public "Audit one OTA statement" lead magnet (02 spec 3.11; Canon 14, 23;
00-PLAN Profit Guard "Public audit"). The UI is /free-ota-audit and
/free-ota-audit/r/[token].

Flow:
1. POST /public/audits (anonymous multipart upload). The file is sniffed by
   its bytes (CSV/TSV, XLSX, XLS or PDF only), capped at 4 MB (Vercel
   refuses a bigger function body before FastAPI runs), 5,000 rows and 10
   PDF pages, and rate-limited to 3 audits per IP hash per day and 50 per
   day in total. It goes through the same core/ingest pipeline as a
   console import, in the sandbox (hotel_slug NULL, via PUBLIC_AUDIT), and is
   never committed: no Profit Guard table of any hotel is touched.
2. The staged rows run through the sandbox detectors below with the default
   assumptions (Dubai, 3-star, convention A, and a default percentage per
   OTA, not a market rate: Booking 15% / Agoda 18% / Expedia 18%). Only what
   the statement's own lines show counts (RECOVERABLE: L3, L12). L1/L1b are
   TO_CONFIRM and L2 CHECK_CONTRACT ("Public classes" below): they keep
   their calc in the full report but never reach an AED figure or the
   findings count. Detectors that need PMS data (L3, L4, L5, L14) run only
   from the statement's own status columns; the rest are listed as not
   checked, with the reason.
3. The answer is a teaser (the AED found, the findings count and the number
   of lines to confirm). The full breakdown unlocks
   after POST /public/audits/{token}/lead (name, hotel, WhatsApp or email,
   the contact consent; the Leakage Index consent is separate and optional).
   Guest names are always masked, whoever asks.
4. The unlock starts a small AI snapshot through visibility.api (stub-safe:
   None means the report shows no AI section), notifies the founder by email
   and WhatsApp (clefs.notify), and, with the index consent, adds one
   anonymised sample to the UAE OTA Leakage Index (index.py).
5. pg.public_audit_purge (daily) deletes the file, the staged rows, the
   snapshot and the lead row at 30 days. Index samples survive, anonymised
   (their audit_id is set NULL).

The token is shown once; only its sha256 is stored, as is an HMAC of the IP.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import io
import logging
import secrets
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from clefs import db, jobs
from clefs.core import clock, privacy
from clefs.core.errors import ClefsError
from clefs.core.money import money_str
from clefs.core.tax import PercentFee, TaxProfile, decompose
from clefs.modules.profit_guard import index as leak_index
from clefs.modules.profit_guard.detectors.base import pct_amount, rnd, step, tol
from clefs.modules.profit_guard.models import normalize_ota
from clefs.settings import env, get_settings

log = logging.getLogger("clefs.profit_guard.public_audit")

router = APIRouter(prefix="/public", tags=["profit_guard-public"])

# ─── Limits and defaults ───

# 4 MB, not the 10 MB of 02 spec 3.11: Vercel refuses a function body over
# 4.5 MB with its own 413 before FastAPI runs, so a bigger cap could only
# fail as an error page nobody wrote. MAX_BYTES + UPLOAD_OVERHEAD (the whole
# request) stays under it. Decimal megabytes, as the copy says "4 MB".
MAX_BYTES = 4_000_000
UPLOAD_OVERHEAD = 64 * 1024           # multipart boundaries and the few text fields
MAX_ROWS = 5000
MAX_PDF_PAGES = 10
IP_DAILY_LIMIT = 3
GLOBAL_DAILY_LIMIT = 50
DAY_S = 86400
RETENTION = timedelta(days=30)
FILE_PURPOSE = "pg.public_audit"
NOTIFY_PURPOSE = "profit_guard.public_audit_lead"
PURGE_JOB = "pg.public_audit_purge"
DUBAI = ZoneInfo("Asia/Dubai")

# Default assumptions (02 spec 3.11): the audit knows nothing about the
# property, so it reads a typical Dubai 3-star under convention A with a
# default percentage per OTA. That percentage is not a market rate (the
# contract decides), which is why L2 against it is CHECK_CONTRACT. Every
# figure in the report says it is an estimate under these.
DEFAULT_PCT = {"BOOKING": Decimal(15), "AGODA": Decimal(18), "EXPEDIA": Decimal(18)}
DEFAULTS = {
    "emirate": "DXB", "city": "Dubai", "star": 3, "property_category": "hotel_3", "convention": "A",
    "commission_pct": {k: str(v) for k, v in DEFAULT_PCT.items()},
    "fees": "service charge 10% of room; municipality fee 7% of room and service charge; VAT 5% of room, "
            "service charge and municipality fee; Tourism Dirham AED 10 per room-night (3-star), first 30 nights",
}
# The Dubai rules of 0023_core_tax_seed.sql for a 3-star, built by hand: the
# sandbox has no hotel, so rules.resolve_profile (per hotel) cannot run.
SANDBOX_PROFILE = TaxProfile(
    convention="A", currency="AED",
    fees=(PercentFee("SC", Decimal("0.10"), ("ROOM",)), PercentFee("MF", Decimal("0.07"), ("ROOM", "SC")),
          PercentFee("VAT", Decimal("0.05"), ("ROOM", "SC", "MF"))),
    td_rate=Decimal(10), td_cap_nights=30, emirate="DXB", property_category="hotel_3",
)
# The core/ingest alias-map source per OTA, used when the uploader
# names the OTA and the headers alone do not identify the export.
SOURCE_FOR_OTA = {"BOOKING": "booking_res_statement", "AGODA": "agoda_finance", "EXPEDIA": "expedia_reservations"}

# ─── Tokens and identity ───


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ip_hash(ip: str | None) -> str:
    """HMAC of the client IP (keyed with SECRET_KEY): enough to count three
    audits a day, useless to anyone reading the table."""
    key = get_settings().SECRET_KEY.encode()
    return hmac.new(key, f"pg.public_audit|{ip or 'unknown'}".encode(), hashlib.sha256).hexdigest()


def _client_ip() -> str | None:
    from clefs.core.context import request_info

    return request_info().ip


def mask_name(name: Any) -> str | None:
    """Initials only ("AL MANSOURI/AHMED" -> "A. M. A."): the report never
    carries a guest name, whether or not the lead unlocked it."""
    if name is None:
        return None
    parts = [p for p in str(name).replace("/", " ").replace(",", " ").split() if p[:1].isalpha()]
    if not parts:
        return "Guest"
    return " ".join(f"{p[0].upper()}." for p in parts[:3])

# ─── Upload: size cap, MIME sniff, row and page caps ───

# Magic numbers of formats that are refused outright, whatever the name
# says (a renamed executable or image never reaches the parsers).
REFUSED_MAGIC = (b"MZ", b"\x7fELF", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"Rar!", b"7z\xbc\xaf", b"\x1f\x8b",
                 b"%!PS", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe")


def _unsupported(detail: str = "Upload a CSV, Excel (XLSX or XLS) or PDF statement") -> ClefsError:
    return ClefsError(415, detail, "unsupported_file_type")


def _too_large() -> ClefsError:
    return ClefsError(413, f"Files can be at most {MAX_BYTES // 1_000_000} MB", "file_too_large")


def sniff_type(data: bytes) -> str:
    """csv | xlsx | xls | pdf from the bytes alone (the declared type and
    the file name are never trusted). 415 unsupported_file_type otherwise."""
    if not data:
        raise ClefsError(422, "The file is empty", "empty_file")
    head = data[:8]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "xls"
    if head.startswith(b"PK\x03\x04"):
        # Any zip starts like this; an XLSX has the OOXML parts inside.
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile as exc:
            raise _unsupported() from exc
        if "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names):
            return "xlsx"
        raise _unsupported()
    if any(head.startswith(m) for m in REFUSED_MAGIC):
        raise _unsupported()
    return _sniff_text(data)


def _sniff_text(data: bytes) -> str:
    """A delimited text file: decodable (UTF-8, UTF-16 with or without a
    BOM, or cp1256), no control bytes, not markup or JSON."""
    sample = data[:8192]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = sample.decode("utf-16", errors="replace")
    elif len(sample) >= 4 and sample[1::2].count(0) > len(sample) // 4:
        text = sample.decode("utf-16-le", errors="replace")
    elif len(sample) >= 4 and sample[0::2].count(0) > len(sample) // 4:
        text = sample.decode("utf-16-be", errors="replace")
    else:
        if b"\x00" in sample:
            raise _unsupported()
        try:
            text = sample.decode("utf-8-sig")
        except UnicodeDecodeError:
            # A cut multi-byte character at the sample edge is fine; a
            # binary file is not (cp1256 decodes almost anything, so the
            # control-byte check below is what refuses binaries).
            text = sample.decode("cp1256", errors="replace")
    body = text.lstrip("﻿ \r\n\t")
    if body[:1] in ("<", "{", "["):
        raise _unsupported()
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t")
    if controls > max(2, len(text) // 200):
        raise _unsupported()
    if not any(d in text for d in (",", ";", "\t", "|")):
        raise _unsupported("The file does not look like a statement table")
    return "csv"


def check_caps(data: bytes, fmt: str, filename: str | None) -> int:
    """The row cap for tables (before anything is staged) and the page cap
    for PDFs. Returns the data rows or pages counted."""
    if fmt == "pdf":
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            pages = len(PdfReader(io.BytesIO(data)).pages)
        except (PdfReadError, ValueError, KeyError, OSError) as exc:
            raise ClefsError(422, "The PDF could not be read", "invalid_pdf") from exc
        if pages > MAX_PDF_PAGES:
            raise ClefsError(413, f"PDF statements can have at most {MAX_PDF_PAGES} pages", "too_many_pages")
        return pages
    from clefs.core.ingest import sniff_file

    try:
        table = sniff_file(data, filename=filename)
    except ClefsError as exc:
        raise ClefsError(422, exc.detail, exc.code) from exc
    if len(table.rows) > MAX_ROWS:
        raise ClefsError(413, f"Statements can have at most {MAX_ROWS} rows", "too_many_rows")
    return len(table.rows)

# ─── Sandbox detectors ───

# The console detectors read one hotel's stored claims and PMS stays; the
# sandbox has neither, so the statement-only formulas run here over the
# staged canonical rows, with the same tolerance, rounding and CalcStep
# shape (detectors/base.py) so a finding reads the same in both places.
NO_REVENUE = ("NO_SHOW", "CANCELLED")
THRESHOLD_PP = Decimal("0.1")          # L2: exactly 0.1 pp does not flag
MISSING_PENALTY = Decimal("0.1")       # confidence lost per unknown input
NOT_CHECKED = {
    "L4": "Early departures need the PMS's actual check-out dates.",
    "L5": "Commission base vs folio needs the PMS folio.",
    "L6": "Discount stacking needs your programme and rate-plan settings.",
    "L7": "Rate parity needs rate evidence from the channels.",
    "L8": "Uncharged virtual cards need the card report.",
    "L9": "Expired virtual cards need the card report.",
    "L10": "Payout mismatches need the payout statement and bank credits.",
    "L11": "FX losses need the payout and bank statements.",
    "L13": "Chargebacks need the payout statement.",
    "L15": "The VAT reverse charge needs the OTA's invoicing entity and your VAT returns.",
}

# ─── Public classes (the GTM guard of 2026-10-06) ───

# A statement alone cannot settle L1 or L2, and the founders pitch with this
# report, so no AED figure may rest on either. Until the upload asks (no
# form fields yet) every upload is read as "not sure":
# - Dubai's 2026 relief may have exempted collecting the municipality fee
#   and the Tourism Dirham, and a property may have them set up as charges:
#   MF and TD inside every Booking gross (SANDBOX_PROFILE, _fees) is a guess,
#   so L1 and L1b are TO_CONFIRM;
# - L2 compares with a default percentage per OTA, not the contract, and a
#   Preferred or Visibility Booster property pays more by its own terms, so
#   L2 is CHECK_CONTRACT.
# Both keep their calc in the full report, "to confirm with you", and stay
# out of teaser.found, the findings count and the Leakage Index. So does
# Agoda's STRUCTURAL row: informational, and it depends on whether MF was
# collected. Only RECOVERABLE counts (L3 and L12, read from the statement's
# own lines; an L12 whose booking has no commissioned line here takes the
# default percent for its amount, and its calc and `missing` say so). The
# client mirrors this in free-ota-audit/_view.ts.
COUNTED = "RECOVERABLE"
TO_CONFIRM = "TO_CONFIRM"
CHECK_CONTRACT = "CHECK_CONTRACT"
CONFIRM_CLASSES = (TO_CONFIRM, CHECK_CONTRACT)
# Byte-identical to DETECTORS.L2.title in _view.ts (a client test compares).
L2_TITLE = "Above the percentage this check assumed: check your contract and programme terms"
L1_TITLE = "Commission on municipality fee and Tourism Dirham: to confirm with you"
L1B_TITLE = "Commission you may stop paying on those fees: to confirm with you"
# The calc step that names the default percent wherever one stands in for
# the contract (L2 always, L12 without a commissioned line of its booking).
ASSUMED_PCT = ("Assumed percent", "a default percentage per OTA, not a market rate")


def _d(v: Any) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _i(v: Any) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(Decimal(str(v)))
    except (ArithmeticError, ValueError):
        return None


def _pct_of(v: Any) -> Decimal | None:
    """The statement's printed percent: staged as a ratio (0.15); a value
    above 1 is taken as already in percent."""
    if v in (None, ""):
        return None
    try:
        x = Decimal(str(v))
    except ArithmeticError:
        return None
    return x * 100 if x <= 1 else x


def normalize_line(r: dict, currency: str) -> dict:
    """One staged canonical row in the shape the checks read."""
    ci, co = _d(r.get("check_in")), _d(r.get("check_out"))
    nights = _i(r.get("room_nights")) or ((co - ci).days if ci and co and co > ci else 1)
    return {
        "row": r.get("_row_no"), "page": r.get("_page"),
        "reservation": str(r.get("ota_res_id") or "").strip() or None,
        "guest": mask_name(r.get("guest_name")), "check_in": ci, "check_out": co,
        "nights": max(1, nights), "rooms": max(1, _i(r.get("rooms")) or 1),
        "status": str(r.get("line_status") or "OK").upper(), "gross": _i(r.get("gross_minor")),
        "base": _i(r.get("commissionable_minor")), "commission": _i(r.get("commission_minor")) or 0,
        "printed_pct": _pct_of(r.get("commission_pct")),
        "currency": (str(r.get("currency") or "").strip().upper()[:3] or currency),
        "collect_model": r.get("collect_model"),
    }


def applied_pct(ln: dict) -> Decimal | None:
    """The percent the OTA applied: printed, else commission / base."""
    if ln["printed_pct"] is not None:
        return ln["printed_pct"]
    base = ln["base"] or ln["gross"]
    if base and base > 0 and ln["commission"] > 0:
        return Decimal(ln["commission"]) * 100 / Decimal(base)
    return None


def _conf(base: str, missing: list[str]) -> str:
    c = Decimal(base) - MISSING_PENALTY * len(missing)
    return str(max(Decimal("0.3"), c).quantize(Decimal("0.01")))


def dispute_deadline(ota: str, ln: dict) -> datetime | None:
    """The OTA's dispute window, read conservatively without a contract:
    Booking 48 h after check-out 00:00 Dubai time; Expedia the 4th of the
    next month at 12:00 Dubai; Agoda counts from its invoice date, which a
    statement row does not carry (None)."""
    co = ln["check_out"]
    if co is None:
        return None
    if ota == "BOOKING":
        return datetime.combine(co, time(0, 0), tzinfo=DUBAI) + timedelta(hours=48)
    if ota == "EXPEDIA":
        y, m = (co.year + 1, 1) if co.month == 12 else (co.year, co.month + 1)
        return datetime.combine(date(y, m, 4), time(12, 0), tzinfo=DUBAI)
    return None


def _finding(detector: str, klass: str, ln: dict | None, amount: int, currency: str, conf: str, *,
             title: str, calc: list[dict], missing: list[str] | None = None, scenario: str | None = None,
             deadline: datetime | None = None, now: datetime | None = None, **extra: Any) -> dict:
    ln = ln or {}
    return {
        "detector": detector, "class": klass, "scenario": scenario, "title": title,
        "amount_minor": amount, "amount": money_str(amount, currency), "currency": currency, "confidence": conf,
        "missing": missing or [], "row": ln.get("row"), "page": ln.get("page"), "reservation": ln.get("reservation"),
        "guest": ln.get("guest"), "check_in": ln["check_in"].isoformat() if ln.get("check_in") else None,
        "check_out": ln["check_out"].isoformat() if ln.get("check_out") else None, "status": ln.get("status"),
        "deadline_at": deadline.isoformat() if deadline else None,
        "window_open": (deadline > now) if deadline and now else None, "calc": calc, **extra,
    }


def check_l3(ota: str, ln: dict, now: datetime) -> dict | None:
    """L3 from the statement's own status: commission on a no-show or a
    cancellation. Whether a fee was collected is unknown (commission on a
    collected fee is due), hence the confidence penalty."""
    commission = ln["commission"]
    if ln["status"] not in NO_REVENUE or commission <= 0 or commission <= tol(commission):
        return None
    deadline = dispute_deadline(ota, ln)
    return _finding(
        "L3", "RECOVERABLE", ln, commission, ln["currency"], _conf("0.9", ["fee_collected"]),
        title="Commission charged on a " + ("no-show" if ln["status"] == "NO_SHOW" else "cancelled booking"),
        missing=["fee_collected"], deadline=deadline, now=now,
        calc=[step("Commission charged", "commission on the line", {"status": ln["status"]}, commission),
              step("Commission due on a fee collected", "pct x fee collected (unknown, taken as 0)", {}, 0),
              step("Overcharge", "commission - due", {}, commission)],
    )


def check_l2(ota: str, ln: dict) -> dict | None:
    """L2 against the default percent: effective - default > 0.1 pp and the
    amount clears the tolerance. CHECK_CONTRACT, never counted: the default
    is not a market rate, and the contract or a programme (Preferred,
    Visibility Booster) can raise the percent legitimately (confidence 0.5)."""
    expected = DEFAULT_PCT.get(ota)
    base, comm = ln["base"] or ln["gross"], ln["commission"]
    if expected is None or ln["status"] in NO_REVENUE or not base or base <= 0 or comm <= 0:
        return None
    effective = Decimal(comm) * 100 / Decimal(base)
    diff = effective - expected
    if diff <= THRESHOLD_PP:
        return None
    amount = rnd(diff * Decimal(base) / 100)
    if amount <= tol(rnd(expected * Decimal(base) / 100)):
        return None
    return _finding(
        "L2", CHECK_CONTRACT, ln, amount, ln["currency"], "0.50",
        title=L2_TITLE, missing=["contract", "programs"],
        calc=[step("Effective percent", "commission / base x 100", {"commission_minor": comm, "base_minor": base},
                   result=effective.quantize(Decimal("0.001"))),
              step(*ASSUMED_PCT, {"ota": ota}, result=expected),
              step("Above the assumed percent", "(effective - assumed) x base",
                   {"diff_pp": diff.quantize(Decimal("0.001")), "threshold_pp": THRESHOLD_PP}, amount)],
    )


def _fees(ln: dict, *, td_in_rate: bool = True) -> dict[str, int]:
    """MF, TD and VAT inside the gross under the sandbox profile (TD per
    room-night, first 30 nights). AED only: the profile's fees are AED.
    td_in_rate=False: the OTA's rate carries no TD (Agoda: collected at the
    property, outside the RSR), so none is stripped from the gross."""
    units = min(ln["nights"], SANDBOX_PROFILE.td_cap_nights or ln["nights"]) * ln["rooms"] if td_in_rate else 0
    gross = Decimal(ln["gross"]) / 100
    b = decompose(gross, units, SANDBOX_PROFILE).minor()
    return {"mf": int(b["mf"]), "td": int(b["td"]), "vat": int(b["vat"])}


def check_l1(ota: str, ln: dict) -> list[dict]:
    """L1 under both charge scenarios (Canon K2). Booking charges commission
    on room + SC + VAT; MF and TD leave the base only when the property set
    them up as charges, which the audit cannot see:
    - fees_configured: commission on MF + TD was charged although excluded,
      recoverable once confirmed (L1);
    - fees_not_configured: the same commission is due under the contract
      but stops once the fees are configured, preventable once confirmed
      (L1b).
    Both are TO_CONFIRM: whether MF and TD sit inside the gross at all is a
    guess too (Public classes). Agoda's RSR is tax-inclusive by contract:
    STRUCTURAL, informational."""
    comm, gross = ln["commission"], ln["gross"]
    if ln["status"] in NO_REVENUE or comm <= 0 or not gross or gross <= 0 or ln["currency"] != "AED":
        return []
    pct = applied_pct(ln) or DEFAULT_PCT.get(ota) or Decimal(0)
    # Agoda's default terms exclude TOURISMFEE (contracts.DEFAULT_TERMS,
    # as detectors/l01_commission_base._agoda does): the TD is not in the
    # rate, so neither stripped nor counted (review public-audit-agoda-td).
    fees = _fees(ln, td_in_rate=ota != "AGODA")
    if ota == "AGODA":
        taxes = fees["mf"] + fees["vat"]
        amount = pct_amount(taxes, pct)
        if amount <= tol(amount):
            return []
        return [_finding("L1", "STRUCTURAL", ln, amount, "AED", "0.70", title="Commission on taxes inside the rate",
                         missing=["contract"], scenario="agoda_tax_inclusive",
                         calc=[step("Taxes inside the rate", "MF + VAT (Dubai 3-star, convention A; the "
                                    "Tourism Dirham is collected at the property)", fees, taxes),
                               step("Commission on them", "pct x taxes", {"pct": pct.quantize(Decimal("0.01"))},
                                    amount)])]
    if ota != "BOOKING":
        return []
    reported = ln["base"] or gross
    excluded = fees["mf"] + fees["td"]
    expected = gross - excluded
    excess = reported - expected
    if excess <= tol(expected):
        return []
    amount = pct_amount(excess, pct)
    calc = [step("Excluded charges", "MF + TD inside the gross (Dubai 3-star, convention A)",
                 {"mf_minor": fees["mf"], "td_minor": fees["td"]}, excluded),
            step("Expected base", "gross - excluded charges", {"gross_minor": gross}, expected),
            step("Excess base", "reported base - expected base",
                 {"reported_minor": reported, "tol_minor": tol(expected)}, excess),
            step("Commission on the excess", "pct x excess", {"pct": pct.quantize(Decimal("0.01"))}, amount)]
    missing = ["charge_configuration", "price_breakdown"]
    return [
        _finding("L1", TO_CONFIRM, ln, amount, "AED", _conf("0.9", missing), missing=missing,
                 title=L1_TITLE, scenario="fees_configured", calc=calc),
        _finding("L1b", TO_CONFIRM, ln, amount, "AED", _conf("0.9", missing), missing=missing,
                 title=L1B_TITLE, scenario="fees_not_configured", calc=calc),
    ]


def check_l12(ota: str, lines: list[dict]) -> list[dict]:
    """L12 per booking: a refund (negative gross) whose commission was not
    reduced pro rata. Expected reduction = pct x refunded; actual = the
    booking's negative commission lines. pct is the booking's own, from its
    commissioned lines in this statement. A booking billed on an earlier
    statement has none here, so the default per OTA stands in for the
    amount. That amount rests on a percentage the check assumed, like L2's,
    so it is CHECK_CONTRACT: kept in the full report with its calc, never in
    the headline, the count or the index. Only an L12 priced at the
    booking's own percent is RECOVERABLE."""
    out: list[dict] = []
    by_res: dict[str, list[dict]] = defaultdict(list)
    for ln in lines:
        if ln["reservation"]:
            by_res[ln["reservation"].upper()].append(ln)
    for group in by_res.values():
        refunded = sum(-ln["gross"] for ln in group if ln["gross"] is not None and ln["gross"] < 0)
        if refunded <= 0:
            continue
        reduced = sum(-ln["commission"] for ln in group if ln["commission"] < 0)
        paid = [ln for ln in group if (ln["gross"] or 0) > 0 and ln["commission"] > 0]
        base = sum(ln["base"] or ln["gross"] for ln in paid)
        assumed = not base
        pct = DEFAULT_PCT.get(ota) if assumed else Decimal(sum(ln["commission"] for ln in paid)) * 100 / Decimal(base)
        if pct is None:
            continue
        expected = pct_amount(refunded, pct)
        amount = expected - reduced
        if amount <= tol(expected):
            continue
        ln = next(x for x in group if x["gross"] is not None and x["gross"] < 0)
        calc = [step("Refunded", "Σ |negative gross|", {}, refunded)]
        if assumed:
            calc.append(step(*ASSUMED_PCT, {"ota": ota}, result=pct))
        calc += [step("Expected reduction", "pct x refunded", {"pct": pct.quantize(Decimal("0.01"))}, expected),
                 step("Shortfall", "expected - commission reduced", {"reduced_minor": reduced}, amount)]
        out.append(_finding(
            "L12", CHECK_CONTRACT if assumed else COUNTED, ln, amount, ln["currency"], "0.80",
            title="Refund without the commission reduced, at an assumed percentage: check your contract"
            if assumed else "Refund without the commission reduced",
            missing=["contract"] if assumed else None, calc=calc))
    return out

# ─── Report ───


def _sum(fs: list[dict]) -> int:
    return sum(f["amount_minor"] for f in fs)


def _money(minor: int, ccy: str) -> dict:
    return {"minor": minor, "amount": money_str(minor, ccy)}


def _tally(fs: list[dict]) -> dict[str, dict]:
    """{detector: {n, minor}} over these findings."""
    out: dict[str, dict] = {}
    for f in fs:
        d = out.setdefault(f["detector"], {"n": 0, "minor": 0})
        d["n"] += 1
        d["minor"] += f["amount_minor"]
    return out


def _indexed(fs: list[dict]) -> list[dict]:
    """What a Leakage Index sample counts: the headline's own findings
    (RECOVERABLE), of the statement-only detectors in INDEX_DETECTORS."""
    return [f for f in fs if f["class"] == COUNTED and f["detector"] in leak_index.INDEX_DETECTORS]


def headline(findings: list[dict], ccy: str) -> dict:
    """The teaser fields the public classes decide. `found` sums RECOVERABLE
    findings only, at both ends: L1 used to make it a range (high = low +
    commission on fees) and is now to confirm, so low == high, still a pair
    for the clients that read one. `to_confirm` is how many statement lines
    carry a TO_CONFIRM or CHECK_CONTRACT finding (L1, its L1b twin and L2 on
    one line count once): a count of lines, never an amount."""
    counted = [f for f in findings if f["class"] == COUNTED]
    confirm = {(f["row"], f["page"]) for f in findings if f["class"] in CONFIRM_CLASSES}
    return {
        "found": {"low": _money(_sum(counted), ccy), "high": _money(_sum(counted), ccy)},
        "findings": len(counted),
        "by_detector": {k: v["n"] for k, v in sorted(_tally(counted).items())},
        "to_confirm": len(confirm),
    }


def run_sandbox(rows: list[dict], *, ota: str | None, currency: str, now: datetime) -> dict:
    """Every sandbox check over the staged rows -> {teaser, full, index}.

    The AED found holds only RECOVERABLE findings (L3, L12), which the
    statement's own lines show; only an L12 amount may rest on the default
    percent, and its calc says so. L1/L1b (TO_CONFIRM) and L2
    (CHECK_CONTRACT) stay in the full report with their calc and are
    counted as lines to confirm; Agoda's structural commission on taxes is
    shown for information. None of them is ever added to a figure. Lines in
    a second currency are counted, not audited."""
    every = [normalize_line(r, currency) for r in rows]
    counts = Counter(ln["currency"] for ln in every)
    ccy = counts.most_common(1)[0][0] if counts else currency
    lines = [ln for ln in every if ln["currency"] == ccy]
    code = ota or "UNKNOWN"
    findings: list[dict] = []
    for ln in lines:
        findings += [f for f in (check_l3(code, ln, now), check_l2(code, ln)) if f is not None]
        findings += check_l1(code, ln)
    findings += check_l12(code, lines)
    findings.sort(key=lambda f: (-f["amount_minor"], f["row"] or 0))

    structural = _sum([f for f in findings if f["class"] == "STRUCTURAL"])
    commission = sum(ln["commission"] for ln in lines if ln["commission"] > 0)
    indexed = _indexed(findings)
    urgent = sorted(({"detector": "L14", "reservation": f["reservation"], "row": f["row"],
                      "deadline_at": f["deadline_at"], "amount_minor": f["amount_minor"], "amount": f["amount"]}
                     for f in findings if f["detector"] == "L3" and f["window_open"]), key=lambda u: u["deadline_at"])
    ins = [ln["check_in"] for ln in lines if ln["check_in"]]
    outs = [ln["check_out"] or ln["check_in"] for ln in lines if ln["check_out"] or ln["check_in"]]
    period = {"from": min(ins).isoformat(), "to": max(outs).isoformat()} if ins and outs else None
    months = Counter(d.replace(day=1) for d in outs)
    teaser = {
        "ota_code": ota, "currency": ccy, "lines": len(lines), "period": period,
        "commission": _money(commission, ccy), **headline(findings, ccy), "open_deadlines": len(urgent),
    }
    full = {
        # No per-scenario totals any more (fees configured or not): each was
        # an AED sum resting on L1. The rows carry both readings and calc.
        # `structural` stays for the API; the report shows rows, not this sum.
        "findings": findings,
        "structural": _money(structural, ccy), "deadlines": urgent,
        "status_counts": dict(Counter(ln["status"] for ln in lines)),
        "skipped": {"other_currency_lines": len(every) - len(lines), "ota_unknown": ota is None},
        "assumptions": DEFAULTS,
        "not_checked": [{"detector": k, "reason": v} for k, v in NOT_CHECKED.items()],
        "notes": _notes(ota, ccy),
    }
    index = {
        "ota_code": code, "currency": ccy, "lines": len(lines), "commission_minor": commission,
        "leak_minor": _sum(indexed),
        "period_month": months.most_common(1)[0][0].isoformat() if months else None,
        "emirate": DEFAULTS["emirate"], "property_band": f"{DEFAULTS['star']}-star",
        "detector_counts": _tally(indexed),
    }
    return {"teaser": teaser, "full": full, "index": index}


def _notes(ota: str | None, ccy: str) -> list[str]:
    out = ["Estimates under default assumptions; your contract, programmes and extranet settings decide the "
           "real figures. Guest names are shown as initials only."]
    if ota is None:
        out.append("The OTA was not recognised: the rate and fee checks were skipped. Upload again and say "
                   "which OTA the statement is from.")
    if ccy != "AED":
        out.append(f"The statement is in {ccy}: the fee checks (L1) need AED amounts and were skipped.")
    return out


# ─── Reports stored before the guard ───

# A report saved before 2026-10-06 has L1 in found.high and L1/L2 in the
# findings count, and its token link lives 30 days (RETENTION). Read through
# current_report (view, unlock), such a report (no teaser.to_confirm) takes
# the public classes on the way out, so an old link never shows an
# unverified AED figure either. The purge removes the last one by
# 2026-11-06; drop this then.
LEGACY_CLASS = {"L1": TO_CONFIRM, "L1b": TO_CONFIRM, "L2": CHECK_CONTRACT}
# The class alone is not enough: Report.tsx prints each row's calc under "How
# it was worked out", and an old L2 called its amount an "Overcharge" against
# a "default contract percent" (title "Commission above the standard 15%"),
# the framing the guard removes. Its words go with its class.
LEGACY_TITLE = {"L1": L1_TITLE, "L1b": L1B_TITLE, "L2": L2_TITLE}
LEGACY_L2_STEP = {"Expected percent": ASSUMED_PCT,
                  "Overcharge": ("Above the assumed percent", "(effective - assumed) x base")}


def _legacy_row(f: dict) -> dict:
    """One pre-guard finding under the public classes, in today's words."""
    d = f.get("detector")
    # Agoda's structural row is an L1 too, and was STRUCTURAL already.
    if d not in LEGACY_CLASS or f.get("scenario") == "agoda_tax_inclusive":
        return f
    out = {**f, "class": LEGACY_CLASS[d], "title": LEGACY_TITLE[d]}
    if d == "L2":
        calc = []
        for s in f.get("calc") or []:
            words = LEGACY_L2_STEP.get(s.get("label"))
            calc.append({**s, "label": words[0], "formula": words[1]} if words else s)
        out["calc"] = calc
    return out


def current_report(rep: dict) -> dict:
    """The stored report under the public classes: unchanged when it was
    built under them, re-read from its own findings when it was not."""
    t, full = rep.get("teaser"), rep.get("full")
    if not isinstance(t, dict) or "to_confirm" in t or not isinstance(full, dict):
        return rep
    fs = [_legacy_row(f) for f in full.get("findings") or []]
    out = {**rep, "teaser": {**t, **headline(fs, t.get("currency") or "AED")},
           "full": {**{k: v for k, v in full.items() if k != "scenarios"}, "findings": fs}}
    if isinstance(rep.get("index"), dict):
        out["index"] = {**rep["index"], "leak_minor": _sum(_indexed(fs)), "detector_counts": _tally(_indexed(fs))}
    return out

# ─── Store and read ───

CONTENT_TYPES = {"csv": "text/csv", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "xls": "application/vnd.ms-excel", "pdf": "application/pdf"}
PUBLIC_OTAS = tuple(DEFAULT_PCT)
# The upload ticks (AuditUpload.tsx shows these exact words; a client test
# compares them): processing, and the transfer abroad. Each grant is
# stored on the audit's report with its wording and version.
# Canon PDPL: Neon has no UAE region, so EVERY upload (CSV and Excel too:
# stored through clefs.files in Postgres, guest names intact) leaves the
# UAE; the tick is required for every format, not only a PDF (review
# compliance-ota-audit-no-transfer-consent). v2 reworded it.
UPLOAD_CONSENT_VERSION = "2026-09-29.2"
UPLOAD_CONSENT_TEXTS = {
    "processing": "Clefs may process this statement to run the audit. It holds guest names and booking numbers: "
                  "they are reduced to initials in the report, and the file, its rows and the report are deleted "
                  "after 30 days.",
    "transfer_abroad": "This statement, guest names and booking numbers included, is processed outside the UAE by "
                       "Clefs' sub-processors: Clefs stores it on its database in Singapore, and a PDF's full text "
                       "is also read by Clefs' AI provider in the United States.",
}


# ─── OTA detection fallback (QA PUB-1) ───

# Core's detect terms need every term present; a real Booking.com export
# headed "Booking number" misses "book number" and fell to the generic
# source, so the teaser read "OTA not recognised" and skipped most checks
# (1 finding instead of 86). The audit then scores the headers against
# every OTA statement source's field aliases itself: the booking number
# and the commission must both be recognised, the winner must clear the
# minimum and be strictly ahead (a tie names no OTA).
DETECT_MIN_FIELDS = 5
DETECT_REQUIRED = ("ota_res_id", "commission_minor")


def guess_source(headers: list[str]) -> tuple[str, str] | None:
    """(source_code, ota_code) of the OTA statement layout these headers
    best match, or None."""
    from clefs.core.ingest import normalize_header
    from clefs.core.ingest.aliases import sources

    norms = {normalize_header(h) for h in headers if h}
    scored: list[tuple[int, str, str]] = []
    for m in sources("OTA_STATEMENT"):
        ota = normalize_ota(getattr(m, "ota_code", None))
        if ota not in PUBLIC_OTAS:
            continue
        hit = {f for f, aliases in m.fields.items() if any(a in norms for a in aliases)}
        if all(f in hit for f in DETECT_REQUIRED) and len(hit) >= DETECT_MIN_FIELDS:
            scored.append((len(hit), m.source_code, ota))
    scored.sort(reverse=True)
    if not scored or (len(scored) > 1 and scored[1][0] == scored[0][0] and scored[1][2] != scored[0][2]):
        return None
    return scored[0][1], scored[0][2]


def _detect(data: bytes, filename: str | None, fmt: str) -> tuple[str, str] | None:
    """guess_source over a CSV or Excel file's header row; None for a PDF
    (core's extraction names its own source) or an unreadable table."""
    if fmt == "pdf":
        return None
    from clefs.core.ingest import sniff_file

    try:
        return guess_source(list(sniff_file(data, filename=filename).headers))
    except ClefsError:
        return None


def _ingest(cur: Any, file_id: str, ota: str | None, *, guess: tuple[str, str] | None = None) -> dict:
    """The sandbox batch, staged and ready to read (REVIEW). A layout the
    alias maps do not cover takes the AI mapping proposal when it covers
    every required field: the uploader is the only person there is, and
    nothing is ever committed from the sandbox. Anything else is 422."""
    from clefs.core.ingest import committable_rows, confirm_mapping, create_batch

    source = SOURCE_FOR_OTA.get(ota) if ota else None
    if ota is None and guess is not None:
        source, ota = guess
    batch = create_batch(cur, None, file_id=file_id, kind="OTA_STATEMENT", source_code=source, ota_code=ota,
                         via="PUBLIC_AUDIT")
    if batch["status"] == "MAPPING_REQUIRED":
        suggestion = batch.get("mapping_suggestion") or {}
        if not suggestion.get("mapping"):
            missing = ", ".join((batch.get("stats") or {}).get("missing") or [])
            raise ClefsError(422, "The statement's columns were not recognised" + (f" (missing: {missing})"
                             if missing else ""), "unrecognised_columns")
        try:
            batch = confirm_mapping(cur, None, batch["id"], mapping=suggestion["mapping"],
                                    transforms=suggestion.get("transforms"), save_template=False)
        except ClefsError as exc:
            raise ClefsError(422, "The statement's columns were not recognised", "unrecognised_columns") from exc
    if batch["status"] == "FAILED":
        err = (batch.get("errors") or [{}])[0]
        raise ClefsError(422, err.get("detail") or "The statement could not be read",
                         err.get("code") or "unreadable_file")
    if batch["status"] not in ("REVIEW", "COMMITTED"):
        raise ClefsError(422, "The statement could not be read", "unreadable_file")
    rows = committable_rows(cur, batch["id"])
    if len(rows) > MAX_ROWS:
        raise ClefsError(413, f"Statements can have at most {MAX_ROWS} rows", "too_many_rows")
    if not rows:
        raise ClefsError(422, "No statement lines were found in the file", "no_rows")
    return {**batch, "_rows": rows}


def create_audit(cur: Any, data: bytes, *, filename: str | None, ota_code: str | None, ip_h: str,
                 consent_transfer: bool = False) -> tuple[str, dict]:
    """Sniff, cap, store, stage, run the sandbox checks and save the lead
    row. Any failure raises and the caller's transaction rolls the file and
    the batch back: an unreadable upload leaves nothing behind.

    Every statement is stored on Clefs' database in Singapore (Neon has
    no UAE region), guest names and booking numbers intact, and a PDF's
    whole text is also read by the AI provider in the United States (core
    ingest pdf.extract). Both are transfers abroad of third-party personal
    data, so every format needs the tick (`consent_transfer`), refused
    before anything is stored."""
    ota = normalize_ota(ota_code) if ota_code else None
    if ota is not None and ota not in PUBLIC_OTAS:
        raise ClefsError(422, "ota_code must be BOOKING, AGODA or EXPEDIA", "invalid_ota")
    if not consent_transfer:
        raise ClefsError(422, "The statement is stored and processed outside the UAE by Clefs' sub-processors. "
                              "Tick the box that allows it", "transfer_consent_required")
    fmt = sniff_type(data)
    counted = check_caps(data, fmt, filename)
    from clefs import files

    ref = files.put_file(cur, None, data, filename=filename or f"statement.{fmt}", content_type=CONTENT_TYPES[fmt],
                         purpose=FILE_PURPOSE, source="upload", retention_days=RETENTION.days)
    batch = _ingest(cur, ref.id, ota, guess=None if ota else _detect(data, filename, fmt))
    ota = ota or normalize_ota(batch.get("ota_code"))
    ota = ota if ota in PUBLIC_OTAS else None
    now = clock.now()
    report = run_sandbox(batch.pop("_rows"), ota=ota, currency=batch.get("currency") or "AED", now=now)
    report["meta"] = {"file_format": fmt, "counted": counted, "gate_status": batch.get("gate_status"),
                      "mapping_source": batch.get("mapping_source"), "source_code": batch.get("source_code")}
    report["consents"] = {
        k: {"granted": granted, "at": now.isoformat(), "version": UPLOAD_CONSENT_VERSION,
            "text": UPLOAD_CONSENT_TEXTS[k]}
        for k, granted in (("processing", True), ("transfer_abroad", bool(consent_transfer)))
    }
    token = secrets.token_urlsafe(24)
    cur.execute(
        """
        INSERT INTO pfg_lead_audits (token_hash, created_at, expires_at, ip_hash, file_id, batch_id, ota_code,
            status, report, consent_processing, consent_transfer_abroad)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'READY', %s, TRUE, %s) RETURNING *
        """,
        (token_hash(token), now, now + RETENTION, ip_h, ref.id, batch["id"], ota, db.Json(report),
         bool(consent_transfer)),
    )
    return token, dict(cur.fetchone())


def get_row(cur: Any, token: str, *, for_update: bool = False) -> dict:
    """The live audit behind a token; 404 not_found when unknown or expired."""
    cur.execute("SELECT * FROM pfg_lead_audits WHERE token_hash = %s AND expires_at > %s"
                + (" FOR UPDATE" if for_update else ""), (token_hash(token or ""), clock.now()))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "This audit does not exist or has expired", "not_found")
    return dict(row)


def view(cur: Any, row: dict, token: str | None = None) -> dict:
    """The public shape. Locked: the teaser only. Unlocked: the full
    breakdown and the AI snapshot. Never the contact details, never the
    index sample, never a guest name (masked at staging time)."""
    rep = current_report(row.get("report") or {})
    unlocked = row.get("unlocked_at") is not None
    out = {
        "status": row["status"], "created_at": row["created_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(), "unlocked": unlocked, "teaser": rep.get("teaser"),
        "full": rep.get("full") if unlocked else None,
        "ai_snapshot": snapshot_view(cur, rep.get("ai_snapshot_batch_id")) if unlocked else None,
        "lead": {"name": row.get("lead_name"), "hotel_name": row.get("hotel_name")} if unlocked else None,
    }
    if token:
        out["token"] = token
    return out

# ─── Lead unlock ───

# The two consents are separate ticks with their own wording (PDPL: one
# purpose per consent). The wording version is stored with the grant; the
# consents table needs a hotel, which a lead does not have yet.
CONSENT_VERSION = "2026-09-29"
CONSENT_TEXTS = {
    "contact": "Clefs may contact me by the email or WhatsApp number I gave about this audit and Clefs Profit "
               "Guard. I can ask Clefs to stop at any time.",
    "index": "Clefs may include this statement's results, anonymised (no hotel name, no guest data), in the UAE "
             "OTA Leakage Index.",
    # Required with the contact tick (review compliance-ota-audit-no-
    # transfer-consent): the lead's contacts go to Neon (Singapore) and,
    # in the founder alert, to the email provider (United States).
    "lead_transfer_abroad": "My name, email and WhatsApp number are processed outside the UAE by Clefs' "
                            "sub-processors: stored on Clefs' database in Singapore and sent to the Clefs team "
                            "through its email provider in the United States.",
}


class LeadIn(BaseModel):
    # Optional at the schema level and checked by hand, so a missing field
    # is the contract's 422 {detail, code} rather than FastAPI's list error.
    name: str | None = None
    hotel_name: str | None = None
    email: str | None = None
    whatsapp: str | None = None
    consent_contact: bool = False
    consent_transfer_abroad: bool = False
    consent_index: bool = False
    city: str | None = None
    area: str | None = None
    website: str | None = None
    star: int | None = None


def _invalid(detail: str, code: str = "invalid_input") -> ClefsError:
    return ClefsError(422, detail, code)


def _text(v: str | None, *, field: str, lo: int, hi: int) -> str:
    t = " ".join((v or "").split())
    if not lo <= len(t) <= hi:
        raise _invalid(f"{field} must be {lo}-{hi} characters")
    return t


def _opt_text(v: str | None, hi: int) -> str | None:
    t = " ".join((v or "").split())[:hi]
    return t or None


def validate_lead(body: LeadIn) -> dict:
    from clefs.core.phone import normalize_phone

    if not body.consent_contact:
        raise _invalid("Tick the box that lets Clefs contact you about the audit", "consent_required")
    if not body.consent_transfer_abroad:
        raise _invalid("Tick the box that lets Clefs' sub-processors outside the UAE handle your contact details",
                       "transfer_consent_required")
    name = _text(body.name, field="name", lo=1, hi=120)
    hotel = _text(body.hotel_name, field="hotel_name", lo=2, hi=160)
    email = (body.email or "").strip().lower()[:200] or None
    wa = (body.whatsapp or "").strip()[:40] or None
    if not email and not wa:
        raise _invalid("An email or a WhatsApp number is required")
    if email and ("@" not in email or "." not in email.split("@")[-1] or " " in email):
        raise _invalid("That email address does not look right")
    if wa:
        try:
            wa = normalize_phone(wa)  # E.164 only: a national number is ambiguous without a country
        except ClefsError as exc:
            raise _invalid("Enter the WhatsApp number with its country code, e.g. +971501234567") from exc
    star = body.star if body.star in (1, 2, 3, 4, 5) else None
    return {"name": name, "hotel": hotel, "email": email, "whatsapp": wa, "consent_index": bool(body.consent_index),
            "city": _opt_text(body.city, 80) or DEFAULTS["city"], "area": _opt_text(body.area, 80),
            "website": _opt_text(body.website, 200), "star": star}


def unlock(cur: Any, token: str, lead: dict) -> dict:
    """Store the lead and its consents and unlock the report. The first
    unlock also starts the AI snapshot and notifies the founder; the index
    sample follows the index consent every time (a withdrawn consent
    removes the sample while the audit still exists)."""
    row = get_row(cur, token, for_update=True)
    if row["status"] != "READY":
        raise _invalid("This audit has no report to unlock", "not_ready")
    now = clock.now()
    first = row.get("unlocked_at") is None
    # Saved back below, so an old report leaves under the public classes for
    # good, and the founder alert and the index sample read the same figures.
    report = dict(current_report(row.get("report") or {}))
    consents = dict(report.get("consents") or {})
    consents["contact"] = {"granted": True, "at": now.isoformat(), "version": CONSENT_VERSION,
                           "text": CONSENT_TEXTS["contact"]}
    consents["index"] = {"granted": lead["consent_index"], "at": now.isoformat(), "version": CONSENT_VERSION,
                         "text": CONSENT_TEXTS["index"]}
    consents["lead_transfer_abroad"] = {"granted": True, "at": now.isoformat(), "version": CONSENT_VERSION,
                                        "text": CONSENT_TEXTS["lead_transfer_abroad"]}
    report["consents"] = consents
    report["lead_context"] = {"city": lead["city"], "area": lead["area"], "star": lead["star"]}
    if first:
        report["ai_snapshot_batch_id"] = start_snapshot(cur, row, lead)
    cur.execute(
        """
        UPDATE pfg_lead_audits SET lead_name = %s, hotel_name = %s, email = NULL, whatsapp_e164 = NULL,
               email_enc = %s, whatsapp_enc = %s, consent_contact = TRUE, consent_lead_transfer_abroad = TRUE,
               consent_index = %s, unlocked_at = COALESCE(unlocked_at, %s), report = %s
         WHERE id = %s RETURNING *
        """,
        # Sealed like vis_public_checks and dr_leads (QA PUB-8).
        (lead["name"], lead["hotel"], privacy.seal(lead["email"]) if lead["email"] else None,
         privacy.seal(lead["whatsapp"]) if lead["whatsapp"] else None, lead["consent_index"], now,
         db.Json(report), row["id"]),
    )
    row = dict(cur.fetchone())
    if lead["consent_index"]:
        band = f"{lead['star']}-star" if lead["star"] else None
        leak_index.record_sample(cur, row, report, emirate=emirate_of(lead["city"]), property_band=band)
    else:
        cur.execute("DELETE FROM pfg_index_samples WHERE audit_id = %s", (row["id"],))
    if first:
        notify_founder(cur, row, report, contacts={"email": lead["email"], "whatsapp": lead["whatsapp"]})
    return row

EMIRATE_BY_CITY = {"dubai": "DXB", "abu dhabi": "AUH", "sharjah": "SHJ", "ajman": "AJM", "ras al khaimah": "RAK",
                   "rak": "RAK", "fujairah": "FUJ", "umm al quwain": "UAQ", "al ain": "AUH"}


def emirate_of(city: str | None) -> str | None:
    """The index's emirate from the lead's city; None (the sample keeps the
    audit's default) for anything unrecognised."""
    return EMIRATE_BY_CITY.get(" ".join((city or "").lower().replace("-", " ").split()))


# ─── Founder notification ───


def lead_contacts(row: dict) -> dict[str, str | None]:
    """The lead's email and WhatsApp, unsealed (legacy rows: plaintext)."""
    return {"email": privacy.unseal(row.get("email_enc")) or row.get("email"),
            "whatsapp": privacy.unseal(row.get("whatsapp_enc")) or row.get("whatsapp_e164")}


def notify_founder(cur: Any, row: dict, report: dict, *, contacts: dict | None = None) -> dict:
    """Email and WhatsApp to the founder on the first unlock (02 spec 3.11).
    The email carries the lead's contact details (the contact consent is
    required to unlock); WhatsApp gets a template with the hotel, the figure
    with the lines to confirm, and the findings count only. A channel with no
    address configured is skipped."""
    # FOUNDATION-REQUEST: model FOUNDER_NOTIFY_EMAIL, FOUNDER_NOTIFY_WHATSAPP and
    # PG_AUDIT_LEAD_WA_TEMPLATE in Settings and list them in .env.example
    # (read through env() until then, as visibility's google check does).
    from clefs import notify

    t = report.get("teaser") or {}
    ccy = t.get("currency") or "AED"
    found = t.get("found") or {}
    low, high = (found.get("low") or {}).get("amount"), (found.get("high") or {}).get("amount")
    # One figure under the public classes (low == high), and the lines to
    # confirm as a count, never an amount, for the founder's review call.
    span = f"{ccy} {low}" if low == high else f"{ccy} {low} to {high}"
    # WhatsApp is the founder's fastest channel, and an audit whose lines are
    # all to confirm reads "AED 0.00" with 0 findings there: a live lead that
    # looks empty. The approved template keeps its 3 params, so the count
    # rides in the second one.
    n_confirm = int(t.get("to_confirm") or 0)
    wa_found = f"{span}, {n_confirm} {'line' if n_confirm == 1 else 'lines'} to confirm" if n_confirm else span
    sent: dict[str, Any] = {}
    c = contacts or lead_contacts(row)
    email_to = env("FOUNDER_NOTIFY_EMAIL", None)
    if email_to:
        e = html.escape
        body = (f"<p><b>{e(row.get('lead_name') or '')}</b> of <b>{e(row.get('hotel_name') or '')}</b> unlocked a "
                f"free OTA audit ({e(t.get('ota_code') or 'OTA unknown')}, {t.get('lines')} lines).</p>"
                f"<p>Found: {e(span)}; {t.get('findings')} findings; {t.get('to_confirm') or 0} lines to confirm "
                f"with the hotel; {t.get('open_deadlines')} dispute windows still open.</p>"
                f"<p>Email: {e(c.get('email') or '-')}<br>WhatsApp: "
                f"{e(c.get('whatsapp') or '-')}<br>Index consent: "
                f"{'yes' if row.get('consent_index') else 'no'}</p>")
        res = notify.send_email(email_to, f"OTA audit lead: {row.get('hotel_name')}", body, hotel_slug=None,
                                purpose=NOTIFY_PURPOSE, category="utility",
                                idempotency_key=f"{NOTIFY_PURPOSE}:email:{row['id']}", cur=cur)
        sent["email"] = res.status
    wa_to = env("FOUNDER_NOTIFY_WHATSAPP", None)
    if wa_to:
        res = notify.send_whatsapp_template(
            wa_to, env("PG_AUDIT_LEAD_WA_TEMPLATE", "clefs_audit_lead"), "en",
            [row.get("hotel_name") or "a hotel", wa_found, str(t.get("findings") or 0)], hotel_slug=None,
            purpose=NOTIFY_PURPOSE, sender="platform", category="utility",
            idempotency_key=f"{NOTIFY_PURPOSE}:wa:{row['id']}", cur=cur)
        sent["whatsapp"] = res.status
    if sent:
        cur.execute("UPDATE pfg_lead_audits SET notified_founder_at = %s WHERE id = %s", (clock.now(), row["id"]))
    return sent


# ─── AI snapshot (visibility.api, stub-safe) ───


def start_snapshot(cur: Any, row: dict, lead: dict) -> int | None:
    """Queue the small AI snapshot (5 prompts x engines x 3 runs) about the
    lead's hotel. None, and no AI section, when Visibility is off, capped,
    out of budget or failing: the audit never depends on it."""
    try:
        from clefs.modules.visibility import api as visibility_api

        subject = visibility_api.SnapshotSubject(hotel_name=lead["hotel"], city=lead["city"], area=lead["area"],
                                                 website=lead["website"], star=lead["star"])
        cur.execute("SAVEPOINT pg_audit_snapshot")
        try:
            got = visibility_api.start_ai_snapshot(cur, subject=subject, requester_ref=f"pg.audit:{row['id']}")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT pg_audit_snapshot")
            raise
        cur.execute("RELEASE SAVEPOINT pg_audit_snapshot")
        return int(got) if got is not None else None
    except Exception as exc:  # noqa: BLE001  (another lane's feature must never break the unlock)
        log.warning("public audit %s: AI snapshot not started: %s", row.get("id"), exc)
        return None


def snapshot_view(cur: Any, batch_id: Any) -> dict:
    """{status: unavailable} without a snapshot, else get_ai_snapshot's
    answer ({status, per_engine, top_cited_domains, answers_about_you,
    label}); a purged or unknown batch reads as unavailable."""
    if batch_id is None:
        return {"status": "unavailable"}
    try:
        from clefs.modules.visibility import api as visibility_api

        cur.execute("SAVEPOINT pg_audit_snapshot_read")
        try:
            got = visibility_api.get_ai_snapshot(cur, int(batch_id))
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT pg_audit_snapshot_read")
            raise
        cur.execute("RELEASE SAVEPOINT pg_audit_snapshot_read")
    except Exception as exc:  # noqa: BLE001
        log.warning("public audit: AI snapshot %s unreadable: %s", batch_id, exc)
        return {"status": "unavailable"}
    return dict(got) if got else {"status": "unavailable"}

# ─── Routes ───

TRUTHY = ("true", "1", "on", "yes")


def _capped(request: Request, limit: int) -> Request:
    """The same request with a receive() that stops once the body passes
    `limit` bytes (a chunked upload has no Content-Length to check)."""
    received = 0

    async def receive() -> dict:
        nonlocal received
        message = await request.receive()
        if message.get("type") == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                raise _too_large()
        return message

    return Request(request.scope, receive)


async def _read_capped(file: UploadFile) -> bytes:
    chunks, size = [], 0
    while True:
        chunk = await file.read(256 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _rate_limit(ip_h: str) -> None:
    """3 audits per IP hash per day, then 50 per day in total. Checked
    before the body is read, so a flood costs a counter, not a parse. A
    refused attempt is not recorded (clefs.ratelimit)."""
    from clefs import ratelimit

    with db.db() as cur:
        if not ratelimit.hit(cur, f"pg:audit:ip:{ip_h[:32]}", limit=IP_DAILY_LIMIT, window_s=DAY_S):
            raise ClefsError(429, f"You have run {IP_DAILY_LIMIT} audits today; please try again tomorrow",
                             "rate_limited")
        if not ratelimit.hit(cur, "pg:audit:global", limit=GLOBAL_DAILY_LIMIT, window_s=DAY_S):
            raise ClefsError(429, "Today's free audits are used up; please try again tomorrow", "rate_limited")


def _create(data: bytes, filename: str | None, ota: str | None, ip_h: str, consent_transfer: bool = False) -> dict:
    with db.db() as cur:
        token, row = create_audit(cur, data, filename=filename, ota_code=ota, ip_h=ip_h,
                                  consent_transfer=consent_transfer)
        return view(cur, row, token)


@router.post("/audits", status_code=201)
async def upload_audit(request: Request) -> dict:
    """Multipart: `file` (required), `consent_processing` ("true"
    required), `consent_transfer_abroad` ("true" required for every
    format: Clefs' sub-processors outside the UAE store and read it),
    `ota_code` (BOOKING | AGODA | EXPEDIA, optional)."""
    limit = MAX_BYTES + UPLOAD_OVERHEAD
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > limit:
                raise _too_large()
        except ValueError as exc:
            raise ClefsError(400, "Bad Content-Length", "bad_request") from exc
    if not (request.headers.get("content-type") or "").lower().startswith("multipart/form-data"):
        raise ClefsError(400, "Send multipart/form-data with the statement as `file`", "bad_request")
    ip_h = ip_hash(_client_ip())
    await run_in_threadpool(_rate_limit, ip_h)
    form = await _capped(request, limit).form(max_files=1, max_fields=5)
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise ClefsError(422, "Attach the statement as `file`", "file_required")
        consent = form.get("consent_processing")
        if not isinstance(consent, str) or consent.strip().lower() not in TRUTHY:
            raise ClefsError(422, "Tick the box that lets Clefs process the statement", "consent_required")
        transfer = form.get("consent_transfer_abroad")
        transfer_ok = isinstance(transfer, str) and transfer.strip().lower() in TRUTHY
        ota = form.get("ota_code")
        data = await _read_capped(file)
        return await run_in_threadpool(_create, data, file.filename, ota if isinstance(ota, str) and ota else None,
                                       ip_h, transfer_ok)
    finally:
        await form.close()


@router.get("/audits/{token}")
def get_audit(token: str) -> dict:
    with db.db() as cur:
        return view(cur, get_row(cur, token))


@router.post("/audits/{token}/lead")
def post_lead(token: str, body: LeadIn) -> dict:
    lead = validate_lead(body)
    with db.db() as cur:
        return view(cur, unlock(cur, token, lead))


@router.get("/leakage-index")
def get_leakage_index() -> dict:
    with db.db() as cur:
        return leak_index.aggregate(cur)

# ─── Purge job ───


def _in_use(cur: Any, column: str, value: Any) -> bool:
    """Another live audit of the same bytes still needs the file or batch
    (core dedupes identical uploads onto one file and one batch)."""
    cur.execute(f"SELECT 1 FROM pfg_lead_audits WHERE {column} = %s LIMIT 1", (value,))
    return cur.fetchone() is not None


def _quietly(cur: Any, name: str, fn: Any, *args: Any) -> bool:
    """Run one deletion in a savepoint; a row core already swept (the
    ingest sweep deletes sandbox batches at 30 days too) is not an error."""
    cur.execute(f"SAVEPOINT {name}")
    try:
        fn(*args)
    except ClefsError:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        return False
    cur.execute(f"RELEASE SAVEPOINT {name}")
    return True


def purge_expired(cur: Any, *, now: datetime | None = None, limit: int = 500) -> dict:
    """Delete audits past expires_at (30 days) with their file, staged
    batch and AI snapshot. Index samples stay, anonymised: their audit_id
    is set NULL by the foreign key."""
    from clefs import files
    from clefs.core.ingest import discard_batch

    now = now or clock.now()
    cur.execute("SELECT id, file_id, batch_id, report FROM pfg_lead_audits WHERE expires_at <= %s "
                "ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED", (now, limit))
    rows = [dict(r) for r in cur.fetchall()]
    out = {"audits": 0, "files": 0, "batches": 0, "snapshots": 0}
    for r in rows:
        snap = (r.get("report") or {}).get("ai_snapshot_batch_id")
        if snap is not None:
            out["snapshots"] += _purge_snapshot(cur, snap)
        cur.execute("DELETE FROM pfg_lead_audits WHERE id = %s", (r["id"],))
        out["audits"] += 1
        if r["batch_id"] is not None and not _in_use(cur, "batch_id", r["batch_id"]):
            out["batches"] += int(_quietly(cur, "pg_audit_purge_batch", discard_batch, cur, None, r["batch_id"]))
        if r["file_id"] is not None and not _in_use(cur, "file_id", r["file_id"]):
            out["files"] += int(_quietly(cur, "pg_audit_purge_file", files.delete_file, cur, None, str(r["file_id"])))
    out["sealed"] = seal_legacy_contacts(cur, limit=limit)
    return out


def seal_legacy_contacts(cur: Any, *, limit: int = 500) -> int:
    """Rows unlocked before 0303 hold plaintext contacts: seal them into
    email_enc / whatsapp_enc and clear the plaintext (QA PUB-8). The daily
    purge job runs it, so within a day no plaintext contact remains."""
    cur.execute("SELECT id, email, whatsapp_e164 FROM pfg_lead_audits WHERE email IS NOT NULL "
                "OR whatsapp_e164 IS NOT NULL ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        cur.execute(
            "UPDATE pfg_lead_audits SET email_enc = COALESCE(email_enc, %s), whatsapp_enc = COALESCE(whatsapp_enc, %s), "
            "email = NULL, whatsapp_e164 = NULL WHERE id = %s",
            (privacy.seal(r["email"]) if r["email"] else None,
             privacy.seal(r["whatsapp_e164"]) if r["whatsapp_e164"] else None, r["id"]))
    return len(rows)


def _purge_snapshot(cur: Any, batch_id: Any) -> int:
    """visibility.api.purge_ai_snapshot, stub-safe (the stub returns 0)."""
    try:
        from clefs.modules.visibility import api as visibility_api

        cur.execute("SAVEPOINT pg_audit_purge_snap")
        try:
            n = int(visibility_api.purge_ai_snapshot(cur, int(batch_id)) or 0)
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT pg_audit_purge_snap")
            raise
        cur.execute("RELEASE SAVEPOINT pg_audit_purge_snap")
        return 1 if n else 0
    except Exception as exc:  # noqa: BLE001  (a failing snapshot purge must not keep the lead row alive)
        log.warning("public audit purge: snapshot %s not purged: %s", batch_id, exc)
        return 0


@jobs.scheduled(PURGE_JOB, jobs.daily_at("03:35", tz="utc"), per_hotel=False)
def purge_job(ctx: jobs.JobCtx) -> dict:
    return purge_expired(ctx.cur)
