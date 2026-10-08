"""
Extension lane "autonomy-cadence-stubs" (04 spec F1/F2, F7, F9, F10, D1).

  F1/F2  SYSTEM and ActionCtx, decided_via for dial approvals, create_action
         (level, absolute verify_until), record_verification(result=...), the
         per-channel template gate, the promotion-suggestion lifecycle (0015),
         level_check, loosening limits needs raise_auto, magic-session
         decisions on surfaced actions
  F7     list_signals by one or several cadences, the per-signal override
  F9     (registry.KEY_PREFIXES is covered in test_module_contracts.py)
  F10    every 7.2 stub returns its documented default
  D1     the frozen stay-event names and payload
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
import pytest
from pydantic import BaseModel

from clefs import jobs
from clefs.auth.deps import Principal, resolve_ctx
from clefs.core import actions, signals
from clefs.core.actions import SYSTEM, ActionType, Gate, VerifyResult
from clefs.core.errors import ClefsError

# ─── Fixtures ───


class Limits(BaseModel):
    max_amount_minor: int = 0
    once_per_stay: bool = True


class Seen:
    """Records what executors, gates and verifiers saw."""

    def __init__(self) -> None:
        self.gate_ids: list[int | None] = []
        self.exec_ctx: list[actions.ActionCtx] = []

    def executor(self, ctx: actions.ActionCtx) -> dict:
        self.exec_ctx.append(ctx)
        return {"sent": True}

    def gate(self, ctx: actions.ActionCtx) -> Gate:
        self.gate_ids.append(ctx.action_id)
        return actions.template_gate(ctx.params.get("channels") or {})


def _no_consent(cur, hotel_slug: str, level: str) -> list[str]:
    cur.execute("SELECT 1 FROM hotels WHERE slug = %s AND knowledge_base = 'consent'", (hotel_slug,))
    ok = cur.fetchone() is not None
    return [] if ok or level not in actions.AUTO_LEVELS else ["auto_reply_consent_required: no Google source has consent"]


@pytest.fixture
def seen():
    s = Seen()
    types = [
        ActionType(key="core.x_send", module="core", label="Journey send", executor=s.executor, gate=s.gate,
                   default_level="auto", verifier=lambda ctx: VerifyResult(status="pending"),
                   verify_until=timedelta(days=3)),
        ActionType(key="core.x_auto", module="core", label="Auto thing", executor=s.executor),
        ActionType(key="core.x_suggest", module="core", label="Draft reply", executor=s.executor, default_level="suggest"),
        ActionType(key="core.x_limited", module="core", label="Gesture", executor=s.executor, default_level="approve",
                   max_level="auto_within_limits", limits_model=Limits),
        ActionType(key="core.x_consent", module="core", label="Positive reply", executor=s.executor,
                   default_level="suggest", level_check=_no_consent),
        ActionType(key="core.x_quote", module="core", label="Group quote", executor=s.executor,
                   decide_perm="core.metrics.view"),  # a key revenue holds and magic sessions do not
    ]
    for t in types:
        actions.register_action_type(t)
    yield s
    for t in types:
        actions.unregister_action_type(t.key)


def _run_queue():
    return jobs.run_pending(only=["core.actions.execute"])


def _decide_many(cur, slug, actor, type_: str, n: int, decision: str = "approve") -> None:
    for i in range(n):
        a = actions.create_action(cur, slug, type=type_, title=f"T{i}", params={"n": i}, actor=SYSTEM)
        if decision == "adjust":
            actions.decide(cur, a["id"], "adjust", actor=actor, params_patch={"n": -i})
        elif decision == "assign":
            actions.decide(cur, a["id"], "assign", actor=actor, assignee_role="gm")
        else:
            actions.decide(cur, a["id"], decision, actor=actor)


def _suggestion_row(cur, event_id: int) -> dict:
    cur.execute("SELECT kind, status, resolves_event_id FROM action_autonomy_events WHERE id = %s", (event_id,))
    return dict(cur.fetchone())


# ─── F1/F2: SYSTEM, ActionCtx, decided_via, create_action ───


def test_dial_approvals_are_attributed_to_system_with_decided_via_dial(cur, hotel_uae, seen, owner_ctx):
    actions.set_autonomy(cur, hotel_uae.slug, "core.x_auto", level="auto", actor=owner_ctx)
    a = actions.create_action(cur, hotel_uae.slug, type="core.x_auto", title="Send", actor=SYSTEM)
    assert a["status"] == "approved" and a["decided_via"] == "dial" and a["level"] == "auto"
    assert a["decided_by"] is None and not a["human_decided"]
    approve = actions.list_events(cur, a["id"])[-1]
    assert (approve["event"], approve["actor_type"], approve["actor_label"], approve["via"]) == ("approve", "dial", "system", "dial")
    assert SYSTEM.kind == "service" and SYSTEM.label == "system"
    # A dial approval is never counted as a human decision toward promotion.
    assert actions.promotion_stats(cur, hotel_uae.slug, "core.x_auto")["decisions"] == 0


def test_action_ctx_exposes_the_spec_fields_and_is_frozen(cur, hotel_uae, seen):
    a = actions.create_action(cur, hotel_uae.slug, type="core.x_send", title="J1", params={"channels": {"email": True}},
                              actor=SYSTEM)
    assert seen.gate_ids == [None]  # the gate runs on the draft, before the row exists
    _run_queue()
    [ctx] = seen.exec_ctx
    assert (ctx.action_id, ctx.type, ctx.params, ctx.hotel_slug) == (a["id"], "core.x_send", {"channels": {"email": True}}, hotel_uae.slug)
    assert seen.gate_ids[-1] == a["id"]  # re-resolved at execution with the stored row
    with pytest.raises(AttributeError):
        ctx.actor = SYSTEM  # type: ignore[misc]


def test_decide_refuses_unknown_or_dial_via(cur, hotel_uae, seen, owner_ctx):
    a = actions.create_action(cur, hotel_uae.slug, type="core.x_suggest", title="Draft", actor=SYSTEM)
    for via in ("dial", "carrier-pigeon"):
        with pytest.raises(ClefsError) as exc:
            actions.decide(cur, a["id"], "approve", actor=owner_ctx, via=via)
        assert exc.value.code == "invalid_via"
    with pytest.raises(ClefsError) as exc:
        actions.create_action(cur, hotel_uae.slug, type="core.x_suggest", title="Draft", actor=SYSTEM, via="dial")
    assert exc.value.code == "invalid_via"
    assert actions.decide(cur, a["id"], "approve", actor=owner_ctx, via="email")["decided_via"] == "email"


def test_a_hotel_ctx_cannot_decide_another_hotels_action(cur, hotel_uae, seen, ctx_for):
    from clefs.core import hotels

    hotels.create_hotel(cur, slug="other-dubai", name="Other", city="Dubai")
    a = actions.create_action(cur, "other-dubai", type="core.x_suggest", title="Theirs", actor=SYSTEM)
    owner_here, _ = ctx_for("owner")
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, a["id"], "approve", actor=owner_here)
    assert exc.value.status == 404


def test_absolute_verify_until_overrides_the_type_window(cur, hotel_uae, seen, clock):
    clock.freeze(datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc))
    anchor = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
    fixed = actions.create_action(cur, hotel_uae.slug, type="core.x_send", title="Quote", actor=SYSTEM,
                                  params={"channels": {"email": True}}, verify_until=anchor)
    typed = actions.create_action(cur, hotel_uae.slug, type="core.x_send", title="Quote", actor=SYSTEM,
                                  params={"channels": {"email": True}})
    assert fixed["verify_until"] == anchor and typed["verify_until"] is None
    _run_queue()
    assert actions.get_action(cur, hotel_uae.slug, fixed["id"])["verify_until"] == anchor
    assert actions.get_action(cur, hotel_uae.slug, typed["id"])["verify_until"] == clock.now() + timedelta(days=3)
    with pytest.raises(ValueError):
        actions.create_action(cur, hotel_uae.slug, type="core.x_send", title="Q", actor=SYSTEM,
                              verify_until=datetime(2026, 10, 20))


def test_record_verification_takes_a_verify_result(cur, hotel_uae, seen, owner_ctx):
    a = actions.create_action(cur, hotel_uae.slug, type="core.x_suggest", title="Draft", actor=SYSTEM, expected_minor=5000)
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    actions.mark_done(cur, a["id"], actor=owner_ctx)
    out = actions.record_verification(
        cur, a["id"], result=VerifyResult(status="verified", outcome_minor=4200, attribution="caused",
                                          counts_toward_verified_profit=True),
    )
    assert (out["status"], out["outcome_minor"], out["currency"], out["counts_toward_verified_profit"]) == ("verified", 4200, "AED", True)
    b = actions.create_action(cur, hotel_uae.slug, type="core.x_suggest", title="Draft", actor=SYSTEM)
    actions.decide(cur, b["id"], "approve", actor=owner_ctx)
    actions.mark_done(cur, b["id"], actor=owner_ctx)
    with pytest.raises(ValueError):
        actions.record_verification(cur, b["id"], result={"status": "verified"}, outcome_minor=1)
    assert actions.record_verification(cur, b["id"], result={"status": "not_verified"})["status"] == "not_verified"


# ─── F1: per-channel template gate ───


def test_template_gate_caps_at_observe_only_when_no_channel_is_approved(cur, hotel_uae, seen):
    assert actions.template_gate({"whatsapp": False, "email": True}) == Gate(max_level="auto")
    assert actions.template_gate({"whatsapp": True}, open_level="approve") == Gate(max_level="approve")
    none = actions.template_gate({"whatsapp": False, "email": False})
    assert none.max_level == "observe" and none.reasons == ["no approved template on any eligible channel (email, whatsapp)"]
    assert actions.template_gate({}).reasons == ["no eligible channel"]

    blocked = actions.create_action(cur, hotel_uae.slug, type="core.x_send", title="J4", actor=SYSTEM,
                                    params={"channels": {"whatsapp": False, "email": False}})
    sent = actions.create_action(cur, hotel_uae.slug, type="core.x_send", title="J4", actor=SYSTEM,
                                 params={"channels": {"whatsapp": False, "email": True}})
    assert blocked["status"] == "observed" and blocked["level"] == "observe" and blocked["terminal"]
    assert sent["status"] == "approved" and sent["level"] == "auto"
    _run_queue()
    assert [c.action_id for c in seen.exec_ctx] == [sent["id"]]


# ─── F1: promotion lifecycle (0015) ───


def test_a_suggestion_is_open_until_accepted_and_accepting_to_approve_needs_only_manage(cur, hotel_uae, seen, ctx_for):
    owner_ctx, _ = ctx_for("owner")
    gm_ctx, _ = ctx_for("gm")
    slug = hotel_uae.slug
    _decide_many(cur, slug, owner_ctx, "core.x_suggest", 50)
    [s] = actions.review_promotions(cur, slug)
    assert (s["from_level"], s["to_level"]) == ("suggest", "approve")
    assert _suggestion_row(cur, s["event_id"]) == {"kind": "promotion_suggested", "status": "open", "resolves_event_id": None}
    view = actions.get_autonomy(cur, slug, "core.x_suggest")["open_suggestion"]
    assert set(view) == {"event_id", "to_level", "at"} and view["event_id"] == s["event_id"]
    assert actions.accept_perm_for("approve") == "core.autonomy.manage"

    out = actions.accept_suggestion(cur, slug, s["event_id"], actor=gm_ctx)  # manage is enough up to approve
    assert out["level"] == "approve" and out["open_suggestion"] is None
    assert _suggestion_row(cur, s["event_id"])["status"] == "accepted"
    cur.execute(
        "SELECT kind, resolves_event_id FROM action_autonomy_events WHERE hotel_slug = %s AND action_type = 'core.x_suggest' "
        "ORDER BY id DESC LIMIT 1",
        (slug,),
    )
    assert dict(cur.fetchone()) == {"kind": "promotion_accepted", "resolves_event_id": s["event_id"]}
    with pytest.raises(ClefsError) as exc:
        actions.accept_suggestion(cur, slug, s["event_id"], actor=owner_ctx)
    assert exc.value.code == "suggestion_stale"


def test_accepting_above_approve_needs_raise_auto(cur, hotel_uae, seen, ctx_for):
    owner_ctx, _ = ctx_for("owner")
    gm_ctx, _ = ctx_for("gm")
    _decide_many(cur, hotel_uae.slug, owner_ctx, "core.x_auto", 50)
    [s] = actions.review_promotions(cur, hotel_uae.slug)
    assert actions.accept_perm_for(s["to_level"]) == "core.autonomy.raise_auto"
    with pytest.raises(ClefsError) as exc:
        actions.accept_suggestion(cur, hotel_uae.slug, s["event_id"], actor=gm_ctx)
    assert exc.value.status == 403
    assert _suggestion_row(cur, s["event_id"])["status"] == "open"  # still there for the owner
    actions.accept_suggestion(cur, hotel_uae.slug, s["event_id"], actor=owner_ctx)
    assert _suggestion_row(cur, s["event_id"])["status"] == "accepted"


def test_dismissal_resolves_the_suggestion_and_keeps_the_review_quiet_for_30_days(cur, hotel_uae, seen, owner_ctx, clock):
    slug = hotel_uae.slug
    clock.freeze(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc))
    _decide_many(cur, slug, owner_ctx, "core.x_suggest", 50)
    [s] = actions.review_promotions(cur, slug)
    actions.dismiss_suggestion(cur, slug, s["event_id"], actor=owner_ctx)
    assert _suggestion_row(cur, s["event_id"])["status"] == "dismissed"
    cur.execute(
        "SELECT resolves_event_id FROM action_autonomy_events WHERE kind = 'promotion_dismissed' AND hotel_slug = %s", (slug,)
    )
    assert cur.fetchone()["resolves_event_id"] == s["event_id"]
    # A later dial event (a demotion, then the level set back) does not end
    # the quiet period: it is 30 days from the dismissal, not "latest event".
    actions.demote(cur, slug, "core.x_suggest", trigger="guest_complaint")
    actions.set_autonomy(cur, slug, "core.x_suggest", level="suggest", actor=owner_ctx)
    clock.advance(days=10)
    _decide_many(cur, slug, owner_ctx, "core.x_suggest", 50)
    assert actions.review_promotions(cur, slug) == []
    clock.advance(days=21)
    [again] = actions.review_promotions(cur, slug)
    assert again["event_id"] != s["event_id"]


def test_manual_changes_and_demotions_supersede_but_a_no_op_put_does_not(cur, hotel_uae, seen, owner_ctx):
    slug = hotel_uae.slug
    _decide_many(cur, slug, owner_ctx, "core.x_suggest", 50)
    [s] = actions.review_promotions(cur, slug)
    actions.set_autonomy(cur, slug, "core.x_suggest", level="suggest", actor=owner_ctx)  # changes nothing
    assert _suggestion_row(cur, s["event_id"])["status"] == "open"
    assert len(signals.list_signals(cur, slug, sections=["compliance"])) == 1
    actions.set_autonomy(cur, slug, "core.x_suggest", level="observe", actor=owner_ctx)
    assert _suggestion_row(cur, s["event_id"])["status"] == "superseded"
    assert signals.list_signals(cur, slug, sections=["compliance"]) == []

    actions.set_autonomy(cur, slug, "core.x_suggest", level="suggest", actor=owner_ctx)
    [s2] = actions.review_promotions(cur, slug)
    actions.report_incident(cur, slug, kind="meta_quality", action_types=["core.x_suggest"], evidence=[])
    assert _suggestion_row(cur, s2["event_id"])["status"] == "superseded"
    with pytest.raises(ClefsError) as exc:
        actions.accept_suggestion(cur, slug, s2["event_id"], actor=owner_ctx)
    assert exc.value.code == "suggestion_stale"


def test_one_open_suggestion_per_type_is_enforced_by_the_database(cur, hotel_uae, seen, owner_ctx):
    _decide_many(cur, hotel_uae.slug, owner_ctx, "core.x_suggest", 50)
    actions.review_promotions(cur, hotel_uae.slug)
    cur.execute("SAVEPOINT dup")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute(
            "INSERT INTO action_autonomy_events (hotel_slug, action_type, kind, from_level, to_level, status) "
            "VALUES (%s, 'core.x_suggest', 'promotion_suggested', 'suggest', 'approve', 'open')",
            (hotel_uae.slug,),
        )
    cur.execute("ROLLBACK TO SAVEPOINT dup")
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "INSERT INTO action_autonomy_events (hotel_slug, action_type, kind, status) VALUES (%s, 'core.x_suggest', 'set', 'open')",
            (hotel_uae.slug,),
        )
    cur.execute("ROLLBACK TO SAVEPOINT dup")


def test_promotion_stats_count_approve_adjust_and_ignore_only(cur, hotel_uae, seen, owner_ctx):
    slug = hotel_uae.slug
    _decide_many(cur, slug, owner_ctx, "core.x_suggest", 3)
    _decide_many(cur, slug, owner_ctx, "core.x_suggest", 2, decision="adjust")
    _decide_many(cur, slug, owner_ctx, "core.x_suggest", 1, decision="ignore")
    _decide_many(cur, slug, owner_ctx, "core.x_suggest", 4, decision="assign")
    for _ in range(2):
        a = actions.create_action(cur, slug, type="core.x_suggest", title="Dispute", actor=SYSTEM)
        actions.decide(cur, a["id"], "dispute", actor=owner_ctx)
    stats = actions.promotion_stats(cur, slug, "core.x_suggest")
    assert (stats["decisions"], stats["approved_unedited"]) == (6, 3)


def test_next_level_skips_auto_within_limits_without_a_limits_model(seen):
    auto = actions.get_action_type("core.x_auto")
    limited = actions.get_action_type("core.x_limited")
    assert actions.next_level(auto, "approve", skip_unlimited=True) == "auto"
    assert actions.next_level(limited, "approve", skip_unlimited=True) == "auto_within_limits"
    assert actions.next_level(limited, "auto_within_limits", skip_unlimited=True) is None  # at the ceiling
    capped = ActionType(key="core.x_capped", module="core", label="x", max_level="auto_within_limits")
    assert actions.next_level(capped, "approve", skip_unlimited=True) is None  # the only step up is skipped
    assert actions.next_level(auto, "approve", skip_unlimited=False) == "auto_within_limits"
    assert actions.next_level(auto, "approve") == ("auto" if actions.PROMOTION_SKIPS_UNLIMITED else "auto_within_limits")


# ─── F1: level_check and loosening limits ───


def test_level_check_refuses_a_raise_but_never_a_lowering(cur, hotel_uae, seen, owner_ctx):
    slug = hotel_uae.slug
    with pytest.raises(ClefsError) as exc:
        actions.set_autonomy(cur, slug, "core.x_consent", level="auto", actor=owner_ctx)
    assert (exc.value.status, exc.value.code) == (409, "auto_reply_consent_required")
    assert actions.set_autonomy(cur, slug, "core.x_consent", level="approve", actor=owner_ctx)["level"] == "approve"
    assert actions.set_autonomy(cur, slug, "core.x_consent", level="observe", actor=owner_ctx)["level"] == "observe"
    # The review never suggests a level the check would refuse.
    actions.set_autonomy(cur, slug, "core.x_consent", level="approve", actor=owner_ctx)
    _decide_many(cur, slug, owner_ctx, "core.x_consent", 50)
    assert [s for s in actions.review_promotions(cur, slug) if s["action_type"] == "core.x_consent"] == []
    cur.execute("UPDATE hotels SET knowledge_base = 'consent' WHERE slug = %s", (slug,))
    assert actions.set_autonomy(cur, slug, "core.x_consent", level="auto", actor=owner_ctx)["level"] == "auto"


def test_loosening_limits_needs_raise_auto_and_tightening_does_not(cur, hotel_uae, seen, ctx_for):
    owner_ctx, _ = ctx_for("owner")
    gm_ctx, _ = ctx_for("gm")
    slug = hotel_uae.slug
    actions.set_autonomy(cur, slug, "core.x_limited", level="auto_within_limits", limits={"max_amount_minor": 20000},
                         actor=owner_ctx)
    tighter = actions.set_autonomy(cur, slug, "core.x_limited", level="auto_within_limits",
                                   limits={"max_amount_minor": 10000}, actor=gm_ctx)
    assert tighter["limits"] == {"max_amount_minor": 10000, "once_per_stay": True}
    for looser in ({"max_amount_minor": 15000}, {"max_amount_minor": 10000, "once_per_stay": False}):
        with pytest.raises(ClefsError) as exc:
            actions.set_autonomy(cur, slug, "core.x_limited", level="auto_within_limits", limits=looser, actor=gm_ctx)
        assert exc.value.status == 403
    # Lowering the level is always allowed to a manager, whatever the level.
    assert actions.set_autonomy(cur, slug, "core.x_limited", level="approve", actor=gm_ctx)["level"] == "approve"
    assert actions.set_autonomy(cur, slug, "core.x_limited", level="approve", limits={"max_amount_minor": 50000},
                                actor=owner_ctx)["limits"]["max_amount_minor"] == 50000


def test_limits_loosen_rule_and_a_custom_is_looser(cur, hotel_uae, owner_ctx, ctx_for):
    ll = actions.limits_loosen
    assert not ll({"max_minor": 5}, {"max_minor": 3}) and ll({"max_minor": 3}, {"max_minor": 5})
    assert ll({"max_minor": 3}, {}) and not ll({"min_nights": 1}, {"min_nights": 2}) and ll({"min_nights": 2}, {"min_nights": 1})
    assert not ll({}, {"max_minor": 3}) and ll({"max_minor": 3}, {"max_minor": None})
    assert not ll({"fields": ["a", "b"]}, {"fields": ["a"]}) and ll({"fields": ["a"]}, {"fields": ["a", "c"]})
    assert ll({"once_per_stay": True}, {"once_per_stay": False}) and ll({}, {"mode": "x"})
    assert not ll({"max_minor": 3, "fields": ["a"]}, {"max_minor": 3, "fields": ["a"]})

    calls: list[tuple[int, int]] = []

    def looser(old: Limits, new: Limits) -> bool:
        calls.append((old.max_amount_minor, new.max_amount_minor))
        return False  # this type says nothing loosens

    t = ActionType(key="core.x_custom", module="core", label="x", max_level="auto_within_limits", limits_model=Limits,
                   is_looser=looser)
    actions.register_action_type(t)
    try:
        gm_ctx, _ = ctx_for("gm")
        actions.set_autonomy(cur, hotel_uae.slug, "core.x_custom", level="approve", limits={"max_amount_minor": 999},
                             actor=gm_ctx)
        assert calls == [(0, 999)]
    finally:
        actions.unregister_action_type("core.x_custom")


# ─── F1: magic-link decisions (04 spec 1.1) ───


def _magic_ctx(cur, user_id: int, slug: str, action_ids: list[int] | None):
    scope = {"hotel": slug, "purposes": ["brief", "actions"]}
    if action_ids is not None:
        scope["action_ids"] = action_ids
    p = Principal(kind="user", user_id=user_id, session_kind="magic", scope=scope, label="magic")
    return resolve_ctx(p, slug, cur=cur)


def test_a_magic_session_decides_surfaced_actions_with_its_full_role(cur, hotel_uae, seen, make_user):
    slug = hotel_uae.slug
    revenue = make_user("revenue", slug)
    quote = actions.create_action(cur, slug, type="core.x_quote", title="Group quote", actor=SYSTEM)
    other = actions.create_action(cur, slug, type="core.x_quote", title="Another quote", actor=SYSTEM)

    plain = _magic_ctx(cur, revenue["id"], slug, None)
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, quote["id"], "approve", actor=plain)  # magic-filtered set lacks the decide_perm
    assert exc.value.code == "forbidden"

    ctx = _magic_ctx(cur, revenue["id"], slug, [quote["id"]])
    out = actions.decide(cur, quote["id"], "approve", actor=ctx, via="whatsapp")
    assert out["status"] == "approved" and out["decided_by"] == revenue["id"]
    cur.execute(
        "SELECT meta FROM audit_log WHERE action = 'core.actions.approve' AND entity_id = %s ORDER BY id DESC LIMIT 1",
        (str(quote["id"]),),
    )
    assert cur.fetchone()["meta"]["via"] == "magic"
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, other["id"], "approve", actor=ctx)
    assert (exc.value.status, exc.value.code) == (403, "not_surfaced")

    task = actions.create_action(cur, slug, type="core.task", title="Minibar", actor=SYSTEM)
    task_ctx = _magic_ctx(cur, revenue["id"], slug, [task["id"]])
    with pytest.raises(ClefsError) as exc:  # the full role still lacks core.actions.decide
        actions.decide(cur, task["id"], "approve", actor=task_ctx)
    assert exc.value.code == "forbidden"


def test_magic_link_scope_reaches_the_decide_route(cur, app, hotel_uae, seen, make_user):
    from fastapi.testclient import TestClient

    from clefs.auth import magic

    slug = hotel_uae.slug
    revenue = make_user("revenue", slug)
    quote = actions.create_action(cur, slug, type="core.x_quote", title="Group quote", actor=SYSTEM)
    other = actions.create_action(cur, slug, type="core.x_quote", title="Other", actor=SYSTEM)
    token = magic.create_link(cur, user_id=revenue["id"], hotel_slug=slug, purpose="brief",
                              scope={"action_ids": [quote["id"]]})
    c = TestClient(app)
    assert c.post("/auth/magic/exchange", json={"token": token}).status_code == 200
    r = c.post(f"/hotels/{slug}/actions/{quote['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = c.post(f"/hotels/{slug}/actions/{other['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 403 and r.json()["code"] == "not_surfaced"


def test_autonomy_routes_expose_accept_perm_and_refuse_loosening(cur, client_as, hotel_uae, seen):
    base = f"/hotels/{hotel_uae.slug}/autonomy"
    owner, gm = client_as("owner"), client_as("gm")
    assert owner.put(f"{base}/core.x_limited", json={"level": "auto_within_limits", "limits": {"max_amount_minor": 500}}).status_code == 200
    r = gm.put(f"{base}/core.x_limited", json={"level": "auto_within_limits", "limits": {"max_amount_minor": 900}})
    assert r.status_code == 403
    assert gm.put(f"{base}/core.x_limited", json={"level": "auto_within_limits", "limits": {"max_amount_minor": 100}}).status_code == 200
    r = owner.put(f"{base}/core.x_consent", json={"level": "auto"})
    assert r.status_code == 409 and r.json()["code"] == "auto_reply_consent_required"
    row = next(x for x in owner.get(base).json() if x["action_type"] == "core.x_limited")
    assert row["limits_schema"]["properties"]["once_per_stay"]["type"] == "boolean" and row["open_suggestion"] is None


# ─── F7: cadence ───


def test_list_signals_filters_by_one_or_several_cadences(cur, hotel_uae):
    signals.register_signal_kind("core.x_weekly", label="Weekly", default_owner_role="gm", section="money_available",
                                 cadence="weekly")
    try:
        def up(key: str, cadence: str | None = None):
            return signals.upsert_signal(cur, hotel_uae.slug, signals.SignalIn(
                module="core", kind="core.x_weekly", dedupe_key=key, title=key, confidence=0.5, cadence=cadence))

        assert up("w1")["cadence"] == "weekly"
        assert up("d1", cadence="daily")["cadence"] == "daily"  # "daily only when L1"
        up("m1", cadence="monthly")
        keys = lambda c: sorted(s["dedupe_key"] for s in signals.list_signals(cur, hotel_uae.slug, cadence=c))  # noqa: E731
        assert keys("weekly") == ["w1"] and keys(["daily", "monthly"]) == ["d1", "m1"] and keys("daily,weekly") == ["d1", "w1"]
        assert keys(None) == ["d1", "m1", "w1"]
        assert up("d1")["cadence"] == "weekly"  # no override on this run: back to the kind's cadence
        with pytest.raises(ClefsError) as exc:
            signals.list_signals(cur, hotel_uae.slug, cadence="hourly")
        assert exc.value.code == "invalid_cadence"
    finally:
        with signals._lock:
            signals._kinds.pop("core.x_weekly", None)


def test_signals_route_accepts_several_cadences(cur, client_as, hotel_uae):
    owner = client_as("owner")
    assert owner.get(f"/hotels/{hotel_uae.slug}/signals?cadence=daily,weekly").status_code == 200
    r = owner.get(f"/hotels/{hotel_uae.slug}/signals?cadence=yearly")
    assert r.status_code == 400 and r.json()["code"] == "invalid_cadence"


# ─── F10: 7.2 stubs return their documented defaults ───


def test_api_stubs_return_their_documented_defaults(cur, hotel_uae):
    from clefs.modules.brief import api as brief
    from clefs.modules.concierge import api as concierge
    from clefs.modules.demand import api as demand
    from clefs.modules.finance import api as finance
    from clefs.modules.guests import api as guests
    from clefs.modules.inquiries import api as inquiries
    from clefs.modules.profit_guard import api as pg
    from clefs.modules.reviews import api as reviews
    from clefs.modules.service import api as service
    from clefs.modules.visibility import api as vis

    s, d, now = hotel_uae.slug, date(2026, 10, 1), datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
    assert demand.area_queries(cur, s) == ["hotels in Dubai"]
    assert demand.comp_set(cur, s) == [] and demand.expected_adr(cur, s, check_in=d, check_out=d) is None
    assert demand.price_guardrails(cur, s, d) is None and demand.price_guardrails(cur, s, d, "DLX") is None
    assert demand.comp_rates_snapshot(cur, s, stay_dates=[d]) == {} and demand.upcoming_events(cur, s) == []
    assert demand.find_event(cur, s, text="GITEX") is None and demand.demand_index(cur, s, stay_date=d) is None

    assert concierge.kb_facts(cur, s, keyed_only=True) == []
    # Built by the concierge lane: a proposal is queued pending for the owner.
    eid = concierge.propose_kb_entry(cur, s, question_en="Parking?", answer_en="Yes", source="visibility", actor=None)
    assert concierge.kb_entry_status(cur, s, eid)["status"] == "pending"
    assert concierge.kb_entry_status(cur, s, eid + 100_000) is None

    assert reviews.reply_stats(cur, s) is None and reviews.review_counts(cur, s, start=d, end=d) == {}
    assert pg.record_ota_leak(cur, s, source="x", inquiry_ref="i", reservation_ref=None, channel_code="BDC", evidence={},
                              commission_minor=100, currency="AED") is None
    assert pg.expected_commission(cur, s, ota_code="BDC", breakdown={}, on=d) is None and pg.reference_ota(cur, s, as_of=d) is None
    assert pg.distribution_cost_by_night(cur, s, start=d, end=d) == [] and pg.cancellation_cost_by_date(cur, s, start=d, end=d) == []
    assert pg.distribution_cost_for_reservation(cur, s, reservation_id=1) == 0
    assert pg.record_rate_evidence(cur, s, evidence={}) == 0
    assert pg.parity_tolerance_pct(cur, s) == Decimal("1.0") and pg.treat_sc_as_revenue(cur, s) is True

    subject = vis.SnapshotSubject(hotel_name="Test", city="Dubai")
    assert vis.start_ai_snapshot(cur, subject=subject, requester_ref="r") is None and vis.get_ai_snapshot(cur, 1) is None
    assert vis.purge_ai_snapshot(cur, 1) == 0 and vis.parity_status(cur, s, stay_date=d) is None
    assert vis.attribution(cur, s, month=d) is None and vis.open_fact_errors(cur, s) == [] and vis.gbp_location(cur, s) is None

    assert service.open_items_for_stay(cur, s, room="101", since=now) == []
    assert service.has_open_complaint(cur, s, since=now) is False
    assert service.recovery_cost(cur, s, start=d, end=d) == 0 and service.on_duty(cur, s, role="duty_manager", at=now) == []

    assert brief.wa_template_info(cur, s, name="clefs_j1", language="en") is None
    with pytest.raises(ClefsError) as exc:
        brief.submit_template(cur, s, name="clefs_j1", language="en", category="UTILITY", components=[])
    assert exc.value.code == "not_connected"

    assert guests.is_known_guest_sender(cur, s, phone_hmac=b"x") is False
    # Built by the guests lane: an unknown identity has no marketing consent.
    assert guests.can_send_marketing(cur, s, identity_hmacs=[b"x"], channel="whatsapp", at=now, phase="post_stay") == (False, "no_consent")
    assert guests.record_external_send(cur, s, identity_hmacs=[b"x"], channel="email", source="demand", sent_at=now, ref="c1") is None
    assert guests.opt_out(cur, s, kind="phone", value="+971500000000", source="whatsapp") is None
    assert guests.journey_timeline(cur, s, 1) == [] and guests.arrival_prefs(cur, s, day=d) == []

    # Built by the inquiries lane: an enabled hotel gets a real inquiry back;
    # NotConfigured is kept for a hotel that switched Inquiry Guard off.
    made = inquiries.create_inquiry(cur, s, source="concierge", text="12 rooms", contact=inquiries.ContactIn(name="A"), actor=None)
    assert {"id", "ref", "stage"} <= set(made) and made["id"]
    from clefs.core.hotels import set_module_settings

    set_module_settings(cur, s, "inquiries", {}, actor=None, enabled=False)
    with pytest.raises(ClefsError) as exc:
        inquiries.create_inquiry(cur, s, source="concierge", text="12 rooms", contact=inquiries.ContactIn(name="A"), actor=None)
    assert exc.value.code == "not_configured"
    set_module_settings(cur, s, "inquiries", {}, actor=None, enabled=True)
    assert inquiries.brief_summary(cur, s, day=d) == {} and inquiries.verified_outcomes(cur, s, start=d, end=d) == []

    assert finance.weekly_pulse(cur, s, week_start=d) is None and finance.monthly_summary(cur, s, month=d) is None


def test_record_own_contact_enforces_transfer_consent_first(cur, hotel_uae):
    from clefs.modules.guests import api as guests

    kw = dict(kind="phone", value="+971500000000", capture_source="registration_card", evidence={}, actor=None)
    with pytest.raises(guests.ConsentNotAllowed) as exc:
        guests.record_own_contact(cur, hotel_uae.slug, consents={"transfer_abroad": True}, consent_text_versions={}, **kw)
    assert (exc.value.status, exc.value.code, exc.value.reason) == (422, "consent_not_allowed", "transfer_abroad_required")
    with pytest.raises(ClefsError) as exc:
        guests.record_own_contact(cur, hotel_uae.slug, consents={"transfer_abroad": True},
                                  consent_text_versions={"transfer_abroad": "v1"}, **kw)
    # Built by the guests lane: past the transfer check, a registration-card
    # capture needs its evidence (card_crop or staff_attestation); the stored
    # path is covered by tests/modules/guests.
    assert (exc.value.status, exc.value.code) == (422, "evidence_required")


def test_create_followup_files_a_human_task_until_service_builds_its_queue(cur, hotel_uae):
    from clefs.modules.service import api as service

    due = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
    out = service.create_followup(cur, hotel_uae.slug, kind="guest_feedback", summary="Rated the stay 2/5",
                                  guest_ref="res-42", owner_role="gm", due_at=due, source="j7_feedback")
    a = actions.get_action(cur, hotel_uae.slug, out["action_id"])
    assert out["kind"] == "guest_feedback" and out["status"] == "proposed" and out["id"] == a["id"]
    assert (a["type"], a["owner_role"], a["due_at"]) == ("core.task", "gm", due)
    assert a["params"]["followup_kind"] == "guest_feedback" and a["params"]["guest_ref"] == "res-42"
    with pytest.raises(ValueError):
        service.create_followup(cur, hotel_uae.slug, kind="gossip", summary="x", guest_ref=None, owner_role="gm",
                                due_at=None, source="j7_feedback")


def test_verified_profit_reads_the_ledger(cur, hotel_uae, owner_ctx, clock):
    from clefs.modules.finance import api as finance

    clock.freeze(datetime(2026, 8, 10, 8, tzinfo=timezone.utc))
    for outcome, counts in ((302000, True), (121000, True), (73000, False)):
        a = actions.create_action(cur, hotel_uae.slug, type="core.task", title="x", actor=SYSTEM)
        actions.decide(cur, a["id"], "approve", actor=owner_ctx)
        actions.mark_done(cur, a["id"], actor=owner_ctx)
        actions.record_verification(cur, a["id"], result=VerifyResult(status="verified", outcome_minor=outcome,
                                                                       counts_toward_verified_profit=counts))
    out = finance.verified_profit(cur, hotel_uae.slug, start=date(2026, 8, 1), end=date(2026, 8, 31))
    assert (out["verified_minor"], out["verified_value"], out["currency"], out["actions"]) == (423000, "4230.00", "AED", 2)
    assert out["by_module"] == {"core": 423000}
    assert finance.verified_profit(cur, hotel_uae.slug, start=date(2026, 9, 1), end=date(2026, 9, 30))["verified_minor"] == 0


# ─── D1: stay events ───


def test_stay_event_names_and_payload_are_frozen():
    from typing import get_args

    from clefs.core import stay_events as se

    assert se.STAY_EVENTS == (
        "created", "dates_changed", "cancelled", "no_show", "checked_in", "checked_out", "contact_changed"
    )
    assert set(get_args(se.StayEventName)) == set(se.STAY_EVENTS) == set(se.CHANGE_FIELDS)
    assert set(se.StayEvent.__annotations__) == {"id", "event", "hotel_slug", "reservation_id", "occurred_at", "changes"}
    assert se.CHANGE_FIELDS["dates_changed"] == ("check_in", "check_out")
    assert se.CHANGE_FIELDS["contact_changed"] == ("reservation_contact_ids",)  # ids, never contact values
    assert (se.STAY_EVENTS_TABLE, se.DISPATCH_JOB) == ("stay_events", "stays.dispatch_events")
