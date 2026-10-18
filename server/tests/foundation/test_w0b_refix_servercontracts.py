"""
Wave 0b refix, group server-contracts: regressions for the spec findings.

  - spec-autonomy-seeded-rows: no hotel gets action_autonomy rows up front
    (04 spec 1.4 / 7.4); the first set_autonomy or demote logs a kind='seed'
    event with the default then in force.
  - spec-cadence-default-daily: register_signal_kind has no cadence default
    (F7), and an unregistered kind without a cadence is 400.
  - spec-dial-revoked-surface: a revoked dial approval lands where
    create_action would place a new action at the new level.
  - spec-contracts-adjust-row: approved -> adjust works before execution and
    CONTRACTS documents every TRANSITIONS pair.
  - spec-contracts-email-headers: every Content-* email header is refused.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from clefs import cli, jobs
from clefs.auth.deps import Principal
from clefs.core import actions, signals
from clefs.core.actions import ActionType, Gate
from clefs.core.errors import ClefsError
from clefs.notify import validate
from clefs.notify.validate import PayloadError
from clefs.seed import demo_uae

SYSTEM = Principal(kind="service", label="test")
CONTRACTS = Path(actions.__file__).resolve().parents[1] / "CONTRACTS.md"


# ─── Fixtures and helpers ───


class Runs:
    def __init__(self) -> None:
        self.params: list[dict] = []

    def __call__(self, ctx: actions.ActionCtx) -> dict:
        self.params.append(dict(ctx.params))
        return {"sent": True}


# A gate the test can close between creation and execution (a template
# revoked mid-flight): closed caps the level at observe, like template_gate.
GATE = {"open": True}


def _gate(ctx: actions.ActionCtx) -> Gate:
    return Gate(max_level="auto") if GATE["open"] else Gate(max_level="observe", reasons=["template revoked"])


@pytest.fixture
def runs():
    r = Runs()
    GATE["open"] = True
    types = [
        ActionType(key="core.rfx_send", module="core", label="Send", executor=r, default_level="auto"),
        ActionType(key="core.rfx_gated", module="core", label="Journey", executor=r, default_level="auto", gate=_gate),
        ActionType(key="core.rfx_quote", module="core", label="Quote", executor=r),  # default approve
        ActionType(key="core.rfx_draft", module="core", label="Draft", default_level="suggest"),
    ]
    for t in types:
        actions.register_action_type(t)
    yield r
    for t in types:
        actions.unregister_action_type(t.key)
    GATE["open"] = True


def _run_queue():
    return jobs.run_pending(only=["core.actions.execute"])


def _signal(cur, slug: str, key: str) -> dict:
    return signals.upsert_signal(cur, slug, signals.SignalIn(
        module="core", kind="core.rfx_adhoc", dedupe_key=key, title=key, confidence=0.9,
        section="money_available", cadence="daily",
    ))


def _dial_rows(cur, slug: str) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM action_autonomy WHERE hotel_slug = %s", (slug,))
    return cur.fetchone()["n"]


def _dial_events(cur, slug: str) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM action_autonomy_events WHERE hotel_slug = %s", (slug,))
    return cur.fetchone()["n"]


# ─── spec-autonomy-seeded-rows ───


def test_cli_create_hotel_writes_no_dial_rows(cur, runs):
    assert cli.main(["create-hotel", "--slug", "rfx-cli", "--name", "CLI Hotel"]) == 0
    assert _dial_rows(cur, "rfx-cli") == 0 and _dial_events(cur, "rfx-cli") == 0
    res = actions.resolve_level(cur, "rfx-cli", "core.rfx_draft")
    assert (res.configured, res.source, res.policy_version) == ("suggest", "default", 0)
    assert not hasattr(actions, "seed_autonomy")  # nothing left that pins every type


def test_demo_seed_writes_no_dial_rows(cur, runs):
    assert demo_uae.seed(cur, slug="rfx-demo")["created"] is True
    assert _dial_rows(cur, "rfx-demo") == 0 and _dial_events(cur, "rfx-demo") == 0
    assert all(row["source"] == "default" for row in actions.list_autonomy(cur, "rfx-demo"))


def test_a_changed_default_reaches_a_hotel_that_never_set_the_dial(cur, runs):
    """The point of 1.4: a lane lowering its type's default after a safety
    review changes every hotel that never touched that dial."""
    assert cli.main(["create-hotel", "--slug", "rfx-later", "--name", "Later"]) == 0
    assert actions.resolve_level(cur, "rfx-later", "core.rfx_send").configured == "auto"
    actions.unregister_action_type("core.rfx_send")
    actions.register_action_type(ActionType(key="core.rfx_send", module="core", label="Send", executor=runs,
                                            default_level="approve"))
    assert actions.resolve_level(cur, "rfx-later", "core.rfx_send").configured == "approve"


def test_the_first_set_logs_the_default_then_in_force(cur, hotel_uae, runs, owner_ctx):
    slug = hotel_uae.slug
    actions.set_autonomy(cur, slug, "core.rfx_draft", level="approve", actor=owner_ctx)
    newest, seed = actions.list_autonomy_events(cur, slug, "core.rfx_draft")
    assert (newest["kind"], newest["from_level"], newest["to_level"], newest["policy_version"]) == ("set", "suggest", "approve", 1)
    assert (seed["kind"], seed["from_level"], seed["to_level"], seed["policy_version"]) == ("seed", None, "suggest", 0)
    assert seed["actor_type"] == "system" and newest["actor_type"] == "user"
    actions.set_autonomy(cur, slug, "core.rfx_draft", level="suggest", actor=owner_ctx)
    kinds = [e["kind"] for e in actions.list_autonomy_events(cur, slug, "core.rfx_draft")]
    assert kinds == ["set", "set", "seed"]  # only the first row write seeds


def test_the_first_demotion_logs_the_seed_too(cur, hotel_uae, runs):
    slug = hotel_uae.slug
    actions.demote(cur, slug, "core.rfx_send", trigger="optout_spike")
    newest, seed = actions.list_autonomy_events(cur, slug, "core.rfx_send")
    assert (newest["kind"], newest["from_level"], newest["to_level"]) == ("demoted", "auto", "approve")
    assert (seed["kind"], seed["to_level"], seed["policy_version"]) == ("seed", "auto", 0)
    actions.demote(cur, slug, "core.rfx_send", trigger="guest_complaint")
    kinds = [e["kind"] for e in actions.list_autonomy_events(cur, slug, "core.rfx_send")]
    assert kinds == ["demoted", "demoted", "seed"]


# ─── spec-cadence-default-daily ───


def test_register_signal_kind_requires_a_cadence():
    param = inspect.signature(signals.register_signal_kind).parameters["cadence"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY and param.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        signals.register_signal_kind("core.rfx_nocadence", label="x", default_owner_role="gm", section="reviews")  # type: ignore[call-arg]
    assert signals.get_kind("core.rfx_nocadence") is None
    for kind in signals.signal_kinds():  # every kind states its brief
        assert kind.cadence in signals.CADENCES


def test_an_unregistered_kind_needs_a_cadence_not_a_silent_daily(cur, hotel_uae):
    s = signals.SignalIn(module="core", kind="core.rfx_adhoc", dedupe_key="nc", title="T", confidence=0.5, section="reviews")
    with pytest.raises(ClefsError) as exc:
        signals.upsert_signal(cur, hotel_uae.slug, s)
    assert exc.value.status == 400 and exc.value.code == "unknown_signal_kind"
    cur.execute("SELECT COUNT(*) AS n FROM signals WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 0
    weekly = signals.upsert_signal(cur, hotel_uae.slug, s.model_copy(update={"cadence": "weekly"}))
    assert weekly["cadence"] == "weekly" and weekly["section"] == "reviews"


# ─── spec-dial-revoked-surface ───


@pytest.mark.parametrize(
    ("new_level", "status", "surface"),
    [
        ("approve", "proposed", "brief"),
        ("suggest", "proposed", "console"),  # a draft is never pushed to the brief
        ("observe", "observed", "none"),
        ("off", "cancelled", "none"),
    ],
)
def test_a_revoked_dial_approval_lands_where_the_new_level_puts_it(
    cur, hotel_uae, runs, owner_ctx, new_level, status, surface
):
    slug = hotel_uae.slug
    sig = _signal(cur, slug, f"rv-{new_level}")
    a = actions.create_action(cur, slug, type="core.rfx_send", title="Send", signal_id=sig["id"], actor=SYSTEM)
    assert a["status"] == "approved" and a["decided_via"] == "dial"
    assert signals.get_signal(cur, slug, sig["id"])["status"] == "decided"
    actions.set_autonomy(cur, slug, "core.rfx_send", level=new_level, actor=owner_ctx)
    _run_queue()
    assert runs.params == []  # never executed
    back = actions.get_action(cur, slug, a["id"])
    assert (back["status"], back["surface"]) == (status, surface)
    assert back["decided_at"] is None and back["decided_via"] is None and back["executed_at"] is None
    assert back["terminal"] is (status != "proposed")
    last = actions.list_events(cur, a["id"])[-1]
    assert (last["event"], last["from_status"], last["to_status"], last["actor_type"]) == (
        "dial_revoked", "approved", status, "dial")
    assert last["data"]["level"] == new_level
    assert last["data"].get("reason") == ("dial_off" if new_level == "off" else None)
    # At creation only a dial approval marks the signal decided; undo that.
    assert signals.get_signal(cur, slug, sig["id"])["status"] == "open"


def test_a_gate_closing_mid_flight_marks_the_send_observed(cur, hotel_uae, runs):
    slug = hotel_uae.slug
    a = actions.create_action(cur, slug, type="core.rfx_gated", title="J4", actor=SYSTEM)
    assert a["status"] == "approved"
    GATE["open"] = False  # the template was revoked before the executor ran
    _run_queue()
    back = actions.get_action(cur, slug, a["id"])
    assert (back["status"], back["surface"]) == ("observed", "none") and runs.params == []
    assert back["limit_reasons"] == ["template revoked"]


def test_a_human_approval_still_runs_after_the_dial_drops(cur, hotel_uae, runs, owner_ctx):
    slug = hotel_uae.slug
    a = actions.create_action(cur, slug, type="core.rfx_quote", title="Quote", params={"n": 1}, actor=SYSTEM)
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    actions.set_autonomy(cur, slug, "core.rfx_quote", level="off", actor=owner_ctx)
    _run_queue()
    assert actions.get_action(cur, slug, a["id"])["status"] == "done" and runs.params == [{"n": 1}]


# ─── spec-contracts-adjust-row ───


def test_an_approved_action_can_be_adjusted_until_it_runs(cur, hotel_uae, runs, owner_ctx):
    slug = hotel_uae.slug
    a = actions.create_action(cur, slug, type="core.rfx_quote", title="Quote", params={"price": 100}, actor=SYSTEM)
    actions.decide(cur, a["id"], "approve", actor=owner_ctx)
    edited = actions.decide(cur, a["id"], "adjust", actor=owner_ctx, params_patch={"price": 90})
    assert edited["status"] == "approved" and edited["edited"] is True and edited["params"] == {"price": 90}
    _run_queue()
    assert runs.params == [{"price": 90}]  # the edited quote went out, once
    with pytest.raises(ClefsError) as exc:
        actions.decide(cur, a["id"], "adjust", actor=owner_ctx, params_patch={"price": 80})
    assert exc.value.code == "illegal_transition"


def _state_table() -> list[list[str]]:
    text = CONTRACTS.read_text()
    start = text.index("**State machine**")
    rows = []
    for line in text[start:].splitlines()[1:]:
        if rows and not line.startswith("|"):
            break
        if line.startswith("|") and not line.startswith("|---") and not line.startswith("| From"):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows


def test_contracts_state_table_documents_every_decision_transition():
    rows = _state_table()
    assert rows, "CONTRACTS.md state machine table not found"
    missing = []
    for (frm, decision), to in actions.TRANSITIONS.items():
        if not any(
            frm in re.findall(r"[a-z_]+", r[0]) and re.search(rf"\b{decision}\b", r[1]) and re.search(rf"\b{to}\b", r[2])
            for r in rows
        ):
            missing.append(f"{frm} --{decision}--> {to}")
    assert not missing, f"CONTRACTS.md Part 2 state table lacks: {missing}"


# ─── spec-contracts-email-headers ───


@pytest.mark.parametrize(
    "name", ["Content-Type", "content-transfer-encoding", "Content-Disposition", "Content-ID", "CONTENT-LANGUAGE",
             "Content-Location"],
)
def test_every_content_header_is_refused(name):
    with pytest.raises(PayloadError):
        validate.email_headers({name: "x"})


def test_ordinary_headers_still_pass():
    out = validate.email_headers({"List-Unsubscribe": "<https://clefs.world/u/a>", "In-Reply-To": "<m@x>",
                                  "X-Content-Note": "not a MIME header"})
    assert [h["Name"] for h in out] == ["List-Unsubscribe", "In-Reply-To", "X-Content-Note"]
