# Amenities (`amenities`)

Amenity leakage for hotels (02 spec section 5, Canon 8): the catalog every
module resolves items through, the issuance log, request linking, the daily
variance rollup with a 7-day spike rule, CPOR, minibar unbilled, par levels,
neutral per-staff views and cohort benchmarks. Owned by lane E Amenities.

- Migrations `0500_amenities_catalog.sql`, `0501_amenities_ledger.sql` (range 0500-0549). Tables start with `amn_`.
- Console routes live under `/hotels/{slug}/amenities/` (`router.py`). The browser calls them through `/api/clefs`.
- `api.py` is the public surface; other modules import only `clefs.modules.amenities.api`.

## Files

| File | What |
|---|---|
| `constants.py` | Enums mirroring the CHECKs and every 5.2 threshold (spike 15% / AED 100 / z 2, n0 200, gap 1.5x over 14 days, par z 1.65, staff 14 days / 3 people / 60%, benchmarks 3 / 5 / 50%) |
| `seed.py` | UAE default catalog (EN/AR, source URLs, costs in fils incl. excise) |
| `catalog.py` | Items, unit cost history (`CostBook`), alias matcher, seeding |
| `stock.py` | Locations, par rows, stock counts, on-hand and count-based consumption |
| `issuance.py` | Issuance log (manual, bulk, device) and the `ISSUANCE` / `SUPPLIER_INVOICE` ingest commit handlers |
| `requests.py` | Request lines: tool, alias pass, nightly LLM batch, manual |
| `variance.py` | Daily rollup and the 7-day windows, the occupancy regression |
| `detectors.py` | Signal kinds and detectors |
| `reports.py` | Grouped report, CPOR, minibar, staff view, item trend, overview |
| `rates.py`, `par.py`, `benchmarks.py` | Learned on-request rates, par recompute, cohort benchmarks |
| `actions.py`, `metrics.py`, `jobs.py` | Action types, metrics, jobs (jobs.py imports the other registrants) |

## Data model

- `amn_items`: `key` (A-Z0-9_), `name_en`/`name_ar`, `category` BATH|DENTAL|SHAVING|SLIPPERS|WATER|TEA_COFFEE|MINIBAR|OTHER, `basis` PER_OCCUPIED_ROOM_NIGHT|PER_ARRIVAL|PER_GUEST_NIGHT|ON_REQUEST, `std_qty`, `unit_cost_minor` (incl. excise) + `currency`, `excise_category` NONE|SWEETENED|ENERGY, `is_single_use_plastic`, `sup_ban_ref`, `aliases[]`, `request_type_key`, `max_qty`, `chargeable`, `price_minor`, `needs_review`, `source` MANUAL|SEED|CSV|DEMO, `source_url`, `active`, `sort`.
- `amn_item_costs`: cost from `effective_from` (incl. excise, with `excise_minor` as the excise part), source SUPPLIER_INVOICE|MANUAL|SEED|DEMO. The cost of day d is the latest row on or before d; before the first row the earliest row applies.
- `amn_locations` (STORE|FLOOR_CLOSET|CART|DEPARTMENT|MINIBAR, floor), `amn_par_levels` (par, days of supply, safety stock, lead days, `pinned`, `computed` inputs), `amn_stock_counts`.
- `amn_issuance`: signed `qty` (RETURN is negative), `business_date` (core.time), shift, item, from/to location, `to_floor`, staff id/name, reason REPLENISH|GUEST_REQUEST|VIP|DAMAGE|RETURN, `request_id`, source MANUAL|CSV|API|DEMO, `import_batch_id` + `import_row_no` (re-imports upsert), unit cost snapshot, `voided_at`.
- `amn_request_lines` (request, item, qty, `match_method` TOOL|ALIAS|LLM|MANUAL, confidence) and `amn_request_scans` (so the LLM never pays twice).
- `amn_variance_daily` per business date, item and floor ('' = not floor-tagged): basis count, expected std, requested, expected total, issued (net, without VIP and DAMAGE), excluded, variance units, unit cost, issued/expected/variance minor. No shift column: expected use is per day, so shifts and staff are compared on what they issued. `amn_daily_totals` per day: ORN, arrivals, issued units, issued/expected minor, requested units.
- `amn_item_rates` (learned per 100 ORN), `amn_benchmarks` (cohort, category, month; no hotel_slug), `amn_seed_runs`.

