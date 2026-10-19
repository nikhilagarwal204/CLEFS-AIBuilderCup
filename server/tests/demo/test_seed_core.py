"""Demo seed, core part: the property, people and idempotency (02 spec 9)."""

from __future__ import annotations

from datetime import date

import pytest

from clefs.core.errors import ClefsError
from clefs.seed import demo_uae

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-core"


def _seed(cur, **kw):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=["core"], password="demo-pass-123", **kw)


def test_core_part_builds_the_property(cur):
    out = _seed(cur)
    assert out["created"] is True
    core = out["parts"]["core"]
    assert core["rooms"] == 80 and core["staff"] == 18 and core["users"] == 6
    cur.execute("SELECT room_type_code, count(*) AS n FROM hotel_rooms WHERE hotel_slug = %s GROUP BY 1", (SLUG,))
    assert {r["room_type_code"]: r["n"] for r in cur.fetchall()} == {"STD_KING": 40, "STD_TWIN": 30, "FAM_2BR": 10}
    cur.execute("SELECT count(DISTINCT floor) AS f, count(*) FILTER (WHERE bedrooms = 2) AS fam FROM hotel_rooms "
                "WHERE hotel_slug = %s", (SLUG,))
    row = cur.fetchone()
    assert (row["f"], row["fam"]) == (8, 10)
    cur.execute("SELECT name, status, star_rating, currency, timezone FROM hotels WHERE slug = %s", (SLUG,))
    h = cur.fetchone()
    assert h["status"] == "demo" and h["star_rating"] == 3
    cur.execute("SELECT count(*) AS n FROM staff_members WHERE hotel_slug = %s "
                "AND pin_hash IS NOT NULL", (SLUG,))
    assert cur.fetchone()["n"] == 18
    assert len(out["credentials"]["staff_pins"]) == 18


def test_reseed_is_idempotent_and_refuses_real_hotels(cur, hotel_uae):
    _seed(cur)
    again = _seed(cur)
    assert again["created"] is False
    cur.execute("SELECT count(*) AS n FROM hotel_rooms WHERE hotel_slug = %s", (SLUG,))
    assert cur.fetchone()["n"] == 80
    cur.execute("SELECT count(*) AS n FROM staff_members WHERE hotel_slug = %s", (SLUG,))
    assert cur.fetchone()["n"] == 18
    with pytest.raises(ClefsError) as exc:
        demo_uae.seed(cur, hotel_uae.slug, anchor=ANCHOR, parts=["core"])
    assert exc.value.code == "not_demo"


def test_catalog_part_writes_kb_menu_comps_and_contracts(cur):
    out = demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=["core", "core_catalog"], password="demo-pass-123")
    cat = out["parts"]["core_catalog"]
    assert cat["kb_entries"] == 16 and cat["comp_set"] == 5 and cat["contracts"] == 3
    assert cat["menu_items"] >= 20 and cat["pairings"] >= 6
    cur.execute("SELECT fact_key, fact_value FROM cc_kb_entries WHERE hotel_slug = %s AND status = 'approved' "
                "AND fact_key IS NOT NULL", (SLUG,))
    facts = {r["fact_key"]: r["fact_value"] for r in cur.fetchall()}
    assert facts["checkin_time"] == "14:00" and facts["parking"] == "free"
    cur.execute("SELECT count(*) AS n FROM cc_kb_entries WHERE hotel_slug = %s AND answer_ar IS NULL", (SLUG,))
    assert cur.fetchone()["n"] == 0
    cur.execute("SELECT count(*) AS n FROM dr_comp_set WHERE hotel_slug = %s", (SLUG,))
    assert cur.fetchone()["n"] == 5


def test_full_reseed_across_parts_is_idempotent(cur):
    parts = ["core", "core_catalog", "core_stays"]
    first = demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=parts, scale=0.05, password="demo-pass-123")

    def fingerprint():
        cur.execute("SELECT pms_res_id, channel_code, status, check_in, check_out, room_number, room_revenue_minor, "
                    "guest_name FROM reservations WHERE hotel_slug = %s ORDER BY pms_res_id", (SLUG,))
        res = [tuple(r.values()) for r in cur.fetchall()]
        cur.execute("SELECT date, rooms_occupied, room_revenue_minor, source FROM occupancy_daily "
                    "WHERE hotel_slug = %s ORDER BY date", (SLUG,))
        occ = [tuple(r.values()) for r in cur.fetchall()]
        cur.execute("SELECT count(*) AS n, SUM(amount_minor) AS s FROM folio_charges WHERE hotel_slug = %s", (SLUG,))
        return res, occ, tuple(cur.fetchone().values())

    before = fingerprint()
    second = demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=parts, scale=0.05, password="demo-pass-123")
    assert second["created"] is False and second["parts"]["core_stays"] == first["parts"]["core_stays"]
    assert fingerprint() == before
    assert second["parts"]["core_catalog"]["kb_entries"] == 16


def test_the_wipe_empties_every_hotel_table(cur):
    _seed(cur)
    again = _seed(cur)
    assert again["wipe"]["kept"] == [] and again["wipe"]["tables"] > 50


def test_part_selection_is_validated(cur):
    with pytest.raises(ValueError, match="unknown demo part"):
        demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=["core", "nope"])
    with pytest.raises(ValueError, match="include core"):
        demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=["core_stays"])
    names = [name for _o, name, _f in demo_uae.discover_parts()]
    assert names[:3] == ["core", "core_catalog", "core_stays"]
