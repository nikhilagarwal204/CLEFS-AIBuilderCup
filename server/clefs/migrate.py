"""
Migration runner.

    python -m clefs.migrate up                 apply every pending migration, in version order
    python -m clefs.migrate status             applied / pending / drifted / missing
    python -m clefs.migrate verify             CI gate: names, ranges, duplicates, checksum drift
    python -m clefs.migrate new --module service "add sla tables" [--py] [--number 0105]

Discovery: clefs/migrations/ (core + integrator) plus clefs/modules/<m>/migrations/
for every enabled module. Files are NNNN_<module>_<desc>.sql or .py; a .py file
defines `def up(cur): ...`.

Why "every unapplied version in ascending order" and not a high-water mark:
eight lanes land migrations in parallel, so service's 0103 may be merged after
profit_guard's 0305. Each number is owned by exactly one module range, and
foreign keys may only point at core tables (0001-0099), so cross-module order
never matters.

Each file runs in its own transaction, which first takes
pg_advisory_xact_lock and re-checks schema_migrations. Two processes starting at
once (uvicorn workers, a release step, the worker, Vercel cold starts) therefore
apply every file exactly once, in order. A transaction-level lock (not a
session lock) is used on purpose: it also works through PgBouncer transaction
pooling (a Neon "-pooler" URL), where a session lock could be taken on one
backend and never released. The unpooled URL is still preferred.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras

from clefs import registry

CLEFS_DIR = Path(__file__).resolve().parent
CORE_DIR = CLEFS_DIR / "migrations"
MODULES_DIR = CLEFS_DIR / "modules"
LOCK_KEY = "clefs_migrate"
FILENAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.(sql|py)$")

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INT PRIMARY KEY,
    module      TEXT NOT NULL,
    name        TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_ms INT NOT NULL DEFAULT 0
)
"""


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    module: str
    name: str  # file name without extension
    path: Path
    kind: str  # "sql" | "py"

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


# ─── Discovery ───


def _sources(modules: list[str]) -> list[tuple[Path, tuple[str, ...]]]:
    out: list[tuple[Path, tuple[str, ...]]] = [(CORE_DIR, registry.CORE_MIGRATION_OWNERS)]
    out += [(MODULES_DIR / m / "migrations", (m,)) for m in modules]
    return out


def discover(modules: list[str] | None = None) -> list[Migration]:
    """All migration files for core + the given modules (default: enabled ones).
    Raises MigrationError listing every naming, range or duplicate problem."""
    if modules is None:
        modules = registry.enabled_modules()
    found: list[Migration] = []
    errors: list[str] = []
    for directory, owners in _sources(modules):
        if not directory.is_dir():
            continue
        for p in sorted(directory.iterdir()):
            if p.name.startswith((".", "_")) or p.is_dir():
                continue  # .gitkeep, __init__.py, __pycache__
            if p.suffix not in (".sql", ".py"):
                errors.append(f"{p.relative_to(CLEFS_DIR)}: only .sql and .py migrations are allowed")
                continue
            m = FILENAME_RE.match(p.name)
            if not m:
                errors.append(f"{p.relative_to(CLEFS_DIR)}: name must be NNNN_<module>_<desc>.sql|.py")
                continue
            version, rest, kind = int(m.group(1)), m.group(2), m.group(3)
            owner = next(
                (o for o in sorted(owners, key=len, reverse=True) if rest.startswith(o + "_")),
                None,
            )
            if owner is None:
                errors.append(f"{p.relative_to(CLEFS_DIR)}: file name must start NNNN_{'|'.join(owners)}_")
                continue
            lo, hi = registry.MIGRATION_RANGES[owner]
            if not lo <= version <= hi:
                errors.append(f"{p.relative_to(CLEFS_DIR)}: {version:04d} outside {owner} range {lo:04d}-{hi:04d}")
                continue
            found.append(Migration(version, owner, p.stem, p, kind))
    seen: dict[int, Migration] = {}
    for mig in found:
        if mig.version in seen:
            errors.append(f"duplicate version {mig.version:04d}: {seen[mig.version].path.name} and {mig.path.name}")
        seen[mig.version] = mig
    if errors:
        raise MigrationError("\n".join(errors))
    return sorted(found, key=lambda x: x.version)


# ─── Connection helpers ───


def _connect(database_url: str | None) -> Any:
    if database_url:
        return psycopg2.connect(database_url, application_name="clefs-migrate", connect_timeout=10)
    from clefs.db import raw_connection

    conn = raw_connection(unpooled=True)
    return conn


def _redact(url: str) -> str:
    u = urlparse(url)
    host = u.hostname or "localhost"
    return f"{u.scheme}://{host}{':' + str(u.port) if u.port else ''}{u.path}"


def _target_url(database_url: str | None) -> str:
    if database_url:
        return database_url
    from clefs.settings import get_settings

    return get_settings().database_url_unpooled


