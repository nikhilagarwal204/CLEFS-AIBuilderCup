"""
Finding a data subject's rows for the DSR executors (dsr.py).

A person is identified by their contact HMACs (privacy.contact_hmac of the
phone and email they gave), which is how every Clefs table keys a guest, plus
the stays those contacts or a booking reference lead to. Their "own"
transcripts and requests are the legacy rows filed from their room during
their stay (conversation_logs and staff_requests carry a room, not a guest).

Tables of other modules are reached through clefs.core.privacy's PII
registry: a module declares which columns identify a subject
(subject_columns kind hmac / phone / email / phone_or_email / subject_ref),
and this file matches on them. So Trust never imports another module, and a
module that registers its table is covered by access and erase for free.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from clefs.core import privacy
from clefs.core import time as ctime
from clefs.core.hotels import get_hotel

# ─── Subject ───


@dataclass
class Subject:
    hotel_slug: str
    hmacs: list[bytes] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    reservation_ids: list[int] = field(default_factory=list)
    stays: list[dict] = field(default_factory=list)  # {room, start, end, start_ms, end_ms, matched_by}

    @property
    def refs(self) -> list[str]:
        """consents.subject_ref values (contact_ref is the HMAC in hex)."""
        return [h.hex() for h in self.hmacs]

    @property
    def empty(self) -> bool:
        return not (self.hmacs or self.reservation_ids or self.stays)


def _table_exists(cur: Any, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (table,))
    return bool(cur.fetchone()["ok"])


def _window(hotel: Any, start: date, end: date, matched_by: str, room: str | None) -> dict:
    s, _ = ctime.local_day_bounds(hotel, start)
    _, e = ctime.local_day_bounds(hotel, end)
    return {"room": room, "start": s, "end": e, "start_ms": ctime.to_ms(s), "end_ms": ctime.to_ms(e),
            "matched_by": matched_by}


def resolve(cur: Any, hotel_slug: str, identifiers: dict, hmac_hex: list[str]) -> Subject:
    hotel = get_hotel(cur, hotel_slug)
    sub = Subject(hotel_slug=hotel_slug, hmacs=[bytes.fromhex(h) for h in hmac_hex])
    cc = privacy.hotel_calling_code(hotel_slug, cur=cur)
    if identifiers.get("phone"):
        try:
            sub.phones.append(privacy.normalize_contact("phone", identifiers["phone"], default_country_code=cc))
        except Exception:  # noqa: BLE001 - an unparseable number just matches nothing
            pass
    if identifiers.get("email"):
        sub.emails.append(identifiers["email"].strip().lower())
    if _table_exists(cur, "reservations"):
        ids: set[int] = set()
        if sub.hmacs and _table_exists(cur, "reservation_contacts"):
            cur.execute(
                "SELECT DISTINCT reservation_id FROM reservation_contacts WHERE hotel_slug = %s AND value_hmac = ANY(%s) "
                "AND reservation_id IS NOT NULL",
                (hotel_slug, sub.hmacs),
            )
            ids |= {r["reservation_id"] for r in cur.fetchall()}
        ref = (identifiers.get("reservation_ref") or "").strip()
        if ref:
            cur.execute("SELECT id FROM reservations WHERE hotel_slug = %s AND (pms_res_id = %s OR ota_res_id = %s)",
                        (hotel_slug, ref, ref))
            ids |= {r["id"] for r in cur.fetchall()}
        sub.reservation_ids = sorted(ids)
        if ids:
            cur.execute(
                "SELECT id, room_number, check_in, COALESCE(actual_check_out, check_out) AS check_out FROM reservations "
                "WHERE id = ANY(%s)",
                (sub.reservation_ids,),
            )
            for r in cur.fetchall():
                if r["room_number"]:
                    sub.stays.append(_window(hotel, r["check_in"], r["check_out"], "reservation", r["room_number"]))
    # A room and a night given on the form, with no reservation on file (a
    # hotel without a PMS feed): that one local day in that room.
    if not sub.stays and identifiers.get("room") and identifiers.get("stay_date"):
        try:
            d = date.fromisoformat(identifiers["stay_date"])
            sub.stays.append(_window(hotel, d, d, "room_and_date", str(identifiers["room"]).strip()))
        except ValueError:
            pass
    return sub


# ─── Rendering ───


def render_row(row: dict) -> dict:
    """JSON-safe copy for a bundle: sealed *_enc columns are opened under
    their plain name (the person's own data), other bytes are dropped (a
    hash means nothing to its subject)."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (bytes, memoryview, bytearray)):
            if k.endswith("_enc"):
                try:
                    out[k[:-4]] = privacy.unseal(v)
                except ValueError:
                    pass
            continue
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        out[k] = v
    return json.loads(json.dumps(out, default=str))


