# Inquiry Guard (`inquiries`)

Owned by lane **I Inquiry Guard** from Wave 1. The foundation created this stub; only that lane edits this directory (plus `tests/modules/inquiries/`).

- Migrations: `migrations/NNNN_inquiries_<desc>.sql|.py`, numbers 0950-0999 (`python -m clefs.migrate new --module inquiries "desc"`).
- New tables start with `inq_`.
- Console routes live under `/hotels/{slug}/inquiries/` (`router.py`); guest and lead routes under `/public/`, device routes under `/device/`, webhooks under `/webhooks/`.
- Permissions are declared in `module.py` and must start with `inquiries.`.
- `api.py` is this module's public surface. Other modules may import ONLY `clefs.modules.inquiries.api`; its signatures are frozen (see `clefs/CONTRACTS.md`). Implement the bodies, never change the signatures.
- Need something from a frozen foundation file? Work around it locally and leave a `# FOUNDATION-REQUEST:` comment.

## Layout (intake side, built)

| File | What it owns |
|---|---|
| `migrations/0950-0953` | The whole 4.1 schema (core, quotes, channel accounts + payments, outcomes) so every part builds on one schema |
| `models.py` | Vocabularies mirroring the CHECKs, consent basis per source, priors (A5), segment room-night defaults (A6), SLA minutes, `InquiryExtraction` |
| `settings.py` | `InquirySettings` (SLA minutes, business hours, `ai_on_unconsented_inbound` = false, ack, privacy URL, web form ceilings) |
| `store.py` | The only writer of `inq_inquiries` / `inq_messages` columns: sealed + hmac contacts, refs, thread, `to_api` (contacts masked by role) |
| `service.py` | Stage machine `transition()` (logs `inq_stage_events`, refreshes money at risk), the reopen rule, `on_outbound()` for replies |
| `sla.py` | Business-hours clock, per-inquiry timer (`inquiries.sla_check`), `inq.unanswered`, `record_first_response()` |
| `risk.py` | Money at risk = room-nights x expected ADR x stage probability (est.) |
| `extraction.py` | Redaction, deterministic date/pax parser, LLM reader (`ai.structured`, store=False), validation, routing by intent |
| `intake/core.py` | `ingest(cur, hotel, Inbound, *, actor=None, thread=None)`: the one pipeline (dedupe, append, reopen, create, ack, timer, extraction) |
| `intake/whatsapp.py`, `intake/email.py`, `intake/leads.py` | Channel adapters (WhatsApp priority 20, email purpose `inquiries`, Meta/Google lead mapping and the Graph fetch) |
| `ack.py` | Action type `inquiries.ack` (dial `auto`, fixed text, EN/AR) |
| `consents.py` | Web form consent lines, immutable per-wording versions, recording |
| `channels.py` | `inq_channel_accounts`: web widget key + origins, Meta page (sealed token, leadgen subscribe), Google lead form keys |
| `summary.py` | `api.brief_summary` |
| `dedupe.py` | Merge suggestions (same name, overlapping dates within 72 h, one side without contact) and the merge itself |
| `metrics.py` | `inq.inquiries_count`, `inq.first_response_p50_min`, `inq.first_response_p90_min`, `inq.sla_breach_rate`, `inq.money_at_risk` |
| `jobs.py` | Tasks, signal kinds, PII registrations, `inquiries.purge` |

### For the other parts of this lane

- Every stage change goes through `service.transition(cur, row, to_stage, *, actor, reason, data, lost_reason, lost_note, remind_on, hotel)`. It raises 409 `illegal_stage` outside the 4.2 graph and resolves `inq.unanswered` when the inquiry leaves the open stages.
- After a staff or approved-AI reply reaches the guest (never the ack), call `service.on_outbound(cur, row, by="staff:<user_id>"|"ai", at=None, hotel=None, actor=None)`: it stamps `first_response_*`, cancels the SLA timer, resolves the signal, moves `new -> replied` and opens the CTWA 72 h free window when answered inside 24 h.
- Contacts in clear for sending: `store.unsealed(row) -> {phone, email}`. Append an outbound message with `store.add_message(cur, row, direction="out", ...)`.
- `risk.compute(cur, hotel, row)` returns the money-at-risk columns; write them with `store.update_inquiry`.
- The quote side should register the remaining 4.11 signal kinds (`inq.quote_awaiting_approval`, `inq.paid_not_in_pms`, `inq.leaked_to_ota`, `inq.pipeline_weekly`) and add `awaiting_approval` to `summary.brief_summary`.