## Rules

- **Expected** `E = basis_count x std + requested`. Basis: occupied rooms per floor (`occupancy_by_floor_daily`, else the hotel total on floor ''), arrivals (hotel), guests (stayed `reservation_nights` only, so nights after an early departure never count; else 2 per occupied room). A day without an `occupancy_daily` row is **excluded, never zero-filled**.
- **Variance** `V = issued - E`, money `V x unit_cost(d)`. VIP and DAMAGE lines are explained use (`issued_excluded`).
- **Spike** (`am.variance.spike`, money_lost, weekly brief: a backward-looking loss is not a daily deadline, Canon 18): hotel-wide per item over 7 business dates, all of `V7/E7 > 15%`, `V7 > AED 100`, `z = (V7 - mu_8w)/sigma_8w > 2` (at least 4 earlier covered windows, sigma floored at 1 unit, a window needs 4 covered days). Impact = 7-day money; the monthly projection is only in the text. The regression `issued ~ a + b x occupied` over 56 days is evidence ("not explained by occupancy").
- **Logging gap** (`am.request_logging_gap`, service_risk): ON_REQUEST items (minibar aside) issued > 1.5 x logged requests over 14 days, with at least 10 issued.
- **Stockout** (`am.stockout_risk`, service_risk, weekly): counted par rows where on hand < max(safety stock, daily use x lead days). One signal overrides to `cadence="daily"` when less than one day of use is left (use > 0), since it bites before the next brief.
- **Single-use plastic** (`am.sup_compliance`, compliance, never in the daily top 5): active SUP items issued in 30 days or on a standard basis.
- **Minibar** (`am.minibar_unbilled`, money_lost): 7-day refills - folio `MINIBAR` qty > 10% and > AED 100, valued at the refills' average price. Silent without folio data in 90 days.
- **Rates**: `r = (req + prior x n0/100)/(ORN + n0) x 100`, n0 200, priors DENTAL 6, SHAVING 3, SLIPPERS 10, BATH 5, WATER 15, TEA_COFFEE 8, MINIBAR 10, OTHER 2 (90-day window).
- **Par** `= daily_usage x days_of_supply + 1.65 x sigma_daily x sqrt(lead_days)`, daily use from OTB + pickup (else the 28-day mean) scaled by the floor's share. Pinned rows keep their par; `computed.suggested_par` still updates.
- **Staff view**: only with 14+ days and 3+ issuers; "issued per occupied room served"; neutral note; counter-rotation note when one issuer holds > 60% of the above-expected money (allocated per day, floor and item).
- **Benchmarks**: units per 100 ORN by category, cohort `AE-DXB-HOTEL_4`, monthly. Published only with >= 3 hotels and no hotel > 50% of the volume; below 5 hotels only the band (p25 floored, p75 ceiled to a multiple of 5). Demo and archived hotels are left out.
- **Request linking**: `amenities.api.link_request` (TOOL, from the concierge `notify_staff` tool and Service intake) -> alias pass (whole-word aliases, plural s/es, "2 toothbrushes", "2x razor") -> nightly LLM batch for unmatched housekeeping requests (confidence >= 0.6, digit runs removed, at most 40 per call). MANUAL replaces all lines. A weaker method never overwrites a stronger one.

## Public API (`api.py`)

- `list_items(cur, hotel_slug, *, language="en", active_only=True) -> list[dict]`
- `resolve_item(cur, hotel_slug, amenity_item_id) -> dict | None`
- `link_request(cur, hotel_slug, *, request_id, amenity_item_id, qty) -> None`

