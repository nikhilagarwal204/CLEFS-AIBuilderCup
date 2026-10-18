> Design spec produced during planning (2026-09-27). Where it conflicts with `00-CANON.md`, the Canon wins.

# Clefs Foundation Architecture Plan

## 0. Decisions at a glance

| Concern | Decision |
|---|---|
| Server shape | New `server/clefs/` package. `server/main.py` shrinks to `app = create_app()` so the Vercel entrypoint still works. |
| Router inclusion | An **explicit, pre-declared** module list in `clefs/registry.py`. The foundation creates a stub package for every module. Module agents never touch `main.py`, `app.py` or `registry.py`. |
| Legacy routes | **Moved out of main.py now.** They are split by future owner (service / concierge / guests), keep their paths and response shapes, and are guarded by golden tests. |
| DB | psycopg2 `ThreadedConnectionPool` plus a blocking semaphore, and `with db() as cur` (RealDictCursor, commit/rollback/return). |
| Migrations | SQL or Python files per module, with **reserved number ranges**. The runner applies everything not yet applied, runs under an advisory lock, and uses the unpooled URL. |
| Sessions | **Opaque random tokens**, stored SHA-256 hashed. They live in an **httpOnly cookie on the web origin**, and the browser reaches the API through a Next rewrite `/api/clefs/*`. SSE and large uploads go straight to the API origin using 60s HMAC tickets. |
| Passwords/PINs | `hashlib.scrypt` from the standard library. |
| Realtime bus | Postgres `LISTEN/NOTIFY`, fanned out to per-process SSE queues. This works across uvicorn workers and the worker process. |
| Jobs | One registry and one `tick()`. The always-on worker drives it; `/internal/cron/tick` is the Vercel fallback. The queue uses `SKIP LOCKED`. |
| Files | Postgres `bytea` behind a `FileStore` interface, with an S3 adapter slot. |
| UAE month | AWS me-central-1: VM running api + worker + Caddy, with RDS Postgres 16. The Next client stays on Vercel. |
| Tests | pytest with a fresh database per session (`clefs_t_<pid>`), so 8 agents can run tests at the same time. Each test rolls back to a SAVEPOINT. |

---

## A. Server package layout

```
server/
  main.py                      # 3 lines, frozen: from clefs.app import create_app; app = create_app()
  pyproject.toml               # pytest config only
  requirements.txt / requirements-dev.txt
  Dockerfile  Procfile  docker-compose.yml  .env.example  .python-version (3.12)  vercel.json
  clefs/
    app.py            # create_app(): middleware, CORS, errors, lifespan, router mounting
    registry.py       # INSTALLED_MODULES, MIGRATION_RANGES, TABLE_PREFIXES, ROUTE_PREFIXES, Module dataclass
    settings.py       # typed env (plain pydantic BaseModel, no pydantic-settings dep)
    db.py  migrate.py  cli.py  audit.py  events.py  jobs.py  ai.py  files.py  ratelimit.py
    CONTRACTS.md      # the rules below, for module agents
    migrations/       # 0001-0099 foundation
    auth/  __init__ passwords tokens sessions devices magic deps permissions routes
    notify/ __init__ outbox whatsapp whatsapp_inbound email email_inbound sms
    core/  clock time money hotels rooms signals actions metrics consent privacy errors pagination routes
    legacy/seed_data.py        # HOTELS_DATA, DEMO_GUESTS, DEMO_STAFF moved verbatim
    seed/demo_uae.py           # UAE demo hotel, rooms with floors, staff, owner user
    worker/ __main__.py loop.py
    modules/
      service/ concierge/ profit_guard/ demand/ amenities/ upsell/ reviews/ guests/ brief/ ask/ trust/
        __init__.py  module.py  router.py  api.py  migrations/.gitkeep  README.md   # all stubs from foundation
      service/legacy.py  concierge/legacy.py  guests/legacy.py                      # moved legacy routes
  tests/ conftest.py  foundation/  modules/<name>/__init__.py
```

**Module manifest.** Each module's `module.py` exports `MODULE`:

```python
@dataclass
class Module:
    name: str                                   # must equal registry key
    routers: list[APIRouter] = field(default_factory=list)
    permissions: dict[str, frozenset[str]] = field(default_factory=dict)  # perm -> roles
    worker_services: list[Callable[[], Awaitable[None]]] = field(default_factory=list)  # e.g. SIP voice
    on_startup: Callable[[], None] | None = None
```

Importing `module.py` also imports the module's `jobs.py`, `metrics.py`, `actions.py` and `handlers.py`. Their decorators register into the foundation registries.

**Why an explicit list instead of auto-discovery.** `INSTALLED_MODULES` is frozen in `registry.py` with all 11 names, and the foundation ships a stub for each. Agents therefore only fill in their own directory, which gives the same zero-conflict property as auto-discovery. On top of that, route order is deterministic (it matters for path precedence) and half-built modules never load by accident. A module can be turned off in prod with `CLEFS_DISABLED_MODULES=demand,reviews`.

**`create_app()` does the following:**
- Adds a request-id middleware.
- Sets CORS with an explicit allowlist (`CORS_ALLOWED_ORIGINS` plus `CORS_ALLOW_ORIGIN_REGEX` for Vercel previews) and `allow_credentials=False`. Cookies never cross origins.
- Registers the `ClefsError` handler. It keeps the `{"detail": ...}` shape the legacy client reads and adds `code`.
- Uses a lifespan in place of the deprecated `on_event`: `db.init_pool()`, then `migrate.up()` if `MIGRATE_ON_STARTUP`, then `events.start_listener(loop)`.
- Mounts the core routers (auth, hotels/settings, rooms, signals, actions, metrics, files, stream, webhooks, internal), then each module's routers in registry order.

**Legacy routes move now** because the Service, Concierge and Guests agents each need to own and evolve their part. Leaving them in `main.py` would put three agents in one file. The move is mechanical:
- Handlers switch to `with db() as cur`.
- The connection leak in `get_staff_analytics` (`get_db().cursor()` per staff member, never closed) is fixed by reusing `cur`.
- The hardcoded `'Asia/Kolkata'` is replaced by the hotel's timezone.
- Paths and JSON shapes stay identical.

| Legacy endpoint | New home | Auth after move |
|---|---|---|
| `GET /hotels/{slug}` | `core/routes.py` | public, **projected columns** (name, city, assistant_name, star_rating, locale, languages). The old `SELECT *` would leak the new settings columns. |
| `/hotels/{slug}/requests*`, `/staff*`, `/staff-analytics` | `modules/service/legacy.py` | member or device (or `legacy_open_access`) |
| `POST /hotels/{slug}/pending` (guest request) | `modules/service/legacy.py` | public, DB rate-limited |
| `GET/DELETE /pending` | same | member |
| `/hotels/{slug}/stream` | `events.py` | stream ticket (or `legacy_open_access`) |
| `/conversation-logs` POST / GET | `modules/concierge/legacy.py` | public append / member read |
| `/locations` GET / POST, DELETE | same | public read / member write |
| `/hotels/{slug}/guests*` | `modules/guests/legacy.py` | member only. New public `GET /public/stay/{slug}/{room}` returns **one room**, minimal fields. |

---

## B. DB layer and migrations

```python
# clefs/db.py
def init_pool() -> None                        # ThreadedConnectionPool(minconn=0, maxconn=DB_POOL_MAX)
@contextmanager
def db(*, readonly: bool = False, statement_timeout_ms: int | None = None) -> Iterator[RealDictCursor]
    # acquire (BoundedSemaphore, 10s timeout -> 503), SET LOCAL statement_timeout / READ ONLY,
    # yield cur; commit | rollback on exception; always return conn (discard if conn.closed or OperationalError)
def fetch_one(sql: str, params=None) -> dict | None
def fetch_all(sql: str, params=None) -> list[dict]
def execute(sql: str, params=None) -> int
Json = psycopg2.extras.Json                    # wrap dicts for JSONB params
def raw_connection(unpooled: bool = False) -> psycopg2.extensions.connection   # LISTEN, advisory locks
def get_db()                                   # legacy shim: pooled conn whose close() returns to pool
def _test_override(conn) / _test_clear()       # tests: db() uses SAVEPOINTs on one shared connection
```

