"""
Regression tests for the final foundation review fixes:

  - inbound email sender verdicts only count when our own verifier wrote them
  - a manager cannot choose the password of a brand-new global account
  - whatsapp_phone_number_id is unique and admin-only
  - production_gaps names a localhost API origin and missing cron auth
  - TRIPADVISOR_API_KEY reaches Settings
"""

from __future__ import annotations

import psycopg2
import pytest

from clefs import notify
from clefs.notify import inbound as notify_inbound
from clefs.settings import get_settings
from tests.foundation import resend_mail

# ─── Settings: production gaps and loaded keys ───


def _prod(env, **extra):
    env(ENV="production", SECRET_KEY="r" * 40, COOKIE_SECURE="true", CONTACT_HMAC_PEPPER="pepper",
        PII_ENC_KEY="00" * 32, WEB_ORIGINS="https://www.clefs.world", OAUTH_REDIRECT_BASE=None,
        GBP_OAUTH_CLIENT_ID=None, XERO_CLIENT_ID=None, QBO_CLIENT_ID=None, ZOHO_CLIENT_ID=None, **extra)


def test_production_names_a_localhost_api_origin_and_missing_cron_auth(env):
    _prod(env, PUBLIC_API_ORIGIN=None, CRON_SECRET=None, WORKER_TOKEN=None)
    gaps = get_settings().production_gaps()
    assert [g.split()[0] for g in gaps] == ["PUBLIC_API_ORIGIN", "CRON_SECRET"]
    env(PUBLIC_API_ORIGIN="http://api.clefs.world")  # plain http is still a gap
    assert [g.split()[0] for g in get_settings().production_gaps()] == ["PUBLIC_API_ORIGIN", "CRON_SECRET"]
    env(PUBLIC_API_ORIGIN="https://api.clefs.world", WORKER_TOKEN="w" * 32)  # the worker token also opens cron
    assert get_settings().production_gaps() == []
    env(WORKER_TOKEN=None, CRON_SECRET="c" * 32)
    assert get_settings().production_gaps() == []
    env(ENV="development", PUBLIC_API_ORIGIN=None, CRON_SECRET=None)
    assert get_settings().production_gaps() == []


def test_tripadvisor_key_is_loaded_into_settings(env):
    env(TRIPADVISOR_API_KEY="ta-key")
    assert get_settings().TRIPADVISOR_API_KEY == "ta-key"
    env(TRIPADVISOR_API_KEY=None)
    assert get_settings().TRIPADVISOR_API_KEY is None


# ─── Inbound email: forged sender verdicts never reach a handler ───

_STAMP_FAIL = ("Fail (sender not authorized) identity=mailfrom; client-ip=203.0.113.9; "
               "envelope-from=x@attacker.test; receiver=inbound.resend.com")
_STAMP_PASS = ("Pass (sender SPF authorized) identity=mailfrom; client-ip=192.0.2.1; "
               "envelope-from=bounce@hotel.ae; receiver=inbound.resend.com")


def _message(message_id: str, headers: list[tuple[str, str]]) -> dict:
    to = "testdubai+inq@in.clefs.world"
    return {"From": "gm@hotel.ae", "FromFull": {"Email": "gm@hotel.ae", "Name": "GM"}, "To": to,
            "ToFull": [{"Email": to, "Name": ""}], "Subject": "stmt", "MessageID": message_id, "TextBody": "x",
            "Attachments": [], "Headers": [{"Name": k, "Value": v} for k, v in headers]}


def test_email_webhook_drops_sender_written_verdicts(client, cur, hotel_uae, env, handlers):
    """Review finding security-inbound-email-auth-forgeable: the attacker
    appends "Authentication-Results: x; dmarc=pass" and a second Received-SPF
    below our verifier's real (failing) stamp; the old dict kept the last copy.
    (from_resend drops every such header first; this is the second line.)"""
    env(INBOUND_EMAIL_ENABLED="true", RESEND_WEBHOOK_SECRET=resend_mail.SECRET, INBOUND_EMAIL_DOMAIN="in.clefs.world",
        INBOUND_EMAIL_SPF_VERIFIERS=None, INBOUND_EMAIL_AUTHSERV_IDS=None)
    cur.execute("UPDATE hotels SET inbound_email_alias = 'testdubai' WHERE slug = %s", (hotel_uae.slug,))
    seen: list[notify.InboundEmail] = []

    @handlers
    @notify.on_inbound_email(purpose="inquiries")
    def capture(mail: notify.InboundEmail) -> bool:
        seen.append(mail)
        return True

    forged = [("Received-SPF", _STAMP_FAIL), ("References", "<a@x>"), ("References", "<b@x>"),
              ("Authentication-Results", "x; dmarc=pass header.from=hotel.ae; dkim=pass header.d=hotel.ae"),
              ("Received-SPF", _STAMP_PASS.replace("bounce@hotel.ae", "gm@hotel.ae"))]
    r = resend_mail.post(client, _message("fv-1", forged))
    assert r.status_code == 200 and r.json()["status"] == "handled", r.text
    mail = seen[-1]
    assert (mail.spf, mail.dkim) == (None, None)  # two copies claim our verifier: both void
    assert not any(k.lower() in notify_inbound.AUTH_VERDICT_HEADERS for k in mail.headers)
    assert mail.headers["References"] == "<a@x>"  # ordinary headers keep their first copy
    assert len(mail.header_list) == 5  # every copy is still there for audit
    # The genuine single stamp still proves SPF.
    r = resend_mail.post(client, _message("fv-2", [("Received-SPF", _STAMP_PASS)]))
    assert r.status_code == 200 and seen[-1].spf == "pass"


