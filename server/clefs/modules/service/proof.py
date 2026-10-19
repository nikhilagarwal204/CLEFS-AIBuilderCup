"""
Proof before closure (05 spec, decisions 8 and 9).

"Mark as Done" on a job whose type needs a photo uploads one (POST
/device/service/requests/{id}/proof), and Gemini's vision checks it against
the job inside that same call (the default model, with the AI layer's own
fallback to the fast one):

  match      the job completes (state completed, proof_status verified)
  mismatch   nothing moves: proof_status rejected, proof_reason says why,
             the supervisor on shift is alerted, the staff app shows the
             reason in the worker's language
  no check   AI off, slow or failing: proof_status unverified, the job stays
             open and the supervisor is asked to look (a manager can still
             complete it from the console)

Storage: a Cloud Storage bucket when PROOF_BUCKET is set (the bucket's
lifecycle rule deletes objects after 7 days), else Postgres
(svc_request_proofs.data; the daily service.proof_retention job drops the
bytes after 7 days). The console sees a photo through photo_path(): a
5-minute signed link on the API, the same pattern as files.signed_path.

The model calls and the bucket upload run between two short transactions,
never inside one. The client downsizes photos (1280 px JPEG) before upload.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import timedelta
from typing import Any, Protocol

import psycopg2
from pydantic import BaseModel

from clefs import ai, db, events
from clefs.auth import tokens
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.service import constants as C
from clefs.modules.service import escalation, staff_lang, state
from clefs.modules.service import settings as svc_settings
from clefs.settings import ai_model_for, env

log = logging.getLogger("clefs.service.proof")

MAX_BYTES = 3 * 1024 * 1024
MAGIC = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
KEEP_DAYS = 7
# A "match" below this confidence is not proof enough to close a job.
MIN_CONFIDENCE = 0.6
CHECK_TIMEOUT_S = 20.0
PHOTO_LINK_TTL_S = 300
TICKET = "svc.proof"
OPEN_TO_COMPLETE = ("assigned", "acknowledged", "in_progress")


class ProofCheck(BaseModel):
    """The vision verdict (05 spec, section 2 item 5)."""

    match: bool
    confidence: float
    reason: str
    observed: str


# ─── Storage ───


class ProofStore(Protocol):
    name: str

    def put(self, key: str, data: bytes, content_type: str) -> dict:
        """Column values: {"storage", "object_key", "data"}."""

    def get(self, row: dict) -> bytes: ...

    def delete(self, row: dict) -> None: ...


class DbProofStore:
    """Postgres bytea (tests, local work, a deploy without a bucket)."""

    name = "db"

    def put(self, key: str, data: bytes, content_type: str) -> dict:
        return {"storage": "db", "object_key": None, "data": data}

    def get(self, row: dict) -> bytes:
        if row.get("data") is None:
            raise ClefsError(410, "This photo was deleted after 7 days", "photo_deleted")
        return bytes(row["data"])

    def delete(self, row: dict) -> None:
        return None  # the caller clears the column


class GcsProofStore:
    """A Cloud Storage bucket (PROOF_BUCKET). Credentials are the service's
    own identity on Cloud Run (Application Default Credentials). `client`
    lets tests pass a fake with the same bucket()/blob() calls."""

    name = "gcs"

    def __init__(self, bucket: str, client: Any = None) -> None:
        self.bucket_name = bucket
        self._client = client

    def _bucket(self) -> Any:
        if self._client is None:
            from google.cloud import storage  # imported here: only a deploy with a bucket needs it

            self._client = storage.Client()
        return self._client.bucket(self.bucket_name)

    def _blob_name(self, row: dict) -> str:
        key = row.get("object_key") or ""
        prefix = f"gs://{self.bucket_name}/"
        return key[len(prefix):] if key.startswith(prefix) else key

    def put(self, key: str, data: bytes, content_type: str) -> dict:
        self._bucket().blob(key).upload_from_string(data, content_type=content_type)
        return {"storage": "gcs", "object_key": f"gs://{self.bucket_name}/{key}", "data": None}

    def get(self, row: dict) -> bytes:
        try:
            return self._bucket().blob(self._blob_name(row)).download_as_bytes()
        except Exception as exc:  # NotFound after the lifecycle rule, or the bucket is unreachable
            if type(exc).__name__ == "NotFound":
                raise ClefsError(410, "This photo was deleted after 7 days", "photo_deleted") from exc
            raise ClefsError(502, "The photo store could not be reached", "photo_store_error") from exc

    def delete(self, row: dict) -> None:
        try:
            self._bucket().blob(self._blob_name(row)).delete()
        except Exception as exc:  # already gone (the lifecycle rule) is fine
            if type(exc).__name__ != "NotFound":
                raise


_store_override: ProofStore | None = None


def get_store() -> ProofStore:
    """The bucket when PROOF_BUCKET is set and the client library is
    installed, else Postgres."""
    if _store_override is not None:
        return _store_override
    bucket = (env("PROOF_BUCKET") or "").strip()
    if bucket:
        try:
            from google.cloud import storage  # noqa: F401  (availability check only)

            return GcsProofStore(bucket)
        except ImportError:
            log.warning("PROOF_BUCKET is set but google-cloud-storage is not installed: photos stay in Postgres")
    return DbProofStore()


def set_store(store: ProofStore | None) -> None:
    """Tests swap the store (a fake bucket)."""
    global _store_override
    _store_override = store


def store_for(row: dict) -> ProofStore:
    """The store that holds this proof's bytes (rows outlive a config change)."""
    current = get_store()
    if row.get("storage") == current.name:
        return current
    if row.get("storage") == "gcs":
        key = (row.get("object_key") or "")[len("gs://"):]
        return GcsProofStore(key.split("/", 1)[0])
    return DbProofStore()


