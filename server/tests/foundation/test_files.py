"""
Files (01 spec E7): the bytea store, sha256 dedupe, the size cap, tenant
isolation, retention purge, upload through the cookie or a direct-upload
ticket, downloads with permission checks and signed links.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from clefs import files, jobs
from clefs.core import hotels
from clefs.core.errors import ClefsError

UTC = timezone.utc
CSV = b"reservation,commission\n4000012345,84.70\n"


def test_put_get_and_sha256_dedupe(cur, hotel_uae, clock):
    now = clock.freeze(datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    ref = files.put_file(cur, hotel_uae.slug, CSV, filename="../../etc/booking sep.csv", content_type="text/csv",
                         purpose="profit.statement", retention_days=30)
    assert ref.model_dump().keys() == {"id", "filename", "content_type", "size_bytes", "sha256"}
    assert ref.filename == "booking sep.csv" and ref.size_bytes == len(CSV) and ref.sha256 == hashlib.sha256(CSV).hexdigest()
    same, data = files.get_file(cur, hotel_uae.slug, ref.id)
    assert same == ref and data == CSV
    dup = files.put_file(cur, hotel_uae.slug, CSV, filename="again.csv", content_type="text/csv", purpose="profit.statement",
                         retention_days=365)
    assert dup.id == ref.id
    cur.execute("SELECT retention_until FROM files WHERE id = %s", (ref.id,))
    assert cur.fetchone()["retention_until"] == now + timedelta(days=365)  # the longer retention wins
    other = hotels.create_hotel(cur, slug="other-dubai", name="Other")
    assert files.put_file(cur, other.slug, CSV, filename="x.csv", content_type="text/csv", purpose="profit.statement").id != ref.id


def test_isolation_and_bad_ids(cur, hotel_uae):
    ref = files.put_file(cur, hotel_uae.slug, CSV, filename="a.csv", content_type="text/csv", purpose="profit.statement")
    for slug, fid in (("hotel-nandan", ref.id), (hotel_uae.slug, "not-a-uuid"), (hotel_uae.slug, "00000000-0000-0000-0000-000000000000")):
        with pytest.raises(ClefsError) as exc:
            files.get_file(cur, slug, fid)
        assert exc.value.status == 404


def test_size_cap_and_purpose_rules(cur, hotel_uae, env):
    env(FILE_MAX_BYTES="10")
    with pytest.raises(ClefsError) as exc:
        files.put_file(cur, hotel_uae.slug, b"x" * 11, filename="big.bin", content_type=None, purpose="core.test")
    assert exc.value.status == 413
    with pytest.raises(ClefsError) as exc:
        files.put_file(cur, hotel_uae.slug, b"x", filename="a", content_type=None, purpose="Bad Purpose")
    assert exc.value.code == "invalid_purpose"
    with pytest.raises(ValueError):
        files.put_file(cur, hotel_uae.slug, b"x", filename="a", content_type=None, purpose="core.test", source="carrier-pigeon")


def test_retention_purge_drops_the_bytes(cur, hotel_uae, clock):
    clock.freeze(datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    keep = files.put_file(cur, hotel_uae.slug, b"keep", filename="k.txt", content_type="text/plain", purpose="core.test")
    gone = files.put_file(cur, hotel_uae.slug, b"gone", filename="g.txt", content_type="text/plain", purpose="core.test", retention_days=1)
    clock.freeze(datetime(2026, 10, 3, 3, 45, tzinfo=UTC))  # after the 03:40 UTC purge slot
    jobs.tick(only=["core.files.purge"], queue=False)
    cur.execute("SELECT id::TEXT AS id, data IS NULL AS purged, deleted_at IS NOT NULL AS deleted FROM files WHERE hotel_slug = %s", (hotel_uae.slug,))
    state = {r["id"]: (r["purged"], r["deleted"]) for r in cur.fetchall()}
    assert state == {keep.id: (False, False), gone.id: (True, True)}
    with pytest.raises(ClefsError):
        files.get_file(cur, hotel_uae.slug, gone.id)


def test_safe_filename():
    assert files.safe_filename("C:\\Users\\me\\statement.pdf") == "statement.pdf"
    assert files.safe_filename("\x00\x07evil.csv") == "evil.csv"
    assert files.safe_filename("") == "file"
    long = files.safe_filename("a" * 300 + ".xlsx")
    assert len(long) <= 200 and long.endswith(".xlsx")


# ─── Routes ───


def _upload(c, url, *, purpose="profit.statement", data=CSV, name="booking.csv"):
    return c.post(url, files={"file": (name, data, "text/csv")}, data={"purpose": purpose})


def test_cookie_upload_and_download(cur, client, client_as, hotel_uae):
    owner = client_as("owner")
    r = _upload(owner, f"/hotels/{hotel_uae.slug}/files")
    assert r.status_code == 200, r.text
    ref = r.json()
    assert set(ref) == {"id", "filename", "content_type", "size_bytes", "sha256"} and ref["size_bytes"] == len(CSV)
    cur.execute("SELECT action, meta FROM audit_log WHERE entity_type = 'file' AND entity_id = %s", (ref["id"],))
    assert cur.fetchone()["meta"]["purpose"] == "profit.statement"
    d = owner.get(f"/hotels/{hotel_uae.slug}/files/{ref['id']}")
    assert d.status_code == 200 and d.content == CSV and d.headers["content-type"].startswith("text/csv")
    assert 'filename="booking.csv"' in d.headers["content-disposition"] and d.headers["cache-control"] == "private, no-store"

    staff = client_as("staff")
    assert staff.get(f"/hotels/{hotel_uae.slug}/files/{ref['id']}").status_code == 403
    own = _upload(staff, f"/hotels/{hotel_uae.slug}/files", purpose="service.lost_found", data=b"photo", name="bag.jpg").json()
    assert staff.get(f"/hotels/{hotel_uae.slug}/files/{own['id']}").status_code == 200  # the uploader can read it back
    assert client.get(f"/hotels/{hotel_uae.slug}/files/{ref['id']}").status_code == 401
    assert _upload(client, f"/hotels/{hotel_uae.slug}/files").status_code == 401
    assert _upload(owner, f"/hotels/{hotel_uae.slug}/files", purpose="Nope!").status_code == 400


def test_direct_upload_with_a_ticket(cur, app, client_as, hotel_uae):
    from fastapi.testclient import TestClient

    owner = client_as("owner")
    t = owner.post("/files/upload-ticket", json={"hotel_slug": hotel_uae.slug, "purpose": "profit.statement"}).json()
    anon = TestClient(app)  # no cookies: the direct upload to the API origin
    r = _upload(anon, t["url"])
    assert r.status_code == 200, r.text
    cur.execute("SELECT uploaded_by FROM files WHERE id = %s", (r.json()["id"],))
    assert cur.fetchone()["uploaded_by"] == owner.user["id"]
    assert _upload(anon, t["url"], purpose="finance.pl").status_code == 403  # the ticket is bound to its purpose
    other = hotels.create_hotel(cur, slug="other-dubai", name="Other")
    assert _upload(anon, t["url"].replace(hotel_uae.slug, other.slug)).status_code == 403
    assert _upload(anon, f"/hotels/{hotel_uae.slug}/files?t=garbage").status_code == 401


def test_signed_download_links(cur, app, hotel_uae):
    from fastapi.testclient import TestClient

    ref = files.put_file(cur, hotel_uae.slug, b"%PDF-1.4", filename="packet.pdf", content_type="application/pdf", purpose="profit.dispute")
    path = files.signed_path(hotel_uae.slug, ref.id)
    c = TestClient(app)
    assert c.get(path).status_code == 200
    other = files.put_file(cur, hotel_uae.slug, b"other", filename="o.txt", content_type="text/plain", purpose="core.test")
    assert c.get(path.replace(ref.id, other.id)).status_code == 401  # a link opens one file only
    assert c.get(f"/hotels/{hotel_uae.slug}/files/{ref.id}?t=forged").status_code == 401


def test_magic_sessions_cannot_upload(cur, app, make_user, hotel_uae):
    from fastapi.testclient import TestClient

    from clefs.auth import magic

    u = make_user("owner", hotel_uae.slug)
    c = TestClient(app)
    c.post("/auth/magic/exchange", json={"token": magic.create_link(cur, user_id=u["id"], hotel_slug=hotel_uae.slug, purpose="brief")})
    assert _upload(c, f"/hotels/{hotel_uae.slug}/files").status_code == 403
