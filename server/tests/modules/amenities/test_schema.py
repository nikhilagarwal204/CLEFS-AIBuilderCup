"""The amenities migrations create every amn_ table with the Canon shapes."""

from __future__ import annotations

TABLES = (
    "amn_items", "amn_item_costs", "amn_locations", "amn_par_levels", "amn_stock_counts", "amn_seed_runs",
    "amn_issuance", "amn_request_lines", "amn_request_scans", "amn_variance_daily", "amn_daily_totals",
    "amn_item_rates", "amn_benchmarks",
)


def test_every_table_exists(cur):
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'amn\\_%%'")
    found = {r["table_name"] for r in cur.fetchall()}
    assert set(TABLES) <= found


def test_money_columns_are_bigint_minor_units(cur):
    cur.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_name LIKE 'amn\\_%%' AND column_name LIKE '%%\\_minor'"
    )
    rows = cur.fetchall()
    assert rows
    assert all(r["data_type"] == "bigint" for r in rows), rows


def test_new_instants_are_timestamptz(cur):
    cur.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_name LIKE 'amn\\_%%' AND (column_name LIKE '%%\\_at')"
    )
    rows = cur.fetchall()
    assert rows
    assert all(r["data_type"] == "timestamp with time zone" for r in rows), rows
