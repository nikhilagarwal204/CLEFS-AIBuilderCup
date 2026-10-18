"""
Amenities public API (frozen signatures; other modules import only this file).

Canon 8: the amenity catalog is amn_items. Service intake accepts
amenity_item_id + qty and resolves items through these functions.

Rows carry the frozen fields {id, key, name, request_type, max_qty,
chargeable, price, currency} plus the catalog columns callers already read
(name_en, name_ar, request_type_key, category, basis, unit_cost, ...). price
is a Decimal string in the item currency, or None when the item is free.
"""

from __future__ import annotations

from typing import Any


def list_items(cur: Any, hotel_slug: str, *, language: str = "en", active_only: bool = True) -> list[dict]:
    """[{id, key, name, request_type, max_qty, chargeable, price, currency}]"""
    from clefs.modules.amenities import catalog

    return [catalog.item_out(r, language=language)
            for r in catalog.list_items(cur, hotel_slug, active_only=active_only)]


def resolve_item(cur: Any, hotel_slug: str, amenity_item_id: int) -> dict | None:
    """One catalog item (same shape as list_items rows) or None."""
    from clefs.modules.amenities import catalog

    if not isinstance(amenity_item_id, int) or isinstance(amenity_item_id, bool) or amenity_item_id <= 0:
        return None
    row = catalog.get_item(cur, hotel_slug, amenity_item_id)
    return catalog.item_out(row) if row else None


def link_request(cur: Any, hotel_slug: str, *, request_id: int, amenity_item_id: int, qty: int) -> None:
    """Record that a service request consumed qty of an item (feeds leakage
    analytics). The line is TOOL-matched (the caller named the item); an
    unknown item, another hotel's request or a non-positive qty is ignored,
    because the ledger is analytics and must never block a guest request."""
    from clefs.modules.amenities import catalog, requests

    if not isinstance(qty, int) or qty <= 0 or not isinstance(request_id, int):
        return None
    item = catalog.get_item(cur, hotel_slug, amenity_item_id) if isinstance(amenity_item_id, int) else None
    if not item:
        return None
    cur.execute("SELECT 1 FROM staff_requests WHERE id = %s AND hotel_slug = %s", (request_id, hotel_slug))
    if not cur.fetchone():
        return None
    # Not capped at max_qty: if staff delivered 6 waters, 6 is what expected use must add.
    requests.link(cur, hotel_slug, request_id, item["id"], min(qty, 50), method="TOOL")
    return None
