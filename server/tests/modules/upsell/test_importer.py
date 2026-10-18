"""Menu CSV import: aliases, upserts, partial vs strict, dry run."""

from __future__ import annotations

from clefs.modules.upsell import importer, menu

CSV = """Outlet,SKU,Section,Name,Name Arabic,Price AED,Tags,Days,From,To,Alcohol,Prep
ird,C1,Mains,Club Sandwich,كلوب,50.00,contains_pork,daily,,,no,20
ird,C2,Mains,Falafel Wrap,,AED 35,vegan;halal,Mon-Fri,11:00,23:00,no,10
pool-bar,,Drinks,Virgin Mojito,,30,,Sat-Sun,,,,
ird,C3,Mains,Broken Row,,abc,,,,,,
ird,C4,Mains,Bad Tag,,20,glutenous,,,,,
"""


def test_import_creates_outlets_items_and_reports_bad_rows(cur, hotel_uae):
    slug = hotel_uae.slug
    r = importer.import_csv(cur, slug, "AED", CSV.encode())
    assert r["rows"] == 5 and r["created"] == 3 and r["updated"] == 0 and r["applied"]
    assert r["outlets_created"] == ["ird", "pool-bar"]
    assert [e["row"] for e in r["errors"]] == [5, 6]
    assert "price" in r["errors"][0]["error"] and "dietary" in r["errors"][1]["error"]
    items = {i["name_en"]: i for i in menu.list_items(cur, slug)}
    wrap = items["Falafel Wrap"]
    assert wrap["price_minor"] == 3500 and wrap["days_mask"] == 31 and wrap["dietary"] == ["vegan", "halal"]
    assert str(wrap["available_from"])[:5] == "11:00"
    assert items["Club Sandwich"]["name_ar"] == "كلوب" and items["Virgin Mojito"]["days_mask"] == 96


def test_reimport_updates_by_sku_and_name(cur, hotel_uae):
    slug = hotel_uae.slug
    importer.import_csv(cur, slug, "AED", CSV)
    r = importer.import_csv(cur, slug, "AED", "outlet,sku,name,price\nird,C1,Club Sandwich XL,55\n"
                                              "pool-bar,,virgin mojito,32\n")
    assert r["updated"] == 2 and r["created"] == 0
    items = {i["sku"] or i["name_en"].lower(): i for i in menu.list_items(cur, slug)}
    assert items["C1"]["price_minor"] == 5500 and items["C1"]["name_en"] == "Club Sandwich XL"
    # matched case-insensitively by name; the row's own spelling is kept
    assert items["virgin mojito"]["price_minor"] == 3200 and len(items) == 3


def test_dry_run_and_strict_write_nothing(cur, hotel_uae):
    slug = hotel_uae.slug
    r = importer.import_csv(cur, slug, "AED", CSV, dry_run=True)
    assert r["created"] == 3 and not r["applied"]
    assert menu.list_items(cur, slug) == [] and menu.list_outlets(cur, slug) == []
    r = importer.import_csv(cur, slug, "AED", CSV, strict=True)
    assert r["errors"] and not r["applied"] and menu.list_items(cur, slug) == []


def test_default_outlet_and_no_autocreate(cur, hotel_uae):
    slug = hotel_uae.slug
    menu.create_outlet(cur, slug, menu.OutletIn(key="ird", name_en="IRD"))
    r = importer.import_csv(cur, slug, "AED", "name,price\nTea,12\n", default_outlet="ird")
    assert r["created"] == 1
    r = importer.import_csv(cur, slug, "AED", "outlet,name,price\nspa,Massage,300\n", create_outlets=False)
    assert r["errors"][0]["error"].startswith("unknown outlet")


# ─── On core/ingest's MENU kind ───


def _batch(cur, batch_id):
    cur.execute("SELECT kind, status, source_code, options, commit_result FROM import_batches WHERE id = %s",
                (batch_id,))
    return dict(cur.fetchone())


def test_an_import_is_a_committed_menu_batch_with_flagged_rows(cur, hotel_uae):
    from clefs.core import ingest

    r = importer.import_csv(cur, hotel_uae.slug, "AED", CSV)
    assert r["applied"] and r["batch_id"] and not r["already_imported"]
    b = _batch(cur, r["batch_id"])
    assert (b["kind"], b["status"], b["source_code"]) == ("MENU", "COMMITTED", "menu")
    assert b["options"]["commit"] == {"default_outlet": None, "create_outlets": True, "strict": False}
    assert b["commit_result"]["created"] == 3
    # the rows the menu refused are ERROR on the batch, for the Imports console
    bad = ingest.list_rows(cur, r["batch_id"], status="ERROR")
    assert [x["row_no"] for x in bad] == [4, 5] and "price" in bad[0]["messages"][0]


def test_the_same_file_twice_is_one_batch(cur, hotel_uae):
    slug = hotel_uae.slug
    first = importer.import_csv(cur, slug, "AED", CSV)
    again = importer.import_csv(cur, slug, "AED", CSV.encode())
    assert again["already_imported"] and not again["applied"] and again["batch_id"] == first["batch_id"]
    assert again["created"] == 3 and len(menu.list_items(cur, slug)) == 3


def test_dry_run_then_import_commits_the_same_batch(cur, hotel_uae):
    slug = hotel_uae.slug
    dry = importer.import_csv(cur, slug, "AED", CSV, dry_run=True)
    assert _batch(cur, dry["batch_id"])["status"] == "REVIEW"
    real = importer.import_csv(cur, slug, "AED", CSV)
    assert real["batch_id"] == dry["batch_id"] and real["applied"] and real["created"] == 3
    assert [e["row"] for e in real["errors"]] == [5, 6]


def test_missing_required_columns_wait_for_a_mapping(cur, hotel_uae):
    """Headers the alias map cannot place: nothing is written and the batch
    waits in MAPPING_REQUIRED; a person maps it in the Imports console and
    commits with the menu choices as commit options."""
    from clefs.core import ingest

    slug = hotel_uae.slug
    r = importer.import_csv(cur, slug, "AED", "Dish,Cost\nTea,12\nCoffee,14\n", default_outlet="ird")
    assert r["mapping_required"] == ["name_en", "price"] and not r["applied"] and r["errors"][0]["row"] == 1
    assert _batch(cur, r["batch_id"])["status"] == "MAPPING_REQUIRED" and menu.list_items(cur, slug) == []
    b = ingest.confirm_mapping(cur, slug, r["batch_id"], mapping={"name_en": "Dish", "price": "Cost"})
    assert b["status"] == "REVIEW"
    done = ingest.commit_batch(cur, slug, r["batch_id"], options={"default_outlet": "ird"})
    assert done["status"] == "COMMITTED" and done["commit_result"]["created"] == 2
    assert {i["name_en"]: i["price_minor"] for i in menu.list_items(cur, slug)} == {"Tea": 1200, "Coffee": 1400}


def test_strict_console_commit_refuses_the_whole_file(cur, hotel_uae):
    import pytest

    from clefs import files
    from clefs.core import ingest
    from clefs.core.errors import ClefsError

    slug = hotel_uae.slug
    fid = files.put_file(cur, slug, CSV.encode(), filename="m.csv", content_type="text/csv", purpose="ingest.upload").id
    b = ingest.create_batch(cur, slug, file_id=fid, kind="MENU")
    with pytest.raises(ClefsError) as e:
        ingest.commit_batch(cur, slug, b["id"], options={"strict": True})
    assert e.value.code == "menu_rows_refused" and "line 5" in e.value.detail
    assert menu.list_items(cur, slug) == []
