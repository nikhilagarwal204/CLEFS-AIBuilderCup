"""
The concierge.live switch and its readiness gate (03 spec 5.4): switching
ON needs trust.api.readiness() ready (or its owner override); while off, no
guest conversation starts; pre-rebuild hotels are grandfathered.
"""

from __future__ import annotations

import pytest

from tests.modules.concierge.conftest import switch_live

GRANTS = {"ai_processing": True, "transfer_abroad": True, "recording": True}


@pytest.fixture
def gate(monkeypatch):
    """trust.api.readiness stand-in (Trust's tables are not migrated in the
    scoped run); set gate["ready"] / gate["overridden"] per test."""
    from clefs.modules.trust import api as trust_api

    state = {"ready": False, "overridden": False,
             "checks": [{"key": "safety", "ok": False}, {"key": "routing", "ok": True}]}
    monkeypatch.setattr(trust_api, "readiness", lambda cur, slug: dict(state))
    return state


@pytest.fixture
def not_live(cur, hotel_uae):
    switch_live(cur, hotel_uae.slug, None)
    return hotel_uae


@pytest.fixture
def minted(monkeypatch):
    from clefs.modules.concierge import sessions

    sessions.set_mint_transport(lambda body: {"value": "ek_live_gate", "expires_at": 0})
    yield
    sessions.set_mint_transport(None)


def _mint(client, slug, room="101"):
    return client.post("/public/concierge/sessions",
                       json={"hotel": slug, "target": {"room": room}, "lang": "en", "mode": "voice", "consents": GRANTS})


# ─── Default and grandfathering ───


def test_new_hotel_starts_not_live_and_mint_is_refused(client, not_live, minted):
    r = _mint(client, not_live.slug)
    assert r.status_code == 409 and r.json()["code"] == "concierge_not_live"


def test_new_hotel_default_is_not_live(cur, not_live):
    from clefs.modules.concierge import live

    assert live.live_state(cur, not_live.slug) == {"live": False, "source": "default"}


def test_pre_rebuild_hotel_is_grandfathered_and_survives_lockdown(cur, legacy_slug):
    """Migration 0204 captured every legacy_open_access hotel as live. A
    later dashboard lockdown (legacy_open_access FALSE) must not take the
    guest concierge offline; an explicit OFF does."""
    from clefs.core import hotels
    from clefs.modules.concierge import live

    assert live.live_state(cur, legacy_slug) == {"live": True, "source": "grandfathered"}
    cur.execute("UPDATE hotels SET legacy_open_access = FALSE WHERE slug = %s", (legacy_slug,))
    hotels.invalidate(legacy_slug)
    assert live.is_live(cur, legacy_slug) is True
    switch_live(cur, legacy_slug, False)
    assert live.is_live(cur, legacy_slug) is False, "an explicit OFF beats grandfathering"
    hotels.invalidate(legacy_slug)


# ─── The switch ───


def test_switch_on_is_blocked_until_ready(client_as, not_live, gate, minted):
    client = client_as("owner")
    r = client.put(f"/hotels/{not_live.slug}/concierge/live", json={"live": True})
    assert r.status_code == 409 and r.json()["code"] == "readiness_not_met"
    assert "safety" in r.json()["detail"] and "routing" not in r.json()["detail"]
    assert _mint(client, not_live.slug).status_code == 409

    gate["ready"] = True
    r = client.put(f"/hotels/{not_live.slug}/concierge/live", json={"live": True})
    assert r.status_code == 200 and r.json()["live"] is True and r.json()["source"] == "setting"
    assert _mint(client, not_live.slug).status_code == 200


def test_owner_override_counts_as_ready(client_as, not_live, gate):
    client = client_as("owner")
    gate.update(ready=True, overridden=True)
    r = client.put(f"/hotels/{not_live.slug}/concierge/live", json={"live": True})
    assert r.status_code == 200 and r.json()["overridden"] is True


def test_switch_off_needs_no_gate_and_is_audited(client_as, cur, hotel_uae, gate):
    client = client_as("owner")
    r = client.put(f"/hotels/{hotel_uae.slug}/concierge/live", json={"live": False})
    assert r.status_code == 200 and r.json()["live"] is False
    got = client.get(f"/hotels/{hotel_uae.slug}/concierge/live").json()
    assert got["live"] is False and got["readiness"]["ready"] is False
    cur.execute("SELECT action, after FROM audit_log WHERE hotel_slug = %s AND action = 'concierge.live_switched'",
                (hotel_uae.slug,))
    [row] = cur.fetchall()
    assert row["after"]["live"] is False


