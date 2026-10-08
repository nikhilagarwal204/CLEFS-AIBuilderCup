"""
USALI mapping: proposals, confirmation and locked, versioned reuse (04 spec 6.4).

Proposal pipeline, first answer wins:
  1. deterministic rules (confidence 1.0);
  2. the hotel's own locked mapping for the same code + normalised name;
  3. global memory for an exact name confirmed by 3+ hotels (0.9; memory is
     built from non-Xero data only);
  4. the LLM (ai.structured, store=False) in batches of 60, few-shot examples
     taken ONLY from this hotel's confirmed mappings.

Nothing is used until a person confirms it: a confirmed mapping is locked and
reused every month; a renamed or regrouped account gets a new version. Xero
rule: Xero-origin mappings never leave the hotel (never in memory, never a
few-shot example for anyone else).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from clefs import ai
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.modules.finance import usali
from clefs.modules.finance.accounts import norm_name

log = logging.getLogger("clefs.finance.mapping")

BULK_MIN_CONFIDENCE = 0.85
MEMORY_MIN_HOTELS = 3
LLM_BATCH = 60
AI_TASK = "finance.map_accounts"

# ─── Deterministic rules (confidence 1.0) ───

# (pattern on the normalised name, line for an expense or unknown section,
#  line for an income section or None to fall through). Names are Arabic,
# English or Tally ledger names; norm_name folds alef forms and ta marbuta.
_RULES: tuple[tuple[re.Pattern[str], str, str | None], ...] = (
    (re.compile(r"\bvat\b|value added tax|ضريبه القيمه المضافه"), "excluded.vat", "excluded.vat"),
    (re.compile(r"tourism dirham|درهم السياحه"), "excluded.tourism_dirham", "excluded.tourism_dirham"),
    (re.compile(r"municipality|رسوم البلديه|\bبلديه\b"), "excluded.municipality_fee", "excluded.municipality_fee"),
    (re.compile(r"depreciation|استهلاك"), "below_line.depreciation", None),
    (re.compile(r"amorti[sz]ation|اطفاء"), "below_line.amortization", None),
    (re.compile(r"\binterest\b|فوائد"), "below_line.interest", "non_operating.other"),
    (re.compile(r"\bdrawings?\b|مسحوبات"), "below_line.owner_drawings", None),
    (re.compile(r"\brent(al)?\b|ايجار"), "non_operating.rent", "misc.income"),
    (re.compile(r"\bdewa\b|electricity|\bwater\b|كهرباء|مياه"), "eww.other", None),
)

# Words that make an account "lexicon-matched below GOP" (check 5).
BELOW_GOP_LEXICON = re.compile(r"depreciation|استهلاك|\binterest\b|فوائد|\brent\b|ايجار|\bdrawings?\b|مسحوبات")

INCOME_SECTIONS = ("income", "other_income")


# The service charge follows Profit Guard's single setting (6.4): counted as
# revenue, SC income is rooms revenue and its distribution payroll; as a staff
# pass-through, both sides leave the P&L (excluded, netting to zero).
_SERVICE_CHARGE = re.compile(r"service charge|رسوم الخدمه|\bsc (income|distribution|distributed)\b")


def rule_for(name: str, section: str | None, *, sc_as_revenue: bool = True) -> str | None:
    """The deterministic line for an account name, or None."""
    n = norm_name(name)
    income = section in INCOME_SECTIONS
    if _SERVICE_CHARGE.search(n):
        if not sc_as_revenue:
            return "excluded.balance_sheet"
        return "rooms.revenue" if income else "rooms.payroll"
    for pattern, expense_line, income_line in _RULES:
        if pattern.search(n):
            return income_line if income else expense_line
    return None


def below_gop_lexicon(name: str) -> bool:
    return bool(BELOW_GOP_LEXICON.search(norm_name(name)))


# ─── LLM schema ───


class Alt(BaseModel):
    usali_line: usali.UsaliLine
    confidence: float


class MappingProposal(BaseModel):
    account_ref: str
    usali_line: usali.UsaliLine
    is_pass_through_tax: bool
    pass_through_kind: Literal["vat", "municipality_fee", "tourism_dirham"] | None
    confidence: float
    rationale: str = Field(max_length=200)
    alternatives: list[Alt] = Field(max_length=2)


class MappingBatch(BaseModel):
    proposals: list[MappingProposal]


SYSTEM = """Map hotel chart-of-accounts lines to USALI 12th edition line keys (enum below). UAE rules: VAT, municipality fee
and Tourism Dirham are pass-through taxes -> excluded.*; depreciation, amortisation, interest, rent, income tax and owner
drawings are below GOP; OTA/travel-agent commissions -> rooms.other.commissions. Names may be Arabic, English or Tally
ledger names. Use only name, group and type. Return a rationale citing the words that decided it. Never invent keys."""


# ─── Map rows ───


def live_map(cur: Any, account_id: int) -> dict | None:
    cur.execute("SELECT * FROM fin_usali_map WHERE account_id = %s AND superseded_at IS NULL", (account_id,))
    return cur.fetchone()


def _line_fields(line: str) -> dict:
    s = usali.spec(line)
    kind = usali.PASS_THROUGH_KIND.get(line)
    return {"usali_line": line, "usali_schedule": s.schedule, "usali_dept": s.dept, "category": s.category,
            "is_pass_through_tax": kind is not None, "pass_through_kind": kind}


def write_proposal(cur: Any, hotel_slug: str, account_id: int, line: str, *, learned_from: str, data_origin: str,
                   confidence: float | None, rationale: str | None = None, model: str | None = None,
                   alternatives: list | None = None, new_version: bool = False) -> dict:
    """Store an unconfirmed proposal. An unconfirmed live row is replaced in
    place; a locked row is never touched: `new_version` supersedes it and
    writes version + 1 (a renamed or regrouped account)."""
    f = _line_fields(line)
    live = live_map(cur, account_id)
    conf = None if confidence is None else round(max(0.0, min(1.0, float(confidence))), 2)
    if live is not None and not live["locked"]:
        cur.execute(
            """UPDATE fin_usali_map SET usali_line = %s, usali_schedule = %s, usali_dept = %s, category = %s,
                   is_pass_through_tax = %s, pass_through_kind = %s, ai_confidence = %s, ai_rationale = %s,
                   ai_model = %s, learned_from = %s, data_origin = %s, alternatives = %s
               WHERE id = %s RETURNING *""",
            (f["usali_line"], f["usali_schedule"], f["usali_dept"], f["category"], f["is_pass_through_tax"],
             f["pass_through_kind"], conf, rationale, model, learned_from, data_origin,
             Json(alternatives or []), live["id"]),
        )
        return cur.fetchone()
    if live is not None and not new_version:
        return live  # locked and unchanged: reused as is
    version = 1
    if live is not None:
        cur.execute("UPDATE fin_usali_map SET superseded_at = %s WHERE id = %s", (clock.now(), live["id"]))
        version = int(live["version"]) + 1
    cur.execute(
        """INSERT INTO fin_usali_map (hotel_slug, account_id, version, usali_line, usali_schedule, usali_dept, category,
               is_pass_through_tax, pass_through_kind, ai_confidence, ai_rationale, ai_model, learned_from, data_origin,
               alternatives)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (hotel_slug, account_id, version, f["usali_line"], f["usali_schedule"], f["usali_dept"], f["category"],
         f["is_pass_through_tax"], f["pass_through_kind"], conf, rationale, model, learned_from, data_origin,
         Json(alternatives or [])),
    )
    return cur.fetchone()


