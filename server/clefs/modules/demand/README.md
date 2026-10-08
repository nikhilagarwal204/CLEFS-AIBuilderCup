# Demand Radar (`demand`)

Owned by lane **D Demand Radar** from Wave 1. Only that lane edits this directory (plus `tests/modules/demand/`).

- Migrations: `migrations/NNNN_demand_<desc>.sql|.py`, numbers 0400-0499 (`python -m clefs.migrate new --module demand "desc"`). The events side uses `0400`, the rates side `0410`, the engine `0420`; later parts take the next free numbers.
- New tables start with `dr_`. Job, signal, metric and action keys start with `dr.` (or `demand.`).
- Console routes live under `/hotels/{slug}/demand/` (`router.py`, and `engine_routes.py` for the engine); guest and lead routes under `/public/`, device routes under `/device/`, webhooks under `/webhooks/`.
- Permissions are declared in `module.py`: `demand.view` (owner, gm, revenue) reads, `demand.manage` (owner, gm, revenue) writes.
- `api.py` is this module's public surface. Other modules may import ONLY `clefs.modules.demand.api`; its signatures are frozen (see `clefs/CONTRACTS.md`). Implement the bodies, never change the signatures.

## Events side (02 spec 4.1-4.2)

| File | Role |
|---|---|
| `migrations/0400_demand_events.sql` | `dr_venues`, `dr_events`, `dr_event_sources`, `dr_event_changes`, `dr_holidays`, `dr_source_markets`, `dr_demand_features_daily`, `dr_sync_state` |
| `seeds/venue_events_2026q4_2027.yaml` | Curated venues (with corridor tags), events and holidays for 2026 Q4 to 2027, each row with `source_url` and `last_verified_at` |
| `curated.py` | Loads the seed idempotently (skipped while its sha256 is unchanged); writes the ATM reschedule history; lists rows stale for a quarter |
| `dedup.py` | Pure rules: `canonical_key`, title/acronym matching, venue and 1 km matching, date overlap, field precedence |
| `events.py` | The event store: `ingest()` (every adapter ends here), `recompute()`, change history, `dr.event.changed`, reads, manual entry, owner confirmation, `upcoming()` / `find()` for `api.py` |
| `adapters/phq_events.py` | PredictHQ Events API (60 km, rank >= 40, incremental `updated.gte` cursor) |
| `adapters/phq_features.py` | PredictHQ Features API (10 km, daily attendance sums and public-holiday rank) |
| `adapters/ticketmaster.py` | Ticketmaster Discovery API, `countryCode=AE`, low trust |
| `adapters/ai_web.py` | Weekly AI web discovery through `clefs.ai.web_search` |
| `ics.py` | ICS import (console route and the core ingest commit handler for kind `ICS`) |
| `markets.py` | Source-market shares (regions or ISO countries) and holiday reads |
| `events_jobs.py` | Scheduled jobs and the ICS commit-handler registration (imported by `module.py`) |
| `fixtures/*.json` | Vendor payload fixtures (PredictHQ Events and Features, Ticketmaster) for tests and demos |

### Scopes and trust

