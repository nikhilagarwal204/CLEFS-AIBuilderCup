"""Mapping sources in order: a saved template for this layout, the static
alias map, then an AI proposal a person confirms (02 spec 2.2 steps 3-4).

Templates are keyed by the header fingerprint, so one export layout maps the
same way every month without anyone looking at it again. The AI sees the
headers and at most five sample rows with guest names and contact values
masked, never the file.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from clefs import ai, db
from clefs.core import clock
from clefs.core.ingest.kinds import schema_for
from clefs.core.ingest.sniff import normalize_header

AI_TASK = "ingest.map_columns"
SAMPLE_ROWS = 5
MASK = "<masked>"
# Kinds whose rows carry guests or staff: their name columns are masked.
PERSON_KINDS = ("OTA_STATEMENT", "OTA_PAYOUT", "VCC_REPORT", "CREDIT_NOTE", "PMS_RESERVATIONS", "PMS_FOLIO",
                "ISSUANCE", "BOOKING_ENGINE_ATTRIBUTION", "BANK")
PERSON_WORDS = ("name", "guest", "customer", "booker", "traveler", "traveller", "staff", "attendant", "issued by",
                "counterparty", "payer", "remitter", "beneficiary")
CONTACT_WORDS = ("email", "e mail", "phone", "mobile", "telephone", "contact", "address", "passport", "card number",
                 "whatsapp")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_CARD = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b")
# Two to five words of letters (any script), no digits: "Jane Doe", "DOE, Jane",
# "محمد علي". Room types and rate plans match too; masking them costs the AI
# a little context, never a guest their privacy.
_NAME = re.compile(r"^[^\W\d_]+(?:[ ,.'-]+[^\W\d_]+){1,4}\.?$")
NAME_SHARE = 0.6


# ─── Templates ───


def find_template(cur: Any, hotel_slug: str | None, fingerprint: str, *, kind: str | None = None,
                  source_code: str | None = None) -> dict | None:
    """The newest active template for this layout: the hotel's own first,
    then a global one Clefs staff seeded."""
    cur.execute(
        """
        SELECT * FROM mapping_templates
         WHERE header_fingerprint = %s AND active
           AND (hotel_slug = %s OR hotel_slug IS NULL)
           AND (%s::text IS NULL OR kind = %s) AND (%s::text IS NULL OR source_code = %s)
         ORDER BY (hotel_slug IS NULL), version DESC, id DESC
         LIMIT 1
        """,
        (fingerprint, hotel_slug, kind, kind, source_code, source_code),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def save_template(cur: Any, hotel_slug: str | None, *, kind: str, source_code: str, fingerprint: str,
                  headers: list[str], mapping: dict, transforms: dict, origin: str, confirmed_by: int | None) -> dict:
    """Save a confirmed mapping. The same mapping again only counts a use;
    a different one becomes the next version and retires the old."""
    cur.execute(
        """
        SELECT * FROM mapping_templates
         WHERE COALESCE(hotel_slug, '') = COALESCE(%s, '') AND source_code = %s AND header_fingerprint = %s
         ORDER BY version DESC LIMIT 1 FOR UPDATE
        """,
        (hotel_slug, source_code, fingerprint),
    )
    last = cur.fetchone()
    if last and last["active"] and last["mapping"] == mapping and (last["transforms"] or {}) == transforms:
        cur.execute(
            "UPDATE mapping_templates SET uses = uses + 1, last_used_at = %s WHERE id = %s RETURNING *",
            (clock.now(), last["id"]),
        )
        return dict(cur.fetchone())
    if last:
        cur.execute(
            "UPDATE mapping_templates SET active = FALSE WHERE COALESCE(hotel_slug, '') = COALESCE(%s, '') "
            "AND source_code = %s AND header_fingerprint = %s",
            (hotel_slug, source_code, fingerprint),
        )
    now = clock.now()
    cur.execute(
        """
        INSERT INTO mapping_templates (hotel_slug, kind, source_code, header_fingerprint, headers, mapping,
                                       transforms, origin, confirmed_by, confirmed_at, version, uses, last_used_at,
                                       created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
        RETURNING *
        """,
        (hotel_slug, kind, source_code, fingerprint, db.Json(headers), db.Json(mapping), db.Json(transforms),
         origin, confirmed_by, now, (last["version"] + 1) if last else 1, now, now),
    )
    return dict(cur.fetchone())


def touch_template(cur: Any, template_id: int) -> None:
    cur.execute("UPDATE mapping_templates SET uses = uses + 1, last_used_at = %s WHERE id = %s",
                (clock.now(), template_id))


def list_templates(cur: Any, hotel_slug: str, *, include_inactive: bool = False) -> list[dict]:
    cur.execute(
        """
        SELECT id, hotel_slug, kind, source_code, header_fingerprint, headers, mapping, transforms, origin,
               confirmed_by, confirmed_at, version, active, uses, last_used_at, created_at
          FROM mapping_templates
         WHERE (hotel_slug = %s OR hotel_slug IS NULL) AND (%s OR active)
         ORDER BY kind, source_code, version DESC
        """,
        (hotel_slug, include_inactive),
    )
    return [dict(r) for r in cur.fetchall()]


def deactivate_template(cur: Any, hotel_slug: str, template_id: int) -> bool:
    """A hotel may retire its own templates, never a global one."""
    cur.execute("UPDATE mapping_templates SET active = FALSE WHERE id = %s AND hotel_slug = %s",
                (template_id, hotel_slug))
    return cur.rowcount > 0


# ─── Masking ───


def _person_header(header: str) -> bool:
    n = normalize_header(header)
    return any(f" {w} " in f" {n} " for w in PERSON_WORDS)


def _contact_header(header: str) -> bool:
    n = normalize_header(header)
    return any(f" {w} " in f" {n} " for w in CONTACT_WORDS)


def looks_like_name(v: Any) -> bool:
    return isinstance(v, str) and bool(_NAME.match(v.strip()))


def mask_value(v: Any) -> Any:
    """Contact-looking text inside any cell (an email in a remarks column)."""
    if not isinstance(v, str):
        return v
    v = _EMAIL.sub(MASK, v)
    v = _CARD.sub(MASK, v)
    return _PHONE.sub(lambda m: MASK if sum(c.isdigit() for c in m.group()) >= 9 else m.group(), v)


def mask_samples(kind: str, headers: list[str], rows: list[dict], *, mapping: dict | None = None) -> list[dict]:
    """At most SAMPLE_ROWS rows with guest (or staff) names and contact
    values replaced. Name columns of finance kinds stay: "Account name" is
    what the AI must read there."""
    schema = schema_for(kind)
    sensitive = {mapping[f] for f in (mapping or {}) if f == "guest_name" or f in schema.contact_fields}
    if kind in PERSON_KINDS:
        # A name column under an unhelpful header ("Pax", "Lead"): judged by
        # its values. Mapped columns are known not to be names.
        mapped = set((mapping or {}).values())
        for h in headers:
            vals = [r.get(h) for r in rows[:SAMPLE_ROWS] if r.get(h) not in (None, "")]
            if h not in mapped and vals and sum(1 for v in vals if looks_like_name(v)) >= NAME_SHARE * len(vals):
                sensitive.add(h)
    out = []
    for r in rows[:SAMPLE_ROWS]:
        masked = {}
        for h in headers:
            v = r.get(h)
            if v is None:
                continue
            hide = h in sensitive or _contact_header(h) or (kind in PERSON_KINDS and _person_header(h))
            masked[h] = MASK if hide else mask_value(v)
        out.append(masked)
    return out


# ─── AI proposal ───


class AIColumn(BaseModel):
    canonical: str
    header: str
    transform: Literal["none", "date_fmt", "decimal_sep", "sign_flip", "pct_to_ratio"]
    # "DD/MM/YYYY" for date_fmt, "," or "." for decimal_sep, "true" for the
    # two flags, "" for none (strict schemas have no optional fields).
    transform_value: str
    confidence: float = Field(ge=0, le=1)


class AIMapping(BaseModel):
    mappings: list[AIColumn]
    unmapped: list[str]


SYSTEM = (
    "You map the columns of a hotel data export to canonical fields. You get the file kind, the canonical "
    "fields with their types (required ones marked), the column headers and a few sample rows in which "
    "personal data is masked. Map each canonical field to at most one header, and each header to at most one "
    "field, using only headers from the list. Use a transform only when needed: date_fmt with a pattern like "
    "DD/MM/YYYY when dates are ambiguous, decimal_sep ',' for decimal commas, sign_flip 'true' when amounts "
    "are printed with the opposite sign, pct_to_ratio 'true' when a percentage column holds 15 meaning 15%. "
    "List canonical fields you cannot map in unmapped. Confidence is 0 to 1."
)


def _transform_entry(col: AIColumn) -> dict | None:
    if col.transform == "none":
        return None
    if col.transform in ("sign_flip", "pct_to_ratio"):
        return {col.transform: col.transform_value.strip().lower() in ("true", "1", "yes")}
    if col.transform == "decimal_sep":
        return {"decimal_sep": "," if "," in col.transform_value else "."}
    return {"date_fmt": col.transform_value.strip()[:40]} if col.transform_value.strip() else None


def suggest_mapping(*, hotel_slug: str | None, kind: str, headers: list[str], rows: list[dict],
                    current: dict[str, str] | None = None, min_confidence: float = 0.5) -> dict | None:
    """Ask the AI for the fields the alias map left open. Returns
    {mappings:[{canonical, header, transform, confidence}], unmapped,
    mapping, transforms} where `mapping` merges the current mapping with the
    accepted proposals, or None when AI is off or failed (the person maps by
    hand). The alias mapping is kept: the AI only fills gaps."""
    schema = schema_for(kind)
    current = dict(current or {})
    fields = [{"name": f.name, "type": f.type, "required": f.required, "mapped_to": current.get(f.name)}
              for f in schema.fields]
    payload = {"kind": kind, "fields": fields, "headers": headers,
               "sample_rows": mask_samples(kind, headers, rows, mapping=current)}
    try:
        out = ai.structured(AI_TASK, system=SYSTEM, input=json.dumps(payload, ensure_ascii=False, default=str),
                            schema=AIMapping, hotel_slug=hotel_slug, max_output_tokens=2000)
    except (ai.AIDisabled, ai.AIError):
        return None
    used = set(current.values())
    mapping, transforms, accepted = dict(current), {}, []
    for col in out.mappings:
        if col.canonical not in schema.names or col.header not in headers:
            continue  # the model named something that is not there
        entry = {"canonical": col.canonical, "header": col.header, "transform": _transform_entry(col),
                 "confidence": round(col.confidence, 3)}
        accepted.append(entry)
        if col.canonical in mapping or col.header in used or col.confidence < min_confidence:
            continue
        mapping[col.canonical] = col.header
        used.add(col.header)
        if entry["transform"]:
            transforms[col.canonical] = entry["transform"]
    unmapped = [f for f in schema.names if f not in mapping]
    return {"mappings": accepted, "unmapped": unmapped, "mapping": mapping, "transforms": transforms,
            "model_unmapped": [u for u in out.unmapped if u in schema.names]}
