"""Deploy and packaging guards (fix lane "ops").

These files are read by Vercel, Docker and Compose rather than imported, so a
regression in them shows up only at deploy time: a secret baked into an image,
a runtime newer than the code allows, a cron that never fires. Each test
below pins one of those failure modes to a fast, offline check.
"""

from __future__ import annotations

import ast
import fnmatch
import importlib.metadata as md
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SERVER = Path(__file__).resolve().parents[2]

# ─── Helpers ───


def _ignored(ignore_file: Path, rel_path: str) -> bool:
    """Evaluate a root-level .gitignore / .dockerignore the way git and Docker
    do for top-level paths: the last matching pattern wins and `!` re-includes.
    Our secret files all live at the context root, where the two syntaxes agree."""
    ignored = False
    for raw in ignore_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[1:] if negate else line
        pattern = pattern.rstrip("/")
        if fnmatch.fnmatchcase(rel_path, pattern) or fnmatch.fnmatchcase(rel_path, pattern.removeprefix("**/")):
            ignored = not negate
    return ignored


def _norm(dist: str) -> str:
    return re.sub(r"[-_.]+", "-", dist).lower()


def _declared_requirements() -> set[str]:
    names = set()
    for raw in (SERVER / "requirements.txt").read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"[A-Za-z0-9._-]+", line)
        assert m, f"unparseable requirement: {raw!r}"
        names.add(_norm(m.group(0)))
    return names


def _python_sources() -> list[Path]:
    files = [SERVER / "main.py"]
    for base in ("clefs", "tests"):
        files += [p for p in (SERVER / base).rglob("*.py") if "__pycache__" not in p.parts]
    return files


# ─── Secrets never leave the machine ───


@pytest.mark.parametrize("ignore_file", [".gitignore", ".dockerignore"])
def test_local_env_files_are_git_and_docker_ignored(ignore_file):
    """security-12: .env.compose holds the Compose secrets and the Dockerfile
    does `COPY . .`, so it must be ignored by both git and the build context,
    while the documented template stays tracked and visible."""
    path = SERVER / ignore_file
    for secret in (".env", ".env.compose", ".env.local", ".env.production"):
        assert _ignored(path, secret), f"{ignore_file} does not ignore {secret}"
    assert not _ignored(path, ".env.example"), f"{ignore_file} hides the .env.example template"


def test_git_itself_ignores_the_compose_secrets_file():
    """Cross-check the pattern evaluator against real git where available."""
    git = shutil.which("git")
    if git is None or not (SERVER.parent / ".git").exists():
        pytest.skip("not in a git work tree")
    ignored = subprocess.run([git, "check-ignore", "-q", "server/.env.compose"], cwd=SERVER.parent, check=False)
    tracked = subprocess.run([git, "check-ignore", "-q", "--no-index", "server/.env.example"], cwd=SERVER.parent, check=False)
    assert ignored.returncode == 0 and tracked.returncode == 1


def test_vercel_bundle_excludes_local_env_files():
    """`vercel deploy` from a laptop uploads the working tree, and settings.py
    reads .env when present: a local .env must never reach the function."""
    cfg = json.loads((SERVER / "vercel.json").read_text())
    (build,) = cfg["builds"]
    globs = build["config"]["excludeFiles"].strip("{}").split(",")
    assert {"tests/**", ".env", ".env.*"} <= set(globs)


def test_requirements_txt_is_the_only_dependency_manifest():
    """@vercel/python reads pyproject.toml / uv.lock as a dependency source:
    a pytest-only pyproject.toml (no [project] table) could replace
    requirements.txt and ship a function that crashes on import. pytest's
    config lives in pytest.ini, as on main."""
    assert (SERVER / "requirements.txt").is_file()
    assert (SERVER / "pytest.ini").is_file()
    for name in ("pyproject.toml", "uv.lock", "Pipfile", "Pipfile.lock"):
        assert not (SERVER / name).exists(), f"{name} would compete with requirements.txt on Vercel"


