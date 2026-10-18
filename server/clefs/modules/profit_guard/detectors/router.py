"""Detector routes under /hotels/{slug}/profit-guard (02 spec 3.8):
run the detectors on demand and read each detector's tuning state.
Reads need `profit.view`, the manual run `profit.manage`.

Mounted by the module manifest next to the data router:
`routers=[router, data_router, detectors_router]`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import money
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.profit_guard import confidence as conf
from clefs.modules.profit_guard import repo
from clefs.modules.profit_guard.detectors import DETECTOR_IDS, run
from clefs.modules.profit_guard.detectors.signals import KIND_FOR, NOUNS
from clefs.modules.profit_guard.models import normalize_ota

router = APIRouter(prefix="/hotels/{slug}/profit-guard", tags=["profit_guard"])
VIEW = require_perm("profit.view")
MANAGE = require_perm("profit.manage")


class RunIn(BaseModel):
    detectors: list[str] | None = Field(default=None, max_length=20)
    ota_code: str | None = Field(default=None, max_length=40)


@router.post("/detectors/run")
def run_detectors(slug: str, body: RunIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Run the named detectors (default: all of L1-L15) now, inside one
    transaction. Returns the run summary."""
    with db.db() as cur:
        get_hotel(cur, slug)
        try:
            out = run(cur, slug, trigger="manual", detectors=body.detectors, ota_code=normalize_ota(body.ota_code))
        except ValueError as exc:
            raise ClefsError(400, str(exc), "unknown_detector") from exc
        audit(cur, ctx, "profit.detectors_run", "pfg_detectors", out["run_id"],
              meta={"detectors": body.detectors, "findings": out["findings"], "created": out["created"]},
              hotel_slug=slug)
        return out


@router.get("/detectors")
def list_detectors(slug: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """Each detector's feedback and signalling thresholds for this hotel."""
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, slug)
        policies = repo.get_policies(cur, slug)
        rows = conf.tuning_rows(cur, slug)
        cur.execute(
            "SELECT detector, COUNT(*) AS n, COALESCE(SUM(amount_minor), 0) AS amount FROM pfg_findings "
            "WHERE hotel_slug = %s AND status IN ('DETECTED', 'ACCEPTED') AND source = 'detector' GROUP BY detector",
            (slug,),
        )
        open_ = {r["detector"]: r for r in cur.fetchall()}
        out = []
        for det in DETECTOR_IDS:
            th = conf.thresholds(policies, rows.get(det))
            accepted, fp = conf.counts(cur, det, slug)
            opened = open_.get(det) or {"n": 0, "amount": 0}
            out.append({
                "detector": det, "label": NOUNS.get(det, (det, det))[1], "signal_kind": KIND_FOR.get(det),
                "feedback": str(conf.feedback(cur, slug, det, tuning_row=rows.get(det)).quantize(conf.Q)),
                "accepted": accepted, "dismissed_fp": fp,
                "min_amount_minor": th["min_amount_minor"],
                "min_amount": money.money_str(th["min_amount_minor"], hotel.currency),
                "confidence_floor": str(th["confidence_floor"]),
                "suppressed_until": th["suppressed_until"].isoformat() if th["suppressed_until"] else None,
                "open_findings": int(opened["n"]), "open_amount_minor": int(opened["amount"]),
            })
        return {"currency": hotel.currency, "detectors": out}
