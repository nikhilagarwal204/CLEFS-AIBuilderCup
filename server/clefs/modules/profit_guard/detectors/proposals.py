"""Signals into proposed actions (review completeness-01).

A Profit Guard signal names its recommended action (signals.ACTIONS /
ACTION_OVERRIDES), but a recommendation alone never reaches the dial: the
brief showed "No action proposed yet" with no approve, assign or dispute
verb, and the verifiers (_vcc_verify, _payout_verify, _config_verify,
_no_show_verify) never ran, so nothing became verified value.

Here every signal refresh keeps exactly one live pg.* action per signal,
covering the signal's findings that no OTHER live action already covers
(a dispute packet's own action, or a neighbour signal's action). Rules:

- Undecided ('proposed') actions follow the group: when its finding set
  changes they are cancelled ("regrouped") and a fresh one is proposed.
- Decided actions (approved, assigned, snoozed, in progress, done) are
  never touched: a person already acted, and cancelling would stop the
  verifier from counting the money.
- A proposal a person REJECTED is never re-proposed for the same set
  (no nagging); one Clefs cancelled itself may come back.
- An empty group cancels its undecided proposals ("resolved").
The dial decides where each lands (brief, console, or suppressed when off).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from clefs.core import actions as core_actions
from clefs.core.errors import ClefsError
from clefs.modules.profit_guard.detectors.base import Ctx

log = logging.getLogger(__name__)

MODULE = "profit_guard"
TERMINAL = tuple(sorted(core_actions.TERMINAL))
UNDECIDED = ("proposed",)
REPROPOSE_AFTER = ("cancelled",)   # our own cancellations only; a person's "rejected" sticks


# ─── Keys ───


def prefix(signal_key: str) -> str:
    return f"{signal_key}:action:"


def digest(ids: list[int]) -> str:
    return hashlib.sha1(",".join(str(i) for i in sorted(ids)).encode()).hexdigest()[:12]


def _ids(action: dict) -> set[int]:
    raw = (action.get("params") or {}).get("finding_ids") or []
    return {int(i) for i in raw if str(i).isdigit()}


# ─── Reads ───


def _live_covering(ctx: Ctx, act_type: str, ids: list[int]) -> list[dict]:
    """Live actions of this type whose finding_ids touch any of `ids`."""
    ctx.cur.execute(
        """
        SELECT id, status, dedupe_key, params, signal_id FROM actions
         WHERE hotel_slug = %s AND module = %s AND type = %s AND NOT (status = ANY(%s))
           AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(params->'finding_ids', '[]'::jsonb)) e
                        WHERE e ~ '^[0-9]+$' AND e::BIGINT = ANY(%s))
         ORDER BY id
        """,
        (ctx.slug, MODULE, act_type, list(TERMINAL), ids),
    )
    return [dict(r) for r in ctx.cur.fetchall()]


def _own(ctx: Ctx, signal_key: str, *, live: bool) -> list[dict]:
    """This signal's own proposals (dedupe_key under its prefix)."""
    ctx.cur.execute(
        f"""
        SELECT id, status, dedupe_key, params, type FROM actions
         WHERE hotel_slug = %s AND module = %s AND starts_with(dedupe_key, %s)
           AND {"NOT" if live else ""} (status = ANY(%s))
         ORDER BY id
        """,
        (ctx.slug, MODULE, prefix(signal_key), list(TERMINAL)),
    )
    return [dict(r) for r in ctx.cur.fetchall()]


def _cancel(ctx: Ctx, action_id: int, reason: str) -> None:
    """A reason other than superseded/expired leaves the signal's status
    alone (core.actions.cancel only moves the signal for those two)."""
    try:
        core_actions.cancel(ctx.cur, action_id, actor=core_actions.SYSTEM, reason=reason)
    except ClefsError:
        log.info("profit_guard: action %s not cancellable (%s)", action_id, reason)


# ─── Sync and retire ───


def _card_ids(rows: list[dict]) -> list[int]:
    """L8/L9 findings point at their virtual card through evidence refs;
    pg.vcc_charge's verifier checks each card on the next card report."""
    out: list[int] = []
    for r in rows:
        for e in r.get("evidence") or []:
            ref = e.get("ref") if isinstance(e, dict) else None
            if isinstance(ref, dict) and ref.get("table") == "pfg_virtual_cards" and str(ref.get("id")).isdigit():
                if int(ref["id"]) not in out:
                    out.append(int(ref["id"]))
                break
    return out


