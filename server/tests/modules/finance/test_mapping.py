"""USALI mapping: rules, LLM, confirmation, locked reuse, renames, memory and the Xero rule (04 spec 6.4)."""

from __future__ import annotations

from datetime import date

import psycopg2
import pytest

from clefs.core import hotels
from clefs.core.errors import ClefsError
from clefs.modules.finance import accounts, imports, mapping, review
from tests.modules.finance.conftest import AUG_LINES, confirm_all, statement_csv

AUG, SEP = date(2026, 8, 1), date(2026, 9, 1)


def _lines(names_amounts, source="upload_csv"):
    return [{"code": c, "name": n, "type_raw": t, "amount_minor": a} for c, n, t, a in names_amounts]


def _write(cur, slug, month, rows, *, source="upload_csv", sha=None):
    return imports.write_month(cur, slug, period_month=month, statement_kind="pl", source=source,
                               sha256=sha or f"{source}-{month}-{len(rows)}", currency="AED", lines=_lines(rows))


def test_rules_cover_pass_through_below_gop_and_eww():
    assert mapping.rule_for("VAT Output", "income") == "excluded.vat"
    assert mapping.rule_for("ضريبة القيمة المضافة", None) == "excluded.vat"
    assert mapping.rule_for("درهم السياحة", "income") == "excluded.tourism_dirham"
    assert mapping.rule_for("رسوم البلدية", "expense") == "excluded.municipality_fee"
    assert mapping.rule_for("Depreciation - FF&E", "expense") == "below_line.depreciation"
    assert mapping.rule_for("Owner Drawings", "expense") == "below_line.owner_drawings"
    assert mapping.rule_for("Office Rent", "expense") == "non_operating.rent"
    assert mapping.rule_for("DEWA Water", "expense") == "eww.other"
    assert mapping.rule_for("Current account charges", "expense") is None   # "rent" inside a word


def test_service_charge_follows_profit_guard(cur, hotel_uae, neighbours):
    neighbours.sc = True
    b = _write(cur, hotel_uae.slug, AUG, [("7000", "Service Charge Income", "income", 500_000)])
    live = mapping.live_map(cur, review.lines_for_batch(cur, b)[0]["account_id"])
    assert live["usali_line"] == "rooms.revenue"
    neighbours.sc = False
    b = _write(cur, hotel_uae.slug, SEP, [("7001", "Service Charge Collected", "income", 500_000)])
    live = mapping.live_map(cur, review.lines_for_batch(cur, b)[0]["account_id"])
    assert live["usali_line"] == "excluded.balance_sheet"


def test_llm_proposals_and_bulk_confirm(cur, hotel_uae, neighbours, fake_ai):
    b = _write(cur, hotel_uae.slug, AUG, [("1", "Room Revenue", "income", 100), ("2", "Laundry", "income", 50)])
    ids = [ln["account_id"] for ln in review.lines_for_batch(cur, b)]
    fake_ai.push({"proposals": [
        {"account_ref": str(ids[0]), "usali_line": "rooms.revenue", "is_pass_through_tax": False,
         "pass_through_kind": None, "confidence": 0.97, "rationale": "'Room Revenue'", "alternatives": []},
        {"account_ref": str(ids[1]), "usali_line": "ood.revenue", "is_pass_through_tax": False,
         "pass_through_kind": None, "confidence": 0.6, "rationale": "'Laundry'",
         "alternatives": [{"usali_line": "rooms.revenue", "confidence": 0.3}]},
        {"account_ref": "999999", "usali_line": "fnb.revenue", "is_pass_through_tax": False,
         "pass_through_kind": None, "confidence": 0.9, "rationale": "not asked", "alternatives": []},
    ]})
    res = mapping.propose_llm(cur, hotel_uae.slug, ids)
    assert res["proposed"] == 2
    assert mapping.live_map(cur, ids[1])["alternatives"][0]["usali_line"] == "rooms.revenue"
    req = fake_ai.calls[-1]
    assert req.task == "finance.map_accounts" and req.hotel_slug == hotel_uae.slug
    assert "confirmed mappings" not in str(req.input)      # no few-shot yet: nothing confirmed at this hotel
    assert f"- {ids[0]} | 1 | Room Revenue" in str(req.input)
    with pytest.raises(ClefsError) as e:
        mapping.confirm(cur, hotel_uae.slug, actor_id=None, all_above=0.5)
    assert e.value.code == "bulk_threshold"
    out = mapping.confirm(cur, hotel_uae.slug, actor_id=None, all_above=0.85)
    assert out["confirmed"] == [ids[0]]
    with pytest.raises(ClefsError) as e:
        mapping.confirm(cur, hotel_uae.slug, actor_id=None, items=[{"account_id": ids[1]}, {"account_id": ids[0]}])
    assert e.value.code == "confirm_one_at_a_time"
    mapping.confirm(cur, hotel_uae.slug, actor_id=None, items=[{"account_id": ids[1]}])
    assert mapping.live_map(cur, ids[1])["locked"]


