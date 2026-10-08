"""Direct-booking attribution (04 spec D2): a booking-engine export of
booking ref, utm_* fields, promo code and landing URL, matched onto
reservations. core/ingest's BOOKING_ENGINE_ATTRIBUTION commit calls
apply_attribution with the canonical rows.

A ref matches reservations.booking_engine_ref, else a PMS reservation id,
else the OTA-id column a PMS sometimes fills with the engine's reference,
on a direct channel only (an OTA booking is never attributed to a campaign).
"""

from __future__ import annotations

import json
from typing import Any

from clefs.core.stays.contacts import DIRECT_CHANNELS

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")

_FIND = """
SELECT id FROM reservations
WHERE hotel_slug = %(hotel)s AND (channel_code IS NULL OR channel_code = ANY(%(direct)s))
  AND (booking_engine_ref = %(ref)s OR pms_res_id = %(ref)s OR ota_res_id = %(ref)s)
ORDER BY (booking_engine_ref = %(ref)s) DESC, (pms_res_id = %(ref)s) DESC, id
LIMIT 1
"""


def apply_attribution(cur: Any, hotel_slug: str, rows: list[dict]) -> dict[str, Any]:
    """rows: [{booking_ref, utm_source?, utm_medium?, utm_campaign?,
    utm_term?, utm_content?, promo_code?, landing_url?}]. Sets utm (with
    landing_url inside), promo_code and booking_engine_ref on the matched
    reservation. Returns {matched, unmatched: [refs]}."""
    matched, unmatched = 0, []
    for r in rows:
        ref = str(r.get("booking_ref") or "").strip()
        if not ref:
            continue
        cur.execute(_FIND, {"hotel": hotel_slug, "direct": sorted(DIRECT_CHANNELS), "ref": ref})
        hit = cur.fetchone()
        if hit is None:
            unmatched.append(ref)
            continue
        utm = {k: str(r[k]).strip()[:200] for k in UTM_KEYS if r.get(k)}
        if r.get("landing_url"):
            utm["landing_url"] = str(r["landing_url"]).strip()[:500]
        cur.execute(
            """
            UPDATE reservations SET utm = COALESCE(utm, '{}'::jsonb) || %s::jsonb,
                promo_code = COALESCE(%s, promo_code), booking_engine_ref = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (json.dumps(utm), (r.get("promo_code") or None), ref, hit["id"]),
        )
        matched += 1
    return {"matched": matched, "unmatched": unmatched}


def attributed_bookings(cur: Any, hotel_slug: str, *, utm_source: str | None = None) -> list[dict]:
    """Reservations carrying attribution (optionally from one utm_source)."""
    cur.execute(
        """
        SELECT id, pms_res_id, channel_code, booked_at, check_in, nights, status, room_revenue_minor, currency, utm,
               promo_code, booking_engine_ref
        FROM reservations WHERE hotel_slug = %s AND utm IS NOT NULL AND (%s::text IS NULL OR utm->>'utm_source' = %s)
        ORDER BY booked_at NULLS LAST, id
        """,
        (hotel_slug, utm_source, utm_source),
    )
    return [dict(r) for r in cur.fetchall()]
