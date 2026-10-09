"""
Typed environment settings.

Plain pydantic BaseModel filled from os.environ, so there is no extra
pydantic-settings dependency. Every variable the platform knows about is
declared here once; `env(name, default)` covers anything unforeseen (for example
AI_MODEL_<TASK> overrides) without a settings edit.

FROZEN after the foundation wave (module agents leave FOUNDATION-REQUEST notes).

Database URL precedence is deliberate: DATABASE_URL, then the legacy
PSQL_DATABASE_URL, then the DB_* parts. server/.env historically holds the
production PSQL_DATABASE_URL, so any local run that exports DATABASE_URL wins
over it, and the test harness forces all three to a local database.
DATABASE_URL_UNPOOLED (migrations, LISTEN, the scheduler lock) is used only
when it pairs with that effective URL: a .env copy of Neon's pooled/unpooled
pair must never send `DATABASE_URL=<local> python -m clefs.migrate up` to the
production database, and neither may a stale exported direct URL of another
project that happens to share Neon's default database name.

Production detection fails closed: ENV unset on Vercel means production (or
"preview"), and only an explicitly development/test process, or an
unconfigured one on a local database outside any container or hosting
platform, may run with the public DEV_SECRET_KEY or non-Secure cookies.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Literal
from urllib.parse import quote, unquote, urlparse

from pydantic import BaseModel, PrivateAttr

# ─── .env loading ───
# CLEFS_DOTENV=0 disables it (the test harness sets that). load_dotenv never
# overrides variables that are already set in the process environment.

_DOTENV_LOADED = False
# Names whose value came from server/.env rather than the process environment
# (the unpooled-URL pairing rule below needs to know).
_DOTENV_KEYS: set[str] = set()


def _load_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    if os.getenv("CLEFS_DOTENV", "1").lower() in ("0", "false", "no", "off"):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is declared
        return
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    before = set(os.environ)
    load_dotenv(os.path.join(here, ".env"))
    _DOTENV_KEYS.update(set(os.environ) - before)


def from_dotenv(name: str) -> bool:
    """True when `name` was filled from server/.env, not exported by the operator."""
    return name in _DOTENV_KEYS


class RemoteDatabaseRefused(RuntimeError):
    """A remote database URL was picked up from server/.env without an explicit opt-in."""


def _refuse_dotenv_remote(name: str, url: str) -> str:
    """Never let a stray local command reach a remote database through server/.env.

    server/.env historically carries the PRODUCTION Neon URL for the legacy
    app. An empty `DATABASE_URL=` on a CLI invocation (for example
    `DATABASE_URL= python -m clefs.migrate verify`) used to fall straight
    through to it. Deployments are unaffected: platforms export their
    variables into the process environment, and a URL the operator exported
    in the shell is honoured as before. Only a remote URL that arrived from
    the .env file needs the explicit CLEFS_ALLOW_DOTENV_REMOTE_DB=1 opt-in.
    """
    if not from_dotenv(name) or is_local_db(url):
        return url
    if os.getenv("CLEFS_ALLOW_DOTENV_REMOTE_DB", "").lower() in ("1", "true", "yes", "on"):
        return url
    raise RemoteDatabaseRefused(
        f"{name} from server/.env points at a remote database; refusing to connect. "
        "Export DATABASE_URL=postgresql://localhost/<db> for local work, or set "
        "CLEFS_ALLOW_DOTENV_REMOTE_DB=1 if you really mean to use the remote database."
    )


def env(name: str, default: str | None = None) -> str | None:
    """Raw accessor for variables not modelled below."""
    _load_dotenv_once()
    val = os.getenv(name)
    return default if val is None or val == "" else val


def _bool(name: str, default: bool) -> bool:
    val = env(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    val = env(name)
    return default if val is None else int(val)


def _csv(name: str, default: str = "") -> list[str]:
    raw = env(name, default) or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


def _int_or_none(name: str) -> int | None:
    val = env(name)
    return None if val is None else int(val)


def _decimal(name: str, default: str | None) -> Decimal | None:
    # Budgets and prices are money (Canon 2): Decimal, never float. A typo
    # fails at startup naming the variable (decimal's own error names nothing).
    val = env(name, default)
    if val is None:
        return None
    try:
        return Decimal(val.strip())
    except InvalidOperation as exc:
        raise ValueError(f"{name}={val!r} is not a decimal number") from exc


DEV_SECRET_KEY = "clefs-dev-insecure-secret-key-do-not-use-in-production"
DEFAULT_WEB_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
DEFAULT_PROD_WEB_ORIGINS = "https://www.clefs.world,https://clefs.world"
PROD_ENVS = ("production", "prod")
DEV_ENVS = ("development", "dev", "test", "local")
LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})
# Canon 19: OTA relay addresses (never marketed to). Booking.com and Expedia;
# Agoda's alias domain is unknown (04 spec A2).
DEFAULT_OTA_ALIAS_DOMAINS = "guest.booking.com,m.expediapartnercentral.com"
# The verifier named on the sender verdicts of inbound mail: the converter
# (clefs.notify.inbound.from_resend) writes them from Resend's API under this
# name, and by default it is the only verifier whose verdicts count.
RESEND_INBOUND_VERIFIER = "inbound.resend.com"

# ─── Hosted-process detection ───
# An unset ENV defaults to "development", which is only believable on a
# developer's machine. These variables are set by hosting platforms (Heroku,
# Render, Railway, Fly, Cloud Run, Kubernetes, ECS, Lambda, Azure App Service)
# and /.dockerenv exists inside every Docker container, including our own
# Dockerfile image; any of them means "deployed", so the local-database shortcut to
# is_dev no longer applies and the operator must say ENV=development.
HOSTED_MARKERS = (
    "DYNO",
    "RENDER",
    "RAILWAY_ENVIRONMENT",
    "FLY_APP_NAME",
    "K_SERVICE",
    "KUBERNETES_SERVICE_HOST",
    "ECS_CONTAINER_METADATA_URI_V4",
    "AWS_LAMBDA_FUNCTION_NAME",
    "WEBSITE_SITE_NAME",
)
DOCKERENV_PATH = "/.dockerenv"


def running_hosted() -> bool:
    """Is this process on a hosting platform or in a container?"""
    return any(os.getenv(k) for k in HOSTED_MARKERS) or os.path.exists(DOCKERENV_PATH)


def db_host(url: str | None) -> str:
    """Host of a postgres URL ("" for a unix socket or an unparsable URL)."""
    try:
        host = urlparse(url or "").hostname or ""
    except ValueError:
        return ""
    return "" if host.startswith("/") else host.lower()


def db_name(url: str | None) -> str:
    try:
        return (urlparse(url or "").path or "").lstrip("/")
    except ValueError:
        return ""


def is_local_db(url: str | None) -> bool:
    return db_host(url) in LOCAL_DB_HOSTS


def _credentials(url: str | None) -> tuple[str, str]:
    try:
        p = urlparse(url or "")
        return unquote(p.username or ""), unquote(p.password or "")
    except ValueError:
        return "", ""


def _same_role(a: str, b: str) -> bool:
    # Supabase's pooler names the role "<role>.<project ref>" where the direct
    # endpoint says "<role>"; Neon uses the same role on both endpoints.
    return a == b or a.split(".", 1)[0] == b or b.split(".", 1)[0] == a


def urls_pair(pooled: str, unpooled: str) -> bool:
    """Do the pooled and unpooled URLs name the same database? Providers put
    the pooler on a different host (Neon's "-pooler" label, Supabase's
    pooler domain), so hosts are not compared directly; the database name and
    locality are. A local pooled URL never pairs with a remote unpooled one.

    Every Neon project calls its database "neondb", so for remote URLs the
    credentials must match too: a provider's pooled and direct URLs share the
    role's password, while a staging and a production project never do. That
    stops a stale exported production direct URL from pairing with a staging
    DATABASE_URL (migrations and the scheduler lock would go to production)."""
    if db_name(pooled) != db_name(unpooled) or is_local_db(pooled) != is_local_db(unpooled):
        return False
    if is_local_db(pooled):
        return True
    (u1, p1), (u2, p2) = _credentials(pooled), _credentials(unpooled)
    return p1 == p2 and _same_role(u1, u2)


class Settings(BaseModel):
    # ── Core ──
    ENV: str = "development"
    SECRET_KEY: str = DEV_SECRET_KEY
    WEB_ORIGINS: list[str] = []
    PUBLIC_API_ORIGIN: str = "http://localhost:8000"
    CORS_ALLOWED_ORIGINS: list[str] = []
    CORS_ALLOW_ORIGIN_REGEX: str | None = None
    COOKIE_SECURE: bool = False
    TRUST_FORWARDED_FOR: bool = False
    SESSION_TTL_DAYS: int = 30
    LOG_LEVEL: str = "INFO"
    CLEFS_DISABLED_MODULES: list[str] = []
    CLEFS_ONLY_MODULES: list[str] = []

    # ── Database ──
    DATABASE_URL: str | None = None
    PSQL_DATABASE_URL: str | None = None
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "aria"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""
    DATABASE_URL_UNPOOLED: str | None = None
    DB_POOL_MAX: int = 10
    MIGRATE_ON_STARTUP: bool = False

    # ── Runtime ──
    EVENTS_BACKEND: Literal["notify", "local"] = "notify"
    CRON_SECRET: str | None = None
    WORKER_TOKEN: str | None = None
    VOICE_ENABLED: bool = False

    # ── AI ──
    # Google Gemini: the Developer API with GEMINI_API_KEY, or Vertex AI
    # (GOOGLE_GENAI_USE_VERTEXAI=true) as the Cloud Run service identity.
    GEMINI_API_KEY: str | None = None
    GOOGLE_GENAI_USE_VERTEXAI: bool = False
    GOOGLE_CLOUD_PROJECT: str | None = None
    GOOGLE_CLOUD_LOCATION: str = "global"
    VOICE_WEBHOOK_SECRET: str | None = None
    AI_ENABLED: bool = False
    AI_MODEL_DEFAULT: str = "gemini-3.5-flash"
    AI_MODEL_FAST: str = "gemini-3.1-flash-lite"
    AI_LOG_CONTENT: bool = False
    REALTIME_MODEL: str = "gemini-2.5-flash-native-audio-preview-09-2025"
    PERPLEXITY_API_KEY: str | None = None
    SERPAPI_KEY: str | None = None
    GOOGLE_MAPS_API_KEY: str | None = None
    # Reviews: the Tripadvisor Content API (5 newest reviews per location).
    TRIPADVISOR_API_KEY: str | None = None

    # ── WhatsApp / Meta ──
    WA_ACCESS_TOKEN: str | None = None
    WA_APP_SECRET: str | None = None
    WA_VERIFY_TOKEN: str | None = None
    WA_PLATFORM_PHONE_NUMBER_ID: str | None = None
    WA_BUSINESS_ACCOUNT_ID: str | None = None
    WA_API_VERSION: str = "v23.0"
    META_APP_SECRET: str | None = None
    # The approved authentication template magic links fall back to.
    WA_MAGIC_LINK_TEMPLATE: str = "clefs_magic_link"
    # hub.verify_token of the Lead Ads (leadgen) webhook (04 spec 4.3).
    META_LEADGEN_VERIFY_TOKEN: str | None = None
    # Free service/utility messages per business number per month from
    # 2026-10-01 (04 spec 3.10, A4).
    WA_SERVICE_FREE_ALLOWANCE: int = 0

    # ── Privacy (clefs.core.privacy) ──
    # Unset outside production: derived from SECRET_KEY. Production refuses
    # to derive them (NotConfigured), so production_gaps() names them at startup.
    CONTACT_HMAC_PEPPER: str | None = None
    PII_ENC_KEY: str | None = None  # 32 bytes, base64 or hex
    # Canon 19 OTA relay domains; their addresses are never marketed to.
    OTA_ALIAS_DOMAINS: list[str] = DEFAULT_OTA_ALIAS_DOMAINS.split(",")

    # ── Notifications ──
    NOTIFY_MODE: Literal["live", "log", "capture"] = "log"
    # Email goes through Resend both ways. The same key sends mail and reads
    # received mail back (the webhook carries metadata only), so it needs
    # full access, not sending access.
    RESEND_API_KEY: str | None = None
    RESEND_WEBHOOK_SECRET: str | None = None  # "whsec_..." signing secret of the email.received webhook
    EMAIL_FROM: str | None = None
    EMAIL_REPLY_TO: str | None = None  # where replies go when a message names no Reply-To
    INBOUND_EMAIL_DOMAIN: str | None = None
    INBOUND_EMAIL_ENABLED: bool = False
    # Who may vouch for an inbound sender (clefs.notify.inbound.trusted_headers).
    # Authentication-Results and Received-SPF travel inside the message, so a
    # sender can write their own "dmarc=pass". Only a verdict that names one
    # of these verifiers counts, and only when exactly one such copy exists
    # (a forged copy next to the real stamp makes the verdict void). The
    # inbound converter drops every verdict header the message carried and
    # writes one of each from Resend's API, naming RESEND_INBOUND_VERIFIER.
    # SPF: hosts in the receiver= of the Received-SPF stamp.
    INBOUND_EMAIL_SPF_VERIFIERS: list[str] = [RESEND_INBOUND_VERIFIER]
    # Authentication-Results authserv-ids.
    INBOUND_EMAIL_AUTHSERV_IDS: list[str] = [RESEND_INBOUND_VERIFIER]
    SMS_PROVIDER: str = "msg91"
    SMS_ENABLED: bool = False
    SMS_AUTH_KEY: str | None = None
    SMS_TEMPLATE_ID: str | None = None
    TWILIO_ACCOUNT_SID: str | None = None
    TWILIO_AUTH_TOKEN: str | None = None
    TWILIO_FROM: str | None = None

    # ── Files ──
    FILE_STORE: Literal["db", "s3"] = "db"
    FILE_MAX_BYTES: int = 25 * 1024 * 1024
    S3_BUCKET: str | None = None
    S3_REGION: str | None = None

    # ── Demand / visibility data ──
    PREDICTHQ_TOKEN: str | None = None
    RATE_SHOP_PROVIDER: str | None = None
    RATE_SHOP_API_KEY: str | None = None
    GBP_OAUTH_CLIENT_ID: str | None = None
    GBP_OAUTH_CLIENT_SECRET: str | None = None
    GOOGLE_ADS_LEAD_KEY: str | None = None
    # CrUX and PageSpeed Insights (visibility site health, 04 spec 5.6).
    GOOGLE_API_KEY: str | None = None
    # Absolute origin of core.oauth callbacks (None: PUBLIC_API_ORIGIN).
    OAUTH_REDIRECT_BASE: str | None = None
    # core.serpapi (04 spec F5): the global month budget across every purpose,
    # and the separate cap for the public lead magnet (purpose public_check).
    # None = no global ceiling; the per-hotel visibility budgets still apply.
    SERPAPI_MONTHLY_BUDGET_USD: Decimal | None = None
    PUBLIC_SERPAPI_MONTHLY_CAP: int | None = None
    # USD per search written to the serpapi_calls ledger (the Developer plan's
    # $75 per 5,000 searches).
    SERPAPI_COST_PER_SEARCH_USD: Decimal = Decimal("0.015")
    # Visibility AI radar (04 spec 5.5, 5.10): kill switch, Perplexity model,
    # and the Canon 20 legal gate that must be flipped explicitly.
    VISIBILITY_AI_ENABLED: bool = True
    PERPLEXITY_MODEL: str = "sonar"
    PERPLEXITY_TERMS_APPROVED: bool = False
    # Public checks and AI snapshots are budgeted globally (04 spec 5.9).
    PUBLIC_CHECK_DAILY_CAP: int = 20
    SNAPSHOT_DAILY_CAP: int = 10
    SNAPSHOT_MONTHLY_BUDGET_USD: Decimal = Decimal("50")
    # Clefs' Google Cloud quota for the Business Profile APIs, queries per
    # minute (0 = access not approved yet, 300 = approved; 04 spec 5.4).
    GBP_PROJECT_QPM: int = 0
    # Demand data sources (Wave 2): Ticketmaster Discovery, the commercial
    # Open-Meteo key (blank = the free non-commercial endpoint), MakCorps
    # rate shopping, and the unofficial Xotelo feed (off unless flipped).
    TICKETMASTER_API_KEY: str | None = None
    OPEN_METEO_API_KEY: str | None = None
    MAKCORPS_API_KEY: str | None = None
    DEMAND_XOTELO_ENABLED: bool = False

    # ── Public lead magnets (Google check, OTA audit, demand preview) ──
    # Where the founder hears about a new lead (unset = nobody is told), and
    # the Meta-approved marketing templates of the WhatsApp follow-ups.
    FOUNDER_NOTIFY_EMAIL: str | None = None
    FOUNDER_NOTIFY_WHATSAPP: str | None = None
    VIS_LEAD_WA_TEMPLATE: str = "clefs_google_check_followup"
    PG_AUDIT_LEAD_WA_TEMPLATE: str = "clefs_audit_lead"  # params [hotel, found and lines to confirm, findings]

    # ── Web Push (service devices) ──
    # VAPID keys for staff device push; without the private key and subject
    # service/push.py stays log-only (pywebpush is optional, imported lazily).
    VAPID_PUBLIC_KEY: str | None = None
    VAPID_PRIVATE_KEY: str | None = None
    VAPID_SUBJECT: str | None = None

    # ── Finance integrations ──
    XERO_CLIENT_ID: str | None = None
    XERO_CLIENT_SECRET: str | None = None
    QBO_CLIENT_ID: str | None = None
    QBO_CLIENT_SECRET: str | None = None
    QBO_ENV: Literal["sandbox", "production"] = "sandbox"
    ZOHO_CLIENT_ID: str | None = None
    ZOHO_CLIENT_SECRET: str | None = None

    # ── Payments (Inquiry Guard payment links) ──
    PAYMENT_PROVIDER: Literal["none", "ngenius", "telr", "stripe", "tap"] = "none"
    NGENIUS_API_KEY: str | None = None
    NGENIUS_OUTLET_REF: str | None = None
    NGENIUS_BASE_URL: str = "https://api-gateway.sandbox.ngenius-payments.com"
    NGENIUS_WEBHOOK_SECRET: str | None = None
    TELR_STORE_ID: str | None = None
    TELR_AUTH_KEY: str | None = None
    TELR_WEBHOOK_SECRET: str | None = None
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_PUBLISHABLE_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    TAP_SECRET_KEY: str | None = None
    TAP_PUBLISHABLE_KEY: str | None = None
    TAP_WEBHOOK_SECRET: str | None = None
    # API bases (04 spec 4.8), overridable so sandbox tests hit a stub.
    TELR_API_BASE: str = "https://secure.telr.com"
    STRIPE_API_BASE: str = "https://api.stripe.com"
    TAP_API_BASE: str = "https://api.tap.company"

    # ─── Derived values ───
    # Whether ENV was set by the operator (False: defaulted from the platform).
    _env_explicit: bool = PrivateAttr(default=True)
    # Whether the process runs on a hosting platform or in a container.
    _hosted: bool = PrivateAttr(default=False)
    # Configuration notes load_settings() made (for example an ignored
    # DATABASE_URL_UNPOOLED); validate_for_startup() returns them as warnings.
    _notes: list[str] = PrivateAttr(default_factory=list)

    @property
    def is_production(self) -> bool:
        return self.ENV.strip().lower() in PROD_ENVS

    @property
    def database_is_local(self) -> bool:
        return is_local_db(self.database_url)

    @property
    def is_dev(self) -> bool:
        """May this process relax production guards (the public dev
        SECRET_KEY, non-Secure cookies, logging magic links)? Only when ENV
        explicitly says development/test, or when ENV is unset off Vercel (so
        it defaulted to development), the database is local, and nothing says
        the process is deployed (a container or a hosting platform: a VM image
        talking to Postgres on the same host is still production)."""
        if self.is_production:
            return False
        name = self.ENV.strip().lower()
        if self._env_explicit:
            return name in DEV_ENVS
        return name == "development" and self.database_is_local and not self._hosted

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return _refuse_dotenv_remote("DATABASE_URL", self.DATABASE_URL)
        if self.PSQL_DATABASE_URL:
            return _refuse_dotenv_remote("PSQL_DATABASE_URL", self.PSQL_DATABASE_URL)
        pw = f":{quote(self.DB_PASSWORD)}" if self.DB_PASSWORD else ""
        return f"postgresql://{quote(self.DB_USER)}{pw}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def database_url_unpooled(self) -> str:
        # PgBouncer transaction mode breaks LISTEN, advisory locks and
        # multi-statement migrations, so those use the direct endpoint, but
        # only when it names the same database as the effective URL.
        pooled = self.database_url
        if self.DATABASE_URL_UNPOOLED and urls_pair(pooled, self.DATABASE_URL_UNPOOLED):
            return self.DATABASE_URL_UNPOOLED
        return pooled

    @property
    def web_base_url(self) -> str:
        """Absolute origin used in links sent to people (magic links, pairing)."""
        return self.WEB_ORIGINS[0] if self.WEB_ORIGINS else "http://localhost:3000"

    def validate_for_startup(self) -> list[str]:
        """Raise on fatal misconfiguration; return warnings otherwise.

        Fails closed: the public DEV_SECRET_KEY (or any key under 32 chars)
        lets anyone forge stream/upload tickets, QR keys and button refs, so
        only an is_dev process may run with it."""
        warnings: list[str] = list(self._notes)
        weak_key = self.SECRET_KEY == DEV_SECRET_KEY or len(self.SECRET_KEY) < 32
        if weak_key and not self.is_dev:
            raise RuntimeError(
                f"SECRET_KEY must be set to 32+ random characters (ENV={self.ENV}, database host "
                f"{db_host(self.database_url) or 'local'}). Only ENV=development or ENV=test may use the development key."
            )
        if self.is_production and not self.COOKIE_SECURE:
            raise RuntimeError("COOKIE_SECURE must be true in production (session cookies need Secure)")
        if not self.COOKIE_SECURE and not self.is_dev:
            # Explicitly switched off on a non-production deploy (preview,
            # staging): allowed for plain-HTTP test rigs, but never silently.
            warnings.append(
                f"COOKIE_SECURE is false outside development (ENV={self.ENV}): session cookies may travel over plain HTTP"
            )
        if weak_key:
            warnings.append("SECRET_KEY is the public development key; set a real one before exposing this process")
        return warnings

    def production_gaps(self) -> list[str]:
        """Features a production process will refuse at first use because a
        key is missing (logged at startup by create_app; not fatal, so a
        deploy without them still serves everything else)."""
        if not self.is_production:
            return []
        # clefs.core.privacy refuses to derive these in production.
        gaps = [
            f"{name} is not set: guest contact hashing and sealed PII are unavailable in production"
            for name in ("CONTACT_HMAC_PEPPER", "PII_ENC_KEY")
            if not getattr(self, name)
        ]
        # core.oauth binds the callback to the browser's clefs_session cookie,
        # which lives on the web origin (set through the Next /api/clefs
        # rewrite, no Domain attribute). A redirect URI on any other host never
        # carries it, so every connect would end in oauth_error=session_mismatch.
        oauth_apps = ("GBP_OAUTH_CLIENT_ID", "XERO_CLIENT_ID", "QBO_CLIENT_ID", "ZOHO_CLIENT_ID")
        if any(getattr(self, k, None) for k in oauth_apps):
            base = self.OAUTH_REDIRECT_BASE or self.PUBLIC_API_ORIGIN
            web_hosts = {urlparse(o).hostname for o in self.WEB_ORIGINS}
            if urlparse(base).hostname not in web_hosts:
                gaps.append(
                    "OAUTH_REDIRECT_BASE is not on a web origin (set it to <web origin>/api/clefs): OAuth callbacks "
                    "cannot see the session cookie and every connect fails with session_mismatch"
                )
        # PUBLIC_API_ORIGIN builds links strangers fetch: WhatsApp document
        # links (Meta downloads them), guest unsubscribe links (a compliance
        # link), the voice MCP server URL handed to the call bridge and the OAuth base.
        # The localhost default (or plain http) breaks every one of them.
        origin = urlparse(self.PUBLIC_API_ORIGIN or "")
        if origin.scheme != "https" or origin.hostname in (None, "localhost", "127.0.0.1", "0.0.0.0"):
            gaps.append(
                f"PUBLIC_API_ORIGIN is {self.PUBLIC_API_ORIGIN!r} (set the public https API origin): WhatsApp "
                "document links, guest unsubscribe links and the voice MCP URL point nowhere"
            )
        # require_service("cron") answers 503 without either token, so Vercel
        # Cron calls fail and no scheduled job (reminders, syncs, briefs) runs.
        if not self.CRON_SECRET and not self.WORKER_TOKEN:
            gaps.append("CRON_SECRET is not set (nor WORKER_TOKEN): every cron call is refused and no job ever runs")
        return gaps


def _default_env(on_vercel: bool) -> str:
    # Vercel sets VERCEL_ENV (production | preview | development). A deploy
    # that never set ENV is production there, not a development process.
    if not on_vercel:
        return "development"
    vercel_env = (os.getenv("VERCEL_ENV") or "production").lower()
    return "production" if vercel_env == "production" else "preview"


def load_settings() -> Settings:
    _load_dotenv_once()
    on_vercel = bool(os.getenv("VERCEL"))
    # "production " (a stray space in a dashboard) must still be production.
    explicit_env = (env("ENV") or "").strip() or None
    env_name = explicit_env or _default_env(on_vercel)
    prod = env_name.lower() in PROD_ENVS
    web_origins = _csv("WEB_ORIGINS", DEFAULT_PROD_WEB_ORIGINS if prod else DEFAULT_WEB_ORIGINS)
    notes: list[str] = []
    unpooled = env("DATABASE_URL_UNPOOLED")
    if unpooled and env("DATABASE_URL") and not from_dotenv("DATABASE_URL") and from_dotenv("DATABASE_URL_UNPOOLED"):
        # The operator exported DATABASE_URL (say, a local database) while .env
        # carries a provider's pooled/unpooled pair: the .env direct URL
        # belongs to the other database, so it is dropped.
        notes.append("DATABASE_URL_UNPOOLED from .env ignored: DATABASE_URL was set in the environment")
        unpooled = None
    # Legacy dashboards and guest pages still call the API origin directly, so
    # the production web origins stay allowed even in a dev-configured process.
    cors_default = ",".join(dict.fromkeys(web_origins + DEFAULT_PROD_WEB_ORIGINS.split(",")))

    data: dict[str, object] = {
        "ENV": env_name,
        "SECRET_KEY": env("SECRET_KEY", DEV_SECRET_KEY),
        "WEB_ORIGINS": web_origins,
        "PUBLIC_API_ORIGIN": env("PUBLIC_API_ORIGIN", "http://localhost:8000"),
        "CORS_ALLOWED_ORIGINS": _csv("CORS_ALLOWED_ORIGINS", cors_default),
        "CORS_ALLOW_ORIGIN_REGEX": env("CORS_ALLOW_ORIGIN_REGEX"),
        # Secure by default wherever the process serves real users over HTTPS;
        # anything that is not a development process is finalised below.
        "COOKIE_SECURE": _bool("COOKIE_SECURE", prod or on_vercel),
        "SESSION_TTL_DAYS": _int("SESSION_TTL_DAYS", 30),
        "LOG_LEVEL": env("LOG_LEVEL", "INFO"),
        "CLEFS_DISABLED_MODULES": _csv("CLEFS_DISABLED_MODULES"),
        "CLEFS_ONLY_MODULES": _csv("CLEFS_ONLY_MODULES"),
        "DATABASE_URL": env("DATABASE_URL"),
        "PSQL_DATABASE_URL": env("PSQL_DATABASE_URL"),
        "DB_HOST": env("DB_HOST", "localhost"),
        "DB_PORT": _int("DB_PORT", 5432),
        "DB_NAME": env("DB_NAME", "aria"),
        "DB_USER": env("DB_USER", "postgres"),
        "DB_PASSWORD": env("DB_PASSWORD", ""),
        "DATABASE_URL_UNPOOLED": unpooled,
        # Serverless instances each hold their own pool, so keep it small there.
        "DB_POOL_MAX": _int("DB_POOL_MAX", 3 if on_vercel else 10),
        # Vercel has no release step; migrating on cold start preserves the old
        # init_db-on-startup behaviour (one SELECT when nothing is pending).
        "MIGRATE_ON_STARTUP": _bool("MIGRATE_ON_STARTUP", on_vercel),
        # Serverless instances cannot share LISTEN connections; SSE degrades to polling.
        "EVENTS_BACKEND": env("EVENTS_BACKEND", "local" if on_vercel else "notify"),
        "CRON_SECRET": env("CRON_SECRET"),
        "WORKER_TOKEN": env("WORKER_TOKEN"),
        "VOICE_ENABLED": _bool("VOICE_ENABLED", False),
        "GEMINI_API_KEY": env("GEMINI_API_KEY") or env("GOOGLE_API_KEY"),
        "GOOGLE_GENAI_USE_VERTEXAI": _bool("GOOGLE_GENAI_USE_VERTEXAI", False),
        "GOOGLE_CLOUD_PROJECT": env("GOOGLE_CLOUD_PROJECT"),
        "GOOGLE_CLOUD_LOCATION": env("GOOGLE_CLOUD_LOCATION", "global"),
        "VOICE_WEBHOOK_SECRET": env("VOICE_WEBHOOK_SECRET"),
        "AI_ENABLED": _bool(
            "AI_ENABLED", bool(env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")) or _bool("GOOGLE_GENAI_USE_VERTEXAI", False)
        ),
        "AI_MODEL_DEFAULT": env("AI_MODEL_DEFAULT", "gemini-3.5-flash"),
        "AI_MODEL_FAST": env("AI_MODEL_FAST", "gemini-3.1-flash-lite"),
        "AI_LOG_CONTENT": _bool("AI_LOG_CONTENT", False),
        "REALTIME_MODEL": env("REALTIME_MODEL", "gemini-2.5-flash-native-audio-preview-09-2025"),
        "PERPLEXITY_API_KEY": env("PERPLEXITY_API_KEY"),
        "SERPAPI_KEY": env("SERPAPI_KEY"),
        "GOOGLE_MAPS_API_KEY": env("GOOGLE_MAPS_API_KEY"),
        "TRIPADVISOR_API_KEY": env("TRIPADVISOR_API_KEY"),
        "WA_ACCESS_TOKEN": env("WA_ACCESS_TOKEN"),
        "WA_APP_SECRET": env("WA_APP_SECRET"),
        "WA_VERIFY_TOKEN": env("WA_VERIFY_TOKEN"),
        "WA_PLATFORM_PHONE_NUMBER_ID": env("WA_PLATFORM_PHONE_NUMBER_ID"),
        "WA_BUSINESS_ACCOUNT_ID": env("WA_BUSINESS_ACCOUNT_ID"),
        "WA_API_VERSION": env("WA_API_VERSION", "v23.0"),
        "META_APP_SECRET": env("META_APP_SECRET"),
        "WA_MAGIC_LINK_TEMPLATE": env("WA_MAGIC_LINK_TEMPLATE", "clefs_magic_link"),
        "META_LEADGEN_VERIFY_TOKEN": env("META_LEADGEN_VERIFY_TOKEN"),
        "WA_SERVICE_FREE_ALLOWANCE": _int("WA_SERVICE_FREE_ALLOWANCE", 0),
        "CONTACT_HMAC_PEPPER": env("CONTACT_HMAC_PEPPER"),
        "PII_ENC_KEY": env("PII_ENC_KEY"),
        "OTA_ALIAS_DOMAINS": [d.lower() for d in _csv("OTA_ALIAS_DOMAINS", DEFAULT_OTA_ALIAS_DOMAINS)],
        "NOTIFY_MODE": env("NOTIFY_MODE", "log"),
        "RESEND_API_KEY": env("RESEND_API_KEY"),
        "RESEND_WEBHOOK_SECRET": env("RESEND_WEBHOOK_SECRET"),
        "EMAIL_FROM": env("EMAIL_FROM"),
        "EMAIL_REPLY_TO": env("EMAIL_REPLY_TO"),
        "INBOUND_EMAIL_DOMAIN": env("INBOUND_EMAIL_DOMAIN"),
        "INBOUND_EMAIL_ENABLED": _bool("INBOUND_EMAIL_ENABLED", False),
        "INBOUND_EMAIL_SPF_VERIFIERS": [
            d.lower().strip(".") for d in _csv("INBOUND_EMAIL_SPF_VERIFIERS", RESEND_INBOUND_VERIFIER)
        ],
        "INBOUND_EMAIL_AUTHSERV_IDS": [
            d.lower().strip(".") for d in _csv("INBOUND_EMAIL_AUTHSERV_IDS", RESEND_INBOUND_VERIFIER)
        ],
        "SMS_PROVIDER": env("SMS_PROVIDER", "msg91"),
        "SMS_ENABLED": _bool("SMS_ENABLED", False),
        "SMS_AUTH_KEY": env("SMS_AUTH_KEY"),
        "SMS_TEMPLATE_ID": env("SMS_TEMPLATE_ID"),
        "TWILIO_ACCOUNT_SID": env("TWILIO_ACCOUNT_SID"),
        "TWILIO_AUTH_TOKEN": env("TWILIO_AUTH_TOKEN"),
        "TWILIO_FROM": env("TWILIO_FROM"),
        "FILE_STORE": env("FILE_STORE", "db"),
        "FILE_MAX_BYTES": _int("FILE_MAX_BYTES", 25 * 1024 * 1024),
        "S3_BUCKET": env("S3_BUCKET"),
        "S3_REGION": env("S3_REGION"),
        "PREDICTHQ_TOKEN": env("PREDICTHQ_TOKEN"),
        "RATE_SHOP_PROVIDER": env("RATE_SHOP_PROVIDER"),
        "RATE_SHOP_API_KEY": env("RATE_SHOP_API_KEY"),
        "GBP_OAUTH_CLIENT_ID": env("GBP_OAUTH_CLIENT_ID"),
        "GBP_OAUTH_CLIENT_SECRET": env("GBP_OAUTH_CLIENT_SECRET"),
        "GOOGLE_ADS_LEAD_KEY": env("GOOGLE_ADS_LEAD_KEY"),
        "GOOGLE_API_KEY": env("GOOGLE_API_KEY"),
        "OAUTH_REDIRECT_BASE": env("OAUTH_REDIRECT_BASE"),
        "SERPAPI_MONTHLY_BUDGET_USD": _decimal("SERPAPI_MONTHLY_BUDGET_USD", None),
        "PUBLIC_SERPAPI_MONTHLY_CAP": _int_or_none("PUBLIC_SERPAPI_MONTHLY_CAP"),
        "SERPAPI_COST_PER_SEARCH_USD": _decimal("SERPAPI_COST_PER_SEARCH_USD", "0.015"),
        "VISIBILITY_AI_ENABLED": _bool("VISIBILITY_AI_ENABLED", True),
        "PERPLEXITY_MODEL": env("PERPLEXITY_MODEL", "sonar"),
        "PERPLEXITY_TERMS_APPROVED": _bool("PERPLEXITY_TERMS_APPROVED", False),
        "PUBLIC_CHECK_DAILY_CAP": _int("PUBLIC_CHECK_DAILY_CAP", 20),
        "SNAPSHOT_DAILY_CAP": _int("SNAPSHOT_DAILY_CAP", 10),
        "SNAPSHOT_MONTHLY_BUDGET_USD": _decimal("SNAPSHOT_MONTHLY_BUDGET_USD", "50"),
        "GBP_PROJECT_QPM": _int("GBP_PROJECT_QPM", 0),
        "TICKETMASTER_API_KEY": env("TICKETMASTER_API_KEY"),
        "OPEN_METEO_API_KEY": env("OPEN_METEO_API_KEY"),
        "MAKCORPS_API_KEY": env("MAKCORPS_API_KEY"),
        "DEMAND_XOTELO_ENABLED": _bool("DEMAND_XOTELO_ENABLED", False),
        "FOUNDER_NOTIFY_EMAIL": env("FOUNDER_NOTIFY_EMAIL"),
        "FOUNDER_NOTIFY_WHATSAPP": env("FOUNDER_NOTIFY_WHATSAPP"),
        "VIS_LEAD_WA_TEMPLATE": env("VIS_LEAD_WA_TEMPLATE", "clefs_google_check_followup"),
        "PG_AUDIT_LEAD_WA_TEMPLATE": env("PG_AUDIT_LEAD_WA_TEMPLATE", "clefs_audit_lead"),
        "VAPID_PUBLIC_KEY": env("VAPID_PUBLIC_KEY"),
        "VAPID_PRIVATE_KEY": env("VAPID_PRIVATE_KEY"),
        "VAPID_SUBJECT": env("VAPID_SUBJECT"),
        "XERO_CLIENT_ID": env("XERO_CLIENT_ID"),
        "XERO_CLIENT_SECRET": env("XERO_CLIENT_SECRET"),
        "QBO_CLIENT_ID": env("QBO_CLIENT_ID"),
        "QBO_CLIENT_SECRET": env("QBO_CLIENT_SECRET"),
        "QBO_ENV": env("QBO_ENV", "sandbox"),
        "ZOHO_CLIENT_ID": env("ZOHO_CLIENT_ID"),
        "ZOHO_CLIENT_SECRET": env("ZOHO_CLIENT_SECRET"),
        "PAYMENT_PROVIDER": env("PAYMENT_PROVIDER", "none"),
        "NGENIUS_API_KEY": env("NGENIUS_API_KEY"),
        "NGENIUS_OUTLET_REF": env("NGENIUS_OUTLET_REF"),
        "NGENIUS_BASE_URL": env("NGENIUS_BASE_URL", "https://api-gateway.sandbox.ngenius-payments.com"),
        "NGENIUS_WEBHOOK_SECRET": env("NGENIUS_WEBHOOK_SECRET"),
        "TELR_STORE_ID": env("TELR_STORE_ID"),
        "TELR_AUTH_KEY": env("TELR_AUTH_KEY"),
        "TELR_WEBHOOK_SECRET": env("TELR_WEBHOOK_SECRET"),
        "STRIPE_SECRET_KEY": env("STRIPE_SECRET_KEY"),
        "STRIPE_PUBLISHABLE_KEY": env("STRIPE_PUBLISHABLE_KEY"),
        "STRIPE_WEBHOOK_SECRET": env("STRIPE_WEBHOOK_SECRET"),
        "TAP_SECRET_KEY": env("TAP_SECRET_KEY"),
        "TAP_PUBLISHABLE_KEY": env("TAP_PUBLISHABLE_KEY"),
        "TAP_WEBHOOK_SECRET": env("TAP_WEBHOOK_SECRET"),
        "TELR_API_BASE": env("TELR_API_BASE", "https://secure.telr.com"),
        "STRIPE_API_BASE": env("STRIPE_API_BASE", "https://api.stripe.com"),
        "TAP_API_BASE": env("TAP_API_BASE", "https://api.tap.company"),
        # Vercel overwrites X-Forwarded-For with the caller's address; anywhere
        # else uvicorn (--proxy-headers, --forwarded-allow-ips) already put the
        # real peer in scope["client"], and a raw header would be spoofable.
        "TRUST_FORWARDED_FOR": _bool("TRUST_FORWARDED_FOR", on_vercel),
    }
    settings = Settings(**data)  # type: ignore[arg-type]
    settings._env_explicit = explicit_env is not None
    settings._hosted = on_vercel or running_hosted()
    if env("COOKIE_SECURE") is None and not settings.is_dev:
        # Fail closed: a preview, staging or unlabelled deploy (ENV unset on a
        # VM or in a container) serves people over HTTPS too. Only a
        # development process defaults to plain cookies for http://localhost.
        settings.COOKIE_SECURE = True
    s_unpooled = settings.DATABASE_URL_UNPOOLED
    if s_unpooled and not urls_pair(settings.database_url, s_unpooled):
        # Never echo the URLs themselves: they carry passwords.
        notes.append(
            f"DATABASE_URL_UNPOOLED ignored: it names database {db_name(s_unpooled)!r} on "
            f"{db_host(s_unpooled) or 'localhost'}, which does not pair with the effective "
            f"{db_name(settings.database_url)!r} on {db_host(settings.database_url) or 'localhost'} "
            "(database name, locality and credentials must all match)"
        )
    settings._notes = notes
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def reload_settings() -> Settings:
    """Re-read the environment (tests change env vars between cases)."""
    get_settings.cache_clear()
    return get_settings()


def ai_model_for(task: str) -> str:
    """AI_MODEL_<TASK> override, else AI_MODEL_DEFAULT."""
    key = "AI_MODEL_" + "".join(ch if ch.isalnum() else "_" for ch in task).upper()
    return env(key) or get_settings().AI_MODEL_DEFAULT
