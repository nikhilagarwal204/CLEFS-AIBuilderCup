"""
The Tokyo demo hotel: Hotel Aozora Shinjuku (aozora-shinjuku), the hotel
judges meet in the AI Builder Cup prototype.

    python -m clefs.cli seed-demo [--hotel aozora-shinjuku] [--anchor 2026-10-21]

A variant of the UAE demo seed (clefs.seed.demo_uae), not a copy of it.
That package's machinery runs here unchanged: the orchestrator
(run_variant), the wipe, the stays planner, the Service history and its
live episodes, the amenity issuance log, the deck cast and demo-check.
Each part module of this package restates the data of its UAE namesake
(same names, Tokyo values) and calls the shared part code, which reads the
data through ctx.data("<part>") (see demo_uae's docstring, "Variants").

Fictional property: a mid-range hotel in Nishi-Shinjuku, 80 rooms on 8
floors, JPY, Asia/Tokyo, check-in 15:00 and check-out 11:00, eleven guest
languages and a team of Japanese and foreign staff. Everything the
prototype shows is seeded: the property and its people (core), the
concierge's knowledge in English and Japanese, the menu and the amenity
catalogue (core_catalog), the stays behind guests and occupancy
(core_stays), Service history, roster, amenity log, in-room dining and the
room roster (ops), the live concierge and the deck cast. The money and
growth parts are left out: their modules are switched off in the
prototype, and the hotel switches them off too (core.OFF_MODULES).

Deterministic like the UAE seed (same RNG_SEED and anchor rules, in Tokyo
time); idempotent and refused on any hotel that is not a demo.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from clefs.seed import demo_uae
from clefs.seed.demo_uae import RNG_SEED, Part

DEMO_SLUG = "aozora-shinjuku"
TZ = ZoneInfo("Asia/Tokyo")
MANIFEST_PATH = __path__[0] + "/expected_plants.json"
# The demo-check sections this hotel has plants for (demo_uae.check).
CHECK_SECTIONS = ("service", "amenities", "upsell")


def live_anchor(wall: datetime | None = None) -> tuple[date, datetime | None]:
    """demo_uae.live_anchor_in() in Tokyo time."""
    return demo_uae.live_anchor_in(TZ, wall)


def check_as_of(anchor: date, as_of: datetime | None) -> datetime | None:
    return demo_uae.check_as_of_in(TZ, anchor, as_of)


def discover_parts() -> list[Part]:
    return demo_uae.discover_parts(sys.modules[__name__])


def hotel_fields(slug: str) -> dict[str, Any]:
    from clefs.seed.demo_jp import core

    return core.hotel_fields(slug)


def seed(
    cur: Any,
    slug: str = DEMO_SLUG,
    *,
    anchor: date | None = None,
    scale: float = 1.0,
    parts: list[str] | None = None,
    seed: int = RNG_SEED,
    password: str | None = None,
    as_of: datetime | None = None,
) -> dict:
    """Build (or rebuild) the Tokyo demo hotel `slug` in the caller's
    transaction. The arguments and result are demo_uae.seed()'s, with the
    anchor in Tokyo time."""
    return demo_uae.run_variant(sys.modules[__name__], cur, slug, anchor=anchor, scale=scale, parts=parts,
                                seed=seed, password=password, as_of=as_of)


manifest = demo_uae.manifest


def write_manifest(result: dict, path: str | None = None) -> str:
    return demo_uae.write_manifest(result, path or MANIFEST_PATH)