# ─── Where a subject's rows live ───

ERASED = "[erased]"
# Free-text columns that hold the guest's own words on the legacy tables that
# key a guest by room, not by contact (so the PII registry loop below skips
# them). staff_requests: description is the staff-facing summary,
# original_text what the guest said in their own language, guest_comment the
# status tracker's "not resolved" note. Erase (DSR) and the retention class
# request_free_text both read this, so the two can never drift apart again.
GUEST_WORDS: dict[str, tuple[str, ...]] = {
    "staff_requests": ("description", "original_text", "guest_comment"),
}
# Kinds of register_pii subject columns that identify one person exactly.
_MATCH_KINDS = {"hmac", "phone", "email", "phone_or_email", "subject_ref", "ref"}


@dataclass
class Match:
    section: str  # bundle section name
    table: str
    where: str  # SQL predicate with %s placeholders
    params: list
    erase: str  # delete | anonymize | keep
    erase_set: dict[str, str] = field(default_factory=dict)  # column -> SQL value


def _columns(cur: Any, table: str) -> dict[str, dict]:
    cur.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    return {r["column_name"]: {"type": r["data_type"], "nullable": r["is_nullable"] == "YES"} for r in cur.fetchall()}


def _erased_value(col: dict) -> str:
    if col["type"] in ("text", "character varying"):
        return f"'{ERASED}'"
    if col["nullable"]:
        return "NULL"
    return {"jsonb": "'{}'::jsonb", "json": "'{}'::json", "bytea": "''::bytea", "ARRAY": "'{}'"}.get(col["type"], "NULL")


def _stay_predicate(sub: Subject, room_col: str, ts_col: str) -> tuple[str, list] | None:
    if not sub.stays:
        return None
    parts, params = [], []
    for s in sub.stays:
        parts.append(f"({room_col} = %s AND {ts_col} >= %s AND {ts_col} < %s)")
        params += [s["room"], s["start_ms"], s["end_ms"]]
    return "(" + " OR ".join(parts) + ")", params


BUILTIN_TABLES = ("consents", "reservations", "reservation_contacts", "conversation_logs", "staff_requests")


