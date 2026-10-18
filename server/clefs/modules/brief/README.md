# Owner Brief (`brief`)

Owned by lane **G Brief + Ask** from Wave 1. Only that lane edits this directory (plus `tests/modules/brief/`).

- Migrations: `migrations/NNNN_brief_<desc>.sql|.py`, numbers 0700-0749 (`python -m clefs.migrate new --module brief "desc"`).
- New tables start with `brf_`.
- Console routes live under `/hotels/{slug}/brief/` (`router.py`); guest and lead routes under `/public/`, device routes under `/device/`, webhooks under `/webhooks/`.
- Permissions are declared in `module.py` and must start with `brief.`.
- `api.py` is this module's public surface. Other modules may import ONLY `clefs.modules.brief.api`; its signatures are frozen (see `clefs/CONTRACTS.md`). Implement the bodies, never change the signatures.
- Need something from a frozen foundation file? Work around it locally and leave a `# FOUNDATION-REQUEST:` comment.

## What the brief-core part built (02 spec 6.1, 6.3, 6.4; Canon 18)

| File | What |
|---|---|
| `ranking.py` | Pure `I x C x U` scoring and the top-N pick with section (<= 3) and detector (<= 2) caps; ties on the earliest deadline. The detector cap counts SOURCE detectors (`detector_key`): Profit Guard's L1-L5, L12 and L13 share the kind `pg.leak.detected`, so the key reads the detector from its `pg:<detector>:...` dedupe key (or a signal's own `detector` field), else the kind |
| `brief.py` | Candidates from `clefs.core.signals` (+ pending `surface='brief'` approvals), cadence tiers, blocks, verified line, runs/items persistence, magic links, delivery channels, `push_now`, the daily/weekly/monthly jobs, `BriefSettings` |
| `render.py` | Plain lines (`"1) AED 1,020 · Booking no-show commission · 90% · by 14 Oct · Finance"`), promo-word lint, the 1,024-char body budget, the HTML email |
| `producers/service_risk.py` | `brief.sla_breach` per request from `service_requests_v1` (new > 10 min; in progress past Housekeeping 30 / Maintenance 60 / F&B 45 / other 60 min) |
| `escalate.py` | The ladder: deadline (remind the assignee), +4 h (GM), +24 h (owner, pinned "Overdue", `brief.action.overdue`) |
| `weekly.py` | Weekly verified-value report (payload, HTML, fpdf2 PDF in `clefs.files`) |
| `metrics.py` | `brief.time_to_decision_h`, `brief.approval_rate`, `brief.overdue_count`, `brief.briefs_sent` |

There is **no brief state machine**: decisions use the foundation (`clefs.core.actions`, Canon 7 + K1, the autonomy dial). Brief reads and reminds; it only writes `actions.escalation_level` / `escalated_at` (the counters the foundation keeps for Brief) and calls `actions.decide(..., "remind")`.

### Cadence tiers (Canon 18, 04 spec 7.3)

- **daily**: `list_signals(cadence="daily")` minus compliance, plus proposed `surface='brief'` actions not carried by an open signal (money-bearing ones when their own signal sits in another tier). Blocks: `inquiries` (`inquiries.api.brief_summary`, when non-empty).
- **weekly** (Mon 08:00 local; period = previous ISO week): weekly signals (top 8 under the same caps), `autonomy` block (open `promotion_suggested` events with `promotion_stats`, and demotions in the last 7 days), `compliance` digest (compliance never ranks, Canon 6), `finance` (`finance.api.weekly_pulse`, when it returns data), `report` (the weekly verified-value report pointer).
- **monthly** (1st, 08:00 local; period = previous month): monthly signals, `finance` P&L slot (`finance.api.monthly_summary(month=<1st of last month>)`; `available: false` while Finance returns None).
- Every tier: `verified` line (verified, caused, same-currency outcomes in the last 24 h and month-to-date), and `overdue` items (actions at escalation level 3), numbered after the ranked items and rendered above them.

