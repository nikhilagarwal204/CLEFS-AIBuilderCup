"""
demo-check's property checks for the Tokyo demo hotel (the counterpart of
demo_uae._check.core_checks): the hotel row, rooms, staff and users, then
the history's volume, occupancy, ADR in JPY and channel mix. demo-check
skips the volume checks below scale 1, as for the UAE hotel.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs.seed.demo_jp import core as cast
from clefs.seed.demo_uae._check import Check, _one

OCCUPANCY = (0.78, 0.92)
ADR_JPY = (15_000, 23_000)
# The channel shares of core_stays.CHANNEL_MIX, grouped as an owner reads them.
MIX = {"BOOKING": 0.22, "AGODA": 0.14, "EXPEDIA": 0.10, "JAPAN_OTA": 0.26, "TRIPCOM": 0.06, "DIRECT": 0.16,
       "CORP_WALKIN": 0.06}
MIX_TOLERANCE = 0.04
MIX_GROUP = {"RAKUTEN": "JAPAN_OTA", "JALAN": "JAPAN_OTA", "DIRECT_WEB": "DIRECT", "DIRECT_PHONE": "DIRECT",
             "CORPORATE": "CORP_WALKIN", "WALKIN": "CORP_WALKIN"}


def core_checks(cur: Any, slug: str) -> list[Check]:
    out: list[Check] = []
    h = _one(cur, "SELECT name, status, country, timezone, currency, rooms_count FROM hotels WHERE slug = %s", (slug,))
    if not h:
        return [("hotel exists", False, f"no hotel {slug}")]
    out.append(("hotel is a demo hotel", h["status"] == "demo", f"{h['name']} status={h['status']}"))
    out.append(("Tokyo: JP, Asia/Tokyo, JPY", (h["country"].strip(), h["timezone"], h["currency"].strip())
                == ("JP", "Asia/Tokyo", "JPY"), f"{h['country']} {h['timezone']} {h['currency']}"))
    r = _one(cur, "SELECT count(*) AS n, count(DISTINCT floor) AS floors, "
                  "count(*) FILTER (WHERE room_type_code = %s) AS fam FROM hotel_rooms "
                  "WHERE hotel_slug = %s AND is_active", (cast.FAMILY_TYPE, slug))
    out.append(("80 rooms on 8 floors, 10 family rooms", (r["n"], r["floors"], r["fam"]) == (80, 8, 10),
                f"rooms={r['n']} floors={r['floors']} family={r['fam']}"))
    s = _one(cur, "SELECT count(*) AS n, count(*) FILTER (WHERE pin_hash IS NOT NULL) AS pins FROM staff_members "
                  "WHERE hotel_slug = %s", (slug,))
    n_staff = len(cast.STAFF)
    out.append((f"{n_staff} demo staff with PINs", (s["n"], s["pins"]) == (n_staff, n_staff),
                f"staff={s['n']} pins={s['pins']}"))
    roles = [role for role, *_rest in cast.USERS]
    u = _one(cur, "SELECT count(DISTINCT role) AS n FROM hotel_memberships WHERE hotel_slug = %s AND is_active "
                  "AND role = ANY(%s)", (slug, roles))
    out.append(("owner, gm, finance, revenue, front desk, duty manager users", u["n"] == len(roles),
                f"roles={u['n']}"))
    last = _one(cur, "SELECT max(date) AS d FROM occupancy_daily WHERE hotel_slug = %s AND source = 'DERIVED'",
                (slug,))
    anchor = last.get("d")
    if anchor is None:
        out.append(("history occupancy", False, "no derived occupancy (stays part not run)"))
        return out
    start = anchor - timedelta(days=119)
    o = _one(cur, "SELECT count(*) AS n, SUM(rooms_occupied) AS occ, SUM(rooms_available) AS avail, "
                  "SUM(room_revenue_minor) AS rev FROM occupancy_daily "
                  "WHERE hotel_slug = %s AND date BETWEEN %s AND %s", (slug, start, anchor))
    occ = (o["occ"] or 0) / max(1, o["avail"] or 0)
    adr = (o["rev"] or 0) / max(1, o["occ"] or 0)  # the yen has no minor unit
    out.append(("120 history days", o["n"] == 120, f"days={o['n']} from {start} to {anchor}"))
    out.append((f"history occupancy {OCCUPANCY[0]:.0%}-{OCCUPANCY[1]:.0%}", OCCUPANCY[0] <= occ <= OCCUPANCY[1],
                f"occupancy={occ:.3f}"))
    out.append((f"history ADR JPY {ADR_JPY[0]:,}-{ADR_JPY[1]:,}", ADR_JPY[0] <= adr <= ADR_JPY[1], f"adr={adr:,.0f}"))
    cur.execute("SELECT channel_code, count(*) AS n FROM reservations WHERE hotel_slug = %s AND status <> 'CANCELLED' "
                "AND check_in BETWEEN %s AND %s GROUP BY 1", (slug, start, anchor))
    counts: dict[str, int] = {}
    for row in cur.fetchall():
        key = MIX_GROUP.get(row["channel_code"], row["channel_code"])
        counts[key] = counts.get(key, 0) + row["n"]
    total = max(1, sum(counts.values()))
    shares = {k: counts.get(k, 0) / total for k in MIX}
    worst = max(abs(shares[k] - v) for k, v in MIX.items())
    out.append(("channel mix 22/14/10/26/6/16/6", worst <= MIX_TOLERANCE,
                ", ".join(f"{k}={v:.2f}" for k, v in shares.items())))
    f = _one(cur, "SELECT count(*) AS n, count(DISTINCT check_in) AS days FROM reservations WHERE hotel_slug = %s "
                  "AND status = 'CONFIRMED' AND check_in > %s", (slug, anchor))
    out.append(("forward on the books", f["n"] > 0, f"reservations={f['n']} arrival days={f['days']}"))
    return out
