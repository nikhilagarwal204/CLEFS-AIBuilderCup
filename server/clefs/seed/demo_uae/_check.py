"""
`python -m clefs.cli demo-check`: read-only checks that a seeded demo hotel
matches 02 spec 9 (property, people, windows, mix, prices, pace). Each check
is (name, ok, detail); the CLI prints them and exits non-zero on a failure.
Later parts add their own checks through CHECKS in their module:

    CHECKS = [fn]   # fn(cur, slug) -> list[(name, ok, detail)]
"""

from __future__ import annotations

import importlib
import pkgutil
from datetime import timedelta
from typing import Any, Callable

Check = tuple[str, bool, str]


def _one(cur: Any, sql: str, params: tuple) -> dict:
    cur.execute(sql, params)
    return cur.fetchone() or {}


def core_checks(cur: Any, slug: str) -> list[Check]:
    out: list[Check] = []
    h = _one(cur, "SELECT name, status, star_rating, rooms_count, timezone, currency FROM hotels WHERE slug = %s",
             (slug,))
    if not h:
        return [("hotel exists", False, f"no hotel {slug}")]
    out.append(("hotel is a demo hotel", h["status"] == "demo",
                f"{h['name']} status={h['status']}"))
    r = _one(cur, "SELECT count(*) AS n, count(DISTINCT floor) AS floors, count(*) FILTER (WHERE bedrooms = 2) AS fam "
                  "FROM hotel_rooms WHERE hotel_slug = %s AND is_active", (slug,))
    out.append(("80 rooms on 8 floors, 10 family units", (r["n"], r["floors"], r["fam"]) == (80, 8, 10),
                f"rooms={r['n']} floors={r['floors']} fam={r['fam']}"))
    s = _one(cur, "SELECT count(*) AS n, count(*) FILTER (WHERE pin_hash IS NOT NULL) AS pins FROM staff_members "
                  "WHERE hotel_slug = %s", (slug,))
    out.append(("16 demo staff with PINs", (s["n"], s["pins"]) == (16, 16), f"staff={s['n']} pins={s['pins']}"))
    u = _one(cur, "SELECT count(DISTINCT role) AS n FROM hotel_memberships WHERE hotel_slug = %s AND is_active "
                  "AND role IN ('owner', 'gm', 'finance', 'revenue', 'front_desk')", (slug,))
    out.append(("owner, gm, finance, revenue, front desk users", u["n"] == 5, f"roles={u['n']}"))
    last = _one(cur, "SELECT max(date) AS d FROM occupancy_daily WHERE hotel_slug = %s AND source = 'DERIVED'",
                (slug,))
    anchor = last.get("d")
    if anchor is None:
        out.append(("history occupancy", False, "no derived occupancy (stays part not run)"))
        return out
    start = anchor - timedelta(days=119)
    o = _one(cur, "SELECT count(*) AS n, SUM(rooms_occupied) AS occ, SUM(rooms_available) AS avail, "
                  "SUM(room_revenue_minor) AS rev FROM occupancy_daily "
                  "WHERE hotel_slug = %s AND date BETWEEN %s AND %s",
             (slug, start, anchor))
    occ = (o["occ"] or 0) / max(1, o["avail"] or 0)
    adr = (o["rev"] or 0) / max(1, o["occ"] or 0) / 100
    out.append(("120 history days", o["n"] == 120, f"days={o['n']} from {start} to {anchor}"))
    out.append(("history occupancy 55-66%", 0.55 <= occ <= 0.66, f"occupancy={occ:.3f}"))
    out.append(("history ADR AED 240-330", 240 <= adr <= 330, f"adr={adr:.0f}"))
    return out + _mix_checks(cur, slug, start, anchor)


# The spec mix with a tolerance: the draw is random per stay, so a real run
# lands within a few points of each share.
MIX = {"BOOKING": 0.45, "AGODA": 0.15, "EXPEDIA": 0.12, "DIRECT": 0.20, "CORP_WALKIN": 0.08}
MIX_TOLERANCE = 0.04
MIX_GROUP = {"DIRECT_WEB": "DIRECT", "DIRECT_PHONE": "DIRECT", "CORPORATE": "CORP_WALKIN", "WALKIN": "CORP_WALKIN"}


def _mix_checks(cur: Any, slug: str, start: Any, anchor: Any) -> list[Check]:
    cur.execute(
        "SELECT channel_code, count(*) AS n FROM reservations WHERE hotel_slug = %s AND status <> 'CANCELLED' "
        "AND check_in BETWEEN %s AND %s GROUP BY 1",
        (slug, start, anchor),
    )
    counts: dict[str, int] = {}
    for r in cur.fetchall():
        key = MIX_GROUP.get(r["channel_code"], r["channel_code"])
        counts[key] = counts.get(key, 0) + r["n"]
    total = max(1, sum(counts.values()))
    shares = {k: counts.get(k, 0) / total for k in MIX}
    worst = max(abs(shares[k] - v) for k, v in MIX.items())
    out: list[Check] = [("channel mix 45/15/12/20/8", worst <= MIX_TOLERANCE,
                         ", ".join(f"{k}={v:.2f}" for k, v in shares.items()))]
    f = _one(cur, "SELECT count(*) AS n, count(DISTINCT check_in) AS days FROM reservations WHERE hotel_slug = %s "
                  "AND status = 'CONFIRMED' AND check_in > %s", (slug, anchor))
    out.append(("forward on the books", f["n"] > 0, f"reservations={f['n']} arrival days={f['days']}"))
    snaps = _one(cur, "SELECT count(DISTINCT snapshot_date) AS n FROM otb_snapshots WHERE hotel_slug = %s", (slug,))
    out.append(("pace snapshots", (snaps["n"] or 0) >= 2, f"snapshots={snaps['n']}"))
    base = _one(cur, "SELECT count(*) AS n FROM occupancy_daily WHERE hotel_slug = %s AND source = 'DEMO' "
                     "AND date BETWEEN '2024-01-01' AND '2025-12-31'", (slug,))
    out.append(("2024-2025 baselines", base["n"] == 731, f"days={base['n']}"))
    return out


def part_checks() -> list[Callable[[Any, str], list[Check]]]:
    """core_checks plus every sibling module's CHECKS list."""
    from clefs.seed import demo_uae

    fns: list[Callable[[Any, str], list[Check]]] = [core_checks]
    for info in pkgutil.iter_modules(demo_uae.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"clefs.seed.demo_uae.{info.name}")
        fns.extend(getattr(mod, "CHECKS", []) or [])
    return fns


def run_checks(cur: Any, slug: str) -> list[Check]:
    out: list[Check] = []
    for fn in part_checks():
        out.extend(fn(cur, slug))
    return out
