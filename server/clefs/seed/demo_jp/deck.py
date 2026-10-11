"""
Tokyo demo part "deck": the live state the AI Builder Cup deck shows, as
demo_uae.deck sets it up (Maria's phone paired with her shift open, the
shadow week over, the 22 standard tests queued, the keyed Room 204 link),
for this hotel's cast (core.py).

Service settings for Tokyo, written with Room 204's guest limit after the
history replays: an air-conditioning request counts as urgent above 35 C
outside (Japan's "extreme heat day"; the UAE default is 40 C), and the
emergency card shows the front desk's extension, 9, next to 110 and 119.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from clefs.seed.demo_jp import core as cast
from clefs.seed.demo_uae import deck as uae

PART_ORDER = 85
ACTOR = uae.ACTOR

# The guest UI languages the hotel must offer: all of its own.
LANGUAGES = frozenset(cast.GUEST_LANGUAGES)
SERVICE_SETTINGS = {"guest_room_limit": uae.GUEST_ROOM_LIMIT, "heat_threshold_c": 35.0, "security_extension": "9"}


def service_settings(ctx: Any) -> dict:
    """The Service settings above, merged into whatever the hotel's Service
    settings already say (the core writer audits the change)."""
    from clefs.core.hotels import module_settings_row, set_module_settings

    current = (module_settings_row(ctx.cur, ctx.slug, "service") or {}).get("settings") or {}
    set_module_settings(ctx.cur, ctx.slug, "service", {**current, **SERVICE_SETTINGS}, actor=ACTOR)
    return {"guest_room_limit": SERVICE_SETTINGS["guest_room_limit"],
            "heat_threshold_c": SERVICE_SETTINGS["heat_threshold_c"]}


def seed_part(ctx: Any) -> dict:
    out: dict[str, Any] = {}
    out.update(uae.pair_maria(ctx))
    out.update(uae.close_shadow_week(ctx))
    out.update(service_settings(ctx))
    out.update(uae.queue_tests(ctx))
    uae.demo_links(ctx)
    return out


deck_checks = partial(uae.deck_checks, cast=cast, languages=LANGUAGES)
CHECKS = [deck_checks]