**Pool details.**
- psycopg2's pool raises immediately when it is exhausted, and FastAPI runs sync handlers on roughly 40 threads. The semaphore makes callers block instead of failing.
- `DATABASE_URL` falls back to `PSQL_DATABASE_URL`, then the `DB_*` variables (the current behaviour). On Neon it should be the **pooled** endpoint.
- `DATABASE_URL_UNPOOLED` is used for migrations, advisory locks and `LISTEN`. PgBouncer transaction mode breaks all three.
- Suggested `DB_POOL_MAX`: 3 on Vercel, 10 per uvicorn worker on the VM.

**Migration runner** (`python -m clefs.migrate up|status|new --module service "desc"|verify`):
- **Discovery.** Reads `clefs/migrations/` plus every installed module's `migrations/`. Files are named `NNNN_<module>_<desc>.sql` or `.py`, where a `.py` file defines `def up(cur): ...`.
- **Range check.** Each number must fall inside `MIGRATION_RANGES[module]`, otherwise startup fails.
- **Tracking.** The `schema_migrations` table has `version INT PK, module, name, checksum, applied_at, duration_ms`. The runner applies **every unapplied version in ascending order**. It does not use a high-water mark, so service's `0103` can land after profit_guard's `0305`.
- **Transactions and drift.** Each file runs in its own transaction. A checksum mismatch prints a warning in dev and fails `verify` in CI.
- **Lock.** Everything runs under `pg_advisory_lock(hashtext('clefs_migrate'))` on the unpooled connection.
- **Ordering rule.** Foreign keys may only reference core tables (0001-0099), so cross-module ordering never matters.

| Range | Owner | Table prefix |
|---|---|---|
| 0001-0099 | foundation (core) | none (core names) |
| 0100-0199 | service | `svc_` (+ legacy `staff_*`, `pending_requests`) |
| 0200-0299 | concierge | `cc_` (+ `conversation_logs`, `hotel_locations`) |
| 0300-0399 | profit_guard | `pfg_` |
| 0400-0499 | demand | `dr_` |
| 0500-0549 / 0550-0599 | amenities / upsell | `amn_` / `ups_` |
| 0600-0649 / 0650-0699 | reviews / guests | `rev_` / `gst_` (+ `guest_rooms`) |
| 0700-0749 / 0750-0799 | brief / ask | `brf_` / `ask_` |
| 0800-0899 | trust | `trs_` |
| 0900-0999 | reserved (integrator hotfixes) | none |

**Foundation migrations:**
- `0001_core_legacy_baseline.sql`: the current `init_db` DDL verbatim. It is idempotent, so running it against an existing prod database is a no-op, and it includes the `DROP COLUMN notes`.
- `0002_core_seed_legacy_hotels.py`: the `HOTELS_DATA` upsert.
- `0003_core_hotel_settings.sql`
- `0004_core_auth.sql`
- `0005_core_platform.sql`: audit, files, jobs, outbox/inbound, ai_calls, rate limits, consents, module settings.
- `0006_core_signals_actions.sql`
- `0007_core_rooms_daily_stats.sql`
- `0008_core_contract_views.sql`

**Running migrations:**
- Local: `brew services start postgresql@16 && createdb clefs_dev`, then `DATABASE_URL=postgresql://localhost/clefs_dev python -m clefs.migrate up && python -m clefs.cli seed-demo`.
- Neon and RDS: the `release:` step, or `MIGRATE_ON_STARTUP=true`. Keep that default on Vercel to preserve today's init-on-cold-start behaviour. When nothing is pending, the cost is one SELECT.

---

## C. Auth and RBAC

**Tables** (`0004`):

```sql
users(id BIGSERIAL PK, email TEXT UNIQUE, phone_e164 TEXT UNIQUE, name TEXT NOT NULL, password_hash TEXT,
      locale TEXT DEFAULT 'en', is_clefs_admin BOOL DEFAULT FALSE, is_active BOOL DEFAULT TRUE,
      whatsapp_opt_in_at TIMESTAMPTZ, created_at, last_login_at)
hotel_memberships(id, user_id REFERENCES users, hotel_slug REFERENCES hotels(slug), role TEXT CHECK (role IN
      ('owner','gm','finance','revenue','dept_head','front_desk','staff')), departments TEXT[] DEFAULT '{}',
      floors TEXT[] DEFAULT '{}', is_active, created_at, UNIQUE(user_id, hotel_slug))
auth_sessions(id, token_hash BYTEA UNIQUE, user_id, kind TEXT ('web','magic'), scope JSONB, created_at,
      expires_at, last_seen_at, ip INET, user_agent TEXT, revoked_at)
devices(id, hotel_slug, name, kind ('floor','department','desk','phone_console','personal'), floors TEXT[],
      departments TEXT[], token_hash BYTEA UNIQUE, paired_by, paired_at, last_seen_at, revoked_at)
device_pairing_codes(code_hash BYTEA PK, hotel_slug, spec JSONB, created_by, expires_at, used_at)
device_shifts(id, device_id, staff_member_id, started_at, ended_at, handover_note, handover_ack_by, handover_ack_at)
magic_links(id, token_hash UNIQUE, user_id, hotel_slug, purpose ('brief','login','action'), next_path TEXT,
      scope JSONB, expires_at, max_uses INT DEFAULT 3, uses INT DEFAULT 0, created_at)
ALTER TABLE staff_members ADD pin_hash TEXT, ADD user_id BIGINT, ADD floors TEXT[], ADD whatsapp_e164 TEXT,
      ADD pin_failed_count INT DEFAULT 0, ADD pin_locked_until TIMESTAMPTZ;
```

**Why opaque tokens and not JWT.** Staff turnover, lost floor tablets and owner phone changes all need instant revocation, and a JWT would need a denylist anyway. The lookup is one indexed query, cached in-process for 30 seconds. There is no key rotation to manage. Tokens come from `secrets.token_urlsafe(32)` and only the `sha256` is stored.

**Password and PIN hashing.** `auth/passwords.py` uses `hashlib.scrypt(n=2**14, r=8, p=1, dklen=64)` with a 16-byte salt, encoded as `scrypt$16384$8$1$<salt>$<hash>`, and compares with `hmac.compare_digest`. PINs (4-6 digits) use the same hash. Their real protection is that a PIN only works on a **paired device of that hotel**, plus a lockout: 5 failures lock that staff member on that device for 5 minutes.

**How the browser sends credentials.** This is an httpOnly cookie, not a Bearer token in localStorage.
- `next.config.mjs` rewrites `/api/clefs/:path*` to `${CLEFS_API_ORIGIN}/:path*`. `CLEFS_API_ORIGIN` is a server-only env var.
- FastAPI sets `clefs_session` (and `clefs_device` for tablets) as `HttpOnly; Secure; SameSite=Lax; Path=/` with no Domain attribute. Because the browser sees the web origin, the cookie is first-party on www, on previews and on localhost alike. XSS cannot read it, which matters for a console holding financial and guest data.
- **CSRF protection:** on cookie-authenticated mutations the server checks `Origin`/`Sec-Fetch-Site` against `WEB_ORIGINS`, plus `SameSite=Lax`. Because no custom header is required, the legacy dashboard's raw `fetch` calls keep working once `API_BASE` becomes `/api/clefs`.
- Bearer tokens (`Authorization: Bearer`) are accepted too, for scripts, the worker, and `CRON_SECRET`/service tokens.