# ─── Photos ───


def sniff(data: bytes, declared: str | None) -> str:
    """The photo's real type: JPEG or PNG by its first bytes. 413 over 3 MB."""
    if len(data) > MAX_BYTES:
        raise ClefsError(413, "The photo can be at most 3 MB", "photo_too_large")
    for kind, magic in MAGIC.items():
        if data.startswith(magic):
            return kind
    raise ClefsError(415, "Send the photo as a JPEG or PNG", "unsupported_photo")


def photo_path(row: dict) -> str | None:
    """A 5-minute link to the request's latest photo on the API origin (the
    console prefixes the API base URL), or None without one."""
    proof_id = row.get("proof_id")
    if not proof_id:
        return None
    # "p" is the ticket's own purpose claim: the proof id travels as "pid"
    t = tokens.mint_ticket(TICKET, {"h": row["hotel_slug"], "r": int(row["id"]), "pid": int(proof_id)},
                           ttl_s=PHOTO_LINK_TTL_S)
    return f"/hotels/{row['hotel_slug']}/service/requests/{row['id']}/proof-photo?t={t}"


def verify_link(hotel_slug: str, request_id: int, t: str | None) -> int | None:
    """The proof id a photo link grants, or None when it is not valid here."""
    claims = tokens.verify_ticket(TICKET, t) if t else None
    if not claims or claims.get("h") != hotel_slug or claims.get("r") != int(request_id) or not claims.get("pid"):
        return None
    return int(claims["pid"])


def photo_row(cur: Any, hotel_slug: str, request_id: int, proof_id: int | None = None) -> dict:
    """One proof of the request (the latest when proof_id is None). 404
    without one, 410 once retention deleted its photo."""
    if proof_id is None:
        cur.execute("SELECT * FROM svc_request_proofs WHERE hotel_slug = %s AND request_id = %s ORDER BY id DESC LIMIT 1",
                    (hotel_slug, request_id))
    else:
        cur.execute("SELECT * FROM svc_request_proofs WHERE hotel_slug = %s AND request_id = %s AND id = %s",
                    (hotel_slug, request_id, proof_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "No photo for this request", "photo_not_found")
    row = dict(row)
    if row.get("deleted_at") is not None:
        raise ClefsError(410, "This photo was deleted after 7 days", "photo_deleted")
    return row


def photo_bytes(row: dict) -> bytes:
    """The photo itself, from whichever store holds it (call it outside a
    transaction: the bucket is a network read)."""
    return store_for(row).get(row)


def proof_out(p: dict) -> dict:
    return {"id": p["id"], "status": p["status"], "match": p.get("match"), "confidence": p.get("confidence"),
            "reason": p.get("reason"), "observed": p.get("observed"), "created_at": p["created_at"].isoformat(),
            "staff_member_id": p.get("staff_member_id")}


# ─── The vision check ───

SYSTEM = (
    "You check a hotel worker's completion photo against the job they were given. Judge only what the photo "
    "shows: are the requested items there, delivered or placed in a guest room (or is the reported fault visibly "
    "fixed, or the room visibly made up), and does the quantity look right? A photo of other items, of an empty or "
    "unrelated place, a dark or blurred photo, a screenshot or a photo of a screen does not match. Never identify "
    "people. Reply as JSON: match (true or false), confidence (0 to 1), reason (one short sentence the worker reads, "
    "plain English), observed (what the photo shows, a few words)."
)


def check(hotel_slug: str, row: dict, data: bytes, mime: str) -> dict:
    """Run the vision check. -> {status: verified|rejected|unverified,
    match, confidence, reason, observed, model}. Never raises."""
    job = {"job": staff_lang.job_title_en(row), "quantity": row.get("quantity"), "room": row.get("room"),
           "details": (row.get("description") or "")[:400]}
    model = ai_model_for("service.verify_proof")
    try:
        out = ai.structured("service.verify_proof", system=SYSTEM, input=json.dumps(job), schema=ProofCheck,
                            hotel_slug=hotel_slug, media=[(data, mime)], max_output_tokens=400,
                            timeout_s=CHECK_TIMEOUT_S, temperature=0)
    except Exception as exc:  # AIDisabled, AIError, a timeout: a person looks instead
        log.info("proof check unavailable for %s: %s", hotel_slug, type(exc).__name__)
        return {"status": "unverified", "match": None, "confidence": None, "model": model, "observed": None,
                "reason": "The photo could not be checked automatically. Your supervisor has been asked to look at it."}
    confidence = min(1.0, max(0.0, float(out.confidence or 0)))
    reason = " ".join((out.reason or "").split())[:300]
    ok = bool(out.match) and confidence >= MIN_CONFIDENCE
    if out.match and not ok:
        reason = reason or "The photo is not clear enough to show the job is done."
    return {"status": "verified" if ok else "rejected", "match": bool(out.match), "confidence": confidence,
            "reason": reason or ("The photo shows the job done." if ok else "The photo does not show the job done."),
            "observed": " ".join((out.observed or "").split())[:300], "model": model}


# ─── POST /device/service/requests/{id}/proof ───


def _replay(cur: Any, hotel_slug: str, client_event_id: str | None) -> dict | None:
    if not client_event_id:
        return None
    cur.execute("SELECT * FROM svc_request_proofs WHERE client_event_id = %s AND hotel_slug = %s", (client_event_id, hotel_slug))
    r = cur.fetchone()
    return dict(r) if r else None


def _uuid(v: Any) -> str | None:
    if v in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(v)))
    except ValueError as exc:
        raise ClefsError(400, "client_event_id must be a UUID", "bad_request") from exc


