"""HTTP flow: upload -> mapping -> publish -> one-pager; permissions; no milestones (04 spec 6.8, 7.1)."""

from __future__ import annotations

from clefs import files
from tests.foundation.route_walk import route_paths
from tests.modules.finance.conftest import AUG_LINES, statement_csv

BASE = "/hotels/test-dubai/finance"


def _upload(cur, slug):
    return files.put_file(cur, slug, statement_csv(), filename="pl-aug.csv", content_type="text/csv",
                          purpose="finance.statement", source="upload")


def test_upload_confirm_publish_over_http(cur, hotel_uae, client_as, neighbours):
    owner = client_as("owner")
    ref = _upload(cur, hotel_uae.slug)
    r = owner.post(f"{BASE}/imports", json={"file_id": str(ref.id), "statement_kind": "pl", "period_month": "2026-08"})
    assert r.status_code == 201, r.text
    fb = r.json()["finance_batches"]
    assert len(fb) == 1 and fb[0]["status"] == "mapping_required" and fb[0]["period_month"] == "2026-08-01"
    batch_id = fb[0]["id"]
    m = owner.get(f"{BASE}/mapping", params={"status": "open", "batch_id": batch_id}).json()
    assert len(m["catalog"]) == 45
    ids = {a["code"]: a["account_id"] for a in m["accounts"]}
    assert len(ids) == len(AUG_LINES) == 24
    items = [{"account_id": ids[c], "usali_line": AUG_LINES[c]} for c in ids]
    r = owner.post(f"{BASE}/mapping/confirm", json={"items": items})
    assert r.status_code == 200, r.text
    r = owner.put(f"{BASE}/stat-inputs/2026-08", json={"available_rn": 2480, "rooms_rev_pms_minor": 42_410_000})
    assert r.status_code == 200 and r.json()["available_rn"] == 2480
    d = owner.get(f"{BASE}/imports/{batch_id}").json()
    assert d["status"] == "review" and d["summary"]["gop"] == "174000.00" and d["usali"]
    r = owner.post(f"{BASE}/imports/{batch_id}/publish")
    assert r.status_code == 200, r.text
    one = owner.get(f"{BASE}/monthly/2026-08").json()
    assert one["gop"] == "174000.00" and one["gop_margin_pct"] == "32.83" and one["goppar"] == "70.16"
    assert one["trevpar"] == "213.71" and one["revpar"] == "169.35" and one["labour_pct"] == "26.98"
    assert owner.get(f"{BASE}/monthly/latest").json()["month"] == "2026-08-01"
    assert [t["month"] for t in owner.get(f"{BASE}/trend").json()["months"]] == ["2026-08-01"]


def test_revenue_sees_weekly_only(hotel_uae, client_as, neighbours):
    rev = client_as("revenue")
    assert rev.get(f"{BASE}/weekly").status_code == 200
    assert rev.get(f"{BASE}/monthly/2026-08").status_code == 403
    assert rev.get(f"{BASE}/mapping").status_code == 403
    gm = client_as("gm")
    assert gm.get(f"{BASE}/monthly/2026-08").status_code == 404      # can view, nothing published
    assert gm.post(f"{BASE}/imports", json={"file_id": "x"}).status_code == 403   # import is owner/finance


def test_weekly_strip_has_13_weeks(hotel_uae, client_as, neighbours):
    body = client_as("finance").get(f"{BASE}/weekly").json()
    assert len(body["weeks"]) == 13 and body["latest"]["nrevpar"] is None   # no data: n/a, no error


def test_benchmarks_admin_only(hotel_uae, client_as):
    payload = {"region": "UAE", "period": "2026-07", "period_kind": "ytd", "value": "-47.8",
               "source_url": "https://example.org/hotstats-uae-jul-2026"}
    assert client_as("owner").post(f"{BASE}/benchmarks", json=payload).status_code == 403
    admin = client_as("owner", admin=True)
    r = admin.post(f"{BASE}/benchmarks", json=payload)
    assert r.status_code == 201 and r.json()["value"] == "-47.8"


def test_no_milestone_fields_or_routes(cur, app):
    bad = ("milestone", "target", "tier")
    assert not [p for p in route_paths(app.routes) if "/finance" in p and any(b in p for b in bad)]
    cur.execute("""SELECT table_name, column_name FROM information_schema.columns
                   WHERE table_name LIKE 'fin\\_%%' ESCAPE '\\'""")
    cols = [f"{r['table_name']}.{r['column_name']}" for r in cur.fetchall()]
    assert cols and not [c for c in cols if any(b in c for b in bad)]
    from clefs.core.metrics import registered_metrics

    assert not [m.key for m in registered_metrics() if m.key.startswith("fin.") and any(b in m.key for b in bad)]


