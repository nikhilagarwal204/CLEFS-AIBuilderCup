"""
Module contract tests: the rules that let 8+ lanes build in parallel without
stepping on each other. Every lane runs these before committing.

  - the registry: 15 modules in canon order, ranges, prefixes, route segments
  - every module stub exists and exports MODULE with the registry name
  - migrations sit in the module's number range; CREATE TABLE uses its prefix
  - permission keys are namespaced to the module
  - routes live under the module's prefixes (or are frozen legacy paths)
  - no duplicate (method, path) pairs in the assembled app
  - a module imports only foundation code and other modules' api.py
  - the frozen api.py signatures have not drifted

  - registered jobs, tasks, metrics, action types and signal kinds are keyed
    by their owning module; every metric compiles and passes EXPLAIN
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from clefs import migrate, registry
from clefs.auth import permissions
from tests.foundation.route_walk import iter_routes, route_paths

CLEFS_DIR = Path(migrate.CLEFS_DIR)
MODULES_DIR = CLEFS_DIR / "modules"
CREATE_TABLE_RE = re.compile(r"CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?\"?([a-z_][a-z0-9_]*)", re.I)

CANON_ORDER = (
    "service", "concierge", "profit_guard", "demand", "amenities", "upsell", "reviews", "guests",
    "brief", "ask", "trust", "voice", "visibility", "inquiries", "finance",
)


# ─── Registry ───


def test_registry_declares_the_fifteen_modules_in_canon_order():
    assert registry.INSTALLED_MODULES == CANON_ORDER


def test_migration_ranges_cover_every_module_and_never_overlap():
    assert set(registry.MIGRATION_RANGES) == set(CANON_ORDER) | {"core", "integrator"}
    spans = sorted(registry.MIGRATION_RANGES.values())
    for (lo1, hi1), (lo2, hi2) in zip(spans, spans[1:]):
        assert lo1 <= hi1 < lo2 <= hi2
    assert registry.MIGRATION_RANGES["core"] == (1, 99)
    assert registry.CORE_SUBRANGES == {"core-foundation": (1, 19), "core-domain": (20, 99)}


def test_table_prefixes_are_unique():
    prefixes = list(registry.TABLE_PREFIXES.values())
    assert set(registry.TABLE_PREFIXES) == set(CANON_ORDER)
    assert len(prefixes) == len(set(prefixes))
    assert all(p.endswith("_") for p in prefixes)


def test_module_filter_env(monkeypatch):
    monkeypatch.setenv("CLEFS_ONLY_MODULES", "demand,service")
    assert registry.enabled_modules() == ["service", "demand"]
    monkeypatch.setenv("CLEFS_DISABLED_MODULES", "service")
    assert registry.enabled_modules() == ["demand"]
    monkeypatch.delenv("CLEFS_ONLY_MODULES")
    assert "service" not in registry.enabled_modules() and len(registry.enabled_modules()) == 14
    with pytest.raises(ValueError):
        registry.enabled_modules(["nope"])


# ─── Stubs ───


@pytest.mark.parametrize("module", CANON_ORDER)
def test_module_stub_layout(module):
    d = MODULES_DIR / module
    for name in ("__init__.py", "module.py", "router.py", "api.py", "README.md"):
        assert (d / name).is_file(), f"{module}/{name} missing"
    assert (d / "migrations").is_dir()
    m = registry.load_module(module)
    assert m.name == module
    for r in m.routers:
        assert r.__class__.__name__ == "APIRouter"


# ─── Migrations and table prefixes ───


def _tables_created(path: Path) -> list[str]:
    return [t.lower() for t in CREATE_TABLE_RE.findall(path.read_text(encoding="utf-8"))]


def test_all_discovered_migrations_are_in_range():
    migs = migrate.discover(list(registry.INSTALLED_MODULES))
    for m in migs:
        lo, hi = registry.MIGRATION_RANGES[m.module]
        assert lo <= m.version <= hi, m.name


@pytest.mark.parametrize("module", CANON_ORDER)
def test_module_tables_use_the_module_prefix(module):
    prefix = registry.TABLE_PREFIXES[module]
    allowed_legacy = registry.GRANDFATHERED_TABLES.get(module, frozenset())
    for path in sorted((MODULES_DIR / module / "migrations").glob("*.*")):
        if path.suffix not in (".sql", ".py"):
            continue
        for table in _tables_created(path):
            assert table.startswith(prefix) or table in allowed_legacy, f"{path.name}: table {table} must start with {prefix}"


def test_core_migrations_never_create_module_prefixed_tables():
    prefixes = tuple(registry.TABLE_PREFIXES.values())
    for path in sorted(migrate.CORE_DIR.glob("*.*")):
        if path.suffix in (".sql", ".py"):
            for table in _tables_created(path):
                assert not table.startswith(prefixes), f"{path.name}: {table} looks like a module table"


# ─── Permissions ───


def test_module_permissions_are_namespaced(app):
    for module in CANON_ORDER:
        m = registry.load_module(module)
        prefixes = permissions.permission_prefixes(module)
        for key in m.permissions:
            assert key.startswith(prefixes), f"{module}: {key}"
            assert permissions.owner_of(key) == module
    with pytest.raises(ValueError):
        permissions.register_permissions({"profit.view": frozenset({"owner"})}, module="demand")


def test_every_module_has_a_view_permission_for_its_nav_tab(app):
    for module in CANON_ORDER:
        seg = registry.ROUTE_SEGMENTS[module][0]
        assert f"{seg}.view" in permissions.all_permissions(), f"{module} should register {seg}.view"


NAV_TS = CLEFS_DIR.parents[1] / "client" / "app" / "app" / "[hotelSlug]" / "(console)" / "nav.ts"
NAV_ENTRY_RE = re.compile(r'href: "([a-z-]+)", perm: "([a-z_.]+)", module: "([a-z_]+)"')


def test_console_nav_gates_on_permissions_the_server_issues(app):
    """The client's nav.ts is frozen after Wave 0 and hides a tab unless the
    viewer holds its `perm`; session.can() matches exact keys only. A key the
    server never registers (it once said profit_guard.view for profit.view)
    hides the tab from every role but Clefs admins, so every perm must be
    registered, held by the owner, and be the module's own tab gate."""
    if not NAV_TS.exists():
        pytest.skip("client checkout not present")
    entries = NAV_ENTRY_RE.findall(NAV_TS.read_text())
    assert len(entries) == 15  # 14 module tabs (voice lives under concierge) + settings
    owner = permissions.permissions_for_role("owner")
    for href, perm, module in entries:
        assert perm in permissions.all_permissions(), f"nav tab {href!r} gates on {perm!r}, which no module registers"
        assert perm in owner, f"nav tab {href!r}: the owner role does not hold {perm!r}"
        if module == "core":
            continue
        assert module in registry.INSTALLED_MODULES, f"nav tab {href!r} names unknown module {module!r}"
        assert href in registry.ROUTE_SEGMENTS[module], f"nav tab {href!r} is not a route segment of {module}"
        assert perm == f"{href}.view", f"nav tab {href!r} should gate on {href}.view, not {perm!r}"
    by_href = {href: perm for href, perm, _ in entries}
    assert "profit.view" in permissions.permissions_for_role("finance") and by_href["profit"] == "profit.view"
    assert by_href["menu"] in permissions.permissions_for_role("front_desk")


