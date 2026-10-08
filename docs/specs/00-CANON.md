# Canon (source of truth; overrides the 01/02/03 specs)

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

## Autonomy dial

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

## Amendments adopted 2026-09-28 (from 04-growth-modules.md section 0.7)

- **K1 (item 7, Actions):** add terminal side states:
  - `observed`: the autonomy dial logged the action and nothing ran.
  - `failed`: the executor returned `ok=false` after the task's retries.
  - `expired`: the action was still `proposed` or `approved` at `actions.expires_at`.

  Shipped in migration `0013_core_action_states.sql` and `core/actions.py` (wave 0b). Migration `0011` stays at its applied bytes: editing an applied migration is checksum drift (CONTRACTS section 4), so no lane touches it.
- **K2 (OTA rules):**
  - Booking.com charges commission on room + service charge + VAT (GDT v2601 cl. 2.3.2(ii)).
  - The municipality fee and Tourism Dirham are excluded only when the property configured them as charges.
- **K3 (item 2, Money):** vendor-cost ledgers are stored in micro-units, `*_micros BIGINT` plus currency. This covers SerpApi, Google Gemini, Perplexity and Meta per-call costs.
  - The shared SerpApi client and its cost ledger (`core/serpapi.py`, migration `0018_core_serpapi.sql`) are already built (wave 0b extension lane). No Wave 1 lane recreates or edits them; a change goes through a FOUNDATION-REQUEST.
  - Demand Radar, Visibility and Reviews all call `core.serpapi.search`, and never SerpApi directly.
- **K4 (item 14, Client routes):** add these public routes:
  - `/g/[token]` and `/g/f/[token]`: guest journey preference and feedback pages.
  - `/q/[token]`: inquiry quote page.
  - `/w/inquiry/[slug]`: embeddable inquiry form.
  - `/google-check/r/[token]`: Google-check lead-magnet result.
- **K5 (item 21, Claims):** add the claim ids:
  - `google_third_party_rates_unavailable_2026_09_30`
  - `triptease_free_booking_links_unaffected`
  - `uae_ota_leakage_index` (hidden until n ≥ 30)
