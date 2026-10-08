# core/stays

What the PMS says happened (Canon: `core/stays`; 02 spec 2.1; 04 spec D1-D3).
PMS data is truth and OTA data is a claim: the two never share a table and
meet only through `reservation_matches`.

Tables (migration `0021_core_stays.sql`; `room_types`, `hotel_rooms` and
`channels` come from `0006`): `reservations`, `reservation_nights`,
`folio_charges`, `occupancy_daily`, `occupancy_by_floor_daily`,
`otb_snapshots`, `baseline_exclusions`, `reservation_matches`,
`reservation_contacts`, `stay_events`, `stay_event_cursors`,
`stay_event_failures`. There is **no plaintext `guest_contacts` table**
(04 spec D1 overrides 02 spec 2.1); a test asserts no core table has a phone
or email column.

Importing `clefs.core.stays` registers its jobs, PII tables and metrics
(`jobs.OPTIONAL_REGISTRANTS`) and exposes `router` (`app.CORE_ROUTER_HOOKS`).
Every function takes the caller's cursor and joins its transaction.

## Files

| File | What |
|---|---|
| `normalize.py` | OTA id and guest-name normalisation, `name_similarity` |
| `reservations.py` | `upsert_reservation` (the single write path), stay chains, `ensure_channel` |
| `nights.py` | `reservation_nights` rows, fees per night, the `stayed` flag |
| `contacts.py` | sealed + HMAC'd contacts, provenance and lifetimes, `contact_value`, purge |
| `events.py` | stay-event outbox: `emit`, `@on_stay_event`, `dispatch`, dead letters |
| `occupancy.py` | occupancy derivation, by floor, manual overrides, baseline exclusions |
| `otb.py` | nightly on-the-books snapshots and pace |
| `matching.py` | OTA claim to PMS stay: exact, fuzzy, review queue |
| `breakdown.py` | `stay_lines`, `reservation_breakdown` (core.tax `compose_stay`) |
| `folio.py` | folio charges, idempotent on `pms_txn_id` |
| `attribution.py` | direct-booking attribution (04 spec D2) |
| `jobs.py` | the four scheduled jobs |
| `registrations.py` | PII registry entries and `stays.*` metrics |
| `router.py` | HTTP routes under `/hotels/{slug}/stays` |

## Writing reservations

```python
upsert_reservation(cur, hotel_slug, data, *, source_system="PMS", nights=None, contacts=None,
                   import_batch_id=None, occurred_at=None)
  -> {id, created, events: [names], changed: [columns], stay_chain_id, nights: {nights, rate_basis, room_revenue_minor} | None}
```

- **Key.** `(hotel_slug, source_system, data["pms_res_id"])`; `source_system` is `PMS | CM | MANUAL | DEMO`.
  The row is locked `FOR UPDATE`, so two imports of one reservation serialise.
- **`data`** holds any of the writable columns (`reservations.WRITABLE`). Dates are `date` or ISO text; instants
  are tz-aware or read as hotel-local. Only the keys given are written (a partial update is fine).
  `nights`, `ota_res_id_norm`, `guest_name_norm`, `stay_chain_id` and `rate_basis` are always derived.
- **Channels.** A new `channel_code` is upper-cased and its `channels` row created on first sight (kind `ota` for
  BOOKING/AGODA/EXPEDIA, `direct`, `walk_in`, `corporate`, `gds`, else `other`), so the FK never fails an import.
- **Status timestamps.** CANCELLED without `cancelled_at` gets now. IN_HOUSE/CHECKED_OUT without
  `checked_in_at` gets local noon of `check_in` (never later than now); CHECKED_OUT without `checked_out_at`
  likewise. A `checked_out_at` before `check_out` sets `actual_check_out` (an early departure).
- **Errors.** 400 `invalid_reservation` for a missing key or dates, `check_out < check_in`, or an unknown status.
- Cost: roughly 20 statements per reservation. An ingest commit pages its rows to stay inside a 20 s job.

### Nights (`reservation_nights`)

`rate_minor` is R (room revenue net of every fee) per room per night; the fee columns are the row's totals.