def submit(ctx: Any, request_id: int, *, staff_id: int, data: bytes, content_type: str | None,
           client_event_id: Any = None) -> dict:
    """Store the photo, check it and apply the outcome. -> {verified, reason
    (the worker's language), reason_en, completed, proof_status, proof,
    replayed, row}. The route turns `row` into the staff app's card."""
    slug = ctx.hotel.slug
    cid = _uuid(client_event_id)
    mime = sniff(data, content_type)
    with db.db(readonly=True) as cur:
        prior = _replay(cur, slug, cid)
        if prior is not None:
            row = state.load(cur, slug, prior["request_id"])
            return {"verified": prior["status"] == "verified", "reason": prior.get("reason"), "reason_en": prior.get("reason"),
                    "completed": (row or {}).get("state") == "completed", "proof_status": prior["status"],
                    "proof": proof_out(prior), "replayed": True, "row": row}
        row = state.load(cur, slug, request_id)
        if row is None:
            raise ClefsError(404, "Request not found", "request_not_found")
        if row.get("state") not in OPEN_TO_COMPLETE:
            raise state.TransitionConflict(
                "This request is already closed" if row.get("state") in C.TERMINAL_STATES
                else f"Cannot complete a request that is {row.get('state')}",
                "already_closed" if row.get("state") in C.TERMINAL_STATES else "illegal_transition",
                {"request_id": row["id"], "state": row.get("state"), "version": row.get("version"),
                 "by": row.get("assigned_to"), "at": row.get("resolved_at") or row.get("cancelled_at")},
            )
        worker_lang = staff_lang.reading_language(cur, slug, staff_id)
    stored = get_store().put(f"{slug}/{request_id}/{uuid.uuid4().hex}.{'png' if mime == 'image/png' else 'jpg'}",
                             data, mime)
    verdict = check(slug, row, data, mime)
    reason_worker = verdict["reason"]
    if worker_lang != "en" and verdict["reason"]:
        # the staff app says why in the worker's language (English if the model cannot)
        reason_worker = staff_lang.english_to(slug, verdict["reason"], worker_lang, cache=False)[0]
    actor = state.Actor.from_ctx(ctx, staff_id=staff_id)
    with db.db() as cur:
        cur.execute(
            """INSERT INTO svc_request_proofs (hotel_slug, request_id, staff_member_id, device_id, client_event_id,
                   content_type, size_bytes, sha256, storage, object_key, data, status, match, confidence, reason,
                   observed, model, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (client_event_id) DO NOTHING RETURNING *""",
            (slug, request_id, staff_id, getattr(ctx.principal, "device_id", None), cid, mime, len(data),
             hashlib.sha256(data).hexdigest(), stored["storage"], stored["object_key"],
             psycopg2.Binary(stored["data"]) if stored.get("data") is not None else None, verdict["status"],
             verdict["match"], verdict["confidence"], verdict["reason"], verdict["observed"], verdict["model"],
             clock.now()),
        )
        got = cur.fetchone()
        if got is None:  # the same upload raced itself: the first one's outcome stands
            p = _replay(cur, slug, cid) or {}
            row = state.load(cur, slug, request_id) or row
            return {"verified": p.get("status") == "verified", "reason": p.get("reason"), "reason_en": p.get("reason"),
                    "completed": row.get("state") == "completed", "proof_status": p.get("status"),
                    "proof": proof_out(p) if p else None, "replayed": True, "row": row}
        p = dict(got)
        row = apply(cur, ctx, request_id, p, actor=actor)
    return {"verified": p["status"] == "verified", "reason": reason_worker, "reason_en": p["reason"],
            "completed": row.get("state") == "completed", "proof_status": p["status"], "proof": proof_out(p),
            "replayed": False, "row": row}


