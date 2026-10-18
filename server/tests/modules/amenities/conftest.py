"""Amenities test fixtures: a seeded Dubai catalog, occupancy rows and
service requests written straight into the baseline tables."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

DXB = ZoneInfo("Asia/Dubai")
# Tuesday 2026-09-29 10:00 Dubai.
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=DXB)
TODAY = date(2026, 9, 29)
END_DEFAULT = date(2026, 9, 28)                # yesterday: the default report end


@pytest.fixture
def frozen(clock):
    clock.freeze(NOW)
    return clock


@pytest.fixture
def items(cur, hotel_uae, frozen):
    """The seeded UAE catalog, keyed by item key."""
    from clefs.modules.amenities import catalog

    catalog.seed_defaults(cur, hotel_uae.slug, "AED")
    return {r["key"]: r for r in catalog.list_items(cur, hotel_uae.slug)}


@pytest.fixture
def occ(cur, hotel_uae):
    """occ(d, occupied, arrivals=0, floors=None): an occupancy_daily row and
    optional per-floor rows ({floor: occupied})."""

    def _occ(d: date, occupied: int, arrivals: int = 0, floors: dict | None = None) -> None:
        cur.execute(
            "INSERT INTO occupancy_daily (hotel_slug, date, rooms_available, rooms_occupied, arrivals, currency, "
            "source, is_complete) VALUES (%s, %s, 100, %s, %s, 'AED', 'MANUAL', TRUE) ON CONFLICT (hotel_slug, date) "
            "DO UPDATE SET rooms_occupied = EXCLUDED.rooms_occupied, arrivals = EXCLUDED.arrivals",
            (hotel_uae.slug, d, occupied, arrivals),
        )
        for f, n in (floors or {}).items():
            cur.execute(
                "INSERT INTO occupancy_by_floor_daily (hotel_slug, date, floor, rooms_occupied) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (hotel_slug, date, floor) DO UPDATE SET rooms_occupied = EXCLUDED.rooms_occupied",
                (hotel_uae.slug, d, f, n),
            )

    return _occ


@pytest.fixture
def occ_range(occ):
    def _range(start: date, days: int, occupied: int, arrivals: int = 0, floors: dict | None = None) -> None:
        for i in range(days):
            occ(start + timedelta(days=i), occupied, arrivals, floors)

    return _range


@pytest.fixture
def make_request(cur, hotel_uae):
    """make_request(room, type, subtype=None, description=None, at=NOW) -> id,
    through the legacy baseline columns (Service may be off in these tests)."""

    def _make(room: str, type_: str = "Amenities", subtype: str | None = None, description: str | None = None,
              at: datetime | None = None, category: str = "Housekeeping") -> int:
        ts = int((at or NOW).timestamp() * 1000)
        cur.execute(
            "INSERT INTO staff_requests (hotel_slug, room, type, subtype, description, category, timestamp) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (hotel_uae.slug, room, type_, subtype, description, category, ts),
        )
        return cur.fetchone()["id"]

    return _make


@pytest.fixture
def issue(cur, hotel_uae):
    """issue(item, qty, at, floor=None, reason='REPLENISH', staff=None) -> row."""
    from clefs.modules.amenities import issuance

    def _issue(item: dict, qty, at: datetime, floor: str | None = None, reason: str = "REPLENISH",
               staff: str | None = None, **kw) -> dict:
        body = issuance.IssueIn(lines=[issuance.IssueLine(item_id=item["id"], qty=qty, issued_at=at, to_floor=floor,
                                                          reason=reason, staff_name=staff, **kw)])
        return issuance.create_lines(cur, hotel_uae.slug, body)[0]

    return _issue


def at(d: date, hour: int = 9) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=DXB)
