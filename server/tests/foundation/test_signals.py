"""
Signals (Canon 6): dedupe upsert, resolve_missing, reopen rules, sections and
cadence from registered kinds, listing order, and the GET route's access.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from clefs.core import signals
from clefs.core.errors import ClefsError
from clefs.core.signals import Evidence, RecommendedAction, SignalIn


def _sig(**kw) -> SignalIn:
    base = dict(module="core", kind="core.test_kind", dedupe_key="k1", title="Leak", confidence=0.8, section="money_lost",
                cadence="daily")
    base.update(kw)
    return SignalIn(**base)


def _count(cur, slug: str) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM signals WHERE hotel_slug = %s", (slug,))
    return cur.fetchone()["n"]


@pytest.fixture
def test_kind():
    k = signals.register_signal_kind(
        "core.test_registered", label="Test", default_owner_role="finance", section="compliance", cadence="weekly"
    )
    yield k
    with signals._lock:
        signals._kinds.pop("core.test_registered", None)


def test_upsert_dedupes_and_refreshes(cur, hotel_uae):
    a = signals.upsert_signal(
        cur,
        hotel_uae.slug,
        _sig(
            impact_amount=Decimal("100.00"),
            currency="aed",
            evidence=[Evidence(kind="row", label="Line 7", ref={"table": "x", "id": 7})],
            recommended_action=RecommendedAction(type="core.task", label="Check it"),
        ),
    )
    assert a["created"] and not a["reopened"]
    assert a["impact_minor"] == 10000 and a["impact_amount"] == "100.00" and a["currency"] == "AED"
    assert a["evidence"][0]["ref"] == {"table": "x", "id": 7} and a["recommended_action"]["type"] == "core.task"
    b = signals.upsert_signal(cur, hotel_uae.slug, _sig(title="Leak (updated)", impact_minor=12000, currency="AED"))
    assert not b["created"] and b["id"] == a["id"]
    assert b["occurrences"] == 2 and b["title"] == "Leak (updated)" and b["impact_amount"] == "120.00"
    assert _count(cur, hotel_uae.slug) == 1


def test_signal_money_needs_a_currency_and_agreement():
    with pytest.raises(ValidationError):
        _sig(impact_minor=100)
    with pytest.raises(ValidationError):
        _sig(impact_minor=100, impact_amount=Decimal("2.00"), currency="AED")
    assert _sig(impact_minor=200, impact_amount=Decimal("2.00"), currency="AED").minor() == 200


def test_section_and_cadence_default_from_the_registered_kind(cur, hotel_uae, test_kind):
    s = signals.upsert_signal(cur, hotel_uae.slug, _sig(kind="core.test_registered", section=None, cadence=None))
    assert s["section"] == "compliance" and s["cadence"] == "weekly" and s["owner_role"] == "finance"
    assert s["rankable"] is False  # compliance never ranks into the daily top 5
    daily = signals.upsert_signal(cur, hotel_uae.slug, _sig(kind="core.test_registered", section=None, cadence="daily", dedupe_key="k2"))
    assert daily["cadence"] == "daily"
    with pytest.raises(ClefsError) as exc:
        signals.upsert_signal(cur, hotel_uae.slug, _sig(kind="core.unregistered_kind", section=None, cadence=None, dedupe_key="k3"))
    assert exc.value.code == "unknown_signal_kind"


def test_register_signal_kind_validates_and_protects_ownership(test_kind):
    with pytest.raises(ValueError):
        signals.register_signal_kind("nodot", label="x", default_owner_role="gm", section="money_lost", cadence="daily")
    with pytest.raises(ValueError):
        signals.register_signal_kind("core.x", label="x", default_owner_role="gm", section="nope", cadence="daily")  # type: ignore[arg-type]
    assert signals.get_kind("core.test_registered").module == "core"


def test_resolve_missing_closes_only_what_the_run_did_not_see(cur, hotel_uae):
    for key in ("k1", "k2", "k3"):
        signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key=key))
    signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key="other", kind="core.other_kind"))
    assert signals.resolve_missing(cur, hotel_uae.slug, "core", "core.test_kind", {"k1"}) == 2
    cur.execute("SELECT dedupe_key, status, resolution FROM signals WHERE hotel_slug = %s ORDER BY dedupe_key", (hotel_uae.slug,))
    rows = {r["dedupe_key"]: (r["status"], r["resolution"]) for r in cur.fetchall()}
    assert rows == {"k1": ("open", None), "k2": ("resolved", "not_seen"), "k3": ("resolved", "not_seen"), "other": ("open", None)}
    back = signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key="k2"))
    assert back["reopened"] and back["status"] == "open" and back["resolved_at"] is None
    assert signals.resolve_missing(cur, hotel_uae.slug, "core", "core.test_kind", set()) == 2


def test_a_person_closed_signal_reopens_only_when_the_impact_grows(cur, hotel_uae):
    s = signals.upsert_signal(cur, hotel_uae.slug, _sig(impact_minor=10000, currency="AED"))
    signals.set_status(cur, s["id"], "ignored", resolution="not worth it")
    again = signals.upsert_signal(cur, hotel_uae.slug, _sig(impact_minor=12000, currency="AED"))
    assert not again["reopened"] and again["status"] == "ignored"
    grown = signals.upsert_signal(cur, hotel_uae.slug, _sig(impact_minor=12500, currency="AED"))
    assert grown["reopened"] and grown["status"] == "open"
    custom = signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key="c", impact_minor=100, currency="AED"))
    signals.set_status(cur, custom["id"], "decided")
    assert signals.upsert_signal(
        cur, hotel_uae.slug, _sig(dedupe_key="c", impact_minor=150, currency="AED"), reopen_if_impact_grows_pct=50
    )["reopened"]


def test_resolve_signal(cur, hotel_uae):
    signals.upsert_signal(cur, hotel_uae.slug, _sig())
    assert signals.resolve_signal(cur, hotel_uae.slug, "k1", resolution="fixed")
    assert not signals.resolve_signal(cur, hotel_uae.slug, "k1", resolution="again")
    assert not signals.resolve_signal(cur, hotel_uae.slug, "missing", resolution="x")


def test_list_order_filters_and_mark_surfaced(cur, hotel_uae, clock):
    now = clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    late = signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key="late", due_at=now + timedelta(days=3)))
    soon = signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key="soon", due_at=now + timedelta(hours=2)))
    big = signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key="big", impact_minor=500000, currency="AED"))
    small = signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key="small", impact_minor=100, currency="AED"))
    comp = signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key="comp", section="compliance"))
    ids = [s["id"] for s in signals.list_signals(cur, hotel_uae.slug)]
    assert ids == [soon["id"], late["id"], big["id"], small["id"], comp["id"]]
    assert [s["id"] for s in signals.list_signals(cur, hotel_uae.slug, sections=["compliance"])] == [comp["id"]]
    signals.mark_surfaced(cur, [soon["id"], late["id"]])
    listed = {s["id"]: s["status"] for s in signals.list_signals(cur, hotel_uae.slug)}
    assert listed[soon["id"]] == "surfaced" and listed[big["id"]] == "open"
    assert [s["id"] for s in signals.list_signals(cur, hotel_uae.slug, status=("surfaced",))] == [soon["id"], late["id"]]


def test_expiry_and_snooze_wake(cur, hotel_uae, clock):
    now = clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    exp = signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key="exp", expires_at=now + timedelta(hours=1)))
    snz = signals.upsert_signal(cur, hotel_uae.slug, _sig(dedupe_key="snz"))
    signals.set_status(cur, snz["id"], "snoozed", snoozed_until=now + timedelta(hours=1))
    clock.advance(hours=2)
    assert signals.expire_due(cur, hotel_slug=hotel_uae.slug) == 2
    assert signals.get_signal(cur, hotel_uae.slug, exp["id"])["status"] == "expired"
    assert signals.get_signal(cur, hotel_uae.slug, snz["id"])["status"] == "open"


def test_signals_route_needs_the_view_permission(cur, client, client_as, hotel_uae):
    signals.upsert_signal(cur, hotel_uae.slug, _sig(impact_minor=4200, currency="AED"))
    owner = client_as("owner")
    r = owner.get(f"/hotels/{hotel_uae.slug}/signals")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1 and body[0]["impact_amount"] == "42.00" and body[0]["action_id"] is None
    assert owner.get(f"/hotels/{hotel_uae.slug}/signals?section=compliance").json() == []
    assert owner.get(f"/hotels/{hotel_uae.slug}/signals?status=bogus").status_code == 400
    assert client_as("staff").get(f"/hotels/{hotel_uae.slug}/signals").status_code == 403
    assert client.get(f"/hotels/{hotel_uae.slug}/signals").status_code == 401


def test_legacy_open_access_never_reaches_signals(client, legacy_slug):
    assert client.get(f"/hotels/{legacy_slug}/signals").status_code == 401