Scoring: `I = min(1, ln(1+AED)/ln(1+R_ref))`, `R_ref = 2 x` the trailing-30-day average of `occupancy_daily.room_revenue_minor` (fallback AED 20,000); zero-money items use `signals.severity` (4 critical .9, 3 high .7, 2 medium .4, 1 low .25) and a <= 48 h-old `reviews` item .5. `C` = signal confidence (0.8 for a signal-less approval). `U` = 1 (<= 24 h or past), .85 (<= 72 h), .6 (<= 7 d), .4 (<= 30 d), .25.

### Jobs

| Job | Schedule |
|---|---|
| `brief.daily` | `daily_at("07:00", setting="brief_time_local")` hotel-local, same-day catch-up |
| `brief.weekly` | Monday 08:00 hotel-local |
| `brief.monthly` | 1st 08:00 hotel-local |
| `brief.weekly_report` | Monday 08:30 hotel-local (backfills the report when the weekly brief is off) |
| `brief.escalate` | every 15 min |
| `brief.service_risk` | every 5 min |
| `brief.delivery_fallback` | every 10 min (WhatsApp brief not delivered in 30 min, or failed: email + urgent SMS) |
| `wa.template_sync` | daily 04:30 UTC, not per hotel (platform WABA + every connected hotel WABA; purges nonces and stale sessions) |

A scheduled run exists once per (hotel, cadence, local date) (`brf_runs_scheduled_uniq`); a retried slot returns the stored run and never re-sends.

### Signals and settings

