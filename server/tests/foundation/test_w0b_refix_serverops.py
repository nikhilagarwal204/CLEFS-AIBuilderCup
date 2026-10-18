"""Refix lane "server-ops": texts that steer the next lane must match the code.

The Canon wins every conflict and lanes are told to follow its amendments, so
a stale amendment is an instruction, not a typo: K1 once sent the integrator
to edit the applied migration 0011 (checksum drift), and K3 once handed the
already-built SerpApi client and 0018 to a Wave 1 lane to build again. The
same goes for WHY comments in deploy files: the requirements.txt FastAPI-cap
comment once said the route walkers still needed work that was already done.
Each test below pins one of those texts to the repo state it describes, all
offline (files only, no database).
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
REPO = SERVER.parent
SPECS = REPO / "docs" / "specs"
MIGRATIONS = SERVER / "clefs" / "migrations"

# ─── Helpers ───


def _canon_amendment(key: str) -> str:
    """The text of one Canon amendment bullet (K1..K5), up to the next one.
    Bullets look like '- **K1 (item 7, Actions):** ...' and may run over
    several indented lines, so slice from this key to the next '- **K'."""
    canon = (SPECS / "00-CANON.md").read_text()
    start = canon.index("## Amendments adopted")
    section = canon[start:]
    m = re.search(rf"^- \*\*{key}\b.*?(?=^- \*\*K\d|\Z)", section, re.S | re.M)
    assert m, f"Canon amendment {key} not found"
    return m.group(0)


def _python_pin() -> str:
    return (SERVER / ".python-version").read_text().strip()


# ─── Canon K1 / K3: no Wave 1 lane is sent to built or forbidden work ───


def test_canon_k1_ships_in_0013_and_leaves_0011_alone():
    """spec-canon-k1-k3-stale: K1's states live in 0013. The Canon must name
    that file and must not tell anyone to apply K1 to 0011, whose bytes are
    frozen at the applied checksum (CONTRACTS section 4)."""
    k1 = _canon_amendment("K1")
    assert "0013_core_action_states.sql" in k1
    assert not re.search(r"appl(y|ies) this to migration `?0011", k1), k1
    body = (MIGRATIONS / "0013_core_action_states.sql").read_text()
    for state in ("observed", "failed", "expired"):
        assert state in k1 and state in body, state
    # The text says 0011 "stays at its applied bytes": hold the code to it.
    digest = hashlib.sha256((MIGRATIONS / "0011_core_signals_actions.sql").read_bytes()).hexdigest()
    assert digest.startswith("92ecb57f"), digest


def test_canon_k3_marks_the_serpapi_client_as_built():
    """spec-canon-k1-k3-stale: core/serpapi.py and 0018 exist (wave 0b
    extension lane). K3 must not assign them to a Wave 1 lane to build, and
    must name every caller of the shared client, Reviews included (04 spec F5)."""
    k3 = _canon_amendment("K3")
    assert (SERVER / "clefs" / "core" / "serpapi.py").is_file()
    assert (MIGRATIONS / "0018_core_serpapi.sql").is_file()
    assert "core/serpapi.py" in k3 and "0018_core_serpapi.sql" in k3
    assert not re.search(r"built by the F-domain lane|built by .{0,40}in Wave 1", k3), k3
    assert "core.serpapi.search" in k3
    for caller in ("Demand", "Visibility", "Reviews"):
        assert caller in k3, caller


def test_canon_amendment_file_references_exist():
    """Any migration or core module an amendment names as delivered must be a
    real file, so the next stale reference (a renumbered migration, a moved
    module) fails here instead of misleading a lane."""
    canon = (SPECS / "00-CANON.md").read_text()
    section = canon[canon.index("## Amendments adopted"):]
    for name in re.findall(r"`(\d{4}_[a-z0-9_]+\.sql)`", section):
        assert (MIGRATIONS / name).is_file(), name
    for rel in re.findall(r"`(core/[a-z0-9_/]+\.py)`", section):
        assert (SERVER / "clefs" / rel).is_file(), rel


# ─── 01 foundation spec: Python pins agree with the repo ───


def test_foundation_spec_python_pins_match_python_version():
    """spec-canon-k1-k3-stale: 01-foundation.md said 3.13 in three places
    (.python-version, the Procfile image, the Vercel note) while the repo pins
    3.12 everywhere (test_w0b_ops.test_python_pins_agree_on_312)."""
    pin = _python_pin()
    doc = (SPECS / "01-foundation.md").read_text()
    assert "3.13" not in doc
    pins = re.findall(r"\.python-version \((\d+\.\d+)\)", doc) + re.findall(r"python:(\d+\.\d+)-slim", doc)
    assert pins, "01-foundation.md no longer names the Python pins; update this test"
    assert set(pins) == {pin}, pins


# ─── requirements.txt: the FastAPI-cap WHY comment matches the suite ───


def _fastapi_comment() -> str:
    """The contiguous '#' comment block directly above the fastapi pin."""
    lines = (SERVER / "requirements.txt").read_text().splitlines()
    idx = next(i for i, line in enumerate(lines) if re.match(r"fastapi\b", line))
    block: list[str] = []
    for line in reversed(lines[:idx]):
        if not line.startswith("#"):
            break
        block.append(line.lstrip("# "))
    return " ".join(reversed(block))


def test_fastapi_cap_comment_points_at_route_walk():
    """conventions-stale-fastapi-cap-comment: the comment must not say the
    walkers still need to recurse into routers (route_walk.iter_routes already
    does), and must name the real lift procedure (CONTRACTS Part 3)."""
    text = _fastapi_comment()
    assert text, "the fastapi pin lost its WHY comment"
    assert "route_walk.iter_routes" in text
    assert not re.search(r"only after those walkers recurse|would silently inspect nothing", text), text
    assert "test_w0b_ops.py" in text

    from tests.foundation import route_walk

    assert callable(route_walk.iter_routes)


def test_every_route_walker_goes_through_iter_routes():
    """The comment (and CONTRACTS Part 2, Routes) claim every walker in the
    suite uses route_walk.iter_routes / route_paths. Hold the suite to that
    claim: a direct read of some_app.routes silently sees nothing on FastAPI
    0.137+. The one deliberate flat read is the cap probe in test_w0b_ops."""
    allowed_funcs = {("test_w0b_ops.py", "test_fastapi_cap_keeps_app_routes_flat")}
    wrappers = {"iter_routes", "route_paths"}
    offenders: list[str] = []
    for path in sorted((SERVER / "tests").rglob("*.py")):
        if path.name == "route_walk.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        wrapped: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
                if name in wrappers:
                    wrapped.update(id(a) for a in node.args)
        exempt: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and (path.name, node.name) in allowed_funcs:
                exempt.update(id(n) for n in ast.walk(node))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "routes" and id(node) not in wrapped | exempt:
                offenders.append(f"{path.relative_to(SERVER)}:{node.lineno}")
    assert offenders == [], f"walk routes via tests/foundation/route_walk.iter_routes: {offenders}"
