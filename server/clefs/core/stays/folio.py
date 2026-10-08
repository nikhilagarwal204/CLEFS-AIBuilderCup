"""folio_charges: what the PMS posted to a guest's bill (02 spec 2.1).

Idempotent on (hotel_slug, pms_txn_id): a re-imported folio updates its rows
instead of doubling them. A line with no PMS transaction id gets
"b<batch>:r<row>" from the importer. Charges link to their reservation by
pms_res_id when it is known.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from clefs.core.errors import ClefsError

CODES = ("ROOM", "SC", "MF", "TF", "VAT", "TD", "FNB", "MINIBAR", "NOSHOW_FEE", "CXL_FEE", "REFUND", "OTHER")


def _as_date(v: Any) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def upsert_folio_charges(
    cur: Any, hotel_slug: str, rows: list[dict], *, import_batch_id: int | None = None, currency: str | None = None
) -> dict[str, int]:
    """Write charges [{pms_txn_id, posted_date, code, amount_minor, pms_res_id?,
    stay_date?, description?, qty?, currency?, is_reversal?, room_number?}].
    Unknown codes land as OTHER. Returns {written, linked}."""
    if not rows:
        return {"written": 0, "linked": 0}
    if currency is None:
        from clefs.core.hotels import get_hotel

        currency = get_hotel(cur, hotel_slug).currency
    ids = sorted({str(r["pms_res_id"]) for r in rows if r.get("pms_res_id")})
    links: dict[str, int] = {}
    if ids:
        cur.execute(
            """
            SELECT DISTINCT ON (pms_res_id) pms_res_id, id FROM reservations
            WHERE hotel_slug = %s AND pms_res_id = ANY(%s)
            ORDER BY pms_res_id, (source_system = 'PMS') DESC, id
            """,
            (hotel_slug, ids),
        )
        links = {r["pms_res_id"]: int(r["id"]) for r in cur.fetchall()}
    written = linked = 0
    for r in rows:
        txn = str(r.get("pms_txn_id") or "").strip()
        if not txn:
            raise ClefsError(400, "every folio line needs a pms_txn_id", "invalid_folio")
        code = str(r.get("code") or "OTHER").upper()
        code = code if code in CODES else "OTHER"
        rid = links.get(str(r.get("pms_res_id"))) if r.get("pms_res_id") else None
        amount = int(r["amount_minor"])
        cur.execute(
            """
            INSERT INTO folio_charges (hotel_slug, reservation_id, pms_res_id, posted_date, stay_date, code,
                description, qty, amount_minor, currency, is_reversal, pms_txn_id, room_number, import_batch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, pms_txn_id) DO UPDATE SET
                reservation_id = EXCLUDED.reservation_id, pms_res_id = EXCLUDED.pms_res_id,
                posted_date = EXCLUDED.posted_date, stay_date = EXCLUDED.stay_date, code = EXCLUDED.code,
                description = EXCLUDED.description, qty = EXCLUDED.qty, amount_minor = EXCLUDED.amount_minor,
                currency = EXCLUDED.currency, is_reversal = EXCLUDED.is_reversal, room_number = EXCLUDED.room_number,
                import_batch_id = COALESCE(EXCLUDED.import_batch_id, folio_charges.import_batch_id)
            """,
            (hotel_slug, rid, r.get("pms_res_id"), _as_date(r["posted_date"]), _as_date(r.get("stay_date")), code,
             r.get("description"), r.get("qty") or 1, amount, (r.get("currency") or currency).upper(),
             bool(r.get("is_reversal")) or (amount < 0 and code != "REFUND"), txn, r.get("room_number"),
             import_batch_id),
        )
        written += 1
        linked += int(rid is not None)
    return {"written": written, "linked": linked}


def link_orphans(cur: Any, hotel_slug: str) -> int:
    """Attach charges imported before their reservation. Returns rows linked."""
    cur.execute(
        """
        UPDATE folio_charges f SET reservation_id = r.id FROM reservations r
        WHERE f.hotel_slug = %s AND f.reservation_id IS NULL AND f.pms_res_id IS NOT NULL
          AND r.hotel_slug = f.hotel_slug AND r.pms_res_id = f.pms_res_id AND r.source_system = 'PMS'
        """,
        (hotel_slug,),
    )
    return cur.rowcount or 0


def folio_for(cur: Any, hotel_slug: str, reservation_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT id, posted_date, stay_date, code, description, qty, amount_minor, currency, is_reversal, pms_txn_id,
               room_number FROM folio_charges WHERE hotel_slug = %s AND reservation_id = %s
        ORDER BY posted_date, id
        """,
        (hotel_slug, reservation_id),
    )
    return [dict(r) for r in cur.fetchall()]


def folio_totals(cur: Any, hotel_slug: str, reservation_id: int) -> dict[str, int]:
    """Net amount per code (reversals are signed amounts, so a plain sum)."""
    cur.execute(
        """
        SELECT code, SUM(amount_minor) AS total FROM folio_charges WHERE hotel_slug = %s AND reservation_id = %s
        GROUP BY code ORDER BY code
        """,
        (hotel_slug, reservation_id),
    )
    return {r["code"]: int(r["total"]) for r in cur.fetchall()}