**SSE and uploads use tickets.** EventSource cannot set headers, and a long-lived stream should not depend on the Vercel proxy.
- `POST /auth/stream-ticket {hotel_slug}` returns `{url, expires_in: 60}`. The ticket is `b64url(payload).HMAC_SHA256(SECRET_KEY)`, where the payload binds principal kind and id, hotel, audience filters and expiry.
- The client opens `EventSource(NEXT_PUBLIC_API_URL + "/hotels/{slug}/stream?t=...")`. The ticket is checked only at connect time. The server closes the stream after 30 minutes and the client re-mints a ticket on error.
- `POST /files/upload-ticket` works the same way, so large OTA statement PDFs can go directly to the API.

**FastAPI dependencies** (`auth/deps.py`):

```python
@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "device", "service", "anonymous"]
    user_id: int | None; device_id: int | None; active_staff_ids: tuple[int, ...]
    is_clefs_admin: bool; session_id: int | None; scope: dict

@dataclass(frozen=True)
class HotelCtx:
    principal: Principal; hotel: Hotel; role: str | None; permissions: frozenset[str]
    departments: tuple[str, ...]; floors: tuple[str, ...]

def current_principal(request: Request) -> Principal                 # never raises
def require_user() -> Principal                                      # 401
def require_perm(*perms: str) -> Callable[..., HotelCtx]             # reads path param `slug`; 403 unless any perm
def require_device(*, departments: set[str] | None = None) -> Callable[..., HotelCtx]
def require_perm_or_device(*perms: str) -> Callable[..., HotelCtx]
def require_legacy_or(*perms: str) -> Callable[..., HotelCtx]        # passes anonymous if hotel.legacy_open_access
def require_service(name: Literal["cron", "worker"]) -> Callable[..., Principal]
def acting_staff(ctx: HotelCtx, request: Request) -> int | None      # X-Clefs-Staff header, validated vs active shifts
```

**Permissions, not raw roles.** The foundation registers the core permissions:

| Permission | Roles |
|---|---|
| `core.settings.view` | owner, gm, finance, revenue, dept_head |
| `core.settings.manage` | owner, gm |
| `core.team.manage` | owner, gm |
| `core.signals.view` | owner, gm, finance, revenue, dept_head |
| `core.actions.decide` | owner, gm |

Each module registers its own permissions through `Module.permissions`, keyed `<module>.*`. Defaults each module should adopt:

| Module permission | Roles |
|---|---|
| `service.view` | all roles plus devices |
| `profit.*` | owner, gm, finance |
| `demand.*` | owner, gm, revenue |
| `guests.view` (PII) | owner, gm, front_desk |
| `trust.audit.view` | owner, gm |

- `clefs_admin` (a flag on `users`) passes every check and is audited.
- `dept_head` is scoped by `departments[]`.
- `/auth/me?hotel=` returns the resolved `permissions[]`, so the client never duplicates the matrix.

**Auth routes** (`auth/routes.py`):
- `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
- `POST /auth/magic/request {phone|email}`: sends a WhatsApp or email link.
- `POST /auth/magic/exchange {token}`
- `POST /hotels/{slug}/devices/pairing-codes` (requires `service.manage`), `POST /auth/devices/pair {code}`
- `POST /auth/devices/shift/start {staff_member_id, pin}`, `POST /auth/devices/shift/end {shift_id, handover_note}`
- `POST /auth/stream-ticket`

**Magic links:**
- Opaque and stored hashed. They expire after 24 hours and allow at most 3 uses.
- **The exchange is a POST from the landing page**, never the GET. WhatsApp's link-preview fetcher would otherwise burn the token.
- A magic session has `kind='magic'` with a scope of `{hotel, purposes:['brief','actions']}` and lasts 12 hours. Full console access needs a password login.
- WhatsApp button taps do not need links at all: Meta verifies the sender's phone, which the server maps to a user.

**Guest QR flow stays public and stops leaking.**
- The public endpoints (`/public/stay/{slug}/{room}`, `POST /pending`, `POST /conversation-logs`, `GET /locations/{loc}`) are rate-limited per IP and room through `ratelimit.hit()`, which is DB-backed and replaces the in-memory dict.
- Newly printed QR codes carry `?k=<qr_key>`, where `qr_key = base32(HMAC(SECRET_KEY, slug:room:version))[:10]`.
- **Old printed QR codes stay valid** (the `/aria` redirect is permanent). Without `k`, the per-room endpoint returns personalization only if `hotel_module_settings['concierge'].legacy_qr_personalization` is true. Otherwise it returns no PII.
- The foundation edits `getRoomData` in `client/app/lib/guestData.ts` to call the per-room endpoint instead of downloading the whole roster.

**Grandfathering existing hotels.** Migration `0003` adds `hotels.legacy_open_access BOOL DEFAULT FALSE` and sets it TRUE for every existing row. Today's Indian dashboards and staff phone pages keep working without login, while new UAE hotels are locked by default. The Service agent removes the flag once devices ship.

**Bootstrap CLI** (`python -m clefs.cli ...`):
- `create-hotel --slug --name --country AE --emirate dubai --tz Asia/Dubai --currency AED --locale en-AE`
- `create-user --email --name --phone --password-prompt`
- `grant --email --hotel --role owner [--departments ...]`
- `make-admin --email`
- `pair-code --hotel --name "Floor 5" --floors 5 --departments Housekeeping`
- `seed-demo --hotel demo-dubai`

---

## D. Hotel settings

```sql
-- 0003: typed core settings (existing rows keep India defaults)
ALTER TABLE hotels
  ADD COLUMN IF NOT EXISTS country CHAR(2) NOT NULL DEFAULT 'IN',
  ADD COLUMN IF NOT EXISTS emirate TEXT,                        -- dubai|abu_dhabi|sharjah|ajman|rak|fujairah|uaq
  ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
  ADD COLUMN IF NOT EXISTS currency CHAR(3) NOT NULL DEFAULT 'INR',
  ADD COLUMN IF NOT EXISTS locale TEXT NOT NULL DEFAULT 'en-IN',
  ADD COLUMN IF NOT EXISTS guest_languages TEXT[] NOT NULL DEFAULT '{en}',
  ADD COLUMN IF NOT EXISTS weekend_days SMALLINT[] NOT NULL DEFAULT '{6,7}',   -- ISO weekday, Sat-Sun
  ADD COLUMN IF NOT EXISTS lat NUMERIC(9,6), ADD COLUMN IF NOT EXISTS lng NUMERIC(9,6), ADD COLUMN IF NOT EXISTS address TEXT,
  ADD COLUMN IF NOT EXISTS business_day_cutoff TIME NOT NULL DEFAULT '04:00',
  ADD COLUMN IF NOT EXISTS brief_time_local TIME NOT NULL DEFAULT '07:00',
  ADD COLUMN IF NOT EXISTS tax_profile JSONB NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS whatsapp_phone_number_id TEXT, ADD COLUMN IF NOT EXISTS whatsapp_display_number TEXT,
  ADD COLUMN IF NOT EXISTS front_desk_phone TEXT, ADD COLUMN IF NOT EXISTS inbound_email_alias TEXT UNIQUE,
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',            -- active|pilot|demo|archived
  ADD COLUMN IF NOT EXISTS legacy_open_access BOOLEAN NOT NULL DEFAULT FALSE;
-- 0005: per-module settings, so modules never ALTER hotels
hotel_module_settings(hotel_slug, module TEXT, enabled BOOL DEFAULT TRUE, settings JSONB DEFAULT '{}',
                      updated_by BIGINT, updated_at, PRIMARY KEY(hotel_slug, module))
