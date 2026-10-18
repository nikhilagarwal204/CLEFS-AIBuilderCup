"""
Structured bilingual knowledge base (03 spec 2.5, 04 spec 5.5/7.2).

What the concierge may say comes from cc_kb_entries with status 'approved'
only. Entries arrive as 'pending' from the console, the legacy-text import or
Visibility (propose_kb_entry) and a person approves them. Every change writes
a cc_kb_revisions snapshot, so "who said the pool opens at 7" has an answer.

kb_mode per hotel: 'legacy_text' keeps the prompt on hotels.knowledge_base
verbatim (existing hotels, shown as "Not approved" by the readiness gate);
'structured' reads approved entries. Approving a legacy import flips a hotel
to structured.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from clefs.audit import actor_fields, audit
from clefs.core import time as hotel_time
from clefs.core.errors import bad_request, conflict, not_found
from clefs.core.hotels import get_hotel, get_module_settings, module_settings_row, set_module_settings
from clefs.db import Json
from clefs.modules.concierge.constants import (
    BOOLEAN_FACTS,
    FACT_KEYS,
    FACT_SECTION,
    KB_KINDS,
    PARKING_VALUES,
    STANDARD_SECTIONS,
    TIME_FACTS,
    ConciergeSettings,
)

log = logging.getLogger("clefs.concierge.kb")

ENTRY_COLUMNS = (
    "id, hotel_slug, section_id, import_id, kind, question_en, answer_en, question_ar, answer_ar, tags, "
    "fact_key, fact_value, location_slug, source, source_url, source_label, valid_from, valid_to, status, "
    "version, approved_by, approved_at, rejected_reason, created_by_type, created_by_id, created_at, updated_at"
)
EDITABLE_FIELDS = (
    "section_id", "kind", "question_en", "answer_en", "question_ar", "answer_ar", "tags", "fact_key",
    "fact_value", "location_slug", "source_url", "source_label", "valid_from", "valid_to",
)
TEXT_MAX = 4000


# ─── Sections ───


def ensure_sections(cur: Any, hotel_slug: str) -> None:
    """Create the standard sections once per hotel (idempotent). Required
    ones gate the readiness check, so every hotel has the same keys."""
    for sort, (key, title_en, title_ar, required) in enumerate(STANDARD_SECTIONS):
        cur.execute(
            """
            INSERT INTO cc_kb_sections (hotel_slug, key, title_en, title_ar, sort, required)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, key) DO NOTHING
            """,
            (hotel_slug, key, title_en, title_ar, sort * 10, required),
        )


def list_sections(cur: Any, hotel_slug: str) -> list[dict]:
    """Sections with approved and pending counts (the console's left rail)."""
    ensure_sections(cur, hotel_slug)
    cur.execute(
        """
        SELECT s.id, s.key, s.title_en, s.title_ar, s.sort, s.required,
               COUNT(e.id) FILTER (WHERE e.status = 'approved') AS approved,
               COUNT(e.id) FILTER (WHERE e.status = 'pending') AS pending
        FROM cc_kb_sections s
        LEFT JOIN cc_kb_entries e ON e.section_id = s.id
        WHERE s.hotel_slug = %s
        GROUP BY s.id ORDER BY s.sort, s.id
        """,
        (hotel_slug,),
    )
    return [dict(r) for r in cur.fetchall()]


def section_id(cur: Any, hotel_slug: str, key: str) -> int:
    ensure_sections(cur, hotel_slug)
    cur.execute("SELECT id FROM cc_kb_sections WHERE hotel_slug = %s AND key = %s", (hotel_slug, key))
    row = cur.fetchone()
    if row is None:
        raise bad_request(f"unknown section {key!r}", "unknown_section")
    return row["id"]


def _check_section(cur: Any, hotel_slug: str, sid: int | None) -> None:
    # A section id from the body must belong to this hotel (tenant check).
    if sid is None:
        return
    cur.execute("SELECT 1 FROM cc_kb_sections WHERE id = %s AND hotel_slug = %s", (sid, hotel_slug))
    if cur.fetchone() is None:
        raise bad_request("section not found for this hotel", "unknown_section")


# ─── kb_mode ───


def settings(cur: Any, hotel_slug: str) -> ConciergeSettings:
    return get_module_settings(cur, hotel_slug, "concierge", ConciergeSettings)


def kb_mode(cur: Any, hotel_slug: str) -> str:
    """The explicit setting wins. Unset: a hotel with legacy text and no
    approved entry stays on legacy_text (its owner has approved nothing
    yet); anything else reads the structured KB."""
    mode = settings(cur, hotel_slug).kb_mode
    if mode:
        return mode
    cur.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(h.knowledge_base), ''), '') <> '' AS has_text,
               EXISTS (SELECT 1 FROM cc_kb_entries e WHERE e.hotel_slug = h.slug AND e.status = 'approved') AS has_approved
        FROM hotels h WHERE h.slug = %s
        """,
        (hotel_slug,),
    )
    row = cur.fetchone() or {}
    return "legacy_text" if row.get("has_text") and not row.get("has_approved") else "structured"


def set_kb_mode(cur: Any, hotel_slug: str, mode: str, *, actor: Any) -> str:
    if mode not in ("legacy_text", "structured"):
        raise bad_request("kb_mode must be legacy_text or structured", "invalid_kb_mode")
    row = module_settings_row(cur, hotel_slug, "concierge")
    # Merge, never replace: set_module_settings overwrites the whole JSON.
    data = dict((row or {}).get("settings") or {})
    data["kb_mode"] = mode
    set_module_settings(cur, hotel_slug, "concierge", data, actor=actor)
    return mode


# ─── Entries: validation ───

_TIME_RE = re.compile(r"^\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\s*$", re.I)
_TRUE = {"true", "yes", "y", "1", "available", "allowed", "free"}
_FALSE = {"false", "no", "n", "0", "none", "not allowed", "unavailable"}


def normalize_fact_value(fact_key: str, value: Any) -> str:
    """The normalised value Visibility compares ("15:00", "paid", "true");
    the free-text answer stays what guests hear."""
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        raise bad_request(f"{fact_key} needs a value", "invalid_fact_value")
    if fact_key in TIME_FACTS:
        m = _TIME_RE.match(raw)
        if not m:
            raise bad_request(f"{fact_key} must be a time like 15:00", "invalid_fact_value")
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            raise bad_request(f"{fact_key} must be a time like 15:00", "invalid_fact_value")
        return f"{hour:02d}:{minute:02d}"
    if fact_key == "parking":
        if raw not in PARKING_VALUES:
            raise bad_request(f"parking must be one of {', '.join(PARKING_VALUES)}", "invalid_fact_value")
        return raw
    if fact_key in BOOLEAN_FACTS:
        if raw in _TRUE:
            return "true"
        if raw in _FALSE:
            return "false"
        raise bad_request(f"{fact_key} must be true or false", "invalid_fact_value")
    if fact_key == "star_rating":
        if raw.isdigit() and 1 <= int(raw) <= 7:
            return raw
        raise bad_request("star_rating must be 1 to 7", "invalid_fact_value")
    raise bad_request(f"unknown fact key {fact_key!r}", "invalid_fact_key")


def _clip(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:TEXT_MAX] if text else None


def _clean_fields(cur: Any, hotel_slug: str, data: dict) -> dict:
    """Validate and normalise the editable fields present in `data`."""
    out: dict[str, Any] = {}
    for key in EDITABLE_FIELDS:
        if key in data:
            out[key] = data[key]
    for key in ("question_en", "answer_en", "question_ar", "answer_ar", "source_label", "location_slug"):
        if key in out:
            out[key] = _clip(out[key])
    if "kind" in out and out["kind"] not in KB_KINDS:
        raise bad_request(f"kind must be one of {', '.join(KB_KINDS)}", "invalid_kind")
    if "tags" in out:
        out["tags"] = [str(t).strip()[:40] for t in (out["tags"] or []) if str(t).strip()][:20]
    if out.get("source_url") is not None:
        url = str(out["source_url"]).strip()
        if url and not re.match(r"^https?://", url, re.I):
            raise bad_request("source_url must be an http(s) link", "invalid_source_url")
        out["source_url"] = url[:1000] or None
    if out.get("fact_key") is not None:
        if out["fact_key"] not in FACT_KEYS:
            raise bad_request(f"fact_key must be one of {', '.join(FACT_KEYS)}", "invalid_fact_key")
        out["fact_value"] = normalize_fact_value(out["fact_key"], out.get("fact_value"))
    elif "fact_key" in out:
        out["fact_value"] = None
    if "section_id" in out:
        _check_section(cur, hotel_slug, out["section_id"])
    return out


def _check_row(row: dict) -> None:
    # The DB CHECKs, raised as 400s with a readable code instead of a 500.
    if not (row.get("answer_en") or "").strip():
        raise bad_request("answer_en is required", "answer_required")
    if row.get("kind") == "local_place" and not row.get("source_url"):
        raise bad_request("a local recommendation needs a source link", "source_url_required")


def _user_id(actor: Any) -> int | None:
    principal = getattr(actor, "principal", actor)
    uid = getattr(principal, "user_id", None)
    return uid if isinstance(uid, int) else None


# ─── Entries: create, edit, status ───


def get_entry(cur: Any, hotel_slug: str, entry_id: int, *, lock: bool = False) -> dict | None:
    cur.execute(
        f"SELECT {ENTRY_COLUMNS} FROM cc_kb_entries WHERE hotel_slug = %s AND id = %s" + (" FOR UPDATE" if lock else ""),
        (hotel_slug, entry_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def create_entry(cur: Any, hotel_slug: str, data: dict, *, source: str = "console", actor: Any = None, import_id: int | None = None) -> dict:
    """A new entry is always pending: nothing reaches a guest unapproved."""
    fields = _clean_fields(cur, hotel_slug, data)
    fields.setdefault("kind", "fact")
    if fields.get("section_id") is None:
        key = data.get("section_key") or FACT_SECTION.get(fields.get("fact_key") or "", "general")
        fields["section_id"] = section_id(cur, hotel_slug, key)
    _check_row(fields)
    actor_type, actor_id = _actor(actor)
    cols = list(fields)
    cur.execute(
        f"""
        INSERT INTO cc_kb_entries (hotel_slug, import_id, source, created_by_type, created_by_id, {", ".join(cols)})
        VALUES (%s, %s, %s, %s, %s, {", ".join(["%s"] * len(cols))})
        RETURNING {ENTRY_COLUMNS}
        """,
        [hotel_slug, import_id, (source or "console")[:40], actor_type, actor_id] + [fields[c] for c in cols],
    )
    row = dict(cur.fetchone())
    _revision(cur, row, "created", actor)
    return row


def update_entry(cur: Any, hotel_slug: str, entry_id: int, data: dict, *, actor: Any, approve: bool = False) -> dict:
    """Edit an entry. An edit of approved text goes back to pending (a new
    version) unless the editor approves it in the same step, so an approved
    answer never changes without someone who may approve seeing it."""
    row = get_entry(cur, hotel_slug, entry_id, lock=True)
    if row is None:
        raise not_found("KB entry not found", "kb_entry_not_found")
    if "fact_value" in data and "fact_key" not in data:
        data = {**data, "fact_key": row["fact_key"]}  # normalise against the entry's own key
    fields = _clean_fields(cur, hotel_slug, data)
    changed = {k: v for k, v in fields.items() if row.get(k) != v}
    if not changed and not approve:
        return row
    merged = {**row, **changed}
    _check_row(merged)
    status = "pending" if row["status"] in ("approved", "rejected") else row["status"]
    sets = [f"{k} = %s" for k in changed] + ["status = %s", "version = version + 1", "updated_at = NOW()"]
    cur.execute(
        f"UPDATE cc_kb_entries SET {', '.join(sets)} WHERE id = %s RETURNING {ENTRY_COLUMNS}",
        [changed[k] for k in changed] + [status, entry_id],
    )
    out = dict(cur.fetchone())
    _revision(cur, out, "edited", actor)
    return approve_entry(cur, hotel_slug, entry_id, actor=actor) if approve else out


def approve_entry(cur: Any, hotel_slug: str, entry_id: int, *, actor: Any) -> dict:
    row = get_entry(cur, hotel_slug, entry_id, lock=True)
    if row is None:
        raise not_found("KB entry not found", "kb_entry_not_found")
    if row["status"] == "approved":
        return row
    if row["status"] == "archived":
        raise conflict("an archived entry cannot be approved; create a new one", "kb_entry_archived")
    _check_row(row)
    if row["fact_key"]:
        # One live answer per structured fact (the partial unique index):
        # the old approved value is archived first, in this transaction.
        cur.execute(
            f"""
            UPDATE cc_kb_entries SET status = 'archived', updated_at = NOW()
            WHERE hotel_slug = %s AND fact_key = %s AND status = 'approved' AND id <> %s
            RETURNING {ENTRY_COLUMNS}
            """,
            (hotel_slug, row["fact_key"], entry_id),
        )
        for old in cur.fetchall():
            _revision(cur, dict(old), "archived", actor)
    cur.execute(
        f"""
        UPDATE cc_kb_entries SET status = 'approved', approved_by = %s, approved_at = NOW(),
               rejected_reason = NULL, updated_at = NOW()
        WHERE id = %s RETURNING {ENTRY_COLUMNS}
        """,
        (_user_id(actor), entry_id),
    )
    out = dict(cur.fetchone())
    _revision(cur, out, "approved", actor)
    audit(cur, actor, "concierge.kb.approve", "cc_kb_entry", entry_id, after=_snapshot(out), hotel_slug=hotel_slug)
    return out


def set_entry_status(cur: Any, hotel_slug: str, entry_id: int, status: str, *, actor: Any, reason: str | None = None) -> dict:
    """Reject a pending entry or archive any entry (withdraws it from guests)."""
    if status not in ("rejected", "archived"):
        raise bad_request("status must be rejected or archived", "invalid_status")
    row = get_entry(cur, hotel_slug, entry_id, lock=True)
    if row is None:
        raise not_found("KB entry not found", "kb_entry_not_found")
    if row["status"] == status:
        return row
    if status == "rejected" and row["status"] != "pending":
        raise conflict("only a pending entry can be rejected", "kb_entry_not_pending")
    cur.execute(
        f"""
        UPDATE cc_kb_entries SET status = %s, rejected_reason = %s, updated_at = NOW()
        WHERE id = %s RETURNING {ENTRY_COLUMNS}
        """,
        (status, _clip(reason) if status == "rejected" else row["rejected_reason"], entry_id),
    )
    out = dict(cur.fetchone())
    _revision(cur, out, status, actor)
    if row["status"] == "approved":
        audit(cur, actor, f"concierge.kb.{status}", "cc_kb_entry", entry_id, before=_snapshot(row), hotel_slug=hotel_slug)
    return out


def list_entries(
    cur: Any, hotel_slug: str, *, status: str | None = None, section_id: int | None = None, q: str | None = None, limit: int = 500
) -> list[dict]:
    where, params = ["hotel_slug = %s"], [hotel_slug]
    if status:
        where.append("status = %s")
        params.append(status)
    if section_id:
        where.append("section_id = %s")
        params.append(section_id)
    if q:
        where.append("search @@ (plainto_tsquery('english', %s) || plainto_tsquery('simple', %s))")
        params += [q, q]
    cur.execute(
        f"SELECT {ENTRY_COLUMNS} FROM cc_kb_entries WHERE {' AND '.join(where)} "
        "ORDER BY section_id NULLS LAST, id LIMIT %s",
        params + [max(1, min(limit, 2000))],
    )
    return [dict(r) for r in cur.fetchall()]


# ─── Revisions ───


def _actor(actor: Any) -> tuple[str, str | None]:
    kind, ident, _ = actor_fields(actor)
    return kind, ident


def _snapshot(row: dict) -> dict:
    keep = ("kind", "question_en", "answer_en", "question_ar", "answer_ar", "fact_key", "fact_value", "source_url", "status", "version", "section_id")
    return {k: row.get(k) for k in keep}


def _revision(cur: Any, row: dict, change: str, actor: Any) -> None:
    actor_type, actor_id = _actor(actor)
    cur.execute(
        """
        INSERT INTO cc_kb_revisions (entry_id, hotel_slug, version, change, snapshot, actor_type, actor_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (row["id"], row["hotel_slug"], row["version"], change, Json(_snapshot(row)), actor_type, actor_id),
    )