Rows: `{id, key, name, name_en, name_ar, sku, category, uom, basis, std_qty, unit_cost, unit_cost_minor, currency, excise_category, is_single_use_plastic, sup_ban_ref, aliases, request_type, request_type_key, max_qty, chargeable, price, price_minor, needs_review, source, source_url, active, sort}`. `name` is Arabic when `language="ar"` and one exists. `link_request` ignores an unknown item, another hotel's request or qty <= 0 (analytics never blocks a request).

## Jobs, signals, actions, metrics

| Key | Kind | When / what |
|---|---|---|
| `am.link_requests_llm` | scheduled | 02:30 hotel-local: alias pass, then the LLM batch |
| `am.variance_rollup` | scheduled | 03:30: alias pass, rollup (last 14 days; 63 on the first run), every detector for yesterday |
| `am.learn_rates` | scheduled | Mon 04:00 |
| `am.par_recompute` | scheduled | Mon 04:30 |
| `am.benchmark_rollup` | scheduled, system | 2nd of the month 05:00 UTC, previous month |
| `am.rollup_range` | task | queued by back-dated lines, CSV commits, cost changes and voids |
| `am.investigate_variance` | action (human task) | approve; verified from 15 days after done: the next 14 days for the item and the flagged floor (`params.floor`, else hotel-wide) must be below the spike threshold (V/E <= 15% or V <= AED 100), else not_verified. Value = flagged-week money minus the 14-day money pro-rated to 7 days (counts toward verified profit, attribution caused). A window needs 8 covered days |
| `am.replace_sup_item` | action (human task) | suggest, ceiling approve |
| `am.post_minibar` | action (human task) | always approve (folio change) |

Metrics (permission `amenities.view`): `am.issued_units` (dims item, category, floor, shift, staff), `am.expected_units`, `am.variance_units`, `am.variance_aed` (dims item, category, floor), `am.cpor_aed`, `am.requests_per_100_orn`, `am.issued_per_100_orn`.

PII: `amn_issuance.staff_name` is redacted after 730 days (Trust retention).

## HTTP API (`/hotels/{slug}/amenities/...`)

Permissions: **V** `amenities.view` (owner, gm, finance, dept_head), **M** `amenities.manage` (owner, gm, dept_head), **I** `amenities.issue` (owner, gm, dept_head, staff, paired devices; `amenities.manage` also passes). Errors are `{detail, code}`. Money is a Decimal string in the hotel currency next to `*_minor` ints; quantities are decimal strings (`"2"`, `"0.5"`, `"-1"`). Date ranges take `?from=YYYY-MM-DD&to=YYYY-MM-DD` (hotel business dates); `to` defaults to yesterday (hotel-local), `from` to 30 days before unless noted; at most 400 days (400 `range_too_long`; `from > to` is 400 `invalid_range`). Every write is audited as `amenities.<entity>.<verb>`.

`Item` = the `api.py` row above. `Location` = `{id, kind, name, floor, active}`. `Par` = `{location_id, item_id, par_qty, days_of_supply, safety_stock, lead_days, pinned, computed{forecast_occ, source otb_pickup|trailing_28d, share, std_qty, rate_per_100, daily_usage, sigma_daily, safety_stock, suggested_par, as_of}, computed_at, location_name, location_kind, floor, item_key, item_name, on_hand, last_counted_at}` (`on_hand` null without a count). `Line` = `{id, issued_at, business_date, shift, item_id, item_key, item_name, qty, from_location_id, to_location_id, to_floor, staff_member_id, staff_name, reason, request_id, source, import_batch_id, unit_cost, line_cost, currency, note, voided_at}`.

### Catalog

