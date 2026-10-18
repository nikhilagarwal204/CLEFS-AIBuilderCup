"""
Event dedup and field precedence (02 spec 4.2, "Dedup"). Pure functions: no
database, so every rule here is tested on plain dicts.

canonical_key = norm(title without year, edition or "the") + venue + ISO week.
Two events merge when they share a venue (or sit within 1 km), their dates
overlap by at least one day, and their titles match (token-set ratio >= 0.8,
or an acronym match such as GITEX or ATM). An event with no venue and no
coordinates (a holiday, a city-wide festival, an AI hit that named no venue)
matches on title and dates alone: an unknown venue is a wildcard, not a
mismatch, or every AI discovery would duplicate the curated row.

Field precedence for dates is CURATED > PREDICTHQ > MANUAL > ICS >
TICKETMASTER > AI_WEB. Attendance takes the largest credible value and keeps
its provenance.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date
from typing import Any, Iterable

from rapidfuzz import fuzz

# ─── Constants ───

SOURCES = ("CURATED", "PREDICTHQ", "MANUAL", "ICS", "TICKETMASTER", "AI_WEB")
# Lower rank wins. ICS sits with manual entry (the owner chose the file) but
# below it: a calendar export is often a colleague's copy of a listing.
SOURCE_RANK = {s: i for i, s in enumerate(SOURCES)}
HOTEL_SCOPED_SOURCES = frozenset({"MANUAL", "ICS", "AI_WEB"})
TITLE_MATCH = 0.8
VENUE_MATCH_KM = 1.0
AI_CONFIDENCE_CAP = 0.6

_STOP = {"the", "a", "an", "of", "and", "in", "at", "for", "de", "la", "le", "dubai", "uae"}
_EDITION = re.compile(
    r"\b(\d{1,3}(st|nd|rd|th)\s+(edition|annual)|edition|annual|(19|20)\d{2}(\s*[-/]\s*\d{2,4})?)\b"
)


# ─── Titles ───


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def norm_title(title: str) -> str:
    """Lowercase words without year, edition, "the" or punctuation. The city
    name goes too: "GITEX Global Dubai" and "GITEX Global" are one show."""
    t = _ascii(title or "").lower().replace("&", " and ")
    t = _EDITION.sub(" ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    words = [w for w in t.split() if w not in _STOP]
    return " ".join(words)


def _upper_tokens(title: str) -> set[str]:
    """Acronym-looking tokens as written: GITEX, ATM, WHX, ADIPEC."""
    return {w.lower() for w in re.findall(r"[A-Za-z0-9]+", _ascii(title or ""))
            if len(w) >= 3 and w.isupper() and not w.isdigit()}


_ARTICLES = {"the", "a", "an", "of", "and", "in", "at", "for"}


def _initials(title: str) -> str:
    # City words stay here (unlike norm_title): DSF is Dubai Shopping Festival.
    t = _EDITION.sub(" ", _ascii(title or "").lower())
    words = [w for w in re.findall(r"[a-z]+", t) if w not in _ARTICLES]
    return "".join(w[0] for w in words) if len(words) >= 2 else ""


def acronym_match(a: str, b: str) -> bool:
    ua, ub = _upper_tokens(a), _upper_tokens(b)
    if ua & ub:
        return True
    ia, ib = _initials(a), _initials(b)
    return bool((len(ia) >= 3 and ia in ub) or (len(ib) >= 3 and ib in ua))


def title_score(a: str, b: str) -> float:
    """0..1. Token-set ratio of the normalised titles; an acronym match
    counts as exactly the threshold so it merges but never outranks a real
    title match."""
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    score = fuzz.token_set_ratio(na, nb) / 100.0
    if score >= TITLE_MATCH:
        # token_set_ratio is 1.0 for any subset ("gitex global" inside
        # "gitex global summit"); the plain ratio breaks such ties.
        return 0.8 + 0.2 * (fuzz.ratio(na, nb) / 100.0)
    return TITLE_MATCH if acronym_match(a, b) else score


def canonical_key(title: str, venue_codes: Iterable[str] | None, start: date) -> str:
    venue = sorted(venue_codes or [])
    iso = start.isocalendar()
    return f"{norm_title(title).replace(' ', '-') or 'untitled'}|{venue[0] if venue else '-'}|{iso[0]}-W{iso[1]:02d}"


# ─── Venues and distance ───


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def resolve_venue(text: str | None, venues: list[dict]) -> str | None:
    """Venue code for free text ("Expo City Dubai, Hall 3"), by alias. The
    longest alias wins so "Dubai World Central" never reads as "Dubai"."""
    t = _ascii(text or "").lower()
    if not t:
        return None
    best: tuple[int, str] | None = None
    for v in venues:
        for alias in [v["name"], *(v.get("aliases") or [])]:
            a = _ascii(alias).lower()
            if a and re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", t) and (best is None or len(a) > best[0]):
                best = (len(a), v["code"])
    return best[1] if best else None


def nearest_venue(lat: float | None, lon: float | None, venues: list[dict], *, max_km: float = 1.5) -> str | None:
    """A source that gives only coordinates (PredictHQ) gets the venue it sits
    on. 1.5 km, not 1: the curated venue points are approximate."""
    if lat is None or lon is None:
        return None
    best: tuple[float, str] | None = None
    for v in venues:
        if v.get("lat") is None or v.get("lon") is None:
            continue
        d = haversine_km(float(lat), float(lon), float(v["lat"]), float(v["lon"]))
        if d <= max_km and (best is None or d < best[0]):
            best = (d, v["code"])
    return best[1] if best else None


# ─── Matching ───


def overlap_days(a_start: date, a_end: date, b_start: date, b_end: date) -> int:
    return max(0, (min(a_end, b_end) - max(a_start, b_start)).days + 1)


def _has_place(e: dict) -> bool:
    return bool(e.get("venue_codes")) or (e.get("lat") is not None and e.get("lon") is not None)


def venue_match(a: dict, b: dict) -> bool:
    """Shared venue code, or within 1 km. Unknown place on either side is a
    wildcard (see the module docstring)."""
    if not _has_place(a) or not _has_place(b):
        return True
    if set(a.get("venue_codes") or []) & set(b.get("venue_codes") or []):
        return True
    if a.get("venue_codes") and b.get("venue_codes"):
        return False
    if None not in (a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon")):
        return haversine_km(float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"])) <= VENUE_MATCH_KM
    return False


def match_score(cand: dict, event: dict) -> float:
    """0 when the two cannot be one event, else a score for choosing among
    several matches (title first, then how much the dates overlap)."""
    ov = overlap_days(cand["start"], cand["end"], event["start_date"], event["end_date"])
    if ov < 1 or not venue_match(cand, event):
        return 0.0
    ts = title_score(cand["title"], event["title"])
    if ts < TITLE_MATCH:
        return 0.0
    span = max((cand["end"] - cand["start"]).days, (event["end_date"] - event["start_date"]).days) + 1
    return ts + 0.1 * (ov / span)


def best_match(cand: dict, events: list[dict]) -> dict | None:
    scored = [(match_score(cand, e), e) for e in events]
    scored = [(s, e) for s, e in scored if s > 0]
    return max(scored, key=lambda t: t[0])[1] if scored else None


# ─── Field precedence ───


def _ranked(sources: list[dict]) -> list[dict]:
    """Best source first; within one source the newest fetch wins."""
    by_time = sorted(sources, key=lambda s: str(s.get("fetched_at") or ""), reverse=True)
    return sorted(by_time, key=lambda s: SOURCE_RANK.get(s["source"], 99))


def _first(ranked: list[dict], key: str) -> tuple[Any, str | None]:
    for s in ranked:
        v = (s.get("fields") or {}).get(key)
        if v not in (None, "", []):
            return v, s["source"]
    return None, None


def source_confidence(source: str, fields: dict) -> float:
    """Default trust per source. AI discovery is capped at 0.6 whatever the
    model claims (02 spec 4.2); PredictHQ 'predicted' events and curated rows
    marked UNVERIFIED sit at 0.5."""
    if source == "AI_WEB":
        return min(float(fields.get("confidence") or 0.5), AI_CONFIDENCE_CAP)
    if fields.get("verification") == "UNVERIFIED":
        return 0.5
    return {"CURATED": 0.9, "PREDICTHQ": 0.85, "MANUAL": 0.9, "ICS": 0.8, "TICKETMASTER": 0.7}.get(source, 0.5)


def resolve_fields(sources: list[dict]) -> dict:
    """The event's merged fields from its (in-scope) source rows. Every
    source's `fields` uses the keys of the candidate dict in events.py."""
    ranked = _ranked(sources)
    if not ranked:
        raise ValueError("an event needs at least one source")
    best = ranked[0]
    bf = best.get("fields") or {}
    out: dict[str, Any] = {
        "title": bf.get("title"), "category": bf.get("category"),
        "start_date": bf["start"], "end_date": bf["end"], "status": bf.get("status") or "SCHEDULED",
        "date_source": best["source"],
    }
    out["venue_codes"], _ = _first(ranked, "venue_codes")
    out["venue_codes"] = list(out["venue_codes"] or [])
    for key in ("lat", "lon", "emirate", "description", "phq_rank", "phq_local_rank"):
        out[key], _ = _first(ranked, key)
    out["predicted_accom_spend_minor"], _ = _first(ranked, "spend_minor")
    out["predicted_accom_spend_currency"] = "USD" if out["predicted_accom_spend_minor"] is not None else None
    # Attendance: the largest credible figure. AI numbers count only when no
    # other source has one (a model can inflate a crowd by 10x).
    credible = [s for s in ranked if (s.get("fields") or {}).get("attendance") and s["source"] != "AI_WEB"] or \
               [s for s in ranked if (s.get("fields") or {}).get("attendance")]
    if credible:
        top = max(credible, key=lambda s: int(s["fields"]["attendance"]))
        out["attendance_est"], out["attendance_source"] = int(top["fields"]["attendance"]), top["source"]
    else:
        out["attendance_est"], out["attendance_source"] = None, None
    out["moon_dependent"] = any((s.get("fields") or {}).get("moon_dependent") for s in ranked)
    out["date_uncertainty_days"] = max(int((s.get("fields") or {}).get("uncertainty_days") or 0) for s in ranked)
    verified = [s for s in ranked if s["source"] != "AI_WEB" and (s.get("fields") or {}).get("verification") != "UNVERIFIED"]
    out["verification"] = "VERIFIED" if verified else "UNVERIFIED"
    confs = [float(s.get("confidence") if s.get("confidence") is not None
                   else source_confidence(s["source"], s.get("fields") or {})) for s in ranked]
    out["confidence"] = round(max(confs), 3)
    urls: list[str] = []
    for s in ranked:
        for u in (s.get("fields") or {}).get("urls") or []:
            if u and u not in urls:
                urls.append(u)
    out["source_urls"] = urls
    return out
