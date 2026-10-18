"""Ask test helpers: a hermetic demo metric registry over a demo fact table,
a frozen clock, a HotelCtx per role, and fake-model helpers."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

# 2026-09-29 10:00 Asia/Dubai (a Tuesday) = 06:00 UTC
T0 = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)
TODAY = date(2026, 9, 29)

_F = "FROM ask_eval_facts f"
_CH_VALUES = "SELECT DISTINCT channel FROM ask_eval_facts WHERE hotel_slug = %(hotel)s AND channel IS NOT NULL ORDER BY 1"
_AM_VALUES = "SELECT DISTINCT amenity FROM ask_eval_facts WHERE hotel_slug = %(hotel)s AND amenity IS NOT NULL ORDER BY 1"
_FL_VALUES = "SELECT DISTINCT floor FROM ask_eval_facts WHERE hotel_slug = %(hotel)s AND floor IS NOT NULL ORDER BY 1"
_RT_VALUES = "SELECT DISTINCT room_type FROM ask_eval_facts WHERE hotel_slug = %(hotel)s AND room_type IS NOT NULL ORDER BY 1"
_DEPT_VALUES = ("SELECT DISTINCT department FROM ask_eval_facts WHERE hotel_slug = %(hotel)s "
                "AND department IS NOT NULL ORDER BY 1")


def _demo_metrics():
    from clefs.core.metrics import Dimension as D
    from clefs.core.metrics import Metric

    ch = D("channel", "Channel", "f.channel", _CH_VALUES)
    am = D("amenity", "Amenity", "f.amenity", _AM_VALUES)
    fl = D("floor", "Floor", "f.floor", _FL_VALUES)
    rt = D("room_type", "Room type", "f.room_type", _RT_VALUES)
    dept = D("department", "Department", "f.department", _DEPT_VALUES)
    seg = D("segment", "Segment", "f.segment")
    guest = D("guest_name", "Guest", "f.guest_name")  # must never reach the model

    def m(key, label, unit, dims, *, perm="core.metrics.view", measure="COALESCE(SUM(f.value), 0)", money=None,
          syn=(), desc=None, hib=True):
        return Metric(key=key, label=label, description=desc or label, unit=unit, source=_F,
                      hotel_column="f.hotel_slug", time_column="f.day", time_kind="date", measure=measure,
                      where=f"f.metric = '{key}'", dimensions=tuple(dims), permission=perm,
                      currency="hotel" if (money if money is not None else unit == "money") else None,
                      synonyms=tuple(syn), higher_is_better=hib)

    fin = "core.ingest.manage"  # owner, gm, finance, revenue (not dept_head)
    return [
        m("pg.commission_aed", "OTA commission paid", "money", [ch], perm=fin, syn=("commission", "OTA fees"), hib=False),
        m("pg.commission_on_tax_aed", "OTA commission charged on taxes", "money", [ch], perm=fin,
          syn=("commission on taxes", "commission on VAT", "tax commission"), hib=False),
        m("pg.net_revenue_aed", "Net room revenue", "money", [ch, rt], perm=fin, syn=("net revenue",)),
        m("pg.net_adr_aed", "Net ADR", "money", [ch, rt], perm=fin, syn=("net average daily rate",),
          measure="COALESCE(SUM(f.value), 0)::NUMERIC / NULLIF(SUM(f.qty), 0)"),
        m("pg.vcc_expiring_aed", "Virtual cards expiring", "money", [ch], perm=fin,
          syn=("VCCs expiring", "virtual credit cards")),
        m("pg.findings_open", "Open money leaks", "count", [ch], perm=fin, syn=("leaks", "findings")),
        m("am.leakage_aed", "Amenity leakage", "money", [am, fl], syn=("amenity leak", "leaked"), hib=False),
        m("am.issued_units", "Amenities issued", "count", [am, fl, dept], syn=("amenities given",)),
        m("stays.room_nights", "Room nights", "nights", [ch, rt, seg], syn=("rooms sold",)),
        m("stays.room_revenue", "Room revenue", "money", [ch, rt, seg], perm=fin, syn=("rooms revenue",)),
        m("stays.bookings", "Bookings", "count", [ch, guest], syn=("reservations",)),
        m("service.requests", "Guest requests", "count", [dept, fl], syn=("requests", "tickets")),
        m("service.sla_breaches", "SLA breaches", "count", [dept], syn=("late requests",), hib=False),
        m("reviews.count", "Reviews", "count", [ch], syn=("reviews received",)),
        m("reviews.avg_rating", "Average review rating", "rate", [ch], syn=("rating", "review score"),
          measure="AVG(f.value)::NUMERIC"),
        m("inq.inquiries_count", "Inquiries", "count", [ch], syn=("leads", "enquiries")),
        m("upsell.order_revenue", "In-stay order revenue", "money", [dept], syn=("F&B orders", "room service revenue")),
        m("dr.comp_median_rate_aed", "Competitor median rate", "money", [rt], syn=("competitor rates", "comp set rate")),
        m("fin.gop_aed", "Gross operating profit", "money", [], perm="core.settings.manage", syn=("GOP", "profit")),
    ]


@pytest.fixture
def frozen(clock):
    clock.freeze(T0)
    return clock


@pytest.fixture
def demo(cur, hotel_uae, frozen):
    """The demo registry (hermetic: other lanes' metrics are hidden for the
    test and restored after) over a TEMP fact table in the test transaction."""
    from clefs.core import metrics
    from clefs.modules.ask import values

    cur.execute(
        """
        CREATE TEMP TABLE ask_eval_facts (hotel_slug TEXT, metric TEXT, day DATE, channel TEXT, amenity TEXT,
            floor TEXT, room_type TEXT, department TEXT, segment TEXT, guest_name TEXT, value BIGINT, qty BIGINT)
        """
    )
    with metrics._lock:
        saved = (dict(metrics._metrics), dict(metrics._origins))
        metrics._metrics.clear()
        metrics._origins.clear()
    for mt in _demo_metrics():
        metrics.register_metric(mt)
    values.clear()
    yield Facts(cur, hotel_uae.slug)
    with metrics._lock:
        metrics._metrics.clear()
        metrics._origins.clear()
        metrics._metrics.update(saved[0])
        metrics._origins.update(saved[1])
    values.clear()


class Facts:
    def __init__(self, cur, slug):
        self.cur, self.slug = cur, slug

    def add(self, metric: str, day: date | str, value: int, **dims):
        d = day if isinstance(day, date) else date.fromisoformat(day)
        qty = dims.pop("qty", None)
        cols = ["hotel_slug", "metric", "day", "value", "qty", *dims]
        self.cur.execute(
            f"INSERT INTO ask_eval_facts ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
            (self.slug, metric, d, value, qty, *dims.values()),
        )


@pytest.fixture
def ctx_for(cur, hotel_uae, make_user):
    """ctx_for(role) -> (HotelCtx, user) for a fresh member of hotel_uae."""
    from clefs.auth.deps import Principal, resolve_ctx

    def _ctx(role: str = "owner", **kw):
        u = make_user(role, hotel_uae.slug, **kw)
        return resolve_ctx(Principal(kind="user", user_id=u["id"], session_kind="web"), hotel_uae.slug, cur=cur), u

    return _ctx


def call(name: str, **arguments):
    return {"name": name, "arguments": arguments}


def q(metric: str, *, dims=(), filters=(), preset="mtd", frm=None, to=None, grain="total", compare="none",
      sort=None, limit=20):
    """query_metric arguments exactly as the strict schema has the model write them."""
    return {
        "metric": metric, "dimensions": list(dims),
        "filters": [{"field": f, "op": op, "value": v if isinstance(v, list) else [v]} for f, op, v in filters],
        "period": {"preset": None if frm else preset, "from": frm, "to": to},
        "grain": grain, "compare_to": compare, "sort": sort or ("time_asc" if grain != "total" else "value_desc"),
        "limit": limit,
    }


def grounded_answer(req):
    """A fake answer model that writes ONLY numbers from the results it got."""
    body = json.loads(req.input)
    r = body["results"][0]
    total = r.get("total")
    first = r["rows"][0] if r["rows"] else None
    if total is None and first is not None:
        total = first["value"]
    if total is None:
        return {"answer_text": f"No {r['metric']} was recorded for {r['period']}."}
    txt = f"{r['metric']} for {r['period']} was {total:,.2f}"
    if first is not None and r["dimensions"]:
        name = " / ".join(str(first.get(d)) for d in r["dimensions"])
        txt += f"; {name} led with {first['value']:,.2f}"
    if r.get("change_pct") is not None:
        txt += f", a change of {r['change_pct']:.1f}%"
    return {"answer_text": txt + "."}


def seed_standard(f: Facts):
    """A small demo month: Aug and Sep 2026, and Sep 2025 for year-on-year."""
    for ch, v in (("BOOKING", 1_020_000), ("EXPEDIA", 450_000), ("DIRECT", 0)):
        f.add("pg.commission_aed", "2026-08-10", v, channel=ch)
    for ch, v in (("BOOKING", 38_500), ("EXPEDIA", 12_000)):
        f.add("pg.commission_on_tax_aed", "2026-08-12", v, channel=ch)
    for ch, rev, n in (("BOOKING", 4_200_000, 60), ("EXPEDIA", 2_100_000, 35), ("DIRECT", 3_300_000, 40)):
        f.add("pg.net_adr_aed", "2026-08-15", rev, channel=ch, qty=n, room_type="DLX")
        f.add("stays.room_revenue", "2026-08-15", rev, channel=ch, room_type="DLX")
        f.add("stays.room_nights", "2026-08-15", n, channel=ch, room_type="DLX")
    for d, am, fl, v in (("2026-09-28", "Water bottle", "1", 12_000), ("2026-09-29", "Slippers", "2", 30_000),
                         ("2026-09-29", "Water bottle", "5", 5_000)):
        f.add("am.leakage_aed", d, v, amenity=am, floor=fl)
    for d, n in (("2026-09-10", 250), ("2026-09-20", 260), ("2025-09-10", 200), ("2025-09-20", 210)):
        f.add("stays.room_nights", d, n, channel="BOOKING", room_type="DLX")
    for d, v in (("2026-10-05", 250_000), ("2026-10-09", 180_000), ("2026-11-20", 999_000)):
        f.add("pg.vcc_expiring_aed", d, v, channel="EXPEDIA")
    f.add("service.requests", "2026-09-29", 7, department="Housekeeping", floor="2")
    f.add("service.requests", "2026-09-28", 3, department="Engineering", floor="1")
    f.add("fin.gop_aed", "2026-08-31", 12_345_600)


@pytest.fixture
def seeded(demo):
    seed_standard(demo)
    return demo


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)
