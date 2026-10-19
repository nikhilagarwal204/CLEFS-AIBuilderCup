"""
Voice test fixtures: a signed bridge webhook poster (HMAC-SHA256
with a whsec_ secret, exactly what realtime_ops.unwrap verifies), a recorder in
place of the telephony bridge's call control, and one enabled line with an extension map.
"""

from __future__ import annotations

import base64
import json
import time
import uuid

import pytest

from clefs.settings import reload_settings

WEBHOOK_SECRET = "vwh_" + base64.urlsafe_b64encode(b"clefs-voice-test-secret-32bytes!").decode()
PROJECT = "proj_testvoice"
PBX_HOST = "pbx.test-dubai.ae"
FRONT_DESK = "sip:frontdesk@pbx.test-dubai.ae"


# ─── Environment ───


@pytest.fixture
def voice_env(monkeypatch):
    """VOICE_ENABLED, a webhook secret and an API key (never used: the
    recorder replaces the SDK transport)."""
    values = {"VOICE_ENABLED": "true", "VOICE_WEBHOOK_SECRET": WEBHOOK_SECRET, "GEMINI_API_KEY": "test-voice-key"}
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    reload_settings()
    yield values
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def recorder():
    from clefs.modules.voice import realtime_ops

    rec = realtime_ops.Recorder()
    realtime_ops.set_transport(rec)
    yield rec
    realtime_ops.set_transport(None)


# ─── Signing ───


def sign(body: bytes, *, webhook_id: str, ts: int | None = None, secret: str = WEBHOOK_SECRET) -> dict:
    """The bridge's headers: webhook id, timestamp (default now) and
    X-Clefs-Signature over "<id>.<timestamp>.<raw body>"."""
    from clefs.modules.voice import realtime_ops

    stamp = str(int(time.time()) if ts is None else ts)
    return {
        "x-clefs-webhook-id": webhook_id,
        "x-clefs-timestamp": stamp,
        "x-clefs-signature": realtime_ops.signature(body, secret, webhook_id=webhook_id, timestamp=stamp),
        "content-type": "application/json",
    }


def incoming(call_id: str | None = None, *, from_user: str | None = "501", from_host: str = PBX_HOST,
             line_secret: str | None = None, extra: list[dict] | None = None, project: str = PROJECT) -> dict:
    headers = [
        {"name": "From", "value": f'"Room" <sip:{from_user}@{from_host}>;tag=abc' if from_user else f"<sip:{from_host}>"},
        {"name": "To", "value": f"<sip:{project}@sip.voice.clefs.app;transport=tls>"},
        {"name": "Via", "value": f"SIP/2.0/TLS {from_host}:5061;branch=z9hG4bK1"},
        {"name": "Call-ID", "value": uuid.uuid4().hex},
    ]
    if line_secret:
        headers.append({"name": "X-Clefs-Line", "value": line_secret})
    headers += extra or []
    return {
        "id": "evt_" + uuid.uuid4().hex[:12],
        "object": "event",
        "type": "call.incoming",
        "created_at": int(time.time()),
        "data": {"call_id": call_id or "rtc_" + uuid.uuid4().hex[:16], "sip_headers": headers},
    }


@pytest.fixture
def post_event(client):
    """post_event(event, *, webhook_id=None, headers=None) -> Response."""

    def _post(event: dict, *, webhook_id: str | None = None, headers: dict | None = None, body: bytes | None = None):
        raw = body if body is not None else json.dumps(event).encode()
        hdrs = headers if headers is not None else sign(raw, webhook_id=webhook_id or "msg_" + uuid.uuid4().hex)
        return client.post("/webhooks/voice/incoming", content=raw, headers=hdrs)

    return _post


# ─── A live concierge ───


@pytest.fixture
def hotel_uae(hotel_uae, cur):
    """The fixture hotel post-dates migration 0204 (not grandfathered), so
    its concierge starts not live and the webhook would 486 every call.
    Switch it on as a hotel that passed the readiness gate would be."""
    cur.execute("INSERT INTO cc_live (hotel_slug, live) VALUES (%s, TRUE) ON CONFLICT (hotel_slug) DO UPDATE SET live = TRUE",
                (hotel_uae.slug,))
    return hotel_uae


# ─── A line ───


