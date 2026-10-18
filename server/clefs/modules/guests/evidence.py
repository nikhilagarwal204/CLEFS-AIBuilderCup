"""
Consent evidence for own-contact captures (04 spec 3.4, privacy-reviews A3).

A registration-card capture carries minimal evidence: either a crop of the
card's contact-and-consent block only, or a staff attestation plus the
guest's signature image. ID fields (passport or Emirates ID number,
nationality, date of birth) are never stored: Clefs must not become a store
of ID scans. So an evidence file is refused when it is over 300 KB or when
text detection finds a machine-readable zone (MRZ: "P<" / "ID" lines padded
with "<" fillers).

Text detection: text-like files (text/*, PDF) are scanned directly; images
go through clefs.ai when it is enabled. With AI off a crop cannot be
checked, so it is refused (fail closed) and the desk uses the attestation
route, whose signature image carries no ID fields by construction.

Evidence files are a high-risk PII class (registered in module.py with
core.privacy.register_pii) and are deleted when the contact is forgotten.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel

from clefs import ai, files
from clefs.core.errors import ClefsError

log = logging.getLogger("clefs.guests.evidence")

MAX_EVIDENCE_BYTES = 300 * 1024
EVIDENCE_PURPOSE = "guests.contact_evidence"
MAX_EVIDENCE_JSON = 2000  # characters of free-form evidence kept on a point

# A TD3 passport line starts "P<" + issuing state; TD1/TD2 ID cards start
# "I" or "ID". Real MRZ lines are 30-44 characters of [A-Z0-9<] with "<<<"
# fillers; requiring the filler run keeps ordinary prose from matching.
MRZ_RE = re.compile(r"(?:P<|ID|I<)[A-Z0-9<]{3,}<<<[A-Z0-9<]*")


class MrzCheck(BaseModel):
    has_mrz: bool
    has_id_document: bool


def text_has_mrz(text: str) -> bool:
    compact = re.sub(r"[ \t]", "", (text or "").upper())
    return any(len(m.group(0)) >= 20 for m in MRZ_RE.finditer(compact))


def _looks_like_id(cur: Any, hotel_slug: str, row: dict, data: bytes) -> bool:
    ctype = (row.get("content_type") or "").lower()
    if ctype.startswith("text/") or ctype == "application/pdf":
        return text_has_mrz(data.decode("latin-1", "replace"))
    if not ctype.startswith("image/"):
        raise ClefsError(422, "Evidence must be an image, a PDF or text", "evidence_type")
    if not ai.enabled():
        raise ClefsError(
            422,
            "This card image cannot be checked for ID fields right now; use a staff attestation with the guest's signature",
            "evidence_unverified",
        )
    out = ai.structured(
        "guests.evidence_mrz",
        system=(
            "You check consent evidence photos for identity-document content. Answer has_mrz=true when the image "
            "shows a machine-readable zone (lines of capitals and digits padded with '<'), and has_id_document=true "
            "when it shows a passport or national ID card page. Answer only about what is visible."
        ),
        input="Does this image show a machine-readable zone or an identity document?",
        schema=MrzCheck,
        hotel_slug=hotel_slug,
        file_ids=(str(row["id"]),),
    )
    return bool(out.has_mrz or out.has_id_document)


def check_file(cur: Any, hotel_slug: str, file_id: Any, *, needs_id_check: bool) -> dict:
    """The file's row once it passes the size and MRZ rules; raises 422."""
    row = files.file_row(cur, hotel_slug, str(file_id))
    if int(row.get("size_bytes") or 0) > MAX_EVIDENCE_BYTES:
        raise ClefsError(422, "Evidence files must be 300 KB or smaller", "evidence_too_large")
    if needs_id_check:
        _, data = files.get_file(cur, hotel_slug, str(file_id))
        if _looks_like_id(cur, hotel_slug, row, data):
            raise ClefsError(422, "Evidence must not show ID document fields; crop to the contact and consent block", "evidence_has_id")
    return row


def check(cur: Any, hotel_slug: str, capture_source: str, evidence: dict | None) -> dict:
    """Validated evidence to store on the point (ids and flags only)."""
    ev = dict(evidence or {})
    if capture_source == "registration_card":
        kind = ev.get("kind")
        if kind == "card_crop":
            if not ev.get("file_id"):
                raise ClefsError(422, "A card crop needs its file_id", "evidence_required")
            check_file(cur, hotel_slug, ev["file_id"], needs_id_check=True)
        elif kind == "staff_attestation":
            if not ev.get("signature_file_id"):
                raise ClefsError(422, "A staff attestation needs the guest's signature image", "evidence_required")
            check_file(cur, hotel_slug, ev["signature_file_id"], needs_id_check=False)
        else:
            raise ClefsError(422, "Registration-card evidence is a card_crop or a staff_attestation", "evidence_required")
    if len(repr(ev)) > MAX_EVIDENCE_JSON:
        raise ClefsError(422, "Evidence is too large", "evidence_too_large")
    return ev


def file_ids(evidence: dict | None) -> list[str]:
    ev = evidence or {}
    return [str(ev[k]) for k in ("file_id", "signature_file_id") if ev.get(k)]


def delete_files(cur: Any, hotel_slug: str, evidence: dict | None) -> int:
    n = 0
    for fid in file_ids(evidence):
        try:
            files.delete_file(cur, hotel_slug, fid)
            n += 1
        except ClefsError:
            pass  # already purged or never this hotel's: nothing left to delete
    return n
