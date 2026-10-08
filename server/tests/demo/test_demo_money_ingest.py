"""Demo seed, money part: every document enters through clefs.files and
core/ingest (three Booking header variants, Agoda XLSX, Expedia PDF, credit
note, payouts, bank, VCC reports, PMS folio) and the manifest is stable."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date

import pytest

from clefs.seed import demo_uae

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-money-ingest"
PARTS = ["core", "core_catalog", "core_stays", "money"]


@pytest.fixture
def seeded(cur):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.1, password="demo-pass-123")


def test_documents_go_through_ingest_and_the_manifest_is_stable(cur, seeded):
    cur.execute("SELECT kind, source_code, ota_code, file_format, encoding, via, status, gate_status, filename "
                "FROM import_batches WHERE hotel_slug = %s ORDER BY id", (SLUG,))
    batches = [dict(r) for r in cur.fetchall()]
    assert {b["status"] for b in batches} == {"COMMITTED"} and {b["via"] for b in batches} == {"DEMO"}
    kinds = Counter(b["kind"] for b in batches)
    assert kinds == {"OTA_STATEMENT": 5, "CREDIT_NOTE": 1, "OTA_PAYOUT": 1, "BANK": 1, "VCC_REPORT": 2,
                     "PMS_FOLIO": 1}
    formats = {(b["ota_code"], b["file_format"], (b["encoding"] or "").lower()) for b in batches
               if b["kind"] == "OTA_STATEMENT"}
    assert ("BOOKING", "csv", "utf-16") in formats and ("AGODA", "xlsx", "") in formats
    assert ("EXPEDIA", "pdf", "") in formats
    assert all(b["gate_status"] in ("ok", "n/a") for b in batches)
    cur.execute("SELECT count(DISTINCT doc_no) AS n FROM pfg_ota_statements WHERE hotel_slug = %s "
                "AND ota_code = 'BOOKING' AND doc_kind = 'RES_STATEMENT'", (SLUG,))
    assert cur.fetchone()["n"] == 3  # v1 CSV, v2 UTF-16 TSV, v3 decimal-comma CSV all mapped by aliases
    files = seeded["parts"]["money"]
    assert files["imports"] == 11 and files["files"] >= 12
    body = json.dumps(demo_uae.manifest(seeded), sort_keys=True, default=str)
    assert '"reservation_ids"' not in body and '"res:' not in body  # no database ids: stable across re-seeds
    keys = [(p["detector"], p["key"]) for p in json.loads(body)["plants"]]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
