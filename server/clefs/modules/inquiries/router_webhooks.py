"""
Inquiry Guard webhooks (Canon 13 `/webhooks/`), 04 spec 4.3.

- GET  /webhooks/inquiries/meta-leadgen            hub.verify_token against
  META_LEADGEN_VERIFY_TOKEN; echoes hub.challenge.
- POST /webhooks/inquiries/meta-leadgen            X-Hub-Signature-256 (HMAC-SHA256
  of the raw body with META_APP_SECRET, constant-time); page_id -> hotel;
  queues inquiries.meta_lead_fetch deduped on leadgen_id; 200.
- POST /webhooks/inquiries/google-leadform/{key}   `google_key` compared in
  constant time with the hotel's sealed key; deduped on lead_id (200 for a
  duplicate too); is_test stored flagged. 5xx only on a transient DB error,
  so Google retries exactly when a retry can help.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from typing import Any

import psycopg2
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from clefs import db
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel, module_enabled
from clefs.modules.inquiries import channels
from clefs.modules.inquiries.intake.leads import enqueue_meta_fetch, lead_inbound
from clefs.settings import get_settings

log = logging.getLogger("clefs.inquiries.webhooks")
router = APIRouter(tags=["inquiries-webhooks"])
MAX_BODY = 256 * 1024


async def _raw(request: Request) -> bytes:
    body = await request.body()
    if len(body) > MAX_BODY:
        raise ClefsError(413, "payload too large", "payload_too_large")
    return body


# ─── Meta leadgen ───


@router.get("/webhooks/inquiries/meta-leadgen")
def meta_verify(request: Request) -> PlainTextResponse:
    q = request.query_params
    want = get_settings().META_LEADGEN_VERIFY_TOKEN
    token = q.get("hub.verify_token") or ""
    if q.get("hub.mode") == "subscribe" and want and hmac.compare_digest(token.encode(), want.encode()):
        return PlainTextResponse(q.get("hub.challenge") or "")
    raise ClefsError(403, "verification failed", "verify_failed")


def meta_signature_ok(raw: bytes, header: str | None) -> bool:
    secret = get_settings().META_APP_SECRET
    if not secret:
        raise ClefsError(503, "META_APP_SECRET is not configured", "not_configured")
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), (header or "").strip().encode())


@router.post("/webhooks/inquiries/meta-leadgen")
async def meta_leadgen(request: Request) -> dict:
    raw = await _raw(request)
    if not meta_signature_ok(raw, request.headers.get("X-Hub-Signature-256")):
        raise ClefsError(401, "bad signature", "bad_signature")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise ClefsError(400, "not JSON", "bad_payload") from exc
    queued = skipped = 0
    with db.db() as cur:
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                if change.get("field") != "leadgen":
                    continue
                value = change.get("value") or {}
                acct = channels.meta_account_for_page(cur, str(value.get("page_id") or ""))
                if acct is None or acct["status"] == "disconnected" or not module_enabled(cur, acct["hotel_slug"], "inquiries"):
                    skipped += 1
                    continue
                if enqueue_meta_fetch(cur, acct["hotel_slug"], value) is not None:
                    queued += 1
    return {"ok": True, "queued": queued, "skipped": skipped}


# ─── Google Ads lead form ───


@router.post("/webhooks/inquiries/google-leadform/{account_key}")
async def google_leadform(account_key: str, request: Request) -> JSONResponse:
    raw = await _raw(request)
    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise ClefsError(400, "not JSON", "bad_payload") from exc
    try:
        return _google(account_key, payload)
    except psycopg2.OperationalError:
        log.warning("google lead form: transient database error", exc_info=True)
        return JSONResponse({"ok": False, "retry": True}, status_code=503)


def _google(account_key: str, payload: dict) -> JSONResponse:
    with db.db() as cur:
        acct = channels.google_account_for_key(cur, account_key)
        if acct is None or acct["status"] != "active":
            raise ClefsError(404, "unknown lead form", "unknown_leadform")
        want = channels.secret_of(acct) or ""
        got = str(payload.get("google_key") or "")
        if not want or not secrets.compare_digest(want.encode(), got.encode()):
            raise ClefsError(403, "google_key does not match", "bad_google_key")
        lead_id = str(payload.get("lead_id") or "")
        if not lead_id:
            raise ClefsError(400, "lead_id is required", "bad_payload")
        hotel = get_hotel(cur, acct["hotel_slug"])
        answers = _google_answers(payload.get("user_column_data") or [])
        meta = {"platform": "google", "form_id": payload.get("form_id"), "campaign_id": payload.get("campaign_id"),
                "adgroup_id": payload.get("adgroup_id"), "creative_id": payload.get("creative_id"),
                "api_version": payload.get("api_version")}
        res = _ingest_lead(cur, hotel, answers, lead_id, meta, payload)
    return JSONResponse({"ok": True, "outcome": res}, status_code=200)


_GOOGLE_COLUMNS = {
    "FULL_NAME": "full_name", "FIRST_NAME": "first_name", "LAST_NAME": "last_name", "EMAIL": "email",
    "WORK_EMAIL": "email", "PHONE_NUMBER": "phone_number", "WORK_PHONE": "phone_number", "COMPANY_NAME": "company_name",
}


def _google_answers(cols: list[dict]) -> list[tuple[str, str]]:
    out = []
    for c in cols:
        cid = str(c.get("column_id") or "")
        key = _GOOGLE_COLUMNS.get(cid) or c.get("column_name") or cid
        out.append((key, str(c.get("string_value") or "")))
    return out


def _ingest_lead(cur: Any, hotel: Any, answers: list, lead_id: str, meta: dict, payload: dict) -> str:
    from clefs.modules.inquiries.intake.core import ingest

    inb = lead_inbound(answers, source="google_leadform", lead_id=lead_id, meta=meta,
                       is_test=bool(payload.get("is_test")), gclid=payload.get("gcl_id") or None)
    return ingest(cur, hotel, inb).outcome
