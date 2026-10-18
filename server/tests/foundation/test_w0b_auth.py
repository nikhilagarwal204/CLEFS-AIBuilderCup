"""
Wave 0b fix lane "auth": regressions for the review findings on identity,
settings and request context that were still open after the foundation wave.

  - DATABASE_URL_UNPOOLED pairs with the effective URL only when the database
    name, locality AND credentials match (a stale exported production direct
    URL never pairs with a staging DATABASE_URL that shares "neondb");
  - an unset ENV in a container or on a hosting platform is not development,
    and every non-development process defaults to Secure cookies;
  - a trusted forwarded IP must parse as an IP address;
  - a stored magic-link next_path is re-checked on exchange;
  - /auth/magic/request does no account lookup before answering;
  - the contract permissions are core permissions (they survive reset());
  - the 04 spec environment variables are modelled settings.

The findings already fixed in the foundation commit keep their regressions in
test_hardening.py (team identity, invitations, magic scope, next paths) and
test_core.py (settings precedence, fail-closed secret key).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import BackgroundTasks
from starlette.datastructures import Headers

from clefs import settings as settings_mod
from clefs.auth import magic, permissions
from clefs.settings import DEV_SECRET_KEY, get_settings, load_settings, urls_pair

STRONG_KEY = "s" * 48


@pytest.fixture
def bare_env(monkeypatch, tmp_path):
    """No ENV/secret/cookie/platform variables and no /.dockerenv, so each
    test states exactly what the process looks like."""
    for k in ("ENV", "SECRET_KEY", "COOKIE_SECURE", "VERCEL", "VERCEL_ENV", "TRUST_FORWARDED_FOR", *settings_mod.HOSTED_MARKERS):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(settings_mod, "DOCKERENV_PATH", str(tmp_path / "no-dockerenv"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/clefs_dev")
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://localhost/clefs_dev")
    return monkeypatch


# ─── Unpooled URL pairing ───


NEON_STAGING = "postgresql://neondb_owner:stagingpw@ep-stg-111-pooler.me-central-1.aws.neon.tech/neondb?sslmode=require"
NEON_PROD_DIRECT = "postgresql://neondb_owner:prodpw@ep-prod-222.me-central-1.aws.neon.tech/neondb?sslmode=require"
NEON_STAGING_DIRECT = "postgresql://neondb_owner:stagingpw@ep-stg-111.me-central-1.aws.neon.tech/neondb?sslmode=require"


def test_a_stale_exported_direct_url_of_another_project_never_pairs(monkeypatch):
    """Both exported (so the .env provenance rule does not apply), both remote,
    both "neondb": only the credentials tell staging from production."""
    monkeypatch.setenv("DATABASE_URL", NEON_STAGING)
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", NEON_PROD_DIRECT)
    s = load_settings()
    assert s.database_url_unpooled == NEON_STAGING
    notes = [w for w in s.validate_for_startup() if "DATABASE_URL_UNPOOLED ignored" in w]
    assert notes and "prodpw" not in notes[0] and "stagingpw" not in notes[0]

    monkeypatch.setenv("DATABASE_URL_UNPOOLED", NEON_STAGING_DIRECT)  # the real pair
    assert load_settings().database_url_unpooled == NEON_STAGING_DIRECT


def test_provider_pairs_still_pair():
    # Supabase: the pooler role is "<role>.<project ref>", the direct one "<role>".
    assert urls_pair(
        "postgresql://postgres.abcref:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres",
        "postgresql://postgres:pw@db.abcref.supabase.co:5432/postgres",
    )
    assert not urls_pair(
        "postgresql://postgres.abcref:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres",
        "postgresql://postgres:other@db.zzzref.supabase.co:5432/postgres",
    )
    # Local URLs pair on the database name alone; locality must match.
    assert urls_pair("postgresql://localhost/clefs_dev", "postgresql://u:p@127.0.0.1/clefs_dev")
    assert not urls_pair("postgresql://localhost/neondb", NEON_PROD_DIRECT)
    # Percent-encoded passwords compare decoded.
    assert urls_pair("postgresql://u:p%40ss@a-pooler.x/db", "postgresql://u:p%40ss@a.x/db")


# ─── ENV detection and cookies ───


@pytest.mark.parametrize("marker", ["RENDER", "DYNO", "FLY_APP_NAME", "KUBERNETES_SERVICE_HOST"])
def test_unset_env_on_a_hosting_platform_is_not_development(bare_env, marker):
    bare_env.setenv(marker, "1")
    s = load_settings()
    assert (s.ENV, s.is_dev, s.COOKIE_SECURE) == ("development", False, True)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        s.validate_for_startup()


def test_unset_env_inside_a_container_is_not_development(bare_env, tmp_path):
    dockerenv = tmp_path / ".dockerenv"
    dockerenv.write_text("")
    bare_env.setattr(settings_mod, "DOCKERENV_PATH", str(dockerenv))
    s = load_settings()
    assert not s.is_dev and s.COOKIE_SECURE
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        s.validate_for_startup()
    bare_env.setenv("ENV", "development")  # saying so explicitly is still allowed
    s = load_settings()
    assert s.is_dev and not s.COOKIE_SECURE and s.SECRET_KEY == DEV_SECRET_KEY


def test_a_developer_machine_keeps_the_local_shortcut(bare_env):
    s = load_settings()
    assert s.is_dev and not s.COOKIE_SECURE
    assert any("development key" in w for w in s.validate_for_startup())


@pytest.mark.parametrize("name", ["staging", "preview", "qa"])
def test_every_non_development_env_defaults_to_secure_cookies(bare_env, name):
    bare_env.setenv("ENV", name)
    bare_env.setenv("SECRET_KEY", STRONG_KEY)
    s = load_settings()
    assert not s.is_production and not s.is_dev and s.COOKIE_SECURE
    assert s.validate_for_startup() == []
    # Switching it off there is possible (plain-HTTP test rigs) but never silent.
    bare_env.setenv("COOKIE_SECURE", "false")
    s = load_settings()
    assert not s.COOKIE_SECURE
    assert any("COOKIE_SECURE is false outside development" in w for w in s.validate_for_startup())


def test_env_is_trimmed_before_it_is_classified(bare_env):
    bare_env.setenv("ENV", " production ")
    bare_env.setenv("SECRET_KEY", STRONG_KEY)
    s = load_settings()
    assert (s.ENV, s.is_production, s.COOKIE_SECURE) == ("production", True, True)
    bare_env.setenv("ENV", "  ")  # blank means unset, not a mystery environment
    assert load_settings().ENV == "development"


def test_production_names_missing_privacy_keys(bare_env):
    bare_env.setenv("ENV", "production")
    bare_env.setenv("SECRET_KEY", STRONG_KEY)
    bare_env.setenv("PUBLIC_API_ORIGIN", "https://api.clefs.world")  # its own gap: tested in test_final_foundation
    bare_env.setenv("CRON_SECRET", "c" * 32)
    for k in ("CONTACT_HMAC_PEPPER", "PII_ENC_KEY"):
        bare_env.delenv(k, raising=False)
    gaps = load_settings().production_gaps()
    assert [g.split()[0] for g in gaps] == ["CONTACT_HMAC_PEPPER", "PII_ENC_KEY"]
    bare_env.setenv("CONTACT_HMAC_PEPPER", "pepper")
    bare_env.setenv("PII_ENC_KEY", "00" * 32)
    assert load_settings().production_gaps() == []
    bare_env.setenv("ENV", "development")
    bare_env.delenv("PII_ENC_KEY")
    assert load_settings().production_gaps() == []


# ─── Modelled environment variables (04 spec 7.6) ───


GROWTH_VARS = (
    "CONTACT_HMAC_PEPPER", "PII_ENC_KEY", "WA_MAGIC_LINK_TEMPLATE", "META_LEADGEN_VERIFY_TOKEN",
    "WA_SERVICE_FREE_ALLOWANCE", "OTA_ALIAS_DOMAINS", "GOOGLE_API_KEY", "OAUTH_REDIRECT_BASE",
    "SERPAPI_MONTHLY_BUDGET_USD", "PUBLIC_SERPAPI_MONTHLY_CAP", "VISIBILITY_AI_ENABLED", "PERPLEXITY_MODEL",
    "PERPLEXITY_TERMS_APPROVED", "PUBLIC_CHECK_DAILY_CAP", "SNAPSHOT_DAILY_CAP", "SNAPSHOT_MONTHLY_BUDGET_USD",
    "QBO_ENV", "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "TELR_API_BASE", "STRIPE_API_BASE", "TAP_API_BASE",
)


def test_growth_variables_have_spec_defaults(monkeypatch):
    for k in GROWTH_VARS:
        monkeypatch.delenv(k, raising=False)
    s = load_settings()
    assert s.WA_MAGIC_LINK_TEMPLATE == "clefs_magic_link"
    assert s.OTA_ALIAS_DOMAINS == ["guest.booking.com", "m.expediapartnercentral.com"]
    assert (s.PUBLIC_CHECK_DAILY_CAP, s.SNAPSHOT_DAILY_CAP, s.SNAPSHOT_MONTHLY_BUDGET_USD) == (20, 10, Decimal("50"))
    assert (s.SERPAPI_MONTHLY_BUDGET_USD, s.PUBLIC_SERPAPI_MONTHLY_CAP) == (None, None)
    assert (s.VISIBILITY_AI_ENABLED, s.PERPLEXITY_TERMS_APPROVED, s.PERPLEXITY_MODEL) == (True, False, "sonar")
    assert (s.WA_SERVICE_FREE_ALLOWANCE, s.QBO_ENV) == (0, "sandbox")
    assert (s.CONTACT_HMAC_PEPPER, s.PII_ENC_KEY, s.GOOGLE_API_KEY, s.OAUTH_REDIRECT_BASE) == (None, None, None, None)


def test_growth_variables_parse(monkeypatch):
    monkeypatch.setenv("SERPAPI_MONTHLY_BUDGET_USD", " 12.50 ")
    monkeypatch.setenv("PUBLIC_SERPAPI_MONTHLY_CAP", "300")
    monkeypatch.setenv("PERPLEXITY_TERMS_APPROVED", "true")
    monkeypatch.setenv("VISIBILITY_AI_ENABLED", "off")
    monkeypatch.setenv("OTA_ALIAS_DOMAINS", "Guest.Booking.com, agoda-messaging.com")
    monkeypatch.setenv("QBO_ENV", "production")
    s = load_settings()
    assert s.SERPAPI_MONTHLY_BUDGET_USD == Decimal("12.50") and isinstance(s.SERPAPI_MONTHLY_BUDGET_USD, Decimal)
    assert (s.PUBLIC_SERPAPI_MONTHLY_CAP, s.PERPLEXITY_TERMS_APPROVED, s.VISIBILITY_AI_ENABLED) == (300, True, False)
    assert s.OTA_ALIAS_DOMAINS == ["guest.booking.com", "agoda-messaging.com"]
    assert s.QBO_ENV == "production"
    monkeypatch.setenv("QBO_ENV", "live")  # a typo fails at startup, not at the first sync
    with pytest.raises(ValueError):
        load_settings()


# ─── Client IP ───


def test_a_trusted_forwarded_ip_must_parse(env):
    from clefs.app import _client_ip

    env(TRUST_FORWARDED_FOR="true")
    s = get_settings()
    scope = {"client": ("203.0.113.9", 5000)}
    junk_real_ip = Headers({"x-real-ip": "not-an-ip", "x-forwarded-for": "198.51.100.7, 10.0.0.1"})
    assert _client_ip(scope, junk_real_ip, s) == "198.51.100.7"
    assert _client_ip(scope, Headers({"x-forwarded-for": "<script>, 1.2.3.4"}), s) == "203.0.113.9"
    assert _client_ip(scope, Headers({"x-real-ip": "[2001:db8::1]:443"}), s) == "2001:db8::1"
    assert _client_ip(scope, Headers({"x-real-ip": "203.0.113.50:8443"}), s) == "203.0.113.50"
    assert _client_ip(scope, Headers({"x-real-ip": "2001:DB8:0:0::2"}), s) == "2001:db8::2"
    assert _client_ip(scope, Headers({"x-real-ip": "9" * 100}), s) == "203.0.113.9"
    assert _client_ip({}, Headers({}), s) is None


# ─── Magic links ───


def test_exchange_rechecks_a_stored_next_path(cur, client, make_user, hotel_uae):
    """A row that predates safe_next_path (or was written around create_link)
    still cannot send the browser off our origin."""
    u = make_user("owner", hotel_uae.slug)
    token = magic.create_link(cur, user_id=u["id"], hotel_slug=hotel_uae.slug, purpose="brief")
    cur.execute("UPDATE magic_links SET next_path = %s WHERE user_id = %s", ("/\t/evil.example/x", u["id"]))
    r = client.post("/auth/magic/exchange", json={"token": token})
    assert r.status_code == 200 and r.json()["next_path"] == f"/app/{hotel_uae.slug}"


def test_magic_request_looks_nothing_up_before_it_answers(cur, make_user, monkeypatch, hotel_uae):
    """Known and unknown identities cost the same inside the request: the
    lookup, minting and delivery are all queued for after the response."""
    from clefs.auth import routes as auth_routes

    calls: list[str] = []
    monkeypatch.setattr(magic, "request_link", lambda *a, **kw: calls.append("lookup"))
    u = make_user("gm", hotel_uae.slug)
    queued = []
    for email in (u["email"], "nobody@example.test"):
        bg = BackgroundTasks()
        out = auth_routes.magic_request(auth_routes.MagicRequestBody(email=email), bg)
        assert out == {"ok": True} and calls == []
        assert [t.func for t in bg.tasks] == [auth_routes._send_magic_link]
        queued.append(bg)
    queued[0].tasks[0].func(*queued[0].tasks[0].args)  # the deferred work does run
    assert calls == ["lookup"]


# ─── Permissions ───


def test_contract_permissions_are_core_permissions():
    # reset() (run by every create_app) rebuilds the registry from
    # CORE_PERMISSIONS, so membership there is what keeps them. Not called
    # here: the session app's module permissions must stay registered.
    expect = {
        "core.autonomy.view": {"owner", "gm", "finance", "revenue", "dept_head"},
        "core.autonomy.manage": {"owner", "gm"},
        "core.autonomy.raise_auto": {"owner"},
        "core.metrics.view": {"owner", "gm", "finance", "revenue", "dept_head"},
        "core.files.view": {"owner", "gm", "finance", "revenue", "dept_head"},
        # Wave 1 integration: core domain routes stop borrowing core.settings.*.
        "core.stays.view": {"owner", "gm", "finance", "revenue", "dept_head"},
        "core.stays.manage": {"owner", "gm"},
        "core.ingest.manage": {"owner", "gm", "finance", "revenue"},
    }
    for key, roles in expect.items():
        assert permissions.CORE_PERMISSIONS[key] == frozenset(roles)
        assert permissions.owner_of(key) == "core"
    assert "core.autonomy.raise_auto" not in permissions.permissions_for_role("gm")
    assert not {"core.files.view", "core.metrics.view"} & permissions.device_permissions()
    assert not {"core.files.view", "core.autonomy.view"} & permissions.permissions_for_role("front_desk")
    # A magic-link session never reaches the dial, metrics or files.
    magic_perms = permissions.restrict_for_magic(permissions.all_permissions(), ["brief", "actions"])
    assert not any(p.startswith(("core.autonomy.", "core.metrics.", "core.files.")) for p in magic_perms)
