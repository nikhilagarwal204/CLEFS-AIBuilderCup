# Clefs growth modules: Guest Journeys, Inquiry Guard, Visibility, Owner P&L pulse, Autonomy dial, Claims registry

> Design spec written during planning on 2026-09-28. It draws on the webinar research (`docs/specs/research/webinar/*.md`), the UAE research (`docs/specs/research/uae/*.md`) and the PLAN module summaries. It was revised after two adversarial reviews: research fidelity, and contract consistency.
>
> Where this spec conflicts with `00-CANON.md`, the Canon wins. Sections 0.4, 0.5 and 7.2 list every contract this spec changes in 01, 02 or 03. For those named items only, this spec wins.

Foundation file paths: where this spec names a foundation file (e.g. core/actions), the implemented layout in server/clefs/CONTRACTS.md wins.

## 0. Scope, decisions and shared extensions

### 0.1 What this spec covers

| Area | Where it lives | Lane (wave) | Migrations / prefix | Main research |
|---|---|---|---|---|
| Autonomy dial and ledger fields (Canon 17) | foundation `core/autonomy.py`, a sibling of the `core/actions.py` module | F-contracts (0a, per `server/clefs/CONTRACTS.md` Part 2) | `0017` (core, inside F-contracts' 0010-0019) | journey-automation.md §4 |
| Shared foundation additions F1-F10 | `core/`, `notify/`, `jobs.py`, `ai.py` (frozen files such as `registry.py`, `settings.py` and `app.py` change only through a FOUNDATION-REQUEST, 7.5) | F-contracts (0a, per `server/clefs/CONTRACTS.md` Part 2) | `0017`-`0019` | |
| Shared domain additions D1-D4 | `core/stays`, `core/tax`, `core/ingest` | F-domain (0b) | 0020-0099 | uae-taxes-fees.md |
| Claims registry, marketing copy, `/google-check` UI (Canon 21-23) | client | H (1) | none | claims-factcheck.md, zz-critique.md §4 |
| Guest journeys J1-J8 | `modules/guests/journeys/` | F2 (1) | 0670-0689 `gst_` | journey-automation.md |
| Inquiry Guard | `modules/inquiries/` | I (2) | 0950-0999 `inq_` | inquiry-crm.md |
| Visibility | `modules/visibility/` | V (2) | 0900-0949 `vis_` | local-seo-google.md, ai-visibility.md |
| Owner P&L pulse | `modules/finance/` | P (2) | 1000-1049 `fin_` | owner-kpis-pnl.md |

Lane C owns Profit Guard's public audit, including its AI-snapshot section, and the OTA Leakage Index (02). This spec defines only the Visibility side of the snapshot (5.9).

### 0.2 Decisions and non-goals

1. **Journeys live in the `guests` module** (Canon 10), in `gst_journey_*` tables numbered 0670-0689. The range 0650-0669 stays with 03's guest identity work. Both halves belong to lane F2.
2. **Quotes are HTML pages only.** No PDF quote is generated.
3. **There is no CRM pipeline UI:** no kanban, no lead scoring and no sequences builder. Inquiries get a list with filters, and the weighted pipeline is a number (PLAN, inquiry-crm.md §6).
4. **Inquiry-to-OTA leak output stays internal** (Canon 19). It is never sent to a guest, never exported, and never used for audiences or public audits.
5. **Visibility uses Gemini only through the Gemini API (Grounding with Google Search) and never scrapes consumer AI apps** (Canon 20). Every AI number is labelled (Gemini and Perplexity as "API simulation", AI Mode as third-party SerpApi data, 5.5), shows its n and confidence interval, and is hidden below the minimum sample.
6. **Map rank is an internal KPI.** There is no heatmap UI and no rank promise.
7. **The headline ROI is "Clefs-verified profit".** GOPPAR appears only as market-indexed context. There are no milestone tiers, and no training on Xero data.
8. **Review requests are identical for every guest.** There is no sentiment routing, no incentive, no pressure on the premises and no velocity target (Google contribution policy, Tripadvisor guidelines, GDT 4.2.3).
9. **Marketing on any channel is sent only in [09:00, 18:00) Asia/Dubai** (uae/zz-critique.md C7). Utility messages have their own window (3.5).
10. **Money is persisted as `BIGINT` minor units plus `CHAR(3)` currency** (Canon 2). The one exception is vendor-cost ledgers. SerpApi, Google Gemini and Meta per-call costs are sub-cent, so they are stored in micro-units (`*_micros BIGINT` plus a currency column). This exception is a pending Canon 2 amendment (0.7). 1 minor unit of USD = 10,000 micros.
11. **Value is credited only when a Clefs action preceded the outcome.** Anything else is recorded as `influenced` and does not count toward verified profit. The rule is to measure first and claim nothing causal (inquiry-crm.md §6, ai-visibility.md §6).
12. **Every missing key degrades to a "not connected" state** with no errors. Kill switches are env flags.
13. **Every number on the marketing site goes through the claims registry** (section 2).

### 0.3 Assumptions (each is labelled where it is used and listed in 7.7)

| # | Assumption | Used in |
|---|---|---|
| A1 | Meta categorises J1, J2, J4 and J5 as UTILITY. This is inferred from Meta's order-management examples; reservations are not named. J7 may be recategorised to MARKETING, so it is budgeted as marketing. | 3.6, 3.10 |
| A2 | **OTA contact lifetimes.** The Booking.com alias and Booking-provided contacts are usable until check-out + 30 d (GDT 2.2.6). The Expedia alias is usable only until check-out ("only valid for a short time after check-out", secondary source). Agoda's alias domain and lifetime are unknown, so Agoda is treated like Expedia. | 3.4, 4.9 |
| A3 | Inquiry business hours are 08:00-22:00 every day. | 4.5 |
| A4 | The WhatsApp AED rate card is seeded from Meta's rate-card CSV. The reported 1,000 free service messages per number is UNVERIFIED, so the allowance defaults to 0. | 3.10 |
| A5 | The stage probability for `replied` is 0.15, interpolated. The research gives only New 10%, Quoted 30%, Negotiation 50% and Payment pending 80%, and calls all of them assumptions. | 4.5 |
| A6 | When room-nights are missing, segment defaults apply: fit 1×2, corporate 1×3, long_stay 1×30, group 10×3, event 5×2. | 4.5 |
| A7 | A direct reservation can verify an inquiry if `booked_at` falls in [inquiry.created_at − 1 d, anchor + 14 d]. The research says "within N days" without giving a value. | 4.9 |
| A8 | Leak matching is limited to 30 days, the Booking.com data window (GDT 2.2.6). | 4.9 |
| A9 | The USALI 12th-edition schedules and lines come from secondary sources; the book itself was not read. | 6.4 |
| A10 | ARN = Σ `occupancy_daily.rooms_available`. Out-of-order rooms stay in the denominator. | 6.1, 6.6 |
| A11 | Quote validity drops to 24 h when any night has `demand_index ≥ 80`. The research only says validity is "shorter during high-demand periods". | 4.6 |
| A12 | **Group terms defaults.** Cutoff is check-in − 30 d and attrition is 80%. The deposit is 25% at signing, then 50% and 75% cumulative as arrival nears. These come from US and secondary sources; Dubai norms are UNVERIFIED. | 4.6 |
| A13 | Places Text Search ordering approximates the ordering in the Maps app. | 5.3 |
| A14 | An iframe widget avoids per-hotel CORS. | 4.3 |
| A15 | **J8 counterfactual.** A guest who redeems a J8 code would otherwise have rebooked through the channel of their previous stay. | 3.7 |

### 0.4 Foundation extensions (F-contracts, wave 0a; frozen in `CONTRACTS.md` Part 2)

F-server-core has closed. Per `server/clefs/CONTRACTS.md`, every item below belongs to F-contracts (its files and migrations 0010-0019), except edits to frozen files (`registry.py`, `settings.py`, `app.py`, `auth/permissions.py`, `requirements*.txt`, `.env.example`), which F-contracts requests with `# FOUNDATION-REQUEST:` and the integrator applies (7.5).

**F1. Autonomy dial.** See section 1.

**F2. Ledger contract amendments.** These amend 01 §E2; see 1.3.

**F3. Jobs.** Add these two schedules next to `every`, `daily_at` and `weekly_at`:
- `monthly_at(day: int, hhmm: str, *, tz="hotel")`
- `nth_weekday_at(n: int, iso_weekday: int, hhmm: str, *, tz="hotel")`

Both catch up within the same local day, like `daily_at`.

**F4. Notify and inbound dispatch.**
- **WhatsApp handler registration.** The signature is `on_whatsapp(audience, *, priority: int = 50, match: Callable[[InboundCtx], bool] | None = None, button_prefix=None, kind=None)`. Handlers run in ascending priority, and the first one that returns `handled=True` wins. The order for `audience='guest'` is frozen:

  | Priority | Handler | Matches |
  |---|---|---|
  | 5 | `guests.optout_consent` | STOP words from any guest sender. YES/نعم only when 3.8 disambiguates it to a journey message |
  | 10 | `guests.journey_replies` | `context.id` is a journey wamid, or the sender hmac matches a point that a journey messaged in the last 72 h |
  | 20 | `inquiries.whatsapp_intake` | A `referral` object is present, or the sender is not a known in-house guest |
  | 30 | `concierge.whatsapp_chat` | A known in-house guest |
  | 90 | `service.whatsapp_fallback` | Anything left |

- **Delivery-status hook.** `on_outbound_status(purpose_prefix: str)` calls the handler with the outbound row, the new status (`sent|delivered|read|failed`) and `error_code`.
- **Email headers.** `send_email(..., headers: dict | None = None)`, used for `List-Unsubscribe`.
- **Idempotency.** `outbound_messages.idempotency_key` stays UNIQUE, so a retry needs a new key. Journeys use `gst:{plan_id}:a{attempt}`.
- **Pacing and backoff.** `core.notify.deliver` owns the 6 s per-recipient spacing and the backoff for 131056 and 130429 (`not_before = now + 6 s × 2^n`), for every WhatsApp sender. Brief's `wa_outbox` (02 §6.2) delegates to it.
- **Inbound email addresses.** One grammar covers all of them: `<hotel_alias>+<purpose>[-<ref>]@in.clefs.world`.
  - The purpose is the token before the first `-`.
  - `inq` maps to `inquiries`, and `reviews` maps to `reviews`.
  - With no purpose, the mail goes to ingest.
  - Reviews' `reviews+<token>@` (03 §4.1) becomes `<alias>+reviews@`.
- **Inbound message fields.** `InboundCtx` carries `referral` (CTWA), `context_id`, `sender_hmac` (F8) and `window_open_until`.
- **Inbound lookups outside the handler.** `inbound_messages` gains `sender_hmac BYTEA` with an index on `(hotel_slug, sender_hmac, received_at)`. `core.notify.wa_window_open_until(cur, hotel_slug, *, sender_hmac) -> datetime | None` returns when the window the sender last opened closes (None when closed). Journeys use it for the "guest wrote first today" cap exception and for 131047 handling; Inquiries use it for `wa_window_expires_at`.
- **Known guest.** A "known in-house guest" (priorities 20 and 30 above) is defined once, in `guests.api.is_known_guest_sender`: the hmac is on a `reservation_contacts` or `gst_contact_points` row of a reservation with status IN_HOUSE, or CONFIRMED with check-in within 1 d.

**F5. `core.serpapi`.**
- `search(engine, params, *, hotel_slug, purpose, max_age=timedelta(hours=20)) -> dict`.
- **Cache key:** sha1 of the engine plus the sorted, normalised params, excluding `api_key` and `no_cache`.
- **Ledger and budget:** calls and cache hits go to a ledger. `SERPAPI_MONTHLY_BUDGET_USD` sets the overall budget, and the purpose `public_check` is capped by `PUBLIC_SERPAPI_MONTHLY_CAP`. When a cap is exhausted, the call raises `BudgetExhausted`.
- **Shared parameter builder:** `google_hotels_area_params(hotel, *, q, check_in, check_out) -> {engine:'google_hotels', q, check_in_date, check_out_date, currency: hotel.currency, gl:'ae', hl:'en', adults:2}`. Demand (lane D) and Visibility must both use it, so identical requests share one billed call.
- **Single billing path.** Sharing works only if every SerpApi call goes through `search`. Demand's `SerpApiGoogleHotels` adapter (02) calls `core.serpapi.search('google_hotels', google_hotels_area_params(...), hotel_slug=..., purpose='demand.comp_rates')` and treats `BudgetExhausted` as a skipped fetch. Reviews' SerpAPI Google Maps sync (03 §4.1) calls it with `purpose='reviews.sync'`. No lane calls SerpApi directly, so `SERPAPI_MONTHLY_BUDGET_USD` is a real global budget (7.5).
- **Tables** (`0018_core_serpapi.sql`):
  - `serpapi_cache(cache_key PK, engine, params JSONB, response JSONB, status, fetched_at, expires_at)`
  - `serpapi_calls(id, hotel_slug NULL, purpose, engine, cache_key, cache_hit, billed, cost_micros BIGINT, cost_currency CHAR(3) DEFAULT 'USD', at)`

**F6. `core.oauth`.**
- **Providers:**
  - `google_business` (scope `business.manage`), shared by Visibility and Reviews
  - `xero` (`accounting.reports.read offline_access`)
  - `qbo` (`com.intuit.quickbooks.accounting`)
  - `zoho`
- **Functions:** `get_access_token(cur, hotel_slug, provider) -> str`, which raises `NotConnected`.
- **Routes:** `GET /hotels/{slug}/oauth/{provider}/start` and `GET /public/oauth/{provider}/callback`. Hotel dependencies read the `slug` path parameter (CONTRACTS §6), so starting a flow uses `require_perm` per provider: `visibility.manage` or `reviews.manage` for Google, and `finance.connect` for the accounting providers. The callback takes the hotel from its `oauth_states` row, never from the query.
- **Mounting.** `clefs.core.oauth` is not in the frozen `CORE_ROUTER_HOOKS`. `# FOUNDATION-REQUEST: add clefs.core.oauth to CORE_ROUTER_HOOKS` (the integrator applies it; 7.5).
- **Tables** (`0019_core_oauth.sql`):
  - `oauth_connections(id, hotel_slug NOT NULL REFERENCES hotels(slug), provider, account_ref, scopes TEXT[], access_token_enc BYTEA, refresh_token_enc BYTEA, expires_at, status, connected_by, connected_at, last_used_at, UNIQUE(hotel_slug, provider))`
  - `oauth_states(state_hash PK, hotel_slug, provider, user_id, next_path, expires_at)`

**F7. Signal cadence.**
- `register_signal_kind(key, *, label, default_owner_role, section, cadence: Literal['daily','weekly','monthly'], icon=None)`.
- `SignalIn` gains `cadence: ... | None`, so one signal can override its kind's default (for example "daily only when L1").
- Brief (lane G) renders by cadence (7.3).

**F8. Privacy and AI helpers.**
- **Contact hmac.** `core.privacy.contact_hmac(hotel_slug, kind, raw) -> bytes` = HMAC-SHA256(`CONTACT_HMAC_PEPPER`, `f"{hotel_slug}:{kind}:{normalized}"`).
  - Phones are normalised to E.164, defaulting to the hotel's country. Emails are trimmed and lowercased.
  - Guests (03 §2.7 profiles and 04 contact points), Inquiries and `core/stays` all hash through this function and nothing else.
- **Sealing.** `seal(text) -> bytes` and `unseal(bytes) -> str` use AES-256-GCM with a version byte, keyed by `PII_ENC_KEY`. `cryptography` goes into the frozen requirements through a FOUNDATION-REQUEST (integrator).
- **Retention registration.** `register_pii(..., where: str | None = None)` allows conditional retention.
- **AI call storage.** Every `ai.*` call takes `store: bool = False`, and the default is never overridden.
- **Raw Responses calls.** `ai.responses_raw(task, *, input, tools, include=(), model=None, hotel_slug=None, timeout_s=15) -> dict` returns the raw Responses JSON. It is logged in `ai_calls` with its cost.

**F9. Key prefixes for jobs, metrics, action types and signal kinds.**
- `registry.py` is frozen and has no `KEY_PREFIXES`. F-contracts defines `KEY_PREFIXES` in its own file, `clefs/core/signals.py`, listing the prefixes each module may use:
  - `guests: ("guests.",)`
  - `inquiries: ("inquiries.", "inq.")`
  - `visibility: ("visibility.", "vis.")`
  - `finance: ("finance.", "fin.")`
- F-contracts also adds the short prefixes that 02 and 03 already use (`pg.`, `tax.`, `dr.`, `am.`, `wa.`, `rv.`, `ops.`) after checking with those lanes.
- The Registries contract test (`tests/foundation/test_module_contracts.py`) reads this map for job, metric, action-type and signal-kind keys only. It is never used for permissions: `register_permissions` keeps accepting only `<module>.` or route-segment prefixes, so every permission in 7.1 uses a full prefix. This lands before Wave 2.

**F10. Stubs and events.**
- Every 7.2 signature ships as a working stub with the default return listed in 7.2 ("Stub returns"). Existing stubs are only extended with keyword arguments that have defaults; they are never broken.
- **Frozen signatures.** `test_frozen_api_signatures` compares exact strings in `FROZEN_API` (`tests/foundation/test_module_contracts.py`). F-contracts updates `FROZEN_API` in the same change that extends the stubs: the three extended signatures (`demand.price_guardrails` gains `room_type_code`, `concierge.kb_facts` gains `language` and `keyed_only`, `profit_guard.record_ota_leak` gains `commission_minor` and `currency`) plus every new 7.2 function (guests, inquiries, visibility, finance, demand, profit_guard, service, concierge, reviews, brief). This lands before Wave 1 lanes start. Lanes never change `api.py` signatures themselves; they leave a FOUNDATION-REQUEST instead.
- The stay-event names and payload (D1) are frozen in `CONTRACTS.md` in wave 0a, so lane F2 can build against them in parallel with F-domain.

### 0.5 Domain extensions (F-domain, wave 0b)

**D1. `core/stays` contacts, check-in times and stay events.**
- **New `reservations` columns:** `checked_in_at TIMESTAMPTZ`, `checked_out_at TIMESTAMPTZ`, `guest_language TEXT`, and `stay_chain_id BIGINT`. The stay chain is the back-to-back chain for the same guest and room with no gap; the TD night count uses the same chain.
- **Contacts table:** `reservation_contacts(id, hotel_slug NOT NULL REFERENCES hotels(slug), reservation_id REFERENCES reservations(id), kind CHECK IN ('phone','email'), value_enc BYTEA, value_hmac BYTEA, provenance CHECK IN ('pms_direct','pms_ota','ota_alias'), alias_domain TEXT, usable_until TIMESTAMPTZ, purge_after TIMESTAMPTZ, import_batch_id, created_at, purged_at, CHECK (purged_at IS NOT NULL OR value_hmac IS NOT NULL))`.
  - Provenance and lifetimes follow 3.4.
  - The PMS import seals the values. `import_rows.raw` is redacted of contact columns once the batch commits.
  - **No plaintext contacts in core.** 02 §2.1's core `guest_contacts` table (plaintext `phone_e164`, `email`) is **not built**. PMS contacts go only to `reservation_contacts` (sealed and hashed). Demand audiences come only from `guests.api.marketing_audience()` (Canon 10). A contract test asserts that no core table has a plaintext phone or email column.
- **Unsealing.** `core.stays.contact_value(cur, reservation_contact_id) -> str | None` unseals one value and writes an audit row.
- **Stay events.**
  - The outbox is `stay_events(id, hotel_slug, reservation_id, event, occurred_at, changes JSONB)`. Consumers register with `@on_stay_event(consumer)`.
  - The job `stays.dispatch_events` runs every minute and keeps a cursor per consumer.
  - Events: `created`, `dates_changed`, `cancelled`, `no_show`, `checked_in`, `checked_out`, `contact_changed`.
  - Payload: `{event, hotel_slug, reservation_id, occurred_at, changes{field:[old,new]}}`.
- **Contact purge.** The job `stays.contact_purge` (daily 03:30) nulls `value_enc` after `purge_after`. For `pms_ota` and `ota_alias` that is check-out + 30 d; `pms_direct` follows retention.
  - For `pms_ota` and `ota_alias` rows it also sets `value_hmac = NULL` and `purged_at`, so no hashed OTA identifier outlives the Booking.com 30-day window (Canon 19, GDT 2.2.6) or can be matched against later data. `guests.journey_contact_purge` does the same for `gst_contact_points` (3.2). A test asserts that no OTA-provenance hmac exists after `purge_after`.

**D2. Direct-booking attribution.**
- `reservations` gains `utm JSONB`, `promo_code TEXT` and `booking_engine_ref TEXT`.
- A new ingest kind, `BOOKING_ENGINE_ATTRIBUTION`, takes a CSV of booking ref, `utm_*`, promo code and landing URL, and matches it to reservations.

**D3. Stay tax composition.** `core.tax.compose_stay(cur, hotel_slug, *, lines: list[StayLine]) -> StayTaxBreakdown{room, sc, mf, vat, td, td_nights_charged, td_units, currency, convention, fee_rules[{id, rule_status}], notices[]}`, where `StayLine{stay_date: date, room_type_code: str | None, rooms: int, rate_minor: int}` and `rate_minor` is the rate per room per night in minor units.
- **Mixed room types.** One call covers every room type of a stay (a group with STD and FAM_2BR rooms is one call). TD units are computed per line from `room_types.bedrooms`. Callers never sum per-type results.
- **From a reservation.** `core.stays.reservation_breakdown(cur, reservation_id) -> StayTaxBreakdown` builds the lines from `reservation_nights` and calls `compose_stay`. It is the only way other lanes build the `breakdown` that `profit_guard.api.expected_commission` takes (7.2).
- **Date-effective fee rules.** Fee rules are resolved per stay date. A rule with status `ANNOUNCED_UNVERIFIED` applies only when the hotel has an override with `apply=true`.
- **Notices.** `notices` carries the banner text whenever an unverified rule intersects the stay.
- **TD units.** TD units = `rooms × (bedrooms if rule.per_bedroom else 1)`, because multi-bedroom units are charged per room (uae-taxes-fees.md §2).
- **TD cap.** TD is capped at 30 consecutive nights per unit, counted from the stay's first night.
- **Rounding.** Each component is rounded once per stay, over all lines.
- **TD line label.** The TD line is labelled "Tourism Dirham fee", not a tax (uae-taxes-fees.md §2).

**D4. New ingest kinds.** `PL_STATEMENT`, `TRIAL_BALANCE` and `BOOKING_ENGINE_ATTRIBUTION`.
- **Commit handlers.** Core never imports modules (CONTRACTS §1), so `core/ingest` adds `register_commit_handler(kind: str, fn: Callable[[cur, batch: dict, rows: list[dict]], None])`. Finance registers the handler for `PL_STATEMENT` and `TRIAL_BALANCE` at import; the commit step calls it inside the commit transaction. `BOOKING_ENGINE_ATTRIBUTION` is committed by core itself (D2).
- **Canonical rows** (produced by ingest's mapping step; amounts in minor units):
  - `PL_STATEMENT`: `{code?, name, group_raw, type_raw, section, period_month, amount_minor}`, with income positive and expenses negative. A file with several month columns becomes one row per account per month column; ingest does the split.
  - `TRIAL_BALANCE`: `{code?, name, group_raw?, type_raw?, debit_minor, credit_minor}` for the `period_month` given at upload.
- Finance's handler creates one `fin_import_batches` row per `period_month` (6.3).

### 0.6 Module layout and conventions

- **Server layout.** Each module lives in `server/clefs/modules/<module>/` with:
  - `module.py`, `router.py`, `api.py`, `service.py`, `jobs.py`, `metrics.py`, `actions.py`, `settings.py`
  - `migrations/NNNN_<module>_<desc>.sql`
  - any sub-packages named per section below
- **Imports.** A module imports only foundation code and other modules' `api.py`.
- **Keys and tenancy.** Keys follow F9. Every tenant table has `hotel_slug TEXT NOT NULL REFERENCES hotels(slug)` unless it is marked "intentionally global".
- **Routes.**
  - console: `/hotels/{slug}/<module>/...`
  - public: `/public/...`
  - webhooks: `/webhooks/...`
  - client console: `client/app/app/[hotelSlug]/(console)/<module>/`
- **Style.** No U+2014 anywhere. `# ─── Section ───` banners. Python 3.12 syntax.

### 0.7 Pending Canon amendments

The Canon wins over this spec, so the items below are **not in force** until the integrator applies them to `00-CANON.md` (and F-contracts mirrors the ledger items in `CONTRACTS.md` Part 2). Each names the lanes that must not merge before it lands.

| # | Canon item | Amendment | Needed before |
|---|---|---|---|
| K1 | 7 (Actions) | Side states add `observed` (terminal; the dial logged the action and nothing ran, 1.1), `failed` (terminal; the executor returned `ok=false` after the task's retries) and `expired` (terminal; still `proposed` or `approved` at `actions.expires_at`, 1.3) | F-contracts freezes the `actions.status` CHECK (wave 0a) |
| K2 | OTA rules | "Booking.com: commission on room + service charge + VAT (GDT v2601 cl. 2.3.2(ii); uae/zz-critique.md C1; 02 §3.2). MF and TD are excluded only when configured as charges." If K2 is not applied, the 4.9 goldens become 127.06 gross, 114.45 net and a leak of 166.77 | Lanes C and I merge |
| K3 | 2 (Money) | Vendor-cost ledgers (SerpApi, Google Gemini, Perplexity, Meta per-call costs) are stored in micro-units, `*_micros BIGINT` plus currency (0.2 #10) | F-contracts ships `0018_core_serpapi.sql` |
| K4 | 14 (Client routes) | Add `/g/[token]`, `/g/f/[token]`, `/q/[token]`, `/w/inquiry/[slug]` and `/google-check/r/[token]` (7.9) | Lanes F2, H and I merge |
| K5 | 21 (Claims) | Add the claim ids `google_third_party_rates_unavailable_2026_09_30`, `triptease_free_booking_links_unaffected` and `uae_ota_leakage_index` (2.1) | Lane H merges |

---

## 1. Autonomy dial (foundation, `server/clefs/core/autonomy.py`)

This implements Canon 17 and the Canon's Autonomy dial section. There is one dial per (hotel, action type), for every registered action type, not only messaging (journey-automation.md §4). `core/actions` is a module (`clefs/core/actions.py`), not a package: the dial lives in the sibling `clefs/core/autonomy.py` (or an autonomy section of `actions.py`), and its routes hang on the `clefs.core.actions` router, which `CORE_ROUTER_HOOKS` already mounts.

### 1.1 Levels and resolution

| Level | Meaning | Effect in `create_action` |
|---|---|---|
| `off` | Feature switched off | Writes nothing. Returns `{status:'suppressed', reason:'dial_off'}` |
| `observe` | Log only | Writes the action with status `observed` and never executes it. `observed` is a terminal side state, a pending Canon 7 amendment (0.7 K1) |
| `suggest` | A draft for a person | `proposed`, `surface='console'`. Not pushed to the brief |
| `approve` | One tap | `proposed`, `surface='brief'` |
| `auto_within_limits` | Executes when limits pass | System-approves and enqueues when `limit_checker` passes. Otherwise `proposed` with `limit_reasons` |
| `auto` | Executes | System-approves and enqueues |

`resolve_level(cur, hotel_slug, action_type, params) -> Resolution{configured, effective, reasons, policy_version}` works as follows:
1. `configured` is the hotel's `action_autonomy` row, or the type's `default_level` when there is none.
2. `effective` is the lowest of three levels, in level order:
   - `configured`
   - `ActionType.max_level` (the hard ceiling)
   - `ActionType.gate(ctx).max_level`, the per-action precondition (for example, "template not approved" caps a send at `observe`).
3. For `auto_within_limits`, `limit_checker(ctx, limits)` runs. If it fails, the level drops to `approve` and the reasons are recorded.
4. A system approval is recorded as a decision with `decided_via='dial'` and actor `SYSTEM`.
5. `core.actions.execute` resolves the level again before executing.
   - If the effective level has fallen below the level that approved the action, and no human has decided it, the action goes back to `proposed`.
   - Execution stamps `dial_level_at_execution` and `policy_version`.

**Who may decide.** `POST /hotels/{slug}/actions/{id}/decide` requires only an authenticated member of the hotel, or a magic session with purpose `actions` scoped to that hotel. It does **not** gate on `core.actions.decide` (owner and gm only), or revenue users could never approve quotes. `decide()` then checks `ActionType.decide_perm` (default `core.actions.decide`) against the **membership role's full permission set**, not the magic-filtered set (magic sessions hold only `brief.`, `core.signals.view` and `core.actions.` permissions, CONTRACTS §6). For a magic session this applies only to actions the brief surfaced to that user; anything else returns 403. The decision is audited with `via='magic'`. This is how, for example, a revenue user approves a group quote (`inquiries.approve`) with one tap from the mobile brief. F-contracts mirrors this rule in the Actions section of `CONTRACTS.md` Part 2.

### 1.2 DDL (reserved `0017_core_action_autonomy.sql`, F-contracts)

F-contracts owns 0010-0019 (CONTRACTS §4) and reserves 0017-0019 inside that range for the 04 additions (autonomy, SerpApi, OAuth). It may instead fold these tables into its earlier files (its in-progress `0011_core_signals_actions.sql` already carries `action_autonomy` and `action_autonomy_events`); the implemented layout wins, as long as the columns below exist.

```sql
CREATE TABLE action_autonomy (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), action_type TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('off','observe','suggest','approve','auto_within_limits','auto')),
  limits JSONB NOT NULL DEFAULT '{}', policy_version INT NOT NULL DEFAULT 1, reason TEXT,
  updated_by BIGINT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (hotel_slug, action_type));
CREATE TABLE action_autonomy_events (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  action_type TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('seed','set','promotion_suggested','promotion_accepted','promotion_dismissed','demoted')),
  from_level TEXT, to_level TEXT,
  trigger TEXT CHECK (trigger IN ('guest_complaint','optout_spike','meta_quality','eval_regression')),
  policy_version INT, evidence JSONB NOT NULL DEFAULT '[]', actor_type TEXT, actor_id TEXT,
  status TEXT CHECK (status IN ('open','accepted','dismissed','superseded')),   -- promotion_suggested rows only
  resolves_event_id BIGINT REFERENCES action_autonomy_events(id),              -- accepted/dismissed rows point at their suggestion
  at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ON action_autonomy_events (hotel_slug, action_type, at DESC);
CREATE UNIQUE INDEX ON action_autonomy_events (hotel_slug, action_type) WHERE kind = 'promotion_suggested' AND status = 'open';
```

### 1.3 Ledger contract (amends 01 §E2; F-contracts creates `actions` like this from the start)

**Columns on `actions`:**
- **Money** (replacing `NUMERIC`, Canon 2):
  - `expected_minor BIGINT`
  - `outcome_minor BIGINT`
  - `currency CHAR(3)`
- **Attribution:**
  - `counts_toward_verified_profit BOOL NOT NULL DEFAULT FALSE`
  - `attribution TEXT CHECK (attribution IN ('caused','influenced','observed'))`
- **Dial:**
  - `dial_level_at_execution TEXT`
  - `policy_version INT`
  - `surface TEXT`
  - `limit_reasons TEXT[]`
  - `edited BOOL NOT NULL DEFAULT FALSE` (approved with a `params_patch`)
- **Execution:**
  - `template_id TEXT` (`meta_name@version`)
  - `template_category TEXT`
  - `consent_basis TEXT`
  - `reversal_handle JSONB`
- **Verification schedule:**
  - `verify_after`
  - `next_verify_at TIMESTAMPTZ`
  - `verify_until TIMESTAMPTZ`
  - `reversed_at TIMESTAMPTZ`
  - `reversal_reason TEXT`
  - `expires_at TIMESTAMPTZ` (an action still `proposed` or `approved` at this time becomes `expired`)
- **Status** follows Canon 7, plus the terminal side states `observed`, `failed` and `expired` (pending Canon amendment 0.7 K1).
  - `failed`: `ExecResult.ok=false` after the `core.actions.execute` task's retries. The error is kept in `execution_error`.
  - `expired`: the job `core.actions.expire` (`every(minutes=5)`) moves `proposed` and `approved` rows whose `expires_at` has passed. Journeys set `expires_at = plan.valid_until`; inquiries set it to the quote's `valid_until`.
- **`decided_via`** is `console|whatsapp|email|api|system|dial`. `'dial'` marks a system approval by the dial (1.1).

```python
Level = Literal["off","observe","suggest","approve","auto_within_limits","auto"]
@dataclass(frozen=True)
class ActionType:                                   # 01 §E2 fields, plus:
    default_level: Level = "approve"
    max_level: Level = "auto"                       # hard ceiling; a hotel setting can never exceed it
    limits_model: type[BaseModel] | None = None     # JSON-schema'd editor in the console
    limit_checker: Callable[[ActionCtx, BaseModel | None], LimitCheck] | None = None
    gate: Callable[[ActionCtx], Gate] | None = None # per-action precondition (template approved, offer active)
    level_check: Callable[[Any, str, Level], list[str]] | None = None  # (cur, hotel_slug, target level) -> reasons; non-empty = 409 on PUT/accept
    is_looser: Callable[[BaseModel, BaseModel], bool] | None = None    # (old, new limits) -> True when new loosens any bound
    decide_perm: str = "core.actions.decide"
    counts_toward_verified_profit: bool = False     # default, a verifier may override per outcome
    verify_every: timedelta | None = None; verify_until: timedelta | None = None
    reversible: bool = False
class LimitCheck(BaseModel): ok: bool; reasons: list[str] = []
class Gate(BaseModel): max_level: Level; reasons: list[str] = []
class ExecResult(BaseModel):                        # executors return this (01 said dict)
    ok: bool; data: dict = {}; template_id: str | None = None
    template_category: Literal["UTILITY","MARKETING"] | None = None
    consent_basis: str | None = None; reversal_handle: dict   # {kind:'none', reason} | {kind:'gbp_patch', field, previous} | ...
class VerifyResult(BaseModel):
    status: Literal["verified","not_verified","pending"]
    outcome_minor: int | None = None; currency: str | None = None
    counts_toward_verified_profit: bool | None = None          # None = the ActionType default
    attribution: Literal["caused","influenced","observed"] = "caused"
    evidence: list[Evidence] = []; confidence: float = 1.0
@dataclass(frozen=True)
class ActionCtx:                                    # passed to executors, verifiers, gates and limit checkers
    cur: Any; hotel: Hotel; action_id: int | None   # None while create_action evaluates the gate
    type: str; params: dict; actor: Principal
SYSTEM = Principal(kind="service", label="system", ...)   # exported by core.actions; the dial's actor
# Amended 01 §E2 signatures (money in minor units, Canon 2):
def create_action(cur, hotel_slug, *, type, title, params, signal_id=None, expected_minor: int | None = None,
                  currency=None, owner_role=None, due_at=None, expires_at=None,
                  verify_until: datetime | None = None,   # absolute; overrides ActionType.verify_until (4.9)
                  actor: Principal) -> dict      # {id, status, level, surface} | {status: 'suppressed', reason}
def decide(cur, action_id: int, decision: Literal["approve","adjust","assign","dispute","remind","ignore"], *,
           actor: Principal, via: str, params_patch: dict | None = None,
           assignee_user_id: int | None = None, note: str | None = None) -> dict   # 'ignore' moves the action to 'rejected'
def record_verification(cur, action_id: int, *, result: VerifyResult) -> dict
def reverse_verification(cur, action_id: int, *, reason: str, evidence: list[Evidence]) -> dict
def report_incident(cur, hotel_slug: str, *, kind: Literal['guest_complaint','optout_spike','meta_quality','eval_regression'],
                    action_types: list[str], evidence: list[Evidence]) -> list[dict]
def set_level(cur, hotel_slug, action_type, *, level: Level, limits: dict | None, actor, reason: str | None) -> dict
```

- **Replaced 01 forms.** 01's `expected_value: Decimal` and `record_verification(..., verified: bool, outcome_value: Decimal, ...)` are replaced by the forms above, so there is one money convention (minor units). F-contracts mirrors `ActionCtx`, `SYSTEM` and these signatures in `CONTRACTS.md` Part 2.
- **Verification job.** `core.actions.verify` runs hourly and calls the verifier for `done` actions whose `next_verify_at <= now`.
  - `pending` moves `next_verify_at` forward by `verify_every` until `verify_until` is reached.
  - Then the action becomes `not_verified`.
- **Reversals.** `reverse_verification` handles outcomes that unwind later, such as a refund or a PMS cancellation.
  - It writes an `action_events` row, `verification_reversed`, carrying the old outcome.
  - It sets `outcome_minor = 0` and stamps `reversed_at`.
  - The status stays `verified`, which is terminal in Canon 7.

### 1.4 Defaults and ceilings

Every lane registers these values on its `ActionType`. There is no `action_autonomy` row per hotel by default: `resolve_level` falls back to `ActionType.default_level`, so types registered later and legacy hotels need no seeding. The first `set_level` or `report_incident` for a type writes the row, and an `action_autonomy_events(kind='seed')` row records the default that was in force.

| Action type | Default | Ceiling | Limits / gate |
|---|---|---|---|
| `guests.journey_j1_confirm`, `_j2_planning`, `_j4_checkin`, `_j5_daycheck`, `_j7_feedback` | auto | auto | Gate: at least one channel eligible for this guest has an approved template (3.4, 3.5); otherwise `observe`. Journey guards (3.5) enforce caps and windows |
| `guests.journey_j3_offer`, `guests.journey_j8_rebook` | auto_within_limits | auto_within_limits | Gate: an approved, active offer (the "campaign"). Limits `{max_sends_per_day, max_offer_price_minor}` (the only per-day send cap; offers carry none) |
| `guests.checkout_recovery`, `guests.offer_accept` (human tasks) | approve | approve | |
| `inquiries.ack` (fixed text, no AI) | auto | auto | |
| `inquiries.respond` (AI draft) | suggest | approve | |
| `inquiries.send_quote`, `inquiries.send_payment_link` | approve | auto_within_limits | 4.7 |
| `inquiries.follow_up` | approve | auto_within_limits | `{max_followups: 2}` |
| `inquiries.direct_booking` (system record with a no-op executor, 4.9), `inquiries.create_pms_reservation` (task) | auto / approve | auto / approve | |
| `visibility.gbp_utm_patch` | approve | auto | |
| `visibility.lodging_sync` | approve | auto_within_limits | `{fields: allowlist}` |
| `visibility.parity_fix`, `.ota_listing_fix`, `.site_fact_fix` (tasks) | approve | approve | |
| `visibility.add_kb_faq`, `finance.investigate_anomaly` | suggest | approve | |
| `reviews.reply_positive` (4-5★) | suggest | auto | Gate: `max_level='approve'` unless the review's source has `auto_reply_consent=true` (who and when logged, 03 §4.1) **and** the review is 4-5★ with no negative theme and severity no higher than low. `level_check`: a PUT or promotion to `auto_within_limits` or `auto` returns 409 `auto_reply_consent_required` until at least one Google source has that consent recorded (GBP API policy: no automated replies without the user's "prior specific and express consent", privacy-reviews.md B1, uae/zz-critique.md) |
| `reviews.reply_negative` (1-3★) | approve | approve | |
| `service.recovery_gesture` | suggest | auto_within_limits | `{max_minor, once_per_stay: true}`. `max_minor` defaults to 0, so it is never automatic until the owner sets AED X |
| Refunds, folio, rate and OTA changes (`pg.*`, `dr.rate_change`, `dr.close_plans`) | approve | approve | Never automatic |

### 1.5 Promotion and demotion

- **Promotion suggestions.** The job `core.autonomy_review` runs daily at 04:10.
  - For each (hotel, type) below its ceiling, it counts human `approve`, `adjust` and `ignore` decisions over the trailing 30 d (`ignore` moves an action to `rejected`). `assign`, `remind` and `dispute` are excluded.
  - At 50 or more decisions with at least 95% approved unedited (`approve` with `edited=false`), it upserts the signal `core.autonomy_promotion` (section `compliance`, cadence `weekly`) and writes a `promotion_suggested` event with `status='open'`.
  - **Target level.** `to_level` is the next level up the order `off < observe < suggest < approve < auto_within_limits < auto`, skipping `auto_within_limits` when the type has no `limits_model`, and never above the ceiling.
  - **Lifecycle.** There is at most one open suggestion per (hotel, type). The job does not re-suggest while one is open, or for 30 d after a dismissal. Accept and dismiss write `promotion_accepted` or `promotion_dismissed` with `resolves_event_id` pointing at the suggestion, and set its status. A manual `set_level` marks an open suggestion `superseded`.
  - **Permission to accept** follows `to_level`: `core.autonomy.manage` up to `approve`, `core.autonomy.raise_auto` above it. A type's `level_check` can still refuse with 409 (1.4). A suggestion is never applied automatically.
- **Automatic demotion.** `report_incident` lowers each listed type to `min(current, 'approve')`, increments `policy_version`, writes a `demoted` event, and upserts `core.autonomy_demoted` (compliance, weekly). The triggers are:
  - `guest_complaint`: a complaint about messaging (3.8).
  - `optout_spike`: from `guests.optout_monitor`. Rates are per action type: opt-outs attributed to that type / delivered sends of that type. It fires when the 7 d rate is at least 5% with at least 3 opt-outs, or when the 7 d rate is at least 2× the trailing 28 d rate. Attribution and the types passed are defined in 3.10.
  - `meta_quality`: Brief's template sync reports RED quality or PAUSED on any template the type uses.
  - `eval_regression`: from Trust.
- **Brief.** Both kinds appear in the brief's weekly autonomy block (7.3).

### 1.6 API, console, tests

- **Routes:**
  - `GET /hotels/{slug}/autonomy` (`core.autonomy.view`)
  - `PUT /hotels/{slug}/autonomy/{action_type}` (`core.autonomy.manage`). Setting `auto_within_limits` or `auto` requires `core.autonomy.raise_auto`. A limits change that loosens any bound (`ActionType.is_looser(old, new)`, for example a higher `max_total_minor`) also requires `core.autonomy.raise_auto`; tightening needs only `manage`. A non-empty `level_check` returns 409 with its reason code.
  - `POST /hotels/{slug}/autonomy/suggestions/{event_id}/accept|dismiss` (permission per 1.5)
- **Console** (`(console)/settings/autonomy/`, lane F-client; if F-client has already closed, the integrator reassigns it to lane G):
  - One row per action type showing the level select (options above the ceiling are disabled), the limits form generated from `limits_model`, 30-day decision stats and history.
  - Open suggestions appear with Accept and Dismiss.
- **Tests** (`tests/foundation/test_autonomy.py`):
  - Each level's effect.
  - The ceiling wins over a hand-set `auto` (a refund is never executed automatically).
  - A gate caps the level at `observe`.
  - Limits fall back to `approve`.
  - Execution re-resolves the level after a mid-flight demotion.
  - A promotion is suggested at 50/95% and not at 49 decisions or 94%. It targets the next level (skipping `auto_within_limits` without a `limits_model`), is not re-suggested while open or within 30 d of a dismissal, and accepting to `approve` needs only `manage`.
  - Loosening limits without `raise_auto` is refused; tightening passes.
  - `reviews.reply_positive`: without `auto_reply_consent` the effective level is capped at `approve`, and a PUT to `auto` returns 409 `auto_reply_consent_required`; a 3★ review or one with a negative theme is capped even with consent.
  - Each demotion trigger works.
  - `policy_version` is stamped at execution.
  - `observe` writes no execution.
  - An executor error ends in `failed`; a `proposed` action past `expires_at` ends in `expired`.
  - A revenue user approves a group quote from a magic-link brief session; the same session gets 403 on an action the brief did not surface.
  - `reverse_verification` nets the outcome to zero.

---

## 2. Claims registry and marketing (lane H)

### 2.1 Registry (`client/app/lib/claims.ts`)

**Fields:**
- `claim_id`, `exact_quote`, `value`, `unit`, `geography`, `population`, `period`, `publisher`
- `source_type` (`primary|secondary`), `url`, `retrieved_at`, `review_by` (expiry)
- `status` (`VERIFIED|SECONDARY|UNVERIFIED|BANNED|PROPRIETARY`)
- `allowed_surfaces[]` (`home|faq|solution_pages|google_check|free_ota_audit|pilot|seo_meta`)
- `label` (the caption shown with the number)
- `flag?` (Canon 16 feature flags such as `claims.guest_memory`)

**Seeded allowed claims (Canon 21):**
- `cloudbeds_2025_ota_direct_share`: 63.4% OTA / 36.6% direct. Label "independent hotels globally, 2025 data". Cite the report page.
- `cloudbeds_2025_cancellations`: label "OTA 21.8% vs direct 10.6% cancellation rate, independent hotels globally, 2025".
- `jll_uae_revpar_ytd_jun_2026`: −31.8%. `review_by` 2026-12-31.
- `skyscanner_uae_ai_confidence_2026`: 81%. Label as confidence, not usage.
- `microsoft_uae_genai_usage_q2_2026`: 73.3%. Label "UAE population aged 15-64 using generative AI, Q2 2026" (ai-visibility.md). `review_by` 2027-03-31.
- `booking_ai_used_2025`: 67% "have already used AI in some aspect of travel". Label "global survey, 33 markets, 2025".
- `hbr_speed_to_lead_2011`: `exact_quote` "about 7x more likely to qualify" the lead when replying within 1 hour (inquiry-crm.md wording). Label "general sales research, not hotel data; 2,241 US firms, 2011".
- `det_2025_inventory`: 154,264 rooms, 827 establishments.
- `google_third_party_rates_unavailable_2026_09_30`: Google help 14310302 (primary). `review_by` 2026-10-31; after 2026-09-30 the copy is reworded to the past tense ("have been unavailable since 30 Sep 2026").
- `triptease_free_booking_links_unaffected`: secondary source.
- `uae_ota_leakage_index`: PROPRIETARY. Its data comes from lane C, and it stays hidden until n is at least 30.
- The last three ids are not in Canon 21 yet (pending amendment 0.7 K5); lane H does not merge before it lands.

**Banned (status BANNED; the linter matches these phrases):**
- 8.6M workforce gap
- the 46/29/14/11 discovery split
- "89% expect AI for bookings"
- repeat guests "20-40%"
- "#41 to #1"
- "350 reviews"
- missed-call statistics ($127 per call, 85% never retry, 40-60% unanswered)
- labour-shortage framing
- GOPPAR "+10/+25/+50%" tiers
- the TBRC AI market size
- the legacy "60% of front desk calls", "80% of routine calls" and "200-400 calls"

**Rendering:** `<Claim id="..." />` renders the value, the label and a footnote link. `claimText(id)` returns the same as a string.

### 2.2 Linter (`client/scripts/check-claims.mjs`)

- **Parser.** It uses the TypeScript compiler API (`typescript`, already a devDependency).
- **Scope.** Every `.ts` and `.tsx` file under `client/app/` except `app/app/**`, `app/stay/**`, `app/device/**`, `app/api/**`, `app/g/**`, `app/q/**`, `app/w/**` and test files.
- **Visible text is:**
  - JSX text
  - literals in JSX attributes `title|alt|aria-label|placeholder|content|label`
  - literals under object keys `q|a|title|description|body|label|text|caption|eyebrow|sub` in `lib/faq.ts`, `lib/seo.ts` and page data arrays
- **Skipped:**
  - `<style>` contents
  - literals assigned to identifiers matching `/(styles?|css)$/i` (for example `brandStyles`)
  - `className` and `style` props
  - `rootMargin` and `threshold` options
  - SVG `d`, `viewBox` and `transform`
  - URLs
  
  This is why `MarketingHomepage.tsx`'s `50%`/`100%` CSS and `rootMargin: "0px 0px -12% 0px"` pass.
- **Flagged:** a number followed by `%` or `pp`, adjacent to `AED|Dh|$|USD|₹`, or used as a multiplier (`7x`, `7×`). It passes only when one of these holds:
  - the text is inside `<Claim>`
  - it comes from `claimText()`
  - its line carries `// claim: <id>`
- **Failures:**
  - an unknown `claim_id`
  - status BANNED or UNVERIFIED
  - a surface not in `allowed_surfaces`
  - `review_by` has passed (a warning appears 30 days before)
  - any banned phrase
  - U+2014
  - "No calls", "any language", "no bugs", "replaces staff", "independent hotels only", "rank #1", "guaranteed"
  - outcome promises, matched case-insensitively whether or not a number is present (the research rejects causal AI uplift, rank and review-velocity promises, and GOPPAR growth promises):
    - `/(boost|improve|increase|raise|climb)\s+(your\s+)?(google\s+)?(rank|ranking|position|visibility)/i`
    - `/(recommended|ranked)\s+by\s+(ai|gemini|perplexity)/i`
    - `/more reviews/i`
    - `/uplift/i`
    - `/increase (direct )?bookings by/i`
    - `/GOPPAR\s*(growth|\+)/i`
    - `/top of (google|maps|gemini)/i`
    - Arabic equivalents: `/(رفع|تحسين|زيادة)\s+(ترتيب|ظهور)/`, `/(زيادة|مضاعفة)\s+الحجوزات/`, `/المزيد من التقييمات/`, `/(الأول|الأولى|أعلى)\s+(على|في)\s+(جوجل|خرائط)/`
- **Calculators.** Calculator outputs are computed from the visitor's own inputs and are not literals, so they are not flagged. A bare "AED" label passes.
- **Run command:** `node scripts/check-claims.mjs`. `package.json` is frozen, so it is invoked directly, as in the PLAN client checks.

### 2.3 Copy changes (Canon 22)

- The eyebrow is "For hotels in the UAE". This overrides 03 §6's "For independent hotels in the UAE".
- `BRAND.tagline` is "Profit and service control for hotels".
- No copy says "independent hotels only".
- Every numeric statement on the homepage and in the FAQ moves onto `<Claim>` or is removed. The `lib/faq.ts` CLAIM RULE block is kept.

### 2.4 `/google-check` page (UI; lane V builds the server side in 5.9)

- **Files:** `client/app/google-check/page.tsx` (indexable) and `client/app/google-check/r/[token]/page.tsx` (noindex).
- **Form:** hotel name and city, plus two separate required ticks:
  - "Process this check" (Clefs)
  - "My request may be processed outside the UAE by Clefs' sub-processors (list)"
- **Flow:** a candidate picker when there are 2-5 matches, then the teaser, then the lead-unlock form. The unlock form has separate ticks: "Clefs may contact me about this check by email" and "Clefs may contact me on WhatsApp". Each tick names Clefs and the channel.
- **Result note** (claims `google_third_party_rates_unavailable_2026_09_30` and `triptease_free_booking_links_unaffected`): "Google third-party rates for Hotel Ads and PMax Travel Goals are unavailable after 30 Sep 2026 (Google); free booking links are not affected (Triptease)."
- **Vendor disclosure** on `/google-check/r/[token]`, rendered above the parity table and the rank whenever either is shown: "Google Hotels data via SerpApi, a third-party service; prices indicative; checked {date} for stays {dates}", plus the price-accuracy note from 5.2 (Google policy 6064419). The rank is labelled "organic position on these dates, not a ranking guarantee". SerpApi is a Canon 20 vendor risk (Google v. SerpApi was dismissed; a narrower re-filing is reported but UNVERIFIED, local-seo-google.md).
- **API.** The request and response shapes are in 5.9; lane H builds against mocks of them.
- **Degradation.** Until lane V lands, the page handles 404 and 503 with "Check unavailable, try again later". It also handles 503 `budget_exhausted`, 429 `rate_limited` ("You have reached today's limit, try again tomorrow") and `status:'no_match'` ("We could not find this hotel on Google Hotels; check the name and city").

### 2.5 Tests

`client/scripts/__tests__/check-claims.test.mjs` uses `node:test` with fixtures in `client/scripts/__fixtures__/claims/`:
- CSS percentages and `rootMargin` pass.
- An untagged "63.4%" fails, and the tagged version passes.
- A banned phrase fails.
- An expired claim fails.
- A claim on the wrong surface fails.
- U+2014 fails.
- A calculator label passes.
- Each outcome-promise pattern from 2.2 (EN and AR) fails on a fixture without a number.
- A UI test that the vendor disclosure renders whenever the parity table or the rank is shown on `/google-check/r/[token]`, and that the 429 and `no_match` states render.

---

## 3. Guest journeys (`modules/guests/journeys/`, lane F2)

This is a fixed library with no campaign builder (journey-automation.md §3, §5). It knows each booking's source and each contact's consent. Every send goes through the ledger and is governed by the dial.

### 3.1 Step library

| Step | Planned on | Due (hotel tz) | Valid until | Category | Not sent when |
|---|---|---|---|---|---|
| J1 Confirm + preferences | `created` | Immediately. Exempt from the utility window within 2 h of booking | check_in − 1 d 21:00 | UTILITY | Channel `WALKIN`; booked less than 24 h before check-in (J4 covers it); a later reservation in a stay chain |
| J2 Planning info | `created` | check_in − 7 d 10:00 | check_in − 2 d | UTILITY | Lead time ≤ `j2_min_lead_days` (10) |
| J3 Pre-arrival offer | `created` | check_in − 3 d 11:00 | check_in − 1 d 00:00 | MARKETING | No eligible marketing contact; no approved J3 offer; segment GROUP, CORPORATE, MICE or CREW |
| J4 Check-in instructions | `created` | check_in − 1 d 16:00, or 09:00 on arrival day if booked after that | check_in at `standard_checkin_time` | UTILITY | |
| J5 Day-1 check | `checked_in` | `checked_in_at` + `j5_offset_h` (18), moved into the utility window | check-out day 12:00 | UTILITY | Already checked out |
| J6_pre gate | `created` | Departure day, `standard_checkout_time` − 3 h | Departure day 23:59 | internal | |
| J6_post gate | `checked_out` | At the event | +24 h | internal | |
| J7 Feedback | `checked_out` | `checked_out_at` + `j7_offset_days` (2), 11:00 | +7 d | UTILITY, budgeted as marketing (A1) | Contact lifetime ended (`alias_expired`); OTA-provided contacts while `j7_for_ota_provided_contacts=false` |
| J8 Rebooking | `checked_out` | check_out + 30 d and + 60 d, 11:00 | +7 d each | MARKETING | No eligible marketing contact; no approved J8 offer |

Notes:
- J8 supersedes 03 §2.7's `guests.rebook_offers` job, which is not built. The direct codes come from 03's `gst_direct_codes`, amended in 3.2 so a code needs no guest profile (J8 recipients may have no `profile_memory` consent) and is unique per plan (3.7).
- Multi-room reservations get one plan set per reservation.

### 3.2 DDL (0670-0689)

```sql
-- 0670_guests_contact_points.sql
CREATE TABLE gst_contact_points (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  reservation_id BIGINT REFERENCES reservations(id), kind TEXT NOT NULL CHECK (kind IN ('phone','email')),
  provenance TEXT NOT NULL CHECK (provenance IN ('pms_direct','pms_ota','ota_alias','guest_direct','guest_stay')),
  reservation_contact_id BIGINT,          -- core/stays row for pms_* and ota_alias; the value lives there
  value_enc BYTEA,                        -- only for guest_direct and guest_stay points (captured by this module)
  value_hmac BYTEA,                       -- nulled at purge for pms_ota, ota_alias and guest_stay (D1)
  country_prefix TEXT, alias_domain TEXT,
  capture_source TEXT NOT NULL CHECK (capture_source IN ('pms','j1_form','concierge','registration_card','j5_reply','inquiry','console')),
  evidence JSONB NOT NULL DEFAULT '{}', usable_until TIMESTAMPTZ,
  last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- updated on every send to, or inbound from, the point
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suppressed','purged')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), purged_at TIMESTAMPTZ,
  CHECK (status = 'purged' OR value_hmac IS NOT NULL));
CREATE UNIQUE INDEX ON gst_contact_points (hotel_slug, COALESCE(reservation_id, 0), kind, value_hmac, provenance)
  WHERE value_hmac IS NOT NULL;           -- also dedupes guest_direct points with no reservation; purged rows never collide
CREATE INDEX ON gst_contact_points (hotel_slug, value_hmac);
CREATE TABLE gst_suppressions (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), value_hmac BYTEA NOT NULL,
  scope TEXT NOT NULL CHECK (scope IN ('all','marketing')),
  reason TEXT NOT NULL CHECK (reason IN ('stop','manual','wrong_number','complaint_about_messaging','dncr','bounce')),
  source TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (hotel_slug, value_hmac, scope));
-- 0671_guests_journey_templates_offers.sql
CREATE TABLE gst_journey_templates (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  step TEXT NOT NULL CHECK (step IN ('j1','j2','j3','j4','j5','j7','j8')), channel TEXT NOT NULL CHECK (channel IN ('whatsapp','email')),
  meta_name TEXT, version INT NOT NULL, language TEXT NOT NULL CHECK (language IN ('en','ar')),
  category_expected TEXT NOT NULL CHECK (category_expected IN ('UTILITY','MARKETING')), category_meta TEXT, meta_status TEXT,
  quality TEXT, subject TEXT, body TEXT NOT NULL, buttons JSONB NOT NULL DEFAULT '[]', variables JSONB NOT NULL DEFAULT '[]',
  lint JSONB NOT NULL DEFAULT '{}', approved_by BIGINT, approved_at TIMESTAMPTZ, submitted_at TIMESTAMPTZ, synced_at TIMESTAMPTZ,
  active BOOL NOT NULL DEFAULT TRUE, UNIQUE (hotel_slug, step, channel, language, version));
CREATE TABLE gst_journey_offers (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  step TEXT NOT NULL CHECK (step IN ('j3','j8')), title_en TEXT NOT NULL, title_ar TEXT,
  price_minor BIGINT, discount_pct NUMERIC(5,2), currency CHAR(3) NOT NULL, code_prefix TEXT,
  valid_from DATE NOT NULL, valid_to DATE NOT NULL,   -- the per-day send cap is the dial limit only (1.4)
  approved_by BIGINT, approved_at TIMESTAMPTZ, active BOOL NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
-- 03's direct_codes table, amended in 0672 (after the plans table) for J8: no profile needed, one code per plan
-- gst_direct_codes(code TEXT, hotel_slug, guest_profile_id BIGINT NULL, plan_id BIGINT REFERENCES gst_journey_plans(id),
--   offer_id BIGINT REFERENCES gst_journey_offers(id), discount_pct, valid_to, redeemed_at, redeemed_reservation_id BIGINT,
--   UNIQUE (hotel_slug, code))
-- 0672_guests_journey_plans.sql
CREATE TABLE gst_journey_plans (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  reservation_id BIGINT NOT NULL REFERENCES reservations(id),
  step TEXT NOT NULL CHECK (step IN ('j1','j2','j3','j4','j5','j6_pre','j6_post','j7','j8')), seq SMALLINT NOT NULL DEFAULT 1,
  due_at TIMESTAMPTZ NOT NULL, valid_until TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','deferred','claimed','proposed','observed','sent',
    'delivered','read','replied','skipped','cancelled','failed','done')),
  skip_reason TEXT, channel TEXT, contact_point_id BIGINT REFERENCES gst_contact_points(id),
  template_id BIGINT REFERENCES gst_journey_templates(id), offer_id BIGINT REFERENCES gst_journey_offers(id), direct_code TEXT,
  action_id BIGINT, outbound_message_id BIGINT, wamid TEXT, attempt SMALLINT NOT NULL DEFAULT 0, error_code TEXT,
  category_at_send TEXT, plan_version INT NOT NULL DEFAULT 1, checkin_time_inferred BOOL NOT NULL DEFAULT FALSE,
  claimed_at TIMESTAMPTZ, last_attempt_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX ON gst_journey_plans (hotel_slug, reservation_id, step, seq) WHERE status <> 'cancelled';
CREATE INDEX ON gst_journey_plans (status, due_at) WHERE status IN ('planned','deferred','claimed');
CREATE TABLE gst_marketing_sends (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  identity_hmac BYTEA NOT NULL, channel TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('journey_j3','journey_j8','demand_export','upsell','other')),
  phase TEXT NOT NULL CHECK (phase IN ('pre_stay','in_stay','post_stay')), reservation_id BIGINT, ref TEXT, sent_at TIMESTAMPTZ NOT NULL);
CREATE INDEX ON gst_marketing_sends (hotel_slug, identity_hmac, sent_at);
-- 0673_guests_journey_replies_feedback.sql
CREATE TABLE gst_journey_replies (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  plan_id BIGINT REFERENCES gst_journey_plans(id), reservation_id BIGINT, inbound_message_id BIGINT, wamid TEXT,
  classification JSONB NOT NULL,          -- without summary_en, which has its own column for retention
  summary_en TEXT, deterministic_hit TEXT, llm_used BOOL NOT NULL DEFAULT FALSE,
  outcome TEXT NOT NULL CHECK (outcome IN ('none','service_request','followup','opt_out','consent_yes','offer_accept','wrong_number')),
  service_ref TEXT, action_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE gst_public_tokens (token_hash BYTEA PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  reservation_id BIGINT NOT NULL, purpose TEXT NOT NULL CHECK (purpose IN ('prefs','feedback','consent')),
  expires_at TIMESTAMPTZ NOT NULL, used_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
  -- expires_at: prefs = check-in day 23:59 hotel tz; feedback = the J7 plan's valid_until; consent = sent_at + 24 h (3.4)
CREATE TABLE gst_stay_prefs (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), reservation_id BIGINT NOT NULL,
  arrival_time TEXT, requests TEXT, occasion TEXT, language TEXT, submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  purge_after TIMESTAMPTZ NOT NULL,       -- check_out + 30 d, re-set on dates_changed
  PRIMARY KEY (hotel_slug, reservation_id));
CREATE TABLE gst_feedback (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  reservation_id BIGINT NOT NULL, plan_id BIGINT, overall SMALLINT CHECK (overall BETWEEN 1 AND 5), went_well TEXT,
  improve TEXT, contact_ok BOOL, language TEXT, followup_ref TEXT, submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (hotel_slug, reservation_id));
CREATE TABLE gst_review_link_clicks (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), day DATE NOT NULL,
  platform TEXT NOT NULL CHECK (platform IN ('google','tripadvisor')), clicks INT NOT NULL DEFAULT 0,
  PRIMARY KEY (hotel_slug, day, platform));      -- counts only, no identity
-- 0674_guests_wa_rate_card.py   (intentionally global: Meta rate card rows, every row with source_url, UNVERIFIED)
-- gst_wa_rate_card(market CHAR(2), category TEXT, effective_from DATE, rate_micros BIGINT, currency CHAR(3), source_url TEXT, status TEXT,
--   PRIMARY KEY (market, category, effective_from))
```

**Retention (`register_pii`; every rule names a real `ts_column`):**
- `gst_contact_points.value_enc` is deleted when `usable_until` passes (`ts_column='usable_until'`, `where` provenance is not `guest_direct`), and for `guest_direct` at 730 d after `last_used_at`. `value_hmac` is nulled at the same time for `pms_ota`, `ota_alias` and `guest_stay` points (D1).
- `gst_feedback.went_well` and `improve` are redacted at 365 d after `submitted_at`.
- `gst_stay_prefs` is deleted at `purge_after`.
- `gst_journey_replies.summary_en` is redacted at 365 d after `created_at`.
- Registration-card evidence files are a high-risk class (3.4): registered with `register_pii` and deleted when the consent is withdrawn or when the point's `guest_direct` retention ends.

### 3.3 Planner (`journeys/planner.py`)

1. **Consumer.** The stay-event consumer `guests.journeys` (`@on_stay_event`) is idempotent on `stay_events.id`.
2. **`created`:**
   - A reservation that continues a stay chain (a predecessor shares its `stay_chain_id`) gets no J1-J4. At `created` only the predecessor's unsent J6_pre and J5 exist (J7 and J8 are planned at `checked_out`), so those are set to `cancelled` with reason `chain_continued`, and this reservation plans its own J6_pre. Rows are cancelled and re-created, never moved, so `(hotel_slug, reservation_id, step, seq)` stays unique among live rows.
   - Otherwise it plans J1, J2, J3, J4 and J6_pre. Eligibility is checked again at dispatch.
3. **`dates_changed`:**
   - It recomputes `due_at` and `valid_until` for `planned` and `deferred` rows and increments `plan_version`.
   - Rows that no longer fit become `skipped` with reason `dates_changed`.
   - J2 eligibility is re-evaluated.
4. **`cancelled` or `no_show`:**
   - Every unsent row becomes `cancelled`.
   - A J3 that was already sent stays `sent`. A pending `guests.offer_accept` task is cancelled.
5. **`checked_in`** plans J5.
6. **`checked_out`** plans J6_post, J7 and J8 (seq 1 and 2), and cancels an unsent J5. It plans J6_post, J7 and J8 only when no later reservation shares the `stay_chain_id`, so a guest who extends is asked once, after the final check-out. A test covers a 3-reservation chain.
7. **`contact_changed`** re-plans nothing. The channel is resolved at dispatch.
8. **Nightly repair.** `guests.journey_plan_reconcile` (04:30) repairs missed events. If a PMS never reports `checked_in_at`, the check-in date plus `standard_checkin_time` is used, with `checkin_time_inferred=true`.

**Plan states** (every change bumps `updated_at`; the dispatcher is the only writer of `claimed`):

| From | To | When |
|---|---|---|
| `planned`, `deferred` | `claimed` | The dispatcher claims a due row and stamps `claimed_at` |
| `claimed` | `planned`/`deferred` | Reclaim: a row still `claimed` 10 min after `claimed_at` (a crashed tick) is released |
| `claimed` | `skipped` | A guard fails (reason recorded) |
| `claimed` | `observed` | The dial is `observe` (an `observed` action is written) |
| `claimed` | `proposed` | The dial is `suggest` or `approve`, or limits fail |
| `claimed` | `sent` | The dial auto-approved and the executor sent |
| `proposed` | `sent` | Approved; the executor re-runs the guards first, else `skipped` |
| `proposed` | `skipped` | Rejected (`rejected`) or the action expired at `valid_until` (`approval_expired`) |
| `sent` | `delivered` → `read` → `replied` | Status hooks and replies |
| `sent`, `delivered` | `deferred` | Meta 131049 (3.5) |
| any unsent | `cancelled` | Stay events (above) |
| any | `failed` | The executor or delivery failed with no retry left |
| `planned` | `done` | J6_pre and J6_post gates only, after they run (3.9) |

### 3.4 Contacts, consent and channel eligibility

**Provenance and lifetime.** `core.stays` classifies each contact at import (D1) using `OTA_ALIAS_DOMAINS`. Guests owns `guest_direct`.

| Provenance | What it is | `usable_until` |
|---|---|---|
| `ota_alias` | Booking `@guest.booking.com`, Expedia `@m.expediapartnercentral.com`, the Agoda heuristic (UNVERIFIED) | Booking: check_out + 30 d. Expedia and Agoda: check_out (A2) |
| `pms_ota` | A real phone or email the OTA passed on for a Booking, Agoda or Expedia reservation | check_out + 30 d (GDT 2.2.6), then purged |
| `pms_direct` | A contact on a DIRECT_WEB, DIRECT_PHONE, WALKIN or CORPORATE reservation | Retention policy |
| `guest_direct` | The guest's own contact, captured with evidence through a Canon 19 route (see below) | Until consent is withdrawn, or 24 months inactive (`last_used_at`) |
| `guest_stay` | The guest's own contact given on the J1 page `/g` for an OTA reservation (`pms_ota`/`ota_alias`) | check_out + 30 d, then purged. Utility only for this stay: never J8, `marketing_audience` or `can_send_marketing`, and never linked to a guest profile |

**Consent purposes** (foundation `consents` via `core.consent`; `subject_ref` = hex of `contact_hmac`, which `core.privacy.contact_ref` returns):
- `whatsapp_updates`: Meta's opt-in for utility messages. The text names the hotel and says messages come on WhatsApp (uae/whatsapp.md §4).
- `marketing_whatsapp` and `marketing_email`: separate per channel. These retire 03's single `marketing` purpose; F2 builds both halves and registers them with `core.consent.register_consent_purpose` (the implemented core already has `whatsapp_updates`, `transfer_abroad`, `recording`, `ai_analytics`, `profile_memory` and `auto_reply`).
- `transfer_abroad`: express consent (PDPL Arts. 22-23). Canon PDPL: pilots rely on it because Neon, Google Gemini and Meta are outside the UAE.
- Every consent record stores its `consent_texts` version, language and evidence, through the implemented `record_consent(cur, hotel_slug, subject_ref, purpose, granted, *, source, evidence=None, subject_type='guest', text_version=None, language='en', ...)`; F2 always passes `text_version` and `language`.
- **Texts.** F2's migration `0675_guests_consent_texts.py` seeds `consent_texts` version 1 in EN and AR for `whatsapp_updates`, `marketing_whatsapp`, `marketing_email` and `transfer_abroad` (`core.consent.record_consent_text`), using the exact lines in this section. Lane I seeds its inquiry lines (4.3) the same way. Public pages fetch the texts from `GET /public/journeys/{token}` (and the inquiry widget config) instead of hardcoding them.

**Eligibility (`journeys/channels.py`):**

| Send | Requires |
|---|---|
| Utility on WhatsApp (J1, J2, J4, J5, J7) | All of the following: <br>• a phone point with provenance `pms_direct`, `guest_direct` or `guest_stay` <br>• a recorded `whatsapp_updates` consent, or an open window the guest opened (an inbound from that number within 24 h) <br>• within `usable_until` <br>• not suppressed <br>• an approved WhatsApp template for the step in the guest's language or EN: owner `approved_at` set **and** `meta_status='APPROVED'` from `brief.api.wa_template_info` <br>• the hotel's WABA is billing-ready (3.5) <br><br>`pms_ota` phones never receive business-initiated WhatsApp. That changes only when the guest opts in on property (which makes a `guest_direct` or `guest_stay` point) or messages first. |
| Utility on email | Any email point, including `ota_alias` and `pms_ota`, within `usable_until` and not suppressed, with an owner-approved (`approved_at`) email template for the step in the guest's language or EN. For J7, OTA-provided contacts also need `j7_for_ota_provided_contacts` (default `false`, pending legal review; 7.7). |
| Marketing (J3, J8) | All of the following: <br>• provenance `guest_direct`, or `pms_direct` with a separately recorded consent for that channel <br>• channel-specific marketing consent with evidence <br>• within `usable_until` <br>• guards pass (3.5) <br>• no suppression <br>• an approved template for that channel, as above <br><br>`ota_alias`, `pms_ota` and `guest_stay` are never eligible. |
| WhatsApp marketing to a `+1` number | Blocked. +1 is the NANP (US, Canada, Caribbean), a conservative superset of Meta's ban on marketing templates to US numbers (zz-critique.md §3). It falls back to email only if `marketing_email` consent exists. Area-code filtering can be revisited later. |

Order: the first eligible channel wins, WhatsApp before email. Template approval is part of eligibility, so a guest who is WhatsApp-eligible but has no approved WhatsApp template falls through to email. With none, the step is skipped as `no_eligible_contact` (or `template_missing` when a contact exists but no channel has an approved template).

**Transfer consent is required for every capture.** `record_own_contact` raises `ConsentNotAllowed` unless `consents['transfer_abroad']` is true with a text version, for every `capture_source` (Canon PDPL; PDPL Arts. 22-23). Each capture surface therefore carries a separate transfer tick. A test covers each `capture_source`.

**How an OTA guest becomes direct (Canon 19).**
- `guests.api.record_own_contact()` creates a `guest_direct` point with evidence. Only the three Canon 19 routes feed it (plus inquiries, below, which are not OTA guests):
  - **The concierge** (QR consent sheet, 03 §2.3), which carries a separate transfer tick.
  - **The registration card.** The console posts `POST /hotels/{slug}/guests/contacts` (`guests.contacts.capture`). The card carries a separate transfer tick.
    - **Evidence is minimal.** It is either a crop of the card's contact-and-consent block only, or a staff attestation plus the guest's signature image. ID fields (passport or Emirates ID number, nationality, date of birth) are never uploaded or OCR'd: Clefs must not become a store of ID scans (privacy-reviews.md A3).
    - The upload is rejected when it is larger than 300 KB or when text detection finds an MRZ pattern (`P<` or `ID` lines of `<` fillers).
    - Evidence files are a high-risk class, registered with `register_pii` and deleted when the consent is withdrawn or when the point's retention ends (3.2).
  - **A J5 reply** (3.8). After a non-negative reply, inside the window the guest opened, one free-form ask is sent: "Would you like direct-booking offers from {hotel} on WhatsApp? Reply YES. Reply STOP anytime." A single YES cannot stand for two separate consents, so:
    - YES records only `marketing_whatsapp` consent, with the inbound message id as evidence.
    - The reply to that YES, still inside the window, carries a `/g/[token]` link (token purpose `consent`, valid 24 h) showing only the transfer tick.
    - When the guest ticks it, `record_own_contact(capture_source='j5_reply')` creates the `guest_direct` point from the sender number. Until then no point exists and the guest receives no marketing.
- **The J1 page `/g/[token]`:**
  - It shows marketing tick boxes only for direct reservations: "Send me offers from {hotel} on WhatsApp" and "... by email", each separate and unticked.
  - For `pms_ota` and `ota_alias` reservations it shows no marketing boxes. It may still capture the guest's own phone or email with `whatsapp_updates` for this stay's service messages. That contact is stored as a `guest_stay` point (`usable_until = check_out + 30 d`), not `guest_direct`, because Canon 19 does not list the J1 form as a route to a direct contact. `record_own_contact` rejects `guest_direct` creation and any marketing consent for `capture_source='j1_form'` on OTA reservations.
  - Submitting any contact needs the separate required `transfer_abroad` tick.
- **Inquiries.** A verified inquiry that converts passes its consented contact through `record_own_contact(capture_source='inquiry')`; the web form already carries the transfer tick (4.3). Inquiries from other channels have no transfer consent and create no point.

### 3.5 Guards (`journeys/guards.py`), checked at plan time and again in the executor

| Guard | Rule |
|---|---|
| Eligibility | 3.4 |
| Dial | Effective level from section 1. `off` skips with `dial_off`. `observe` writes an observed action and sends nothing |
| Marketing window | [09:00, 18:00) hotel tz, half-open, on every channel. Settings may narrow it but never widen it. A send due outside the window moves to the next window start. It is skipped as `window_expired` if that is past `valid_until` |
| Utility window | [08:00, 21:00) by default (a setting), half-open. J1 is exempt within 2 h of booking. Replies inside a window the guest opened are not bound by it |
| Daily cap | 1 business-initiated journey message per guest identity per local day, unless the guest wrote first that day. J1 and J4 always send (they are transactional) but they count. On a collision the priority is J1 > J4 > J5 > J7 > J2 > J3 > J8. The loser is deferred 1 day within `valid_until`, else skipped as `cap_daily` |
| Pre-stay marketing | At most 1 per reservation before check-in (J3) |
| In-stay marketing | At most 1 per reservation, reserved for upsell, through `can_send_marketing(phase='in_stay')` |
| Post-stay marketing | At most 2 per identity per rolling 30 days, across J8, Demand exports and upsell (`gst_marketing_sends`) |
| Complaint pause | `service.api.has_open_complaint` pauses marketing (`complaint_open`). It **never** pauses J7 |
| Review policy | J3 and J8 bodies and variables are lint-blocked for review words (3.6). No step except J7's completion page refers to reviews |
| Meta 131049 | Arrives through `on_outbound_status` after the action is `done`. The hook sets the plan to `deferred` with `error_code='131049'`, `attempt=2` and `due_at = max(now + 24 h, next window start)`. Dispatch re-uses the **same** `action_id` and enqueues `core.actions.execute` again with key `gst:{plan_id}:a2`; no new action and no new approval. The utility verifier measures its 48 h from `last_attempt_at`. A second 131049 skips it as `meta_131049`. Falls back to email only with `marketing_email` consent. Messages inside a window the guest opened are not capped by Meta |
| Other Meta errors (semantics UNVERIFIED; check Meta's error reference, 7.7) | 131047 (outside the window): resend as a template. 131026 (undeliverable): email only for utility steps, or for marketing steps with `marketing_email` consent. 131056 and 130429: backoff in `core.notify.deliver` (F4) |
| DNCR | The `dncr_check()` adapter stub returns `unknown`; how to check is UNVERIFIED. The manual suppression list is honoured |
| Template gate | Per channel, as part of eligibility (3.4). WhatsApp needs owner `approved_at` **and** Meta `APPROVED` (from `brief.api.wa_template_info`); email needs owner `approved_at` only. Use the guest's language (ar or en), else EN. The dial gate caps the step at `observe` only when no eligible channel has an approved template; if a contact exists but no template does, the step is skipped as `template_missing`. The category synced from Meta must be MARKETING for J3 and J8. If Meta recategorised a utility step to MARKETING, that step follows the marketing rules (consent, window, caps) |
| WhatsApp billing readiness | Directly integrated businesses with no Meta payment method on file by 2026-09-30 reportedly stop having service messages delivered (uae/whatsapp.md §3 and §8; partly UNVERIFIED). A per-hotel check reads the WABA's payment-method status through Brief's WhatsApp integration, or an owner attestation in settings. While it is not ready, free-form WhatsApp sends (the inquiry ack, free-form quotes, the J5 consent ask and replies) go to email, and the signal `guests.wa_billing_not_ready` (service_risk, weekly) is raised |

### 3.6 Template library (seeded per hotel, pre-sorted by Meta category, variables `{{n}}`)

| Name | Cat | EN body | AR body | Buttons |
|---|---|---|---|---|
| `gst_j1_confirm_v1` | UTILITY | Hello {{1}}, your stay at {{2}} is confirmed: {{3}} to {{4}}, booking {{5}}. To help us prepare, share your arrival time and any requests. | مرحباً {{1}}، تم تأكيد إقامتك في {{2}} من {{3}} إلى {{4}}، رقم الحجز {{5}}. لمساعدتنا في التحضير، شاركنا موعد وصولك وأي طلبات. | URL "Add details" / "أضف التفاصيل", `https://<web>/g/{{1}}` (dynamic suffix) |
| `gst_j2_planning_v1` | UTILITY | Hello {{1}}, your stay at {{2}} starts on {{3}}. For planning: {{4}}. Reply here if you need anything before you arrive. | مرحباً {{1}}، تبدأ إقامتك في {{2}} يوم {{3}}. للتخطيط: {{4}}. راسلنا هنا إذا احتجت أي شيء قبل وصولك. | |
| `gst_j3_offer_v1` | MARKETING | Hello {{1}}, this is {{2}}. Before you arrive on {{3}}: {{4}} for {{5}}. Reply YES to add it to your booking. Reply STOP to opt out of offers. | مرحباً {{1}}، معك {{2}}. قبل وصولك يوم {{3}}: {{4}} مقابل {{5}}. أرسل نعم لإضافته إلى حجزك. أرسل إيقاف لإلغاء الاشتراك في العروض. | |
| `gst_j4_checkin_v1` | UTILITY | Hello {{1}}, we look forward to welcoming you on {{2}}. Check-in is from {{3}}. Please bring {{4}}. Directions: {{5}}. Reply here if you need help finding us. | مرحباً {{1}}، نتطلع إلى استقبالك يوم {{2}}. تسجيل الوصول من الساعة {{3}}. يرجى إحضار {{4}}. الاتجاهات: {{5}}. راسلنا هنا إذا احتجت مساعدة في الوصول إلينا. | |
| `gst_j5_daycheck_v1` | UTILITY | Hello {{1}}, this is {{2}}. How is room {{3}} for your stay until {{4}}? If anything needs fixing, reply here and we will send someone. | مرحباً {{1}}، معك {{2}}. كيف الغرفة {{3}} خلال إقامتك حتى {{4}}؟ إذا كان هناك ما يحتاج إلى إصلاح، راسلنا هنا وسنرسل من يساعدك. | Quick replies "All good" / "كل شيء جيد" and "Something to fix" / "هناك مشكلة" |
| `gst_j7_feedback_v1` | UTILITY | Hello {{1}}, thank you for staying at {{2}} from {{3}} to {{4}}. How was your stay? It takes one minute. | مرحباً {{1}}، شكراً لإقامتك في {{2}} من {{3}} إلى {{4}}. كيف كانت إقامتك؟ يستغرق ذلك دقيقة واحدة. | URL "Give feedback" / "شاركنا رأيك", `https://<web>/g/f/{{1}}` |
| `gst_j8_rebook_v1` | MARKETING | Hello {{1}}, thank you again for staying at {{2}}. Book direct with code {{3}} for {{4}} until {{5}}: {{6}}. Reply STOP to opt out. | مرحباً {{1}}، شكراً مجدداً لإقامتك في {{2}}. احجز مباشرة بالرمز {{3}} للحصول على {{4}} حتى {{5}}: {{6}}. أرسل إيقاف لإلغاء الاشتراك. | |

- **Variable fallbacks and sources.**
  - `{{1}}` falls back to "Guest" / "ضيفنا".
  - J2's `{{4}}` and J4's `{{4}}` and `{{5}}` come from approved KB entries (arrival, ID policy, directions), each 200 chars or fewer.
  - J5's `{{3}}` is the room number, falling back to "booking {ref}".
  - J8's `{{6}}` is the booking-engine URL with `utm_source=clefs&utm_medium=<channel>&utm_campaign=gst_j8&utm_content=<plan_id>`.
- **Category basis.** J1, J2, J4 and J5 are UTILITY by inference from Meta's order-management examples (A1). J5 carries the room and the stay's dates because a generic survey is not accepted as utility (journey-automation.md §2).
- **Email variants** carry the same content. Marketing email carries a `List-Unsubscribe` header (F4) and a footer link.
- **Lint on all templates:**
  - no U+2014
  - the variable count matches
  - body 1,024 chars or fewer
  - no variable at the start or end of the body, ignoring trailing punctuation
  - no two adjacent variables
  - quick-reply text 25 chars or fewer
  - at most 2 URL buttons, and dynamic URL variables only as a suffix
  - header and footer 60 chars or fewer
  
  Source: uae/whatsapp.md §2a.
- **Utility lint** blocks promotional words in the body and in variable values: offer, deal, discount, % off, promo, save, special, free, upgrade, عرض, خصم, تخفيض, مجاني, ترقية.
- **Marketing lint:**
  - The body must name the hotel (Meta opt-in guidance; Cabinet Resolution 56/2024 business-identity requirement reported by secondary sources, UNVERIFIED, uae/whatsapp.md §5, 7.7).
  - The body must carry STOP text.
  - J3 and J8 block review words: review, rate us, rating, تقييم, مراجعة.
- **Submission.** Templates go to Meta through `brief.api.submit_template`. The owner approves each version in the console.
- **STOP words:** STOP, UNSUBSCRIBE, إيقاف, الغاء, توقف, matched as the whole message or its first token.
- **YES words:** YES, Y, نعم, ايوه.

### 3.7 Sending through the ledger

1. `guests.journey_dispatch` runs every minute and claims due plans (`SKIP LOCKED`, batch 200), setting `claimed` and `claimed_at`. Rows left `claimed` for 10 min are released (3.3 plan states).
2. It evaluates the guards. A failure sets `skipped` with a reason and creates no action.
3. On a pass it calls `create_action(type=<step type>, title=f"{step_label} · booking {ref}", params={plan_id, reservation_id, step, template_id, contact_point_id}, expires_at=plan.valid_until, actor=SYSTEM)`, and the dial decides what happens. For J8 it first mints the plan's direct code: `offer.code_prefix` + 6 base32 characters, inserted into `gst_direct_codes` with `plan_id` and `offer_id` (`UNIQUE (hotel_slug, code)`, retried on collision) and stored in `plan.direct_code`. The step types are:
   - `guests.journey_j1_confirm`
   - `guests.journey_j2_planning`
   - `guests.journey_j3_offer`
   - `guests.journey_j4_checkin`
   - `guests.journey_j5_daycheck`
   - `guests.journey_j7_feedback`
   - `guests.journey_j8_rebook`
4. The executor `send_step` runs the guards again (idempotent on `plan_id`) and unseals the contact through `core.stays.contact_value` or the point's own `value_enc`. It then sends through one of:
   - `notify.send_whatsapp_template(..., sender='hotel', purpose='guests.journey.<step>', idempotency_key=f'gst:{plan_id}:a{attempt}')`
   - `send_email(headers=...)`
   
   Marketing sends write `gst_marketing_sends`.
5. It returns an `ExecResult` with:
   - `template_id=meta_name@version`
   - `template_category` = Meta's current category
   - `consent_basis` = `'contract:reservation:<id>'` for utility (J7's basis is under legal review, 7.7), or `'consent:<consent_id>'` for marketing
   - `reversal_handle={kind:'none', reason:'message_sent'}`
6. `@on_outbound_status('guests.journey')` updates the plan to `delivered`, `read` or `failed`, with the error code (131049 goes to `deferred` instead, 3.5). Every send and every inbound from a point updates its `last_used_at`.
7. **Verifiers:**
   - **Utility steps and J3** are verified when delivered within 48 h of `last_attempt_at`. They carry no money. J3's money is on `guests.offer_accept` (3.8).
   - **J8** runs with `verify_after=1 d`, `verify_every=1 d`, `verify_until=30 d`. A direct reservation for the same guest identity (contact hmac) in `core/stays` within 30 days is matched and then scored:
     - **Counts toward verified profit** only when this plan's J8 code was redeemed by the matched guest (`reservations.promo_code` equals `plan.direct_code`; the verifier then sets `redeemed_at` and `redeemed_reservation_id`) or the J8 link was used (`utm_campaign=gst_j8`, `utm_content=plan_id`). A code redeemed on another guest's reservation does not count. Then `outcome_minor = expected_commission(cur, slug, ota_code=<previous stay's OTA channel>, breakdown=core.stays.reservation_breakdown(cur, new_reservation_id), on=check_in)['commission_minor'] − code discount − direct distribution cost (profit_guard.api.distribution_cost_for_reservation) − J8 send cost`.
     - **Previous stay direct.** If the previous stay was direct, the commission term is 0 and the outcome may be negative. That is recorded honestly (A15).
     - **A match on the hmac only** is recorded as `verified` with `attribution='influenced'` and `counts_toward_verified_profit=false`.

### 3.8 Replies (`journeys/replies.py`)

- **Handlers.** `@on_whatsapp(audience='guest', priority=10)` handles journey replies (F4 order). The priority-5 handler handles STOP for any guest sender.
- **YES disambiguation.** YES is resolved by `context.id`, or else by the latest journey message sent to that contact within 24 h:
  - After J3, YES creates the human task `guests.offer_accept` for the front desk ("Add {offer} to booking {ref}"). It is verified when staff mark it done. Its outcome is the offer price, labelled upsell revenue, with `counts_toward_verified_profit=false`.
  - After the J5 consent ask, YES records consent (3.4).
  - With no match, the message passes to the next handler.
- **Classification:**
  1. First a deterministic EN/AR lexicon runs, covering opt-out, negative phrases ("not working", "dirty", "noisy", "cold", "لا يعمل", "وسخ", "مشكلة") and the button payloads.
  2. Then `ai.structured(store=False)` returns `ReplyClass{sentiment, is_request, summary_en, department_hint, severity L1-L3, is_opt_out, is_complaint_about_messaging, is_wrong_number, language}`. The LLM can only upgrade the deterministic result, never downgrade it.
     - The LLM step runs only when the contact has `transfer_abroad` consent, or when the hotel setting `ai_on_unconsented_inbound` is true (default **false** until counsel signs off, 7.7). Otherwise only the lexicon runs (`llm_used=false`): lexicon hits act as below, and other replies appear unclassified on the reservation timeline for staff to read. A test asserts that no LLM call is made for a journey reply when the setting is off.
  3. `department_hint` is one of housekeeping, engineering, ird, front_office, concierge, security, general.
- **Negative or request.**
  - While the guest is in house: `service.api.create_request(room=<current room>, type=service.api.REQUEST_TYPE_FOR_DEPARTMENT[department_hint], description=summary_en, source='whatsapp_journey', language, guest_ref=reservation_id, priority_hint=severity)`.
  - After check-out: `service.api.create_followup(kind='guest_issue', source='whatsapp_journey', ...)`, which skips the ack ladder.
  - Then one fixed acknowledgement is sent: "Thank you, the team has been told and will contact you shortly" (and the AR version).
  - After that the AI stays silent and a human takes over. Marketing pauses until the ticket closes.
- **`is_complaint_about_messaging`:** opt-out plus `report_incident('guest_complaint', [the step's action type], evidence)`.
- **`is_wrong_number`:** a suppression with scope `all`, and the point is set to `suppressed`.

### 3.9 J6 gate and J7 feedback (no sentiment routing)

**J6_pre:**
- It calls `service.api.open_items_for_stay(room, since=check_in)`, which returns `[{id, request_type, severity: 'L1'|'L2'|'L3', status, created_at}]`, and looks for unresolved negative J5 replies.
- If any exist:
  - It creates the action `guests.checkout_recovery` (human task, `owner_role='gm'`), for example: "Room 504 departs today with 1 open AC ticket; attempt recovery at check-out".
  - It notifies whoever `service.api.on_duty(role='duty_manager')` returns.
  - It raises the signal `guests.checkout_open_issues` (service_risk). Its cadence is `daily` only when a linked open item is L1, otherwise `weekly` (F7).
- J6_post turns items still open into a post-stay recovery task through `create_followup(kind='post_stay_recovery', source='journey_gate', ...)`.
- Both gates end in plan status `done`.
- **J6 never reads or writes J7 plans.**

**Review links:**
- `review_links(cur, hotel_slug) -> ReviewLinks{google_url, tripadvisor_url}` reads settings only.
- The Google link is the GBP "Get more reviews" share link on the host `g.page`, or a `search.google.com` writereview link, verified at setup (7.7). Tripadvisor links must be on `tripadvisor.*`.
- There are no other parameters, and a test asserts the signature.

**`/g/f/[token]` feedback page:**
- A line "Prefer to post a public review? Google · Tripadvisor" sits above a short survey: overall 1-5, what went well, what to improve, and whether contact is OK.
- `POST /public/journeys/{token}/feedback` stores the answers and returns `{completion_key:'thanks', review_links: review_links(hotel)}`. The response is identical for every payload.
- The completion page renders the same `ReviewLinks` component with the same props. There is no incentive text and no rating-dependent copy.

**Low ratings.** Only after the response has been built, a rating of 2 or less, or a negative comment, calls `service.api.create_followup(kind='guest_feedback', source='j7_feedback', owner_role='gm')`.

**Scripts and staff:**
- Recovery and follow-up scripts never mention reviews.
- Staff never request ratings on the premises (Google contribution policy).
- These rules are printed on the recovery task card.

**Click logging.** `GET /public/journeys/{token}/go/{google|tripadvisor}` returns a 302 and increments `gst_review_link_clicks`. Nothing else is stored.

### 3.10 Jobs, signals, metrics, API, console, public pages

**Jobs:**
- `guests.journey_dispatch` (every 1 m)
- `guests.journey_plan_reconcile` (04:30)
- `guests.journey_contact_purge` (03:40): marks points past `usable_until` as `purged`, deletes their `value_enc`, nulls `value_hmac` for `pms_ota`, `ota_alias` and `guest_stay` points, and deletes `guest_direct` values after 730 d without use (`last_used_at`)
- `guests.optout_monitor` (hourly): calls `report_incident('optout_spike')` per 1.5.
  - **Attribution.** A STOP counts against the step of the journey message it replies to (`context.id`), else the most recent journey send to that contact within 72 h. Otherwise it is unattributed and counts for no type.
  - **Rates** are per action type: attributed opt-outs / delivered sends of that type, over 7 d and 28 d. Only the types over threshold are passed to `report_incident`.
- `guests.template_category_check` (daily): compares the expected and actual category through `brief.api.wa_template_info`
- `guests.wa_billing_check` (daily): the WABA payment-method readiness check (3.5)
- `guests.journey_feedback_expire` (daily): sets `used_at` on `feedback` and `consent` tokens past `expires_at`, so their pages show "This link has expired"
- the stay-event consumer `guests.journeys`

**Signals:**

| Kind | Section | Cadence |
|---|---|---|
| `guests.checkout_open_issues` | service_risk | daily if L1, else weekly |
| `guests.templates_pending_approval` | service_risk | weekly |
| `guests.optout_spike` | compliance | weekly |
| `guests.meta_cap_blocks` | compliance | weekly |
| `guests.wa_billing_not_ready` | service_risk | weekly |
| `guests.j8_conversion_verified` | verified | weekly |

**Metrics** (dimensions: step, channel, category, language, month):
- `guests.journey_sends`
- `guests.journey_delivery_rate`
- `guests.journey_response_rate`
- `guests.j5_negative_rate`
- `guests.feedback_completion_rate`
- `guests.review_link_clicks` (dimension: platform only)
- `guests.direct_contact_capture_rate`: OTA stays that gained a `guest_direct` point with consent
- `guests.optout_rate`
- `guests.direct_rebook_conversions`: one key shared with 03; counted J8 conversions only, with influenced ones as a separate dimension value
- `guests.returning_share`: 03's key, measured from PMS identities and never claimed
- `guests.journey_cost` (`Metric(unit='money', currency='hotel')`; no currency in the key, because legacy tenants use INR):
  - Marketing templates delivered are charged at the marketing rate, and J7 is charged at the marketing rate.
  - Utility templates delivered outside a window are charged at the utility rate.
  - From 2026-10-01, utility templates inside a window and service messages are charged at the utility rate, less `WA_SERVICE_FREE_ALLOWANCE` per business number per month, applied in send order across all purposes (A4).
  - Rates come from `gst_wa_rate_card`, UNVERIFIED.
- Review velocity is shown from `reviews.api.review_counts` with the caption "measured, not targeted".

**Console routes (`/hotels/{slug}/guests/journeys/...`):**
- `overview`
- `templates` (GET), `templates/{id}/approve`
- `offers` (CRUD), `offers/{id}/approve`
- `reservations/{id}/timeline`
- `settings` (GET/PUT)
- `/hotels/{slug}/guests/contacts` (POST, registration-card capture). It uses `require_perm_or_device("guests.contacts.capture")`; a device principal must also carry the Front Office department (`principal.device_departments`), so housekeeping tablets cannot capture contacts.

**Public routes** (rate-limited through `ratelimit.hit`; the token purpose is enforced):
- `GET /public/journeys/{token}` (includes the consent texts to render, 3.4)
- `POST /public/journeys/{token}/prefs`
- `POST /public/journeys/{token}/consent` (the J5 transfer tick, 3.4)
- `POST /public/journeys/{token}/feedback`
- `GET /public/journeys/{token}/go/{platform}`

**Console UI (`(console)/guests/journeys/`):**
- `page.tsx`: a step funnel table (planned, sent, delivered, replied, skipped by reason), the capture rate, opt-outs, cost, and dial chips per step. Empty state, when no template is approved: "All steps are observing: approve templates to start sending", linking to `templates/`.
- `templates/`: EN and AR side by side with `dir=rtl`, expected vs Meta category, lint results, and Approve.
- `offers/`
- `timeline/[reservationId]`
- `settings/`: review links, utility window, enabled steps.

**Public client pages** (noindex, EN/AR RTL):
- `client/app/g/[token]/page.tsx`: preferences, own contact, and the consent ticks from 3.4.
- `client/app/g/f/[token]/page.tsx`

**Settings model `JourneySettings`:**
- `enabled_steps`, `utility_window`, `marketing_window` (narrow only)
- `j2_min_lead_days=10`, `j5_offset_h=18`, `j7_offset_days=2`, `j8_offsets=[30,60]`
- `review_links`, `j7_for_ota_provided_contacts=False`
- `standard_checkin_time`, `standard_checkout_time`
- `ai_on_unconsented_inbound=False` (3.8; the same default as Inquiries, 4.4)
- `wa_billing_attested_at` (owner attestation for 3.5 when the API cannot read the payment method)

### 3.11 Tests and edge cases

**Tests (`tests/modules/guests/test_journeys_*.py`):**
- **`test_no_sentiment_based_review_routing`:**
  - Parametrises overall rating 1..5 × J5 negative or positive × open tickets or none.
  - Asserts that the `review_links` payload is byte-identical, the J7 `due_at` is identical and the completion payload is identical.
  - Asserts that `inspect.signature(review_links)` is exactly `(cur, hotel_slug)`.
  - Asserts that J6 functions never touch J7 plans (grep and behaviour).
- **`test_no_marketing_to_ota_aliases`:**
  - Covers Booking `@guest.booking.com`, Expedia `@m.expediapartnercentral.com`, the Agoda heuristic and a Booking real phone (`pms_ota`), each with a marketing consent row forced in.
  - J3 and J8 are skipped with `no_eligible_contact`, and the capture outbox holds 0 marketing sends.
  - After a registration-card capture, J8 goes to the `guest_direct` point only.
- **`test_no_whatsapp_without_optin`:**
  - A `pms_direct` phone without `whatsapp_updates` falls to email.
  - A `pms_ota` phone gets WhatsApp only after an inbound from the guest opens a window, or after an opt-in on property.
- **`test_j1_form_no_marketing_for_ota`:** the `/g` config for an OTA reservation has no marketing boxes; a contact submitted there becomes a `guest_stay` point that J8, `marketing_audience` and `can_send_marketing` never return; and `record_own_contact` rejects `guest_direct` creation and marketing consents with `capture_source='j1_form'` on OTA reservations.
- **`test_transfer_consent_required`:** for each `capture_source`, `record_own_contact` without `transfer_abroad` raises `ConsentNotAllowed`; a J5 YES alone records `marketing_whatsapp` and creates no point until the `/g` consent tick.
- **`test_registration_card_evidence`:** an upload over 300 KB or with an MRZ pattern is rejected; the evidence file is registered as high-risk PII and deleted on withdrawal.
- **`test_channel_fallthrough`:** a WhatsApp-eligible guest with no Meta-approved WhatsApp template receives email when an approved email template exists; with no approved template on any channel the step is `observe`.
- **`test_plan_states`:** a `claimed` row is reclaimed after 10 min; a rejected `proposed` step ends `skipped('rejected')`; an unapproved one ends `skipped('approval_expired')` when its action expires.
- **Other cases:**
  - Meta category mapping; template lint: J4 without its closing sentence fails; adjacent variables; the hotel name missing from a marketing template; review words in J3.
  - Caps: pre-stay, post-stay 2 per 30 d (including a Demand export row) and daily with priority.
  - Half-open windows at 08:59, 09:00, 17:59 and 18:00 Asia/Dubai (18:00 is outside).
  - 131049 retries after 24 h with key `a2` on the same action (no new approval), then skips; delivery within 48 h of the retry verifies.
  - Opt-out attribution: a STOP replying to a J3 counts against J3 only; a STOP with no journey send in 72 h counts for no type; the demo 4/40 demotes J3 alone.
  - +1 numbers get no WhatsApp marketing.
  - While the WABA is not billing-ready, free-form sends go to email and `guests.wa_billing_not_ready` is raised.
  - A J8 code redeemed by a different guest does not count toward verified profit.
  - J6 creates the task and the L1 cadence.
  - A J5 negative opens a service request, and after check-out a follow-up.
  - STOP in EN and AR; YES disambiguation.
  - A step with no approved template on any eligible channel drops to `observe`.
  - The `usable_until` purge, with Expedia J7 skipped as `alias_expired`.
  - Re-planning on a date change.
  - The J8 verifier: a redeemed code counts; a match on the hmac only is influenced.
  - The cost formula before and after 2026-10-01.

**Edge cases:**
- A multi-room reservation sends once.
- Back-to-back extensions suppress J1 and plan J7 and J8 only after the final check-out (a 3-reservation chain).
- A cancellation after J3 was sent.
- A hotel without WhatsApp configured sends email only.
- `NOTIFY_MODE=capture`.
- GROUP and CORPORATE segments get no J3.
- A wrong-number reply suppresses the contact.
- A PMS with no check-in timestamps (inferred time).

---

## 4. Inquiry Guard (`modules/inquiries/`, lane I)

### 4.1 DDL

```sql
-- 0950_inquiries_core.sql
CREATE TABLE inq_inquiries (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug), ref TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('whatsapp','email','web_form','meta_leadgen','google_leadform','phone_desk','concierge')),
  channel_ref TEXT, external_lead_id TEXT, ad_referral JSONB, ctwa_clid TEXT, gclid TEXT, utm JSONB, page_url TEXT, is_test BOOL NOT NULL DEFAULT FALSE,
  contact_name TEXT, contact_phone_enc BYTEA, contact_phone_hmac BYTEA, contact_email_enc BYTEA, contact_email_hmac BYTEA, company TEXT,
  consent_basis TEXT NOT NULL CHECK (consent_basis IN ('express_web_form','pre_contract_notice','staff_logged')),
  consent_ids BIGINT[] NOT NULL DEFAULT '{}', notice_sent_at TIMESTAMPTZ,
  language TEXT, segment TEXT NOT NULL DEFAULT 'unknown' CHECK (segment IN ('fit','corporate','group','long_stay','event','unknown')),
  is_rfp BOOL NOT NULL DEFAULT FALSE, intent TEXT,
  check_in DATE, check_out DATE, nights INT, rooms INT, adults INT, children INT, room_type_code TEXT,
  budget_minor BIGINT, budget_currency CHAR(3), dates_flexible BOOL, extraction JSONB, extraction_confidence NUMERIC(3,2),
  stage TEXT NOT NULL DEFAULT 'new' CHECK (stage IN ('new','replied','quoted','payment_pending','confirmed_verified','lost',
    'turned_down','future','spam','rerouted')),
  guest_engaged BOOL NOT NULL DEFAULT FALSE, stage_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  first_response_due_at TIMESTAMPTZ, first_response_at TIMESTAMPTZ, first_response_by TEXT, first_response_business_min INT,
  sla_breached BOOL NOT NULL DEFAULT FALSE, wa_window_expires_at TIMESTAMPTZ, ctwa_free_window_expires_at TIMESTAMPTZ,
  expected_adr_minor BIGINT, room_nights_est INT, room_nights_estimated BOOL, stage_probability NUMERIC(4,3),
  money_at_risk_minor BIGINT, currency CHAR(3) NOT NULL,
  lost_reason TEXT CHECK (lost_reason IN ('PRICE_VS_COMP','NO_AVAILABILITY','SLOW_RESPONSE','BOOKED_VIA_OTA','BOOKED_COMPETITOR',
    'GUEST_UNRESPONSIVE','PAYMENT_LINK_EXPIRED','POLICY_TERMS','DATES_CHANGED','PRODUCT_LOCATION_MISMATCH','TRIP_CANCELLED',
    'SPAM_DUPLICATE','DISPLACEMENT','POLICY','BELOW_FLOOR')),     -- lost codes plus turned_down codes (4.10)
  lost_note TEXT, future_remind_on DATE, duplicate_of BIGINT REFERENCES inq_inquiries(id), assignee_user_id BIGINT,
  last_inbound_at TIMESTAMPTZ, last_outbound_at TIMESTAMPTZ, last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (hotel_slug, ref));
CREATE UNIQUE INDEX ON inq_inquiries(hotel_slug, source, external_lead_id) WHERE external_lead_id IS NOT NULL;
CREATE INDEX ON inq_inquiries(hotel_slug, stage, first_response_due_at);
CREATE INDEX ON inq_inquiries(hotel_slug, contact_phone_hmac); CREATE INDEX ON inq_inquiries(hotel_slug, contact_email_hmac);
CREATE TABLE inq_messages (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  inquiry_id BIGINT NOT NULL REFERENCES inq_inquiries(id), direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  channel TEXT NOT NULL, provider_message_id TEXT, body TEXT, attachments JSONB NOT NULL DEFAULT '[]',
  author_type TEXT NOT NULL CHECK (author_type IN ('guest','staff','ai','system')), author_user_id BIGINT,
  outbound_message_id BIGINT, template_name TEXT, action_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (hotel_slug, channel, provider_message_id));
CREATE TABLE inq_stage_events (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  inquiry_id BIGINT NOT NULL REFERENCES inq_inquiries(id), from_stage TEXT, to_stage TEXT NOT NULL,
  at TIMESTAMPTZ NOT NULL DEFAULT now(), actor_type TEXT, actor_id TEXT, reason TEXT, data JSONB);
CREATE TABLE inq_stage_probabilities (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), stage TEXT NOT NULL,
  p NUMERIC(4,3) NOT NULL, n INT NOT NULL, computed_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (hotel_slug, stage));                       -- recalibrated values (4.5); absent rows use the assumed priors
CREATE TABLE inq_templates (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  name TEXT NOT NULL CHECK (name IN ('inq_quote_ready_v1','inq_quote_followup_v1')), language TEXT NOT NULL CHECK (language IN ('en','ar')),
  body TEXT NOT NULL, buttons JSONB NOT NULL DEFAULT '[]', version INT NOT NULL, meta_status TEXT, category_meta TEXT,
  approved_by BIGINT, approved_at TIMESTAMPTZ, submitted_at TIMESTAMPTZ, synced_at TIMESTAMPTZ,
  UNIQUE (hotel_slug, name, language, version));          -- seeded per hotel from 4.6 and 4.10
-- 0951_inquiries_quotes.sql
CREATE TABLE inq_quotes (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  inquiry_id BIGINT NOT NULL REFERENCES inq_inquiries(id), version INT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','awaiting_approval','approved','sent','accepted','expired','superseded','withdrawn')),
  lines JSONB NOT NULL,   -- [{stay_date, room_type_code, rooms, rate_minor, bar_minor, floor_minor, ceiling_minor, discount_pct, source}]
  room_minor BIGINT NOT NULL, sc_minor BIGINT NOT NULL, mf_minor BIGINT NOT NULL, vat_minor BIGINT NOT NULL, td_minor BIGINT NOT NULL,
  td_nights_charged INT NOT NULL, td_units INT NOT NULL, total_minor BIGINT NOT NULL, currency CHAR(3) NOT NULL,
  tax_convention CHAR(1), tax_rule_ids BIGINT[], fee_notices JSONB NOT NULL DEFAULT '[]',
  below_bar BOOL NOT NULL, max_discount_pct NUMERIC(5,2) NOT NULL DEFAULT 0, valid_until TIMESTAMPTZ NOT NULL,
  deposit_schedule JSONB NOT NULL, cancellation_terms JSONB NOT NULL, group_terms JSONB, inclusions JSONB, language TEXT NOT NULL,
  pricing_trace JSONB NOT NULL, guardrails_snapshot JSONB NOT NULL, comp_snapshot JSONB,
  auto_send_eligible BOOL NOT NULL, auto_send_reasons TEXT[] NOT NULL DEFAULT '{}', public_token_hash BYTEA UNIQUE,
  action_id BIGINT, created_by BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), sent_at TIMESTAMPTZ,
  UNIQUE (hotel_slug, inquiry_id, version));
-- 0952_inquiries_payments.sql
CREATE TABLE inq_channel_accounts (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  kind TEXT NOT NULL CHECK (kind IN ('meta_page','google_leadform','web_widget','pay_ngenius','pay_telr','pay_stripe','pay_tap')),
  config JSONB NOT NULL DEFAULT '{}',     -- pay_*: {fee_pct, fee_fixed_minor, currency}; web_widget: {allowed_origins[]}
  secret_enc BYTEA, webhook_key_hash BYTEA UNIQUE, status TEXT NOT NULL DEFAULT 'active',
  connected_by BIGINT, connected_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (hotel_slug, kind));
CREATE UNIQUE INDEX ON inq_channel_accounts ((config->>'page_id')) WHERE kind = 'meta_page';   -- leadgen page_id -> hotel
CREATE TABLE inq_payment_links (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  inquiry_id BIGINT NOT NULL REFERENCES inq_inquiries(id), quote_id BIGINT NOT NULL REFERENCES inq_quotes(id),
  provider TEXT NOT NULL, provider_ref TEXT, url TEXT, amount_minor BIGINT NOT NULL, currency CHAR(3) NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('deposit','full','balance')), includes_td_minor BIGINT NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('created','sent','paid','partially_paid','expired','cancelled','failed','refunded')),
  expires_at TIMESTAMPTZ, paid_at TIMESTAMPTZ, paid_amount_minor BIGINT,
  fee_minor BIGINT, fee_source TEXT CHECK (fee_source IN ('provider','settings')),
  idempotency_key TEXT NOT NULL, provider_payload JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (hotel_slug, idempotency_key), UNIQUE (provider, provider_ref));
CREATE TABLE inq_payment_events (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  account_id BIGINT NOT NULL REFERENCES inq_channel_accounts(id), provider TEXT NOT NULL, provider_event_id TEXT NOT NULL,
  payment_link_id BIGINT, payload JSONB NOT NULL, processed_at TIMESTAMPTZ, received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_event_id));           -- verified signatures only, so a forged event cannot pre-empt a real one
CREATE TABLE inq_payment_events_rejected (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  account_id BIGINT NOT NULL, provider TEXT NOT NULL, reason TEXT NOT NULL CHECK (reason IN ('bad_signature','unparseable')),
  headers_redacted JSONB, payload_sha256 TEXT NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now());
-- 0953_inquiries_outcomes.sql
CREATE TABLE inq_outcomes (inquiry_id BIGINT PRIMARY KEY REFERENCES inq_inquiries(id), hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  outcome TEXT NOT NULL CHECK (outcome IN ('verified_direct','leaked_to_ota','lost','turned_down','cancelled_after_confirm')),
  verification_path TEXT CHECK (verification_path IN ('payment_and_pms','stay_completed')),
  attribution TEXT CHECK (attribution IN ('caused','influenced')), counts_toward_verified_profit BOOL NOT NULL DEFAULT FALSE,
  verified_reservation_id BIGINT REFERENCES reservations(id), match_score NUMERIC(4,3), match_evidence JSONB,
  payment_link_id BIGINT, verified_revenue_minor BIGINT, commission_avoided_minor BIGINT, payment_fee_minor BIGINT,
  outcome_net_minor BIGINT, commission_basis JSONB,
  leaked_reservation_id BIGINT REFERENCES reservations(id), leaked_channel TEXT, leaked_commission_minor BIGINT,
  profit_guard_ref TEXT, currency CHAR(3) NOT NULL, decided_at TIMESTAMPTZ NOT NULL DEFAULT now(), action_id BIGINT);
CREATE TABLE inq_match_reviews (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  inquiry_id BIGINT NOT NULL, reservation_id BIGINT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('verify','leak')),
  score NUMERIC(4,3), evidence JSONB, status TEXT NOT NULL DEFAULT 'open', decided_by BIGINT, decided_at TIMESTAMPTZ,
  UNIQUE (inquiry_id, reservation_id, kind));
```

**Retention (`register_pii`):**
- Inquiry contact columns are redacted from `last_activity_at` onward: after 730 d where `stage NOT IN ('spam','lost')`, and after 365 d where `stage IN ('spam','lost')`. These are two registrations with `where`.
- `inq_messages.body` is kept 365 d.
- `inq_payment_links.provider_payload` and `inq_payment_events.payload` are redacted at 365 d; amounts are kept.

### 4.2 Stage machine (`service.transition`, logged in `inq_stage_events`)

```
new --first outbound (staff or approved AI; not the ack)--> replied --quote sent--> quoted --payment link sent--> payment_pending --verify--> confirmed_verified
new|replied --quote sent--> quoted      quoted --guest replies--> quoted(guest_engaged=true)
quoted (pay at hotel) --verify(stay_completed)--> confirmed_verified
any open --lost(reason)--> lost | --decline(reason)--> turned_down | --dates_changed--> future(remind_on) | --spam--> spam
new --routed to concierge or service (existing_booking, complaint)--> rerouted
future --remind date or new inbound--> new ;  lost --new inbound within 30 d--> new (reopen)
confirmed_verified --PMS cancel or refund--> outcome cancelled_after_confirm (stage unchanged; core reverse_verification)
```

- **Reopen rule.** Intake (4.3) and dedupe (4.4) apply one rule: a new inbound from the same contact hmac reopens a `lost` or `future` inquiry (to `new`) when its `last_activity_at` is within 30 d; otherwise it creates a new inquiry. `spam`, `turned_down`, `rerouted` and `confirmed_verified` never reopen.
- **Probabilities.** Recalibrated stage probabilities are stored in `inq_stage_probabilities` (4.1, 4.5).

### 4.3 Intake adapters (`intake/`)

**Consent basis at creation** (the column is NOT NULL):
- `whatsapp` and `email`: `pre_contract_notice`.
- `meta_leadgen` and `google_leadform`: `pre_contract_notice` (the advertiser's lead form carries its own notice).
- `web_form`: `express_web_form`.
- `phone_desk` and `concierge`: `staff_logged`.

**Acknowledgement (all channels except `phone_desk`, `concierge` and staff-logged).**
- The first inbound triggers `inquiries.ack` (dial `auto`, fixed text with no AI). Inside the window it is free-form WhatsApp (email while the WABA is not billing-ready, 3.5); otherwise it is an email reply.
- The text is: "Thank you for contacting {hotel}. A member of our team will reply shortly. How we handle your details: {privacy_url}", in EN and AR.
- It records `notice_sent_at`. It sets `consent_basis='pre_contract_notice'` only when the current value is not `express_web_form`.
- The ack does not stop the SLA clock.
- From 2026-10-01 the ack is a billable service message.

**WhatsApp** (`@on_whatsapp(audience='guest', priority=20, match=referral present or not guests.api.is_known_guest_sender(phone_hmac))`):
- It appends to an open inquiry from the same sender hmac seen in the last 30 d, reopens one per the 4.2 reopen rule, or creates a new one.
- It stores the `referral` fields `source_url`, `source_id`, `source_type`, `headline`, `body`, `media_type` and `ctwa_clid` (inquiry-crm.md §3).
- `wa_window_expires_at` = inbound time + 24 h. For CTWA leads, answering within 24 h opens a 72 h free window (Android and iOS only), recorded as `ctwa_free_window_expires_at`.
- Known in-house guests stay with the concierge, whose tool may call `inquiries.api.create_inquiry`.
- **Conversions API is not built** in this lite version (PLAN: lightweight Inquiry Guard). `ctwa_clid` is still stored. CAPI is listed under "next" in 7.7, with its consent precondition.

**Email** (`@on_inbound_email(purpose='inquiries')`):
- Addresses follow F4: `<alias>+inq@in.clefs.world`.
- Threads join through In-Reply-To/References, or through `+inq-<ref>`.
- Quoted history and signatures are stripped.
- OTA notification senders (booking.com, agoda.com, expediapartnercentral.com) and `Auto-Submitted` mail are ignored.
- Replies use `reply_to=<alias>+inq-<ref>@in.clefs.world`.

**Web form** (`POST /public/inquiries/{slug}`):
- **Body:** `{name, email, phone, check_in, check_out, rooms, adults, children, message, language, consent_contact:true (required), consent_transfer_abroad:true (required, separate), whatsapp_optin (optional), consent_marketing_whatsapp, consent_marketing_email (optional, separate, unticked), utm{}, gclid, page_url, widget_key, hp, rendered_at}`.
- **Consents:** each one is recorded with `record_consent` and its text version, and the inquiry gets `consent_basis='express_web_form'`.
- **Checks:**
  - `ratelimit.hit` 5 per hour per IP hash and 200 per day per hotel
  - an empty honeypot
  - at least 3 s to fill in
  - `widget_key` matches the hotel's `web_widget` account
- **Response:** 202 with no inquiry detail.
- **Consent lines (EN/AR):**
  - "{hotel} may contact me about this request." (required)
  - "I agree my request may be processed outside the UAE by {hotel}'s service providers (Clefs and its sub-processors)." (required, separate, with the list link)
  - "{hotel} may reply to me on WhatsApp."
  - "Send me offers from {hotel} by WhatsApp" and "... by email"
- **Widget:**
  - `client/app/w/inquiry.js/route.ts` returns JS that injects an iframe at `/w/inquiry/{slug}?k=&lang=`, forwards the host page's `utm_*` and `gclid`, and resizes itself through `postMessage`.
  - `client/app/w/inquiry/[slug]/route.ts` returns self-contained HTML with `Content-Security-Policy: frame-ancestors 'self' <allowed_origins>` and `X-Robots-Tag: noindex`, and posts same-origin through `/api/clefs` (A14).

**Meta leadgen:**
- **Connecting a page** (lightweight, no OAuth): `POST /hotels/{slug}/inquiries/channels {kind:'meta_page', page_id, page_access_token}` (`inquiries.manage`). The token is sealed into `secret_enc`. On save the server calls `POST /{WA_API_VERSION}/{page_id}/subscribed_apps?subscribed_fields=leadgen` with that token and stores the result in `status`. `page_id` is unique across hotels (4.1 index), which is how a webhook finds its hotel.
- `GET /webhooks/inquiries/meta-leadgen` checks `hub.verify_token` against `META_LEADGEN_VERIFY_TOKEN` and echoes `hub.challenge`.
- `POST` checks `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with `META_APP_SECRET`, compared in constant time), maps `page_id` to the hotel, dedupes on `leadgen_id` and returns 200.
- The task `inquiries.meta_lead_fetch` calls `GET /{WA_API_VERSION}/{leadgen_id}?fields=field_data,created_time,ad_id,form_id,campaign_id` with the sealed page token and maps `field_data[{name,values}]`.
- It retries with backoff. After 72 h of failure it raises `inq.lead_fetch_failed`, because leads can only be retrieved for 90 days.
- Permissions: `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement`, `ads_management`, plus App Review.
- A lead is replied to by email, or on WhatsApp only when the form carried a WhatsApp opt-in question.

**Google Ads lead form** (`POST /webhooks/inquiries/google-leadform/{account_key}`):
- It compares `google_key` in constant time against the sealed key.
- It dedupes on `lead_id` and returns 200 for duplicates too.
- A lead with `is_test=true` is stored flagged and excluded from metrics.
- It maps `user_column_data[{column_id,string_value}]` (FULL_NAME, EMAIL, PHONE_NUMBER and custom questions), and keeps `gcl_id`, `campaign_id` and `form_id`.
- It returns 5xx only on a transient DB error, so Google retries.

**Phone desk:** `POST /hotels/{slug}/inquiries` with `source='phone_desk'` and `consent_basis='staff_logged'`, backed by the console form `/inquiries/new`. Service's PhoneEntryConsole links to `/app/{slug}/inquiries/new?source=phone_desk&room=`; that is a lane A edit (7.5).

### 4.4 Extraction and dedupe

The `inquiries.extract` task runs `ai.structured(store=False)`.
- Phones and emails are regex-redacted before the model sees the text, because contact details come from channel metadata.
- **`ai_on_unconsented_inbound`** (InquirySettings) defaults to **false** until counsel signs off (7.7). While it is false, every inquiry whose `consent_basis` is not `express_web_form` (so `pre_contract_notice` and `staff_logged`: WhatsApp, email, Meta and Google leads, phone desk and concierge) gets only the deterministic date and pax parser, and a person reads it. Under the Canon, PDPL has no legitimate-interest basis and pilots rely on explicit `transfer_abroad` consent; only the web form collects it.

```python
class InquiryExtraction(BaseModel):
    language: Literal['en','ar','other']; intent: Literal['room_booking','group_booking','rfp','long_stay','event_space','info_only','existing_booking','complaint','spam','vendor','other']
    segment: Literal['fit','corporate','group','long_stay','event','unknown']; is_rfp: bool
    check_in: date | None; check_out: date | None; nights: int | None; dates_flexible: bool; date_text: str | None
    rooms: int | None; adults: int | None; children: int | None; room_type_hint: str | None
    budget_amount: Decimal | None; budget_currency: str | None; budget_per: Literal['night','stay','month','unknown']
    company: str | None; event_name: str | None; special_requests: list[str]
    missing: list[Literal['dates','guests','rooms']]; confidence: float
    evidence: list[FieldQuote]          # {field, quote}: verbatim span for every extracted field
```

- **Relative dates** resolve against hotel-local today. Event names ("GITEX week") resolve through `demand.api.find_event`.
- **Validation:**
  - `check_out > check_in`
  - nights of 365 or fewer
  - rooms of 200 or fewer
  - segment: 30 or more nights is `long_stay`; 9 or more rooms is `group`; otherwise the model's value.
- **Routing by intent:**
  - `existing_booking` or `complaint`:
    - If the sender maps to an in-house reservation, it calls `service.api.create_request(room=...)`.
    - Otherwise it calls `service.api.create_followup(kind='guest_contact', owner_role='front_desk', source='inquiries', ...)`.
    - Either way the stage becomes `rerouted`.
  - `vendor` becomes `spam`.
- **Dedupe:**
  - An open inquiry (not terminal, with activity in the last 30 d) that has the same phone or email hmac is merged: the message is appended and new fields are filled in. A `lost` or `future` one is reopened per the 4.2 rule.
  - A matching name plus overlapping dates within 72 h, with no contact, produces a merge suggestion in the drawer.

### 4.5 SLAs and money at risk

**First-response SLA** (minutes, `InquirySettings.sla`):

| Segment | Minutes |
|---|---|
| fit / unknown | 15 |
| corporate | 60 |
| long_stay | 60 |
| group | 240 |
| event | 240 |
| rfp | 1440 |

- **Business hours.** The clock counts only business hours, 08:00-22:00 every day by default (A3). Outside those hours, the due time is opening time + SLA. Expected values: FIT at 21:59 is due 08:14 the next day; FIT at 22:01 is due 08:15.
- **Timer.** The timer is `enqueue('inquiries.sla_check', run_at=due, dedupe_key=f'inq.sla:{id}:{due_epoch}')`. Extraction can change the segment after the timer is set, so `sla_check` re-reads `first_response_due_at`:
  - If the due time moved, it re-enqueues with the new key and exits.
  - If there is still no outbound at the due time, it sets `sla_breached` and upserts `inq.unanswered`.
- **Response-time metric.** The first qualifying outbound message sets `first_response_at`, `first_response_by` and `first_response_business_min`.

**Money at risk** = `room_nights × expected_ADR × stage_probability` (PLAN). It is labelled "est." in the UI and the brief.
- `room_nights` = rooms × nights. Missing values use A6 and are marked estimated, with confidence × 0.6.
- `expected_ADR` comes from `demand.api.expected_adr`, falling back to the same-day-of-week ADR over the trailing 28 days in `core/stays`.
- **Assumed starting probabilities:** new 0.10, replied 0.15 (A5), quoted 0.30, quoted and engaged 0.50, payment_pending 0.80, future 0.05.
- **Recalibration.** Once there are 30 or more closed inquiries **and** at least 90 d of history (inquiry-crm.md §5), the probabilities are recalibrated monthly: `p_s = (won_s + 20·prior_s)/(closed_s + 20)`, written to `inq_stage_probabilities`.

### 4.6 Quote builder (`quotes.py`)

1. **Rate per night.** For each night and room type, `g = demand.api.price_guardrails(cur, hotel_slug, stay_date, room_type_code)`. The rate is `g.recommended` or `g.bar`, clamped to [floor, ceiling]. Values are Decimal strings, converted to minor units.
   - Group and long-stay policies from settings create `discount_pct > 0`, so `below_bar = true`. For example: group 10% off BAR for 9 or more rooms; long stay 15% off for 30 or more nights.
   - Staff edits are bounded to [floor, ceiling]. Going below the floor needs `inquiries.approve` plus a reason.
   - **No guardrails.** The frozen `demand.price_guardrails` stub returns None, and lane D ships in the same wave as lane I. When it returns None for any night, that night's rate is left empty with `source='manual'` and staff must enter it (no floor or ceiling clamp; sending needs `inquiries.approve`). `below_bar` is unknown and treated as true, `auto_send_eligible=false` with reason `no_guardrails`, and the editor shows "No Demand guardrails: enter the rate". Inquiry Guard has no other BAR source: `bar_calendar` is Demand's table.
2. **Taxes.** `core.tax.compose_stay(lines=[StayLine per night and room type])` (D3) produces the lines: room, 10% SC, 7% MF, 5% VAT, and the "Tourism Dirham fee". A group with mixed room types is one call.
   - TD is charged per unit (per bedroom when the fee rule says so), capped at 30 consecutive nights, and sits outside VAT.
   - Fee rules are date-effective. `fee_notices` renders the Dubai 2026 exemption banner on the quote whenever an unverified rule intersects the stay (Canon).
3. **Validity:**
   - FIT 48 h, corporate 72 h, group 7 d.
   - 24 h if any night has `demand_index` of 80 or more (A11).
   - Never later than check_in − 1 d.
4. **Deposit** (settings):
   - FIT: first night.
   - Long stay: first 30 nights.
   - Group: 25% at signing, then 50% and 75% cumulative as arrival nears, configurable (A12, "Dubai norms UNVERIFIED").
   - A deposit that includes TD shows it on its own line as "refundable if you do not check in". A Hotel Room Night starts at check-in, so no-shows owe no TD.
5. **Cancellation text** comes from settings, in EN and AR.
6. **Group terms** (A12, labelled "Dubai norms UNVERIFIED" in settings):
   - cutoff = check_in − 30 d
   - attrition 80% of the block
   - rooming list due at cutoff
   - `comp_snapshot` = `demand.api.comp_rates_snapshot`
7. **Output.** An HTML quote page at `/q/[token]` (client, noindex, EN/AR), backed by `GET /public/inquiries/quotes/{token}` and `POST .../accept`.
   - Inside the 24 h window the quote goes out as free-form WhatsApp.
   - Outside it, the template `inq_quote_ready_v1` is used (UTILITY, budgeted as marketing), with a URL button:
     - EN: "Your quote {{1}} for {{2}} is ready: total {{3}}, valid until {{4}}. Reply here with any questions."
     - AR: "عرض السعر {{1}} لـ {{2}} جاهز: الإجمالي {{3}}، صالح حتى {{4}}. راسلنا هنا لأي استفسار."
   - **Template storage and approval.** Both inquiry templates live in `inq_templates` (4.1), seeded per hotel, approved by the owner in inquiries settings and submitted through `brief.api.submit_template`. Outside the window, with no owner-approved and Meta-`APPROVED` template, the quote falls back to email; with no email address the action ends `failed` with reason `template_missing`.
   - **Template category gate.** Meta may file these templates as MARKETING (mixed content is marketing by default, uae/whatsapp.md §3). Before each template send the executor reads `brief.api.wa_template_info(...).category`. When it is MARKETING or unknown (including the None stub), the 3.5 marketing rules apply: send only in [09:00, 18:00) Asia/Dubai (deferred to the next window start, else email); no WhatsApp to `+1` numbers (email instead); on 131049, defer at least 24 h and retry once, then email.
   - Email is also available.
   - No PDF is generated (0.2).
8. **Accept.** `POST /public/inquiries/quotes/{token}/accept` sets the quote to `accepted` and the inquiry's `guest_engaged=true`, and creates an `inquiries.send_payment_link` action for the first deposit milestone (the dial decides whether it goes out). Later group milestones are created manually from the drawer (lightweight). The stage moves to `payment_pending` only when a link is sent (4.2).

**Golden values** (Dubai, 3★, convention A, TD AED 10 per unit per night, one rounding per component). These goldens assume the exemption toggle is off, except the last row.

| Case | Room | SC | MF | VAT | TD | Total |
|---|---|---|---|---|---|---|
| 1 room × 2 nights × AED 400 | 800.00 | 80.00 | 61.60 | 47.08 | 20.00 | 1,008.68 |
| 1 room × 45 nights × AED 250 | 11,250.00 | 1,125.00 | 866.25 | 662.06 | 300.00 (30 nights, not 450) | 14,203.31 |
| 12 rooms × 3 nights × AED 350 | 12,600.00 | 1,260.00 | 970.20 | 741.51 | 360.00 | 15,931.71 |
| 1 FAM_2BR (2 bedrooms, per-bedroom rule) × 2 nights × AED 600 | 1,200.00 | 120.00 | 92.40 | 70.62 | 40.00 | 1,523.02 |
| 1 room × 2 nights × AED 400, exemption toggle on (factor 1.155) | 800.00 | 80.00 | 0.00 | 44.00 | 0.00 | 924.00 |
| Mixed, one call: 8 STD × 2 nights × AED 400 + 2 FAM_2BR (per-bedroom rule) × 2 nights × AED 600 | 8,800.00 | 880.00 | 677.60 | 517.88 | 240.00 (12 units) | 11,115.48 |

Mixed-row TD: 8 STD units × 2 nights × 10 = 160.00, plus 2 FAM_2BR rooms × 2 bedrooms = 4 units × 2 nights × 10 = 80.00. The golden equals 8 × the first row plus 2 × the FAM_2BR row, because every component is exact to the fil.

### 4.7 Auto-send rule

`auto_send_eligible` holds only when all of the following are true:
- every night's rate is at or above the BAR for that date and room type, and the BAR is at most 7 days old
- there is no discount line
- `guardrails.source` is not `fallback`, and no night lacks guardrails (`price_guardrails` returned a value)
- the segment, rooms, nights and total are all within limits
- `extraction_confidence ≥ 0.8` and `missing = []`

The limits schema for `inquiries.send_quote` has these defaults: `{"segments":["fit","corporate"], "max_rooms":2, "max_nights":7, "max_total_minor":500000, "max_bar_age_days":7}`.

The quote then goes through `create_action('inquiries.send_quote')`:
- At `auto_within_limits`, the `limit_checker` re-checks the same rule and auto-approves, and the executor sends.
- Anything else is `proposed`. A group quote raises `inq.quote_awaiting_approval` for one-tap approval in the brief.
- `ActionType.max_level='auto_within_limits'`, so a quote below BAR can never go out automatically, even if the dial is hand-set to `auto`. This follows the Canon rule that rate changes always need approval, and the zz-critique.md §2 resolution.
- The payment link for an auto-sent quote follows the same rule.

### 4.8 Payment links (`payments/`)

```python
class PaymentLinkRequest(BaseModel): amount_minor: int; currency: str; description: str; reference: str; expires_at: datetime
    customer_name: str | None; customer_email: str | None; customer_phone: str | None; language: str; return_url: str; idempotency_key: str
class PaymentEvent(BaseModel): provider: str; provider_event_id: str; provider_ref: str
    kind: Literal['paid','partially_paid','failed','expired','cancelled','refunded']; amount_minor: int; currency: str
    occurred_at: datetime; fee_minor: int | None = None
class PaymentProvider(Protocol):
    key: str
    def create_link(self, acct, req: PaymentLinkRequest) -> PaymentLink
    def cancel_link(self, acct, provider_ref: str) -> None
    def fetch_status(self, acct, provider_ref: str) -> PaymentEvent | None     # authoritative
    def verify_webhook(self, acct, headers, raw: bytes) -> bool
    def parse_webhook(self, acct, headers, raw: bytes) -> list[PaymentEvent]
```

| Adapter | Create | Status and webhook | Fee |
|---|---|---|---|
| N-Genius | `POST {base}/invoices/outlets/{outletRef}/invoice`, `Content-Type: application/vnd.ni-invoice.v1+json`, fields `invoiceExpiryDate`, `total`, `items`, `paymentAttempts`, `skipInvoiceCreatedEmailNotification`. The link is in `_links.payment.href` (inquiry-crm.md). The access-token flow is UNVERIFIED | Webhook with a header secret configured in the portal (UNVERIFIED), then re-fetch the order | Bank-negotiated (UNVERIFIED); taken from settings |
| Telr | Hosted payment-link API, UNVERIFIED (inquiry-crm.md has pricing only) | Poll status; the advice URL is only a hint | Settings default 2.25% + AED 0.50 (Medium plan, inquiry-crm.md) |
| Stripe | Payment Links with `metadata{inquiry_ref, quote_version}`, deactivated at `valid_until`. UNVERIFIED; check Stripe's docs at build | `Stripe-Signature` HMAC with a 5-minute tolerance (UNVERIFIED), then fetch the session | Settings default 2.9% + AED 1 (inquiry-crm.md) |
| Tap | `POST https://api.tap.company/v2/invoices` with `due`, `expiry`, `notifications.channels`, `post.url` (inquiry-crm.md) | Signature header and `GET /v2/invoices/{id}` UNVERIFIED | Settings |
| manual | Staff paste a link, or record a bank transfer with evidence | n/a | 0 |

- **Fees.** `fee_minor` comes from `fetch_status` when the provider reports it (`fee_source='provider'`). Otherwise it is `amount × fee_pct + fee_fixed_minor` from the account config (`fee_source='settings'`).
- **Webhook route:** `POST /webhooks/inquiries/payments/{provider}/{account_key}`.
  1. Look up the account by `webhook_key_hash`. An unknown account returns 404 and stores nothing.
  2. If the signature is bad, insert into `inq_payment_events_rejected` and return 401. No state changes.
  3. Otherwise insert into `inq_payment_events` with `ON CONFLICT DO NOTHING`. When the provider sends no id, `provider_event_id` is the sha256 of the body.
  4. Return 200 immediately.
  5. The task `inquiries.payment_event_process` calls `fetch_status`. Only that result changes `inq_payment_links`.
- **Reconciliation.** `inquiries.payment_reconcile` runs hourly and polls links in `created` or `sent`.
- **After payment.** A paid event (deposit or full, per the quote's schedule) moves the stage to `payment_pending` if it is earlier, and enqueues verification.
- **Paid but not in the PMS.** A payment with no PMS reservation after 48 h:
  - creates the human task `inquiries.create_pms_reservation` for the front desk ("create the reservation in the PMS"), and
  - upserts `inq.paid_not_in_pms` (service_risk, `impact_minor` = the paid amount). Its cadence is `weekly`, raised to `daily` only when check-in is within 48 h (7.3).

### 4.9 Verification and OTA-leak detection

**When `inquiries.verify` runs:** hourly, when a payment is paid, and on the stay events `created` (direct channels) and `cancelled`.

**Candidates.** It scores `core/stays` reservations that meet both conditions:
- `channel_code ∈ {DIRECT_WEB, DIRECT_PHONE, WALKIN, CORPORATE}`
- `booked_at ∈ [inquiry.created_at − 1 d, anchor + 14 d]` (A7), where the anchor is the payment time, else the quote-sent time, else `last_outbound_at`, else `created_at`

**Score:**
- identity hmac exact match (`reservation_contacts` with `pms_direct` provenance): 0.50
- name token-sort Jaro-Winkler ≥ 0.88 (reusing `core.stays.match.name_similarity`): 0.20
- check_in exact 0.20, or ±1 d 0.10
- nights equal: 0.05
- amount within 5% of the quote or payment: 0.05

**Thresholds.** A score of 0.75 or more auto-verifies. A score from 0.55 to 0.75 opens `inq_match_reviews`.

**Paths:**
- `payment_and_pms`: a paid link plus a match.
- `stay_completed`: no link, and the matched reservation reaches IN_HOUSE or CHECKED_OUT.

**Ledger attachment** (exactly one action carries the outcome, so verified profit is never double-counted):
- The outcome attaches to the latest executed `inquiries.send_payment_link`, else the latest executed `inquiries.send_quote`.
- Every other send_quote or send_payment_link action of the inquiry verifies with `outcome_minor=0` and `counts_toward_verified_profit=false`.
- With neither, the job creates `inquiries.direct_booking`. Its ActionType has a no-op executor and dial `auto`, so `create_action(actor=SYSTEM)` system-approves it, the executor records the reservation ref, `mark_done` follows, and the verifier attaches the outcome. It follows the Canon 7 path and logs every transition in `action_events`.
- **Schedule.** Both send types use `verify_every=1 d`, and `actions.verify_until` is set at creation to the inquiry's `check_out + 7 d`, so a `stay_completed` match months later still finds a live action.
- A test asserts that an inquiry with two quote versions and two payment links counts its outcome once.

**The outcome records:**
- `verified_revenue_minor` = the reservation's room revenue.
- `commission_avoided_minor` = `profit_guard.api.expected_commission(cur, slug, ota_code=reference_ota, breakdown=core.stays.reservation_breakdown(cur, reservation_id), on=check_in)['commission_minor']`.
  - `reference_ota` is the OTA with the highest share over the trailing 90 d (`reference_ota(cur, slug, as_of=today)[0]`).
  - Booking's base is room + SC + VAT. MF and TD are excluded only when configured as charges (02 §3.2; uae/zz-critique.md C1: the service charge is commissionable per Booking's help page). The Canon's OTA rule says "room price plus VAT only", so this base is pending Canon amendment 0.7 K2; if K2 is not applied, the goldens below become 127.06 gross, 114.45 net and a leak of 166.77.
- `payment_fee_minor` = Σ `fee_minor` of the paid links.
- `outcome_net_minor = commission_avoided_minor − payment_fee_minor`. This is what `record_verification` passes as `outcome_minor`.

**Counting rule.** An outcome counts toward verified profit (`attribution='caused'`) only if a Clefs-sent reply, quote or payment link exists for the inquiry. The auto-ack does not qualify. Otherwise it is `influenced`, with `counts_toward_verified_profit=false`.

**Reversals.** A later refund, or a PMS cancellation, calls `reverse_verification` and records `cancelled_after_confirm`.

**Golden.** The 2-night FIT at 1,008.68 with Booking at 15%:
- gross commission avoided = 0.15 × 927.08 = **AED 139.06**
- a 504.34 first-night deposit link at the demo N-Genius fee setting of 2.5% (a demo assumption) costs 12.61
- **net = AED 126.45**

**`inquiries.leak_match`** runs daily at 05:00 and is internal only (Canon 19).
- **Candidates:** open, lost or future inquiries created in the last 30 d, against OTA reservations that meet all of these:
  - `booked_at ∈ [inquiry.created_at, +30 d]`
  - check_in within ±3 d of the requested dates
  - `now ≤ purge_after` for their contacts
- **Match:**
  - by identity hmac only, against `reservation_contacts` whose provenance is not `ota_alias` (an alias email can never equal a real one), or
  - by name ≥ 0.90 plus exact dates, which goes to review.
- **On a match:**
  - The outcome is `leaked_to_ota`, and `lost_reason='BOOKED_VIA_OTA'` is set automatically.
  - The job calls `profit_guard.api.record_ota_leak(..., commission_minor=None, currency=None)` (None = the hotel's currency). Profit Guard fills the commission from its own matched statement line, falling back to `expected_commission`, and returns the amount and basis.
  - **Golden:** Booking, 3 nights × AED 350, gives a base of 1,216.79 and a leak of **AED 182.52**.
- **Output restrictions:** results are never sent to the guest, never exported, never used for audiences or public audits, and masked for roles without `guests.view`.

### 4.10 Lost reasons and pipeline

**Lost:**
- `PRICE_VS_COMP`: stores the comp snapshot.
- `NO_AVAILABILITY`
- `SLOW_RESPONSE`: set automatically when the SLA was breached.
- `BOOKED_VIA_OTA`: set automatically.
- `BOOKED_COMPETITOR`
- `GUEST_UNRESPONSIVE`: set automatically.
  - Follow-ups are proposed as `inquiries.follow_up` at quote-sent + 24 h and + 72 h, while the quote is still valid. They use free-form text inside the window, or otherwise `inq_quote_followup_v1` (UTILITY, budgeted as marketing; stored and gated like `inq_quote_ready_v1`, 4.6). A "still available, reply to confirm" nudge reads like a reminder, so the template send is **always** timed inside [09:00, 18:00) Asia/Dubai (moved to the next window start) and follows the 4.6 category gate (+1 numbers, 131049):
    - EN: "Your quote {{1}} for {{2}} is still available until {{3}}. Reply here with any questions or to confirm."
    - AR: "عرض السعر {{1}} لـ {{2}} لا يزال متاحاً حتى {{3}}. راسلنا هنا لأي استفسار أو للتأكيد."
  - The reason is set when the quote expires after 2 follow-ups with no inbound, or 7 d after expiry with no inbound when no follow-up was approved.
- `PAYMENT_LINK_EXPIRED`: set automatically.
- `POLICY_TERMS`
- `DATES_CHANGED`: moves the inquiry to `future` with a remind date.
- `PRODUCT_LOCATION_MISMATCH`
- `TRIP_CANCELLED`
- `SPAM_DUPLICATE`

**Turned down:** `NO_AVAILABILITY`, `DISPLACEMENT`, `POLICY`, `BELOW_FLOOR`.

**Weighted pipeline** = Σ of money-at-risk values over open inquiries with `check_in ≥ today`, grouped by check-in month and segment. It is labelled "est.". There is no kanban.

### 4.11 Signals, metrics, jobs, API, console

**Signals:**

| Kind | Section | Cadence | Notes |
|---|---|---|---|
| `inq.unanswered` | money_available | daily | Impact = money at risk (est.); due = SLA; action `inquiries.respond`. Evidence includes, for example, "ad lead: free window closes in 1h40m" |
| `inq.quote_awaiting_approval` | money_available | daily only while the guest has received no substantive reply and the first-response SLA is breached (Canon 18's unanswered-inquiry case); otherwise weekly | The approval card itself still reaches the brief through the action's `surface='brief'` (1.1) |
| `inq.paid_not_in_pms` | service_risk | weekly; daily when check-in ≤ 48 h | Impact = paid amount |
| `inq.leaked_to_ota` | money_lost | weekly | |
| `inq.pipeline_weekly` | verified | weekly | "9 inquiries (AED 21k potential, est.); last week 4 verified bookings, AED 11.2k, AED 1.7k commission avoided (net of payment fees)" |
| `inq.lead_fetch_failed` | service_risk | weekly | Plus a health banner in the settings |

The brief line takes the form "2 unanswered > 2 h, AED 2.5k at risk (est.)".

**Metrics:**
- `inq.inquiries_count` (dimensions: source, segment, language, stage, month)
- `inq.first_response_p50_min` and `inq.first_response_p90_min` (`percentile_cont` over `first_response_business_min`; dimensions: source, segment)
- `inq.sla_breach_rate`
- `inq.conversion_rate` (verified / closed)
- `inq.verified_revenue`
- `inq.commission_avoided` (net)
- `inq.lost_reason_mix`
- `inq.leak_share` (leaked / (verified + leaked + lost))
- `inq.weighted_pipeline`
- `inq.money_at_risk`
- Money metrics are registered with `Metric(unit='money', currency='hotel')` and carry no currency in their keys (legacy tenants use INR).

**Jobs:**
- `inquiries.extract`, `.sla_check`, `.meta_lead_fetch` and `.payment_event_process` (tasks)
- `.payment_reconcile` (`every(minutes=60)`; `every` is keyword-only, 01 §E4)
- `.verify` (`every(minutes=60)`, plus the stay-event consumer `inquiries.verify_on_stay`)
- `.leak_match` (daily 05:00)
- `.quote_expiry` (`every(minutes=15)`; also proposes follow-ups)
- `.future_remind` (09:30)
- `.pipeline_weekly` (`weekly_at(1, "06:30")`)
- `.recalibrate` (`monthly_at(1, "04:00")`)
- `.purge` (daily)

**Console routes (`/hotels/{slug}/inquiries`):**
- `GET` with filters; `POST` to log an inquiry
- `GET|PATCH /{id}`
- `POST /{id}/messages`: a staff-typed reply. It creates `inquiries.respond` through `create_action`, is decided at once as an approval by that user (`decided_via='console'`) and executes immediately; the dial governs only AI drafts. Either path sets `first_response_*` and counts as a Clefs-sent reply (4.9)
- `POST /{id}/draft-reply`: an AI draft, which becomes an `inquiries.respond` action governed by the dial. The content lint rejects prices, which come only from quotes
- `POST /{id}/quotes`, `PUT /{id}/quotes/{v}`, `POST /{id}/quotes/{v}/send`
- `POST /{id}/payment-links {quote_version, purpose, amount_minor?}` (creates an `inquiries.send_payment_link` action), `POST /payment-links/{lid}/cancel`, `POST /payment-links/{lid}/mark-paid {evidence_file_id}` (manual provider only)
- `POST /{id}/lost|decline|future`
- `POST /match-reviews/{id}/confirm|reject`
- `GET /pipeline`
- `GET|PUT /settings`
- CRUD `/channels`
- `GET /widget` (embed code), `POST /widget/rotate`

**UI (`(console)/inquiries/`):**
- **List:**
  - Filters: stage, segment, source, SLA state, dates.
  - Columns: ref, received, source, segment, dates, room-nights, AED at risk (est.), SLA `CountdownChip`, stage.
- **Drawer:** the thread, an extraction panel with evidence quotes and editing, a reply composer that knows about the window, the quote editor, a payment-link panel, the lost-reason picker, and match review.
- **Quote editor:** a per-night grid with the BAR/floor/ceiling band, live tax lines with fee notices, the terms, the auto-send reasons, and "Send" or "Request approval".
- `new/`, `settings/` (channels, payments, widget, SLAs, `ai_on_unconsented_inbound`, and approval of the `inq_templates` rows).
- **Empty state** (no channel connected and no inquiries): a connect-channel checklist (WhatsApp, email alias, Meta page, Google lead form, payment provider) plus the widget embed code.

### 4.12 Tests and edge cases

**Tests:**
- SLA with a frozen clock: FIT at 21:59 is due 08:14; at 22:01 it is due 08:15. A segment change after extraction re-enqueues the timer. The ack does not stop the clock.
- The tax goldens, including the TD cap, the 2BR per-bedroom row, the exemption-on row and the mixed-type row in one call.
- `price_guardrails` returning None (the stub): the rate must be entered, `source='manual'`, `auto_send_eligible=false` with `no_guardrails`.
- Template gate: with category MARKETING or unknown, a `+1` number gets email, a send due at 17:59 goes and one due at 18:00 moves to 09:00, and 131049 defers 24 h, retries once, then emails.
- A staff-typed reply creates and executes `inquiries.respond` once, sets `first_response_at`, and makes a later verification `caused`.
- Quote accept creates the first-deposit `send_payment_link` action; `mark-paid` works only for the manual provider.
- An inquiry with two quote versions and two links counts its verified outcome once.
- Reopen: a new inbound 29 d after `lost` reopens; at 31 d, or after `spam`, it creates a new inquiry.
- **Auto-send:**
  - a quote at BAR sends automatically
  - 1 fil below BAR, or any discount, goes to approval
  - a hand-set dial of `auto` still cannot exceed the ceiling
  - the limits defaults apply
- **Payment webhook:**
  - a duplicate event
  - a replay
  - a bad signature returns 401 and is stored as rejected
  - a forged event with a real event's id does not block the real one
  - state changes only after `fetch_status`
  - fee capture from the provider and from settings
- **Verification:**
  - the plants and the review-band cases
  - the golden gross 139.06 and net 126.45
  - `stay_completed` with no Clefs outbound is `influenced` and not counted
  - a refund reverses the verification
- **Leak matching:**
  - a booking at +31 d is not matched
  - a purged contact is not matched
  - an email-alias reservation is not matched on email
  - no outbound message is produced
- Google `lead_id` duplicate and `is_test`.
- The Meta signature and the fetch mapping.
- The CTWA referral is captured.
- **Web form:** rate limit, honeypot, both required consents enforced separately.
- Arabic extraction on the fake AI.
- `ai_on_unconsented_inbound=false` (the default) makes no LLM call for `pre_contract_notice` and `staff_logged` inquiries (WhatsApp, `meta_leadgen`, `phone_desk`), and still calls it for `express_web_form`.
- The lost-reason automation, including follow-ups.
- Pipeline maths.

**Edge cases:**
- A USD budget, converted at the 3.6725 peg.
- A partial payment.
- BAR changing after a quote is sent: the quote is honoured until `valid_until`.
- Hijri-relative dates such as "after Eid", treated as flexible.
- A reservation made before payment.
- An OTA guest asking on WhatsApp to change dates is rerouted.
- A group with mixed room types (one `compose_stay` call, 4.6 golden).

---

## 5. Visibility (`modules/visibility/`, lane V)

### 5.1 DDL (0900-0906)

```sql
-- 0900_visibility_profile.sql
CREATE TABLE vis_profile (hotel_slug TEXT PRIMARY KEY REFERENCES hotels(slug), place_id TEXT, gbp_location TEXT,
  gh_property_token TEXT, website_url TEXT, booking_engine_host TEXT, brand_aliases TEXT[], rank_keywords TEXT[],
  area TEXT, landmarks TEXT[],                                  -- template inputs for rank keywords and prompts (5.3, 5.5)
  grid_size SMALLINT NOT NULL DEFAULT 7, grid_spacing_m INT NOT NULL DEFAULT 800, ai_markets JSONB NOT NULL DEFAULT '[]',
  monthly_budget_minor BIGINT NOT NULL DEFAULT 3000, budget_currency CHAR(3) NOT NULL DEFAULT 'USD',
  engines_enabled TEXT[] NOT NULL DEFAULT '{gemini_web_search,serpapi_ai_mode}', serpapi_enabled BOOL NOT NULL DEFAULT TRUE,
  updated_by BIGINT, updated_at TIMESTAMPTZ);                  -- Google Hotels area queries come from demand.api.area_queries
CREATE TABLE vis_competitors (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), comp_id TEXT NOT NULL, name TEXT,
  place_id TEXT, property_token TEXT, aliases TEXT[], PRIMARY KEY (hotel_slug, comp_id));   -- synced from demand.api.comp_set()
-- 0901_visibility_google.sql
CREATE TABLE vis_google_hotels_obs (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  observed_at TIMESTAMPTZ NOT NULL, query TEXT, check_in DATE, check_out DATE, pages_fetched SMALLINT, organic_rank SMALLINT,
  organic_total SMALLINT, sponsored_count SMALLINT, official_present BOOL, official_price_minor BIGINT, min_other_price_minor BIGINT,
  min_other_source TEXT, min_other_channel TEXT, price_basis TEXT, parity_gap_minor BIGINT, parity_gap_pct NUMERIC(7,3),
  prices JSONB, currency CHAR(3) NOT NULL, serp_cache_key TEXT);
-- 0902_visibility_rank.sql
CREATE TABLE vis_rank_runs (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug), run_at TIMESTAMPTZ NOT NULL,
  keyword TEXT NOT NULL, source TEXT NOT NULL CHECK (source IN ('places_ids','serpapi_maps')), points SMALLINT, found SMALLINT,
  arp NUMERIC(5,2), atrp NUMERIC(5,2), solv NUMERIC(5,4), comp_metrics JSONB, calls INT);
CREATE TABLE vis_rank_points (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), run_id BIGINT NOT NULL REFERENCES vis_rank_runs(id),
  idx SMALLINT NOT NULL, lat NUMERIC(9,6), lng NUMERIC(9,6), rank SMALLINT, comp_ranks JSONB, PRIMARY KEY (run_id, idx));
CREATE TABLE vis_rank_calibration (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), month DATE, keyword TEXT,
  spearman NUMERIC(4,3), top3_agreement NUMERIC(4,3), status TEXT CHECK (status IN ('ok','drift')), PRIMARY KEY (hotel_slug, month, keyword));
-- 0903_visibility_gbp.sql   (raw GBP API data expires after 30 days: privacy-reviews.md B1, uae/zz-critique.md C10)
CREATE TABLE vis_gbp_daily (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), date DATE, metric TEXT, value BIGINT,
  fetched_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (hotel_slug, date, metric));
CREATE TABLE vis_gbp_keywords (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), month DATE, keyword TEXT, value BIGINT, threshold BIGINT,
  is_branded BOOL, fetched_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (hotel_slug, month, keyword), CHECK ((value IS NULL) <> (threshold IS NULL)));
CREATE TABLE vis_gbp_kpis_monthly (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), month DATE, reach_user_days BIGINT,
  actions BIGINT, action_rate NUMERIC(6,4), website_clicks BIGINT, branded_share NUMERIC(5,4), keywords_under_threshold INT,
  computed_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (hotel_slug, month));    -- derived Clefs KPIs, retained
CREATE TABLE vis_lodging_snapshots (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug), fetched_at TIMESTAMPTZ,
  kind TEXT CHECK (kind IN ('lodging','google_updated')), payload JSONB, diff_vs_kb JSONB, expires_at TIMESTAMPTZ NOT NULL);
-- 0904_visibility_ai.sql
CREATE TABLE vis_ai_prompts (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug), prompt_key TEXT NOT NULL,
  version INT NOT NULL, text TEXT NOT NULL, language TEXT NOT NULL, market CHAR(2) NOT NULL, city TEXT,
  intent TEXT NOT NULL CHECK (intent IN ('generic','persona','event','fact_check','brand')), fact_key TEXT, event_id UUID,
  active BOOL NOT NULL DEFAULT TRUE, retired_at TIMESTAMPTZ, UNIQUE (hotel_slug, prompt_key, version));
CREATE TABLE vis_ai_batches (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT REFERENCES hotels(slug),
  kind TEXT NOT NULL CHECK (kind IN ('weekly','snapshot')), week DATE, subject JSONB, planned INT, done INT NOT NULL DEFAULT 0,
  cost_micros BIGINT NOT NULL DEFAULT 0, cost_currency CHAR(3) NOT NULL DEFAULT 'USD', status TEXT NOT NULL DEFAULT 'running',
  expires_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), CHECK (kind = 'snapshot' OR hotel_slug IS NOT NULL));
CREATE TABLE vis_ai_runs (id BIGSERIAL PRIMARY KEY, batch_id BIGINT NOT NULL REFERENCES vis_ai_batches(id),
  batch_kind TEXT NOT NULL CHECK (batch_kind IN ('weekly','snapshot')), hotel_slug TEXT REFERENCES hotels(slug),
  prompt_id BIGINT, prompt_intent TEXT, prompt_text TEXT, market CHAR(2), language TEXT,
  engine TEXT NOT NULL CHECK (engine IN ('gemini_web_search','perplexity_sonar','serpapi_ai_mode')),
  model_id TEXT, baseline_epoch INT NOT NULL, locale JSONB NOT NULL, repeat_idx SMALLINT, run_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok','error','empty','discarded')), searched BOOL, answer_text TEXT, raw JSONB,
  citations JSONB, entities JSONB, self_mentioned BOOL, self_position SMALLINT, self_cited BOOL,
  cost_micros BIGINT, cost_currency CHAR(3) NOT NULL DEFAULT 'USD', parser_version TEXT,
  CHECK (batch_kind = 'snapshot' OR hotel_slug IS NOT NULL));
CREATE TABLE vis_ai_weekly (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), week DATE, window_weeks SMALLINT NOT NULL CHECK (window_weeks IN (1,4)),
  engine TEXT, market CHAR(2), language TEXT, entity TEXT, baseline_epoch INT, runs INT, mentions INT, rate NUMERIC(5,4),
  ci_low NUMERIC(5,4), ci_high NUMERIC(5,4), avg_position NUMERIC(4,2), sov NUMERIC(5,4), sufficient BOOL, source_mix JSONB,
  PRIMARY KEY (hotel_slug, week, window_weeks, engine, market, language, entity));   -- discovery prompts only
CREATE TABLE vis_engine_versions (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), engine TEXT, version_key TEXT,
  baseline_epoch INT, first_seen_at TIMESTAMPTZ, reason TEXT, PRIMARY KEY (hotel_slug, engine, version_key));
CREATE TABLE vis_ai_facts (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug), fingerprint TEXT NOT NULL,
  fact_key TEXT NOT NULL, claimed_value TEXT, claimed_quote TEXT, truth_value TEXT, truth_source TEXT,
  error_type TEXT CHECK (error_type IN ('wrong_value','false_absent','false_closed','wrong_star','price_band_off','unverifiable')),
  severity SMALLINT, engines TEXT[], cited_domains TEXT[], occurrences INT NOT NULL DEFAULT 1, run_ids BIGINT[],
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','fixing','fixed','dismissed','recurred')),
  first_seen TIMESTAMPTZ, last_seen TIMESTAMPTZ, action_id BIGINT, UNIQUE (hotel_slug, fingerprint));
CREATE FUNCTION vis_wilson_halfwidth(k NUMERIC, n NUMERIC) RETURNS NUMERIC LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN n > 0 THEN (1.96 * sqrt((k/n) * (1 - k/n) / n + 1.96^2 / (4 * n^2))) / (1 + 1.96^2 / n) END $$;
-- 0905_visibility_site_costs.sql
CREATE TABLE vis_site_health (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug), checked_at TIMESTAMPTZ,
  url TEXT, crux JSONB, psi JSONB, jsonld_found BOOL, jsonld_types TEXT[], jsonld_fields JSONB, jsonld_issues JSONB, facts_vs_kb JSONB);
CREATE TABLE vis_api_costs (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT REFERENCES hotels(slug), provider TEXT NOT NULL,
  purpose TEXT NOT NULL, cost_micros BIGINT NOT NULL, cost_currency CHAR(3) NOT NULL DEFAULT 'USD', at TIMESTAMPTZ NOT NULL,
  CHECK (hotel_slug IS NOT NULL OR purpose IN ('public_check','snapshot')));
-- 0906_visibility_attribution_public.sql
CREATE TABLE vis_attribution_monthly (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), month DATE, gbp_website_clicks BIGINT,
  direct_bookings INT, direct_room_revenue_minor BIGINT, commission_avoided_minor BIGINT, reference_ota TEXT,
  currency CHAR(3) NOT NULL, PRIMARY KEY (hotel_slug, month));
CREATE TABLE vis_public_checks (id UUID PRIMARY KEY, token_hash BYTEA UNIQUE, ip_hash BYTEA, hotel_query TEXT, property_token TEXT,
  result JSONB, lead_name TEXT, lead_email_enc BYTEA, lead_whatsapp_enc BYTEA,   -- sealed with core.privacy.seal() (F8)
  consent_processing BOOL NOT NULL,
  consent_transfer_abroad BOOL NOT NULL, consent_contact_email BOOL, consent_contact_whatsapp BOOL, status TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL);   -- intentionally global (public lead magnet)
```

**Retention (`register_pii`):**
- `vis_public_checks` lead columns (`lead_name`, `lead_email_enc`, `lead_whatsapp_enc`) are deleted at 30 d.
- `vis_gbp_daily`, `vis_gbp_keywords` and `vis_lodging_snapshots` are deleted at `expires_at`.
- Snapshot batches and runs are purged through `purge_ai_snapshot`.

### 5.2 Google Hotels: shared SerpApi, official price and parity

- **Calls.**
  - Every call goes through `core.serpapi.search` (F5) with params from `google_hotels_area_params`.
  - `q` comes only from `demand.api.area_queries`.
  - The date pairs are next Friday + 1 night and today + 30 + 1 night. Both are inside Demand's daily 0-30-day fetch, so Demand's `dr.comp_rates_fetch` (04:00) and `visibility.google_hotels_daily` (05:30) share one billed call per page 1.
  - If the hotel is not on page 1, up to 2 more pages are fetched with `next_page_token` (Visibility only). `organic_total` counts non-sponsored properties across the fetched pages.
- **Organic rank** is the index of `gh_property_token` in `properties[]` after dropping `sponsored=true`. `ads[]` are counted as sponsored.
- **Official price:**
  - Property details are fetched for the hotel for the same two date pairs.
  - `official_present` is true when any entry in `prices[]` or `featured_prices[]` has `official: true` (local-seo-google.md).
  - Comparisons are like-for-like: the same field is used for the official and other entries (`total_rate.extracted_lowest`, else `rate_per_night.extracted_lowest`), and `price_basis` is recorded.
- **Parity:**
  - `gap = official − min(non-official)` and `gap_pct = gap / official × 100`. This is the same direction as Profit Guard L7, which compares against the direct price.
  - A breach needs `gap_pct > profit_guard.api.parity_tolerance_pct` (default 1.0; strictly greater).
  - Exposure = `gap × OTB rooms on the channel of the lowest non-official price for that stay date`. Unmapped sources use all OTA OTB rooms. This is L7's exposure formula.
  - Evidence is recorded with `profit_guard.api.record_rate_evidence(source='SERPAPI', emit_signal=False)`. Profit Guard persists `emit_signal` and L7 skips rows where it is false, so no gap is signalled twice.
  - **Goldens:**
    - AED 450 vs Booking AED 412: gap 38 (8.44%), a breach, exposure 38 × 14 = AED 532.
    - 400 vs 396 (exactly 1.00%): no breach.
    - 404 vs 400 (0.99%): no breach.
    - 450 vs 446.50 (0.78%): no breach.
- **Disclosure.** The Google tab says: "Google Hotels data comes from SerpApi, a third-party service; treat prices as indicative." It links to the vendor-risk note (Canon 20) and has a `serpapi_enabled` toggle, like Reviews' SerpApi toggle. Parity is labelled "indicative, third-party data". The public result page carries its own disclosure (2.4).
- **What `serpapi_enabled=false` turns off** (every SerpApi use in Visibility, not only the Google tab):
  - the Google Hotels jobs and parity;
  - the `serpapi_ai_mode` engine, which is removed from `engines_enabled` in weekly batches and from the AI snapshot;
  - `visibility.rank_calibrate_monthly`, so the rank shows "uncalibrated".
  - A test covers all three.
- **Price accuracy.** Parity tasks and `vis.official_price_missing` carry this note: "Your booking-engine landing price and taxes must match the price Google shows (Google hotel price accuracy policy 6064419)." They link to Demand's Hotel Center readiness checklist (02 §4.3).

### 5.3 Map rank grid (internal KPI; no heatmap UI)

- **Request:** `POST https://places.googleapis.com/v1/places:searchText` with header `X-Goog-FieldMask: places.id`. A contract test asserts that the mask equals exactly `places.id`, which is the Essentials (IDs Only) SKU with an unlimited free cap.
- **Body:** `{textQuery, locationBias:{circle:{center, radius:1000}}, rankPreference:'RELEVANCE', includedType:'lodging', strictTypeFiltering:true, pageSize:20, regionCode:'AE', languageCode}`.
- **IDs.** The hotel's and competitors' `place_id` come from `vis_profile` and `demand.api.comp_set()`, which carries `place_id` (7.2). The IDs-only mask cannot resolve names.
- **Grid.** 7×7 points at 800 m spacing, with 3-5 keywords. The defaults are "hotel {area}", "hotels near {landmark}", "{star} star hotel {area}" and "فندق في {area}". `{area}` and `{landmark}` come from `vis_profile.area` and `landmarks[0]` (seeded from settings); `{star}` comes from `hotels.star_rating`. A keyword whose input is missing is not generated. The job `visibility.rank_grid_weekly` runs Monday at 04:00, about 1,000 calls a month.
- **Metrics** (per keyword, for the hotel and each comp from the same results):
  - rank is 1-based; not found = 21
  - `ARP = mean(rank | found)`
  - `ATRP = mean(rank, not found = 21)`
  - `SoLV` = share of points with rank ≤ 3
  - **Golden:** 30 found points with ranks summing to 150 and 9 points in the top 3 give ARP 5.00, ATRP 549/49 = 11.20 and SoLV 18.37%.
- **Calibration.** `visibility.rank_calibrate_monthly` runs on the first Monday at 04:30 (`nth_weekday_at(1, 1, "04:30")`).
  - It uses SerpApi `google_maps` (`type=search, q, ll=@lat,lng,14z, gl=ae`) on a 3×3 subsample per keyword.
  - Drift is flagged when Spearman ρ < 0.6 or top-3 agreement < 0.7. The UI then shows "ordering may differ from Maps" (A13).

### 5.4 Google Business Profile

- **Access.**
  - GBP API approval belongs to Clefs' Google Cloud project, not to each hotel: 0 QPM means not approved and 300 QPM means approved.
  - **Prerequisites** (local-seo-google.md; webinar/zz-critique.md §4): Clefs' own Business Profile verified and active for at least 60 days, a website on that profile, and the Basic API Access contact form. Quota is 0 QPM until Google approves, 300 QPM after. For a new company this is at least 60 days plus review, a launch-critical lead time, so the owner action "create and verify Clefs' Business Profile and start the 60-day clock now" is listed in 7.7.
  - A `clefs_admin` page tracks the application and these prerequisites.
  - Per hotel, the settings show "Connect GBP (OAuth); the location must be verified", with the project status read-only.
  - **Until approval:** the GBP tiles, the UTM action and the lodging check show "not connected (project not approved, 0 QPM)", and Reviews' Google replies use draft plus Copy.
  - **No Q&A.** The Q&A API was discontinued on 2025-11-03, so there is no Q&A feature or Q&A seeding.
  - The connection is `core.oauth` provider `google_business`, shared with Reviews, so owners connect Google once.
  - **Location picker** (owned by Visibility). After connecting, `GET /hotels/{slug}/visibility/gbp/locations` calls `accounts.list` and `accounts.locations.list`; the owner picks one and `PUT .../visibility/settings {gbp_location}` stores it. `visibility.api.gbp_location(cur, hotel_slug) -> str | None` exposes it to Reviews, which builds no picker of its own. The UI shows "Pick location" after connect.
- **Daily** (`visibility.gbp_daily`, 06:00): `GET https://businessprofileperformance.googleapis.com/v1/locations/{id}:fetchMultiDailyMetricsTimeSeries`.
  - Metrics: the four `BUSINESS_IMPRESSIONS_*`, `BUSINESS_CONVERSATIONS`, `BUSINESS_DIRECTION_REQUESTS`, `CALL_CLICKS` and `WEBSITE_CLICKS`. `BUSINESS_BOOKINGS` (Reserve with Google only) and `BUSINESS_FOOD_ORDERS` (deprecated) are excluded.
  - Each run re-fetches the last 10 days, because the data lag is undocumented.
  - Raw rows expire 30 days after fetch.
  - **Reach** = impressions summed over 28 d vs the prior 28 d, labelled "user-days". Year-over-year compares against a live re-fetch of the year-ago 28-day range when the monthly KPI is computed; history is not stored.
  - **Action rate** = (calls + website + directions + conversations) / impressions.
  - `visibility.gbp_kpis_monthly` (`monthly_at(3, "06:30")`) writes the derived `vis_gbp_kpis_monthly`, which is retained (7.7).
- **Monthly keywords** (`visibility.gbp_keywords_monthly`, `monthly_at(5, "06:00")`): `GET /v1/locations/{id}/searchkeywords/impressions/monthly` with `pageSize=100` and pagination.
  - `insightsValue.value` and `.threshold` are stored separately, and **thresholds are never summed**.
  - Branded share uses exact values only, with the footnote "+N keywords under threshold". For example, values 120, 45 and 30 plus 5 threshold rows display as "195 + 5 under threshold".
  - Raw rows expire after 30 days.
- **UTM action `visibility.gbp_utm_patch`:**
  1. Read with `GET mybusinessbusinessinformation.googleapis.com/v1/locations/{id}?readMask=websiteUri`.
  2. Merge in `utm_source=google&utm_medium=organic&utm_campaign=gbp_listing`, keeping the existing params, host and path.
  3. Write with `PATCH ...?updateMask=websiteUri`, with `reversal_handle={kind:'gbp_patch', field:'websiteUri', previous}`.
  4. Verify after 1 h: the GET shows the new URI.
- **Lodging** (`visibility.lodging_check`, daily):
  - `GET mybusinesslodging.googleapis.com/v1/locations/{id}/lodging?readMask=*` and `:getGoogleUpdated`.
  - The desired state comes from `concierge.api.kb_facts(keyed_only=True)` plus settings, mapped fact by fact (Lodging field names to be checked against the Lodging API reference at build, 7.7):

    | `fact_key` | Lodging field(s) |
    |---|---|
    | `checkin_time` | `policies.checkinTime` |
    | `checkout_time` | `policies.checkoutTime` |
    | `parking` (`none`, `free`, `paid`) | `parking.parkingAvailable` (false for `none`) and `parking.freeParking` (true only for `free`) |
    | `pool` | `pools.pool` |
    | `breakfast` | `foodAndDrink.breakfastAvailable` |
    | `pets` | `pets.petsAllowed` |
    | `wifi` | `connectivity.wifiAvailable` |

  - The allowlist is exactly these fields. `accessibility.*` is dropped until a fact key exists for it.
  - A diff becomes `visibility.lodging_sync`, executed as `PATCH .../lodging?updateMask=<fields>` with the previous values in `reversal_handle`.
  - Verification after 24 h: if `getGoogleUpdated` shows Google overrode the value, the result is `not_verified` with evidence.
- **Reviews:**
  - Visibility shows the hotel's own unanswered count and response rate from `reviews.api.reply_stats`.
  - Competitor `rating` and `userRatingCount` are shown live through Reviews' Places proxy widget and never stored (03 §4.1). There is no competitor unanswered metric, because Places returns at most 5 reviews and no owner replies.
  - "Draft replies" calls `reviews.api.draft_reply`. Visibility never posts replies itself.

### 5.5 AI visibility radar

**Engines (`engines/`).** The registry holds exactly these three.

| Engine | Call | Citations | Localisation |
|---|---|---|---|
| `gemini_web_search` | `ai.responses_raw(model=AI_MODEL_VISIBILITY_GEMINI, tools=[{google_search:{}}], input=prompt)` (Grounding with Google Search; the market's city and country go in the prompt) | `text` plus `grounding_metadata` (`grounding_chunks` web `{uri,title}`, `grounding_supports` segments, `web_search_queries`); `model_id=response.model_version`; `searched` is recorded | The prompt market's main city |
| `perplexity_sonar` | `POST https://api.perplexity.ai/chat/completions {model: PERPLEXITY_MODEL, messages, web_search_options:{search_context_size:'low'}}`. The context size is pinned (it sets the per-request price, 5.10) and recorded in `vis_ai_runs.locale`. Enabled only with `PERPLEXITY_API_KEY` and `PERPLEXITY_TERMS_APPROVED=true` (the Canon 20 legal gate) | `search_results[]{title,url,date,snippet}`; the old `citations` field is gone | None, so it runs UAE-market prompts only |
| `serpapi_ai_mode` | `core.serpapi.search('google_ai_mode', {q, location:<market city>, gl:<market>, hl:<language>, device:'desktop'})` | `text_blocks`, `references[]`, `reconstructed_markdown`; `model_id='google_ai_mode'` | The prompt market's city |

- **Gemini is excluded.** `test_no_gemini_engine` asserts:
  - the registry keys equal the set above
  - no `generativelanguage.googleapis.com`, `google_search` tool or `gemini` identifier appears in `modules/visibility/**` outside the ban test
  - there is no Gemini setting
- **No scraping.** The same test greps for `perplexity.ai/search`, `playwright` and `selenium`.
- **Prompts** (`visibility.ai_prompts_refresh`, Sunday 20:00). Templates are deterministic so weeks stay comparable. There are 24 prompts per week:
  - UAE market (Dubai):
    - 10 EN discovery prompts: 5 generic, 3 persona, 2 event from `demand.api.upcoming_events(days=60)`
    - 1 EN brand prompt
    - 5 AR MSA discovery prompts: generic 1-3 and persona 1-2
    - 4 EN fact-check prompts: check-in time, parking, pool or breakfast, star rating
  - Source markets: 2 EN discovery prompts for each of the top 2 `guest_country` values over the trailing 180 d
  - A change to a template's inputs creates a new `version`. Retired prompts keep their history.
- **Repeats:** 4 per prompt per engine per week. That is 96 runs per engine per week (80 for Perplexity), about 416 per engine per month.
- **Execution.**
  - `visibility.ai_radar_weekly` (Monday 01:00) creates the batch.
  - The task `visibility.ai_batch_step` runs at most 4 runs in one concurrent round, with a 15 s timeout per call, then re-enqueues itself. This stays inside the 25 s tick budget.
  - A run that times out is retried once in a later step.
  - A prompt × engine either keeps all 4 repeats or all are marked `discarded`, so samples are never partial.
  - `visibility.ai_weekly_rollup` follows.
- **Parsing** (`parsers/`, `parser_version`):
  1. Split the answer into list items.
  2. Resolve entities against the hotel's aliases, comp aliases, the website domain and place ids in cited URLs. Names are normalised: lowercase, no diacritics or tashkeel, alef and taa marbuta normalised, "hotel" and "(DEMO)" stripped. Matching uses token-set ratio ≥ 0.9.
  3. If nothing resolves, fall back to `ai.structured(store=False)` hotel extraction.
  4. `self_position` is the order among hotel entities.
  5. Cited domains are classed as `ota`, `review`, `official`, `google`, `ugc`, `wiki` or `press_other`.
- **Statistics.** Only discovery prompts (`intent IN ('generic','persona','event')`) count toward mention rate and share of voice. Brand and fact-check prompts name the hotel, so they feed fact errors only. Cells are per engine × market × language × entity, within one `baseline_epoch`:
  - UAE-EN uses `window_weeks=1` (n=40 per engine).
  - UAE-AR (n=80) and each source market (n=32) use a rolling `window_weeks=4` inside the same epoch. A window that spans an epoch change is truncated to the new epoch.
  - Mention rate uses the Wilson 95% interval. **Golden:** k=12, n=40 gives [0.1807, 0.4543]; k=0, n=30 gives [0.0000, 0.1135].
  - `sufficient = runs ≥ 30 and vis_wilson_halfwidth(mentions, runs) ≤ 0.15`. When a cell is insufficient, the UI and Ask show "insufficient sample (n=…)".
  - SoV = self mentions / Σ mentions over the hotel plus its comp set, reported with Wilson, with "other hotels" shown separately.
  - Source mix is shown for when the hotel wins vs when a competitor wins.
  - A trend change is surfaced only when the CI moves outside the prior CI for 2 or more consecutive windows.
- **Fact errors:**
  - `ai.structured(FactClaims{facts:[{fact_key, value, quote}]})` runs over the sentences that mention the hotel, from every prompt intent.
  - Truth comes from `concierge.api.kb_facts(keyed_only=True)` plus hotel settings. `fact_key` is a closed vocabulary: `checkin_time`, `checkout_time`, `parking`, `pool`, `breakfast`, `star_rating`, `pets`, `wifi`, `price_band`. Concierge adds `kb_entries.fact_key` (7.5) for all but `price_band`, which is Visibility-only: its truth is the trailing-30-day ADR from `core/stays`, and `star_rating`'s is `hotels.star_rating`.
  - **Comparators:**
    - times differ by 30 minutes or more
    - a boolean or paid-status mismatch (`false_absent`, for example "no parking" when the KB says paid parking)
    - a star mismatch
    - "closed" is `false_closed`, severity 1
    - a price band outside ±40% of the trailing-30-day ADR
  - A missing KB fact is `unverifiable` and leads to `visibility.add_kb_faq`.
  - Fingerprint = `sha1(hotel|fact_key|normalised claimed value)`. Occurrences are tracked across weeks and engines.
- **Version changes.**
  - A new `model_id` for an engine, or a change to `AI_MODEL_VISIBILITY_GEMINI`, inserts a `vis_engine_versions` row with a new `baseline_epoch`. Trends never cross epochs, and the UI marks the break ("Model changed 12 Oct; baseline reset").
  - AI Mode has no model id. For it, a Jensen-Shannon divergence above 0.35 in the cited-domain distribution week over week raises "possible source shift", and a baseline reset is suggested for manual acceptance.
- **Label.** Gemini and Perplexity numbers are marked "API simulation: consumer app answers vary". AI Mode cells and snapshot rows are marked "Google AI Mode via SerpApi (third-party); indicative", because they are Google's consumer AI Mode page fetched by a third-party service (the Canon 20 vendor risk), not an API simulation. The method note cites the arXiv 2609.18729 audit (ai-visibility.md §2).
- **Citations are clickable.** Every grounding source and every source in the evidence Drawer renders as a clickable link, and Gemini answers show Google's Search Suggestions, as the Grounding with Google Search terms require.

### 5.6 Site health (`visibility.site_health_weekly`, Sunday 03:00)

- **CrUX:** `POST https://chromeuxreport.googleapis.com/v1/records:queryRecord` with `{origin, formFactor:'PHONE', metrics:[largest_contentful_paint, interaction_to_next_paint, cumulative_layout_shift]}`. A 404 means insufficient data.
- **PSI:** `GET https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url&strategy=mobile&category=performance`.
- **"Good" thresholds** at p75: LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1 (local-seo-google.md §4). A metric above its threshold is reported as "outside Google's 'good' threshold". This covers both the "needs improvement" and the "poor" bands.
- **JSON-LD:**
  - Fetch the homepage and the configured room pages with httpx, honouring robots.txt, as user agent `ClefsBot/1.0`. Parse `application/ld+json`, including `@graph`.
  - **Google hotel price structured data** (used for price-accuracy validation) on `Hotel` / `HotelRoom`:
    - required: `name`, `address`, `makesOffer`/`offers`
    - recommended: room `bed` and `occupancy`
  - **Useful schema.org fields**, which are not Google requirements: `checkinTime`, `checkoutTime`, `starRating`, `numberOfRooms`, `amenityFeature`, `petsAllowed`, `priceRange`, `geo`.
  - Mismatches in `facts_vs_kb` become `visibility.site_fact_fix`.
- **Presentation:** this is hygiene only. No ranking claim is made, because the effect of site speed is UNVERIFIED.

### 5.7 Actions and verifiers

| Type | Executor | Verifier (verify_after) | Value |
|---|---|---|---|
| `visibility.parity_fix` | Human task for the revenue or channel manager. The card carries the price-accuracy note from 5.2 | Property details for the same dates show a gap within tolerance (after 24 h, then daily up to 7 d) | NULL |
| `visibility.gbp_utm_patch` | `locations.patch websiteUri` | The GET matches (1 h) | NULL |
| `visibility.lodging_sync` | `updateLodging` | `getGoogleUpdated` shows no override (24 h) | NULL |
| `visibility.ota_listing_fix` | Human task naming the cited OTA and the field | The fact-check prompt shows 0 errors in the next batch across the engines that previously erred (n ≥ 8) | NULL |
| `visibility.site_fact_fix` | Human task | The next JSON-LD check matches the KB | NULL |
| `visibility.add_kb_faq` | `concierge.api.propose_kb_entry` (unapproved) | `concierge.api.kb_entry_status` shows the entry approved within 14 d | NULL |

A fact error is routed by the domain it cites:
- OTA → `ota_listing_fix`
- official site → `site_fact_fix`
- Google → `lodging_sync`
- none → `add_kb_faq`

`decide_perm` for all of these is `visibility.act`.

### 5.8 Attribution

`visibility.attribution_monthly` runs `monthly_at(2, "07:00")`.

- **What counts:** direct reservations in `core/stays` (D2 `utm`) where `utm_source='google' AND (utm_campaign='gbp_listing' OR <the configured free-booking-link pattern>)`.
- **Commission avoided** = Σ over those reservations of `profit_guard.api.expected_commission(cur, slug, ota_code=reference_ota(cur, slug, as_of=month_end)[0], breakdown=core.stays.reservation_breakdown(cur, reservation_id), on=check_in)['commission_minor']`.
- **The line reads:** "Google sent N website clicks → M direct bookings → AED X commission avoided", captioned "bookings that lost the tag are not counted". This is attribution, **not** ledger-verified profit.

### 5.9 Public lead magnet and AI snapshot

**Google check** (Canon 23, the secondary magnet). Lane H builds the UI (2.4).
- **`POST /public/visibility/google-check`** with `{hotel_name, city, consent_processing:true, consent_transfer_abroad:true}`:
  - Rate limits: 3 per IP per day, and `PUBLIC_CHECK_DAILY_CAP` (default 20) in total.
  - SerpApi calls are charged to `purpose='public_check'`. When `PUBLIC_SERPAPI_MONTHLY_CAP` is exhausted, the endpoint fails closed with 503.
  - It searches `google_hotels` for `"{hotel_name} {city}"` and matches by name ≥ 0.85. Otherwise it returns up to 5 candidates for the user to pick from.
  - It then fetches property details for the two date pairs.
- **The teaser** answers "Official site price on Google: yes/no".
- **`POST .../{token}/lead`** takes name, email or WhatsApp, and separate contact consents that name Clefs and the channel. It unlocks the parity table, the organic rank and the note from 2.4. Lead contacts are sealed (5.1).
- **`GET .../{token}`** returns the result.
- **Retention** is 30 d.
- **Schemas** (lane H mocks these in wave 1):

  | Call | Response |
  |---|---|
  | `POST /public/visibility/google-check` | `{token, status: 'needs_pick'\|'ready'\|'no_match', candidates: [{property_token, name, address}], teaser: {official_present: bool} \| null}` (`candidates` only for `needs_pick`; `teaser` only for `ready`) |
  | `POST .../{token}/pick {property_token}` | the same shape, now `ready` or `no_match` |
  | `POST .../{token}/lead {name, email?, whatsapp?, consent_contact_email?, consent_contact_whatsapp?}` | `{unlocked: {parity: [{source, channel, price_minor, currency, official: bool}], gap_minor, gap_pct, organic_rank, checked_at, stay_dates: [[check_in, check_out]], note_claim_ids: [...]}}` |
  | `GET .../{token}` | the latest of the shapes above |
  | Errors | 422 `invalid_input` (missing ticks or fields), 429 `rate_limited`, 503 `budget_exhausted`, 404 `not_found` (unknown or expired token) |
- **Founder follow-up.** The founder is notified through `notify`. WhatsApp outreach to the lead is queued inside [09:00, 18:00) Asia/Dubai and only when `consent_contact_whatsapp` is true.

**AI snapshot for Profit Guard's audit (Canon 23):**
- **Start.** `visibility.api.start_ai_snapshot(cur, *, subject: SnapshotSubject{hotel_name, city, area, website, star: int | None}, requester_ref) -> int | None` runs 5 prompts × the enabled engines × 3 runs as a `kind='snapshot'` batch.
  - With Perplexity enabled that is 3 engines, 45 runs. With `PERPLEXITY_TERMS_APPROVED=false` it is 2 engines, 30 runs. The snapshot has no hotel row, so AI Mode is dropped when SerpApi is unavailable (no `SERPAPI_KEY`, or `BudgetExhausted`); its rows carry the third-party label (5.5).
  - It returns None when the module is disabled, `SNAPSHOT_DAILY_CAP` (10) is reached, or `SNAPSHOT_MONTHLY_BUDGET_USD` (50) is exhausted.
- **The 5 prompts:**
  1. "Best {star}-star hotels in {area}, {city}" ("Best hotels in {area}, {city}" when `star` is None)
  2. "Where should I stay in {area}, {city} for a short business trip?"
  3. "Recommend hotels near {area} in {city}"
  4. "Is {hotel_name} in {city} a good hotel?" (brand)
  5. "What time is check-in at {hotel_name}, {city}, and does it have parking?" (fact_check)
- **Read.** `get_ai_snapshot(cur, batch_id) -> AiSnapshot | None` returns `{status, per_engine:[{engine, mentioned, runs, ci}], top_cited_domains, answers_about_you, label}`.
  - Mentions are counted over prompts 1-3 only. Prompts 4-5 are shown as quoted answers.
  - Figures are shown as counts ("mentioned in 4 of 9 answers"), because n is below the minimum sample.
- **Purge.** `purge_ai_snapshot(cur, batch_id)` is called by Profit Guard's 30-day purge.
- **Existing stub.** The foundation stub `ai_snapshot(cur, hotel_slug, *, hotel_name, city, max_queries=3)` stays importable. It returns the latest completed snapshot for that subject, or None.

### 5.10 Cost controls

- **Ledger:** `vis_api_costs` plus the SerpApi ledger (F5).
- **Budget:** every job checks the month-to-date spend against `monthly_budget_minor` (default USD 30.00; the PLAN target is $15-30). Spend is held in micros and the budget in minor units, so the check is Σ `cost_micros` (this hotel's `vis_api_costs` and `serpapi_calls`) against `monthly_budget_minor × 10_000` when both are USD; other currencies are converted with `core.money.convert` first.
- **Degradation order:**
  1. Drop the source-market prompts.
  2. Cut repeats to 3.
  3. Drop the AR prompts.
  4. Drop the most expensive engine.
- **Integrity:** a prompt × engine runs all of its repeats or none.
- **Kill switch:** `VISIBILITY_AI_ENABLED`.
- **Missing keys** show a "not connected" state with no errors.
- **Estimate per hotel per month** (about 416 runs per engine):

  | Item | Cost |
  |---|---|
  | Places | $0 |
  | SerpApi Google Hotels (shared with Demand) | ~$1.5 |
  | Calibration | ~$0.5 |
  | AI Mode | ~$4.2 (1 credit per request assumed; the credit cost is UNVERIFIED) |
  | Gemini with Grounding with Google Search | ~$5-9 ($14 per 1k search queries plus tokens) |
  | Perplexity, when enabled (about 346 requests at $5 per 1k with `search_context_size='low'`, plus $1/M tokens) | ~$2-3 |
  | Parsing and fact extraction | ~$1 |
  | GBP, CrUX, PSI | $0 |
  | **Total** | **~$14-21** |

- Public checks and snapshots are budgeted globally (5.9), not per hotel.

### 5.11 Jobs, signals, metrics, API, console

**Jobs:**

| Job | Schedule |
|---|---|
| `visibility.google_hotels_daily` | daily 05:30 |
| `visibility.rank_grid_weekly` | `weekly_at(1, "04:00")` |
| `visibility.rank_calibrate_monthly` | `nth_weekday_at(1, 1, "04:30")` |
| `visibility.gbp_daily` | daily 06:00 |
| `visibility.gbp_kpis_monthly` | `monthly_at(3, "06:30")` |
| `visibility.gbp_keywords_monthly` | `monthly_at(5, "06:00")` |
| `visibility.lodging_check` | daily 06:15 |
| `visibility.ai_prompts_refresh` | `weekly_at(7, "20:00")` |
| `visibility.ai_radar_weekly` | `weekly_at(1, "01:00")` |
| `visibility.ai_batch_step` | task |
| `visibility.ai_weekly_rollup` | task after the batch |
| `visibility.site_health_weekly` | `weekly_at(7, "03:00")` |
| `visibility.attribution_monthly` | `monthly_at(2, "07:00")` |
| `visibility.competitors_sync` | daily, from `demand.api.comp_set` |
| `visibility.gbp_raw_expire`, `visibility.public_check_purge`, `visibility.snapshot_purge` | daily |

**Signals:**

| Kind | Section | Cadence | Impact |
|---|---|---|---|
| `vis.parity_gap_google` | money_available | daily (Canon 18) | exposure |
| `vis.official_price_missing` | money_available | weekly | NULL |
| `vis.rank_drop` (SoLV −15 pts for 2 weeks) | money_available | weekly | NULL |
| `vis.ai_sov_change` | money_available | weekly | NULL |
| `vis.ai_fact_error` | money_available | weekly | NULL |
| `vis.lodging_overridden`, `vis.site_health_not_good`, `vis.jsonld_mismatch`, `vis.gbp_setup` | service_risk | weekly | NULL |
| `vis.google_attribution` | money_available | monthly | NULL |

**Metrics:**
- `vis.gh_organic_rank`
- `vis.official_presence_rate`
- `vis.parity_gap` (money metrics use `Metric(unit='money', currency='hotel')`, with no currency in the key)
- `vis.solv_pct` and `vis.atrp` (dimensions: keyword, entity)
- `vis.gbp_reach_user_days`, `vis.gbp_website_clicks` and `vis.gbp_action_rate`, read from `vis_gbp_kpis_monthly` only
- `vis.ai_mention_rate`
  - Source: `vis_ai_weekly WHERE window_weeks = 1`.
  - Measure: `CASE WHEN SUM(runs) >= 30 AND vis_wilson_halfwidth(SUM(mentions), SUM(runs)) <= 0.15 THEN SUM(mentions)::numeric / SUM(runs) END`, so Ask applies the same gate as the UI.
  - Dimensions: engine, market, language.
- `vis.ai_share_of_voice` (same gate)
- `vis.ai_fact_errors_open`
- `vis.google_direct_bookings`
- `vis.google_commission_avoided`
- `vis.cwv_lcp_p75_ms`

**Routes (`/hotels/{slug}/visibility/...`):**
- `google`, `rank`, `gbp`, `gbp/locations` (GET, the location picker, 5.4)
- `ai`, `ai/runs/{id}`, `ai/facts`, `ai/facts/{id}/fix`
- `site-health`, `attribution`
- `settings` (GET/PUT)
- `actions/utm-patch` (POST, proposes the action)
- `actions/lodging-sync` (POST, proposes the action)

**Console tabs (`(console)/visibility/`):**
- **Google:** presence; the parity table with the SerpApi price list as evidence, plus the "indicative" label and the vendor disclosure; rank; GBP tiles (derived KPIs); keywords with threshold rows shown as "< N"; own review reply stats; UTM status and button; the attribution line.
- **AI answers:**
  - engine × market cells showing the rate ± CI, or "insufficient sample (n=…)"
  - the SoV bar
  - a source-mix `Donut` with 5 slices or fewer
  - the fact-error list
  - an evidence `Drawer` showing the raw answer with the claim highlighted, clickable citations and sources, the model id and run time, the label, and "Create fix"
- **Rank:** a table with sparklines and the calibration status.
- **Site health.**
- **Settings:** place id, GBP connection plus the read-only project QPM status and the location picker, property token, area and landmarks, keywords, markets, budget, and engines including the Perplexity legal gate and the SerpApi toggle.
- **Empty states:** each tab shows a setup checklist for what it lacks instead of an error: Google (property token; SerpApi on), Rank (place id, area), GBP ("not connected", "project not approved (0 QPM)" or "Pick location"), AI answers (engines and keys), Site health (website URL).

### 5.12 Tests (`tests/modules/visibility/`)

**Fixtures in `fixtures/`:**
- `gemini_web_search.json`, `perplexity_sonar.json`, `serpapi_ai_mode.json`
- `serpapi_gh_search.json` (sponsored and ads), `serpapi_gh_search_page2.json`
- `serpapi_gh_property_official.json`, `_no_official.json`, `_parity_gap.json`
- `places_ids_page.json`, `serpapi_maps.json`
- `gbp_daily.json`, `gbp_keywords.json` (with thresholds)
- `lodging_get.json`, `lodging_google_updated.json`
- `crux.json`, `crux_404.json`, `psi.json`
- `site_jsonld.html`, `site_graph.html`, `site_none.html`

**Cases:**
- Parsers per engine.
- Organic rank ignores sponsored results; the page-2 fetch.
- Parity goldens with `gap_pct = gap/official`, including the 1.00% boundary.
- `record_rate_evidence` is called with `emit_signal=False`.
- Wilson goldens.
- **Gate:**
  - n=29, k=6: hidden
  - n=30, k=6 (p=0.2, half-width 0.139): shown
  - n=30, k=15 (p=0.5, half-width 0.168): hidden
  - the metric SQL and the UI agree
- Brand and fact-check runs are excluded from mention rate and SoV.
- The AR 4-week rollup reaches n=80.
- SoV.
- **Fact detector:** check-in 12:00 vs 14:00; "no parking" vs paid parking; unverifiable.
- The epoch resets on a model change.
- Gemini and scraping are absent.
- The Places field mask.
- Thresholds are never summed.
- Raw GBP rows expire at 30 d while derived KPIs remain.
- The UTM merge keeps existing params.
- A lodging override gives `not_verified`.
- The budget degradation order.
- The batch step runs ≤ 4 runs with a 15 s timeout.
- The snapshot is 45 runs, or 30 with `PERPLEXITY_TERMS_APPROVED=false`, and returns None when the budget is exhausted.
- Public-check limits, the 503 when the budget is exhausted, and teaser gating.
- A UI test that every citation renders as a link.
- A single SerpApi call when Demand and Visibility build identical params through `google_hotels_area_params`.
- `serpapi_enabled=false` removes `serpapi_ai_mode` from the weekly batch, skips Google Hotels, and skips `rank_calibrate_monthly` (rank shows "uncalibrated").
- AI Mode cells carry the third-party label; Gemini and Perplexity cells carry "API simulation".
- The budget check compares micros with `monthly_budget_minor × 10_000`.
- Lodging diff uses only the mapped allowlist fields.

**Trust eval registration** (ai-visibility.md §6: golden prompts and parser accuracy on citations). Lane V registers a visibility eval with Trust:
- A frozen fixture set of about 30 answers per engine, with labelled entities, positions and cited domains.
- CI gates: entity recall and precision ≥ 95%, citation extraction ≥ 98%.
- When parser output changes on the golden set, `parser_version` is bumped and `baseline_epoch` is reset (5.5), so mention rates and SoV never silently shift. A regression feeds `eval_regression` (1.5).

---

## 6. Finance (`modules/finance/`, lane P)

### 6.1 Weekly pulse (no P&L needed)

`finance.weekly_rollup` runs `weekly_at(1, "05:00")` for the prior ISO week, in hotel tz.

- **Inputs:**
  - `ARN` = Σ `occupancy_daily.rooms_available` (A10)
  - `OccRN` = Σ `rooms_occupied`
  - `RoomsRev` = Σ stayed `reservation_nights.rate_fils`, plus `sc_fils` when `profit_guard.api.treat_sc_as_revenue` is true. Pass-through taxes are excluded.
  - `Dist` = Σ `profit_guard.api.distribution_cost_by_night` rows (`DistNight`, 7.2): `commission_minor + payment_fee_minor + booking_engine_fee_minor`. The OTA part is the rows with `is_ota`, per `channel_code`
  - `recovery_cost` = `service.api.recovery_cost`
- **Outputs:**
  - `NRevPAR = (RoomsRev − Dist) / ARN`
  - `OTA cost per occupied room` = OTA commission and fees / OccRN across all channels. A channel dimension gives each channel's cost per that channel's room-nights.
  - `cancellation leakage` = Σ `profit_guard.api.cancellation_cost_by_date`, bucketed by cancel date
  - `recovery_cost`
- **Golden:** ARN 560, RoomsRev 98,000, Dist 12,600 (OTA 10,500 + 840, direct 1,260), OccRN 350 gives:
  - NRevPAR **152.50**
  - RevPAR 175.00
  - OTA cost per occupied room **32.40**
  - cancellation leakage 1,150
- **Zero and missing denominators** (here and in 6.6): any ratio whose denominator is 0 or NULL (ARN with no `occupancy_daily` rows, OccRN = 0, TOR = 0) is stored as NULL and shown "n/a". The 13-week strip and trend rollups skip weeks with ARN = 0. Tests cover ARN = 0 and OccRN = 0.

### 6.2 DDL (1000-1004)

```sql
-- 1000_finance_imports.sql
CREATE TABLE fin_import_batches (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug), period_month DATE NOT NULL,
  statement_kind TEXT NOT NULL CHECK (statement_kind IN ('pl','trial_balance')),
  source TEXT NOT NULL CHECK (source IN ('upload_xlsx','upload_csv','upload_pdf','xero','qbo','zoho','tally_upload')),
  core_import_batch_id BIGINT REFERENCES import_batches(id), file_id UUID,
  sha256 TEXT NOT NULL,                 -- file hash, or hash of the canonical report JSON for connector batches
  currency CHAR(3) NOT NULL, basis TEXT CHECK (basis IN ('accrual','cash','unknown')),
  status TEXT NOT NULL CHECK (status IN ('uploaded','mapping_required','review','checks_failed','published','superseded','failed')),
  source_net_profit_minor BIGINT, mapped_net_profit_minor BIGINT, checks JSONB NOT NULL DEFAULT '{}', map_versions JSONB,
  connector_meta JSONB, published_by BIGINT, published_at TIMESTAMPTZ, superseded_by BIGINT, created_by BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (hotel_slug, period_month, statement_kind, sha256));
CREATE TABLE fin_coa_accounts (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug), source_system TEXT NOT NULL,
  code TEXT, name_raw TEXT NOT NULL, name_norm TEXT NOT NULL, language TEXT, parent_group_raw TEXT, type_raw TEXT,
  first_batch_id BIGINT, last_batch_id BIGINT, UNIQUE NULLS NOT DISTINCT (hotel_slug, source_system, code, name_norm));
-- 1001_finance_mapping.sql
CREATE TABLE fin_usali_map (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  account_id BIGINT NOT NULL REFERENCES fin_coa_accounts(id), version INT NOT NULL, usali_line TEXT NOT NULL, usali_schedule SMALLINT,
  usali_dept TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('revenue','cost_of_sales','payroll','other_expense','undistributed','mgmt_fee','non_operating','below_line','excluded')),
  is_pass_through_tax BOOL NOT NULL DEFAULT FALSE, pass_through_kind TEXT CHECK (pass_through_kind IN ('vat','municipality_fee','tourism_dirham')),
  ai_confidence NUMERIC(3,2), ai_rationale TEXT, ai_model TEXT,
  learned_from TEXT NOT NULL CHECK (learned_from IN ('rule','hotel_locked','global_memory','llm','manual')),
  data_origin TEXT NOT NULL, confirmed_by BIGINT, confirmed_at TIMESTAMPTZ, locked BOOL NOT NULL DEFAULT FALSE,
  override_reason TEXT, superseded_at TIMESTAMPTZ, UNIQUE (hotel_slug, account_id, version));
CREATE TABLE fin_mapping_memory (name_norm TEXT, language TEXT, usali_line TEXT, confirmations INT, hotels INT,
  PRIMARY KEY (name_norm, usali_line));   -- intentionally global; never fed from data_origin='xero'
-- 1002_finance_lines_summary.sql
CREATE TABLE fin_pl_lines (id BIGSERIAL PRIMARY KEY, batch_id BIGINT NOT NULL REFERENCES fin_import_batches(id),
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug), account_id BIGINT NOT NULL, amount_minor BIGINT NOT NULL, currency CHAR(3) NOT NULL,
  row_ref JSONB, tracking JSONB);
CREATE TABLE fin_stat_inputs (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), period_month DATE, available_rn INT, rooms_sold INT,
  rooms_rev_pms_minor BIGINT, ota_share NUMERIC(5,4), ota_commission_expected_minor BIGINT, currency CHAR(3) NOT NULL,
  fte_hours NUMERIC, overrides JSONB, computed_at TIMESTAMPTZ, PRIMARY KEY (hotel_slug, period_month));
CREATE TABLE fin_monthly_summary (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), period_month DATE, batch_id BIGINT,
  currency CHAR(3) NOT NULL, normalized BOOL, total_revenue_minor BIGINT, rooms_revenue_minor BIGINT, dept_profit_minor BIGINT,
  undistributed_minor BIGINT, gop_minor BIGINT, gop_margin NUMERIC(6,4), goppar_minor BIGINT, trevpar_minor BIGINT, revpar_minor BIGINT,
  payroll_minor BIGINT, labour_pct NUMERIC(6,4), flow_through NUMERIC(7,4), flow_through_basis TEXT, ebitda_minor BIGINT,
  net_profit_minor BIGINT, market_index JSONB, verified_profit_minor BIGINT, verified_profit_par_minor BIGINT,
  published_at TIMESTAMPTZ, PRIMARY KEY (hotel_slug, period_month));
CREATE TABLE fin_weekly (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), week_start DATE, available_rn INT, occupied_rn INT,
  rooms_revenue_minor BIGINT, distribution_cost_minor BIGINT, nrevpar_minor BIGINT, ota_cost_por_minor BIGINT,
  cancellation_leakage_minor BIGINT, recovery_cost_minor BIGINT, currency CHAR(3) NOT NULL, PRIMARY KEY (hotel_slug, week_start));
-- 1003_finance_anomalies_connections.sql
CREATE TABLE fin_anomalies (id BIGSERIAL PRIMARY KEY, hotel_slug TEXT NOT NULL REFERENCES hotels(slug), period_month DATE NOT NULL,
  fingerprint TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('labour_ratio','eww_ratio','line_spike','booked_net','rooms_rev_mismatch','pass_through_in_revenue','reconciliation')),
  usali_line TEXT, value NUMERIC, expected NUMERIC, delta_minor BIGINT, currency CHAR(3) NOT NULL, zscore NUMERIC, evidence JSONB,
  signal_id BIGINT, action_id BIGINT, status TEXT NOT NULL DEFAULT 'open', UNIQUE (hotel_slug, fingerprint));
CREATE TABLE fin_connections (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), provider TEXT CHECK (provider IN ('xero','qbo','zoho')),
  tenant_ref TEXT, status TEXT, last_sync_at TIMESTAMPTZ, PRIMARY KEY (hotel_slug, provider));   -- tokens live in core oauth_connections
-- 1004_finance_benchmarks_baselines.sql
CREATE TABLE fin_market_benchmarks (id BIGSERIAL PRIMARY KEY, region TEXT, metric TEXT, period TEXT, period_kind TEXT
  CHECK (period_kind IN ('month','ytd')), value NUMERIC, source_url TEXT NOT NULL, publisher TEXT, entered_by BIGINT,
  entered_at TIMESTAMPTZ);                                                   -- intentionally global
CREATE TABLE fin_baselines (hotel_slug TEXT NOT NULL REFERENCES hotels(slug), registered_at TIMESTAMPTZ NOT NULL,
  period_start DATE NOT NULL, period_end DATE NOT NULL, map_version_snapshot JSONB NOT NULL, metrics JSONB NOT NULL,
  currency CHAR(3) NOT NULL, registered_by BIGINT, PRIMARY KEY (hotel_slug, registered_at));   -- immutable: UPDATE/DELETE trigger raises
```

### 6.3 Import and connectors

- **Upload** follows the Files contract, then the `core/ingest` kinds `PL_STATEMENT` and `TRIAL_BALANCE` (D4), as XLSX, CSV or PDF. PDF has a totals gate.
  - The client calls `uploadFile(slug, file, 'finance.statement')` (upload ticket, then `POST /hotels/{slug}/files`, returning a `FileRef`), then `POST /hotels/{slug}/finance/imports {file_id, statement_kind, period_month?}`. The server creates a core `import_batches` row of kind `PL_STATEMENT` or `TRIAL_BALANCE`; ingest maps it to the canonical rows (D4) and calls finance's commit handler.
  - Section headers drive sign and group: Income/الإيرادات, Cost of Sales, Operating Expenses/المصروفات.
  - A file with several month columns is split by ingest into one row per month column; the handler creates one `fin_import_batches` row per month.
  - Tally Excel exports map these groups: Direct/Indirect Incomes, Direct/Indirect Expenses, Sales and Purchase Accounts, and Duties & Taxes (→ excluded).
- **Xero** (`core.oauth` provider `xero`):
  - `GET https://api.xero.com/connections` returns the tenant.
  - `GET https://api.xero.com/api.xro/2.0/Reports/ProfitAndLoss?fromDate&toDate&standardLayout=true` and `Reports/TrialBalance?date`, with the header `xero-tenant-id`. The `standardLayout` parameter is UNVERIFIED (7.7).
  - Parse `Rows` (Section, Row, SummaryRow) and take account ids from cell `Attributes`.
  - The Journals endpoint is not used, because it needs the Advanced tier.
  - The Starter tier allows only 5 connections (owner-kpis-pnl.md §3), and a pilot beyond 5 hotels needs Core (7.7).
- **QuickBooks Online** (`qbo`):
  - `GET /v3/company/{realmId}/reports/ProfitAndLoss?start_date&end_date&accounting_method=Accrual` and `reports/TrialBalance`. The endpoint and parameters are UNVERIFIED (7.7).
  - It serves UAE hotels only, because QBO is discontinued in India. Report reads are metered.
- **Zoho Books stub.** `ZohoAdapter.fetch_pl` raises `NotConnected("coming next")`. The research found no documented Reports module, so the plan is to build the P&L from `/chartofaccounts` and `/transactions`. Zoho's data centres include `.sa`.
- **Sync.** `finance.connector_sync` runs `monthly_at(3, "06:00")` for the previous month, and on demand.

### 6.4 USALI 12th-edition taxonomy and AI mapping

**Schedules** (owner-kpis-pnl.md, a secondary source; A9):
1. Rooms
2. F&B
3. Other Operated Departments
4. Miscellaneous Income
5. A&G
6. Information and Telecommunications Systems
7. Sales & Marketing, including the 12th-edition lines Paid Search, Display, Social and loyalty
8. Property Operations & Maintenance
9. Energy, Water & Waste (renamed from Utilities)
10. Management Fees
11. Non-Operating Income and Expenses

**Line keys** (a closed list; `UsaliLine` is a `Literal` generated from this table, and the LLM enum, the deterministic rules, the summary maths and the `fin.pl_line` dimensions all use it):

| Keys | Schedule | `usali_dept` | `category` | `is_payroll` |
|---|---|---|---|---|
| `rooms.revenue`, `fnb.revenue`, `ood.revenue` | 1, 2, 3 | rooms, fnb, ood | revenue | no |
| `rooms.cost_of_sales`, `fnb.cost_of_sales`, `ood.cost_of_sales` | 1, 2, 3 | rooms, fnb, ood | cost_of_sales | no |
| `rooms.payroll`, `fnb.payroll`, `ood.payroll` | 1, 2, 3 | rooms, fnb, ood | payroll | yes |
| `rooms.other`, `fnb.other`, `ood.other` | 1, 2, 3 | rooms, fnb, ood | other_expense | no |
| `rooms.other.commissions`, `fnb.other.commissions`, `ood.other.commissions` | 1, 2, 3 | rooms, fnb, ood | other_expense | no |
| `misc.income` | 4 | misc | revenue | no |
| `ag.payroll`, `its.payroll`, `sm.payroll`, `pom.payroll`, `eww.payroll` | 5, 6, 7, 8, 9 | ag, its, sm, pom, eww | undistributed | yes |
| `ag.other`, `its.other`, `sm.other`, `pom.other`, `eww.other` | 5, 6, 7, 8, 9 | ag, its, sm, pom, eww | undistributed | no |
| `sm.paid_search`, `sm.display`, `sm.social`, `sm.loyalty` | 7 | sm | undistributed | no |
| `mgmt_fee.base`, `mgmt_fee.incentive` | 10 | mgmt_fee | mgmt_fee | no |
| `non_operating.rent`, `non_operating.property_tax`, `non_operating.insurance`, `non_operating.other` | 11 | non_operating | non_operating | no |
| `below_line.depreciation`, `below_line.amortization`, `below_line.interest`, `below_line.income_tax`, `below_line.owner_drawings` | none | below_line | below_line | no |
| `excluded.vat`, `excluded.municipality_fee`, `excluded.tourism_dirham`, `excluded.balance_sheet` | none | excluded | excluded | no |

That is 45 keys. Departmental profit = revenue − cost_of_sales − payroll − other_expense per department; labour % uses every `is_payroll` key.

**Service charge.** There is one source of truth: Finance reads `profit_guard.api.treat_sc_as_revenue()` everywhere (weekly pulse and monthly mapping) and has no setting of its own. `false` means the service charge is a staff pass-through. It requires accountant confirmation (UNVERIFIED, owner-kpis-pnl.md). The finance settings page shows it read-only, with a link to Profit Guard's settings.

**Proposal pipeline:**
1. Deterministic rules (confidence 1.0):
   - VAT output/ضريبة القيمة المضافة → `excluded.vat`
   - Tourism Dirham/درهم السياحة → `excluded.tourism_dirham`
   - Municipality/رسوم البلدية → `excluded.municipality_fee`
   - depreciation/استهلاك, interest/فوائد, rent/إيجار, drawings → below GOP
   - DEWA/electricity/water → `eww`
2. The hotel's own locked mapping for the same `code+name_norm`.
3. Global memory for an exact `name_norm` confirmed by 3 or more hotels (confidence 0.9; non-Xero data only).
4. The LLM (`ai.structured(store=False)`), in batches of 60 accounts:

```python
SYSTEM = """Map hotel chart-of-accounts lines to USALI 12th edition line keys (enum below). UAE rules: VAT, municipality fee
and Tourism Dirham are pass-through taxes -> excluded.*; depreciation, amortisation, interest, rent, income tax and owner
drawings are below GOP; OTA/travel-agent commissions -> rooms.other.commissions. Names may be Arabic, English or Tally
ledger names. Use only name, group and type. Return a rationale citing the words that decided it. Never invent keys."""
class MappingProposal(BaseModel): account_ref: str; usali_line: UsaliLine; is_pass_through_tax: bool
    pass_through_kind: Literal['vat','municipality_fee','tourism_dirham'] | None; confidence: float
    rationale: str = Field(max_length=200); alternatives: list[Alt] = Field(max_length=2)
```

**Model input:** code, raw name, parent group, type and a magnitude bucket. Few-shot examples come **only** from this hotel's confirmed mappings.

**Confirmation:**
- Every new or changed account needs confirmation. The console offers "Confirm all ≥ 0.85" in bulk; anything below 0.85 is confirmed one at a time.
- A confirmed mapping is `locked` and reused every month.
- A change of name or group proposes a new version.
- A published batch snapshots `map_versions`.
- **Xero rule.** Xero-origin mappings are reused only for that hotel. `fin_mapping_memory` and other hotels' few-shot examples exclude `data_origin='xero'` (Xero ToS). A legal review of per-hotel LLM mapping on Xero data is listed in 7.7.
- Ask Clefs sees only confirmed mappings.

### 6.5 Checks before publish (stored in `checks`)

1. **Reconciliation.** Mapped net profit must equal the source net profit (the report's Net Profit, or the TB sum) within max(AED 1, 0.01%). Unmapped accounts block publishing.
2. **PMS rooms revenue.** P&L rooms revenue must be within 2% of PMS rooms revenue. Outside that it warns but does not block.
3. **OTA booked net.** Fires when all of these hold:
   - `ota_share ≥ 30%`
   - mapped `rooms.other.commissions` < 25% of `ota_commission_expected` (Σ `commission_fils` of OTA channels from `profit_guard.api.channel_economics(start, end)`; `ota_share` = OTA `room_nights` / all `room_nights`; PMS rooms revenue = Σ `room_rev_fils`), all held in `fin_stat_inputs`
   - `|PMS − P&L rooms − expected| ≤ 30% × expected`
   
   The flag produces a normalised view: rooms revenue is grossed up and the commission added. GOP is unchanged; margin, TRevPAR and RevPAR change.
4. **Pass-through.** Tax accounts must map to `excluded.*`. A ratio of P&L rooms to PMS net in [1.20, 1.27] suggests revenue booked gross of taxes, and is flagged.
5. **Below GOP.** Lexicon-matched depreciation, interest, rent and drawings cannot sit above GOP without an `override_reason`.
6. **Trial balance.** Debits must equal credits.
7. **Ratio bands.**
   - **Primary trigger:** z ≥ 2.5 with Δ ≥ 3 pp against the trailing 6 months.
   - **Secondary:** labour % outside [20%, 45%], or EWW % outside [3%, 12%]. These bands are assumed, configurable defaults (UNVERIFIED; the only research figure is a 2019 MENA labour share of about 27%).
8. **Revenue sign sanity.**

### 6.6 Outputs and golden values

**Formulas:**
- GOP = departmental profit − undistributed.
- Margin = GOP / Total Operating Revenue.
- GOPPAR = GOP / ARN.
- TRevPAR = TOR / ARN.
- Flow-through = ΔGOP / ΔTOR against the same month last year, else the prior month. It is n/a when |ΔTOR| < 1% of TOR.
- Labour % = all payroll / TOR.

**Golden, 2026-08** (ARN 2,480 = 80 × 31):
- **Revenue:** rooms 420,000, F&B 95,000, OOD 12,000, misc 3,000, so **TOR 530,000**.
- **Departmental expenses:**
  - rooms 150,000 (payroll 70,000, commissions 55,000, other 25,000)
  - F&B 70,000 (COS 32,000, payroll 30,000, other 8,000)
  - OOD 5,000
  - Departmental profit is 305,000.
- **Undistributed:** A&G 45,000 (payroll 25,000), I&TS 8,000, S&M 18,000 (payroll 8,000), POM 22,000 (payroll 10,000), EWW 38,000. Total 131,000.
- **Headline results:**

  | Measure | Value |
  |---|---|
  | GOP | **174,000** |
  | Margin | **32.83%** |
  | GOPPAR | **70.16** |
  | TRevPAR | **213.71** |
  | RevPAR | 169.35 |
  | Labour | 143,000, **26.98%** |

- **Below GOP:** insurance 6,000 gives EBITDA 168,000. Depreciation 30,000 and interest 12,000 give net profit 126,000, which equals the source.
- **Pass-through:** TD collected and remitted, 14,880 each, are both excluded. This golden assumes the exemption toggle is off for August 2026.
- **PMS check:** PMS rooms revenue of 424,100 is −0.97% away, which passes.
- **Flow-through:** against Aug 2025 (TOR 700,000, GOP 280,000), −106,000 / −170,000 = **62.35%**. The demo seeds 13 months so Aug 2025 exists (7.4).

**Booked-net golden, 2026-07:** PMS rooms revenue 400,000, OTA share 70%, expected commission 44,100, P&L rooms revenue 355,900 and a commission line of 0 give:
- `booked_net = true`
- a rooms difference of −11.03%
- normalised rooms revenue of 400,000
- GOP unchanged

### 6.7 Clefs-verified profit and market index

- **Clefs-verified profit** is the headline and the first tile.
  - It is Σ `actions.outcome_minor` where `status='verified'` and `counts_toward_verified_profit`, bucketed by `verified_at` in hotel tz, then divided by ARN.
  - Reversals are already netted, because reversed actions carry `outcome_minor = 0` (1.3).
  - Golden: 4,960 (Profit Guard 3,020 + inquiries 1,210 net of payment fees + amenities 730) / 2,480 = **AED 2.00 per available room**. The Aug 2026 ledger plants that produce it are listed by lane in 7.4.
- **Baseline.** At pilot start, `finance.register_baseline` stores 12 trailing months of P&L with the confirmed mapping in `fin_baselines`, which is immutable (owner-kpis-pnl.md §6).
- **GOPPAR is context only:**
  - The hotel's same-month year-on-year change is set against the market's from `fin_market_benchmarks`. Those are admin-entered with a `source_url`, for example HotStats UAE GOPPAR −47.8% YTD July 2026.
  - The comparison is seasonality-matched: same month against same month. When the market figure is YTD only, the hotel's YTD is used and the chip says "YTD".
  - Relative = (1 + hotel_yoy)/(1 + market_yoy) − 1.
  - Caption: "Market figures from public HotStats releases, mostly branded hotels; indicative; not a Clefs outcome."
- **No milestone tiers.** There is no target, tier or milestone field, route or metric, and a test asserts this.

### 6.8 Jobs, signals, metrics, API, console, tests

**Jobs:**
- `finance.weekly_rollup` (`weekly_at(1, "05:00")`)
- `finance.stat_inputs` (`monthly_at(2, "05:30")`)
- `finance.connector_sync` (`monthly_at(3, "06:00")`)
- `finance.map_propose` (task)
- `finance.anomaly_scan` (on publish)
- `finance.memory_rebuild` (weekly, non-Xero data only)
- `finance.register_baseline` (task)

**Signals:**

| Kind | Section | Cadence |
|---|---|---|
| `fin.nrevpar_weekly` | money_available | weekly |
| `fin.pl_pulse_ready` | verified | monthly |
| `fin.mapping_needed` | service_risk | monthly |
| `fin.anomaly` (the top 3 by \|delta\| × confidence, each leading to `finance.investigate_anomaly`) | money_lost | monthly |
| `fin.booked_net` | compliance | monthly |

**Metrics** (registered for Ask; P&L metrics read only confirmed, published data):
- `fin.nrevpar`, `fin.ota_cost_por`, `fin.cancellation_leakage`, `fin.recovery_cost`
- `fin.gop`, `fin.gop_margin_pct`, `fin.goppar`, `fin.trevpar`, `fin.revpar_pl`
- `fin.flow_through_pct`, `fin.labour_pct`
- `fin.dept_profit` (dimension: department)
- `fin.pl_line` (dimensions: usali_line, dept)
- `fin.verified_profit`, `fin.verified_profit_par`
- Money metrics are registered with `Metric(unit='money', currency='hotel')`; no key carries a currency (legacy tenants use INR).

**Routes (`/hotels/{slug}/finance/...`):**
- `weekly?from&to` (`finance.weekly.view`)
- `monthly/{month}`, `trend?months=12` and every P&L route below (`finance.pl.view` or the specific act permission)
- `imports` (POST `{file_id, statement_kind, period_month?}` after the Files upload, 6.3; GET), `imports/{id}`, `imports/{id}/publish`
- `mapping?status`, `mapping/confirm` (bulk)
- `connections`, `connections/{provider}/sync`
- `anomalies/{id}/action`
- `baseline` (GET, POST register)
- `benchmarks` (clefs_admin write)
- OAuth goes through core `/hotels/{slug}/oauth/{provider}/start` and `/public/oauth/{provider}/callback` (F6).

**Console (`(console)/finance/`):**
- **Access.** The nav tab gates on `finance.view` (nav.ts), which revenue now holds (7.1); revenue sees the weekly pulse only, and the P&L sections render only with `finance.pl.view`.
- **Empty state** (no published month yet): the upload call to action plus the weekly pulse only.
- **The monthly one-pager:** verified profit, then GOP, margin, GOPPAR with the market chip and caveat, TRevPAR, RevPAR, flow-through and labour %.
- A USALI summary table.
- A checks panel.
- The top 3 anomalies with "Create action".
- A 13-week NRevPAR strip.
- **Sub-pages:**
  - `mapping/`: grouped by department, AR `dir=rtl`, confidence pill, rationale, source, alternatives, bulk confirm
  - `imports/`, `trend/`
  - `settings/`: the ratio bands, and the service-charge treatment shown read-only from Profit Guard (6.4)

**Tests:**
- GOP, GOPPAR and NRevPAR goldens.
- Reconciliation to the source net profit, including the unmapped-account block.
- The booked-net month is flagged and normalised with GOP unchanged.
- Pass-through is excluded.
- The below-GOP guard.
- The labour anomaly in 2026-06, triggered by the z-score.
- Xero, QBO and Tally fixtures parse.
- Xero data is never in `fin_mapping_memory` or in cross-hotel few-shot examples.
- Locked reuse, and a new proposal on rename.
- `UNIQUE NULLS NOT DISTINCT` blocks duplicate accounts that have no code.
- No milestone fields.
- The flow-through n/a rule, and the flow-through golden against Aug 2025.
- Baseline immutability.
- Verified profit nets out reversals.
- `UsaliLine` has exactly the 45 keys of 6.4, and every key maps to one category.
- ARN = 0 and OccRN = 0 give NULL ratios ("n/a") and no division error.
- The finance goldens read `fin_stat_inputs` (demo overrides, 7.4), not live stays.
- The service charge follows `profit_guard.api.treat_sc_as_revenue` in both the weekly pulse and the monthly mapping.

---

## 7. Cross-cutting

### 7.1 Permissions (`Module.permissions`)

| Key | Roles | Notes |
|---|---|---|
| `core.autonomy.view` | owner, gm, finance, revenue, dept_head | |
| `core.autonomy.manage` | owner, gm | Levels up to `approve`; tightening limits; accepting promotions up to `approve` |
| `core.autonomy.raise_auto` | owner | Raise to `auto_within_limits` or `auto`; loosen limits; accept promotions above `approve` |
| `guests.journeys.view` | owner, gm, front_desk | |
| `guests.journeys.manage` | owner, gm | Templates, offers, settings; `decide_perm` for journey action types |
| `guests.contacts.capture` | owner, gm, front_desk, `device` | Registration-card capture. The route adds a Front Office department check for devices (3.10), because `device` covers every paired device |
| `inquiries.view`, `inquiries.respond` | owner, gm, revenue, front_desk | Contact details are masked per the Trust matrix (revenue sees none) |
| `inquiries.quote` | owner, gm, revenue, front_desk | |
| `inquiries.approve` | owner, gm, revenue | `decide_perm` for `send_quote`, `send_payment_link` and `follow_up`; below BAR, below floor and group quotes |
| `inquiries.manage` | owner, gm | Channels, payments, widget |
| `visibility.view` | owner, gm, revenue | |
| `visibility.act` | owner, gm, revenue | `decide_perm` for visibility action types |
| `visibility.manage` | owner, gm | |
| `finance.view` | owner, gm, finance, revenue | The nav tab and module gate (nav.ts) |
| `finance.weekly.view` | owner, gm, finance, revenue | The weekly pulse |
| `finance.pl.view` | owner, gm, finance | Every P&L route and section (monthly, trend, imports, mapping, anomalies, baseline) |
| `finance.act` | owner, gm, finance | `decide_perm` for `finance.investigate_anomaly` |
| `finance.import`, `finance.mapping.confirm`, `finance.publish`, `finance.connect` | owner, finance | |

- **Core permissions.** `core.autonomy.*` are core permissions, not module ones: no module may register a `core.` key. F-contracts registers them from `clefs/core/autonomy.py` with `clefs.auth.permissions.register_permissions({...}, module='core')` (which accepts the `core.` prefix), so frozen `CORE_PERMISSIONS` needs no edit, and lists them in `CONTRACTS.md` §6.
- **Prefixes.** Every permission above starts with its module name or route segment, as `register_permissions` requires; the short `inq.`, `vis.` and `fin.` prefixes are for signal and metric keys only (F9).

### 7.2 `api.py` contracts (stubs frozen by F-contracts in `FROZEN_API`; existing stubs extended, never broken)

**Exported:**

| Module | Function | Callers |
|---|---|---|
| guests | `record_own_contact(cur, hotel_slug, *, reservation_id=None, room=None, kind, value, capture_source, consents: dict[str, bool], consent_text_versions: dict[str, str], evidence: dict, actor) -> int`. Raises `ConsentNotAllowed` when `consents['transfer_abroad']` is not true with a text version (every `capture_source`), and for `guest_direct` creation or marketing consents from `j1_form` on OTA reservations (those become `guest_stay` points, 3.4) | concierge, service (console registration card), inquiries |
| guests | `is_known_guest_sender(cur, hotel_slug, *, phone_hmac=None, email_hmac=None) -> bool`: true when the hmac is on a reservation with status IN_HOUSE, or CONFIRMED with check-in within 1 d (F4) | inquiries |
| guests | `can_send_marketing(cur, hotel_slug, *, identity_hmacs: list[bytes], channel, at, phase: Literal['pre_stay','in_stay','post_stay']) -> tuple[bool, str]` | upsell, demand |
| guests | `record_external_send(cur, hotel_slug, *, identity_hmacs, channel, source, sent_at, ref) -> None` | demand (campaign export, counted at export time), upsell |
| guests | `opt_out(cur, hotel_slug, *, kind, value, source, scope='all') -> None` | concierge, service |
| guests | `journey_timeline(cur, hotel_slug, reservation_id) -> list[dict]` | service, brief |
| guests | `arrival_prefs(cur, hotel_slug, *, day) -> list[dict]` | service (handover) |
| guests | `marketing_audience(cur, hotel_slug, *, purpose='marketing', channel='whatsapp', segment: dict \| None = None) -> list[dict]` (existing signature, unchanged; now filtered by 3.4). `purpose='marketing'` maps to the consent purpose `f'marketing_{channel}'`. Each row's `contact_ref` is the hex `contact_hmac`, which Demand passes (as bytes) in `identity_hmacs` to `can_send_marketing` and `record_external_send`. `guest_stay`, `pms_ota` and `ota_alias` points are never returned | demand |
| guests | `guest_context_for_room` (existing, unchanged) | concierge |
| inquiries | `create_inquiry(cur, hotel_slug, *, source, text, contact: ContactIn, check_in=None, check_out=None, rooms=None, consent_basis='staff_logged', actor) -> dict` | concierge |
| inquiries | `brief_summary(cur, hotel_slug, *, day) -> dict` | brief |
| inquiries | `verified_outcomes(cur, hotel_slug, *, start, end) -> list[dict]` | trust (pilot report), finance |
| visibility | `ai_snapshot(...)` (existing stub, kept, 5.9); `start_ai_snapshot`, `get_ai_snapshot`, `purge_ai_snapshot` (5.9) | profit_guard |
| visibility | `parity_status(cur, hotel_slug, *, stay_date) -> dict \| None` | profit_guard, brief |
| visibility | `attribution(cur, hotel_slug, *, month) -> dict \| None` | finance |
| visibility | `open_fact_errors(cur, hotel_slug) -> list[dict]` | concierge (KB editor) |
| visibility | `gbp_location(cur, hotel_slug) -> str \| None` (the picked GBP location, 5.4) | reviews |
| finance | `weekly_pulse(cur, hotel_slug, *, week_start) -> dict \| None` | brief |
| finance | `monthly_summary(cur, hotel_slug, *, month) -> dict \| None` | brief, ask |
| finance | `verified_profit(cur, hotel_slug, *, start, end) -> dict` | trust (pilot report), brief |

**Required from others** (API money values are Decimal strings or minor-unit ints, as stated):

| Module | Function |
|---|---|
| demand | `price_guardrails(cur, hotel_slug, stay_date, room_type_code: str \| None = None) -> dict \| None`. The existing stub gains the optional argument. Keys: existing `{floor, ceiling, recommended, currency, confidence, reasons[]}` plus `bar`, `bar_captured_at`, `source` (`'rate_guardrails'\|'bar_only'\|'fallback'`) and `demand_index` |
| demand | `expected_adr(cur, hotel_slug, *, check_in, check_out, room_type_code=None) -> dict{amount, currency, source, confidence}` |
| demand | `comp_set(cur, hotel_slug) -> list[dict{comp_id, name, property_token, place_id, lat, lng, stars, aliases}]`. `place_id` is set once from settings, or with one Places lookup using mask `places.id,places.displayName` (a Pro SKU call) |
| demand | `area_queries(cur, hotel_slug) -> list[str]` (the single source of Google Hotels `q`); `comp_rates` (existing); `comp_rates_snapshot(cur, hotel_slug, *, stay_dates) -> dict` |
| demand | `upcoming_events(cur, hotel_slug, *, days=60, min_score=0.3)`, `find_event(cur, hotel_slug, *, text)`, `demand_index(cur, hotel_slug, *, stay_date) -> int \| None` |
| profit_guard | `record_ota_leak(cur, hotel_slug, *, source, inquiry_ref, reservation_ref, channel_code, evidence, commission_minor: int \| None = None, currency: str \| None = None) -> dict \| None`. The existing stub is extended; `currency=None` means the hotel's currency. It returns `{finding_ref, commission_minor, currency, basis: 'statement_line'\|'expected'\|'provided'}` and fills the commission itself when none is given |
| profit_guard | `channel_economics(cur, hotel_slug, *, start, end)` (existing). Rows carry at least `channel, room_nights, room_rev_fils, commission_fils` (02 §3.6), which finance check 3 reads (6.5) |
| profit_guard | `expected_commission(cur, hotel_slug, *, ota_code, breakdown: StayTaxBreakdown, on: date) -> dict{commission_minor, base_minor, pct, currency, basis}`. `breakdown` comes from `core.tax.compose_stay` or `core.stays.reservation_breakdown(cur, reservation_id)` (D3); callers always pass keyword arguments. `reference_ota(cur, hotel_slug, *, as_of) -> tuple[str, Decimal]` |
| profit_guard | `distribution_cost_by_night(cur, hotel_slug, *, start, end) -> list[DistNight]`, `DistNight{stay_date, channel_code, is_ota: bool, commission_minor, payment_fee_minor, booking_engine_fee_minor, currency}`; `distribution_cost_for_reservation(cur, hotel_slug, *, reservation_id) -> int`; `cancellation_cost_by_date(cur, hotel_slug, *, start, end) -> list[CxlCost]`, `CxlCost{cancel_date, channel_code, cost_minor, currency}` (TypedDicts) |
| profit_guard | `record_rate_evidence(cur, hotel_slug, *, evidence: dict, emit_signal: bool = False) -> int`. `emit_signal` is persisted on Profit Guard's rate-evidence table (`pfg_rate_evidence`, per the registry's `pfg_` prefix), and L7 filters on it. `parity_tolerance_pct(cur, hotel_slug) -> Decimal`; `treat_sc_as_revenue(cur, hotel_slug) -> bool` (the single source for the service-charge treatment, 6.4) |
| service | `create_request` (existing). `SOURCES` adds `whatsapp_journey`, `phone_desk`, `inquiries`, `j7_feedback` and `journey_gate`. `REQUEST_TYPE_FOR_DEPARTMENT: dict[str, str]` plus a seeded `guest_feedback` type |
| service | `create_followup(cur, hotel_slug, *, kind, summary, guest_ref, owner_role, due_at, source, language=None, room=None) -> dict`. No ack ladder, for guests who have left or have no room. `kind` is one of `guest_feedback` (J7 low rating), `post_stay_recovery` (J6_post), `guest_issue` (J5 negative after check-out) and `guest_contact` (inquiries rerouting) |
| service | `open_items_for_stay(cur, hotel_slug, *, room, since, until=None) -> list[{id, request_type, severity: 'L1'\|'L2'\|'L3', status, created_at}]`, `has_open_complaint(cur, hotel_slug, *, room=None, reservation_id=None, since) -> bool`, `recovery_cost(cur, hotel_slug, *, start, end) -> int`, `on_duty(cur, hotel_slug, *, role, at) -> list[dict]` |
| concierge | `kb_facts(cur, hotel_slug, *, language='en', keyed_only=False) -> list[dict]`. The existing stub is extended; dicts gain `fact_key`, `value`, `entry_id` and `approved_at` |
| concierge | `propose_kb_entry(cur, hotel_slug, *, question_en, answer_en, question_ar=None, answer_ar=None, fact_key=None, source, actor) -> int`; `kb_entry_status(cur, hotel_slug, entry_id) -> dict \| None` |
| reviews | `draft_reply(cur, hotel_slug, review_id) -> dict \| None` (existing); `review_counts(cur, hotel_slug, *, start, end, platform=None) -> dict`; `reply_stats(cur, hotel_slug, *, platform='google', days=90) -> dict{unanswered, response_rate, median_reply_h}` |
| brief | `wa_template_info(cur, hotel_slug, *, name, language) -> dict \| None {status, category, quality}`. The stub returns None, so no WhatsApp template counts as Meta-approved: WhatsApp journey sends are ineligible and steps fall through to email (3.4); a step is `observe` only when no channel has an approved template. Inquiry template sends treat None as category unknown (4.6). `submit_template(cur, hotel_slug, *, name, language, category, components) -> dict`. The stub raises `NotConnected` |

**Stub returns.** Each stub ships with a safe default, and every caller handles it (lanes C and V, and lanes I and D, ship in the same wave):

| Function | Stub returns | Caller behaviour |
|---|---|---|
| `demand.area_queries` | `[f"hotels in {hotel.city}"]` | Google Hotels still runs on one generic query |
| `demand.comp_set` | `[]` | No competitor columns; SoV shows the hotel against "other hotels" only |
| `demand.expected_adr` | None | Inquiries use the trailing-28-day same-weekday ADR from `core/stays` (4.5) |
| `demand.price_guardrails` | None | Manual rate entry, no auto-send (4.6) |
| `demand.comp_rates_snapshot`, `upcoming_events`, `find_event`, `demand_index` | `{}`, `[]`, None, None | No comp snapshot; no event prompts; dates stay flexible; standard validity |
| `concierge.kb_facts(keyed_only=True)` | `[]` | Fact errors are `unverifiable`; no lodging diff |
| `concierge.propose_kb_entry`, `kb_entry_status` | raise `NotConfigured`, None | `add_kb_faq` stays a human task |
| `reviews.reply_stats`, `review_counts` | None, `{}` | The UI hides those tiles |
| `profit_guard.record_ota_leak` | None | Inquiries compute `expected_commission` themselves for the internal record |
| `profit_guard.expected_commission`, `reference_ota` | None, None | Commission avoided is NULL and the outcome counts nothing |
| `profit_guard.distribution_cost_by_night`, `cancellation_cost_by_date`, `distribution_cost_for_reservation` | `[]`, `[]`, 0 | The pulse shows Dist and leakage as "n/a" |
| `profit_guard.record_rate_evidence`, `parity_tolerance_pct`, `treat_sc_as_revenue` | 0, `Decimal("1.0")`, True | |
| `visibility.parity_status`, `start_ai_snapshot`, `attribution`, `gbp_location` | None | Callers show "not connected" |
| `service.open_items_for_stay`, `has_open_complaint`, `recovery_cost`, `on_duty` | `[]`, False, 0, `[]` | J6 finds nothing; no complaint pause |
| `guests.*` new functions | `[]`, False, or no-op | Demand exports stay blocked until `can_send_marketing` is real (it returns `(False, 'not_built')`) |

### 7.3 Brief cadence (F7)

- **Daily** (money at risk and service L1 only, Canon 18):
  - `inq.unanswered`
  - `inq.quote_awaiting_approval`, only while the guest has had no substantive reply and the first-response SLA is breached (the unanswered-inquiry case)
  - `vis.parity_gap_google`
  - `guests.checkout_open_issues`, only when a linked item is L1
  - `inq.paid_not_in_pms`, only when check-in is within 48 h
- **Weekly:**
  - all other `vis.*`
  - `inq.leaked_to_ota`, `inq.pipeline_weekly`, `inq.lead_fetch_failed`, and `inq.quote_awaiting_approval` otherwise
  - `guests.wa_billing_not_ready`
  - `fin.nrevpar_weekly`
  - `guests.*` weekly kinds, including the non-L1 checkout issues
  - `inq.paid_not_in_pms` otherwise
  - the autonomy block (`core.autonomy_promotion`, `core.autonomy_demoted`)
- **Monthly:** `fin.pl_pulse_ready`, `fin.anomaly`, `fin.booked_net`, `fin.mapping_needed`, `vis.google_attribution`.

### 7.4 Demo dataset additions

Each lane ships `modules/<m>/demo.py` with `seed_demo(cur, hotel_slug, rng, anchor)`, which returns plants for `expected_plants.json`.
- The slug is `demo-dubai` (the implemented CLI and seed), the seed is 20261001 and the anchor is 2026-09-28.
- This supersedes 02 §9's `demo-deira-crest` and 2026-09-27.
- No wall clock is read, and every name carries "(DEMO)".
- **Pinned hotel attributes.** The foundation placeholder in `server/clefs/seed/demo_uae.py` creates `demo-dubai` as 4-star with 50 rooms, which reproduces none of the goldens. The Wave 3 demo lane updates the existing row idempotently to 02 §9's property: `star_rating=3`, `rooms_count=80` (8 floors × 10 rooms), area Deira, tax convention A, and OTA contracts Booking 15%, Agoda 18%, Expedia 18%. The goldens (TD AED 10: 1,008.68, 504.34, 126.45, 182.52; ARN 560 and 2,480) depend on these values.
- **Golden unit tests** do not use the conftest `hotel_uae` (4-star, 13 rooms). Each lane's golden tests use a dedicated fixture hotel with 3 stars, 80 rooms and convention A.

**Inquiries** (about 60 across channels: WhatsApp 22, including 6 CTWA with `ctwa_clid`; email 12; web 14; Meta 5; Google 4 plus 1 duplicate delivery; phone 3):
- **Unanswered:** 2 past SLA.
  - A long stay: 1 × 30 × AED 300 × 0.10 = 900.
  - A corporate booking in GITEX week, 7-11 Dec 2026: 5 × 5 × 650 × 0.10 = 1,625.
  - The brief reads "2 unanswered > 2 h, AED 2.5k at risk (est.)".
- **Group quote awaiting approval:** 12 rooms × 3 nights at 10% below BAR.
- **Leak:** a web inquiry on 2026-09-10, and a Booking reservation on 2026-09-14 whose Booking-provided real phone (`pms_ota`) has the same phone hmac. It gives AED 182.52.
- **Verified booking:** an N-Genius paid-webhook fixture for a 504.34 first-night deposit (demo fee setting 2.5%) and a DIRECT_WEB match. Revenue is 800.00; commission avoided is 139.06 gross and 126.45 net. It verifies in September 2026, so it is outside the August verified-profit golden.
- **Aug 2026 verified outcomes** (for the 6.7 golden): four verified inquiry outcomes with `verified_at` in August 2026 and `outcome_minor` 402.10, 318.75, 276.40 and 212.75 (net of payment fees), totalling **1,210.00**.
- **Decoys:**
  - a same-name OTA booking at +35 d
  - the same name with a different phone
  - a real web email against a Booking `@guest.booking.com` alias (no match)
  - an at-BAR FIT quote that auto-sends
  - the Google duplicate

**Visibility:**
- Google Hotels details with and without an official price, the parity plant 450 vs 412, and a 0.78% decoy.
  - The parity plant's stay date is 2026-10-02 (the Friday after the anchor), with `otb_snapshots.by_channel` BOOKING = 14 for that date, which gives the 38 × 14 = AED 532 exposure.
- GBP: `vis_gbp_kpis_monthly` for 4 months (derived and retained), plus only the last 30 d of raw `vis_gbp_daily`, so `gbp_raw_expire` deletes nothing on day one. Keywords with thresholds, also within 30 d.
- A rank-grid fixture.
- AI answer fixtures with mentions and competitor wins, and 2 planted fact errors: check-in 12:00 vs KB 14:00, and "no parking" vs KB paid parking.
  - Volume: 8 weekly batches for each of Gemini and AI Mode (the full 24-prompt set, 96 runs per engine per week), with `k` values that make the UAE-EN cells sufficient and shown. One source market is seeded in the latest week only (n = 8), so its cell shows "insufficient sample (n=8)". (A UAE-AR 4-week cell reaches n = 80 and cannot be insufficient.)
- One model-version change.
- Site health: CrUX with LCP 3.1 s, which fires `vis.site_health_not_good`, and JSON-LD missing `checkinTime`.

**Finance:**
- 13 months of P&L CSV (2025-08 to 2026-08) with mixed EN/AR names, including "Room Revenue / إيرادات الغرف", "رواتب الغرف", "DEWA Electricity", "Tourism Dirham Collected" and "رسوم البلدية". Aug 2025 has TOR 700,000 and GOP 280,000.
- **Statistical inputs.** The finance goldens read `fin_stat_inputs`, so `finance/demo.py` seeds `overrides` for 2026-07 (PMS rooms revenue 400,000, OTA share 70%, expected OTA commission 44,100) and 2026-08 (PMS rooms revenue 424,100; ARN 2,480) instead of deriving them from the stays and Profit Guard seeds of other lanes.
- **The other months** are generated under constraints rather than hand-listed: labour % in [25.5%, 27.5%] and not all equal (sd ≥ 0.3 pp), and every other line's share of TOR within ±1 pp of its Aug 2026 share. Then only the planted anomalies fire, and the finance demo test asserts that no anomaly or booked-net flag appears outside these two months:
  - The labour anomaly in 2026-06: TOR 450,000 with payroll 142,000 (31.56%, at least 4 pp above the trailing 6-month mean).
  - The booked-net plant in 2026-07: P&L rooms revenue 355,900 with a commission line of 0.
- The 2026-08 golden.
- Xero and QBO report fixtures.
- A registered baseline.

**Journeys:**
- Sends across the 120-day history.
- One J5 "AC is not cooling in 504" that opened a ticket.
- Booking and Expedia alias contacts, with Expedia's J7 skipped as `alias_expired`.
- One Booking guest whose own mobile, WhatsApp opt-in, marketing consent and transfer consent were captured on the registration card (evidence: a crop of the contact-and-consent block only, no ID fields, 3.4), and who later receives J8.
- One Booking guest who gave a phone on the J1 page: a `guest_stay` point that receives J4 and J5 but never J8.
- One STOP and one 131049.
- J7 feedback at ratings 1-5, all with identical links.

**Autonomy:**
- Type defaults, with no `action_autonomy` rows until something is set (1.4).
- `reviews.reply_positive` with 52 decisions, 50 of them unedited (96.2%), which produces a promotion suggestion from `suggest` to `approve`. The demo Google source has `auto_reply_consent=false`, so `auto` stays refused (409) until consent is recorded.
- `guests.journey_j3_offer` with 40 delivered sends and 4 opt-outs attributed to J3 in 7 d (10%), which demotes it to `approve`.
- **Aug 2026 verified profit plants by lane** (the 6.7 golden of 4,960): Profit Guard verified findings totalling 3,020 (lane C lists them in its plants); the four inquiry outcomes totalling 1,210 (above); amenities outcomes totalling 730 (lane E).

### 7.5 File ownership

| Lane (wave) | Owns |
|---|---|
| F-contracts (0a, per `server/clefs/CONTRACTS.md` Part 2) | <ul><li>`core/actions.py` plus the sibling `core/autonomy.py` (routes on the hooked `clefs.core.actions` router), and the §1.3 ledger columns in the actions migration</li><li>migrations 0017-0019 inside its 0010-0019 range (or folded into its earlier files, 1.2)</li><li>`core/serpapi.py`, `core/oauth.py` (OAuth routes per F6)</li><li>`core/privacy.py` additions (F8)</li><li>`notify` additions (F4)</li><li>`jobs.py` schedules (F3)</li><li>`ai.responses_raw` and `store`</li><li>`register_signal_kind` cadence (F7)</li><li>`KEY_PREFIXES` in `core/signals.py` and the Registries contract test (F9)</li><li>`core.autonomy.*` registered with `module='core'` (7.1)</li><li>the 7.2 stubs in each module's `api.py`, and `FROZEN_API` (F10)</li><li>the `CONTRACTS.md` §13 listing and Part 2, including stay events</li><li>`tests/foundation/test_autonomy.py`</li></ul> |
| Integrator | Frozen files, from FOUNDATION-REQUEST lines only: `clefs.core.oauth` in `CORE_ROUTER_HOOKS` (`app.py`), `cryptography` in the requirements, and the new variables in `server/.env.example` (7.6). `registry.py`, `settings.py` and `auth/permissions.py` need no edit under this spec. Applies the 0.7 Canon amendments |
| F-client (0a) | `client/app/app/[hotelSlug]/(console)/settings/autonomy/**` (reassigned to lane G by the integrator if F-client has closed). The `nav.ts` entries for inquiries, visibility, finance and guests already exist |
| F-domain (0b) | D1-D4: `core/stays` (contacts, events, stay chain, check-in columns, UTM and promo columns, contact purge, dispatch), `core.tax.compose_stay` with the 4.6 goldens, the ingest kinds |
| F2 Guests (1) | `server/clefs/modules/guests/journeys/**`, `guests/migrations/0670-0689`, journey action types in `guests/actions.py`, `(console)/guests/journeys/**`, `client/app/g/**`, `tests/modules/guests/test_journeys_*` |
| H Trust + Marketing (1) | `client/app/lib/claims.ts`, `client/app/components/Claim.tsx`, `client/scripts/check-claims.mjs`, `client/scripts/__tests__/**`, `client/scripts/__fixtures__/claims/**`, `client/app/google-check/**` (UI), and moving marketing copy onto claims |
| I Inquiry Guard (2) | `server/clefs/modules/inquiries/**` (including `intake/`, `payments/`, `quotes.py`, `matching.py`), `(console)/inquiries/**`, `client/app/w/**`, `client/app/q/**`, `tests/modules/inquiries/**` |
| V Visibility (2) | `server/clefs/modules/visibility/**` (including `engines/`, `parsers/`, `google/`, `rank/`, `gbp/`, `site/`, `public.py`), `(console)/visibility/**`, `tests/modules/visibility/**` with fixtures |
| P Finance (2) | `server/clefs/modules/finance/**` (including `connectors/{xero,qbo,zoho}.py`, `mapping.py`, `checks.py`, `pulse.py`), `(console)/finance/**`, `tests/modules/finance/**` |
| A Service (1) | 7.2 service functions; `SOURCES` and request types; the PhoneEntryConsole link to `/app/{slug}/inquiries/new?source=phone_desk&room=` |
| B Concierge (1) | `kb_entries.fact_key` with the 5.5 vocabulary; `kb_facts` extension; `propose_kb_entry`; `kb_entry_status`; the concierge ConsentSheet capturing `whatsapp_updates`, marketing consents and a separate `transfer_abroad` tick through `record_own_contact` |
| F1 Reviews (1) | `reviews.api.review_counts` and `reply_stats`; Reviews uses the `core.oauth` `google_business` connection, Visibility's `gbp_location` (5.4), and the `<alias>+reviews@` address (F4); its SerpAPI Google Maps sync goes through `core.serpapi.search` with `purpose='reviews.sync'` (F5); the `auto_reply_consent` gate for `reviews.reply_positive` (1.4) |
| C Profit Guard (2) | 7.2 profit_guard functions; `pfg_rate_evidence.emit_signal` persisted and filtered in L7; the snapshot section showing 30 or 45 runs |
| D Demand (2) | `area_queries`; `comp_set.place_id`; `SerpApiGoogleHotels` calls `core.serpapi.search('google_hotels', google_hotels_area_params(...), hotel_slug=..., purpose='demand.comp_rates')` and treats `BudgetExhausted` as a skipped fetch; the `price_guardrails` extension; calling `record_external_send` on campaign export |
| G Brief + Ask (2) | `wa_template_info` and `submit_template`; `wa_outbox` delegating to `core.notify.deliver`; rendering the 7.3 cadence and the autonomy block |

### 7.6 Environment variables (read through `clefs.settings.env(name, default)`; existing names reused)

`settings.py` is frozen. Modules and F-contracts read the new variables with `clefs.settings.env(name, default)` and never edit `settings.py`; each new name is added to `server/.env.example` through a FOUNDATION-REQUEST (integrator).

- **PII:** `PII_ENC_KEY`, `CONTACT_HMAC_PEPPER`.
- **SerpApi:** `SERPAPI_KEY` (exists), `SERPAPI_MONTHLY_BUDGET_USD`, `PUBLIC_SERPAPI_MONTHLY_CAP`.
- **Google:**
  - `GOOGLE_MAPS_API_KEY` (exists)
  - `GOOGLE_API_KEY` (CrUX, PSI)
  - `GBP_OAUTH_CLIENT_ID` and `GBP_OAUTH_CLIENT_SECRET` (exist; used by `core.oauth` `google_business`)
  - `OAUTH_REDIRECT_BASE`
- **Visibility AI:** `AI_MODEL_VISIBILITY_GEMINI` (the `AI_MODEL_<TASK>` pattern), `VISIBILITY_AI_ENABLED`, `PERPLEXITY_API_KEY` (exists), `PERPLEXITY_MODEL`, `PERPLEXITY_TERMS_APPROVED`.
- **Meta:** `META_APP_SECRET` (exists), `META_LEADGEN_VERIFY_TOKEN`. Graph calls use `WA_API_VERSION` (exists). (No CAPI flag: CAPI is not built, 4.3.)
- **Google Ads lead forms:** `GOOGLE_ADS_LEAD_KEY` (exists) serves the demo and sandbox only. Per-hotel keys are sealed in `inq_channel_accounts`.
- **Accounting:** `XERO_*` and `QBO_CLIENT_*` exist; add `QBO_ENV`, `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`.
- **Payments:**
  - The existing single-merchant variables (`PAYMENT_PROVIDER`, `NGENIUS_*`, `TELR_*`, `STRIPE_*`, `TAP_*`) serve the demo hotel and sandbox tests only. Production credentials are per hotel, sealed in `inq_channel_accounts`.
  - Add `TELR_API_BASE`, `STRIPE_API_BASE` and `TAP_API_BASE`.
- **Journeys:** `OTA_ALIAS_DOMAINS`, `WA_SERVICE_FREE_ALLOWANCE` (default 0).
- **Public caps:** `PUBLIC_CHECK_DAILY_CAP` (20), `SNAPSHOT_DAILY_CAP` (10), `SNAPSHOT_MONTHLY_BUDGET_USD` (50).

### 7.7 Verify before build

- **Payment APIs:**
  - Telr hosted links
  - Stripe webhook tolerance and fields
  - N-Genius access-token flow and webhook secret
  - Tap signature and `GET /v2/invoices/{id}`
  - whether each provider reports fees
- **Perplexity API terms** (legal).
- **OTA contact lifetimes and domains (A2):** the Agoda alias domain, the Expedia alias lifetime, and the Booking alias lifetime.
- **USALI and ARN:** the 12th-edition line definitions against the book, and the ARN convention (A9, A10).
- **WhatsApp billing:** Meta's AED rate card, the service-message allowance per number (A4), and a Meta payment method on each hotel's WABA before 2026-09-30, without which service messages reportedly stop being delivered (3.5).
- **WhatsApp templates:**
  - whether J5 and J7 stay UTILITY after submission (A1)
  - the semantics of Meta errors 131047 and 131026
  - dynamic URL button suffixes for `/g/{token}` and `/g/f/{token}`
- **GBP:**
  - project-level API approval for Clefs' Cloud project. Prerequisites: Clefs' own Business Profile verified and active for at least 60 days, a website on it, and the Basic API Access form; 0 QPM until approval, 300 QPM after (5.4). **Owner action now:** create and verify the profile to start the 60-day clock, because this is launch-critical lead time
  - the Lodging API field names in the 5.4 mapping (`pools.pool`, `parking.freeParking` and the rest)
  - the Performance-data caching and aggregation policy (30-day raw expiry; whether derived monthly KPIs are allowed)
  - the Performance API history window for re-fetching year-ago data
  - the host of the GBP "Get more reviews" share link
- **Visibility vendors:** the Places vs Maps calibration result (A13), and SerpApi AI Mode's credit cost.
- **Attribution:** whether pilot booking engines pass `utm_*` and promo codes into their exports (D2).
- **UAE telemarketing:** the DNCR check mechanism; Cabinet Resolution 56/2024's prior-approval requirement and its business-identity requirement (both from secondary sources); and whether hotel WhatsApp to its own consenting guests is in scope.
- **PDPL (counsel sign-off):**
  - AI processing of inbound inquiries and journey replies without express `transfer_abroad` consent (`ai_on_unconsented_inbound`, default false until sign-off)
  - the basis for J7 (contract or consent)
  - whether GDT 2.9.3 applies to J7 sent through OTA contacts
- **Xero:**
  - a developer tier for more than 5 hotels (Starter 5 connections, Core AUD 35 for 50)
  - a legal review of the no-AI/ML-training clause against per-hotel few-shot reuse and LLM mapping
  - the report parameters for Xero (`standardLayout`) and QBO
- **Dubai 2026 fees:** the municipality fee and TD exemption dates (owner toggle), and whether the per-bedroom TD rule applies to hotels as well as hotel apartments.
- **Next, not in this build: Meta CAPI.** Sending a Purchase event with `ctwa_clid`, value and currency to Meta is ad measurement, not performance of the stay contract, and it is a cross-border transfer. When it is built it needs a separately recorded consent (purpose `ad_measurement`, EN/AR text) plus `transfer_abroad` on the inquiry before any call, default off, counsel sign-off on its PDPL lawful basis, the DPA sub-processor listing, and a test that no call is made without both consents. The API details are from a secondary source.
- **Group terms:** Dubai group-quote norms (A12).

### 7.8 End-to-end QA additions (Wave 3, with `NOTIFY_MODE=capture`)

1. **Inquiry flow:**
   1. An inquiry arrives through the web form, with both required consents.
   2. The ack is captured.
   3. The brief shows "AED at risk (est.)".
   4. A quote is approved with a payment link.
   5. **A matching DIRECT_WEB reservation fixture is imported**.
   6. The payment-webhook fixture fires.
   7. The verification shows gross and net values.

   Without step 5 the flow ends in `inq.paid_not_in_pms`, not `verified`.
2. **Journeys:** a J5 reply opens a ticket; J6 creates the recovery task; the J7 page shows the same links at every rating.
3. **Autonomy:** accept the `reviews.reply_positive` promotion (`suggest` to `approve`). Setting it to `auto` returns 409 `auto_reply_consent_required`; after recording `auto_reply_consent` on the demo Google source (who and when), `auto` is accepted, and a 3★ review is still capped at `approve`. Setting a refund type to `auto` in the UI is refused.
4. **Visibility:** the fact-error evidence drawer shows clickable citations, and "Create fix" puts the action in the ledger.
5. **Finance:** a P&L upload, then mapping confirmation, then the one-pager with the flow-through golden.
6. **Google check:** the teaser, then the unlock, then the 503 once the budget cap is exhausted.

### 7.9 Canon 13/14 route extensions

**Server:**
- `/hotels/{slug}/oauth/{provider}/start` and `/public/oauth/{provider}/callback` (core, F6)
- `/webhooks/inquiries/{meta-leadgen, google-leadform/{key}, payments/{provider}/{key}}`
- `/public/journeys/*`
- `/public/inquiries/*`
- `/public/visibility/google-check*`

**Client:**
- `/g/[token]` and `/g/f/[token]`
- `/q/[token]`
- `/w/inquiry.js` and `/w/inquiry/[slug]`
- `/google-check` and `/google-check/r/[token]`
- `/w/inquiry.js` and `/google-check` are already in Canon 14. The others are a pending Canon 14 amendment (0.7 K4), which the integrator applies before lanes F2, H and I merge.

**Crawling:** all are noindex except `/google-check`. `robots.ts` (lane H) disallows `/g/`, `/q/`, `/w/` and `/google-check/r/`.

---

## Appendix A. Review decisions (points rejected or only partly accepted)

| Review point | Decision and reason |
|---|---|
| Research 3 / Contract 31: express `transfer_abroad` consent for all intake | **Accepted** for the web form, the `/g` page and the Google check (separate required ticks). **Partly accepted** for inbound WhatsApp, email, Meta and Google leads. Consent cannot be collected before the first message, and gating would break speed-to-lead. Instead the consent basis is recorded, the first ack carries the notice, a per-hotel switch `ai_on_unconsented_inbound` turns the LLM off, and counsel sign-off is listed in 7.7 |
| Research 32 vs Contract 24: cadence of `inq.paid_not_in_pms` | The reviews disagreed. Adopted: an immediate front-desk task, weekly in the brief, and daily only when check-in is within 48 h (a service L1 equivalent under Canon 18). Impact = the paid amount, as Contract 24 asked |
| Contract 2: autonomy migration at 0012-0014 | The intent is accepted: after the actions table, inside F-contracts' sub-range. The numbers used are 0017-0019, which leaves F-contracts 0010-0016 without renumbering its own files |
| Research 11: keep only derived GBP KPIs | Raw series and keywords expire at 30 d, as asked. Derived monthly KPIs are retained, following the uae/zz-critique.md C10 resolution for reviews. Because privacy-reviews.md B1 says content "cannot be aggregated", the question is listed in 7.7 |
| Contract 54: Demand exports "not counted" | Changed to conservative counting. `record_external_send` counts every exported contact as sent at export time, so the post-stay cap of 2 per 30 days still holds |
| Contract 56: pin one slug | Pinned `demo-dubai`, which the implemented CLI and `seed/demo_uae.py` already use, rather than 02's `demo-deira-crest`. The anchor is 2026-09-28 |
| Research 4: `j7_for_ota_provided_contacts=False` "for Booking-sourced contacts" | Applied to all OTA-provided contacts (Booking, Agoda, Expedia). The same legal question (7.7) applies to each OTA |
| Research 13: snapshot and public-check caps | Accepted, and the defaults were lowered further (`PUBLIC_CHECK_DAILY_CAP` 20, `SNAPSHOT_DAILY_CAP` 10), because the review's arithmetic showed the draft caps exceeded SerpApi plans |
| Where a review offered alternatives | Contract 1: the prefix map, not renaming keys. Contract 17: `create_followup`. Contract 28: the follow-up template. Contract 16: a human task for J3 acceptance. Research 2: registration-card evidence for the demo plant |
| Research 9: J8 net outcome | Accepted. The netting can make an outcome negative for guests whose previous stay was direct. That is recorded as is rather than floored at zero, so verified profit is not overstated (A15) |
| Third review (research, contracts, implementability), rows below | Everything not listed here was applied as proposed |
| `ai_on_unconsented_inbound` default | Now **false** until counsel signs off, and it also covers `staff_logged` inquiries and journey replies (3.8, 4.4). This narrows the first row: only the notice-on-first-ack part of that partial acceptance remains |
| Canon edits (commission base, vendor micros, routes, claim ids, new action statuses) | 00-CANON.md is outside this spec's edit scope, so they are listed as pending amendments 0.7 K1-K5 for the integrator, with fallback goldens for K2, rather than applied |
| `observed` via `action_events` only (contract alternative) | Rejected: `action_events.action_id` is NOT NULL (F-contracts' 0011), so an observed step needs an `actions` row. Kept as K1; the implemented CHECK already has it |
| Meta CAPI consent gate (research) | Superseded: CAPI is removed from this build (implementability, PLAN lite scope). The consent gate is recorded as its precondition under "next" in 7.7 |
| Add `reject`/`snooze` to `decide()` and `initial_status='done'` (implementability) | Rejected: `ignore` already moves an action to `rejected` (contract review). `inquiries.direct_booking` uses a no-op executor instead of a `done` insert, keeping the Canon 7 path and `action_events` |
| F9: rename keys to full prefixes (option a) | Not taken: option (b), `KEY_PREFIXES` in `core/signals.py` and never for permissions, keeps 02/03's short prefixes working |
| Integrator owns `KEY_PREFIXES` and `core.autonomy.*` in `CORE_PERMISSIONS` (implementability) | Partly: F-contracts does both without touching frozen files (`core/signals.py`; `register_permissions(module='core')`). The integrator applies only the hooks, requirements and `.env.example` requests |
| `UNIQUE NULLS NOT DISTINCT` on contact points | Replaced by a partial unique index with `COALESCE(reservation_id, 0)`, because purged OTA rows (hmac NULL) would otherwise collide |
| Extend `record_consent` with version and language | Not needed: the implemented `core.consent.record_consent` already takes `text_version` and `language`; F2 passes them |
| 11-month P&L table (implementability) | Replaced by generation constraints plus a test that no anomaly fires outside the two planted months; a hand table adds numbers, not checks |
| One insufficient UAE-AR demo cell | Not possible: a 4-week AR cell has n = 80 and a Wilson half-width of at most 0.11. A one-week source-market cell (n = 8) is used instead |
| HBR "nearly 7x" wording | The research (inquiry-crm.md) says "about 7x"; the registry quotes that until the HBR page is re-checked |
| Lodging `pools.poolsCount > 0` | Used `pools.pool`; the research gives no Lodging field names, so every mapped field is listed for build-time verification (7.7) |
| Perplexity estimate | Kept ~$2-3 by pinning `search_context_size='low'` rather than widening the range |
| `serpapi_enabled` for the snapshot | The snapshot has no hotel row, so AI Mode is dropped when SerpApi is unavailable (no key or budget) instead of by the per-hotel toggle |
| J1 form on OTA reservations | Took the `guest_stay` provenance, not a Canon 19 amendment |
| Cadence of `inq.quote_awaiting_approval` (rationale, as asked) | Accepted: Canon 18's daily tier names unanswered inquiries, so the signal is daily only while the guest has had no substantive reply and the SLA is breached, else weekly. The approval card still reaches the brief through the action's `surface='brief'`, so speed-to-quote does not wait for the weekly tier |

### Critical Files for Implementation
- /Users/nikhilagarwal/MyCodes/Clefs/docs/specs/00-CANON.md
- /Users/nikhilagarwal/MyCodes/Clefs/docs/specs/01-foundation.md (E2 actions, E4 jobs, E6 notify and inbound dispatch, E7 privacy and AI, amended by 0.4 and 1.3)
- /Users/nikhilagarwal/MyCodes/Clefs/docs/specs/02-money-modules.md (core/stays, tax conventions, Profit Guard L7 and base rule, SerpApi params, demo dataset)
- /Users/nikhilagarwal/MyCodes/Clefs/docs/specs/03-service-guest-trust-marketing.md (guest identity and direct codes, KB, Reviews GBP OAuth, service intake)
- /Users/nikhilagarwal/MyCodes/Clefs/server/clefs/CONTRACTS.md (lane ownership, frozen files, core sub-ranges 0001-0009, 0010-0019 and 0020-0099; wins on foundation file paths)
- /Users/nikhilagarwal/MyCodes/Clefs/server/clefs/core/signals.py (`KEY_PREFIXES`, F9) and /Users/nikhilagarwal/MyCodes/Clefs/server/tests/foundation/test_module_contracts.py (`FROZEN_API`, F10)
- /Users/nikhilagarwal/MyCodes/Clefs/server/clefs/modules/guests/api.py (the frozen `marketing_audience` signature, extended by 7.2)