# ─── Routes ───


def test_module_routes_live_under_their_prefixes():
    for module in CANON_ORDER:
        m = registry.load_module(module)
        prefixes = registry.route_prefixes(module)
        legacy = registry.LEGACY_ROUTE_PATHS.get(module, frozenset())
        for r in m.routers:
            for route in iter_routes(r.routes):
                path = getattr(route, "path", "")
                ok = path in legacy or any(path == p or path.startswith(p + "/") or path.startswith(p) and p.endswith("/") for p in prefixes)
                assert ok, f"{module}: {path} is outside {prefixes}"


def test_no_duplicate_method_and_path(app):
    seen: dict[tuple[str, str], str] = {}
    for route in iter_routes(app.routes):
        for method in sorted(getattr(route, "methods", None) or ()):
            if method == "HEAD":
                continue
            key = (method, route.path)
            assert key not in seen, f"duplicate route {key}"
            seen[key] = route.name
    # The walk must have seen the app: a FastAPI that nests routers in a way
    # iter_routes does not know would otherwise make this test vacuous.
    assert {("POST", "/auth/login"), ("GET", "/hotels/{slug}/signals"), ("GET", "/hotels/{slug}/oauth")} <= set(seen)


def test_create_app_can_load_a_single_module():
    from clefs.app import create_app

    a = create_app(modules=["demand"])
    paths = route_paths(a.routes)
    assert "/hotels/{slug}/requests" not in paths  # service not loaded
    assert "/auth/login" in paths and "/hotels/{slug}/context" in paths
    assert a.state.clefs_modules == ["demand"]
    create_app()  # restore the full permission registry for later tests