# ─── Proposal pipeline ───


def _hotel_locked(cur: Any, hotel_slug: str, acct: dict) -> dict | None:
    """The hotel's own locked mapping of another account with the same code
    and normalised name (for example the same books moved from uploads to a
    connector)."""
    cur.execute(
        """SELECT m.* FROM fin_usali_map m JOIN fin_coa_accounts a ON a.id = m.account_id
           WHERE m.hotel_slug = %s AND m.locked AND m.superseded_at IS NULL AND a.id <> %s
             AND a.code IS NOT DISTINCT FROM %s AND a.name_norm = %s
           ORDER BY m.confirmed_at DESC NULLS LAST LIMIT 1""",
        (hotel_slug, acct["id"], acct["code"], acct["name_norm"]),
    )
    return cur.fetchone()


def _memory(cur: Any, name_norm: str) -> str | None:
    cur.execute(
        """SELECT usali_line FROM fin_mapping_memory WHERE name_norm = %s AND hotels >= %s
           ORDER BY hotels DESC, confirmations DESC LIMIT 2""",
        (name_norm, MEMORY_MIN_HOTELS),
    )
    rows = cur.fetchall()
    # Two lines both backed by 3+ hotels: the name is ambiguous, ask the LLM.
    if len(rows) == 1 and usali.is_line(rows[0]["usali_line"]):
        return rows[0]["usali_line"]
    return None


