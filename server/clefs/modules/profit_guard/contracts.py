"""
OTA contract terms: defaults, date-effective lookup, AI extraction with
clause citations and human confirmation, and the expected commission of a
stay (02 spec 3.1-3.2 with Canon K2).

Why defaults exist at all: most hotels never upload a contract, yet
Inquiry Guard and Finance need an expected commission. The defaults are the
public-audit assumptions (02 spec 3.11: Booking 15%, Agoda 18%, Expedia
18%) and every result says `basis: "default"`, so nothing downstream can
mistake them for the hotel's own terms. Only CONFIRMED rows are ever read as
the hotel's terms: an AI extraction is a DRAFT until a person confirms it.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from clefs import ai, db, files
from clefs.audit import audit
from clefs.core import clock
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.profit_guard import repo
from clefs.modules.profit_guard.models import (
    BASE_RULES,
    COLLECT_MODELS,
    DISPUTE_RULE_KINDS,
    CanonicalContractTerms,
    normalize_ota,
)

log = logging.getLogger("clefs.profit_guard")

AI_TASK = "pg.contract_extract"
MAX_PAGES = 40
MAX_CHARS = 150_000

# ─── Default terms (02 spec 3.11; Canon OTA rules and K2) ───

GDT = "GDT v2601 2.3.2(ii)"
DEFAULT_TERMS: dict[str, dict[str, Any]] = {
    "BOOKING": {
        "base_commission_pct": Decimal("15"),
        "commission_base_rule": "ROOM_INCL_VAT",
        "excluded_charge_types": ["MUNICIPALITYFEE", "TOURISMFEE"],
        "collect_models": ["HOTEL_COLLECT", "OTA_COLLECT"],
        "dispute_rules": [{"kind": "HOURS_AFTER_CHECKOUT", "hours": 48, "applies_to": "no_show_or_change"}],
        "vcc_rules": {"charge_within_days_after_checkout": 365},
        "clauses": [{"ref": GDT, "field": "commission_base_rule", "page": None,
                     "text": "Commission is due on the room price including service charge and VAT; municipality "
                             "fee and Tourism Dirham are excluded when configured as charges (Canon K2)."}],
    },
    "AGODA": {
        "base_commission_pct": Decimal("18"),
        "commission_base_rule": "RSR_TAX_INCL",
        # The Tourism Dirham is collected at the property, outside the RSR.
        "excluded_charge_types": ["TOURISMFEE"],
        "collect_models": ["OTA_COLLECT", "HOTEL_COLLECT"],
        "dispute_rules": [{"kind": "BUSINESS_DAYS_AFTER_INVOICE", "days": 14}],
        "vcc_rules": {"upc_charge_within_h": 24},
        "clauses": [{"ref": "Canon OTA rules", "field": "commission_base_rule", "page": None,
                     "text": "Agoda charges commission on the tax-inclusive reference sell rate."}],
    },
    "EXPEDIA": {
        "base_commission_pct": Decimal("18"),
        "commission_base_rule": "ROOM_EXCL_TAX",
        "excluded_charge_types": [],
        "collect_models": ["HOTEL_COLLECT", "OTA_COLLECT"],
        "dispute_rules": [{"kind": "DAY_OF_NEXT_MONTH", "day": 4, "applies_to": "hotel_collect"}],
        "vcc_rules": {"expires_days_after_checkout": 180},
        "clauses": [{"ref": "Canon OTA rules", "field": "vcc_rules", "page": None,
                     "text": "Expedia EVC expires 180 days after check-out; Hotel Collect bookings are reconciled "
                             "by the 4th of the next month."}],
    },
}


def default_terms(ota_code: str | None) -> dict | None:
    """The default terms of a known OTA as a contract-shaped dict
    (`is_default` True, no id), or None for any other channel."""
    ota = normalize_ota(ota_code)
    base = DEFAULT_TERMS.get(ota or "")
    if base is None:
        return None
    return {"id": None, "ota_code": ota, "status": "DEFAULT", "is_default": True, "effective_from": None,
            "effective_to": None, "entity_name": None, "entity_country": None,
            **json.loads(json.dumps(base, default=str)),
            "base_commission_pct": base["base_commission_pct"]}


# ─── Lookup ───


def list_contracts(cur: Any, hotel_slug: str, *, ota_code: str | None = None, status: str | None = None) -> list[dict]:
    cur.execute(
        """
        SELECT * FROM pfg_ota_contracts WHERE hotel_slug = %s AND (%s::text IS NULL OR ota_code = %s)
           AND (%s::text IS NULL OR status = %s) ORDER BY ota_code, effective_from DESC, id DESC
        """,
        (hotel_slug, ota_code, ota_code, status, status),
    )
    return [{**dict(r), "is_default": False} for r in cur.fetchall()]


def get_contract(cur: Any, hotel_slug: str, contract_id: int, *, for_update: bool = False) -> dict:
    cur.execute("SELECT * FROM pfg_ota_contracts WHERE hotel_slug = %s AND id = %s"
                + (" FOR UPDATE" if for_update else ""), (hotel_slug, contract_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Contract not found", "contract_not_found")
    return {**dict(row), "is_default": False}


def contract_on(cur: Any, hotel_slug: str, ota_code: str, on: date) -> dict | None:
    """The confirmed contract whose window holds `on` (the newest start
    wins), else the OTA's default terms, else None."""
    ota = normalize_ota(ota_code)
    cur.execute(
        """
        SELECT * FROM pfg_ota_contracts WHERE hotel_slug = %s AND ota_code = %s AND status = 'CONFIRMED'
           AND effective_from <= %s AND (effective_to IS NULL OR effective_to >= %s)
         ORDER BY effective_from DESC, id DESC LIMIT 1
        """,
        (hotel_slug, ota, on, on),
    )
    row = cur.fetchone()
    return {**dict(row), "is_default": False} if row is not None else default_terms(ota)