def test_generic_module_settings_cannot_bypass_the_gate(client_as, cur, not_live, gate):
    """The live flag is not a module setting: PUT /module-settings/concierge
    (core.settings.manage, no gate) with live=true changes nothing."""
    from clefs.modules.concierge import live

    client = client_as("owner")
    r = client.put(f"/hotels/{not_live.slug}/module-settings/concierge", json={"settings": {"live": True}})
    assert r.status_code in (200, 422), r.text
    assert live.is_live(cur, not_live.slug) is False


def test_unavailable_trust_gate_is_red(client_as, not_live, monkeypatch):
    from clefs.modules.trust import api as trust_api

    def boom(cur, slug):
        cur.execute("SELECT * FROM trs_table_that_does_not_exist")

    monkeypatch.setattr(trust_api, "readiness", boom)
    client = client_as("owner")
    r = client.put(f"/hotels/{not_live.slug}/concierge/live", json={"live": True})
    assert r.status_code == 409 and r.json()["code"] == "readiness_not_met"


def test_api_is_live_for_other_modules(cur, hotel_uae):
    from clefs.modules.concierge import api

    assert api.is_live(cur, hotel_uae.slug) is True
    switch_live(cur, hotel_uae.slug, False)
    assert api.is_live(cur, hotel_uae.slug) is False


def test_staff_cannot_switch(client_as, hotel_uae, gate):
    gate["ready"] = True
    r = client_as("front_desk").put(f"/hotels/{hotel_uae.slug}/concierge/live", json={"live": False})
    assert r.status_code == 403


# ─── Demo hotels and the stay context (QA GS-1) ───


def _make_demo(cur, slug):
    from clefs.core import hotels

    cur.execute("UPDATE hotels SET status = 'demo' WHERE slug = %s", (slug,))
    hotels.invalidate(slug)


def test_demo_hotel_goes_live_without_the_gate(client, cur, not_live, gate, minted):
    """The demo seed wipes cc_live and has no eval run: go_live_demo must
    switch it on anyway, audited, and guest mints then succeed."""
    from clefs.modules.concierge import api, live

    _make_demo(cur, not_live.slug)
    out = api.go_live_demo(cur, not_live.slug, actor=None)
    assert out["live"] is True and out["source"] == "demo" and out["overridden"] is False
    assert live.live_state(cur, not_live.slug) == {"live": True, "source": "demo"}
    cur.execute("SELECT meta FROM audit_log WHERE hotel_slug = %s AND action = 'concierge.live_switched' "
                "ORDER BY id DESC LIMIT 1", (not_live.slug,))
    assert (cur.fetchone()["meta"] or {}).get("demo") is True
    assert _mint(client, not_live.slug).status_code == 200


def test_demo_live_is_refused_for_a_real_hotel(cur, not_live):
    from clefs.core.errors import ClefsError
    from clefs.modules.concierge import api, live

    with pytest.raises(ClefsError) as e:
        api.go_live_demo(cur, not_live.slug, actor=None)
    assert e.value.code == "not_demo_hotel"
    assert live.is_live(cur, not_live.slug) is False


def test_demo_live_can_still_be_switched_off(client_as, cur, not_live, gate):
    from clefs.modules.concierge import api, live

    _make_demo(cur, not_live.slug)
    api.go_live_demo(cur, not_live.slug, actor=None)
    r = client_as("owner").put(f"/hotels/{not_live.slug}/concierge/live", json={"live": False})
    assert r.status_code == 200, r.text
    assert live.live_state(cur, not_live.slug) == {"live": False, "source": "setting"}


def test_stay_context_features_follow_live(client, cur, hotel_uae):
    """While concierge.live is off the page learns it from /context, before
    the consent sheet, instead of from a 409 on the first message."""
    url = f"/public/stay/{hotel_uae.slug}/101/context"
    feats = client.get(url).json()["features"]
    assert feats["assistant"] is True and feats["voice"] is True
    switch_live(cur, hotel_uae.slug, False)
    feats = client.get(url).json()["features"]
    assert feats["assistant"] is False and feats["voice"] is False