def test_settings_show_sc_from_profit_guard(hotel_uae, client_as, neighbours):
    """Regression: the card linked to /profit/settings, which does not exist,
    so the SC treatment could be changed nowhere. Now no dead link is given,
    and the card writes through Profit Guard's own policies route whenever
    the viewer holds profit.manage (Finance keeps no setting of its own)."""
    from clefs.auth import permissions

    neighbours.sc = False
    body = client_as("finance").get(f"{BASE}/settings").json()
    # profit.manage exists only when Profit Guard is loaded (CLEFS_ONLY_MODULES).
    can_edit = "profit.manage" in permissions.permissions_for_role("finance")
    assert body["service_charge"] == {"treat_sc_as_revenue": False, "source": "profit_guard",
                                      "read_only": not can_edit, "can_edit": can_edit,
                                      "edit_api": "profit-guard/policies", "settings_path": None}
    assert body["settings"]["labour_band_min_pct"] == 20.0
    r = client_as("finance").put(f"{BASE}/settings", json={"labour_band_min_pct": 50, "labour_band_max_pct": 40})
    assert r.status_code == 422


def test_sc_links_only_to_pages_and_routes_that_exist(app):
    """Guards the 404: a settings_path must be a real console page, and the
    edit_api must be a mounted PUT route whenever Profit Guard is loaded."""
    from pathlib import Path

    from clefs.auth import permissions
    from clefs.modules.finance import router as fin_router

    if fin_router.SC_SETTINGS_PATH is not None:
        console = Path(__file__).resolve().parents[4] / "client/app/app/[hotelSlug]/(console)"
        assert (console / fin_router.SC_SETTINGS_PATH / "page.tsx").is_file(), fin_router.SC_SETTINGS_PATH
    if "profit.manage" in permissions.permissions_for_role("owner"):
        from tests.foundation.route_walk import iter_routes

        # iter_routes, not app.routes: a flat read sees nothing on FastAPI 0.137+.
        puts = [r.path for r in iter_routes(app.routes) if "PUT" in (getattr(r, "methods", None) or ())]
        assert any(p.endswith("/profit-guard/policies") for p in puts), "Profit Guard policies route missing"


def test_pl_without_month_leaves_no_pending_batch_and_takes_the_month_later(cur, hotel_uae, client_as, neighbours):
    """Regression (QA OWN-07): a single-month P&L posted without a month used
    to sit as a pending core batch that could never commit, and the same
    bytes posted again with the month deduped straight back to it."""
    owner = client_as("owner")
    ref = _upload(cur, hotel_uae.slug)
    r = owner.post(f"{BASE}/imports", json={"file_id": str(ref.id), "statement_kind": "pl"})
    assert r.status_code == 400 and r.json()["code"] == "period_month_required", r.text
    cur.execute("SELECT COUNT(*) AS n FROM import_batches WHERE hotel_slug = %s AND kind = 'PL_STATEMENT'",
                (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 0
    # the stored file is re-posted with the month: no second upload
    r = owner.post(f"{BASE}/imports", json={"file_id": str(ref.id), "statement_kind": "pl", "period_month": "2026-09"})
    assert r.status_code == 201, r.text
    fb = r.json()["finance_batches"]
    assert len(fb) == 1 and fb[0]["period_month"] == "2026-09-01"


def test_open_month_less_batch_is_replaced_when_the_month_arrives(cur, hotel_uae, client_as, neighbours):
    """A month-less batch already pending (made before the fix, or by the
    ingest screen) is discarded and re-created with the month given now."""
    from clefs.core import ingest

    ref = _upload(cur, hotel_uae.slug)
    old = ingest.create_batch(cur, hotel_uae.slug, file_id=ref.id, kind="PL_STATEMENT", source_code="pl_statement")
    assert old["status"] == "REVIEW" and old["period_month"] is None
    r = client_as("owner").post(f"{BASE}/imports", json={"file_id": str(ref.id), "statement_kind": "pl",
                                                         "period_month": "2026-09"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["core_batch"]["id"] != old["id"] and body["core_batch"]["status"] == "COMMITTED"
    assert [b["period_month"] for b in body["finance_batches"]] == ["2026-09-01"]
    cur.execute("SELECT 1 FROM import_batches WHERE id = %s", (old["id"],))
    assert cur.fetchone() is None
