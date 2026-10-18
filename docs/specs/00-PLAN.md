# Clefs Owner Autopilot: UAE build plan

## Context

Two strategy reports reach the same verdict. As a pure "AI front desk", Clefs sits in a crowded category (Canary, HiJiffy, Akia, Duve, Rannkly and others). It should become the **owner's profit-and-service control layer for hotels**. The core loop is **detect → explain → approve → execute → verify**, delivered as a daily owner brief.

**Market vs product scope:** the product is single-property and works for any hotel. The same demo and product can be shown to independent hotels and to chain-brand properties. The sales focus stays on independents, because they buy fast. **Nothing organization- or chain-specific is built** (no org hierarchy, portfolio views or SSO). The effort goes into making the single-property product excellent.

The existing guest concierge stays. It becomes the Service Control module and the capture layer, not the headline.

The user wants the **union of both reports' features built now**, so any owner pain point can be demoed and piloted. Where a related feature is cheap to add, it gets added too. The first market is the **UAE for one month (not India)**: AED, Asia/Dubai, Arabic, PDPL, and the Dubai event calendar.

Decisions the user made:
- **Adjacencies get lightweight versions.** That means review signals with drafted replies, repeat-guest recognition with a direct-rebooking offer, event-led campaign drafts, and rate recommendations with guardrails. None of these includes a points engine, social scheduler, full RMS or booking engine.
- **All four external services are in scope:** Meta WhatsApp Cloud API, in-room phone AI over SIP, the PredictHQ events API and a rate-shopping data feed. Each needs a graceful fallback when its key is absent.
- **The marketing site gets repositioned now** for the UAE owner pitch.

**Addendum (2026-09-28): review of the hotel-growth webinar.**

The webinar was mostly a course pitch, but four of its ideas held up under research. They are added in lite, money-verified form:
- Inquiry Guard
- Google & AI visibility
- Guest journeys with compliant review requests
- Owner P&L pulse

Two small items were added at the same time, because they are cheap and foundational:
- **Autonomy dial:** observe, then suggest, then approve, then auto, set per action type.
- **Sourced-claims registry** for the website.

**Rejected from the webinar:**
- The "happy guests to Google, unhappy guests to a form" review-gating funnel. Google and Tripadvisor ban it.
- Review-velocity pushes and "rank #1" promises.
- GOPPAR +10/25/50% milestone tiers. These are not USALI, and the 2026 UAE collapse would make 2027 year-on-year gains misleading.
- A referral programme.
- A full CRM pipeline UI.
- Automated outbound calling (UAE telemarketing rules).
- llms.txt and scraping consumer AI apps.

**Webinar statistics.** Several are misattributed or unsourced: the 8.6M workforce gap, the 46/29/14/11 discovery split, "89% expect AI bookings" and "repeat guests 20-40%". These must never appear in marketing.

**2026 UAE context.** UAE RevPAR is down 31.8% year to date (JLL) and UAE GOPPAR is down 47.8% (HotStats). Positioning should therefore lean on margin control, not growth or labour-shortage framing.

What exists today, from exploration:
- **Server:** one-file FastAPI (`server/main.py`, about 1800 lines) with raw psycopg2 and startup DDL. There are no migrations, **no auth anywhere**, no jobs, no tests and no server-side LLM calls. Connections leak.
- **Client:** Next.js 16. `StaffDashboard.tsx` is 2865 lines. The guest page `ClefsConcierge.tsx` builds the system prompt **in the browser**. The menu is hardcoded in INR, the timezone is hardcoded to `Asia/Kolkata`, and there is no i18n or RTL.
- **Data:** hotels are keyed by `hotel_slug` and there are no UAE hotels.

## Feature inventory (union of both reports, grouped by module)

Legend:
- **R1** = "Product Strategy, Competitive Gap, and 90-Day GTM Plan"
- **R2** = "Stay in Hotel-Tech, Narrow Hard"
- **+** = related feature added because it is cheap and strengthens a core one