_INCOME_TYPES = {"income", "other_income", "revenue", "sales", "otherincome", "other income", "direct incomes",
                 "indirect incomes", "sales accounts"}


def section_of_account(acct: dict) -> str | None:
    """'income' | 'expense' | None from the account's type or group (ingest
    sections, Xero/QBO account types, Tally groups)."""
    for v in (acct.get("type_raw"), acct.get("parent_group_raw")):
        n = norm_name(v).replace(" ", "") if v else ""
        if not n:
            continue
        if n in {t.replace(" ", "") for t in _INCOME_TYPES} or "income" in n or "revenue" in n or "ايراد" in n:
            return "income"
        return "expense"
    return None


def _magnitude(amount_minor: int | None) -> str:
    a = abs(int(amount_minor or 0)) / 100
    for bound, label in ((1_000, "<1k"), (10_000, "1k-10k"), (100_000, "10k-100k"), (1_000_000, "100k-1m")):
        if a < bound:
            return label
    return ">1m"


def propose(cur: Any, hotel_slug: str, accounts: list[dict], *, data_origin: str) -> dict:
    """Steps 1-3 for each account ({id, changed?, amount_minor?}); returns
    {proposed, reused, pending: [account ids left for the LLM]}. Locked and
    unchanged accounts are reused without a new proposal."""
    from clefs.modules.finance import ports

    out = {"proposed": 0, "reused": 0, "pending": []}
    sc = ports.treat_sc_as_revenue(cur, hotel_slug)
    for item in accounts:
        cur.execute("SELECT * FROM fin_coa_accounts WHERE id = %s AND hotel_slug = %s", (item["id"], hotel_slug))
        acct = cur.fetchone()
        if acct is None:
            continue
        changed = bool(item.get("changed"))
        live = live_map(cur, acct["id"])
        if live is not None and live["locked"] and not changed:
            out["reused"] += 1
            continue
        if live is not None and not live["locked"] and live["learned_from"] in ("llm", "manual") and not changed:
            continue  # an open LLM or manual proposal waits for its person
        line = rule_for(acct["name_raw"], section_of_account(acct), sc_as_revenue=sc)
        how, conf, why = "rule", 1.0, "deterministic rule"
        if line is None:
            prior = _hotel_locked(cur, hotel_slug, acct)
            if prior is not None:
                line, how, why = prior["usali_line"], "hotel_locked", "this hotel's confirmed mapping"
        if line is None:
            line = _memory(cur, acct["name_norm"])
            how, conf, why = "global_memory", 0.9, f"confirmed by {MEMORY_MIN_HOTELS}+ hotels"
        if line is None and changed and live is not None and live["locked"]:
            # Renamed or regrouped: the previous confirmed line is the new
            # version's proposal, under the bulk threshold so a person looks
            # at it on its own.
            line, how, conf = live["usali_line"], "hotel_locked", 0.8
            why = "renamed or regrouped in the books: the previous confirmed line, to re-confirm"
        if line is None:
            out["pending"].append(acct["id"])
            continue
        write_proposal(cur, hotel_slug, acct["id"], line, learned_from=how, data_origin=data_origin,
                       confidence=conf, rationale=why, new_version=changed)
        out["proposed"] += 1
    return out