```

**Tax profile.** This is data-driven, not hardcoded. Presets live in `core/money.py`, for example `dubai_standard`:
- service charge 10% on room
- municipality fee 7% on room
- Tourism Dirham per room-night by classification
- VAT 5%, with a per-component `vatable` flag

Finance must confirm each preset. Profit Guard needs this to catch commission charged on the tax-inclusive amount.

**How modules read settings** (`core/hotels.py`):

```python
class Hotel(BaseModel): slug; name; country; emirate; timezone; currency; locale; guest_languages;
                        weekend_days; business_day_cutoff; brief_time_local; tax_profile; ...
def get_hotel(cur, slug: str) -> Hotel                       # 60s in-process cache, invalidated on PATCH
def get_module_settings(cur, slug: str, module: str, model: type[T]) -> T   # validated, defaults applied
def set_module_settings(cur, slug: str, module: str, data: BaseModel, *, actor: Principal) -> None  # audited
def module_enabled(cur, slug: str, module: str) -> bool
```

Each module defines its own `settings.py` Pydantic model with defaults, so no module needs a migration to add a setting.

**Routes:**
- `GET /hotels/{slug}/context` (member): hotel, settings, enabled modules, my role and permissions.
- `PATCH /hotels/{slug}/settings` (`core.settings.manage`).

---

## E. Cross-module contracts

These are the key to parallel work. Every contract below has fixed signatures in the foundation wave.

### E1. Signals (candidate brief items any module emits)

```sql
signals(id BIGSERIAL PK, hotel_slug REFERENCES hotels(slug), module TEXT, kind TEXT, dedupe_key TEXT NOT NULL,
  title TEXT, summary TEXT, severity SMALLINT DEFAULT 2 CHECK (severity BETWEEN 1 AND 4),
  impact_amount NUMERIC(14,2), impact_kind TEXT,              -- recoverable|revenue_upside|cost_leak|risk|service
  currency CHAR(3), confidence NUMERIC(3,2) CHECK (confidence BETWEEN 0 AND 1),
  evidence JSONB NOT NULL DEFAULT '[]', recommended_action JSONB, owner_role TEXT, owner_user_id BIGINT,
  due_at TIMESTAMPTZ, subject_type TEXT, subject_id TEXT,
  status TEXT NOT NULL DEFAULT 'open',   -- open|surfaced|decided|resolved|expired|ignored|superseded
  occurrences INT DEFAULT 1, first_seen_at, last_seen_at, surfaced_at, resolved_at, resolution TEXT, expires_at,
  UNIQUE(hotel_slug, dedupe_key));
CREATE INDEX ON signals(hotel_slug, status, due_at);
```

```python
class Evidence(BaseModel):
    kind: Literal["metric", "row", "file", "quote", "link", "calc", "series"]
    label: str; value: Any | None = None; ref: dict | None = None   # {"file_id","page"} | {"table","id"} | {"metric","query"}
class RecommendedAction(BaseModel):
    type: str                      # "<module>.<verb>", must be a registered action type
    label: str; params: dict = {}; owner_role: str | None = None
    due_in_hours: int | None = None; expected_value: Decimal | None = None
class SignalIn(BaseModel):
    module: str; kind: str; dedupe_key: str; title: str; summary: str | None = None; severity: int = 2
    impact_amount: Decimal | None = None; impact_kind: str | None = None; currency: str | None = None
    confidence: float; evidence: list[Evidence] = []; recommended_action: RecommendedAction | None = None
    owner_role: str | None = None; due_at: datetime | None = None
    subject_type: str | None = None; subject_id: str | None = None; expires_at: datetime | None = None
def upsert_signal(cur, hotel_slug: str, s: SignalIn, *, reopen_if_impact_grows_pct: float = 25) -> dict
def resolve_signal(cur, hotel_slug: str, dedupe_key: str, *, resolution: str) -> bool
def resolve_missing(cur, hotel_slug: str, module: str, kind: str, seen_keys: set[str]) -> int
def list_signals(cur, hotel_slug: str, *, status=("open", "surfaced"), modules=None, limit=50) -> list[dict]
def mark_surfaced(cur, ids: list[int]) -> None
def register_signal_kind(key: str, *, label: str, default_owner_role: str, icon: str | None = None) -> None
```

Detectors are idempotent. `upsert_signal` refreshes an existing signal (it does not duplicate), and `resolve_missing` closes signals whose detector no longer sees them. Brief ranking belongs to the Brief module, not the foundation.

### E2. Actions ledger (the detect, explain, approve, execute, verify loop)

```sql
actions(id BIGSERIAL PK, hotel_slug, signal_id BIGINT REFERENCES signals(id), module, type, title, params JSONB,
  status TEXT NOT NULL DEFAULT 'proposed', -- proposed|approved|assigned|in_progress|done|verified|failed|disputed|ignored|cancelled
  decided_by BIGINT, decided_at, decided_via TEXT,             -- console|whatsapp|api|system
  assignee_user_id BIGINT, assignee_role TEXT, due_at TIMESTAMPTZ,
  executed_at, execution_result JSONB, verify_after TIMESTAMPTZ, verified_at, verification JSONB,
  expected_value NUMERIC(14,2), outcome_value NUMERIC(14,2), currency CHAR(3), outcome_confidence NUMERIC(3,2),
  reminders_sent INT DEFAULT 0, last_reminded_at, escalated_at, created_by BIGINT, created_at, updated_at);
action_events(id, action_id, at, actor_type, actor_id, event, data JSONB);
```

```python
@dataclass(frozen=True)
class ActionType:
    key: str; module: str; label: str
    executor: Callable[[ActionCtx], dict] | None      # None = human task
    verifier: Callable[[ActionCtx], VerifyResult] | None
    verify_after: timedelta | None; default_owner_role: str
def register_action_type(t: ActionType) -> ActionType
def create_action(cur, hotel_slug, *, type, title, params, signal_id=None, expected_value=None,
                  currency=None, owner_role=None, due_at=None, actor: Principal) -> dict
def decide(cur, action_id: int, decision: Literal["approve","adjust","assign","dispute","remind","ignore"], *,
           actor: Principal, via: str, params_patch: dict | None = None,
           assignee_user_id: int | None = None, note: str | None = None) -> dict   # audits, enqueues executor
def mark_done(cur, action_id: int, *, actor: Principal, result: dict | None = None) -> dict
def record_verification(cur, action_id: int, *, verified: bool, outcome_value: Decimal | None,
                        evidence: list[Evidence], confidence: float) -> dict
```

The foundation ships:
- the `core.actions.execute` queue task
- the `core.actions.verify` hourly job
- the routes `GET/POST /hotels/{slug}/actions`, `POST /actions/{id}/decide` and `POST /actions/{id}/done`

Reminders, escalation and the weekly value report belong to the Brief module.

### E3. Metrics registry (governed, parameterized SQL)

```python
@dataclass(frozen=True)
class Dimension: key: str; label: str; sql: str; values_sql: str | None = None
@dataclass(frozen=True)
class Metric:
    key: str; label: str; description: str
    unit: Literal["count","money","percent","minutes","ratio","nights","rate"]
    source: str            # "FROM staff_requests r LEFT JOIN hotel_rooms hr ON ..."
    hotel_column: str      # "r.hotel_slug"
    time_column: str; time_kind: Literal["timestamptz","date","epoch_ms"]
    measure: str           # "COUNT(*)", "100.0*COUNT(*) FILTER (WHERE ...)/NULLIF(COUNT(*),0)"
    where: str = ""; dimensions: tuple[Dimension, ...] = (); permission: str = ""
    currency: Literal["hotel"] | None = None; higher_is_better: bool | None = None; synonyms: tuple[str, ...] = ()
