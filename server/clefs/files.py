"""
Files: one store for uploads, inbound email attachments, WhatsApp media and
generated documents (dispute packets, reports).

    ref = put_file(cur, slug, data, filename="booking_sep.csv", content_type="text/csv",
                   purpose="profit.statement", source="upload", actor=ctx, retention_days=365)
    ref, data = get_file(cur, slug, ref.id)
    url = signed_path(slug, ref.id)          # "/hotels/<slug>/files/<id>?t=..." valid 5 minutes

Storage sits behind the FileStore interface. The default keeps bytes in
Postgres (bytea): no new cloud for the demo and pilots, one backup covers
everything, and 25 MB (FILE_MAX_BYTES) is far above any OTA statement. The S3
adapter slot exists for UAE-resident hosting (FILE_STORE=s3).

Dedupe: the same bytes uploaded twice to one hotel resolve to the same file
(sha256), so re-importing a statement never duplicates it; the longer
retention of the two wins. Retention: retention_until is set by the caller
(purpose-specific); core.files.purge drops the bytes after it.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Protocol

import psycopg2
from pydantic import BaseModel

from clefs import jobs
from clefs.auth import tokens
from clefs.core import clock
from clefs.core.errors import ClefsError, NotConfigured
from clefs.settings import get_settings

SOURCES = ("upload", "email", "whatsapp", "generated", "import", "api")
PURPOSE_RE = re.compile(r"^[a-z0-9_.:-]{1,64}$")
DOWNLOAD_TTL_S = 300
FILE_COLUMNS = (
    "id, hotel_slug, sha256, filename, content_type, size_bytes, storage, s3_key, source, purpose, "
    "uploaded_by, uploaded_by_device, retention_until, created_at, deleted_at"
)


class FileRef(BaseModel):
    """The API shape of a stored file (the client's FileRef)."""

    id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str


def _ref(row: dict) -> FileRef:
    return FileRef(
        id=str(row["id"]),
        filename=row["filename"],
        content_type=row["content_type"],
        size_bytes=int(row["size_bytes"]),
        sha256=row["sha256"],
    )


# ─── Stores ───


class FileStore(Protocol):
    name: str

    def put(self, file_id: str, data: bytes) -> dict:
        """Column values to store: {"storage", "data", "s3_key"}."""

    def get(self, row: dict) -> bytes: ...

    def delete(self, row: dict) -> dict:
        """Column values after deleting the bytes."""


class DbFileStore:
    name = "db"

    def put(self, file_id: str, data: bytes) -> dict:
        return {"storage": "db", "data": data, "s3_key": None}

    def get(self, row: dict) -> bytes:
        if row.get("data") is None:
            raise ClefsError(410, "This file's contents were deleted under the retention policy", "file_purged")
        return bytes(row["data"])

    def delete(self, row: dict) -> dict:
        return {"data": None}


class S3FileStore:
    """Slot for UAE-resident deployments (me-central-1). Not wired yet."""

    name = "s3"

    def put(self, file_id: str, data: bytes) -> dict:
        raise NotConfigured("S3 file storage")

    def get(self, row: dict) -> bytes:
        raise NotConfigured("S3 file storage")

    def delete(self, row: dict) -> dict:
        raise NotConfigured("S3 file storage")


_store_override: FileStore | None = None


def get_store() -> FileStore:
    if _store_override is not None:
        return _store_override
    return S3FileStore() if get_settings().FILE_STORE == "s3" else DbFileStore()


def set_store(store: FileStore | None) -> None:
    """Tests (or a future adapter) swap the store."""
    global _store_override
    _store_override = store


# ─── Helpers ───


def safe_filename(name: str | None) -> str:
    """Last path component, control characters stripped, at most 200 chars."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip().strip(".") or "file"
    if len(base) > 200:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 190] + dot + ext[:9]) if dot else base[:200]
    return base


def _actor_ids(actor: Any) -> tuple[int | None, int | None]:
    principal = getattr(actor, "principal", actor)
    kind = getattr(principal, "kind", None)
    if kind == "user":
        return principal.user_id, None
    if kind == "device":
        return None, principal.device_id
    return None, None


# ─── Put / get ───


def put_file(
    cur: Any,
    hotel_slug: str | None,
    data: bytes,
    *,
    filename: str,
    content_type: str | None,
    purpose: str,
    source: str = "upload",
    actor: Any = None,
    uploaded_by: int | None = None,
    uploaded_by_device: int | None = None,
    retention_days: int | None = None,
    retention_until: datetime | None = None,
) -> FileRef:
    """Store bytes (joins the caller's transaction). 413 over FILE_MAX_BYTES.
    Returns the existing FileRef when the hotel already has these bytes."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if not PURPOSE_RE.match(purpose or ""):
        raise ClefsError(400, "purpose must be a short lowercase key", "invalid_purpose")
    max_bytes = get_settings().FILE_MAX_BYTES
    if len(data) > max_bytes:
        raise ClefsError(413, f"Files can be at most {max_bytes // (1024 * 1024)} MB", "file_too_large")
    digest = hashlib.sha256(data).hexdigest()
    if retention_until is None and retention_days is not None:
        retention_until = clock.now() + timedelta(days=retention_days)
    user_id, device_id = _actor_ids(actor)
    user_id = uploaded_by if uploaded_by is not None else user_id
    device_id = uploaded_by_device if uploaded_by_device is not None else device_id

    cur.execute(
        f"SELECT {FILE_COLUMNS} FROM files WHERE COALESCE(hotel_slug, '') = COALESCE(%s, '') AND sha256 = %s "
        "AND deleted_at IS NULL FOR UPDATE",
        (hotel_slug, digest),
    )
    existing = cur.fetchone()
    if existing is not None:
        if existing["retention_until"] is not None and (retention_until is None or retention_until > existing["retention_until"]):
            cur.execute("UPDATE files SET retention_until = %s WHERE id = %s", (retention_until, existing["id"]))
        return _ref(dict(existing))

    file_id = str(uuid.uuid4())
    stored = get_store().put(file_id, data)
    cur.execute(
        f"""
        INSERT INTO files (id, hotel_slug, sha256, filename, content_type, size_bytes, storage, data, s3_key, source,
                           purpose, uploaded_by, uploaded_by_device, retention_until, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {FILE_COLUMNS}
        """,
        (
            file_id,
            hotel_slug,
            digest,
            safe_filename(filename),
            (content_type or "application/octet-stream")[:200],
            len(data),
            stored["storage"],
            psycopg2.Binary(stored["data"]) if stored.get("data") is not None else None,
            stored.get("s3_key"),
            source,
            purpose,
            user_id,
            device_id,
            retention_until,
            clock.now(),
        ),
    )
    return _ref(dict(cur.fetchone()))


def file_row(cur: Any, hotel_slug: str | None, file_id: str, *, with_data: bool = False) -> dict:
    try:
        uuid.UUID(str(file_id))
    except ValueError as exc:
        raise ClefsError(404, "File not found", "file_not_found") from exc
    cols = FILE_COLUMNS + (", data" if with_data else "")
    cur.execute(
        f"SELECT {cols} FROM files WHERE id = %s AND COALESCE(hotel_slug, '') = COALESCE(%s, '') AND deleted_at IS NULL",
        (str(file_id), hotel_slug),
    )
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "File not found", "file_not_found")
    return dict(row)


def get_file(cur: Any, hotel_slug: str | None, file_id: str) -> tuple[FileRef, bytes]:
    """The file and its bytes; 404 if it is not this hotel's, 410 if purged."""
    row = file_row(cur, hotel_slug, file_id, with_data=True)
    return _ref(row), get_store().get(row)


def delete_file(cur: Any, hotel_slug: str | None, file_id: str) -> None:
    row = file_row(cur, hotel_slug, file_id, with_data=True)
    changes = get_store().delete(row)
    cur.execute(
        "UPDATE files SET data = %s, deleted_at = %s WHERE id = %s",
        (changes.get("data"), clock.now(), row["id"]),
    )


# ─── Signed download paths ───


def signed_path(hotel_slug: str, file_id: str, *, ttl_s: int = DOWNLOAD_TTL_S) -> str:
    """A short-lived download path on the API origin (for WhatsApp document
    sends and links in emails), checked by GET /hotels/{slug}/files/{id}?t=."""
    t = tokens.mint_ticket("download", {"h": hotel_slug, "f": str(file_id)}, ttl_s=ttl_s)
    return f"/hotels/{hotel_slug}/files/{file_id}?t={t}"


def verify_download(hotel_slug: str, file_id: str, t: str | None) -> bool:
    if not t:
        return False
    claims = tokens.verify_ticket("download", t)
    return bool(claims and claims.get("h") == hotel_slug and claims.get("f") == str(file_id))


# ─── Retention ───


@jobs.scheduled("core.files.purge", jobs.daily_at("03:40", tz="utc"), per_hotel=False, timeout_s=300)
def _purge(ctx: jobs.JobCtx) -> dict:
    """Drop the bytes of files past retention_until (the row stays as a tombstone
    so references and audit entries still resolve to a filename)."""
    ctx.cur.execute(
        f"SELECT {FILE_COLUMNS}, data FROM files WHERE deleted_at IS NULL AND retention_until IS NOT NULL "
        "AND retention_until <= %s LIMIT 1000",
        (ctx.now,),
    )
    rows = [dict(r) for r in ctx.cur.fetchall()]
    for row in rows:
        changes = get_store().delete(row)
        ctx.cur.execute(
            "UPDATE files SET data = %s, deleted_at = %s WHERE id = %s", (changes.get("data"), ctx.now, row["id"])
        )
    return {"purged": len(rows)}