| Module | Features |
|---|---|
| **Owner Brief & Action Loop** | 7:00 Asia/Dubai WhatsApp brief plus a mobile web brief. It shows the top 5 exceptions: money lost, money available, service risk, reviews and verified results (R1, R2). Each item shows AED impact, confidence, evidence, recommended action, owner and deadline (R1). One-tap approve/adjust/assign/dispute/remind/ignore (R1). Escalation when an approved action is not done (R1). Weekly verified-value report (R1). Action ledger recommendation→decision→execution→verification, which is the outcome dataset moat (R1). + WhatsApp "STOP/pause" and per-recipient quiet hours |
| **Profit Guard (OTA)** | Booking.com, Agoda and Expedia imports (CSV/XLSX/PDF, email forward) (R1, R2). Header-alias parsers plus AI column mapping (+). Contract/commission rule ingestion with AI extraction and human confirmation (R1). Expected-vs-actual payout reconciliation (R1). 15 leakage detectors L1-L15 (research). UAE fee engine: service charge, municipality fee and VAT, Tourism Dirham by category, 3 stacking conventions, date-effective 2026 relief (research). Tourism Dirham compliance checks plus a monthly TD export (+). Net ADR by channel (R1). Rate-parity evidence records (R1). Dispute packets (R1). Recovery ledger with auto-verification (R1). Virtual-card expiry countdowns (+). Dispute-deadline countdowns (+). **Free "Audit one OTA statement" public lead magnet** (R1, R2) |
| **Demand Radar** | Events from the curated UAE venue/holiday calendar, PredictHQ Events plus Features API, AI web-search discovery with citations, Ticketmaster AE, ICS and manual (R1, R2, research). Dedup, change history, rescheduled/cancelled status (R1, research). Radius/corridor bands (DWTC/DEC/Yas/Sharjah) (R1, research). Property relevance model that learns from dismissals (R1). Comp-set rates via SerpApi Google Hotels (key exists), MakCorps/Xotelo adapters, and Lighthouse/RateGain CSV plus manual, labelled "indicative" (R1, R2). On-the-books/pace (R1). Recommendations with min/max guardrails (R1). Outcome measurement (R1). 30/60/90-day heatmap and top-5 weekly alerts (R1). + From one event: pre-arrival offer draft, housekeeping staffing guidance, KB/transport FAQ suggestion, event-led campaign drafts (R1 lightweight). + Google Hotel Center direct-feed readiness checklist. Google's **third-party rates for Hotel Ads and PMax Travel Goals end on 30 Sep 2026**; free booking links are unaffected, but only a Hotel Center feed puts an "official site" price on Google |
| **Service Control** | One queue for QR voice/text, menu, in-room phone AI, front-desk phone console, WhatsApp and staff (R1, R2). Floor routing (R1, R2). Shared floor/department devices with staff PIN shift login, handover notes, shift templates and roster (R1, R2). Acknowledge step. Ack ladder 3/5/10 plus per-category resolution SLAs. Maintenance L1/L2/L3 (R1, R2, research). Repeating alert until acknowledged, offline-safe retries, escalation to supervisor/MOD/GM via WhatsApp (R1). Load balancing and reassignment (R1). Sensitive-request hard routes with UAE emergency numbers (R1, research). Service recovery with a compensation authority matrix (+). Guest status tracker with "resolved / not resolved" and reopen (R1). Analytics: first response, completion, reopen, SLA breach, confirmed resolution by staff/floor/shift/dept/room type/language (R1). + Staff SOS button. + Labour guardrails (48 h/week, Ramadan hours, midday outdoor ban) |
| **Amenity Leakage** | Amenity catalog with AED unit cost, standard per occupied room and par. Store issuance log (manual/CSV). Occupancy. Requests↔issuance variance in units and AED by floor/shift/staff. CPOR. Signal on variance (R1, R2). Cross-hotel benchmarks per 100 occupied room-nights when there are ≥3 hotels (R2). + Single-use-plastic flag (Dubai 2026 ban) |
| **Guest Concierge v2** | Server-side prompt/tool builder shared by web and phone (fixes the browser-written prompt and the open key-minting route). Structured bilingual KB with approval and a console KB editor (R1). Hotel-knowledge-only answers, confidence, `escalate_to_human` tool with full context (R1). Sensitive-request safety (R1). Guest UI in EN/AR (RTL)/RU/HI/UR/ZH/DE/FR (R2 UAE, research). Fix the roster leak. Consent notice (PDPL) (R2). Menu and orders from the DB in AED with upsell tracking: shown/accepted/paid/fulfilled/refunded (R1). WebRTC ICE probe with fallback to text (research) |
| **In-room Phone AI** | Gemini Live API phone webhook, room from the `From` extension allowlist, phone-variant prompt with read-back, MCP tool server (create_ticket, status, hotel info, transfer), optional sideband worker for transcripts/DTMF, call records, PBX setup recipes in the console, local simulator (R2, research) |
| **Reviews (lightweight)** | SerpAPI Google reviews (reuses review-intel), GBP API (OAuth, draft-and-approve, 30-day raw-text TTL), Tripadvisor Content API, CSV/email ingest. Themes mapped to departments and linked to requests. Negative review → signal plus drafted reply (R1, R2) |
| **Guests (lightweight loyalty)** | Repeat-guest recognition keyed by hashed phone/email with consent. "Returning guest" alert to staff. Direct-rebooking offer template, sent only in 09:00-18:00 marketing hours (R1). Restores the "remembers your guests" claim only once it works |
| **Guest Journeys (webinar, lite)** | A fixed library of 8 steps (no campaign builder) that knows which channel the booking came from and what the guest consented to. The steps are listed under Guests in the module summary below. Rules: never market to OTA alias emails or Booking.com contact data (GDT 2.9.3). Caps: 1 marketing message pre-stay, 2 per 30 days after, 1 per day. Handles Meta error 131049. Utility vs marketing templates are split by Meta category. The repeat-guest share is measured from the PMS, not claimed |
| **Inquiry Guard (webinar, lite)** | **Intake:** WhatsApp (keeps ad referral data and `ctwa_clid`), email forward, an embeddable website form, Meta `leadgen` and Google lead-form webhooks, and a phone-desk "log inquiry" button. An LLM extracts dates, pax, segment (FIT/corporate/group/long-stay/event) and language.<br>**SLAs:** first response per segment (FIT ≤15 min, group ≤4 h, RFP ≤24 h).<br>**Brief:** "AED at risk" = room-nights × expected ADR × stage probability.<br>**Quotes:** AI-drafted, with Dubai tax lines, TD capped at 30 nights, validity, deposit, cancellation terms and group attrition, priced inside Demand Radar guardrails. Auto-send only at or above BAR with no discount; otherwise approval.<br>**Payment links:** N-Genius, Telr, Stripe or Tap behind one adapter.<br>**Verification:** payment plus a PMS reservation match. OTA-leak detection runs internally only, within Booking.com's 30-day data window.<br>**Outcomes:** lost-reason codes and a weighted pipeline. No pipeline UI beyond a list and filters |
| **Visibility (webinar, lite)** | **Google Hotels:** organic position (sponsored removed), plus **official-site price present?** and **direct-vs-OTA parity on Google**. Reuses Demand Radar's `google_hotels` calls, and parity gaps become ledger tasks.<br>**GBP pulse:** Performance API views/actions and keywords, UTM on `websiteUri` so Google → direct bookings → commission avoided, a lodging-facts sync (`updateLodging`, verified via `getGoogleUpdated`), and review replies via Reviews.<br>**Map rank:** an internal-only KPI via Places Text Search IDs-only (free), calibrated monthly with SerpApi.<br>**AI visibility radar,** weekly:<br>• Engines: Gemini with Grounding with Google Search (UAE location in the prompt), Perplexity Sonar, SerpApi `google_ai_mode`. Gemini runs only through the Gemini API, and its grounding display and storage terms are reviewed by legal before launch.<br>• Prompts are auto-built from the hotel profile, comp set and upcoming events, in EN and AR.<br>• Outputs: mention rate with a Wilson CI, share of voice, and cited sources.<br>• **AI fact errors** checked against the hotel's KB, which become ledger fixes.<br>• Labelled "API simulation".<br>**Site health:** CrUX/PSI Core Web Vitals and a Hotel JSON-LD check |
| **Owner P&L pulse (webinar, lite)** | **Weekly (no P&L needed):** NRevPAR = (rooms revenue − distribution cost) ÷ available rooms, plus OTA cost per occupied room, cancellation leakage and recovery cost.<br>**Monthly:** P&L or trial balance via XLSX/CSV/PDF upload, or Xero/QuickBooks connectors (Zoho next; Tally upload-only). AI mapping to USALI 12th ed. with owner or accountant confirmation, and a mapping memory. Reconciliation checks: P&L vs PMS rooms revenue, and OTA booked net.<br>**Outputs:** GOP, GOP margin, GOPPAR, TRevPAR, flow-through, labour %, and the top 3 cost anomalies as actions.<br>**Headline ROI = "Clefs-verified profit"** (ledger). GOPPAR is shown only as market-indexed context. No milestone tiers, and no training on Xero data (Xero ToS) |
| **Ask Clefs** | Natural-language questions over a governed metric registry. The LLM picks the metric, dimensions and filters; there is no raw SQL. Console plus WhatsApp free text. Saved views (R1, R2) |
| **Trust & Privacy** | Per-hotel concierge eval suite with a readiness gate (R1). Append-only audit log and viewer (R1). RBAC with PII masking (R1). PDPL consents (recording, marketing, AI analytics, transfer abroad). DSR handling. Retention policies plus a nightly purge. Tenant export. DPA and sub-processor pages (R1, R2, research) |
| **Pilot & Benchmarks** | Pilot scorecard (modules, baseline, success criteria, conversion price) and an auto-generated end-of-pilot ROI report (R1, R2). Anonymized cross-hotel benchmarks (R2) |
| **Marketing (UAE)** | New homepage ("Find the profit leaks your hotel systems miss."). Profit Guard / Demand Radar / Service Control pages. `/free-ota-audit`, whose report also carries a small "AI snapshot" section. A second lead magnet: **"Google free-booking-link and parity check"**. `/security` with a data-flow diagram. `/pilot`. AED ROI calculators: desk hours plus an OTA leakage estimator. UAE FAQ. Remove "No calls" and "Remembers your guests". Metadata, JSON-LD and sitemap (R1, R2). The existing four SEO pages are kept.<br>**+ Claims registry** (`lib/claims.ts`), fields: claim_id, exact quote, source URL, geography, period, expiry, status, allowed surfaces. The CI linter fails on any %/AED/$/₹ number without a `claim_id`. Allowed stats are listed in Canon item 21.<br>**+ "UAE Independent Hotel OTA Leakage Index"**, built from consented, anonymized audit results. It stays hidden until n is at least 30 |

**Autonomy dial** (foundation, applies to every action type):
- Levels: `off`, `observe`, `suggest`, `approve`, `auto_within_limits`, `auto`.
- Defaults:
  - Utility journey sends: auto after the owner approves the template.
  - Marketing offers: approve the campaign, then auto within limits.
  - Refunds, folio, rate and OTA changes: always approve.
  - Review replies: 4-5★ start at suggest; 1-3★ always need approval.
  - Recovery gestures: suggest, auto only up to AED X once per stay.
- Promotion is *suggested* after 50 decisions in 30 days with at least 95% approved unedited.
- Automatic demotion on a complaint, an opt-out spike, a Meta quality drop or an eval regression.
- The ledger records `dial_level_at_execution`, `policy_version`, `template_id`/category, `consent_basis` and `reversal_handle`.

Explicitly **not** built:
- **Per both reports:** points or tier loyalty, a social scheduler, a full reputation suite, a full RMS with auto-push, a booking engine, a PMS or POS, a native-language staff UI, WhatsApp calling (blocked in the UAE), and uncontrolled local travel booking.
- **Per the webinar review:**
  - Review gating, review-velocity targets and incentives.
  - "Rank #1" promises and GOPPAR milestone tiers.
  - A referral programme.
  - A full CRM pipeline UI, lead scoring and banquet diaries or BEOs (integrate Tripleseat or the PMS instead).
  - Instagram DMs (phase 2, after App Review).
  - Automated outbound calls.
  - A standalone rank-tracking product or heatmap UI.
  - Gemini-grounded visibility checks and scraping consumer AI apps.
  - llms.txt generators and a GBP post scheduler.

## UAE defaults (seed data; every row carries `source_url`)

**Locale and currency**
- Timezone `Asia/Dubai`, UTC+4, no DST. The brief cron runs at `0 3 * * *` UTC.
- Money is stored as AED integer fils (ISO 784).
- The USD peg is 3.6725.

