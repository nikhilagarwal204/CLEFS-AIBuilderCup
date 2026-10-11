# Clefs

**Ask once, in your language, and it gets done.** Clefs is an AI guest service desk for hotels. Hotels have more guests and fewer staff, and requests get lost between phone calls, languages and shifts. With Clefs a guest scans the QR card in the room and asks by voice or text, in their language. Gemini understands the ask, files one job per request with the right free staff member (in that person's language, with an ETA for the guest), climbs the escalation ladder when nobody accepts, and closes the job only with a photo that Gemini vision checks and the guest confirms.

This repository is the **AI Builder Cup prototype**, running on Google Cloud:

- **Final Product Link:** https://clefs-632652019887.asia-south1.run.app (the homepage tells the deck's story).
- **Live prototype for reviewers:** the homepage's "Try it yourself" section ([/try](https://clefs-632652019887.asia-south1.run.app/try) redirects there) opens the three screens on the demo hotel, Hotel Aozora Shinjuku in Tokyo: the guest in Room 204, Maria's staff app and the duty manager's console, one tap each.
- **Demo script, in deck order:** [docs/DEMO.md](docs/DEMO.md).

## The prototype

The deck's flow (slide 5) is the acceptance test: the guest speaks, Gemini understands intent, items, urgency and mood, one job is created per ask, dispatch picks the free staff member nearest the room and gives an ETA, staff read the job in their language, the work is done and photographed, Gemini vision verifies the photo, and the job closes with the guest notified and the dashboard updated. When something goes wrong: no acceptance pages the supervisor at 3 minutes, the duty manager at 5 and the GM at 10; a photo that does not match keeps the job open; "not fixed" or detected frustration opens a recovery case the duty manager approves.

| Deck item | Where it lives |
|---|---|
| Talk or type from a room QR, in your language (11 guest languages) | `client/app/stay/`, `client/app/ClefsConcierge.tsx`, `server/clefs/modules/concierge/` (Gemini Live voice, Gemini Flash text) |
| Approved answers only | the concierge knowledge base (`concierge/kb.py`), approved by the hotel |
| Smart dispatch with ETA, one job per ask | `server/clefs/modules/service/routing.py`, `intake.py` |
| Staff in their own language, proof before closure | the staff app `client/app/device/`, `service/devices_api.py` (Gemini vision photo check) |
| Escalation 3/5/10, safety first, recovery before the review | `service/sla.py`, `escalation.py`, `safety.py`, `recovery.py` |
| Owner view: response times by team, floor and shift | the console's Service analytics (`client/app/app/[hotelSlug]/(console)/service/`) |
| 22 safety and accuracy tests, 7 days in shadow mode | `server/clefs/modules/trust/evals/` (the go-live gate, Gemini as judge), Trust > Quality gate |
| Reviewer access | `server/clefs/core/demo_access.py` (demo hotels only) and `client/app/try/` |

**On Google Cloud:** Cloud Run for the web app (`clefs`) and the API (`clefs-api`), a Cloud Run job for the demo seed (`clefs-seed`), Cloud Scheduler for the minute tick (`clefs-tick`), Cloud SQL (Postgres), Secret Manager for every key, Gemini on Vertex AI (Flash for text, vision for photos) and the Gemini Live API for voice, and Cloud Storage for proof photos. See [docs/DEPLOY.md](docs/DEPLOY.md) section 20.

**Product focus.** The platform has fifteen modules (below). The prototype runs the guest service desk only: `clefs-api` sets `CLEFS_DISABLED_MODULES=profit_guard,demand,reviews,brief,ask,voice,visibility,inquiries,finance`, which keeps service, concierge, guests, amenities, upsell (the concierge's in-room menu) and trust. Switched-off modules keep their tables; their routes, jobs and console tabs go.

## Contents

- [The prototype](#the-prototype)
- [Modules](#modules)
- [Repository layout](#repository-layout)
- [Local setup](#local-setup)
- [Demo hotel and logins](#demo-hotel-and-logins)
- [Tests and checks](#tests-and-checks)
- [The production-database guard](#the-production-database-guard)
- [Further reading](#further-reading)

## Modules

Fifteen modules sit on one foundation (auth, devices, signals, actions, the autonomy dial, jobs, notifications, files, AI, audit). Each can be switched off per hotel, or for the whole deployment with `CLEFS_DISABLED_MODULES` (the prototype keeps six: Service Control, Guest Concierge, Amenities, Menus and upsell, Guests and Trust). Every recommendation becomes an **action** that moves through an audited state machine, and an **autonomy dial** per action type decides how far Clefs may go on its own; refunds, folio, rate and OTA changes always need a person.

| Module | Console tab | What it does for the owner |
|---|---|---|
| Brief (`brief`) | Brief | The daily money-at-risk brief (top 5 in AED), weekly and monthly reports, approve / adjust / assign from the web or WhatsApp (`owner_brief_v1`) |
| Profit Guard (`profit_guard`) | Profit | OTA leakage detectors (commission basis, program rates, no-shows, early departures, VCC deadlines, payouts vs bank, FX, chargebacks, reverse-charge VAT), Tourism Dirham checks, dispute packets, credit-note auto-verification, the public free OTA audit |
| Demand Radar (`demand`) | Demand | UAE event calendar, PredictHQ and weather signals, comp-set rates, a demand heatmap by night, BAR recommendations inside the owner's guardrails, outcome tracking |
| Inquiry Guard (`inquiries`) | Inquiries | Every direct inquiry (web form, WhatsApp, email, Meta and Google lead forms, phone) with a reply SLA, AED at risk, quotes with the UAE tax stack, payment links, OTA-leak matching and verified direct bookings |
| Service Control (`service`) | Service | Guest requests, SLA ladder and escalation, paired staff devices with PIN shifts, roster, handover, incidents, recovery cases, lost and found |
| Guest Concierge (`concierge`) | Concierge | The in-room guest page (`/stay`, eight languages including Arabic, voice avatar and chat), the knowledge base, WhatsApp chat |
| Phone AI (`voice`) | Concierge, Voice | In-room phone calls through the Gemini Live API, PBX setup recipes, call log |
| Amenities (`amenities`) | Amenities | Amenity catalog, issuance log, variance and spike detection, cost per occupied room |
| Menus and upsell (`upsell`) | Menu | Outlets, menus, in-room dining orders, pairing suggestions, the upsell funnel |
| Reviews (`reviews`) | Reviews | Google, Tripadvisor and Booking reviews, themes, reply drafts under the dial, links to the service request behind a complaint |
| Guests and journeys (`guests`) | Guests | Consent-gated guest identity, direct contacts, the J1-J8 stay journeys (utility and marketing WhatsApp templates), opt-outs |
| Visibility (`visibility`) | Visibility | Google Hotels and Business Profile health, rate parity, AI answer radar (Gemini with Grounding with Google Search, Perplexity, SerpApi AI Mode) with fact-error detection against the KB, the free Google check |
| Finance (`finance`) | Finance | P&L import (English and Arabic account names), USALI mapping, weekly pulse, monthly one-pager, GOPPAR and NRevPAR, verified profit |
| Ask Clefs (`ask`) | Ask | Plain-language questions over the governed metrics registry; no raw SQL, every number checked |
| Trust (`trust`) | Trust | Hash-chained audit log viewer, consent ledger, data-subject requests, retention, AI evals, pilot scorecards |

Each module's own README (`server/clefs/modules/<m>/README.md`) documents its tables, routes, jobs and rules.

## Repository layout

```
server/                 FastAPI API, worker and CLI (Python 3.12)
  main.py               app = clefs.app.create_app()
  clefs/                the package: foundation, core domain, modules
    CONTRACTS.md        the frozen interfaces every module builds on
    modules/<m>/        one directory per module
    seed/demo_jp/       the Tokyo demo hotel (Hotel Aozora Shinjuku), the prototype's hotel
    seed/demo_uae/      the UAE demo hotel and the seed machinery both demo hotels share
    seed/retire.py      retire-demo: a demo hotel off the public internet, every row kept
  tests/                pytest suites (foundation, modules, demo)
  vercel.json           the API as one Vercel Python function in sin1
client/                 Next.js 16 app: marketing site, console, guest pages, device PWA
docs/                   ARCHITECTURE.md, DEMO.md, DEPLOY.md, specs/
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the package layout, the module registry and how to add a module.

## Local setup

You need Postgres 16, Python 3.12 and Node 20+.

### 1. Database

```sh
brew install postgresql@16 && brew services start postgresql@16   # or any local Postgres 16
createdb clefs_dev
```

### 2. API

```sh
cd server
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# Always export these three in every shell that runs the API, the worker or the CLI.
# server/.env may hold production values; exported variables win over it.
export DATABASE_URL=postgresql://localhost/clefs_dev
export NOTIFY_MODE=log SMS_ENABLED=false     # never text or call real people from a laptop
export ENV=development

.venv/bin/python -m clefs.migrate up          # apply every migration (status | verify | new also exist)
.venv/bin/python -m clefs.cli seed-demo       # build the demo hotel and print the logins (next section)
.venv/bin/uvicorn main:app --reload --port 8000
```

In a second shell (same three exports), run the worker. It ticks the scheduler and drains the job queue every 15 seconds, and wakes on `LISTEN clefs_jobs`:

```sh
cd server && .venv/bin/python -m clefs.worker          # --once runs one tick and prints the report
```

Without a worker nothing scheduled happens locally (SLA sweeps, detectors, the brief). One tick by hand also works: `curl -H "Authorization: Bearer $CRON_SECRET" http://localhost:8000/internal/cron/tick` once `CRON_SECRET` is set.

`server/.env.example` lists every setting with a comment. Copy it to `server/.env` only for keys you want to try locally (Gemini, SerpApi, PredictHQ); leave `DATABASE_URL` exported in the shell, never in that file.

### 3. Web app

```sh
cd client
npm install
CLEFS_API_ORIGIN=http://localhost:8000 npm run dev
```

Open http://localhost:3000. The browser only ever calls `/api/clefs/*`, which Next rewrites to `CLEFS_API_ORIGIN`, so the session cookie stays first-party. SSE streams and large uploads go straight to `NEXT_PUBLIC_API_URL` (default: `CLEFS_API_ORIGIN`) with a 60-second signed ticket. Keep both on `localhost:8000` for local work.

Useful client flags: `NEXT_PUBLIC_AVATAR_RIG=viseme` evaluates the viseme lip-sync rig (off by default; the video avatar is the default), `npm run dev:https` serves over HTTPS on the LAN for testing the guest page on a phone.

## Demo hotel and logins

`python -m clefs.cli seed-demo` builds **Hotel Aozora Shinjuku (DEMO)** (`aozora-shinjuku`), the hotel the AI Builder Cup prototype shows: a fictional mid-range hotel in Nishi-Shinjuku, Tokyo, 80 rooms on 8 floors, about 8 minutes on foot from Shinjuku Station. It is seeded in yen and Tokyo time with check-in 15:00 and check-out 11:00, 11 guest languages (English, Japanese, Korean, Chinese, Thai, Hindi, French, German, Russian, Arabic, Urdu), the concierge's approved knowledge in English and Japanese, an in-room dining menu and amenity catalogue in JPY (prices tax-inclusive), JAPAC-heavy guests, 120 days of stays, Service history with its planted cases, the roster and the amenity log. The modules the prototype switches off (money and growth) are switched off for the hotel too and not seeded. Everything is synthetic and deterministic for a given anchor date. No vendor or model is called.

The deck's cast is part of it (`server/clefs/seed/demo_jp/core.py` and `deck.py`): the assistant is called Clefs; Room 204 is on floor 2; **Maria Santos** (Housekeeping attendant, floors 1-4, reads Tagalog, PIN 2046) and **Priya Sharma** (Housekeeping attendant, floors 5-8, reads Hindi) are on shift round the clock for the next 14 days with **Hana Suzuki**, the Housekeeping supervisor, and **Yuki Sato**, the duty manager; **Kenji Tanaka**, the GM, is on the ladder; the rest of the team mixes Japanese and foreign staff (Vietnamese, Nepali, Indonesian, Filipino, Chinese), each reading jobs in their own language; Maria's phone is paired with her shift open; the shadow week is over; and the 22 standard safety and accuracy tests are queued, so the first tick after a seed runs them with Gemini as the judge.

It prints, once:

```
demo logins (password shown once; re-run to rotate):
  owner       owner+aozora-shinjuku@example.com        <password>  Michiko Takahashi
  gm          gm+aozora-shinjuku@example.com           <password>  Kenji Tanaka
  finance     finance+aozora-shinjuku@example.com      <password>  Naoko Ito
  revenue     revenue+aozora-shinjuku@example.com      <password>  Daisuke Yamamoto
  front_desk  frontdesk+aozora-shinjuku@example.com    <password>  Aiko Watanabe
  dept_head   dutymanager+aozora-shinjuku@example.com  <password>  Yuki Sato
staff PINs:
  <pin>  <staff name>
deck demo (the keyed room link opens the guest page as that room):
  room_204  http://localhost:3000/stay/aozora-shinjuku/204?k=<key>
  try       http://localhost:3000/try
pair this browser as a device: http://localhost:3000/... (code ..., expires HH:MM UTC)
```

- Log in at http://localhost:3000/login, or open http://localhost:3000 and use "Try it yourself", "Open the manager console" (Yuki Sato, the duty manager, this demo hotel only). The service desk lives at `/app/aozora-shinjuku/service`.
- `--password` (or `CLEFS_DEMO_PASSWORD`) sets one fixed password for every demo user; otherwise each run generates new ones.
- `--anchor YYYY-MM-DD` is the last history day (default: today in the hotel's zone). Without `--anchor` the seed is live: it runs as of the real clock (printed as `as_of=`), so nothing it plants (open requests, incidents, tonight's order) lies after the moment you open the console; before 09:00 hotel time it seeds as of last night. With `--anchor` it runs as of 21:00 hotel time on that day, which is deterministic but looks like the future when presented earlier that day.
- A re-run wipes and rebuilds only a hotel whose status is `demo`; a real hotel is refused.
- `python -m clefs.cli demo-check` runs every per-hotel job once at the anchor (rolled back) and reports the property checks, the deck cast and every planted case. Exit 1 on any miss. Run it before a demo.
- Emails are `@example.com` (RFC 2606), so a magic link can never reach a real inbox.

**The UAE demo hotel** is still one flag away: `seed-demo --hotel demo-deira-crest` builds **Al Rigga Crest Hotel (DEMO)**, a 3-star, 80-room Deira hotel with every module seeded (OTA statements, payouts, bank and card exports, a PMS folio, P&L, reviews, inquiries, journeys, visibility fixtures and a planted case, plus decoys, for every detector). Its OTA statements are fixed at June-August 2026, so an anchor before 23 September 2026 skips its Profit Guard part. `--variant jp|uae` picks the seed for any other slug.

**Retiring a demo hotel.** `python -m clefs.cli retire-demo --hotel demo-deira-crest` takes a demo hotel off the public internet without deleting a row: its status becomes `archived` (the guest page, the concierge and the reviewer doors answer 404), its memberships and demo logins are switched off and their sessions revoked, its devices are revoked and unused pairing codes expire. `--restore` undoes it (a reseed then pairs Maria's phone again).

The other CLI commands (`create-hotel`, `create-user`, `grant`, `make-admin`, `pair-code`, `set-pin`, `magic-link`) are listed in the docstring of `server/clefs/cli.py`. Every command prints the target database host first.

The presenter's walkthrough is [docs/DEMO.md](docs/DEMO.md).

## Tests and checks

### Server

```sh
cd server
TEST_DATABASE_URL=postgresql://localhost/postgres .venv/bin/python -m pytest -q
```

- Each pytest process creates its own database (`clefs_t_<pid>`) from `TEST_DATABASE_URL`, migrates it once and drops it at exit. Each test runs in a transaction that is rolled back. Only local hosts are accepted.
- The harness sets `CLEFS_DOTENV=0`, so `server/.env` is never read by tests. `NOTIFY_MODE` is `capture` and AI is a fake backend.
- One module at a time: `CLEFS_ONLY_MODULES=profit_guard .venv/bin/python -m pytest -q tests/modules/profit_guard`.
- `tests/foundation/` holds the contract tests: frozen API signatures, migration ranges and table prefixes, import rules, golden legacy routes, Python 3.12 parsing, the `requirements.txt` coverage check and the Vercel config.
- `tests/demo/` seeds the demo hotel at a small scale and checks every plant end to end.
- `python -m clefs.migrate verify` fails on checksum drift (an applied migration that was edited).

### Client

```sh
cd client
npm run typecheck                     # tsc --noEmit
npm run lint                          # eslint
npm run test                          # node --test on app/**/*.test.mjs (pure console helpers)
npm run check:claims                  # the claims linter (below)
node scripts/check-i18n.mjs           # every guest language carries every key, no em dashes
npm run build                         # next build
```

- **Claims linter.** Every market statistic on the marketing site must come from the registry in `client/app/lib/claims.ts` (exact quote, publisher, URL, population, period, `review_by` expiry) and be rendered through `<Claim>` / `claimText()` or tagged `// claim: <id>`. The linter fails the build on an untagged percentage, currency amount or multiplier in marketing copy, and on a claim that is banned, unverified, expired or used on a surface it is not cleared for. "Remembers your guests" stays behind the `claims.guest_memory` flag (false until repeat-guest recognition is live).
- **Protected avatar code.** The lip-sync and viseme code in `client/app/ClefsConcierge.tsx`, `client/app/MarketingHomepage.tsx`, `client/app/lib/visemeRig.ts` and `client/public/avatar/**` is protected: the mouth gates on live audio RMS, never on transcript deltas, and the viseme rig stays off by default. Changes there need the manual avatar QA pass (voice turn, barge-in, text toggle, idle mic close, iOS Safari and Android Chrome, rig on and off).

## The production-database guard

`server/.env` has historically carried the **production** database URL (`PSQL_DATABASE_URL`) for the legacy app. To stop a stray local command from reaching it:

- A **remote** database URL that was read from `server/.env` (not exported in the shell) is refused at startup with `RemoteDatabaseRefused`. Every local command therefore needs `export DATABASE_URL=postgresql://localhost/<db>`.
- `CLEFS_ALLOW_DOTENV_REMOTE_DB=1`, exported in the shell, is the explicit opt-in for the rare case where you really mean to use the remote database from `.env`. Do not set it casually.
- Deployed environments are unaffected: Vercel and Compose export their variables into the process environment.
- `DATABASE_URL` wins over `PSQL_DATABASE_URL`. `DATABASE_URL_UNPOOLED` is used only when it names the same database as the effective URL, so a stale production direct URL never pairs with a local or staging pooled URL.
- Every CLI command prints the target database host and name (never the password) before doing anything.
- The demo seed and the voice harness refuse to touch a non-demo hotel or a non-local database respectively.

## Further reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): package layout, the module registry, contracts, the build waves, how to add a module.
- [docs/DEMO.md](docs/DEMO.md): the AI Builder Cup demo, in deck order, on the Cloud Run deployment.
- [docs/specs/05-ai-builder-cup-prototype.md](docs/specs/05-ai-builder-cup-prototype.md): the prototype's settled decisions and contracts.
- [docs/DEPLOY.md](docs/DEPLOY.md): Vercel + Neon deployment, Google Cloud Run, environment variables, cron, WhatsApp, Google Gemini (including the Live API phone path) and the other vendor setups.
- `server/clefs/CONTRACTS.md`: every foundation interface, as built.
- `docs/specs/00-CANON.md`: the decisions that override the design specs, including amendments K1-K5.