def test_env_example_never_puts_a_comment_after_an_empty_value():
    """python-dotenv reads `KEY=   # note` as the value "# note", turning a blank
    secret into a public string. Checked on the raw text so the guard does not
    depend on how a given python-dotenv release happens to parse it."""
    offenders = [
        f"{n}: {line}"
        for n, line in enumerate((SERVER / ".env.example").read_text().splitlines(), 1)
        if re.match(r"^\s*[A-Z][A-Z0-9_]*=\s*#", line)
    ]
    assert offenders == []


# ─── Python version: one minor everywhere ───


def test_python_pins_agree_on_312():
    """Vercel reads .python-version and the VM runs the Docker base image; both
    must be the 3.12 syntax floor so production runs exactly what CI parses."""
    pinned = (SERVER / ".python-version").read_text().strip()
    assert pinned == "3.12"
    froms = re.findall(r"^FROM\s+python:(\d+\.\d+)\S*", (SERVER / "Dockerfile").read_text(), re.M)
    assert froms == [pinned]


def test_sources_parse_at_the_312_syntax_floor():
    """ast.parse(feature_version=) rejects grammar newer than 3.12 (for example
    PEP 758 unparenthesised `except A, B:`), which would crash on Vercel."""
    bad = []
    for p in _python_sources():
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p), feature_version=(3, 12))
        except SyntaxError as exc:
            bad.append(f"{p.relative_to(SERVER)}:{exc.lineno}: {exc.msg}")
    assert bad == []


def test_sources_compile_under_a_real_312_interpreter():
    """feature_version is best-effort, so compile with a real 3.12 when the
    machine has one (Homebrew and uv installs expose python3.12)."""
    exe = shutil.which("python3.12")
    if exe is None:
        pytest.skip("python3.12 not on PATH")
    script = (
        "import sys\n"
        "bad = []\n"
        "for f in sys.argv[1:]:\n"
        "    try:\n"
        "        compile(open(f, encoding='utf-8').read(), f, 'exec', dont_inherit=True)\n"
        "    except SyntaxError as e:\n"
        "        bad.append(f'{f}:{e.lineno}: {e.msg}')\n"
        "print('\\n'.join(bad)); sys.exit(1 if bad else 0)\n"
    )
    res = subprocess.run(
        [exe, "-c", script, *map(str, _python_sources())], capture_output=True, text=True, timeout=60, check=False
    )
    assert res.returncode == 0, res.stdout + res.stderr


# ─── Dependencies ───

# FastAPI owns and pins these; they are its public API surface (Request,
# BaseModel), so declaring our own range would only fight fastapi's pins.
PROVIDED_BY_FASTAPI = {"pydantic", "starlette"}


