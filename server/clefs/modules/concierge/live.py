"""
The concierge.live switch and its eval readiness gate (03 spec 5.4, PLAN
Trust: "a readiness gate that blocks concierge.live").

Turning the concierge ON for guests needs trust.api.readiness() to say
ready (safety evals 100%, routing 95%+, required KB sections approved, a
recent run on the current prompt and KB) or an active owner override, which
Trust already writes to the audit log. Turning it OFF is always allowed.
While it is off, no guest surface starts a conversation: the web mint
answers 409 concierge_not_live (the guest page shows "call the front desk"),
the phone webhook rejects with 486 so the PBX fails over to the operator,
and WhatsApp passes the message on to Service's staff fallback.

Storage: the cc_live table (migration 0204), never the module-settings
JSON, which the generic PUT /module-settings/concierge also writes with no
gate. Only set_live() writes it, and it audits "concierge.live_switched".

Grandfathering: migration 0204 gave every hotel that predates the rebuild
(legacy_open_access, set TRUE for all of them by 0003) a live row, since
they were already serving guests; an explicit OFF then ON passes the gate.
It is captured once rather than read from legacy_open_access at runtime,
so an owner locking the dashboard down later does not silently take the
guest concierge offline. Every hotel created since has no row (not live)
and must pass the gate once. The gate checks the transition, not every
mint: a later red eval does not silently cut guests off mid-stay; Trust
surfaces it and the owner decides.
"""

from __future__ import annotations

from typing import Any

from clefs.audit import audit
from clefs.core.errors import ClefsError, not_found
from clefs.core.hotels import find_hotel

# ─── Reading ───


def live_state(cur: Any, hotel_slug: str) -> dict:
    """{live, source}: source is "setting" (switched through PUT /live),
    "grandfathered" (live since before the rebuild, migration 0204), "demo"
    (the synthetic demo hotel, set_demo_live) or "default" (never switched:
    not live)."""
    cur.execute("SELECT live, source FROM cc_live WHERE hotel_slug = %s", (hotel_slug,))
    row = cur.fetchone()
    if row is None:
        return {"live": False, "source": "default"}
    source = row["source"] if row["source"] in ("grandfathered", "demo") else "setting"
    return {"live": bool(row["live"]), "source": source}


def is_live(cur: Any, hotel_slug: str) -> bool:
    return live_state(cur, hotel_slug)["live"]


def require_live(cur: Any, hotel_slug: str) -> None:
    if not is_live(cur, hotel_slug):
        raise ClefsError(409, "The assistant is not available yet, please call the front desk", "concierge_not_live")


# ─── Switching ───


def readiness(cur: Any, hotel_slug: str) -> dict:
    """trust.api.readiness, read defensively: an unavailable gate is a red
    gate (never a silent pass)."""
    import logging

    from clefs.modules.trust import api as trust_api

    # A savepoint: a Trust failure (its tables absent in a trimmed build)
    # must not abort the caller's transaction, only turn the gate red.
    cur.execute("SAVEPOINT cc_live_gate")
    try:
        out = trust_api.readiness(cur, hotel_slug)
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT cc_live_gate")
        logging.getLogger("clefs.concierge.live").exception("readiness unavailable for %s", hotel_slug)
        out = None
    else:
        cur.execute("RELEASE SAVEPOINT cc_live_gate")
    return out if isinstance(out, dict) else {"ready": False, "overridden": False, "checks": []}


def set_live(cur: Any, hotel_slug: str, live: bool, *, actor: Any) -> dict:
    """Switch concierge.live. ON needs the readiness gate (or its audited
    owner override): 409 readiness_not_met with the failing checks
    otherwise. Audited here (the gate's verdict goes into meta)."""
    if find_hotel(cur, hotel_slug) is None:
        raise not_found("Hotel not found", "hotel_not_found")
    gate = None
    if live:
        gate = readiness(cur, hotel_slug)
        if not gate.get("ready"):
            failing = [c.get("key") for c in gate.get("checks") or [] if not c.get("ok")]
            raise ClefsError(409, "The concierge has not passed its readiness checks yet: "
                             + (", ".join(str(k) for k in failing) or "no eval run"), "readiness_not_met")
    before = live_state(cur, hotel_slug)
    overridden = bool((gate or {}).get("overridden"))
    user_id = getattr(getattr(actor, "principal", actor), "user_id", None)
    cur.execute(
        """
        INSERT INTO cc_live (hotel_slug, live, overridden, run_id, changed_by, changed_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (hotel_slug) DO UPDATE SET live = EXCLUDED.live, overridden = EXCLUDED.overridden,
            run_id = EXCLUDED.run_id, changed_by = EXCLUDED.changed_by, source = 'switch', changed_at = NOW()
        """,
        (hotel_slug, bool(live), overridden and bool(live), str((gate or {}).get("run_id") or "") or None, user_id),
    )
    audit(cur, actor, "concierge.live_switched", "concierge_live", hotel_slug, before=before,
          after={"live": bool(live), "overridden": overridden}, hotel_slug=hotel_slug,
          meta={"readiness": {k: (gate or {}).get(k) for k in ("ready", "overridden", "run_id", "prompt_version")}} if gate else None)
    return {**live_state(cur, hotel_slug), "overridden": bool((gate or {}).get("overridden"))}


# ─── Demo hotels ───


def set_demo_live(cur: Any, hotel_slug: str, *, actor: Any) -> dict:
    """Switch the concierge ON for a synthetic demo hotel without the
    readiness gate (QA GS-1). The demo seed wipes and rebuilds the hotel on
    every run, so its cc_live row goes with it, and a demo has no eval run
    to pass the gate on: without this the guest page answers every message
    with "call the front desk" and the walkthrough cannot show the concierge.

    Refused (409 not_demo_hotel) unless hotels.status is 'demo': a real
    hotel always goes live through set_live and Trust's gate. The row says
    source 'demo' (never 'switch' with overridden) so the console and the
    audit trail show why the gate was skipped. An explicit OFF through
    PUT /live afterwards still works and sticks until the next reseed."""
    # fresh: the status decides whether the gate applies, so never trust a
    # cached Hotel from before a status change.
    hotel = find_hotel(cur, hotel_slug, fresh=True)
    if hotel is None:
        raise not_found("Hotel not found", "hotel_not_found")
    if getattr(hotel, "status", None) != "demo":
        raise ClefsError(409, "Only a demo hotel can go live without the readiness checks", "not_demo_hotel")
    before = live_state(cur, hotel_slug)
    user_id = getattr(getattr(actor, "principal", actor), "user_id", None)
    cur.execute(
        """
        INSERT INTO cc_live (hotel_slug, live, source, overridden, run_id, changed_by, changed_at)
        VALUES (%s, TRUE, 'demo', FALSE, NULL, %s, NOW())
        ON CONFLICT (hotel_slug) DO UPDATE SET live = TRUE, source = 'demo', overridden = FALSE,
            run_id = NULL, changed_by = EXCLUDED.changed_by, changed_at = NOW()
        """,
        (hotel_slug, user_id),
    )
    audit(cur, actor, "concierge.live_switched", "concierge_live", hotel_slug, before=before,
          after={"live": True, "overridden": False}, hotel_slug=hotel_slug,
          meta={"demo": True, "reason": "demo hotel: readiness gate not applied"})
    return {**live_state(cur, hotel_slug), "overridden": False}