**Dubai fee stack**
- The stack is 10% service charge, 7% municipality fee (Decree 27/2019) and 5% VAT.
- **Tourism Dirham** is charged per room per night, by category:

  | Category | AED |
  |---|---|
  | 5★ | 20 |
  | 4★ | 15 |
  | 3★ / 2★ | 10 |
  | 1★ | 7 |
  | Luxury hotel apartment | 20 |
  | Deluxe hotel apartment | 15 |
  | Standard hotel apartment | 10 |

- TD is capped at 30 consecutive nights, applies only after check-in and sits outside VAT. It is remitted before the 16th of the following month.
- Each hotel stores its stacking convention. Convention A is compound, with factor 1.23585.
- Fee rules are **date-effective** because the Dubai 2026 relief dates are unverified. An owner toggle and a banner cover this.

**Other emirates**
- Abu Dhabi: 4% tourism fee plus 4% municipality fee, with no per-night fee.
- The other emirates are owner-configured with `verified_by`.

**OTA rules**
- Booking.com charges commission on room price plus VAT only, as long as the municipality fee and TD are configured as charges.
- Agoda charges commission on the tax-inclusive RSR.
- Deadlines:
  - Booking.com: 48 h after check-out for no-shows and changes; a VCC must be charged within 12 months.
  - Expedia: EVC expires 180 days after check-out; Hotel Collect bookings must be reconciled by the 4th of the next month.
  - Agoda: UPC must be charged within 24 h.

**Emergency numbers:** 999 police, 998 ambulance, 997 civil defence, 996 coast guard, 995 search and rescue, 991 electricity/water.

**Week and demand calendar**
- The weekend is Sat-Sun, and leisure nights are Fri and Sat.
- Hijri holidays are flagged `moon_dependent` with a ±1 day window.
- Seasonality baselines use 2024-25, because the 2026 conflict distorted history.

**WhatsApp**
- The brief is a utility template with no promotional content.
- Inside the 24 h window, use reply buttons (max 3) or a list (max 10 rows).
- Deduplicate on `messages[].id`, keep at least 6 s between messages to the same recipient, and verify the HMAC signature.
- Service messages are billable from 2026-10-01.
- Marketing sends go out only 09:00-18:00 Dubai time.

**PDPL:** consent is the default and there is no legitimate-interest basis. Consents for recording, marketing, AI analytics and transfer abroad are kept separate. Neon has no UAE region, so the demo and pilots rely on explicit `transfer_abroad` consent. UAE-resident hosting happens only when a customer contract requires it.

## Architecture (foundation)

### Server: `server/clefs/` package
- **`main.py`** shrinks to `from clefs.app import create_app; app = create_app()`, so the Vercel entrypoint is unchanged.
- **`registry.py`** pre-declares all 15 modules in `INSTALLED_MODULES`, each with its migration range and table prefix:

  | Module | Migrations | Table prefix |
  |---|---|---|
  | core | 0001-0099 | none |
  | service | 0100-0199 | `svc_` |
  | concierge | 0200-0299 | `cc_` |
  | profit_guard | 0300-0399 | `pfg_` |
  | demand | 0400-0499 | `dr_` |
  | amenities | 0500-0549 | `amn_` |
  | upsell | 0550-0599 | `ups_` |
  | reviews | 0600-0649 | `rev_` |
  | guests | 0650-0699 | `gst_` |
  | brief | 0700-0749 | `brf_` |
  | ask | 0750-0799 | `ask_` |
  | trust | 0800-0849 | `trs_` |
  | voice | 0850-0899 | `vc_` |
  | visibility | 0900-0949 | `vis_` |
  | inquiries | 0950-0999 | `inq_` |
  | finance | 1000-1049 | `fin_` |
  | integrator hotfixes | 1900-1999 | none |

  Each module's `module.py` exports `MODULE(routers, permissions, worker_services)`. `CLEFS_DISABLED_MODULES` turns modules off.
- **`db.py`:** `ThreadedConnectionPool` plus a blocking semaphore, and `with db() as cur` (RealDictCursor, commit/rollback/return). This fixes the existing connection leaks.
- **`migrate.py`:** numbered `.sql`/`.py` files per module, a `schema_migrations` table with checksums, and an advisory lock on the unpooled URL. It applies every unapplied version in order. `0001` is the current `init_db` DDL verbatim, so it is a no-op on prod.
- **Legacy routes move out of `main.py` now** into `modules/service/legacy.py`, `modules/concierge/legacy.py` and `modules/guests/legacy.py`. Paths and JSON shapes stay the same, guarded by golden tests written *before* the move. Two fixes land with the move:
  - The hardcoded `Asia/Kolkata` becomes the hotel's timezone.
  - `GET /hotels/{slug}` returns only projected columns.
- **Auth** (`auth/`):
  - Tables: `users`, `hotel_memberships` (owner/gm/finance/revenue/dept_head/front_desk/staff, plus `departments[]`/`floors[]` scoping; one user can belong to several hotels), `auth_sessions` with opaque SHA-256-hashed tokens, `devices` with pairing codes, `device_sessions`, `magic_links`, and `staff_members.pin_hash`.
  - Passwords and PINs use `hashlib.scrypt`. A PIN only works on a paired device and locks after 5 failures.
  - **httpOnly cookie on the web origin via a Next rewrite `/api/clefs/*`.** SSE and large uploads use 60 s HMAC tickets. Checks are Origin-based (CSRF) and CORS uses an allowlist.
  - Dependencies: `require_perm()`, `require_device()`, `require_perm_or_device()`, `require_legacy_or()`, `require_service()`, `acting_staff()`.
  - Permissions are declared per module; `/auth/me` returns the resolved list.
  - Magic links are exchanged by POST only, because WhatsApp link previews would burn a GET.
  - `hotels.legacy_open_access=TRUE` for existing Indian hotels keeps today's demo flows working. New UAE hotels are locked by default.
- **Guest QR stays public but safe.**
  - `/public/stay/{slug}/{room}` returns one room's minimal context, which fixes the roster leak.
  - New QR codes carry `?k=` HMAC keys; old printed QRs keep working without PII.
  - Rate limiting is DB-backed.
