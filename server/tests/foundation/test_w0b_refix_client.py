"""Refix lane "client": the autonomy level select and the types.ts sections.

correctness-client-blocks-lowering-auto: dial.ts levelOptions closed every
auto level other than the current one to a user without
core.autonomy.raise_auto, so a GM on "Auto" could not pick "Auto within
limits" even though set_autonomy allows it (only a RAISE into an auto level
needs raise_auto; "a GM can always make a dial safer"). The guard here does
not restate the rule: it asks the live server, one PUT per (current level,
target level) pair, and requires the select to close exactly the choices the
server refuses for that role. A future change on either side that makes
them disagree fails here.

conventions-types-banner-missections-action: a "The autonomy dial" banner
landed above AutonomyLevel, so the Canon 7 Action ledger types after it read
as dial types and two banners claimed the dial. The guard maps every export
in types.ts to its banner.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from clefs.core import actions
from clefs.core.actions import LEVELS, ActionType, level_rank

ROOT = Path(__file__).resolve().parents[3]
CLIENT = ROOT / "client"
APP_DIR = CLIENT / "app"
TYPES_TS = APP_DIR / "lib" / "types.ts"
AUTONOMY_DIR = APP_DIR / "app" / "[hotelSlug]" / "(console)" / "settings" / "autonomy"
DIAL_TS = AUTONOMY_DIR / "dial.ts"
PAGE_TSX = AUTONOMY_DIR / "page.tsx"
TS_PATH = CLIENT / "node_modules" / "typescript"

pytestmark = pytest.mark.skipif(not DIAL_TS.exists(), reason="client checkout not present")

# ─── Node harness: dial.ts transpiled by the client's own TypeScript ───

# require() hook, so dial.ts and what it imports (lib/types, lib/format,
# ../shared) load exactly as written. Loading types.ts at runtime also proves
# the moved dial block has no use-before-definition at module load.
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
const rows = JSON.parse(fs.readFileSync(process.env.INPUT, "utf8"));
const pick = (row, can) => d.levelOptions(row, can);
console.log(JSON.stringify(rows.map(row => ({
  level: row.level,
  max_level: row.max_level,
  gm: pick(row, false).filter(o => o.disabled).map(o => o.value),
  owner: pick(row, true).filter(o => o.disabled).map(o => o.value),
  gmLabels: Object.fromEntries(pick(row, false).map(o => [o.value, o.label])),
}))));
"""


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


