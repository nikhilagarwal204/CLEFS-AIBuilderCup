"""Profit Guard leakage detectors L1-L15 (02 spec 3.3-3.4).

`run(cur, hotel_slug, trigger=...)` is the one entry point: it runs the
detectors a trigger names, stores their drafts as findings (fingerprinted,
so a re-run updates instead of duplicating), scores confidence, rebuilds
the grouped signals and closes still-true findings a full scan no longer
sees. Importing the package registers the signal kinds and the jobs
(detectors/jobs.py: on commit, hourly deadline watch, nightly, weekly
feedback tuning).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from clefs.core.hotels import get_hotel
from clefs.modules.profit_guard import repo
from clefs.modules.profit_guard.detectors import (
    l01_commission_base, l02_commission_rate, l03_no_show_commission, l04_early_departure, l05_base_vs_folio,
    l06_discount_stacking, l07_parity, l08_vcc_uncharged, l09_vcc_expired, l10_payout_mismatch, l11_fx_loss,
    l12_refund_commission, l13_chargebacks, l14_deadline_watch, l15_vat_reverse_charge, signals, store,
)
from clefs.modules.profit_guard.detectors.base import FindingDraft, Scope, make_ctx

# ─── Registry ───

REGISTRY: dict[str, Callable[..., list[FindingDraft]]] = {
    "L1": l01_commission_base.run, "L2": l02_commission_rate.run, "L3": l03_no_show_commission.run,
    "L4": l04_early_departure.run, "L5": l05_base_vs_folio.run, "L6": l06_discount_stacking.run,
    "L7": l07_parity.run, "L8": l08_vcc_uncharged.run, "L9": l09_vcc_expired.run, "L10": l10_payout_mismatch.run,
    "L11": l11_fx_loss.run, "L12": l12_refund_commission.run, "L13": l13_chargebacks.run,
    "L14": l14_deadline_watch.run, "L15": l15_vat_reverse_charge.run,
}
# Finding detector ids each runner can emit (L1 also raises L1b; L14 hands
# a missed deadline over as L3).
PRODUCES = {"L1": ("L1", "L1b"), "L14": ("L14", "L3")}
DETECTOR_IDS = ("L1", "L1b", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10", "L11", "L12", "L13", "L14", "L15")

STATEMENT_SET = ("L1", "L2", "L3", "L4", "L5", "L6", "L12", "L15")
TRIGGERS: dict[str, tuple[str, ...]] = {
    "OTA_STATEMENT": STATEMENT_SET,
    "CREDIT_NOTE": STATEMENT_SET,         # credits net off: re-run the OTA's lines (window scope)
    "OTA_PAYOUT": ("L10", "L11", "L12", "L13"),
    "BANK": ("L10", "L11"),
    "VCC_REPORT": ("L8", "L9"),
    "deadline": ("L8", "L14"),                       # hourly (T7 is the tax part's)
    "nightly": ("L1", "L6", "L7", "L9", "L13", "L15"),  # 02:00 local (L1 for L1b's 90-day view)
    "manual": tuple(REGISTRY),
}
COMMIT_KINDS = ("OTA_STATEMENT", "CREDIT_NOTE", "OTA_PAYOUT", "BANK", "VCC_REPORT")


def scope_for(trigger: str, *, batch_id: int | None = None, ota_code: str | None = None) -> Scope:
    if trigger in COMMIT_KINDS:
        # A credit note's lines are not claims: re-read the OTA's window.
        return Scope(kind="commit", batch_id=None if trigger == "CREDIT_NOTE" else batch_id, ota_code=ota_code)
    return Scope(kind=trigger if trigger in ("nightly", "deadline") else "manual", ota_code=ota_code)


# ─── Run ───


def run(cur: Any, hotel_slug: str, *, trigger: str = "manual", detectors: list[str] | tuple[str, ...] | None = None,
        batch_id: int | None = None, ota_code: str | None = None, run_id: str | None = None) -> dict:
    """Run detectors for one hotel inside the caller's transaction.

    Returns {run_id, trigger, detectors: {id: {found, created, signalable}},
    findings, created, signals: {groups, items, resolved, proposed}, closed, side_signals}.
    Unknown detector ids raise ValueError (the route turns it into a 400)."""
    names = tuple(detectors) if detectors else TRIGGERS.get(trigger, ())
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise ValueError(f"unknown detector(s): {', '.join(unknown)}")
    hotel = get_hotel(cur, hotel_slug)
    ctx = make_ctx(cur, hotel, repo.get_policies(cur, hotel_slug), run_id or f"{trigger}:{uuid.uuid4().hex[:12]}")
    store.load_tuning(ctx)
    scope = scope_for(trigger, batch_id=batch_id, ota_code=ota_code)
    drafts: list[FindingDraft] = []
    for name in names:
        drafts += REGISTRY[name](ctx, scope)
    moved = store.previous_groups(ctx, drafts)
    stored = store.persist(ctx, drafts)
    per: dict[str, dict[str, int]] = {}
    for s in stored:
        c = per.setdefault(s.draft.detector, {"found": 0, "created": 0, "signalable": 0})
        c["found"] += 1
        c["created"] += int(s.created)
        c["signalable"] += int(s.signalable)
    closed: list[dict] = []
    if scope.full_scan:
        for name in names:
            for det in PRODUCES.get(name, (name,)):
                seen = {s.draft.fingerprint for s in stored if s.draft.detector == det}
                closed += store.auto_close(ctx, det, seen)
    groups = {(s.draft.detector, s.draft.ota_code, s.draft.group or "item") for s in stored
              if s.draft.group != "nosignal"}
    groups |= {(r["detector"], r["ota_code"], signals.group_of(r)) for r in closed}
    groups |= moved
    known = {s.draft.fingerprint: s.draft.severity for s in stored if s.draft.severity}
    totals = {"groups": 0, "items": 0, "resolved": 0, "proposed": 0}
    for det, ota, group in sorted(groups, key=lambda g: (g[0], g[1] or "", g[2])):
        if det not in signals.KIND_FOR or group == "nosignal":
            continue
        got = signals.refresh_group(ctx, det, ota, group, known=known)
        totals["groups"] += got["group"]
        totals["items"] += got["items"]
        totals["resolved"] += got["resolved"]
        totals["proposed"] += got.get("proposed", 0)
    totals["resolved"] += signals.resolve_items(ctx, closed)
    return {"run_id": ctx.run_id, "trigger": trigger, "detectors": per, "findings": len(stored),
            "created": sum(int(s.created) for s in stored), "signals": totals, "closed": len(closed),
            "side_signals": signals.raise_side_signals(ctx)}


def refresh_signals_for(cur: Any, hotel_slug: str, finding_ids: list[int]) -> int:
    """Rebuild the signals of the groups these findings belong to (call it
    after a person dismisses, accepts or recovers a finding)."""
    if not finding_ids:
        return 0
    hotel = get_hotel(cur, hotel_slug)
    ctx = make_ctx(cur, hotel, repo.get_policies(cur, hotel_slug), "refresh")
    store.load_tuning(ctx)
    cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND id = ANY(%s) AND source = 'detector'",
                (hotel_slug, list(finding_ids)))
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        if r["detector"] in signals.KIND_FOR and signals.group_of(r) != "nosignal":
            signals.refresh_group(ctx, r["detector"], r["ota_code"], signals.group_of(r))
    signals.resolve_items(ctx, [r for r in rows if r["status"] not in ("DETECTED", "ACCEPTED")])
    return len(rows)


from clefs.modules.profit_guard.detectors import jobs as _jobs  # noqa: E402,F401  (registers the jobs)