# ─── Import boundaries ───


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module)
            out += [f"{node.module}.{a.name}" for a in node.names]
    return out


def test_modules_import_only_foundation_and_other_modules_api():
    for module in CANON_ORDER:
        for path in (MODULES_DIR / module).rglob("*.py"):
            for name in _imports(path):
                if not name.startswith("clefs.modules."):
                    continue
                parts = name.split(".")
                other = parts[2]
                if other == module:
                    continue
                assert parts[3:4] in (["api"], []) and (len(parts) == 3 or parts[3] == "api"), (
                    f"{path.relative_to(CLEFS_DIR)} imports {name}; only clefs.modules.{other}.api is allowed"
                )


def test_foundation_never_imports_module_internals():
    for path in CLEFS_DIR.rglob("*.py"):
        if MODULES_DIR in path.parents:
            continue
        for name in _imports(path):
            assert not name.startswith("clefs.modules."), f"{path.relative_to(CLEFS_DIR)} imports {name}"


# ─── Frozen api.py signatures ───

# 04 spec F10 / 7.2: every cross-module function other lanes build against.
# A lane never changes these itself (FOUNDATION-REQUEST); existing entries are
# only ever extended with defaulted keyword arguments.
FROZEN_API: dict[str, dict[str, str]] = {
    "service": {
        "create_request": "(cur, hotel_slug, *, room, type, description, source, subtype=None, priority_hint=None, language=None, guest_ref=None, idempotency_key=None)",
        "escalate_to_human": "(cur, hotel_slug, *, room, reason, channel, sensitive, excerpt)",
        "request_status_for_guest": "(cur, hotel_slug, tracking_token)",
        "create_followup": "(cur, hotel_slug, *, kind, summary, guest_ref, owner_role, due_at, source, language=None, room=None)",
        "open_items_for_stay": "(cur, hotel_slug, *, room, since, until=None, reservation_id=None)",
        "has_open_complaint": "(cur, hotel_slug, *, room=None, reservation_id=None, since)",
        "recovery_cost": "(cur, hotel_slug, *, start, end)",
        "on_duty": "(cur, hotel_slug, *, role, at)",
        # Wave 1 preflight: the safety classifier concierge, voice and journeys call.
        "classify_safety": "(text, *, language=None, text_en=None, hotel_slug=None, use_llm=True, cur=None)",
        # Wave 1 gaps: Upsell's order follows its linked request's state.
        "on_request_state": "(fn)",
    },
    "concierge": {
        "build_session": "(hotel_slug, *, channel, room=None, location=None, language=None)",
        "respond_text": "(spec, messages)",
        "kb_facts": "(cur, hotel_slug, *, language='en', keyed_only=False)",
        "propose_kb_entry": "(cur, hotel_slug, *, question_en, answer_en, question_ar=None, answer_ar=None, fact_key=None, source, actor)",
        "kb_entry_status": "(cur, hotel_slug, entry_id)",
        # Wave 1 integration: voice binds calls and Trust runs evals through these.
        "build_surface_session": "(hotel_slug, *, surface, room=None, location=None, language=None, verified=False, guest=None, dry_run=False, cur=None)",
        "open_session": "(cur, spec, *, key_status='extension', call_ref=None, transport='sip', consents=None, with_token=False)",
        # Wave 1 gaps: the one tool executor door for voice (MCP, sideband) and evals.
        "run_tool": "(cur, hotel_slug, *, session_ctx, name, args)",
    },
    "upsell": {
        "get_menu": "(cur, hotel_slug, *, outlet=None, language='en')",
        "quote": "(cur, hotel_slug, *, room, lines)",
        "create_order": "(cur, hotel_slug, *, room, lines, source)",
    },
    "guests": {
        "guest_context_for_room": "(cur, hotel_slug, room)",
        "marketing_audience": "(cur, hotel_slug, *, purpose='marketing', channel='whatsapp', segment=None)",
        "record_own_contact": "(cur, hotel_slug, *, reservation_id=None, room=None, kind, value, capture_source, consents, consent_text_versions, evidence, actor)",
        "is_known_guest_sender": "(cur, hotel_slug, *, phone_hmac=None, email_hmac=None)",
        "can_send_marketing": "(cur, hotel_slug, *, identity_hmacs, channel, at, phase)",
        "record_external_send": "(cur, hotel_slug, *, identity_hmacs, channel, source, sent_at, ref)",
        "opt_out": "(cur, hotel_slug, *, kind, value, source, scope='all')",
        "journey_timeline": "(cur, hotel_slug, reservation_id)",
        "arrival_prefs": "(cur, hotel_slug, *, day)",
        # Wave 1 integration: read by Service (queue, device, handover) and Trust (DSR).
        "returning_badge": "(cur, hotel_slug, *, room, role, departments=(), at=None)",
        "returning_arrivals": "(cur, hotel_slug, *, day, role, departments=())",
        "forget_contact": "(cur, hotel_slug, *, kind, value, source, actor=None)",
        "unsubscribe_path": "(hotel_slug, *, identity_hmac, channel)",
        # Wave 2 integration: concierge and inquiries map a WhatsApp sender to its room.
        "room_for_sender": "(cur, hotel_slug, *, sender_hmac=None, phone_hmac=None)",
    },
    "demand": {
        "price_guardrails": "(cur, hotel_slug, stay_date, room_type_code=None)",
        "outdoor_temp_c": "(hotel, at)",
        "comp_rates": "(cur, hotel_slug, stay_date)",
        "expected_adr": "(cur, hotel_slug, *, check_in, check_out, room_type_code=None)",
        "comp_set": "(cur, hotel_slug)",
        "area_queries": "(cur, hotel_slug)",
        "comp_rates_snapshot": "(cur, hotel_slug, *, stay_dates)",
        "upcoming_events": "(cur, hotel_slug, *, days=60, min_score=0.3)",
        "find_event": "(cur, hotel_slug, *, text)",
        "demand_index": "(cur, hotel_slug, *, stay_date)",
    },
    "amenities": {
        "list_items": "(cur, hotel_slug, *, language='en', active_only=True)",
        "resolve_item": "(cur, hotel_slug, amenity_item_id)",
        "link_request": "(cur, hotel_slug, *, request_id, amenity_item_id, qty)",
    },
    "profit_guard": {
        "record_ota_leak": "(cur, hotel_slug, *, source, inquiry_ref, reservation_ref, channel_code, evidence, commission_minor=None, currency=None)",
        "channel_economics": "(cur, hotel_slug, *, start, end)",
        "expected_commission": "(cur, hotel_slug, *, ota_code, breakdown, on)",
        "reference_ota": "(cur, hotel_slug, *, as_of)",
        "distribution_cost_by_night": "(cur, hotel_slug, *, start, end)",
        "distribution_cost_for_reservation": "(cur, hotel_slug, *, reservation_id)",
        "cancellation_cost_by_date": "(cur, hotel_slug, *, start, end)",
        "record_rate_evidence": "(cur, hotel_slug, *, evidence, emit_signal=False)",
        "parity_tolerance_pct": "(cur, hotel_slug)",
        "treat_sc_as_revenue": "(cur, hotel_slug)",
        # Wave 2 integration: Brief's WhatsApp door sends the packet PDF.
        "dispute_packet": "(cur, hotel_slug, *, action_id)",
    },
    "reviews": {
        "draft_reply": "(cur, hotel_slug, review_id)",
        "review_counts": "(cur, hotel_slug, *, start, end, platform=None)",
        "reply_stats": "(cur, hotel_slug, *, platform='google', days=90)",
    },
    "visibility": {
        "ai_snapshot": "(cur, hotel_slug, *, hotel_name, city, max_queries=3)",
        "start_ai_snapshot": "(cur, *, subject, requester_ref)",
        "get_ai_snapshot": "(cur, batch_id)",
        "purge_ai_snapshot": "(cur, batch_id)",
        "parity_status": "(cur, hotel_slug, *, stay_date)",
        "attribution": "(cur, hotel_slug, *, month)",
        "open_fact_errors": "(cur, hotel_slug)",
        "gbp_location": "(cur, hotel_slug)",
    },
    "brief": {
        "push_now": "(cur, hotel_slug, *, cadence='daily', user_id=None)",
        "wa_template_info": "(cur, hotel_slug, *, name, language)",
        "submit_template": "(cur, hotel_slug, *, name, language, category, components)",
        # Wave 2 integration: Guests journeys gate WhatsApp on the WABA's billing.
        "wa_billing_status": "(cur, hotel_slug)",
    },
    "trust": {
        "readiness": "(cur, hotel_slug)",
        # Wave 1 preflight: the module-API door to clefs.core.pii.mask (03 spec 5.2).
        "mask": "(obj, role, *, field_classes, departments=(), subject_department=None, department_field='department')",
        # Wave 1 integration: marketing senders (guests, demand) check DSR restrictions.
        "is_restricted": "(cur, hotel_slug, subject_ref, *, scope='marketing')",
    },
    "inquiries": {
        "create_inquiry": "(cur, hotel_slug, *, source, text, contact, check_in=None, check_out=None, rooms=None, consent_basis='staff_logged', actor)",
        "brief_summary": "(cur, hotel_slug, *, day)",
        "verified_outcomes": "(cur, hotel_slug, *, start, end)",
    },
    "finance": {
        "weekly_pulse": "(cur, hotel_slug, *, week_start)",
        "monthly_summary": "(cur, hotel_slug, *, month)",
        "verified_profit": "(cur, hotel_slug, *, start, end)",
    },
    "ask": {
        # Wave 2: the one answering door (web, WhatsApp) and the catalog it grounds on.
        "answer_question": "(cur, hotel_slug, *, user_id, question, channel='WEB')",
        "askable_metrics": "(cur, hotel_slug, *, user_id)",
    },
    "voice": {
        # Wave 1 integration: the brief and Trust read phone activity through it.
        "call_stats": "(cur, hotel_slug, *, start, end)",
    },
}