def _params(act_type: str, detector: str, ota: str | None, rows: list[dict]) -> dict:
    p: dict[str, Any] = {"finding_ids": [r["id"] for r in rows], "detector": detector, "ota_code": ota}
    if act_type == "pg.vcc_charge":
        cards = _card_ids(rows)
        p["card_ids"] = cards
        if len(cards) == 1:
            p["card_id"] = cards[0]
    return p


def _next_key(ctx: Ctx, signal_key: str, want: list[int]) -> str | None:
    """The dedupe key for a proposal of exactly `want`: None when a person
    already rejected (or the verifier already judged) that same set."""
    base = f"{prefix(signal_key)}{digest(want)}"
    ctx.cur.execute("SELECT dedupe_key, status FROM actions WHERE hotel_slug = %s AND starts_with(dedupe_key, %s) "
                    "ORDER BY id", (ctx.slug, base))
    prior = [dict(r) for r in ctx.cur.fetchall()]
    if not prior:
        return base
    if any(p["status"] not in REPROPOSE_AFTER for p in prior):
        return None
    return f"{base}:{len(prior)}"


def sync(ctx: Ctx, *, signal: dict, signal_key: str, act_type: str, label: str, detector: str,
         ota: str | None, rows: list[dict], title: str, impact_minor: int | None) -> dict | None:
    """Keep one live proposal on this signal for its uncovered findings.
    Returns the proposed action (API shape), or None."""
    if not act_type.startswith("pg.") or not rows:
        return None
    ids = [int(r["id"]) for r in rows]
    own = _own(ctx, signal_key, live=True)
    own_ids = {a["id"] for a in own}
    covered: set[int] = set()
    for a in _live_covering(ctx, act_type, ids):
        if a["id"] not in own_ids or a["status"] not in UNDECIDED:
            covered |= _ids(a)
    want_rows = [r for r in rows if int(r["id"]) not in covered]
    want = sorted(int(r["id"]) for r in want_rows)
    keep = None
    for a in own:
        if a["status"] not in UNDECIDED:
            continue
        if keep is None and sorted(_ids(a)) == want and want:
            keep = a
        else:
            _cancel(ctx, a["id"], "regrouped")
    if keep is not None or not want:
        return keep
    key = _next_key(ctx, signal_key, want)
    if key is None:
        return None
    deadlines = [r["deadline_at"] for r in want_rows if r.get("deadline_at") is not None]
    # The signal's impact is already converted to the hotel currency; a
    # partial set counts only its hotel-currency findings (no FX guess).
    expected = impact_minor if len(want_rows) == len(rows) else \
        sum(int(r["amount_minor"]) for r in want_rows if r["currency"] == ctx.currency)
    # Its own savepoint: a proposal is advisory, and a failure here must
    # never roll back the detector run's findings (the OWN-04 lesson).
    ctx.cur.execute("SAVEPOINT pg_propose")
    try:
        act = core_actions.create_action(
            ctx.cur, ctx.slug, type=act_type, title=f"{label}: {title}"[:200],
            params=_params(act_type, detector, ota, want_rows), signal_id=signal["id"],
            expected_minor=expected or None, currency=ctx.currency,
            due_at=min(deadlines) if deadlines else None, dedupe_key=key,
            actor=core_actions.SYSTEM, via="system")
    except Exception:  # noqa: BLE001 - an unregistered type or a refused param: the signal still stands
        ctx.cur.execute("ROLLBACK TO SAVEPOINT pg_propose")
        ctx.cur.execute("RELEASE SAVEPOINT pg_propose")
        log.warning("profit_guard: could not propose %s for %s", act_type, signal_key, exc_info=True)
        return None
    ctx.cur.execute("RELEASE SAVEPOINT pg_propose")
    return act if act.get("id") else None


def retire(ctx: Ctx, signal_key: str, reason: str = "resolved") -> int:
    """The signal's group emptied: cancel its undecided proposals. Decided
    ones keep verifying (the money may already be on its way)."""
    n = 0
    for a in _own(ctx, signal_key, live=True):
        if a["status"] in UNDECIDED:
            _cancel(ctx, a["id"], reason)
            n += 1
    return n
