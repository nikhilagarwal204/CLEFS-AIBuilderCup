"""Wave 2 integration: the foundation doors added for the lanes' requests
(magic.peek, stays.delete_matches, ingest.batch_view, the modelled settings)
and the Profit Guard manifest wiring that mounts its routers and jobs."""

from __future__ import annotations

import pytest

from clefs.core.errors import ClefsError
from tests.foundation.route_walk import route_paths

# ─── auth.magic.peek ───


def test_peek_validates_without_using_the_link_up(cur, make_user, hotel_uae, clock):
    from clefs.auth import magic

    from clefs.core import clock as core_clock

    clock.freeze(core_clock.now())
    u = make_user("owner", hotel_uae.slug)
    token = magic.create_link(cur, user_id=u["id"], hotel_slug=hotel_uae.slug, purpose="brief", max_uses=1)
    for _ in range(3):
        link = magic.peek(cur, token)
        assert link["user_id"] == u["id"] and link["uses"] == 0 and link["purpose"] == "brief"
    with pytest.raises(ClefsError) as exc:
        magic.peek(cur, "not-a-token")
    assert exc.value.code == "magic_link_invalid"
    clock.advance(days=30)
    with pytest.raises(ClefsError):
        magic.peek(cur, token)


# ─── core.stays.delete_matches ───


def test_delete_matches_drops_only_the_named_lines(cur, hotel_uae):
    from clefs.core import stays

    s = hotel_uae.slug
    for line_id in (1, 2):
        stays.match_claim(cur, s, statement_line_id=line_id, line_kind="pfg_statement", ota_code="BOOKING",
                          ota_res_id=f"40000000{line_id}")
    assert stays.delete_matches(cur, s, "pfg_statement", []) == 0
    assert stays.delete_matches(cur, s, "pfg_statement", [1]) == 1
    assert stays.match_for_line(cur, s, 1, line_kind="pfg_statement") is None
    assert stays.match_for_line(cur, s, 2, line_kind="pfg_statement") is not None


# ─── core.ingest.batch_view ───


def test_batch_view_is_exported_from_the_package():
    from clefs.core import ingest

    out = ingest.batch_view({"id": 1, "kind": "OTA_STATEMENT", "file_id": None, "gate_status": "ok"}, counts={"OK": 2})
    assert out["id"] == 1 and out["file_id"] is None and out["gate_blocking"] is False and out["row_counts"] == {"OK": 2}


# ─── Settings ───


def test_lane_env_vars_are_modelled(monkeypatch):
    from clefs.settings import get_settings, reload_settings

    monkeypatch.setenv("GBP_PROJECT_QPM", "300")
    monkeypatch.setenv("DEMAND_XOTELO_ENABLED", "true")
    reload_settings()
    try:
        st = get_settings()
        assert st.GBP_PROJECT_QPM == 300 and st.DEMAND_XOTELO_ENABLED is True
        assert st.PG_AUDIT_LEAD_WA_TEMPLATE == "clefs_audit_lead"
        assert st.VIS_LEAD_WA_TEMPLATE == "clefs_google_check_followup"
    finally:
        monkeypatch.delenv("GBP_PROJECT_QPM")
        monkeypatch.delenv("DEMAND_XOTELO_ENABLED")
        reload_settings()


# ─── Profit Guard manifest ───


def test_profit_guard_manifest_mounts_every_router(app):
    paths = route_paths(app.routes)
    for p in ("/hotels/{slug}/profit-guard/ledger", "/hotels/{slug}/profit-guard/disputes",
              "/hotels/{slug}/profit-guard/tax/td-ledger", "/hotels/{slug}/profit-guard/channel-economics/monthly",
              "/hotels/{slug}/profit-guard/detectors/run", "/public/audits", "/public/leakage-index"):
        assert p in paths, p


def test_profit_guard_registers_jobs_actions_and_hooks(app):
    from clefs import jobs
    from clefs.core import actions
    from clefs.core.ingest import commit_handler, post_commit_hook_names

    for key in ("pg.dispute", "pg.config_fix", "pg.vcc_charge", "pg.mark_no_show", "pg.payout_query"):
        assert actions.get_action_type(key) is not None, key
    names = {j.name for j in jobs.registered_jobs()} | {t.name for t in jobs.registered_tasks()}
    assert {"pg.nightly_detect", "pg.auto_verify", "pg.public_audit_purge", "pg.detect_on_commit"} <= names
    assert commit_handler("OTA_STATEMENT") is not None
    hooks = post_commit_hook_names("OTA_STATEMENT")
    assert "clefs.modules.profit_guard.detectors.jobs.enqueue_detection" in hooks
    assert "clefs.modules.profit_guard.jobs.enqueue_verify" in hooks
