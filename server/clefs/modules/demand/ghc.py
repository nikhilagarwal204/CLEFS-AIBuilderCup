"""
Google Hotel Center checklist (02 spec 4.3; Canon K5 claims).

Google's third-party rates for Hotel Ads (and Performance Max for Travel
Goals) stop after 30 September 2026: a hotel advertising on Google needs
its own Hotel Center feed, directly or through a connectivity partner.
Free booking links are NOT affected by that change (claim
triptease_free_booking_links_unaffected), but they also need a feed, so the
same checklist gets a hotel onto them. Items: Hotel Center account, ARI
feed partner, free booking links, landing page, price accuracy.

dr.ghc.deadline (money_available) stays open while any item is TODO, and
resolves once every item is DONE or NA.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from clefs import db as clefs_db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.signals import Evidence, RecommendedAction
from clefs.core.time import local_date
from clefs.modules.demand import signals as sig

DEADLINE = date(2026, 9, 30)
CLAIM_IDS = ("google_third_party_rates_unavailable_2026_09_30", "triptease_free_booking_links_unaffected")
STATUSES = ("TODO", "DONE", "NA")
SOURCE_SUNSET = "https://support.google.com/hotelprices/answer/14310302"
SOURCE_FBL = "https://www.triptease.com/resources/google-is-ending-third-party-rates"

ITEMS = (
    {"code": "HOTEL_CENTER_ACCOUNT", "label": "Hotel Center account",
     "detail": "Your property has a Google Hotel Center account, yours or your connectivity partner's, and you "
               "can see its reports.", "source_url": SOURCE_SUNSET},
    {"code": "ARI_FEED_PARTNER", "label": "Prices and availability feed (ARI partner)",
     "detail": "A connectivity partner, or your own feed, sends rates and availability to Hotel Center. Google's "
               "third-party rates for Hotel Ads end after 30 Sep 2026, so ads need this feed.",
     "source_url": "https://developers.google.com/hotels/connectivity-partners"},
    {"code": "FREE_BOOKING_LINKS", "label": "Free booking links switched on",
     "detail": "Free booking links are not affected by the 30 Sep 2026 change; they need the feed above and are "
               "free per click.", "source_url": SOURCE_FBL},
    {"code": "LANDING_PAGE", "label": "Landing page shows the same price",
     "detail": "The official-site link lands on a booking page for the searched dates showing the listed price.",
     "source_url": "https://support.google.com/hotelprices/answer/10472393"},
    {"code": "PRICE_ACCURACY", "label": "Price accuracy",
     "detail": "Taxes and fees are shown as on your own site; Google tightened price-accuracy enforcement for "
               "ads and free booking links.", "source_url": "https://support.google.com/hotelprices/answer/6064419"},
)
CODES = tuple(i["code"] for i in ITEMS)


def checklist(cur: Any, hotel: Any) -> dict:
    rows = {r["item_code"]: r for r in clefs_db.fetch_all(
        "SELECT * FROM dr_ghc_checklist WHERE hotel_slug = %s", (hotel.slug,), cur=cur)}
    items = []
    for it in ITEMS:
        r = rows.get(it["code"])
        items.append({**it, "status": r["status"] if r else "TODO", "note": r["note"] if r else None,
                      "updated_at": r["updated_at"].isoformat() if r else None})
    today = local_date(hotel)
    open_n = sum(1 for i in items if i["status"] == "TODO")
    return {"deadline": DEADLINE.isoformat(), "days_left": (DEADLINE - today).days, "passed": today > DEADLINE,
            "open_items": open_n, "claim_ids": list(CLAIM_IDS), "items": items,
            "summary": ("Google third-party rates for hotel ads end after 30 Sep 2026; free booking links are not "
                        "affected by that change.")}


def update(cur: Any, hotel: Any, items: list[dict], *, user_id: int | None) -> dict:
    for it in items:
        code = str(it.get("item_code") or it.get("code") or "").upper()
        status = str(it.get("status") or "").upper()
        if code not in CODES:
            raise ClefsError(400, f"Unknown checklist item {code!r}", "invalid_item")
        if status not in STATUSES:
            raise ClefsError(400, "status is TODO, DONE or NA", "invalid_status")
        clefs_db.execute(
            """INSERT INTO dr_ghc_checklist (hotel_slug, item_code, status, note, updated_by, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (hotel_slug, item_code) DO UPDATE SET status = EXCLUDED.status,
                   note = EXCLUDED.note, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at""",
            (hotel.slug, code, status, (it.get("note") or None) and str(it["note"])[:500], user_id, clock.now()),
            cur=cur)
    check_deadline(cur, hotel)
    return checklist(cur, hotel)


def check_deadline(cur: Any, hotel: Any) -> dict:
    """Raise (or refresh) dr.ghc.deadline while items are open; resolve it
    when none is."""
    cl = checklist(cur, hotel)
    key = DEADLINE.isoformat()
    if cl["open_items"] == 0:
        return {"open": 0, "resolved": sig.resolve(cur, hotel.slug, kind=sig.GHC_DEADLINE, dedupe_key=key,
                                                   resolution="checklist_complete")}
    verb = "ended after" if cl["passed"] else "end after"
    sig.raise_signal(
        cur, hotel.slug, kind=sig.GHC_DEADLINE, dedupe_key=key,
        title=f"Google third-party hotel prices {verb} 30 Sep 2026: {cl['open_items']} checklist item(s) open",
        summary="Hotel Ads need your own Hotel Center feed. Free booking links are not affected by this change, "
                "but they need the same feed.",
        confidence=0.9, severity=3 if cl["days_left"] <= 7 else 2,
        due_at=datetime.combine(DEADLINE, time(23, 59), tzinfo=hotel.tz),
        evidence=[Evidence(kind="link", label="Google Hotel Center help", value=SOURCE_SUNSET),
                  Evidence(kind="link", label="Free booking links unaffected", value=SOURCE_FBL)]
        + [Evidence(kind="row", label=i["label"], value=i["status"]) for i in cl["items"] if i["status"] == "TODO"],
        action=RecommendedAction(type="core.task", label="Work through the Google Hotel Center checklist",
                                 params={"page": "demand/settings#ghc"}, owner_role="owner"),
        subject_type="dr_ghc_checklist", subject_id=hotel.slug)
    return {"open": cl["open_items"]}
