"""Client contract guards (fix lane "client").

The console is TypeScript and the server is Python, so a drift between them
compiles on both sides and fails only in a browser: a nav tab gated on a
permission no module registers is invisible to every role but Clefs admins,
and a type that lags the server hides fields (the K1 states, the autonomy
ledger) from every screen written against it. These tests read the client
sources and compare them with what the server actually registers and emits.

The dial helpers (settings/autonomy/dial.ts) are exercised for real: node
transpiles them with the client's own TypeScript and runs them against JSON
that the server produced in this test, so the page's parsing of limits
schemas, money limits and demotion signals is pinned to the live contract.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import typing
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from clefs import registry
from clefs.auth import permissions
from clefs.auth.deps import Principal
from clefs.core import actions, signals
from clefs.core.actions import ActionType
from tests.foundation.route_walk import route_paths

ROOT = Path(__file__).resolve().parents[3]
CLIENT = ROOT / "client"
APP_DIR = CLIENT / "app"
CONSOLE = APP_DIR / "app" / "[hotelSlug]" / "(console)"
TYPES_TS = APP_DIR / "lib" / "types.ts"
NAV_TS = CONSOLE / "nav.ts"
SHELL_TSX = CONSOLE / "ConsoleShell.tsx"
SETTINGS = CONSOLE / "settings"
AUTONOMY_PAGE = SETTINGS / "autonomy" / "page.tsx"
DIAL_TS = SETTINGS / "autonomy" / "dial.ts"

pytestmark = pytest.mark.skipif(not TYPES_TS.exists(), reason="client checkout not present")

SYSTEM = Principal(kind="service", label="test")

# ─── TypeScript source readers ───


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//[^\n\"]*$", "", src)


def _types() -> str:
    return TYPES_TS.read_text()


def ts_union(name: str, src: str | None = None) -> set[str]:
    """String literals of `export type <name> = "a" | "b" ...;`."""
    m = re.search(rf"export type {name}\s*=(.*?);", src or _types(), re.S)
    assert m, f"types.ts has no union {name}"
    return set(re.findall(r'"([^"]+)"', _strip_comments(m.group(1))))


def ts_array(name: str) -> list[str]:
    """String literals of `export const <name>: ... = [ ... ]`, in order."""
    m = re.search(rf"export const {name}\b[^=]*=\s*\[(.*?)\]", _types(), re.S)
    assert m, f"types.ts has no array {name}"
    return re.findall(r'"([^"]+)"', m.group(1))


def ts_set(name: str) -> set[str]:
    m = re.search(rf"export const {name}\b[^=]*=\s*new Set<[^>]*>\(\[(.*?)\]\)", _types(), re.S)
    assert m, f"types.ts has no Set {name}"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def ts_record_keys(name: str) -> set[str]:
    m = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}};", _types(), re.S)
    assert m, f"types.ts has no record {name}"
    return set(re.findall(r"(?m)^\s+(\w+):", m.group(1)))


def ts_interface(name: str) -> dict[str, bool]:
    """Top-level fields of `export interface <name> { ... }` -> optional?"""
    m = re.search(rf"export interface {name}\b[^{{]*\{{(.*?)\n\}}", _types(), re.S)
    assert m, f"types.ts has no interface {name}"
    body = _strip_comments(m.group(1))
    return {f: bool(q) for f, q in re.findall(r"(?m)^  ([A-Za-z_$][\w$]*)(\?)?:", body)}


def literal_values(annotation: object) -> set[str]:
    """Every string in a (possibly Optional) Literal annotation."""
    out: set[str] = set()
    for arg in typing.get_args(annotation):
        if isinstance(arg, str):
            out.add(arg)
        else:
            out |= literal_values(arg)
    return out


def check_literals(cur, table: str, column: str) -> set[str]:
    """Allowed values of `CHECK (<column> IN (...))` on `table`, read back from
    the live schema (Postgres renders it as `<column> = ANY (ARRAY[...])`)."""
    cur.execute(
        "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'c'",
        (table,),
    )
    for row in cur.fetchall():
        if re.search(rf"(?<![\w.]){column} = ANY", row["d"]):
            return set(re.findall(r"'([^']+)'", row["d"]))
    raise AssertionError(f"no CHECK on {table}.{column}")


# ─── Nav tabs gate on permissions the server issues (finding correctness-nav-perm-keys) ───

NAV_ENTRY = re.compile(r'\{ key: "(?P<key>[a-z]+)"(?P<body>[^{}]*)\}')


def nav_entries() -> list[dict[str, str]]:
    out = []
    for m in NAV_ENTRY.finditer(NAV_TS.read_text()):
        body = m.group("body")
        entry = {"key": m.group("key")}
        for field in ("href", "perm", "module"):
            fm = re.search(rf'\b{field}: "([^"]+)"', body)
            assert fm, f"nav entry {entry['key']!r} has no {field}"
            entry[field] = fm.group(1)
        out.append(entry)
    return out


def test_every_nav_tab_gates_on_its_own_modules_view_permission(app):
    """Field-order independent: each tab's perm is registered BY the module
    the tab names, so neither a typo nor a sibling's key can gate it."""
    entries = nav_entries()
    assert {e["key"] for e in entries} == ts_union("NavKey", NAV_TS.read_text())
    registered = permissions.all_permissions()
    for e in entries:
        assert e["perm"] in registered, f"nav tab {e['key']!r} gates on {e['perm']!r}, which the server never issues"
        owner_module = "core" if e["module"] == "core" else e["module"]
        assert permissions.owner_of(e["perm"]) == owner_module, (e, permissions.owner_of(e["perm"]))
        if e["module"] != "core":
            assert e["href"] in registry.ROUTE_SEGMENTS[e["module"]]