def matches(cur: Any, sub: Subject) -> list[Match]:
    """Every table section holding this subject's rows, with how erase treats it."""
    slug = sub.hotel_slug
    out: list[Match] = []
    if sub.refs:
        # The consent ledger is kept: it is the evidence of what was agreed
        # and withdrawn, and it holds only hashed identities.
        out.append(Match("consents", "consents", "hotel_slug = %s AND subject_ref = ANY(%s)", [slug, sub.refs], "keep"))
    if sub.reservation_ids and _table_exists(cur, "reservations"):
        # Stays are tax and accounting records: the name goes, the stay stays.
        out.append(Match("stays", "reservations", "hotel_slug = %s AND id = ANY(%s)", [slug, sub.reservation_ids],
                         "anonymize", {"guest_name": f"'{ERASED}'", "guest_name_norm": "NULL"}))
    if sub.hmacs and _table_exists(cur, "reservation_contacts"):
        out.append(Match("contacts", "reservation_contacts", "hotel_slug = %s AND value_hmac = ANY(%s)", [slug, sub.hmacs],
                         "anonymize", {"value_enc": "NULL", "value_hmac": "NULL", "purged_at": "NOW()"}))
    for section, table, col, text_cols in (("transcripts", "conversation_logs", "room_number", ("message",)),
                                           ("requests", "staff_requests", "room", GUEST_WORDS["staff_requests"])):
        pred = _stay_predicate(sub, col, "timestamp")
        if not pred:
            continue
        # Only the columns this schema has: service added original_text and
        # guest_comment in a later migration, and a hotel on an older schema
        # must still erase the description rather than fail on a missing column.
        cols = _columns(cur, table)
        erase_set = {c: _erased_value(cols[c]) for c in text_cols if c in cols}
        if erase_set:
            out.append(Match(section, table, f"hotel_slug = %s AND {pred[0]}", [slug, *pred[1]],
                             "anonymize", erase_set))
    values = {"hmac": sub.hmacs, "phone": sub.phones, "email": sub.emails,
              "phone_or_email": sub.phones + sub.emails, "subject_ref": sub.refs, "ref": sub.refs}
    for spec in privacy.pii_registry():
        if spec.table in BUILTIN_TABLES:
            continue
        cols = _columns(cur, spec.table)
        if "hotel_slug" not in cols:
            continue
        preds, params = [], []
        for c, kind in spec.subject_columns.items():
            vals = values.get(kind) if kind in _MATCH_KINDS else None
            if c in cols and vals:
                # An array column (gst_stays.contact_hmacs) matches on overlap.
                preds.append(f"{c} && %s" if cols[c]["type"] == "ARRAY" else f"{c} = ANY(%s)")
                params.append(vals)
            elif kind == "room" and c in cols and sub.stays and spec.ts_column in cols:
                # Rows keyed only by room (orders, quotes) are the guest's
                # when they fall inside one of their stays in that room.
                ms = cols[spec.ts_column]["type"] in ("bigint", "integer")
                for st in sub.stays:
                    preds.append(f"({c} = %s AND {spec.ts_column} >= %s AND {spec.ts_column} < %s)")
                    params += [st["room"], st["start_ms"] if ms else st["start"], st["end_ms"] if ms else st["end"]]
        if not preds:
            continue
        erase_set = {c: _erased_value(cols[c]) for c in spec.redact_columns if c in cols}
        # Subject columns are cleared too on redact, or the row would still
        # point at the person after their "erasure". A room number is not the
        # person (the next guest has it too) and keeps order statistics whole.
        erase_set.update({c: _erased_value(cols[c]) for c, kind in spec.subject_columns.items()
                          if c in cols and kind != "room"})
        out.append(Match(f"{spec.module}.{spec.table}", spec.table, "hotel_slug = %s AND (" + " OR ".join(preds) + ")",
                         [slug, *params], "delete" if spec.strategy == "delete" else "anonymize", erase_set))
    return out


def collect(cur: Any, sub: Subject, *, limit_per_section: int = 5000) -> dict[str, list[dict]]:
    """Everything the hotel holds on the subject, section by section."""
    out: dict[str, list[dict]] = {}
    for m in matches(cur, sub):
        cur.execute(f"SELECT * FROM {m.table} WHERE {m.where} LIMIT %s", (*m.params, limit_per_section))
        rows = [render_row(dict(r)) for r in cur.fetchall()]
        if m.section == "stays":
            rows = [{k: v for k, v in r.items() if k != "guest_name_norm"} for r in rows]
        out.setdefault(m.section, []).extend(rows)
    return out


def erase(cur: Any, sub: Subject) -> dict[str, int]:
    """Apply each section's erase treatment. Returns counts per section."""
    counts: dict[str, int] = {}
    for m in matches(cur, sub):
        if m.erase == "keep":
            continue
        if m.erase == "delete":
            cur.execute(f"DELETE FROM {m.table} WHERE {m.where}", m.params)
        else:
            if not m.erase_set:
                continue
            sets = ", ".join(f"{c} = {v}" for c, v in m.erase_set.items())
            cur.execute(f"UPDATE {m.table} SET {sets} WHERE {m.where}", m.params)
        counts[m.section] = counts.get(m.section, 0) + (cur.rowcount or 0)
    return counts