- **Global events** (`hotel_slug` NULL) come from PredictHQ, Ticketmaster and the curated seed. **Hotel-scoped events** come from manual entry, ICS files and AI discovery.
- A hotel-scoped source that matches a global event attaches to it as corroboration only. It never edits the global row, and other hotels never see it (`sources` lists only global rows and the caller's own).
- A global record that matches an earlier hotel-scoped event folds it in (`merged_into`); the old id keeps resolving to the survivor, so references stay valid.
- **Dedup:** two records merge when they share a venue (or sit within 1 km), overlap by at least one day, and their titles match (token-set ratio >= 0.8 or an acronym such as GITEX, ATM, DSF). A record with no venue and no coordinates matches on title and dates alone.
- **Precedence** for dates, title and status: CURATED > PREDICTHQ > MANUAL > ICS > TICKETMASTER > AI_WEB (newest fetch first within one source). Attendance is the largest credible figure (AI numbers count only when nothing else has one) with its source in `attendance_source`.
- **Verification:** `VERIFIED` when any non-AI source is not marked UNVERIFIED (a curated UNVERIFIED row, a PredictHQ `predicted` event), or when the owner confirmed the event. AI discovery is always UNVERIFIED with confidence capped at 0.6; `actionable` (corroborated by a non-AI source, or owner-confirmed) gates any recommendation.
- **Changes:** the date owner moving its own dates is a reschedule (`status` RESCHEDULED, `original_start/end` kept); a better source overriding a worse one is a correction. Cancellations and postponements come from the source (PredictHQ `deleted_reason`, Ticketmaster `dates.status.code`, the seed, or the owner). Every date, status and venue change is a `dr_event_changes` row.
- **Hijri holidays** carry `moon_dependent` and `date_uncertainty_days = 1`.

### Jobs

| Job | Schedule | Scope | Notes |
|---|---|---|---|
| `dr.curated_sync` | daily 00:30 UTC | global | Skipped while the seed file is unchanged |
| `dr.phq_events_sync` | daily 01:00 UTC | per hotel | Needs `PREDICTHQ_TOKEN` and hotel `lat`/`lng`; one fetch per block per day |
| `dr.phq_features_sync` | daily 01:30 UTC | per hotel | Same requirements |
| `dr.ticketmaster_sync` | Mondays 02:00 UTC | global | Needs `TICKETMASTER_API_KEY` (read through `settings.env`) |
| `dr.ai_discovery` | Mondays 03:00 hotel time | per hotel | Needs AI on; an answer without citations keeps nothing |
| `dr.curated_verify_reminder` | 1 Jan/Apr/Jul/Oct 05:00 UTC | global | Logs seed rows not re-verified for 92 days |

A permanent vendor error (401, 403) is a skipped run; a transient one (network, 429, 5xx) fails the slot so the scheduler retries.

### Signals

| Kind | Section | Cadence | When |
|---|---|---|---|
| `dr.event.changed` | `money_available` when an approved Demand action depends on the event, else `service_risk` | daily | An event's dates or status changed and something references it: a `demand` action whose `params.event_id` or `params.driver_event_ids` names it, or a registered finder |

The dedupe key is `dr.event.changed:<event_id>:<start>:<end>:<status>`, so each distinct change is its own signal. `subject_type` is `dr_event`. SSE `demand.event_changed {event_id, status}` goes to the same hotels.

### Extension points for later Demand parts (`events.py`)

- `register_ref_finder(fn(cur, event_id) -> [{hotel_slug, ref_type, ref_id, approved}])`: recommendations, guidance and campaigns that name an event.
- `on_event_changed(fn(cur, event, changes, refs))`: supersede recommendations when their event moves.
- `register_scorer(fn(cur, hotel, events) -> {event_id: score})`: the relevance model; until one registers, `quick_score` (distance decay by crowd size times scale) feeds `api.upcoming_events`.

### Public API (`api.py`, events side)

- `upcoming_events(cur, hotel_slug, *, days=60, min_score=0.3)` -> `[{event_id, name, start, end, score, source, category, status, verification}]`, not cancelled, scored as above. `[]` for an unknown hotel.
- `find_event(cur, hotel_slug, *, text)` -> `{event_id, name, start, end}` or None. Every remaining word of the phrase ("GITEX week", "during the F1") must appear in the title, or the phrase is the title's acronym, or the titles match by the dedup rule. Ties go to the title with the fewest unasked words, then the bigger crowd, then the nearest date.

## Rates side (02 spec 4.1-4.2, Canon 12, K3)

Migration `0410_demand_rates.sql`: `dr_rate_settings`, `dr_comp_set`, `dr_comp_rates`, `dr_bar_calendar`, `dr_bar_changes`, `dr_rate_guardrails`, `dr_weather`. Money is BIGINT minor units plus CHAR(3) currency, always the hotel's currency.

- **Comp set** (`comp_rates.py`): up to 25 active comps per hotel, `comp_id` a slug of the name. Deleting deactivates (the rate history stays). One comp per Google `property_token`. `demand.api.comp_set` returns `{comp_id, name, property_token, place_id, lat, lng, stars, aliases}` for active comps.
- **Comp rates are always indicative** (`indicative: true`, `label: "Indicative"`): public shopping prices, never contract rates. Sold out is `sold_out: true` with no price (a demand signal, never zero). One row per comp, night, LOS, adults, source and hotel-local capture day, so a same-day refetch replaces and the day-by-day history stays. The current view per night takes the latest capture day, then the most trusted source (CSV > MakCorps > SerpApi > Xotelo), and ignores captures older than 14 days.
- **Adapters** implement `fetch(hotel, comp_ids, dates, los=1, adults=2, currency) -> list[CompRate]` and never write rows (`comp_rates.record_rates` is the one writer):
  - `adapters/serpapi_hotels.py` (default): one area query per night and query through `clefs.core.serpapi.search('google_hotels', google_hotels_area_params(...), purpose='demand.comp_rates')`, so Visibility's identical requests share the billed call. Price is `rate_per_night.extracted_lowest`; `prices[]` become `by_source`. Comps missing from page 1 get a per-`property_token` call for nights 0-14 only, at most `fallback_calls_max` (default 5) per run. `BudgetExhausted` and `NotConfigured` stop the run (reported, not failed); 3 consecutive `SerpApiError`s stop it as `vendor_down`.
  - `adapters/csv_rates.py`: Lighthouse and RateGain rate-shop CSVs. Core ingest kind `COMP_RATES` (handler `commit_comp_rates`, registered at import) or the direct import route, which also reads the wide grid (date column, one column per hotel, "Sold out" cells). The source is `CSV_RATEGAIN` when the source hint or file name says RateGain, else `CSV_LIGHTHOUSE`. Unknown names become comps (`origin: csv`); the hotel's own column is skipped. `LighthouseApi` is a stub (`NotConfigured`).
  - `adapters/xotelo.py`: demo only (hotel `status = 'demo'` or `DEMAND_XOTELO_ENABLED`), comps with `xotelo_key`. USD is converted with `clefs.core.money` (fx_rates, else the 3.6725 peg) and flagged `fx_converted` with the vendor amount kept.
  - `adapters/makcorps.py`: stub; `configured()` needs `MAKCORPS_API_KEY`, and even then returns no rows (`stopped: not_built`) until the contract is reviewed.
- **Cadence** (`fetch_dates`): nights 0-30 daily, 31-120 when the lead time is a multiple of 3 (each far night every third day): 61 area calls a day per query.
- **Area queries** (`demand.api.area_queries`): the owner's (max 3, `PUT /rates/settings`), else `hotels in <city>`, else `[]`.
- **BAR calendar** (`bar.py`): per night and room type code; sources `MANUAL`, `CSV`, `RATE_EVIDENCE`. Every change is kept in `dr_bar_changes` (the APPLIED check of 02 spec 4.3). Rate evidence (`bar.record_rate_evidence`) never overwrites a MANUAL or CSV value younger than 7 days. The reference room type (a NULL `room_type_code`) is the setting, else the active type with the most rooms.
- **Guardrails** (`guardrails.py`): rows per room type (`*` = all), month (NULL = any) and ISO weekday (NULL = any) with `floor`, `ceiling` and `max_step_pct` (default 15). The most specific row wins: exact room type, then month, then weekday. Helpers `round5`, `clamp`, `step_cap` implement the 02 spec 4.3 arithmetic.
- **Weather** (`weather.py`): Open-Meteo hourly (yesterday plus 7 days) at the hotel's lat/lng, else its emirate's centroid; `OPEN_METEO_API_KEY` switches to the commercial endpoint (the free API is non-commercial). `demand.api.outdoor_temp_c(hotel|slug, at)` reads the cache only (it runs on Service's request intake): the nearest cached hour within 90 minutes, memoised per hotel and hour for 10 minutes, else None.

### `demand.api.price_guardrails(cur, hotel_slug, stay_date, room_type_code=None)`

`{stay_date, room_type_code, floor, ceiling, recommended, recommendation_id, currency, confidence, reasons[], bar, bar_captured_at, bar_source, source, demand_index, max_step_pct, guardrail_id, comp: {median, priced, sold_out, indicative}}`, amounts as Decimal strings in the hotel's currency, or None.

| `source` | When | floor / ceiling | confidence |
|---|---|---|---|
| `rate_guardrails` | an owner row applies | the row | 0.9 with a BAR, 0.6 without |
| `bar_only` | no row, a BAR | BAR -10% / +30%, rounded to 5 | 0.6 |
| `fallback` | neither, indicative comp rates | 80% / 130% of the comp median, rounded to 5 | 0.3 (never auto-send, 04 spec 4.7) |
| None | none of these | | |

`reasons`: `no_bar`, `bar_stale` (BAR older than 7 days, confidence -0.2), `bar_below_floor`, `bar_above_ceiling`, `no_guardrails_configured`, `from_indicative_comp_rates`. `recommended` comes from the registered recommender (`guardrails.register_recommender`; the engine's `recommend.py` registers the decided rate, else the open proposal), clamped to [floor, ceiling]; None without one. `demand_index` is `demand.api.demand_index` (see Engine).

### Other public API bodies (rates side)

- `comp_rates(cur, hotel_slug, stay_date)`: `[{competitor, comp_id, stay_date, channel, rate, currency, sold_out, source, fetched_at, indicative, label, fx_converted, is_demo, by_source[]}]`, cheapest first, sold out last.
- `comp_rates_snapshot(cur, hotel_slug, *, stay_dates)`: `{iso date: [{competitor, comp_id, rate, currency, fetched_at, sold_out, source}]}`; nights without data are left out.

### Jobs (rates side)

- `dr.comp_rates_fetch` daily 04:00 hotel time, per hotel: the 61-night plan with a 90 s budget; nights left over go to the task `dr.comp_rates_fetch_more` (also what `POST /comp-rates/fetch` queues).
- `dr.weather_fetch` every 3 hours, per hotel (401/403 skip; transient errors retry).
- `dr.rates_retention` daily 03:45 UTC, global: comp rates for nights more than 400 days past, weather hours older than 14 days.

## Engine (02 spec 4.3-4.4)

| File | Role |
|---|---|
| `migrations/0420_demand_engine.sql` | `dr_engine_settings`, `dr_event_relevance`, `dr_event_class_prefs`, `dr_calibration`, `dr_demand_days`, `dr_rate_recommendations`, `dr_guidance`, `dr_campaign_drafts`, `dr_ghc_checklist`, `dr_leads` |
| `relevance.py` | Stay nights per event type, the relevance score, class weights, dismissals, calibration from own pickup, source-market holiday scores, engine settings; registers the `events.upcoming` scorer |
| `demand_index.py` | The demand index per night (`dr_demand_days`) and `demand.api.demand_index` |
| `recommend.py` | Rate recommendations, levers, "why this matters", supersede/expire, decisions, `dr.pace.behind`, `demand.api.expected_adr`; registers the guardrails recommender and the event-change listener |
| `measure.py` | APPLIED verification from BAR evidence, observed outcomes, actionability, the weekly top 5 |
| `graph_actions.py` | Staffing guidance (`dr.staffing.gap`), pre-arrival offer drafts, KB update suggestions |
| `campaigns.py` | Export-only campaign drafts (WhatsApp, Instagram, email) with the 09:00-18:00 Dubai send window |
| `ghc.py` | Google Hotel Center checklist and `dr.ghc.deadline` |
| `actions.py` | Action types `dr.rate_change`, `dr.pre_arrival_offer`, `dr.staffing`, `dr.kb_update`, `dr.campaign`; `safe_call` for cross-module calls |
| `signals.py` | Signal kinds `dr.opportunity`, `dr.pace.behind`, `dr.staffing.gap`, `dr.ghc.deadline`, `dr.rec.applied_verified` |
| `metrics.py` | `dr.occupancy_pct`, `dr.adr_aed`, `dr.revpar_aed`, `dr.rooms_otb`, `dr.pickup_rooms`, `dr.demand_index`, `dr.rec_decision_rate` |
| `jobs.py` | Engine jobs; imports the modules above so their registrations run (imported by `module.py`) |
| `engine_routes.py` | Console routes of the engine and the public `POST /public/leads/demand-preview` |
| `leads.py` | The demand-preview lead: validation, consents, rate limits, sealed contacts, founder email, the 30-day preview |

### Stay nights affected (`relevance.nights_affected`)

A night is the date a guest sleeps over.
- CONFERENCE, EXPO on [s, e]: nights s-1 .. e-2 at 1.0 and e-1 at 0.6.
- SPORTS, CONCERT: each event day's night at 1.0.
- FESTIVAL, COMMUNITY, CITYWIDE: every night, Fri/Sat 1.0, Sun-Thu 0.5.
- PUBLIC_HOLIDAY, RELIGIOUS, SCHOOL_HOLIDAY (UAE): the days-off block is the holiday, a bridge day when a holiday falls on a Tue (the Mon) or Thu (the Fri), and the weekend it touches (`hotels.weekend_days`). Every night of the block but the last counts 1.0; the eve counts 0.5. National Day 2-3 Dec 2026 gives nights 2, 3, 4, 5 Dec (and the eve, 1 Dec).
- SOURCE_MARKET_HOLIDAY: every night at 1.0.
- `moon_dependent` (Hijri) widens by `date_uncertainty_days` on both sides; shifted nights count half.

### Relevance (`relevance.score_event`)

`score = w_class x decay x corridor x scale x dow_fit x segment_fit` (0 for CANCELLED or POSTPONED events).
- `decay = exp(-d / lambda)`, lambda 8 km (< 10k attendance), 15 km (10k-100k), 25 km (> 100k or CITYWIDE). `d` is the closest of the event's venues to the hotel (`hotels.lat/lng`). No location (holidays) is 1.0. A CITYWIDE event in the hotel's emirate is at least 0.6. A measured travel time (`travel_minutes[venue]`) replaces the km decay: <= 15 min 1.0, <= 30 0.7, <= 45 0.45, <= 60 0.25, else 0.1.
- `corridor = 1.5` when the hotel's `corridor_tags` (engine settings) meet a venue's (dual-venue: any venue), else 1.0.
- `scale = min(1, log10(attendance) / 5)`, else `phq_rank / 100`, else a category default (public holiday 0.6, religious and school 0.5, citywide 0.8, other 0.4).
- `dow_fit` per night, weighted over the event's nights: MICE 0.6 on Fri/Sat nights, else 1.0; leisure 1.0 Fri/Sat, 0.5 otherwise; UAE public and religious holidays 1.0 Thu/Fri, 0.8 otherwise.
- `segment_fit`: the hotel's own MICE (MICE, CORPORATE, GROUP) or LEISURE share of room nights in the trailing year (reservations with a `segment`, at least 200 room nights) over the market default (MICE 0.30, LEISURE 0.60; UNVERIFIED placeholders), else the midweek vs Fri/Sat split of occupied nights over 5/7 vs 2/7; clipped to [0.5, 1.5]; 1.0 without history.
- `w_class = (useful + 2) / (useful + dismissed + 3)` for the category and each `CATEGORY:VENUE` key, the lower of the two (0 when muted). Dismissals add to `dismissed`; an approved or adjusted recommendation, or a measured night that beat its baseline pickup, adds to `useful`.
- AI-only discoveries score nothing until another source corroborates them or the owner confirms them.
- Source-market holidays (`dr_holidays` outside AE, through `markets.market_holidays`) add `sum over markets of share x kind weight` (school 1.0, religious 0.7, public 0.5) to the night's event score; one kind per market per night.

### Calibration (`relevance.calibrate`, weekly)

For each past event since 2024-01-01: `lift = mean occupancy on its full-weight nights / mean same-DOW, same-month baseline - 1`. The baseline is 2024-2025 `occupancy_daily` without core `baseline_exclusions` (the global 2026-03-01..2026-07-31 conflict row plus the hotel's own) and without the event's own nights. Per category `k = sum(lift) / sum(score)`; `fit = 1 - rmse / (rms(lift) + 0.05)` clipped to [0, 1]. Fewer than 5 own events keep the global prior (`K_PRIORS`, fit 0.3), and a recommendation driven by a prior class has its confidence capped at 0.6. `expected_rn_uplift = k x score x sum(night weights) x capacity`.

### Demand index (`demand_index.py`)

`DI = round(100 x logistic(0.35 season_z + 0.30 event_z + 0.20 pace_z + 0.15 comp_z))`, each z capped at +/-4.
- `season_z`: the night's same-DOW, same-month 2024-2025 baseline occupancy against the mean and spread of the whole baseline.
- `event_z = min(4, 3 x event_score)`, `event_score` = sum of relevance x night weight plus the source-market holiday score. The factor 3 puts one strong nearby show (score about 1) near +3 sigma; the spec leaves the unit open, so this is the calibration knob.
- `pace_z = (OTB - base) / sigma` at the same lead time: OTB from the latest core `otb_snapshots`, base from same-weekday nights of the trailing 12 weeks photographed at that lead (+/- 2 days), sigma at least 5% of capacity. `pace_gap = (OTB - base) / capacity` feeds the recommendation.
- `comp_z = (median comp price / same-DOW median over d +/- 14 without event nights - 1) / sigma` (sigma at least 10%) plus 0.1 per 10% of comps sold out. Comp prices are indicative.
- `coverage` = share of inputs with data (season, pace, comp, events). `demand.api.demand_index` returns the stored value (else a live one-night computation) and None when no input has data.

### Rate recommendations (`recommend.py`)

Never pushed to a channel manager. Per night (next 90) and room type with a BAR in the hotel's currency:
- `d_raw = 0.10 (DI - 50) / 50 + 0.40 pace_gap + 0.5 comp_move` (comp_move capped at +/-20%); `d = clip(d_raw, -10%, +30%)`, then within +/- `max_step_pct` (the guardrail row's, default 15).
- `rec = clamp(round_to_5(BAR x (1 + d)), floor, ceiling)`. Rounding never carries the move past the step cap; floor and ceiling win over the step. Bounds are the owner's `dr_rate_guardrails` row, else BAR -10% / +30%.
- Emitted only when `|rec - BAR| >= max(15 major units, 3% of BAR)`.
- `confidence = 0.4 event_conf + 0.3 coverage + 0.3 calibration_fit` (event_conf and fit weighted by each event's night score; 0.5 and 0.3 without events), capped at 0.6 on global priors.
- `impact = min(capacity, OTB + expected_pickup) x (rec - BAR) x (1 - commission_share)`, labelled "Estimated opportunity". `expected_pickup = sum(k x night score x capacity)`. `commission_share`: engine settings, else Profit Guard `channel_economics` commission over gross for the trailing 90 days, else 0.12.
- Levers: DI >= 80 with lead <= 21 days and OTB >= 70% of capacity gives `close_discounts`, `min_stay_2` (an event of 2+ full nights), `reduce_ota_allotment` (`ceil(10% x capacity)` rooms), `direct_offer`. DI <= 35 gives `pre_arrival_offer` and `open_closed_plans` (never below the floor).
- "Why this matters": `ai.text` (task `demand.why_this_matters`) over structured facts only (events and distance, comp move, sold-out comps, pace gap, DI). The prompt forbids causal claims, and a lint rejects "will increase", "guaranteed", "will boost", "will raise", "will drive", "will fill", "certain to", "definitely", "ensures", "will sell out", "will generate" and the em dash. A failed lint, AI off or an error falls back to a deterministic template. At most 20 LLM calls per run.
- Lifecycle: `PROPOSED` then `APPROVED` / `ADJUSTED` / `REJECTED` (the owner), `SUPERSEDED` (DI moved more than 15 points since the latest recommendation for the night, or a driving event changed), `EXPIRED` (the stay date passed undecided; the action carries `expires_at` = end of the stay date), `APPLIED` (BAR evidence). A night whose latest recommendation (any status but superseded or expired) is within 15 DI points is left alone.
- Each recommendation opens a `dr.rate_change` action (always approve). Its params carry `adjust = {field: "recommended_minor", min_minor: floor, max_minor: ceiling, label: "rate"}`, so every Adjust surface (brief console, WhatsApp, magic brief) edits the rate, never `current_bar_minor` (QA OWN-01). An adjust may change `recommended_minor` only: the facts (`current_bar_minor`, `floor_minor`, `ceiling_minor`, stay date, room type, currency, the spec) are 422 `invalid_params`; an action created before the spec that patches `current_bar_minor` alone has the value moved to the rate. Deciding through the action API, the brief or WhatsApp reaches the recommendation row (`sync_from_actions`: ADJUSTED at the owner's rate), which runs before every read and write of the rows because core has no per-type decide hook; a re-adjust after approval re-syncs too.
- `price_guardrails.recommended` is the decided rate, else the open proposal. A change to a driving event supersedes open proposals; decided ones stay and `dr.event.changed` (money section when approved) is the prompt to revert.

### Measurement (`measure.py`)

- APPLIED: a BAR (manual, CSV or rate evidence) captured after the decision within +/- 2% of the decided rate. The daily check marks the human task done and runs the verifier: `verified`, attribution `observed`, never counted as verified profit; `dr.rec.applied_verified` follows.
- Outcomes (`dr.measure_outcomes`, the day after the stay): pickup after the decision (rooms sold minus OTB at the decision) vs the mean pickup of the same weekday in the 8 weeks before at the same lead (excluded days and nights with a decided recommendation skipped); ADR, occupancy, RevPAR from `occupancy_daily`; net revenue from Profit Guard `channel_economics`. Every outcome carries `label: "observed, not causal"`.
- Actionability first: decided within 48 h (% of all non-superseded), applied (% of approved), median hours to decision, rejected, expired.
- Top 5: open recommendations in the next 60 days ranked by `impact x confidence`, at most 2 per driving event; `dr.weekly_top5` raises one `dr.opportunity` per item and resolves last week's leftovers.

### Graph actions (`graph_actions.py`, daily for events scoring >= 0.35 in the next 45 days)

- Staffing, for the morning after each full-weight event night (at most 10): `rooms_to_clean = departures + stayovers x stayover_ratio (0.8)`, `attendants = ceil(rooms_to_clean / credits (14))` (both in engine settings). The roster is `service.api.on_duty(role="attendant", at=10:00)`. A gap raises `dr.staffing.gap` and a `dr.staffing` action; with nobody rostered for that day the guidance is kept with `roster_unknown` and no signal (an unplanned roster is not a gap).
- Pre-arrival offer: a drafted headline and body for guests booked to arrive over the event nights, with a `dr.pre_arrival_offer` action when anyone is booked. Nothing is sent from Demand.
- KB update: travel advice for the venue (the metro station for DWTC and Expo City, traffic advice otherwise). Approving the `dr.kb_update` action calls `concierge.api.propose_kb_entry` (source `demand`); the concierge's own approval puts it live, and the action verifies once `kb_entry_status` is approved.

### Campaign drafts (`campaigns.py`, export only)

One draft per channel (WHATSAPP, INSTAGRAM, EMAIL) from a structured LLM call (`demand.campaign_copy`, template fallback, same lint). The audience is `guests.api.marketing_audience(purpose="marketing", channel, segment={"countries": the hotel's source-market countries})`: consented direct contacts only (Canon 19 keeps OTA-sourced contacts out); Instagram is an organic post without a list. `suggested_send_at` is 14 days before the event at 10:00 Dubai time, never in the past, and every send time is clamped into [09:00, 18:00) Asia/Dubai. The CSV lists contact refs (hmac hex, never addresses), channel, locale, send time, copy and, for email, `guests.api.unsubscribe_path`. Consent is honoured at export time: every export re-runs the same audience query and keeps only the stored contacts still in it (unsubscribed, withdrawn or suppressed guests drop out; the approved list never grows), and fails closed with no contacts when Guests cannot answer. The kept and dropped counts are stored as `audience.export_check` and on the export audit row. Export marks the draft EXPORTED; an exported draft is final.

### Google Hotel Center checklist (`ghc.py`)

Items: `HOTEL_CENTER_ACCOUNT`, `ARI_FEED_PARTNER`, `FREE_BOOKING_LINKS`, `LANDING_PAGE`, `PRICE_ACCURACY` (TODO | DONE | NA), each with a source URL. Canon K5 claims `google_third_party_rates_unavailable_2026_09_30` and `triptease_free_booking_links_unaffected`: Google's third-party rates for Hotel Ads end after 30 Sep 2026; free booking links are not affected by that change. `dr.ghc.deadline` (money_available) stays open while an item is TODO and resolves when none is.

### Public API bodies (engine)

- `demand_index(cur, hotel_slug, *, stay_date)`: 0-100 or None (see above).
- `expected_adr(cur, hotel_slug, *, check_in, check_out, room_type_code=None)`: `{amount (Decimal string), currency, room_type_code, source: recommendation|bar|mixed, confidence}`, the mean of each night's decided recommendation else its BAR; None when a night has neither (or the stay is over 60 nights).
- `upcoming_events` now ranks by the stored relevance score (dismissed events score 0) and falls back to `events.quick_score`.

### Jobs (engine, hotel-local unless noted)

| Job | Schedule | What |
|---|---|---|
| `dr.relevance` | daily 00:05 | score events for the next 180 days |
| `dr.calibrate` | Mon 00:02 | k per class from own pickup |
| `dr.demand_index` | daily 00:15 (after `stays.otb_snapshot` 23:55) | the next 120 nights |
| `dr.recommend` | daily 00:30 | expire, supersede, propose; `dr.pace.behind` (pace_z <= -1.5 within 45 days) |
| `dr.graph_actions` | daily 00:45 | staffing, pre-arrival offers, KB suggestions |
| `dr.measure_outcomes` | daily 05:00 | APPLIED from BAR evidence; observed outcomes |
| `dr.weekly_top5` | Mon 06:00 | `dr.opportunity` for the top 5 |
| `dr.ghc_deadline` | daily 07:00 | `dr.ghc.deadline` |
| `dr.leads_purge` | daily 03:35 UTC, global | demand-preview leads past 90 days (`dr_leads` is registered with `core.privacy.register_pii`) |

### Signals and action types (engine)

| Signal kind | Section | Cadence |
|---|---|---|
| `dr.opportunity` | money_available | weekly |
| `dr.pace.behind` | money_available | daily |
| `dr.staffing.gap` | service_risk | daily |
| `dr.ghc.deadline` | money_available | daily |
| `dr.rec.applied_verified` | verified | weekly |

Action types (all `decide_perm` `demand.manage`, ceiling approve, none counts toward verified profit): `dr.rate_change` (human task, `always_approve`, params within [floor, ceiling], BAR verifier), `dr.pre_arrival_offer`, `dr.staffing`, `dr.campaign` (human tasks), `dr.kb_update` (executor: `concierge.api.propose_kb_entry`).

## HTTP API (console, `/hotels/{slug}/demand`)

Errors are `{detail, code}`. Reads need `demand.view`; writes need `demand.manage` and are audited as `demand.<entity>.<verb>`. Dates are ISO `YYYY-MM-DD`; a window (`from`, `to`) defaults to hotel-local today plus 90 days and is at most 400 days (400 `invalid_range`).

`Event` = `{id, title, category, venue_codes[], emirate, lat, lon, start_date, end_date, status: SCHEDULED|RESCHEDULED|POSTPONED|CANCELLED, original_start, original_end, attendance_est, attendance_source, phq_rank, phq_local_rank, predicted_accom_spend (Decimal string, USD), predicted_accom_spend_currency, moon_dependent, date_uncertainty_days, verification: VERIFIED|UNVERIFIED, confidence (0-1), date_source, source_urls[], description, scope: global|hotel, owner_confirmed, first_seen_at, last_checked_at, sources: [{source, external_id, fetched_at, confidence, urls[]}], corroborated, actionable}`. `category` is one of CONFERENCE, EXPO, SPORTS, CONCERT, FESTIVAL, PUBLIC_HOLIDAY, SCHOOL_HOLIDAY, RELIGIOUS, SOURCE_MARKET_HOLIDAY, COMMUNITY, CITYWIDE.

### Events

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /events?from&to&category&include_cancelled=true&limit=500` | view | `category` comma-separated (400 `invalid_category`) | `[Event]` overlapping the window, by start date |
| `GET /events/{id}` | view | | `Event` plus `changes: [{at, field: dates|status|venue_codes|verification|merge, old, new, source, reason: rescheduled|correction|status|update|merge, source_url}]` (also the changes of rows folded into it); 404 `event_not_found` |
| `GET /events/lookup?q=` | view | `q` 2-200 chars | `{match: {event_id, name, start, end} | null}` (the `find_event` resolver) |
| `GET /events/upcoming?days=60&min_score=0.3` | view | | the `upcoming_events` rows |
| `POST /events` | manage | `{title, category, start, end?, venue_codes?, venue_text?, attendance?, description?, urls?, moon_dependent?, uncertainty_days?}` | 201 `Event` plus `created`, `matched` (true when it matched an existing event, which it then corroborates); 400 `invalid_event`, 422 on shape |
| `PATCH /events/{id}` | manage | any `POST` field, `status`, and `reason: rescheduled|correction` for moved dates | `Event`; only the hotel's own manual entries: 409 `event_not_editable` |
| `DELETE /events/{id}` | manage | | `{deleted, event_id, sources_removed?}`: removes the hotel's manual, ICS and AI rows; a hotel event with none left is deleted. 409 `event_not_editable` when the hotel has no row on it |
| `POST /events/{id}/confirm` | manage | | `Event` (a hotel-scoped event becomes VERIFIED and actionable; a global one is returned unchanged) |
| `POST /events/import-ics` | manage | JSON `{content, filename?}` or multipart `file` (2 MB max, 413 `file_too_large`) | `{imported, created, matched, rescheduled, event_ids[]}`; 400 `unreadable_file` / `no_events`, 422 `invalid_body`. Re-importing the same UIDs updates in place |

### Reference data and sources

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /venues` | view | | `[{code, name, lat, lon, emirate, corridor_tags[], capacity, coords_verified, feed_available, last_verified_at, source_url}]` |
| `GET /holidays?from&to&country&markets_only=false` | view | `country` comma-separated ISO2; `markets_only` returns only the hotel's source markets' holidays | `[{country_code, name, start_date, end_date, kind: PUBLIC|SCHOOL|RELIGIOUS, moon_dependent, uncertainty_days, verification, source_url}]`, plus `market_code`, `market_share_pct` with `markets_only` |
| `GET /source-markets` | view | | `{is_default, source_url, markets: [{market_code, label, share_pct (Decimal string), countries[]}]}`; the default is the DET Jan-Aug 2026 mix |
| `PUT /source-markets` | manage | `{markets: [{market_code (ISO2 or WEU|SAS|GCC|CIS|MENA|NESEA|AMER|AFR|OCE), share_pct}]}` | same as GET; 400 `invalid_market` / `invalid_share` (sum over 100) |
| `GET /features?from&to` | view | | `{from, to, days: {date: {feature: value}}}` from PredictHQ Features |
| `GET /sources/status` | view | | `{configured: {predicthq, ticketmaster, ai_discovery}, runs: [{source, scope, last_run_at, last_result}]}` |
| `POST /sources/sync` | manage, 4 per hour per hotel (429 `rate_limited`) | `{source: curated|predicthq|ai_discovery}` | `{source, result}` (`result.skipped` names why nothing ran: `no_token`, `no_location`, `fresh`, `ai_disabled`, `no_citations`) |

### Rates: settings, comp set, comp rates

Every comp rate in a response carries `indicative: true` and `label: "Indicative"`; the UI shows that label next to every figure. Amounts are Decimal strings in the hotel's currency.

`Comp` = `{comp_id, name, property_token, place_id, xotelo_key, makcorps_id, lat, lng, stars, rooms, aliases[], is_demo, active, origin: manual|discovered|csv|demo, created_at, updated_at}`

`NightBand` = `{comps, priced, sold_out, min, median, max, median_minor, currency, captured_at, sources[], indicative, label}`

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /rates/settings` | view | | `{area_queries[], self_property_token, reference_room_type, comp_fetch_enabled, fallback_calls_max, updated_at, effective_area_queries[]}` |
| `PUT /rates/settings` | manage | any of `{area_queries (max 3), self_property_token, reference_room_type, comp_fetch_enabled, fallback_calls_max 0-20}` | same as GET; 400 `invalid_settings` / `invalid_room_type`, 422 on shape |
| `GET /comp-set?include_inactive=false` | view | | `[Comp]` |
| `POST /comp-set` | manage | `{name, property_token?, place_id?, xotelo_key?, makcorps_id?, lat?, lng?, stars? (1-5), rooms?, aliases[]? (max 10), is_demo?}` | 201 `Comp`; 409 `comp_exists` (token already used), 409 `comp_set_full` (25 active), 400 `invalid_comp` |
| `PATCH /comp-set/{comp_id}` | manage | any `POST` field, `active?` | `Comp`; 404 `comp_not_found` |
| `DELETE /comp-set/{comp_id}` | manage | | `Comp` with `active: false` (history kept) |
| `POST /comp-set/discover` | manage | `{check_in? (default today + 7), query? (default the first area query)}` | `{query, check_in, currency, candidates: [{name, property_token, stars, lat, lng, rate, overall_rating, reviews, is_self, comp_id}], indicative, label}`: one SerpApi area search (cached, shared); 400 `no_area_query`, 503 `budget_exhausted`, 409 `not_configured`, 502 `serpapi_error` |
| `GET /comp-rates?from&to` | view | window default today + 30, max 400 days | `{from, to, indicative, label, nights: [{stay_date, ...NightBand}]}` (nights with data only) |
| `GET /comp-rates/{stay_date}?history=false` | view | | `{stay_date, ...NightBand, rates: [CompRateRow], history?: [CompRateRow] (every capture, oldest first)}`; `CompRateRow` = `{competitor, comp_id, stay_date, channel, rate (null when sold out), currency, sold_out, source, fetched_at, indicative, label, fx_converted, is_demo, by_source: [{source, rate, official}]}` |
| `POST /comp-rates/import` | manage | JSON `{content, source?: lighthouse|rategain, currency?, filename?}` or multipart `file` (+ form `source`), 2 MB max | `{source: CSV_LIGHTHOUSE|CSV_RATEGAIN, rows, rates, skipped, sold_out, unmatched[], nights, indicative}`; 400 `unreadable_file` / `no_rates` / `invalid_currency`, 413 `file_too_large`, 422 `invalid_body` |
| `POST /comp-rates/fetch?days=14` | manage, 2 per hour per hotel (429 `rate_limited`) | `days` 1-30 | 202 `{queued, nights, from, to}`: queues `dr.comp_rates_fetch_more`; the worker makes the calls |

### Rates: BAR, guardrails, weather

`BarRow` = `{stay_date, room_type_code, bar, bar_minor, currency, source: MANUAL|CSV|RATE_EVIDENCE, captured_at, note}`

`Guardrail` = `{id, room_type_code ('*' = all), month (1-12 | null), dow (ISO 1-7 | null), floor, ceiling, currency, max_step_pct, note, updated_at}`

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /bar?from&to&room_type` | view | window default today + 90 | `{from, to, currency, reference_room_type, rows: [BarRow]}` |
| `PUT /bar` | manage | `{rows: [{stay_date, room_type_code, bar, note?}]}` (1-5000) | `{written, changed, nights, room_types[]}`; 400 `invalid_bar` (whole request rejected, row index in `detail`) |
| `POST /bar/import` | manage | JSON `{content}` or multipart `file`: long (date, room type, rate) or wide (date + one column per room type code) | same as `PUT /bar` (source CSV); 400 `unreadable_file` / `no_rates` |
| `GET /bar/changes?stay_date&room_type` | view | | `[{room_type_code, old, new, currency, source, actor, at}]` oldest first |
| `DELETE /bar/{stay_date}/{room_type}` | manage | | `{deleted, stay_date, room_type_code}`; 404 `bar_not_found` |
| `GET /guardrails` | view | | `{currency, rows: [Guardrail]}` |
| `PUT /guardrails` | manage | `{rows: [{room_type_code='*', month?, dow?, floor, ceiling, currency?, max_step_pct? (default 15), note?}]}` (1-200) | `{currency, rows: [Guardrail]}`: upsert by (room type, month, weekday); 400 `invalid_guardrail` |
| `DELETE /guardrails/{id}` | manage | | the deleted `Guardrail`; 404 `guardrail_not_found` |
| `GET /guardrails/preview?stay_date&room_type` | view | | `{stay_date, guardrails}`: exactly `demand.api.price_guardrails` (null when Demand has no view) |
| `GET /weather?from&to` | view | window default today + 7 | `{from, to, days: [{date, max_c, min_c, max_apparent_c, precip_mm, hours, forecast}], now_c, last_fetched_at, hours_cached, coordinates: {lat, lon} | null, from_hotel, provider: "open-meteo", commercial_key}` |

Audit actions: `demand.rate_settings.update`, `demand.comp.create|update|deactivate`, `demand.comp_rates.import|fetch`, `demand.bar.update|import|delete`, `demand.guardrails.update|delete`.

### Engine: calendar, day view, relevance

`Night` = `{stay_date, demand_index (0-100), coverage (0-1), components: {season_z, event_score, event_z, pace_z, comp_z, season: {baseline_occ, n} | null, pace: {otb, lead, base, n, pace_gap} | null, comp: {median_minor, sold_out, comps, ref_median_minor, comp_move} | null, market_holidays[], has: {season, pace, comp, events}, events: [{event_id, title, category, night_score, score, distance_km, confidence, verification, status, k, k_source, calibration_fit, event_nights}], signal_free}, top_event_ids[], computed_at | null}` (`computed_at` null = computed live, not stored).

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /calendar?days=30&from=` | view | `days` 30, 60 or 90 (400 `invalid_days`); `from` defaults to hotel-local today | `{from, to, days, currency, reference_room_type, comp_label: "Indicative", cells: [{stay_date, demand_index (null when no input has data), coverage, weekend, events: [{event_id, title, category, night_score, verification}] (top 3), market_holidays[], recommendation: {id, status, current_bar, recommended, change_pct} | null, comp: comp band | null, bar}]}` |
| `GET /days/{date}` | view | | `{stay_date, demand_index: Night, events: [Event + {night_score, relevance, distance_km, travel_band, corridor_match, features, dismissed, dismiss_reason}], comp: {label, rates[], summary}, bar[], reference_room_type, pace: {own: [{days_before, rooms_otb}], baseline: [{days_before, rooms_otb}], label}, recommendations: [Recommendation], guardrails: price_guardrails, guidance: [Guidance]}` |
| `POST /events/{id}/dismiss` | manage | `{reason?, scope: event|class|venue}` (reason `"mute"` with scope class or venue also mutes) | `{event_id, dismissed, scope, reason, class_key}`; 404 `event_not_found`; audited `demand.event.dismiss` |
| `DELETE /events/{id}/dismiss` | manage | | `{event_id, dismissed: false}`; 404 `not_dismissed` |
| `GET /relevance?from&to&include_dismissed=false` | view | window default today + 90, max 400 days | `[{event_id, title, category, start, end, status, score, distance_km, travel_band, corridor_match, expected_rn_uplift, nights: {date: weight}, features: {w_class, decay, corridor, scale, dow_fit, segment_fit, k, k_source, calibration_fit, lambda_km, status}, dismissed, dismiss_reason, computed_at}]` |
| `GET /class-prefs` | view | | `[{class_key, useful, dismissed, muted, weight}]` |
| `PUT /class-prefs/{class_key}` | manage | `{muted}`; key `CATEGORY` or `CATEGORY:VENUE` (400 `invalid_class`) | the class row |
| `GET /calibration` | view | | `{min_own_events: 5, baseline_years: [2024, 2025], exclusions: [{from, to}], classes: [{category, k, n_events, fit, source: own|prior, confidence_cap, computed_at}]}` |
| `GET /engine-settings` | view | | `{corridor_tags[], travel_minutes: {venue_code: minutes}, stayover_ratio (0.8), credits_per_attendant (14), commission_share | null, known_corridors[]}` |
| `PUT /engine-settings` | manage | any of `{corridor_tags[], travel_minutes{}, stayover_ratio (0.1-1), credits_per_attendant (4-30), commission_share (0-1) | null}` | the settings; 400 `invalid_settings` |
| `POST /recompute` | manage, 6 per hour (429 `rate_limited`) | | `{relevance: {events}, calibrated_classes, nights, recommendations: {expired, created, superseded, kept, skipped, llm, pace: {behind, resolved}, graph: {events}}}` |

### Engine: recommendations, top 5, outcomes

`Recommendation` = `{id, stay_date, room_type_code, currency, current_bar, recommended, floor, ceiling, decided (Decimal strings), change_pct, levers: [{code, label, rooms?}], rationale, why, why_source: llm|template, driver_event_ids[], demand_index, confidence, impact, impact_minor, impact_label: "Estimated opportunity", status, status_reason, action_id, outcome | null, outcome_label, created_at, decided_at, applied_at}`.

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /recommendations?status&from&to&limit=200` | view | `status` comma-separated (400 `invalid_status`) | `[Recommendation]` by stay date, room type, newest first |
| `GET /recommendations/{id}` | view | | `Recommendation` + `facts` (the "why" inputs) + `baseline`; 404 `recommendation_not_found` |
| `POST /recommendations/{id}/decide` | manage | `{verb: approve|adjust|reject, recommended? (Decimal string) or recommended_minor?, note?}` | `Recommendation`; 409 `recommendation_closed` unless PROPOSED; 400 `rate_out_of_bounds` / `invalid_rate` / `invalid_amount`; the linked action moves (approve, adjust, ignore); audited `demand.recommendation.<verb>` |
| `GET /top5` | view | | `{label, items: [Recommendation + {rank_score, group}]}` |
| `GET /outcomes?from&to` | view | window default today - 90 .. today + 90 (max 400 days) | `{from, to, label: "Observed, not causal", actionability: {recommendations, decided, decided_within_48h_pct, approved, applied, applied_pct, median_hours_to_decision, rejected, expired}, outcomes: [Recommendation + outcome {label, lead_days, otb_at_decision, rooms_sold, occupancy, adr, revpar, pickup_after_decision, baseline_pickup, pickup_vs_baseline, net_revenue, hours_to_decision}]}` |

### Engine: guidance, campaigns, Google Hotel Center

`Guidance` = `{id, event_id, kind: STAFFING|KB_UPDATE|PRE_ARRIVAL_OFFER, stay_date, payload, status: PROPOSED|APPROVED|DONE|DISMISSED|EXPIRED, action_id, created_at, updated_at}`. `Campaign` = `{id, event_id, channel, audience: {kind: consented_contacts|organic_post, count, countries[], consent?, note?, export_check?: {at, exported_count, dropped_count, recheck: ok|guests_unavailable}}, copy: {text} | {caption} | {subject, body}, copy_source, suggested_send_at (Dubai offset), status: DRAFT|APPROVED|EXPORTED|ARCHIVED, action_id, created_at, exported_at, export_only: true}`.

| Method and path | Access | Request | Response |
|---|---|---|---|
| `GET /guidance?kind&event_id` | view | | `[Guidance]` |
| `POST /events/{id}/guidance` | manage | | `{event_id, staffing: [gap | null], offer, kb, guidance: [Guidance]}`: builds the event's guidance now |
| `POST /guidance/{id}/dismiss` | manage | | `Guidance` (its action cancelled); 404 `guidance_not_found` |
| `GET /campaigns?event_id` | view | | `[Campaign]` (no contact list) |
| `POST /campaigns` | manage | `{event_id, channels?: [WHATSAPP|INSTAGRAM|EMAIL], send_at? (tz-aware)}` | 201 `[Campaign]`; 404 `event_not_found`; 400 `invalid_send_at` |
| `PATCH /campaigns/{id}` | manage | `{copy?: {field: text}, send_at?, status?: DRAFT|APPROVED|ARCHIVED}` | `Campaign`; 400 `invalid_copy` / `copy_lint`; 409 `campaign_exported` |
| `GET /campaigns/{id}/export.csv` | manage | | `text/csv` attachment: `contact_ref, channel, locale, suggested_send_at_dubai, subject, message, unsubscribe_path`, only contacts still consented now; marks EXPORTED |
| `GET /ghc-checklist` | view | | `{deadline: "2026-09-30", days_left, passed, open_items, claim_ids[], summary, items: [{code, label, detail, source_url, status, note, updated_at}]}` |
| `PUT /ghc-checklist` | manage | `{items: [{item_code, status: TODO|DONE|NA, note?}]}` | the checklist; 400 `invalid_item`; refreshes `dr.ghc.deadline` |

Audit actions (engine): `demand.event.dismiss|undismiss`, `demand.class.mute|unmute`, `demand.engine_settings.update`, `demand.engine.recompute`, `demand.recommendation.approve|adjust|reject`, `demand.guidance.generate|dismiss`, `demand.campaign.create|update|export`, `demand.ghc.update`.

## HTTP API (public)

| Method and path | Access | Request | Response |
|---|---|---|---|
| `POST /public/leads/demand-preview` | public; 3 per IP per day and 50 a day in total (429 `rate_limited`) | `{hotel_name (1-120), city (1-80), area?, rooms? (1-5000), name?, role?, email?, whatsapp? (E.164), consent_processing, consent_transfer_abroad, consent_contact_email, consent_contact_whatsapp, website? (honeypot)}` | 202 `{ok: true, lead_id, preview: {area: {label, lat, lon, emirate} | null, from, to, note, events: [{title, category, start, end, venue, distance_km, relevance: high|medium|low, verification}] (up to 8), busy_nights: [{date, level}]}}`. 422 `invalid_input`, `consent_required` (both processing ticks), `contact_required`, `contact_consent_required` (a contact tick on a channel that was filled in). Contacts are sealed (`core.privacy.seal`); the founder gets an email at `FOUNDER_NOTIFY_EMAIL` with the hotel and preview headline, no contact details. A filled honeypot answers 202 with `lead_id: null` and stores nothing. |

## Foundation requests

- `TICKETMASTER_API_KEY` is read through `settings.env()`; add it to `Settings` and `.env.example` next to `PREDICTHQ_TOKEN`.
- `OPEN_METEO_API_KEY`, `MAKCORPS_API_KEY` and `DEMAND_XOTELO_ENABLED` are read through `settings.env()`; add them to `Settings` and `.env.example`.
- Ingest aliases for the Lighthouse and RateGain long exports (`aliases/lighthouse_rates.yaml`, `aliases/rategain_rates.yaml`, kind COMP_RATES) so the ingest door detects them by source; today only `generic.yaml` spellings map, and the wide grid goes through the direct route.
- `FOUNDER_NOTIFY_EMAIL` (the demand-preview lead notification, shared with Visibility's Google check) is read through `settings.env()`; add it to `Settings` and `.env.example`.
- Cross-lane (guests): `guests.api.marketing_audience(segment={"countries": [ISO2]})` should narrow to contacts whose stays came from those countries (`reservations.guest_country` or the phone's calling code). Demand already passes the key; until guests honours it, campaign audiences are every consented direct contact, not only the event's source markets.
- Cross-lane (service): `service.api.on_duty` has no department filter, so staffing guidance counts every `attendant` shift as housekeeping; a `department=` keyword (or a `housekeeping_attendant` shift role) would make the roster comparison exact.