| `rate_basis` | Source | Rows |
|---|---|---|
| `nightly` | `nights=[{stay_date, rate_minor, room_seq?, rooms?, room_number?, room_type_code?, sc_minor?, ...}]` | as given |
| `room_revenue` | `room_revenue_minor` | allocated exactly (`money.allocate`) over nights x rooms, one row per room (`room_seq` 1..n, `rooms=1`) |
| `gross_decomposed` | `gross_minor` only | allocated the same way, then `core.tax.decompose` per night gives R and each fee |
| `none` | neither | `rate_minor = 0` |

- **Fees** come from `core.tax` for each stay date (`load_context`, `compose`/`decompose`): SC, MF, TF (+ DEST),
  VAT, TD. A PMS that sends its own split (`sc_minor`, ...) keeps it.
- **TD** is charged per unit (`rooms` x `room_types.bedrooms` when the rule is per bedroom) on nights 1..30 of the
  stay chain, and is 0 on a night that will not be stayed (cancelled, no-show, after an early departure).
- **`stayed`**: CONFIRMED, CANCELLED and NO_SHOW never; IN_HOUSE for nights up to the hotel's business date;
  CHECKED_OUT for nights before `actual_check_out` (else `check_out`). `refresh_stayed(cur, hotel, today=)` re-derives
  it (the nightly job does).
- A nightly-rate stay whose dates move without new nights keeps each remaining night's rate and prices an added
  night like the nearest one.
- The reservation's `room_revenue_minor` is set to the sum of its nights (except for `none`).

### Stay chains (04 spec D1)

`stay_chain_id` is the id of the first reservation of a back-to-back chain: the same guest (same
`guest_name_norm`, or a shared contact hmac) in the same room (same `room_number`, or, with none assigned on
either side, the same `room_type_code`) with no gap (`check_in` = the previous stay's actual or planned
check-out). A chain head points at itself; a cancelled or no-show stay has NULL. `relink_chain(cur, id)` walks
forward from a changed stay (at most 200 hops) and rebuilds the nights of every stay whose chain moved, so the TD
30-night cap always counts across the chain (`prior_chain_nights`).

### Reads

`get_reservation(cur, id)`, `find_reservation(cur, hotel, pms_res_id, *, source_system="PMS")`,
`ensure_channel(cur, hotel, code)`, `rebuild_nights(cur, row, nights=None)`, `stayed_flag(...)`.

## Stay events (04 spec D1)

Names and payload are frozen in `clefs.core.stay_events`: `created`, `dates_changed`, `cancelled`, `no_show`,
`checked_in`, `checked_out`, `contact_changed`. `upsert_reservation` emits them in the writer's transaction:

| Change | Events | `changes` (field: [old, new]) |
|---|---|---|
| new reservation | `created` (+ the status events below when it arrives already moved) | status, check_in, check_out, channel_code |
| check_in or check_out moved | `dates_changed` | check_in, check_out (both always), nights |
| to CANCELLED | `cancelled` | status, cancelled_at |
| to NO_SHOW | `no_show` | status |
| to IN_HOUSE | `checked_in` | status, checked_in_at |
| to CHECKED_OUT | `checked_in` first if the stay never was IN_HOUSE, then `checked_out` | status, checked_out_at, actual_check_out |
| active contact set changed | `contact_changed` | reservation_contact_ids: [[old ids], [new ids]] (ids only, never values) |

- `occurred_at` is the PMS time (booked_at, cancelled_at, checked_in_at, checked_out_at, else `occurred_at=` or
  now) and never goes backwards within a reservation. Reinstating a cancelled booking has no frozen event and
  emits none.
- `emit(cur, hotel, reservation_id, event, changes, *, occurred_at=None) -> id` checks the name, the event's
  `CHANGE_FIELDS` and tz-aware instants; dates become "YYYY-MM-DD", instants ISO 8601 with offset.
- `events_for(cur, hotel, reservation_id) -> [StayEvent]`.

**Consuming.**

```python
from clefs.core.stays import on_stay_event

@on_stay_event("guests.journeys")        # "<module>.<what>"; one cursor per name
def on_event(cur, ev: StayEvent) -> None: ...
```

