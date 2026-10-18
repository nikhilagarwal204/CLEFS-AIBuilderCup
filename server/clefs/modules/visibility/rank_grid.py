"""
Map rank grid: an internal KPI, no heatmap UI (04 spec 5.3).

Each week, for 3-5 keywords, a 7x7 grid of Places Text Search calls centred
on the hotel asks "who ranks here" with the field mask `places.id` ONLY. That
mask is the Essentials (IDs Only) SKU with an unlimited free cap, which is
what makes ~1,000 calls a month cost $0; any extra field (displayName, ...)
would move every call to a paid SKU. A contract test pins the mask.

IDs cannot resolve names, so the hotel's and competitors' place ids come
from vis_profile and the vis_competitors mirror of demand.api.comp_set.

Monthly, a 3x3 subsample per keyword is compared with SerpApi google_maps
(Spearman rho and top-3 agreement); drift shows "ordering may differ from
Maps" (A13). With serpapi_enabled=false the rank shows "uncalibrated".
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock
from clefs.modules.visibility import vsignals as _vsignals  # noqa: F401  (registers vis.rank_drop)
from clefs.settings import get_settings

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = "places.id"          # IDs Only SKU: never widen this (see module docstring)
NOT_FOUND = 21                    # a 20-result page, so "not found" ranks just below it
TOP = 3                           # SoLV counts points ranked 1-3
BIAS_RADIUS_M = 1000.0
PAGE_SIZE = 20
CONCURRENCY = 8                   # 49 calls finish in a couple of seconds, inside the tick budget
EARTH_M_PER_DEG = 111_320.0
DRIFT_SPEARMAN = Decimal("0.6")
DRIFT_TOP3 = Decimal("0.7")
MAPS_PURPOSE = "visibility.rank_calibration"

TEMPLATES = ("hotel {area}", "hotels near {landmark}", "{star} star hotel {area}", "فندق في {area}")


# ─── Keywords and grid ───


def keywords(profile: dict, hotel: Any) -> list[str]:
    """The owner's keywords, else the templates. A template whose input is
    missing (no area, no landmark, no star rating) is not generated."""
    if profile.get("rank_keywords"):
        return list(profile["rank_keywords"])[:5]
    area = (profile.get("area") or "").strip()
    landmark = ((profile.get("landmarks") or [None])[0] or "").strip()
    star = getattr(hotel, "star_rating", None)
    out = []
    for t in TEMPLATES:
        if "{area}" in t and not area:
            continue
        if "{landmark}" in t and not landmark:
            continue
        if "{star}" in t and not star:
            continue
        out.append(t.format(area=area, landmark=landmark, star=star))
    return out


def language_for(keyword: str) -> str:
    return "ar" if any("؀" <= ch <= "ۿ" for ch in keyword) else "en"


def grid_points(lat: float, lng: float, size: int = 7, spacing_m: int = 800) -> list[tuple[float, float]]:
    """size x size points, row by row from the north-west, centred on the
    hotel. Longitude degrees shrink with cos(latitude)."""
    half = (size - 1) / 2
    m_per_deg_lng = EARTH_M_PER_DEG * math.cos(math.radians(lat))
    pts = []
    for r in range(size):
        for c in range(size):
            dy = (half - r) * spacing_m
            dx = (c - half) * spacing_m
            pts.append((round(lat + dy / EARTH_M_PER_DEG, 6), round(lng + dx / m_per_deg_lng, 6)))
    return pts


def subsample(size: int = 7) -> list[int]:
    """The 3x3 calibration subsample: corners, edge midpoints and centre."""
    lines = sorted({0, (size - 1) // 2, size - 1})
    return [r * size + c for r in lines for c in lines]


# ─── Metrics ───


def _q(v: Decimal, places: str) -> Decimal:
    return v.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def metrics(ranks: Iterable[int | None]) -> dict:
    """ARP = mean(rank | found); ATRP = mean(rank, not found = 21); SoLV =
    share of points ranked <= 3. Golden: 30 found points summing to 150 with 9
    in the top 3, over 49 points: ARP 5.00, ATRP 549/49 = 11.20, SoLV 18.37%."""
    rs = [NOT_FOUND if (r is None or r > PAGE_SIZE) else int(r) for r in ranks]
    n = len(rs)
    found = [r for r in rs if r < NOT_FOUND]
    arp = _q(Decimal(sum(found)) / len(found), "0.01") if found else None
    atrp = _q(Decimal(sum(rs)) / n, "0.01") if n else None
    solv = _q(Decimal(sum(1 for r in rs if r <= TOP)) / n, "0.0001") if n else None
    return {"points": n, "found": len(found), "arp": arp, "atrp": atrp, "solv": solv}


# ─── Places (IDs only) ───


def places_request(keyword: str, lat: float, lng: float) -> tuple[dict, dict]:
    """(headers minus the key, body) of one grid call. Split out so the
    contract test can assert the exact mask and body without the network."""
    headers = {"X-Goog-FieldMask": FIELD_MASK, "Content-Type": "application/json"}
    body = {
        "textQuery": keyword,
        "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": BIAS_RADIUS_M}},
        "rankPreference": "RELEVANCE",
        "includedType": "lodging",
        "strictTypeFiltering": True,
        "pageSize": PAGE_SIZE,
        "regionCode": "AE",
        "languageCode": language_for(keyword),
    }
    return headers, body


def places_ids(keyword: str, lat: float, lng: float, api_key: str) -> list[str]:
    """Place ids in Google's order at one point (at most 20)."""
    from clefs.modules.visibility import http

    headers, body = places_request(keyword, lat, lng)
    data = http.request_json("places", "POST", PLACES_URL, json=body, headers={**headers, "X-Goog-Api-Key": api_key})
    return [p["id"] for p in (data.get("places") or []) if isinstance(p, dict) and p.get("id")]


