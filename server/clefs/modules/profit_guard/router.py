"""
Profit Guard console routes. Every path here lives under /hotels/{slug}/profit/
(Canon 13); public, device and webhook routes go on separate routers under
/public/, /device/ and /webhooks/ and are added to MODULE.routers.

─── Tax (02 spec 3.8, added by the Wave 1 integrator) ───
core.tax mounts no routes (its README): the owner-facing tax API belongs to
Profit Guard under its prefix. These four are thin wrappers over the core
functions so the settings Tax page (client settings/tax) works before the
Profit Guard lane builds the rest of this router. Each keeps the core
function's return shape unchanged.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import tax
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel

router = APIRouter(prefix="/hotels/{slug}/profit", tags=["profit_guard"])

VIEW = require_perm("profit.view")
MANAGE = require_perm("profit.manage")


# ─── Tax ───


@router.get("/tax/fee-rules")
def fee_rules(slug: str, on: date | None = Query(None), ctx: HotelCtx = Depends(VIEW)) -> dict:
    """{rules: core.tax.list_fee_rules(...)}; `on` adds applies_on per rule."""
    with db.db() as cur:
        return {"rules": tax.list_fee_rules(cur, slug, on=on)}


@router.get("/tax/profile")
def tax_profile(slug: str, on: date | None = Query(None), ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The profile in force on `on` (default: the hotel's business date)."""
    with db.db() as cur:
        day = on or htime.business_date(get_hotel(cur, slug))
        return tax.get_tax_profile(cur, slug, on=day)


class ProfileIn(BaseModel):
    effective_from: date
    property_category: str = Field(min_length=1, max_length=40)
    convention: Literal["A", "B", "C"] = "A"
    trn: str | None = Field(None, max_length=40)
    td_per_bedroom: bool | None = None


@router.put("/tax/profile")
def put_tax_profile(slug: str, body: ProfileIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return tax.set_tax_profile(cur, slug, **body.model_dump(), ctx=ctx)


class OverrideIn(BaseModel):
    fee_rule_id: int | None = None
    rule_key: str | None = Field(None, max_length=120)
    apply: bool
    effective_from: date | None = None
    effective_to: date | None = None
    note: str | None = Field(None, max_length=1000)


@router.post("/tax/overrides")
def post_override(slug: str, body: OverrideIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Both ids may be sent (the console does); the numeric id wins."""
    rule = body.fee_rule_id if body.fee_rule_id is not None else body.rule_key
    if rule is None:
        raise ClefsError(400, "fee_rule_id or rule_key is required", "rule_required")
    with db.db() as cur:
        return tax.set_override(cur, slug, rule, apply=body.apply, effective_from=body.effective_from,
                                effective_to=body.effective_to, note=body.note, ctx=ctx)
