# Clefs server contracts

The frozen interfaces every lane builds on. `docs/specs/00-CANON.md` (including the amendments K1-K5 adopted 2026-09-28) wins any conflict; `docs/specs/01-foundation.md` is the design this implements and `docs/specs/04-growth-modules.md` sections 0.4 (F1-F10), 1 and 7 the growth extensions. Part 1 is owned by F-server-core. Part 2 is written by the contracts lane (F-contracts) and its wave 0b extension lane. Part 3 covers deploy, packaging and the client mirrors of these contracts, and ends with the ledger of foundation requests.

---

# Part 1: platform core (F-server-core)

## 1. Rules for module lanes

- **Your directory only.** A lane edits `server/clefs/modules/<m>/**` and `server/tests/modules/<m>/**`. The foundation created the stub (`__init__.py`, `module.py`, `router.py`, `api.py`, `README.md`, `migrations/.gitkeep`).
- **Frozen shared files** (no lane edits them): `server/main.py`, `clefs/app.py`, `clefs/registry.py`, `clefs/settings.py`, `requirements*.txt`, `pytest.ini` (pytest config; no `pyproject.toml`, see DEPLOY.md section 3), `vercel.json`, `tests/conftest.py`. Need a change? Work around it locally and leave `# FOUNDATION-REQUEST: <what and why>` in your own code; the integrator applies these.
- **Imports.** A module imports foundation code (`clefs.*` outside `clefs.modules`) and other modules' `api.py` only (`from clefs.modules.guests import api as guests_api`). Never another module's internals. Foundation code never imports modules (`app.py` loads them dynamically). Enforced by `tests/foundation/test_module_contracts.py`.
- **Tenant key** is `hotel_slug TEXT REFERENCES hotels(slug)`. Foreign keys may point only at core tables.
- **Money** is `BIGINT` minor units + `CHAR(3)` currency in storage, Decimal strings in APIs; rates `NUMERIC(18,8)` (Canon 2).
- **Time**: legacy tables keep epoch-ms `BIGINT`; new tables use `TIMESTAMPTZ`; every day bucket goes through `clefs.core.time` in the hotel's timezone (Canon 3). "Now" is always `clefs.core.clock.now()`.
- **Conventions**: `# ─── Section ───` banners, comments that explain why, no em dash characters, Python 3.12 syntax floor.

## 2. Registry (`clefs/registry.py`)

| Module | Migrations | Table prefix | Console route segment(s) |
|---|---|---|---|
| core (foundation 0001-0019, domain 0020-0099) | 0001-0099 | none | n/a |
| service | 0100-0199 | `svc_` (+ `staff_requests`, `staff_members`, `staff_shifts`, `pending_requests`) | `service` |
| concierge | 0200-0299 | `cc_` (+ `conversation_logs`, `hotel_locations`) | `concierge` |
| profit_guard | 0300-0399 | `pfg_` | `profit`, `profit-guard` |
| demand | 0400-0499 | `dr_` | `demand` |
| amenities | 0500-0549 | `amn_` | `amenities` |
| upsell | 0550-0599 | `ups_` | `menu`, `upsell` |
| reviews | 0600-0649 | `rev_` | `reviews` |
| guests | 0650-0699 | `gst_` (+ `guest_rooms`) | `guests` |
| brief | 0700-0749 | `brf_` | `brief` |
| ask | 0750-0799 | `ask_` | `ask` |
| trust | 0800-0849 | `trs_` | `trust` |
| voice | 0850-0899 | `vc_` | `voice` |
| visibility | 0900-0949 | `vis_` | `visibility` |
| inquiries | 0950-0999 | `inq_` | `inquiries` |
| finance | 1000-1049 | `fin_` | `finance` |
| integrator hotfixes | 1900-1999 | none | n/a |

- `INSTALLED_MODULES` (order = router mount order) is the tuple above. `enabled_modules(modules=None)` honours `CLEFS_ONLY_MODULES` (tests of one module) and `CLEFS_DISABLED_MODULES` (prod kill switch).
- `route_prefixes(m)` = `/hotels/{slug}/<segment>` for each segment, plus the shared namespaces `/public/`, `/device/`, `/webhooks/`, `/internal/`. `LEGACY_ROUTE_PATHS[m]` lists the grandfathered pre-refactor paths.
- Manifest, exported as `MODULE` from `clefs/modules/<m>/module.py`:

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

- `clefs.app.create_app(modules: list[str] | None = None) -> FastAPI`. Mounts: `GET /`, `GET /healthz`, auth routes, core hotel routes, the SSE stream, then every module in `CORE_ROUTER_HOOKS` that exists and defines `router`, then each module's routers. `CORE_ROUTER_HOOKS` starts with `clefs.core.routes_contracts` and ends with `clefs.core.oauth` (the OAuth router is defined in that module, not in routes_contracts).
- `registry.KEY_PREFIXES` (also `signals.KEY_PREFIXES`) lists the short key prefixes each module may use besides its own name (Part 2, Keys). Keys only, never permissions.

## 3. Database (`clefs/db.py`)

```python
with db(readonly=False, statement_timeout_ms=None) as cur: ...   # RealDictCursor; commit/rollback; always returned
fetch_one(sql, params=None, *, cur=None) -> dict | None
fetch_all(sql, params=None, *, cur=None) -> list[dict]
execute(sql, params=None, *, cur=None) -> int                    # rowcount
Json = psycopg2.extras.Json                                      # wrap dicts for JSONB params
on_commit(cur, fn)                                               # run fn after that transaction commits; dropped on rollback
raw_connection(unpooled=False) -> connection                     # caller closes; unpooled for LISTEN/advisory locks
init_pool(); close_pool(); get_db()                              # get_db is the deprecated legacy shim
_test_override(conn); _test_clear()                              # tests only
```

- Pool: `ThreadedConnectionPool(0, DB_POOL_MAX)` behind a `BoundedSemaphore`; waits up to 10 s then 503 `db_busy`. Idle connections are pinged after 60 s.
- Do not nest `db()` blocks in request code (each pooled block is its own connection and transaction). Pass `cur` down.
- URL precedence: `DATABASE_URL` > `PSQL_DATABASE_URL` > `DB_*`. `DATABASE_URL_UNPOOLED` for migrations/LISTEN/the scheduler lock, but only when it pairs with the effective URL and was not read from `.env` while `DATABASE_URL` was exported; otherwise it is ignored with a startup warning (and a `[migrate]` note). The warning never prints the URLs.
- `settings.urls_pair(pooled, unpooled)` requires the same database name and the same locality. For remote URLs it also requires the same password and role; a Supabase pooler role `<role>.<ref>` pairs with `<role>`. Every Neon project calls its database `neondb`, so a stale exported production direct URL never pairs with a staging `DATABASE_URL`.

## 4. Migrations (`python -m clefs.migrate up|status|verify|new`)

- Files: `clefs/migrations/NNNN_core_<desc>.sql|.py` and `clefs/modules/<m>/migrations/NNNN_<m>_<desc>.sql|.py`. A `.py` file defines `def up(cur)`.
- The number must fall in the module's range; the runner applies **every unapplied version in ascending order** (no high-water mark), each file in its own transaction under `pg_advisory_xact_lock(hashtext('clefs_migrate'))` with a re-check of `schema_migrations` (safe through PgBouncer transaction pooling; the unpooled URL is still preferred). Checksum drift warns on `up` and fails `verify`.
- `new --module <m> "desc" [--py] [--number N]` creates the next free number.
- A file must not contain `BEGIN`/`COMMIT` or `CREATE INDEX CONCURRENTLY` (the runner wraps each file in a transaction). Prefer idempotent DDL (`IF NOT EXISTS`). Never edit an applied file: add a new one. The runner skips applied versions, so an edit never reaches a database that already ran the file, and `verify` reports its checksum drift (this is why Canon K1 ships in 0013, not in 0011).
- Core numbering: 0001-0009 F-server-core, 0010-0019 F-contracts, 0020-0099 core-domain (stays / ingest / tax).
- Shipped: `0001_core_legacy_baseline.sql` (the old `init_db` DDL verbatim, idempotent, a no-op on production), `0002_core_seed_legacy_hotels.py` (HOTELS_DATA upsert), `0003_core_hotel_settings.sql` (typed hotel columns, `legacy_open_access` TRUE for every pre-existing hotel, `hotel_module_settings`), `0004_core_auth.sql` (users, hotel_memberships, auth_sessions, devices, device_pairing_codes, device_sessions, device_pin_failures, magic_links, staff PIN columns), `0005_core_audit_ratelimit.sql` (audit_log + chain/append-only triggers, rate_limits), `0006_core_rooms_channels.sql` (room_types, hotel_rooms, channels), `0007_core_team_invites.sql` (`hotel_memberships.invited_at/invited_by/invite_name/accepted_at`: a pending invitation is an inactive membership with `invited_at` set and `accepted_at` NULL).

## 5. Settings (`clefs/settings.py`)

`get_settings() -> Settings` (cached), `reload_settings()`, `env(name, default)` for anything not modelled (e.g. `AI_MODEL_<TASK>`, `AI_PRICE_<MODEL>`), `ai_model_for(task)`. Read modelled values as `get_settings().<NAME>` (`env()` still works for them). Every modelled variable is listed in `server/.env.example` (comments on their own line: dotenv reads `KEY=  # note` as the value "# note"); `test_core.py::test_env_example_lists_every_setting` enforces it. `.env` is loaded unless `CLEFS_DOTENV=0`.

- **Fails closed.** `ENV` (trimmed: `" production "` is production) unset means `production` on a Vercel production deploy (`VERCEL_ENV`), `preview` on other Vercel deploys, `development` elsewhere.
  - `Settings.is_dev` is true for an explicit `ENV=development|dev|test|local`. An unset `ENV` counts as development only when all of these hold: not on Vercel, a local database, and not in a container or on a hosting platform (no `/.dockerenv` and none of `settings.HOSTED_MARKERS`: `DYNO`, `RENDER`, `RAILWAY_ENVIRONMENT`, `FLY_APP_NAME`, `K_SERVICE`, `KUBERNETES_SERVICE_HOST`, `ECS_CONTAINER_METADATA_URI_V4`, `AWS_LAMBDA_FUNCTION_NAME`, `WEBSITE_SITE_NAME`). Deployed images must set `ENV`.
  - `validate_for_startup()` (API and worker) refuses the public `DEV_SECRET_KEY` (or any key under 32 chars) unless `is_dev`, and refuses `COOKIE_SECURE=false` in production.
  - `COOKIE_SECURE` defaults to true for every process that is not `is_dev` (production, preview, staging, an unlabelled VM or container). An explicit `false` outside development is a startup warning; production still refuses to start.
  - `Settings.production_gaps() -> list[str]` names a missing `CONTACT_HMAC_PEPPER` / `PII_ENC_KEY`, a `PUBLIC_API_ORIGIN` that is still localhost or not https, and a missing `CRON_SECRET` (with no `WORKER_TOKEN` either) in production. `create_app` and the worker log these with the other startup warnings (not fatal).
  - Magic-link URLs are logged only when `is_dev`.
- **Fields added in wave 0b** (04 spec 7.6), defaults in brackets:
  - WhatsApp and privacy: `WA_MAGIC_LINK_TEMPLATE` (`clefs_magic_link`), `META_LEADGEN_VERIFY_TOKEN`, `WA_SERVICE_FREE_ALLOWANCE` (0), `CONTACT_HMAC_PEPPER`, `PII_ENC_KEY`, `OTA_ALIAS_DOMAINS` (list, lowercased; `guest.booking.com,m.expediapartnercentral.com`).
  - Google and OAuth: `GOOGLE_API_KEY`, `OAUTH_REDIRECT_BASE` (None means `PUBLIC_API_ORIGIN`).
  - SerpApi: `SERPAPI_MONTHLY_BUDGET_USD` (Decimal or None; None = no global ceiling), `PUBLIC_SERPAPI_MONTHLY_CAP` (int or None), `SERPAPI_COST_PER_SEARCH_USD` (Decimal, 0.015).
  - Visibility: `VISIBILITY_AI_ENABLED` (true, the kill switch), `PERPLEXITY_MODEL` (`sonar`), `PERPLEXITY_TERMS_APPROVED` (false, the Canon 20 gate), `PUBLIC_CHECK_DAILY_CAP` (20), `SNAPSHOT_DAILY_CAP` (10), `SNAPSHOT_MONTHLY_BUDGET_USD` (Decimal 50).
  - Finance and payments: `QBO_ENV` (`sandbox` | `production`; any other value fails at startup), `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `TELR_API_BASE` (`https://secure.telr.com`), `STRIPE_API_BASE` (`https://api.stripe.com`), `TAP_API_BASE` (`https://api.tap.company`).
  - A Decimal setting that does not parse fails at startup with `ValueError` naming the variable.
  - `AI_MODEL_VISIBILITY_GEMINI` stays under the `AI_MODEL_<TASK>` pattern (`ai_model_for`).
- **Client IP.** `TRUST_FORWARDED_FOR` (default: on Vercel only) reads `X-Real-IP`, then the first `X-Forwarded-For` entry (Vercel overwrites both), and uses a value only if it parses as an IP address; `[v6]:port` and `v4:port` are accepted and normalised. Otherwise, and everywhere else, the IP is `scope["client"]`, which uvicorn `--proxy-headers` resolves from trusted peers only (`FORWARDED_ALLOW_IPS`). Free text never becomes a rate-limit key or an audit IP.

## 6. Identity and access (`clefs/auth/`)

```python
@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "device", "service", "anonymous"]
    user_id: int | None; device_id: int | None; active_staff_ids: tuple[int, ...]
    is_clefs_admin: bool; session_id: int | None; scope: dict; session_kind: str | None  # web|magic|api
    label: str | None; email: str | None; via_cookie: bool
    device_hotel: str | None; device_floors: tuple[str, ...]; device_departments: tuple[str, ...]; user: dict | None

@dataclass(frozen=True)
class HotelCtx:
    principal: Principal; hotel: Hotel; role: str | None; permissions: frozenset[str]
    departments: tuple[str, ...]; floors: tuple[str, ...]; via_legacy: bool; membership_id: int | None
    def can(*perms) -> bool

current_principal(request) -> Principal                 # never raises; cached on request.state
resolve_ctx(principal, slug, *, cur=None) -> HotelCtx   # 404 if no hotel; no access = empty permissions
require_user                                             # Depends(require_user) -> Principal, 401
require_perm(*perms)                                     # user holding ANY perm at {slug}; 401 anonymous / 403
require_legacy_or(*perms)                                # same, but anyone passes while hotel.legacy_open_access
require_device(*, departments=None)                      # clefs_device cookie; device of {slug} if present
require_perm_or_device(*perms)                           # a user with perm, or any device of {slug}
require_service("cron" | "worker")                       # Bearer CRON_SECRET / WORKER_TOKEN (worker may call cron routes)
acting_staff(ctx, request) -> int | None                 # X-Clefs-Staff, must be on an open PIN shift on this device
```

