"""Adjusting a dr.rate_change from outside the Demand page (QA OWN-01).

Every Adjust surface (brief console, WhatsApp, magic brief) edits the
action through core decide(adjust, params_patch). Before the fix the
action carried no params.adjust, the surfaces guessed the first
*_minor key in JSONB order (current_bar_minor), and the suggested rate
never moved. These tests pin: the explicit spec, the rate-only rule, the
legacy remap, and the sync of the recommendation row."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from clefs import db as clefs_db
from clefs.core import actions as core_actions
from clefs.core.errors import ClefsError
from clefs.modules.demand import bar, guardrails, recommend as rec

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
NIGHT = date(2026, 10, 12)


# ─── Fixtures ───


@pytest.fixture
def hotel(cur, hotel_uae, clock):
    clock.freeze(NOW)
    bar.set_bar(cur, hotel_uae, stay_date=NIGHT, room_type_code="DLX", bar_minor=50000, source="MANUAL")
    guardrails.upsert_rows(cur, hotel_uae, [{"room_type_code": "DLX", "floor": "450", "ceiling": "560"}],
                           user_id=None)
    return hotel_uae


def _night(di: int, *, pace_gap: float = 0.3, comp_move: float = 0.3):
    return {"stay_date": NIGHT.isoformat(), "demand_index": di, "coverage": 0.75,
            "components": {"pace": {"otb": 10, "pace_gap": pace_gap, "base": 8.0},
                           "comp": {"comp_move": comp_move, "sold_out": 0, "comps": 5},
                           "events": [], "market_holidays": [], "season": None}}


@pytest.fixture
def proposed(cur, hotel):
    rec.run(cur, hotel, today=NOW.date(), nights=[_night(95)], llm_budget=0)
    [r] = rec.list_recs(cur, hotel.slug)
    assert r["status"] == "PROPOSED" and r["action_id"]
    return r


@pytest.fixture
def owner(app, cur, make_user, hotel):
    """A real owner HotelCtx (`app` loads the module permissions, so the
    type's decide_perm demand.manage is checked as in production)."""
    from clefs.auth.deps import Principal, resolve_ctx

    user = make_user("owner", hotel.slug)
    return resolve_ctx(Principal(kind="user", user_id=user["id"], label="Owner", session_kind="web"), hotel.slug,
                       cur=cur)


def _adjust(cur, actor, action_id, patch):
    return core_actions.decide(cur, action_id, "adjust", actor=actor, via="whatsapp", params_patch=patch)


# ─── The explicit spec ───


def test_rate_action_names_the_editable_field(cur, hotel, proposed):
    # Read back through JSONB (key order is length then bytes there).
    p = core_actions.get_action(cur, hotel.slug, proposed["action_id"])["params"]
    assert p["adjust"] == {"field": "recommended_minor", "min_minor": 45000, "max_minor": 56000, "label": "rate"}
    assert list(p).index("current_bar_minor") < list(p).index("recommended_minor")  # why the guess went wrong


# ─── Adjust through core decide (brief, WhatsApp, actions API) ───


def test_adjust_moves_the_rate_and_marks_the_row_adjusted(cur, hotel, proposed, owner):
    act = _adjust(cur, owner, proposed["action_id"], {"recommended_minor": 53000})
    assert act["status"] == "approved" and act["params"]["recommended_minor"] == 53000
    assert act["params"]["current_bar_minor"] == 50000
    ev = [e for e in core_actions.list_events(cur, proposed["action_id"]) if e["event"] == "adjust"][-1]
    assert ev["data"]["params_diff"] == {"recommended_minor": [56000, 53000]}
    [r] = rec.list_recs(cur, hotel.slug)
    assert r["status"] == "ADJUSTED" and r["decided"] == "530.00"
    # The guardrail feed and quotes price the owner's rate.
    assert rec._recommender(cur, hotel.slug, NIGHT, "DLX")["recommended_minor"] == 53000


def test_only_the_rate_moves(cur, hotel, proposed, owner):
    for patch in ({"ceiling_minor": 90000, "recommended_minor": 90000}, {"floor_minor": 1000},
                  {"stay_date": "2026-10-13"}, {"current_bar_minor": 52000}):
        with pytest.raises(ClefsError) as e:
            _adjust(cur, owner, proposed["action_id"], patch)
        assert e.value.code == "invalid_params", patch
    with pytest.raises(ClefsError) as e:
        _adjust(cur, owner, proposed["action_id"], {"recommended_minor": 57000})  # above the ceiling
    assert e.value.code == "invalid_params"
    assert rec.list_recs(cur, hotel.slug)[0]["status"] == "PROPOSED"


def test_legacy_action_without_spec_remaps_a_bar_edit(cur, hotel, proposed, owner):
    # Actions created before params.adjust: a surface that still guesses
    # current_bar_minor meant the rate.
    clefs_db.execute("UPDATE actions SET params = params - 'adjust' WHERE id = %s", (proposed["action_id"],), cur=cur)
    act = _adjust(cur, owner, proposed["action_id"], {"current_bar_minor": 53000})
    assert act["params"]["recommended_minor"] == 53000 and act["params"]["current_bar_minor"] == 50000
    [r] = rec.list_recs(cur, hotel.slug)
    assert r["status"] == "ADJUSTED" and r["decided"] == "530.00" and r["current_bar"] == "500.00"


def test_readjust_after_approval_resyncs_the_row(cur, hotel, proposed, owner):
    core_actions.decide(cur, proposed["action_id"], "approve", actor=owner, via="whatsapp")
    assert rec.list_recs(cur, hotel.slug)[0]["status"] == "APPROVED"
    _adjust(cur, owner, proposed["action_id"], {"recommended_minor": 52000})
    [r] = rec.list_recs(cur, hotel.slug)
    assert r["status"] == "ADJUSTED" and r["decided"] == "520.00"


def test_decided_elsewhere_is_never_redecided_or_superseded(cur, hotel, proposed, owner):
    _adjust(cur, owner, proposed["action_id"], {"recommended_minor": 53000})
    # No read synced the row yet. The Demand page's verb is a 409, not a
    # second decision on the approved action.
    with pytest.raises(ClefsError) as e:
        rec.decide(cur, owner, proposed["id"], "approve")
    assert e.value.code == "recommendation_closed"
    # A DI move of more than 15 points must not cancel the owner's action.
    clefs_db.execute("UPDATE dr_rate_recommendations SET status = 'PROPOSED' WHERE id = %s", (proposed["id"],),
                     cur=cur)
    rec.run(cur, hotel, today=NOW.date(), nights=[_night(60, pace_gap=0.1, comp_move=0.1)], llm_budget=0)
    assert core_actions.get_action(cur, hotel.slug, proposed["action_id"])["status"] == "approved"
    assert rec.get(cur, hotel.slug, proposed["id"])["status"] == "ADJUSTED"