# ─── Commission base (02 spec 3.2, Canon K2) ───

# Which breakdown component each OTA charge type names. Booking's
# price_breakdown calls the municipality fee MUNICIPALITYFEE and the Tourism
# Dirham TOURISMFEE; Abu Dhabi's tourism fee and a destination fee are the
# same kind of per-stay levy, so they follow TOURISMFEE.
CHARGE_COMPONENTS: dict[str, tuple[str, ...]] = {"MUNICIPALITYFEE": ("mf",), "TOURISMFEE": ("td", "tf", "dest")}
# The channel_config flag that must be true before a charge type may leave
# the base (a fee the property did not configure as a charge is inside the
# price the OTA commissions).
CHARGE_FLAG = {"MUNICIPALITYFEE": "mf_configured_as_charge", "TOURISMFEE": "td_configured_as_charge"}
COMPONENTS = ("room", "sc", "mf", "tf", "dest", "vat", "td")


def _part(breakdown: Any, key: str) -> int | None:
    v = breakdown.get(key) if isinstance(breakdown, dict) else getattr(breakdown, key, None)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def breakdown_parts(breakdown: Any) -> dict[str, int] | None:
    """{component: minor} from a core.tax StayTaxBreakdown (or its as_dict()),
    None when it carries no room component (nothing to base a commission on)."""
    if breakdown is None:
        return None
    room = _part(breakdown, "room")
    if room is None:
        return None
    return {k: (_part(breakdown, k) or 0) for k in COMPONENTS}


def commission_base(parts: dict[str, int], terms: dict, cfg: dict) -> tuple[int, list[str], list[dict]]:
    """(base_minor, components in the base, calc steps). The base starts
    from the rule's components; a charge type the contract excludes leaves
    only while the property configured it as a charge on that date."""
    rule = terms.get("commission_base_rule") or "ROOM_INCL_VAT"
    if rule in ("ROOM_EXCL_TAX", "NET_RATE"):
        included = ["room"]
    elif rule == "RSR_TAX_INCL":
        included = ["room", "sc", "mf", "tf", "dest", "vat", "td"]
    else:  # ROOM_INCL_VAT: room + service charge + VAT, levies too until excluded (Canon K2)
        included = ["room", "sc", "mf", "tf", "dest", "vat", "td"]
    excluded: list[str] = []
    for ctype in terms.get("excluded_charge_types") or []:
        comps = CHARGE_COMPONENTS.get(str(ctype).upper())
        flag = CHARGE_FLAG.get(str(ctype).upper())
        if comps and flag and cfg.get(flag):
            excluded += [c for c in comps if c in included]
    if rule == "ROOM_INCL_VAT":
        # Booking's rule names room, SC and VAT; a levy stays in only when it
        # was not configured as a charge (it is then part of the room price).
        for comp, flag in (("mf", "mf_configured_as_charge"), ("tf", "td_configured_as_charge"),
                           ("dest", "td_configured_as_charge"), ("td", "td_configured_as_charge")):
            if cfg.get(flag) and comp not in excluded:
                excluded.append(comp)
    base_parts = [c for c in included if c not in excluded and parts.get(c)]
    base = sum(parts[c] for c in base_parts)
    steps = [{"label": "Commission base", "formula": " + ".join(base_parts) or "0",
              "inputs": {c: parts[c] for c in base_parts} | {"rule": rule, "excluded": excluded},
              "result_minor": base}]
    return base, base_parts, steps


