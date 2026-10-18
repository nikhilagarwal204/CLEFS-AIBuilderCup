"""Xero, QuickBooks and Tally fixtures parse; the connector sync imports a month (04 spec 6.3)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from clefs.core import oauth
from clefs.core.oauth import NotConnected
from clefs.modules.finance import connectors, review
from clefs.modules.finance.connectors import common, qbo, xero, zoho

FIX = Path(__file__).parent / "fixtures"
AUG = date(2026, 8, 1)


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def test_xero_pl_fixture_parses():
    rep = xero.parse_pl(_load("xero_pl_2026_08.json"), "AED")
    assert rep.net_profit_minor == 12_600_000
    assert len(rep.lines) == 22
    by = {ln["code"]: ln for ln in rep.lines}
    assert by["a-4000"]["amount_minor"] == 42_000_000 and by["a-4000"]["type_raw"] == "income"
    assert by["a-6000"]["amount_minor"] == -7_000_000 and by["a-5000"]["amount_minor"] == -3_200_000
    assert sum(ln["amount_minor"] for ln in rep.lines) == rep.net_profit_minor


def test_qbo_pl_fixture_parses():
    rep = qbo.parse_pl(_load("qbo_pl_2026_08.json"), "AED")
    assert rep.net_profit_minor == 39_000_000
    by = {ln["code"]: ln for ln in rep.lines}
    assert set(by) == {"10", "11", "12", "20", "30", "31", "40"}
    assert by["12"]["amount_minor"] == 1_200_000 and by["20"]["amount_minor"] == -3_200_000
    assert sum(ln["amount_minor"] for ln in rep.lines) == rep.net_profit_minor


def test_tally_upload_parses_and_is_tagged(cur, hotel_uae, upload_statement, neighbours):
    res = upload_statement(hotel_uae.slug, (FIX / "tally_pl_2026_08.csv").read_bytes(), AUG, filename="export.csv")
    assert res["commit_result"]["source"] == "tally_upload"
    cur.execute("SELECT * FROM fin_import_batches WHERE hotel_slug = %s", (hotel_uae.slug,))
    b = cur.fetchone()
    assert b["source"] == "tally_upload" and b["source_net_profit_minor"] == 37_800_000
    lines = review.lines_for_batch(cur, b)
    names = {ln["name_raw"] for ln in lines}
    assert "Output VAT" not in names          # Duties & Taxes never reach the P&L
    assert sum(ln["amount_minor"] for ln in lines) == 37_800_000
    cur.execute("SELECT DISTINCT source_system FROM fin_coa_accounts WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert [r["source_system"] for r in cur.fetchall()] == ["tally"]


def test_zoho_is_a_stub():
    with pytest.raises(NotConnected) as e:
        zoho.ADAPTER.fetch_pl(None, "x", AUG, "AED")
    assert "coming next" in e.value.detail


@pytest.fixture
def xero_http(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/connections":
            return httpx.Response(200, json=[{"tenantId": "t-1", "tenantType": "ORGANISATION", "tenantName": "X"}])
        assert request.headers["xero-tenant-id"] == "t-1"
        return httpx.Response(200, json=_load("xero_pl_2026_08.json"))

    common.set_transport(httpx.MockTransport(handler))
    monkeypatch.setattr(oauth, "get_access_token", lambda cur, slug, provider: "tok")
    monkeypatch.setattr(oauth, "get_connection", lambda cur, slug, provider: {"account_ref": None})
    monkeypatch.setattr(oauth, "set_account_ref", lambda *a, **k: True)
    yield calls
    common.set_transport(None)


def test_xero_sync_imports_month_idempotently(cur, hotel_uae, xero_http, neighbours):
    out = connectors.sync_provider(cur, hotel_uae.slug, "xero", AUG)
    assert out["lines"] == 22 and out["batch"] and not out["reused"]
    q = dict(xero_http[-1].url.params)
    assert q["fromDate"] == "2026-08-01" and q["toDate"] == "2026-08-31" and q["standardLayout"] == "true"
    cur.execute("SELECT * FROM fin_import_batches WHERE id = %s", (out["batch"],))
    b = cur.fetchone()
    assert b["source"] == "xero" and b["source_net_profit_minor"] == 12_600_000 and b["basis"] == "accrual"
    again = connectors.sync_provider(cur, hotel_uae.slug, "xero", AUG)
    assert again["batch"] == out["batch"]
    cur.execute("SELECT status, tenant_ref FROM fin_connections WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert dict(cur.fetchone()) == {"status": "active", "tenant_ref": "t-1"}


def test_xero_401_is_not_connected(cur, hotel_uae, monkeypatch):
    common.set_transport(httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    monkeypatch.setattr(oauth, "get_access_token", lambda cur, slug, provider: "tok")
    monkeypatch.setattr(oauth, "get_connection", lambda cur, slug, provider: {"account_ref": "t-1"})
    try:
        with pytest.raises(NotConnected):
            connectors.sync_provider(cur, hotel_uae.slug, "xero", AUG)
    finally:
        common.set_transport(None)