# Names (not functions) other lanes import from api.py (04 spec 7.2).
FROZEN_API_NAMES: dict[str, tuple[str, ...]] = {
    "service": ("SOURCES", "REQUEST_TYPE_FOR_DEPARTMENT", "SAFETY_LEVELS"),
    "guests": ("ConsentNotAllowed",),
    "inquiries": ("ContactIn",),
    "visibility": ("SnapshotSubject", "AiSnapshot"),
    "profit_guard": ("DistNight", "CxlCost"),
}


def _plain_signature(fn) -> str:
    sig = inspect.signature(fn)
    params = [p.replace(annotation=inspect.Parameter.empty) for p in sig.parameters.values()]
    return str(sig.replace(parameters=params, return_annotation=inspect.Signature.empty))


@pytest.mark.parametrize("module", CANON_ORDER)
def test_frozen_api_signatures(module):
    import importlib

    api = importlib.import_module(f"clefs.modules.{module}.api")
    for fn_name, expected in FROZEN_API[module].items():
        fn = getattr(api, fn_name, None)
        assert callable(fn), f"{module}.api.{fn_name} is missing"
        assert _plain_signature(fn) == expected, f"{module}.api.{fn_name} signature changed"
    for name in FROZEN_API_NAMES.get(module, ()):
        assert hasattr(api, name), f"{module}.api.{name} is missing"


