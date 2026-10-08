"""A live seed never plants the future (QA OWN-08, GS-3): run at 13:42 Dubai
on the anchor day, every event it writes on that day (requests, incidents,
inquiries, amenity logs, the brief) happened by 13:42, the last three hours
keep their exact minutes (SLA ages), and the planted cases still surface."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from clefs.seed import demo_uae
from clefs.seed.demo_uae import TZ, DemoCtx, live_anchor, ops

ANCHOR = date(2026, 9, 27)
AS_OF = datetime(2026, 9, 27, 13, 42, tzinfo=TZ)
SLUG = "demo-t-live"
PARTS = ["core", "core_catalog", "core_stays", "ops"]
# Columns that record when something happened (as opposed to deadlines,
# validity ends or scheduled runs, which may legitimately lie ahead).
EVENT_COLS = {"created_at", "received_at", "generated_at", "logged_at", "accepted_at", "acknowledged_at",
              "resolved_at", "completed_at", "sent_at", "occurred_at", "opened_at", "closed_at", "posted_at",
              "booked_at", "paid_at", "answered_at", "responded_at", "first_response_at", "checked_in_at",
              "checked_out_at", "reported_at", "started_at", "finished_at", "delivered_at", "read_at"}


# ─── The warp ───


def test_warp_keeps_order_bounds_and_recent_minutes():
    ctx = DemoCtx(cur=None, slug="x", anchor=ANCHOR, as_of=AS_OF)
    times = [ctx.at(ANCHOR, h, m) for h in range(24) for m in (0, 15, 30, 45)]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert ctx.now == AS_OF and ctx.at(ANCHOR, 4, 30).hour == 4  # before the 04:00 roll stays put
    assert all(t <= AS_OF for t in times if t < ctx.at(ANCHOR, 21, 0) + timedelta(seconds=1))
    # the last 3 h shift as a block: a request 28 minutes old stays 28 minutes old
    assert AS_OF - ctx.at(ANCHOR, 20, 32) == timedelta(minutes=28)
    # other days never move; without as_of nothing moves
    assert ctx.at(ANCHOR - timedelta(days=1), 23, 30) == datetime(2026, 9, 26, 23, 30, tzinfo=TZ)
    assert DemoCtx(cur=None, slug="x", anchor=ANCHOR).now == datetime(2026, 9, 27, 21, 0, tzinfo=TZ)


def test_live_anchor_picks_last_night_early_and_the_evening_late():
    assert live_anchor(datetime(2026, 9, 27, 8, 59, tzinfo=TZ)) == (date(2026, 9, 26), None)
    assert live_anchor(datetime(2026, 9, 27, 13, 42, 31, tzinfo=TZ)) == (ANCHOR, AS_OF)
    assert live_anchor(datetime(2026, 9, 27, 22, 0, tzinfo=TZ)) == (ANCHOR, None)
    with pytest.raises(ValueError):
        demo_uae.check_as_of(ANCHOR, datetime(2026, 9, 28, 13, 0, tzinfo=TZ))


# ─── A live seed ───


def _future_events(cur, slug: str, lo: datetime, hi: datetime, as_of: datetime) -> dict[str, int]:
    """{table.column: rows} of anchor-day event instants after as_of."""
    cur.execute(
        """SELECT c.table_name, c.column_name FROM information_schema.columns c
           JOIN information_schema.columns h ON h.table_schema = c.table_schema AND h.table_name = c.table_name
                AND h.column_name = 'hotel_slug'
           WHERE c.table_schema = current_schema() AND c.data_type = 'timestamp with time zone'""")
    out: dict[str, int] = {}
    for r in cur.fetchall():
        if r["column_name"] not in EVENT_COLS:
            continue
        cur.execute(f'SELECT count(*) AS n FROM "{r["table_name"]}" WHERE hotel_slug = %s '
                    f'AND "{r["column_name"]}" > %s AND "{r["column_name"]}" BETWEEN %s AND %s',
                    (slug, as_of + timedelta(seconds=59), lo, hi))
        if n := cur.fetchone()["n"]:
            out[f'{r["table_name"]}.{r["column_name"]}'] = n
    return out


def test_live_seed_writes_nothing_after_as_of_and_plants_still_surface(cur):
    out = demo_uae.seed(cur, SLUG, anchor=ANCHOR, as_of=AS_OF, parts=PARTS, scale=0.1, password="demo-pass-123")
    assert out["as_of"] == AS_OF.isoformat()
    # The anchor day up to the old evening plus an hour (database now()
    # defaults carry the real date, outside this window).
    lo = datetime(2026, 9, 27, 0, 0, tzinfo=TZ)
    hi = datetime(2026, 9, 27, 22, 0, tzinfo=TZ)
    assert _future_events(cur, SLUG, lo, hi, AS_OF) == {}
    missing = [(p["detector"], p["key"]) for p in out["plants"]
               if p["module"] == "service" and p["klass"] != "DECOY" and not ops.surfaced(cur, SLUG, p)]
    assert missing == []


def test_cli_without_anchor_seeds_as_of_the_live_clock(cur, capsys, monkeypatch):
    from clefs import cli

    monkeypatch.setattr(demo_uae, "live_anchor", lambda wall=None: (ANCHOR, AS_OF))
    assert cli.main(["seed-demo", "--hotel", "demo-t-livecli", "--parts", "core", "--scale", "0.05",
                     "--password", "demo-pass-123", "--no-manifest"]) == 0
    assert "anchor=2026-09-27 as_of=2026-09-27T13:42:00+04:00" in capsys.readouterr().out
    # --anchor keeps the deterministic anchor evening
    assert cli.main(["seed-demo", "--hotel", "demo-t-livecli", "--anchor", "2026-09-27", "--parts", "core",
                     "--scale", "0.05", "--password", "demo-pass-123", "--no-manifest"]) == 0
    assert "as_of=2026-09-27T21:00:00+04:00" in capsys.readouterr().out