def test_profit_guard_and_menu_tabs_reach_their_roles(app):
    """The two tabs the findings named: registry names (profit_guard, upsell)
    differ from their route segments (profit, menu). The keys a registry-name
    guess would produce do not exist, so they would hide the tabs."""
    by_key = {e["key"]: e for e in nav_entries()}
    registered = permissions.all_permissions()
    assert by_key["profit"]["perm"] == "profit.view" and by_key["profit"]["module"] == "profit_guard"
    assert by_key["menu"]["perm"] == "menu.view" and by_key["menu"]["module"] == "upsell"
    assert "profit_guard.view" not in registered and "upsell.view" not in registered
    for role in ("owner", "gm", "finance"):
        assert "profit.view" in permissions.permissions_for_role(role)
    for role in ("owner", "gm", "dept_head", "front_desk"):
        assert "menu.view" in permissions.permissions_for_role(role)


def test_every_permission_the_console_shell_and_settings_check_is_registered(app):
    registered = permissions.all_permissions()
    files = [SHELL_TSX, *sorted(SETTINGS.rglob("*.ts*"))]
    seen: set[str] = set()
    for f in files:
        src = f.read_text()
        for perm in re.findall(r'\bcan\(\s*"([a-z_.]+)"\s*\)|\bperm: "([a-z_.]+)"', src):
            key = perm[0] or perm[1]
            seen.add(key)
            assert key in registered, f"{f.relative_to(CLIENT)} checks {key!r}, which the server never issues"
    assert {"core.autonomy.view", "core.autonomy.manage", "core.autonomy.raise_auto"} <= seen


# ─── types.ts matches the server's constants (finding spec-client-types-drift) ───


def test_signal_unions_match_the_server():
    assert ts_union("SignalSection") == set(signals.SECTIONS)
    assert ts_record_keys("SIGNAL_SECTION_LABELS") == set(signals.SECTIONS)
    assert ts_union("SignalStatus") == set(signals.SIGNAL_STATUSES)
    assert ts_union("SignalCadence") == set(signals.CADENCES)
    assert ts_array("SIGNAL_CADENCES") == list(signals.CADENCES)
    assert ts_array("OPEN_SIGNAL_STATUSES") == list(signals.OPEN_STATUSES)
    assert ts_union("EvidenceKind") == literal_values(signals.Evidence.model_fields["kind"].annotation)
    src = _types()
    assert 'AUTONOMY_PROMOTION_KIND = "core.autonomy_promotion"' in src
    assert 'AUTONOMY_DEMOTED_KIND = "core.autonomy_demoted"' in src


def test_action_unions_match_the_server(cur):
    statuses = ts_union("ActionStatus")
    assert statuses == check_literals(cur, "actions", "status")
    assert {"observed", "failed", "expired"} <= statuses  # Canon 7 + K1
    assert ts_record_keys("ACTION_STATUS_LABELS") == statuses
    assert ts_set("TERMINAL_ACTION_STATUSES") == set(actions.TERMINAL)
    assert ts_union("Decision") == set(actions.DECISIONS)
    assert ts_union("DecidedVia") == set(typing.get_args(actions.Via))
    assert ts_union("ActionSurface") == check_literals(cur, "actions", "surface")
    assert ts_union("TemplateCategory") == literal_values(actions.ExecResult.model_fields["template_category"].annotation)
    assert ts_union("Attribution") == literal_values(actions.VerifyResult.model_fields["attribution"].annotation)