- Hotel-scoped dependencies read the `slug` path parameter: every hotel route names it `slug`.
- **Roles**: `owner, gm, finance, revenue, dept_head, front_desk, staff`; pseudo-role `device`. `clefs_admin` is a user flag that passes every check (audited once per session/hotel/permission per 10 minutes as `auth.admin_bypass`).
- **Core permissions** (`permissions.CORE_PERMISSIONS`; they survive `permissions.reset()`): `core.settings.view` (owner, gm, finance, revenue, dept_head), `core.settings.manage` (owner, gm), `core.team.manage` (owner, gm), `core.signals.view` (owner, gm, finance, revenue, dept_head), `core.actions.decide` (owner, gm), `core.devices.manage` (owner, gm), `core.files.upload` (all roles and devices), `core.autonomy.view` (owner, gm, finance, revenue, dept_head), `core.autonomy.manage` (owner, gm), `core.autonomy.raise_auto` (owner), `core.metrics.view` and `core.files.view` (owner, gm, finance, revenue, dept_head), `core.stays.view` (owner, gm, finance, revenue, dept_head), `core.stays.manage` (owner, gm), `core.ingest.manage` (owner, gm, finance, revenue).
- **Module permissions** start with the module name or a route segment (`profit.view`, `menu.manage`). Defaults shipped in each `module.py`: every module has `<segment>.view` (the nav tab gate) and usually `<segment>.manage`; service also has `service.work` (all roles + device); trust has `trust.audit.view`.
- **Legacy open access**: anonymous visitors to a `legacy_open_access` hotel get `service.view/manage`, `concierge.view/manage`, `guests.view/manage` (what the pre-auth dashboard could do).
- **Magic sessions** (`kind='magic'`, 12 h, scope `{hotel, purposes: ["brief","actions"]}`) only hold permissions starting `brief.`, `core.signals.view` or `core.actions.`, and only at the scoped hotel (never `core.autonomy.*`, `core.metrics.view` or `core.files.view`). At any other hotel they have no role, membership or scope at all (every "any member" check denies), and a Clefs admin's magic session has no admin powers (`deps.has_admin_powers`). A link whose scope carries `action_ids` decides exactly those actions with the role's full permissions (Part 2, Actions: magic sessions).
- **Next paths.** `magic.safe_next_path()` parses: a `next_path` must start with one `/`, contain no backslash or control character (the URL parser strips tab/newline, so `/\t/evil.example` would leave the origin) and resolve to our own origin. The client's `safeNextPath` applies the same rule with `new URL()`.
- **Tokens** (`clefs.auth.tokens`): `new_token()`, `hash_token()` (sha256; only hashes are stored), `mint_ticket(purpose, claims, ttl_s=60)`, `verify_ticket(purpose, t) -> dict | None`, `sign_ref(ref)`, `verify_ref(signed) -> ref | None` (WhatsApp button ids), `qr_digest(message)`.
- **Magic links** (`clefs.auth.magic`): `create_link(cur, *, user_id, hotel_slug, purpose in {brief, login, action}, next_path=None, scope=None, ttl=24h, max_uses=3) -> token`, `link_path(token) -> "/auth/magic/{token}"`, `request_link(...)`, `deliver(user, hotel_slug, url, purpose)`, `register_sender(fn)`. Exchange is POST only, and `exchange()` re-validates the stored `next_path` with `safe_next_path` (fallback `/app/{slug}`), so a row written around `create_link` cannot redirect off-origin. Senders are `fn(user, hotel_slug, url, purpose) -> bool`; with none registered, non-production processes log the link. Brief passes `scope={"action_ids": [...]}` for the actions it surfaced.
- **Devices** (`clefs.auth.devices`): `create_pairing_code`, `pair`, `list_devices`, `revoke_device`, `roster`, `start_shift`, `end_shift`, `set_staff_pin`, `active_staff_ids`. A PIN shift sets `staff_members.is_online` and writes `staff_shifts` so the legacy auto-assigner and analytics stay in step. 5 wrong PINs lock a staff member on that device for 5 minutes; 15 across devices lock them everywhere for 15 minutes.
- **CSRF**: a mutation carrying `clefs_session`/`clefs_device` must have an `Origin` in `WEB_ORIGINS` (or matching `CORS_ALLOW_ORIGIN_REGEX`, or `PUBLIC_API_ORIGIN`); `Sec-Fetch-Site: cross-site` is refused; requests with neither header are non-browser clients and pass. CORS is an allowlist with `allow_credentials=False`.

## 7. Hotels, rooms, time

```python
# clefs/core/hotels.py
class Hotel(BaseModel): id; slug; name; star_rating; rooms_count; category; website; city; knowledge_base;
    assistant_name; created_at; country; emirate; timezone; currency; locale; guest_languages; weekend_days;
    lat; lng; address; business_day_cutoff; brief_time_local; tax_profile; whatsapp_phone_number_id;
    whatsapp_display_number; front_desk_phone; inbound_email_alias; status; legacy_open_access; updated_at
    tz -> ZoneInfo; project(fields) -> dict
get_hotel(cur, slug, *, fresh=False) -> Hotel            # 60 s cache, 404 ClefsError
find_hotel(cur, slug, *, fresh=False) -> Hotel | None
create_hotel(cur, *, slug, name, country="AE", emirate="dubai", timezone="Asia/Dubai", currency="AED", ...) -> Hotel
    # writes no action_autonomy rows, and neither do cli create-hotel and seed-demo
    # (04 spec 1.4): every dial follows its type's default_level until first set
update_hotel(cur, slug, changes) -> (before, after)
get_module_settings(cur, slug, module, model) -> model   # defaults applied; invalid stored data -> defaults
set_module_settings(cur, slug, module, data, *, actor, enabled=None) -> dict   # audited
module_enabled(cur, slug, module) -> bool
LEGACY_PUBLIC_FIELDS, CONTEXT_FIELDS, SETTINGS_FIELDS, PUBLIC_STAY_FIELDS          # projections

# clefs/core/rooms.py
qr_key(slug, room, version=1)   # base32(HMAC_SHA256(SECRET_KEY, "slug:room:version"))[:10]
verify_qr_key(slug, room, version, key); stay_path(slug, room, version) -> "/stay/{slug}/{room}?k=..."
get_room, list_rooms, has_register, upsert_rooms, rotate_qr, floor_for, infer_floor ("502" -> "5")

# clefs/core/time.py (all take a Hotel)
hotel_now(h); local_date(h, ts=None); business_date(h, ts=None)   # rolls at business_day_cutoff (04:00)
local_day_bounds(h, d); business_day_bounds(h, d); local_range(h, start, end)
is_weekend(h, d)   # ISO weekday in hotels.weekend_days (UAE {6,7})
to_ms(dt); from_ms(ms); today_start_ms(h)

# clefs/core/phone.py
normalize_phone(phone) -> str | None     # E.164 or 400 invalid_phone

# clefs/core/clock.py
now(); now_ms(); freeze(dt); advance(delta); unfreeze(); frozen(dt)
```

## 8. Audit (`clefs/audit.py`)

`audit(cur, ctx_or_principal, action, entity_type, entity_id, *, before=None, after=None, meta=None, hotel_slug=None)`. Joins the caller's transaction. Credential-looking keys are masked. Actions are `<area>.<verb>`.