def few_shot(cur: Any, hotel_slug: str, limit: int = 40) -> list[dict]:
    """Examples for the LLM: ONLY this hotel's own confirmed mappings (never
    another hotel's, so Xero-origin data never crosses hotels)."""
    cur.execute(
        """SELECT a.name_raw, a.parent_group_raw, a.type_raw, m.usali_line
           FROM fin_usali_map m JOIN fin_coa_accounts a ON a.id = m.account_id
           WHERE m.hotel_slug = %s AND a.hotel_slug = %s AND m.locked AND m.superseded_at IS NULL
           ORDER BY m.confirmed_at DESC NULLS LAST LIMIT %s""",
        (hotel_slug, hotel_slug, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def _llm_input(accts: list[dict], examples: list[dict]) -> str:
    lines = ["USALI line keys (the only allowed values):"]
    lines += [f"- {c['key']}: {c['label']}" for c in usali.catalog()]
    if examples:
        lines.append("\nThis hotel's confirmed mappings (examples):")
        lines += [f"- {e['name_raw']} | group: {e['parent_group_raw'] or '-'} | type: {e['type_raw'] or '-'}"
                  f" -> {e['usali_line']}" for e in examples]
    lines.append("\nAccounts to map (account_ref | code | name | group | type | magnitude):")
    lines += [f"- {a['id']} | {a['code'] or '-'} | {a['name_raw']} | {a['parent_group_raw'] or '-'} | "
              f"{a['type_raw'] or '-'} | {a.get('magnitude') or '-'}" for a in accts]
    return "\n".join(lines)


def propose_llm(cur: Any, hotel_slug: str, account_ids: list[int], *, amounts: dict[int, int] | None = None) -> dict:
    """Step 4: the LLM in batches of 60. AI off or failing leaves the
    accounts unmapped (a person maps them in the console): {proposed, failed}."""
    out = {"proposed": 0, "failed": 0, "ai": ai.enabled()}
    if not account_ids:
        return out
    cur.execute("SELECT * FROM fin_coa_accounts WHERE hotel_slug = %s AND id = ANY(%s) ORDER BY id",
                (hotel_slug, list(account_ids)))
    accts = [dict(r) for r in cur.fetchall()]
    for a in accts:
        a["magnitude"] = _magnitude((amounts or {}).get(a["id"]))
    examples = few_shot(cur, hotel_slug)
    origin = {a["id"]: a["source_system"] for a in accts}
    for i in range(0, len(accts), LLM_BATCH):
        chunk = accts[i:i + LLM_BATCH]
        try:
            res = ai.structured(AI_TASK, system=SYSTEM, input=_llm_input(chunk, examples), schema=MappingBatch,
                                hotel_slug=hotel_slug, store=False)
        except Exception as exc:  # noqa: BLE001  (AI off or failing: the accounts wait for a person)
            log.info("finance mapping LLM unavailable: %s", exc)
            out["failed"] += len(chunk)
            continue
        wanted = {str(a["id"]) for a in chunk}
        for p in res.proposals:
            if p.account_ref not in wanted or not usali.is_line(p.usali_line):
                continue
            wanted.discard(p.account_ref)
            aid = int(p.account_ref)
            live = live_map(cur, aid)
            write_proposal(cur, hotel_slug, aid, p.usali_line, learned_from="llm",
                           data_origin=_origin_label(origin.get(aid)), confidence=p.confidence,
                           rationale=p.rationale[:200], model=ai_model_name(),
                           alternatives=[x.model_dump() for x in p.alternatives[:2]],
                           new_version=live is not None and live["locked"])
            out["proposed"] += 1
        out["failed"] += len(wanted)
    return out


def _origin_label(source_system: str | None) -> str:
    return {"upload": "upload", "tally": "tally_upload"}.get(source_system or "", source_system or "upload")


def ai_model_name() -> str | None:
    try:
        from clefs.settings import ai_model_for

        return ai_model_for(AI_TASK)
    except Exception:  # noqa: BLE001
        return None


# ─── Confirmation ───


def _needs_override(acct: dict, line: str) -> bool:
    """Check 5: a lexicon-matched depreciation, interest, rent or drawings
    account may sit above GOP only with an override_reason."""
    return usali.above_gop(line) and below_gop_lexicon(acct["name_raw"])


def confirm_one(cur: Any, hotel_slug: str, account_id: int, *, actor_id: int | None, usali_line: str | None = None,
                override_reason: str | None = None) -> dict:
    """Lock the account's live proposal, or map it by hand (`usali_line`).
    A change to a locked mapping supersedes it with a new locked version."""
    cur.execute("SELECT * FROM fin_coa_accounts WHERE id = %s AND hotel_slug = %s", (account_id, hotel_slug))
    acct = cur.fetchone()
    if acct is None:
        raise ClefsError(404, "account not found", "account_not_found")
    if usali_line is not None and not usali.is_line(usali_line):
        raise ClefsError(400, f"unknown USALI line {usali_line!r}", "invalid_usali_line")
    live = live_map(cur, account_id)
    if live is None and usali_line is None:
        raise ClefsError(409, "this account has no proposal yet: choose a line", "no_proposal")
    line = usali_line or live["usali_line"]
    reason = (override_reason or "").strip() or None
    if _needs_override(acct, line) and not reason:
        raise ClefsError(400, f"{acct['name_raw']!r} reads as below GOP; give an override reason to map it to {line}",
                         "override_reason_required")
    origin = _origin_label(acct["source_system"])
    if live is None or (usali_line is not None and usali_line != live["usali_line"]):
        row = write_proposal(cur, hotel_slug, account_id, line, learned_from="manual", data_origin=origin,
                             confidence=1.0, rationale="mapped by a person",
                             new_version=live is not None and live["locked"])
    else:
        row = live
    cur.execute(
        """UPDATE fin_usali_map SET locked = TRUE, confirmed_by = %s, confirmed_at = %s,
               override_reason = COALESCE(%s, override_reason)
           WHERE id = %s RETURNING *""",
        (actor_id, clock.now(), reason, row["id"]),
    )
    return cur.fetchone()


def confirm(cur: Any, hotel_slug: str, *, actor_id: int | None, items: list[dict] | None = None,
            all_above: float | None = None, account_ids: list[int] | None = None) -> dict:
    """Bulk confirm. Either `all_above` (at least 0.85: every open proposal of
    the hotel, or of `account_ids`, with that confidence or more) or `items`
    [{account_id, usali_line?, override_reason?}]. A proposal under 0.85 is
    confirmed one at a time (a single item), never in bulk."""
    done: list[int] = []
    if all_above is not None:
        if all_above < BULK_MIN_CONFIDENCE:
            raise ClefsError(400, f"bulk confirmation needs a confidence of at least {BULK_MIN_CONFIDENCE}",
                             "bulk_threshold")
        cur.execute(
            """SELECT m.account_id FROM fin_usali_map m
               WHERE m.hotel_slug = %s AND NOT m.locked AND m.superseded_at IS NULL AND m.ai_confidence >= %s
                 AND (%s::bigint[] IS NULL OR m.account_id = ANY(%s::bigint[]))
               ORDER BY m.account_id""",
            (hotel_slug, all_above, account_ids, account_ids),
        )
        for r in cur.fetchall():
            cur.execute("SELECT name_raw FROM fin_coa_accounts WHERE id = %s", (r["account_id"],))
            live = live_map(cur, r["account_id"])
            if live and _needs_override(cur.fetchone() or {"name_raw": ""}, live["usali_line"]):
                continue  # needs a reason: confirmed on its own
            confirm_one(cur, hotel_slug, r["account_id"], actor_id=actor_id)
            done.append(r["account_id"])
        return {"confirmed": done}
    items = items or []
    if not items:
        raise ClefsError(400, "nothing to confirm", "nothing_to_confirm")
    if len(items) > 1:
        for it in items:
            live = live_map(cur, int(it["account_id"]))
            conf = 1.0 if it.get("usali_line") else float((live or {}).get("ai_confidence") or 0)
            if conf < BULK_MIN_CONFIDENCE:
                raise ClefsError(400, "a proposal under 0.85 must be confirmed on its own", "confirm_one_at_a_time")
    for it in items:
        confirm_one(cur, hotel_slug, int(it["account_id"]), actor_id=actor_id, usali_line=it.get("usali_line"),
                    override_reason=it.get("override_reason"))
        done.append(int(it["account_id"]))
    return {"confirmed": done}


# ─── Global memory (weekly, non-Xero data only) ───


def rebuild_memory(cur: Any) -> int:
    """Rebuild fin_mapping_memory from every hotel's locked, live mappings,
    excluding anything of Xero origin (Xero ToS: its data never trains a
    shared memory). Returns the row count."""
    cur.execute("DELETE FROM fin_mapping_memory")
    cur.execute(
        """INSERT INTO fin_mapping_memory (name_norm, language, usali_line, confirmations, hotels)
           SELECT a.name_norm, MAX(a.language), m.usali_line, COUNT(*), COUNT(DISTINCT m.hotel_slug)
           FROM fin_usali_map m JOIN fin_coa_accounts a ON a.id = m.account_id
           WHERE m.locked AND m.superseded_at IS NULL
             AND m.data_origin <> 'xero' AND a.source_system <> 'xero'
           GROUP BY a.name_norm, m.usali_line"""
    )
    return cur.rowcount
