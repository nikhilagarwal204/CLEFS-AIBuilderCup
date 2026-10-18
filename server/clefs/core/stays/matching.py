"""Linking an OTA claim to the PMS stay it is about (02 spec 2.1).

A claim is one line of an OTA statement, payout or VCC report, stored in
the owning module's table (Profit Guard); the link lives in
`reservation_matches`, keyed (hotel, line_kind, statement_line_id).

1. Exact: the normalised OTA id against reservations.ota_res_id_norm. A
   multi-room booking split into several PMS rows links all of them.
2. Fuzzy: 0.45 name (token-sort Jaro-Winkler) + 0.25 check-in (same day 1.0,
   one day off 0.6) + 0.15 nights equal + 0.15 amount within 5%.
   >= 0.85 links, 0.65 to 0.85 goes to the review queue, below is UNMATCHED.

A human decision (MANUAL link or REJECTED) is never overwritten by a re-run.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs.audit import audit
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.stays.contacts import DIRECT_CHANNELS, OTA_CHANNELS
from clefs.core.stays.normalize import name_similarity, normalize_name, normalize_ota_res_id

AUTO_LINK = Decimal("0.85")
REVIEW = Decimal("0.65")
WEIGHTS = {"name": Decimal("0.45"), "check_in": Decimal("0.25"), "nights": Decimal("0.15"), "amount": Decimal("0.15")}
AMOUNT_TOLERANCE = Decimal("0.05")
# Two different OTA ids on both sides: never auto-linked on the other fields.
CONFLICTING_ID_CAP = Decimal("0.84")
MAX_CANDIDATES = 5
LINE_KIND = "ota_statement_line"


@dataclass
class Claim:
    """What the OTA says (amount in minor units of `currency`)."""

    ota_code: str | None
    ota_res_id: str | None = None
    guest_name: str | None = None
    check_in: date | None = None
    nights: int | None = None
    amount_minor: int | None = None
    currency: str | None = None

    def norm_id(self) -> str | None:
        return normalize_ota_res_id(self.ota_code, self.ota_res_id)


# ─── Scoring ───


def _amount_close(claim: Claim, res: dict) -> bool:
    if claim.amount_minor is None or not claim.amount_minor:
        return False
    if claim.currency and res.get("currency") and claim.currency.upper() != str(res["currency"]).strip().upper():
        return False
    amt = Decimal(abs(int(claim.amount_minor)))
    for key in ("gross_minor", "room_revenue_minor"):
        v = res.get(key)
        if v and abs(Decimal(int(v)) - amt) <= AMOUNT_TOLERANCE * amt:
            return True
    return False


def score(claim: Claim, res: dict) -> tuple[Decimal, dict[str, float]]:
    """(score, parts) of one reservation row against a claim."""
    parts = {"name": 0.0, "check_in": 0.0, "nights": 0.0, "amount": 0.0}
    parts["name"] = name_similarity(claim.guest_name, res.get("guest_name_norm") or res.get("guest_name"))
    if claim.check_in and res.get("check_in"):
        gap = abs((res["check_in"] - claim.check_in).days)
        parts["check_in"] = 1.0 if gap == 0 else (0.6 if gap == 1 else 0.0)
    if claim.nights is not None and res.get("nights") is not None:
        parts["nights"] = 1.0 if int(claim.nights) == int(res["nights"]) else 0.0
    parts["amount"] = 1.0 if _amount_close(claim, res) else 0.0
    total = sum((WEIGHTS[k] * Decimal(str(v)) for k, v in parts.items()), Decimal(0))
    theirs, ours = claim.norm_id(), res.get("ota_res_id_norm")
    if theirs and ours and theirs != ours:
        total = min(total, CONFLICTING_ID_CAP)
    return total.quantize(Decimal("0.001")), parts


def status_for(s: Decimal) -> str:
    return "LINKED" if s >= AUTO_LINK else ("REVIEW" if s >= REVIEW else "UNMATCHED")


# ─── Candidates ───

_COLS = """id, pms_res_id, channel_code, ota_res_id_norm, guest_name, guest_name_norm, check_in, check_out, nights,
           rooms_count, status, gross_minor, room_revenue_minor, currency"""


def exact_matches(cur: Any, hotel_slug: str, claim: Claim) -> list[dict]:
    norm = claim.norm_id()
    if not norm:
        return []
    cur.execute(
        f"""
        SELECT {_COLS} FROM reservations
        WHERE hotel_slug = %s AND ota_res_id_norm = %s AND (channel_code IS NULL OR channel_code = %s OR %s IS NULL)
        ORDER BY id
        """,
        (hotel_slug, norm, (claim.ota_code or "").upper() or None, claim.ota_code),
    )
    return [dict(r) for r in cur.fetchall()]


def fuzzy_candidates(cur: Any, hotel_slug: str, claim: Claim) -> list[dict]:
    """Reservations arriving within a day of the claim, not booked through
    a direct channel or another OTA, scored best first."""
    if claim.check_in is None:
        return []
    ota = (claim.ota_code or "").upper()
    excluded = sorted(DIRECT_CHANNELS | (OTA_CHANNELS - {ota}))
    cur.execute(
        f"""
        SELECT {_COLS} FROM reservations
        WHERE hotel_slug = %s AND check_in BETWEEN %s AND %s
          AND (channel_code IS NULL OR NOT (channel_code = ANY(%s)))
        """,
        (hotel_slug, claim.check_in - timedelta(days=1), claim.check_in + timedelta(days=1), excluded),
    )
    scored = []
    for r in cur.fetchall():
        s, parts = score(claim, dict(r))
        scored.append(dict(r) | {"score": s, "parts": parts})
    scored.sort(key=lambda r: (-r["score"], r["id"]))
    return scored


# ─── Matching a claim ───


def _query_json(claim: Claim) -> str:
    """The claim as stored for re-runs: the name is kept normalised only."""
    q = asdict(claim) | {"guest_name": normalize_name(claim.guest_name)}
    q["check_in"] = claim.check_in.isoformat() if claim.check_in else None
    return json.dumps(q)


def _claim_from(query: dict) -> Claim:
    q = dict(query or {})
    ci = q.get("check_in")
    q["check_in"] = date.fromisoformat(ci) if isinstance(ci, str) else ci
    return Claim(**{k: q.get(k) for k in Claim.__dataclass_fields__})


def _human(row: dict) -> bool:
    """A decision a person made (a system actor leaves confirmed_by NULL)."""
    return row["method"] == "MANUAL" or row["status"] == "REJECTED" or row["confirmed_by"] is not None


def get_match(cur: Any, hotel_slug: str, match_id: int) -> dict | None:
    cur.execute("SELECT * FROM reservation_matches WHERE hotel_slug = %s AND id = %s", (hotel_slug, match_id))
    row = cur.fetchone()
    return dict(row) if row else None


def match_for_line(cur: Any, hotel_slug: str, statement_line_id: int, *, line_kind: str = LINE_KIND) -> dict | None:
    cur.execute(
        "SELECT * FROM reservation_matches WHERE hotel_slug = %s AND line_kind = %s AND statement_line_id = %s",
        (hotel_slug, line_kind, statement_line_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def delete_matches(cur: Any, hotel_slug: str, line_kind: str, line_ids: list[int]) -> int:
    """Drop the match rows of claim lines that left their document (a
    corrected statement or payout re-committed without them): a human
    decision on such a row no longer has a subject. Returns rows deleted.
    The claim owner calls this; it never touches reservations."""
    ids = [int(i) for i in line_ids or []]
    if not ids:
        return 0
    cur.execute(
        "DELETE FROM reservation_matches WHERE hotel_slug = %s AND line_kind = %s AND statement_line_id = ANY(%s)",
        (hotel_slug, line_kind, ids),
    )
    return cur.rowcount or 0


def decide(cur: Any, hotel_slug: str, claim: Claim) -> dict:
    """The matching decision without writing: {method, status, score,
    reservation_id, reservation_ids, candidates}."""
    exact = exact_matches(cur, hotel_slug, claim)
    if exact:
        ids = [int(r["id"]) for r in exact]
        return {"method": "EXACT", "status": "LINKED", "score": Decimal("1.000"), "reservation_id": ids[0],
                "reservation_ids": ids, "candidates": [{"reservation_id": i, "score": 1.0} for i in ids]}
    ranked = fuzzy_candidates(cur, hotel_slug, claim)
    cands = [{"reservation_id": int(r["id"]), "score": float(r["score"]), "parts": r["parts"]}
             for r in ranked[:MAX_CANDIDATES]]
    if not ranked:
        return {"method": "NONE", "status": "UNMATCHED", "score": None, "reservation_id": None,
                "reservation_ids": [], "candidates": []}
    best = ranked[0]
    status = status_for(best["score"])
    # Two candidates both good enough to link: a human picks.
    if status == "LINKED" and len(ranked) > 1 and ranked[1]["score"] >= AUTO_LINK:
        status = "REVIEW"
    linked = status == "LINKED"
    return {"method": "FUZZY" if status != "UNMATCHED" else "NONE", "status": status, "score": best["score"],
            "reservation_id": int(best["id"]) if linked else None,
            "reservation_ids": [int(best["id"])] if linked else [], "candidates": cands}


def match_claim(
    cur: Any,
    hotel_slug: str,
    *,
    statement_line_id: int,
    ota_code: str | None,
    ota_res_id: str | None = None,
    guest_name: str | None = None,
    check_in: date | None = None,
    check_out: date | None = None,
    nights: int | None = None,
    amount_minor: int | None = None,
    currency: str | None = None,
    line_kind: str = LINE_KIND,
) -> dict:
    """Match one claim and upsert its reservation_matches row. A row a human
    decided (confirmed_by set, or REJECTED) is returned unchanged."""
    if nights is None and check_in and check_out:
        nights = (check_out - check_in).days
    claim = Claim((ota_code or "").upper() or None, ota_res_id, guest_name, check_in, nights, amount_minor,
                  (currency or "").upper() or None)
    existing = match_for_line(cur, hotel_slug, statement_line_id, line_kind=line_kind)
    if existing and _human(existing):
        return existing
    d = decide(cur, hotel_slug, claim)
    cur.execute(
        """
        INSERT INTO reservation_matches (hotel_slug, line_kind, statement_line_id, ota_code, reservation_id,
            reservation_ids, method, score, status, candidates, query)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, line_kind, statement_line_id) DO UPDATE SET
            ota_code = EXCLUDED.ota_code, reservation_id = EXCLUDED.reservation_id,
            reservation_ids = EXCLUDED.reservation_ids, method = EXCLUDED.method, score = EXCLUDED.score,
            status = EXCLUDED.status, candidates = EXCLUDED.candidates, query = EXCLUDED.query, updated_at = NOW()
        RETURNING *
        """,
        (hotel_slug, line_kind, statement_line_id, claim.ota_code, d["reservation_id"], d["reservation_ids"],
         d["method"], d["score"], d["status"], json.dumps(d["candidates"]), _query_json(claim)),
    )
    return dict(cur.fetchone())


def rematch_open(cur: Any, hotel_slug: str, *, limit: int = 500) -> dict[str, int]:
    """Re-run UNMATCHED and REVIEW rows (after a new PMS import). Returns
    counts by resulting status."""
    cur.execute(
        """
        SELECT * FROM reservation_matches WHERE hotel_slug = %s AND status IN ('UNMATCHED', 'REVIEW')
          AND confirmed_by IS NULL AND method <> 'MANUAL' ORDER BY id LIMIT %s
        """,
        (hotel_slug, limit),
    )
    counts: dict[str, int] = {}
    for row in [dict(r) for r in cur.fetchall()]:
        c = _claim_from(row["query"])
        out = match_claim(cur, hotel_slug, statement_line_id=row["statement_line_id"], line_kind=row["line_kind"],
                          ota_code=c.ota_code, ota_res_id=c.ota_res_id, guest_name=c.guest_name,
                          check_in=c.check_in, nights=c.nights, amount_minor=c.amount_minor, currency=c.currency)
        counts[out["status"]] = counts.get(out["status"], 0) + 1
    return counts


# ─── Review queue (humans) ───


def _user_id(actor: Any) -> int | None:
    return getattr(getattr(actor, "principal", actor), "user_id", None)


def review_queue(cur: Any, hotel_slug: str, *, status: str = "REVIEW", limit: int = 100) -> list[dict]:
    """Matches in `status` (REVIEW by default), oldest first, each with its
    candidate reservations summarised (guest names included: callers that
    serialize this mask by role)."""
    if status not in ("LINKED", "REVIEW", "UNMATCHED", "REJECTED"):
        raise ClefsError(400, f"unknown match status {status!r}", "invalid_status")
    cur.execute(
        """
        SELECT id, line_kind, statement_line_id, ota_code, reservation_id, reservation_ids, method, score, status,
               candidates, query, confirmed_by, confirmed_at, created_at, updated_at
        FROM reservation_matches WHERE hotel_slug = %s AND status = %s ORDER BY created_at, id LIMIT %s
        """,
        (hotel_slug, status, min(max(int(limit), 1), 500)),
    )
    rows = [dict(r) for r in cur.fetchall()]
    ids = sorted({int(c["reservation_id"]) for r in rows for c in (r["candidates"] or [])})
    summary: dict[int, dict] = {}
    if ids:
        cur.execute(
            """
            SELECT id, pms_res_id, channel_code, guest_name, check_in, check_out, nights, status, gross_minor,
                   room_revenue_minor, currency FROM reservations WHERE hotel_slug = %s AND id = ANY(%s)
            """,
            (hotel_slug, ids),
        )
        summary = {int(r["id"]): dict(r) for r in cur.fetchall()}
    for r in rows:
        r["candidates"] = [c | {"reservation": summary.get(int(c["reservation_id"]))} for c in (r["candidates"] or [])]
    return rows


def confirm_match(cur: Any, hotel_slug: str, match_id: int, *, reservation_ids: list[int], actor: Any = None) -> dict:
    """A human links the claim to these reservations (method MANUAL)."""
    row = get_match(cur, hotel_slug, match_id)
    if row is None:
        raise ClefsError(404, "match not found", "match_not_found")
    ids = sorted({int(i) for i in reservation_ids})
    if not ids:
        raise ClefsError(400, "reservation_ids is empty", "invalid_match")
    cur.execute("SELECT id FROM reservations WHERE hotel_slug = %s AND id = ANY(%s)", (hotel_slug, ids))
    if len(cur.fetchall()) != len(ids):
        raise ClefsError(404, "reservation not found", "reservation_not_found")
    cur.execute(
        """
        UPDATE reservation_matches SET reservation_id = %s, reservation_ids = %s, method = 'MANUAL', status = 'LINKED',
            confirmed_by = %s, confirmed_at = %s, updated_at = NOW()
        WHERE id = %s RETURNING *
        """,
        (ids[0], ids, _user_id(actor), clock.now(), match_id),
    )
    out = dict(cur.fetchone())
    audit(cur, actor, "stays.match_confirm", "reservation_match", match_id,
          before={"status": row["status"], "reservation_ids": row["reservation_ids"]},
          after={"status": "LINKED", "reservation_ids": ids}, hotel_slug=hotel_slug)
    return out


def reject_match(cur: Any, hotel_slug: str, match_id: int, *, actor: Any = None) -> dict:
    """A human says none of the candidates is this claim's stay."""
    row = get_match(cur, hotel_slug, match_id)
    if row is None:
        raise ClefsError(404, "match not found", "match_not_found")
    cur.execute(
        """
        UPDATE reservation_matches SET reservation_id = NULL, reservation_ids = '{}', status = 'REJECTED',
            confirmed_by = %s, confirmed_at = %s, updated_at = NOW()
        WHERE id = %s RETURNING *
        """,
        (_user_id(actor), clock.now(), match_id),
    )
    out = dict(cur.fetchone())
    audit(cur, actor, "stays.match_reject", "reservation_match", match_id,
          before={"status": row["status"], "reservation_ids": row["reservation_ids"]}, after={"status": "REJECTED"},
          hotel_slug=hotel_slug)
    return out
