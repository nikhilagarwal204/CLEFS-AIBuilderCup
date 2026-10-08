"""
Demo part "concierge_live": the guest concierge is switched ON for the demo
hotel (QA GS-1).

WHY a part of its own: the seed wipes every row of the demo hotel first,
cc_live included, and a new hotel starts not live until it passes Trust's
readiness gate on an eval run the demo never has. Without this the guest
page (/stay/demo-deira-crest/<room>) answers every message and quick chip
with "call the front desk" and files no staff request, so the walkthrough
cannot show the concierge at all.

Module door: concierge.go_live_demo (api.py). It skips the readiness gate
only for a hotel whose status is 'demo' (the seed refuses any other hotel
in _reset.prepare_hotel anyway), writes cc_live with source 'demo' so the
console's go-live card says why, and audits concierge.live_switched.

Runs last (core parts use 10-40, later parts 50+): like a real hotel, the
concierge goes live once the property, its approved knowledge base
(core_catalog) and its operations are in place.
"""

from __future__ import annotations

from typing import Any

from clefs.seed.demo_uae.core import ACTOR

PART_ORDER = 80


# ─── Part ───


def seed_part(ctx: Any) -> dict:
    api = ctx.module_api("concierge")
    if api is None or not hasattr(api, "go_live_demo"):
        # Concierge switched off in this process: nothing to switch on.
        return {"concierge_live": 0}
    out = api.go_live_demo(ctx.cur, ctx.slug, actor=ACTOR)
    return {"concierge_live": int(bool(out.get("live")))}


# ─── Checks ───


def concierge_live_checks(cur: Any, slug: str) -> list[tuple[str, bool, str]]:
    """demo-check: the guest concierge answers (a missing row means every
    guest message on the demo QR pages gets "call the front desk")."""
    cur.execute("SELECT to_regclass('public.cc_live') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        return []
    cur.execute("SELECT live, source FROM cc_live WHERE hotel_slug = %s", (slug,))
    row = cur.fetchone()
    ok = bool(row and row["live"])
    return [("concierge live for guests", ok, f"source={row['source']}" if row else "no cc_live row")]


CHECKS = [concierge_live_checks]