class MetricQuery(BaseModel):
    metric: str; start: date; end: date; grain: Literal["total","day","week","month"] = "total"
    group_by: list[str] = []; filters: dict[str, list[str]] = {}; compare: Literal["prev_period","prev_year"] | None = None
def register_metric(m: Metric) -> Metric
def list_metrics(ctx: HotelCtx) -> list[Metric]
def compile_metric(hotel: Hotel, q: MetricQuery) -> tuple[str, dict]
def run_metric(ctx: HotelCtx, q: MetricQuery) -> MetricResult     # READ ONLY txn, statement_timeout 5s, LIMIT 500
```

Only identifiers from the registry are ever interpolated into SQL. Every value is a parameter, and `hotel_column = %(hotel)s` is always injected. Time buckets use `AT TIME ZONE %(tz)s`. Ask Clefs's function-calling schema is generated from this registry.

### E4. Jobs

```python
def every(*, minutes: int = 0, seconds: int = 0) -> Schedule
def daily_at(hhmm: str, *, tz: Literal["hotel","utc"] = "hotel", setting: str | None = None) -> Schedule  # setting="brief_time_local"
def weekly_at(iso_weekday: int, hhmm: str, *, tz="hotel") -> Schedule
def scheduled(name: str, schedule: Schedule, *, per_hotel: bool = True, timeout_s: int = 120): ...  # fn(ctx: JobCtx) -> dict|None
def task(name: str, *, max_attempts: int = 5, backoff_s: int = 30): ...                           # fn(ctx: JobCtx, payload: dict)
def enqueue(name: str, payload: dict, *, hotel_slug=None, run_at=None, dedupe_key=None, cur=None) -> int | None
def tick(*, budget_s: float = 25.0) -> TickReport
```

**Tables:**
- `job_runs(job, hotel_slug, scheduled_for, status, attempts, started_at, finished_at, error, result, UNIQUE(job, hotel_slug, scheduled_for))`. The unique constraint makes claiming idempotent.
- `job_queue(id, name, hotel_slug, payload, run_at, dedupe_key UNIQUE, attempts, locked_by, locked_until, status, last_error)`, claimed with `FOR UPDATE SKIP LOCKED`.

**Behaviour:**
- Hotel-local daily jobs catch up if a tick was missed on the same local day.
- SLA timers (3/5/10 min) use `enqueue(run_at=...)`, not polling.
- Job names must start with `<module>.`.

### E5. Events (SSE)

```python
def publish(hotel_slug: str, event: str, data: dict, *, audience: dict | None = None, cur=None) -> None
```

- With `cur`, the event goes out as `pg_notify` inside the transaction, so it is **delivered only on commit**.
- Payloads must be 7KB or less. Anything larger sends ids and the client refetches.
- `audience` can be `{roles, departments, floors, device_ids}`. It is filtered per subscriber on the server.
- Each API process runs one LISTEN thread on the unpooled URL and fans out to asyncio queues.
- `EVENTS_BACKEND=local` keeps today's in-process behaviour, for Vercel.
- Event names are `<module>.<event>`. The Service module keeps emitting the legacy `requests_changed`, `new_clefs_request` and `new_aria_request`.

### E6. Notifications

```python
class NotifyResult(BaseModel): ok: bool; channel: str; outbox_id: int; status: str; error: str | None = None
def send_whatsapp_template(to: str, template: str, lang: str, params: list, *, hotel_slug, purpose,
                           sender: Literal["platform","hotel"] = "platform", buttons: list | None = None,
                           idempotency_key: str | None = None) -> NotifyResult
def send_whatsapp_text(to, body, **kw) -> NotifyResult          # returns status 'outside_window' if >24h
def send_whatsapp_interactive(to, body, *, buttons: list[tuple[str, str]] | None = None,
                              sections: list | None = None, header=None, footer=None, **kw) -> NotifyResult
def send_whatsapp_document(to, file_id: str, filename: str, caption: str | None = None, **kw) -> NotifyResult
def send_email(to, subject, html, *, text=None, attachments: list[str] = (), reply_to=None, **kw) -> NotifyResult
def send_sms(to, text, **kw) -> NotifyResult
def notify(recipient: Recipient, msg: Message, *, channels=("whatsapp","sms","email")) -> NotifyResult  # fallback chain
```

- Every send writes to `outbound_messages(... status queued|sent|delivered|read|failed|skipped_unconfigured, provider_id, idempotency_key UNIQUE)` and is delivered by the `core.notify.deliver` task.
- Meta status webhooks update `delivered`/`read`. The escalation ladder uses the `read` status.
- `NOTIFY_MODE=live|log|capture` gives graceful degradation and makes tests easy.
- SMS providers: `msg91` (legacy India) or `twilio` (UAE).
- Brief delivery at 07:00 is a **template** message, because it is business-initiated. Per-item approve/assign buttons are interactive messages sent inside the 24-hour window that the owner's tap opens.

**Inbound dispatch** is owned by the foundation, because one Meta webhook serves every module:
- `/webhooks/whatsapp` verifies `X-Hub-Signature-256` and dedupes on `inbound_messages.provider_id UNIQUE`.
- It resolves the sender: platform number plus known user means `staff_owner`; a hotel number means `guest`.
- Modules register handlers with `@on_whatsapp(audience=..., button_prefix="brief:")` or `@on_whatsapp(audience="guest", kind="text")`.
- Button ids carry a short HMAC tag from `tokens.sign_ref`/`verify_ref`.
- Email works the same way: `/webhooks/email/postmark`, with `<alias>@in.clefs.world` mapping to a hotel. Attachments are stored as files and dispatched via `@on_inbound_email(purpose=...)`.

### E7. Audit, files, AI, money, time, consent

```python
# audit.py -> audit_log(id, hotel_slug, at, actor_type, actor_id, actor_label, action, entity_type, entity_id,
#                       before JSONB, after JSONB, ip, user_agent, request_id, meta JSONB)
def audit(cur, ctx: HotelCtx | Principal, action: str, entity_type: str, entity_id: str | int, *,
          before: dict | None = None, after: dict | None = None, meta: dict | None = None) -> None
# files.py -> files(id UUID, hotel_slug, sha256, filename, content_type, size_bytes, storage 'db'|'s3',
#             data BYTEA, s3_key, source 'upload'|'email'|'whatsapp'|'generated', purpose, uploaded_by,
#             retention_until, created_at, deleted_at)   max FILE_MAX_BYTES=25MB
def put_file(cur, hotel_slug, data: bytes, *, filename, content_type, purpose, source, actor) -> FileRef
def get_file(cur, hotel_slug, file_id) -> tuple[FileRef, bytes]
# routes: POST /hotels/{slug}/files (multipart, needs python-multipart), GET /hotels/{slug}/files/{id}
# ai.py (Gemini API through google-genai; logs ai_calls(hotel, task, model, tokens_in/out, latency_ms, status), no content unless AI_LOG_CONTENT)
def structured(task: str, *, system: str, input: str | list, schema: type[T], hotel_slug: str | None = None,
               model: str | None = None, file_ids: list[str] = (), max_output_tokens: int = 4000) -> T