def commission_pct(cur: Any, hotel_slug: str, terms: dict, on: date, *, booker_country: str | None = None,
                   rate_plan: str | None = None) -> tuple[Decimal, list[dict]]:
    """Contract percent plus the commission uplifts in force for the stay
    (Preferred, Visibility Booster, ...). Returns (pct, uplift programs)."""
    pct = Decimal(str(terms["base_commission_pct"]))
    uplifts = [p for p in repo.active_programs(cur, hotel_slug, terms["ota_code"], on, booker_country=booker_country,
                                                rate_plan=rate_plan)
               if p["kind"] == "COMMISSION_UPLIFT" and p["pct"] is not None]
    return pct + sum((Decimal(str(p["pct"])) for p in uplifts), Decimal("0")), uplifts


def expected_commission(cur: Any, hotel_slug: str, *, ota_code: str, breakdown: Any, on: date,
                        booker_country: str | None = None, rate_plan: str | None = None) -> dict | None:
    """{commission_minor, base_minor, pct, currency, basis, contract_id,
    base_rule, base_components, uplifts, calc} or None when the channel has
    neither a confirmed contract nor default terms, or the breakdown has no
    room component. Rounds once, half up, to minor units."""
    parts = breakdown_parts(breakdown)
    if parts is None:
        return None
    terms = contract_on(cur, hotel_slug, ota_code, on)
    if terms is None:
        return None
    cfg = repo.channel_config_on(cur, hotel_slug, terms["ota_code"], on)
    base, comps, steps = commission_base(parts, terms, cfg)
    pct, uplifts = commission_pct(cur, hotel_slug, terms, on, booker_country=booker_country, rate_plan=rate_plan)
    commission = int((Decimal(base) * pct / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    currency = (breakdown.get("currency") if isinstance(breakdown, dict) else getattr(breakdown, "currency", None)) \
        or get_hotel(cur, hotel_slug).currency
    steps.append({"label": "Commission", "formula": "base x pct / 100",
                  "inputs": {"base_minor": base, "pct": str(pct),
                             "uplifts": [{"id": p["id"], "program": p["program"], "pct": str(p["pct"])}
                                         for p in uplifts]},
                  "result_minor": commission})
    return {
        "commission_minor": commission, "base_minor": base, "pct": pct, "currency": currency,
        "basis": "default" if terms.get("is_default") else "contract", "contract_id": terms.get("id"),
        "base_rule": terms["commission_base_rule"], "base_components": comps,
        "uplifts": [p["id"] for p in uplifts], "calc": steps,
    }


# ─── Extraction (clefs.ai.structured, then a person confirms) ───

SYSTEM = (
    "You read an OTA (online travel agency) contract or terms document for a hotel and extract the commercial "
    "terms. The text of each page is between '--- page N ---' markers. Copy values only from the document: "
    "never use general knowledge of what an OTA usually charges. Use '' for a string and 0 for a number the "
    "document does not state, and confidence below 0.5 when key terms are missing or ambiguous.\n"
    "ota_code: BOOKING, AGODA, EXPEDIA or the OTA's name in capitals. base_commission_pct: the percentage as "
    "printed without the % sign ('15'). commission_base_rule: ROOM_INCL_VAT (room price including VAT and "
    "service charge), RSR_TAX_INCL (tax-inclusive sell rate), NET_RATE (merchant or net-rate model) or "
    "ROOM_EXCL_TAX (room revenue before taxes). excluded_charge_types: charge types the document says carry "
    "no commission, as MUNICIPALITYFEE, TOURISMFEE, CITYTAX, VAT, SERVICECHARGE or OTHER. collect_models: "
    "OTA_COLLECT and/or HOTEL_COLLECT. dispute_rules: each deadline for disputing commission or reporting "
    "no-shows, kind HOURS_AFTER_CHECKOUT (hours), BUSINESS_DAYS_AFTER_INVOICE (days) or DAY_OF_NEXT_MONTH "
    "(day), applies_to a few words. vcc_rules: virtual card limits in days or hours. Dates as YYYY-MM-DD. "
    "clauses: one citation per extracted term, with the clause reference as printed (section number, "
    "document name and version), a verbatim excerpt of at most 400 characters, the page number, and field = "
    "the term it supports (base_commission_pct, commission_base_rule, excluded_charge_types, collect_models, "
    "dispute_rules, vcc_rules, effective_from, effective_to, entity_name)."
)
CITED_FIELDS = ("base_commission_pct", "commission_base_rule")


def _pdf_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        if len(reader.pages) > MAX_PAGES:
            raise ClefsError(413, f"At most {MAX_PAGES} pages per contract", "too_many_pages")
        return [(p.extract_text() or "").strip() for p in reader.pages]
    except ClefsError:
        raise
    except Exception as exc:  # pypdf raises assorted errors on odd files
        raise ClefsError(400, "The contract PDF could not be read", "unreadable_file") from exc


def pages_from_file(cur: Any, hotel_slug: str, file_id: str) -> list[str]:
    """Page texts of a stored contract: a PDF's text layer, or a text file
    as one page (a pasted contract)."""
    ref, data = files.get_file(cur, hotel_slug, file_id)
    is_pdf = data[:5] == b"%PDF-" or (ref.content_type or "").endswith("pdf")
    pages = _pdf_pages(data) if is_pdf else [data.decode("utf-8", errors="replace")]
    if sum(len(p) for p in pages) < 40:
        raise ClefsError(422, "This contract has no text layer; type the terms instead", "pdf_no_text")
    return pages


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip()[:10]) if text and text.strip() else None
    except ValueError:
        return None


def _parse_pct(text: str) -> Decimal | None:
    try:
        v = Decimal(str(text).replace("%", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if 0 < v < 1:  # "0.15" is a ratio
        v = v * 100
    return v if 0 <= v <= 100 else None


def normalize_terms(terms: CanonicalContractTerms, *, ota_code: str | None, page_count: int,
                    today: date) -> tuple[dict, list[str]]:
    """The model's reading as contract columns plus review flags: what a
    person must look at before confirming (missing terms, uncited terms,
    citations on pages the document does not have)."""
    flags: list[str] = []
    ota = normalize_ota(ota_code) or normalize_ota(terms.ota_code)
    if not ota:
        raise ClefsError(422, "Say which OTA this contract is with (ota_code)", "ota_required")
    dflt = DEFAULT_TERMS.get(ota, {})
    pct = _parse_pct(terms.base_commission_pct)
    if pct is None:
        flags.append("base_commission_pct_missing")
        pct = dflt.get("base_commission_pct", Decimal("0"))
    rule = terms.commission_base_rule.strip().upper()
    if rule not in BASE_RULES:
        flags.append("commission_base_rule_missing")
        rule = dflt.get("commission_base_rule", "ROOM_INCL_VAT")
    eff_from = _parse_date(terms.effective_from)
    if eff_from is None:
        flags.append("effective_from_missing")
        eff_from = today
    eff_to = _parse_date(terms.effective_to)
    if eff_to is not None and eff_to < eff_from:
        flags.append("effective_to_before_from")
        eff_to = None
    clauses = []
    for c in terms.clauses:
        if not 1 <= c.page <= page_count:
            flags.append(f"citation_page_out_of_range:{c.ref[:40]}")
            continue
        clauses.append({"ref": c.ref.strip()[:200], "text": c.text.strip()[:400], "page": c.page,
                        "field": c.field.strip()})
    cited = {c["field"] for c in clauses}
    flags += [f"uncited:{f}" for f in CITED_FIELDS if f not in cited]
    rules = [{k: v for k, v in r.model_dump().items() if v not in (0, "")} for r in terms.dispute_rules
             if r.kind in DISPUTE_RULE_KINDS]
    vcc = {k: v for k, v in terms.vcc_rules.model_dump().items() if v}
    if terms.confidence < 0.5:
        flags.append("low_confidence")
    cols = {
        "ota_code": ota, "entity_name": terms.entity_name.strip()[:200] or None,
        "entity_country": terms.entity_country.strip().upper()[:2] or None,
        "effective_from": eff_from, "effective_to": eff_to, "base_commission_pct": pct,
        "commission_base_rule": rule,
        "excluded_charge_types": sorted({t.strip().upper() for t in terms.excluded_charge_types if t.strip()}),
        "collect_models": [m for m in dict.fromkeys(x.strip().upper() for x in terms.collect_models)
                           if m in COLLECT_MODELS],
        "dispute_rules": rules, "vcc_rules": vcc, "clauses": clauses,
    }
    return cols, flags


CONTRACT_COLS = ("ota_code", "entity_name", "entity_country", "effective_from", "effective_to", "base_commission_pct",
                 "commission_base_rule", "excluded_charge_types", "collect_models", "dispute_rules", "vcc_rules",
                 "clauses")
JSON_COLS = ("dispute_rules", "vcc_rules", "clauses")


def _db_values(cols: dict) -> dict:
    return {k: (db.Json(cols[k]) if k in JSON_COLS else cols[k]) for k in CONTRACT_COLS if k in cols}


def extract_contract(cur: Any, hotel_slug: str, *, pages: list[str], ota_code: str | None = None,
                     file_id: str | None = None, batch_id: int | None = None, actor: Any = None) -> dict:
    """Run the model over the page texts and store a DRAFT contract with
    its citations and review flags. AI off -> 409 ai_unavailable (the owner
    types the terms with POST /contracts instead)."""
    text = "\n".join(f"--- page {i} ---\n{p}" for i, p in enumerate(pages, start=1))
    if len(text) > MAX_CHARS:
        raise ClefsError(413, "The contract has too much text to extract", "pdf_too_large")
    payload = {"ota_hint": normalize_ota(ota_code) or "", "text": text}
    try:
        terms = ai.structured(AI_TASK, system=SYSTEM, input=json.dumps(payload, ensure_ascii=False),
                              schema=CanonicalContractTerms, hotel_slug=hotel_slug, max_output_tokens=6000,
                              timeout_s=60.0)
    except (ai.AIDisabled, ai.AIError) as exc:
        raise ClefsError(409, "Contract reading is unavailable; enter the terms by hand", "ai_unavailable") from exc
    hotel = get_hotel(cur, hotel_slug)
    cols, flags = normalize_terms(terms, ota_code=ota_code, page_count=len(pages), today=htime.business_date(hotel))
    vals = _db_values(cols)
    cur.execute(
        f"""
        INSERT INTO pfg_ota_contracts (hotel_slug, {', '.join(vals)}, source_file_id, import_batch_id, extracted_by,
            extraction, status, created_by)
        VALUES (%s, {', '.join(['%s'] * len(vals))}, %s, %s, 'LLM', %s, 'DRAFT', %s) RETURNING *
        """,
        (hotel_slug, *vals.values(), file_id, batch_id,
         db.Json({"terms": terms.model_dump(), "review_flags": flags, "page_count": len(pages)}),
         repo._user_id(actor)),
    )
    row = {**dict(cur.fetchone()), "is_default": False}
    audit(cur, actor, "profit.contract.extract", "pfg_ota_contract", row["id"], hotel_slug=hotel_slug,
          meta={"ota_code": row["ota_code"], "review_flags": flags, "file_id": file_id, "batch_id": batch_id})
    return row


def _close_overlaps(cur: Any, hotel_slug: str, row: dict) -> list[int]:
    """A newly confirmed contract ends the open-ended one it follows (the
    usual renewal); any other overlap with confirmed terms is a conflict a
    person must resolve, because two sets of terms for one night would make
    every expected commission ambiguous."""
    cur.execute(
        """
        SELECT * FROM pfg_ota_contracts WHERE hotel_slug = %s AND ota_code = %s AND status = 'CONFIRMED'
           AND id <> %s AND effective_from <= COALESCE(%s::date, 'infinity'::date)
           AND COALESCE(effective_to, 'infinity'::date) >= %s FOR UPDATE
        """,
        (hotel_slug, row["ota_code"], row["id"], row["effective_to"], row["effective_from"]),
    )
    closed = []
    for other in [dict(r) for r in cur.fetchall()]:
        if other["effective_to"] is None and other["effective_from"] < row["effective_from"]:
            cur.execute("UPDATE pfg_ota_contracts SET effective_to = %s, updated_at = %s WHERE id = %s",
                        (row["effective_from"] - timedelta(days=1), clock.now(), other["id"]))
            closed.append(other["id"])
        else:
            raise ClefsError(409, f"These dates overlap confirmed contract {other['id']}", "contract_overlap")
    return closed


def save_contract(cur: Any, hotel_slug: str, data: dict, *, contract_id: int | None = None,
                  actor: Any = None) -> dict:
    """Create (manual entry) or update a contract from a person's values;
    `confirm` (default true) makes it the hotel's terms from effective_from."""
    confirm = bool(data.get("confirm", True))
    cols = {k: data.get(k) for k in CONTRACT_COLS if k in data}
    cols["ota_code"] = normalize_ota(cols.get("ota_code"))
    cols["excluded_charge_types"] = sorted({str(t).strip().upper() for t in cols.get("excluded_charge_types") or []})
    cols["entity_country"] = (cols.get("entity_country") or "").upper() or None
    if cols.get("effective_to") and cols["effective_to"] < cols["effective_from"]:
        raise ClefsError(400, "effective_to is before effective_from", "invalid_range")
    vals = _db_values(cols)
    now, uid = clock.now(), repo._user_id(actor)
    status_sql = "CONFIRMED" if confirm else "DRAFT"
    if contract_id is None:
        cur.execute(
            f"""
            INSERT INTO pfg_ota_contracts (hotel_slug, {', '.join(vals)}, extracted_by, status, note, created_by,
                confirmed_by, confirmed_at)
            VALUES (%s, {', '.join(['%s'] * len(vals))}, 'MANUAL', %s, %s, %s, %s, %s) RETURNING *
            """,
            (hotel_slug, *vals.values(), status_sql, data.get("note"), uid, uid if confirm else None,
             now if confirm else None),
        )
        before = None
    else:
        before = get_contract(cur, hotel_slug, contract_id, for_update=True)
        if before["status"] == "RETIRED":
            raise ClefsError(409, "A retired contract cannot be edited", "contract_retired")
        sets = ", ".join(f"{k} = %s" for k in vals)
        cur.execute(
            f"""
            UPDATE pfg_ota_contracts SET {sets}, note = COALESCE(%s, note), status = %s,
                confirmed_by = CASE WHEN %s THEN %s ELSE confirmed_by END,
                confirmed_at = CASE WHEN %s THEN %s ELSE confirmed_at END, updated_at = %s
             WHERE id = %s RETURNING *
            """,
            (*vals.values(), data.get("note"), status_sql if confirm else before["status"], confirm, uid, confirm,
             now, now, contract_id),
        )
    row = {**dict(cur.fetchone()), "is_default": False}
    closed = _close_overlaps(cur, hotel_slug, row) if row["status"] == "CONFIRMED" else []
    audit(cur, actor, "profit.contract.confirm" if confirm else "profit.contract.save", "pfg_ota_contract", row["id"],
          hotel_slug=hotel_slug, before=repo.out(before), after=repo.out(row), meta={"closed": closed})
    return row


def retire_contract(cur: Any, hotel_slug: str, contract_id: int, *, actor: Any = None) -> dict:
    before = get_contract(cur, hotel_slug, contract_id, for_update=True)
    cur.execute("UPDATE pfg_ota_contracts SET status = 'RETIRED', updated_at = %s WHERE id = %s RETURNING *",
                (clock.now(), contract_id))
    row = {**dict(cur.fetchone()), "is_default": False}
    audit(cur, actor, "profit.contract.retire", "pfg_ota_contract", contract_id, hotel_slug=hotel_slug,
          before=repo.out(before), after=repo.out(row))
    return row


# ─── Deadlines from terms ───


def vcc_charge_deadline(terms: dict | None, *, expiration_date: date, check_out: date | None,
                        activation_date: date | None) -> tuple[date, str]:
    """(charge_deadline, basis): min(card expiry, the contract's rule), 02
    spec 3.1. Booking: charge within 365 days of check-out; Expedia: the EVC
    expires 180 days after check-out; Agoda UPC: within 24 h of issue."""
    candidates: list[tuple[date, str]] = [(expiration_date, "card_expiry")]
    rules = (terms or {}).get("vcc_rules") or {}
    if check_out is not None:
        for key in ("charge_within_days_after_checkout", "expires_days_after_checkout"):
            days = int(rules.get(key) or 0)
            if days > 0:
                candidates.append((check_out + timedelta(days=days), key))
    hours = int(rules.get("upc_charge_within_h") or 0)
    if hours > 0 and activation_date is not None:
        candidates.append((activation_date + timedelta(days=max(1, -(-hours // 24))), "upc_charge_within_h"))
    return min(candidates, key=lambda c: c[0])
