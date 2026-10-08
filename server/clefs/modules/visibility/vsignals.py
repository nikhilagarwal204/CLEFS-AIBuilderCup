"""
Visibility signal kinds and their emitters (04 spec 5.11).

Registered at import (module.py imports this), so every code path that can
emit a kind has registered it. Cadences follow Canon 18: only the parity gap
is money at risk today (daily); the rest are weekly, attribution monthly.
The AI kinds (vis.ai_sov_change, vis.ai_fact_error) are registered by the AI
radar part of this lane next to its detectors.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from clefs.core import signals
from clefs.core.signals import Evidence, RecommendedAction, SignalIn

MODULE = "visibility"

# ─── Kinds ───

signals.register_signal_kind("vis.parity_gap_google", label="Google Hotels parity gap", default_owner_role="revenue",
                             section="money_available", cadence="daily")
signals.register_signal_kind("vis.official_price_missing", label="Official site price missing on Google",
                             default_owner_role="revenue", section="money_available", cadence="weekly")
signals.register_signal_kind("vis.rank_drop", label="Map rank dropped", default_owner_role="gm",
                             section="money_available", cadence="weekly")
signals.register_signal_kind("vis.lodging_overridden", label="Google overrode a hotel fact",
                             default_owner_role="gm", section="service_risk", cadence="weekly")
signals.register_signal_kind("vis.site_health_not_good", label="Website outside Google's 'good' thresholds",
                             default_owner_role="gm", section="service_risk", cadence="weekly")
signals.register_signal_kind("vis.jsonld_mismatch", label="Website structured data disagrees with the KB",
                             default_owner_role="gm", section="service_risk", cadence="weekly")
signals.register_signal_kind("vis.gbp_setup", label="Google Business Profile setup", default_owner_role="gm",
                             section="service_risk", cadence="weekly")
signals.register_signal_kind("vis.google_attribution", label="Google direct bookings", default_owner_role="gm",
                             section="money_available", cadence="monthly")


def _money(minor: int | None, ccy: str) -> str:
    from clefs.core.money import fmt_money

    return fmt_money(minor or 0, ccy, minor=True)


# ─── Google Hotels ───


def parity_gap(cur: Any, hotel: Any, ci: date, co: date, a: dict, *, exposure_minor: int, rooms: int,
               rooms_basis: str, channel: str | None, tolerance: Decimal) -> dict:
    from clefs.modules.visibility.google_hotels import DISCLOSURE, PARITY_LABEL, PRICE_ACCURACY_NOTE, q3

    ccy = hotel.currency
    pct = q3(a["parity_gap_pct"])
    src = a["min_other_source"] or "an OTA"
    return signals.upsert_signal(cur, hotel.slug, SignalIn(
        module=MODULE, kind="vis.parity_gap_google", dedupe_key=f"vis.parity_gap_google:{ci.isoformat()}",
        title=f"{src} undercuts your site on Google for {ci:%d %b} by {pct}%",
        summary=(f"Official {_money(a['official_price_minor'], ccy)} vs {src} "
                 f"{_money(a['min_other_price_minor'], ccy)} ({PARITY_LABEL}). {PRICE_ACCURACY_NOTE}"),
        severity=2 if pct is not None and pct < 5 else 3,
        impact_minor=exposure_minor, currency=ccy, impact_kind="revenue_upside", confidence=0.6,
        evidence=[
            Evidence(kind="calc", label="Gap", value=f"{_money(a['parity_gap_minor'], ccy)} ({pct}%), tolerance "
                     f"{tolerance}%"),
            Evidence(kind="calc", label="Exposure",
                     value=f"{_money(a['parity_gap_minor'], ccy)} x {rooms} rooms on the books ({rooms_basis})"),
            Evidence(kind="row", label="Google Hotels prices", value=a["prices"],
                     ref={"table": "vis_google_hotels_obs", "check_in": ci.isoformat()}),
            Evidence(kind="quote", label="Source", value=DISCLOSURE),
        ],
        recommended_action=RecommendedAction(
            type="visibility.parity_fix", label=f"Fix the {src} rate for {ci:%d %b}", owner_role="revenue",
            params={"check_in": ci.isoformat(), "check_out": co.isoformat(), "source": src, "channel": channel},
            due_in_hours=24),
        subject_type="stay_date", subject_id=ci.isoformat(),
        # A gap on a night that has passed is no longer money available.
        expires_at=datetime.combine(co, time(0), tzinfo=hotel.tz),
    ))


def official_price_missing(cur: Any, hotel: Any, stay_dates: list[str]) -> dict:
    from clefs.modules.visibility.google_hotels import PRICE_ACCURACY_NOTE

    return signals.upsert_signal(cur, hotel.slug, SignalIn(
        module=MODULE, kind="vis.official_price_missing", dedupe_key="vis.official_price_missing",
        title="Your own site's price is not shown on Google Hotels",
        summary=("Google Hotels lists OTA prices for your hotel but no official-site price, so guests "
                 f"comparing on Google cannot book you direct. {PRICE_ACCURACY_NOTE}"),
        severity=2, confidence=0.7,
        evidence=[Evidence(kind="row", label="Stay dates checked", value=stay_dates)],
        recommended_action=RecommendedAction(
            type="core.task", label="Connect free booking links in Hotel Center (see the readiness checklist)",
            owner_role="revenue", params={"link": "/demand/hotel-center"}),
    ))


# ─── GBP ───


def lodging_overridden(cur: Any, hotel_slug: str, over: list[dict], *, action_id: int | None = None) -> dict:
    return signals.upsert_signal(cur, hotel_slug, SignalIn(
        module=MODULE, kind="vis.lodging_overridden", dedupe_key="vis.lodging_overridden",
        title=f"Google changed {len(over)} hotel fact(s) back after your update",
        summary="Google's own sources disagree with your approved answers; check the listing and your website.",
        severity=2, confidence=0.9,
        evidence=[Evidence(kind="row", label="Overridden fields", value=over,
                           ref={"action_id": action_id} if action_id else None)],
        recommended_action=RecommendedAction(type="core.task", label="Check the Google listing's hotel details",
                                             owner_role="gm"),
    ))


def gbp_setup(cur: Any, hotel_slug: str, state: dict) -> dict | None:
    """A weekly nudge while the profile is connectable but not connected or
    no location is picked. Project approval is Clefs' job, not the hotel's,
    so project_not_approved raises nothing."""
    key = "vis.gbp_setup"
    if state["status"] not in ("not_connected", "pick_location"):
        signals.resolve_signal(cur, hotel_slug, key, resolution=state["status"])
        return None
    title = ("Connect your Google Business Profile" if state["status"] == "not_connected"
             else "Pick your Google Business Profile location")
    return signals.upsert_signal(cur, hotel_slug, SignalIn(
        module=MODULE, kind="vis.gbp_setup", dedupe_key=key, title=title, summary=state.get("message"),
        severity=1, confidence=1.0,
        recommended_action=RecommendedAction(type="core.task", label=title, owner_role="gm",
                                             params={"link": "/visibility/settings"}),
    ))


# ─── Site health ───


def site_health_not_good(cur: Any, hotel_slug: str, url: str, outside: list[dict]) -> dict | None:
    key = "vis.site_health_not_good"
    if not outside:
        signals.resolve_signal(cur, hotel_slug, key, resolution="good")
        return None
    names = ", ".join(o["label"] for o in outside)
    return signals.upsert_signal(cur, hotel_slug, SignalIn(
        module=MODULE, kind="vis.site_health_not_good", dedupe_key=key,
        title=f"Your website is outside Google's 'good' threshold for {names}",
        summary="Measured on phones by Chrome users (p75). This is site hygiene; no ranking effect is claimed.",
        severity=1, confidence=0.9,
        evidence=[Evidence(kind="metric", label=o["label"], value=o["value"], ref={"url": url}) for o in outside],
        recommended_action=RecommendedAction(type="core.task", label="Send the PageSpeed report to your web agency",
                                             owner_role="gm", params={"url": url}),
    ))


def jsonld_mismatch(cur: Any, hotel_slug: str, url: str, mismatches: list[dict]) -> dict | None:
    key = "vis.jsonld_mismatch"
    if not mismatches:
        signals.resolve_signal(cur, hotel_slug, key, resolution="matches")
        return None
    return signals.upsert_signal(cur, hotel_slug, SignalIn(
        module=MODULE, kind="vis.jsonld_mismatch", dedupe_key=key,
        title=f"Your website's structured data disagrees with your approved answers ({len(mismatches)})",
        severity=2, confidence=0.9,
        evidence=[Evidence(kind="row", label=m["fact_key"], value={"site": m["theirs"], "kb": m["ours"]},
                           ref={"url": m.get("source_url") or url}) for m in mismatches],
        recommended_action=RecommendedAction(type="visibility.site_fact_fix", label="Fix the website's hotel facts",
                                             owner_role="gm", params={"url": url,
                                                                      "facts": [m["fact_key"] for m in mismatches]}),
    ))


# ─── Attribution ───


def google_attribution(cur: Any, hotel_slug: str, row: dict, line: str) -> dict:
    return signals.upsert_signal(cur, hotel_slug, SignalIn(
        module=MODULE, kind="vis.google_attribution", dedupe_key=f"vis.google_attribution:{row['month']}",
        title=line, summary="Bookings that lost the tag are not counted. This is attribution, not ledger-verified "
                            "profit.",
        severity=1, confidence=0.6,
        evidence=[Evidence(kind="calc", label="Month", value={k: (str(v) if v is not None else None)
                                                               for k, v in row.items()})],
    ))
