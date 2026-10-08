"""
Per-hotel Service Control settings (hotel_module_settings['service'],
validated by this model; GET/PUT /hotels/{slug}/module-settings/service).

Every field has a safe default so a hotel that never opened the settings
page gets the spec's behaviour: shadow-mode escalations for the first 7 days,
the 5-per-room guest rate limit, the 10-minute merge window, translation and
the safety LLM on.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from clefs.core import clock
from clefs.core.hotels import get_module_settings


class ServiceSettings(BaseModel):
    # auto: shadow for `shadow_days` after the hotel's first sweep, then live.
    # shadow: always computed and shown, nothing external sent (calibration).
    # live: WhatsApp / SMS / email escalations go out.
    escalation_mode: Literal["auto", "shadow", "live"] = "auto"
    shadow_days: int = Field(7, ge=0, le=60)
    # Guest-channel requests per room per 10 minutes (03 spec 1.3 step 3).
    guest_room_limit: int = Field(5, ge=1, le=100)
    # Same room + request type open within this many minutes is merged.
    merge_window_min: int = Field(10, ge=0, le=60)
    translate: bool = True
    translate_timeout_s: float = Field(2.0, gt=0, le=10)
    # Layer 2 of the safety classifier (upgrade-only LLM).
    safety_llm: bool = True
    safety_llm_timeout_s: float = Field(1.5, gt=0, le=10)
    # Shown on the EmergencyCard next to the public numbers.
    security_extension: str | None = Field(None, max_length=20)
    # A department queue deeper than this for 10 minutes is "understaffed".
    understaffed_queue_depth: int = Field(6, ge=1, le=100)
    # Shared devices log a PIN session out after this long without activity.
    idle_logout_min: int = Field(10, ge=1, le=240)
    # device_sessions end at planned_end_at + this grace.
    shift_grace_min: int = Field(15, ge=0, le=120)
    # AC requests become high priority and L1 above this outdoor temperature.
    heat_threshold_c: float = Field(40.0, ge=20, le=60)
    # Value of the gesture the system proposes when a guest says "not
    # resolved" (minor units of the hotel currency; 0 = propose nothing).
    gesture_default_minor: int = Field(0, ge=0)
    gesture_default_kind: str = "amenity"


def load(cur: Any, hotel_slug: str) -> ServiceSettings:
    return get_module_settings(cur, hotel_slug, "service", ServiceSettings)


def shadow_until(cur: Any, hotel_slug: str, s: ServiceSettings | None = None) -> datetime | None:
    """When auto mode goes live (None when the hotel was never swept yet)."""
    s = s or load(cur, hotel_slug)
    cur.execute("SELECT shadow_started_at FROM svc_sweep_state WHERE hotel_slug = %s", (hotel_slug,))
    row = cur.fetchone()
    if row is None:
        return None
    return row["shadow_started_at"] + timedelta(days=s.shadow_days)


def escalation_live(cur: Any, hotel_slug: str, s: ServiceSettings | None = None) -> bool:
    """True when escalations may leave the building (WhatsApp, SMS, email).
    In shadow mode the sweep still computes, records and shows them."""
    s = s or load(cur, hotel_slug)
    if s.escalation_mode == "live":
        return True
    if s.escalation_mode == "shadow":
        return False
    until = shadow_until(cur, hotel_slug, s)
    return until is not None and clock.now() >= until
