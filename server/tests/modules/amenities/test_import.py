"""CSV issuance and supplier invoices through clefs.core.ingest."""

from __future__ import annotations

from clefs import files
from clefs.core import ingest
from clefs.modules.amenities import catalog
from clefs.modules.amenities import issuance as _issuance  # noqa: F401  registers the commit handlers

ISSUANCE_CSV = (
    "Issued At,Item,Qty,Floor,Shift,Staff Name,Reason,Location\n"
    "28/09/2026 09:15,SHAMPOO,40,1,Morning,Ana,Restock,\n"
    "28/09/2026 21:40,toothbrush,2,2,E,Ben,guest,\n"
    "28/09/2026 22:00,Dental kit,1,2,E,Ben,returned,\n"
    "28/09/2026 23:00,Unicorn horn,1,2,N,Ben,,\n"
).encode()

INVOICE_CSV = (
    "Invoice No,Invoice Date,Supplier,Item,Qty,Amount\n"
    "INV-77,15/09/2026,Gulf Supplies,MB_ENERGY,24,192.00\n"
    "INV-77,15/09/2026,Gulf Supplies,MB_ENERGY,24,216.00\n"
    "Total,,,,,408.00\n"
).encode()


def _batch(cur, slug, data, name, kind):
    ref = files.put_file(cur, slug, data, filename=name, content_type="text/csv", purpose="amenities.import")
    return ingest.create_batch(cur, slug, file_id=str(ref.id), kind=kind, use_ai=False)


def test_issuance_csv_commits_through_ingest_and_skips_unknown_items(cur, hotel_uae, items):
    b = _batch(cur, hotel_uae.slug, ISSUANCE_CSV, "issuance.csv", "ISSUANCE")
    assert b["status"] == "REVIEW", (b["status"], b.get("errors"), b.get("mapping"))
    done = ingest.commit_batch(cur, hotel_uae.slug, b["id"])
    res = done["commit_result"]
    assert res["written"] == 3 and res["skipped"] == 1 and res["unmatched_rows"][0]["reason"] == "unknown_item"
    cur.execute("SELECT i.key, s.qty, s.reason, s.shift, s.to_floor, s.source FROM amn_issuance s "
                "JOIN amn_items i ON i.id = s.item_id WHERE s.import_batch_id = %s ORDER BY s.import_row_no", (b["id"],))
    rows = [(r["key"], str(r["qty"]), r["reason"], r["shift"], r["to_floor"], r["source"]) for r in cur.fetchall()]
    assert rows == [("SHAMPOO", "40.0", "REPLENISH", "MORNING", "1", "CSV"),
                    ("DENTAL_KIT", "2.0", "GUEST_REQUEST", "EVENING", "2", "CSV"),
                    ("DENTAL_KIT", "-1.0", "RETURN", "EVENING", "2", "CSV")]
    # A back-dated import queues a re-roll of the days it touched.
    cur.execute("SELECT payload FROM job_queue WHERE name = 'am.rollup_range' AND hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["payload"] == {"start": "2026-09-28", "end": "2026-09-28"}


def test_supplier_invoice_writes_weighted_cost_with_excise(cur, hotel_uae, items):
    b = _batch(cur, hotel_uae.slug, INVOICE_CSV, "invoice.csv", "SUPPLIER_INVOICE")
    assert b["status"] == "REVIEW", (b["status"], b.get("errors"))
    ingest.commit_batch(cur, hotel_uae.slug, b["id"])
    costs = catalog.list_costs(cur, hotel_uae.slug, items["MB_ENERGY"]["id"])
    new = costs[0]
    assert new["source"] == "SUPPLIER_INVOICE" and new["invoice_ref"] == "INV-77"
    assert new["unit_cost_minor"] == 850                        # 408.00 / 48 units
    assert new["excise_minor"] == 425                           # 100% excise: half of the price
