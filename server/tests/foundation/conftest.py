"""
Fixtures for the foundation tests. outbox, fake_ai and run_jobs live in the
root tests/conftest.py (module lanes use them too).

  env(**vars)          set environment variables and reload settings; undone after the test
  owner_ctx / ctx_for  HotelCtx objects for calling contract functions directly
  wa                   WhatsApp webhook settings + a signer for request bodies
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from clefs.settings import reload_settings


@pytest.fixture
def env(monkeypatch):
    def _set(**values: str | None) -> None:
        for k, v in values.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, str(v))
        reload_settings()

    yield _set
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def ctx_for(cur, make_user, hotel_uae):
    """ctx_for(role, hotel=hotel_uae.slug, **make_user_kw) -> (HotelCtx, user dict)."""
    from clefs.auth.deps import Principal, resolve_ctx

    def _ctx(role: str, hotel: str | None = None, **kw):
        user = make_user(role, hotel or hotel_uae.slug, **kw)
        principal = Principal(
            kind="user", user_id=user["id"], label=f"Test {role}", is_clefs_admin=bool(kw.get("admin")), session_kind="web"
        )
        return resolve_ctx(principal, hotel or hotel_uae.slug, cur=cur), user

    return _ctx


@pytest.fixture
def owner_ctx(ctx_for):
    return ctx_for("owner")[0]


class _WA:
    secret = "test-app-secret"
    verify_token = "verify-me"
    platform_id = "PNID-PLATFORM"

    def sign(self, body: bytes) -> str:
        return "sha256=" + hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()

    def post(self, client, payload: dict, *, signature: str | None = "auto"):
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if signature == "auto":
            headers["X-Hub-Signature-256"] = self.sign(body)
        elif signature is not None:
            headers["X-Hub-Signature-256"] = signature
        return client.post("/webhooks/whatsapp", content=body, headers=headers)

    @staticmethod
    def message_payload(messages: list[dict], *, phone_number_id: str = "PNID-PLATFORM", contacts: list[dict] | None = None) -> dict:
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"display_phone_number": "971500000000", "phone_number_id": phone_number_id},
                                "contacts": contacts or [],
                                "messages": messages,
                            },
                        }
                    ],
                }
            ],
        }

    @staticmethod
    def status_payload(statuses: list[dict], *, phone_number_id: str = "PNID-PLATFORM") -> dict:
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": phone_number_id},
                                "statuses": statuses,
                            },
                        }
                    ],
                }
            ],
        }


@pytest.fixture
def wa(env):
    w = _WA()
    env(WA_APP_SECRET=w.secret, WA_VERIFY_TOKEN=w.verify_token, WA_PLATFORM_PHONE_NUMBER_ID=w.platform_id)
    return w


@pytest.fixture
def handlers():
    """handlers(fn) registers a test WhatsApp/email/status handler on an
    empty registry. The module handlers the app imported (guests STOP at slot
    5, journeys at 10, Service's fallback at 90, ...) are set aside for the
    test and restored after it, so a test at a frozen slot never depends on
    which modules are loaded."""
    from clefs.notify import inbound

    with inbound._lock:
        saved = (list(inbound._wa_handlers), list(inbound._email_handlers), list(inbound._status_hooks))
        inbound._wa_handlers.clear()
        inbound._email_handlers.clear()
        inbound._status_hooks.clear()
    registered = []

    def add(fn):
        registered.append(fn)
        return fn

    try:
        yield add
    finally:
        for fn in registered:
            inbound.unregister_handler(fn)
        with inbound._lock:
            inbound._wa_handlers[:] = saved[0]
            inbound._email_handlers[:] = saved[1]
            inbound._status_hooks[:] = saved[2]
