# Deploying Clefs

Clefs runs on the accounts it already has: **Vercel** for the web app and the API, **Neon** for Postgres. No new cloud is provisioned for the UAE demo or the first pilots. The API can also run on **Google Cloud Run** from `server/Dockerfile` (section 20). This page replaces `server/README-deploy.md`.

## Contents

1. [Topology](#1-topology)
2. [Neon (database)](#2-neon-database)
3. [Vercel: the API project](#3-vercel-the-api-project)
4. [Vercel: the web project](#4-vercel-the-web-project)
5. [Environment variables](#5-environment-variables)
6. [The job tick (cron)](#6-the-job-tick-cron)
7. [The worker](#7-the-worker)
8. [WhatsApp Cloud API](#8-whatsapp-cloud-api)
9. [Google Gemini and in-room phone (SIP)](#9-google-gemini-and-in-room-phone-sip)
10. [Data vendors: PredictHQ, SerpApi, Perplexity, Google](#10-data-vendors-predicthq-serpapi-perplexity-google)
11. [Email: Resend outbound and inbound](#11-email-resend-outbound-and-inbound)
12. [Payment gateways](#12-payment-gateways)
13. [Accounting apps: Xero, QuickBooks, Zoho](#13-accounting-apps-xero-quickbooks-zoho)
14. [SMS and web push](#14-sms-and-web-push)
15. [Data residency statement](#15-data-residency-statement)
16. [Deploy checklist](#16-deploy-checklist)
17. [Rollout order](#17-rollout-order)
18. [Local runs against a deployed database](#18-local-runs-against-a-deployed-database)
19. [Appendix: Docker Compose (UAE-resident option)](#19-appendix-docker-compose-uae-resident-option)
20. [Google Cloud Run](#20-google-cloud-run)

## 1. Topology

```
Browser (UAE) ──> Vercel edge ──> web project (client/, Next.js 16)
                                     │  /api/clefs/*  (rewrite, server side)
                                     v
                                  API project (server/, one Python function, sin1)
                                     │
                                     v
                                  Neon Postgres (aws-ap-southeast-1, Singapore)

Vercel Cron or an external pinger ──> API /internal/cron/tick  (every minute)
Meta, SIP bridge, Resend, gateways ──> API /webhooks/...
Guest voice (WebSocket) ──> Gemini Live API directly (the API only mints the token)
```

- **Two Vercel projects** from this repository: the web app with root directory `client/`, and the API with root directory `server/`.
- The browser only calls `/api/clefs/*` on the web origin; Next rewrites that to `CLEFS_API_ORIGIN` on the server side. Cookies (`clefs_session`, `clefs_device`) are therefore first-party on the web origin, with no CORS credentials and no cookie `Domain`.
- SSE streams and large uploads go straight to `NEXT_PUBLIC_API_URL` with a 60-second HMAC ticket, so a long-lived stream never hangs off the proxy hop. On Vercel, `EVENTS_BACKEND=local`: an event reaches only streams held by the same function instance, so the stream stays open but most events (guest requests, webhooks, cron sweeps) never arrive on it. The console therefore keeps refetching every 20 seconds while such a stream is connected: the stream ticket says `poll: true`, and a web build on Vercel assumes it when an older API leaves the flag out.
- Files (statements, PDFs, evidence) are stored in Postgres (`FILE_STORE=db`).

## 2. Neon (database)

- **Region:** keep the existing Neon project in **`aws-ap-southeast-1` (Singapore)**. Neon has no Middle East or India region, and Singapore is the closest one that pairs with Vercel's `sin1`.
- **One database, demo hotel included.** Since 6 October 2026 the synthetic demo hotel `demo-deira-crest` lives in the production branch next to the real hotels; there is no separate demo branch or deployment. The seed only rebuilds a hotel whose status is `demo` and refuses any other, so a real hotel's rows are never touched. A reseed runs as one transaction: the old demo stays visible until it commits, and writes to the demo hotel's rows wait for it.
- Each branch has two endpoints:
  - **pooled** (`-pooler` host) goes in `DATABASE_URL`, used by requests;
  - **direct** goes in `DATABASE_URL_UNPOOLED`, used by migrations, `LISTEN` and advisory locks.

  The direct URL is used only when it names the same database, role and password as the pooled one (`settings.urls_pair`). Every Neon project calls its database `neondb`, so this check is what stops a stale production direct URL from pairing with a staging pooled URL.
- **Migrations** run on cold start on Vercel (`MIGRATE_ON_STARTUP` defaults to true there). Each file runs in its own transaction under a Postgres advisory lock, so concurrent cold starts are safe. From a laptop: `python -m clefs.migrate status`, then `up`, with the URLs exported in the shell (section 18).
- `python -m clefs.migrate verify` fails when an applied migration file was edited. Never edit an applied migration; add a new one.
- Refresh the demo hotel on production with `gtm/tools/fast_reseed.py` (from server/: `prep`, `seed`, `wipe`, `push`; about 6 minutes). It seeds a local PostgreSQL 18 with the secrets from `~/.clefs/prod-secrets.env`, then uploads only the demo hotel's rows in one transaction, so nothing crosses the network statement by statement; `wipe` and `push` refuse a hotel that is not a demo. Run it after 09:00 Dubai on the morning of a demo so deadlines line up with the day.

## 3. Vercel: the API project

- **Root directory:** `server/`. `server/vercel.json` builds `main.py` with `@vercel/python`, routes every path to it, pins **`"regions": ["sin1"]`** (next to the database; Vercel's default is `iad1`, US East) and declares the cron (section 6).
- **Python 3.12.** `server/.python-version` pins it for Vercel and the `Dockerfile` uses `python:3.12-slim`. 3.12 is the syntax floor for all code; `tests/foundation/test_w0b_ops.py` fails if the pins drift or a file stops parsing as 3.12.
- **Dependencies.** `requirements.txt` declares every third-party package that `clefs` imports directly, and Vercel installs fresh on every deploy, so an open upper bound drifts there first. FastAPI is capped below 0.137: from 0.137.0 `include_router()` nests routers inside `app.routes`. The suite already walks nested routes (`tests/foundation/route_walk.iter_routes`) and passes on 0.137.0 apart from the cap assertion; lift the cap as its own change (bump, run the full suite, drop that assertion).
- **No `pyproject.toml` in `server/`.** `@vercel/python` treats a `pyproject.toml` (or `uv.lock`) as a dependency manifest and may prefer it over `requirements.txt`; one with no `[project]` table fails the build or installs nothing. The pytest config therefore lives in `server/pytest.ini`. Run `vercel build` in `server/` on a preview before adding either file.
- `.env` and `.env.*` are listed in `excludeFiles`, so a local `server/.env` uploaded by `vercel deploy` never reaches the function bundle.
- `DB_POOL_MAX` defaults to 3 on Vercel. `TRUST_FORWARDED_FOR` defaults to true on Vercel (it overwrites `X-Real-IP` and `X-Forwarded-For`), so rate limits and audit rows see the real client IP.
- **Function duration.** A cron tick runs for `budget_s` seconds (default 25, max 55). The function's max duration must be longer than the budget; otherwise call the tick with a smaller `?budget_s=`.

## 4. Vercel: the web project

- **Root directory:** `client/`. Framework preset Next.js; `npm run build`.
- **Function region: `sin1`.** `client/vercel.json` pins `"regions": ["sin1"]` (Singapore); check that Project Settings, Functions, Function Region agrees after the first deploy. Vercel's default for a new project is `iad1` (US East). Left there, `/api/realtime` (guest room, consent and language on every concierge mint), the privacy pages and any server rendering run in the US, add a US to Singapore round trip to the database-backed API, and contradict the residency statement (section 15).
- Environment:

| Variable | Purpose |
|---|---|
| `CLEFS_API_ORIGIN` | Server-only. The API project's URL, target of the `/api/clefs/*` rewrite and of the realtime mint proxy (`app/api/realtime`). Never exposed to the browser. **Read at build time** for the rewrite: `next build` writes it into `.next/routes-manifest.json`, and `next start` with a different value still proxies to the built one (it warns at start). Set it before building. |
| `CLEFS_PROXY_SECRET` | Server-only, the same random value on the web and API projects. The realtime mint proxy sends the guest's IP in `X-Clefs-Client-IP` with this key, so the API's per-IP session limit counts guests, not the web function's egress IP. Without it every guest shares one 30-per-hour budget. |
| `NEXT_PUBLIC_API_URL` | **Required.** The API origin as the browser reaches it: SSE streams, file uploads (`uploadFile` posts straight to it) and the webhook URLs shown in Inquiries, Channels and Payments. Browser code falls back to `http://localhost:8000`, **not** to `CLEFS_API_ORIGIN` (server-only, it never reaches the browser; only the SSE redirect in `next.config.mjs` falls back to it). Left unset, a production deploy points every stream, upload and displayed webhook URL at localhost. Like every `NEXT_PUBLIC_*` value it is inlined at build time, so set it before building. |
| `NEXT_PUBLIC_VAPID_PUBLIC_KEY` | Web push for staff devices; must match the API's `VAPID_PUBLIC_KEY`. |
| `NEXT_PUBLIC_AVATAR_RIG` | Leave unset (video avatar). `viseme` only to evaluate the lip-sync rig. |
| `NEXT_PUBLIC_WHATSAPP_NUMBER` | UAE WhatsApp number for the footer opt-in link; leave unset to hide it. |
| `NEXT_PUBLIC_STAFF_LINK_SUNSET`, `NEXT_PUBLIC_SERVICE_CLASSIC_ENTRY` | Legacy staff-link banner date and the classic request-entry switch; leave unset unless retiring personal staff links. |

- Add the web origin (and each preview origin you test on) to the API's `WEB_ORIGINS`, or match previews with `CORS_ALLOW_ORIGIN_REGEX`. A cookie-authenticated mutation from an origin not on that list is refused (CSRF).
- `npm run build` does not run the claims linter or the i18n check. Run `npm run check:claims`, `node scripts/check-i18n.mjs` and `npm run test` before promoting a deploy (CI should run all three); a failing claim is meant to block a marketing deploy.

## 5. Environment variables

Set these on the **API project** (Project Settings, Environment Variables, Production; repeat for Preview with preview values). `server/.env.example` lists every variable the code knows, with a comment each, and a test fails if a setting is missing from it. Blank means unset or the default. Put comments on their own line in any `.env` file: dotenv reads `KEY=  # note` as the value `# note`.

### Core (required)

| Variable | Purpose |
|---|---|
| `ENV` | `production` (or `preview`). Unset resolves to production on a Vercel production deploy and preview on other Vercel deploys, but set it explicitly anyway. |
| `SECRET_KEY` | 32+ random characters. Signs tickets, QR keys, WhatsApp button refs and derives the voice JWT key. The public development key is refused outside development. |
| `DATABASE_URL` / `DATABASE_URL_UNPOOLED` | Neon pooled / direct endpoints of the right branch (section 2). |
| `CRON_SECRET` | Random. Bearer for `/internal/cron/tick` and `/internal/status`; Vercel Cron sends it automatically. |
| `WORKER_TOKEN` | Random. Bearer for worker-only internal routes (it may also call the cron routes). |
| `WEB_ORIGINS` | The web origins allowed to send cookie-authenticated mutations, e.g. `https://www.clefs.world,https://clefs.world`. The first one is the base of links sent to people (magic links, pairing). |
| `PUBLIC_API_ORIGIN` | The absolute API origin, used in webhook URLs. |
| `CORS_ALLOWED_ORIGINS`, `CORS_ALLOW_ORIGIN_REGEX` | CORS allowlist (no credentials) and a regex for preview origins, e.g. `^https://clefs-[a-z0-9-]+\.vercel\.app$`. |
| `CONTACT_HMAC_PEPPER` | Pepper for hashed guest phone and email identities. Required in production (startup logs name it while missing). Never rotate it casually: every stored hash depends on it. |
| `PII_ENC_KEY` | 32 bytes, base64 or hex; AES-256-GCM key for sealed PII. Required in production. |
| `TRUST_FORWARDED_FOR` | `true` behind Google's front end (Cloud Run) or Vercel, so rate limits key on each caller's address instead of the proxy's. |
| `OAUTH_REDIRECT_BASE` | The API path on the **web** origin, e.g. `https://www.clefs.world/api/clefs`. The OAuth callback must see the browser's `clefs_session` cookie; any other host fails every connect with `session_mismatch`. |

Leave `COOKIE_SECURE` unset (true outside development; production refuses to start with `false`), `EVENTS_BACKEND` unset (`local` on Vercel), `MIGRATE_ON_STARTUP` and `DB_POOL_MAX` unset (Vercel defaults). `CLEFS_DISABLED_MODULES` is the production kill switch for a module (comma list, e.g. `demand,reviews`). `LOG_LEVEL` defaults to `INFO`; `SESSION_TTL_DAYS` to 30.

### AI and voice

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Enables AI (concierge, ingest mapping, reply drafts, Ask, visibility radar). `AI_ENABLED` defaults to true when it is set. |
| `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_LOCATION` | `true` plus a region (e.g. `asia-southeast1`) to call Gemini through Vertex AI with the service account instead of a key. |
| `GOOGLE_CLOUD_PROJECT`, `VOICE_WEBHOOK_SECRET` | Google Cloud project (Vertex AI and voice) and the in-room phone webhook secret (section 9). |
| `VOICE_ENABLED` | `true` to accept SIP calls. |
| `REALTIME_MODEL` | Web concierge Live API model (default `gemini-2.5-flash-native-audio-preview-09-2025`). Model names are read from env and checked at build time. |
| `VOICE_REALTIME_MODEL` | In-room phone (SIP) realtime model. A voice line's own model wins, then this, then `REALTIME_MODEL`. |
| `VOICE_TRANSCRIBE_MODEL` | Optional; caller transcripts on SIP calls come from Gemini Live native transcription by default. |
| `LIVE_VIA_VERTEX` | Guest voice through the server's Vertex AI Live relay instead of browser tokens on the Gemini Developer API. Defaults to on when `GOOGLE_GENAI_USE_VERTEXAI` is on; no AI Studio credits needed. |
| `LIVE_VERTEX_MODEL`, `LIVE_VERTEX_LOCATION` | The relay's Live model and location (defaults `gemini-3.8-live`, `us-central1`; Vertex serves Live models regionally, not from `global`). Fallback model: `gemini-live-2.5-flash-native-audio`. |
| `LIVE_RELAY_ORIGIN` | Optional: the public `wss://` origin of the relay when it differs from `PUBLIC_API_ORIGIN`. |
| `AI_MODEL_FALLBACK` | The model a call retries on once after a 429, a 5xx or a timeout (default `AI_MODEL_FAST`). |
| `TRUST_EVAL_DAILY_CAP_USD` | Trust's AI eval spend cap, USD per hotel per day (default 5). A run started over the cap is skipped; one that crosses it stops with `cost_cap_reached`. |
| `AI_MODEL_DEFAULT`, `AI_MODEL_FAST`, `AI_MODEL_<TASK>` | Text models (defaults `gemini-3.5-flash` and `gemini-3.1-flash-lite`; a call that fails with 429, 5xx or a timeout retries once on `AI_MODEL_FALLBACK`, default the fast model); `AI_MODEL_<TASK>` overrides one task (for example `AI_MODEL_INGEST_MAPPING`, `AI_MODEL_VISIBILITY_GEMINI`). |
| `AI_PRICE_<MODEL>` | Price override for the `ai_calls` cost ledger, USD per 1M tokens. |
| `AI_LOG_CONTENT` | Keep `false`. Prompts and outputs are never logged unless debugging. |

### WhatsApp and Meta (section 8)

| Variable | Purpose |
|---|---|
| `WA_ACCESS_TOKEN` | System-user token with `whatsapp_business_messaging` and `whatsapp_business_management`. |
| `WA_APP_SECRET` | Verifies `X-Hub-Signature-256` on every webhook (401 on mismatch, 503 when unset). `META_APP_SECRET` is the fallback and also verifies Lead Ads webhooks. |
| `WA_VERIFY_TOKEN` | Answers Meta's `hub.challenge` when subscribing the webhook. |
| `WA_PLATFORM_PHONE_NUMBER_ID`, `WA_BUSINESS_ACCOUNT_ID` | The Clefs platform number and WABA (owner brief, magic links, staff escalations, lead follow-ups). |
| `WA_API_VERSION` | Graph API version (default `v23.0`). |
| `WA_MAGIC_LINK_TEMPLATE` | Approved authentication template for magic links (default `clefs_magic_link`). |
| `WA_SERVICE_FREE_ALLOWANCE` | Free service/utility messages per number per month (default 0). |
| `META_LEADGEN_VERIFY_TOKEN` | `hub.verify_token` of the Lead Ads webhook (`/webhooks/inquiries/meta-leadgen`). |
| `VIS_LEAD_WA_TEMPLATE`, `PG_AUDIT_LEAD_WA_TEMPLATE` | Approved follow-up templates for the two lead magnets (defaults `clefs_google_check_followup`, `clefs_audit_lead`). |
| `FOUNDER_NOTIFY_EMAIL`, `FOUNDER_NOTIFY_WHATSAPP` | Where new-lead alerts go (blank: none). |

### Notifications, email and SMS

| Variable | Purpose |
|---|---|
| `NOTIFY_MODE` | `live` in production (real HTTP to Graph, Resend, Twilio). `log` writes every message to the log instead; `capture` is the test mode. |
| `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_REPLY_TO` | Email through Resend (section 11). The key needs full access: it also reads received mail back. `EMAIL_REPLY_TO` is where replies go when a message names no Reply-To. |
| `INBOUND_EMAIL_ENABLED`, `RESEND_WEBHOOK_SECRET`, `INBOUND_EMAIL_DOMAIN` | Inbound email webhook (section 11): the switch, the webhook's `whsec_` signing secret, and the address domain, which defaults to `in.clefs.world` (set it only for another domain). |
| `INBOUND_EMAIL_SPF_VERIFIERS`, `INBOUND_EMAIL_AUTHSERV_IDS` | Whose sender verdicts count (mailed imports that commit without review, forwarded reviews). A sender can write their own `Authentication-Results`, so only a verdict naming one of these verifiers counts, and only when exactly one copy does. Both default to `inbound.resend.com`: the inbound converter drops every verdict header the message carried and writes one of each from Resend's API under that name. Leave both unset. |
| `SMS_PROVIDER`, `SMS_ENABLED` | `twilio` for UAE numbers (MSG91 is the legacy India provider and cannot send compliant UAE SMS). `SMS_ENABLED=false` switches SMS off. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` | Twilio credentials and sender. |
| `SMS_AUTH_KEY`, `SMS_TEMPLATE_ID` | MSG91 (legacy India hotels only). |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Web push to staff devices. Push stays log-only until all three are set and the optional `pywebpush` package is installed (it is not in `requirements.txt` yet). |

### Data vendors (section 10)

| Variable | Purpose |
|---|---|
| `PREDICTHQ_TOKEN` | PredictHQ Events and Features for Demand Radar. Without it the curated UAE calendar and the hotel's own events still work. |
| `TICKETMASTER_API_KEY`, `OPEN_METEO_API_KEY`, `MAKCORPS_API_KEY`, `DEMAND_XOTELO_ENABLED` | Optional demand sources. Open-Meteo works without a key on its free non-commercial endpoint; set the key for commercial use. Xotelo is an unofficial feed and stays off. |
| `RATE_SHOP_PROVIDER`, `RATE_SHOP_API_KEY` | A rate-shop API if the hotel has one; otherwise comp rates come from SerpApi or an uploaded rate-shop export. |
| `SERPAPI_KEY`, `SERPAPI_COST_PER_SEARCH_USD`, `SERPAPI_MONTHLY_BUDGET_USD`, `PUBLIC_SERPAPI_MONTHLY_CAP` | SerpApi (Google Hotels, AI Mode, reviews) through `core.serpapi`, with a per-call cost ledger in micros, a global monthly budget and a cap for the public lead magnets. |
| `PERPLEXITY_API_KEY`, `PERPLEXITY_MODEL`, `PERPLEXITY_TERMS_APPROVED` | Perplexity Sonar for the AI answer radar. Stays unused until `PERPLEXITY_TERMS_APPROVED=true`, which is set only after legal has reviewed the API terms. |
| `VISIBILITY_AI_ENABLED`, `PUBLIC_CHECK_DAILY_CAP`, `SNAPSHOT_DAILY_CAP`, `SNAPSHOT_MONTHLY_BUDGET_USD` | Visibility kill switch and spend caps for public checks and AI snapshots. |
| `GOOGLE_MAPS_API_KEY` | Places API (IDs-only search is the free SKU) for the rank metric. |
| `GOOGLE_API_KEY` | CrUX and PageSpeed Insights for site health. |
| `GBP_OAUTH_CLIENT_ID`, `GBP_OAUTH_CLIENT_SECRET`, `GBP_PROJECT_QPM` | Google Business Profile OAuth app and the approved per-minute quota (0 until Google approves the API application, 300 after). |
| `GOOGLE_ADS_LEAD_KEY` | Google Ads lead-form webhook key. |
| `TRIPADVISOR_API_KEY` | Tripadvisor Content API; the Tripadvisor review source is off without it. |

### Finance and payments (sections 12 and 13)

| Variable | Purpose |
|---|---|
| `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET` | Xero app for P&L sync. |
| `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_ENV` | QuickBooks Online app; `QBO_ENV` is `sandbox` or `production` (anything else fails at startup). |
| `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET` | Zoho Books app. |
| `TELR_API_BASE`, `STRIPE_API_BASE`, `TAP_API_BASE` | Gateway API bases, overridable to point at a sandbox or a stub. |
| `PAYMENT_PROVIDER`, `NGENIUS_*`, `TELR_*`, `STRIPE_*`, `TAP_*` keys | Modelled in settings, but Inquiry Guard reads each hotel's own gateway account (section 12). Leave them unset. |

### Files and privacy

| Variable | Purpose |
|---|---|
| `PROOF_BUCKET` | Cloud Storage bucket for completion photos (proof before closure). Give it a lifecycle rule that deletes objects after 7 days; without it photos are kept in Postgres. |
| `FILE_STORE`, `FILE_MAX_BYTES` | `db` (Postgres bytea, the Vercel choice) and the upload cap (default 25 MB). **On Vercel set `FILE_MAX_BYTES=4000000`:** Vercel rejects any function request body over **4.5 MB** with `413 FUNCTION_PAYLOAD_TOO_LARGE` before FastAPI runs, so a 25 MB cap is never reached and a larger upload fails with a non-JSON platform error. With 4,000,000 the API refuses early with a clear message (multipart overhead fits under the platform limit). Files larger than that (big OTA statement PDFs, P&L exports) need the VM topology with `s3` (`S3_BUCKET` / `S3_REGION`, section 19). |
| `OTA_ALIAS_DOMAINS` | OTA relay domains that never receive marketing (`guest.booking.com,m.expediapartnercentral.com`). |

## 6. The job tick (cron)

On Vercel nothing runs between requests: there is no always-on worker, so the scheduler and the job queue advance only when `/internal/cron/tick` is called. Every minute matters: `core.notify.sweep` and `service.sla_sweep` run every minute, the action wake and expiry jobs every 5 minutes, and hotel-local daily jobs (the 07:00 brief, detectors, rollups) on their slot. Extra or overlapping ticks are harmless, because each scheduled slot is claimed once in `job_runs`.

### Vercel Pro: Vercel Cron

`server/vercel.json` already schedules it:

```json
"crons": [{ "path": "/internal/cron/tick", "schedule": "* * * * *" }]
```

- Vercel Cron calls **production deployments only**, with `GET` and `Authorization: Bearer <CRON_SECRET>`. The route accepts `GET` and `POST` and returns 401 without the secret.
- A tick runs for up to `budget_s` seconds (default 25, max 55); keep the function's max duration above that.

### Vercel Hobby: an external pinger

Production runs on Hobby today. The API is the Vercel project `clefs-api` (https://clefs-api.vercel.app, root directory `server`), and the web project `clefs` proxies to it. The project runs on Fluid compute, whose default function timeout is 300 seconds, so a tick may use the full `budget_s=50`. Two callers drive the tick:

- the GitHub Actions workflow `.github/workflows/job-tick.yml`, every 30 minutes, with the `CRON_SECRET` repository secret. GitHub may start a scheduled run late or skip it under load, so it is not the only caller;
- one daily Vercel cron in `server/vercel.json` at 03:00 UTC (07:00 Dubai). Hobby fires it somewhere within that hour, which is enough to compile the 07:00 brief on a day when GitHub skipped its runs.

That interval lets the Neon Free compute scale to zero between ticks (it suspends after 5 idle minutes), so the monthly compute allowance lasts. SLA state is still evaluated on read, and hotel-local daily jobs run on the first tick of their slot, so the 07:00 brief can arrive up to an hour late. To trigger a tick by hand: `gh workflow run job-tick.yml`. A pilot that needs minute-level SLA escalation needs a 1-5 minute pinger (below) or Vercel Pro.

Hobby cron jobs may run at most once a day, and Vercel rejects a deploy whose `vercel.json` has a more frequent schedule. On a Hobby project:

1. Keep at most one daily schedule in the `crons` block of `server/vercel.json` (the foundation test allows only the tick path there).
2. For a shorter interval, create a job on **cron-job.org** (free, 1-minute interval) or an uptime monitor:

   ```
   POST https://<api-host>/internal/cron/tick?budget_s=25
   Authorization: Bearer <CRON_SECRET>
   ```

   `GET` works too. Set the request timeout above the budget.
3. Check `GET /internal/status` (same bearer): last tick, queue depth, failed jobs, outbox health, workers, notify mode and the AI switch.

A missed minute loses no work. Hotel-local daily jobs catch up on the next tick of the same local day, and queued tasks wait for the next tick. Service SLA state is also evaluated on read, so the console is right even between ticks.

## 7. The worker

`python -m clefs.worker` is the always-on alternative to the cron tick. Vercel has none, and the demo does not need one: the per-minute tick covers every job, and the in-room phone works serverless in MCP mode.

- It ticks every 15 seconds, wakes early on `LISTEN clefs_jobs`, and supervises each module's `worker_services`. Only the process holding the `clefs_scheduler` advisory lock schedules; every worker drains the queue. It writes `worker_heartbeats` (visible in `/internal/status`) and stops cleanly on SIGTERM. `--once` runs one tick and prints the report.
- **When you need it:** the **voice sideband** (`voice_sideband`): call transcripts into `conversation_logs`, DTMF events, safety injection and sideband function tools for a line whose `tool_transport` is `sideband`. Without a worker and without a call-ended webhook, the `voice.lease_reaper` job marks a call `stale` with no end time.
- **Where to run it:** on a laptop during a live phone demo (section 18 for the exports), or on a small always-on host only when a pilot needs sideband calls. Give it the same `ENV`, `SECRET_KEY` (the voice JWT key derives from it), database URLs and vendor keys as the API. It needs the **direct** Neon URL for `LISTEN` and the advisory lock.
- Running the worker and the cron tick together is safe: scheduled slots are claimed once, and queue claims use `FOR UPDATE SKIP LOCKED`.

## 8. WhatsApp Cloud API

Two kinds of number: the **Clefs platform number** (owner brief, magic links, staff escalations, lead follow-ups) and each **hotel's own number** (guest journeys, inquiry replies and quotes, the request tracker, concierge chat). The platform number is configured by env; a hotel's number is connected in the console.

### Meta setup (platform number)

1. **Meta Business portfolio.** Verify the business in Meta Business Suite (legal name, trade licence, website on the same domain).
2. **Meta app.** In developers.facebook.com create an app of type Business and add the **WhatsApp** product. Note the **app secret** (App settings, Basic): it becomes `WA_APP_SECRET`.
3. **WhatsApp Business Account (WABA) and number.** Add a dedicated number that is not registered on the consumer WhatsApp app, verify it by SMS or call, and submit the display name for approval. Note the **phone number id** (`WA_PLATFORM_PHONE_NUMBER_ID`) and the **WABA id** (`WA_BUSINESS_ACCOUNT_ID`).
4. **Payment method.** Add a card or credit line to the WABA in Business Settings, WhatsApp accounts, Payment settings. It must be on file by **2026-09-30**: service messages are billable from **2026-10-01**, and business-initiated templates fail without one. For a hotel's own WABA, the owner attests it in the console (`billing_ready`); WhatsApp journeys fail closed to email until billing is confirmed.
5. **Permanent token.** Create a system user (Business Settings, Users, System users), assign it the app and the WABA with full control, and generate a token with `whatsapp_business_messaging` and `whatsapp_business_management`. That is `WA_ACCESS_TOKEN`.
6. **Webhook.** In the app's WhatsApp configuration set:
   - Callback URL: `https://<api-host>/webhooks/whatsapp`
   - Verify token: the value of `WA_VERIFY_TOKEN` (the API answers `hub.challenge` when it matches)
   - Subscribe the **`messages`** field (it carries inbound messages and delivery statuses), and subscribe the app to the WABA (`POST /<WABA_ID>/subscribed_apps`).

   Every POST is verified with `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with `WA_APP_SECRET`); a bad signature gets 401, and an unset secret 503. Messages dedupe on `messages[].id`.
7. Set `NOTIFY_MODE=live`. Until then nothing is sent to Meta and template submissions are recorded as `PENDING` without a Graph call.

### A hotel's own number

Repeat steps 1-5 in the hotel's (or its management company's) Meta business, then connect it in the console:

- **Settings, Hotel profile:** `whatsapp_phone_number_id` and `whatsapp_display_number` (`PATCH /hotels/{slug}/settings`). This is the number every `sender="hotel"` message goes out from, and the number whose inbound messages count as guests.
- **Brief, Settings, WhatsApp:** the WABA (`PUT /hotels/{slug}/brief/wa/account` with `waba_id`, `phone_number_id`, `display_phone` and the owner's `billing_ready` attestation). Journey and inquiry templates are submitted to this WABA.

Without a hotel number, journeys fall back to email. The same app webhook receives the hotel's messages once the app is subscribed to that WABA.

### Templates to submit

Meta approval takes up to 24 hours, so submit templates early (spec 03 section 7 puts this in step 2 of the rollout). Brief lints every definition and refuses promotional words in a UTILITY template; a daily `wa.template_sync` job alerts once when Meta pauses, rejects or recategorises one.

| Template | WABA | Category | How it is submitted |
|---|---|---|---|
| `owner_brief_v1` | platform | UTILITY (3 quick-reply buttons) | Clefs admin: `POST /hotels/{slug}/brief/wa/templates/submit {name, scope: "platform"}` (Brief's own definition) |
| `weekly_report_v1` | platform | UTILITY | same |
| `clefs_magic_link` (`WA_MAGIC_LINK_TEMPLATE`) | platform | AUTHENTICATION | WhatsApp Manager |
| `service_escalation_v1` | platform | UTILITY (`{{1}}` hotel, `{{2}}` digest) | WhatsApp Manager |
| `clefs_google_check_followup`, `clefs_audit_lead` | platform | per Meta's review | WhatsApp Manager |
| `gst_j1_confirm_v1`, `gst_j2_planning_v1`, `gst_j4_checkin_v1`, `gst_j5_daycheck_v1`, `gst_j7_feedback_v1` | hotel, EN and AR | UTILITY expected (J7 is charged as marketing) | Owner approves each version in Guests, Journeys, Templates; approval submits it |
| `gst_j3_offer_v1`, `gst_j8_rebook_v1` | hotel, EN and AR | MARKETING (sent only 09:00-18:00 Dubai) | same |
| `inq_quote_ready_v1`, `inq_quote_followup_v1` | hotel, EN and AR | UTILITY | Owner approves in Inquiries, Settings (`POST /hotels/{slug}/inquiries/templates/{id}/approve`); approval submits it |
| `request_status_v1` | hotel | UTILITY | WhatsApp Manager (request tracker updates outside the 24 h window) |

Rules the code enforces: the brief carries no promotional content; inside the 24-hour window replies use buttons (max 3) or lists (max 10 rows); at least 6 seconds between messages to one recipient; marketing only 09:00-18:00 Asia/Dubai; OTA alias addresses and OTA-sourced contacts never receive marketing.

**Meta Lead Ads** (optional, Inquiry Guard): subscribe the app's Page to the `leadgen` field with callback `https://<api-host>/webhooks/inquiries/meta-leadgen` and verify token `META_LEADGEN_VERIFY_TOKEN`; signatures use `META_APP_SECRET`. Each hotel connects its Page in Inquiries, Settings, Channels (page id and a page access token, stored sealed). Reading lead data needs Meta app review for the `leads_retrieval` permission.

## 9. Google Gemini and in-room phone (SIP)

### Gemini API

- `GEMINI_API_KEY` switches on every AI feature (Gemini Developer API, google-genai SDK). For Vertex AI instead, set `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`, and give the runtime service account the Vertex AI User role; no key is stored.
- The web concierge's voice session is minted by the API (`/api/realtime` on the web app proxies to it) as a short-lived Gemini Live API token, and the guest's browser then streams audio to the Gemini Live API directly, so the server region barely affects voice latency.
- `REALTIME_MODEL` defaults to `gemini-2.5-flash-native-audio-preview-09-2025`. Confirm model names at build time (Canon 15); they are read from env, never hard-coded. Text tasks default to `gemini-2.5-flash` (`AI_MODEL_DEFAULT`) and `gemini-2.5-flash-lite` (`AI_MODEL_FAST`).
- Data terms: on the paid tier, Gemini API prompts and responses are not used to train Google's models. The Vertex AI option keeps processing in the chosen Google Cloud region.
- Every call is recorded in `ai_calls` with tokens and cost in micros, per hotel.

### In-room phones over the Gemini Live API

1. **Use a dedicated Google Cloud project** for voice (`GOOGLE_CLOUD_PROJECT`). Anyone who knows a line's SIP URI can dial it, so the URI is not a secret and the line secret below is the real gate.
2. **Webhook.** The SIP bridge in front of the PBX posts each incoming call to `https://<api-host>/webhooks/voice/incoming`. Set the shared signing secret as `VOICE_WEBHOOK_SECRET`; the API verifies every delivery (HMAC over the raw body, timestamp skew included) and dedupes on the webhook id.
3. Set `VOICE_ENABLED=true` and redeploy.
4. **Console, Concierge, Voice, Setup** (`/app/{slug}/concierge/voice/setup`):
   - pick the PBX and create a line; it shows the line's SIP URI and the `X-Clefs-Line` header secret once;
   - map extensions to rooms (CSV import or a numbering rule);
   - set the front-desk transfer target, which must be `sip:` or `sips:` (never `tel:`: PSTN breakout is a UAE regulatory grey area);
   - follow the test-call checklist while watching the last 20 webhook events.
5. **PBX recipes** live in `client/app/app/[hotelSlug]/(console)/concierge/voice/setup/recipes.ts` and render on that page: Grandstream UCM, 3CX, Asterisk / FreePBX (PJSIP, `PJSIP_HEADER(add,X-Clefs-Line)`, SDES media encryption), FreeSWITCH (`sip_h_X-Clefs-Line`), analog lines through an HT813 or GXW410x FXO gateway, a SIP bridge in front of any PBX, and other SIP PBXs.
6. **Tool transport per line.** `mcp` (default) runs serverless: tool calls from the call's Live API session reach `https://<api-host>/webhooks/voice/mcp` with a per-call JWT that binds hotel, call and room. `sideband` needs the worker (section 7).
7. **Transcripts** come from Gemini Live native transcription (input and output audio transcription on the call's session); there is no separate transcription model.

A call is rejected with SIP 486 (so the PBX fails over to the operator) when the line cannot be identified, is disabled, over capacity, outside after-hours policy, rate limited, or when the hotel's concierge is not live.

Before a real PBX, run the local harness, which signs a fake incoming-call webhook and stubs the Gemini Live API: `python -m clefs.modules.voice.harness --hotel <slug> --setup --from 1501 --room 501 --tool create_ticket ...` (see `server/clefs/modules/voice/README.md`; it refuses a non-local database).

Still to confirm against the Gemini Live API at build time: the SIP bridge end to end, call-ended lifecycle events, and DTMF events. The code falls back to the sideband worker and the lease reaper. Get e& or du sign-off before putting AI on a hotel's in-room phones.

## 10. Data vendors: PredictHQ, SerpApi, Perplexity, Google

Every vendor sits behind an adapter and a flag, so a missing key degrades a feature rather than breaking a page.

- **PredictHQ** (Demand Radar). Start the 14-day trial at predicthq.com, create an API token with Events and Features access, and set `PREDICTHQ_TOKEN`. UAE coverage is unverified, which is why Demand also ships a curated UAE calendar (CABSAT, GITEX, NYE and the Hijri holidays with a moon-dependent window) and accepts the hotel's own events.
- **SerpApi** (Google Hotels comp rates and parity, Google AI Mode for the AI radar, reviews). Set `SERPAPI_KEY` and a `SERPAPI_MONTHLY_BUDGET_USD`. Every call goes through `core.serpapi.search`, which caches, meters and records the cost in micros. SerpApi remains a vendor risk (Google v. SerpApi was dismissed on 2026-07-20, but scraping terms can change); the core rank metric uses the official Places API instead.
- **Perplexity** (AI answer radar). Set `PERPLEXITY_API_KEY`, but leave `PERPLEXITY_TERMS_APPROVED=false` until legal has read Perplexity's API terms for storing and analysing answers; the engine is skipped until then. Gemini grounding is never used (its terms forbid storing or analysing results), and no consumer app is scraped.
- **Google Maps Platform.** Create a key restricted to the Places API (New) and set `GOOGLE_MAPS_API_KEY`; IDs-only search is the free SKU. A second key with CrUX and PageSpeed Insights enabled is `GOOGLE_API_KEY`.
- **Google Business Profile API.** Apply for GBP API access for the Clefs Cloud project (Google's form; the profile must be verified for 60+ days, and approval also unlocks the Performance and Lodging APIs). Create an OAuth client (web application) with the redirect URI `{OAUTH_REDIRECT_BASE}/public/oauth/google_business/callback`, set `GBP_OAUTH_CLIENT_ID` / `GBP_OAUTH_CLIENT_SECRET`, and raise `GBP_PROJECT_QPM` from 0 to the approved quota (300). Lead time is long: apply first.
- **Google Hotel Center.** Google's third-party rates for Hotel Ads and PMax Travel Goals end on 2026-09-30. Free booking links are unaffected, but only a Hotel Center feed shows an "official site" price, which Visibility's parity check reads.
- **Tripadvisor** Content API key (`TRIPADVISOR_API_KEY`) enables that review source.

## 11. Email: Resend outbound and inbound

One Resend account carries both directions. Create its API key with **full access**: the API sends mail with it and also reads received mail back, because Resend's inbound webhook carries metadata only. A sending-only key still sends, but every inbound email would answer 502 and be retried until Resend gives up.

**Outbound** (magic links, brief emails, dispute packets, quotes, journeys by email):

1. Add the sending domain `clefs.world` in Resend and create the DNS records the dashboard lists, with the values it shows:
   - `TXT` on `resend._domainkey` (DKIM);
   - `MX` and `TXT` on `send` (SPF for the bounce domain Resend sends from);
   - `CNAME` on `rsend`.

   Wait until the domain shows as verified.
2. Set `RESEND_API_KEY` and `EMAIL_FROM` (an address on `clefs.world`), and `EMAIL_REPLY_TO`, a mailbox someone reads: replies go there when a message names no Reply-To.

Mail to reserved domains (`example.com`, `.test` and the like: the production demo hotel's users) is dropped before the call, so it never bounces. The outbox records provider `resend` and Resend's email id.

**Inbound** (forwarded OTA statements, review emails, inquiry replies):

1. Add the receiving domain `in.clefs.world` in Resend (or your own `INBOUND_EMAIL_DOMAIN`). DNS: an `MX` record on host `in` pointing at `inbound-smtp.us-east-1.amazonaws.com`, priority 10. It must be the lowest-priority MX of `in.clefs.world`, or the mail goes elsewhere.
2. In Resend, add a webhook for the event `email.received` with the URL `https://clefs-api.vercel.app/webhooks/email/resend`, and copy its signing secret.
3. On the API, set `RESEND_WEBHOOK_SECRET` (the `whsec_...` secret) and `INBOUND_EMAIL_ENABLED=true`. The route answers 503 until these and `RESEND_API_KEY` are set, and 401 when the Svix signature does not verify or is more than 5 minutes old.
4. Per hotel, set `inbound_email_alias` in Settings, Hotel profile. Addresses then take the form `<alias>+<purpose>[-<ref>]@in.clefs.world`:
   - `+inq` (or `+inquiries`) goes to Inquiry Guard; quote emails set this as Reply-To with the inquiry ref, so guest replies thread back;
   - `+reviews` goes to Reviews;
   - no tag, or `+ingest`, goes to ingest (forward OTA statements, payouts and credit notes here).

For each `email.received`, the API reads the message back from Resend (`GET /emails/receiving/{id}`) and downloads its attachments from their signed links. When Resend errors or times out, the route answers 502 and Resend delivers the webhook again later; an email Resend no longer has is logged and acknowledged. Duplicates are dropped by `MessageID` (the message's own Message-ID), attachments become files, and the reply names the status (`handled`, `unhandled`, `unrouted`, `duplicate`, `ignored`).

**Sender verdicts.** Mailed imports commit without a person, and forwarded reviews are accepted, only from an authenticated sender. The SPF, DKIM and DMARC results come from Resend's API (what its receiving server found), never from header lines in the message, which the sender writes. They name no domain, so in practice a sender's domain needs a DMARC policy (`dmarc=pass`) before its mail is trusted without a person.

**Size.** The webhook body is metadata only, so Vercel's 4.5 MB request limit does not apply to inbound mail. Each attachment up to `FILE_MAX_BYTES` (25 MB) is stored; a larger one is not downloaded and is recorded on the message as skipped (`too_large`).

## 12. Payment gateways

Inquiry Guard sends payment links for quotes and verifies the direct booking when the money arrives. Accounts are **per hotel**, because the money goes to the hotel's own merchant account; supported providers are **N-Genius** (Network International), **Telr**, **Stripe** (UAE entity) and **Tap**.

1. The hotel opens a merchant account with its provider and creates API credentials (and a webhook secret where the provider has one).
2. In the console, Inquiries, Settings, Payments, connect the account (`POST /hotels/{slug}/inquiries/payment-accounts` with the provider, config and secret). Secrets are sealed per hotel. The reply shows the webhook path **once**; reconnecting rotates it.
3. In the provider's dashboard, register `https://<api-host>/webhooks/inquiries/payments/<provider>/<account_key>` (the path from step 2).

A webhook is only stored: a queued task then asks the provider for the payment status (`fetch_status`) and applies that, so a forged or replayed webhook cannot mark a link paid. Signatures are still checked (N-Genius header secret, `Stripe-Signature` with a 5-minute tolerance, Tap `hashstring`, Telr advice secret when set), and rejected bodies are kept redacted for review. Fees come from the provider when reported, else from the account config. A hotel without a gateway uses manual links (for example a bank transfer) and marks them paid with an uploaded proof.

## 13. Accounting apps: Xero, QuickBooks, Zoho

Finance reads the P&L from the hotel's accounting system (read-only) or from an uploaded export.

| Provider | Developer portal | Scopes requested | Env |
|---|---|---|---|
| Xero | developer.xero.com, a Web app | `accounting.reports.read offline_access` | `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET` |
| QuickBooks Online | developer.intuit.com | `com.intuit.quickbooks.accounting` | `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_ENV` (`sandbox` until Intuit approves production keys) |
| Zoho Books | api-console.zoho.com, a server-based client | `ZohoBooks.accountants.READ,ZohoBooks.settings.READ` | `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET` |

- Register exactly `{OAUTH_REDIRECT_BASE}/public/oauth/<xero|qbo|zoho>/callback` as the redirect URI (and `.../google_business/callback` for the GBP client in section 10). `OAUTH_REDIRECT_BASE` must be the API path on the **web** origin (for example `https://www.clefs.world/api/clefs`), because the callback checks the browser's session cookie.
- Owners connect from Finance, Settings (`GET /hotels/{slug}/oauth/<provider>/start`, permission `finance.connect`).
- Xero's developer terms forbid using Xero data to train AI models. Finance's global mapping memory is never fed by Xero data, and no Xero data leaves the hotel's own mapping.

## 14. SMS and web push

- **SMS** is a fallback channel. UAE numbers need **Twilio** (`SMS_PROVIDER=twilio`, `TWILIO_*`); MSG91 serves the legacy Indian hotels only and cannot deliver compliant UAE SMS. SMS to +971 may not contain links (the code refuses them). UAE telemarketing hours and the Do-Not-Call Register apply to anything promotional.
- **Web push** to paired staff devices needs a VAPID key pair (`npx web-push generate-vapid-keys`): `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` and `VAPID_SUBJECT` (`mailto:` or `https:`) on the API, `NEXT_PUBLIC_VAPID_PUBLIC_KEY` on the web project, and `NOTIFY_MODE=live`. Until then push is log-only; the device app still polls.

## 15. Data residency statement

Use this wording, which matches `/security` and `/subprocessors` on the site (`client/app/lib/subprocessors.ts`):

> Today our database runs in Singapore (Neon, AWS ap-southeast-1) and AI processing runs in the United States (Google Gemini API). UAE hosting (AWS me-central-1 or Azure UAE North) is available when a hotel's contract requires it. Moving the database does not stop AI processing abroad, so guests are asked for consent to that transfer before the assistant starts.

- UAE PDPL: consent is the default basis, with separate consents for recording, marketing, AI analytics and transfer abroad. The demo and pilots rely on explicit `transfer_abroad` consent.
- Vercel functions run in `sin1` (Singapore), next to the database, provided both projects are pinned there: the API by `server/vercel.json`, the web project by `client/vercel.json` (section 4; Vercel's default is `iad1`, US East). Check both before quoting this. UAE visitors still enter through Vercel's nearest edge.
- Personal data is minimised at rest: guest phones and emails are stored as peppered hashes (`CONTACT_HMAC_PEPPER`), captured values are sealed with `PII_ENC_KEY`, and Trust runs retention purges and data-subject requests.
- UAE-resident hosting is provisioned only when a paying hotel's contract requires it (section 19).

## 16. Deploy checklist

1. **Neon:** the right branch for this deployment (the UAE demo never shares the Indian pilot's database). Pooled and direct URLs copied.
2. **API env:** `ENV`, `SECRET_KEY`, both database URLs, `CRON_SECRET`, `WORKER_TOKEN`, `WEB_ORIGINS`, `PUBLIC_API_ORIGIN`, `CONTACT_HMAC_PEPPER`, `PII_ENC_KEY`, `OAUTH_REDIRECT_BASE`, then vendor keys as they arrive. `NOTIFY_MODE=live` only once WhatsApp and email are configured.
3. **Web env and region:** `CLEFS_API_ORIGIN` and `NEXT_PUBLIC_API_URL` (both required, set before building; `NEXT_PUBLIC_API_URL` has no production default). Function Region `sin1` (section 4). On the API, `FILE_MAX_BYTES=4000000` (Vercel's 4.5 MB body limit, section 5, Files).
4. **Migrations before code.** `MIGRATE_ON_STARTUP` applies them on the first cold start on Vercel. Some migrations must land before the code that uses them (for example `0014_core_inbound_sender.sql` before the webhooks that write `inbound_messages.sender_hmac`); on a VM or before a risky deploy, run `python -m clefs.migrate up` from a laptop first (section 18) and `verify` after.
5. **Cron:** Vercel Cron on Pro, or the cron-job.org pinger on Hobby (section 6). Check `GET /internal/status` shows a tick within the last minute.
6. **Startup warnings:** the function logs name any missing production setting (`production_gaps()`: privacy keys, a localhost or plain-http `PUBLIC_API_ORIGIN`, no `CRON_SECRET`), a weak key, an insecure cookie flag, or a direct database URL that was ignored. Read them after each deploy.
7. **Smoke test:** log in, open the brief, open `/stay/<slug>/<room>`, send a WhatsApp message to the hotel number and see it land in the console, run one tick, and check `/internal/status` for failed jobs and outbox errors.
8. **Preview deployments:** add the preview origin to `WEB_ORIGINS` (or `CORS_ALLOW_ORIGIN_REGEX`), or cookie-authenticated writes from it are refused. Vercel Cron does not call previews; tick them by hand if needed.

Open item to check on the first preview deploy: `/auth/magic/request` does its lookup and delivery in a background task so the response time does not reveal whether an account exists. If the `@vercel/python` adapter waits for the background task before returning, the timing difference comes back; the fix is to enqueue the delivery as a job (CONTRACTS, Part 3, open requests).

## 17. Rollout order

From spec 03 section 7, for moving an existing hotel (the Indian pilots on the legacy app) and onboarding a new UAE pilot. Each step is deployable on its own; do not skip ahead.

1. **Hotfixes first.** The staff-page alert fix; remove the token logs and the GET mint in `/api/realtime`; put the unauthenticated `guests` and `conversation-logs` GETs behind a flag; limit Clarity to the marketing pages.
2. **Foundation and data.** Deploy the foundation migrations, Trust's audit tables and the Service migrations, with backfills batched and resumable. Legacy routes are re-pointed at the adapters; the golden-shape tests (`tests/foundation/test_legacy_contract.py`) must pass before the deploy. Existing hotels keep working through `legacy_open_access`. **Submit the WhatsApp templates to Meta now** (section 8), because approval takes days.
3. **Concierge.** Server prompts, the mint endpoint, the stay-context endpoint and the client cutover, with a one-week soak on one hotel. The knowledge-base import stays in `legacy_text` mode until the owner approves it. Import the menus.
4. **Devices.** Pair devices, set PINs, load the roster. Show the staff-link banner, then redirect personal staff links after 14 days.
5. **SLA.** Run the SLA ladder in shadow mode for 7 days, then switch on live WhatsApp escalation (the templates from step 2 are approved by then).
6. **Voice pilot** on one hotel: the harness first, then the MCP transport, with the sideband worker as the fallback (section 9).
7. **Reviews, guests (consent-gated), the evals gate, pilots.** Journeys start only after the owner approves each template version and billing is confirmed.
8. **Marketing switch**, once `/free-ota-audit` intake works end to end.

The money and growth modules (Profit Guard, Demand Radar, Inquiry Guard, Visibility, Finance, Amenities, Brief, Ask) need no legacy migration: switch each on per hotel in Settings, Modules once its data is flowing (statement imports or forwarding, a rate-shop source, a lead channel, a P&L export). Start every action type at its default autonomy level; the brief suggests promotions after 50 decisions in 30 days with at least 95% approved unedited.

## 18. Local runs against a deployed database

Only when you mean it (seeding the demo branch, a one-off migration, a live phone demo worker). Export everything in the shell; never put a remote URL in `server/.env`:

```sh
cd server
export ENV=production                                  # or preview for the demo branch
export DATABASE_URL='<neon pooled URL of the branch>'
export DATABASE_URL_UNPOOLED='<neon direct URL of the same branch>'
export SECRET_KEY='<the deployment's SECRET_KEY>' CONTACT_HMAC_PEPPER='<...>' PII_ENC_KEY='<...>'
export NOTIFY_MODE=log SMS_ENABLED=false               # unless you really want messages to go out
.venv/bin/python -m clefs.migrate status
```

- Every CLI command prints the target database host and name first. Read it before pressing enter.
- A remote URL that comes from `server/.env` instead of the shell is refused (`RemoteDatabaseRefused`) unless `CLEFS_ALLOW_DOTENV_REMOTE_DB=1` is exported. Do not use that opt-in for production.
- `CONTACT_HMAC_PEPPER` and `PII_ENC_KEY` must match the deployment, or hashed contacts and sealed values written from the laptop will not match the ones the API writes.
- `DATABASE_URL_UNPOOLED` is ignored (with a warning) unless it names the same database, role and password as `DATABASE_URL`, and when it came from `.env` while `DATABASE_URL` was exported.

**Local development never texts or calls real people.** A developer's `server/.env` may hold production-grade values (`SMS_ENABLED`, `SMS_AUTH_KEY`, WhatsApp tokens, a production `PSQL_DATABASE_URL`). Environment variables win over `.env`, so `export NOTIFY_MODE=log SMS_ENABLED=false` is enough to keep routing and journeys from reaching real staff and guests.

### A local QA stack on other ports

Three things trip a stack that does not use the default ports (web 3000, API 8000):

1. **Build the web with the API origin.** The `/api/clefs` rewrite is fixed when `next build` runs, so export `CLEFS_API_ORIGIN` (and `NEXT_PUBLIC_API_URL` if the browser reaches the API elsewhere) before building. `next start` with a different value still proxies to the built origin, and prints a warning saying so.
2. **Allow the web origin on the API.** `WEB_ORIGINS` defaults to `http://localhost:3000,http://127.0.0.1:3000`. A web server on another port gets `403 csrf_failed` ("Cross-site request blocked") on sign-in and on guest writes from a browser that carries a Clefs cookie.
3. **Run the worker or tick the cron.** Approved actions (a quote send, a reply) run as jobs. Start `python -m clefs.worker`, or call `/internal/cron/tick`, or they stay queued.

```sh
# API on :8811, web on :3111, same local database
cd server && DATABASE_URL=postgresql://localhost/clefs_dev WEB_ORIGINS=http://localhost:3111 \
  .venv/bin/uvicorn main:app --port 8811 > /tmp/api.log 2>&1 &
cd server && DATABASE_URL=postgresql://localhost/clefs_dev .venv/bin/python -m clefs.worker > /tmp/worker.log 2>&1 &
cd client && CLEFS_API_ORIGIN=http://localhost:8811 npx next build && CLEFS_API_ORIGIN=http://localhost:8811 npx next start -p 3111
```

## 19. Appendix: Docker Compose (UAE-resident option)

Not provisioned for the demo or the first pilots. It exists so a hotel whose contract requires UAE residency can be served from a single VM (AWS me-central-1 or Azure UAE North) without code changes. The same image also runs on a laptop.

```sh
cd server
docker compose up --build                                            # laptop: http://localhost:8000
API_DOMAIN=api.example.ae docker compose --profile tls up -d --build  # VM with TLS through Caddy
```

- Services: `postgres` (16), `api` (2 uvicorn workers with `--proxy-headers`), `worker`, and `caddy` (profile `tls`). The `api` service runs `python -m clefs.migrate up` before uvicorn starts (the Procfile's `release` step does the same on a Procfile host), and the worker ticks every 15 seconds, so a VM needs no cron.
- Secrets go in `server/.env.compose` (at least `ENV` and `SECRET_KEY`; `ENV=development` only on a laptop). The file is git-ignored and docker-ignored, so `COPY . .` never bakes it into the image. Compose sets `CLEFS_DOTENV=0`, and its `environment:` entries override that file.
- Only Caddy listens on public interfaces. Postgres and the API bind to `127.0.0.1`, so the `X-Forwarded-For` that uvicorn trusts can only come from Caddy, which overwrites it.
- For a managed database (RDS), change `DATABASE_URL` and `DATABASE_URL_UNPOOLED` in the `api` and `worker` `environment` blocks, then drop the `postgres` service and the `depends_on` that points at it.
- AI still runs in the United States (Google Gemini API) on this topology, unless Vertex AI is pinned to a region; the `transfer_abroad` consent stays.

## 20. Google Cloud Run

The API also runs on Google Cloud Run from `server/Dockerfile` (uvicorn listens on `$PORT`, which Cloud Run sets). From `server/`:

```bash
gcloud run deploy clefs-api --source . --region asia-southeast1 --allow-unauthenticated \
  --set-env-vars ENV=production,GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=<project>,GOOGLE_CLOUD_LOCATION=asia-southeast1 \
  --set-secrets DATABASE_URL=clefs-database-url:latest,DATABASE_URL_UNPOOLED=clefs-database-url-unpooled:latest,SECRET_KEY=clefs-secret-key:latest,CRON_SECRET=clefs-cron-secret:latest
```

- **AI:** either `GEMINI_API_KEY` (kept in Secret Manager, passed with `--set-secrets`) or Vertex AI as above (`GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`), with `roles/aiplatform.user` on the service's service account.
- **Everything else** from section 5 applies unchanged: `DATABASE_URL`, `DATABASE_URL_UNPOOLED`, `SECRET_KEY`, `CRON_SECRET`, `OAUTH_REDIRECT_BASE`, vendor keys. Put secrets in Secret Manager, never in `--set-env-vars`.
- **Migrations:** run `python -m clefs.migrate up` against the database before sending traffic to a new revision.
- **Job tick:** Cloud Scheduler replaces Vercel Cron: `gcloud scheduler jobs create http clefs-tick --location asia-southeast1 --schedule "* * * * *" --uri https://<service-url>/internal/cron/tick --http-method GET --headers "Authorization=Bearer <CRON_SECRET>"`.
- **Web project:** point `CLEFS_API_ORIGIN` and `NEXT_PUBLIC_API_URL` at the Cloud Run URL (or its custom domain).
- **Region:** `asia-southeast1` (Singapore) sits next to the Neon database; `me-central1` (Doha) is the nearest Google Cloud region to the UAE.

### The AI Builder Cup prototype (project CLEFS, asia-south1)

| Piece | Setting |
|---|---|
| Web | Cloud Run service `clefs` from `client/Dockerfile`, built by `client/cloudbuild.yaml` with the API origin as a build argument |
| API | Cloud Run service `clefs-api` from `server/Dockerfile`, Cloud SQL `clefs-db` attached, secrets from Secret Manager, `--timeout=3600` (guest voice runs over a WebSocket), `--concurrency=80`, `--min-instances=1` while judging |
| Database | Cloud SQL for PostgreSQL 17 `clefs-db`; migrations run at startup (`MIGRATE_ON_STARTUP=true`) |
| AI | Vertex AI (`GOOGLE_GENAI_USE_VERTEXAI=true`, location `global`), `AI_MODEL_DEFAULT=gemini-3.5-flash`, `AI_MODEL_FAST=gemini-3.1-flash-lite`; voice through the Live relay (`gemini-3.8-live`, `us-central1`) |
| Photos | `PROOF_BUCKET=disco-axis-442314-k2-clefs-proof` (asia-south1, deleted after 7 days) |
| Job tick | Cloud Scheduler `clefs-tick`, every minute, `POST /internal/cron/tick?budget_s=25` with the cron bearer |
| Demo data | Cloud Run job `clefs-seed` (`python -m clefs.cli seed-demo`), about 15 minutes; `/try` gives reviewers one-tap access to the guest, staff and manager screens of the demo hotel |