def test_locked_reuse_and_new_version_on_rename(cur, hotel_uae, upload_statement, neighbours):
    upload_statement(hotel_uae.slug, statement_csv(), AUG)
    cur.execute("SELECT id FROM fin_import_batches WHERE hotel_slug = %s", (hotel_uae.slug,))
    aug = cur.fetchone()["id"]
    confirm_all(cur, hotel_uae.slug, aug, AUG_LINES)
    # September: same books, one account renamed in the books (same code)
    upload_statement(hotel_uae.slug, statement_csv(overrides={"4000": 400000}).replace(
        "Room Revenue / إيرادات الغرف".encode(), b"Rooms Revenue - Transient"), SEP)
    cur.execute("SELECT * FROM fin_import_batches WHERE hotel_slug = %s AND period_month = %s", (hotel_uae.slug, SEP))
    sep = cur.fetchone()
    lines = {ln["code"]: ln for ln in review.lines_for_batch(cur, sep)}
    assert all(ln["locked"] for c, ln in lines.items() if c != "4000")      # reused every month
    renamed = lines["4000"]
    assert not renamed["locked"] and renamed["version"] == 2               # a new proposal to confirm
    assert sep["status"] == "mapping_required"
    cur.execute("SELECT version, superseded_at IS NOT NULL AS old FROM fin_usali_map WHERE account_id = %s ORDER BY version",
                (renamed["account_id"],))
    assert [(r["version"], r["old"]) for r in cur.fetchall()] == [(1, True), (2, False)]


def test_unique_nulls_not_distinct_blocks_duplicate_codeless_accounts(cur, hotel_uae):
    a1, _ = accounts.upsert_account(cur, hotel_uae.slug, source="upload_csv", code=None, name="Room Revenue")
    a2, _ = accounts.upsert_account(cur, hotel_uae.slug, source="upload_xlsx", code=None, name="room  revenue!")
    assert a1 == a2
    cur.execute("SAVEPOINT dup")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute("""INSERT INTO fin_coa_accounts (hotel_slug, source_system, code, name_raw, name_norm)
                       VALUES (%s, 'upload', NULL, 'Room Revenue', 'room revenue')""", (hotel_uae.slug,))
    cur.execute("ROLLBACK TO SAVEPOINT dup")


def _hotel(cur, slug):
    return hotels.create_hotel(cur, slug=slug, name=f"{slug} (DEMO)", city="Dubai")


def test_memory_needs_three_hotels_and_never_learns_from_xero(cur, hotel_uae, neighbours):
    for i, src in enumerate(("upload_csv", "upload_csv", "xero", "xero")):
        slug = f"mem-hotel-{i}"
        _hotel(cur, slug)
        b = _write(cur, slug, AUG, [("9", "Banquet Hall Hire", "income", 1000)], source=src)
        aid = review.lines_for_batch(cur, b)[0]["account_id"]
        mapping.confirm_one(cur, slug, aid, actor_id=None, usali_line="fnb.revenue")
    mapping.rebuild_memory(cur)
    cur.execute("SELECT * FROM fin_mapping_memory WHERE name_norm = 'banquet hall hire'")
    row = cur.fetchone()
    assert row["hotels"] == 2          # the two Xero hotels are not counted
    # with only 2 non-Xero hotels, a new hotel gets no memory proposal
    b = _write(cur, hotel_uae.slug, AUG, [("9", "Banquet Hall Hire", "income", 1000)])
    assert review.lines_for_batch(cur, b)[0]["usali_line"] is None
    # a third non-Xero hotel confirms it: memory now proposes at 0.9
    _hotel(cur, "mem-hotel-9")
    b9 = _write(cur, "mem-hotel-9", AUG, [("9", "Banquet Hall Hire", "income", 1000)])
    mapping.confirm_one(cur, "mem-hotel-9", review.lines_for_batch(cur, b9)[0]["account_id"], actor_id=None,
                        usali_line="fnb.revenue")
    mapping.rebuild_memory(cur)
    b = _write(cur, hotel_uae.slug, SEP, [("9", "Banquet Hall Hire", "income", 1000)], sha="again")
    live = mapping.live_map(cur, review.lines_for_batch(cur, b)[0]["account_id"])
    assert live["usali_line"] == "fnb.revenue" and live["learned_from"] == "global_memory"
    assert float(live["ai_confidence"]) == 0.9
    # the name is also used by Xero hotels, but no Xero row fed the memory
    cur.execute("SELECT confirmations FROM fin_mapping_memory WHERE name_norm = 'banquet hall hire'")
    assert cur.fetchone()["confirmations"] == 3


def test_few_shot_is_own_hotel_only(cur, hotel_uae, neighbours):
    _hotel(cur, "other-xero")
    b = _write(cur, "other-xero", AUG, [("1", "Xero Secret Line", "income", 10)], source="xero")
    mapping.confirm_one(cur, "other-xero", review.lines_for_batch(cur, b)[0]["account_id"], actor_id=None,
                        usali_line="misc.income")
    assert mapping.few_shot(cur, hotel_uae.slug) == []
    assert [e["name_raw"] for e in mapping.few_shot(cur, "other-xero")] == ["Xero Secret Line"]