## Decisions

- **Phone desk** inquiries record their first response at creation (`first_response_by = 'phone_desk'`) and move to `replied`: the caller spoke to the person who logged it, so no SLA timer runs (and they are left out of the response-time and breach metrics). Concierge-created inquiries do run the timer (the guest still needs a person).
- **Reopen** resets `first_response_*`, `first_response_due_at` and `sla_breached`: the returning guest is owed a fresh first response, and the SLA clock starts at `stage_changed_at` (the reopen time).
- **Thread bodies** are not masked as `transcript`: they are the sales request itself. Contacts are masked per the Trust matrix (revenue sees none; front desk full).
- **Web form bots**: a filled honeypot or a post under `web_form_min_fill_s` (3 s) after the signed render stamp gets the same 202 and stores nothing. The per-IP (5/h) and per-hotel (200/day) limits commit in their own transaction, so refused posts still spend a slot.
- **Marketing opt-ins** from the form are recorded as consents (purpose `marketing`, per channel identity) and, when Guests is loaded, also passed to `guests.api.record_own_contact(capture_source='inquiry')` so audiences can reach them (Canon 10). A Guests refusal never loses the inquiry.
- **PII**: 4.1 asks for two `register_pii` specs on `inq_inquiries`; the registry keeps one per table, so the 730 d rule is registered and the 365 d spam/lost rule runs in `inquiries.purge` (FOUNDATION-REQUEST in `jobs.py`).
- **Known-guest routing**: when the Guests module is not loaded (or its lookup fails) a WhatsApp sender counts as unknown, so the message becomes an inquiry rather than being dropped.

## HTTP API (intake side)

Errors are `{detail, code}`. Money fields come as `*_minor` + `currency` plus a Decimal string `*_value` (always an estimate for money at risk: label it "est."). An **Inquiry** is the `inq_inquiries` row without the sealed/hmac columns, plus `contact_name`, `contact_phone`, `contact_email` (masked by role: owner/gm/front_desk full, revenue none), `has_phone`, `has_email`, `money_at_risk_value`, `expected_adr_value`, `budget_value`.

### Console (`/hotels/{slug}/inquiries`)

