"""core/ingest housekeeping: the daily sweep redacts contact columns from
batches nobody committed and drops staged rows past retention; XLS files
are read through xlrd with native dates and integers."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from clefs import files
from clefs.core import clock
from clefs.core.ingest import batches, registrations, sniff

RESERVATIONS = (
    "Res No,Guest Name,Arrival,Departure,Email,Phone\n"
    "R1,Jane Doe,2025-10-01,2025-10-03,jd@example.com,+971501234567\n"
)


def _batch(cur, hotel: str, text: str = RESERVATIONS) -> dict:
    fid = files.put_file(cur, hotel, text.encode(), filename="res.csv", content_type="text/csv",
                         purpose="ingest.upload").id
    return batches.create_batch(cur, hotel, file_id=fid, kind="PMS_RESERVATIONS", use_ai=False)


# ─── Sweep ───


def test_sweep_redacts_contacts_of_stale_uncommitted_batches(cur, hotel_uae):
    b = _batch(cur, hotel_uae.slug)
    assert b["status"] == "REVIEW", b["errors"]
    [row] = batches.list_rows(cur, b["id"])
    assert row["raw"]["Email"] == "jd@example.com"  # still staged for review
    now = clock.now()
    ctx = SimpleNamespace(cur=cur, now=now, hotel_slug=None)
    # Fresh: nothing to redact yet (a person may still be reviewing it).
    assert registrations.sweep(ctx)["redacted_batches"] == 0
    cur.execute("UPDATE import_batches SET updated_at = %s WHERE id = %s", (now - timedelta(days=31), b["id"]))
    assert registrations.sweep(ctx)["redacted_batches"] == 1
    [row] = batches.list_rows(cur, b["id"])
    assert "Email" not in row["raw"] and "Phone" not in row["raw"]
    assert "guest_email" not in row["canonical"] and row["raw"]["Res No"] == "R1"
    b = batches.get_batch(cur, hotel_uae.slug, b["id"])
    assert b["stats"]["contacts_redacted"] is True
    assert registrations.sweep(ctx)["redacted_batches"] == 0  # once only


def test_sweep_drops_staged_rows_of_closed_batches_past_retention(cur, hotel_uae):
    b = _batch(cur, hotel_uae.slug)
    now = clock.now()
    old = now - timedelta(days=registrations.ROW_RETENTION_DAYS + 1)
    cur.execute("UPDATE import_batches SET status = 'FAILED', updated_at = %s WHERE id = %s", (old, b["id"]))
    out = registrations.sweep(SimpleNamespace(cur=cur, now=now, hotel_slug=None))
    assert out["rows_deleted"] == 1
    assert batches.list_rows(cur, b["id"]) == []
    assert batches.get_batch(cur, hotel_uae.slug, b["id"])["status"] == "FAILED"  # the batch record stays


# ─── XLS (xlrd) ───


def test_xls_cells_become_native_values(monkeypatch):
    """No XLS writer ships with the venv, so xlrd's workbook is faked; the
    reader's job is the cell-type conversion and the sheet choice."""
    import xlrd

    def cell(ctype: int, value):
        return SimpleNamespace(ctype=ctype, value=value)

    T, N, D, E = xlrd.XL_CELL_TEXT, xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE, xlrd.XL_CELL_EMPTY
    grid = [
        [cell(T, "Reservations report"), cell(E, ""), cell(E, "")],
        [cell(T, "Res No"), cell(T, "Arrival"), cell(T, "Rate")],
        [cell(N, 1001.0), cell(D, 45931.0), cell(N, 450.5)],  # 45931 = 2025-10-01 (1900 mode)
        [cell(N, 1002.0), cell(D, 45932.0), cell(E, "")],
    ]
    main = SimpleNamespace(name="Data", nrows=len(grid), row=lambda i: grid[i])
    notes = SimpleNamespace(name="Notes", nrows=1, row=lambda i: [cell(T, "exported by PMS")])
    book = SimpleNamespace(datemode=0, sheets=lambda: [notes, main])
    monkeypatch.setattr(xlrd, "open_workbook", lambda file_contents: book)
    t = sniff.sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1fake", filename="res.xls")
    assert (t.file_format, t.sheet_name, t.header_row) == ("xls", "Data", 1)
    assert t.headers == ["Res No", "Arrival", "Rate"]
    assert t.rows[0] == [1001, datetime(2025, 10, 1), 450.5]
    assert t.rows[1][2] is None
