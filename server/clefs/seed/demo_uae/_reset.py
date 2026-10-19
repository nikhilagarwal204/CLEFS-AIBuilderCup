"""
Idempotency for the demo seed: wipe one demo hotel's rows, then create or
refresh its hotels row.

Why catalogue-driven: fifteen modules own ~200 tables and keep adding more.
A hand-kept delete list would rot the first time a lane adds a table, and a
stale row then breaks a re-seed with a unique violation. Instead every base
table with a hotel_slug column is emptied for this hotel, in passes (a
parent that still has children fails inside a savepoint and is retried on
the next pass), and child tables without a hotel_slug of their own (the
per-entry revision tables) are cleared through their foreign key first.

What is kept: the hotels row itself (audit_log references it and is
append-only by trigger, Part 1 section 8) and audit_log. Demo users are
global rows; their memberships go with the hotel and are re-created.
"""

from __future__ import annotations

from typing import Any

import psycopg2

from clefs.core.errors import ClefsError

# Never deleted: hotels is the tenant root; audit_log is append-only.
KEEP = frozenset({"hotels", "audit_log"})
MAX_PASSES = 12


# ─── Catalogue ───


def _hotel_tables(cur: Any) -> list[str]:
    cur.execute(
        """
        SELECT c.table_name FROM information_schema.columns c
        JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = 'public' AND c.column_name = 'hotel_slug' AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name
        """
    )
    return [r["table_name"] for r in cur.fetchall() if r["table_name"] not in KEEP]


def _blocking_children(cur: Any, table: str) -> list[tuple[str, list[str], list[str]]]:
    """Foreign keys into `table` that would block a delete (NO ACTION or
    RESTRICT), as (child, child columns, parent columns)."""
    cur.execute(
        """
        SELECT ch.relname AS child,
               ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, i)
                     JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.i) AS ccols,
               ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i)
                     JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n ORDER BY k.i) AS pcols
        FROM pg_constraint con
        JOIN pg_class ch ON ch.oid = con.conrelid
        JOIN pg_class pa ON pa.oid = con.confrelid
        JOIN pg_namespace ns ON ns.oid = pa.relnamespace
        WHERE con.contype = 'f' AND ns.nspname = 'public' AND pa.relname = %s AND con.confdeltype IN ('a', 'r')
        """,
        (table,),
    )
    return [(r["child"], list(r["ccols"]), list(r["pcols"])) for r in cur.fetchall()]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# ─── Purge ───


def _purge(cur: Any, table: str, where: str, slug: str, has_slug: set[str], depth: int = 0) -> None:
    """Delete `table` rows matching `where`, clearing blocking children that
    have no hotel_slug of their own first (those with one get their own
    pass). Depth-capped against FK cycles."""
    if depth < 4:
        for child, ccols, pcols in _blocking_children(cur, table):
            if child == table or child in has_slug or child in KEEP:
                continue
            cw = f"({', '.join(map(_q, ccols))}) IN (SELECT {', '.join(map(_q, pcols))} FROM {_q(table)} WHERE {where})"
            _purge(cur, child, cw, slug, has_slug, depth + 1)
    cur.execute(f"DELETE FROM {_q(table)} WHERE {where}", {"slug": slug})


def wipe_hotel_rows(cur: Any, slug: str) -> dict:
    """Empty every hotel-scoped table for `slug`. Returns {deleted_tables,
    kept: [tables that refused, e.g. an append-only ledger]}."""
    tables = _hotel_tables(cur)
    has_slug = set(tables)
    pending = list(tables)
    for _ in range(MAX_PASSES):
        left: list[str] = []
        for t in pending:
            cur.execute("SAVEPOINT demo_wipe")
            try:
                _purge(cur, t, "hotel_slug = %(slug)s", slug, has_slug)
                cur.execute("RELEASE SAVEPOINT demo_wipe")
            except psycopg2.Error:
                cur.execute("ROLLBACK TO SAVEPOINT demo_wipe")
                left.append(t)
        if not left or len(left) == len(pending):
            pending = left
            break
        pending = left
    return {"tables": len(tables) - len(pending), "kept": pending}


# ─── The hotel row ───

NAME = "Al Rigga Crest Hotel"
# The eight guest UI languages (concierge constants.GUEST_LANGUAGES and the
# client's stay/i18n). Kept here as data: the seed never imports a module.
GUEST_LANGUAGES = ("en", "ar", "ru", "hi", "ur", "zh", "de", "fr")
# Deira, off Al Rigga Road: ~9 km from DWTC, ~7 km from Expo Centre Sharjah
# (02 spec 9). Demand's distance scoring and Visibility's area searches need
# the coordinates.
LAT, LNG = "25.2650", "55.3230"


def hotel_fields(slug: str) -> dict[str, Any]:
    from decimal import Decimal

    return {
        "name": NAME if slug == "demo-deira-crest" else f"{NAME[:-7]} {slug}",
        "city": "Dubai",
        "country": "AE",
        "emirate": "dubai",
        "timezone": "Asia/Dubai",
        "currency": "AED",
        "locale": "en-AE",
        # Every guest UI language the product has (05 spec 2.14).
        "guest_languages": list(GUEST_LANGUAGES),
        "weekend_days": [6, 7],
        "star_rating": 3,
        "rooms_count": 80,
        "category": "hotel",
        "status": "demo",
        "lat": Decimal(LAT),
        "lng": Decimal(LNG),
        "address": "Al Rigga Road, Deira, Dubai",
        # The deck's screens greet guests as Clefs (05 spec 2.15).
        "assistant_name": "Clefs",
        "website": "https://example.com/al-rigga-crest-demo",
        # The tax engine's fallback when no dated profile row exists; the
        # dated row itself is written by core.py through core.tax.
        "tax_profile": {"convention": "A", "property_category": "hotel_3"},
        "legacy_open_access": False,
    }


def prepare_hotel(ctx: Any) -> bool:
    """Refuse a non-demo hotel, wipe a demo one, then create or refresh the
    row. Returns True when the hotels row was created."""
    from clefs.core import hotels

    cur, slug = ctx.cur, ctx.slug
    existing = hotels.find_hotel(cur, slug, fresh=True)
    if existing is not None and existing.status != "demo":
        raise ClefsError(409, f"{slug} is a {existing.status} hotel, not a demo hotel; refusing to reseed it",
                         "not_demo")
    fields = hotel_fields(slug)
    if existing is None:
        hotels.create_hotel(cur, slug=slug, **fields)
        ctx.stats["wipe"] = {"tables": 0, "kept": []}
        return True
    ctx.stats["wipe"] = wipe_hotel_rows(cur, slug)
    changes = {k: v for k, v in fields.items() if k != "legacy_open_access"}
    hotels.update_hotel(cur, slug, changes)
    cur.execute("UPDATE hotels SET legacy_open_access = FALSE WHERE slug = %s", (slug,))
    hotels.invalidate(slug)
    return False