def test_service_sources_cover_the_journey_and_inquiry_channels():
    from clefs.modules.service import api

    assert {"whatsapp_journey", "phone_desk", "inquiries", "j7_feedback", "journey_gate"} <= set(api.SOURCES)
    # The 04 spec department_hint keys (what journeys and inquiries send).
    assert set(api.REQUEST_TYPE_FOR_DEPARTMENT) >= {"housekeeping", "engineering", "ird", "front_office", "concierge",
                                                    "security", "general"}
    # Legacy staff_members.department names stay aliases until no caller
    # sends them; drop this line when the service lane removes them.
    assert set(api.REQUEST_TYPE_FOR_DEPARTMENT) >= {"Housekeeping", "Maintenance", "Front Desk", "Food & Beverage"}


# ─── Registries (contracts lane) ───
# Jobs, tasks, metrics, action types and signal kinds are keyed "<prefix>.":
# a module uses its own name, a route segment, or a short prefix listed in
# registry.KEY_PREFIXES (04 spec F9: "pg.", "dr.", "inq.", ...); foundation
# code uses "core." (and the core-domain packages "stays.", "ingest.", "tax.").
# Entries registered by test code are ignored (their origin is not clefs.*).

CORE_KEY_PREFIXES = ("core.", "stays.", "ingest.", "tax.")
CONTRACT_MODULES = (
    "clefs.core.signals", "clefs.core.actions", "clefs.core.metrics", "clefs.core.consent", "clefs.core.privacy",
    "clefs.core.money", "clefs.files", "clefs.jobs", "clefs.ai", "clefs.notify",
)