def test_autonomy_unions_match_the_server(cur):
    # Order matters: the client ranks levels by their index, as LEVELS does.
    assert ts_array("AUTONOMY_LEVELS") == list(actions.LEVELS)
    assert ts_union("AutonomyLevel") == set(actions.LEVELS)
    assert ts_set("AUTO_AUTONOMY_LEVELS") == set(actions.AUTO_LEVELS)
    assert ts_record_keys("AUTONOMY_LEVEL_LABELS") == set(actions.LEVELS)
    assert ts_record_keys("AUTONOMY_LEVEL_HINTS") == set(actions.LEVELS)
    assert ts_union("AutonomyEventKind") == check_literals(cur, "action_autonomy_events", "kind")
    assert ts_union("DemotionTrigger") == set(actions.INCIDENT_KINDS) | {"manual"}


# ─── types.ts carries every field the server emits ───

LEDGER_FIELDS = (
    "status", "terminal", "currency", "expected_value", "outcome_value", "expires_at", "limit_reasons",
    "dial_level_at_creation", "dial_level_at_execution", "policy_version",
    "template_id", "template_category", "consent_basis", "reversal_handle",
)


def test_action_type_covers_the_api_row(cur, hotel_uae):
    created = actions.create_action(cur, hotel_uae.slug, type="core.task", title="Check the minibar", actor=SYSTEM)
    row = actions.get_action(cur, hotel_uae.slug, created["id"])
    fields = ts_interface("Action")
    missing = set(row) - set(fields)
    assert not missing, f"Action in types.ts lacks {sorted(missing)}"
    for f in LEDGER_FIELDS:
        assert fields[f] is False, f"Action.{f} is always sent, so it must not be optional"
    assert "created" in created  # CreateActionResult adds it
    ev_fields = ts_interface("ActionEvent")
    for ev in row["events"]:
        assert set(ev) <= set(ev_fields), sorted(set(ev) - set(ev_fields))


def test_suppressed_action_type_matches_the_dial_off_answer(cur, hotel_uae, owner_ctx):
    actions.set_autonomy(cur, hotel_uae.slug, "core.task", level="off", actor=owner_ctx)
    off = actions.create_action(cur, hotel_uae.slug, type="core.task", title="x", actor=SYSTEM)
    assert set(off) == set(ts_interface("SuppressedAction"))


def test_signal_type_covers_the_api_row(cur, hotel_uae):
    signals.upsert_signal(
        cur,
        hotel_uae.slug,
        signals.SignalIn(
            module="core", kind="core.autonomy_demoted", dedupe_key="core.autonomy_demoted:core.task:1",
            title="Task: lowered", confidence=1.0, impact_minor=12_345, currency="AED",
        ),
    )
    [row] = signals.list_signals(cur, hotel_uae.slug, modules=["core"])
    fields = ts_interface("Signal")
    missing = set(row) - set(fields)
    assert not missing, f"Signal in types.ts lacks {sorted(missing)}"
    for f in ("section", "cadence", "rankable", "impact_minor", "impact_amount", "currency", "confidence", "status"):
        assert fields[f] is False, f"Signal.{f} is always sent, so it must not be optional"
    assert (row["impact_minor"], row["impact_amount"], row["currency"]) == (12_345, "123.45", "AED")


def test_autonomy_setting_type_matches_get_autonomy(cur, hotel_uae):
    slug = hotel_uae.slug
    actions._autonomy_event(  # an open promotion suggestion, as core.autonomy_review writes it
        cur, slug, "core.task", "promotion_suggested", from_level="suggest", to_level="approve", policy_version=0
    )
    row = actions.get_autonomy(cur, slug, "core.task")
    assert set(row) == set(ts_interface("AutonomySetting"))
    assert set(row["stats"]) == set(ts_interface("AutonomyStats"))
    assert set(row["open_suggestion"]) == set(ts_interface("AutonomySuggestion"))


# ─── The autonomy settings page (finding spec-autonomy-console-missing) ───