def list_revisions(cur: Any, hotel_slug: str, entry_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT id, entry_id, version, change, snapshot, actor_type, actor_id, at
        FROM cc_kb_revisions WHERE hotel_slug = %s AND entry_id = %s ORDER BY id
        """,
        (hotel_slug, entry_id),
    )
    return [dict(r) for r in cur.fetchall()]


# ─── Reads for the prompt, kb_facts and search ───


def _today(cur: Any, hotel_slug: str):
    return hotel_time.local_date(get_hotel(cur, hotel_slug))


def approved_entries(cur: Any, hotel_slug: str, *, keyed_only: bool = False, today=None) -> list[dict]:
    """Approved entries in force today (hotel-local date), with their
    section, in section order. The only rows the concierge may state."""
    today = today or _today(cur, hotel_slug)
    cur.execute(
        f"""
        SELECT {", ".join("e." + c.strip() for c in ENTRY_COLUMNS.split(","))},
               s.key AS section_key, s.title_en AS section_title_en, s.title_ar AS section_title_ar, s.sort AS section_sort
        FROM cc_kb_entries e
        LEFT JOIN cc_kb_sections s ON s.id = e.section_id
        WHERE e.hotel_slug = %s AND e.status = 'approved'
          AND (e.valid_from IS NULL OR e.valid_from <= %s) AND (e.valid_to IS NULL OR e.valid_to >= %s)
          {"AND e.fact_key IS NOT NULL" if keyed_only else ""}
        ORDER BY s.sort NULLS LAST, e.id
        """,
        (hotel_slug, today, today),
    )
    return [dict(r) for r in cur.fetchall()]


def entry_text(entry: dict, language: str = "en") -> str:
    """One entry as a line of the prompt: the question as a heading when
    there is one, in Arabic when asked for and written, else English."""
    ar = (language or "en").startswith("ar") and (entry.get("answer_ar") or "").strip()
    q = (entry.get("question_ar") if ar else entry.get("question_en")) or entry.get("question_en")
    a = entry.get("answer_ar") if ar else entry.get("answer_en")
    text = f"{q.strip()} {a.strip()}" if q else (a or "").strip()
    if entry.get("kind") == "local_place" and entry.get("source_url"):
        text += f" (source: {entry.get('source_label') or entry['source_url']})"
    return text


def fact_row(entry: dict, language: str = "en") -> dict:
    """The kb_facts row shape (CONTRACTS 13)."""
    return {
        "source": "kb_entry",
        "text": entry_text(entry, language),
        "fact_key": entry.get("fact_key"),
        "value": entry.get("fact_value"),
        "entry_id": entry["id"],
        "approved_at": entry.get("approved_at"),
    }


def legacy_paragraphs(cur: Any, hotel_slug: str) -> list[str]:
    cur.execute("SELECT knowledge_base FROM hotels WHERE slug = %s", (hotel_slug,))
    text = ((cur.fetchone() or {}).get("knowledge_base") or "").replace("\r\n", "\n")
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def facts(cur: Any, hotel_slug: str, *, language: str = "en", keyed_only: bool = False) -> list[dict]:
    """kb_facts: approved entries; in legacy_text mode the legacy paragraphs
    come first (they carry no key, entry or approval). keyed_only gives the
    approved structured facts Visibility diffs against the web."""
    rows = [fact_row(e, language) for e in approved_entries(cur, hotel_slug, keyed_only=keyed_only)]
    if keyed_only or kb_mode(cur, hotel_slug) != "legacy_text":
        return rows
    legacy = [
        {"source": "knowledge_base", "text": p, "fact_key": None, "value": None, "entry_id": None, "approved_at": None}
        for p in legacy_paragraphs(cur, hotel_slug)
    ]
    return legacy + rows


def search_approved(cur: Any, hotel_slug: str, query: str, *, language: str = "en", limit: int = 5) -> list[dict]:
    """search_hotel_info: Postgres FTS over approved entries (english and
    simple configs, so Arabic matches on exact words). Falls back to a
    substring match when FTS finds nothing (short or odd queries)."""
    query = (query or "").strip()[:200]
    if not query:
        return []
    today = _today(cur, hotel_slug)
    base = """
        FROM cc_kb_entries e WHERE e.hotel_slug = %s AND e.status = 'approved'
          AND (e.valid_from IS NULL OR e.valid_from <= %s) AND (e.valid_to IS NULL OR e.valid_to >= %s)
    """
    cur.execute(
        f"""
        SELECT e.*, ts_rank(e.search, plainto_tsquery('english', %s) || plainto_tsquery('simple', %s)) AS rank
        {base} AND e.search @@ (plainto_tsquery('english', %s) || plainto_tsquery('simple', %s))
        ORDER BY rank DESC, e.id LIMIT %s
        """,
        (query, query, hotel_slug, today, today, query, query, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        cur.execute(
            f"""
            SELECT e.* {base} AND (e.question_en ILIKE %s OR e.answer_en ILIKE %s OR e.question_ar ILIKE %s OR e.answer_ar ILIKE %s)
            ORDER BY e.id LIMIT %s
            """,
            (hotel_slug, today, today, like, like, like, like, limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return [{"entry_id": r["id"], "kb_ref": f"KB:{r['id']}", "text": entry_text(r, language)} for r in rows]


# ─── Legacy-text import ───
# The owner's old free text (or pasted text) is split into pending entries,
# by the AI when it is on and by paragraphs otherwise. Approving the import
# approves its pending entries in one step and flips the hotel to structured.

IMPORT_TEXT_MAX = 60_000
IMPORT_MAX_ENTRIES = 200
_SECTION_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wifi", ("wifi", "wi-fi", "internet", "password", "واي فاي", "انترنت")),
    ("checkout", ("check-in", "check in", "checkin", "check-out", "check out", "checkout", "late checkout")),
    ("breakfast", ("breakfast", "إفطار", "الفطور")),
    ("pool", ("pool", "gym", "fitness", "swimming", "مسبح")),
    ("parking", ("parking", "valet", "car park", "موقف")),
    ("emergency", ("emergency", "fire", "first aid", "evacuat", "doctor", "طوارئ")),
    ("pets", ("pet", "dog", "cat ", "animal")),
    ("smoking", ("smok", "cigar", "vape", "shisha", "تدخين")),
    ("spa", ("spa", "massage", "sauna")),
    ("dining", ("restaurant", "dining", "room service", "menu", "bar ", "cafe", "lunch", "dinner")),
    ("transport", ("airport", "taxi", "shuttle", "metro", "transfer")),
    ("local", ("nearby", "mall", "beach", "museum", "attraction", "souk")),
    ("policies", ("policy", "policies", "visitor", "id card", "passport", "deposit")),
)


def guess_section(text: str) -> str:
    low = (text or "").lower()
    for key, words in _SECTION_WORDS:
        if any(w in low for w in words):
            return key
    return "general"


def start_import(cur: Any, hotel_slug: str, *, source: str = "legacy_text", text: str | None = None, actor: Any = None) -> dict:
    """Queue an import. legacy_text reads hotels.knowledge_base at run time;
    text imports the pasted text (kept only on the job payload)."""
    if source not in ("legacy_text", "text"):
        raise bad_request("source must be legacy_text or text", "invalid_import_source")
    if source == "text":
        text = (text or "").strip()
        if not text:
            raise bad_request("text is required", "import_text_required")
        if len(text) > IMPORT_TEXT_MAX:
            raise bad_request(f"text is over {IMPORT_TEXT_MAX} characters", "import_text_too_long")
    elif not legacy_paragraphs(cur, hotel_slug):
        raise conflict("this hotel has no legacy knowledge text to import", "no_legacy_text")
    cur.execute(
        """
        INSERT INTO cc_kb_imports (hotel_slug, source, status, text_chars, requested_by)
        VALUES (%s, %s, 'queued', %s, %s) RETURNING *
        """,
        (hotel_slug, source, len(text or ""), _user_id(actor)),
    )
    row = dict(cur.fetchone())
    from clefs import jobs

    jobs.enqueue(IMPORT_TASK, {"import_id": row["id"], "text": text if source == "text" else None}, hotel_slug=hotel_slug, cur=cur)
    audit(cur, actor, "concierge.kb.import", "cc_kb_import", row["id"], after={"source": source}, hotel_slug=hotel_slug)
    return row


def _split_with_ai(hotel_slug: str, text: str) -> list[dict] | None:
    """The AI split, or None when AI is off or fails (the caller falls back)."""
    from pydantic import BaseModel

    from clefs import ai

    class _Entry(BaseModel):
        section_key: str
        kind: str
        question_en: str
        answer_en: str
        question_ar: str | None
        answer_ar: str | None
        fact_key: str | None
        fact_value: str | None

    class _Out(BaseModel):
        entries: list[_Entry]

    system = (
        "Split a hotel's information text into short knowledge-base entries for a guest concierge. "
        "Each entry is one fact, answered in one or two plain sentences, with the question a guest would ask. "
        f"section_key is one of: {', '.join(k for k, *_ in STANDARD_SECTIONS)}. kind is fact, faq or policy. "
        f"fact_key is one of {', '.join(FACT_KEYS)} only when the entry states exactly that fact, else null; "
        "fact_value is then the plain value (15:00, free, paid, none, true, false). "
        "Copy facts exactly; never add, guess or translate prices. Arabic fields only if the text has Arabic."
    )
    try:
        out = ai.structured("concierge.kb_import", system=system, input=text[:IMPORT_TEXT_MAX], schema=_Out, hotel_slug=hotel_slug)
    except Exception as exc:  # AIDisabled, AIError: the paragraph split still works
        log.info("kb import AI split unavailable for %s: %s", hotel_slug, exc)
        return None
    return [e.model_dump() for e in out.entries]


def _split_paragraphs(text: str) -> list[dict]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    return [{"section_key": guess_section(p), "kind": "fact", "answer_en": p} for p in parts]


def _import_entry(cur: Any, hotel_slug: str, item: dict, import_id: int, source: str) -> dict | None:
    """One split item as a pending entry; an item the validators refuse (a
    bad fact value, say) is kept as plain text rather than dropped."""
    data = {
        "section_key": item.get("section_key") if item.get("section_key") in {k for k, *_ in STANDARD_SECTIONS} else "general",
        "kind": item.get("kind") if item.get("kind") in ("fact", "faq", "policy") else "fact",
        "question_en": item.get("question_en"),
        "answer_en": item.get("answer_en"),
        "question_ar": item.get("question_ar"),
        "answer_ar": item.get("answer_ar"),
        "fact_key": item.get("fact_key") if item.get("fact_key") in FACT_KEYS else None,
        "fact_value": item.get("fact_value"),
    }
    if not (data["answer_en"] or "").strip():
        return None
    cur.execute("SAVEPOINT cc_kb_import_item")
    try:
        row = create_entry(cur, hotel_slug, data, source=source, import_id=import_id)
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT cc_kb_import_item")
        data.update(fact_key=None, fact_value=None)
        row = create_entry(cur, hotel_slug, data, source=source, import_id=import_id)
    else:
        cur.execute("RELEASE SAVEPOINT cc_kb_import_item")
    return row


def run_import(cur: Any, hotel_slug: str, import_id: int, *, text: str | None = None) -> dict:
    cur.execute("SELECT * FROM cc_kb_imports WHERE id = %s AND hotel_slug = %s FOR UPDATE", (import_id, hotel_slug))
    imp = cur.fetchone()
    if imp is None or imp["status"] not in ("queued", "running", "failed"):
        return {"skipped": True}
    if imp["source"] == "legacy_text":
        text = "\n\n".join(legacy_paragraphs(cur, hotel_slug))
    text = (text or "").strip()
    items = _split_with_ai(hotel_slug, text) if text else []
    method = "ai"
    if not items:
        items, method = _split_paragraphs(text), "paragraphs"
    source = "legacy_import" if imp["source"] == "legacy_text" else "text_import"
    created = 0
    for item in items[:IMPORT_MAX_ENTRIES]:
        if _import_entry(cur, hotel_slug, item, import_id, source) is not None:
            created += 1
    cur.execute(
        """
        UPDATE cc_kb_imports SET status = 'done', method = %s, entries_created = %s, text_chars = %s,
               finished_at = NOW(), error = NULL
        WHERE id = %s RETURNING *
        """,
        (method, created, len(text), import_id),
    )
    return dict(cur.fetchone())


def approve_import(cur: Any, hotel_slug: str, import_id: int, *, actor: Any) -> dict:
    """Approve every pending entry of a finished import, then flip the hotel
    to structured: the owner has now read and accepted the whole KB."""
    cur.execute("SELECT * FROM cc_kb_imports WHERE id = %s AND hotel_slug = %s FOR UPDATE", (import_id, hotel_slug))
    imp = cur.fetchone()
    if imp is None:
        raise not_found("import not found", "kb_import_not_found")
    if imp["status"] == "approved":
        return dict(imp)
    if imp["status"] != "done":
        raise conflict("the import has not finished", "kb_import_not_done")
    cur.execute(
        "SELECT id FROM cc_kb_entries WHERE hotel_slug = %s AND import_id = %s AND status = 'pending' ORDER BY id",
        (hotel_slug, import_id),
    )
    ids = [r["id"] for r in cur.fetchall()]
    for entry_id in ids:
        approve_entry(cur, hotel_slug, entry_id, actor=actor)
    cur.execute(
        "UPDATE cc_kb_imports SET status = 'approved', approved_at = NOW(), approved_by = %s WHERE id = %s RETURNING *",
        (_user_id(actor), import_id),
    )
    out = dict(cur.fetchone())
    set_kb_mode(cur, hotel_slug, "structured", actor=actor)
    out["approved_entries"] = len(ids)
    return out


def list_imports(cur: Any, hotel_slug: str, *, limit: int = 20) -> list[dict]:
    cur.execute(
        """
        SELECT id, source, status, method, text_chars, entries_created, error, created_at, finished_at, approved_at
        FROM cc_kb_imports WHERE hotel_slug = %s ORDER BY created_at DESC, id DESC LIMIT %s
        """,
        (hotel_slug, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def _register_import_task() -> str:
    from clefs import jobs

    @jobs.task("concierge.kb_import", max_attempts=3, backoff_s=60, timeout_s=110)
    def kb_import_task(ctx: Any, payload: dict) -> dict:
        return run_import(ctx.cur, ctx.hotel_slug, int(payload["import_id"]), text=payload.get("text"))

    return "concierge.kb_import"


IMPORT_TASK = _register_import_task()


# ─── Unknowns ───
# A question the concierge could not answer from approved knowledge. One
# cluster per distinct question (normalised text, fuzzy-matched against open
# clusters on insert); 5 or more in 7 days raise concierge.kb_gap with the
# suggested question, so the owner fills the gap guests actually hit.

UNKNOWN_MATCH_SCORE = 85
KB_GAP_THRESHOLD = 5
_STOP = frozenset(
    "a an the is are do does did you your we i me my can could would will to of in on at for and or "
    "what when where how which there this that it be have has please any some".split()
)


def normalize_question(text: str) -> str:
    low = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return " ".join(w for w in low.split() if w not in _STOP)[:300]


def _cluster_key(norm: str) -> str:
    return " ".join(sorted(set(norm.split())))[:200] or "blank"


def record_unknown(cur: Any, hotel_slug: str, *, text: str, language: str | None = None, session_id: Any = None) -> dict:
    from rapidfuzz import fuzz

    text = (text or "").strip()[:1000]
    norm = normalize_question(text)
    key = _cluster_key(norm)
    cur.execute(
        """
        SELECT id, cluster_key, title FROM cc_kb_unknowns
        WHERE hotel_slug = %s AND status = 'open' AND last_at > NOW() - INTERVAL '30 days'
        ORDER BY last_at DESC LIMIT 200
        """,
        (hotel_slug,),
    )
    match = None
    for row in cur.fetchall():
        if row["cluster_key"] == key or fuzz.token_set_ratio(row["cluster_key"], key) >= UNKNOWN_MATCH_SCORE:
            match = row
            break
    lang = (language or "")[:8] or None
    if match is None:
        cur.execute(
            """
            INSERT INTO cc_kb_unknowns (hotel_slug, cluster_key, title, samples, languages, count)
            VALUES (%s, %s, %s, %s, %s, 1) RETURNING id
            """,
            (hotel_slug, key, text[:200] or "(no text)", Json([text[:300]]), [lang] if lang else []),
        )
        uid = cur.fetchone()["id"]
    else:
        uid = match["id"]
        cur.execute(
            """
            UPDATE cc_kb_unknowns SET count = count + 1, last_at = NOW(),
                   samples = CASE WHEN jsonb_array_length(samples) < 5 THEN samples || %s ELSE samples END,
                   languages = CASE WHEN %s::text IS NULL OR %s = ANY(languages) THEN languages ELSE array_append(languages, %s) END
            WHERE id = %s
            """,
            (Json([text[:300]]), lang, lang, lang, uid),
        )
    cur.execute(
        """
        INSERT INTO cc_kb_unknown_events (hotel_slug, unknown_id, session_id, text, text_norm, language)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (hotel_slug, uid, str(session_id) if session_id else None, text, norm, lang),
    )
    _maybe_kb_gap(cur, hotel_slug, uid)
    return get_unknown(cur, hotel_slug, uid) or {"id": uid}