def _key_prefixes(module: str) -> tuple[str, ...]:
    extra = tuple(registry.KEY_PREFIXES.get(module, ()))
    return tuple(dict.fromkeys(permissions.permission_prefixes(module) + extra))


KEY_PREFIX_RE = re.compile(r"^[a-z][a-z_]*\.$")


def test_key_prefixes_cover_every_module_and_list_the_spec_short_prefixes():
    assert set(registry.KEY_PREFIXES) == set(CANON_ORDER)
    for module, prefixes in registry.KEY_PREFIXES.items():
        assert f"{module}." in prefixes, module
        for p in prefixes:
            assert KEY_PREFIX_RE.match(p), f"{module}: bad key prefix {p!r}"
    # 04 spec F9 plus the 02/03 short prefixes.
    wanted = {
        "guests": {"guests."},
        "inquiries": {"inquiries.", "inq."},
        "visibility": {"visibility.", "vis."},
        "finance": {"finance.", "fin."},
        "profit_guard": {"pg.", "tax."},
        "demand": {"dr."},
        "amenities": {"am."},
        "brief": {"wa."},
        "reviews": {"rv."},
        "service": {"ops."},
    }
    for module, must in wanted.items():
        assert must <= set(registry.KEY_PREFIXES[module]), module
    from clefs.core import signals

    assert signals.KEY_PREFIXES is registry.KEY_PREFIXES  # the 04 spec names signals as its home


def test_key_prefixes_never_overlap_between_modules():
    """A short prefix belongs to one module, and never shadows another
    module's own name or route segment ("tax." is shared with core's tax
    package on purpose; core keys are checked separately)."""
    owner: dict[str, str] = {}
    for module in CANON_ORDER:
        for p in _key_prefixes(module):
            assert owner.setdefault(p, module) == module, f"{p!r} is claimed by {owner[p]} and {module}"
    assert set(owner) & set(CORE_KEY_PREFIXES) == {"tax."}


def test_key_prefixes_do_not_widen_permissions():
    """04 spec F9 / 7.1: short prefixes are for job, metric, action-type and
    signal-kind keys only; a permission always carries a full prefix."""
    for module, prefixes in registry.KEY_PREFIXES.items():
        full = set(permissions.permission_prefixes(module))
        for p in prefixes:
            if p in full:
                continue
            with pytest.raises(ValueError):
                permissions.register_permissions({f"{p}view": frozenset({"owner"})}, module=module)
            assert permissions.owner_of(f"{p}view") is None


def _owner(origin: str | None) -> str | None:
    if not origin or not origin.startswith("clefs."):
        return None
    parts = origin.split(".")
    return parts[2] if len(parts) >= 3 and parts[1] == "modules" else "core"


def _registered_keys() -> list[tuple[str, str, str | None]]:
    from clefs import jobs
    from clefs.core import actions, metrics, signals

    jobs.load_all(list(registry.INSTALLED_MODULES))
    out: list[tuple[str, str, str | None]] = []
    out += [("job", j.name, j.registered_from) for j in jobs.registered_jobs()]
    out += [("task", t.name, t.registered_from) for t in jobs.registered_tasks()]
    out += [("metric", m.key, metrics.metric_origin(m.key)) for m in metrics.registered_metrics()]
    out += [("action type", t.key, actions.action_type_origin(t.key)) for t in actions.action_types()]
    out += [("signal kind", k.key, k.registered_from) for k in signals.signal_kinds()]
    return out


