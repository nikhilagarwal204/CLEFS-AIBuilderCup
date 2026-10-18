"""
Source markets and holidays (02 spec 4.1): where a hotel's guests come from,
and the holidays of those markets. A market is an ISO2 country ("SA") or a
region code ("GCC"); MARKET_REGIONS expands a region to its countries so a
Saudi school break counts for a hotel that only said "GCC 16%".

Until the owner sets shares, the DET Jan-Aug 2026 mix for Dubai applies
(research section 3; not persisted, so a later DET update reaches every
hotel that never customised it).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from clefs import db as clefs_db
from clefs.core.errors import ClefsError

DEFAULT_SHARES: dict[str, Decimal] = {
    "WEU": Decimal("20"), "SAS": Decimal("17"), "GCC": Decimal("16"), "CIS": Decimal("14"),
    "MENA": Decimal("10"), "NESEA": Decimal("9"),
}
DEFAULT_SOURCE = "https://www.bwtravel.com/industry-insights/dubai-tourism-records-sustained-growth-through-august-2026-12531735"
MARKET_REGIONS: dict[str, tuple[str, ...]] = {
    "WEU": ("GB", "DE", "FR", "IT", "ES", "NL", "BE", "CH", "AT", "IE", "PT", "SE", "NO", "DK", "FI"),
    "SAS": ("IN", "PK", "BD", "LK", "NP"),
    "GCC": ("SA", "KW", "QA", "BH", "OM", "AE"),
    "CIS": ("RU", "KZ", "UZ", "AZ", "AM", "GE", "UA", "BY", "PL", "RO", "CZ", "HU"),
    "MENA": ("EG", "JO", "LB", "IQ", "MA", "TN", "DZ", "IR", "SY"),
    "NESEA": ("CN", "JP", "KR", "HK", "TW", "SG", "MY", "TH", "PH", "ID", "VN"),
    "AMER": ("US", "CA", "BR", "MX", "AR"),
    "AFR": ("NG", "ZA", "KE", "ET", "GH"),
    "OCE": ("AU", "NZ"),
}
REGION_LABELS = {"WEU": "Western Europe", "SAS": "South Asia", "GCC": "GCC", "CIS": "CIS and Eastern Europe",
                 "MENA": "Middle East and North Africa", "NESEA": "North-East and South-East Asia",
                 "AMER": "Americas", "AFR": "Africa", "OCE": "Australasia"}


def countries_of(market_code: str) -> tuple[str, ...]:
    code = market_code.upper()
    return MARKET_REGIONS.get(code, (code,))


def _valid(code: str) -> bool:
    c = code.upper()
    return c in MARKET_REGIONS or (len(c) == 2 and c.isalpha())


# ─── Shares ───


def get_shares(cur: Any, hotel_slug: str) -> dict:
    rows = clefs_db.fetch_all(
        "SELECT market_code, share_pct, source, updated_at FROM dr_source_markets WHERE hotel_slug = %s "
        "ORDER BY share_pct DESC, market_code", (hotel_slug,), cur=cur)
    if not rows:
        return {"is_default": True, "source_url": DEFAULT_SOURCE, "markets": [
            {"market_code": k, "label": REGION_LABELS.get(k, k), "share_pct": str(v), "countries": list(countries_of(k))}
            for k, v in DEFAULT_SHARES.items()]}
    return {"is_default": False, "source_url": None, "markets": [
        {"market_code": r["market_code"], "label": REGION_LABELS.get(r["market_code"], r["market_code"]),
         "share_pct": str(r["share_pct"]), "countries": list(countries_of(r["market_code"])), "source": r["source"]}
        for r in rows]}


def set_shares(cur: Any, hotel_slug: str, markets: list[dict], *, user_id: int | None) -> dict:
    """Replace the hotel's shares. Shares may sum to under 100 (the rest is
    "other"), never over."""
    clean: dict[str, Decimal] = {}
    for m in markets:
        code = str(m.get("market_code") or "").strip().upper()
        if not _valid(code):
            raise ClefsError(400, f"Unknown market {code!r}: use an ISO country code or a region", "invalid_market")
        share = Decimal(str(m.get("share_pct")))
        if share < 0 or share > 100:
            raise ClefsError(400, "A share is a percentage between 0 and 100", "invalid_share")
        clean[code] = clean.get(code, Decimal(0)) + share
    if sum(clean.values()) > 100:
        raise ClefsError(400, "Market shares add up to more than 100%", "invalid_share")
    clefs_db.execute("DELETE FROM dr_source_markets WHERE hotel_slug = %s", (hotel_slug,), cur=cur)
    for code, share in clean.items():
        clefs_db.execute(
            "INSERT INTO dr_source_markets (hotel_slug, market_code, share_pct, source, updated_by) "
            "VALUES (%s, %s, %s, 'owner', %s)", (hotel_slug, code, share, user_id), cur=cur)
    return get_shares(cur, hotel_slug)


# ─── Holidays ───


def list_holidays(cur: Any, *, start: date, end: date, countries: list[str] | None = None) -> list[dict]:
    rows = clefs_db.fetch_all(
        "SELECT * FROM dr_holidays WHERE start_date <= %s AND end_date >= %s AND (%s OR country_code = ANY(%s)) "
        "ORDER BY start_date, country_code", (end, start, not countries, countries or []), cur=cur)
    return [{"country_code": r["country_code"], "name": r["name"], "start_date": r["start_date"].isoformat(),
             "end_date": r["end_date"].isoformat(), "kind": r["kind"], "moon_dependent": r["moon_dependent"],
             "uncertainty_days": r["uncertainty_days"], "verification": r["verification"],
             "source_url": r["source_url"]} for r in rows]


def market_holidays(cur: Any, hotel_slug: str, *, start: date, end: date) -> list[dict]:
    """Holidays of the hotel's source markets, each with the share of the
    market it belongs to (the relevance model's source-market input)."""
    shares = get_shares(cur, hotel_slug)["markets"]
    by_country: dict[str, tuple[str, Decimal]] = {}
    for m in shares:
        for c in m["countries"]:
            by_country.setdefault(c, (m["market_code"], Decimal(m["share_pct"])))
    out = []
    for h in list_holidays(cur, start=start, end=end, countries=list(by_country)):
        market, share = by_country[h["country_code"]]
        out.append({**h, "market_code": market, "market_share_pct": str(share)})
    return out