- `dispatch(cur, *, consumer=None, batch=200, budget_s=20) -> {consumer: {delivered, failed, dead}}` delivers in
  `(tx_id, id)` order, each event in a savepoint of the dispatcher's transaction. Delivery is at least once:
  key on `ev["id"]`.
- Only events whose writing transaction can no longer be overtaken are read: below the smallest in-flight xid of
  this database (xids another database owns are ignored; the dispatcher's own transaction may read its own rows
  when nothing older is in flight). A long import that commits after a short one is never skipped.
- A raising handler stops that consumer for the run (order holds; the others go on) and counts a failure on
  `stay_event_cursors`. After `MAX_FAILURES` (5) the event is dead-lettered to `stay_event_failures` and the cursor
  moves on. `replay_failure(cur, consumer, event_id) -> bool` re-runs one.
- The dispatcher does not check whether a consumer's module is enabled for the hotel: the consumer does.
- `registered_consumers()`, `unregister_consumer(name)` (tests).

## Contacts (04 spec D1, 3.4)

`set_contacts(cur, hotel, reservation_id, [{kind: "phone"|"email", value, provenance?}], *, replace=True,
import_batch_id=None, occurred_at=None) -> {ids, added, removed, invalid}` (also `upsert_reservation(contacts=)`).

- The value is normalised (E.164 with the hotel's calling code for national numbers; lower-cased email), sealed
  with `privacy.seal` into `value_enc`, and `privacy.contact_hmac` goes to `value_hmac`. A value that does not parse
  is skipped (`invalid`). With `replace`, an active contact missing from the list is purged (value and hmac
  nulled, `purged_at` set).
- Provenance (`classify`): an email on an `OTA_ALIAS_DOMAINS` domain, or any agoda domain on an AGODA booking (the
  UNVERIFIED heuristic, A2), is `ota_alias`; otherwise a BOOKING/AGODA/EXPEDIA reservation gives `pms_ota`, anything
  else `pms_direct`.

| Provenance | `usable_until` | `purge_after` (value nulled; OTA rows also lose the hmac) |
|---|---|---|
| `ota_alias` Booking | check_out + 30 d | check_out + 30 d |
| `ota_alias` Expedia, Agoda | check_out | check_out + 30 d |
| `pms_ota` | check_out + 30 d | check_out + 30 d |
| `pms_direct` | check_out + 730 d (03 spec 5 retention) | same; the hmac stays |

Times are hotel-local midnight; `check_out` is the actual one when known. `refresh_lifetimes(cur, id)` recomputes
them when the check-out moves (automatic in `upsert_reservation`).

- `contact_value(cur, reservation_contact_id, *, actor=None, purpose=None) -> str | None` unseals one value and
  writes the audit row `stays.contact_unseal` (kind, provenance, reservation_id, purpose; never the value). None
  when unknown, purged, or past `usable_until`.
- `contacts_for(cur, hotel, reservation_id, *, include_purged=False)` returns metadata only: id, kind, value_hmac,
  provenance, alias_domain, usable_until, purge_after, purged_at, has_value.
- `reservations_for_contact(cur, hotel, value_hmac) -> [ids]`.
- `purge_contacts(cur, *, now=None, limit=5000) -> {purged, ota, direct}` (the `stays.contact_purge` job).

## Occupancy, OTB, baseline exclusions

- `derive_occupancy(cur, hotel, start, end, *, today=None) -> {days, by_floor_rows}` (at most 400 days) recomputes
  `occupancy_daily` from stayed nights: rooms_occupied and room revenue (SUM rate x rooms), arrivals (arrived stays
  by check-in), departures (checked-out stays by actual check-out), `is_complete` for days before the business date.
  `rooms_available` is the active `hotel_rooms` count (else `hotels.rooms_count`) and keeps out-of-order rooms in
  (ARN); `rooms_ooo` is stamped for today only. It also rebuilds `occupancy_by_floor_daily` (floor from
  `hotel_rooms` by room number; unknown floor is `"?"`).
- **Source ranks.** MANUAL > PMS = DEMO > DERIVED. A derivation only updates DERIVED rows.
  `set_occupancy(cur, hotel, day, values, *, source="MANUAL"|"PMS"|"DEMO", actor=None, import_batch_id=None)`
  writes rooms_available / rooms_ooo / rooms_occupied / arrivals / departures / room_revenue_minor (missing fields
  keep the derived value) and returns None when a higher-ranked row exists. MANUAL writes are audited
  (`stays.occupancy_override`). `clear_override(cur, hotel, day, *, actor=None)` goes back to the derived figure
  (`stays.occupancy_override_clear`).
- `get_occupancy(cur, hotel, start, end)` returns each stored day plus `occupancy` (ratio string, 4 dp),
  `adr_minor` and `revpar_minor` (half up). `occupancy_by_floor(cur, hotel, start, end)`.
- `snapshot_otb(cur, hotel, *, snapshot_date=None, horizon_days=365) -> rows` photographs CONFIRMED and IN_HOUSE
  nights not yet left for every stay date in the horizon (zero rows included), with `by_channel` rooms and
  `by_channel_revenue`; a re-run the same day replaces that day's photograph. `get_otb(cur, hotel, *,
  snapshot_date, start, end)`, `pace(cur, hotel, stay_date, *, days_out=90)` (each snapshot of one night, with
  `days_before`), `latest_snapshot_date(cur, hotel)`.
- `baseline_exclusions(cur, hotel)` lists the global rows (seeded: 2026-03-01..2026-07-31 "conflict distortion")
  plus the hotel's own; `is_excluded(cur, hotel, day)`; `add_exclusion(cur, hotel, from, to, reason, *, actor)`
  and `remove_exclusion(cur, hotel, id, *, actor)` (a hotel's own rows only; audited).

## Matching OTA claims (02 spec 2.1)

```python
match_claim(cur, hotel, *, statement_line_id, ota_code, ota_res_id=None, guest_name=None, check_in=None,
            check_out=None, nights=None, amount_minor=None, currency=None, line_kind="ota_statement_line") -> row