def _rank_of(ids: list[str], place_id: str | None) -> int | None:
    if not place_id:
        return None
    try:
        return ids.index(place_id) + 1
    except ValueError:
        return None


# ─── One keyword ───


def run_keyword(cur: Any, hotel: Any, profile: dict, keyword: str) -> dict:
    """Run the grid for one keyword and store the run and its points. The 49
    calls run concurrently (read-only HTTP; the cursor is used only after)."""
    from clefs.db import Json
    from clefs.modules.visibility import store
    from clefs.modules.visibility.http import VendorError

    api_key = get_settings().GOOGLE_MAPS_API_KEY
    if not api_key:
        return {"skipped": "not_configured"}
    if not (profile.get("place_id") and hotel.lat is not None and hotel.lng is not None):
        return {"skipped": "not_set_up"}
    pts = grid_points(float(hotel.lat), float(hotel.lng), int(profile["grid_size"]), int(profile["grid_spacing_m"]))
    comps = [c for c in store.competitors(cur, hotel.slug) if c.get("place_id")]

    def one(pt: tuple[float, float]) -> list[str] | None:
        try:
            return places_ids(keyword, pt[0], pt[1], api_key)
        except VendorError:
            return None

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        results = list(pool.map(one, pts))
    ok = [(i, pt, ids) for i, (pt, ids) in enumerate(zip(pts, results, strict=True)) if ids is not None]
    if not ok:
        return {"skipped": "all_calls_failed", "calls": len(pts)}
    self_ranks = [_rank_of(ids, profile["place_id"]) for _, _, ids in ok]
    comp_ranks = {c["comp_id"]: [_rank_of(ids, c["place_id"]) for _, _, ids in ok] for c in comps}
    m = metrics(self_ranks)
    comp_metrics = {cid: {k: (str(v) if isinstance(v, Decimal) else v) for k, v in metrics(rs).items()}
                    for cid, rs in comp_ranks.items()}
    cur.execute(
        """
        INSERT INTO vis_rank_runs (hotel_slug, run_at, keyword, source, points, found, arp, atrp, solv, comp_metrics,
                                   calls)
        VALUES (%s, %s, %s, 'places_ids', %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (hotel.slug, clock.now(), keyword, m["points"], m["found"], m["arp"], m["atrp"], m["solv"],
         Json(comp_metrics), len(pts)),
    )
    run_id = cur.fetchone()["id"]
    for j, (i, pt, _ids) in enumerate(ok):
        cur.execute(
            "INSERT INTO vis_rank_points (hotel_slug, run_id, idx, lat, lng, rank, comp_ranks) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (hotel.slug, run_id, i, pt[0], pt[1], self_ranks[j] or NOT_FOUND,
             Json({cid: (rs[j] or NOT_FOUND) for cid, rs in comp_ranks.items()})),
        )
    return {"run_id": run_id, "keyword": keyword, "calls": len(pts), "failed": len(pts) - len(ok),
            **{k: (str(v) if isinstance(v, Decimal) else v) for k, v in m.items()}}


# ─── Rank drop (vis.rank_drop: SoLV -15 pts for 2 weeks) ───

DROP_PTS = Decimal("0.15")


def check_drop(cur: Any, hotel: Any, keyword: str) -> bool:
    """Newest runs r0, r1, r2: a drop is r0 and r1 BOTH at least 15 points
    of SoLV below r2, so one noisy week never raises it."""
    from clefs.core import signals
    from clefs.core.signals import Evidence, RecommendedAction, SignalIn

    cur.execute("SELECT run_at, solv FROM vis_rank_runs WHERE hotel_slug = %s AND keyword = %s "
                "AND source = 'places_ids' AND solv IS NOT NULL ORDER BY run_at DESC, id DESC LIMIT 3",
                (hotel.slug, keyword))
    runs = cur.fetchall()
    key = f"vis.rank_drop:{keyword}"
    if len(runs) < 3:
        return False
    base = Decimal(runs[2]["solv"])
    dropped = all(base - Decimal(r["solv"]) >= DROP_PTS for r in runs[:2])
    if not dropped:
        signals.resolve_signal(cur, hotel.slug, key, resolution="recovered")
        return False
    pts = [f"{Decimal(r['solv']) * 100:.1f}%" for r in reversed(runs)]
    signals.upsert_signal(cur, hotel.slug, SignalIn(
        module="visibility", kind="vis.rank_drop", dedupe_key=key,
        title=f"Map visibility for \"{keyword}\" fell for two weeks",
        summary=f"Share of local voice {pts[0]} -> {pts[1]} -> {pts[2]} (points in the top 3 of the map grid).",
        severity=2, confidence=0.7,
        evidence=[Evidence(kind="series", label="SoLV by week", value=pts)],
        recommended_action=RecommendedAction(type="core.task", label="Review the Google Business Profile "
                                             "(categories, photos, recent reviews)", owner_role="gm"),
    ))
    return True


# ─── Monthly calibration against SerpApi google_maps ───


def spearman(a: list[str], b: list[str]) -> Decimal | None:
    """Spearman rho over the ids both orderings contain (re-ranked 1..n
    within that intersection); None below 3 shared ids."""
    common = [x for x in a if x in set(b)]
    n = len(common)
    if n < 3:
        return None
    rank_b = {x: i for i, x in enumerate([x for x in b if x in set(common)], start=1)}
    d2 = sum((i - rank_b[x]) ** 2 for i, x in enumerate(common, start=1))
    return Decimal(1) - Decimal(6 * d2) / Decimal(n * (n * n - 1))


def top3_agreement(a: list[str], b: list[str]) -> Decimal:
    return Decimal(len(set(a[:TOP]) & set(b[:TOP]))) / TOP


def maps_ids(hotel: Any, keyword: str, lat: float, lng: float) -> list[str]:
    from clefs.core import serpapi

    data = serpapi.search("google_maps", {"type": "search", "q": keyword, "ll": f"@{lat},{lng},14z", "gl": "ae",
                                          "hl": language_for(keyword)},
                          hotel_slug=hotel.slug, purpose=MAPS_PURPOSE)
    return [r["place_id"] for r in (data.get("local_results") or []) if isinstance(r, dict) and r.get("place_id")]


def calibrate(cur: Any, hotel: Any, only: list[str] | None = None) -> dict:
    """visibility.rank_calibrate_monthly: per keyword, the mean rho and top-3
    agreement over the 3x3 subsample; drift when rho < 0.6 or top-3 < 0.7."""
    from clefs.core import serpapi
    from clefs.core.errors import NotConfigured
    from clefs.modules.visibility import store
    from clefs.modules.visibility.http import VendorError

    profile = store.get_profile(cur, hotel.slug)
    if not profile["serpapi_enabled"]:
        return {"skipped": "serpapi_disabled"}
    api_key = get_settings().GOOGLE_MAPS_API_KEY
    if not api_key or hotel.lat is None or hotel.lng is None:
        return {"skipped": "not_set_up"}
    if store.budget_state(cur, hotel.slug, profile=profile)["exhausted"]:
        return {"skipped": "budget_exhausted"}
    size = int(profile["grid_size"])
    pts = grid_points(float(hotel.lat), float(hotel.lng), size, int(profile["grid_spacing_m"]))
    month = clock.now().date().replace(day=1)
    out = {}
    try:
        for kw in [k for k in keywords(profile, hotel) if only is None or k in only]:
            rhos, tops = [], []
            for idx in subsample(size):
                lat, lng = pts[idx]
                try:
                    a = places_ids(kw, lat, lng, api_key)
                    b = maps_ids(hotel, kw, lat, lng)
                except (VendorError, serpapi.SerpApiError):
                    continue
                rho = spearman(a, b)
                if rho is not None:
                    rhos.append(rho)
                if a and b:
                    tops.append(top3_agreement(a, b))
            if not rhos and not tops:
                continue
            rho = _q(sum(rhos) / len(rhos), "0.001") if rhos else None
            top = _q(sum(tops) / len(tops), "0.001") if tops else None
            drift = (rho is not None and rho < DRIFT_SPEARMAN) or (top is not None and top < DRIFT_TOP3)
            status = "drift" if drift else "ok"
            cur.execute(
                """
                INSERT INTO vis_rank_calibration (hotel_slug, month, keyword, spearman, top3_agreement, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (hotel_slug, month, keyword) DO UPDATE SET spearman = EXCLUDED.spearman,
                    top3_agreement = EXCLUDED.top3_agreement, status = EXCLUDED.status
                """,
                (hotel.slug, month, kw, rho, top, status),
            )
            out[kw] = status
    except (NotConfigured, serpapi.BudgetExhausted) as exc:
        out["stopped"] = type(exc).__name__
    return out


# ─── Reads ───

UNCALIBRATED = "uncalibrated"


def calibration_status(cur: Any, hotel_slug: str, keyword: str, *, serpapi_enabled: bool) -> dict:
    """The latest calibration for the keyword, or "uncalibrated" (never run,
    or SerpApi switched off for this hotel)."""
    if not serpapi_enabled:
        return {"status": UNCALIBRATED, "reason": "serpapi_disabled"}
    cur.execute("SELECT month, spearman, top3_agreement, status FROM vis_rank_calibration "
                "WHERE hotel_slug = %s AND keyword = %s ORDER BY month DESC LIMIT 1", (hotel_slug, keyword))
    r = cur.fetchone()
    if not r:
        return {"status": UNCALIBRATED, "reason": "not_run"}
    return {"status": r["status"], "month": r["month"].isoformat(),
            "spearman": None if r["spearman"] is None else str(r["spearman"]),
            "top3_agreement": None if r["top3_agreement"] is None else str(r["top3_agreement"]),
            "note": "ordering may differ from Maps" if r["status"] == "drift" else None}


def overview(cur: Any, hotel: Any, *, weeks: int = 12) -> dict:
    """GET /visibility/rank: one row per keyword with the latest metrics, a
    SoLV/ATRP history for the sparkline and the calibration status."""
    from clefs.modules.visibility import store

    profile = store.get_profile(cur, hotel.slug)
    kws = keywords(profile, hotel)
    cur.execute("SELECT DISTINCT keyword FROM vis_rank_runs WHERE hotel_slug = %s AND source = 'places_ids'",
                (hotel.slug,))
    kws += [r["keyword"] for r in cur.fetchall() if r["keyword"] not in kws]
    comps = {c["comp_id"]: c.get("name") for c in store.competitors(cur, hotel.slug)}
    rows = []
    for kw in kws:
        cur.execute("SELECT run_at, points, found, arp, atrp, solv, comp_metrics FROM vis_rank_runs "
                    "WHERE hotel_slug = %s AND keyword = %s AND source = 'places_ids' "
                    "ORDER BY run_at DESC, id DESC LIMIT %s", (hotel.slug, kw, weeks))
        runs = cur.fetchall()
        latest = runs[0] if runs else None
        rows.append({
            "keyword": kw,
            "latest": None if not latest else {
                "run_at": latest["run_at"].isoformat(), "points": latest["points"], "found": latest["found"],
                "arp": _s(latest["arp"]), "atrp": _s(latest["atrp"]), "solv": _s(latest["solv"]),
                "competitors": [{"comp_id": cid, "name": comps.get(cid), **m}
                                for cid, m in (latest["comp_metrics"] or {}).items()]},
            "history": [{"run_at": r["run_at"].isoformat(), "solv": _s(r["solv"]), "atrp": _s(r["atrp"])}
                        for r in reversed(runs)],
            "calibration": calibration_status(cur, hotel.slug, kw, serpapi_enabled=profile["serpapi_enabled"]),
        })
    setup = []
    if not profile.get("place_id"):
        setup.append("place_id")
    if not profile.get("area"):
        setup.append("area")
    if hotel.lat is None or hotel.lng is None:
        setup.append("hotel_location")
    if not get_settings().GOOGLE_MAPS_API_KEY:
        setup.append("places_api_key")
    return {"keywords": rows, "setup_missing": setup, "grid": {"size": profile["grid_size"],
            "spacing_m": profile["grid_spacing_m"]}, "label": "Internal KPI from the Places API (IDs only)"}


def _s(v: Any) -> str | None:
    return None if v is None else str(v)
