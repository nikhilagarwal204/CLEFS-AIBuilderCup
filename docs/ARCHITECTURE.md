# Clefs architecture

How the code is organised, what the foundation guarantees, and how to add a module. The authoritative interface reference is `server/clefs/CONTRACTS.md` (what the foundation actually provides, signature by signature); where the design specs disagree, `docs/specs/00-CANON.md` wins.

## Contents

1. [The shape of the system](#1-the-shape-of-the-system)
2. [Server package layout](#2-server-package-layout)
3. [The module registry](#3-the-module-registry)
4. [Contracts between modules](#4-contracts-between-modules)
5. [Data conventions](#5-data-conventions)
6. [Identity and access](#6-identity-and-access)
7. [Client layout](#7-client-layout)
8. [Build waves](#8-build-waves)
9. [How to add a module](#9-how-to-add-a-module)
10. [Rules the tests enforce](#10-rules-the-tests-enforce)

## 1. The shape of the system

```
                 ┌──────────────────────── client/ (Next.js 16) ────────────────────────┐
 owners, GMs  -> │ /app/[hotelSlug]/(console)/*   brief, profit, demand, service, ...   │
 staff        -> │ /device/[hotelSlug]            paired tablet PWA, PIN shifts         │
 guests       -> │ /stay/[hotelSlug]/[roomId], /g, /q, /w   guest pages                 │
 prospects    -> │ /, /free-ota-audit, /google-check        marketing and lead magnets  │
                 └───────────────────────────────┬──────────────────────────────────────┘
                                                 │ /api/clefs/*  (Next rewrite)
                 ┌───────────────────────────────v──────────────────────────────────────┐
                 │ server/ FastAPI: create_app() mounts core routes, then each module   │
                 │   foundation: auth, hotels, signals, actions + autonomy dial,        │
                 │               metrics, jobs, notify, files, ai, audit, consent       │
                 │   core domain: stays, ingest, tax                                     │
                 │   modules: 15 directories under clefs/modules/                       │
                 └───────┬──────────────────────────┬───────────────────────────────────┘
                         │                          │
                 Postgres 16 (Neon)       /internal/cron/tick or python -m clefs.worker
                                          (scheduled jobs, the task queue, the outbox)
```

A module turns raw data (imports, webhooks, guest requests, vendor answers) into **signals** (something is wrong or available, with an AED impact and evidence) and proposes **actions** (a decision for a person or, within the dial, for Clefs). Brief ranks the open signals and actions into the owner's daily top 5. Every send goes through one outbox, every state change through one audited state machine.

## 2. Server package layout

```
server/
  main.py                   app = clefs.app.create_app()
  clefs/
    app.py                  create_app(modules=None): middleware, core routes, module routers
    registry.py             INSTALLED_MODULES, migration ranges, table prefixes, route segments, key prefixes
    settings.py             typed settings from env (+ server/.env), fails closed, the remote-DB guard
    db.py                   pooled psycopg2: db() context, fetch_one/fetch_all/execute, on_commit
    migrate.py              python -m clefs.migrate up|status|verify|new
    migrations/             core migrations 0001-0099, integrator hotfixes 1900-1999
    auth/                   users, sessions, passwords, permissions, devices + PINs, magic links, tokens
    core/
      hotels.py rooms.py    the tenant (Hotel), room register, room types
      time.py clock.py      hotel-timezone bucketing; clock.now() (frozen in tests)
      signals.py            signal kinds, upsert by dedupe_key, sections, ranking inputs
      actions.py            action types, the state machine, executors, verifiers, the autonomy dial
      metrics.py            the governed metrics registry (Ask and reports query only through it)
      money.py              minor units, Decimal strings, the AED/USD peg
      consent.py privacy.py pii.py   append-only consent ledger, contact HMACs, sealing, PII masking
      serpapi.py oauth.py   the only SerpApi path (cache + cost ledger); OAuth for Google, Xero, QBO, Zoho
      stay_events.py        stay lifecycle hooks
      stays/ ingest/ tax/   the core domain: reservations and nights, file imports, the UAE fee stack
      routes.py routes_contracts.py  hotel, settings, team, rooms; signals, actions, autonomy, files routes
    jobs.py                 @scheduled / @task, enqueue, tick(), the job_runs and job_queue tables
    worker/                 python -m clefs.worker: tick loop, LISTEN, worker services, heartbeats
    notify/                 the outbox: WhatsApp, email, SMS; inbound WhatsApp and email webhooks
    files.py ai.py audit.py events.py ratelimit.py logsafe.py
    cli.py                  bootstrap commands, seed-demo, demo-check
    seed/demo_uae/          the deterministic synthetic demo hotel (parts discovered by PART_ORDER)
    legacy/                 pre-refactor helpers kept for the Indian hotels (MSG91, seed data)
    modules/<m>/            one directory per module (next section)
  tests/
    conftest.py             one database per pytest process, a savepoint per test, fixtures
    foundation/             contract tests (see section 10)
    modules/<m>/            each module's own suite
    demo/                   the demo seed end to end
```

Each module directory has the same skeleton: `module.py` (the manifest), `router.py`, `api.py` (the only public surface), `README.md`, `migrations/`, plus whatever internals it needs (`jobs.py` usually registers its jobs, signals, action types and metrics on import).

## 3. The module registry

`server/clefs/registry.py` is the single explicit list of product modules. It is explicit on purpose: the foundation shipped a stub for every module, so module work only fills in its own directory, route order is deterministic (path precedence matters in FastAPI), and a half-built module never loads by accident.

| Module | Migrations | Table prefix | Console route segment(s) | Extra key prefixes |
|---|---|---|---|---|
| core (foundation 0001-0019, domain 0020-0099) | 0001-0099 | none | n/a | `core.`, `stays.`, `ingest.`, `tax.` |
| service | 0100-0199 | `svc_` (+ legacy `staff_requests`, `staff_members`, `staff_shifts`, `pending_requests`) | `service` | `ops.` |
| concierge | 0200-0299 | `cc_` (+ `conversation_logs`, `hotel_locations`) | `concierge` | |
| profit_guard | 0300-0399 | `pfg_` | `profit`, `profit-guard` | `pg.`, `tax.` |
| demand | 0400-0499 | `dr_` | `demand` | `dr.` |
| amenities | 0500-0549 | `amn_` | `amenities` | `am.` |
| upsell | 0550-0599 | `ups_` | `menu`, `upsell` | |
| reviews | 0600-0649 | `rev_` | `reviews` | `rv.` |
| guests | 0650-0699 | `gst_` (+ `guest_rooms`) | `guests` | |
| brief | 0700-0749 | `brf_` | `brief` | `wa.` |
| ask | 0750-0799 | `ask_` | `ask` | |
| trust | 0800-0849 | `trs_` | `trust` | |
| voice | 0850-0899 | `vc_` | `voice` | |
| visibility | 0900-0949 | `vis_` | `visibility` | `vis.` |
| inquiries | 0950-0999 | `inq_` | `inquiries` | `inq.` |
| finance | 1000-1049 | `fin_` | `finance` | `fin.` |
| integrator hotfixes | 1900-1999 | none | n/a | |

- **`INSTALLED_MODULES`** is that tuple, in router mount order. `enabled_modules()` honours `CLEFS_ONLY_MODULES` (load only these, for a module's test run) and `CLEFS_DISABLED_MODULES` (the production kill switch).
- **Per hotel**, each module can also be switched off in Settings, Modules (`hotel_module_settings`). Per-hotel jobs skip a hotel that switched the owning module off, and the console hides its tab (`GET /hotels/{slug}/context` returns `modules_enabled`).
- **Routes.** A module owns `/hotels/{slug}/<segment>/...` for its segments, plus routes under the shared namespaces `/public/` (guests, leads), `/device/` (paired devices), `/webhooks/` and `/internal/`. Pre-refactor legacy paths are grandfathered in `LEGACY_ROUTE_PATHS` and golden-tested.
- **Keys.** Jobs, tasks, metrics, action types and signal kinds are named `<prefix>.<what>`, where the prefix is the module's name, one of its route segments, or one of its `KEY_PREFIXES`. Permissions use the module name or a route segment only.

**The manifest.** Each module exports `MODULE` from `clefs/modules/<m>/module.py`:

```python
@dataclass
class Module:
    name: str                                            # == registry key
    routers: list[APIRouter] = []
    permissions: dict[str, frozenset[str]] = {}          # perm -> roles ("device" = paired devices)
    worker_services: list[Callable[[], Awaitable[None]]] = []
    on_startup: Callable[[], None] | None = None
    settings_model: type[BaseModel] | None = None        # validates hotel_module_settings[<m>].settings
```

`clefs.app.create_app(modules=None)` mounts `GET /`, `GET /healthz`, the auth routes, the core hotel routes and the SSE stream, then the core contract routers (`routes_contracts` first, `oauth` last), then each enabled module's routers in registry order. `jobs.load_all()` imports the core registrants, then the optional core domain registrants (stays, ingest, tax), then each enabled module's manifest, which is how a module's `jobs.py` gets registered. Foundation code never imports a module.

## 4. Contracts between modules

A module may import foundation code (`clefs.*` outside `clefs.modules`) and **other modules' `api.py` only**, never their internals. The frozen `api.py` signatures are listed in `CONTRACTS.md` section 13 and pinned by `test_frozen_api_signatures`; a module implements the bodies and may add functions, but never changes a frozen signature. A caller handles the documented default return gracefully, because another module may be switched off for that hotel.

The shared contracts, in the order a feature usually touches them:

| Contract | What it gives a module |
|---|---|
| **Signals** (`core.signals`) | `register_signal_kind(key, section, cadence, ...)` and `upsert_signal(...)` keyed on `(hotel_slug, dedupe_key)`. A signal carries a section (`money_lost`, `money_available`, `service_risk`, `reviews`, `verified`, `compliance`), an impact in minor units, confidence, evidence, a recommended action, an owner role and a due date. Compliance signals never rank into the daily top 5. |
| **Actions** (`core.actions`) | `register_action_type(ActionType(...))` with an executor, a verifier, a default and maximum autonomy level, limits and gates; `create_action` and `decide`. The state machine: `proposed -> approved -> assigned -> in_progress -> done -> verified / not_verified`, side states `snoozed`, `rejected`, `cancelled`, the dispute path `submitted -> awaiting_counterparty -> verified / rejected_by_counterparty -> unresolved`, and the terminal `observed`, `failed`, `expired` (Canon K1). Every transition is an `action_events` row. |
| **Autonomy dial** (`core.actions`) | Per hotel and action type: `off`, `observe`, `suggest`, `approve`, `auto_within_limits`, `auto`. `always_approve` types (refunds, folio, rate, OTA changes) never run automatically. Promotions are suggested (50 decisions in 30 days, 95% approved unedited); demotions happen on complaints, opt-out spikes, Meta quality drops and eval regressions. The ledger records the dial level at execution, policy version, template and category, consent basis and reversal handle. |
| **Metrics** (`core.metrics`) | Governed metric definitions with their own permission, hotel filter and read-only execution. Ask Clefs and the reports query data only through this registry, never with raw SQL. |
| **Jobs** (`clefs.jobs`) | `@scheduled(name, every/daily_at/weekly_at/monthly_at/...)` per hotel in its timezone, `@task(name)` with retries and backoff, `enqueue(..., run_at=, dedupe_key=, cur=)` inside the caller's transaction. Each scheduled slot runs once whoever ticks. |
| **Notify** (`clefs.notify`) | One outbox for WhatsApp (templates, text, interactive, documents), email and SMS, with idempotency keys, the 24-hour window, 6-second pacing per recipient, Meta error handling and the marketing send window. Inbound handlers register with `@on_whatsapp(audience, priority=...)` and `@on_inbound_email(purpose)`; the guest priority order is frozen. |
| **Files, AI, audit** | `clefs.files` (stored in Postgres, signed links), `clefs.ai` (structured calls, tool calls, cost ledger in micros, a fake backend in tests), `clefs.audit` (append-only, hash-chained `audit_log`). |
| **Consent and privacy** | `core.consent` is append-only (no row means no consent); `core.privacy` hashes contacts with a pepper and seals captured values; `core.pii` masks by field class and role. |
| **Core domain** | `core.stays` (reservations, nights, folio charges, occupancy, on-the-books snapshots, reservation matching), `core.ingest` (import batches, mapping templates and aliases, AI column mapping, PDF extraction with a totals gate, commit handlers per import kind), `core.tax` (date-effective fee rules, the Dubai and Abu Dhabi stacks, the Tourism Dirham, conventions A/B/C with golden values). |
| **Vendors** | `core.serpapi.search` is the only SerpApi path (cache, budget, cost ledger); `core.oauth` runs every OAuth connection (Google Business Profile, Xero, QuickBooks, Zoho). |

The HTTP contract between server and client (auth, context, devices, team, settings, rooms, the legacy endpoints) is `CONTRACTS.md` section 12. `client/app/lib/types.ts` mirrors the signal, action and autonomy serializers, and a server test compares them, so a new server field fails the build until the client type gains it.

## 5. Data conventions

- **Tenant key:** `hotel_slug TEXT REFERENCES hotels(slug)` on every hotel-scoped table. A module's foreign keys may point only at core tables or its own.
- **Money:** `BIGINT` minor units (fils for AED) plus `CHAR(3)` currency in storage; Decimal strings in APIs (`"45.00"` next to `*_minor`); rates `NUMERIC(18,8)`. Vendor costs (SerpApi, Google Gemini, Perplexity, Meta) are `*_micros BIGINT`. The AED/USD peg is 3.6725.
- **Time:** new tables use `TIMESTAMPTZ`; legacy tables keep epoch milliseconds. Every day, week or month bucket goes through `clefs.core.time` in the **hotel's** timezone (Asia/Dubai for the UAE). "Now" is always `clefs.core.clock.now()`, which tests freeze and advance.
- **Migrations:** `NNNN_<module>_<desc>.sql` (or `.py` defining `up(cur)`) inside the module's range, each applied in its own transaction under an advisory lock, every unapplied version in ascending order. No `BEGIN`/`COMMIT`, no `CREATE INDEX CONCURRENTLY`. Prefer idempotent DDL. Never edit an applied file: `verify` reports checksum drift, and the edit would never reach a database that already ran it.
- **Tables** start with the module's prefix. **Keys** (jobs, signals, actions, metrics) start with the module's name, a route segment or a registered short prefix.
- **Personal data:** guest phones and emails live as peppered HMACs; captured values are sealed. OTA relay addresses never receive marketing, and Booking.com contact data is used only within its 30-day window.
- **Style:** `# ─── Section ───` banners, comments that explain why, no em dash characters anywhere (code, copy, docs), Python 3.12 syntax floor.

## 6. Identity and access

- **Principals:** a console **user** (cookie `clefs_session`, from password login or a magic link), a paired **device** (cookie `clefs_device`; staff start a shift on it with a PIN), a **service** (Bearer `CRON_SECRET` or `WORKER_TOKEN`), or **anonymous** (guest pages and public lead routes, rate-limited).
- **Roles** per hotel membership: `owner`, `gm`, `finance`, `revenue`, `dept_head`, `front_desk`, `staff`, plus the pseudo-role `device`. `clefs_admin` is a user flag that passes every check and is audited.
- **Permissions** are strings. The core ones (`core.settings.view`, `core.actions.decide`, `core.autonomy.raise_auto`, ...) are defined by the foundation; each module declares its own in `module.py` (`<segment>.view` is the nav tab gate, usually with `<segment>.manage`), mapped to roles. Route dependencies: `require_perm(...)`, `require_device()`, `require_perm_or_device(...)`, `require_service("cron")`.
- **Magic links** are single-purpose and scoped: a brief link decides exactly the actions it surfaced and nothing else.
- **Legacy open access:** the Indian hotels that predate accounts keep working anonymously on their old dashboard and `/stay` (`legacy_open_access`), with only the permissions the old dashboard had.
- **CSRF:** a cookie-authenticated mutation must come from an allowed `Origin`. CORS never carries credentials: the browser reaches the API through the same-origin `/api/clefs` rewrite.

## 7. Client layout

```
client/app/
  page.tsx, MarketingHomepage.tsx, <solution pages>/   marketing site ("For hotels in the UAE")
  free-ota-audit/, google-check/, pilot/                 lead magnets and the pilot page
  security/, privacy/, dpa/, subprocessors/              trust pages (residency statement, processors)
  login/, auth/                                          password login, magic-link exchange
  app/[hotelSlug]/(console)/                             the console, one folder per nav tab
    layout.tsx, ConsoleShell.tsx, nav.ts                 shell and navigation (frozen, all 15 modules pre-declared)
    brief/ profit/ demand/ inquiries/ service/ concierge/ amenities/ menu/
    reviews/ guests/ visibility/ finance/ ask/ trust/ settings/
  device/[hotelSlug]/                                    the paired-device PWA (top level for PWA scope)
  stay/[hotelSlug]/[roomId]/                             the guest page: concierge, requests, menu, tracker; 8 languages
  g/[token], g/f/[token]                                 journey preference and feedback pages
  q/[token]                                              inquiry quote page
  w/inquiry/[slug]                                       embeddable inquiry form (loader script: public/w/inquiry.js)
  brief/[token]                                          the owner's mobile brief from a magic link
  lib/                                                   api.ts, format.ts, session.tsx, hotel.tsx, i18n.ts, types.ts, claims.ts, ...
  ui/                                                    the UI kit (core, forms, table, overlay, charts, upload, toast)
```

- **Next.js 16, React 18, no Tailwind.** Read `client/node_modules/next/dist/docs/` before writing routing code: route groups, async `params` and metadata differ from older versions.
- **Styling:** each surface injects one CSS string with its own class prefix (`svc-`, `cc-`, `ups-`, `rev-`, `gst-`, `trs-`, `mk-`, ...) and uses the `--clefs-*` design tokens and the `app/ui` kit.
- **Data:** `lib/api.ts` (`apiFetch` through `/api/clefs`, SSE and upload tickets), `lib/format.ts` (money and dates in the hotel's currency and timezone), `lib/session.tsx` (`can(perm)` from the server's permission list; the client never decides access on its own).
- **Protected code:** the avatar and lip-sync logic in `ClefsConcierge.tsx`, `MarketingHomepage.tsx` and `lib/visemeRig.ts` is kept byte-identical across refactors (the mouth gates on live audio RMS; the viseme rig is off by default).
- **Guards:** `scripts/check-claims.mjs` (claims registry), `scripts/check-i18n.mjs` (guest dictionaries), `node --test` for pure helpers.

## 8. Build waves

The Owner Autopilot rebuild (branch `feat/owner-autopilot`, plan in `docs/specs/00-PLAN.md`) was built in waves. Within a wave, lanes worked in parallel on disjoint directories against frozen contracts; each wave ended with an integration pass, the full suite, an adversarial review (correctness, security and privacy, spec compliance, UAE rules, conventions) and a fix loop.

| Wave | What it built | Gate |
|---|---|---|
| **0a Foundation** | The `clefs/` package: db and migrations (0001 is the legacy DDL verbatim), auth, devices, magic links, signals, actions and the autonomy dial, metrics, jobs, events, notify with inbound webhooks, audit, files, AI, money, time, consent; the legacy routes moved behind golden tests; a stub for all 15 modules; the worker, CLI, Docker and Procfile. Client: `lib/*`, the UI kit, `ConsoleShell` and `nav.ts`, login and magic pages, module route stubs. | Golden legacy tests unchanged; contract tests; the existing Indian hotel dashboard and `/stay` still work. |
| **0b Core domain** | `core/stays`, `core/ingest`, `core/tax` (golden tax values), plus the SerpApi client and cost ledger, OAuth, stay events, the K1 action states. | Domain tests; the frozen migration bytes. |
| **1 Service and guest** | Service Control v2 (with the device PWA), Concierge v2 and Voice, Upsell and Reviews, Guests and Journeys, Trust and the marketing site with the claims registry. | Lane suites; protected avatar regions unchanged; i18n completeness; the claims linter; no marketing to OTA aliases; no sentiment-based review routing. |
| **2 Money and growth** | Profit Guard (with the public audit), Demand Radar, Amenities, Brief and Ask, Inquiry Guard, Visibility, Finance. | Detectors at full recall on the plant fixtures with no decoy firing; brief payload lints; Ask evals on the fake AI; inquiry verification and leak matching; visibility statistics and fact errors; USALI reconciliation. |
| **3 Integration** | The deterministic demo seed (`seed-demo`, `demo-check`), end-to-end browser QA, the review over the full diff, these docs. | Everything in 00-PLAN "Verification". |

Changes a lane needed in a frozen file were filed as `# FOUNDATION-REQUEST:` notes and applied (or declined, with the reason) by the integrator. `CONTRACTS.md` Part 3 keeps that ledger per wave.

## 9. How to add a module

A new module touches frozen shared files (the registry, the nav), so it is an integrator change followed by module work.

### Server

1. **Register it** in `server/clefs/registry.py`: add the name to `INSTALLED_MODULES` (its position is the router mount order), a free range in `MIGRATION_RANGES`, a unique prefix in `TABLE_PREFIXES`, its console segment(s) in `ROUTE_SEGMENTS`, and any short key prefix in `KEY_PREFIXES`. Update `CANON_ORDER` in `tests/foundation/test_module_contracts.py` and the tables in `CONTRACTS.md` and this file.
2. **Create the skeleton** under `server/clefs/modules/<m>/`: `__init__.py`, `module.py`, `router.py`, `api.py`, `README.md`, `migrations/`. `test_module_stub_layout` checks it.
3. **Manifest** in `module.py`:

   ```python
   from clefs.modules.<m> import jobs as _jobs  # noqa: F401  registers jobs, signals, actions, metrics
   from clefs.modules.<m>.router import router
   from clefs.registry import Module

   MODULE = Module(
       name="<m>",
       routers=[router],
       permissions={
           "<m>.view": frozenset({"owner", "gm"}),      # the nav tab gate
           "<m>.manage": frozenset({"owner", "gm"}),
       },
   )
   ```

4. **Migrations:** `python -m clefs.migrate new --module <m> "tables"` creates the next free number in range. Every table starts with the prefix, carries `hotel_slug`, money as minor units plus currency, `TIMESTAMPTZ` times.
5. **Routes** under `/hotels/{slug}/<segment>/...` with `Depends(require_perm("<m>.view"))` and friends; public ones under `/public/`, device ones under `/device/`, webhooks under `/webhooks/`. The path parameter is always named `slug`.
6. **Register behaviour** in `jobs.py` (imported by the manifest): signal kinds with a section and cadence, action types with executor, verifier and a sensible default autonomy level (`always_approve=True` for anything touching money, rates or OTA settings), metrics, scheduled jobs and tasks, inbound handlers. Use only your own key prefixes.
7. **Public surface** in `api.py`: small functions with documented default returns. If other modules will call them, add their signatures to `FROZEN_API` in `tests/foundation/test_module_contracts.py`, and never change them afterwards.
8. **Settings:** a Pydantic `settings_model` on the manifest validates the module's per-hotel settings (`hotel_module_settings`), editable in Settings, Modules.
9. **Tests** in `server/tests/modules/<m>/`, run alone with `CLEFS_ONLY_MODULES=<m> .venv/bin/python -m pytest -q tests/modules/<m>`. Use the root fixtures (`cur`, `client_as(role)`, `hotel_uae`, `clock`, `outbox`, `fake_ai`, `run_jobs`).
10. **Demo data:** add a seed part in `server/clefs/seed/demo_uae/` (a module with `PART_ORDER` and `seed_part(ctx)`, planted cases in `ctx.plants`, optional `CHECKS` for `demo-check`). Parts are discovered automatically.

### Client

11. **Nav entry** in `client/app/app/[hotelSlug]/(console)/nav.ts` (frozen; integrator): `key`, `href` (the route segment), `perm` (`<segment>.view`), `module` (the registry name), icon and blurb. A server test checks the perm is registered and held by the owner.
12. **Pages** in `client/app/app/[hotelSlug]/(console)/<segment>/`: `page.tsx` and friends, one CSS string with the module's class prefix, data through `apiFetch`, money and dates through `lib/format.ts`, gates through `can(perm)`.
13. Run `npm run typecheck`, `npm run lint`, `npm run test` and, if the module adds marketing copy, `npm run check:claims`.

### Docs

14. The module's `README.md` (tables, routes, jobs, rules), a row in the root `README.md` module table, and any new environment variable in `server/.env.example` (a test enforces it) and `docs/DEPLOY.md`.

## 10. Rules the tests enforce

`server/tests/foundation/` turns the conventions above into failures:

- The registry lists the 15 modules in canon order; migration ranges never overlap; table prefixes are unique; every discovered migration is inside its module's range; module tables use the module prefix, and core migrations never create one.
- Modules import only foundation code and other modules' `api.py`; foundation code never imports a module.
- Frozen `api.py` signatures match `FROZEN_API`.
- Permissions are namespaced; every module has a `.view` permission for its nav tab; every `perm` in `nav.ts` and every `can("...")` in the console shell is a registered key.
- Module routes live under their prefixes; no method and path is mounted twice.
- Jobs, signals, action types and metrics are prefixed by their registrant; action defaults respect their ceilings; every registered metric compiles and passes `EXPLAIN`.
- Golden shapes of the legacy endpoints; the client types match the server serializers and CHECK constraints.
- Operations: every file parses as Python 3.12; the Python pins agree; `requirements.txt` declares every direct import; FastAPI stays capped; `vercel.json` keeps `sin1` and the cron tick; the cron route is a guarded GET; `.env.example` lists every setting.