def text(task: str, *, system: str, input, hotel_slug=None, model=None) -> str
def tool_call(task: str, *, system: str, input, tools: list[dict], hotel_slug=None) -> list[ToolCall]
def web_search(task: str, *, query: str, schema: type[T], hotel_slug=None) -> T
# model = AI_MODEL_<TASK> or AI_MODEL_DEFAULT; AI_ENABLED kill switch; set_backend(Fake) for tests
# core/money.py
def D(x) -> Decimal; def quantize(a: Decimal, ccy: str) -> Decimal; def fmt_money(a, ccy, locale) -> str
def convert(cur, a: Decimal, src: str, dst: str, on: date) -> Decimal     # fx_rates table, AED/USD 3.6725 peg fallback
def compose(net_room: Decimal, room_nights: int, profile: TaxProfile) -> TaxBreakdown
def decompose(gross: Decimal, room_nights: int, profile: TaxProfile) -> TaxBreakdown
# core/time.py (all "now" goes through core/clock.now(), which tests can freeze)
def hotel_now(h: Hotel) -> datetime; def business_date(h, ts: datetime | None = None) -> date
def local_day_bounds(h, d: date) -> tuple[datetime, datetime]; def local_range(h, start, end) -> tuple[datetime, datetime]
def is_weekend(h, d: date) -> bool; def to_ms(dt) -> int; def from_ms(ms: int) -> datetime
# core/consent.py -> consents(hotel_slug, subject_type, subject_ref (hashed), purpose, granted, source, evidence, created_at, expires_at)
def record_consent(cur, hotel_slug, subject_ref, purpose, granted: bool, *, source, evidence=None) -> None
def has_consent(cur, hotel_slug, subject_ref, purpose) -> bool
# core/privacy.py (registry only; the Trust module implements the retention/DSAR engine)
def register_pii(module, table, *, subject_columns: dict, ts_column: str, retention_days: int,
                 strategy: Literal["delete","redact"], redact_columns=()) -> None
