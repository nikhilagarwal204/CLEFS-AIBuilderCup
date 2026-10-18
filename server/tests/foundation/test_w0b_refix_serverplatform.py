"""
Refix lane "server-platform": regression tests for the review findings.

  - privacy: a phone typed without "+" that already carries its country code
    ("971501234567") never gets the hotel's code a second time, and digits
    that fit no reading ("919876543210" at a UAE hotel) are refused, not
    hashed to a stranger.
  - oauth: the callback is bound to the signed-in user who started the flow;
    another user's or an anonymous browser connects nothing and never
    reaches the token endpoint.
  - outbox: a queued session message whose window closed before delivery
    fires on_outbound_status ("failed", "outside_window") after its fallback.
  - notify.validate: every Content-* email header is refused.
  - serpapi / logsafe: the SerpApi key never reaches a log record, and the
    API / worker logging setup quiets httpx request lines.
  - settings: production names an OAuth redirect base the session cookie
    cannot reach.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from clefs import jobs, logsafe, notify
from clefs.auth.deps import ANONYMOUS, Principal
from clefs.core import hotels, oauth, privacy, serpapi
from clefs.core.errors import ClefsError
from clefs.notify import inbound
from clefs.notify import outbox as ob
from clefs.notify import validate
from clefs.notify.validate import PayloadError
from clefs.settings import get_settings

T0 = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)  # 12:00 Dubai, inside marketing hours
GUEST = "+971501113333"


# ─── privacy: national phones ───


def _phone(raw: str, cc: str) -> str:
    return privacy.normalize_contact("phone", raw, default_country_code=cc)


def _refused(raw: str, cc: str) -> None:
    with pytest.raises(ClefsError) as exc:
        _phone(raw, cc)
    assert exc.value.code == "invalid_phone", raw


def test_an_international_number_without_plus_keeps_its_own_code():
    assert _phone("971501234567", "971") == "+971501234567"  # was +971971501234567
    assert _phone("971 50 123 4567", "971") == "+971501234567"
    assert _phone("919876543210", "91") == "+919876543210"
    assert _phone("447911123456", "44") == "+447911123456"


def test_national_forms_still_take_the_default_code():
    for raw in ("0501234567", "050 123 4567", "050-123-4567", "501234567", "(050) 123 4567"):
        assert _phone(raw, "971") == "+971501234567", raw
    assert _phone("042345678", "971") == "+97142345678"  # an 8-digit Dubai landline
    assert _phone("09876543210", "91") == "+919876543210"
    assert _phone("9876543210", "91") == "+919876543210"
    assert _phone("07911123456", "44") == "+447911123456"


def test_digits_that_fit_no_reading_or_two_readings_are_refused():
    _refused("919876543210", "971")  # an Indian number at a UAE hotel: 12 digits is no UAE NSN
    _refused("05012345", "971")  # trunk 0 + 7 digits
    _refused("00", "971")
    _refused("0", "971")
    _refused("9715012", "971")  # 7 digits: no UAE national length, and 971 + 4 digits is no number
    _refused("9715012345678", "971")  # 971 + 10 digits, 13 digits national: too long either way
    _refused("4930123456", "49")  # German: national 4930123456 or +49 30123456, not guessed
    _refused("050123456a", "971")


def test_a_default_without_a_length_table_refuses_what_it_cannot_tell_apart():
    assert _phone("2125550100", "1") == "+12125550100"
    assert _phone("02125550100", "1") == "+12125550100"
    _refused("12125550100", "1")


def test_every_calling_code_has_national_lengths():
    assert set(privacy.CALLING_CODES.values()) <= set(privacy.NATIONAL_LENGTHS)


def test_contact_hmac_at_an_ae_hotel_matches_the_whatsapp_sender(cur, hotel_uae):
    wa = privacy.contact_hmac(hotel_uae.slug, "phone", "+971501234567")
    assert privacy.contact_hmac(hotel_uae.slug, "phone", "971501234567", cur=cur) == wa
    assert privacy.contact_hmac(hotel_uae.slug, "phone", "0501234567", cur=cur) == wa
    with pytest.raises(ClefsError) as exc:
        privacy.contact_hmac(hotel_uae.slug, "phone", "919876543210", cur=cur)
    assert exc.value.code == "invalid_phone"
    india = hotels.create_hotel(cur, slug="refix-mumbai", name="Refix Mumbai", country="IN", emirate=None,
                                timezone="Asia/Kolkata", currency="INR")
    assert privacy.contact_hmac(india.slug, "phone", "919876543210", cur=cur) == privacy.contact_hmac(
        india.slug, "phone", "+919876543210")


# ─── oauth: the callback is bound to the initiating session ───


GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


class _IdP:
    def __init__(self) -> None:
        self.answers: list[tuple[int, dict]] = []
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"url": str(request.url).split("?")[0], "form": dict(parse_qsl(request.content.decode()))})
        status, body = self.answers.pop(0) if self.answers else (404, {"error": "unexpected"})
        return httpx.Response(status, json=body, request=request)


@pytest.fixture
def idp(env, clock):
    clock.freeze(T0)
    env(PUBLIC_API_ORIGIN="https://api.clefs.test", OAUTH_REDIRECT_BASE=None,
        GBP_OAUTH_CLIENT_ID="gbp-client", GBP_OAUTH_CLIENT_SECRET="gbp-secret")
    fake = _IdP()
    oauth.set_transport(httpx.MockTransport(fake))
    yield fake
    oauth.set_transport(None)


def _start(c: TestClient, slug: str) -> str:
    r = c.get(f"/hotels/{slug}/oauth/google_business/start", params={"format": "json"})
    assert r.status_code == 200, r.text
    return parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]


def _callback(c: TestClient, state: str) -> dict:
    r = c.get("/public/oauth/google_business/callback", params={"state": state, "code": "4/victim-code"},
              follow_redirects=False)
    assert r.status_code == 302, r.text
    parts = urlsplit(r.headers["location"])
    return {"location": r.headers["location"], "path": parts.path, **{k: v[0] for k, v in parse_qs(parts.query).items()}}


def _connections(cur) -> list[dict]:
    cur.execute("SELECT hotel_slug, provider, connected_by FROM oauth_connections")
    return [dict(r) for r in cur.fetchall()]


def test_an_authorize_link_completed_by_another_user_connects_nothing(cur, client_as, idp, hotel_uae):
    """The account-injection attack: a manager at hotel A starts a flow and
    sends the link to another hotel's owner, who approves on the genuine
    consent screen. Nothing is exchanged or stored, and the victim's
    redirect names neither hotel A nor its pages."""
    attacker = client_as("gm")
    other = hotels.create_hotel(cur, slug="refix-victim", name="Victim Hotel", country="AE", emirate="dubai",
                                timezone="Asia/Dubai", currency="AED")
    victim = client_as("owner", other.slug)
    state = _start(attacker, hotel_uae.slug)

    out = _callback(victim, state)
    assert (out["oauth_status"], out["oauth_error"], out["path"]) == ("error", "session_mismatch", "/app")
    assert hotel_uae.slug not in out["location"]
    assert idp.requests == [] and _connections(cur) == []
    # The state was burnt by the mismatch: the link cannot be replayed.
    assert _callback(attacker, state)["oauth_error"] == "invalid_state"
    assert idp.requests == []


def test_an_anonymous_callback_connects_nothing(cur, client, client_as, idp, hotel_uae):
    state = _start(client_as("gm"), hotel_uae.slug)
    out = _callback(client, state)
    assert (out["oauth_status"], out["oauth_error"]) == ("error", "session_mismatch")
    assert idp.requests == [] and _connections(cur) == []


def test_the_same_user_completes_the_flow_from_any_of_their_sessions(cur, app, client_as, login, idp, hotel_uae):
    gm = client_as("gm")
    state = _start(gm, hotel_uae.slug)
    second = TestClient(app)  # the same person signed in on a second browser
    login(second, gm.user)
    idp.answers.append((200, {"access_token": "ya29.A", "refresh_token": "1//R", "expires_in": 3599,
                              "scope": oauth.GOOGLE_BUSINESS_SCOPE}))
    out = _callback(second, state)
    assert out["oauth_status"] == "connected" and len(idp.requests) == 1
    assert _connections(cur) == [{"hotel_slug": hotel_uae.slug, "provider": "google_business",
                                  "connected_by": gm.user["id"]}]


def test_complete_flow_requires_the_starting_user(cur, make_user, idp, hotel_uae):
    user = make_user("gm", hotel_uae.slug)
    other = make_user("gm", hotel_uae.slug)
    for who in (None, ANONYMOUS, Principal(kind="user", user_id=other["id"]),
                Principal(kind="service", label="cron"), Principal(kind="device", device_id=user["id"])):
        out = oauth.start_flow(cur, hotel_uae.slug, "google_business", user_id=user["id"])
        state = parse_qs(urlsplit(out["authorize_url"]).query)["state"][0]
        res = oauth.complete_flow("google_business", {"state": state, "code": "c"}, principal=who)
        assert (res.status, res.error, res.hotel_slug, res.next_path) == ("error", "session_mismatch", None, None), who
    assert idp.requests == []


# ─── outbox: outside_window after queueing fires the status hooks ───


@pytest.fixture
def status_hooks():
    seen: list[tuple] = []

    @notify.on_outbound_status("guests.refix")
    def hook(cur_, row, status, code):
        seen.append((row["id"], status, code, (row.get("meta") or {}).get("fallback_id"), row["status"]))

    yield seen
    inbound.unregister_handler(hook)


def _row(cur, oid: int) -> dict:
    cur.execute("SELECT * FROM outbound_messages WHERE id = %s", (oid,))
    return dict(cur.fetchone())


def test_a_queued_session_message_that_misses_its_window_is_reported(cur, outbox, clock, status_hooks):
    clock.freeze(T0)
    # The window closes 3 s from now; the 6 s pacing will hold the text past it.
    ob.open_window(cur, "platform", GUEST, at=T0 - timedelta(hours=24) + timedelta(seconds=3))
    first = notify.send_whatsapp_template(GUEST, "owner_brief_v1", "en", ["1"], hotel_slug=None, purpose="brief.daily")
    assert first.status == "sent"
    late = notify.send_whatsapp_text(GUEST, "Your room is ready", hotel_slug=None, purpose="guests.refix.j5",
                                     idempotency_key="refix:5:a1",
                                     fallback_template={"name": "gst_j5_daycheck_v1", "lang": "en", "params": ["Sam"]})
    lost = notify.send_whatsapp_text(GUEST, "No fallback", hotel_slug=None, purpose="guests.refix.j6",
                                     idempotency_key="refix:6:a1")
    assert (late.status, lost.status) == ("queued", "queued") and status_hooks == []

    clock.advance(seconds=12)
    jobs.run_pending(only=["core.notify.deliver"])
    clock.advance(seconds=12)  # the fallback template just went out: the next row waits one spacing
    jobs.run_pending(only=["core.notify.deliver"])
    row = _row(cur, late.outbox_id)
    assert row["status"] == "outside_window"
    fb = _row(cur, row["meta"]["fallback_id"])
    assert fb["template_name"] == "gst_j5_daycheck_v1" and fb["fallback_of"] == late.outbox_id
    assert _row(cur, lost.outbox_id)["status"] == "outside_window"
    # Fired after the fallback was created, so the hook sees its id; the
    # message with no fallback is reported as lost (no fallback_id).
    assert sorted(status_hooks) == sorted([
        (late.outbox_id, "failed", "outside_window", fb["id"], "outside_window"),
        (lost.outbox_id, "failed", "outside_window", None, "outside_window"),
    ])


def test_the_synchronous_refusal_still_answers_the_caller_without_a_hook(cur, outbox, clock, status_hooks):
    clock.freeze(T0)
    r = notify.send_whatsapp_text(GUEST, "Hello", hotel_slug=None, purpose="guests.refix.j1")
    assert not r.ok and r.status == "outside_window" and status_hooks == []


# ─── notify.validate: Content-* headers ───


@pytest.mark.parametrize("name", ["Content-Disposition", "content-id", "Content-Language", "Content-Location",
                                  "Content-Type", "CONTENT-TRANSFER-ENCODING", "Content-Anything"])
def test_every_content_header_is_refused(name):
    with pytest.raises(PayloadError):
        validate.email_headers({name: "attachment"})


def test_ordinary_headers_still_pass():
    assert validate.email_headers({"List-Unsubscribe": "<https://x.test/u>", "In-Reply-To": "<a@b>",
                                   "X-Content-Note": "ok"}) == [
        {"Name": "List-Unsubscribe", "Value": "<https://x.test/u>"}, {"Name": "In-Reply-To", "Value": "<a@b>"},
        {"Name": "X-Content-Note", "Value": "ok"}]


# ─── serpapi / logsafe: no key in the logs ───


def test_the_serpapi_key_never_reaches_a_log_record(cur, env, clock, hotel_uae, caplog):
    clock.freeze(T0)
    env(SERPAPI_KEY="LIVE_SERPAPI_KEY_123", SERPAPI_MONTHLY_BUDGET_USD=None, PUBLIC_SERPAPI_MONTHLY_CAP=None,
        SERPAPI_COST_PER_SEARCH_USD=None)
    fake = serpapi.use_fake()
    try:
        caplog.set_level(logging.DEBUG)
        caplog.set_level(logging.DEBUG, logger="httpx")  # as if LOG_LEVEL=DEBUG and nothing quieted httpx
        d = date(2026, 10, 2)
        params = serpapi.google_hotels_area_params(hotel_uae, q="hotels in Deira", check_in=d,
                                                   check_out=d + timedelta(days=1))
        serpapi.search("google_hotels", params, hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    finally:
        serpapi.set_transport(None)
    assert fake.calls == 1
    texts = [r.getMessage() for r in caplog.records]
    request_lines = [t for t in texts if "serpapi.com" in t]
    assert request_lines, texts  # httpx did log the request line, redacted
    assert all("api_key=***" in t for t in request_lines)
    assert not any("LIVE_SERPAPI_KEY_123" in t for t in texts)
    assert not any("LIVE_SERPAPI_KEY_123" in json.dumps(r.__dict__, default=str) for r in caplog.records)


def test_redact_only_touches_whole_secret_parameters():
    assert logsafe.redact("GET https://x.test/s?q=a&api_key=K1&key=K2&monkey=m") == (
        "GET https://x.test/s?q=a&api_key=***&key=***&monkey=m")
    assert logsafe.redact('GET https://x.test/?KEY=abc "HTTP/1.1 200 OK"') == 'GET https://x.test/?KEY=*** "HTTP/1.1 200 OK"'
    assert logsafe.redact("nothing here") == "nothing here"


def test_configure_quiets_httpx_and_installs_the_filter(monkeypatch):
    monkeypatch.setattr(logging, "basicConfig", lambda **_k: None)
    saved = {n: logging.getLogger(n).level for n in logsafe.QUIET_LOGGERS}
    try:
        logsafe.configure(logging.DEBUG)
        assert all(logging.getLogger(n).level == logging.WARNING for n in logsafe.QUIET_LOGGERS)
        httpx_filters = logging.getLogger("httpx").filters
        assert sum(isinstance(f, logsafe.RedactQuerySecrets) for f in httpx_filters) == 1
        logsafe.install()  # idempotent
        assert sum(isinstance(f, logsafe.RedactQuerySecrets) for f in logging.getLogger("httpx").filters) == 1
    finally:
        for n, level in saved.items():
            logging.getLogger(n).setLevel(level)


# ─── settings: the OAuth redirect must reach the session cookie ───


def test_production_names_an_oauth_redirect_off_the_web_origin(env):
    env(ENV="production", SECRET_KEY="r" * 40, COOKIE_SECURE="true", CONTACT_HMAC_PEPPER="pepper",
        PII_ENC_KEY="00" * 32, WEB_ORIGINS="https://www.clefs.world", PUBLIC_API_ORIGIN="https://api.clefs.world",
        CRON_SECRET="c" * 32, OAUTH_REDIRECT_BASE=None, GBP_OAUTH_CLIENT_ID=None, XERO_CLIENT_ID=None, QBO_CLIENT_ID=None, ZOHO_CLIENT_ID=None)
    assert get_settings().production_gaps() == []  # no OAuth app configured: nothing to warn about
    env(XERO_CLIENT_ID="xero-client")
    (gap,) = get_settings().production_gaps()
    assert gap.startswith("OAUTH_REDIRECT_BASE") and "session_mismatch" in gap
    env(OAUTH_REDIRECT_BASE="https://www.clefs.world/api/clefs")
    assert get_settings().production_gaps() == []