| Method and path | Perm | Body / query | Response |
|---|---|---|---|
| GET `/items` | V or I | `?active_only=false&language=en\|ar&category=` | `Item[]`. A UAE hotel's first call seeds the default catalog (once; audited `amenities.catalog.seed`) |
| POST `/items` | M | `{name_en, key?, sku?, name_ar?, category?, uom?, basis?, std_qty?, unit_cost?, excise_category?, is_single_use_plastic?, sup_ban_ref?, aliases[]?, request_type_key?, max_qty?, chargeable?, price?, active?, sort?}` | 201 `Item`; 409 `item_key_taken`, 400 `invalid_key` |
| PATCH `/items/{id}` | M | any of the POST fields plus `needs_review` (send `false` to confirm a seeded row) | `Item`; 404 `item_not_found` |
| DELETE `/items/{id}` | M | | `{id, deleted, deactivated}`: deleted when unused, else deactivated |
| POST `/items/seed-defaults` | M | `{force?}` | `{seeded, already, version}` |
| GET `/items/{id}/costs` | V | | `[{id, item_id, effective_from, unit_cost, unit_cost_minor, excise, excise_minor, currency, source, invoice_ref, import_batch_id, created_at}]` newest first |
| POST `/items/{id}/costs` | M | `{effective_from, unit_cost, excise?, source? MANUAL\|SUPPLIER_INVOICE, invoice_ref?}` (cost incl. excise) | 201 cost row; 400 `invalid_excise`; re-rolls the affected days |
| GET `/items/{id}/trend` | V | range (default 56 days) | `{item, start, end, currency, series[{date, expected, requested, issued, variance, variance_value}], by_floor[{floor, expected, issued, variance_value}], by_shift[{shift, issued}]}` |

### Locations, par, counts

| Method and path | Perm | Body / query | Response |
|---|---|---|---|
| GET `/locations` | V or I | `?active_only=` | `Location[]` |
| POST `/locations` | M | `{kind, name, floor?, active?}` | 201 `Location`; 409 `location_name_taken` |
| PATCH `/locations/{id}` | M | `{kind?, name?, floor?, active?}` | `Location` |
| DELETE `/locations/{id}` | M | | `{id, deleted, deactivated}` |
| GET `/par` | V or I | `?location_id=` | `Par[]` |
| PUT `/par` | M | `{location_id, item_id, par_qty?, days_of_supply?, safety_stock?, lead_days?, pinned?}`; a given `par_qty` pins the row | `Par` (defaults by kind: store 14/7 days, closet 2/1, cart 1/1, department 7/3, minibar 7/1) |
| DELETE `/par/{location_id}/{item_id}` | M | | 204 |
| POST `/par/recompute` | M | | `{updated, pinned, par: Par[]}` |
| POST `/stock-counts` | I | `{lines: [{location_id, item_id, qty, counted_at?, counted_by?, note?}]}` (1-500; `counted_at` must carry a timezone and not be in the future) | 201 `{counts: [{id, location_id, item_id, counted_at, qty, counted_by, note}]}` |
| GET `/stock-counts` | V or I | `?location_id&item_id&limit(1-1000, 200)` | count rows, newest first |
| GET `/stock-counts/consumption` | V | `?location_id&item_id` + range | `{location_id, item_id, periods[{from, to, opening, received, sent, closing, consumed}]}` between consecutive counts |

### Issuance and imports

| Method and path | Perm | Body / query | Response |
|---|---|---|---|
| GET `/issuance` | V or I | range (default 7 days to today) `&item_id&staff&include_voided&limit(1-1000, 200)` | `Line[]` newest first |
| POST `/issuance` | I | one line `{item_id, qty, issued_at?, shift?, from_location_id?, to_location_id?, to_floor?, staff_member_id?, staff_name?, reason?, request_id?, note?}` or `{lines: [...]}` (1-500). RETURN is stored negative; `qty` 0 is 422; > 5 min in the future is 400 `issued_in_future`. On a device, `X-Clefs-Staff` (the on-shift person) becomes the issuer of lines naming nobody | 201 `{lines: Line[]}` |
| POST `/issuance/{id}/void` | M | | `Line` with `voided_at`; 409 `already_voided` |
| POST `/issuance/import` | M | `{file_id, kind? ISSUANCE\|SUPPLIER_INVOICE}` (upload first: `POST /files/upload-ticket {purpose}`) | 201 `{id, kind, status, filename, file_format, row_count, headers, mapping, mapping_source, gate_status, printed_total_minor, parsed_total_minor, currency, errors, commit_result, supersedes_batch_id, created_at, committed_at, row_counts}` |
| GET `/issuance/import/{batch_id}` | M | | the same batch shape; 404 `import_not_found` for other kinds |
| POST `/issuance/import/{batch_id}/commit` | M | | batch with `commit_result` `{written, skipped, unmatched_rows[{row_no, item, reason}]}` (issuance) or `{costs_written, skipped, unmatched_rows}` (invoice). Mapping edits use `/hotels/{slug}/ingest/batches/{id}/mapping` |