- **Hotel settings:** `hotels` gets `country`, `emirate`, `timezone`, `currency`, `locale`, `guest_languages`, `weekend_days`, `lat`/`lng`, `business_day_cutoff`, `brief_time_local`, `tax_profile`, WhatsApp ids, `inbound_email_alias` and `status`. `hotel_module_settings` (JSONB, validated by each module's Pydantic model) means modules never ALTER `hotels`.
- **Cross-module contracts**, frozen in the foundation (see `CONTRACTS.md`):
  - `signals`: `upsert_signal`/`resolve_missing` with a dedupe key, AED impact, confidence, evidence list, recommended action, owner role and due date.
  - `actions` + `action_events`: the state machine from Canon item 7. It has `register_action_type(executor, verifier, verify_after)`, `decide()`, `mark_done()` and `record_verification()`, and holds the outcome dataset.
  - Metrics registry: `register_metric(Metric)` with parameterized SQL only, `compile_metric`/`run_metric` running read-only with a 5 s timeout and hotel-timezone buckets.
  - Jobs: `@scheduled(every|daily_at hotel-local|weekly_at)`, `@task`, `enqueue(run_at=…)` for SLA timers, one `tick()`, `job_runs` and `job_queue` with SKIP LOCKED.
  - Events: `publish()` via Postgres `NOTIFY`, delivered on commit and filtered by audience. Legacy SSE event names are kept.
  - Notifications: WhatsApp template/text/interactive/document, email (Postmark), SMS (msg91/twilio), an outbox, and `NOTIFY_MODE=live|log|capture`.
  - Inbound WhatsApp and email webhooks are owned by the foundation, which dispatches them to module handlers via `@on_whatsapp`/`@on_inbound_email`.
  - Smaller helpers: `audit()`, `files` (bytea behind a `FileStore` interface, 25 MB), `ai.structured/text/tool_call/web_search` (Responses API, model from env, fake backend for tests), `core/money.py` (Decimal, fils, fx with the 3.6725 peg fallback, tax compose/decompose), `core/time.py` (hotel-local business dates, weekend), `core/consent.py`, `core/privacy.register_pii`.
  - Shared tables created in Wave 0a, because Service needs them in parallel with the domain lane:
    - `room_types`
    - `hotel_rooms`, with the union columns: floor, wing, room_type_code, bedrooms, extension, service_state, `qr_key_version`, active
    - `channels`
    
    The rest of `core/stays` lands in Wave 0b.
  - The `service_requests_v1` contract view is what Amenities, Brief and Ask read.
  - Each module's `api.py` has stubs with frozen signatures, for example `service.api.create_request/escalate_to_human/request_status_for_guest`, `concierge.api.build_session/respond_text`, `upsell.api.get_menu/create_order` and `guests.api.guest_context_for_room`.
  - **A module imports only foundation code and other modules' `api.py`.** A contract test enforces this, along with migration ranges, table prefixes and key prefixes.
- **Worker** `python -m clefs.worker`:
  - The scheduler tick runs under an advisory lock, alongside a queue consumer.
  - Each module's `worker_services` run here, including the SIP sideband.
  - It writes heartbeats.
  - `/internal/cron/tick` (Bearer `CRON_SECRET`) is the Vercel fallback.
- **Deps, pre-declared once:** `google-genai`, `httpx`, `python-multipart`, `tzdata`, `openpyxl`, `pypdf`, `icalendar` (+ dev: `pytest`, `pytest-timeout`). Code stays at a Python 3.12 syntax floor.
- **Ops files:** `Dockerfile`, a `Procfile` (web/worker/release), `docker-compose.yml` (pg16, api, worker, caddy) and `.env.example`. Every variable is declared in `settings.py`.

### Client foundation
- **`next.config.mjs`:** adds the `/api/clefs/:path*` rewrite to `CLEFS_API_ORIGIN`. The permanent `/aria` redirect stays.
- **`proxy.ts`:** an optimistic cookie hint only; it does not redirect.
- **Shared libraries:**
  - `app/lib/api.ts`: `apiFetch`/`api.*`, `ApiError`, `openHotelStream` (ticket, re-mint, polling fallback), `uploadFile`, `useApi`.
  - `app/lib/format.ts`: `formatMoney` via `Intl` in the hotel's currency, tz-aware dates, `fmtMins`, `slaColor`. This replaces the hardcoded `en-IN`.
  - `app/lib/session.tsx` (`useSession().can(perm)`), `hotel.tsx`, `types.ts`, `i18n.ts` (`dirFor`, RTL).
- **Routes and shell:**
  - `app/app/[hotelSlug]/(console)/`: `ConsoleShell` is a 56px top module bar (bottom tabs on mobile), filtered by permissions from `nav.ts`, which pre-declares every module.
  - The landing page is role-based: owner/gm → brief, finance → profit, revenue → demand, dept/front desk → service.
  - Shared devices live at the top-level `/device/[hotelSlug]` (PWA scope), per Canon item 14.
  - `/login` and `/auth/magic/[token]`.
- **StaffDashboard is mounted unchanged as the Service module.** It keeps its own sidebar as the sub-nav. The foundation makes only two edits: `API_BASE` → `API_PREFIX`, and `EventSource` → `openHotelStream`.
- **UI kit `app/ui/`** (every class prefixed `cx-`):
  - Layout: Card, StatTile, Table (mobile cards), Modal, Drawer, Tabs, EmptyState, PageHeader.
  - Inputs and feedback: form fields, useToast, FileDrop.
  - Domain: MoneyText, ConfidencePill, EvidencePanel, SignalCard, ActionBar (approve/adjust/assign/dispute/remind/ignore).
- **Charts:** `charts.tsx` exports its private helpers. The new `app/ui/charts-extra.tsx` adds LineChart (guardrail band, event markers), CalendarHeatmap, DivergingBar, Donut and Sparkline.
- **Lint and scripts:** ESLint moves to the flat `eslint.config.mjs` (`next lint` is removed in Next 16). New scripts are `typecheck` and `lint`.

### Deployment (UAE demo and pilots: existing Vercel + Neon, no new cloud)
- **Database:** keep the existing **Neon project in `aws-ap-southeast-1` (Singapore)**.
  - Neon has no Middle East or India region; Singapore is one of its closest to the UAE.
  - Create a **separate Neon branch/database for the UAE demo** so the design-partner pilot-run data (e.g. Sachika, a design partner, not a client) is never touched.
- **Server:** deploy FastAPI on Vercel with functions **pinned to `sin1`** (`regions` in `vercel.json`), next to the database. Vercel's function default is `iad1` (US East).
  - Vercel currently lists no Dubai compute region. Pinning functions to Mumbai (`bom1`) while the database stays in Singapore would add a cross-region hop to every query.
  - UAE users still enter through Vercel's nearest point of presence.
- **Guest voice and chat:** Live API audio streams from the browser to Gemini directly, so server region barely affects voice latency.
- **Background ticks** (SLA sweep, brief scheduling, jobs): `/internal/cron/tick` with Bearer `CRON_SECRET`.
  - If the Clefs Vercel account is **Pro**, use Vercel Cron every minute. If it is **Hobby** (daily crons only), use a free external per-minute pinger such as cron-job.org.
  - Either way, SLA sweeps also evaluate on read.
- **Realtime:** `EVENTS_BACKEND=local` plus a polling fallback on Vercel.
- **Files:** Postgres bytea.
- **Phone AI demo:** MCP-tool mode runs serverless on Vercel. The sideband worker (transcripts, DTMF) is optional; run it on a laptop during a live demo, or on a free-tier host only if a pilot needs it.
- **UAE-resident hosting** (Azure uaenorth or AWS me-central-1) happens **only if a paying hotel's contract requires it**. Until then, PDPL is covered by explicit `transfer_abroad` consent and an honest residency statement on `/security`. The Dockerfile and Compose files keep that option open, but none of it is provisioned for the demo.
- **Vercel MCP:** the connector in this session is the Fortes Education team, which has no Clefs projects. Deploying needs the Clefs Vercel account (CLI or a reconnected MCP).

## Canon: reconciling the three design specs

The designs were produced independently (foundation, money modules, service/guest). Where they differ, **this section wins**. It is written to `docs/specs/00-CANON.md`.

**Keys and money**
1. **Tenant key** is `hotel_slug TEXT REFERENCES hotels(slug)` everywhere. The service design's `hotel_id` is dropped.
2. **Money** is persisted as `BIGINT` minor units (fils) plus `CHAR(3)` currency in every module, including orders and recovery costs. APIs use Decimal strings. Rates are `NUMERIC(18,8)`.
3. **Time:** legacy tables keep epoch-ms. New tables use `TIMESTAMPTZ`. All bucketing uses the hotel timezone through `core/time.py`.

**Core-owned domain layers.** The money design's shared packages become core, built in Wave 0b:
- **`core/stays`:** `reservations`, `reservation_nights`, `folio_charges`, `occupancy_daily`, `occupancy_by_floor_daily`, `otb_snapshots`, `baseline_exclusions`, `reservation_matches`.
  - `room_types`, `hotel_rooms` and `channels` are created earlier, in Wave 0a.
  - `occupancy_daily` replaces the foundation design's `hotel_daily_stats`.
- **`core/ingest`:** `import_batches`, `import_rows`, `mapping_templates`, alias maps, AI column mapping, PDF extraction with a totals gate, Postmark-style inbound email, a PINT-AE stub.
- **`core/tax`:** `fee_rules` (date-effective, `rule_status`), `hotel_tax_overrides`, `fx_rates` (3.6725 peg), and `decompose`/`compose` for conventions A/B/C with golden values.
  - `td_ledger` and the TD detectors live in Profit Guard.

**Ownership of shared tables**
4. `audit_log` (append-only trigger, `prev_hash`/`row_hash` columns), `consents` and `consent_texts` belong to the **foundation**. Trust adds hash-chain verification, the viewer, DSR, retention and `deletion_log`.
5. **Devices:** the foundation owns `devices`, pairing codes, PIN verification and `device_sessions`. Service owns shift templates, roster, handover, labour rules, incidents and lost & found.
6. **Signals** have `section ∈ {money_lost, money_available, service_risk, reviews, verified, compliance}`, `impact_minor` + currency, confidence, `evidence[]`, `recommended_action`, `owner_role`, `due_at` and a `dedupe_key` upsert. Compliance items never rank into the daily top 5.
7. **Actions** use the money design's richer state machine, implemented in the foundation:
   - Main path: `proposed → approved → assigned → in_progress → done → verified | not_verified`.
   - Side states: `snoozed`, `rejected`, `cancelled`.
   - Dispute path: `submitted → awaiting_counterparty → verified | rejected_by_counterparty → unresolved`.
   - An `action_events` log records every transition.
   - Brief owns reminders and escalation. Each module registers its own verifiers.
8. **Amenity catalog** is `amenities` (`amn_items`). Service intake accepts `amenity_item_id` + qty and resolves items through `amenities.api`.
9. **Menus, outlets, orders, upsell events** belong to `upsell` (`ups_`). Concierge tools call `upsell.api.get_menu/quote/create_order`.
10. **Guest identity** (hashed phone/email, consent-gated profiles, stays links, direct codes) belongs to `guests` (`gst_`). Demand campaign audiences come from `guests.api.marketing_audience()`.

**Modules and routes**
11. **Voice** is its own module (`voice`, `vc_`, 0850-0899), built by the Concierge lane.
12. **Weather:** Demand owns `dr_weather` (Open-Meteo, no key) and exposes `demand.api.outdoor_temp_c(hotel, at)`. Service uses it to escalate AC requests to L1 above 40°C, falling back to a heuristic for June-September 11:00-17:00.
13. **Server route shapes:**
    - console: `/hotels/{slug}/<module>/…`
    - devices: `/device/…`
    - guests and leads: `/public/…`
    - `/webhooks/{whatsapp,voice/incoming,email/postmark}`
    - `/internal/{cron/tick,status}`
    - The browser always calls through `/api/clefs`.
14. **Client routes:**
    - `(console)/{brief,service,concierge(+voice),profit,demand,amenities,menu,reviews,guests(+journeys),inquiries,visibility,finance,ask,trust,settings}`
    - Public inquiry widget script: `/w/inquiry.js`. Public lead-magnet page: `/google-check`.
    - The device app is top-level at **`/device/[hotelSlug]`**, for PWA scope.
    - Public pages: `/free-ota-audit` and `/free-ota-audit/r/[token]` (owned by Profit Guard), and `/brief/[token]` (magic link, owned by Brief).
    - The StaffDashboard lives at `(console)/service` (full UI kept).
15. **Build-time checks:** model names are read from env and verified at build time. The web concierge currently uses `gemini-2.5-flash-native-audio-preview-09-2025`. Phone calls use the same Live API model unless a line overrides it. The phone bridge, call-end and DTMF events also need checking. The code falls back to the sideband worker.
16. **Claims** are gated by flags. "Remembers your guests" returns only when `claims.guest_memory` is true, which needs guests recognition to be live.

**Additions from the webinar review**
17. **Autonomy dial** lives in the foundation `actions` module:
    - Table `action_autonomy(hotel_slug, action_type, level, limits JSONB, updated_by)`, plus the ledger fields in the autonomy dial section above.
    - `decide()` consults the dial.
    - Brief shows promotion and demotion suggestions.
18. **Brief cadence:**
    - Daily: money at risk only (Profit Guard deadlines, unanswered inquiries, parity gaps, service L1).
    - Weekly: visibility, rank, AI share of voice, NRevPAR trend, review themes.
    - Monthly: P&L pulse.
19. **OTA-sourced guest data rules:**
    - Booking.com contact data is available for 30 days only (GDT 2.2.6), and unsolicited messages are banned (GDT 2.9.3).
    - OTA alias emails (`@guest.booking.com`, `@m.expediapartnercentral.com`) never receive marketing. OTA guests become direct contacts only through their own contact details and consent, captured by the concierge, the registration card or a day-1 reply.
    - Inquiry-to-OTA leak matching runs within that 30-day window, and its output stays internal.
20. **Visibility engines:**
    - Used: Gemini with Grounding with Google Search, Perplexity Sonar (terms to be reviewed by legal before launch), and SerpApi `google_ai_mode`/`google_hotels`.
    - Never used: Gemini grounding (its terms forbid storing or analysing results) and scraping consumer apps.
    - The core rank metric uses the official Places API. SerpApi stays a vendor risk (Google v. SerpApi was dismissed 2026-07-20).
21. **Claims allowed on the website now** (each with a `claim_id`):
    - Cloudbeds: 63.4% OTA / 36.6% direct, 21.8% vs 10.6% cancellations, labelled "independent hotels globally, 2025".
    - JLL: UAE RevPAR −31.8% YTD June 2026.
    - Skyscanner: 81% of UAE travellers confident using AI for travel planning.
    - Microsoft: 73.3% UAE generative-AI usage.
    - Booking.com: 67% have "used AI in some aspect of travel".
    - HBR speed-to-lead, labelled as general sales research.
    - DET 2025 inventory.
    - Banned: every webinar statistic listed in the Context addendum.
22. **Marketing copy says "hotels", not "independent hotels only".** The same site and demo work for chain-property GMs. The eyebrow is "For hotels in the UAE". No org or portfolio features are built.
23. **Lead magnets:**
    - Primary: `/free-ota-audit`, whose report also carries a small AI-snapshot section and feeds the Leakage Index.
    - Secondary: the Google free-booking-link and parity check.
    - Nothing else.

## Module designs: summary

The full specs are in `docs/specs/`. This is what each lane delivers.

- **Service Control** (`modules/service`):
  - **Requests:** additive ALTERs to `staff_requests`: state and version, source, language plus original text, floor, room type, stay stage, amenity and qty, severity L1-L3, sensitivity, ack and resolve due dates, escalation level, reopen count, guest confirmation and token hash.
    - State machine: `scheduled/new/assigned/acknowledged/in_progress/completed/cancelled/reopened`. The legacy `status` is derived in the same UPDATE.
    - A `request_events` cursor feed.
    - One `intake.create_request()` for every source: idempotency, a 10-minute semantic merge, DB rate limiting, translation, safety, priority and routing.
  - **Routing:** floor, then department, then supervisor, then front office, with load, language and continuity scoring.
  - **SLA:** `sla_policies` with seeds for the ack ladder (0/3/5/10) and per-category resolution targets. `sla.sweep` runs every minute with coalesced WhatsApp escalations. There is a 7-day shadow mode.
  - **Safety:** lexicons for 8 languages plus an LLM classifier that can only upgrade severity. An EmergencyCard shows UAE numbers. Staff SOS.
  - **Service recovery:** LEARN cards and an AED authority matrix routed through the actions ledger.
  - **Shifts:** roster with shift templates and labour guardrails, and handover at login.
  - **Devices and phone desk:** a device PWA at `/device/[hotelSlug]` (pairing, PIN tiles, queue, IndexedDB offline outbox, repeating alerts, Web Push) and a keyboard-first PhoneEntryConsole.
  - **Staff page alert-suppression hotfix**, then deprecation of the old staff page.
  - **Public guest tracker** endpoints.
  - **Analytics:** v2 rollups in the hotel timezone.
  - **StaffDashboard:** surgical edits only (fields, state chips, SLA countdown, transitions, timezone).
- **Concierge** (`modules/concierge`):
  - **Session and tools:** server `build_session(surface)` shared by web, phone, text fallback and evals. Tools are executed server-side, and the room is bound from the session token.
    - `/api/realtime` becomes a proxy only: the GET minting route and the token logs are deleted.
    - Tools: `notify_staff` v2, `escalate_to_human`, `get_request_status`, `get_menu`, `place_order` (quote then confirm), `search_hotel_info`.
  - **Knowledge:** a structured EN/AR KB with approval, a legacy-text import and an unknowns queue.
  - **Guest privacy:** the stay context endpoint (fixes the roster leak), `?k=` room keys with a limited mode for old QR codes, a ConsentSheet (prewarm only after consent), and Clarity restricted to marketing pages.
  - **i18n:** 8 languages, with RTL on content containers only (never on root or avatar).
  - **Guest UI:** StatusTracker and EmergencyCard. The ICE probe falls back to HTTP text chat, which never enters SPEAKING.
  - **Edits to `ClefsConcierge.tsx`** stay inside the listed safe ranges only.
- **Voice** (`modules/voice` + `server/voice_worker.py`):
  - A webhook with `webhooks.unwrap`, a line secret, a 486 rejection for unknown lines, and an extension-to-room allowlist.
  - A phone prompt and `accept`.
  - A stateless FastMCP server with per-call JWT tools; REFER is SIP-only.
  - A sideband worker for transcripts, safety and DTMF.
  - Call log, a PBX setup guide with recipes, and a local harness with a Gemini Live API stub.
- **Profit Guard** (`modules/profit_guard`):
  - **Data:** contracts (AI extract, then confirm, with clause citations), programmes, channel config, statements and lines, payouts, bank credits, virtual cards, rate evidence.
  - **Detectors:** L1-L15, T1-T7 and TX1, each with a formula, class (recoverable/preventable/structural/compliance), confidence, fingerprint and deadline. Confidence uses a Beta-prior feedback-tuning model.
  - **Outputs:** recovery ledger with auto-verification from credit notes, `channel_economics` (net ADR waterfall), dispute packets (HTML/PDF via fpdf2, extranet text, email), the TD ledger and export, and a pilot scorecard.
  - **Public audit** at `/free-ota-audit`: teaser, then lead unlock, then 30-day purge.
    - The report includes a small AI-snapshot section from `visibility.api`: 5 prompts × 3 engines × 3 runs.
    - Consented, anonymized results feed the **UAE Independent Hotel OTA Leakage Index** aggregate (`pfg_index_samples`). It is published only when n is at least 30, with its methodology.
- **Demand Radar** (`modules/demand`):
  - **Sources:** venues with corridors, and events merged from PredictHQ Events and Features, Ticketmaster, a curated YAML seed for 2026 Q4-2027, AI web discovery (UNVERIFIED, confidence ≤0.6), ICS and manual entry. Dedup and change history are kept.
  - **Scoring:**
    - Relevance: distance decay × corridor × scale × day-of-week fit × segment fit × a learned class weight, calibrated on the hotel's own pickup with 2026 excluded from baselines.
    - Demand index: the four components per day.
  - **Pricing:**
    - Comp rates through SerpApi Google Hotels (default), with CSV Lighthouse/RateGain and Xotelo/MakCorps adapters, labelled "Indicative".
    - `bar_calendar` and guardrails.
    - Recommendations clamped to floor and ceiling with step caps. Outcomes are labelled "observed, not causal".
  - **Other outputs:**
    - Graph actions: pre-arrival offer, staffing gap, KB suggestion, and campaign drafts (09:00-18:00 window, export only).
    - GHC checklist, Open-Meteo weather, and a 30/60/90 heatmap with a day drawer.
- **Amenities** (`modules/amenities`):
  - Items with basis, standard and unit cost (including excise), locations, par levels, issuance log (mobile quick entry and CSV), `request_amenity_lines` (tool, then alias, then nightly LLM linking), and stock counts.
  - Rolling 7-day variance, a 3-condition spike rule, a logging-gap alert, shrinkage-adjusted on-request rates, par recompute, CPOR, and minibar unbilled.
  - Neutral per-staff views. Benchmarks need at least 3 hotels, and show bands only below 5.
- **Upsell** (`modules/upsell`):
  - Outlets, menu items (EN/AR, hours, dietary flags, alcohol), `upsell_pairs`, orders and lines (fils, inclusive or plus-plus fee display) and `upsell_events` funnel.
  - The console menu page includes CSV/PDF import.
  - `FoodMenu.tsx` is rewired to the DB with AED and error handling.
- **Reviews and Guests** (`modules/reviews`, `modules/guests`):
  - **Review sources:** SerpAPI Google (ported from review-intel), GBP OAuth (draft-and-approve, 30-day raw purge), Tripadvisor (5 reviews), CSV presets, forwarded email, and a live Places competitor widget.
  - **Review analysis:** LLM themes mapped to departments and linked to requests, a negative-review signal with a drafted reply, and theme trends.
  - **Guests:** HMAC-hashed profiles gated on `profile_memory` consent, allergies masked as health data, a returning-guest badge in the queue, device and handover, and a consented rebooking offer (09:00-18:00, one per 30 days, STOP).
  - **Journeys, in the guests module (`gst_journey_*` tables):**
    - Steps are triggered by `core/stays` reservation events:

      | Step | When | What | Category |
      |---|---|---|---|
      | J1 | On booking | Confirmation plus preference capture, including own-contact and marketing opt-in | Utility |
      | J2 | T-7 | Planning info | Utility |
      | J3 | T-3 | Offer | Marketing, consent only |
      | J4 | T-1 | Check-in instructions | Utility |
      | J5 | Check-in + ~18 h | "Anything to fix?" | Utility |
      | J6 | Checkout | Internal gate | - |
      | J7 | Checkout + 2 days | Stay-specific feedback request | Utility |
      | J8 | +30 / +60 days | Rebooking | Marketing, consent only |

    - **J5:** a negative reply opens a Service Control ticket.
    - **J6:** open tickets alert the manager, and the recovery attempt happens first.
    - **J7:** the completion page shows the **same** Google and Tripadvisor review links to every guest. There is no sentiment routing and no incentive, and it is budgeted as marketing in case Meta recategorizes it.
    - Templates are bilingual EN/AR, and Meta categories are pre-sorted.
    - All journey sends are logged in the ledger and governed by the autonomy dial.
    - Metrics: journey send and response rates, review velocity (measured only, never targeted), and direct rebooking conversions.
- **Brief and Ask** (`modules/brief`, `modules/ask`):
  - **Cadence tiers** (Canon item 18). The brief shows autonomy-dial promotion and demotion suggestions.
  - **Ranking and delivery:** rank by `I×C×U` with section and detector caps. The template `owner_brief_v1` (utility, promo-word lint, ≤1024-character budget).
  - **WhatsApp flow:** quick replies, then list, then reply buttons, then an Adjust session with bounds. Nonces are HMAC-bound, recipient-bound and single-use.
  - **Messaging infrastructure:** `wa_*` tables, an outbox with 6 s per-recipient spacing and error backoff, and fallback to email, then SMS with no URLs.
  - **Escalation:** at deadline, +4 h, then +24 h.
  - **Reporting:** weekly verified-value report (PDF via media), and a magic-link mobile brief.
  - **Ask:** registry-generated strict tools with no raw SQL, a numeric grounding check, saved views, WhatsApp Q&A inside the 24-hour window, and about 60 eval items.
- **Inquiry Guard** (`modules/inquiries`):
  - **Data:** `inq_inquiries` (source, channel ref, ad referral, `ctwa_clid`, extracted stay, segment, language, stage, SLA due dates), `inq_messages`, `inq_quotes` (lines, taxes, validity, deposit, cancellation, attrition, payment link), `inq_outcomes` (verified reservation id, payment ref, leaked OTA reservation id, lost reason).
  - **Stages:** `new`, `replied`, `quoted`, `payment_pending`, then `confirmed_verified`, `lost` (with reason), `turned_down` or `future`.
  - **Intake adapters:** WhatsApp inbound (a foundation `@on_whatsapp` handler for non-guest senders), email forwarding (`@on_inbound_email`), a public `POST /public/inquiries/{slug}` behind an embeddable `<script>` form widget (rate-limited, with a consent line), and Meta `leadgen` and Google Ads lead-form webhooks (deduped on `lead_id`).
  - **Extraction:** LLM, then dedupe on phone/email.
  - **SLA timers:** `enqueue(run_at)` per segment.
  - **Quote builder:** reads `demand.api.price_guardrails(date, room_type)` and `core/tax.compose`.
  - **Payment adapter:** N-Genius, Telr, Stripe or Tap. Status comes back by webhook.
  - **Verification job:** matches payment to PMS reservation within N days, giving verified direct revenue and commission avoided versus the channel's contracted rate. A later OTA reservation for the same identity is marked as a leak, and its commission cost goes to Profit Guard.
  - **Signals:** `inq.unanswered` (money_available), `inq.quote_awaiting_approval`, `inq.leaked_to_ota`, and a weekly pipeline summary.
  - **Metrics:** response time p50/p90 by channel, inquiry→verified conversion, lost-reason mix, and leak share.
  - **Console:** a list with filters, an inquiry drawer, a quote editor, and a lost-reason picker.
  - **Brief line example:** "2 unanswered > 2 h, AED 5.4k at risk".
- **Visibility** (`modules/visibility`):
  - **Data:** `vis_google_hotels_obs` (query, dates, organic rank, sponsored count, official-price present, official vs min-OTA price), `vis_rank_grid` (Places IDs-only 7×7 × 3-5 keywords weekly), `vis_gbp_daily` (Performance API metrics), `vis_gbp_keywords` (monthly; thresholds never summed), `vis_ai_prompts`, `vis_ai_runs` (engine, model id, locale, raw JSON, citations, mentioned, position), `vis_ai_facts` (claim vs KB truth, error type), and `vis_site_health` (CrUX/PSI, JSON-LD fields).
  - **Jobs:**
    - Weekly: AI radar (15-25 prompts × 3 engines × 4-5 repeats, EN+AR, UAE + top 2 source markets) and rank grid.
    - Daily: the Google Hotels check, sharing calls with Demand Radar's comp-rate fetch.
    - Monthly: SerpApi calibration.
  - **Actions (ledger, dial-governed):**
    - Parity fix task.
    - Tag GBP `websiteUri` with UTM (`locations.patch`).
    - `updateLodging` fact sync (verified by `getGoogleUpdated`).
    - Review reply (via Reviews).
    - Fix an OTA listing fact (a human task).
    - Add a KB FAQ.
  - **Attribution:** UTM-tagged direct bookings, matched in `core/stays`, give "Google sent N clicks → M direct bookings → AED commission avoided".
  - **Display rules:** a minimum sample size before any figure is shown, and confidence intervals always.
  - **Console:** Google, AI answers (with evidence drawer), rank, site health.
  - **Public lead-magnet endpoint** for the free-booking-link and parity check.
  - **Cost target:** about $15-30 per hotel per month.
- **Finance** (`modules/finance`):
  - **Weekly metrics** registered from `core/stays` and Profit Guard `channel_economics`: NRevPAR, OTA cost per occupied room, cancellation leakage.
  - **Monthly P&L:** `fin_import_batches`, `fin_coa_accounts`, `fin_usali_map` (versioned, AI proposal plus confidence and rationale, human confirm, locked reuse), `fin_pl_lines`, `fin_stat_inputs`.
  - **Import:** uses `core/ingest` for XLSX/CSV/PDF. Xero `Reports/ProfitAndLoss` and QuickBooks Reports connectors via OAuth. The Zoho Books adapter is a stub.
  - **Checks:** reconciliation to source net profit, P&L vs PMS rooms revenue within 2%, OTA booked-net detection, pass-through taxes excluded, and depreciation/interest/rent below GOP.
  - **Outputs:** GOP, GOP margin, GOPPAR, TRevPAR, flow-through, labour %, and the top 3 anomalies as actions.
  - **Pilot ROI** = Σ verified ledger outcomes per available room. GOPPAR appears only indexed to the market, with a caveat.
  - **Console:** monthly one-pager, mapping review and trend.
- **Trust** (`modules/trust`):
  - Audit viewer with hash-chain verification, and a PII masking matrix (a test covers every cell).
  - PDPL: consent texts, DSR queue and executors, retention purge with `deletion_log`, and tenant export.
  - Evals: DSL, standard set and auto-KB suite; a realtime-text runner (Responses fallback); deterministic checks plus an LLM judge; a readiness gate that blocks `concierge.live`.
  - Pilot scorecard and printable ROI report; benchmark cohorts.
  - Public pages: `/security`, `/dpa`, `/subprocessors`, `/privacy/request`.
- **Marketing** (Trust lane owns the files):
  - **Homepage rewrite:** hero greeting and avatar code untouched, three product blocks, a proof bar, AED calculators including a new OTA leakage calculator.
  - **New pages** `/profit-guard`, `/demand-radar`, `/service-control` (SolutionPage gains `parent` and `cta` props), `/demand-preview` and `/pilot`, with the four existing SEO pages re-parented under Service Control.
  - **Copy and metadata:** UAE FAQ (the CLAIM RULE is kept), new metadata/JSON-LD/sitemap/robots, and `scripts/check-claims.mjs` (no "No calls", "any language", U+2014, or unsourced %).

**Demo dataset** (Wave 3): `python -m clefs.cli seed-demo`.
- **Property:** "Al Rigga Crest Hotel (DEMO)", Deira, 3★, 80 rooms, 8 floors, 10 two-bedroom family units.
- **Comp set and people:** 5 demo comp hotels, 16 demo staff, and an owner/GM/finance/revenue user each.
- **Data:** 120 days of history from 2026-06-01, forward OTB to 2027-01, and baselines for 2024-25 plus the 2026 distortion.
- **Files:** Booking, Agoda and Expedia statements in three header variants, payouts, bank data, VCCs, a credit note and a PMS folio.
- **Planted cases:** every detector, amenity leak, SLA pattern, review and event has a planted case plus decoys that must not fire. They are recorded in `expected_plants.json`.
- **Additions for the webinar modules:**
  - **Inquiries:** about 60 across channels. Planted cases: unanswered past SLA, a group quote awaiting approval, a lead that leaked to an OTA, and a verified direct booking via a payment-link webhook fixture.
  - **Visibility fixtures:** Google Hotels details with and without an `official` price and a parity gap; GBP Performance series; AI-engine answer fixtures including hotel mentions, competitor wins and 2 planted **fact errors** (wrong check-in time, "no parking").
  - **P&L:** 12 months of synthetic P&L CSV with mixed English/Arabic account names, OTA revenue booked net in one month (planted), and a labour anomaly.
  - **Journeys:** sends for recent stays, including one J5 negative reply that opened a ticket.
- **Deterministic** (seeded RNG, no wall clock), and every name and screen is labelled "(DEMO)" / "Synthetic demo data".

## Build execution (after approval)

**Step 0: branch and specs**
- Create branch `feat/owner-autopilot` from `main`. Nothing is pushed and `main` is not touched.
- Per-lane commits on the lane worktree branches are needed to merge the parallel work. This plan's approval covers those commits.
- Extract into `docs/specs/` from the agent outputs:
  - the three design specs;
  - both research sets (7 UAE reports plus critique, and 6 webinar reports plus critique).
- Write `00-CANON.md` (the Canon section above) and `CONTRACTS.md`.
- Write `04-growth-modules.md`, a spec for Inquiry Guard, Visibility, Journeys, Finance, the autonomy dial and the claims registry. It is built from the webinar research and the module summaries above. A Plan agent drafts it and an adversarial reviewer checks it against the research before Wave 1 starts.

Each wave is one **Workflow**:
1. Lane agents run with `isolation: 'worktree'` on their own directories, with the Canon and their spec section in the prompt.
2. Each lane runs its own tests, typecheck and contract tests before committing.
3. I merge the lane branches into `feat/owner-autopilot`.
4. An integration agent runs the full suite on the merged tree.
5. A **multi-lens adversarial review** follows: correctness, security/privacy, spec compliance, UAE rules, and conventions (no em dashes, why-comments, avatar untouched). Each finding goes to 2 skeptics.
6. A fix loop runs until no confirmed findings remain.

| Wave | Lanes (parallel, disjoint files) | Gate to pass |
|---|---|---|
| **0a Foundation** | **F-server:** golden legacy tests first; `clefs/` package, db and migrate (0001 = current DDL), auth/devices/magic links, contracts (signals, actions FSM, metrics, jobs, events, notify plus inbound webhooks, audit, files, ai, money, time, consent), legacy route move, autonomy dial (`action_autonomy`), module stubs for all 15, worker, CLI, Docker/Procfile, `.env.example`, contract tests. **F-client:** `lib/{api,format,session,hotel,i18n,types}`, `app/ui/**`, `charts-extra`, ConsoleShell + `nav.ts` (all 15 modules pre-declared), `/login`, magic page, module route stubs, the 2 StaffDashboard edits, `getRoomData` change, eslint flat config, scripts. | Golden legacy tests pass unchanged. Contract tests pass. Existing Indian hotel dashboard and `/stay` work via `legacy_open_access`. `typecheck`, `lint` and `build` are green. |
| **0b/1** | **F-domain:** `core/stays`, `core/ingest`, `core/tax` with golden tax values. **A Service Control:** plus the staff-page hotfix and device PWA. **B Concierge + Voice.** **F1 Upsell + Reviews.** **F2 Guests + Journeys.** **H Trust + Marketing:** claims registry, plus the second lead-magnet page UI. | Lane test suites pass. Avatar regions are unchanged (a diff check on the protected line ranges). i18n completeness. Claim linter with `claim_id`s. Journey tests confirm no marketing reaches OTA aliases and no sentiment-based review routing. |
| **2** | **C Profit Guard** (plus public audit and the Leakage Index aggregate), **D Demand Radar** (exposes `price_guardrails`), **E Amenities**, **G Brief + Ask** (cadence tiers, autonomy suggestions), **I Inquiry Guard**, **V Visibility**, **P Finance**. | Detectors hit 1.0 recall on the plant fixtures with no decoys firing. Brief payload lints pass. Ask eval runs with the fake AI. Inquiry verification and leak-matching tests pass. Visibility statistics (Wilson CI, minimum n) and fact-error tests pass. USALI mapping reconciliation tests pass. |
| **3 Integration** | Full demo seed generator. Integration agent. E2E browser QA. Adversarial review over the full diff. Fix loop. | Everything under Verification below. |

**Frozen shared files** (only the foundation or integrator edits them; lanes leave `FOUNDATION-REQUEST:` notes):
- Server: `main.py`, `clefs/app.py`, `registry.py`, `settings.py`, requirements, `pyproject.toml`, `vercel.json`, `tests/conftest.py`.
- Client: `next.config.mjs`, `proxy.ts`, `package.json`, `tsconfig.json`, `app/layout.tsx`, `globals.css`, `app/lib/{api,format,session,hotel,i18n,types}`, `app/ui/**`, `charts.tsx`, `nav.ts`.

**Code conventions for all lanes:**
- `# ─── Section ───` banners, dense "why" comments, no em dashes anywhere, Python 3.12 syntax floor.
- Read `client/node_modules/next/dist/docs/` before writing Next 16 code.
- CSS strings are injected with a module class prefix. New code uses `--clefs-*` tokens.

## Verification

1. **Server tests** (`cd server && source .venv/bin/activate && pytest`): a fresh local Postgres 16 database per session, and one savepoint per test. The suite covers:
   - Golden legacy contract tests and module contract tests (ranges, prefixes, imports, metric `EXPLAIN`).
   - Tax golden values (A: 396.49/39.65/30.53/23.33).
   - Every detector (plant, decoy and boundary cases).
   - SLA ladder under a frozen clock across shift and midnight boundaries (Dubai timezone).
   - WhatsApp: signature, dedupe, nonce replay, template body length and promo lint.
   - Voice harness: signed webhook, accept/reject/refer payloads, MCP room binding.
   - Safety lexicons in 8 languages.
   - PII masking matrix and the retention purge.
   - Concierge evals and Ask evals on the fake AI backend.
   - **Autonomy dial:** `decide()` respects levels and limits. Refunds and rate changes never auto-execute. Demotion triggers work.
   - **Inquiry Guard:** SLA timers, quote tax lines (TD cap at 30 nights), the auto-send rule (≥BAR only), payment-webhook idempotency, verification matching, and OTA-leak matching limited to the 30-day window.
   - **Visibility:** parser fixtures for Gemini `grounding_metadata`, Perplexity `search_results` and SerpApi AI Mode/Hotels. Wilson CI. The minimum-sample gate. The parity calculation. A fact-error detector against the KB. Gemini runs only through the API (a contract test).
   - **Journeys:** Meta category mapping, caps, the 09:00-18:00 window, OTA-alias suppression, the J6 recovery gate, and identical review links regardless of sentiment.
   - **Finance:** golden GOP/GOPPAR/NRevPAR values from the demo P&L, and mapping reconciliation to source net profit. The planted booked-net month is flagged.
2. **Client checks:** `npm run typecheck`, `npm run lint`, `npm run build`, `node scripts/check-claims.mjs`, the i18n completeness check, and a diff check that the protected avatar/viseme regions of `ClefsConcierge.tsx` and `MarketingHomepage.tsx` are unchanged.
3. **Live local run:** `brew services start postgresql@16`, `createdb clefs_dev`, `python -m clefs.migrate up`, `python -m clefs.cli seed-demo`, `uvicorn main:app`, `python -m clefs.worker`, `npm run dev`. The console runs with `NOTIFY_MODE=capture`. Browser QA (ego-browser skill) covers:
   - Owner login, then the brief showing the top 5 in AED, then approve and adjust.
   - Profit Guard: upload a Booking CSV variant, map it, open findings, print the dispute packet.
   - Credit note import auto-verifies a finding.
   - Demand heatmap and day drawer.
   - Pair a device, log in with a PIN, see the handover, acknowledge a request, watch SLA escalate in shadow mode.
   - `/stay` in Arabic: RTL, a request, the tracker, then "Not resolved" reopens it.
   - Free OTA audit upload, then teaser, then unlock.
   - Homepage copy and new pages.
   - An inquiry arrives by web form, then the brief's "AED at risk" line appears, then a quote is approved with a payment link, then the payment webhook fixture marks it verified.
   - Visibility tab shows the fact-error evidence drawer, and the fix goes to the ledger.
   - P&L upload, then mapping confirmation, then the monthly one-pager.
   - The free-booking-link and parity lead magnet.
   - The existing Indian hotel dashboard and `/stay` still work.
4. **Manual checks for you** (need a mic or device): the avatar QA checklist (voice turn, barge-in, chip, text toggle, idle mic close, iOS Safari, viseme rig on and off).
5. **Live-service smoke tests,** once you supply keys:
   - WhatsApp template send and button webhook, PredictHQ sync, SerpApi comp rates.
   - A Gemini Live API test call through the harness, then a real PBX.
   - Real OTA exports from a pilot hotel, to validate the header aliases.

## Inputs needed from you (not blocking the build; the code degrades gracefully)

**Time-sensitive**
- **Meta WhatsApp:** you need a Meta app, a WABA and a dedicated number. A **payment method must be on file by 2026-09-30**, and service messages are billed from 2026-10-01. Templates take up to 24 h to approve.
- **Google Hotel Center:** Google's third-party rates for **Hotel Ads and PMax Travel Goals end on 2026-09-30**. Pilot hotels running those campaigns need a Hotel Center feed. Free booking links are unaffected, but only a feed shows an "official site" price.

**Accounts and keys:** a PredictHQ token (14-day trial), the SerpApi key (already in `review-intel/.env`), and the Google Cloud project (`GOOGLE_CLOUD_PROJECT`) plus `VOICE_WEBHOOK_SECRET` for the phone line. Optional: Postmark (email and inbound), a UAE SMS provider (MSG91 cannot send in the UAE), and a GBP API application (long lead time; the profile must be verified 60+ days, and the application also unlocks the Performance and Lodging APIs).

**For the webinar-derived modules** (all optional; the code degrades gracefully):
- A Perplexity API key, plus a legal read of Perplexity's API terms.
- A Google Maps Platform key for Places IDs-only search (free SKU).
- A payment gateway merchant account per hotel: N-Genius, Telr, Stripe UAE or Tap.
- Xero and QuickBooks developer apps.
- Meta Lead Ads app review (for `leadgen` webhooks).
- A Google Ads lead-form webhook key.

**Deploy and verify:**
- Tell me whether the Clefs Vercel account is on Pro (per-minute cron) or Hobby (then we use a free external pinger).
- Give access to the Clefs Vercel account and Neon project, so I can create the UAE demo branch and pin functions to `sin1`.
- Confirm the Dubai 2026 fee-exemption dates. They ship as an owner toggle.
- Get e&/du sign-off for the in-room phone AI.

## Risks

- **Avatar and lip-sync regressions:** mitigated by safe ranges plus a diff check and manual QA.
- **Legacy data semantics:** `accepted_at` means assignment, and assignment is by staff name. Mitigated by golden tests and the derived `status`.
- **Serverless limits:** the worker is needed for SIP sideband and 1-minute SLA ticks. The cron fallback plus evaluate-on-read cover this.
- **Unverified vendor surfaces:** OTA header aliases, MCP over SIP, WhatsApp free tier, PredictHQ UAE coverage. These are isolated behind adapters and flags.
- **Policy landmines from the webinar ideas:**
  - Review gating (Google, Tripadvisor).
  - Marketing to OTA-sourced contacts (Booking.com GDT 2.9.3).
  - WhatsApp recategorization of mixed templates.
  - Gemini grounding terms.
  - SerpApi litigation risk.
  - Xero's AI-training ban.
  - UAE telemarketing hours and the Do-Not-Call Register.
  
  Each is enforced in code and covered by a test, not left to documentation.
- **Visibility numbers are simulations** (API answers differ from consumer apps). They are always labelled as such, with sample sizes and confidence intervals shown.
- **Scope size:** roughly 65-85k lines of code with the webinar additions. Waves gate each other, so a failing wave stops the build rather than compounding.
