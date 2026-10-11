"""Nothing of the Dubai demo leaks into the Tokyo one: after a full seed of
the Japan hotel, no text column of any of its rows (every table keyed by
hotel_slug, the audit log included, its hotels row and its console users)
names Dubai, Deira, Rigga, AED, the UAE, the Emirates, the dirham, a +971
number or the Asia/Dubai zone."""

from __future__ import annotations

from datetime import date

from clefs.seed import demo_jp

ANCHOR = date(2026, 10, 21)
SLUG = "demo-t-jp-clean"
# Postgres regex (~*, case-insensitive; \m and \M are word boundaries).
FORBIDDEN = r"dubai|deira|rigga|emirates|dirham|\+971|\maed\M|\muae\M"
TEXT_TYPES = ("text", "character varying", "character", "json", "jsonb", "ARRAY")


def _text_columns(cur, table: str) -> list[str]:
    cur.execute(
        """SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema()
           AND table_name = %s AND data_type = ANY(%s) ORDER BY ordinal_position""", (table, list(TEXT_TYPES)))
    return [r["column_name"] for r in cur.fetchall()]


def _hits(cur, table: str, where: str, params: tuple) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for col in _text_columns(cur, table):
        cur.execute(f'SELECT left("{col}"::text, 160) AS v FROM "{table}" WHERE {where} AND "{col}"::text ~* %s '
                    "LIMIT 3", (*params, FORBIDDEN))
        found = [r["v"] for r in cur.fetchall()]
        if found:
            out[f"{table}.{col}"] = found
    return out


def test_the_japan_hotel_has_no_uae_strings(cur):
    out = demo_jp.seed(cur, SLUG, anchor=ANCHOR, scale=0.1, password="demo-pass-123")
    assert set(out["parts"]) == {"core", "core_catalog", "core_stays", "ops", "concierge_live", "deck"}
    cur.execute(
        """SELECT DISTINCT c.table_name FROM information_schema.columns c
           JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
           WHERE c.table_schema = current_schema() AND c.column_name = 'hotel_slug' AND t.table_type = 'BASE TABLE'
           ORDER BY 1""")
    tables = [r["table_name"] for r in cur.fetchall()]
    assert len(tables) > 100
    hits: dict[str, list[str]] = {}
    rows = 0
    for table in tables:
        cur.execute(f'SELECT count(*) AS n FROM "{table}" WHERE hotel_slug = %s', (SLUG,))
        rows += cur.fetchone()["n"]
        hits.update(_hits(cur, table, "hotel_slug = %s", (SLUG,)))
    hits.update(_hits(cur, "hotels", "slug = %s", (SLUG,)))
    hits.update(_hits(cur, "users", "id IN (SELECT user_id FROM hotel_memberships WHERE hotel_slug = %s)", (SLUG,)))
    assert rows > 5000  # the scan read a whole hotel, not an empty one
    assert hits == {}