| Method and path | Permission | Request | Response |
|---|---|---|---|
| `GET ` | `inquiries.view` | query `stage`, `segment`, `source` (comma lists), `sla=breached\|due\|answered`, `check_in_from`, `check_in_to`, `include_test=false`, `limit` (1-500, 100), `offset` | `Inquiry[]`, newest first; 400 `invalid_stage\|invalid_segment\|invalid_source` |
| `POST ` | `inquiries.respond` | `{source:"phone_desk", text, name?, phone?, email?, company?, language?, check_in?, check_out?, rooms?, adults?, children?}` | 201 `Inquiry + {outcome: created\|appended\|reopened}`; 400 `invalid_phone\|invalid_email\|invalid_dates` |
| `GET /{id}` | `inquiries.view` | | `Inquiry + {messages:[{id, direction, channel, body, attachments, author_type, author_user_id, template_name, action_id, created_at}], stage_events:[{from_stage, to_stage, at, actor_type, actor_id, reason}], merge_suggestions:[{id, ref, source, score, check_in, check_out, created_at, stage}]}`; 404 |
| `POST /{id}/messages` | `inquiries.respond` | `{body (1-4000), channel: whatsapp\|email}` | 201 the stored message: WhatsApp free-form only inside the 24 h window (409 `wa_window_closed`, `no_phone`), email with the `+inq-<ref>` Reply-To (409 `no_email`); counts as the first response (`service.on_outbound`); 409 `illegal_stage` for turned_down, spam, rerouted or merged; 502 `send_failed`. Sent through `replies.send_reply` (shared with the AI-draft executor); cancels any AI draft still waiting on the inquiry |
| `POST /{id}/draft-reply` | `inquiries.respond` | `{channel?: whatsapp\|email}` (default: WhatsApp inside the window, else email) | 201 `{body, language: en\|ar, channel, action}` where `action` is a new `inquiries.respond` (dial default `suggest`, ceiling `approve`; params `{inquiry_id, channel, body, language, source: ai_draft}`); an older open draft on the inquiry is cancelled. Model input is the redacted thread (`store=False`). 409 `ai_not_allowed` (consent basis not `express_web_form` while `ai_on_unconsented_inbound` is false; no model call), `dial_off`, `illegal_stage`, `no_phone`, `wa_window_closed`, `no_email`, `ai_disabled`; 422 `draft_contains_price` (the price lint failed after one retry); approving with an edited body re-runs the lint (422 `invalid_params`) |
| `POST /{id}/merge` | `inquiries.respond` | `{into_id}` | the target `Inquiry`: the thread moves, missing fields fill in, `{id}` closes as lost `SPAM_DUPLICATE` with `duplicate_of`; 409 `invalid_merge` |
| `PATCH /{id}` | `inquiries.respond` | any of `{segment, is_rfp, check_in, check_out, rooms, adults, children, language, company, contact_name, room_type_code, dates_flexible, assignee_user_id}` | `Inquiry`. A segment set here is never overwritten by extraction; the SLA due time and money at risk are recomputed. 400 `invalid_dates` |
| `POST /{id}/lost` | `inquiries.respond` | `{reason: <4.10 lost code>, note?}` | `Inquiry`; 400 `invalid_lost_reason`; 409 `illegal_stage` |
| `POST /{id}/decline` | `inquiries.respond` | `{reason: NO_AVAILABILITY\|DISPLACEMENT\|POLICY\|BELOW_FLOOR, note?}` | `Inquiry` (stage `turned_down`) |
| `POST /{id}/future` | `inquiries.respond` | `{remind_on: date (after hotel-local today), reason?, note?}` | `Inquiry` (stage `future`, reason default `DATES_CHANGED`); 400 `remind_on_required` |
| `POST /{id}/spam` | `inquiries.respond` | | `Inquiry` (stage `spam`) |
| `GET /settings` | `inquiries.view` | | `InquirySettings` |
| `PUT /settings` | `inquiries.manage` | partial `InquirySettings` (merged) | `InquirySettings`; 422 `invalid_settings` |
| `GET /widget` | `inquiries.manage` | | `{widget_key, embed_code, iframe_path:"/w/inquiry/{slug}?k=", allowed_origins[]}` (creates the widget account on first call) |
| `PUT /widget` | `inquiries.manage` | `{allowed_origins: ["https://host", ...]}` (max 20) | as GET; 400 on a non-origin |
| `POST /widget/rotate` | `inquiries.manage` | | as GET with a new key |
| `GET /channels` | `inquiries.manage` | | `{accounts:[{id, kind, config, status, connected_by, connected_at}], whatsapp_connected, email_address}` (never a secret) |
| `POST /channels` | `inquiries.manage` | `{kind:"meta_page", page_id, page_access_token, page_name?}` \| `{kind:"google_leadform", google_key?}` \| `{kind:"web_widget", allowed_origins?}` | 201 account. Meta: `status` `active` or `subscribe_failed` (Graph `subscribed_apps?subscribed_fields=leadgen`), 409 `page_in_use`. Google: adds `webhook_path` and `google_key` (shown once; paste both into Google Ads) |
| `DELETE /channels/{kind}` | `inquiries.manage` | | 204; secrets nulled; 404 `not_connected` |

