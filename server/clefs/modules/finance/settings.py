"""
Finance settings (hotel_module_settings['finance'], validated by this model).

The ratio bands are assumed, configurable defaults (04 spec 6.5, UNVERIFIED:
the only research figure is a 2019 MENA labour share of about 27%). The
service-charge treatment is NOT here: Finance reads it from Profit Guard
(treat_sc_as_revenue), the single source (6.4).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from clefs.core.hotels import get_module_settings


class FinanceSettings(BaseModel):
    labour_band_min_pct: float = Field(20.0, ge=0, le=100)
    labour_band_max_pct: float = Field(45.0, ge=0, le=100)
    eww_band_min_pct: float = Field(3.0, ge=0, le=100)
    eww_band_max_pct: float = Field(12.0, ge=0, le=100)
    # primary anomaly trigger: z >= zscore_min with a move of at least
    # delta_min_pp percentage points against the trailing months
    zscore_min: float = Field(2.5, ge=1, le=10)
    delta_min_pp: float = Field(3.0, ge=0, le=100)
    trailing_months: int = Field(6, ge=3, le=24)
    # the market region used for the GOPPAR chip (fin_market_benchmarks.region)
    market_region: str | None = Field(None, max_length=60)

    @model_validator(mode="after")
    def _bands(self) -> "FinanceSettings":
        if self.labour_band_min_pct > self.labour_band_max_pct or self.eww_band_min_pct > self.eww_band_max_pct:
            raise ValueError("a band's minimum must not exceed its maximum")
        return self


def load(cur: Any, hotel_slug: str) -> FinanceSettings:
    return get_module_settings(cur, hotel_slug, "finance", FinanceSettings)