def test_autonomy_page_is_wired_to_the_shipped_routes_and_permissions(app):
    assert AUTONOMY_PAGE.exists() and DIAL_TS.exists()
    layout = (SETTINGS / "layout.tsx").read_text()
    assert re.search(r'key: "autonomy".*sub: "/autonomy".*perm: "core.autonomy.view"', layout)
    page = AUTONOMY_PAGE.read_text()
    for needle in ('"/autonomy"', "/autonomy/${encodeURIComponent(row.action_type)}", "/autonomy/suggestions/${s.event_id}/${verb}"):
        assert needle in page, needle
    paths = route_paths(app.routes)
    assert {
        "/hotels/{slug}/autonomy",
        "/hotels/{slug}/autonomy/{action_type}",
        "/hotels/{slug}/autonomy/suggestions/{event_id}/accept",
        "/hotels/{slug}/autonomy/suggestions/{event_id}/dismiss",
    } <= paths
    # The page offers auto levels, and Accept on a step above approve, to
    # raise_auto holders only; that must stay owner-only while managers can
    # still lower a dial and accept a step up to approve.
    owner, gm = permissions.permissions_for_role("owner"), permissions.permissions_for_role("gm")
    assert {"core.autonomy.view", "core.autonomy.manage", "core.autonomy.raise_auto"} <= owner
    assert "core.autonomy.manage" in gm and "core.autonomy.raise_auto" not in gm


# ─── dial.ts, run for real against server output ───

NODE_HARNESS = r"""
const fs = require("fs");
const ts = require(process.env.TS_PATH);
for (const ext of [".ts", ".tsx"]) {
  require.extensions[ext] = (m, filename) => {
    const out = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
      fileName: filename,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    });
    m._compile(out.outputText, filename);
  };
}
const d = require(process.env.DIAL_TS);
const input = JSON.parse(fs.readFileSync(process.env.INPUT, "utf8"));
const row = input.row;
const fields = d.limitFields(row.limits_schema);
const draft = d.draftFromLimits(fields, row.limits, "AED");
const same = d.limitsFromDraft(fields, draft, row.limits, "AED");
const edited = d.limitsFromDraft(fields, { ...draft, max_amount_minor: "300", max_per_day: "" }, row.limits, "AED");
const bad = d.limitsFromDraft(fields, { ...draft, max_amount_minor: "12.345", max_per_day: "two" }, row.limits, "AED");
const kwd = d.limitsFromDraft(fields, { ...draft, max_amount_minor: "1.234" }, row.limits, "KWD");
const opts = can => d.levelOptions(row, can).filter(o => o.disabled).map(o => o.value);
const demoted = d.demotionsByType(input.signals);
console.log(JSON.stringify({
  fields: Object.fromEntries(fields.map(f => [f.key, { kind: f.kind, nullable: f.nullable, options: f.options ?? null }])),
  draft,
  same: { value: same.value, errors: same.errors, unchanged: d.sameLimits(same.value, row.limits) },
  edited: edited.value,
  badErrors: Object.keys(bad.errors).sort(),
  kwdMinor: kwd.value.max_amount_minor,
  disabledWithoutRaise: opts(false),
  disabledWithRaise: opts(true),
  raises: [d.raisesToAuto("approve", "auto_within_limits"), d.raisesToAuto("auto", "auto_within_limits"), d.raisesToAuto("off", "approve")],
  demotedTypes: [...demoted.keys()],
  signalTypes: input.signals.map(s => d.actionTypeOfAutonomySignal(s)),
  money: input.money.map(([t, n]) => d.majorToMinor(t, n)),
  label: d.labelForKey("max_offer_price_minor"),
}));
"""

MONEY_CASES = [
    (["250", 2], 25_000),
    (["250.5", 2], 25_050),
    (["250.50", 2], 25_050),
    (["1,250.50", 2], 125_050),
    (["0.07", 2], 7),
    (["7", 3], 7_000),
    (["12", 0], 12),
    (["12.5", 0], None),
    (["250.505", 2], None),
    (["-5", 2], None),
    (["abc", 2], None),
    (["", 2], None),
]


class GestureLimits(BaseModel):
    max_amount_minor: int = 0
    max_per_day: int | None = None
    once_per_stay: bool = True
    channels: list[str] = ["whatsapp"]
    tier: Literal["any", "gold"] = "any"


