"""Schema smoke checks for the upsell migrations (0550-0551)."""


def test_tables_exist(cur):
    for t in ("ups_outlets", "ups_menu_items", "ups_upsell_pairs", "ups_quotes", "ups_orders", "ups_order_items", "ups_upsell_events"):
        cur.execute("SELECT to_regclass(%s) AS t", (f"public.{t}",))
        assert cur.fetchone()["t"] is not None, t
