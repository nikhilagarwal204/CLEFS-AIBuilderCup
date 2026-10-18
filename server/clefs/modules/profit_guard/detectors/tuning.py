"""Weekly feedback tuning, `pg.feedback_tune` (02 spec 3.4).

- TOO_SMALL: min_amount = max(current, 1.2 x median dismissed amount).
- CONTRACT_EXCEPTION: a contract-edit prompt (the terms the detector
  used are probably not the hotel's).
- 3 or more FALSE_POSITIVE_DATA dismissals in 30 days: the detector is
  suppressed for the hotel for 14 days (stored, not signalled) and a
  founder-review signal is raised.
Dismissal times come from the ledger events (pfg_finding_events), because
a re-run touches a finding's updated_at without changing its dismissal.
"""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal
from typing import Any

from clefs.core import clock
from clefs.core import signals as core_signals
from clefs.modules.profit_guard import confidence as conf
from clefs.modules.profit_guard import repo

SMALL_WINDOW_DAYS = 180
FP_WINDOW_DAYS = 30
FP_LIMIT = 3
SUPPRESS_DAYS = 14
CONTRACT_WINDOW_DAYS = 7
MIN_FACTOR = Decimal("1.2")


def upsert_tuning(cur: Any, hotel_slug: str, detector: str, **cols: Any) -> dict:
    names = list(cols)
    cur.execute(
        f"""
        INSERT INTO pfg_detector_tuning (hotel_slug, detector, {', '.join(names)})
        VALUES (%s, %s, {', '.join(['%s'] * len(names))})
        ON CONFLICT (hotel_slug, detector) DO UPDATE
           SET {', '.join(f'{n} = EXCLUDED.{n}' for n in names)}, updated_at = NOW()
        RETURNING *
        """,
        (hotel_slug, detector, *cols.values()),
    )
    return dict(cur.fetchone())


def _dismissals(cur: Any, hotel_slug: str, reason: str, days: int) -> list[dict]:
    """Findings dismissed with `reason` in the last `days`, one row each,
    with the amount at dismissal."""
    cur.execute(
        """
        SELECT DISTINCT ON (f.id) f.id, f.detector, f.ota_code,
               COALESCE(e.amount_minor, f.amount_minor) AS amount_minor,
               e.at
          FROM pfg_findings f JOIN pfg_finding_events e ON e.finding_id = f.id AND e.to_status = 'DISMISSED'
         WHERE f.hotel_slug = %s AND f.status = 'DISMISSED' AND f.dismiss_reason = %s AND e.at >= %s
         ORDER BY f.id, e.at DESC
        """,
        (hotel_slug, reason, clock.now() - timedelta(days=days)),
    )
    return [dict(r) for r in cur.fetchall()]


def _raise_min(cur: Any, hotel_slug: str, policies: dict, rows: dict[str, dict]) -> dict[str, int]:
    by_det: dict[str, list[int]] = {}
    for d in _dismissals(cur, hotel_slug, "TOO_SMALL", SMALL_WINDOW_DAYS):
        by_det.setdefault(d["detector"], []).append(int(d["amount_minor"]))
    raised = {}
    for det, amounts in by_det.items():
        amounts.sort()
        mid = len(amounts) // 2
        median = Decimal(amounts[mid]) if len(amounts) % 2 else Decimal(amounts[mid - 1] + amounts[mid]) / 2
        current = conf.thresholds(policies, rows.get(det))["min_amount_minor"]
        new = max(current, math.ceil(MIN_FACTOR * median))
        if new > int((rows.get(det) or {}).get("min_amount_minor") or 0) and new > current:
            upsert_tuning(cur, hotel_slug, det, min_amount_minor=new)
            raised[det] = new
    return raised


def _suppress(cur: Any, hotel: Any, rows: dict[str, dict]) -> list[str]:
    now = clock.now()
    by_det: dict[str, int] = {}
    for d in _dismissals(cur, hotel.slug, conf.FP_REASON, FP_WINDOW_DAYS):
        by_det[d["detector"]] = by_det.get(d["detector"], 0) + 1
    out = []
    for det, n in sorted(by_det.items()):
        until = (rows.get(det) or {}).get("suppressed_until")
        if n < FP_LIMIT or (until is not None and until > now):
            continue
        until = now + timedelta(days=SUPPRESS_DAYS)
        upsert_tuning(cur, hotel.slug, det, suppressed_until=until)
        core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
            module="profit_guard", kind="pg.detector.review",
            dedupe_key=f"pg:detector_review:{det}:{now.date().isoformat()}",
            title=f"Leak check {det} paused for 14 days after {n} false positives",
            summary="Its findings are still stored but not sent. The Clefs team reviews the detector with your data.",
            severity=2, confidence=1.0, due_at=until, subject_type="pg_detector", subject_id=det,
            evidence=[core_signals.Evidence(kind="metric", label="False positives in 30 days", value=n)],
        ))
        out.append(det)
    return out


def _contract_prompts(cur: Any, hotel: Any) -> list[str]:
    by_ota: dict[str, list[int]] = {}
    for d in _dismissals(cur, hotel.slug, "CONTRACT_EXCEPTION", CONTRACT_WINDOW_DAYS):
        by_ota.setdefault(d["ota_code"] or "OTA", []).append(d["id"])
    for ota, ids in by_ota.items():
        core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
            module="profit_guard", kind="pg.config.fix_available", dedupe_key=f"pg:contract_review:{ota}",
            title=f"Check the {ota.title()} contract terms: {len(ids)} finding{'s' if len(ids) != 1 else ''} "
                  "dismissed as a contract exception",
            summary="Update the confirmed contract so the leak checks use your real terms.",
            severity=2, confidence=0.9, subject_type="pfg_ota_contracts", subject_id=ota,
            recommended_action=core_signals.RecommendedAction(type="core.task", label="Edit the contract",
                                                              params={"ota_code": ota, "finding_ids": ids}),
        ))
    return sorted(by_ota)


def feedback_tune(cur: Any, hotel: Any) -> dict:
    """Apply the three rules for one hotel. Returns what changed."""
    policies = repo.get_policies(cur, hotel.slug)
    rows = conf.tuning_rows(cur, hotel.slug)
    return {"min_amount_raised": _raise_min(cur, hotel.slug, policies, rows),
            "suppressed": _suppress(cur, hotel, rows),
            "contract_prompts": _contract_prompts(cur, hotel)}
