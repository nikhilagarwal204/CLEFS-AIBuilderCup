"""Audit viewer and hash-chain verification (03 spec 5.1)."""

from __future__ import annotations

import csv
import io

from clefs.audit import audit


def _write(cur, slug, action="core.settings.update", *, before=None, after=None, entity="hotel", eid="x"):
    audit(cur, None, action, entity, eid, before=before, after=after, hotel_slug=slug)
    cur.execute("SELECT max(id) AS id FROM audit_log WHERE hotel_slug = %s", (slug,))
    return cur.fetchone()["id"]


# ─── Viewer ───


def test_owner_lists_filters_and_pages(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    for i in range(3):
        _write(cur, slug, "service.request.create", eid=str(i))
    _write(cur, slug, "core.settings.update", before={"name": "A"}, after={"name": "B"})
    c = client_as("owner")
    r = c.get(f"/hotels/{slug}/trust/audit", params={"action": "service.", "limit": 2})
    assert r.status_code == 200, r.text
    page = r.json()
    assert [x["action"] for x in page["items"]] == ["service.request.create"] * 2
    assert page["next_before_id"] is not None
    r2 = c.get(f"/hotels/{slug}/trust/audit", params={"action": "service.", "before_id": page["next_before_id"]})
    assert len(r2.json()["items"]) == 1 and r2.json()["next_before_id"] is None
    r3 = c.get(f"/hotels/{slug}/trust/audit", params={"action": "core.settings.update"})
    assert r3.json()["items"][0]["has_diff"] is True
    facets = c.get(f"/hotels/{slug}/trust/audit/facets").json()
    assert "service.request.create" in facets["actions"] and "hotel" in facets["entity_types"]


def test_date_filters_use_hotel_local_days(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    _write(cur, slug, "a.one")
    c = client_as("gm")
    cur.execute("SELECT (at AT TIME ZONE 'Asia/Dubai')::date AS d FROM audit_log WHERE hotel_slug = %s ORDER BY id DESC LIMIT 1", (slug,))
    d = cur.fetchone()["d"].isoformat()
    assert c.get(f"/hotels/{slug}/trust/audit", params={"from": d, "to": d, "action": "a.one"}).json()["items"]
    r = c.get(f"/hotels/{slug}/trust/audit", params={"from": "not-a-date"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_date"


def test_entry_diff_and_hash(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    eid = _write(cur, slug, before={"name": "Old", "keep": 1, "gone": True}, after={"name": "New", "keep": 1, "new": "x"})
    r = client_as("owner").get(f"/hotels/{slug}/trust/audit/{eid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hash_ok"] is True and len(body["row_hash"]) == 64
    assert {(d["path"], d["op"]) for d in body["diff"]} == {("gone", "removed"), ("name", "changed"), ("new", "added")}


def test_other_hotels_rows_are_invisible(cur, hotel_uae, client_as, legacy_slug):
    other = _write(cur, legacy_slug, "x.other")
    c = client_as("owner")
    assert c.get(f"/hotels/{hotel_uae.slug}/trust/audit/{other}").status_code == 404
    assert not c.get(f"/hotels/{hotel_uae.slug}/trust/audit", params={"action": "x.other"}).json()["items"]


def test_only_owner_and_gm_read_the_log(hotel_uae, client_as):
    for role in ("finance", "revenue", "dept_head", "front_desk", "staff"):
        assert client_as(role).get(f"/hotels/{hotel_uae.slug}/trust/audit").status_code == 403, role


def test_gm_sees_masked_ip_owner_full(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    # A direct INSERT goes through the chain trigger like audit() does (no
    # request context here to supply an IP).
    cur.execute(
        "INSERT INTO audit_log (hotel_slug, actor_type, action, ip) VALUES (%s, 'system', 'ip.test', '203.0.113.7') RETURNING id",
        (slug,),
    )
    eid = cur.fetchone()["id"]
    assert client_as("owner").get(f"/hotels/{slug}/trust/audit/{eid}").json()["ip"] == "203.0.113.7"
    assert client_as("gm").get(f"/hotels/{slug}/trust/audit/{eid}").json()["ip"] == "203.0.x.x"


def test_csv_export_escapes_formulas_and_is_audited(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    _write(cur, slug, "service.request.create", eid="=HYPERLINK(\"http://evil\")", after={"description": "+1 towel"})
    c = client_as("owner")
    r = c.get(f"/hotels/{slug}/trust/audit/export.csv", params={"action": "service.request.create"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    assert rows[0][0] == "id" and len(rows) == 2
    header = rows[0]
    assert rows[1][header.index("entity_id")].startswith("'=")
    assert rows[1][header.index("changed_fields")] == "description"
    cur.execute("SELECT count(*) AS n FROM audit_log WHERE hotel_slug = %s AND action = 'trust.audit.export'", (slug,))
    assert cur.fetchone()["n"] == 1