def _run_level_options(rows: list[dict], tmp_path: Path) -> list[dict]:
    node = _node()
    if node is None or not TS_PATH.exists():
        pytest.skip("node 18+ and client/node_modules/typescript are needed to run dial.ts")
    (tmp_path / "rows.json").write_text(json.dumps(rows))
    (tmp_path / "harness.js").write_text(NODE_HARNESS)
    proc = subprocess.run(
        [node, str(tmp_path / "harness.js")],
        capture_output=True, text=True, timeout=90, cwd=CLIENT,
        env={"PATH": "/usr/bin:/bin", "TS_PATH": str(TS_PATH), "DIAL_TS": str(DIAL_TS), "INPUT": str(tmp_path / "rows.json")},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ─── The level select offers exactly what the server accepts ───

# One type per ceiling shape: open to "auto", capped at "auto_within_limits",
# and always_approve (ceiling "approve" whatever max_level says).
DIAL_TYPES = [
    ActionType(key="core.test_w0b_refix_open", module="core", label="Open dial", default_level="suggest", max_level="auto"),
    ActionType(key="core.test_w0b_refix_capped", module="core", label="Capped dial", default_level="suggest",
               max_level="auto_within_limits"),
    ActionType(key="core.test_w0b_refix_always", module="core", label="Always approve", default_level="suggest",
               max_level="auto", always_approve=True),
]


@pytest.fixture
def dial_types():
    for t in DIAL_TYPES:
        actions.register_action_type(t)
    yield DIAL_TYPES
    for t in DIAL_TYPES:
        actions.unregister_action_type(t.key)


def _refused(put, base: str, reset, start: str) -> tuple[list[str], dict[str, int]]:
    """Every target the role's PUT refuses from `start`, resetting after each
    accepted one so every attempt starts from the same row."""
    refused, codes = [], {}
    for target in LEVELS:
        r = put(base, json={"level": target})
        codes[target] = r.status_code
        if r.status_code == 200:
            assert reset(base, json={"level": start}).status_code == 200
        else:
            refused.append(target)
    return refused, codes


def test_level_select_matches_what_set_autonomy_accepts(cur, client_as, hotel_uae, dial_types, tmp_path):
    owner, gm = client_as("owner"), client_as("gm")
    slug = hotel_uae.slug
    rows, server = [], []
    for t in dial_types:
        base = f"/hotels/{slug}/autonomy/{t.key}"
        for start in LEVELS:
            if level_rank(start) > level_rank(t.ceiling):
                continue
            assert owner.put(base, json={"level": start}).status_code == 200
            row = owner.get(base).json()
            assert row["level"] == start and row["max_level"] == t.ceiling
            gm_refused, gm_codes = _refused(gm.put, base, owner.put, start)
            owner_refused, _ = _refused(owner.put, base, owner.put, start)
            # Only the two refusals the select models: above the ceiling
            # (422) and a raise into an auto level without raise_auto (403).
            assert set(gm_codes.values()) <= {200, 403, 422}, gm_codes
            rows.append(row)
            server.append({"gm": gm_refused, "owner": owner_refused})

    client = _run_level_options(rows, tmp_path)
    assert len(client) == len(server)
    for row, got, want in zip(rows, client, server):
        where = f"{row['action_type']} at {row['level']}"
        assert got["gm"] == want["gm"], where
        assert got["owner"] == want["owner"], where

    by_key = {(r["action_type"], r["level"]): c for r, c in zip(rows, client)}
    # The finding itself: a GM on Auto may step down to Auto within limits,
    # and the option must not be labelled owner-only.
    on_auto = by_key[("core.test_w0b_refix_open", "auto")]
    assert "auto_within_limits" not in on_auto["gm"]
    assert "owner only" not in on_auto["gmLabels"]["auto_within_limits"]
    assert on_auto["gm"] == []
    # The owner-only guard still holds for a raise into an auto level.
    on_approve = by_key[("core.test_w0b_refix_open", "approve")]
    assert on_approve["gm"] == ["auto_within_limits", "auto"]
    assert on_approve["gmLabels"]["auto"].endswith("(owner only)")
    # A GM keeps the current auto level selectable and may still lower it.
    on_awl = by_key[("core.test_w0b_refix_open", "auto_within_limits")]
    assert on_awl["gm"] == ["auto"]
    # Above the ceiling wins over owner-only in the label.
    capped = by_key[("core.test_w0b_refix_capped", "approve")]
    assert capped["gm"] == ["auto_within_limits", "auto"] and capped["owner"] == ["auto"]
    assert capped["gmLabels"]["auto"].endswith("(above this action's ceiling)")
    always = by_key[("core.test_w0b_refix_always", "approve")]
    assert always["gm"] == always["owner"] == ["auto_within_limits", "auto"]


def test_level_options_reuse_the_raise_rule():
    # One rule, one place: the select closes a level exactly when saving it
    # would be a raise into auto (raisesToAuto), the same test save() uses to
    # ask for confirmation. A bare "is an auto level" check is the old bug.
    src = DIAL_TS.read_text()
    body = src[src.index("export function levelOptions"):src.index("export function raisesToAuto")]
    assert "raisesToAuto(row.level, level)" in body
    assert "level !== row.level" not in body


# ─── types.ts: every export sits under the banner it belongs to ───

BANNER = re.compile(r"^// ─── (.+?) ─+$")
EXPORT = re.compile(r"^export (?:type|interface|const|function) (\w+)")

ACTION_LEDGER = {
    "ActionStatus", "ACTION_STATUS_LABELS", "TERMINAL_ACTION_STATUSES", "Decision", "DecideBody", "DoneBody",
    "CreateActionBody", "SuppressedAction", "CreateActionResult", "DecidedVia", "ActionSurface", "TemplateCategory",
    "Attribution", "ReversalHandle", "ActionEvent", "Action",
}
DIAL = {
    "AutonomyLevel", "AUTONOMY_LEVELS", "AUTO_AUTONOMY_LEVELS", "AUTONOMY_LEVEL_LABELS", "AUTONOMY_LEVEL_HINTS",
    "autonomyRank", "AutonomyStats", "AutonomySuggestion", "AutonomySetting", "AutonomyUpdate", "AutonomyEventKind",
    "DemotionTrigger", "AutonomySuggestionStatus", "AutonomyEvent",
}


def _sections() -> tuple[list[str], dict[str, str]]:
    banners, owner, current = [], {}, ""
    for line in TYPES_TS.read_text().splitlines():
        if m := BANNER.match(line):
            current = m.group(1)
            banners.append(current)
        elif m := EXPORT.match(line):
            owner[m.group(1)] = current
    return banners, owner


def test_action_ledger_and_dial_types_sit_under_their_own_banners():
    banners, owner = _sections()
    assert ACTION_LEDGER | DIAL <= owner.keys(), sorted((ACTION_LEDGER | DIAL) - owner.keys())
    misfiled = {n: owner[n] for n in ACTION_LEDGER if not owner[n].startswith("Actions (Canon item 7)")}
    assert not misfiled, misfiled
    misfiled = {n: owner[n] for n in DIAL if not owner[n].startswith("The autonomy dial")}
    assert not misfiled, misfiled
    # Exactly one section claims the dial, so there is one place to look.
    assert [b for b in banners if "autonomy dial" in b.lower()] == ["The autonomy dial (Canon item 17, 04 spec section 1)"]


# ─── Conventions ───


def test_no_em_dash_in_the_files_this_refix_touched():
    files = [DIAL_TS, PAGE_TSX, TYPES_TS, Path(__file__)]
    em_dash = chr(0x2014)  # built, not typed, so this file passes its own check
    offenders = [f.name for f in files if em_dash in f.read_text()]
    assert not offenders, offenders