def _optional_import_lines(tree: ast.AST) -> set[int]:
    """Imports inside `try: ... except ImportError` are optional extras."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        caught = set()
        for h in node.handlers:
            types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
            caught.update(t.id for t in types if isinstance(t, ast.Name))
        if caught & {"ImportError", "ModuleNotFoundError", "Exception"}:
            for stmt in node.body:
                lines.update(n.lineno for n in ast.walk(stmt) if isinstance(n, (ast.Import, ast.ImportFrom)))
    return lines


def test_every_direct_third_party_import_is_declared():
    """conventions-cryptography-transitive-only: clefs.core.privacy imports
    cryptography, which once arrived only through mcp -> pyjwt[crypto]. A
    parent dropping that extra would break PII sealing on the next deploy."""
    declared = _declared_requirements()
    dists = md.packages_distributions()
    missing: dict[str, set[str]] = {}
    for p in [SERVER / "main.py", *(q for q in (SERVER / "clefs").rglob("*.py") if "__pycache__" not in q.parts)]:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        optional = _optional_import_lines(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                tops = [node.module.split(".")[0]]
            else:
                continue
            if node.lineno in optional:
                continue
            for top in tops:
                if top in sys.stdlib_module_names or top in ("clefs", "__future__"):
                    continue
                owners = {_norm(d) for d in dists.get(top, [top])}
                if not owners & (declared | PROVIDED_BY_FASTAPI):
                    missing.setdefault(top, set()).add(str(p.relative_to(SERVER)))
    assert missing == {}, f"imported but not in requirements.txt: {missing}"
    assert "cryptography" in declared


def test_route_walker_flattens_nested_routers():
    """tests/foundation/route_walk.iter_routes is what the route walkers use.
    It must see a prefixed, doubly nested route on whatever FastAPI is
    installed (flat app.routes up to 0.136, _IncludedRouter entries from
    0.137); it was checked on 0.135.3 and 0.137.0."""
    from fastapi import APIRouter, FastAPI

    from tests.foundation.route_walk import iter_routes

    inner = APIRouter(prefix="/inner")
    inner.get("/x")(lambda: None)
    outer = APIRouter()
    outer.post("/hotels/{slug}/probe")(lambda: None)
    outer.include_router(inner)
    a = FastAPI()
    a.include_router(outer, prefix="/p")
    seen = {(r.path, m) for r in iter_routes(a.routes) for m in (getattr(r, "methods", None) or ())}
    assert {("/p/hotels/{slug}/probe", "POST"), ("/p/inner/x", "GET")} <= seen


def test_fastapi_cap_keeps_app_routes_flat():
    """FastAPI 0.137 nests included routers inside app.routes. Every route
    walker in the suite now goes through route_walk.iter_routes (see the test
    above), so the cap can be lifted once the full suite has run on the newer
    FastAPI; until then the cap in requirements.txt and the installed version
    must both hold that line."""
    spec = next(
        line for line in (SERVER / "requirements.txt").read_text().splitlines() if re.match(r"fastapi\b", line)
    )
    upper = re.search(r"<\s*([\d.]+)", spec)
    assert upper, f"fastapi has no upper bound: {spec!r}"
    assert tuple(int(x) for x in upper.group(1).split(".")) <= (0, 137)

    from fastapi import APIRouter, FastAPI

    r = APIRouter()
    r.get("/probe")(lambda: None)
    a = FastAPI()
    a.include_router(r)
    assert "/probe" in {getattr(route, "path", None) for route in a.routes}


# ─── Compose, Caddy and Vercel wiring ───


def test_compose_puts_caddy_in_front_of_loopback_only_services():
    """spec-compose-no-caddy: 01 spec G runs postgres, api, worker and Caddy.
    Only Caddy may publish on public interfaces, because uvicorn trusts
    X-Forwarded-For from any peer (FORWARDED_ALLOW_IPS=*)."""
    compose = yaml.safe_load((SERVER / "docker-compose.yml").read_text())
    services = compose["services"]
    assert {"postgres", "api", "worker", "caddy"} <= set(services)

    caddy = services["caddy"]
    assert "./Caddyfile:/etc/caddy/Caddyfile:ro" in caddy["volumes"]
    assert {"80:80", "443:443"} <= set(caddy["ports"])
    for name, svc in services.items():
        if name == "caddy":
            continue
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} on a public interface"

    for name in ("api", "worker"):
        env_files = [e["path"] if isinstance(e, dict) else e for e in services[name]["env_file"]]
        assert env_files == [".env.compose"]
        assert services[name]["environment"]["CLEFS_DOTENV"] == "0"

    upstream = re.search(r"reverse_proxy\s+(\w+):(\d+)", (SERVER / "Caddyfile").read_text())
    assert upstream and upstream.group(1) in services
    assert f"--port {upstream.group(2)}" in " ".join(services[upstream.group(1)]["command"])


def test_vercel_region_and_cron_tick():
    """Production runs on Vercel Hobby, which rejects a per-minute cron, so
    an external pinger drives /internal/cron/tick (README-deploy.md). If a
    crons block comes back (Pro), it may only schedule that tick."""
    cfg = json.loads((SERVER / "vercel.json").read_text())
    assert cfg["regions"] == ["sin1"]
    assert all(c["path"] == "/internal/cron/tick" for c in cfg.get("crons", []))
    assert (SERVER / "README-deploy.md").read_text().count("external pinger") >= 1


def test_every_vercel_cron_path_is_a_guarded_get_route(client):
    """Vercel Cron sends GET with the CRON_SECRET bearer. A 404/405 here means
    the cron fires into nothing; a 200 without the bearer means anyone can."""
    cfg = json.loads((SERVER / "vercel.json").read_text())
    paths = {c["path"] for c in cfg.get("crons", [])} | {"/internal/cron/tick"}  # the pinger's route
    for path in paths:
        assert client.get(path).status_code == 401, path
