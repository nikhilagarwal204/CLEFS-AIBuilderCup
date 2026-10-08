"""
Site health (04 spec 5.6, visibility.site_health_weekly, Sunday 03:00).

Hygiene only: no ranking claim is made, because the effect of site speed on
Google ranking is UNVERIFIED. Three reads:
  - CrUX (real Chrome users on phones, p75) for the site's origin; a 404 is
    "insufficient data", not an error.
  - PSI (a lab run of the homepage, mobile, performance category).
  - JSON-LD on the homepage and configured room pages, fetched as
    ClefsBot/1.0 while honouring robots.txt, including @graph documents.
A metric above Google's "good" p75 threshold (LCP 2.5 s, INP 200 ms, CLS
0.1) is reported as "outside Google's 'good' threshold", which covers both
the "needs improvement" and the "poor" bands.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from clefs.core import clock
from clefs.modules.visibility import facts
from clefs.settings import get_settings

CRUX_URL = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
BOT = "ClefsBot"
MAX_PAGES = 5

# metric key -> (label, CrUX name, "good" p75 ceiling)
THRESHOLDS = {
    "lcp_ms": ("LCP", "largest_contentful_paint", Decimal(2500)),
    "inp_ms": ("INP", "interaction_to_next_paint", Decimal(200)),
    "cls": ("CLS", "cumulative_layout_shift", Decimal("0.1")),
}
OUTSIDE = "outside Google's 'good' threshold"

HOTEL_TYPES = frozenset({"Hotel", "LodgingBusiness", "Resort", "Motel", "Hostel", "BedAndBreakfast", "HotelRoom",
                         "Suite"})
REQUIRED = ("name", "address")                                  # plus makesOffer or offers
ROOM_RECOMMENDED = ("bed", "occupancy")
USEFUL = ("checkinTime", "checkoutTime", "starRating", "numberOfRooms", "amenityFeature", "petsAllowed",
          "priceRange", "geo")


# ─── CrUX and PSI ───


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme or "https", p.netloc, "", "", ""))


def parse_crux(body: dict | None) -> dict:
    """{status: ok|insufficient_data, metrics: {lcp_ms, inp_ms, cls}, outside_good: [...]}."""
    if body is None:
        return {"status": "insufficient_data", "metrics": {}, "outside_good": []}
    m = (body.get("record") or {}).get("metrics") or {}
    metrics: dict[str, str | None] = {}
    outside = []
    for key, (label, name, ceiling) in THRESHOLDS.items():
        p75 = ((m.get(name) or {}).get("percentiles") or {}).get("p75")
        if p75 is None:
            metrics[key] = None
            continue
        v = Decimal(str(p75))
        metrics[key] = str(v)
        if v > ceiling:
            outside.append({"metric": key, "label": label, "value": str(v), "threshold": str(ceiling),
                            "note": OUTSIDE})
    return {"status": "ok", "metrics": metrics, "outside_good": outside}


def fetch_crux(origin: str, api_key: str) -> dict:
    from clefs.modules.visibility import http

    body = {"origin": origin, "formFactor": "PHONE",
            "metrics": [v[1] for v in THRESHOLDS.values()]}
    try:
        return parse_crux(http.request_json("crux", "POST", CRUX_URL, params={"key": api_key}, json=body))
    except http.VendorError as exc:
        if exc.http_status == 404:  # CrUX has too few real-user samples for this origin
            return parse_crux(None)
        raise


def parse_psi(body: dict) -> dict:
    lh = body.get("lighthouseResult") or {}
    audits = lh.get("audits") or {}
    score = ((lh.get("categories") or {}).get("performance") or {}).get("score")

    def num(audit: str) -> str | None:
        v = (audits.get(audit) or {}).get("numericValue")
        return None if v is None else str(round(Decimal(str(v)), 3))

    return {"score": None if score is None else str(score), "lcp_ms": num("largest-contentful-paint"),
            "cls": num("cumulative-layout-shift"), "tbt_ms": num("total-blocking-time"),
            "fetched_at": lh.get("fetchTime")}


def fetch_psi(url: str, api_key: str) -> dict:
    from clefs.modules.visibility import http

    return parse_psi(http.request_json("psi", "GET", PSI_URL, params={
        "url": url, "strategy": "mobile", "category": "performance", "key": api_key}))


# ─── JSON-LD ───

_LD_RE = re.compile(r"<script\b[^>]*type\s*=\s*[\"']?application/ld\+json[\"']?[^>]*>(.*?)</script\s*>",
                    re.IGNORECASE | re.DOTALL)


def _types(node: dict) -> list[str]:
    t = node.get("@type")
    vals = t if isinstance(t, list) else [t]
    return [str(v).rsplit("/", 1)[-1] for v in vals if v]


def extract_nodes(page_html: str) -> tuple[list[dict], int]:
    """(every JSON-LD object on the page with @graph flattened, parse errors)."""
    nodes: list[dict] = []
    errors = 0

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for o in obj:
                walk(o)
        elif isinstance(obj, dict):
            if isinstance(obj.get("@graph"), list):
                walk(obj["@graph"])
            if obj.get("@type"):
                nodes.append(obj)
            for key in ("containsPlace", "makesOffer", "offers"):
                sub = obj.get(key)
                for s in sub if isinstance(sub, list) else [sub]:
                    if isinstance(s, dict) and s.get("@type") and any(t in HOTEL_TYPES for t in _types(s)):
                        walk(s)

    for raw in _LD_RE.findall(page_html or ""):
        try:
            walk(json.loads(html.unescape(raw.strip())))
        except (ValueError, TypeError):
            errors += 1
    return nodes, errors


def check_nodes(nodes: list[dict], url: str) -> tuple[list[str], dict, list[dict]]:
    """(types found, useful fields present on the hotel node, issues)."""
    types = sorted({t for n in nodes for t in _types(n)})
    hotels = [n for n in nodes if any(t in HOTEL_TYPES and t not in ("HotelRoom", "Suite") for t in _types(n))]
    rooms = [n for n in nodes if any(t in ("HotelRoom", "Suite") for t in _types(n))]
    issues: list[dict] = []
    fields: dict[str, Any] = {}
    if not hotels:
        issues.append({"severity": "required", "type": "Hotel", "field": "@type", "url": url,
                       "message": "No Hotel (or LodgingBusiness) structured data on this page"})
    for n in hotels[:1]:
        for f in REQUIRED:
            if not n.get(f):
                issues.append({"severity": "required", "type": "Hotel", "field": f, "url": url,
                               "message": f"Hotel is missing {f} (needed for Google hotel price structured data)"})
        if not (n.get("makesOffer") or n.get("offers")):
            issues.append({"severity": "required", "type": "Hotel", "field": "makesOffer", "url": url,
                           "message": "Hotel has no makesOffer/offers (needed for price accuracy validation)"})
        for f in USEFUL:
            if n.get(f) not in (None, "", []):
                fields[f] = n[f]
            else:
                issues.append({"severity": "useful", "type": "Hotel", "field": f, "url": url,
                               "message": f"schema.org {f} is not set (useful, not a Google requirement)"})
    for r in rooms[:10]:
        for f in ROOM_RECOMMENDED:
            if not r.get(f):
                issues.append({"severity": "recommended", "type": "HotelRoom", "field": f, "url": url,
                               "message": f"HotelRoom {r.get('name') or ''} is missing {f}".replace("  ", " ")})
    return types, fields, issues


def _ld_time(v: Any) -> str | None:
    """schema.org Time ("14:00", "14:00:00", "T14:00", "2pm")."""
    if not v:
        return None
    s = str(v).strip().upper().lstrip("T")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(AM|PM)?", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "PM" and h < 12:
        h += 12
    if m.group(3) == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def _amenity(fields: dict, pattern: str) -> str | None:
    feats = fields.get("amenityFeature")
    for f in feats if isinstance(feats, list) else [feats]:
        if isinstance(f, dict) and re.search(pattern, str(f.get("name") or ""), re.IGNORECASE):
            return facts.as_bool(f.get("value", True))
    return None


def site_facts(fields: dict) -> dict[str, str | None]:
    """The website's claims in the KB's vocabulary."""
    star = fields.get("starRating")
    star_v = star.get("ratingValue") if isinstance(star, dict) else star
    pets = fields.get("petsAllowed")
    return {
        "checkin_time": _ld_time(fields.get("checkinTime")),
        "checkout_time": _ld_time(fields.get("checkoutTime")),
        "star_rating": None if star_v in (None, "") else str(int(Decimal(str(star_v)))),
        "pets": facts.as_bool(pets) if pets is not None else None,
        "pool": _amenity(fields, r"pool"),
        "wifi": _amenity(fields, r"wi-?fi|internet"),
        "breakfast": _amenity(fields, r"breakfast"),
    }


def compare_kb(truth: dict[str, str], claims: dict[str, str | None], url: str) -> list[dict]:
    """[{fact_key, ours, theirs, status: match|mismatch|missing, source_url}]
    for every fact both could state."""
    out = []
    for key, theirs in claims.items():
        ours = truth.get(key)
        if ours is None:
            continue
        if theirs is None:
            status = "missing"
        else:
            status = "mismatch" if facts.differs(key, ours, theirs) else "match"
        out.append({"fact_key": key, "ours": ours, "theirs": theirs, "status": status, "source_url": url})
    return out


# ─── Fetching pages (robots.txt honoured) ───


def robots_allows(url: str, cache: dict[str, RobotFileParser | None]) -> bool:
    """robots.txt read once per origin. An unreachable or missing robots.txt
    allows (the web's convention); a Disallow for ClefsBot or * is obeyed."""
    from clefs.modules.visibility import http

    origin = origin_of(url)
    if origin not in cache:
        rp: RobotFileParser | None = None
        try:
            status, text = http.fetch_text(origin + "/robots.txt", max_bytes=200_000)
            if status == 200:
                rp = RobotFileParser()
                rp.parse(text.splitlines())
        except http.VendorError:
            rp = None
        cache[origin] = rp
    rp = cache[origin]
    return True if rp is None else rp.can_fetch(BOT, url)


def read_jsonld(urls: list[str]) -> dict:
    """{pages: [{url, status, found, types, errors}], types, fields, issues}
    across the pages; the first hotel node's fields win (the homepage)."""
    from clefs.modules.visibility import http

    robots: dict[str, RobotFileParser | None] = {}
    pages, types, issues = [], set(), []
    fields: dict[str, Any] = {}
    for url in urls[:MAX_PAGES]:
        if not robots_allows(url, robots):
            pages.append({"url": url, "status": "robots_disallowed", "found": False})
            continue
        try:
            status, text = http.fetch_text(url)
        except http.VendorError as exc:
            pages.append({"url": url, "status": f"error:{exc.detail}", "found": False})
            continue
        if status >= 400:
            pages.append({"url": url, "status": f"http_{status}", "found": False})
            continue
        nodes, errors = extract_nodes(text)
        t, f, iss = check_nodes(nodes, url)
        types.update(t)
        for k, v in f.items():
            fields.setdefault(k, v)
        issues += iss
        if errors:
            issues.append({"severity": "required", "type": "JSON-LD", "field": "syntax", "url": url,
                           "message": f"{errors} JSON-LD block(s) could not be parsed"})
        pages.append({"url": url, "status": "ok", "found": bool(nodes), "types": t})
    return {"pages": pages, "types": sorted(types), "fields": fields, "issues": issues,
            "found": any(p.get("found") for p in pages)}


# ─── Weekly run ───


def run_weekly(cur: Any, hotel: Any) -> dict:
    from clefs.core import actions
    from clefs.db import Json
    from clefs.modules.visibility import store, vsignals
    from clefs.modules.visibility.http import VendorError

    profile = store.get_profile(cur, hotel.slug)
    url = profile.get("website_url")
    if not url:
        return {"skipped": "no_website"}
    key = get_settings().GOOGLE_API_KEY
    crux = psi = None
    if key:
        try:
            crux = fetch_crux(origin_of(url), key)
        except VendorError as exc:
            crux = {"status": "error", "error": exc.detail, "metrics": {}, "outside_good": []}
        try:
            psi = fetch_psi(url, key)
        except VendorError as exc:
            psi = {"status": "error", "error": exc.detail}
    ld = read_jsonld([url, *(profile.get("room_page_urls") or [])])
    truth = facts.kb_truth(cur, hotel.slug)
    vs_kb = compare_kb(truth, site_facts(ld["fields"]), url)
    now = clock.now()
    cur.execute(
        """
        INSERT INTO vis_site_health (hotel_slug, checked_at, url, crux, psi, jsonld_found, jsonld_types, jsonld_fields,
                                     jsonld_issues, facts_vs_kb)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (hotel.slug, now, url, Json(crux) if crux else None, Json(psi) if psi else None, ld["found"], ld["types"],
         Json({"pages": ld["pages"], "fields": ld["fields"]}), Json(ld["issues"]), Json(vs_kb)),
    )
    check_id = cur.fetchone()["id"]
    if crux is not None and crux.get("status") == "ok":
        vsignals.site_health_not_good(cur, hotel.slug, url, crux["outside_good"])
    mismatches = [m for m in vs_kb if m["status"] == "mismatch"]
    sig = vsignals.jsonld_mismatch(cur, hotel.slug, url, mismatches)
    action_id = None
    if mismatches:
        keys = sorted(m["fact_key"] for m in mismatches)
        act = actions.create_action(
            cur, hotel.slug, type="visibility.site_fact_fix", title="Correct the hotel facts on your website",
            params={"url": url, "facts": keys, "mismatches": mismatches}, signal_id=(sig or {}).get("id"),
            dedupe_key=f"visibility.site_fact_fix:{','.join(keys)}", owner_role="gm")
        action_id = act.get("id")
    return {"check_id": check_id, "crux": (crux or {}).get("status", "not_connected"), "jsonld_found": ld["found"],
            "issues": len(ld["issues"]), "mismatches": len(mismatches), "action_id": action_id}


# ─── Reads ───


def latest_check(cur: Any, hotel_slug: str, *, since: datetime | None = None) -> dict | None:
    cur.execute("SELECT * FROM vis_site_health WHERE hotel_slug = %s AND (%s::timestamptz IS NULL OR checked_at > %s) "
                "ORDER BY checked_at DESC, id DESC LIMIT 1", (hotel_slug, since, since))
    r = cur.fetchone()
    return dict(r) if r else None


def open_fact_errors(cur: Any, hotel_slug: str) -> list[dict]:
    r = latest_check(cur, hotel_slug)
    if not r:
        return []
    return [{"fact_key": m["fact_key"], "ours": m["ours"], "theirs": m["theirs"],
             "source_url": m.get("source_url") or r["url"], "seen_at": r["checked_at"].isoformat(),
             "source": "website_jsonld"}
            for m in (r.get("facts_vs_kb") or []) if m.get("status") == "mismatch"]


def overview(cur: Any, hotel: Any) -> dict:
    from clefs.modules.visibility import store

    profile = store.get_profile(cur, hotel.slug)
    r = latest_check(cur, hotel.slug)
    setup = [] if profile.get("website_url") else ["website_url"]
    if not get_settings().GOOGLE_API_KEY:
        setup.append("google_api_key")
    latest = None
    if r:
        latest = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items() if k != "hotel_slug"}
    return {"latest": latest, "setup_missing": setup, "thresholds": {k: str(v[2]) for k, v in THRESHOLDS.items()},
            "note": "Site hygiene only: no ranking effect is claimed."}