@pytest.fixture
def line(cur, hotel_uae):
    """An enabled MCP line with extensions 1501 -> room 501, 1502 -> 502
    and 9000 -> the lobby location. Returns (row, secret)."""
    from clefs.modules.voice import lines

    row, secret = lines.create_line(cur, hotel_uae.slug, {
        "label": "Main PBX", "trunk_id": PROJECT, "allowed_sources": [PBX_HOST], "pbx_kind": "asterisk",
        "front_desk_uri": FRONT_DESK, "outside_line_prefix": "9", "enabled": True, "max_concurrent": 4,
    })
    for ext, room in (("1501", "501"), ("1502", "502"), ("501", "501")):
        lines.add_extension(cur, hotel_uae.slug, row["id"], {"extension": ext, "room": room})
    return row, secret


# ─── Service stand-in ───


class FakeService:
    """service.api's create_request / create_job / escalate_to_human / status,
    recorded. Voice tests run with CLEFS_ONLY_MODULES=concierge,voice, so
    Service's tables may not exist; the contract (api.py) is what voice
    depends on. A phone guest's jobs reach Service through the concierge
    executor's notify_staff, which files them with create_job (05 spec 3);
    the voice fallback and the emergency path still use create_request.
    Both record the same row shape here."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.escalations: list[dict] = []
        self.recoveries: list[dict] = []

    def create_request(self, cur, hotel_slug, *, room, type, description, source, subtype=None, priority_hint=None,
                       language=None, guest_ref=None, **_kw):
        rid = 1000 + len(self.requests) + len(self.escalations)
        row = {"id": rid, "hotel_slug": hotel_slug, "room_number": room, "request_type": type, "description": description,
               "source": source, "priority_hint": priority_hint, "tracking_token": f"tok{rid}", "merged": False}
        self.requests.append(row)
        return row

    def create_job(self, cur, hotel_slug, *, room, request_type, quantity, description_en, original_text, guest_language,
                   priority="normal", mood=None, frustration=None, session_id=None, guest_ref=None, channel="concierge",
                   idempotency_key=None):
        rid = 1000 + len(self.requests) + len(self.escalations)
        row = {"id": rid, "hotel_slug": hotel_slug, "room_number": room, "request_type": request_type,
               "description": description_en, "source": channel, "priority_hint": None, "priority": priority,
               "quantity": quantity, "original_text": original_text, "tracking_token": f"tok{rid}", "merged": False}
        self.requests.append(row)
        return {**self.dispatch_info(cur, hotel_slug, rid), "tracking_token": row["tracking_token"],
                "item_label": request_type, "quantity": quantity, "room": room, "merged": False, "replayed": False}

    def dispatch_info(self, cur, hotel_slug, request_id):
        return {"request_id": request_id, "tracking_token": f"tok{request_id}", "item_label": None, "quantity": None,
                "room": None, "department_key": None, "department_label": None, "staff_id": None,
                "staff_first_name": None, "eta_minutes": None, "eta_at": None, "created_at": None, "accepted_at": None,
                "state": "assigned", "status": "progress", "proof_status": None, "guest_confirmed_at": None,
                "closed_at": None}

    def flag_recovery(self, cur, hotel_slug, *, kind, room, summary, request_id=None, session_id=None,
                      guest_language=None):
        self.recoveries.append({"kind": kind, "room": room, "summary": summary, "request_id": request_id})
        return {"case_id": len(self.recoveries), "created": True}

    def guest_feedback(self, cur, hotel_slug, tracking_token, *, fixed, comment=None):
        self.recoveries.append({"kind": "not_fixed", "token": tracking_token, "fixed": fixed, "summary": comment})
        return {"state": "received"}

    def escalate_to_human(self, cur, hotel_slug, *, room, reason, channel, sensitive, excerpt):
        rid = 1000 + len(self.requests) + len(self.escalations)
        self.escalations.append({"id": rid, "room": room, "reason": reason, "channel": channel, "sensitive": sensitive})
        return {"request_id": rid, "tracking_token": f"tok{rid}"}

    def request_status_for_guest(self, cur, hotel_slug, token):
        for r in self.requests:
            if r["tracking_token"] == token:
                return {"id": r["id"], "type": r["request_type"], "state": "open"}
        return None

    def classify_safety(self, text, **_kw):
        t = (text or "").lower()
        if "fire" in t or "not breathing" in t:
            return {"level": "emergency", "kind": "fire" if "fire" in t else "medical", "source": "lexicon", "confidence": 1.0}
        return {"level": "none", "kind": None, "source": None, "confidence": 0.0}


@pytest.fixture(autouse=True)
def fake_service(monkeypatch):
    from clefs.modules.service import api as service_api

    fake = FakeService()
    for name in ("create_request", "create_job", "dispatch_info", "flag_recovery", "guest_feedback", "escalate_to_human",
                 "request_status_for_guest", "classify_safety"):
        monkeypatch.setattr(service_api, name, getattr(fake, name))
    return fake