```

1. **Exact.** `normalize_ota_res_id(ota_code, raw)`: digits only (leading zeros dropped) for BOOKING and AGODA;
   otherwise upper-case letters and digits with a known prefix (BDC, EXP, ITIN, CONF, ...) stripped when a digit
   follows. Every reservation of the hotel with that `ota_res_id_norm` (and no other channel) links: a multi-room
   booking split into PMS rows fills `reservation_ids`. Method EXACT, score 1.
2. **Fuzzy** over reservations arriving within a day, excluding direct channels and other OTAs:
   0.45 x `name_similarity` + 0.25 x check-in (1.0 same day, 0.6 one day off) + 0.15 x nights equal
   + 0.15 x amount within 5% (against `gross_minor` or `room_revenue_minor`, same currency).
   Both sides carrying different OTA ids caps the score at 0.84.
3. **Thresholds.** >= 0.85 LINKED (method FUZZY); 0.65 to 0.85 REVIEW; below UNMATCHED. Two candidates both at or
   above 0.85 go to REVIEW. `candidates` keeps the top 5 as `{reservation_id, score, parts}`; `query` keeps the claim
   with the name normalised only.

- A row a person decided (method MANUAL, REJECTED, or `confirmed_by` set) is never overwritten by a re-run.
- `rematch_open(cur, hotel, *, limit=500) -> {status: count}` re-runs UNMATCHED and REVIEW rows (after a PMS import).
- `review_queue(cur, hotel, *, status="REVIEW", limit=100)` adds a summary of each candidate reservation.
- `confirm_match(cur, hotel, match_id, *, reservation_ids, actor)` (MANUAL, LINKED; audit `stays.match_confirm`),
  `reject_match(cur, hotel, match_id, *, actor)` (REJECTED; `stays.match_reject`), `get_match`, `match_for_line`.
- `delete_matches(cur, hotel, line_kind, line_ids) -> int`: the claim owner drops the match rows of lines that
  left a corrected document (Profit Guard's re-committed statements and payouts); never touches reservations.
- `name_similarity(a, b)` (also used by Inquiry Guard): names are transliterated (Arabic letters romanised,
  diacritics dropped), case-folded, titles dropped wherever they stand, particles joined to the next token
  (`Al Maktoum` = `Al-Maktoum` = `Almaktoum`), tokens sorted, then Jaro-Winkler; a name whose 2+ tokens all
  appear in the other scores at least 0.95. `normalize_name` is the stored `guest_name_norm`.

## Tax breakdown (04 spec D3)

- `stay_lines(cur, reservation_id) -> [core.tax.StayLine]`: one line per night and room line (rate per room per
  night). After an early departure the unstayed nights are left out; a cancelled or no-show stay keeps its booked
  nights.
- `reservation_breakdown(cur, reservation_id) -> core.tax.StayTaxBreakdown`: `compose_stay` over those lines with
  `prior_chain_nights`, so each component rounds once per stay and TD units follow each line's room type. The only
  way other lanes build the `breakdown` for `profit_guard.api.expected_commission`. 404 `reservation_not_found`.

## Folio and attribution

- `upsert_folio_charges(cur, hotel, rows, *, import_batch_id=None, currency=None) -> {written, linked}`: rows
  `{pms_txn_id, posted_date, code, amount_minor, pms_res_id?, stay_date?, description?, qty?, currency?,
  is_reversal?, room_number?}`; idempotent on `(hotel, pms_txn_id)`; unknown codes become OTHER; a negative
  non-REFUND amount is a reversal. `link_orphans(cur, hotel)` attaches charges imported before their reservation.
  `folio_for`, `folio_totals(cur, hotel, reservation_id) -> {code: net minor}`.
- `apply_attribution(cur, hotel, rows) -> {matched, unmatched}` (04 spec D2, for ingest's
  BOOKING_ENGINE_ATTRIBUTION commit): `{booking_ref, utm_*, promo_code?, landing_url?}` matched on
  `booking_engine_ref`, then `pms_res_id`, then `ota_res_id`, on direct channels only; sets `utm` (merged, with
  `landing_url`), `promo_code` and `booking_engine_ref`. `attributed_bookings(cur, hotel, *, utm_source=None)`.

## Jobs

| Key | Schedule | Scope | Does |
|---|---|---|---|
| `stays.dispatch_events` | every minute | platform | `dispatch` for every consumer, 18 s budget |
| `stays.contact_purge` | 03:30 UTC | platform | `purge_contacts` in batches of 5000 (up to 20 per run) |
| `stays.occupancy_derive` | 04:30 hotel-local | per hotel | `refresh_stayed`, then `derive_occupancy` for the last 7 days and today |
| `stays.otb_snapshot` | 23:55 hotel-local | per hotel | `snapshot_otb` for the next 365 days |

## Metrics (`core.metrics`, permission `core.metrics.view`)

| Key | Unit | Measure | Time | Dimensions |
|---|---|---|---|---|
| `stays.room_nights` | nights | SUM(rooms) of stayed nights | stay date | channel, segment, room_type |
| `stays.room_revenue` | money (hotel currency) | SUM(rate x rooms) of stayed nights | stay date | channel, segment, room_type |
| `stays.bookings` | count | reservations | booked_at | channel, status, segment |
| `stays.cancellations` | count | CANCELLED reservations | cancelled_at | channel, status, segment |

## PII registry (Trust retention and DSAR)

| Table | Subject columns | Retention | Strategy |
|---|---|---|---|
| `reservations` | guest_name | 1095 days after check_out | redact guest_name, guest_name_norm |
| `reservation_contacts` | value_hmac | 1095 days after created_at, purged rows only | delete |
| `reservation_matches` | (none) | 1095 days after created_at | redact query |

## HTTP API (`router.py`, mounted through `app.CORE_ROUTER_HOOKS`)

Reads need `core.settings.view` (owner, gm, finance, revenue, dept_head); writes need `core.settings.manage`
(owner, gm). Guest names pass through `core.pii` (`guest_name` class: revenue sees None). Contact values never
leave the server through these routes. Errors are `{detail, code}` with the status shown.

| Method and path | Perm | Request | Response |
|---|---|---|---|
| `GET /hotels/{slug}/stays/occupancy?start&end&by_floor` | view | dates default to the 30 days ending at the business date; at most 400 | `{start, end, days: [{date, rooms_available, rooms_ooo, rooms_occupied, arrivals, departures, room_revenue_minor, currency, source, is_complete, occupancy, adr_minor, revpar_minor}], by_floor?: [{date, floor, rooms_occupied}]}`; 400 `invalid_range` |
| `POST /hotels/{slug}/stays/occupancy/derive?start&end` | manage | default: the last 7 days | `{days, by_floor_rows}` |
| `PUT /hotels/{slug}/stays/occupancy/{day}` | manage | `{rooms_occupied?, rooms_available?, rooms_ooo?, arrivals?, departures?, room_revenue_minor?}` (ints >= 0, at least one) | the day (source MANUAL); 400 `invalid_occupancy` |
| `DELETE /hotels/{slug}/stays/occupancy/{day}` | manage | | the day, back to its derived figure |
| `GET /hotels/{slug}/stays/otb?snapshot_date&start&end` | view | default: the latest snapshot, 90 stay dates from it | `{snapshot_date, rows: [{snapshot_date, stay_date, rooms_otb, revenue_otb_minor, currency, by_channel, by_channel_revenue}]}` (`snapshot_date` null and no rows before the first snapshot) |
| `GET /hotels/{slug}/stays/otb/pace/{stay_date}?days_out=90` | view | | `[{snapshot_date, days_before, rooms_otb, revenue_otb_minor, currency}]` |
| `GET /hotels/{slug}/stays/matches?status=REVIEW&limit=100` | view | status LINKED, REVIEW, UNMATCHED or REJECTED | `[{id, line_kind, statement_line_id, ota_code, reservation_id, reservation_ids, method, score, status, candidates: [{reservation_id, score, parts, reservation: {id, pms_res_id, channel_code, guest_name, check_in, check_out, nights, status, gross_minor, room_revenue_minor, currency}}], confirmed_by, confirmed_at, created_at, updated_at}]` |
| `POST /hotels/{slug}/stays/matches/{id}/confirm` | manage | `{reservation_ids: [int] (1-20)}` | the match (method MANUAL, LINKED); 404 `match_not_found` / `reservation_not_found` |
| `POST /hotels/{slug}/stays/matches/{id}/reject` | manage | | the match (REJECTED); 404 `match_not_found` |
| `GET /hotels/{slug}/stays/reservations/{id}` | view | | `{reservation: {...columns}, nights: [...], folio_totals: {code: minor}, contacts: [{id, kind, provenance, alias_domain, usable_until, purge_after, purged_at, has_value}], events: [StayEvent], breakdown: StayTaxBreakdown}`; 404 `reservation_not_found` |
| `GET /hotels/{slug}/stays/reservations/{id}/breakdown` | view | | `StayTaxBreakdown` as a dict (`room, sc, mf, vat, td, td_nights_charged, td_units, currency, convention, tf, dest, total, fee_rules, notices, td_label`) |
| `GET /hotels/{slug}/stays/baseline-exclusions` | view | | `[{id, hotel_slug (null = global), from_date, to_date, reason}]` |
| `POST /hotels/{slug}/stays/baseline-exclusions` | manage | `{from_date, to_date, reason}` | 201 the row; 400 `invalid_range` |
| `DELETE /hotels/{slug}/stays/baseline-exclusions/{id}` | manage | | `{ok: true}`; 404 `exclusion_not_found` (global rows cannot be removed) |

## Known limits

- Reservation writes go through `upsert_reservation` only; the HTTP API has no reservation write route (ingest
  and the demo seed are the writers).
- A chain rebuild re-derives a successor's fees from its stored nights, so a PMS-given fee split on a successor
  is replaced by the rule-derived one when its chain moves.
- Dedicated `stays.*` permissions do not exist yet (auth is frozen); the routes use `core.settings.view/manage`.
