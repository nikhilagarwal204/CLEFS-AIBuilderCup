"""
Database access: one pooled, bounded, leak-proof way to get a cursor.

    with db() as cur:                      # RealDictCursor; commit on success,
        cur.execute("SELECT ...", (x,))    # rollback on exception, connection
        row = cur.fetchone()               # always returned to the pool

Why a semaphore in front of the pool: psycopg2's ThreadedConnectionPool raises
immediately when exhausted, and FastAPI runs sync handlers on ~40 threads. The
BoundedSemaphore makes callers wait (up to ACQUIRE_TIMEOUT_S, then 503) instead
of failing, and the pool never has to open more than DB_POOL_MAX connections.

Do not nest db() blocks in request code: each pooled block is its own
connection and transaction. Pass `cur` down instead.

Tests: db._test_override(conn) makes every db() block a SAVEPOINT on one shared
connection whose outer transaction the test rolls back, so tests never commit.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import psycopg2
import psycopg2.extensions
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from clefs.core.errors import ClefsError
from clefs.settings import get_settings

log = logging.getLogger("clefs.db")

Json = psycopg2.extras.Json
RealDictCursor = psycopg2.extras.RealDictCursor

ACQUIRE_TIMEOUT_S = 10.0
# A pooled connection idle longer than this is pinged before use: Neon and
# RDS proxies drop idle connections silently, and the first statement on a dead
# socket would otherwise fail inside the caller's transaction.
IDLE_PING_AFTER_S = 60.0
CONNECT_KWARGS: dict[str, Any] = {
    "connect_timeout": 10,
    "application_name": "clefs",
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}

# ─── Pool state ───

_pool: ThreadedConnectionPool | None = None
_sem: threading.BoundedSemaphore | None = None
_pool_lock = threading.Lock()
_last_used: dict[int, float] = {}
# after-commit callbacks, keyed by id(connection) of an open db() block
_after_commit: dict[int, list[Callable[[], None]]] = {}

# ─── Test override state ───

_override_conn: psycopg2.extensions.connection | None = None
_override_lock = threading.RLock()
_override_callbacks: list[list[Callable[[], None]]] = []
_sp_counter = itertools.count(1)


def init_pool() -> None:
    """Create the pool (idempotent). Called from the app lifespan; db() also
    calls it lazily so scripts and the worker need no setup."""
    global _pool, _sem
    with _pool_lock:
        if _pool is not None:
            return
        s = get_settings()
        size = max(1, s.DB_POOL_MAX)
        _pool = ThreadedConnectionPool(0, size, s.database_url, **CONNECT_KWARGS)
        _sem = threading.BoundedSemaphore(size)


def close_pool() -> None:
    global _pool, _sem
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:  # pragma: no cover - best effort on shutdown
                pass
        _pool = None
        _sem = None
        _last_used.clear()


def _checkout() -> psycopg2.extensions.connection:
    assert _pool is not None
    for _ in range(3):
        conn = _pool.getconn()
        if conn.closed:
            _pool.putconn(conn, close=True)
            continue
        idle = time.monotonic() - _last_used.get(id(conn), time.monotonic())
        if idle > IDLE_PING_AFTER_S:
            try:
                with conn.cursor() as c:
                    c.execute("SELECT 1")
                conn.rollback()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                _pool.putconn(conn, close=True)
                continue
        return conn
    raise ClefsError(503, "Database unavailable, please retry", "db_unavailable")


def _run_callbacks(callbacks: list[Callable[[], None]]) -> None:
    for cb in callbacks:
        try:
            cb()
        except Exception:  # an after-commit hook must never fail the request
            log.exception("after-commit callback failed")


@contextmanager
def db(*, readonly: bool = False, statement_timeout_ms: int | None = None) -> Iterator[Any]:
    """Yield a RealDictCursor inside one transaction.

    readonly: SET TRANSACTION READ ONLY (ignored under the test override,
    because a savepoint cannot change the outer transaction's mode).
    statement_timeout_ms: SET LOCAL statement_timeout for this block only.
    """
    if _override_conn is not None:
        with _override_block(statement_timeout_ms) as cur:
            yield cur
        return

    if _pool is None:
        init_pool()
    assert _sem is not None
    if not _sem.acquire(timeout=ACQUIRE_TIMEOUT_S):
        raise ClefsError(503, "Database is busy, please retry", "db_busy")
    conn = None
    broken = False
    callbacks: list[Callable[[], None]] = []
    try:
        conn = _checkout()
        _after_commit[id(conn)] = []
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            if readonly:
                cur.execute("SET TRANSACTION READ ONLY")
            if statement_timeout_ms:
                cur.execute("SET LOCAL statement_timeout = %s", (int(statement_timeout_ms),))
            yield cur
            conn.commit()
            callbacks = _after_commit.pop(id(conn), [])
        except BaseException as exc:
            _after_commit.pop(id(conn), None)
            if isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
                broken = True
            try:
                conn.rollback()
            except Exception:
                broken = True
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass
    finally:
        if conn is not None and _pool is not None:
            _last_used[id(conn)] = time.monotonic()
            _pool.putconn(conn, close=broken or bool(conn.closed))
        _sem.release()
    _run_callbacks(callbacks)


@contextmanager
def _override_block(statement_timeout_ms: int | None) -> Iterator[Any]:
    conn = _override_conn
    assert conn is not None
    callbacks: list[Callable[[], None]] = []
    with _override_lock:
        sp = f"clefs_sp_{next(_sp_counter)}"
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(f"SAVEPOINT {sp}")
        _override_callbacks.append([])
        prev_timeout = None
        try:
            if statement_timeout_ms:
                cur.execute("SHOW statement_timeout")
                prev_timeout = cur.fetchone()["statement_timeout"]
                cur.execute("SET LOCAL statement_timeout = %s", (int(statement_timeout_ms),))
            yield cur
            if prev_timeout is not None:
                cur.execute("SET LOCAL statement_timeout = %s", (prev_timeout,))
            cur.execute(f"RELEASE SAVEPOINT {sp}")
            mine = _override_callbacks.pop()
            if _override_callbacks:
                _override_callbacks[-1].extend(mine)  # parent block decides
            else:
                callbacks = mine  # outermost block "committed"
        except BaseException:
            _override_callbacks.pop()
            try:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                cur.execute(f"RELEASE SAVEPOINT {sp}")
            except Exception:
                pass
            raise
        finally:
            cur.close()
    _run_callbacks(callbacks)


def on_commit(cur: Any, fn: Callable[[], None]) -> None:
    """Run fn after the transaction that owns `cur` commits; dropped on rollback.

    Used by events.publish(cur=...) on the local backend so an SSE event is only
    delivered for data that actually landed. Outside a db() block it runs now.
    """
    conn = getattr(cur, "connection", None)
    if _override_conn is not None and conn is _override_conn and _override_callbacks:
        _override_callbacks[-1].append(fn)
        return
    pending = _after_commit.get(id(conn)) if conn is not None else None
    if pending is None:
        fn()
    else:
        pending.append(fn)


# ─── Small helpers ───


def fetch_one(sql: str, params: Any = None, *, cur: Any = None) -> dict | None:
    if cur is not None:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None
    with db() as c:
        c.execute(sql, params)
        row = c.fetchone()
        return dict(row) if row is not None else None


def fetch_all(sql: str, params: Any = None, *, cur: Any = None) -> list[dict]:
    if cur is not None:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    with db() as c:
        c.execute(sql, params)
        return [dict(r) for r in c.fetchall()]


def execute(sql: str, params: Any = None, *, cur: Any = None) -> int:
    if cur is not None:
        cur.execute(sql, params)
        return cur.rowcount
    with db() as c:
        c.execute(sql, params)
        return c.rowcount


def raw_connection(unpooled: bool = False) -> psycopg2.extensions.connection:
    """A fresh connection the caller owns and must close.

    unpooled=True uses DATABASE_URL_UNPOOLED: LISTEN, session advisory locks and
    migrations need a real session, which PgBouncer transaction mode does not give.
    """
    s = get_settings()
    url = s.database_url_unpooled if unpooled else s.database_url
    return psycopg2.connect(url, **CONNECT_KWARGS)


# ─── Legacy shim ───


class _LegacyConnection:
    """What the old get_db() returned, but close() hands the connection back to
    the pool instead of dropping it (the old code leaked on every exception)."""

    def __init__(self, conn: psycopg2.extensions.connection, release: Callable[[], None] | None):
        self._conn = conn
        self._release = release

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        return self._conn.cursor(*args, **kwargs)

    def commit(self) -> None:
        if self._release is not None:
            self._conn.commit()

    def rollback(self) -> None:
        if self._release is not None:
            self._conn.rollback()

    def close(self) -> None:
        if self._release is not None:
            release, self._release = self._release, None
            release()


def get_db() -> _LegacyConnection:
    """Deprecated: use `with db() as cur`. Kept so any straggler code still works.
    Under the test override it shares the test transaction (commit is a no-op)."""
    if _override_conn is not None:
        return _LegacyConnection(_override_conn, None)
    if _pool is None:
        init_pool()
    assert _sem is not None and _pool is not None
    if not _sem.acquire(timeout=ACQUIRE_TIMEOUT_S):
        raise ClefsError(503, "Database is busy, please retry", "db_busy")
    try:
        conn = _checkout()
    except BaseException:
        _sem.release()
        raise

    def release() -> None:
        assert _pool is not None and _sem is not None
        try:
            if not conn.closed:
                conn.rollback()
        finally:
            _pool.putconn(conn, close=bool(conn.closed))
            _sem.release()

    return _LegacyConnection(conn, release)


# ─── Test hooks ───


def _test_override(conn: psycopg2.extensions.connection) -> None:
    global _override_conn
    _override_conn = conn
    _override_callbacks.clear()


def _test_clear() -> None:
    global _override_conn
    _override_conn = None
    _override_callbacks.clear()


def in_test_override() -> bool:
    return _override_conn is not None