def _node() -> str | None:
    node = shutil.which("node")
    for candidate in (node, "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if not candidate or not Path(candidate).exists():
            continue
        try:
            out = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if re.match(r"v(1[89]|[2-9]\d)\.", out.strip()):
            return candidate
    return None


@pytest.fixture
def gesture_type():
    t = ActionType(
        key="core.test_w0b_client_gesture", module="core", label="Recovery gesture", default_level="suggest",
        max_level="auto_within_limits", limits_model=GestureLimits,
    )
    actions.register_action_type(t)
    yield t
    actions.unregister_action_type(t.key)


def test_dial_helpers_parse_what_the_server_sends(cur, client_as, hotel_uae, gesture_type, tmp_path):
    node = _node()
    ts_path = CLIENT / "node_modules" / "typescript"
    if node is None or not ts_path.exists():
        pytest.skip("node 18+ and client/node_modules/typescript are needed to run dial.ts")
    slug, key = hotel_uae.slug, gesture_type.key
    owner = client_as("owner")
    base = f"/hotels/{slug}/autonomy/{key}"
    limits = {"max_amount_minor": 25_050, "max_per_day": 3, "once_per_stay": False, "channels": ["whatsapp", "sms"], "tier": "gold"}
    assert owner.put(base, json={"level": "approve", "limits": limits}).status_code == 200
    actions.demote(cur, slug, key, trigger="guest_complaint", reason="A guest complained")
    actions._autonomy_event(cur, slug, key, "promotion_suggested", from_level="approve", to_level="auto_within_limits",
                            policy_version=2)
    row = owner.get(base).json()
    assert row["open_suggestion"]["to_level"] == "auto_within_limits"
    sigs = owner.get(f"/hotels/{slug}/signals", params={"module": "core", "section": "compliance"}).json()
    assert any(s["kind"] == "core.autonomy_demoted" for s in sigs)

    (tmp_path / "input.json").write_text(json.dumps({"row": row, "signals": sigs, "money": [c for c, _ in MONEY_CASES]}))
    (tmp_path / "harness.js").write_text(NODE_HARNESS)
    proc = subprocess.run(
        [node, str(tmp_path / "harness.js")],
        capture_output=True, text=True, timeout=90, cwd=CLIENT,
        env={"PATH": "/usr/bin:/bin", "TS_PATH": str(ts_path), "DIAL_TS": str(DIAL_TS), "INPUT": str(tmp_path / "input.json")},
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    # The form is generated from the Pydantic schema the server publishes.
    assert out["fields"] == {
        "max_amount_minor": {"kind": "money", "nullable": False, "options": None},
        "max_per_day": {"kind": "integer", "nullable": True, "options": None},
        "once_per_stay": {"kind": "boolean", "nullable": False, "options": None},
        "channels": {"kind": "list", "nullable": False, "options": None},
        "tier": {"kind": "enum", "nullable": False, "options": ["any", "gold"]},
    }
    # Money limits are edited in major units and go back as exact minor units.
    assert out["draft"]["max_amount_minor"] == "250.50" and out["draft"]["channels"] == "whatsapp, sms"
    assert out["same"] == {"value": row["limits"], "errors": {}, "unchanged": True}
    assert out["edited"]["max_amount_minor"] == 30_000 and out["edited"]["max_per_day"] is None
    assert out["badErrors"] == ["max_amount_minor", "max_per_day"]
    assert out["kwdMinor"] == 1_234  # KWD has 3 minor digits
    # The body the client builds is one the server accepts, unchanged.
    saved = owner.put(base, json={"level": "approve", "limits": out["edited"]})
    assert saved.status_code == 200, saved.json()
    assert saved.json()["limits"] == out["edited"]

    # Levels: above the ceiling never, auto levels only with raise_auto.
    assert out["disabledWithoutRaise"] == ["auto_within_limits", "auto"]
    assert out["disabledWithRaise"] == ["auto"]
    assert out["raises"] == [True, False, False]

    # Demotions are tied to their row through the server's own dedupe key.
    assert out["demotedTypes"] == [key]
    assert key in out["signalTypes"]
    assert out["money"] == [want for _, want in MONEY_CASES]
    assert out["label"] == "Max offer price"


# ─── Conventions ───


def test_no_em_dash_in_the_client_files_this_lane_owns():
    files = [TYPES_TS, NAV_TS, APP_DIR / "app" / "StaffDashboard.tsx", APP_DIR / "app" / "charts.tsx", *SETTINGS.rglob("*.ts*")]
    offenders = [str(f.relative_to(CLIENT)) for f in files if "\u2014" in f.read_text()]
    assert not offenders, offenders
