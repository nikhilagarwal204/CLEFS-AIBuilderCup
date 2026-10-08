"""Batch kinds, the canonical row schema of each kind, and the commit-handler
registry (04 spec D4).

Why the schema lives here and not in the alias YAML: several sources feed
one kind (Booking, Agoda and Expedia statements all become OTA_STATEMENT
rows), and the consumer (Profit Guard, Finance, core/stays) must see one
shape whatever the source. The YAML files only say which header means which
canonical field.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("clefs.ingest")

# ─── Kinds ───

# Mirrors the CHECK constraint of import_batches.kind (0020_core_ingest.sql,
# widened by 0024_core_ingest_menu_kind.sql for MENU).
KINDS: tuple[str, ...] = (
    "OTA_STATEMENT", "OTA_PAYOUT", "VCC_REPORT", "CREDIT_NOTE", "PMS_RESERVATIONS", "PMS_FOLIO", "BANK",
    "CONTRACT_PDF", "COMP_RATES", "ISSUANCE", "OCCUPANCY", "ICS", "SUPPLIER_INVOICE", "PL_STATEMENT",
    "TRIAL_BALANCE", "BOOKING_ENGINE_ATTRIBUTION", "MENU",
)
STATUSES = ("UPLOADED", "MAPPING_REQUIRED", "EXTRACTING", "REVIEW", "COMMITTED", "SUPERSEDED", "FAILED")
OPEN_STATUSES = ("UPLOADED", "MAPPING_REQUIRED", "EXTRACTING", "REVIEW")
ROW_STATUSES = ("OK", "WARN", "ERROR", "DUP", "SKIP")

# Field types understood by transforms.coerce.
TYPES = ("text", "date", "datetime", "money", "int", "number", "pct", "currency", "status", "contact", "month")


@dataclass(frozen=True)
class Field:
    name: str
    type: str = "text"
    required: bool = False
    # A contact column: sealed by the commit handler, redacted from
    # import_rows.raw once the batch commits (04 spec D1).
    contact: bool = False


@dataclass(frozen=True)
class KindSchema:
    kind: str
    fields: tuple[Field, ...]
    # The canonical money field whose sum is compared with a printed total.
    total_field: str | None = None
    # Values a "status" field may take, keyed by lowercased raw text.
    status_map: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.required)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    @property
    def contact_fields(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.contact)


def _f(spec: str) -> Field:
    """'name:type!' is required, 'name:contact' is a contact column."""
    name, _, typ = spec.partition(":")
    required = typ.endswith("!")
    typ = typ.rstrip("!") or "text"
    if typ == "contact":
        return Field(name, "contact", required, contact=True)
    return Field(name, typ, required)


def _schema(kind: str, specs: str, *, total: str | None = None, status_map: dict[str, str] | None = None) -> KindSchema:
    return KindSchema(kind, tuple(_f(s) for s in specs.split()), total, status_map or {})


# ─── Canonical schemas ───

# OTA statement line status (02 spec 3.1 ota_statement_lines.line_status).
# Booking's reservation statement says ok / cancelled_by_guest / no_show
# (UNVERIFIED, ota-formats.md 1.1); Agoda and Expedia wording is a guess.
LINE_STATUS_MAP = {
    "ok": "OK", "stayed": "OK", "departed": "OK", "confirmed": "OK", "completed": "OK", "booked": "OK",
    "approved": "OK", "paid": "OK", "pending departure": "OK", "checked out": "OK", "postdeparture": "OK",
    "cancelled": "CANCELLED", "canceled": "CANCELLED", "cancelled by guest": "CANCELLED",
    "cancelled_by_guest": "CANCELLED", "cancelled_by_hotel": "CANCELLED", "cancelled by hotel": "CANCELLED",
    "cancellation": "CANCELLED", "no_show": "NO_SHOW", "no show": "NO_SHOW", "noshow": "NO_SHOW",
    "no-show": "NO_SHOW", "modified": "MODIFIED", "amended": "MODIFIED", "adjustment": "ADJUSTMENT",
    "manual adjustment": "ADJUSTMENT",
}
# PMS reservation status (reservations.status in 0021).
RES_STATUS_MAP = {
    "confirmed": "CONFIRMED", "booked": "CONFIRMED", "reserved": "CONFIRMED", "due in": "CONFIRMED",
    "expected": "CONFIRMED", "guaranteed": "CONFIRMED", "tentative": "CONFIRMED", "new": "CONFIRMED",
    "modified": "CONFIRMED", "ok": "CONFIRMED",
    "in house": "IN_HOUSE", "in_house": "IN_HOUSE", "inhouse": "IN_HOUSE", "checked in": "IN_HOUSE",
    "checked-in": "IN_HOUSE", "arrived": "IN_HOUSE", "due out": "IN_HOUSE",
    "checked out": "CHECKED_OUT", "checked_out": "CHECKED_OUT", "checked-out": "CHECKED_OUT",
    "departed": "CHECKED_OUT", "stayed": "CHECKED_OUT", "completed": "CHECKED_OUT",
    "cancelled": "CANCELLED", "canceled": "CANCELLED", "cancelled_by_guest": "CANCELLED",
    "cancelled_by_hotel": "CANCELLED", "no show": "NO_SHOW", "no_show": "NO_SHOW", "noshow": "NO_SHOW",
    "no-show": "NO_SHOW",
}

SCHEMAS: dict[str, KindSchema] = {
    s.kind: s
    for s in (
        _schema(
            "OTA_STATEMENT",
            "ota_res_id:text! guest_name:text check_in:date! check_out:date room_nights:int rooms:int persons:int "
            "line_status:status booked_at:date cancelled_at:date collect_model:text gross_minor:money! "
            "commissionable_minor:money commission_pct:pct commission_minor:money! currency:currency "
            "booker_country:text rate_plan:text room_type:text programs:text payment_status:text "
            "payment_method:text remarks:text doc_no:text",
            total="commission_minor", status_map=LINE_STATUS_MAP,
        ),
        _schema(
            "OTA_PAYOUT",
            "payout_ref:text payout_date:date ota_res_id:text! guest_name:text check_in:date check_out:date "
            "total_amount_paid_minor:money commissionable_minor:money commission_minor:money charges_minor:money "
            "partner_payout_minor:money! line_kind:text payout_type:text currency:currency "
            "bank_transfer_reference:text event_date:date",
            total="partner_payout_minor",
        ),
        _schema(
            "VCC_REPORT",
            "vcc_id:text! ota_res_id:text! activation_date:date expiration_date:date! initial_minor:money "
            "current_balance_minor:money! currency:currency status:text",
            total="current_balance_minor",
        ),
        _schema(
            "CREDIT_NOTE",
            "doc_no:text related_doc_no:text ota_res_id:text! guest_name:text check_in:date "
            "amount_minor:money! currency:currency reason:text",
            total="amount_minor",
        ),
        _schema(
            "PMS_RESERVATIONS",
            "pms_res_id:text! channel_code:text ota_res_id:text guest_name:text guest_country:text "
            "guest_language:text booked_at:datetime check_in:date! check_out:date! rooms_count:int "
            "room_type_code:text room_number:text rate_plan_code:text status:status cancelled_at:datetime "
            "checked_in_at:datetime checked_out_at:datetime gross_minor:money room_revenue_minor:money "
            "currency:currency segment:text adults:int children:int collect_model:text payment_method:text "
            "promo_code:text guest_email:contact guest_phone:contact",
            status_map=RES_STATUS_MAP,
        ),
        _schema(
            "PMS_FOLIO",
            "pms_txn_id:text! posted_date:date! code:text! amount_minor:money! pms_res_id:text stay_date:date "
            "description:text qty:number currency:currency room_number:text",
            total="amount_minor",
        ),
        _schema(
            "OCCUPANCY",
            "date:date! rooms_occupied:int! rooms_available:int rooms_ooo:int arrivals:int departures:int "
            "room_revenue_minor:money",
        ),
        _schema(
            "ISSUANCE",
            "issued_at:datetime! item:text! qty:number! floor:text shift:text staff_name:text reason:text "
            "location:text request_ref:text",
        ),
        _schema(
            "BANK",
            "value_date:date! amount_minor:money! currency:currency reference:text counterparty:text "
            "description:text",
            total="amount_minor",
        ),
        _schema(
            "COMP_RATES",
            "comp_name:text! stay_date:date! los:int adults:int price_minor:money! currency:currency "
            "room_label:text plan_label:text channel:text captured_at:datetime",
        ),
        _schema(
            "SUPPLIER_INVOICE",
            "invoice_no:text invoice_date:date! supplier:text item:text! qty:number unit_cost_minor:money "
            "amount_minor:money! currency:currency",
            total="amount_minor",
        ),
        _schema(
            "PL_STATEMENT",
            "code:text name:text! group_raw:text type_raw:text section:text period_month:month "
            "amount_minor:money!",
        ),
        _schema(
            "TRIAL_BALANCE",
            "code:text name:text! group_raw:text type_raw:text debit_minor:money credit_minor:money",
        ),
        _schema(
            "BOOKING_ENGINE_ATTRIBUTION",
            "booking_ref:text! utm_source:text utm_medium:text utm_campaign:text utm_term:text utm_content:text "
            "promo_code:text landing_url:text",
        ),
        # An F&B menu (Upsell registers the MENU commit handler). Every
        # field is text on purpose: menu cells carry small languages of their
        # own ("AED 35", "Mon-Fri", "vegan;halal", "yes") that the menu
        # module parses and validates with its own messages, so typing them
        # here would validate twice with different wording. name_en and
        # price are the only fields a row cannot do without.
        _schema(
            "MENU",
            "outlet:text outlet_name:text sku:text category:text category_ar:text name_en:text! name_ar:text "
            "desc_en:text desc_ar:text price:text! available_from:text available_to:text days:text "
            "dietary:text alcohol:text prep_min:text active:text sort:text",
        ),
        # Contract PDFs and ICS files are not tabular: their rows come from
        # AI extraction (contracts) or the calendar parser (ICS).
        _schema("CONTRACT_PDF", "clause_ref:text text:text! page:int"),
        _schema("ICS", "uid:text! summary:text! starts_on:date! ends_on:date location:text description:text"),
    )
}


def schema_for(kind: str) -> KindSchema:
    if kind not in SCHEMAS:
        raise KeyError(f"unknown ingest kind {kind!r}")
    return SCHEMAS[kind]


# ─── Commit handlers (04 spec D4) ───

# fn(cur, batch: dict, rows: list[dict]) -> dict | None. `rows` are the
# canonical dicts of the batch's OK and WARN rows, each with "_row_id" and
# "_row_no" added so a handler can point back at import_rows. Core never
# imports modules (CONTRACTS 1), so modules register here at import time
# (Finance for PL_STATEMENT and TRIAL_BALANCE, Profit Guard for the OTA kinds).
CommitHandler = Callable[[Any, dict, list[dict]], Any]

_lock = threading.Lock()
_handlers: dict[str, tuple[CommitHandler, str]] = {}


def register_commit_handler(kind: str, fn: CommitHandler) -> None:
    """Register the writer for one batch kind. Re-registering the same
    function (a module re-imported by the test app) is a no-op; a different
    function replaces the old one with a log line, because the last import
    of a module is the live one."""
    if kind not in KINDS:
        raise ValueError(f"unknown ingest kind {kind!r}")
    name = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', repr(fn))}"
    with _lock:
        prev = _handlers.get(kind)
        _handlers[kind] = (fn, name)
    if prev is not None and prev[1] != name:
        log.info("ingest: commit handler for %s replaced (%s -> %s)", kind, prev[1], name)


def unregister_commit_handler(kind: str) -> None:
    """Tests only."""
    with _lock:
        _handlers.pop(kind, None)


# ─── Post-commit hooks ───
# Modules that react to a committed batch (Profit Guard's detect-on-commit
# and auto-verify) register a hook instead of wrapping the handler. Wrapping
# was fragile: anything that re-registered the plain handler (a module
# re-import, a test) silently dropped the wrappers. Hooks live beside the
# handler, run after it returns inside the committing transaction (so a
# rolled-back commit leaves nothing behind), and never run on dry runs.
PostCommitHook = Callable[[Any, dict, Any], Any]
_post_hooks: dict[str, dict[str, PostCommitHook]] = {}


def register_post_commit_hook(kind: str, fn: PostCommitHook) -> None:
    """Run `fn(cur, batch, result)` after every committed batch of `kind`.
    Idempotent per function (keyed by module.qualname), so a re-imported
    module replaces its own hook instead of adding a second one."""
    if kind not in KINDS:
        raise ValueError(f"unknown ingest kind {kind!r}")
    name = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', repr(fn))}"
    with _lock:
        _post_hooks.setdefault(kind, {})[name] = fn


def post_commit_hooks(kind: str) -> list[PostCommitHook]:
    with _lock:
        return list(_post_hooks.get(kind, {}).values())


def post_commit_hook_names(kind: str) -> list[str]:
    with _lock:
        return sorted(_post_hooks.get(kind, {}))


def commit_handler(kind: str) -> CommitHandler | None:
    with _lock:
        entry = _handlers.get(kind)
    return entry[0] if entry else None


def registered_handlers() -> dict[str, str]:
    """{kind: dotted handler name} for status pages and tests."""
    with _lock:
        return {k: v[1] for k, v in _handlers.items()}


def mark_row_error(cur: Any, row_id: int, message: str) -> None:
    """For commit handlers: flag one staged row the writer refused (ERROR,
    with the reason appended to its messages) while the rest of the batch
    commits. The console then shows which lines did not make it. Pair it
    with a per-row savepoint, as core's own handlers do, so the refused
    row's partial writes roll back and the flag stays."""
    cur.execute(
        "UPDATE import_rows SET status = 'ERROR', messages = array_append(messages, %s) WHERE id = %s",
        (str(message)[:500], row_id),
    )