# ─── Team: no manager-chosen passwords on global accounts ───


def test_a_manager_cannot_choose_a_new_accounts_password(cur, client_as, hotel_uae):
    """Review finding security-account-prehijack-manager-password: a GM who
    set the password of victim@x could later sign in as them and accept
    another hotel's invitation (up to owner) there."""
    base = f"/hotels/{hotel_uae.slug}/team"
    gm = client_as("gm")
    r = gm.post(base, json={"email": "victim@example.test", "name": "V", "role": "staff", "password": "Str0ng-pass-123"})
    assert r.status_code == 403 and r.json()["code"] == "password_not_allowed"
    cur.execute("SELECT 1 FROM users WHERE email = 'victim@example.test'")
    assert cur.fetchone() is None  # nothing created
    ok = gm.post(base, json={"email": "victim@example.test", "name": "V", "role": "staff"})
    assert ok.status_code == 201
    cur.execute("SELECT password_hash FROM users WHERE email = 'victim@example.test'")
    assert cur.fetchone()["password_hash"] is None  # they sign in by magic link and set their own
    # A Clefs admin (trusted on every hotel already) may still seed one.
    admin = client_as("owner", admin=True)
    seeded = admin.post(base, json={"email": "seed@example.test", "name": "S", "role": "staff",
                                    "password": "Str0ng-pass-123"})
    assert seeded.status_code == 201, seeded.text
    cur.execute("SELECT password_hash FROM users WHERE email = 'seed@example.test'")
    assert cur.fetchone()["password_hash"]


# ─── WhatsApp phone_number_id: unique and set only by Clefs ───


def test_whatsapp_number_id_is_admin_only_and_unique(cur, client_as, hotel_uae, env):
    """Review finding security-wa-phone-number-id-unbound: a manager could
    type another hotel's id, send as its number and receive its messages."""
    from clefs.core import hotels

    env(WA_PLATFORM_PHONE_NUMBER_ID="PNID-PLATFORM")
    hotels.create_hotel(cur, slug="other-dubai", name="Other Hotel")
    cur.execute("UPDATE hotels SET whatsapp_phone_number_id = '5550001' WHERE slug = 'other-dubai'")
    url = f"/hotels/{hotel_uae.slug}/settings"
    owner = client_as("owner")
    r = owner.patch(url, json={"whatsapp_phone_number_id": "5550001"})
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    admin = client_as("owner", admin=True)
    taken = admin.patch(url, json={"whatsapp_phone_number_id": "5550001"})
    assert taken.status_code == 409 and taken.json()["code"] == "whatsapp_number_taken"
    platform = admin.patch(url, json={"whatsapp_phone_number_id": "PNID-PLATFORM"})
    assert platform.status_code == 409 and platform.json()["code"] == "whatsapp_number_taken"
    ok = admin.patch(url, json={"whatsapp_phone_number_id": " 5550002 "})
    assert ok.status_code == 200 and ok.json()["whatsapp_phone_number_id"] == "5550002"
    # The owner may resend the current value (a full form save) or clear it.
    assert owner.patch(url, json={"whatsapp_phone_number_id": "5550002", "city": "Dubai"}).status_code == 200
    cleared = owner.patch(url, json={"whatsapp_phone_number_id": ""})
    assert cleared.status_code == 200 and cleared.json()["whatsapp_phone_number_id"] is None
    # The database refuses a duplicate however it is written.
    cur.execute("SAVEPOINT dup")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute("UPDATE hotels SET whatsapp_phone_number_id = '5550001' WHERE slug = %s", (hotel_uae.slug,))
    cur.execute("ROLLBACK TO SAVEPOINT dup")
