"""
Capture the golden legacy contract from the PRE-REFACTOR app.

Usage (from server/):

    git show aaf5cf4:server/main.py > /tmp/legacy_main.py
    .venv/bin/python -m tests.foundation.golden.capture_legacy --legacy-source /tmp/legacy_main.py

What it does:
  1. creates a throwaway database clefs_golden_<pid> on the LOCAL server named by
     TEST_DATABASE_URL (default postgresql://localhost/postgres);
  2. points PSQL_DATABASE_URL and DATABASE_URL at it BEFORE importing the legacy
     module. The legacy main.py calls load_dotenv(), and server/.env holds the
     production PSQL_DATABASE_URL; load_dotenv never overrides variables that are
     already set, so pre-setting both is what keeps this off production;
  3. forces SMS_ENABLED=false so auto-assignment never texts a real phone;
  4. runs tests/foundation/legacy_scenario.py through TestClient (startup event
     runs the legacy init_db) and writes golden/legacy_contract.json;
  5. drops the database.

The fixture is committed; this script only needs re-running if the scenario grows.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

HERE = Path(__file__).resolve().parent
OUT = HERE / "legacy_contract.json"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _db_url(base: str, dbname: str) -> str:
    u = urlparse(base)
    return urlunparse(u._replace(path="/" + dbname))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy-source", required=True, help="path to the pre-refactor main.py")
    args = ap.parse_args()

    base = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/postgres")
    host = urlparse(base).hostname or ""
    if host not in LOCAL_HOSTS:
        print(f"refusing to capture against non-local host {host!r}", file=sys.stderr)
        return 2

    import psycopg2

    dbname = f"clefs_golden_{os.getpid()}"
    admin = psycopg2.connect(base)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        c.execute(f'CREATE DATABASE "{dbname}"')
    url = _db_url(base, dbname)

    # Must be set before the legacy module runs load_dotenv().
    os.environ["PSQL_DATABASE_URL"] = url
    os.environ["DATABASE_URL"] = url
    os.environ["SMS_ENABLED"] = "false"

    try:
        spec = importlib.util.spec_from_file_location("legacy_main", args.legacy_source)
        assert spec and spec.loader
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        assert os.environ["PSQL_DATABASE_URL"] == url, "load_dotenv overrode the DB url"

        from fastapi.testclient import TestClient

        from tests.foundation.legacy_scenario import run_scenario

        with TestClient(legacy.app) as client:
            steps = run_scenario(client)
        OUT.write_text(json.dumps({"source": "server/main.py@aaf5cf4", "steps": steps}, indent=2, sort_keys=True) + "\n")
        print(f"wrote {OUT} ({len(steps)} steps)")
    finally:
        with admin.cursor() as c:
            c.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            c.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        admin.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
