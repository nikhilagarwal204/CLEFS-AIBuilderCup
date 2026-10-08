"""
A remote database URL that arrives from server/.env must never be used
without an explicit opt-in.

Why: server/.env holds the PRODUCTION Neon URL for the legacy app. During
the wave 1 build a verification step ran `DATABASE_URL= python -m
clefs.migrate verify`; the empty DATABASE_URL fell through to that .env
value and a (read-only) query reached production. These tests pin the
guard that turns that fall-through into a loud refusal.
"""

import pytest

from clefs import settings as settings_mod

REMOTE = "postgresql://user:pw@ep-example-pooler.aws.neon.tech/neondb"
LOCAL = "postgresql://localhost/clefs_dev"


@pytest.fixture
def dotenv_keys(monkeypatch):
    keys: set[str] = set()
    monkeypatch.setattr(settings_mod, "_DOTENV_KEYS", keys)
    monkeypatch.delenv("CLEFS_ALLOW_DOTENV_REMOTE_DB", raising=False)
    return keys


def test_remote_psql_url_from_dotenv_is_refused(dotenv_keys):
    dotenv_keys.add("PSQL_DATABASE_URL")
    with pytest.raises(settings_mod.RemoteDatabaseRefused):
        settings_mod._refuse_dotenv_remote("PSQL_DATABASE_URL", REMOTE)


def test_remote_database_url_from_dotenv_is_refused(dotenv_keys):
    dotenv_keys.add("DATABASE_URL")
    with pytest.raises(settings_mod.RemoteDatabaseRefused):
        settings_mod._refuse_dotenv_remote("DATABASE_URL", REMOTE)


def test_local_url_from_dotenv_is_allowed(dotenv_keys):
    dotenv_keys.add("DATABASE_URL")
    assert settings_mod._refuse_dotenv_remote("DATABASE_URL", LOCAL) == LOCAL


def test_exported_remote_url_is_allowed(dotenv_keys):
    # Deployments export their variables into the process environment.
    assert settings_mod._refuse_dotenv_remote("DATABASE_URL", REMOTE) == REMOTE


def test_explicit_opt_in_allows_dotenv_remote(dotenv_keys, monkeypatch):
    dotenv_keys.add("PSQL_DATABASE_URL")
    monkeypatch.setenv("CLEFS_ALLOW_DOTENV_REMOTE_DB", "1")
    assert settings_mod._refuse_dotenv_remote("PSQL_DATABASE_URL", REMOTE) == REMOTE