- Kinds: `brief.sla_breach` (service_risk, daily; per-signal cadence weekly below high priority), `brief.action.overdue` (service_risk, daily), `brief.delivery.failed` (service_risk, daily), `wa.template_alert` (service_risk, daily: a hotel template paused, rejected, disabled or recategorised).
- Metric `wa.service_msgs_month` (count of WhatsApp service messages Brief sent, by `kind`; billable from 2026-10-01).
- `hotel_module_settings["brief"]` (`BriefSettings`): `daily_enabled`, `weekly_enabled`, `monthly_enabled`, `recipient_user_ids` (empty = every active owner and gm), `channels` (order tried, default `["whatsapp", "email"]`), `send_when_empty`, `top_n` (1-10, default 5), `quiet_hours` ([{user_id, start: "HH:MM", end: "HH:MM"}], hotel-local, overnight when start > end; see `quiet.py`: scheduled briefs and non-critical escalations to that person wait for the window to end, flushed by the `brief.escalate` pass; a severity 4 signal's action and "Send now" ignore it). The send time is the core column `hotels.brief_time_local`.
- Ranking keeps up to `ranking.MIN_DECIDABLE` (2) items with a live decidable action in the daily and weekly top N, replacing the lowest-scored read-only picks (QA OWN-02). The headline's "at stake" never counts `verified` items (banked money). A monthly signal whose dedupe key names a month before the brief's period is left out (stale P&L notices). Items carry `section_label` ("Finance to-do") when a desk module files a to-do under `service_risk`. `evidence_url` maps evidence refs `{table, id}`, then the signal subject, then the module to a console page before falling back to `/brief?signal=`.

### Delivery

Per recipient: one magic link (`clefs.auth.magic.create_link`, purpose `brief`, 72 h, 5 uses, `scope={"action_ids": [surfaced ids]}`, next path `/app/{slug}/brief?run={id}`), recorded in `brf_links`; then the first channel that accepts. `email` ships here (utility, HTML + text, idempotency `brief:{run}:{user}:email`). `whatsapp` is registered by `whatsapp.py` (`brief.register_channel("whatsapp", fn)`, `fn(cur, hotel, run, recipient, link) -> {ok, status, outbox_id?, error?}`; see "WhatsApp" below). A non-ok answer moves on to the next channel at once; an accepted WhatsApp send is watched by `fallback.py`.

## WhatsApp (the brief-whatsapp part: 02 spec 6.2, Canon: WhatsApp)

Everything is sent through `clefs.notify` (its outbox paces 6 s per recipient, enforces the 24 h window, dedupes the webhook on `messages[].id`, verifies `X-Hub-Signature-256`, and handles 131056 / 130429 / 131047). Brief has no outbox, no webhook route and no message table of its own: the spec's `wa_outbox`, `wa_messages`, `wa_webhook_events` and `wa_media` are the foundation's `outbound_messages`, `inbound_messages`, `whatsapp_windows` and signed file links. Brief keeps only what the foundation lacks (migration `0701_brief_whatsapp.sql`):

| Table | What |
|---|---|
| `brf_wa_templates` | Template registry: `hotel_slug` NULL = the platform WABA (`WA_BUSINESS_ACCOUNT_ID`), else the hotel's own. `category_expected` (what we built it as) vs Meta's `category`, `status`, `quality`, `alerted_state` |
| `brf_wa_accounts` | A hotel's own WABA (`waba_id`, `phone_number_id`, `billing_ready` attestation) |
| `brf_wa_contacts` | Owner/manager opt-in and opt-out for the WhatsApp brief |
| `brf_phone_verifications` | One-time codes a member sends from their own WhatsApp to prove a number (`0703_brief_phone_verify.sql`) |
| `brf_wa_nonces` | One row per issued button (recipient-bound nonce, verb, run, action, item, expiry) |
| `brf_wa_sessions` | The one open question per member (`awaiting_adjust`, 30 min) |
| `brf_deliveries` | Each WhatsApp brief send, watched by the fallback (`pending` -> `delivered`, `fell_back` or `fallback_failed`) |

| File | What |
|---|---|
| `templates.py` | Definitions (`owner_brief_v1`, `weekly_report_v1`, both UTILITY), definition lint (promo words refused), per-send variables within the 1,024 budget, registry (`template_info`, `upsert`, `list_templates`), `submit_template`, the Graph client (live mode only; `set_graph` for tests), `sync` + once-per-change alerts, the `wa.template_sync` job |
| `nonce.py` | `base32(HMAC(key, action_id\|verb\|run_id\|user_id\|exp))[:12]`, `mint`, `verify` (unknown / other_recipient / expired / mismatch / replayed), `mark_used` |
| `whatsapp.py` | Contacts and opt-in, the `whatsapp` channel, the `brief:` / `act:` button handlers and the text handler, the list / buttons / More flows, Adjust sessions, dispute packet DOCUMENT, weekly report DOCUMENT, the `/wa/*` console routes |
| `fallback.py` | `brief.delivery_fallback` (10 min) and the `on_outbound_status("brief.")` hook: email (fresh magic link) and, for urgent money, an SMS without a URL |
| `magic_brief.py` | The public mobile-brief API `/public/brief/{token}` |

### The conversation

1. **07:00 local**: `owner_brief_v1` from the platform number, only when `wa_template_info` says `APPROVED` and the category is still `UTILITY` (else the attempt is `template_not_approved` / `template_recategorised` and email goes instead), and only to a member who has not opted out (`opted_out`). Header `Clefs brief · 2026-10-01`; body `{{1}} decisions pending today, {{2}} at stake.` then `{{3}}..{{7}}`: the item lines (`1) AED 1,020 · Booking no-show commission · 90% · by 14 Oct · Finance`), the verified line in the first free slot, `-` after it (Meta refuses empty parameters and newlines inside one). Every variable goes through `render.lint`; `templates.fit` keeps the total at 1,024 or less. Buttons: quick reply `Approve #1` (`brief:{date}:1:approve:{nonce}`, carrying item 1's action only while it is `proposed`, else the tap opens the list), quick reply `Review items` (`brief:{date}:list:{nonce}`), URL `Open brief` = `{web}/brief/{token}` (the dynamic suffix is the magic-link token). A weekly run also sends `weekly_report_v1` with the report PDF as a DOCUMENT header (a 6 h signed link; inside an open window a plain document message instead).
2. **A tap opens the 24 h window**; each tap then gets exactly one reply (service messages are billable from 2026-10-01):
   - `Review items` -> a list of up to 5 open decisions (row title <= 24, description <= 72, id `act:{id}:open:{nonce}`);
   - a row -> reply buttons `Approve` / `Assign` / `More` (`act:{id}:approve|assign|more:{nonce}`);
   - `Assign` -> a list of up to 10 members (`assign-u{user_id}`), `More` -> `Adjust`, `Dispute`, `Remind`, `Snooze 1 day`, `Snooze 7 days`, `Ignore`; `Ignore` -> a reason list (`ignore-wrong|done|small|other`, recorded as the decision note);
   - `Adjust` -> "Reply with a rate between AED 380 and 520." and an `awaiting_adjust` session; the next text is parsed (`450`, `AED 455`, `1,020.50`), checked against the bounds (else asked again) and applied as `decide(adjust, params_patch={field: minor})`;
   - `Dispute` -> `decide(dispute)`, then the packet as a DOCUMENT message: `profit_guard.api.dispute_packet(cur, slug, action_id=...) -> {file_id, filename}` when that door exists, else a `packet_file_id` on the action's params or result, else a one-page evidence PDF built here (`files` purpose `brief.dispute_packet`);
   - `Remind` -> `decide(remind)` and the escalation ladder's level-1 audience is nudged (`brief.remind`, no links).
3. **Decisions** run as the tapping member's `HotelCtx` (role permissions and the dial apply exactly as in the console; Clefs admin powers never carry over WhatsApp), with `via="whatsapp"` and the button's nonce: core `decide(nonce=...)` makes it single-use (409 `nonce_replayed` -> "Already done"). A forwarded button tapped from another number recomputes to a different HMAC and is refused. Refusals are answered in words (`illegal_transition` -> "... is already approved.", 403 -> "Your role cannot make this decision.").
4. **Adjust bounds** come from the action's `params`: `adjust: {field, min_minor, max_minor, label?}`, or `<f>_minor` with `min_<f>_minor` / `max_<f>_minor`, or `<f>_minor` with `floor_minor` / `ceiling_minor` (rate recommendations). Without bounds the reply points to the console.
5. **Text**: the text handler (staff audience, priority 40) takes only an open Adjust question or `STOP` / `START` from a brief member (opt-out / opt-in at every hotel where they receive briefs, audited `brief.wa.opt_out|opt_in`); every other text passes on (Ask Clefs).

Opt-in: a member with no `brf_wa_contacts` row is treated as opted in by their account (`implicit`: they gave Clefs the number and the hotel's brief settings list WhatsApp); an explicit opt-out always wins. The number is always the member's `users.phone_e164`, because the foundation maps a WhatsApp sender to a user by it. That column is a GLOBAL identity (magic links are minted by it too), so Brief never stores a typed number: a manager can never add one for someone else, and a member proves their own by sending `CLEFS <code>` from that WhatsApp to the platform number (`POST /wa/contacts/verify`, then the inbound handler `on_verify_code`; Meta names the sender, 5 wrong codes or 30 minutes end a request, only the code's hash is stored).

### Fallback (`brief.delivery_fallback`, every 10 min, and the status hook)

A WhatsApp brief counts once Meta reports `delivered` or `read` for it or for a retry / fallback template after it (`outbound_messages.fallback_of` chain). A terminal failure with nothing in flight falls back at once (status hook); anything not delivered 30 minutes after it was queued falls back on the next sweep. Falling back sends the HTML email brief with a fresh magic link (`brief.fallback`) and, when the brief holds an item with `U = 1` and impact of at least 1,000 in the hotel currency, the SMS `Clefs: 2 urgent items, AED 4,120 at stake. Open Clefs.` (`brief.fallback_sms`, never a URL: UAE SMS rule). The attempts are appended to the run's `delivery` (`fallback_of: "whatsapp"`). Each ledger row resolves once.

### Templates (`wa.template_sync`, daily 04:30 UTC)

Registers Brief's own definitions as `DRAFT` rows, then syncs the platform WABA and every connected hotel WABA from Graph (`GET {waba}/message_templates`, live mode only). A template that Meta paused, rejected, disabled or flagged, or filed in another category than it was built for, alerts once per change: a `wa.template_alert` signal (service_risk, daily) at the hotel for hotel templates, an error log for platform templates (Clefs operations; briefs fall back to email meanwhile). The job also purges nonces 7 days past expiry and stale sessions.

## HTTP API (console, `/hotels/{slug}/brief/...`)

Money is `*_minor` + `currency` next to a Decimal string (`*_value`). Errors are `{detail, code}`.

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /today?cadence=daily\|weekly\|monthly` | `brief.view` | | Today's newest stored run (`preview: false`), else a live assembly (`id: null, preview: true`). Run shape below |
| `GET /runs?cadence=&limit=30` | `brief.view` | | `[Run]` without items, newest first |
| `GET /runs/{run_id}` | `brief.view` | | `Run` with `items[]` and `overdue[]`; 404 `brief_not_found` |
| `GET /{YYYY-MM-DD}?cadence=daily` | `brief.view` | | The newest stored run for that local date; 404 `brief_not_found` |
| `POST /preview` | `brief.view` | `{cadence?: "daily", date?: "YYYY-MM-DD"}` | The assembled brief, nothing stored or sent (`id: null, preview: true`) |
| `POST /send-now` | `brief.manage` | `{cadence?: "daily", only_me?: false}` | `{ok, brief_id, sent_to: [{user_id, name, channel}], status: sent\|partial\|failed, reason?}`; audited `brief.send_now` |
| `GET /settings` | `brief.view` | | `{daily_enabled, weekly_enabled, monthly_enabled, recipient_user_ids, channels, send_when_empty, top_n, brief_time_local: "HH:MM", timezone, recipients: [{user_id, name, role, has_email, has_whatsapp}]}` |
| `PUT /settings` | `brief.manage` | any subset of the settings fields plus `brief_time_local: "HH:MM"` | the settings view; 400 `invalid_recipient` (not an active member), 400 `invalid_quiet_hours` (two windows for one person), 422 bad time |
| `POST /signals/{signal_id}/decide` | `brief.view` (propose/assign also need the type's `decide_perm` or `core.actions.decide`; snooze/dismiss need `core.actions.decide` or `brief.manage`, or the signal's `owner_role` to be the caller's role) | `{decision: propose\|assign\|snooze\|dismiss, assignee_user_id?, assignee_role?, snooze_until? (tz-aware, max 30 d, default 24 h), note?}` | `{signal, action}`: propose creates the signal's recommended action (else a `core.task`) linked to it, or returns its live action; assign proposes then decides assign; snooze/dismiss set the signal `snoozed`/`ignored`. 404 `signal_not_found`, 409 `signal_closed` / `action_suppressed`, 400 `assignee_required` / `invalid_snooze`, 422 `invalid_params` |
| `GET /actions?state=a,b&type=x,y&overdue=true&limit=50` | `brief.view` | | `[Action]` (foundation `actions.to_api`) + `overdue: bool` (deadline passed while approved/assigned/in progress) |
| `GET /actions/{id}/timeline` | `brief.view` | | `{action, timeline: [{at, kind: event\|surfaced\|escalation, ...}]}`: events carry `event, from_status, to_status, via, actor_type, actor_label`; surfaced carry `run_id, cadence, brief_date, item_no, pinned`; escalations carry `level, notified[{channel, ok, status}]` |
| `GET /autonomy` | `brief.view` | | `{promotions: [{event_id, action_type, label, from_level, to_level, at, stats, text}], demotions: [{..., trigger, text}]}` |
| `GET /reports/weekly?limit=12` | `brief.view` | | `[{period_key, period_start, period_end, currency, verified_minor, identified_minor, generated_at}]` |
| `GET /reports/weekly/{iso_week}` | `brief.view` | e.g. `2026-W40` | `{id, period_key, period_start, period_end, currency, payload, html, pdf_file_id, generated_at}` (built on first read); 409 `week_not_over`, 400 `invalid_week` |
| `GET /reports/weekly/{iso_week}/pdf` | `brief.view` | | `application/pdf` attachment |

### WhatsApp console routes (`/hotels/{slug}/brief/wa/...`, `whatsapp.py`)

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /wa/contacts` | `brief.view` | | `[Contact]` for every active owner, gm, finance, revenue and dept_head member |
| `POST /wa/contacts` | `brief.view` for yourself; `brief.manage` for `user_id` of someone else (else 403) | `{user_id?, phone?, opted_in?: true}` | `Contact`; audited `brief.wa.opt_in` / `brief.wa.opt_out`. Never stores a number: `phone` for someone else is 409 `no_phone`; your own `phone` only confirms (the account's number passes, another is 409 `phone_mismatch`, none yet is 409 `phone_unverified`); opting in without any number is 400 `no_phone`; 404 `member_not_found` |
| `POST /wa/contacts/verify` | `brief.view`, the caller's own account only | `{phone}` | `{status: pending, phone_masked, expires_at, code, message: "CLEFS <code>", send_to, wa_link}` (the code is shown once); 409 `phone_taken`, `already_verified`, `phone_mismatch`; audited `brief.wa.verify_start` |
| `GET /wa/contacts/verify` | `brief.view` | | `{status: none\|pending\|verified\|expired\|cancelled, phone_masked?, expires_at?}` for the caller's latest request |
| `GET /wa/templates` | `brief.view` | | `{templates: [{id, scope: platform\|hotel, name, language, category_expected, category, status, quality, rejected_reason, meta_template_id, submitted_at, last_synced_at, updated_at}], brief_templates: [{name, language, category}]}` |
| `POST /wa/templates/submit` | `brief.manage` (`scope: "platform"` needs a Clefs admin, else 403) | `{name, language?: "en", category?: "UTILITY", components?, scope?: "hotel"}`; `components` omitted = Brief's own definition of `name` | `{name, language, status, meta_template_id}`; audited `brief.wa.template_submit`; 400 `invalid_category` / `invalid_template` / `promo_words`; 409 `not_connected` (hotel without a WABA) / `not_configured` (no platform WABA) |
| `POST /wa/templates/sync` | `brief.manage` | | `{hotel: {synced, alerts[], skipped?: not_connected\|not_live}, platform?}` (platform only for Clefs admins) |
| `GET /wa/account` | `brief.view` | | `{account: {hotel_slug, waba_id, phone_number_id, display_phone, quality, tier, billing_ready, billing_checked_at, connected_at, updated_at} \| null}` |
| `PUT /wa/account` | `brief.manage` | `{waba_id: digits, phone_number_id?: digits, display_phone?, billing_ready?: bool}` (`billing_ready` is the owner's attestation, 04 spec 3.5) | `{account}`; audited `brief.wa.account` |

`Contact` = `{user_id, name, role, phone_masked, has_phone, state: implicit|opted_in|opted_out|no_phone, opted_in_at, opt_in_method: console|whatsapp|whatsapp_verify|null, opted_out_at, opt_out_method: console|whatsapp_stop|null}`.

### Public mobile brief (`/public/brief/...`, `magic_brief.py`; client page `/brief/[token]`)

The `{token}` is the raw magic-link token of a brief link (72 h, scoped to the run's action ids; the WhatsApp `Open brief` button and the email link both carry it). These routes read it **without consuming a use**; `POST /auth/magic/exchange {token}` (foundation) is the way to trade a use for a 12 h console session. Every response is `Cache-Control: no-store`, `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer`. Rate limit: 120 requests per IP per 10 minutes (429 `rate_limited`), counted even when the token is bad. An unknown, expired or non-brief token, an inactive user, or a member who left the hotel is 401 `magic_link_invalid`.

| Method and path | Request | Response |
|---|---|---|
| `GET /public/brief/{token}` | | `{hotel: {slug, name, timezone, currency, locale}, user: {name, role}, expires_at, run: {id, cadence, brief_date, headline, currency, at_stake_minor, at_stake_value, verified, items: [MobileItem], overdue: [MobileItem]}}` |
| `POST /public/brief/{token}/actions/{action_id}/decide` | `{decision: approve\|adjust\|assign\|dispute\|remind\|snooze\|ignore, params?, note?, snooze_days? (1-30, default 1), snooze_until?, assignee_user_id?, assignee_staff_id?}`; adjust takes the bounded field in major units: `{params: {<field> or "amount": "455.00"}}` | `{action: Action (actions.to_api), view: MobileAction}`; 403 `not_surfaced` (not in the link's scope), 403 `forbidden` (the member's role lacks the type's decide permission), 409 `illegal_transition` / `action_expired` / `nonce_replayed`, 400 `out_of_bounds` / `invalid_amount`, 409 `adjust_unavailable` (no bounds on the action) |

`MobileItem` = `{item_no, kind, section, title, impact_minor, impact_value, currency, confidence, urgency, deadline_at, owner_role, responsible_label, recommended_action, action_id, action: MobileAction | null}` (null when the link does not cover the action). `MobileAction` = `{id, type, label, status, can_decide, decisions: [...], adjust: {field, label, currency, min, max} | null}`: `decisions` is what the page offers for the state (proposed: approve, adjust when bounded, assign, dispute for Profit Guard types, snooze, ignore; approved/assigned: assign, remind, snooze; in progress: remind; snoozed: approve, assign, remind), empty when the role cannot decide. The decision runs as a `magic` principal scoped to the link's hotel and ids, so core `decide` applies the same rule as a magic session (audit meta `via: magic`, channel `console`).

### WhatsApp webhook

None here: Meta posts to the foundation's `GET|POST /webhooks/whatsapp`; Brief registers `@on_whatsapp("staff", priority=20, button_prefix="brief:")`, `button_prefix="act:"` and `@on_whatsapp("staff", priority=40, kind="text")` (Adjust replies, STOP/START), and `@on_outbound_status("brief.")` for the fallback.

**Decisions** are the foundation's `POST /hotels/{slug}/actions/{id}/decide {decision, params_patch?, assignee_user_id?, assignee_role?, note?, snooze_until?}` (Canon 7 + K1, the dial, nonces). A magic session from a brief link decides only the ids in its scope (others 403 `not_surfaced`).

**Run** = `{id, hotel_slug, cadence, brief_date, period_start, period_end, scheduled_local, generated_at, trigger: schedule|send_now|api, status: generated|sent|partial|failed|empty, headline, currency, at_stake_minor, at_stake_value, delivery[], sent_at, created_by, created_at, verified: {last_24h_minor, last_24h_value, last_24h_actions, mtd_minor, mtd_value, mtd_actions, currency, text}, blocks: {inquiries?, autonomy?, compliance?, finance?, report?}, counts, items[], overdue[]}`.

**Item** = `{item_no, kind: ranked|overdue, section, detector, signal_id, action_id, action_status, title, impact_minor, impact_value, impact_estimated (the amount is an estimate: rendered "est.", and the headline says "(incl. est.)"), currency, confidence, importance, urgency, score, deadline_at, owner_role, responsible_user_id, responsible_staff_id, responsible_label, evidence_url, recommended_action}`.

## Public API (`api.py`)

- `push_now(cur, hotel_slug, *, cadence="daily", user_id=None) -> {ok, brief_id, sent_to[{user_id, name, channel}], status, reason?}`: a fresh `send_now` run; `user_id` sends to that member only; `reason` is `no_recipients`, `not_delivered` or `invalid_cadence`.
- `wa_template_info(cur, hotel_slug, *, name, language) -> {status, category, quality, category_expected, scope, rejected_reason, last_synced_at} | None`: Brief's registry (`brf_wa_templates`), the hotel's own WABA row first, then the platform's; None when neither knows the template (callers treat None as "not approved" and "category unknown").
- `submit_template(cur, hotel_slug, *, name, language, category, components) -> {name, language, status, meta_template_id}`: 400 `invalid_category` / `invalid_template` / `promo_words` (UTILITY only), 409 `not_connected` (no `brf_wa_accounts` row for the hotel). Outside `NOTIFY_MODE=live` the row is recorded `PENDING` with no Graph call.
- `wa_billing_status(cur, hotel_slug) -> {ready: bool | None, checked_at, source: account | none}` (04 spec 3.5; the Guests lane ask `brief.wa_billing_status`). `None` = unknown: treat as not ready for free-form sends. Not in the frozen list; callers use `getattr`.