- Chain: per `hotel_slug` (NULL is its own chain). A BEFORE INSERT trigger serializes writers per chain, re-draws the id under the lock, and sets `prev_hash` = previous row's `row_hash`, `row_hash = audit_row_hash(prev_hash, row)` (SQL function in 0005; Trust's verifier must call it rather than re-implement it).
- Append-only: UPDATE/DELETE/TRUNCATE raise `insufficient_privilege` unless the transaction ran `SET LOCAL clefs.audit_maintenance = 'on'` (reserved for Trust's retention job).

## 9. Rate limits (`clefs/ratelimit.py`)

`hit(cur, key, *, limit, window_s) -> bool` (sliding window, rejected attempts are not recorded), `remaining(...)`, `reset(cur, key)`. Keys are free-form, conventionally `<area>:<scope>:<id>`.

## 10. Events (`clefs/events.py`)

`publish(hotel_slug, event, data, *, audience=None, cur=None)`. With `cur` the event is delivered only if that transaction commits. Names are `<module>.<event>`; the legacy names `requests_changed`, `new_clefs_request`, `new_aria_request` stay. Payloads over 7 KB are reduced to `{"id", "truncated": true}`. `audience` keys: `roles`, `departments`, `floors`, `device_ids`, `user_ids`; anonymous (legacy) subscribers receive only audience-less events. Backend: `EVENTS_BACKEND=notify` (LISTEN/NOTIFY, cross-process) or `local` (in-process, Vercel). Stream: `GET /hotels/{slug}/stream?t=<ticket>`; closes after 30 minutes (clients re-mint).

## 11. Errors

Raise `clefs.core.errors.ClefsError(status, detail, code)` -> `{"detail": str, "code": str}`. Helpers: `unauthorized`, `forbidden`, `not_found`, `conflict`, `bad_request`, `too_many`, `NotConfigured(feature)` (409 `not_configured`, raised by `api.py` stubs whose feature is not built). Legacy routes keep FastAPI's `HTTPException` so their bodies stay `{"detail": ...}` exactly.

## 12. SHARED HTTP CONTRACT (server and client implement exactly this)

The browser calls `/api/clefs/:path*`, rewritten by Next to `${CLEFS_API_ORIGIN}/:path*`. Cookies `clefs_session` and `clefs_device` are `HttpOnly; SameSite=Lax; Path=/`, `Secure` only when `COOKIE_SECURE=true`, no Domain. Errors are `{detail, code?}`; a 401 makes the client dispatch `clefs:unauthorized`.

| Method and path | Access | Response |
|---|---|---|
| `POST /auth/login {email, password}` | public, rate-limited (10 per 15 min per email; a loose per-IP cap); every failing path costs one scrypt | `{user:{id,name,email,is_clefs_admin,phone_e164,locale}, memberships:[{hotel_slug,hotel_name,role}], invites:[Invite]}` + cookie; 401 `invalid_credentials` |
| `POST /auth/logout` | any | 204, cookie cleared, session revoked |
| `GET /auth/me?hotel=<slug>` | session, or anonymous on a legacy hotel | `{user, memberships, hotel_slug, role, permissions[], legacy_mode, invites[]}`; 401 otherwise |
| `POST /auth/invites/{membership_id}/accept` | the invited user (any session kind) | `{hotel_slug, memberships, invites}`; 404 `invite_not_found`. `Invite = {membership_id, hotel_slug, hotel_name, role, invited_at, invited_by_name}` |
| `POST /auth/invites/{membership_id}/decline` | the invited user | 204 (the invitation is deleted) |
| `GET /hotels/{slug}/context` | member, device of the hotel, or legacy visitor | `{hotel:{slug,name,city,country,emirate,timezone,currency,locale,guest_languages,weekend_days,brief_time_local,star_rating,rooms_count,assistant_name,status}, modules_enabled[], role, permissions[], legacy_open_access, principal_kind}` |
| `POST /auth/stream-ticket {hotel_slug}` | member / device / legacy visitor | `{url:"/hotels/{slug}/stream?t=...", expires_in:60}` |
| `GET /hotels/{slug}/stream[?t=]` | ticket, or none on a legacy hotel | `text/event-stream` |
| `POST /files/upload-ticket {hotel_slug, purpose}` | member (not legacy visitors) | `{url:"/hotels/{slug}/files?t=...", expires_in:60}` |
| `POST /auth/magic/exchange {token}` | public (POST only) | `{next_path, user, hotel_slug}` + magic session cookie; 401 `magic_link_invalid` |
| `POST /auth/magic/request {email|phone, purpose="login", hotel_slug?, next_path?}` | public, rate-limited | 202 `{ok: true}` whether or not the account exists; the lookup, minting and delivery run as a background task after the response, so the timing does not tell either (on uvicorn; unverified on Vercel's adapter, see Part 3, requests) |
| `POST /hotels/{slug}/devices/pairing-codes {name, kind, floors[], departments[]}` | `core.devices.manage` or `service.manage` | `{code, expires_at, pair_url}` (15 min, single use) |
| `POST /auth/devices/pair {code}` | public, rate-limited | `{device:{id,name,kind,floors,departments}, hotel_slug}` + `clefs_device` cookie |
| `GET /hotels/{slug}/devices` | same as pairing | `[{id,name,kind,floors,departments,paired_at,last_seen_at,revoked_at}]` |
| `DELETE /hotels/{slug}/devices/{id}` | same | 204; open shifts end with `device_revoked` |
| `POST /auth/devices/shift/start {staff_member_id, pin}` | device | `{session_id, staff:{id,name,department}, planned_end_at}`; 401 `invalid_pin`, 423 `pin_locked`, 409 `pin_not_set`, 404 `staff_not_found` |
| `POST /auth/devices/shift/end {session_id, handover_note?}` | device | `{ok, session_id, ended_at}` |
| `GET /auth/devices/roster` | device | `[{id,name,department,role,has_pin,on_shift}]` scoped to the device's departments/floors |
| `PUT /hotels/{slug}/staff/{staff_id}/pin {pin|null}` | `core.devices.manage` or `service.manage` | `{ok, has_pin}` |
| `GET /hotels/{slug}/team` | `core.team.manage` | `[{id (membership), user_id, name, email, phone, role, departments, floors, is_active, last_login_at, status: active|inactive|invited, invited_at}]`; an invited row shows the name the inviter typed and null `user_id`/`phone`/`last_login_at` |
| `POST /hotels/{slug}/team {email, name, phone?, role, departments[], floors[], password?}` | `core.team.manage` (owners only grant owner) | 201 row. A new email creates the account (`status: active`). An email that already has an account gets an invitation (`status: invited`, only the caller's own values echoed; the account's name, phone and password are never changed) unless the caller is a Clefs admin or the person was an accepted member here before. 409 `already_member`. Only a Clefs admin may send `password` (403 `password_not_allowed` otherwise): accounts are global, so a manager-chosen password would let them sign in as that person at any hotel that later invites them |
| `PATCH /hotels/{slug}/team/{membership_id} {role?, departments?, floors?, is_active?, name?, phone?}` | `core.team.manage` | row; 409 `last_owner`; 409 `profile_shared` when a changed name/phone belongs to an account with a membership or invitation at another hotel (only Clefs admins and the person may edit it); 409 `invite_pending` for `is_active: true` or a phone on an invitation |
| `DELETE /hotels/{slug}/team/{membership_id}` | `core.team.manage` | 204 |
| `GET /hotels/{slug}/settings` / `PATCH` (core columns) | `core.settings.view` / `core.settings.manage` | settings view (`SETTINGS_FIELDS`); only Clefs admins may re-enable `legacy_open_access` or set a new `whatsapp_phone_number_id` (403 `forbidden`; clearing it or resending the current value is allowed). The id is unique across hotels (0016) and never the platform number: 409 `whatsapp_number_taken`; a taken `inbound_email_alias` is 409 `alias_taken` |
| `GET/PUT /hotels/{slug}/module-settings/{module} {enabled?, settings}` | `core.settings.view` / `core.settings.manage` | `{module, enabled, settings, schema, updated_at}`; invalid settings -> 422 FastAPI-style `detail[]` with `loc` under `body.settings` |
| `GET /hotels/{slug}/rooms`, `PUT {rooms:[...]}`, `GET /rooms/qr`, `POST /rooms/{room}/rotate-qr` | settings view / manage | room register and printable stay paths. `PUT` is a partial upsert: an item without `floor` keeps the stored floor (a new room gets the inferred one); `floor: ""` asks for the inferred floor again. An unknown `room_type_code` creates that room type (named by its code); a foreign-key failure is 400 `invalid_room_type`, never a 500 |
| `GET /hotels/{slug}/room-types`, `PUT {room_types:[{code, name?, bedrooms?, max_occupancy?, room_count?, is_active?}]}` | settings view (or `service.view`) / manage | `[{code, name, bedrooms, max_occupancy, room_count, is_active}]`; `PUT` is a partial upsert by code, never deletes (`is_active: false` retires a type) |
| `GET /public/stay/{slug}/{room}?k=` | public, 60/10 min per IP and room | `{hotel:{slug,name,city,star_rating,category,assistant_name,locale,guest_languages}, room_number, floor, personalized, key_status: valid|invalid|absent, room: GuestRoom-subset|null}` (`room` = `guests.api.guest_context_for_room`, read by the client's `getRoomData`) |
| `GET /hotels/{slug}/signals`, `GET/POST /hotels/{slug}/actions`, `POST /hotels/{slug}/actions/{id}/decide`, `/hotels/{slug}/autonomy*` | see Part 2 | contracts lane |
| `/hotels/{slug}/oauth*`, `GET /public/oauth/{provider}/callback` | see Part 2, OAuth | `clefs.core.oauth` |

`clefs.auth.magic.peek(cur, token) -> {id, user_id, hotel_slug, purpose, next_path, scope, expires_at, uses, max_uses}` (Wave 2) validates a link like the exchange (not expired, user active, member unless a Clefs admin) without using it up; 401 `magic_link_invalid`. For pages re-read from a link after its one exchange.

**Legacy endpoints** keep their exact paths and JSON shapes (golden-tested in `tests/foundation/test_legacy_contract.py`): `GET /hotels/{slug}` (public; legacy columns only), `/hotels/{slug}/requests*`, `/pending`, `/staff*`, `/staff-analytics` (service), `/conversation-logs`, `/locations*` (concierge), `/guests*` (guests), `/hotels/{slug}/stream`. Access: `POST /pending` and `POST /conversation-logs` and `GET /locations*` are public and DB-rate-limited; everything else needs a member/device permission, or the hotel's `legacy_open_access`. The body chooses the room, so both public writes also have a per-hotel and a per-IP ceiling, and their free text is truncated (pending description 2000, type/category/subtype 100; log message 8000, room/session 128). `POST /pending` checks `room` with `rooms.valid_room` (400) and, when the hotel has a room register, against it (404; a room is then required). Staff SMS for auto-assigned requests is capped per assignee (20 per hour). `POST /conversation-logs` 404s an unknown hotel.

## 13. Frozen module APIs (`clefs/modules/<m>/api.py`)

Signatures are locked by `test_frozen_api_signatures` (`FROZEN_API`); lanes implement the bodies. Existing stubs are extended, never broken (04 spec 7.2, F10).

| Function | Foundation behaviour |
|---|---|
| `service.create_request(cur, hotel_slug, *, room, type, description, source, subtype=None, priority_hint=None, language=None, guest_ref=None) -> dict` | the v2 intake pipeline: semantic merge, per-room rate limit for guest sources, translation, safety, priority (heat, VIP, L1), department and SLA, routing, legacy events, audit. `type` is a request-type key or a legacy type; `priority_hint` is high/medium/low or a severity `L1`/`L2`/`L3`. Returns the full `staff_requests` row plus `tracking_token`, `merged` and `replayed`; raises `ClefsError` 429 `rate_limited` for a guest source over its room limit. A complaint also opens a LEARN recovery case |
| `service.escalate_to_human(cur, hotel_slug, *, room, reason, channel, sensitive, excerpt) -> dict` | high-priority "Direct Conversation" request; sensitive excerpts are not copied |
| `service.request_status_for_guest(cur, hotel_slug, tracking_token) -> dict | None` | status by signed tracking token, no staff names |
| `concierge.build_session(hotel_slug, *, channel, room=None, location=None, language=None) -> SessionSpec` | instructions from hotel card + KB, model from settings, no tools |
| `concierge.respond_text(spec, messages) -> TextTurn` | hands off to a person (`implemented=False`) |
| `concierge.kb_facts(cur, hotel_slug, *, language='en', keyed_only=False) -> list[dict]` | built: approved KB entries in section order `[{source, text, fact_key, value, entry_id, approved_at}]` (in `kb_mode` legacy_text the old free text comes first); `keyed_only=True` gives the approved structured facts |
| `upsell.get_menu(cur, hotel_slug, *, outlet=None, language="en") -> list[dict]` | `[]` |
| `upsell.quote(cur, hotel_slug, *, room, lines) -> dict` / `upsell.create_order(cur, hotel_slug, *, room, lines, source) -> dict` | raise `NotConfigured` |
| `guests.guest_context_for_room(cur, hotel_slug, room) -> dict | None` | allowlisted fields of `guest_rooms` (names and types, stay type, dates, tags, language, tier, occasions) |
| `guests.marketing_audience(cur, hotel_slug, *, purpose="marketing", channel="whatsapp", segment=None) -> list[dict]` | built: consented direct contacts `[{contact_ref, channel, locale}]`; `segment` narrows by `language`, `returning`, `min_stays`, `last_stay_before/after` and `countries` (ISO2: the reservation's `guest_country`, else the phone's calling code; unknown country never matches) |
| `demand.price_guardrails(cur, hotel_slug, stay_date, room_type_code=None)`, `demand.outdoor_temp_c(hotel, at)`, `demand.comp_rates(cur, hotel_slug, stay_date)` | `None`, `None`, `[]` |
| `amenities.list_items(cur, hotel_slug, *, language="en", active_only=True)`, `resolve_item(cur, hotel_slug, amenity_item_id)`, `link_request(cur, hotel_slug, *, request_id, amenity_item_id, qty)` | built: `list_items`/`resolve_item` return the full catalog row (including `name_en`, `name_ar`, `request_type_key`; `resolve_item` None for an unknown or other hotel's id); `link_request` writes a TOOL-matched consumption line and ignores invalid input (unknown item, another hotel's request, qty <= 0) |
| `profit_guard.record_ota_leak(cur, hotel_slug, *, source, inquiry_ref, reservation_ref, channel_code, evidence, commission_minor=None, currency=None)`, `channel_economics(cur, hotel_slug, *, start, end)` | `None`, `[]` |
| `reviews.draft_reply(cur, hotel_slug, review_id)` | `None` |
| `visibility.ai_snapshot(cur, hotel_slug, *, hotel_name, city, max_queries=3)` | `None` |
| `brief.push_now(cur, hotel_slug, *, cadence="daily", user_id=None)` | `{ok: False, reason}` |
| `trust.readiness(cur, hotel_slug)` | built: `{ready, overridden, checks: [{key, label, ok, detail}], run_id, prompt_version, kb_fingerprint, evaluated_at, override}` |
| `voice.call_stats(cur, hotel_slug, *, start, end) -> dict` | `{calls, accepted, rejected, transferred, requests_created, emergencies, spoken_room, avg_duration_s}` for calls started in `[start, end)`; zeros without lines |
| `ask.answer_question(cur, hotel_slug, *, user_id, question, channel='WEB') -> dict`*, `ask.askable_metrics(cur, hotel_slug, *, user_id) -> list[dict]`* | built in Wave 2: the one answering door (web and the WhatsApp text handler) returning an Answer; the metric catalog the user may ask about (their permissions) |

**Stubs added in wave 0b (F10).** Each returns the value shown until its lane builds it.

- **guests** (built in Wave 1; the lines below are the empty-hotel answers)
  - `record_own_contact`: 422 `ConsentNotAllowed` without transfer consent; a registration-card capture then needs its evidence (422 `evidence_required` without a `card_crop` or `staff_attestation`).
  - `is_known_guest_sender`: False. `can_send_marketing`: `(False, 'no_consent')` for an unknown identity.
  - `record_external_send`, `opt_out`: no-ops. `journey_timeline`, `arrival_prefs`: `[]`.
- **inquiries** (built in Wave 2): `ContactIn`. `create_inquiry` returns `{id, ref, stage, outcome}` (`outcome` is an extra key) and raises `NotConfigured` only for a hotel that switched Inquiry Guard off. `brief_summary`: the day's block, `{}` on an empty hotel. `verified_outcomes`: `[]` on an empty hotel.
- **visibility**: `SnapshotSubject`, `AiSnapshot`. `start_ai_snapshot`, `get_ai_snapshot`, `parity_status`, `attribution`, `gbp_location`: None. `purge_ai_snapshot`: 0. `open_fact_errors`: `[]`.
- **finance**: `weekly_pulse`, `monthly_summary`: None. `verified_profit` sums the ledger: `{start, end, currency, verified_minor, verified_value, actions, by_module}`.
- **demand**
  - `expected_adr`, `find_event`, `demand_index`: None.
  - `comp_set`, `upcoming_events`: `[]`. `comp_rates_snapshot`: `{}`.
  - `area_queries`: `["hotels in <city>"]`.
- **profit_guard**
  - `DistNight`, `CxlCost`.
  - `expected_commission`, `reference_ota`: None. Cost lists (`distribution_cost_by_night`, `cancellation_cost_by_date`): `[]`.
  - `distribution_cost_for_reservation`, `record_rate_evidence`: 0.
  - `parity_tolerance_pct`: `Decimal("1.0")`. `treat_sc_as_revenue`: True.
- **service**
  - `SOURCES` gains the journey and inquiry sources. New `REQUEST_TYPE_FOR_DEPARTMENT`.
  - `create_followup` files a `core.task`.
  - `open_items_for_stay` (built): `[{id, request_type, severity L1|L2|L3, status, created_at}]` still open for the stay in `[since, until)`; `reservation_id` widens it to the reservation's room and requests filed with `guest_ref=str(reservation_id)`, and its `checked_out_at` bounds the window. `has_open_complaint` (an open recovery case for the room since `since`; a `reservation_id` reads the reservation's room), `recovery_cost` (gestures given, minor units) and `on_duty` (roster shift roles, else active memberships) are built: False, 0 and `[]` on an empty hotel.
- **concierge** (built): `propose_kb_entry` queues a pending entry and returns its id; `kb_entry_status` -> `{entry_id, status, approved_at}` or None for another hotel's id.
- **reviews**: `review_counts`: `{}`. `reply_stats`: None.
- **brief**: `wa_template_info`: None. `submit_template` raises `NotConnected`.

**Added by the Wave 1 preflight** (also frozen in `FROZEN_API` / `FROZEN_API_NAMES`):

- **service**
  - `SAFETY_LEVELS = ("emergency", "sensitive", "handoff", "none")`, most severe first. Combining two verdicts keeps the more severe one (never downgrade, 03 spec 0.5).
  - `classify_safety(text, *, language=None, text_en=None, hotel_slug=None, use_llm=True, cur=None) -> {level, kind, source: lexicon|llm|None, confidence}`. The one door to Service's `safety.py` for the concierge turn log, the voice sideband worker and journey replies. The stub returns `{"level": "none", "kind": None, "source": None, "confidence": 0.0}`. Callers then rely on the prompt's own escalation rules, and read `none` as "the server adds no escalation", never as leave to drop one the model or a person raised.
- **trust**
  - `mask(obj, role, *, field_classes, departments=(), subject_department=None, department_field="department")`. Works now: it delegates to `clefs.core.pii.mask` (Part 2, PII masking). Trust may wrap the body (for example per-hotel policy or an audit of unmasked reads) without any caller moving.

**Added in Wave 1** (frozen in `FROZEN_API` where marked *):

- **concierge**
  - `build_surface_session(hotel_slug, *, surface, room=None, location=None, language=None, verified=False, guest=None, dry_run=False, cur=None) -> SessionSpec`* (03 spec 2.1; surface web_room, web_location, phone, text_fallback, eval, whatsapp). `guest` personalises only a verified session.
  - `open_session(cur, spec, *, key_status='extension', call_ref=None, transport='sip', consents=None, with_token=False) -> {session_id, expires_at, prompt_version, session_token?}`*: voice binds a call (`surface='phone'`, `verified=True`), Trust an eval run (`surface='eval'`, `dry_run=True`, `key_status='eval'`, `transport='none'`).
  - `SessionSpec` gains `prompt_version`, `surface`, `verified`, `tool_choice`, `greeting_text`, `kb_mode`, `dry_run`, `kb_entry_ids`.
- **guests**
  - `returning_badge(cur, hotel_slug, *, room, role, departments=(), at=None)`*, `returning_arrivals(cur, hotel_slug, *, day, role, departments=())`* (masked by role), `forget_contact(cur, hotel_slug, *, kind, value, source, actor=None)`* (DSR erase; the STOP suppression stays), `unsubscribe_path(hotel_slug, *, identity_hmac, channel)`* (the one-click link every marketing email carries).
  - `guest_context_for_room` may add `returningGuest: {stayNumber}`.
  - `room_for_sender(cur, hotel_slug, *, sender_hmac=None, phone_hmac=None) -> str | None` (concierge WhatsApp chat; `phone_hmac` is an alias). It returns the room of the one stay the sender's per-hotel contact HMAC is on right now, read from core `reservation_contacts` and the guests snapshot. Only IN_HOUSE stays, or roster stays covering today, count; a CONFIRMED arrival never does. A stay overtaken by a later in-house stay in the same room is ignored. None for an unknown sender or for two or more rooms (the guest then types a room, unverified).
- **trust**: `is_restricted(cur, hotel_slug, subject_ref, *, scope='marketing') -> bool`* (a DSR restriction, objection or erasure for marketing, ai_analytics or profile_memory; `subject_ref` is `privacy.contact_ref` hex).
- **voice**: `call_stats(...)`* (table above).
- **service**: `classify_safety` is built (lexicons in 8 languages, then an LLM that may only upgrade; never raises).
- **service** (Wave 1 gaps, built): `create_request(..., idempotency_key=None)` replays exactly for tool executors (same key, same row); `on_request_state(fn)`* lets Upsell move an order with its linked request; devices get `POST|DELETE /device/service/push-subscriptions` and `POST /device/service/sos/{sos_ref}/cancel`; signal kind `service.l1_or_ack_breach` (the ACK ladder reached the GM; section service_risk, daily).
- **concierge**: `run_tool(cur, hotel_slug, *, session_ctx, name, args) -> dict`* is the one tool executor door (voice MCP, sideband, evals): `session_ctx` is `{session_id, call_id?}`, or `{spec}` for a dry run; it never raises, refusals come back as speakable statuses. `open_session(transport='sip')` raises 409 `concierge_not_live` unless the concierge is live.

**Added in Wave 2** (frozen in `FROZEN_API` where marked *):

- **brief**: `wa_billing_status(cur, hotel_slug) -> {ready: bool | None, checked_at, source: 'account'|'none'}`* (None = unknown: no WABA connected or never checked; Guests then falls back to the owner's attestation). `wa_template_info` returns real registry data `{status, category, quality, category_expected, scope, rejected_reason, last_synced_at}` or None; `submit_template` raises `not_connected` until the hotel saves its WABA.
- **profit_guard**: `dispute_packet(cur, hotel_slug, *, action_id) -> {file_id, filename} | None`* (the packet PDF of a `pg.dispute` action, rendered on first ask; Brief's WhatsApp door sends it). The other doors are built; each keeps its empty-hotel answer listed above.
- **guests**: `room_for_sender`* is pinned (Wave 1 entry above).
- **ask**: `answer_question`*, `askable_metrics`* (table above). Ask's WhatsApp text handler runs at priority 60, after Brief's (40, Adjust sessions and STOP/START only).

## 14. Test harness (`tests/conftest.py`)

One database per pytest process (`clefs_t_<pid>` from `TEST_DATABASE_URL`, default `postgresql://localhost/postgres`, local hosts only), migrated once and dropped at exit. Each test runs in one transaction rolled back afterwards (`db()` blocks become SAVEPOINTs). `@pytest.mark.commits` tests use real transactions and the database is recreated from a template afterwards. Fixtures: `cur`, `app`, `client`, `legacy_slug`, `hotel_uae` (AED, Asia/Dubai, rooms on floors 1, 2, 5), `make_user(role, hotel, *, departments, floors, admin)`, `login(client, user)`, `client_as(role, hotel=None, **kw)`, `device_client(floors, departments, hotel, kind)`, `clock` (freeze/advance), and (01 spec H) `outbox`, `fake_ai` (a `FakeBackend`; queue answers with `.push()`) and `run_jobs(only=None, *, scheduled=False)` (drains due queued tasks, plus due scheduled slots with `scheduled=True`). Events use the local backend; SMS and AI are off.

- `outbox` yields the capture transport (emptied before and after) wrapped so that `outbox.captured(channel=None)` works as a method (it proxies `clefs.notify.captured`). Every other attribute (`sent`, `fail_next(code, msg, channel=None, times=1)`, `unconfigured`, `reset()`) is the transport's own.
- Route walkers go through `tests/foundation/route_walk.iter_routes(app.routes)` (or `route_paths`), never `app.routes` directly: FastAPI 0.137+ nests each `include_router()` as one `_IncludedRouter` entry (Part 3, Deploy and packaging).

---

# Part 2: cross-module contracts (F-contracts)

Files: `clefs/core/{signals,actions,metrics,money,consent,privacy,routes_contracts,serpapi,oauth,stay_events}.py`, `clefs/jobs.py`, `clefs/worker/**`, `clefs/notify/**`, `clefs/files.py`, `clefs/ai.py`, migrations `0010`-`0019` (0016 and 0017 are still free). Every HTTP route below lives in `clefs/core/routes_contracts.py`, mounted first in `CORE_ROUTER_HOOKS`, except the OAuth routes, whose router `clefs.core.oauth` defines and which is mounted last. None of the other contract modules defines its own `router` (a contract test enforces it), so nothing is mounted twice.

Migrations shipped:
- `0010_core_platform.sql`: `files`, `job_runs`, `job_queue`, `outbound_messages` (idempotency_key UNIQUE), `inbound_messages` (provider_id UNIQUE), `whatsapp_windows`, `notify_holds`, `ai_calls`, `consent_texts`, `consents`, `worker_heartbeats`.
- `0011_core_signals_actions.sql`: `signals`, `actions` (Canon 7 states plus `observed`), the autonomy ledger columns, `action_events`, `action_autonomy` (PK hotel_slug, action_type), `action_autonomy_events`. This is the version applied to existing databases (sha256 `92ecb57f...`); it has no K1, which comes from 0013.
- `0012_core_contract_views.sql`: `service_requests_v1` over `staff_requests`, exactly as first applied (the inferred floor goes through BIGINT; 0013 fixes it).
- `0013_core_action_states.sql` (Canon K1, idempotent):
  - `actions.expires_at` (ADD COLUMN IF NOT EXISTS).
  - `actions_status_check` including `failed` and `expired`. Any CHECK on exactly the status column is dropped first, whatever its name.
  - `actions_expiry_idx` on `expires_at` for `proposed`, `approved` and `snoozed` rows.
  - `service_requests_v1.floor` cast through NUMERIC, so a guest-typed room of 20+ digits no longer makes every query on the view fail. The view is replaced only while it still has the foundation's legacy shape (`'legacy'::text AS source` and a `::bigint` cast), so a Service replacement from 0100-0199 is never undone. **Service: keep the NUMERIC cast when you replace the view.**
- `0014_core_inbound_sender.sql`: `inbound_messages.sender_hmac BYTEA` (`privacy.contact_hmac` of the sender's phone or email; NULL when no hotel) and the partial index `inbound_messages_sender_hmac_idx (hotel_slug, sender_hmac, received_at DESC) WHERE sender_hmac IS NOT NULL`. No backfill. **Deploy it before the code**: both webhook INSERTs name the column.
- `0015_core_autonomy_ledger.sql` (idempotent):
  - `action_autonomy_events.status` (`open|accepted|dismissed|superseded`, only and always on `promotion_suggested`).
  - `resolves_event_id` (FK to self; accepted/dismissed rows point at their suggestion).
  - One open suggestion per (hotel_slug, action_type) (partial unique index), and the index `(hotel_slug, action_type, kind, at DESC)`.
  - The backfill replays the old "latest event" rule, so no suggestion changes meaning on upgrade.
- `0018_core_serpapi.sql` and `0019_core_oauth.sql`: see SerpApi and OAuth below.

## Keys

Jobs, tasks, metrics, action types and signal kinds are keyed `<prefix>.<what>`. A module uses its own name or one of its route segments (`profit.`, `profit_guard.`, `menu.`); foundation code uses `core.`, and the domain packages use `stays.`, `ingest.` and `tax.`. The owner is taken from the registering Python module (`clefs.modules.<m>.*`), and `test_module_contracts.py` checks every key.

`registry.KEY_PREFIXES` (also `signals.KEY_PREFIXES`, 04 spec F9) adds these short prefixes to each module's own name. Keys only, never permissions: `register_permissions` still accepts only `<module>.` or route-segment prefixes.

| Module | Prefixes |
|---|---|
| service | `ops.` |
| profit_guard | `pg.`, `tax.` (shared with core) |
| demand | `dr.` |
| amenities | `am.` |
| reviews | `rv.` |
| brief | `wa.` |
| inquiries | `inq.` |
| visibility | `vis.` |
| finance | `fin.` |

## Signals (`clefs.core.signals`)

```python
register_signal_kind(key, *, label, default_owner_role, section, cadence, icon=None) -> SignalKind   # cadence required (F7)
class Evidence(BaseModel): kind: metric|row|file|quote|link|calc|series; label; value=None; ref: dict|None
class RecommendedAction(BaseModel): type; label; params={}; owner_role=None; due_in_hours=None; expected_value: Decimal|None
class SignalIn(BaseModel):
    module; kind; dedupe_key; title; summary=None
    section: money_lost|money_available|service_risk|reviews|verified|compliance | None   # defaults from the kind
    cadence: daily|weekly|monthly | None                                                   # overrides the kind
    # an unregistered kind must give both section and cadence: 400 unknown_signal_kind otherwise
    severity=2 (1-4); impact_minor: int|None | impact_amount: Decimal|None; currency (required with an impact)
    impact_kind=None; confidence (0-1); evidence=[]; recommended_action=None; owner_role=None; owner_user_id=None
    due_at=None; subject_type=None; subject_id=None; expires_at=None
upsert_signal(cur, hotel_slug, s, *, reopen_if_impact_grows_pct=25) -> dict   # + created, reopened
resolve_signal(cur, hotel_slug, dedupe_key, *, resolution) -> bool
resolve_missing(cur, hotel_slug, module, kind, seen_keys) -> int
list_signals(cur, hotel_slug, *, status=("open","surfaced"), modules=None, sections=None,
             cadence: str | list | "daily,weekly" | None = None, limit=50) -> list[dict]   # unknown cadence: 400 invalid_cadence
mark_surfaced(cur, ids); set_status(cur, signal_id, status, *, resolution=None, snoozed_until=None)
get_signal(cur, hotel_slug, id); expire_due(cur, *, hotel_slug=None)
```

- Upsert is keyed on `(hotel_slug, dedupe_key)`. It refreshes the row (title, impact, evidence, due date) and bumps `occurrences`.
- Reopening depends on who closed the signal. If the machine closed it (`resolved` by `resolve_missing`, `expired`, `superseded`), it reopens as soon as the detector sees it again. If a person closed it (`ignored`, `decided`, `snoozed`), it reopens only when `impact_minor` has grown by at least `reopen_if_impact_grows_pct` since closing, or when the snooze has run out.
- Statuses: `open`, `surfaced`, `decided`, `snoozed`, `resolved`, `expired`, `ignored`, `superseded`, `suppressed`.
- API rows add `impact_amount` (a Decimal string), `rankable` (false for compliance: never in the daily top 5, Canon 6) and `action_id`: the latest action that is not terminal. `failed` and `expired` count as terminal, so a signal reopened by a failure offers a new action.
- Core kinds: `core.autonomy_promotion` and `core.autonomy_demoted` (compliance, weekly). Their dedupe keys carry the autonomy event id (`<kind>:<action_type>:<event_id>`), so a new suggestion after a dismissal, or a second demotion, is a new open signal. A newer event, a manual dial change or a demotion supersedes a still-open promotion signal.
- `GET /hotels/{slug}/signals?status=&section=&module=&cadence=&limit=` needs `core.signals.view` and returns a plain JSON array. `cadence` takes one value or the comma form (`daily,weekly`).

## Actions and the autonomy dial (`clefs.core.actions`)

```python
Level = off | observe | suggest | approve | auto_within_limits | auto
SYSTEM = Principal(kind="service", label="system")   # the dial's actor; system code passes every permission check
@dataclass(frozen=True)
class ActionType:
    key; module; label
    executor: Callable[[ActionCtx], ExecResult | dict | None] | None = None   # None = a human task
    verifier: Callable[[ActionCtx], VerifyResult | dict] | None = None
    verify_after=None; verify_every=None; verify_until=None                    # timedeltas
    default_owner_role="gm"; default_level="approve"; max_level="auto"         # .default_autonomy, .ceiling
    always_approve=False            # refunds, folio, rate, OTA changes: ceiling = approve, never automatic
    limits_model: type[BaseModel] | None = None; limit_checker: (ActionCtx, limits) -> LimitCheck | bool
    gate: (ActionCtx) -> Gate(max_level, reasons)                              # e.g. "template not approved" -> observe
    level_check: (cur, hotel_slug, target_level) -> [codes]                   # non-empty: 409 <first code> on a raise
    is_looser: (old_limits, new_limits) -> bool                               # else the generic limits_loosen rule
    params_model=None; validate_params: (ActionCtx, params) -> None (raise ValueError)  # e.g. rate within [floor, ceiling]
    decide_perm="core.actions.decide"; counts_toward_verified_profit=False; reversible=False
class ActionCtx (frozen): cur, hotel, action (row dict), action_type, actor; .params, .action_id (None in
    create_action's gate, which runs on the draft), .type, .hotel_slug
template_gate(channels: {channel: approved}, *, open_level="auto") -> Gate   # one approved channel: open_level; none: observe
limits_loosen(old, new) -> bool; accept_perm_for(to_level) -> perm; next_level(t, current, *, skip_unlimited=None)
MAGIC_SCOPE_ACTION_IDS = "action_ids"
class ExecResult(BaseModel): ok=True; data={}; error=None; template_id; template_category UTILITY|MARKETING; consent_basis; reversal_handle
class VerifyResult(BaseModel): status verified|not_verified|pending; outcome_minor | outcome_value; currency;
                               counts_toward_verified_profit=None; attribution caused|influenced|observed; evidence; confidence

register_action_type(t); get_action_type(key); action_types()
create_action(cur, hotel_slug, *, type, title, params=None, signal_id=None, expected_minor=None | expected_value=None,
              currency=None, owner_role=None, due_at=None, assignee_user_id=None, dedupe_key=None, actor=None, via="system",
              expires_at=None, verify_until=None) -> dict        # row + created + level; datetimes absolute and tz-aware
decide(cur, action_id, decision, *, actor, via="console", params_patch=None, assignee_user_id=None, assignee_role=None,
       assignee_staff_id=None, note=None, snooze_until=None, nonce=None, hotel_slug=None) -> dict
start / mark_done(cur, id, *, actor, result=None, note=None) / submit / await_counterparty / give_up / cancel(reason)
record_verification(cur, id, *, result: VerifyResult | dict)   # or the keyword form, kept:
record_verification(cur, id, *, verified=None | status=..., outcome_value | outcome_minor, currency=None, evidence=[],
                    confidence=1.0, attribution="caused", counts_toward_verified_profit=None, actor=None) -> dict
reverse_verification(cur, id, *, reason, evidence=None)       # outcome -> 0, reversed_at; status stays verified
get_action(cur, hotel_slug, id) (with events); list_actions(cur, hotel_slug, *, status, types, overdue, signal_id, limit); list_events
# autonomy
resolve_level(cur, hotel_slug, action_type, *, ctx=None) -> Resolution(configured, effective, ceiling, reasons, limit_reasons, policy_version, limits, source)
get_autonomy / list_autonomy / set_autonomy(cur, hotel_slug, action_type, *, level, limits=None, actor, reason=None)   # alias set_level
list_autonomy_events(cur, hotel_slug, action_type, *, limit=50) -> [{AUTONOMY_EVENT_FIELDS}]   # newest first, no evidence
promotion_stats(cur, hotel_slug, action_type); review_promotions(cur, hotel_slug); accept_suggestion / dismiss_suggestion(cur, hotel_slug, event_id, *, actor)
demote(cur, hotel_slug, action_type, *, trigger, reason=None, evidence=None); report_incident(cur, hotel_slug, *, kind, action_types, evidence)
```

**State machine** (Canon 7, plus `observed` from the 04 spec and `failed` / `expired` from Canon amendment K1). Anything else raises 409 `illegal_transition`. Terminal: `verified`, `not_verified`, `rejected`, `cancelled`, `unresolved`, `observed`, `failed`, `expired`.

| From | Decision or call | To |
|---|---|---|
| proposed | approve, adjust (`params_patch`, `edited=true`), dispute (`subtype=dispute`) | approved |
| approved | adjust (`params_patch`, `edited=true`; only before the executor ran, else 409 `illegal_transition`). It counts as a human decision and queues the executor again, e.g. editing a quote after approval and before it is sent | approved |
| proposed / approved / assigned / in_progress | assign | assigned |
| proposed | ignore | rejected |
| proposed / approved / assigned | snooze(`snooze_until`, default +24 h) | snoozed |
| snoozed | snooze again (a new `snooze_until`; `snoozed_from` and `expires_at` unchanged) | snoozed |
| snoozed | any other decision wakes it first; the timer (`core.actions.wake`) returns it | previous state (an approved/assigned action with an executor that has not run is queued for execution again) |
| approved / assigned | `start` | in_progress |
| approved / assigned / in_progress | `mark_done`, or a successful executor | done |
| done | `record_verification` | verified / not_verified |
| approved(dispute) / rejected_by_counterparty | `submit`, or a successful executor | submitted |
| submitted | `await_counterparty` | awaiting_counterparty |
| submitted / awaiting_counterparty | `record_verification` | verified / rejected_by_counterparty |
| rejected_by_counterparty / awaiting_counterparty | `give_up` | unresolved |
| proposed / approved / assigned / in_progress / snoozed | `cancel` (supersede or expire) | cancelled |
| approved / assigned | the executor returns `ok=false`, or raises on its last attempt | failed (the signal reopens) |
| proposed / approved, or snoozed from either | `expires_at` passes. `core.actions.expire` (every 5 min) moves it, and so does whichever of `core.actions.wake` or `core.actions.execute` reaches it first. After the lapse, `decide` (including a re-snooze), `start`, `mark_done` and `submit` are 409 `action_expired`; `cancel` still works | expired (the signal expires; the executor never runs) |
| any open state | remind | (no change; `reminders_sent` +1) |

- **Snoozes and expiry.** A snooze defers the decision, not the quote: snoozing never extends `expires_at`. `wake_due()` returns how many actions woke; a lapsed snoozed action is expired instead of woken, and its executor is not re-queued.
- **`via`** is `console|whatsapp|email|api|system`, else 400 `invalid_via`. `decide()` without `hotel_slug` uses the HotelCtx's hotel.
- **Events and audit.** Every call writes an `action_events` row (`created`, `approve`, ..., `executed`, `execution_failed`, `dial_revoked`, `verify_pending`, `verification_reversed`), an `audit_log` row `core.actions.<verb>`, and the SSE event `core.action_changed` for roles owner, gm, finance, revenue and dept_head.
- **Nonces.** `nonce` (WhatsApp buttons) is single-use. A replay returns 409 `nonce_replayed`.
- **Signal links.** approve, adjust, assign and dispute set the signal to `decided`. ignore sets `ignored`, snooze sets `snoozed`, verified sets `resolved`, and a superseded cancel sets `superseded`.
- **Permissions.** `decide()` needs the type's `decide_perm` when `actor` is a HotelCtx. A service Principal (such as `SYSTEM`) or `None` is system code and passes. `mark_done` and `start` also accept the assignee (a user, or a device's on-shift staff member).
- **Magic sessions** (04 spec 1.1). A link whose scope carries `action_ids` (Brief: `create_link(..., scope={"action_ids": [...]})`) decides only those ids, against the membership role's FULL permissions (a revenue user approves an `inquiries.approve` quote from the brief). Other ids are 403 `not_surfaced`. Audit meta carries `via: magic`. A link without the list keeps the plain rule (the magic-filtered permission set, which holds `core.actions.*`).
- **Execution.** An approval of a type with an executor enqueues `core.actions.execute`.
  - The executor runs in its own transaction, inside a savepoint. An executor result with `ok=false` is final (no retries). An exception records `execution_error` and retries with backoff (60 s x 2^n, 5 attempts); the last attempt's exception is final. Both end in `failed`.
  - Success sets `done` (or `submitted` on the dispute path), stamps `dial_level_at_execution`, `policy_version`, `template_id`/`template_category`, `consent_basis` and `reversal_handle`, and schedules verification.
- **Verification.** `core.actions.verify` runs hourly and calls due verifiers. An absolute `verify_until` given to `create_action` holds for the first execution; a dispute re-submission recomputes it. An outcome in another currency is converted to the action's currency (else the hotel's) on write, keeping the original in `verification.original_outcome`; with no rate it is 409 `fx_unavailable` and the job retries. `pending` moves `next_verify_at` forward by `verify_every` (default 1 day) until `verify_until`, after which the action is `not_verified`. `counts_toward_verified_profit` is true only for a `verified` outcome with `attribution="caused"`.
- **Dial.**
  - `create_action` levels:
    - `off` writes nothing and returns `{id: null, status: "suppressed", reason: "dial_off"}`.
    - `observe` writes the action as `observed`, never executed.
    - `suggest` gives `proposed` with surface `console`.
    - `approve` gives `proposed` with surface `brief`.
    - `auto_within_limits` and `auto` approve it by the dial (`decided_via='dial'`, `decided_by` NULL, actor `SYSTEM` logged as `actor_type='dial'`) and enqueue the executor. A dial approval never counts as a human decision toward promotion.
  - **Effective level** is the lowest of the hotel's row (or `default_level`), the type's ceiling (`approve` when `always_approve`) and its `gate`. `template_gate(channels)` is the standard gate for template sends (04 spec 3.5).
  - **Rows** (04 spec 1.4). Nothing seeds the dial: not `create_hotel`, `cli create-hotel` or `seed-demo`.
    - Until a hotel's first `set_autonomy` (or accepted promotion) or `demote` for a type, `resolve_level` uses the type's current `default_level`, with `source: "default"` and `policy_version: 0`. A type registered later, or a default lowered after a safety review, therefore reaches every hotel that never touched that dial.
    - The first write logs a `seed` event before its own `set` / `promotion_accepted` / `demoted` event: `from_level` null, `to_level` the default then in force, `policy_version` 0, actor `system`. From then on the row pins the level.
  - **Limits.** When `auto_within_limits` fails its limits, the action falls back to `approve`, and the reasons go in `limit_reasons`.
    - Without a `limit_checker`, every `max_<field>` limit is compared with `params[<field>]` (or with `expected_minor`, as `amount_minor`).
    - With nothing to compare, the check fails, so `auto_within_limits` never runs without explicit limits.
  - **Re-resolution.** The executor re-resolves the level. A dial-approved action whose effective level has dropped below the auto levels never runs. It lands where `create_action` would place a new action at the new level, and the event is always `dial_revoked` (its `to_status` says where the action went):
    - `approve`: `proposed`, surface `brief`.
    - `suggest`: `proposed`, surface `console` (never pushed to the brief).
    - `observe` (for example a template gate that closed mid-flight): `observed`, terminal.
    - `off`: `cancelled`, with `data.reason = "dial_off"`.
    - In every case `decided_at` / `decided_via` are cleared and the signal goes back to `open`. Human approvals always run.
  - **Setting the dial** (`set_autonomy` / PUT). `core.autonomy.manage`, plus `core.autonomy.raise_auto` to raise the level INTO an auto level or to loosen limits. Lowering and tightening need only `manage`, so a GM can always make a dial safer, including tightening an auto level's limits in place. A raise runs the type's `level_check` (for example 409 `auto_reply_consent_required`).
  - **Loosening** is `ActionType.is_looser(old, new)` when the type defines it, else `limits_loosen`: a higher or removed `max_*`, a lower or removed `min_*`, a list gaining items, or any other change. An added bound is tighter.
  - **Promotion** (1.5). `core.autonomy_review` runs daily at 04:10 hotel-local. When a type has 50 or more human decisions (`approve`/`adjust`/`ignore` by a user) in 30 days with at least 95% approved unedited, it writes a `promotion_suggested` event (`status='open'`) and the signal `core.autonomy_promotion`. It never applies the change itself.
    - Target: `next_level`, one step up and never above the ceiling. `PROMOTION_SKIPS_UNLIMITED = True`: a type without a `limits_model` skips `auto_within_limits` (it would behave exactly like `approve`), so `approve` goes to `auto`. Targets refused by `level_check` are not suggested.
    - One open suggestion per (hotel, type). Quiet while one is open and for 30 days after a dismissal.
    - A `set` that changes level or limits, or a demotion, supersedes it and its signal; a no-op PUT does not.
    - Accept needs `accept_perm_for(to_level)`: `manage` up to `approve`, `raise_auto` above. It is 409 `suggestion_stale` unless the suggestion is open and the dial is still at its `from_level`. `open_suggestion` stays `{event_id, to_level, at}`.
  - **Demotion.** `report_incident(kind in guest_complaint|optout_spike|meta_quality|eval_regression)` lowers the level to min(current, `approve`), bumps `policy_version`, and writes a `demoted` event plus the signal `core.autonomy_demoted`.
- **Core action type:** `core.task` (a human task; ceiling `approve`).

**Routes.** Actions and signals return plain JSON arrays. Money fields are Decimal strings (`expected_value`, `outcome_value`) next to `*_minor` and `currency`.

| Method and path | Access | Notes |
|---|---|---|
| `GET /hotels/{slug}/actions?status=a,b&type=&overdue=&signal_id=&limit=` | `core.signals.view` or `core.actions.decide` | |
| `POST /hotels/{slug}/actions {type, title, params?, signal_id?, expected_value?, currency?, owner_role?, due_at?, assignee_user_id?, dedupe_key?, expires_at?}` | `core.actions.decide` or the type's `decide_perm` | 201 action, or `{status: "suppressed"}` (`expires_at` must carry a timezone) |
| `GET /hotels/{slug}/actions/{id}` | as the list | includes `events[]` |
| `POST /hotels/{slug}/actions/{id}/decide {decision, params_patch?, assignee_user_id?, assignee_role?, note?, snooze_until?}` | member; `decide()` checks `decide_perm` | 409 `illegal_transition` / `nonce_replayed` |
| `POST /hotels/{slug}/actions/{id}/done {note?, result?}` | member; deciders or the assignee | |
| `GET /hotels/{slug}/autonomy` and `/autonomy/{action_type}` | `core.autonomy.view` | level, effective_level, max_level, limits, limits_schema, policy_version, stats, open_suggestion |
| `GET /hotels/{slug}/autonomy/{action_type}/events?limit=` (1-200, default 50) | `core.autonomy.view` | the dial's history, newest first: `[{id, kind, from_level, to_level, trigger, policy_version, actor_type, actor_id, at, status, resolves_event_id}]` (no evidence: a demotion's can quote a guest); 400 `unknown_action_type` |
| `PUT /hotels/{slug}/autonomy/{action_type} {level, limits?, reason?}` | `core.autonomy.manage`; `set_autonomy` adds `core.autonomy.raise_auto` for a raise into an auto level or looser limits | 422 `above_ceiling` / `invalid_limits` / `invalid_level`; 409 `<level_check code>` |
| `POST /hotels/{slug}/autonomy/suggestions/{event_id}/accept` / `dismiss` | `core.autonomy.manage`; accept adds `accept_perm_for(to_level)` (raise_auto above approve) | 409 `suggestion_stale`, 404 `suggestion_not_found` |

The permissions are in `CORE_PERMISSIONS` (Part 1, section 6).

## Metrics (`clefs.core.metrics`)

```python
Dimension(key, label, sql, values_sql=None)
Metric(key, label, description, unit count|money|percent|minutes|ratio|nights|rate, source "FROM ...", hotel_column,
       time_column, time_kind timestamptz|date|epoch_ms, measure, where="", dimensions=(), permission="",
       currency "hotel"|None, money_minor=True, higher_is_better=None, synonyms=())
MetricQuery(metric, start: date, end: date, grain total|day|week|month, group_by<=2, filters {dim: [values]}<=5, compare prev_period|prev_year|None)
register_metric(m); registered_metrics(); list_metrics(ctx); compile_metric(hotel, q) -> (sql, params)
run_metric(ctx, q) -> {metric, label, unit, currency, grain, start, end, group_by, filters, rows[{bucket, group{}, value, compare_value}], total, compare, compare_total, truncated}
dimension_values(ctx, metric, dimension)
```

- **Checked at registration.** Fragments may not contain `;`, `--` or `/*`. A `%` must be `%%` or one of `%(hotel|tz|t0|t1|t0_ms|t1_ms|d0|d1)s`. Dimension keys are plain identifiers.
- **A query picks keys only.** Unknown keys are refused (404 `unknown_metric`, 400 `unknown_dimension`).
- **Values are always bound.** Every value is a parameter, and `<hotel_column> = %(hotel)s` is always in the WHERE clause.
- **Time.** Dates cover the hotel-local `[start, end]`. Buckets are `date_trunc(grain, t AT TIME ZONE %(tz)s)`. Comparisons: day buckets shift by the period length (or one calendar year); week and month buckets pair by position (the k-th bucket of the comparison range with the k-th of the current one).
- **Execution.** Queries run READ ONLY with a 5 s statement timeout (504 `metric_timeout`) and a 500-row cap (`truncated`).
- **Values.** Money values come back as numbers in major units, in the hotel's currency; fractional minor units (averages, ratios) round half up.
- Core metrics: `core.signals_raised`, `core.actions_decided`, `core.verified_value` (money, net of reversals, outcomes in the hotel's currency only).
- Routes, all for members; each metric also has its own permission:
  - `GET /hotels/{slug}/metrics`
  - `POST /hotels/{slug}/metrics/query`
  - `GET /hotels/{slug}/metrics/{metric}/values/{dimension}`

## Jobs and the worker (`clefs.jobs`, `python -m clefs.worker`)

```python
every(*, hours=0, minutes=0, seconds=0); daily_at(hhmm, *, tz="hotel"|"utc", setting=None)   # setting="brief_time_local"
weekly_at(iso_weekday, hhmm); monthly_at(day, hhmm, *, tz="hotel"); nth_weekday_at(n (1..5 | -1), iso_weekday, hhmm, *, tz="hotel")
@scheduled(name, schedule, *, per_hotel=True, timeout_s=120)   # fn(ctx: JobCtx) -> dict | None
@task(name, *, max_attempts=5, backoff_s=30, timeout_s=120)    # fn(ctx: JobCtx, payload: dict) -> dict | None
enqueue(name, payload, *, hotel_slug=None, run_at=None, dedupe_key=None, cur=None) -> id | None (deduped)
cancel(cur, *, dedupe_key=None, job_id=None); raise Retry(run_at, reason)
tick(*, budget_s=25, scheduled=True, queue=True, only=None, max_tasks=None) -> TickReport; run_pending(only=...)
JobCtx: cur (its own transaction), job, hotel_slug, hotel, scheduled_for, attempt, payload, now
load_all(modules=None); status_report(cur); record_heartbeat(worker_id, ...)
```

- **Scheduled slots.** A slot is claimed by inserting into `job_runs (job, hotel, scheduled_for)`, which is unique, so every slot runs once whoever ticks.
  - Hotel-local calendar schedules catch up a missed tick on the same local day, and never the day after (`daily_at`, `weekly_at`, `monthly_at`, `nth_weekday_at` alike).
  - `monthly_at` clamps a day past the month's end to its last day (`monthly_at(31, ...)` runs on 30 April). `nth_weekday_at(-1, ...)` is the month's last such weekday.
  - A failed slot is retried by later ticks, up to 3 attempts at least 60 s apart.
  - Per-hotel jobs skip archived hotels and hotels that switched the owning module off.
- **Queue.**
  - Claims use `FOR UPDATE SKIP LOCKED`.
  - A failure backs off `backoff_s * 2^(attempt-1)` and goes `dead` after `max_attempts`.
  - `Retry` re-queues without counting an attempt and keeps what the task wrote before raising. Any other exception rolls the task back.
  - `enqueue(cur=...)` joins the caller's transaction and NOTIFYs `clefs_jobs` on commit. Use `run_at` for SLA timers, never polling.
- **Registrants.** `load_all()` imports `CORE_REGISTRANTS` (signals, actions, metrics, consent, privacy, files, ai, notify, `clefs.core.serpapi`), then `OPTIONAL_REGISTRANTS` (stays, ingest, tax, when present), then each enabled module's manifest.
- **Worker.** Every 15 s it ticks. Only the process holding `pg_try_advisory_lock('clefs_scheduler')` schedules; every worker drains the queue. It wakes on `LISTEN clefs_jobs`, supervises each module's `worker_services`, writes `worker_heartbeats` and stops cleanly on SIGTERM. At startup it logs `validate_for_startup()` warnings and `production_gaps()`, as `create_app` does. `--once` runs one tick and prints the report.
- **Routes.** Both need Bearer `CRON_SECRET` (the worker token also works).
  - `GET|POST /internal/cron/tick?budget_s=` is the Vercel Cron target (`vercel.json` calls it every minute; Part 3). Vercel Cron sends GET.
  - `GET /internal/status` returns last tick, queue depth, failed jobs, outbox health, workers, events listener, notify mode and AI switch.
- Core jobs:
  - `core.actions.execute` (task)
  - `core.actions.verify` (hourly)
  - `core.actions.wake` (5 min: snoozes and signal expiry)
  - `core.actions.expire` (5 min: K1 `expires_at`)
  - `core.autonomy_review` (04:10 hotel-local)
  - `core.notify.deliver` (task)
  - `core.notify.sweep` (1 min)
  - `core.files.purge` (03:40 UTC)
  - `core.serpapi.purge` (03:50 UTC)
  - `core.jobs.cleanup` (03:20 UTC)

## Notifications and inbound webhooks (`clefs.notify`)

```python
send_whatsapp_template(to, template, lang, params, *, hotel_slug, purpose, sender="platform"|"hotel", buttons=None, header=None,
                       category="utility"|"marketing"|"authentication", idempotency_key=None, action_id=None, cur=None) -> NotifyResult
send_whatsapp_text(to, body, *, hotel_slug, purpose, sender, preview_url=False, fallback_template=None, idempotency_key, cur)
send_whatsapp_interactive(to, body, *, buttons=[(id, title)] | sections=[{title, rows:[{id,title,description}]}], button_text, header, footer, ...)
send_whatsapp_document(to, file_id, filename, caption=None, *, hotel_slug, purpose, ...)
send_email(to, subject, html, *, text=None, attachments=[file ids], reply_to=None, headers=None, hotel_slug=None, purpose, category, from_=None, ...)
send_sms(to, text, *, hotel_slug=None, purpose, provider=None, template_vars=None, ...)      # msg91 for +91, twilio otherwise
notify(Recipient(phone, email, name, user_id, locale), Message(purpose, hotel_slug, category, sender, whatsapp_text,
       whatsapp_template {name, lang, params, buttons?, header?}, sms_text, email_subject, email_html, email_text, idempotency_key),
       *, channels=("whatsapp","sms","email"), cur=None) -> NotifyResult
NotifyResult(ok, channel, outbox_id, status, error, deduped)
@on_whatsapp(audience "guest"|"staff"|"unknown"|"*", *, priority=50, match=None, button_prefix=None, kind=None)   # fn(InboundCtx) -> truthy = handled
@on_inbound_email(purpose="*"|"inquiries"|"reviews"|"ingest"|..., *, priority=50, match=None)                      # fn(InboundEmail)
@on_outbound_status(purpose_prefix)                                                                                  # fn(cur, row, status, error_code)
validate_template_definition(components); PayloadError; captured(channel=None); clear_captured(); CAPTURE.fail_next(code)
GUEST_PRIORITY = {"guests.optout_consent": 5, "guests.journey_replies": 10, "inquiries.whatsapp_intake": 20,
                  "concierge.whatsapp_chat": 30, "service.whatsapp_fallback": 90}   # frozen guest order (04 spec F4)
wa_window_open_until(cur, hotel_slug, *, sender_hmac, at=None) -> datetime | None
attempt_key(base, attempt) -> "<base>:a<attempt>"          # a deliberate re-send needs a new UNIQUE key
cancel_queued(cur, *, idempotency_key=None, outbox_id=None, hotel_slug=None, reason="cancelled", followups=True)
    -> {outbox_id, status, cancelled, cancelled_ids}        # retract a send that has not gone out yet
inbound_address(alias, purpose=None, ref=None, *, domain=None) -> "demo+inq-42@in.clefs.world"
hotel_inbound_address(cur, hotel_slug, purpose=None, ref=None) -> str | None   # None without hotels.inbound_email_alias
inbound_domain() -> INBOUND_EMAIL_DOMAIN or "in.clefs.world"; purpose_for_token(token) -> purpose; parse_address(addr, domain)
InboundEmail.sender_hmac: bytes | None
InboundEmail.header_list: list[tuple[str, str]]   # every header in message order, duplicates kept
InboundEmail.headers: dict[str, str]              # trusted_headers(header_list): first copy of each header;
    # Authentication-Results / Received-SPF only when exactly one copy names our verifier
    # (INBOUND_EMAIL_AUTHSERV_IDS authserv-ids, INBOUND_EMAIL_SPF_VERIFIERS receiver= hosts,
    # both default inbound.resend.com). .spf / .dkim read only those.
trusted_headers(header_list) -> dict[str, str]; AUTH_VERDICT_HEADERS
process_email(payload) -> {ok, status, ...}       # one message as our email payload (field names: Outbound email below)
from_resend(message, attachments) -> payload; fetch_resend_email(email_id) -> payload | None; process_resend(event)
verify_resend_signature(raw_body, svix_id, svix_timestamp, svix_signature) -> bool   # 503 when RESEND_WEBHOOK_SECRET is unset
```

- **Outbox.** Every send writes an `outbound_messages` row (`purpose` is `<module>.<what>`) and enqueues `core.notify.deliver` in the caller's transaction.
  - `idempotency_key` is UNIQUE. A repeat returns the first row with `deduped=True`, so a deliberate retry needs a new key.
  - `ok` means accepted: queued or sent. It is false for `outside_window`, `skipped_unconfigured` and `failed`.
  - **Retract.** `cancel_queued` finds the row by `idempotency_key` or `outbox_id` (both given: both must match), within `hotel_slug` when given.
    - Only `queued` rows change. They become `cancelled` with `meta.cancel_reason` and `meta.cancelled_at`, and their `core.notify.deliver` task is cancelled.
    - With `followups`, the queued rows the outbox made from it (retry copies and template fallbacks, following `fallback_of`) are cancelled too.
    - A worker mid-send holds the row lock, so the cancel waits and then finds the row no longer queued (`cancelled: False`, `status: "sent"`).
    - No `on_outbound_status` hook fires. No row matched: `{outbox_id: None, status: None, cancelled: False, cancelled_ids: []}`. Called with neither key nor id: `ValueError`.
- **NOTIFY_MODE.** `live` makes HTTP calls to Graph, Resend, MSG91 and Twilio, delivering after commit or inline in the caller's own transaction. `log` logs instead. `capture` keeps the payloads in memory for tests. `log` and `capture` deliver inline.
- **WhatsApp rules.**
  - Payload limits:
    - Templates: header 60, body parameters 1024 in total, footer 60, at most 10 buttons, quick-reply text 25.
    - Reply buttons: at most 3, title 20.
    - Lists: at most 10 rows, row title 24, row description 72, ids 200.
    - Text bodies: 4096.
  - Pacing: `core.notify.deliver` is the only pacer. It takes `pg_try_advisory_xact_lock(7261, hashtext(recipient))`; a busy recipient is deferred 6 s without counting an attempt. The 6 s spacing is per recipient across the hotel and platform numbers.
  - Session messages (text, interactive, document) need the 24 h window, which inbound messages open (`whatsapp_windows.csw_expires_at`). Outside it the row is `outside_window`, and `fallback_template` sends a template instead.
  - Meta error codes:
    - 131056 (pair rate limit): retry after 6 s x 2^n.
    - 130429 (throughput): hold the whole sender (`notify_holds`) for 30 s x 2^n. The 04 spec F4 says 6 s; the implemented 30 s is pinned by `test_notify.py::test_meta_130429_holds_the_whole_sender` and kept pending a spec amendment (Part 3, requests).
    - 131047: fail, then send the fallback template.
    - 131049: retry after 24 h.
    - Anything else retries and fails after 7 attempts.
  - Marketing sends wait for 09:00-18:00 Asia/Dubai.
  - Documents go out as a 1-hour signed link to our file.
- **Senders.** `sender="hotel"` uses `hotels.whatsapp_phone_number_id`. `platform` uses `WA_PLATFORM_PHONE_NUMBER_ID`.
- **SMS.** SMS to +971 may not contain links.
- **Email headers.** `send_email(headers=...)` is checked by `validate.email_headers`: names use letters, digits and `-`; From, To, Cc, Bcc, Subject, Reply-To, Sender, Return-Path, Date, MIME-Version and every name starting `Content-` (Type, Transfer-Encoding, Disposition, ID, Language, ...) are refused (use the function's own arguments); values are one line of at most 998 characters.
- **Magic links.** Importing `clefs.notify` registers the magic-link sender with `clefs.auth.magic`. It uses WhatsApp text inside the window, else email, else the approved template `get_settings().WA_MAGIC_LINK_TEMPLATE` (default `clefs_magic_link`).
- **`GET /webhooks/whatsapp`** answers the `hub.challenge` when `hub.verify_token` matches `WA_VERIFY_TOKEN`.
- **`POST /webhooks/whatsapp`.**
  - **Signature.** `X-Hub-Signature-256` is an HMAC-SHA256 over the raw body with `WA_APP_SECRET` (or `META_APP_SECRET`), compared in constant time. A bad signature gets 401; an unconfigured secret gets 503.
  - **Dedupe.** Messages dedupe on `messages[].id`, statuses on `status:<wamid>:<status>`.
  - **Audience.**
    - `staff`: a user (`users.phone_e164`) or a staff member (`staff_members.whatsapp_e164` / `phone_number`).
    - `guest`: someone messaging a hotel's own number.
    - `unknown`: anyone else.
  - **InboundCtx fields:** kind (`text`, `button`, `button_reply`, `list_reply`, media types), text, button_id, **`button_ref` (only a valid `tokens.sign_ref` tag is trusted; `button_prefix` handlers match verified refs only)**, context_id, referral, user_id, staff_member_id, sender_hmac (`privacy.contact_hmac`, also stored on `inbound_messages.sender_hmac`), window_open_until, and `reply_text()`.
  - **Handlers** run by ascending priority in savepoints. A handler wins with a truthy return; a dict or result object with a `handled` field decides by that field, so `{"handled": False}` and `{}` pass the message on. A crash is recorded on the inbound row, and Meta still gets 200.
  - **Guest priority slots.** A guest handler from `clefs.modules.<m>` registered outside that module's `GUEST_PRIORITY` slots is logged as a warning, not refused.
  - **Statuses** only move a row forward (sent, then delivered, then read), record billable and pricing category, apply the error-code follow-ups above, and call `on_outbound_status` hooks.
- **`on_outbound_status` sources.**
  - Meta's status webhook (WhatsApp): each distinct status once, in arrival order.
  - The outbox itself: a terminal failure at send time (any channel, including 131047), and `sent` for email and SMS.
  - On `failed`, `row["meta"]` carries `retried_by` (131056, 131049) or `fallback_id` (131047) when a follow-up row exists. That follow-up row reports its own statuses under the same purpose.
- **Outbound email** goes through Resend (`RESEND_API_KEY`, `EMAIL_FROM`; `EMAIL_REPLY_TO` when a message names no Reply-To). The outbox stores our email payload (Postmark-style field names: From, To, HtmlBody, Headers, Attachments, Tag, ...); the transport converts it for Resend and records provider `resend`. Recipients on reserved domains (RFC 2606 / 6761) are dropped before the call.
- **`POST /webhooks/email/resend`** takes Resend's `email.received` webhook. It needs `INBOUND_EMAIL_ENABLED=true` and `RESEND_WEBHOOK_SECRET` (503 `not_configured` otherwise).
  - **Signature.** Svix: `svix-signature` lists `v1,<base64>` entries, each an HMAC-SHA256 of `<svix-id>.<svix-timestamp>.<raw body>` keyed with the base64 part of the `whsec_` secret; any v1 entry may match (bytes, constant time). A timestamp more than 5 minutes off either way, or no match, gets 401 `invalid_signature`.
  - The body carries metadata only. Other event types are answered `{ok: true, status: "ignored"}`. For `email.received` the message is read back (`GET /emails/receiving/{email_id}`, then its attachments from their signed links) with `RESEND_API_KEY`; a Resend error or timeout answers 502 so Resend redelivers, and an email Resend no longer has (404) is logged and answered `ignored`. Attachments over `FILE_MAX_BYTES` are not downloaded and land in `skipped_attachments` as `too_large`.
  - **Sender verdicts** come only from the API's `authentication` object. Every `Authentication-Results` and `Received-SPF` the message carried is dropped and exactly one of each is written (`<spf> receiver=inbound.resend.com`; `inbound.resend.com; spf=..; dkim=..; dmarc=..`), so a sender-written verdict never reaches a module. They name no domain, so only `dmarc=pass` proves the From domain to ingest and reviews. When the From header names anything but exactly the API's sender, no verdict is written.
  - Addresses have the form `<hotels.inbound_email_alias>+<purpose>[-<ref>]@<inbound_domain()>`, read from To, Cc and the envelope recipients (Resend's `received_for`, which is where forwarded mail names us). Purpose tokens: `inq` or `inquiries` -> inquiries, `reviews` -> reviews, none or `ingest` -> ingest. Refs are compared lowercased. An unset `INBOUND_EMAIL_DOMAIN` means `in.clefs.world`, never any domain.
  - Duplicates are dropped by `MessageID`: the RFC Message-ID without angle brackets (what In-Reply-To / References name), else Resend's email id.
  - Attachments become files (`source='email'`, purpose `email.<purpose>`).
  - The reply is `{ok, status: handled|unhandled|unrouted|duplicate|ignored, purpose, hotel_slug, attachments}`.

## Files (`clefs.files`)

```python
FileRef(id, filename, content_type, size_bytes, sha256)             # exactly the client's FileRef
put_file(cur, hotel_slug|None, data, *, filename, content_type, purpose, source="upload"|"email"|"whatsapp"|"generated"|"import"|"api",
         actor=None, retention_days=None | retention_until=None) -> FileRef
get_file(cur, hotel_slug, file_id) -> (FileRef, bytes); file_row(...); delete_file(...)
signed_path(hotel_slug, file_id, *, ttl_s=300) -> "/hotels/{slug}/files/{id}?t=..."; verify_download(...)
FileStore protocol: DbFileStore (bytea, default) | S3FileStore (slot, raises NotConfigured); set_store() for tests
```

- **Size.** The cap is `FILE_MAX_BYTES` (25 MB); larger files get 413 `file_too_large`. Filenames are reduced to their last path component.
- **Dedupe.** The same bytes sent to the same hotel return the existing file (sha256), and the longer retention wins.
- **Purge.** `core.files.purge` drops the bytes after `retention_until` and keeps a tombstone row.
- `POST /hotels/{slug}/files` takes multipart `file` and `purpose`. It authenticates (ticket or cookie) and checks `Content-Length` against `FILE_MAX_BYTES` + 64 KB BEFORE parsing the body, then parses at most one file and five fields through a byte-capped receive (chunked bodies included). Webhook bodies are capped too (WhatsApp and Resend 1 MB), and signature compares run on bytes.
  - **With a ticket (`?t=`)** from `POST /files/upload-ticket`: `claims["h"]` must equal the slug and `claims["purpose"]` the purpose (403 `ticket_mismatch`), and the upload is attributed to `claims["u"]`/`claims["d"]`.
  - **Without a ticket**, the caller needs `core.files.upload`. Magic sessions and legacy visitors never have it.
  - Every upload is audited as `core.files.upload`.
- `GET /hotels/{slug}/files/{id}` needs `core.files.view`, or the caller must be the uploader (a user or a device), or hold a signed `?t=` link. It is served as an attachment with nosniff and no-store.

## AI (`clefs.ai`)

```python
structured(task, *, system, input, schema: type[T], hotel_slug=None, model=None, file_ids=(), max_output_tokens=4000, store=False, timeout_s=30.0, temperature=None) -> T   # temperature sent only when set
text(task, *, system, input, hotel_slug=None, model=None, max_output_tokens=2000, store=False) -> str
tool_call(task, *, system, input, tools, hotel_slug=None, model=None, store=False) -> list[ToolCall(name, arguments, call_id)]
web_search(task, *, query, schema, hotel_slug=None, model=None, system=None, store=False) -> SearchResult(data: T, citations: [Citation(url, title, start_index, end_index)], text)
responses_raw(task, *, input, tools, include=(), model=None, hotel_slug=None, timeout_s=15, store=False) -> dict
    # a copy of the Gemini response body plus "clefs": {"ai_call_id", "cost_micros", "cost_currency"} for callers' own ledgers
set_backend(FakeBackend(*outputs)) / reset_backend(); enabled(); strict_schema(model)
AIDisabled (409 ai_disabled), AIError (502 ai_error / ai_invalid_output)
cost_micros(model, *, tokens_in, tokens_out, cached_tokens_in=0, web_search_calls=0) -> int | None   # micro-USD (Canon K3)
model_price(model) -> (in, cached_in, out) micro-USD per token | None;  MODEL_PRICES;  WEB_SEARCH_CALL_MICROS = 14_000
AIResponse += cached_tokens_in, web_search_calls, model, cost_micros, ai_call_id
```

- **Backend.** The Gemini API through the google-genai SDK (`GEMINI_API_KEY`, or Vertex AI with `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`), with structured output (`response_mime_type="application/json"` plus a response schema). `web_search` uses Grounding with Google Search.
- **Model.** `model`, else `AI_MODEL_<TASK>` (for example `ingest.map_columns` reads `AI_MODEL_INGEST_MAP_COLUMNS`), else `AI_MODEL_DEFAULT`.
- **Kill switch.** When `AI_ENABLED=false` or there is no key, calls raise `AIDisabled`, and callers degrade.
- **Logging.** Every call writes one `ai_calls` row with task, model, backend, status, tokens, latency, `cost_micros` and `cost_currency='USD'`. Prompts and outputs are stored only when `AI_LOG_CONTENT=true`.
- **Cost.** A dated or preview name (`gemini-2.5-flash-preview-09-2025`) is priced as its base model. `AI_PRICE_<MODEL>="in,out"` or `"in,cached,out"` (USD per 1M tokens, read through `env()`) replaces a price. Unknown models get NULL, never 0.
- **Storage flag.** Every `ai.*` call takes `store: bool = False` (keyword-only) for call-site compatibility; Gemini keeps no stored responses, so `store=True` is ignored with a warning. On the paid tier, Gemini API prompts and responses are not used to train Google's models; the Vertex AI option keeps processing in the chosen region.
- **FakeBackend.** It records requests in `.calls` and answers from a queue: str, dict or BaseModel, a list of tool calls, an `AIResponse`, an exception, or a callable. It works even while AI is switched off.

## Money, consent, privacy

```python
# clefs.core.money
D(x); ccy(code); minor_units(ccy); quantize(a, ccy) (ROUND_HALF_UP); to_minor / from_minor / money_str(minor, ccy)
fmt_money(a, ccy, locale="en-AE", *, minor=False) -> "AED 1,234.50" (Indian grouping for en-IN); allocate(total_minor, weights) (sums exactly)
fx_rate(cur, base, quote, on); convert(cur, a, src, dst, on) -> Decimal; convert_minor(...)   # fx_rates table (core/tax) else the AED/USD 3.6725 peg; 409 fx_unavailable
compose(amount, room_nights, profile) / decompose(...) -> TaxBreakdown   # forward to clefs.core.tax (core/tax/README.md)
# clefs.core.consent (append-only; no row = no consent)
purposes: recording, ai_processing, transfer_abroad, marketing, ai_analytics, profile_memory, auto_reply, whatsapp_updates (+ register_consent_purpose)
record_consent(cur, hotel_slug, subject_ref, purpose, granted, *, source, evidence=None, subject_type="guest", text_version=None, language="en", expires_at=None, actor=None)
has_consent(cur, hotel_slug, subject_ref, purpose, *, at=None) -> bool; withdraw(...); consent_state(cur, hotel_slug, subject_ref)
record_consent_text(cur, purpose, version, body, *, language="en", hotel_slug=None)   # immutable per version (409 consent_text_immutable)
current_consent_text(cur, purpose, *, language="en", hotel_slug=None)
# clefs.core.privacy
register_pii(module, table, *, subject_columns, ts_column, retention_days, strategy delete|redact, redact_columns=(), where=None); pii_registry()
contact_hmac(hotel_slug, "phone"|"email", raw, *, default_country_code=None, cur=None) -> bytes; contact_ref(...) -> hex (consent subject_ref)
hotel_calling_code(hotel_slug, *, cur=None) -> str | None;  CALLING_CODES (ISO2 -> code)
seal(text) -> bytes; unseal(blob) -> str   # AES-256-GCM, version byte; PII_ENC_KEY (32 bytes, base64 or hex)
mask_phone / mask_email
```

- `subject_ref` is always a hashed identity (`contact_ref`) or an internal id, never a raw phone number or email address. Every consent change is audited as `core.consent.grant` or `core.consent.withdraw`.
- Outside production, missing `CONTACT_HMAC_PEPPER` and `PII_ENC_KEY` values (read as `get_settings()` fields) are derived from `SECRET_KEY`. Production refuses to derive them (`NotConfigured`), and `production_gaps()` names them at API and worker startup.
- **National phones** (F8) take the explicit `default_country_code`, else the hotel's calling code. Only national-format input reads the database; E.164 and `00` numbers never look anything up. Pass `cur` in request code. A hotel in a country missing from `CALLING_CODES` (NANP, IT, RU, KZ: their trunk prefixes do not strip cleanly) keeps national numbers refused (`invalid_phone`).
- The PII registry entry for `inbound_messages` lists `sender_hmac` as a subject column and redacts it with `payload`.

## SerpApi (`clefs.core.serpapi`, 04 spec F5, Canon K3)

```python
search(engine, params, *, hotel_slug, purpose, max_age=timedelta(hours=20)) -> dict   # the ONLY SerpApi path
google_hotels_area_params(hotel, *, q, check_in, check_out) -> {engine:'google_hotels', q, check_in_date, check_out_date,
                                                               currency: hotel.currency, gl:'ae', hl:'en', adults:2}
cache_key(engine, params) -> str; normalize_params(params) -> dict[str, str]
month_spend_micros(cur, *, hotel_slug=None, purpose=None, at=None) -> int   # billed USD micros, UTC month
cost_per_search_micros() -> int; purge(cur, *, now=None)
BudgetExhausted (503 budget_exhausted; .scope global|public_check, .limit, .used)
SerpApiError (502 serpapi_error, not billed); NotConfigured without SERPAPI_KEY
use_fake(*answers) -> FakeSerpApi (tests: queued answers, .requests with api_key masked); set_transport(None)
```

- **Cache key** = sha1(`engine?` + urlencoded sorted normalised params), without `api_key`, `no_cache`, `engine`, `output`, `async` or None values (2 == "2", dates ISO, case kept).
- **Cache hit** (fetched within `max_age`): no call, a free `cache_hit` row, served even with the budget spent or no key. A truthy `no_cache` skips the read and overwrites the shared entry.
- **Miss**: under an advisory lock the budget is checked and a `reserved` (billed) row committed; the HTTP call (30 s) runs outside any transaction; the row settles to `ok` (cached) or `error` (billed=false, cost 0). "No results" is billed and cached as `empty`. Two identical concurrent misses may both bill.
- **Budgets**: `SERPAPI_MONTHLY_BUDGET_USD` (None = no global cap) against the sum of billed `cost_micros` this UTC month. Purpose `public_check`: at most `PUBLIC_SERPAPI_MONTHLY_CAP` billed searches per month (unset = 300, 0 = off).
- **Price**: the setting `SERPAPI_COST_PER_SEARCH_USD` (Decimal, default 0.015); 1 USD = 1,000,000 micros. `purpose` is `<module>.<what>` or `public_check`. `search` takes no cursor (it uses its own `db()` blocks).
- Job `core.serpapi.purge` (03:50 UTC, registered by `jobs.load_all()`): cache past `expires_at` (fetched + 7 d), ledger older than 400 d.
- Migration `0018_core_serpapi.sql`:
  - `serpapi_cache(cache_key PK, engine, params JSONB, response JSONB, status ok|empty, fetched_at, expires_at)`
  - `serpapi_calls(id, hotel_slug NULL, purpose, engine, cache_key, cache_hit, billed, cost_micros BIGINT, cost_currency CHAR(3) 'USD', status, error, latency_ms, at)`

## OAuth (`clefs.core.oauth`, 04 spec F6)

```python
get_access_token(cur, hotel_slug, provider) -> str   # refreshes within 2 min of expiry
get_connection(cur, slug, provider); list_connections(cur, slug)   # never tokens
set_account_ref(cur, slug, provider, account_ref, *, meta=None) -> bool   # e.g. the Xero tenant id
disconnect(cur, slug, provider, *, actor) -> bool   # tokens nulled; provider revoke after commit
start_flow(cur, slug, provider, *, user_id, next_path=None) -> {provider, authorize_url, expires_in}
complete_flow(provider, query) -> CallbackResult(provider, status, hotel_slug, next_path, error, connection_id)
NotConnected(detail, *, provider=None)   # 409 not_connected
OAuthError                               # 502 oauth_error; .error, .transient
PROVIDERS; register_provider(p); get_provider(key); redirect_uri(p); set_transport(t | None)
```

- **Providers** (client id/secret settings; start permission):
  - `google_business`: `business.manage`, PKCE, offline + prompt=consent; `GBP_OAUTH_*`; `visibility.manage` or `reviews.manage`
  - `xero`: `accounting.reports.read offline_access`, PKCE, basic auth; `XERO_*`; `finance.connect`
  - `qbo`: `com.intuit.quickbooks.accounting`, basic auth; the callback's `realmId` becomes `account_ref`; `QBO_*`; `finance.connect`
  - `zoho`: `ZohoBooks.accountants.READ,ZohoBooks.settings.READ`; the code is exchanged only with an allowlisted `accounts-server`; `ZOHO_*`; `finance.connect`
  - `finance.connect` is not registered yet (the Finance lane registers it for owner and finance, 04 spec 7.1). Until then only Clefs admins can start the accounting flows.
- **Tokens** are sealed with `privacy.seal` (imported lazily). A refresh commits in its own transaction under `FOR UPDATE SKIP LOCKED` (a rotated refresh token survives a caller rollback), else it waits on the caller's cursor. `invalid_grant` / `invalid_code` -> status `revoked` (audit `core.oauth.revoked`) + NotConnected. 5xx, 429 and network errors are transient OAuthErrors (the connection stays active).
- **Redirect URI** = `(OAUTH_REDIRECT_BASE or PUBLIC_API_ORIGIN)` + `/public/oauth/{provider}/callback`. Register exactly that URI with Google, Xero, Intuit and Zoho.
- **State**: only its sha256 is stored, for 15 min, deleted on first use; the hotel comes from the row. 20 starts per user per 10 min.

| Method and path | Access | Response |
|---|---|---|
| `GET /hotels/{slug}/oauth` | `core.settings.view` | one row per provider: `{provider, label, configured, status, account_ref, scopes, expires_at, connected_at, last_used_at, last_error}`; status `not_connected` when absent |
| `GET /hotels/{slug}/oauth/{provider}/start?next=&format=redirect\|json` | the provider's start perms; a signed-in user (not magic, not a device) | 302 to the provider (no-store), or `{provider, authorize_url, expires_in}`; 404 `unknown_provider`, 409 `not_configured`, 429 |
| `DELETE /hotels/{slug}/oauth/{provider}` | same | 204; 404 `not_connected` |
| `GET /public/oauth/{provider}/callback` | public (state) | 302 to `web_base_url + next_path` (default `/app/{slug}/visibility` or `/finance`; `/app` when the state is unknown) with `oauth=<provider>&oauth_status=connected\|denied\|error[&oauth_error=<code>]` (e.g. invalid_state, state_expired, exchange_failed, scope_denied) |

- **Client use.** A connect button fetches `.../oauth/{provider}/start?format=json&next=<path>` and navigates to `authorize_url` (or links to `start` without `format` for the 302); on return it reads `oauth`, `oauth_status` and `oauth_error`.
- **Audit**: `core.oauth.connect` (actor = the user from the state), `core.oauth.disconnect`, `core.oauth.revoked` (system).
- Migration `0019_core_oauth.sql`:
  - `oauth_connections(id, hotel_slug, provider, account_ref, scopes TEXT[], access_token_enc, refresh_token_enc BYTEA, expires_at, status, meta JSONB, last_error, connected_by, connected_at, last_used_at, last_refreshed_at, UNIQUE(hotel_slug, provider))`
  - `oauth_states(state_hash PK, hotel_slug, provider, user_id, next_path, redirect_uri, code_verifier_enc, expires_at)`
- Mounted through `CORE_ROUTER_HOOKS` (`clefs.core.oauth`, last).

## Stay events (`clefs.core.stay_events`, 04 spec D1)

Types only, next to `clefs.core.stays`; the domain lane emits them.

- `STAY_EVENTS`: created, dates_changed, cancelled, no_show, checked_in, checked_out, contact_changed.
- `StayEvent{id, event, hotel_slug, reservation_id, occurred_at, changes{field: [old, new]}}`; `CHANGE_FIELDS` per event. `contact_changed` carries ids only.
- `DISPATCH_JOB = "stays.dispatch_events"`.

## Core domain (`0020`-`0023`, `clefs.core.stays` / `ingest` / `tax`)

The domain lane's tables and Python packages have shipped. `app.py` mounts their routers through `CORE_ROUTER_HOOKS` and `jobs.OPTIONAL_REGISTRANTS` registers their jobs (both still skip a package that is absent, so the app boots either way). Each package's README is the signature reference; import from the package, never a submodule.

- `0020_core_ingest.sql`: `mapping_templates`, `import_batches` (`hotel_slug` NULL only for the public OTA-statement sandbox), `import_rows`.
- `0021_core_stays.sql`: `reservations` (status `CONFIRMED | CANCELLED | NO_SHOW | IN_HOUSE | CHECKED_OUT`, `UNIQUE(hotel_slug, source_system, pms_res_id)`), `reservation_nights`, `folio_charges`, `occupancy_daily`, `occupancy_by_floor_daily`, `otb_snapshots`, `baseline_exclusions`, `reservation_matches`, `reservation_contacts` (`value_hmac` BYTEA from `privacy.contact_hmac`, NULL once an OTA-provenance row is purged), `stay_events`, `stay_event_cursors`, `stay_event_failures`.
- `0022_core_tax.sql`: `fee_rules`, `hotel_tax_overrides`, `hotel_tax_profiles`, `fx_rates`. `0023_core_tax_seed.sql`: the Dubai and Abu Dhabi rules, the 2026 announcements (ANNOUNCED_UNVERIFIED, applied only with an owner override) and the USD/AED peg.
- `0024_core_ingest_menu_kind.sql`: `import_batches.kind` also allows `MENU` (the CHECK is replaced, found by definition; re-running is a no-op).
- **`clefs.core.stays`** (`core/stays/README.md`): `contact_value`, `reservation_breakdown`, `on_stay_event`, `name_similarity` and the rest. Modules call them directly; the lazy-import fallback in `guests/stays_port.py` is no longer needed (it is harmless). `delete_matches(cur, hotel, line_kind, line_ids) -> int` (Wave 2) lets a claim owner drop the match rows of lines that left a corrected document, so no module deletes `reservation_matches` itself. Routes under `/hotels/{slug}/stays`: reads `core.stays.view` (analyst roles), writes `core.stays.manage` (owner, gm).
- **`clefs.core.ingest`** (`core/ingest/README.md`): batches, mapping templates, AI column mapping, the commit gate; `register_commit_handler(kind, fn)` is exported from the package, and so (Wave 2) is `batch_view(batch, *, counts=None)`, the one Batch serializer, for modules that serve their own import routes (Profit Guard). Routes under `/hotels/{slug}/ingest`: reads `core.settings.view`, writes `core.ingest.manage` (owner, gm, finance, revenue). Staged rows are purged by the `ingest.sweep` job (not by Trust's retention: their tenant key is reached through `import_batches`).
  - **MENU kind** (Wave 1 gaps). Canonical fields, all text: `outlet outlet_name sku category category_ar name_en! name_ar desc_en desc_ar price! available_from available_to days dietary alcohol prep_min active sort`. The alias map is `aliases/menu.yaml` (source `menu`, UNVERIFIED, no `detect`, so an emailed file is never taken for a menu). Upsell registers the commit handler (`upsell/importer.commit_menu`), and `POST /hotels/{slug}/upsell/import` runs on this pipeline.
  - **Commit-handler hooks.** `commit_batch(cur, hotel_slug, batch_id, *, actor=None, skip_errors=False, options=None, dry_run=False)`:
    - `options` reach the handler as `batch["options"]["commit"]` and are stored on the batch on a real commit.
    - `dry_run` runs every check and the handler inside a rolled-back savepoint. The batch stays in REVIEW and the return carries `commit_result` and `dry_run: True`; the handler sees `batch["dry_run"]`.
    - `mark_row_error(cur, row_id, message)` lets a handler flag a refused row as ERROR (use a per-row savepoint).
    - `POST .../batches/{id}/commit` takes `{skip_errors?, options?, dry_run?}`. `options` is limited to 4000 characters of JSON (400 `options_too_large`).
- **`clefs.core.tax`** (`core/tax/README.md`): `compose`, `decompose`, `compose_stay`, `StayLine`, `StayTaxBreakdown`, `resolve_profile`, `decompose_night`, `td_liability`, `td_month`, `td_remittance_due_date`, `notices`, `profile_in_currency(cur, profile, currency, on)` (TD converted on the date, fees quantized to that currency; no rate: TD 0 plus a notice), `list_fee_rules`, `set_override`, `get_tax_profile`, `set_tax_profile`, `configure_rule`. core/tax mounts no routes: Profit Guard serves the owner-facing tax API (`GET /hotels/{slug}/profit/tax/fee-rules?on=` -> `{rules}`, `GET|PUT /profit/tax/profile`, `POST /profit/tax/overrides {fee_rule_id|rule_key, apply, effective_from?, effective_to?, note?}`; `profit.view` / `profit.manage`), thin wrappers that keep the core return shapes.
  - `compose_stay(..., currency=None)`: the currency the line rates are in (default the hotel's). `core.stays.reservation_breakdown` passes the reservation's currency, so a USD booking's breakdown is in (and labelled) USD with the AED TD converted per stay date; `reservation_nights` fee columns follow the same rule. Callers that sum breakdowns across reservations must convert by `breakdown.currency`.

## PII masking (`clefs.core.pii`, 03 spec 5.2, Wave 1 preflight)

Every guest-bearing serializer masks by role before a row leaves the server. The matrix is foundation code rather than a Trust internal: service, concierge, guests, voice, reviews and trust all serialize guest data in the same wave, and foundation code may not import a module. `trust.api.mask` is the module-API door to the same function.

```python
# field classes (rows)                 # policies (cells)
GUEST_NAME, GUEST_CONTACT, HEALTH,     FULL, MASKED, NONE, FIRST_NAME,
TRANSCRIPT, STAFF_PHONE                OWN_DEPARTMENT, FNB_ONLY
MATRIX                                 # read-only live view {class: {role: policy}}, core rows + registered
mask(obj, role, *, field_classes: {dotted path: class}, departments=(), subject_department=None,
     department_field="department") -> masked copy
mask_for_ctx(obj, ctx, *, field_classes, subject_department=None, department_field="department")
role_for_ctx(ctx) -> str | None        # ctx.role ("device" for devices); "owner" for an acting Clefs admin; else None
policy_for(field_class, role, *, departments=(), subject_department=None) -> FULL | MASKED | NONE | FIRST_NAME
column_for(role); first_name(name); register_field_class(name, cells, *, masker=None)
mask_contact_phone(value) -> str        # calling code + at most the last 3 digits ('+971••••••567')
canonical_department(d) -> str          # "Food & Beverage"/"Kitchen" -> "fnb", "Engineering" -> "maintenance", ...
CORE_FIELD_CLASSES; FNB_DEPARTMENTS; DEPARTMENT_ALIASES; COLUMN_ALIASES ({"device": "staff"})
```

| Field class | owner / gm | finance | revenue | dept_head | front_desk | staff, device |
|---|---|---|---|---|---|---|
| `guest_name` | full | full | none | full | full | first name |
| `guest_contact` (phone or email) | full | masked | none | masked | full | none |
| `health` (`preferences.health`, allergies) | full | none | none | F&B only | full | F&B only |
| `transcript` | full | none | none | own department | full | none |
| `staff_phone` | full | none | none | own department | none | none |

- **Fail closed.** An unknown or missing role (an anonymous legacy visitor, a magic session at another hotel, a shift role such as `supervisor`) sees nothing of any class. An unknown field class raises `ValueError`, so a typo fails in tests instead of leaking. A legacy route that must keep its golden shape does not run its rows through `mask` for an anonymous legacy visitor.
- **Shape.** Hidden values become None (the key stays, so client types keep their shape). `masked` uses `privacy.mask_email` for a value containing `@`, else `pii.mask_contact_phone` (the calling code plus at most the last 3 digits; `privacy.mask_phone` now follows the same rule). `first name` drops leading titles (Mr, Dr, Sheikh, ...), reads the PMS order `SMITH, John` (and the Arabic-comma form `المكتوم، محمد`) as the given name, keeps compound given names (`Abdul Rahman`) and never returns the family name. A non-string value under `masked` or `first name` is hidden. The input is never mutated; a pydantic model comes back as a dict.
- **Paths.** Dotted (`guests.name`, `preferences.health`). A list met on the way, at the top or nested, applies the rest of the path to each item. A missing key is skipped.
- **Departments.** `departments` are the viewer's (`HotelCtx.departments`: the membership's, or the device's). F&B means one of `FNB_DEPARTMENTS` after normalising (Service's `Food & Beverage`, `F&B`, `Kitchen`, `In-Room Dining`, ...). Department checks compare canonical keys (`canonical_department`, `DEPARTMENT_ALIASES`), so `Engineering` and `Maintenance`, or `Front Desk` and `Front Office`, are one department. "Own department" compares the subject's department: `subject_department` when given, else `department_field` on the nearest enclosing dict (so a list of requests masks each by its own department). An unknown subject department is nobody's own.
- **Extending.** Trust (or any module) adds a class with `register_field_class(name, {role: policy for every role in permissions.ROLES}, masker=...)`, typically in `on_startup`. The five 5.2 rows cannot be replaced. Re-registering an identical class is a no-op. Trust's matrix test asserts every cell (03 spec 5.2).

## Test fixtures

`outbox`, `fake_ai` and `run_jobs` are in the root `tests/conftest.py` (Part 1, section 14), so every lane has them; `outbox.captured(channel=None)` is a method on the fixture. `tests/foundation/conftest.py` adds, for foundation tests:

- `env(**vars)`: sets variables and reloads settings, undone after the test.
- `ctx_for(role)` returns `(HotelCtx, user)`; `owner_ctx` is the owner's HotelCtx.
- `wa`: the webhook secrets, `sign(body)`, `post(client, payload)`, `message_payload()` and `status_payload()`.

Module lanes can copy the `env`/`ctx_for`/`wa` patterns into their own `tests/modules/<m>/conftest.py`.

---

# Part 3: deploy, packaging and client mirrors (wave 0b)

## Deploy and packaging

- **Python 3.12 everywhere.** `server/.python-version` is `3.12` (Vercel), the `Dockerfile` uses `python:3.12-slim` (VM), and 3.12 is the syntax floor. `tests/foundation/test_w0b_ops.py` fails if the two pins drift, if any `clefs/`, `tests/` or `main.py` file stops parsing with `feature_version=(3, 12)`, or if a real `python3.12` on PATH cannot compile it.
- **requirements.txt declares every direct third-party import.** An import in `clefs/**` must map (via `importlib.metadata.packages_distributions()`) to a distribution listed in `requirements.txt`. Exceptions: imports inside `try/except ImportError`, and `pydantic`/`starlette`, which are FastAPI's own pinned API surface. A lane that needs a new package files a FOUNDATION-REQUEST; the test names the file that imports it.
- **FastAPI is capped `<0.137`.** From 0.137.0, `include_router()` keeps each router as one nested `_IncludedRouter` entry in `app.routes`. Every route walker in the suite now goes through `tests/foundation/route_walk.iter_routes`, which handles both shapes, and the full suite passes on 0.137.0 apart from the cap assertion itself. Lift the cap as its own change (bump, run the suite, drop the cap assertion in `test_w0b_ops.py`), never as a side effect.
- **Vercel cron.** `vercel.json` has `"crons": [{"path": "/internal/cron/tick", "schedule": "* * * * *"}]` and `"regions": ["sin1"]`. Vercel Cron calls production only, with GET and `Authorization: Bearer $CRON_SECRET`. Hobby plans reject crons that run more than once a day: drop the `crons` block there and use an external 1-minute pinger (`GET|POST /internal/cron/tick`, same bearer). See `server/README-deploy.md`. `excludeFiles` also drops `.env` and `.env.*`, so a local `.env` never reaches the function bundle.
- **Secrets files.** `.env`, `.env.compose` and every `.env.*` except `.env.example` are git-ignored, docker-ignored and excluded from the Vercel bundle. Compose reads `.env.compose` for api and worker (`CLEFS_DOTENV=0`); its `environment:` entries override that file.
- **Compose topology.** Services are postgres:16, api, worker and caddy (profile `tls`). Only Caddy publishes public ports; everything else binds to `127.0.0.1`, because uvicorn trusts `X-Forwarded-For` from any peer (`FORWARDED_ALLOW_IPS=*`).
- **Deploy order and settings.**
  - Apply `0014_core_inbound_sender.sql` before the new `clefs.notify` code serves `/webhooks/whatsapp` or `/webhooks/email/resend` (both INSERTs name `inbound_messages.sender_hmac`). `MIGRATE_ON_STARTUP` (the Vercel default) does this on cold start; a VM runs `python -m clefs.migrate up` first.
  - Set `ENV` explicitly in every deployed image, and `CONTACT_HMAC_PEPPER` / `PII_ENC_KEY` in production (`production_gaps()` logs them until then).
  - Set `OAUTH_REDIRECT_BASE` in production (the public API origin, e.g. `https://www.clefs.world/api/clefs`) and register exactly `{OAUTH_REDIRECT_BASE}/public/oauth/{google_business|xero|qbo|zoho}/callback` with Google, Xero, Intuit and Zoho.
  - `INBOUND_EMAIL_DOMAIN` defaults to `in.clefs.world`; set it only for another domain.

## Client console contracts

- **`client/app/lib/types.ts` mirrors the live serializers.** Signal = `signals.to_api`, Action = `actions.to_api` (SELECT * + `expected_value`/`outcome_value`/`terminal`), ActionEvent = `actions.list_events`, AutonomySetting = `actions.get_autonomy`, AutonomyEvent = `actions.list_autonomy_events`, SuppressedAction = the dial-`off` answer of `create_action`, `CreateActionResult = (Action & {created}) | SuppressedAction`. A field the server always emits is required; a NULL-able column is `T | null`.
  - Action carries the Canon 7 + K1 states and the full ledger (`dial_level_at_creation/execution`, `policy_version`, `template_id`, `template_category`, `consent_basis`, `reversal_handle`, `limit_reasons`, attribution, reversal).
  - Signal carries `section`, `cadence`, `rankable`, `impact_minor` + `currency`, `impact_amount`.
  - Constants: `AUTONOMY_LEVELS` (rank order = server `LEVELS`), `AUTO_AUTONOMY_LEVELS`, `AUTONOMY_LEVEL_LABELS/HINTS`, `autonomyRank()`, `SIGNAL_CADENCES`, `OPEN_SIGNAL_STATUSES`, `AUTONOMY_PROMOTION_KIND`, `AUTONOMY_DEMOTED_KIND`; unions `AutonomyEventKind`, `DemotionTrigger`, `AutonomySuggestionStatus`.
- **Guard:** `server/tests/foundation/test_w0b_client.py` (and `test_w0b_integrator.py` for the history row) compares the TS unions with server constants and DB CHECK constraints, and the TS interfaces with real API rows. A new server column or state therefore fails the build until types.ts gains it.
- **Nav gates:** each `nav.ts` entry's `perm` must be registered by the module the entry names (`permissions.owner_of(perm) == module`, `core` for settings). Every `can("...")` / `perm: "..."` in `ConsoleShell.tsx` and `settings/**` must be a registered key.
- **Autonomy console:** `/app/{slug}/settings/autonomy` (tab shown with `core.autonomy.view`).
  - Routes: `GET /hotels/{slug}/autonomy`, `PUT /autonomy/{action_type}` (limits sent only when changed), `POST /autonomy/suggestions/{event_id}/accept|dismiss`, `GET /autonomy/{action_type}/events?limit=20` (a row's History), and `GET /signals?module=core&section=compliance` (with `core.signals.view`) for `core.autonomy_demoted` notices. A notice is mapped to its row by parsing `dedupe_key` `<kind>:<action_type>:<event_id>`.
  - The limits form is generated from `limits_schema` (Pydantic JSON Schema: integer, number, boolean, string, enum, list of strings/ints, anyOf-null means optional, `$ref`/`$defs` resolved). `*_minor` integers are edited in major units of the hotel currency with exact string arithmetic (`majorToMinor`).
  - Gating mirrors the server: levels above `max_level` are disabled; raising into an auto level needs `core.autonomy.raise_auto`; Accept follows `acceptPermFor(to_level)` (manage up to approve, raise_auto above, as `actions.accept_perm_for`). Managers may edit limits at any level; a loosening the server refuses (403) is explained, since only the server can judge `is_looser` for every type.
  - Pure helpers live in `settings/autonomy/dial.ts` (`levelOptions`, `raisesToAuto`, `acceptPermFor`, `canAcceptSuggestion`, `describeAutonomyEvent`, the limits form); the tests run them under node against server output.
- **Kit and shell (Wave 1 integration).**
  - `ApiError.body` keeps the whole parsed error body, so a route's extra fields survive next to `detail` and `code` (Service's 409 `conflict`, the roster's `flags`).
  - `Table`'s `onRowClick` ignores a click or Enter that starts on a control inside the row (`a, button, input, select, textarea, label, summary, [role=button]`, or anything marked `data-row-click="ignore"`); callers no longer need `stopPropagation`.
  - `ConsoleShell` binds a console-wide `N` (no modifier, not while typing or with a modal open) to `/app/{slug}/service/new` for viewers with `service.work` or `service.manage` when Service is on (spec 1.8).
  - Settings tabs: Hotel profile, Team, Devices, Rooms, Imports, Tax (`profit.view`), Modules, Autonomy.
  - `package.json` scripts: `check:claims` (`node scripts/check-claims.mjs`) and `test` (`node --test "app/**/*.test.mjs"`: the device outbox and the google-check view states). CI and the build verifier should run both. To run one file under `app/app/[hotelSlug]/...`, call `node --test <file>` or `node <file>` with the full path: passing the folder makes `[hotelSlug]` a glob character class and finds 0 tests.

## Foundation requests: status after the wave 0b integration

Applied:
- **Permissions**: the contract permissions are in `CORE_PERMISSIONS`; the `actions.install_core_permissions()` shim and `CONTRACT_PERMISSIONS` are gone.
- **Key prefixes**: `registry.KEY_PREFIXES` (Part 2, Keys).
- **Settings**: `CONTACT_HMAC_PEPPER`, `PII_ENC_KEY`, `WA_MAGIC_LINK_TEMPLATE` and the 04 spec 7.6 variables are fields (privacy and notify read them through `get_settings()`); `SERPAPI_COST_PER_SEARCH_USD` is a Decimal field; `.env.example` lists every field, `INBOUND_EMAIL_DOMAIN=in.clefs.world`, and the `AI_PRICE_<MODEL>` pattern.
- **Hotel creation**: reversed in the wave 0b refix. `cli create-hotel` and `seed-demo` no longer pin the dial, and `seed_autonomy()` is gone. Pinning froze each type's default at creation time and depended on which modules were enabled then, against 04 spec 1.4 and 7.4. The seed event is now written by the first `set_autonomy` / `demote` (Part 2, Dial: Rows).
- **Vercel**: the cron block (Part 3, Deploy and packaging).
- **Worker**: logs `production_gaps()` at startup.
- **Jobs**: `clefs.core.serpapi` is a core registrant.
- **Autonomy**: `PROMOTION_SKIPS_UNLIMITED = True` (04 spec 1.5); the accept route needs `manage` and defers to `accept_perm_for`; the history route; the console gates Accept by target and no longer locks a manager out of limits at auto levels.
- **Route walkers** recurse into nested routers (`tests/foundation/route_walk.py`).
- **Migration 0011** is back to its applied bytes (sha256 `92ecb57f...`); K1 ships in 0013.
- **Spec text**: Canon K1 names 0013 as its migration and keeps 0011 as applied; Canon K3 marks `core/serpapi.py` and 0018 as built (Demand, Visibility and Reviews call `core.serpapi.search`); `01-foundation.md` says Python 3.12. `tests/foundation/test_w0b_refix_serverops.py` keeps those texts true, and keeps the `requirements.txt` FastAPI-cap comment in line with `route_walk.iter_routes`.

Open:
- **Finance lane**: register `finance.connect` for owner and finance in `modules/finance/module.py` (04 spec 7.1).
- **Brief lane**: mint brief and action magic links with `scope={"action_ids": [the surfaced ids]}`.
- **Visibility, finance and settings UI**: OAuth connect buttons (Part 2, OAuth: client use).
- **Magic-link timing on Vercel** (security-10): `/auth/magic/request` does its lookup and delivery in a Starlette background task. If the `@vercel/python` adapter waits for the whole ASGI call before returning (the classic `run_until_complete` path does), the timing difference comes back there. Check on a preview deploy; if it leaks, enqueue a `core.auth.magic_request` task in the request transaction (an auth registrant in `jobs.CORE_REGISTRANTS`). The cost is up to a minute of delivery latency on Vercel, where the per-minute cron drains the queue.
- **Meta 130429 backoff**: the code holds the whole sender 30 s x 2^n; the 04 spec F4 says 6 s x 2^n. Kept as implemented (a throughput limit is per number, so a longer hold is the safer reading); the spec owner should amend F4.
- **FastAPI cap**: see Deploy and packaging.
- **Dial revocation (04 spec 1.1 step 5)**: the spec says a revoked dial approval "goes back to `proposed`". The code places it by the new level instead (Part 2, Dial: Re-resolution). `suggest` stays off the brief, `observe` becomes `observed`, and `off` becomes `cancelled` with `dial_off`, as 1.1's own table implies for those levels. The spec owner should amend step 5 to match.

## Foundation requests: status after the Wave 1 integration

Applied:
- **Permissions**: `core.stays.view` (analysts), `core.stays.manage` (owner, gm; the stays route tests pin finance out) and `core.ingest.manage` (owner, gm, finance, revenue). The stays and ingest routers use them; ingest's role fallback is gone.
- **Wiring**: Service's manifest lists `*config_api.ops_routers()` and imports `service.jobs` (before this, no Service job or metric was registered in a real app); `service.tracker_push` runs every minute; the api doors `recovery_cost`, `has_open_complaint`, `on_duty` delegate; a complaint opens a LEARN case. Concierge lists `router_public` directly and sets `settings_model=ConciergeSettings`.
- **Auth**: `devices.close_session` (public; Service's auto-logout uses it); a device write naming an on-shift person touches `device_sessions.last_activity_at` (throttled, GETs excluded so feed polling never keeps an abandoned session alive).
- **Core**: room types are created on first use by `PUT /rooms` (FK failures are 400 `invalid_room_type`) and editable through `GET|PUT /room-types`; `privacy.mask_phone` follows `pii.mask_contact_phone`; `ai.structured(temperature=)`; `InboundEmail.spf` / `.dkim`; `TRIPADVISOR_API_KEY` in Settings and `.env.example`; `jobs.MODULE_OFF_EXEMPT` keeps `trust.retention_purge` and `trust.audit_chain_verify` running for a hotel that switched Trust off; the stale `money.py` TODO is gone.
- **Client**: settings tabs, `ApiError.body`, the table row-click fix, the `N` hotkey, Clarity on the new marketing pages (never `/google-check/r/`), `check:claims` and `test` scripts. `robots.ts` already disallowed `/device/`.
- **Tests**: the stub-default assertions for `concierge.propose_kb_entry`, `guests.can_send_marketing` and `guests.record_own_contact` follow the built bodies; the spec `department_hint` keys are pinned next to the legacy aliases; the `handlers` fixture (now in `tests/foundation/conftest.py`) sets module handlers aside; new cross-lane functions are in `FROZEN_API`.
- **Cross-lane fixes**: guests pins `whatsapp_updates` to its own wording (Trust's newer generic text did not name the hotel); the voice console reads the voice routes' wrapped payloads and calls `/extensions/import` and `/webhook-events`; Service's config console sends each section under its own key; the roster accepts the `start_at`/`end_at` instants it returns and the console reads the 409 flags from the error body; the device outbox sends LEARN ticks with `PATCH /device/service/recovery-cases/{id}`; `GET /service/events?cursor=latest`; the stay context carries `hotel.whatsapp_number` and `guest.language`; Profit Guard serves the tax routes; concierge serves `POST /kb/test`.

Declined or deferred (with the reason):
- **`MENU` import kind**: applied in the Wave 1 gaps pass together with Upsell's importer moving onto `clefs.core.ingest` (0024, Core domain above).
- **vitest / jsdom**: `node --test` already runs the pure client tests; a DOM runner (the staff-page chime regression) needs a dependency install and a lockfile change, which should be its own reviewed change.
- **Staff-link sunset redirect and setting**: phase 2 of the retirement, after every hotel has devices paired; redirecting now would break working personal links.
- **Per-segment `<html lang dir>`**: needs a root-layout split (multiple root layouts in Next 16); the Arabic pages keep `dir`/`lang` on their `<main>` meanwhile.
- **`check-claims` surfaces for `/dpa`, `/subprocessors`, `/privacy`**: no `<Claim>` is placed there; the marketing owner decides the surface when one is.
- **Optional hooks**: `stays.dispatch_soon`, a Module lifespan hook for the MCP SDK, `core.tax.fnb_rates`, a platform-level compliance alert channel, and a privacy registration that joins to the tenant key. None blocks a shipped flow; each changes shared behaviour and belongs in its own change.
- **Lane asks to other lanes**: all built by the end of Wave 1 and its gaps pass (`guests.room_for_sender`, `notify.cancel_queued`, a keyed `service.create_request`, `service.on_request_state`, `brief.wa_billing_status`, `/device/service/push-subscriptions`, `/device/service/sos/{id}/cancel`, `GET /concierge/transcripts`, `GET /amenities/items`).


## Foundation requests: status after the Wave 2 integration

Applied:
- **Profit Guard wiring**: the manifest imports `imports`, `detectors` and `jobs` in that order (raw commit handlers, then the detect hook, then the auto-verify hook; both wrappers carry flags so neither wraps twice), lists the ledger, disputes, tax-checks, economics, detectors and public-audit routers, and grants `profit.approve_disputes` to owner and finance. Detector recommended actions name registered types (`pg.dispute`, `pg.config_fix`, `pg.vcc_charge`, `pg.mark_no_show`, `pg.payout_query`, else `core.task`); `api.dispute_packet` is the Brief door.
- **Inquiry Guard wiring**: the quote-to-cash routers are listed in `MODULE.routers` (console before the intake router) instead of being mounted onto the intake routers from `jobs.py`; `InquirySettings.quotes` keeps the quote policy across `PUT /settings`; `POST /{id}/messages` sends a person's reply (WhatsApp inside the 24 h window, else email with the `+inq-<ref>` Reply-To) and counts as the first response.
- **Core**: `ingest.batch_view` exported (Profit Guard dropped its copy); `stays.delete_matches`; `auth.magic.peek`; generic COMP_RATES spellings for long-format rate-shop exports; the demo hotel has coordinates.
- **Settings and `.env.example`**: `GBP_PROJECT_QPM`, `TICKETMASTER_API_KEY`, `OPEN_METEO_API_KEY`, `MAKCORPS_API_KEY`, `DEMAND_XOTELO_ENABLED`, `FOUNDER_NOTIFY_EMAIL`, `FOUNDER_NOTIFY_WHATSAPP`, `VIS_LEAD_WA_TEMPLATE`, `PG_AUDIT_LEAD_WA_TEMPLATE`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`. Modules still read them through `env()`, which returns the same values.
- **Client**: `OTA_AUDIT_LIVE = true` (the audit page and `/public/audits` both exist; the claims linter is clean); `/free-ota-audit/r/` disallowed in `robots.ts`; `/q/:path*` sends `X-Frame-Options: DENY` and `frame-ancestors 'none'`; UI kit `MoneyText tone="cost"`, `.cx-sr-only`, `DateRange direction="settled"` and `lastNSettledDays`, and `valueFormat` on `ColumnChart` / `HBarChart`.
- **Tests**: the inquiries stub assertion follows the built `create_inquiry`; `FROZEN_API` pins `guests.room_for_sender`, `profit_guard.dispute_packet`, `brief.wa_billing_status`, `ask.answer_question` and `ask.askable_metrics`; the detectors route test walks routes through `route_walk`.
- **Cross-lane fixes**: Guests' billing gate reads `brief.wa_billing_status` `ready: None` as unknown (it was treated as not ready, which switched every WhatsApp journey off); `guests.marketing_audience` honours Demand's `segment.countries`; Guests and Inquiries cancel outbox retries through `notify.cancel_queued`; Upsell's menu import passes the actor and filename to the ingest commit; Reviews' `VendorError` keeps `.status` 502 (the vendor status is `.http_status`); the inquiries SLA signal and the contract-review signal name `core.task`; the inquiries thread hides only its AI draft button when the draft route is missing.

Declined or deferred (with the reason):
- **OTA_STATEMENT `charges` list**: canonical rows are flat CSV fields; a per-line list needs a structured field type and a source that carries Booking's `price_breakdown` (the Reservations API, not the statement export). The `pfg_ota_statement_lines.charges` column stays ready.
- **Per-module alias directories**: no lane ships its own YAML yet; the generic map gained the spellings asked for. Vendor-specific `lighthouse_rates.yaml` / `rategain_rates.yaml` need a real export (their wide grids go through Demand's own import either way).
- **Lane-internal hook call in `imports._hotel_only`**: the wrapper hooks now install in a fixed order from the manifest, so the extra call would be redundant (the job's dedupe key would drop it).
- **`consents` rows without a hotel**, **several PII specs per table** and **redacting a JSONB NOT NULL column to `{}`**: each changes Trust's retention catalogue (data classes are keyed `<module>.<table>`) and the consent ledger's tenant model; Inquiries purges its second rule itself and leads keep their consent on the lead row until Trust takes this up.
- **`service_requests_v1` subtype and description**: the view is a frozen 13-column contract that `0012` must still be able to replace; Amenities keeps its read-only join to `staff_requests`.
- **Magic purpose `ask`**: widening every magic session to `ask.view` is a security change for its own review; the per-answer scope check in `ask/router.py` keeps WhatsApp answer links working.
- **UI kit**: `CalendarHeatmap` badges and fixed `max`, grouped `ColumnChart`, `ActionBar.assignRole`, `ConfidenceDots` and `app/lib/types.ts` Visibility types: each lane has a working local version; lifting them is a UI-kit change with its own review.
- **Notify and AI**: a document media header signed at delivery (Brief signs a 6 h link meanwhile), `ai.tool_call` usage (Ask sums `ai_calls`), `trust.api.register_eval` (Trust's lane), per-hotel `frame-ancestors` for `/w/inquiry/*` in `proxy.ts` (needs a cached per-hotel fetch in the edge proxy), and passing printed total lines to commit handlers.
- **`demand.record_bar_evidence` in `FROZEN_API`**, **`service.on_duty(department=)`** and a staff list readable with `amenities.issue`: optional, no caller blocks on them.
- **`pywebpush` and `ruff`**: new dependencies belong in their own change with a lockfile update; push stays log-only without `pywebpush`.
- **Preview deployments and CSRF**: configuration, not code; add each preview origin to `WEB_ORIGINS` or `CORS_ALLOW_ORIGIN_REGEX`.
- **Module demo seeds** (`demo.seed_demo` per module, curated calendar sync): the Wave 3 demo lane owns `seed()`.
