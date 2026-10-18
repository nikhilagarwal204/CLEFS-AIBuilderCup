"""
Finance signal kinds and their emitters (04 spec 6.8, cadence 7.3).

| kind | section | cadence |
| fin.nrevpar_weekly | money_available | weekly |
| fin.pl_pulse_ready | verified | monthly |
| fin.mapping_needed | service_risk | monthly |
| fin.anomaly | money_lost | monthly (top 3 by |delta| x confidence) |
| fin.booked_net | compliance | monthly |
"""

from __future__ import annotations

from typing import Any

from clefs.core import signals
from clefs.core.signals import Evidence, RecommendedAction, SignalIn
from clefs.modules.finance import calc

signals.register_signal_kind("fin.nrevpar_weekly", label="Weekly NRevPAR", default_owner_role="owner",
                             section="money_available", cadence="weekly", icon="trend")
signals.register_signal_kind("fin.pl_pulse_ready", label="Monthly P&L pulse ready", default_owner_role="owner",
                             section="verified", cadence="monthly", icon="report")
signals.register_signal_kind("fin.mapping_needed", label="P&L accounts need mapping", default_owner_role="finance",
                             section="service_risk", cadence="monthly", icon="list")
signals.register_signal_kind("fin.anomaly", label="P&L anomaly", default_owner_role="gm",
                             section="money_lost", cadence="monthly", icon="alert")
signals.register_signal_kind("fin.booked_net", label="OTA revenue booked net", default_owner_role="finance",
                             section="compliance", cadence="monthly", icon="alert")


def _month_label(d: Any) -> str:
    return d.strftime("%B %Y")


# ─── Weekly ───


def nrevpar_weekly(cur: Any, hotel_slug: str, p: dict) -> dict | None:
    """One pulse signal per week, only when the week has a NRevPAR."""
    if p.get("nrevpar_minor") is None:
        return None
    ccy = p["currency"]
    ota = p.get("ota_cost_minor")
    return signals.upsert_signal(cur, hotel_slug, SignalIn(
        module="finance", kind="fin.nrevpar_weekly", dedupe_key=f"fin.nrevpar_weekly:{p['week_start'].isoformat()}",
        title=f"NRevPAR {ccy} {calc.money_or_none(p['nrevpar_minor'], ccy)} last week",
        summary=(f"RevPAR {calc.money_or_none(p.get('revpar_minor'), ccy)}; OTA cost per occupied room "
                 f"{calc.money_or_none(p.get('ota_cost_por_minor'), ccy) or 'n/a'}"),
        severity=1, impact_minor=ota or None, currency=ccy if ota else None, impact_kind="cost_leak",
        confidence=0.9,
        evidence=[Evidence(kind="metric", label="NRevPAR", value=calc.money_or_none(p["nrevpar_minor"], ccy),
                           ref={"metric": "fin.nrevpar", "week_start": p["week_start"].isoformat()})],
    ))


# ─── Monthly ───


def mapping_needed(cur: Any, hotel_slug: str, batch: dict, unmapped: int) -> None:
    key = f"fin.mapping_needed:{batch['period_month'].isoformat()}"
    if unmapped <= 0 or batch["status"] != "mapping_required":
        signals.resolve_signal(cur, hotel_slug, key, resolution="mapped")
        return
    signals.upsert_signal(cur, hotel_slug, SignalIn(
        module="finance", kind="fin.mapping_needed", dedupe_key=key,
        title=f"{unmapped} P&L account(s) to confirm for {_month_label(batch['period_month'])}",
        summary="Confirm the USALI mapping so the month can be checked and published.",
        severity=2, confidence=1.0, subject_type="fin_import_batch", subject_id=str(batch["id"]),
        recommended_action=RecommendedAction(type="core.task", label="Confirm the P&L mapping",
                                             params={"path": "finance/mapping"}, owner_role="finance"),
    ))


def pl_pulse_ready(cur: Any, hotel_slug: str, month: Any, summary_row: dict) -> dict:
    ccy = summary_row["currency"]
    return signals.upsert_signal(cur, hotel_slug, SignalIn(
        module="finance", kind="fin.pl_pulse_ready", dedupe_key=f"fin.pl_pulse_ready:{month.isoformat()}",
        title=f"P&L pulse for {_month_label(month)} is ready",
        summary=f"GOP {ccy} {calc.money_or_none(summary_row.get('gop_minor'), ccy)}",
        severity=1, confidence=1.0,
        evidence=[Evidence(kind="metric", label="GOP", value=calc.money_or_none(summary_row.get("gop_minor"), ccy),
                           ref={"metric": "fin.gop", "month": month.isoformat()})],
    ))


def booked_net(cur: Any, hotel_slug: str, month: Any, data: dict, currency: str) -> dict:
    return signals.upsert_signal(cur, hotel_slug, SignalIn(
        module="finance", kind="fin.booked_net", dedupe_key=f"fin.booked_net:{month.isoformat()}",
        title=f"OTA revenue looks booked net in {_month_label(month)}",
        summary=("Rooms revenue is below the PMS by about the expected OTA commission, and no commission line is "
                 "booked. The pulse shows a normalised view; GOP is unchanged."),
        severity=2, confidence=0.8, impact_minor=int(data.get("expected_commission_minor") or 0) or None,
        currency=currency if data.get("expected_commission_minor") else None, impact_kind="risk",
        evidence=[Evidence(kind="calc", label=k, value=str(v)) for k, v in data.items() if v is not None],
    ))


def anomaly(cur: Any, hotel_slug: str, a: dict) -> dict:
    return signals.upsert_signal(cur, hotel_slug, SignalIn(
        module="finance", kind="fin.anomaly", dedupe_key=f"fin.anomaly:{a['fingerprint']}",
        title=a["title"], summary=a["detail"], severity=3 if abs(a.get("delta_minor") or 0) else 2,
        impact_minor=abs(int(a["delta_minor"])) if a.get("delta_minor") else None,
        currency=a["currency"] if a.get("delta_minor") else None, impact_kind="cost_leak",
        confidence=float(a.get("confidence") or 0.5), subject_type="fin_anomaly", subject_id=str(a["id"]),
        evidence=[Evidence(kind="calc", label=k, value=str(v)) for k, v in (a.get("evidence") or {}).items()],
        recommended_action=RecommendedAction(type="finance.investigate_anomaly", label="Investigate",
                                             params={"anomaly_id": a["id"]}, owner_role="gm"),
    ))