def get_unknown(cur: Any, hotel_slug: str, unknown_id: int) -> dict | None:
    cur.execute(
        """
        SELECT u.id, u.title, u.samples, u.languages, u.count, u.first_at, u.last_at, u.status, u.kb_entry_id,
               (SELECT COUNT(*) FROM cc_kb_unknown_events ev WHERE ev.unknown_id = u.id AND ev.at > NOW() - INTERVAL '7 days') AS count_7d
        FROM cc_kb_unknowns u WHERE u.hotel_slug = %s AND u.id = %s
        """,
        (hotel_slug, unknown_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def list_unknowns(cur: Any, hotel_slug: str, *, status: str = "open", limit: int = 100) -> list[dict]:
    cur.execute(
        """
        SELECT u.id, u.title, u.samples, u.languages, u.count, u.first_at, u.last_at, u.status, u.kb_entry_id,
               (SELECT COUNT(*) FROM cc_kb_unknown_events ev WHERE ev.unknown_id = u.id AND ev.at > NOW() - INTERVAL '7 days') AS count_7d
        FROM cc_kb_unknowns u WHERE u.hotel_slug = %s AND u.status = %s
        ORDER BY u.count DESC, u.last_at DESC LIMIT %s
        """,
        (hotel_slug, status, max(1, min(limit, 500))),
    )
    return [dict(r) for r in cur.fetchall()]


def answer_unknown(cur: Any, hotel_slug: str, unknown_id: int, data: dict, *, actor: Any, approve: bool = False) -> dict:
    """Turn an unknown into a KB entry (pending unless approved now)."""
    row = get_unknown(cur, hotel_slug, unknown_id)
    if row is None:
        raise not_found("unknown question not found", "kb_unknown_not_found")
    data = {"question_en": row["title"], "kind": "faq", **data}
    data.setdefault("section_key", guess_section(f"{data.get('question_en') or ''} {data.get('answer_en') or ''}"))
    entry = create_entry(cur, hotel_slug, data, source="unknowns", actor=actor)
    if approve:
        entry = approve_entry(cur, hotel_slug, entry["id"], actor=actor)
    cur.execute(
        "UPDATE cc_kb_unknowns SET status = 'answered', answered_at = NOW(), kb_entry_id = %s WHERE id = %s",
        (entry["id"], unknown_id),
    )
    _resolve_kb_gap(cur, hotel_slug, unknown_id)
    return entry


def dismiss_unknown(cur: Any, hotel_slug: str, unknown_id: int, *, actor: Any) -> dict:
    cur.execute(
        "UPDATE cc_kb_unknowns SET status = 'dismissed', dismissed_at = NOW() WHERE hotel_slug = %s AND id = %s RETURNING id",
        (hotel_slug, unknown_id),
    )
    if cur.fetchone() is None:
        raise not_found("unknown question not found", "kb_unknown_not_found")
    _resolve_kb_gap(cur, hotel_slug, unknown_id)
    return get_unknown(cur, hotel_slug, unknown_id) or {}


KB_GAP_KIND = "concierge.kb_gap"


def _maybe_kb_gap(cur: Any, hotel_slug: str, unknown_id: int) -> None:
    row = get_unknown(cur, hotel_slug, unknown_id)
    if row is None or row["count_7d"] < KB_GAP_THRESHOLD:
        return
    from clefs.core import signals

    cur.execute("SAVEPOINT cc_kb_gap")
    try:
        signals.upsert_signal(
            cur,
            hotel_slug,
            signals.SignalIn(
                module="concierge",
                kind=KB_GAP_KIND,
                dedupe_key=f"{KB_GAP_KIND}:{unknown_id}",
                title=f"Guests keep asking: {row['title'][:120]}",
                summary=f"{row['count_7d']} guests asked this in the last 7 days and the concierge had no approved answer.",
                section="service_risk",
                cadence="weekly",
                severity=2,
                confidence=0.9,
                evidence=[signals.Evidence(kind="quote", label="Asked", value=s) for s in (row["samples"] or [])[:3]],
                subject_type="cc_kb_unknown",
                subject_id=str(unknown_id),
            ),
        )
    except Exception as exc:  # a signal must never break the guest's turn
        cur.execute("ROLLBACK TO SAVEPOINT cc_kb_gap")
        log.warning("kb_gap signal failed for %s: %s", hotel_slug, exc)
    else:
        cur.execute("RELEASE SAVEPOINT cc_kb_gap")


def _resolve_kb_gap(cur: Any, hotel_slug: str, unknown_id: int) -> None:
    from clefs.core import signals

    cur.execute("SAVEPOINT cc_kb_gap")
    try:
        signals.resolve_signal(cur, hotel_slug, f"{KB_GAP_KIND}:{unknown_id}", resolution="kb_answered")
    except Exception as exc:
        cur.execute("ROLLBACK TO SAVEPOINT cc_kb_gap")
        log.warning("kb_gap resolve failed for %s: %s", hotel_slug, exc)
    else:
        cur.execute("RELEASE SAVEPOINT cc_kb_gap")


def _register_signal_kind() -> None:
    from clefs.core import signals

    signals.register_signal_kind(
        KB_GAP_KIND, label="Concierge knowledge gap", default_owner_role="gm", section="service_risk", cadence="weekly"
    )


_register_signal_kind()
