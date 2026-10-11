# Guests (`guests`)

Owned by lane **F2 Guests + Journeys**. Two parts share this directory:

- **identity** (this README's HTTP section): hashed contacts, consent-gated profiles, stay linking, the returning-guest badge, direct codes, marketing eligibility (`can_send_marketing`), STOP and unsubscribe. Files: `identity.py`, `contacts.py`, `evidence.py`, `marketing.py`, `linking.py`, `optout.py`, `registrations.py`, `router.py`, `api.py`, `errors.py`, migrations 0650-0669.
- **journeys** (`journeys/**`, migrations 0670-0689): the J1-J8 planner, dispatcher, templates, replies and the data behind the public `/g` pages (see "Journeys" and "HTTP API (journeys part)" below).

Conventions: migrations `migrations/NNNN_guests_<desc>.sql|.py`; new tables start with `gst_` (legacy `guest_rooms` is grandfathered); console routes under `/hotels/{slug}/guests/`, public ones under `/public/`; permissions start with `guests.`; other modules import only `clefs.modules.guests.api` (frozen signatures, `clefs/CONTRACTS.md`).

## Rules the identity part enforces

- **No raw contacts in gst_ tables.** A phone/email becomes `core.privacy.contact_hmac` (per-hotel pepper). Only the guest's own captured value is kept, sealed (`privacy.seal`) on its `guest_direct`/`guest_stay` point. GuestModal values that arrive through the legacy roster PUT are hashed and scrubbed by the hourly job (only a masked copy stays in `guest_rooms`).
- **Profiles need `profile_memory` AND `transfer_abroad`.** No consent, no row; a withdrawal deletes the row (stays unlink).
- **Health data.** Allergies live in `preferences.health` and are masked with `core.pii` class `health` everywhere (badge labels are built after masking). The `profile_memory` wording discloses it (since `gst.v2`, migration 0651).
- **Consent wording.** The platform wording of every purpose this module captures is `gst.v3` (migration 0679, `identity.CONSENT_TEXT_VERSION`), in EN, AR, JA, KO and TH (`identity.CONSENT_LANGUAGES`; other languages record the EN wording). Its `transfer_abroad` text says the data is stored in Google Cloud's India region and that Google Cloud's Gemini models may process messages in other countries, including the United States. `identity.text_version` pins `gst.v3` even when another lane seeds a newer platform text for the same purpose (a hotel's own wording still wins). `gst.v1` (0675, which named the UAE and Singapore) and `gst.v2` stay, retired, as the evidence older consents point at.
- **Canon 19.** A J1-form contact on an OTA booking is a `guest_stay` point (this stay only, check-out + 30 d) and may not carry marketing or profile consent. `pms_ota`, `ota_alias` and `guest_stay` points never link a profile, never enter `marketing_audience`, never pass `can_send_marketing`.
- **Transfer consent on every capture** (`record_own_contact`, all capture sources), checked before anything else.
- **Marketing** (`can_send_marketing`): consent per channel, suppression, provenance, the +1 WhatsApp block (unknown prefix = blocked), the 09:00-18:00 hotel-time window (narrowed by settings), 1 per hotel day, 1 pre-stay and 1 in-stay per reservation, 2 post-stay per rolling 30 days across journeys, Demand exports and upsell (`gst_marketing_sends`). The 03 spec's `guests.rebook_offers` job is superseded by journey step J8 (04 spec 3.1); J8's +30/+60 d spacing keeps "one rebooking offer per 30 days".
- **STOP.** WhatsApp handler at slot `guests.optout_consent` (5): a whole-message stop word (EN/AR) from someone the hotel holds or has messaged suppresses them (`all`; "stop promotions" = `marketing`), withdraws messaging consents and sends one acknowledgement. Email: `api.unsubscribe_path()` links (GET describes, POST unsubscribes). A fresh opt-in lifts a `stop` suppression only, and only on proof that the guest controls the value (`contacts.STOP_LIFT_SOURCES`: a registration card or a signed-in staff entry, or evidence naming an inbound message sent FROM that value). A J1-form or concierge capture of a suppressed value keeps the suppression, leaves the point suppressed and does not record the messaging grants it covers (audited as `held_by_suppression`). Public pages validate posted consent text versions against the wording they could have shown (422 `consent_not_allowed`); an expired `/g` link returns only `{purpose, state, language, hotel}` (feedback links keep `review_links` and `submitted`).
- **Registration-card evidence** (`evidence.py`): `card_crop` (<= 300 KB, no MRZ; images are checked by `clefs.ai`, refused when AI is off) or `staff_attestation` with a signature image. Evidence files are deleted when the contact is forgotten.

## Registered keys

- Job `guests.link_stays` (hourly, per hotel): scrub/link roster contacts, snapshot + link PMS reservations (check-out >= yesterday) through `stays_port`, raise VIP arrivals.
- Signal kind `guests.returning_guest_arrival` (service_risk, daily; no name or health data in it).
- Metrics `guests.returning_share` (percent, from core `reservations`/`reservation_contacts`, dimension `channel`) and `guests.direct_rebook_conversions` (count of redeemed direct codes, dimension `basis`: `j8_code` | `direct_code`). Owned here: the journeys part must not re-register them.
- PII registry: `gst_profiles` (730 d on `updated_at`), `gst_contact_points` where `guest_direct` (730 d on `last_used_at`), roster `gst_stays` (730 d), `files` with purpose `guests.contact_evidence` (730 d).
- Permissions: `guests.view`, `guests.manage` (owner, gm, front_desk), `guests.contacts.capture` (owner, gm, front_desk, device), `guests.journeys.view` (owner, gm, front_desk), `guests.journeys.manage` (owner, gm).

## Journeys plug-in point

`module.py` merges `clefs.modules.guests.journeys.manifest` when it exists: optional `ROUTERS`, `PERMISSIONS`, `WORKER_SERVICES`, `on_startup`. Journeys code may import the identity internals directly (same module): `contacts.record_own_contact / upsert_point / mirror_reservation_contacts / suppress / is_suppressed`, `marketing.can_send_marketing / record_send`, `identity.issue_direct_code / redeem_direct_code / text_version / consent_text` (render tick boxes with `consent_text`, so the pinned wording is the one shown), `linking.snapshot_reservation / link_reservation`.

## HTTP API (identity part)

All console routes need a signed-in member (never legacy open access). Responses carrying guest data are masked for the caller's role by `core.pii` (name `guest_name`, contacts `guest_contact`, allergies `health`). Errors are `{detail, code}`.

| Method and path | Permission | Request | Response |
|---|---|---|---|
| `GET /hotels/{slug}/guests/overview` | `guests.view` | | `{profiles, returning_profiles, vip_profiles, returning_arrivals_today, consents: {profile_memory, marketing_whatsapp, marketing_email, whatsapp_updates, transfer_abroad}, suppressed_contacts, direct_codes: {issued, redeemed}, returning_badge, day}` (consent counts = identities whose latest decision is a grant) |
| `GET /hotels/{slug}/guests/profiles?contact=&vip=&returning=&limit=50&offset=0` | `guests.view` | `contact` is an exact phone/email (hashed, never stored; names are sealed so there is no name search) | `{items: [Profile], total}` |
| `GET /hotels/{slug}/guests/profiles/{id}` | `guests.view` | | `Profile + {stays: [{id, reservation_id, origin, room, check_in, check_out, source, status}], consents: [ConsentView], direct_codes: [Code]}`; 404 `profile_not_found` |
| `PATCH /hotels/{slug}/guests/profiles/{id}` | `guests.manage` | `{first_name?, language?: en\|ar, vip?, preferences?: {tags[], notes, health: {allergies[], notes}}}` (preferences replace; a role that cannot see health keeps the stored health block) | `Profile` |
| `DELETE /hotels/{slug}/guests/profiles/{id}` | `guests.manage` | | `{ok}`: profile_memory withdrawn on every identity, row deleted |
| `POST /hotels/{slug}/guests/rooms/{room}/identity` | `guests.manage` | GuestModal block `{phone?, email?, first_name?, consents: {transfer_abroad, profile_memory, whatsapp_updates, marketing_whatsapp, marketing_email}, consent_language: en\|ar\|ja\|ko\|th, preferences?, check_in?, check_out?}` | `{stay_id, point_ids[], profile_id, profile: Profile\|null, contact: {phone, email} (masked)}`; 422 `consent_not_allowed` without transfer consent; 400 `invalid_phone`/`invalid_email`/`contact_required`/`invalid_room` |
| `GET /hotels/{slug}/guests/arrivals?day=` | `guests.view` | `day` hotel-local, default today | `{day, items: [Badge]}` |
| `POST /hotels/{slug}/guests/consents/lookup` | `guests.view` | `{kind: phone\|email, value}` (POST so the value is never in a URL) | `ConsentView + {points: [{id, provenance, status, capture_source, reservation_id, usable_until, created_at}], profile_id}` |
| `POST /hotels/{slug}/guests/opt-out` | `guests.manage` | `{kind, value, scope: all\|marketing}` | `{ok, created}` (reason `manual`) |
| `POST /hotels/{slug}/guests/forget` | `guests.manage` | `{kind, value}` | `{ok, points_purged, profiles_deleted, evidence_files_deleted}` (STOP suppressions are kept) |
| `GET /hotels/{slug}/guests/codes?limit=100` | `guests.view` | | `[Code]` |
| `POST /hotels/{slug}/guests/codes` | `guests.manage` | `{profile_id?, discount_pct?, valid_to, prefix?}` | `Code` (nothing is sent); 400 `invalid_valid_to`, 404 `profile_not_found` |
| `GET /hotels/{slug}/guests/rebooking?days=70&limit=100` | `guests.view` | | `{day, items: [{stay_id, reservation_id, room, check_out, days_since, source, ota, profile: Profile\|null, marketing: {whatsapp, email}, suppressed, eligible, j8: [{seq, status, due_at, skip_reason}]}]}` |
| `POST /hotels/{slug}/guests/contacts` | `guests.contacts.capture` (a device must be Front Office) | registration card `{kind, value, reservation_id?, room?, consents, consent_text_versions?, language: en\|ar\|ja\|ko\|th, evidence: {kind: card_crop\|staff_attestation, file_id?, signature_file_id?}, first_name?, preferences?}` (evidence files are uploaded first through `POST /hotels/{slug}/files` with purpose `guests.contact_evidence`; omitted versions = current wording) | 201 `{point_id, contact: {kind, value (masked; null for devices)}, profile_id, consents}`; 403 non-Front-Office device; 422 `consent_not_allowed` (`reason` in the error object server-side), `evidence_required`, `evidence_too_large`, `evidence_has_id`, `evidence_unverified`, `evidence_type` |
| `GET /public/guests/unsubscribe/{token}` | public, 30 per 10 min per IP | | `{hotel: {slug, name}, channel, status: subscribed\|unsubscribed}`; 404 `link_invalid` |
| `POST /public/guests/unsubscribe/{token}` | public, same limit | any body (RFC 8058 one-click) | `{ok, status: "unsubscribed", channel}` (marketing suppression) |

Shapes:

- `Profile = {id, first_name, language, preferences: {tags[], notes, health: {allergies[], notes}\|null}, stays_count, first_seen, last_seen, vip, returning, has_phone, has_email, contacts: [{kind, value}], created_at, updated_at}`
- `Badge = {stay_id, reservation_id, room, check_in, check_out, profile_id, stay_number, vip, first_name, tags[], health, label}`; `label` like "3rd stay · extra pillows · no nuts", composed after masking.
- `ConsentView = {ref (12-hex prefix of the identity), purposes: {<purpose>: {granted, at, text_version, source}}, suppressed, marketing_suppressed}`
- `Code = {id, code, hotel_slug, guest_profile_id, discount_pct, valid_to, redeemed_at, redeemed_reservation_id, plan_id, offer_id, created_at}`

Legacy roster routes (`legacy.py`, golden-tested): `GET /hotels/{slug}/guests`, `PUT|DELETE /hotels/{slug}/guests/{room_number}`, `POST /hotels/{slug}/guests/seed`.

## api.py (for other modules)

Frozen (04 spec 7.2): `guest_context_for_room` (adds `returningGuest: {stayNumber}` only for a consented returning guest), `marketing_audience`, `record_own_contact`, `is_known_guest_sender`, `can_send_marketing`, `record_external_send`, `opt_out`, `journey_timeline`, `arrival_prefs`, `ConsentNotAllowed`.

Additive: `returning_badge(cur, hotel_slug, *, room, role, departments=(), at=None) -> Badge | None` `returning_badges(cur, hotel_slug, *, rooms, role, departments=(), at=None) -> dict[room, Badge]` (the same badge for a page of cards in one query: the service queue and device feed serializers call it once per response and put `badges.get(room)` on each card as `returning`) and `returning_arrivals(cur, hotel_slug, *, day, role, departments=()) -> list[Badge]` (service queue, device, handover: pass `core.pii.role_for_ctx(ctx)` and `ctx.departments`), `forget_contact(cur, hotel_slug, *, kind, value, source, actor=None) -> dict` (Trust DSR erase), `unsubscribe_path(hotel_slug, *, identity_hmac, channel) -> str` (every marketing email).

`room_for_sender(cur, hotel_slug, *, sender_hmac=None, phone_hmac=None) -> str | None` (concierge WhatsApp chat; `phone_hmac` is an alias of `sender_hmac`). Returns the room number of the one stay the sender's number (per-hotel contact HMAC) belongs to right now, so the chat may act for that room as verified. The body is in `sender_room.py`:
- It reads core `reservation_contacts` of IN_HOUSE reservations (through `stays_port`, guarded) and the guests snapshot (`gst_stays.contact_hmacs` and the stay's unpurged `gst_contact_points`).
- Only IN_HOUSE stays, or roster stays whose dates cover today, count. A CONFIRMED arrival never does, because the departing guest may still be in the room.
- A stay overtaken by a later in-house stay in the same room is ignored (a check-out the PMS never sent).
- None when nothing matches or when the number matches two or more rooms: the guest then types a room, and requests carry it unverified.
- Only HMACs are compared. It never raises for a sender it does not know.

## Journeys (04 spec section 3)

A fixed J1-J8 library, no campaign builder. Plans come from `core.stay_events` (consumer `guests.journeys`, idempotent on the event id); every send is a ledger action governed by the autonomy dial. Files are listed in `journeys/__init__.py`.

- **Steps and types.** `guests.journey_j1_confirm`, `_j2_planning`, `_j3_offer` (MARKETING), `_j4_checkin`, `_j5_daycheck`, `_j7_feedback` (UTILITY, costed as marketing), `_j8_rebook` (MARKETING, direct code minted per plan); internal gates `j6_pre` / `j6_post`; human tasks `guests.checkout_recovery` (owner gm) and `guests.offer_accept` (front desk). Daily-cap collision order J1 > J4 > J5 > J7 > J2 > J3 > J8.
- **Consent basis per step.** Utility sends record `contract:reservation:<id>`; J3/J8 record `consent:<consent_id>` (channel-specific `marketing_whatsapp` / `marketing_email`).
- **Canon 19.** `ota_alias` (Booking usable to check-out + 30 d; Expedia and the Agoda heuristic to check-out), `pms_ota` and `guest_stay` points never receive marketing; `pms_ota` phones get no business-initiated WhatsApp until the guest writes first or opts in on property. J7 to OTA-provided contacts needs `j7_for_ota_provided_contacts` (default false).
- **Windows and caps.** Marketing [09:00, 18:00) hotel time (Asia/Dubai for UAE hotels), half-open, narrow-only; utility [08:00, 21:00) (J1 exempt within 2 h of booking); 1 business-initiated message per identity per local day; J3 once per reservation; post-stay 2 per rolling 30 d across J8, Demand exports and upsell. `+1` numbers get no WhatsApp marketing.
- **Meta 131049.** The plan goes `deferred` (`attempt=2`, due at max(now + 24 h, next window start)) and the same action is re-executed with key `gst:{plan_id}:a2`; a second 131049 falls back to email only with `marketing_email` consent, else skips as `meta_131049`. Every block upserts the weekly `guests.meta_cap_blocks` signal and removes the send from the marketing caps.
- **Replies** (WhatsApp slot `guests.journey_replies`, priority 10, after the priority-5 STOP handler): deterministic EN/AR lexicon first; `ai.structured(store=False)` only with `transfer_abroad` consent or `ai_on_unconsented_inbound`, and it can only upgrade. A J5 negative in house calls `service.api.create_request` (after check-out `create_followup(kind='guest_issue')`), sends one fixed ack, then a human takes over and marketing pauses. YES after J3 files `guests.offer_accept`; YES after the J5 consent ask records `marketing_whatsapp` only and replies with a `/g/[token]` transfer-tick link.
- **J6 checkout recovery gate.** `j6_pre` reads `service.api.open_items_for_stay` and unresolved J5 negatives; if any, it files `guests.checkout_recovery`, notifies `service.api.on_duty(role='duty_manager')` and raises `guests.checkout_open_issues` (daily when an item is L1, else weekly). `j6_post` turns items still open into `create_followup(kind='post_stay_recovery')`. J6 never reads or writes J7 plans.
- **J7, no sentiment routing, no incentives.** `feedback.review_links(cur, hotel_slug)` reads settings only; every guest sees the same Google + Tripadvisor links above the survey and on the completion page, and the submit response is identical for every payload. A rating <= 2 or a negative comment files `create_followup(kind='guest_feedback', source='j7_feedback')` only after the response is built.
- **WABA billing readiness.** `guests.wa_billing_check` reads Brief's payment-method status (`brief.api.wa_billing_status(cur, hotel_slug) -> {ready: bool}` when Brief adds it; a foundation request, called through `getattr` until then) or the owner attestation (`wa_billing_attested_at`), failing closed; while not ready, WhatsApp sends go to email and `guests.wa_billing_not_ready` is raised.
- **Jobs.** `guests.journey_dispatch` (1 min, SKIP LOCKED batch 200, 10 min reclaim), `guests.journey_plan_reconcile` (04:30), `guests.journey_contact_purge` (03:40), `guests.optout_monitor` (hourly, per-type attributed rates, `report_incident('optout_spike')`), `guests.template_category_check` (05:10, Meta category/quality sync, RED or PAUSED demotes the step), `guests.wa_billing_check` (07:05), `guests.journey_feedback_expire` (02:15).
- **Signals.** `guests.checkout_open_issues`, `guests.templates_pending_approval`, `guests.optout_spike`, `guests.meta_cap_blocks`, `guests.wa_billing_not_ready`, `guests.j8_conversion_verified`.
- **Metrics** (`journeys/metrics.py`; dimensions step, channel, category, language, month): `guests.journey_sends`, `guests.journey_delivery_rate`, `guests.journey_response_rate`, `guests.j5_negative_rate`, `guests.feedback_completion_rate`, `guests.review_link_clicks` (platform), `guests.direct_contact_capture_rate`, `guests.optout_rate`, `guests.journey_cost` (money, hotel currency, from `gst_message_costs` and the UNVERIFIED `gst_wa_rate_card`).
- **J8 verifier.** Counts toward verified profit only when this plan's code was redeemed by the matched guest or the `utm_campaign=gst_j8&utm_content=<plan_id>` link was used; an identity-only match is `influenced`.

## HTTP API (journeys part)

Console routes live under `/hotels/{slug}/guests/journeys/` and need a signed-in member. No console route sends anything: the dispatcher and the dial do. Errors are `{detail, code}`.

| Method and path | Permission | Request | Response |
|---|---|---|---|
| `GET .../journeys/overview?days=30` | `guests.journeys.view` | `days` 1-365 | `{days, funnel: {<step>: {planned, sent, delivered, replied, observed, proposed, skipped: {<reason>: n}, other}}, dial: {<step>: {action_type, level, effective_level, max_level}}, capture: {ota_stays, captured, rate\|null}, optouts: {<step>: n}, cost: {currency, amount}, templates_approved, all_observing}` (`all_observing` drives the empty state "All steps are observing: approve templates to start sending") |
| `GET .../journeys/templates` | `guests.journeys.view` | | `[Template]` (the library is seeded on first read, EN and AR, WhatsApp and email) |
| `POST .../journeys/templates/{id}/approve` | `guests.journeys.manage` | | `Template + {submission}` (owner approval; WhatsApp versions are also submitted through `brief.api.submit_template`, and `submission` is Brief's `{name, language, status, meta_template_id}`, `{error: <code>}` while Brief has no WABA (the version stays owner-approved, email carries the step), or null for email and already-submitted versions); 404 `template_not_found`; 422 `template_lint_failed` |
| `POST .../journeys/templates/{id}/revoke` | `guests.journeys.manage` | | `Template` (approval withdrawn); 404 `template_not_found` |
| `GET .../journeys/offers` | `guests.journeys.view` | | `[Offer]` |
| `POST .../journeys/offers` | `guests.journeys.manage` | `OfferIn = {step: j3\|j8, title_en, title_ar?, price? (decimal, hotel currency), discount_pct? (0-100], code_prefix? ([A-Za-z0-9]{1,8}), valid_from, valid_to}`; titles refuse the em dash and review words | 201 `Offer` (unapproved) |
| `PUT .../journeys/offers/{id}` | `guests.journeys.manage` | `OfferIn` | `Offer` (an edit withdraws the approval); 404 `offer_not_found` |
| `POST .../journeys/offers/{id}/approve` | `guests.journeys.manage` | | `Offer`; 404 `offer_not_found` (also for inactive offers) |
| `DELETE .../journeys/offers/{id}` | `guests.journeys.manage` | | `{ok}` (deactivates; history stays) |
| `GET .../journeys/reservations/{rid}/timeline` | `guests.journeys.view` | | `{reservation_id, plans: [{id, step, seq, status, skip_reason, channel, due_at, valid_until, last_attempt_at, action_id, attempt, error_code, category_at_send, direct_code, plan_version, checkin_time_inferred}], replies: [{id, plan_id, outcome, deterministic_hit, llm_used, summary_en, service_ref, action_id, created_at}], messages: [{id, plan_id, kind, channel, sent_at}], prefs: {arrival_time, requests, occasion, language, submitted_at}\|null, feedback: {overall, went_well, improve, contact_ok, language, followup_ref, submitted_at}\|null}`; guest words masked as transcript for the caller's role |
| `GET .../journeys/settings` | `guests.journeys.view` | | `JourneySettings` |
| `PUT .../journeys/settings` | `guests.journeys.manage` | partial `JourneySettings`, plus `{wa_billing_attested: bool}` for the owner attestation | `JourneySettings`; 400 `unknown_setting`; 422 `invalid_settings` (e.g. a widened marketing window, a non-official review host) |

Registration-card capture (`POST /hotels/{slug}/guests/contacts`) is in the identity table above.

**Public routes** (the data behind the client pages `/g/[token]` and `/g/f/[token]`; no auth, 60 requests per 10 min per IP per route through `ratelimit.hit` (429); the token purpose is enforced, a wrong purpose or unknown token is 404 `link_invalid`, an expired one 410 `link_expired`, a used consent token 410 `link_used`):

| Method and path | Token purpose | Request | Response |
|---|---|---|---|
| `GET /public/journeys/{token}?lang=en\|ar` | any (`prefs`, `consent`, `feedback`) | | `PageConfig` (always 200 for a known token, whatever its state, so the page can say "This link has expired") |
| `POST /public/journeys/{token}/prefs` | `prefs` (J1 page `/g/[token]`) | `{arrival_time?, requests?, occasion?, language?: en\|ar, contact?: {kind: phone\|email, value}, consents?: {transfer_abroad, whatsapp_updates, marketing_whatsapp, marketing_email}, consent_text_versions?: {<purpose>: version}}` | `{ok, contact_saved}`; a contact needs `transfer_abroad` (422 `consent_not_allowed`); on an OTA stay it becomes a `guest_stay` point and any marketing consent is 422 `consent_not_allowed` |
| `POST /public/journeys/{token}/consent` | `consent` (the J5 transfer tick on `/g/[token]`) | `{transfer_abroad: bool, text_version?, language?: en\|ar}` | `{ok, point_created}`; creates the `guest_direct` point from the number that said YES; single use; `transfer_abroad: false` is 422 `consent_not_allowed` |
| `POST /public/journeys/{token}/feedback` | `feedback` (`/g/f/[token]`) | `{overall?: 1-5, went_well?, improve?, contact_ok?, language?: en\|ar}` | `{completion_key: "thanks", review_links: ReviewLinks}`, identical for every payload; stored once per stay |
| `GET /public/journeys/{token}/go/{google\|tripadvisor}` | `feedback` (valid or expired) | | 302 to the hotel's review page; counts one click per day and platform, no identity; 404 `review_link_missing` |

Shapes:

- `PageConfig = {purpose, state: valid\|expired\|used, language, hotel: {slug, name, city}, stay: {check_in, check_out}}` plus, per purpose:
  - `prefs`: `{marketing_boxes: bool (false for OTA stays), consent_texts: {<purpose>: {version, language, body}}, prefs: {arrival_time, requests, occasion, language}\|null}`. OTA stays get only `transfer_abroad` and `whatsapp_updates` texts; direct stays also get `marketing_whatsapp` and `marketing_email` (separate, unticked boxes). Render the tick boxes from `consent_texts` and post the shown `version` back.
  - `consent`: `{consent_texts: {transfer_abroad}}` only.
  - `feedback`: `{submitted: bool, review_links: ReviewLinks}`; render "Prefer to post a public review? Google · Tripadvisor" above the survey, and the same `ReviewLinks` with the same props on the completion page. No incentive text, no rating-dependent copy.
- `ReviewLinks = {google_url\|null, tripadvisor_url\|null}` (settings only; link through `/go/{platform}` to count clicks).
- `Template = {id, step, channel, language, meta_name, version, subject, body, buttons, variables, lint: {ok, errors[]}, category_expected, category_meta, meta_status, quality, approved_at, submitted_at, sendable, dir: ltr\|rtl, category_mismatch}`
- `Offer = {id, hotel_slug, step, title_en, title_ar, price_minor, price (money string)\|null, discount_pct (string)\|null, currency, code_prefix, valid_from, valid_to, approved_by, approved_at, active, created_at}`
- `JourneySettings = {enabled_steps, utility_window, marketing_window (narrow only), j2_min_lead_days (10), j5_offset_h (18), j7_offset_days (2), j8_offsets ([30, 60]), review_links: {google_url, tripadvisor_url}, j7_for_ota_provided_contacts (false), standard_checkin_time, standard_checkout_time, ai_on_unconsented_inbound (false), wa_billing_attested_at, booking_engine_url, planning_info, checkin_bring, directions}`