def test_registered_keys_are_prefixed_by_their_module(app):
    entries = _registered_keys()
    assert any(kind == "job" and key == "core.actions.verify" for kind, key, _ in entries)
    for kind, key, origin in entries:
        owner = _owner(origin)
        if owner is None:
            continue
        prefixes = CORE_KEY_PREFIXES if owner == "core" else _key_prefixes(owner)
        assert key.startswith(prefixes), f"{kind} {key!r} from {origin} must start with one of {prefixes}"


def test_action_type_module_matches_its_registrant(app):
    from clefs.core import actions

    _registered_keys()
    for t in actions.action_types():
        owner = _owner(actions.action_type_origin(t.key))
        if owner is not None:
            assert t.module == owner, f"{t.key}: module={t.module!r} but registered from {owner!r}"


def test_action_type_defaults_respect_their_ceilings(app):
    from clefs.core import actions

    _registered_keys()
    for t in actions.action_types():
        assert actions.level_rank(t.default_autonomy) <= actions.level_rank(t.ceiling), t.key
        if t.always_approve:
            assert t.ceiling == "approve", t.key


def test_every_registered_metric_compiles_and_passes_explain(cur, hotel_uae):
    from datetime import date

    from clefs.core import metrics

    _registered_keys()
    for m in metrics.registered_metrics():
        for grain, group in (("total", []), ("day", [d.key for d in m.dimensions[:2]])):
            q = metrics.MetricQuery(metric=m.key, start=date(2026, 9, 1), end=date(2026, 9, 30), grain=grain, group_by=group)
            sql, params = metrics.compile_metric(hotel_uae, q)
            cur.execute("EXPLAIN " + sql, params)
            assert cur.fetchall(), m.key


def test_contract_migrations_sit_in_the_f_contracts_subrange():
    lo, hi = 10, 19
    names = sorted(p.name for p in migrate.CORE_DIR.glob("00[1][0-9]_*"))
    assert names[:3] == ["0010_core_platform.sql", "0011_core_signals_actions.sql", "0012_core_contract_views.sql"]
    for m in migrate.discover([]):
        if lo <= m.version <= hi:
            assert m.module == "core" and m.name.startswith(f"{m.version:04d}_core_")


def test_service_requests_view_keeps_its_contract_columns(cur):
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'service_requests_v1' ORDER BY ordinal_position"
    )
    assert [r["column_name"] for r in cur.fetchall()] == [
        "id", "hotel_slug", "room", "floor", "request_type", "department", "priority", "status", "source",
        "created_at", "acknowledged_at", "resolved_at", "assignee_label",
    ]
    cur.execute(
        "INSERT INTO staff_requests (hotel_slug, room, type, category, timestamp, accepted_at) "
        "VALUES ('hotel-nandan', '1204', 'Towels', 'Housekeeping', 1790000000000, 1790000060000) RETURNING id"
    )
    rid = cur.fetchone()["id"]
    cur.execute("SELECT floor, department, source, acknowledged_at - created_at AS ack FROM service_requests_v1 WHERE id = %s", (rid,))
    row = cur.fetchone()
    assert (row["floor"], row["department"], row["source"], row["ack"].total_seconds()) == ("12", "Housekeeping", "legacy", 60)


def test_contract_modules_define_no_router_of_their_own():
    """Their routes live in clefs.core.routes_contracts; a `router` attribute
    here would be mounted a second time through CORE_ROUTER_HOOKS."""
    import importlib
    import importlib.util

    for name in CONTRACT_MODULES:
        assert not hasattr(importlib.import_module(name), "router"), name
    assert importlib.util.find_spec("clefs.notify.routes") is None


def test_contract_permissions_are_registered_as_core(app):
    for key in ("core.autonomy.view", "core.autonomy.manage", "core.autonomy.raise_auto", "core.files.view", "core.metrics.view"):
        assert key in permissions.all_permissions() and permissions.owner_of(key) == "core"
    assert permissions.permissions_for_role("gm") >= {"core.autonomy.manage"}
    assert "core.autonomy.raise_auto" not in permissions.permissions_for_role("gm")
