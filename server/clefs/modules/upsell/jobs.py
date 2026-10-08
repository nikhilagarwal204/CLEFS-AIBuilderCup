"""
Upsell registrations: the stuck-order signal and its detector, and the PII
registry entries Trust's retention and DSAR engines read. Imported by
module.py, so jobs.load_all() and create_app() both see them.

Why a stuck-order signal: an order the kitchen accepted but never delivered
is a guest waiting with an empty stomach, and a bill they will dispute. The
linked service request carries the SLA; this signal is the owner's view of
orders nobody moved along (service_risk, never money, so it does not rank
against revenue items it is not).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs import jobs
from clefs.core import signals
from clefs.core.money import money_str
from clefs.core.privacy import register_pii

SIGNAL_STUCK = "upsell.order_stuck"
STUCK_AFTER_MIN = 90


# ─── Personal data ───
# Order notes are guest free text ("no nuts, allergic"): health data under
# PDPL can hide in them, so they are redacted after two years; the order
# totals stay for revenue history. Quotes that never became orders carry a
# room and a basket and nothing else of value: deleted after 30 days.

register_pii("upsell", "ups_orders", subject_columns={"room": "room", "notes": "free_text"},
             ts_column="created_at", retention_days=730, strategy="redact", redact_columns=("notes",))
register_pii("upsell", "ups_order_items", subject_columns={"notes": "free_text"},
             ts_column="created_at", retention_days=730, strategy="redact", redact_columns=("notes",))
register_pii("upsell", "ups_quotes", subject_columns={"room": "room"}, ts_column="created_at",
             retention_days=30, strategy="delete", where="order_id IS NULL")


# ─── Stuck orders ───

signals.register_signal_kind(
    SIGNAL_STUCK,
    label="Guest order not delivered",
    default_owner_role="dept_head",
    section="service_risk",
    cadence="daily",
    icon="utensils",
)


def detect_stuck(cur: Any, hotel: Any, now: Any) -> dict:
    """One signal per open order older than STUCK_AFTER_MIN; orders that moved
    on resolve theirs (resolve_missing)."""
    cur.execute(
        """
        SELECT o.id, o.room, o.status, o.total_minor, o.currency, o.created_at, ol.name_en AS outlet
        FROM ups_orders o LEFT JOIN ups_outlets ol ON ol.id = o.outlet_id
        WHERE o.hotel_slug = %s AND o.status IN ('placed', 'accepted', 'preparing') AND o.created_at < %s
        ORDER BY o.created_at LIMIT 200
        """,
        (hotel.slug, now - timedelta(minutes=STUCK_AFTER_MIN)),
    )
    seen: list[str] = []
    for o in cur.fetchall():
        key = f"{SIGNAL_STUCK}:{o['id']}"
        seen.append(key)
        age = int((now - o["created_at"]).total_seconds() // 60)
        code = o["currency"].strip()
        signals.upsert_signal(cur, hotel.slug, signals.SignalIn(
            module="upsell", kind=SIGNAL_STUCK, dedupe_key=key,
            title=f"Room {o['room']} order #{o['id']} still {o['status']} after {age} min",
            summary=f"{o['outlet'] or 'In-room dining'}, {code} {money_str(o['total_minor'], code)}. "
                    "Check with the kitchen and update the order in Menu > Orders.",
            severity=3, confidence=1.0, subject_type="ups_order", subject_id=str(o["id"]),
            evidence=[{"kind": "row", "label": "Order", "value": f"#{o['id']}", "ref": {"order_id": o["id"]}}],
            owner_role="dept_head",
        ))
    resolved = signals.resolve_missing(cur, hotel.slug, "upsell", SIGNAL_STUCK, seen)
    return {"stuck": len(seen), "resolved": resolved}


@jobs.scheduled("upsell.stuck_orders", jobs.every(minutes=15))
def _stuck_orders(ctx: jobs.JobCtx) -> dict:
    return detect_stuck(ctx.cur, ctx.hotel, ctx.now)