def apply(cur: Any, ctx: Any, request_id: int, p: dict, *, actor: state.Actor) -> dict:
    """The outcome of one checked photo, in the caller's transaction. ->
    the request row after it."""
    slug = ctx.hotel.slug
    row = state.load(cur, slug, request_id, lock=True)
    if row is None:
        raise ClefsError(404, "Request not found", "request_not_found")
    if row.get("state") not in OPEN_TO_COMPLETE:
        # someone closed it while the photo was being checked
        raise state._conflict(cur, row, "This request is already closed", "already_closed")
    fields = {"proof_status": p["status"], "proof_reason": p["reason"], "proof_id": p["id"]}
    if p["status"] == "verified":
        out = state.transition(cur, slug, request_id, "complete", actor, resolution_code="photo_verified",
                               extra={"proof_id": p["id"], "confidence": p.get("confidence")})
        row = out["row"]
    _set(cur, request_id, fields)
    row = {**row, **fields}
    if p["status"] != "verified":
        _alert_supervisor(cur, row, p, actor)
        state.publish_changed(cur, row, "proof")
    return row


def _set(cur: Any, request_id: int, fields: dict) -> None:
    cols = list(fields)
    cur.execute(f"UPDATE staff_requests SET {', '.join(f'{c} = %s' for c in cols)} WHERE id = %s",
                [fields[c] for c in cols] + [request_id])


def _alert_supervisor(cur: Any, row: dict, p: dict, actor: state.Actor) -> None:
    """A photo that did not pass: the feed event (the supervisor's device
    chimes; the console hears service.proof_rejected) and a message to the
    supervisor on shift, live unless the hotel is in shadow mode."""
    hotel = get_hotel(cur, row["hotel_slug"])
    label = staff_lang.job_title_en(row)
    who = row.get("assigned_to") or "staff"
    if p["status"] == "rejected":
        text = f"Photo check failed: room {row.get('room') or '-'} {label} ({who}). {p.get('reason') or ''}".strip()
    else:
        text = f"Photo needs a look: room {row.get('room') or '-'} {label} ({who}). The automatic check could not run."
    live = svc_settings.escalation_live(cur, hotel.slug)
    people = escalation.alert(cur, hotel, "supervisor", row, text[:300], key=f"svc.proof:{p['id']}", live=live)
    state.record_event(cur, row, "proof_rejected" if p["status"] == "rejected" else "proof_unverified", actor,
                       payload={"proof_id": p["id"], "status": p["status"], "reason": p.get("reason"),
                                "observed": p.get("observed"), "to": "supervisor", "alarm": "chime",
                                "shadow": not live, "recipients": [x.name for x in people]})
    events.publish(hotel.slug, "service.proof_rejected",
                   {"request_id": row["id"], "proof_id": p["id"], "status": p["status"], "room": row.get("room")},
                   audience={"roles": ["owner", "gm", "dept_head", "front_desk"]}, cur=cur)


# ─── Retention (daily) ───


def purge(cur: Any, hotel_slug: str, *, limit: int = 500) -> int:
    """Delete the bytes of photos older than 7 days (the row stays: what was
    checked and what Gemini saw). The bucket's lifecycle rule deletes its
    objects on its own; this keeps both stores to the same promise."""
    cur.execute(
        """SELECT * FROM svc_request_proofs WHERE hotel_slug = %s AND deleted_at IS NULL AND created_at < %s
           ORDER BY id LIMIT %s""",
        (hotel_slug, clock.now() - timedelta(days=KEEP_DAYS), limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        try:
            store_for(r).delete(r)
        except Exception:  # one unreachable object never stops the rest
            log.exception("could not delete proof photo %s", r["id"])
            continue
        cur.execute("UPDATE svc_request_proofs SET data = NULL, deleted_at = %s WHERE id = %s", (clock.now(), r["id"]))
    return len(rows)
