"""
Inquiry Guard per-hotel settings (hotel_module_settings['inquiries']).

Read with core.hotels.get_module_settings(cur, slug, "inquiries",
InquirySettings); invalid stored data falls back to these defaults.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from clefs.modules.inquiries.models import DEFAULT_SLA_MINUTES


class InquirySettings(BaseModel):
    # First-response minutes per segment, plus "rfp" (4.5).
    sla: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_SLA_MINUTES))
    # Business hours for the SLA clock, hotel-local, every day (A3).
    business_open: str = "08:00"
    business_close: str = "22:00"
    # 4.4: false until counsel signs off. Only express_web_form inquiries
    # (which collected transfer_abroad) reach the model while it is false.
    ai_on_unconsented_inbound: bool = False
    # The fixed acknowledgement (4.3); {privacy_url} in its text.
    ack_enabled: bool = True
    privacy_url: str | None = None
    # Web form ceilings (4.3).
    web_form_per_ip_hour: int = 5
    web_form_per_hotel_day: int = 200
    web_form_min_fill_s: int = 3
    # Quote policy (quotes.QuoteSettings), kept raw here so PUT /settings and
    # the core module-settings door keep the key; quotes.quote_settings
    # validates it on read and PUT /quote-settings edits it (integrator fix:
    # typing it here would import quotes.py into every settings read).
    quotes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sla")
    @classmethod
    def _sla(cls, v: dict[str, int]) -> dict[str, int]:
        out = dict(DEFAULT_SLA_MINUTES)
        for k, minutes in (v or {}).items():
            if k not in DEFAULT_SLA_MINUTES:
                raise ValueError(f"unknown SLA segment {k!r}")
            if not 1 <= int(minutes) <= 10080:
                raise ValueError("SLA minutes must be between 1 and 10080")
            out[k] = int(minutes)
        return out

    @field_validator("business_open", "business_close")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        hh, _, mm = (v or "").partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) <= 24 and 0 <= int(mm) < 60):
            raise ValueError("use HH:MM")
        return f"{int(hh):02d}:{int(mm):02d}"