def _applied(conn: Any) -> dict[int, dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT to_regclass('schema_migrations') AS t")
        if cur.fetchone()["t"] is None:
            return {}
        cur.execute("SELECT version, module, name, checksum, applied_at, duration_ms FROM schema_migrations")
        return {r["version"]: dict(r) for r in cur.fetchall()}


def _load_py(mig: Migration) -> Any:
    spec = importlib.util.spec_from_file_location(f"clefs_migration_{mig.version:04d}", mig.path)
    if spec is None or spec.loader is None:
        raise MigrationError(f"cannot import {mig.path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, "up", None)):
        raise MigrationError(f"{mig.path.name} must define up(cur)")
    return mod


# ─── Commands ───


def up(
    modules: list[str] | None = None,
    *,
    database_url: str | None = None,
    verbose: bool = True,
) -> list[str]:
    """Apply every pending migration. Returns the names applied (empty = no-op)."""
    migrations = discover(modules)
    conn = _connect(database_url)
    applied_now: list[str] = []
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            # Under the lock so two first-ever runs cannot race on CREATE TABLE.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK_KEY,))
            cur.execute(SCHEMA_MIGRATIONS_DDL)
        conn.commit()
        applied = _applied(conn)  # a snapshot; each file re-checks under the lock
        for mig in migrations:
            prev = applied.get(mig.version)
            if prev is not None:
                if prev["checksum"] != mig.checksum and verbose:
                    print(f"[migrate] WARNING checksum drift in {mig.name} (applied file was edited)", file=sys.stderr)
                continue
            t0 = time.monotonic()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (LOCK_KEY,))
                    cur.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (mig.version,))
                    if cur.fetchone() is not None:
                        conn.rollback()  # another runner applied it while we waited
                        continue
                    if mig.kind == "sql":
                        sql = mig.path.read_text(encoding="utf-8")
                        if sql.strip():
                            cur.execute(sql)
                    else:
                        _load_py(mig).up(cur)
                    cur.execute(
                        "INSERT INTO schema_migrations (version, module, name, checksum, duration_ms) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (mig.version, mig.module, mig.name, mig.checksum, int((time.monotonic() - t0) * 1000)),
                    )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                raise MigrationError(f"{mig.path.name} failed: {exc}") from exc
            applied_now.append(mig.name)
            if verbose:
                print(f"[migrate] applied {mig.name} ({int((time.monotonic() - t0) * 1000)} ms)")
    finally:
        conn.close()
    if verbose and not applied_now:
        print("[migrate] nothing to apply")
    return applied_now


def status(modules: list[str] | None = None, *, database_url: str | None = None) -> list[dict]:
    migrations = discover(modules)
    conn = _connect(database_url)
    try:
        applied = _applied(conn)
    finally:
        conn.close()
    rows: list[dict] = []
    on_disk = {m.version for m in migrations}
    for mig in migrations:
        a = applied.get(mig.version)
        if a is None:
            state = "pending"
        elif a["checksum"] != mig.checksum:
            state = "drift"
        else:
            state = "applied"
        rows.append({"version": mig.version, "module": mig.module, "name": mig.name, "state": state})
    for v, a in sorted(applied.items()):
        if v not in on_disk:
            rows.append({"version": v, "module": a["module"], "name": a["name"], "state": "missing"})
    return sorted(rows, key=lambda r: r["version"])


def verify(modules: list[str] | None = None, *, database_url: str | None = None, strict: bool = False) -> list[str]:
    """Problems that should fail CI. Pending migrations only count when strict."""
    try:
        rows = status(modules, database_url=database_url)
    except MigrationError as exc:
        return str(exc).splitlines()
    enabled = set(registry.enabled_modules() if modules is None else modules) | set(registry.CORE_MIGRATION_OWNERS)
    problems: list[str] = []
    for r in rows:
        if r["state"] == "drift":
            problems.append(f"checksum drift: {r['name']} was edited after it was applied")
        elif r["state"] == "missing" and r["module"] in enabled:
            problems.append(f"missing file: {r['name']} is applied but not on disk")
        elif r["state"] == "pending" and strict:
            problems.append(f"pending: {r['name']}")
    return problems


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:48] or "change"


def new(module: str, desc: str, *, py: bool = False, number: int | None = None) -> Path:
    if module not in registry.MIGRATION_RANGES:
        raise MigrationError(f"unknown module {module!r}")
    directory = CORE_DIR if module in registry.CORE_MIGRATION_OWNERS else MODULES_DIR / module / "migrations"
    directory.mkdir(parents=True, exist_ok=True)
    lo, hi = registry.MIGRATION_RANGES[module]
    all_mods = list(registry.INSTALLED_MODULES)
    taken = {m.version for m in discover(all_mods)}
    if number is None:
        mine = [v for v in taken if lo <= v <= hi]
        number = (max(mine) + 1) if mine else lo
    if not lo <= number <= hi:
        raise MigrationError(f"{number:04d} outside {module} range {lo:04d}-{hi:04d}")
    if number in taken:
        raise MigrationError(f"{number:04d} is already taken")
    name = f"{number:04d}_{module}_{_slug(desc)}"
    path = directory / (name + (".py" if py else ".sql"))
    if py:
        body = f'"""{name}: {desc}"""\n\n\ndef up(cur):\n    pass\n'
    else:
        body = f"-- {name}: {desc}\n-- Runs in its own transaction. Keep it idempotent where cheap (IF NOT EXISTS).\n"
    path.write_text(body, encoding="utf-8")
    return path


# ─── CLI ───


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m clefs.migrate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("up")
    sub.add_parser("status")
    v = sub.add_parser("verify")
    v.add_argument("--strict", action="store_true", help="also fail on pending migrations")
    n = sub.add_parser("new")
    n.add_argument("--module", required=True)
    n.add_argument("--py", action="store_true")
    n.add_argument("--number", type=int)
    n.add_argument("desc")
    args = ap.parse_args(argv)

    if args.cmd == "new":
        print(new(args.module, args.desc, py=args.py, number=args.number))
        return 0

    from clefs.settings import get_settings

    target = _target_url(None)
    for note in get_settings()._notes:
        print(f"[migrate] {note}", file=sys.stderr)
    print(f"[migrate] target {_redact(target)}", file=sys.stderr)
    if args.cmd == "up":
        up()
        return 0
    if args.cmd == "status":
        for r in status():
            print(f"{r['version']:04d}  {r['state']:<8} {r['name']}")
        return 0
    problems = verify(strict=args.strict)
    for p in problems:
        print(f"[migrate] {p}", file=sys.stderr)
    print("[migrate] verify " + ("FAILED" if problems else "ok"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
