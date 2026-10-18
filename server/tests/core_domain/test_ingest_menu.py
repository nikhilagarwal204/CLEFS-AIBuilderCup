"""core/ingest: the MENU kind (0024 widens import_batches.kind), its alias
map, and the commit-handler hooks it needed: per-commit `options`, a
`dry_run` that rolls back, and mark_row_error for a refused row."""

from __future__ import annotations

import pytest

from clefs import files
from clefs.core import ingest
from clefs.core.errors import ClefsError
from clefs.core.ingest import aliases, batches, commit, kinds

MENU_CSV = (
    "Outlet,SKU,Section,Name,Name Arabic,Price AED,Tags,Days,From,To,Alcohol,Prep\n"
    "ird,C1,Mains,Club Sandwich,كلوب,50.00,contains_pork,daily,,,no,20\n"
    "ird,C2,Mains,Falafel Wrap,,AED 35,vegan;halal,Mon-Fri,11:00,23:00,no,10\n"
    "ird,C3,Mains,,,12,,,,,,\n"
)


def put(cur, slug, text: str, name: str = "menu.csv") -> str:
    return files.put_file(cur, slug, text.encode(), filename=name, content_type="text/csv", purpose="ingest.upload").id


@pytest.fixture
def menu_handler():
    """A recording MENU handler; restores whatever was registered (Upsell's
    commit_menu when the module is loaded) afterwards."""
    prev = kinds.commit_handler("MENU")
    calls: list[tuple[dict, list[dict]]] = []

    def handler(cur, batch, rows):
        calls.append((batch, rows))
        cur.execute("INSERT INTO audit_log (actor_type, action) VALUES ('system', 'test.menu_write')")
        kinds.mark_row_error(cur, rows[0]["_row_id"], "refused by the writer")
        return {"written": len(rows) - 1}

    kinds.register_commit_handler("MENU", handler)
    yield calls
    if prev is not None:
        kinds.register_commit_handler("MENU", prev)
    else:
        kinds.unregister_commit_handler("MENU")


def _writes(cur) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'test.menu_write'")
    return cur.fetchone()["n"]


def test_menu_is_a_kind_with_its_schema_and_alias_map():
    assert "MENU" in ingest.KINDS
    schema = kinds.schema_for("MENU")
    assert schema.required == ("name_en", "price") and schema.total_field is None
    assert {f.type for f in schema.fields} == {"text"}
    m = aliases.get_map("menu")
    assert m is not None and m.kind == "MENU" and m.unverified and m.detect == ()
    headers = MENU_CSV.splitlines()[0].split(",")
    mp, _ = aliases.resolve(headers, "MENU", "menu")
    assert mp == {
        "outlet": "Outlet", "sku": "SKU", "category": "Section", "name_en": "Name", "name_ar": "Name Arabic",
        "price": "Price AED", "available_from": "From", "available_to": "To", "days": "Days", "dietary": "Tags",
        "alcohol": "Alcohol", "prep_min": "Prep",
    }
    # never auto-detected from an emailed attachment
    assert aliases.detect_source(headers) != "menu"


def test_a_menu_file_stages_as_a_menu_batch(cur, hotel_uae):
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, MENU_CSV), kind="MENU",
                             source_code="menu")
    assert b["kind"] == "MENU" and b["status"] == "REVIEW", b["errors"]
    rows = batches.list_rows(cur, b["id"])
    assert [r["status"] for r in rows] == ["OK", "OK", "ERROR"]
    assert rows[1]["canonical"]["price"] == "AED 35" and rows[1]["canonical"]["days"] == "Mon-Fri"
    assert rows[2]["messages"] == ["name_en: missing"]


def test_dry_run_runs_the_handler_and_rolls_back(cur, hotel_uae, menu_handler):
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, MENU_CSV), kind="MENU")
    out = commit.commit_batch(cur, hotel_uae.slug, b["id"], skip_errors=True, dry_run=True,
                              options={"default_outlet": "ird"})
    assert out["dry_run"] is True and out["status"] == "REVIEW" and out["commit_result"] == {"written": 1}
    seen, rows = menu_handler[0]
    assert seen["dry_run"] is True and seen["options"]["commit"] == {"default_outlet": "ird"}
    assert len(rows) == 2 and _writes(cur) == 0
    # nothing stuck: the row flag, the options and the status all rolled back
    assert batches.row_counts(cur, b["id"]) == {"OK": 2, "ERROR": 1}
    again = batches.get_batch(cur, hotel_uae.slug, b["id"])
    assert "commit" not in (again.get("options") or {}) and again["committed_at"] is None


def test_real_commit_keeps_options_and_row_errors(cur, hotel_uae, menu_handler):
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, MENU_CSV), kind="MENU")
    with pytest.raises(ClefsError) as e:
        commit.commit_batch(cur, hotel_uae.slug, b["id"], options={"strict": True})
    assert e.value.code == "rows_have_errors"  # the staging error still blocks without skip_errors
    out = commit.commit_batch(cur, hotel_uae.slug, b["id"], skip_errors=True, options={"strict": False})
    assert out["status"] == "COMMITTED" and out["commit_result"] == {"written": 1} and "dry_run" not in out
    assert out["options"]["commit"] == {"strict": False} and menu_handler[-1][0]["dry_run"] is False
    assert batches.row_counts(cur, b["id"]) == {"OK": 1, "ERROR": 2} and _writes(cur) == 1
    flagged = [r for r in batches.list_rows(cur, b["id"], status="ERROR") if r["row_no"] == 1]
    assert flagged[0]["messages"] == ["refused by the writer"]


def test_dry_run_errors_leave_the_batch_as_it_was(cur, hotel_uae, menu_handler):
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, MENU_CSV), kind="MENU")
    with pytest.raises(ClefsError) as e:
        commit.commit_batch(cur, hotel_uae.slug, b["id"], dry_run=True)
    assert e.value.code == "rows_have_errors"
    assert batches.get_batch(cur, hotel_uae.slug, b["id"])["status"] == "REVIEW" and menu_handler == []


def test_commit_route_takes_options_and_dry_run(client_as, cur, hotel_uae, menu_handler):
    owner = client_as("owner")
    fid = put(cur, hotel_uae.slug, MENU_CSV)
    b = owner.post(f"/hotels/{hotel_uae.slug}/ingest/batches", json={"file_id": fid, "kind": "MENU"}).json()
    base = f"/hotels/{hotel_uae.slug}/ingest/batches/{b['id']}/commit"
    r = owner.post(base, json={"skip_errors": True, "dry_run": True, "options": {"default_outlet": "ird"}})
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True and r.json()["status"] == "REVIEW" and r.json()["commit_result"] == {"written": 1}
    big = owner.post(base, json={"skip_errors": True, "options": {"x": "y" * 5000}})
    assert big.status_code == 400 and big.json()["code"] == "options_too_large"
    done = owner.post(base, json={"skip_errors": True}).json()
    assert done["status"] == "COMMITTED" and "dry_run" not in done
    kinds_out = owner.get(f"/hotels/{hotel_uae.slug}/ingest/sources").json()["kinds"]
    assert any(k["kind"] == "MENU" and k["committable"] for k in kinds_out)
