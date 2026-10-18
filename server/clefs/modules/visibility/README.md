# Visibility (`visibility`)

Owned by lane **V Visibility**. Spec: `docs/specs/04-growth-modules.md` section 5 (Canon 20, 23, K3). Other modules import ONLY `clefs.modules.visibility.api` (frozen signatures, `CONTRACTS.md` section 13).

- Migrations `0900-0949` (`migrations/NNNN_visibility_<desc>.sql`), tables `vis_`, keys `visibility.` / `vis.`.
- Console routes under `/hotels/{slug}/visibility/` (`router.py`); the AI radar's routes live in `ai_radar.router` (`/hotels/{slug}/visibility/ai...`) and the public check's in `public_check.router` (`/public/visibility/google-check...`); `module.py` mounts all three.
- Permissions (`module.py`, 04 spec 7.1): `visibility.view` (owner, gm, revenue), `visibility.act` (owner, gm, revenue; decide_perm of every `visibility.*` action type), `visibility.manage` (owner, gm).

## Built so far: the Google side (5.1-5.4, 5.6-5.8)

| File | What |
|---|---|
| `store.py` | `vis_profile` (defaults, partial PUT), the `vis_competitors` mirror of `demand.api.comp_set`, the vendor-cost ledger and the monthly budget check (USD micros vs `monthly_budget_minor x 10_000`, other currencies through `core.money.convert`) |
| `google_hotels.py` | Google Hotels via `core.serpapi.search` + `google_hotels_area_params` only (`q` from `demand.api.area_queries`, page 1 shared with Demand's fetch); organic rank without sponsored/ads, up to 2 extra pages; official presence; like-for-like parity (`gap = official - min(other)`, `gap_pct = gap / official x 100`, breach strictly above `profit_guard.api.parity_tolerance_pct`); exposure = gap x OTB rooms on the lowest source's channel (all OTA rooms when unmapped); evidence to `profit_guard.api.record_rate_evidence(emit_signal=False)` |
| `rank_grid.py` | Places Text Search with `X-Goog-FieldMask: places.id` ONLY (IDs Only SKU, free), 7x7 grid x 3-5 keywords; ARP / ATRP (not found = 21) / SoLV; `vis.rank_drop` (SoLV -15 pts on two consecutive weeks); monthly SerpApi `google_maps` calibration on the 3x3 subsample (drift: Spearman < 0.6 or top-3 < 0.7; "uncalibrated" when SerpApi is off) |
| `gbp.py` | Connection state (`GBP_PROJECT_QPM`: 0 = project not approved), location picker, daily Performance metrics (last 10 days re-fetched, raw rows expire 30 d after fetch), derived monthly KPIs (reach in user-days vs prior 28 d, live year-ago re-fetch, action rate), monthly keywords (value and threshold stored apart, thresholds never summed), the UTM `websiteUri` patch |
| `lodging.py` | Lodging API vs `concierge.api.kb_facts(keyed_only=True)` over the 5.4 allowlist only; `visibility.lodging_sync` (PATCH `updateMask`), verified by `getGoogleUpdated` (an override is `not_verified` + `vis.lodging_overridden`) |
| `site_health.py` | CrUX (PHONE, p75; 404 = insufficient data), PSI (mobile, performance), JSON-LD as `ClefsBot/1.0` honouring robots.txt (incl. `@graph`), KB comparison -> `visibility.site_fact_fix`. Hygiene only, no ranking claim |
| `attribution.py` | UTM-tagged direct bookings from `core/stays` (`utm_source=google` and `utm_campaign=gbp_listing` or the free-booking-link pattern), commission avoided via `profit_guard.api.expected_commission` + `core.stays.reservation_breakdown` |
| `action_types.py` | The six 5.7 action types and verifiers; `route_fact_error(cited_domains, website_host=)` |
| `vsignals.py`, `metrics.py`, `jobs.py` | Signal kinds and emitters, `vis.*` metrics, scheduled jobs and tasks |
| `xmod.py` | Every call into another module's api.py runs in a SAVEPOINT and degrades to that function's documented stub default on failure |
| `http.py` | The module's outbound HTTP (never SerpApi); tests swap it with `set_transport` |

## The AI radar, the public check and the AI snapshot (5.5, 5.9-5.11)

| File | What |
|---|---|
| `migrations/0904_visibility_ai.sql` | `vis_ai_prompts`, `vis_ai_batches`, `vis_ai_runs`, `vis_ai_weekly`, `vis_engine_versions`, `vis_ai_facts`, SQL `vis_wilson_halfwidth(k, n)`. Deviations from the 5.1 sketch are listed at the top of the file (run statuses `pending` / `timeout` + `attempts`, `prompt_key`, `cited_domains`, `facts_checked_at`, `requester_ref` / `plan` / `finished_at` on batches, SoV CI columns, `status` on engine versions) |
| `migrations/0907_visibility_public.sql` | `vis_public_checks` (global; token stored as sha256; lead contacts sealed) |
| `ai_radar.py` | The engine registry (exactly `gemini_web_search`, `perplexity_sonar`, `serpapi_ai_mode`); Gemini through `ai.responses_raw` with the `web_search` tool (Google Search grounding; the market city is recorded as the run locale); Perplexity `POST /chat/completions` with `search_context_size: "low"`, only with `PERPLEXITY_API_KEY` and `PERPLEXITY_TERMS_APPROVED=true`, home-market prompts only; AI Mode through `core.serpapi.search("google_ai_mode", ...)` with `max_age=0` (each repeat is a fresh answer). Weekly batch planning, snapshot batches, the step executor (<= 4 runs per round, 15 s per call, a timeout retried once, all-or-none per prompt x engine), model-version epochs, and the `/ai` console routes |
| `prompts.py` | The 24 weekly prompts (EN + MSA Arabic, generic / persona / event / brand / fact_check, 2 source markets from `profile.ai_markets` or the trailing-180-day `guest_country` top 2), versioned per `prompt_key`; the 5 snapshot prompts verbatim |
| `parsers/` | One parser per engine (`gemini.py`, `perplexity.py`, `ai_mode.py`) into `Parsed`; `common.py` splits list items, normalises names (diacritics, tashkeel, alef, taa marbuta, "hotel", "(DEMO)"), resolves entities (token-set ratio >= 0.9 on aliases, then the website domain and place ids of anchored citations; the AI fallback only when nothing resolved) and classes cited domains (`ota`, `review`, `official`, `google`, `ugc`, `wiki`, `press_other`). `PARSER_VERSION` is part of the epoch key |
| `stats.py` | Wilson 95% interval, the gate (`runs >= 30` and half-width <= 0.15), SoV, source mix (<= 5 slices), Jensen-Shannon divergence, the 2-window trend rule, the weekly rollup (window 1 and 4, truncated to the latest epoch), the AI Mode source-shift suggestion, and the console read model |
| `facts.py` | KB truth (`concierge.api.kb_facts(keyed_only=True)`, `hotels.star_rating`, trailing-30-day ADR for `price_band`), `FactClaims` extraction (`ai.structured`, `store=False`) over the sentences naming the hotel, comparators (times >= 30 min, `false_absent`, `false_closed`, `wrong_star`, price band +/-40%, `unverifiable`), fingerprint upsert |
| `signals.py` | `vis.ai_sov_change` and `vis.ai_fact_error` (weekly), with the fix routed by cited domain |
| `ai_verify.py` | `verify_ota_listing_fix`: verified after >= 8 clean fact-check answers from the engines that erred |
| `snapshot.py` | `get_ai_snapshot` / `ai_snapshot` / purge for Profit Guard's audit |
| `costs.py` | Per-run estimates (micros), the weekly allowance and the degradation ladder, the global snapshot budget and daily cap |
| `public_check.py` | `/public/visibility/google-check` routes, the WhatsApp follow-up task, purge |

Labels: Gemini and Perplexity cells read "API simulation: consumer app answers vary"; AI Mode cells and snapshot rows read "Google AI Mode via SerpApi (third-party); indicative". Every citation in `GET /ai/runs/{id}` carries its `url` so the console renders it as a link (Google Search grounding terms).

## HTTP API (`/hotels/{slug}/visibility/...`)

Errors are `{detail, code}`. Money is minor units + currency; ratios and percentages are Decimal strings.

| Method and path | Access | Response |
|---|---|---|
| `GET /google` | `visibility.view` | `{serpapi_enabled, setup_missing: ["property_token"\|"serpapi_enabled"\|"serpapi_key"], presence: {latest, rate_30d}, parity: [{check_in, check_out, official_present, official_minor, lowest_minor, lowest_source, lowest_channel, price_basis, gap_minor, gap_pct, breach, prices: [{source, official, price_minor, currency, channel}], currency, observed_at}], tolerance_pct, rank_history: [{day, organic_rank, organic_total, official_present}], attribution: <attribution row> \| null, labels: {disclosure, parity, price_accuracy, vendor_risk_url}}` |
| `GET /rank` | `visibility.view` | `{keywords: [{keyword, latest: {run_at, points, found, arp, atrp, solv, competitors: [{comp_id, name, points, found, arp, atrp, solv}]} \| null, history: [{run_at, solv, atrp}], calibration: {status: ok\|drift\|uncalibrated, month?, spearman?, top3_agreement?, note?, reason?}}], setup_missing: ["place_id"\|"area"\|"hotel_location"\|"places_api_key"], grid: {size, spacing_m}, label}` |
| `GET /gbp` | `visibility.view` | `{state: {status: project_not_approved\|not_connected\|pick_location\|connected, qpm, location, message, connection}, kpis: [{month, reach_user_days, reach_prior_user_days, reach_yoy_user_days, actions, action_rate, website_clicks, branded_share, keywords_under_threshold, computed_at}] (last 4), reach_label: "user-days", keywords: {month, rows: [{keyword, value, threshold, is_branded, display ("120" or "< 15")}], summary: {total_exact, branded_exact, under_threshold, branded_share, display ("195 + 5 under threshold")}}, review_replies: reviews.api.reply_stats \| null, utm: {action_id, status, created_at}}` |
| `GET /gbp/locations` | `visibility.manage` | `{locations: [{location ("locations/123"), title, address, website_uri, account}], selected}`; 409 `project_not_approved` / `not_connected`, 502 `gbp_error`. Pick one with `PUT /settings {gbp_location}` |
| `GET /site-health` | `visibility.view` | `{latest: {id, checked_at, url, crux: {status: ok\|insufficient_data\|error, metrics: {lcp_ms, inp_ms, cls}, outside_good: [{metric, label, value, threshold, note}]}, psi: {score, lcp_ms, cls, tbt_ms, fetched_at}, jsonld_found, jsonld_types, jsonld_fields: {pages: [{url, status, found, types}], fields}, jsonld_issues: [{severity: required\|recommended\|useful, type, field, url, message}], facts_vs_kb: [{fact_key, ours, theirs, status: match\|mismatch\|missing, source_url}]} \| null, setup_missing: ["website_url"\|"google_api_key"], thresholds: {lcp_ms, inp_ms, cls}, note}` |
| `GET /attribution?month=YYYY-MM-DD` | `visibility.view` | `{months: [{month, gbp_website_clicks, direct_bookings, direct_room_revenue_minor, commission_avoided_minor, reference_ota, currency, computed_at, line, label}], label}` (without `month`: the last 6 computed months) |
| `GET /settings` | `visibility.view` | every `vis_profile` field (`place_id, gbp_location, gh_property_token, website_url, booking_engine_host, brand_aliases, rank_keywords, area, landmarks, room_page_urls, free_booking_link_pattern, grid_size, grid_spacing_m, ai_markets, monthly_budget_minor, budget_currency, engines_enabled, serpapi_enabled`) + `updated_at`, `gbp` (the state above), `budget: {spent_micros, budget_micros, remaining_micros, exhausted}` |
| `PUT /settings` | `visibility.manage` | same body fields, all optional; only sent keys change. 422 on a bad `gbp_location` (must be `locations/...`), non-http URLs, more than 5 `rank_keywords`, `grid_size` outside 3-9. Returns the GET shape. Audited `visibility.settings.update` |
| `POST /gbp/draft-replies` | `visibility.act` | `{drafted, drafts: [{review_id, reply_id, status, needs_approval, language}], considered, unanswered, reviews_tab: "replies"}`: calls `reviews.api.draft_reply` for the 5 newest unanswered Google reviews of the last 90 days (drafts only; approval and posting stay in Reviews). Audited `visibility.review_drafts.request` |
| `POST /actions/utm-patch` | `visibility.act` | 201 the created action (`visibility.gbp_utm_patch`, deduped per location; the dial sets its level) or `{status: "suppressed"}`; 409 `gbp_project_not_approved` / `gbp_not_connected` / `gbp_pick_location` |
| `POST /actions/lodging-sync` | `visibility.act` | 201 `{diff, action_id?}` after a fresh lodging check (`{diff: 0}` when Google already matches the KB); same 409s; 502 `gbp_error` |

Actions are then decided through the core routes (`POST /hotels/{slug}/actions/{id}/decide`); a parity fix is proposed from the `vis.parity_gap_google` signal's `recommended_action`.

### AI radar (`/hotels/{slug}/visibility/ai...`)

| Method and path | Access | Response |
|---|---|---|
| `GET /ai` | `visibility.view` | `{enabled, setup_missing: ["no_gemini_key"\|"no_serpapi_key"\|"no_perplexity_key"\|"terms_not_approved"\|"serpapi_disabled"\|"kill_switch"\|"engines"], engines: [{engine, label, status: ok\|disabled\|<reason>, disclaimer}], week, cells: [{engine, market, language, window_weeks (1 for home-market EN, else 4), epoch, label, avg_position, runs, mentions, rate, ci_low, ci_high, sufficient, display ("30% (18-45%)" or "insufficient sample (n=29)")}], sov: [{engine, market, language, sufficient, entities: [{entity: self\|<comp_id>\|other, name, mentions, sov, ci_low, ci_high}]}], source_mix: {engine: {self_wins, comp_wins, all: {class: count}}}, facts: [Fact], min_sample: {runs: 30, max_halfwidth: "0.15"}, epochs: [{engine, epoch, status: active\|suggested\|dismissed, version_key, reason, since, note ("Model changed 12 Oct; baseline reset")}], suggested_resets: [version_key], latest_batch: {id, status, planned, done, cost_micros, week, steps: [degradation steps]} \| null, budget: {spent_micros, budget_micros, remaining_micros, exhausted}, labels: {simulation, third_party}, method_note}`. Rates and CIs are Decimal strings (4 dp) |
| `GET /ai/runs?prompt_key=&engine=&batch_id=&limit=50` | `visibility.view` | `{runs: [Run]}` newest first (ok and empty answers only) |
| `GET /ai/runs/{id}` | `visibility.view` | `Run = {id, batch_id, engine, label, prompt_key, prompt, intent, market, language, model_id, baseline_epoch, repeat_idx, run_at, status, searched, answer_text, citations: [{url, title, domain, class, kind: citation\|source, start_index, end_index}], entities: [{key, name, position, via}], self_mentioned, self_position, self_cited, locale, parser_version}`; 404 `run_not_found` |
| `GET /ai/prompts` | `visibility.view` | `{prompts: [{id, prompt_key, version, text, language, market, city, intent, fact_key}]}` (active versions) |
| `GET /ai/facts?status=open\|recurred\|fixing\|fixed\|dismissed` | `visibility.view` | `{facts: [Fact], label}`; default open + recurred + fixing. `Fact = {id, fact_key, label, claimed_value, claimed_quote, truth_value, truth_source, error_type: wrong_value\|false_absent\|false_closed\|wrong_star\|price_band_off\|unverifiable, severity (1 worst), engines, cited_domains, occurrences, run_ids (last 10), status, first_seen, last_seen, action_id}` |
| `POST /ai/facts/{id}/fix` | `visibility.act` | 201 `{fact_id, action}`: proposes `visibility.ota_listing_fix` (OTA cited), `site_fact_fix` (own site), `lodging_sync` (Google) or `add_kb_faq` (nothing cited, or unverifiable); deduped per fingerprint; the fact becomes `fixing`. 404 `fact_not_found`, 409 `fact_closed`. Audited `visibility.ai_fact.fix` |
| `POST /ai/facts/{id}/dismiss` | `visibility.act` | `{fact_id, status: "dismissed"}` (stays dismissed if seen again; the signal is resolved). Audited |
| `POST /ai/engines/{engine}/baseline {version_key, accept=true}` | `visibility.manage` | `{engine, version_key, status, epoch}`: accept (new epoch from now) or dismiss a suggested AI Mode reset; 404 `suggestion_not_found`. Audited |

### Public Google check (`/public/visibility/google-check`, no auth; Canon 23)

| Method and path | Response |
|---|---|
| `POST /public/visibility/google-check {hotel_name, city, consent_processing: true, consent_transfer_abroad: true}` | `{token, status: needs_pick\|ready\|no_match, candidates: [{property_token, name, address}] (needs_pick only), teaser: {official_present} \| null (ready only)}` |
| `POST .../{token}/pick {property_token}` | the same shape, now `ready` or `no_match` (the token must be one of the offered candidates). The pick claims its row first (internal status `picking`, a 5-minute lease; shown as `needs_pick` on the wire), so a parallel pick gets 409 `pick_in_progress` and spends no search; a failed pick releases the claim. 3 picks per IP per day |
| `POST .../{token}/lead {name, email?, whatsapp? (E.164), consent_contact_email?, consent_contact_whatsapp?}` | `{unlocked: {parity: [{source, channel, price_minor, currency, official}], gap_minor, gap_pct, organic_rank, checked_at, stay_dates: [[check_in, check_out]] x 2, note_claim_ids, currency, label}}`; contacts sealed; founder emailed (`FOUNDER_NOTIFY_EMAIL`); WhatsApp follow-up queued in [09:00, 18:00) Dubai only with `consent_contact_whatsapp` |
| `GET .../{token}` | the latest of the shapes above (`unlocked` included once the lead step is done) |
| Errors | 422 `invalid_input` (missing ticks or fields, a bad pick, no contact), 429 `rate_limited` (3 checks and 3 picks per IP per day; `PUBLIC_CHECK_DAILY_CAP` in total), 409 `pick_in_progress`, 503 `budget_exhausted` (`PUBLIC_SERPAPI_MONTHLY_CAP` spent, or no SerpApi key), 502 `serpapi_error`, 404 `not_found` (unknown or expired token) |

Retention 30 days (lead columns redacted by the PII registry, rows deleted by `visibility.public_check_purge`). SerpApi calls are ledgered with `purpose='public_check'` and no hotel.

### `api.py` (other modules)

- `start_ai_snapshot(cur, *, subject: SnapshotSubject, requester_ref) -> int | None`: 5 prompts x available engines x 3 runs (45 with Perplexity, 30 without; AI Mode dropped without `SERPAPI_KEY`). None when `VISIBILITY_AI_ENABLED=false`, no engine is available, `SNAPSHOT_DAILY_CAP` is reached or `SNAPSHOT_MONTHLY_BUDGET_USD` cannot cover the estimate (spend = `vis_api_costs` purpose `snapshot` + SerpApi purpose `visibility.snapshot`).
- `get_ai_snapshot(cur, batch_id) -> {status: running|ready|failed, per_engine: [{engine, label, mentioned, runs, ci: [low, high], display: "mentioned in 4 of 9 answers"}], top_cited_domains, answers_about_you: [{prompt, engine, label, answer, run_at, citations: [{url, title}]}], label} | None` (mentions over prompts 1-3 only; prompts 4-5 quoted).
- `purge_ai_snapshot(cur, batch_id) -> int` (runs + batch removed); `ai_snapshot(cur, hotel_slug, *, hotel_name, city, max_queries=3) -> {batch_id, queries, mentioned, share_of_voice, sample_size, engines, label} | None` (latest ready snapshot for that subject).
- `open_fact_errors` now also returns unresolved AI fact errors (`source: "ai_answers"`, wrong values only).

## Jobs

`visibility.google_hotels_daily` 05:30, `rank_grid_weekly` Mon 04:00 (task `rank_grid_keyword` per keyword), `rank_calibrate_monthly` first Mon 04:30 (task `rank_calibrate_keyword`), `gbp_daily` 06:00 (also refreshes `vis.gbp_setup`), `gbp_kpis_monthly` day 3 06:30, `gbp_keywords_monthly` day 5 06:00, `lodging_check` 06:15, `site_health_weekly` Sun 03:00, `attribution_monthly` day 2 07:00, `competitors_sync` 03:30, `gbp_raw_expire` 03:45 UTC (platform). AI radar: `ai_prompts_refresh` Sun 20:00, `ai_radar_weekly` Mon 01:00 (plans the batch), task `ai_batch_step` (<= 4 calls per round, re-enqueues itself; snapshots use it too), task `ai_weekly_rollup` (cells, source shift, SoV signal, then fact checks in chunks of 6). Public: `snapshot_purge` 03:55 UTC and `public_check_purge` 04:05 UTC (platform), task `public_lead_followup`. All hotel-local unless marked.

## Signals and metrics

Signals: `vis.parity_gap_google` (money_available, daily, impact = exposure), `vis.official_price_missing` (weekly), `vis.rank_drop` (weekly), `vis.lodging_overridden`, `vis.site_health_not_good`, `vis.jsonld_mismatch`, `vis.gbp_setup` (service_risk, weekly), `vis.google_attribution` (monthly), `vis.ai_sov_change` and `vis.ai_fact_error` (money_available, weekly).

Metrics (`visibility.view`): `vis.gh_organic_rank`, `vis.official_presence_rate`, `vis.parity_gap` (money, hotel currency), `vis.solv_pct` and `vis.atrp` (dimensions keyword, entity), `vis.gbp_reach_user_days`, `vis.gbp_website_clicks`, `vis.gbp_action_rate` (derived KPIs only), `vis.google_direct_bookings`, `vis.google_commission_avoided`, `vis.cwv_lcp_p75_ms`, `vis.ai_mention_rate` and `vis.ai_share_of_voice` (`vis_ai_weekly` window 1, self cells; dimensions engine, market, language; NULL unless `SUM(runs) >= 30 AND vis_wilson_halfwidth(SUM(mentions), SUM(runs)) <= 0.15`, the UI's gate), `vis.ai_fact_errors_open` (dimension fact_key).

## Settings read with `clefs.settings.env`

- `GBP_PROJECT_QPM` (default 0 = not approved). FOUNDATION-REQUEST: model it in `Settings` and list it in `.env.example`.
- `FOUNDER_NOTIFY_EMAIL` (unset = no founder email for public-check leads) and `VIS_LEAD_WA_TEMPLATE` (default `clefs_google_check_followup`, an approved marketing template on the platform number). FOUNDATION-REQUEST: model both.
- AI radar (modelled): `VISIBILITY_AI_ENABLED`, `AI_MODEL_VISIBILITY_GEMINI` (through `ai_model_for`), `PERPLEXITY_API_KEY`, `PERPLEXITY_MODEL`, `PERPLEXITY_TERMS_APPROVED`, `SNAPSHOT_DAILY_CAP`, `SNAPSHOT_MONTHLY_BUDGET_USD`, `PUBLIC_CHECK_DAILY_CAP`, `PUBLIC_SERPAPI_MONTHLY_CAP`, `SERPAPI_COST_PER_SEARCH_USD`.
- Existing: `SERPAPI_KEY`, `GOOGLE_MAPS_API_KEY` (Places), `GOOGLE_API_KEY` (CrUX, PSI), `GBP_OAUTH_*` (through `core.oauth`).