```

**Shared core data** (`0007`), used by several modules:
- `hotel_rooms(hotel_slug, room_number, floor, room_type, qr_key_version, is_active, PK(hotel_slug, room_number))`, with `rooms.floor_for(h, room)`.
- `hotel_daily_stats(hotel_slug, date, rooms_available, rooms_sold, room_revenue, source, PK(hotel_slug, date, source))`.

**Contract view** (`0008`): `service_requests_v1` over `staff_requests`, with columns `id, hotel_slug, room, floor, request_type, department, priority, status, source, created_at, acknowledged_at, resolved_at, assignee_label`. The Service module may `CREATE OR REPLACE` it but must keep these columns. Amenities, Ask and Brief read only this view.

**Cross-module `api.py` stubs.** Signatures are frozen by the foundation, which also ships working minimal implementations:
- `service.api.create_request(cur, hotel_slug, *, room, type, description, source, subtype=None, priority_hint=None, language=None, guest_ref=None) -> dict`. It delegates to the moved legacy insert-and-assign.
- `service.api.escalate_to_human(cur, hotel_slug, *, room, reason, channel, sensitive: bool, excerpt: str | None) -> dict`
- `service.api.request_status_for_guest(cur, hotel_slug, tracking_token) -> dict | None`
- `concierge.api.build_session(hotel_slug, *, channel: Literal["web","sip","whatsapp","text"], room=None, location=None, language=None) -> SessionSpec`
- `concierge.api.respond_text(spec, messages) -> TextTurn`, used by Trust evals.
- `upsell.api.get_menu(cur, hotel_slug, *, outlet=None, language="en") -> list[dict]`. Returns `[]` until implemented.
- `upsell.api.create_order(cur, hotel_slug, *, room, lines, source) -> dict`
- `guests.api.guest_context_for_room(cur, hotel_slug, room) -> dict | None`. Minimal fields, consent-aware.

The rule: a module may import only foundation code and other modules' `api.py`.

---

## F. Client foundation

**Next config and proxy:**
- `next.config.mjs` adds `rewrites(): [{source: "/api/clefs/:path*", destination: \`${process.env.CLEFS_API_ORIGIN}/:path*\`}]`. The `/aria` redirect stays.
- `proxy.ts` (the Next 16 name for middleware) does only an optimistic check. For `/app/:path*` with no `clefs_session` or `clefs_device` cookie, it adds the header `x-clefs-anon: 1`. **It does not redirect**, because `legacy_open_access` hotels must still load. `ConsoleShell` decides.

**`app/lib/api.ts`:**

```ts
export const API_PREFIX = "/api/clefs";
export class ApiError extends Error { status: number; code?: string; detail?: unknown }
export function apiFetch<T>(path: string, init?: RequestInit & { json?: unknown; query?: Record<string, string | number | boolean | undefined> }): Promise<T>
export const api: { get<T>(p: string, q?): Promise<T>; post<T>(p: string, body?): Promise<T>; patch<T>(p: string, body?): Promise<T>; put<T>(p: string, body?): Promise<T>; del<T>(p: string): Promise<T> };
export const hotelPath = (slug: string, p = "") => `/hotels/${encodeURIComponent(slug)}${p}`;
export function openHotelStream(slug: string, handlers: Record<string, (data: any) => void>,
  opts?: { onStatus?: (s: "live" | "reconnecting" | "polling") => void }): () => void;   // ticket, re-mint, backoff, 20s poll fallback
export function uploadFile(slug: string, file: File, purpose: string, onProgress?: (pct: number) => void): Promise<FileRef>;
export function useApi<T>(path: string | null, deps?: unknown[]): { data?: T; error?: ApiError; loading: boolean; reload(): void };
```

A 401 dispatches the `clefs:unauthorized` window event, and the session provider redirects to `/login?next=`.

**Other shared client files:**
- `app/lib/format.ts`: `formatMoney(amount, currency, locale)`, `formatDateTime(ts, tz, locale, style)`, `formatDate`, `formatTime`, `relativeTime`, `fmtMins`, `slaColor(mins, targetMins)`, `formatPct`, plus `useFormat()`, which binds to the hotel's currency, timezone and locale. This replaces the hardcoded `en-IN`.
- `app/lib/session.tsx`: `SessionProvider`, `useSession(): { me, role, permissions, can(perm), legacyMode, logout() }`
- `app/lib/hotel.tsx`: `HotelProvider`, `useHotel()`
- `app/lib/types.ts`: `Signal`, `Evidence`, `RecommendedAction`, `Action`, `Decision`, `MetricResult`, `HotelContext`, `Me`, `Role`, `Page<T>`
- `app/lib/i18n.ts`: `translate(dict, key, vars)`, `dirFor(locale)`, `RTL_LOCALES`, `pickLocale(accepted, supported)`

**Route map.** Route groups let the console and kiosk surfaces use different layouts.

```
/login, /auth/magic/[token]                                   foundation
/app/[hotelSlug]/(console)/layout.tsx  ConsoleShell.tsx  nav.ts  page.tsx (role landing)  settings/**   foundation
/app/[hotelSlug]/(console)/service/**        service   (page.tsx mounts <StaffDashboard/> unchanged)
/app/[hotelSlug]/(console)/concierge/**      concierge (knowledge, locations, transcripts)
/app/[hotelSlug]/(console)/profit/**         profit_guard     /free-ota-audit/**   profit_guard (public)
/app/[hotelSlug]/(console)/demand/**         demand
/app/[hotelSlug]/(console)/amenities/**      amenities        /(console)/menu/**   upsell
/app/[hotelSlug]/(console)/reviews/**  guests/**   reviews, guests
/app/[hotelSlug]/(console)/brief/**  ask/**      brief, ask
/app/[hotelSlug]/(console)/trust/**           trust
/app/[hotelSlug]/(kiosk)/device/**, /device (pairing)   service
/app/[hotelSlug]/staff/[staffId]              legacy, service
/stay/[hotelSlug]/[roomId]                    concierge; /stay/requests/** service
```

The old `app/app/[hotelSlug]/page.tsx` is deleted, and `(console)/page.tsx` replaces it with a role-based landing:

| Role | Lands on |
|---|---|
| owner, gm, clefs_admin | brief |
| finance | profit |
| revenue | demand |
| dept_head, front_desk, staff | service |

**Mounting StaffDashboard without a rewrite:**
- `ConsoleShell` is a 56px top bar with brand, hotel, horizontal module tabs from `nav.ts` filtered by `can(perm)` and enabled modules, and a user menu. On mobile it becomes a bottom tab bar. There is **no left rail**, so StaffDashboard's own sidebar remains the Service sub-nav.
- The shell's injected CSS sets `--cx-top: 56px` and overrides `.cx-shell .sidebar-aside { top: var(--cx-top); height: calc(100vh - var(--cx-top)); }`, `.cx-shell .app-container { min-height: calc(100vh - var(--cx-top)); }` and `.cx-shell .mobile-top-bar { top: var(--cx-top); }`.
- The foundation makes exactly **two edits** to `StaffDashboard.tsx`: `API_BASE` becomes `API_PREFIX` from `../lib/api`, and the `EventSource` block becomes `openHotelStream(...)`. The legacy staff page gets the `API_BASE` edit only.

**UI kit** (`app/ui/`, barrel `index.ts`). Its CSS string `uiStyles` is injected once by the shell. Every class is prefixed `cx-` to avoid collisions with StaffDashboard's global classes like `.glass-card`, `.modal-box` and `.status-badge`. Components:
- Layout and content: `Card`, `StatTile` (with a sparkline slot), `Badge`, `Button`, `Table<T>` (mobile cards via `data-label`, reusing the history-table technique), `Modal`, `Drawer`, `Tabs`, `EmptyState`, `Skeleton`, `PageHeader`, `Field/Input/Select/Textarea`, `Segmented`, `DateRange`.
- Feedback: `ToastProvider` and `useToast`.
- Uploads and money: `FileDrop` plus `useUpload`, `MoneyText`.
- Brief and signal rendering: `ConfidencePill`, `EvidencePanel`, `SignalCard`, `ActionBar` (approve, adjust, assign with a picker, dispute, remind, ignore).

Module CSS strings must use a module prefix (`svc-`, `pfg-`, `dr-` and so on).

**Charts.**
- The foundation adds `export` to `useWidth`, `Tip`, `niceMax` and `axisTicks` in `app/app/charts.tsx`. The file is frozen after that.
- A new `app/ui/charts-extra.tsx` adds `LineChart` (multi-series, guardrail band, event markers), `CalendarHeatmap` (weekend shading from `weekend_days`), `DivergingBar`, `Donut` (at most 5 slices plus "Other") and `Sparkline`.
- New diverging colours are validated for colour-vision deficiency the same way the header of `charts.tsx` describes.

**Guest i18n.** Dictionaries live in `app/stay/i18n/{en,ar,hi,ur,ru,zh,...}.ts`, owned by Concierge. The console stays English-only for now.

**Lint.** `next lint` is removed in Next 16. The foundation replaces `.eslintrc.json` with `eslint.config.mjs` and adds the scripts `typecheck: tsc --noEmit` and `lint: eslint .`.

---

## G. Worker and deployment

**Worker** (`python -m clefs.worker [--once] [--no-services]`):
1. Checks migrations.
2. Every 15 seconds, runs `jobs.tick()` under `pg_try_advisory_lock('clefs_scheduler')`, so only one worker schedules.
3. Runs the queue consumer with SKIP LOCKED plus `LISTEN clefs_jobs` for immediate wakeups.
4. Starts every module's `worker_services`. Concierge registers the SIP voice service there. The incoming-call webhook (signed with `VOICE_WEBHOOK_SECRET`) lands on the `/webhooks/voice/incoming` route, which accepts the call and then notifies `clefs_voice`. The worker then opens the call's Gemini Live API session (google-genai `client.aio.live.connect`).
5. Writes a row to `worker_heartbeats` each loop. `/internal/status` reports the last tick, queue depth and outbox failures.

**Procfile** (one image, `python:3.12-slim`):

```
web: uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2 --proxy-headers
worker: python -m clefs.worker
release: python -m clefs.migrate up
```

`docker-compose.yml` runs postgres:16, api, worker and caddy. Code must stick to **Python 3.12 syntax as the floor**: the local venv is 3.14, but `.python-version` pins Vercel to 3.12 and the `Dockerfile` uses the same minor, so both deploy targets run the floor that CI checks.

**Staying compatible with Vercel.** `main.py:app` is unchanged. Vercel bundles the directory (it supports `excludeFiles`), so add `"excludeFiles": "{tests/**}"`. `/internal/cron/tick` requires a Bearer `CRON_SECRET`. On Vercel, set `EVENTS_BACKEND=local` and SSE degrades to polling. The SIP worker cannot run on Vercel.

**Recommended topology for the UAE pilot month.** Neon has no UAE region, and guest PII would otherwise leave the country.
- AWS **me-central-1**: one EC2 instance (t4g.medium) with Docker Compose running Caddy (TLS for `api.clefs.world`), the api (2 uvicorn workers) and the worker.
- **RDS Postgres 16** in me-central-1, with 7-day point-in-time recovery and a nightly `pg_dump` to S3 in the same region. Azure UAE North is the alternative.
- The Next client stays on Vercel. It stores no PII at rest; it only proxies traffic.
- The India stack (Vercel + Neon) keeps running from the same codebase.
- Disclose that Google (Gemini API) and Meta process data outside the UAE. Do not send guest names to LLMs unless a feature needs them.
- In-memory rate limits are replaced by the DB-backed `ratelimit`. SSE works across processes through NOTIFY.

**Environment variables.** `settings.py` pre-declares all of these, and `env(name, default)` covers anything unforeseen:

| Group | Variables |
|---|---|
| Core | `ENV`, `SECRET_KEY`, `WEB_ORIGINS`, `PUBLIC_API_ORIGIN`, `CORS_ALLOWED_ORIGINS`, `CORS_ALLOW_ORIGIN_REGEX`, `COOKIE_SECURE`, `SESSION_TTL_DAYS`, `LOG_LEVEL`, `CLEFS_DISABLED_MODULES` |
| Database | `DATABASE_URL` (legacy fallbacks `PSQL_DATABASE_URL`, `DB_*`), `DATABASE_URL_UNPOOLED`, `DB_POOL_MAX`, `MIGRATE_ON_STARTUP` |
| Runtime | `EVENTS_BACKEND`, `CRON_SECRET`, `VOICE_ENABLED` |
| AI | `GEMINI_API_KEY` (or `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`), `AI_ENABLED`, `AI_MODEL_DEFAULT`, `AI_MODEL_FAST`, `AI_MODEL_<TASK>`, `AI_LOG_CONTENT`, `REALTIME_MODEL`, `VOICE_WEBHOOK_SECRET` |
| WhatsApp | `WA_ACCESS_TOKEN`, `WA_APP_SECRET`, `WA_VERIFY_TOKEN`, `WA_PLATFORM_PHONE_NUMBER_ID`, `WA_BUSINESS_ACCOUNT_ID`, `WA_API_VERSION` |
| Notifications | `NOTIFY_MODE`, `EMAIL_PROVIDER`, `POSTMARK_SERVER_TOKEN`, `EMAIL_FROM`, `INBOUND_EMAIL_DOMAIN`, `POSTMARK_INBOUND_BASIC_AUTH`, `SMS_PROVIDER`, `SMS_ENABLED`, `SMS_AUTH_KEY`, `SMS_TEMPLATE_ID`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` |
| Files | `FILE_STORE`, `FILE_MAX_BYTES`, `S3_BUCKET`, `S3_REGION` |
| Demand data | `PREDICTHQ_TOKEN`, `RATE_SHOP_PROVIDER`, `RATE_SHOP_API_KEY` |
| Client | `CLEFS_API_ORIGIN` (server-only), `NEXT_PUBLIC_API_URL`, `GEMINI_API_KEY` |

**requirements.txt, pre-declared once so module agents never edit it:** fastapi, uvicorn[standard], psycopg2-binary, python-dotenv, google-genai, httpx, python-multipart (required for `UploadFile`; not currently installed), tzdata, openpyxl, pypdf, icalendar.

**requirements-dev.txt:** pytest, pytest-timeout.

---

## H. Test harness

- `pyproject.toml`: `[tool.pytest.ini_options] testpaths=["tests"]`, with markers `commits` and `slow`.
- **One database per session.** `tests/conftest.py` connects to the maintenance database, runs `CREATE DATABASE clefs_t_<pid>`, runs `migrate.up()` (about 2 seconds) and drops the database at exit. Eight agents in separate worktrees therefore never collide. The base URL comes from `TEST_DATABASE_URL`, defaulting to `postgresql://localhost/postgres`.
- **Per-test isolation.** One connection opens a BEGIN, and `db._test_override(conn)` makes every `db()` call use `SAVEPOINT`/`RELEASE`. The test rolls back at teardown. TestClient handlers share the connection sequentially.
- Tests marked `@pytest.mark.commits` (NOTIFY, advisory locks) use real commits and then TRUNCATE non-migration tables.
- **Fixtures:** `cur`, `hotel_uae` (AED, Asia/Dubai, rooms with floors), `make_user(role, hotel)`, `client`, `client_as(role)`, `device_client(floors, departments)`, `fake_ai` (records calls, returns queued outputs), `outbox` (`NOTIFY_MODE=capture`), `clock` (freeze and advance), `run_jobs()`.
- **Golden tests are written first** (`tests/foundation/test_legacy_contract.py`). They capture status codes and JSON key sets for every legacy endpoint against the old `main.py`, then must pass unchanged after the move.
- **Contract test** (`tests/foundation/test_module_contracts.py`). For every installed module it checks:
  - migrations fall inside the module's range
  - `CREATE TABLE` names use the module's prefix
  - job, metric, action-type, permission and signal-kind keys are prefixed `<module>.`
  - routes live under the module's prefixes
  - no duplicate method and path pairs exist
  - every metric compiles and passes `EXPLAIN`
  - no module imports another module's internals (only `api.py`)
- **Client checks:** `npm run typecheck`, `npm run lint`, `npm run build`, each run per worktree.

---

## I. File-ownership map and sequencing

**Wave 0 (foundation).** Two agents run in parallel on disjoint trees and must land before Wave 1.
- **F-server** owns `server/main.py`, `server/clefs/{app,registry,settings,db,migrate,cli,audit,events,jobs,ai,files,ratelimit}.py`, `server/clefs/{auth,notify,core,worker,seed,legacy,migrations}/**`, the three `legacy.py` moves, every module stub (`__init__`, `module.py`, `router.py`, `api.py`, `README.md`, `migrations/.gitkeep`), `tests/conftest.py`, `tests/foundation/**`, the empty `tests/modules/<m>/`, requirements, pyproject, Docker files, Procfile, `vercel.json`, `.env.example` and `CONTRACTS.md`.
- **F-server works in this order:** golden tests, db and migrate with `0001`, the legacy move, auth, contracts, stubs, contract tests.
- **F-client** owns `next.config.mjs`, `proxy.ts`, `package.json`, `eslint.config.mjs`, `app/lib/{api,format,session,hotel,i18n,types}.ts(x)`, `app/ui/**`, the `export`s in `app/app/charts.tsx`, `app/login/**`, `app/auth/**`, the `(console)` `layout`/`ConsoleShell`/`nav.ts`/`page`/`settings/**`, a stub `page.tsx` per module route (an EmptyState, so nav links resolve), the `StaffDashboard.tsx` and staff-page two-line edits, the `getRoomData` change in `guestData.ts`, and a `RequestTracker.tsx` stub that returns null.

**`nav.ts` is pre-declared** with every entry: brief, service, concierge, profit, demand, amenities, menu, reviews, guests, ask, trust, settings. Each has a label, `perm` and `module`.

**Wave 1 (eight agents, each in its own git worktree and branch).** Every lane also owns `tests/modules/<m>/**`.

| Agent | Server (exclusive) | Migrations / prefix | Client (exclusive) |
|---|---|---|---|
| A Service | `modules/service/**` | 0100-0199 `svc_` | `(console)/service/**`, `(kiosk)/**`, `app/device/**`, `app/app/StaffDashboard.tsx`, `app/app/[hotelSlug]/staff/**`, `app/stay/requests/**` |
| B Concierge + Voice | `modules/concierge/**` (voice/, WhatsApp guest chat) | 0200-0299 `cc_` | `app/ClefsConcierge.tsx` (viseme code untouched), `app/stay/**` except `requests/` and `FoodMenu.tsx`, `app/stay/i18n/**`, `app/api/realtime/route.ts`, `app/lib/{assistant,guestData}.ts`, `(console)/concierge/**` |
| C Profit Guard | `modules/profit_guard/**` | 0300-0399 `pfg_` | `(console)/profit/**`, `app/free-ota-audit/**` |
| D Demand Radar | `modules/demand/**` | 0400-0499 `dr_` | `(console)/demand/**` |
| E Amenities + Upsell | `modules/amenities/**`, `modules/upsell/**` | 0500-0599 `amn_`/`ups_` | `(console)/amenities/**`, `(console)/menu/**`, `app/stay/FoodMenu.tsx` |
| F Reviews + Guests | `modules/reviews/**`, `modules/guests/**` | 0600-0699 `rev_`/`gst_` | `(console)/reviews/**`, `(console)/guests/**`, `app/rebook/**` |
| G Brief + Ask | `modules/brief/**`, `modules/ask/**` | 0700-0799 `brf_`/`ask_` | `(console)/brief/**`, `(console)/ask/**` |
| H Trust + Marketing | `modules/trust/**` | 0800-0899 `trs_` | `(console)/trust/**`, `app/page.tsx`, `app/MarketingHomepage.tsx`, `app/components/**`, `app/lib/{seo,faq}.ts`, `app/sitemap.ts`, `app/robots.ts`, all marketing route dirs, `app/security/**` |

**Frozen shared files** (no module edits):
- Server: `main.py`, `app.py`, `registry.py`, `settings.py`, requirements files, `pyproject.toml`, `vercel.json`, `tests/conftest.py`.
- Client: `next.config.mjs`, `proxy.ts`, `package.json`, `tsconfig.json`, `app/layout.tsx`, `app/globals.css`, `app/lib/{api,format,session,hotel,i18n,types}`, `app/ui/**`, `app/app/charts.tsx`, `nav.ts`.

A module that needs something from a frozen file works around it locally and leaves a `# FOUNDATION-REQUEST:` comment in its own code. The integrator applies these in Wave 2.

**Wave 2 (integrator).**
- Merge all branches. They should be conflict-free by construction.
- Run `migrate verify`, the full pytest suite including the contract tests, `typecheck`, `lint` and `next build`.
- Turn off `legacy_open_access` for any hotel moved onto devices.

### Critical Files for Implementation
- /Users/nikhilagarwal/MyCodes/Clefs/server/main.py (source of the legacy routes to move; becomes the 3-line entrypoint)
- /Users/nikhilagarwal/MyCodes/Clefs/client/app/app/StaffDashboard.tsx (mounted as the Service module; `API_BASE` at line 743 and `EventSource` at line 1632 are the only foundation edits)
- /Users/nikhilagarwal/MyCodes/Clefs/client/app/lib/guestData.ts (its `getRoomData` downloads the whole guest roster; must switch to the per-room public endpoint)
- /Users/nikhilagarwal/MyCodes/Clefs/client/next.config.mjs (add the `/api/clefs` rewrite and keep the permanent `/aria` redirect)
- /Users/nikhilagarwal/MyCodes/Clefs/client/app/app/charts.tsx (export the private helpers so the new chart file can build on them)
