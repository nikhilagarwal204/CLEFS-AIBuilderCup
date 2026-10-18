"""Tenant export (03 spec 5.3): owner only, a zip via clefs.files, a 7-day link."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timedelta

from clefs.core import clock as core_clock
from clefs.files import get_file


def test_owner_exports_hotel_data_without_secrets(cur, hotel_uae, client_as, run_jobs, legacy_slug):
    slug = hotel_uae.slug
    cur.execute(
        "INSERT INTO staff_requests (hotel_slug, room, type, description, timestamp) VALUES (%s, '101', 'Cleaning', 'towels', 1)",
        (slug,),
    )
    cur.execute(
        "INSERT INTO staff_requests (hotel_slug, room, type, description, timestamp) VALUES (%s, '9', 'Cleaning', 'other hotel', 1)",
        (legacy_slug,),
    )
    owner = client_as("owner")
    assert client_as("gm").post(f"/hotels/{slug}/trust/export").status_code == 403
    r = owner.post(f"/hotels/{slug}/trust/export")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert owner.post(f"/hotels/{slug}/trust/export").json()["code"] == "export_in_progress"
    run_jobs(["trust.tenant_export"])
    exports = owner.get(f"/hotels/{slug}/trust/exports").json()
    ready = exports[0]
    assert ready["status"] == "ready" and ready["download_url"].startswith(f"/hotels/{slug}/files/")
    left = datetime.fromisoformat(ready["expires_at"]) - core_clock.now()
    assert timedelta(days=6, hours=23) < left <= timedelta(days=7)
    _, blob = get_file(cur, slug, ready["file_id"])
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = set(z.namelist())
    assert {"manifest.json", "hotels.csv", "staff_requests.csv", "hotel_memberships.csv"} <= names
    assert not names & {"auth_sessions.csv", "magic_links.csv", "oauth_states.csv", "job_queue.csv"}
    reqs = list(csv.DictReader(io.StringIO(z.read("staff_requests.csv").decode("utf-8-sig"))))
    assert [x["description"] for x in reqs] == ["towels"]
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["tables"]["staff_requests"] == 1 and manifest["hotel_slug"] == slug
    cur.execute("SELECT retention_until FROM files WHERE id = %s", (ready["file_id"],))
    assert cur.fetchone()["retention_until"] <= core_clock.now() + timedelta(days=7, minutes=1)
    cur.execute("SELECT action FROM audit_log WHERE hotel_slug = %s AND action LIKE 'trust.export.%%' ORDER BY id", (slug,))
    assert [x["action"] for x in cur.fetchall()] == ["trust.export.request", "trust.export.ready"]


def test_stale_export_does_not_block_a_new_one(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    owner = client_as("owner")
    first = owner.post(f"/hotels/{slug}/trust/export").json()
    cur.execute("UPDATE trs_exports SET created_at = created_at - interval '2 hours' WHERE id = %s", (first["id"],))
    assert owner.post(f"/hotels/{slug}/trust/export").status_code == 202
    statuses = {x["id"]: x["status"] for x in owner.get(f"/hotels/{slug}/trust/exports").json()}
    assert statuses[first["id"]] == "failed"