### Public (`/public/inquiries`)

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /public/inquiries/{slug}/widget?k=&lang=` | widget key | | `{hotel:{slug, name, languages}, language, consents:[{key, purpose, required, text, version, language}], allowed_origins[], privacy_url, rendered_at, min_fill_s}`; 404 for an unknown hotel or key. `allowed_origins` feeds the iframe's `frame-ancestors`; `rendered_at` is a signed render stamp to send back |
| `POST /public/inquiries/{slug}` | public, 5/h per IP hash, 200/day per hotel (429 `rate_limited`) | `{name?, email?, phone?, check_in?, check_out?, rooms?, adults?, children?, message?, language?, consent_contact:true, consent_transfer_abroad:true, whatsapp_optin?, consent_marketing_whatsapp?, consent_marketing_email?, utm?{utm_*}, gclid?, page_url?, widget_key, hp:"", rendered_at}` | 202 `{ok:true}` (no inquiry detail). `rendered_at` must be the signed stamp from the widget call, at most 6 h old; a bare number, a foreign or stale stamp is a bot (202, nothing stored). 403 `invalid_widget_key`; 422 `consent_contact_required`, `consent_transfer_abroad_required` (each separately), `contact_required`; 400 `invalid_phone\|invalid_email\|invalid_dates`. Honeypot or < 3 s: 202, nothing stored |

### Webhooks

| Method and path | Auth | Behaviour |
|---|---|---|
| `GET /webhooks/inquiries/meta-leadgen` | `hub.verify_token` = `META_LEADGEN_VERIFY_TOKEN` | echoes `hub.challenge` (text/plain); 403 otherwise |
| `POST /webhooks/inquiries/meta-leadgen` | `X-Hub-Signature-256` over the raw body with `META_APP_SECRET` (401 `bad_signature`; 503 `not_configured` without the secret) | 200 `{ok, queued, skipped}`. Each `leadgen` change maps `page_id` to its hotel and queues `inquiries.meta_lead_fetch` (dedupe `inq.meta_lead:<leadgen_id>`). The task GETs `/{WA_API_VERSION}/{leadgen_id}?fields=field_data,created_time,ad_id,form_id,campaign_id` with the sealed page token, retries with a growing delay, and after 72 h raises `inq.lead_fetch_failed` |
| `POST /webhooks/inquiries/google-leadform/{account_key}` | `account_key` (sha256 stored) + body `google_key` compared in constant time (404 `unknown_leadform`, 403 `bad_google_key`) | 200 `{ok, outcome: created\|appended\|reopened\|duplicate}`; deduped on `lead_id`; `is_test` stored flagged (no ack, no signal, excluded from lists by default); 503 only on a transient DB error |

### Inbound dispatch (no HTTP route of its own)

- WhatsApp: `@on_whatsapp("guest", priority=20)` when a CTWA `referral` is present or the sender is not a known in-house guest. Stores `ad_referral` (source_url, source_id, source_type, headline, body, media_type, ctwa_clid), `ctwa_clid`, `wa_window_expires_at`.
- Email: `@on_inbound_email("inquiries")` for `<alias>+inq[-<ref>]@in.clefs.world`; `+inq-<ref>` or In-Reply-To/References join the thread; quoted history and signatures stripped; OTA senders and `Auto-Submitted` mail left unhandled.

### Jobs, signals, actions

- Tasks: `inquiries.extract`, `inquiries.sla_check` (queued at the due time, dedupe `inq.sla:<id>:<due_epoch>`), `inquiries.meta_lead_fetch`. Scheduled: `inquiries.purge` (daily 03:45 hotel-local).
- Signals: `inq.unanswered` (money_available, daily; impact = money at risk est.; due = SLA; recommended `inquiries.respond`), `inq.lead_fetch_failed` (service_risk, weekly).
- Action type: `inquiries.ack` (default and ceiling `auto`, decide perm `inquiries.respond`).
- Events: `inquiries.changed` `{id, ref, stage, outcome, source}` to owner, gm, revenue, front_desk.
- Module API: `create_inquiry` (built; `NotConfigured` when the module is off for the hotel), `brief_summary` (built), `verified_outcomes` (built: delegates to `verify.verified_outcomes`; `[]` for an unknown hotel or one with Inquiry Guard off).

## Layout (quote-to-cash side, built)

| File | What it owns |
|---|---|
| `quotes.py` | Quote builder (4.6): nightly rates from `demand.api.price_guardrails` (recommended else BAR, policy discount off BAR, clamp to [floor, ceiling]; a night without guardrails is `source='manual'`), staff edits, `core.tax.compose_stay` in ONE call, validity, deposit schedule, cancellation, group terms, the 4.7 auto-send reasons, `request_send`, the send and follow-up executors, the public page data, accept, expiry and the lost-reason automation. `QuoteSettings` lives under `settings.quotes` |
| `templates.py` | `inq_quote_ready_v1` / `inq_quote_followup_v1` EN+AR (seed, owner approval, Meta submission through `brief.api.submit_template`), the category gate `plan_send` and the 131049 hook |
| `actions.py` | Action types `inquiries.send_quote`, `inquiries.send_payment_link`, `inquiries.follow_up`, `inquiries.direct_booking`, `inquiries.create_pms_reservation` |
| `payments/base.py` | `PaymentLinkRequest`, `PaymentLink`, `PaymentEvent`, `Account`, the `PaymentProvider` Protocol and a replaceable HTTP transport (`set_transport`) |
| `payments/{ngenius,telr,stripe,tap,manual}.py` | The adapters (UNVERIFIED shapes are marked in each file) |
| `payments/service.py` | Provider accounts, `request_link` and its executor, the webhook inbox, `process_event` (only `fetch_status` moves a link), `apply_status`, fees, reconciliation, manual mark-paid, cancel |
| `payments/routes.py` | Every HTTP route of this side: `console`, `public` and `webhooks` routers, listed in `MODULE.routers` (console before the intake router) |
| `replies.py` | The shared reply send path (`send_reply`), the price lint, the AI draft (`draft_reply`) and the `inquiries.respond` executor and params check |
| `verify.py` | Candidates, score, outcome, ledger attachment (one carrier), verifier, reversals, match reviews, the stay-event consumer, `verified_outcomes` |
| `leak.py` | `inquiries.leak_match` (internal only) and `record_leak` |
| `signals.py` | `inq.quote_awaiting_approval`, `inq.paid_not_in_pms`, `inq.leaked_to_ota`, `inq.pipeline_weekly`; the weighted pipeline; stage-probability recalibration; `awaiting_approval` for the brief summary |
| `metrics.py` | Adds `inq.verified_revenue`, `inq.commission_avoided`, `inq.conversion_rate`, `inq.lost_reason_mix`, `inq.leak_share`, `inq.weighted_pipeline` |
| `jobs.py` | Adds the payment, verify, leak, expiry, pipeline, recalibrate and template-seed jobs, the stay-event consumer and the outbound-status hooks |

## Decisions (quote-to-cash side)

- **Guardrails.** Each night and room type is priced from `demand.api.price_guardrails` (implemented in `demand/guardrails.py`), which answers `{floor, ceiling, recommended, bar, bar_captured_at, currency, confidence, reasons[], demand_index, source}`. `source` is `rate_guardrails` (an owner floor/ceiling row applies; the most specific of room type, month and weekday wins), `bar_only` (no row but a BAR: floor BAR -10%, ceiling BAR +30%, rounded to 5) or `fallback` (neither, but indicative comp rates: 80% to 130% of the comp median). The line rate is the recommendation, else the BAR, less any policy discount off BAR, clamped to [floor, ceiling] (`source='guardrails'`). A `fallback` night is never auto-send eligible (`fallback_guardrails`); a stale or missing BAR adds `bar_stale` / `no_bar`.
- **No guardrails.** When Demand has nothing to go on it returns None: the night is left empty (`source='manual'`), staff type the rate (no clamp), the quote is `below_bar` and never auto-sends (`no_guardrails`). The same happens when the Demand call fails (module not loaded, unmigrated or broken): every Demand, Brief or Profit Guard call runs in a savepoint (`quotes.guarded`) and degrades to its documented default. `price_guardrails` itself does not check the hotel's Demand switch; the owner's floor/ceiling rows apply whenever they exist.
- **Validity** is FIT/unknown 48 h, corporate/long stay 72 h, group/event 7 d; 24 h when any night's `demand_index` is 80+; never past 00:00 hotel time on the arrival day; at least 1 h.
- **Deposits.** FIT, corporate, event: the first night (its own `compose_stay`, so 504.34 of 1,008.68). Long stay: the first 30 nights. Group: cumulative 25% at signing, 50% at 60 d and 75% at 30 d before arrival (a milestone already inside its window is due at signing). Then the balance at arrival. A deposit carrying TD has the "refundable if you do not check in" note.
- **Send.** A holder of `inquiries.approve` pressing Send approves at once (a human decision); anyone else, and every system path, goes through the dial. A system approval is not a human one, so the executor revokes it. Editing a quote cancels its pending send action (a fresh decision is needed); a sent quote is locked (make a new version, which supersedes the old one).
- **Delivery.** Free-form WhatsApp inside the 24 h window, else the template through the category gate, else email; no channel ends the action `failed` with `template_missing`. A template the gate treats as marketing (and every follow-up) is queued with `category='marketing'`, so the outbox holds it to [09:00, 18:00) Dubai; `+1` numbers get email; a window start after the quote's expiry means email now.
- **131049.** The outbox re-sends a 131049 failure after 24 h (its retry copy). When that retry fails with 131049 too, the hook cancels the next copy and sends the email instead.
- **Quote page token** = HMAC(`SECRET_KEY`, hotel:id:created_at), base64url. Only its sha256 is stored; follow-ups and the email fallback link the same page. Rotating `SECRET_KEY` invalidates open quote links.
- **Payment link delivery.** There is no payment template: inside the window it is WhatsApp text, else email, and the quote page always shows the newest open link (`pay_url`, only ever an https URL: checked on request, in the manual adapter and again when read). Accepting with no provider connected keeps the acceptance and returns `payment_reason: no_payment_provider`.
- **Payments.** A webhook is only stored; the task asks the provider (`fetch_status`) and applies that. A paid link never moves back to failed; an expired or cancelled one can still become paid (a late payment counts). A payment below the link amount is `partially_paid`. The fee is the provider's when reported, else `amount x fee_pct + fee_fixed_minor` from the account config, else the provider default (N-Genius 2.5% demo, Telr 2.25% + 0.50, Stripe 2.9% + 1.00, Tap 2.5%). A refund reverses the verification (the whole outcome).
- **Verification.** A 0.75+ match with no paid link waits (`pending`) until the stay is IN_HOUSE or CHECKED_OUT. A staff-confirmed review counts as a full match; a rejected one is never proposed again. One reservation verifies at most one inquiry: `candidates()` skips a reservation another inquiry already verified against (first verified wins, row-locked against a concurrent job), backed by the partial unique index of migration 0954, so a guest's two unlinked inquiries never count one booking twice. With no reference OTA (Profit Guard) the commission avoided is 0 and the net is minus the fees. An inquiry verified from `new`, `replied`, `lost` or `future` moves to `confirmed_verified` with its own stage-event line (the stage machine has no such edge; request below).
- **Lost-reason automation.** An expired quote with no inbound since it was sent closes as lost after 2 follow-ups went out, else 7 d after expiry; the reason is `SLOW_RESPONSE` when the first-response SLA was breached, else `GUEST_UNRESPONSIVE`. An expired link that leaves a `payment_pending` inquiry with nothing open or paid closes it as `PAYMENT_LINK_EXPIRED`. A leak sets `BOOKED_VIA_OTA`.
- **Leaks stay internal.** `leak.py` imports no notify; the signal names the ref and channel only; leak details in `/outcome` and `/match-reviews` are hidden without `guests.view`; `inq.leak_share` needs `inquiries.approve`.
- **Recalibration** (monthly) writes a stage's probability only with 30+ closed inquiries behind it in the last 365 d.

### Requests to other owners

- `summary.py` owner: add `signals.awaiting_approval(cur, hotel_slug)` to the brief summary.
- `service.py` owner: allow `new|replied|lost|future -> confirmed_verified` so verification can use `transition()`.
- Foundation: `notify.cancel(cur, outbox_id)` (the 131049 hook writes `outbound_messages.status` today).

## HTTP API (quote-to-cash side)

Errors are `{detail, code}`. Money is minor units plus a Decimal string (`*_value`) in the row's currency. Permissions: `inquiries.view` (owner, gm, revenue, front_desk), `inquiries.quote` (same), `inquiries.approve` (owner, gm, revenue), `inquiries.manage` (owner, gm).

**Quote** = the `inq_quotes` row without `public_token_hash`, plus `room_value`, `sc_value`, `mf_value`, `vat_value`, `td_value`, `total_value`, `has_public_link`, `max_discount_pct` (string); each line `{stay_date, room_type_code, rooms, rate_minor, bar_minor, floor_minor, ceiling_minor, discount_pct, source: guardrails|staff|manual, demand_index, bar_captured_at, guardrails_source, below_floor_reason?, *_value}`; `deposit_schedule[] {seq, purpose: deposit|full|balance, label, due_on, amount_minor, amount_value, includes_td_minor, td_note{en,ar}|null, cumulative_pct?}`; `group_terms {cutoff_date, attrition_pct, min_rooms_after_attrition, rooming_list_due, label}|null`; `auto_send_reasons[]` from `rates_missing, no_guardrails, fallback_guardrails, below_bar, no_bar, discount, bar_stale, segment_not_allowed, too_many_rooms, too_many_nights, total_over_limit, low_confidence, missing_fields`.

**PaymentLink** = the `inq_payment_links` row without `provider_payload`, plus `amount_value`, `includes_td_value`, `paid_amount_value`, `fee_value`, `evidence_file_id`.

### Console (`/hotels/{slug}/inquiries`)

| Method and path | Perm | Request | Response |
|---|---|---|---|
| `GET /pipeline` | view | | `{currency, estimated: true, label: "est.", as_of, total_minor, total_value, count, rows[{month, segment, count, weighted_minor, value}], months[{month, weighted_minor, value}]}` (open inquiries arriving today or later) |
| `GET /match-reviews?status=open\|confirmed\|rejected\|superseded&kind=verify\|leak` | approve | | `[{id, inquiry_id, ref, reservation_id, kind, score, evidence, status, res_check_in, res_check_out, channel_code, pms_res_id, res_guest_name}]`; guest name and identity evidence null without `guests.view` |
| `POST /match-reviews/{id}/confirm` / `reject` | approve | | `{id, status, kind, verification?|leak?}`; 404; 409 `review_closed` |
| `GET /payment-accounts` | manage | | `[{id, kind, provider, config, status, connected_by, connected_at}]` (never secrets) |
| `POST /payment-accounts` | manage | `{provider: ngenius\|telr\|stripe\|tap, config{outlet_ref?, api_base?, webhook_header?, store_id?, test_mode?, fee_pct?, fee_fixed_minor?, currency?, post_url?}, secret{api_key?\|secret_key?\|auth_key?, webhook_secret?\|advice_secret?}}` | 201 account + `webhook_key` and `webhook_path` (shown once; reconnecting rotates them); 400 `invalid_api_base` unless `api_base` is an `https://*.ngenius-payments.com` gateway (it is fetched server-side; other providers drop it) |
| `DELETE /payment-accounts/{provider}` | manage | | 204; 404 `not_connected` |
| `GET /templates` | view | | the latest `inq_templates` row per name and language (seeded on first read) |
| `POST /templates/{id}/approve` | manage | | the row with `approved_at`, `meta_status` (Meta's status, `not_connected` or `submit_failed`) |
| `GET /quote-settings` / `PUT` | view / manage | partial `QuoteSettings` | `{group_min_rooms, group_discount_pct, long_stay_min_nights, long_stay_discount_pct, group_deposits[{pct, days_before_arrival}], group_cutoff_days, group_attrition_pct, group_norms_label, cancellation{en,ar}, inclusions{en?,ar?}, public_base_url}` (empty `public_base_url` = the deployment's `WEB_ORIGINS[0]`); 422 `invalid_settings` |
| `POST /payment-links/{lid}/cancel` | quote | | PaymentLink; 409 `illegal_link_state` |
| `POST /payment-links/{lid}/mark-paid` | approve | `{evidence_file_id}` (an uploaded file of the hotel) | PaymentLink (paid, fee 0); 409 `not_manual`; 400 `evidence_required` |
| `GET /{id}/quotes` | view | | `Quote[]`, newest version first |
| `POST /{id}/quotes` | quote | `{rooms?: [{room_type_code, rooms}], edits?: [{stay_date, room_type_code?, rate_minor}], reason?, language?: en\|ar}` | 201 Quote (draft; older open versions superseded). 400 `dates_required` / `invalid_rooms` / `unknown_line` / `invalid_rate` / `above_ceiling` / `reason_required`; 403 `below_floor_needs_approval`; 409 `illegal_stage` |
| `GET /{id}/quotes/{v}` | view | | Quote; 404 |
| `PUT /{id}/quotes/{v}` | quote | `{edits[], reason?, language?}` | Quote (back to draft; a pending send action is cancelled); 409 `quote_locked` once sent |
| `POST /{id}/quotes/{v}/send` | quote | `{request_approval?: false}` | `{quote, action}`. A holder of `inquiries.approve` approves at once unless `request_approval`; otherwise the dial decides (`proposed` = awaiting approval, `inq.quote_awaiting_approval` raised). 409 `quote_locked` / `rates_missing` / `quote_expired` / `dial_off` |
| `GET /{id}/payment-links` | view | | `PaymentLink[]` |
| `POST /{id}/payment-links` | quote | `{quote_version, purpose: deposit\|full\|balance, amount_minor?, milestone?, provider?: ngenius\|telr\|stripe\|tap\|manual, url? (manual), request_approval?}` | 201 `{action}` (`inquiries.send_payment_link`). 409 `quote_not_sent` / `no_payment_provider` / `dial_off`; 400 `invalid_amount`, `invalid_payment_url` (`url` must be an absolute `https://` URL with a host; it becomes the Pay button's href) |
| `GET /{id}/outcome` | view | | `{inquiry_id, outcome: null}` or the `inq_outcomes` row plus `*_value`; leak details null without `guests.view` |

### Public (the `/q/[token]` page)

| Method and path | Behaviour |
|---|---|
| `GET /public/inquiries/quotes/{token}?lang=en\|ar` | 60 per 10 min per IP. `{hotel{slug,name,city,star_rating,locale}, ref, version, status: sent\|accepted\|expired\|superseded\|withdrawn\|draft..., language, valid_until, currency, stay{check_in, check_out, nights, rooms}, lines[{stay_date, room_type_code, room_type_name, rooms, rate_value}], taxes{room, service_charge, municipality_fee, vat, tourism_dirham, tourism_dirham_label, td_nights_charged, total}, fee_notices[], deposit_schedule[{seq, purpose, label, due_on, amount_value, includes_td_value, td_note}], cancellation, group_terms, inclusions, can_accept, accepted, noindex: true, pay_url}`. `X-Robots-Tag: noindex`, no-store. Never a contact, BAR, floor, ceiling or trace. 404 for an unknown token |
| `POST /public/inquiries/quotes/{token}/accept` | 60 per 10 min per IP. `{ok, status: "accepted", payment_action_id, payment_action_status, payment_reason?}` (a replay adds `replayed: true`). Creates the first-milestone `inquiries.send_payment_link` action; 409 `quote_not_open` / `quote_expired` |

### Webhooks

| Method and path | Behaviour |
|---|---|
| `POST /webhooks/inquiries/payments/{provider}/{account_key}` | 404 (nothing stored) for an unknown provider or account key. Bad signature: a row in `inq_payment_events_rejected` (headers redacted, body sha256) and 401 `bad_signature`; an unparseable body 400 `unparseable`. Otherwise each event is stored once (`ON CONFLICT (provider, provider_event_id) DO NOTHING`; no id = the body's sha256), `inquiries.payment_event_process` is queued and the reply is 200 `{ok, stored}` at once. Only that task's `fetch_status` changes a link. Signatures: N-Genius header secret (`X-Webhook-Secret` or `config.webhook_header`), Stripe `Stripe-Signature` (5 min tolerance), Tap `hashstring`, Telr advice is a hint (checked only when an `advice_secret` is set). Body cap 256 KB |

### Jobs, signals, actions, metrics (this side)

- Tasks: `inquiries.payment_event_process`, `inquiries.verify_inquiry`. `inquiries.purge` also empties `inq_payment_events.payload` at 365 d (`inq_payment_links.provider_payload` is registered with `register_pii`). Scheduled: `inquiries.payment_reconcile` and `inquiries.verify` (every 60 min), `inquiries.quote_expiry` (every 15 min; expiry, follow-ups, lost automation), `inquiries.leak_match` (05:00), `inquiries.pipeline_weekly` (Monday 06:30), `inquiries.recalibrate` (1st, 04:00), `inquiries.template_seed` (03:50).
- Stay events: consumer `inquiries.verify_on_stay` (created, checked_in, checked_out on direct channels re-score; cancelled, no_show reverse). Outbound status hooks on `inquiries.quote_ready` and `inquiries.follow_up` (131049).
- Signals: `inq.quote_awaiting_approval` (money_available; daily only while unanswered past SLA, else weekly), `inq.paid_not_in_pms` (service_risk; daily within 48 h of check-in, else weekly), `inq.leaked_to_ota` (money_lost, weekly), `inq.pipeline_weekly` (verified, weekly).
- Action types: `inquiries.send_quote` and `inquiries.send_payment_link` (default approve, ceiling auto_within_limits, limits `{segments: [fit, corporate], max_rooms: 2, max_nights: 7, max_total_minor: 500000, max_bar_age_days: 7}`, decide `inquiries.approve`, verify daily until check_out + 7 d), `inquiries.follow_up` (approve / auto_within_limits, `{max_followups: 2}`), `inquiries.direct_booking` (auto, no-op executor), `inquiries.create_pms_reservation` (human task, approve).
- Metrics: `inq.verified_revenue`, `inq.commission_avoided` (net, caused only), `inq.conversion_rate`, `inq.lost_reason_mix` (+ `lost_reason` dimension), `inq.leak_share` (perm `inquiries.approve`), `inq.weighted_pipeline` (by check-in).