ISSUANCE columns (ingest aliases): `issued_at`, `item` (key, SKU, name or alias), `qty`, `floor`, `shift`, `staff_name`, `reason` (restock, guest, vip, damage, return...), `location` (a location name), `request_ref`. SUPPLIER_INVOICE: `invoice_no`, `invoice_date`, `supplier`, `item`, `qty`, `unit_cost_minor`, `amount_minor` (printed-total gate).

### Requests

| Method and path | Perm | Body | Response |
|---|---|---|---|
| GET `/requests/{id}/items` | V or I | | `[{request_id, item_id, item_key, item_name, qty, match_method, confidence}]`; 404 `request_not_found` |
| POST `/requests/{id}/items` | I | `{lines: [{item_id, qty}]}` (0-20, each item once) | the lines, all MANUAL (replaces every line) |

### Reports

| Method and path | Perm | Query | Response |
|---|---|---|---|
| GET `/overview` | V | `?to` | `{end, currency, variance_7d, variance_7d_minor, variance_30d, variance_30d_minor, cpor_30d{cpor, expected_cpor, gap, orn}, top_leaking[<=5 item rows], chart[<=8 {item_id, name, requested, issued, expected}], catalog{active, needs_review}, days_covered_30d, days_missing_occupancy_30d}` |
| GET `/report` | V | range `&group=item\|floor\|shift\|staff` | item/floor: `{group, start, end, currency, rows[{item_id, key, name, category \| floor, expected, requested, issued, issued_excluded, variance_units, variance_pct, issued_value, expected_value, variance_value, variance_minor}], days, days_covered, days_missing_occupancy, orn}`; shift: rows `{shift, issued, lines, issued_value, share_pct}` + `note`; staff: `{available, reason?, note, rows[{staff_name, issued, issued_value, days, floors, rooms_served, issued_per_room, share_of_above_expected_pct}], counter_rotation, counter_rotation_note}`; 400 `invalid_group` |
| GET `/cpor` | V | range | `{start, end, currency, orn, cpor, expected_cpor, gap, issued_value, expected_value, series[{date, orn, cpor, expected_cpor}], days, days_covered, days_missing_occupancy}` |
| GET `/minibar` | V | range (default 7 days) | `{start, end, currency, has_folio, refills, billed, unbilled, unbilled_share_pct, unbilled_value, unbilled_minor, billed_value}` (unbilled fields null without a folio feed) |
| GET `/rates` | V | | `[{item_id, key, name, category, window_days, requests, orn, prior_per_100, n0, rate_per_100, raw_per_100, computed_at}]` |
| GET `/benchmarks` | V | | `{cohort_key, month, min_hotels, exact_from, available, reason (needs_3_hotels), rows[{category, hotels_count, published, suppressed_reason, p25, p50, p75, band_low, band_high, yours_per_100_orn}]}`; p25-p75 are null below 5 hotels |
| POST `/rollup` | M | body `{from?, to?}` (default the last 14 days, at most 93) | `{from, to, alias{scanned, linked}, rollup{days, days_missing_occupancy, rows}, detectors{spike, logging_gap, stockout, sup, minibar}}` |
