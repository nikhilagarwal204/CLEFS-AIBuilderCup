# `clefs.core.tax`: UAE fees and taxes

Owned by the core-domain lane (Canon: core/tax; 02 spec 2.3; 04 spec D3). Import from `clefs.core.tax` only; the submodules are internal.

- `models.py`: value types (no DB).
- `calc.py`: pure maths for conventions A/B/C, compose, decompose, the TD night index and the due date.
- `rules.py`: date-effective rule resolution, owner overrides, the hotel tax profile and owner-configured rules.
- `stay.py`: `compose_stay`, one-night decomposition, TD liability over stayed nights, remittance due date.

## Tables

The schema is in `0022_core_tax.sql` and the seed rows in `0023_core_tax_seed.sql`.

| Table | What it holds |
|---|---|
| `fee_rules` | Date-effective rules: `fee_code` SC, MF, TF, TD, VAT or DEST; `calc_mode` PERCENT, PER_ROOM_NIGHT or REMIT_DEFERRAL; `rule_status` VERIFIED, ANNOUNCED_UNVERIFIED or OWNER_CONFIGURED. Seeded rows have `hotel_slug` NULL, a stable `rule_key` and a `source_url`. |
| `hotel_tax_overrides` | The owner toggle. It holds one row per hotel and rule: `apply`, plus optional dates that narrow the rule's window. |
| `hotel_tax_profiles` | Date-effective `property_category`, `convention` (A/B/C), `trn` and `td_per_bedroom` (NULL means follow the rule). |
| `fx_rates` | Units of `quote` per 1 `base`. The USD/AED 3.6725 peg is seeded (PEG, 1997-11-01). `clefs.core.money.fx_rate/convert` read it. |

### Seeded rules

Source: `docs/specs/research/uae/uae-taxes-fees.md`, checked 2026-09-27.

**Dubai (DXB)**
- `dxb_sc_10`: SC 10% on [ROOM].
- `dxb_mf_7`: MF 7% on [ROOM, SC].
- `dxb_vat_5`: VAT 5% on [ROOM, SC, MF].
- `dxb_td_<category>`: the Tourism Dirham, per room-night. The rates are 20/15/10/10/7 for hotels by star, 20/15/10 for hotel apartments, 7 for guest houses and 15/10 for holiday homes. Each rule has `cap_consecutive_nights = 30`, `per_bedroom = TRUE` and `vat_applies = FALSE`.

**Dubai 2026 announcements (ANNOUNCED_UNVERIFIED)**
- `dxb_2026_deferral_td` and `dxb_2026_deferral_mf`: a 3-month remittance deferral, 2026-04-01 to 2026-06-30.
- `dxb_2026_exempt_td` and `dxb_2026_exempt_mf`: rate 0 from 2026-05-21, with no end date. The official dates were never announced.
- These rules apply **only** to a hotel with an override where `apply = TRUE`.

**Abu Dhabi (AUH)**
- `auh_tf_4` and `auh_mf_4`: 4% each on [ROOM, SC], from 2023-09-01. There is no per-night fee.
- `auh_vat_5`: VAT 5% on [ROOM, SC, MF, TF].
- The Abu Dhabi service charge is OWNER_CONFIGURED (see `configure_rule`).

**Other emirates:** no rules until the owner configures them.

`effective_from 2000-01-01` is a sentinel meaning "in force before any data Clefs imports".

## Resolution (one hotel, one stay date)

1. **Candidates.**
   - Include the rules of the hotel's emirate (`hotels.emirate` maps to a code: `dubai` becomes DXB) and the hotel's own rules.
   - Keep a rule only when its window holds the date and its `property_category` is `*` or the hotel's category.
   - A non-UAE hotel (`country != 'AE'`) has no rules.
2. **Unverified rules and overrides.**
   - An ANNOUNCED_UNVERIFIED rule counts only while an override has `apply = TRUE`.
   - When an override has dates, they narrow the rule's window. They never widen it.
   - An override with `apply = FALSE` switches off any rule, including a verified one.
3. **Winner per fee code.** The order is: an applied unverified rule, then the hotel's own rule, then a category-specific rule, then the newest `effective_from`. The applied exemption (rate 0) therefore replaces MF and TD for its window.
4. **Hotel category and convention.**
   - They come from the newest `hotel_tax_profiles` row on or before the date.
   - Without a profile row, they come from `hotels.tax_profile` JSON (`{"convention", "property_category"}`), then from `hotels.category` plus `star_rating` (`derive_category`).
   - A hotel with neither a category nor a star rating gets no TD rate, and a notice says so.
5. **Notices.**
   - Every ANNOUNCED_UNVERIFIED rule whose announced window meets the stay produces one banner line per announcement group. This happens whether or not the rule is applied, and the line says which.
   - A stay whose convention changes mid-way gets a notice.

## Conventions and rounding

Every percentage fee is `rate × Σ(its base components)`, so each component is `k × R`, and the gross ex-TD equals `R × Σk`.

| Convention | Rule | Dubai factor | Abu Dhabi factor |
|---|---|---|---|
| A (compound) | Keeps each rule's `base[]` | 1.23585 | 1.2474 |
| B | MF, TF and DEST move to [ROOM]; VAT stays on everything | 1.2285 | 1.239 |
| C (additive) | Every fee, VAT included, moves to [ROOM] | 1.22 | 1.23 |

- **TD** is always stripped first (it is fixed and outside VAT). A base that names TD is ignored, because VAT on TD is Profit Guard's T4 anomaly.
- **Rounding:** each fee is rounded once (ROUND_HALF_UP to minor units) from its exact value.
- **decompose** gives the residual to R, so the parts always sum to the gross.
- **compose_stay** sums the exact values over every line and date, then rounds each component once per stay.

**Goldens (AED 500 gross, Dubai 3-star, TD 10):**

| Convention | R | SC | MF | VAT |
|---|---|---|---|---|
| A | 396.49 | 39.65 | 30.53 | 23.33 |
| B | 398.86 | 39.89 | 27.92 | 23.33 |
| C | 401.65 | 40.16 | 28.11 | 20.08 |

The 04 spec 4.6 quote goldens (45 nights, 12 rooms, FAM_2BR, exemption, mixed types) are all in `tests/core_domain/test_tax_stay.py`.

## Functions

All DB functions take a psycopg2 `RealDictCursor` and run inside the caller's transaction. Minor-unit amounts are in the hotel's currency.

### Pure (no DB)

```python
compose(net_room, room_nights, profile: TaxProfile) -> TaxBreakdown      # R -> full price
decompose(gross, room_nights, profile: TaxProfile) -> TaxBreakdown       # gross -> R + fees
factor(profile) -> Decimal; coefficients(fees) -> {component: k}; apply_convention(fees, "A"|"B"|"C")
td_night_indexes(dates, *, prior_nights=0) -> {date: 1-based index}      # a gap restarts the count
remittance_due_date(month, deferral_months=0) -> date                    # the 15th of the next month (+ deferral)
derive_category(category, star_rating) -> str | None; emirate_code("dubai") -> "DXB"
```

- `compose` and `decompose` take major-unit amounts. `room_nights` is the number of TD-liable unit-nights; the caller applies the cap and the stayed-nights rule.
- `TaxBreakdown` has the fields `room, sc, mf, tf, dest, vat, td, gross` (quantized Decimals), plus `currency`, `convention` and `factor`. `.minor()` gives ints and `.as_dict()` gives strings.
- `clefs.core.money.compose/decompose` forward to these two functions unchanged.

### Stay composition (04 spec D3)

```python
compose_stay(cur, hotel_slug, *, lines: list[StayLine], prior_chain_nights=0, currency=None) -> StayTaxBreakdown
    # currency: what the line rates are in (default the hotel's); TD converted per date, result labelled with it
profile_in_currency(cur, profile, currency, on) -> TaxProfile   # no FX rate: TD 0 plus a notice
StayLine(stay_date, room_type_code, rooms, rate_minor, bedrooms=None)     # rate_minor = R per room per night
StayTaxBreakdown{room, sc, mf, tf, dest, vat, td, total, td_nights_charged, td_units, currency, convention,
                 fee_rules[{id, rule_key, fee_code, rule_status}], notices[], td_label="Tourism Dirham fee"}
```

- **One call per stay.** Every room type goes in the same call, and each night is resolved with its own date's rules. A stay that crosses the start of an applied exemption is charged before the start and exempt after it.
- **TD units** = `rooms × (bedrooms if per_bedroom else 1)`. `bedrooms` comes from `room_types.bedrooms`, unless the line gives its own.
- **The 30-night cap** counts over the stay's consecutive dates from its first night, plus `prior_chain_nights`.
- `td_units` is the largest number of units on any charged night. `td_nights_charged` is the number of distinct charged nights.

### One night from the gross (02 spec 2.3 `decompose(gross, stay_date, hotel)`)

```python
decompose_night(cur, hotel_slug, *, gross_minor, stay_date, rooms=1, room_type_code=None, bedrooms=None,
                td_liable=True) -> {room, sc, mf, tf, dest, vat, td, gross, currency, convention, factor,
                                    td_units, fee_rules, notices}
```

Pass `td_liable=False` for a night that never started (a no-show) or a night past the cap. No TD is stripped for such a night.

### Tourism Dirham liability and remittance

```python
td_liability(cur, hotel_slug, *, start, end) -> {liable_unit_nights, capped_unit_nights, td_expected_minor, currency,
                                                 by_reservation{id: {unit_nights, td_minor}}, fee_rules, notices}
td_remittance_due_date(cur, hotel_slug, month) -> date
td_month(cur, hotel_slug, month) -> {month, liable_unit_nights, capped_unit_nights, td_expected_minor, currency,
                                     due_date, deferral_months, notices}
```

**`td_liability`** reads `reservation_nights` joined to `reservations`.
- **Which nights count:** only nights with `stayed = TRUE` whose reservation is not CANCELLED or NO_SHOW. A Hotel Room Night starts at check-in (AR 2/2020).
- **Night index:** counted per room line (`room_seq`) across the stay chain (`COALESCE(stay_chain_id, id)`), so night 31 onwards of a back-to-back chain owes nothing. Nights before `start` only feed the index.
- **Bedrooms:** the larger of `hotel_rooms.bedrooms` (matched by room number) and `room_types.bedrooms`.
- **Exempt nights:** a night inside an applied exemption (rate 0) counts as neither liable nor capped.

**`td_remittance_due_date`** is the 15th of the next month. When the hotel has an applied REMIT_DEFERRAL rule for TD in that collection month, its `params.remit_deferral_months` are added. The 2026 deferral applies only with the owner's override.

**`td_month`** is the input for Profit Guard's `td_ledger` rollup (`tax.td_ledger_rollup`). Profit Guard owns the ledger, the posted and remitted amounts, and the T1-T7 detectors.

### Rules, overrides and profiles

```python
resolve_profile(cur, hotel_slug, on) -> TaxProfile        # that date's fees, TD rate/cap/per-bedroom, notices
load_context(cur, hotel_slug, start=None, end=None) -> HotelTaxContext   # load once, .profile(d) per night
list_fee_rules(cur, hotel_slug, *, on=None) -> list[dict] # + override, + applies_on when `on` is given
notices(cur, hotel_slug, *, start, end=None) -> list[str] # banner; default window = the next 90 days
set_override(cur, hotel_slug, rule_id_or_key, *, apply, effective_from=None, effective_to=None, note=None, ctx=None)
get_tax_profile(cur, hotel_slug, *, on) -> {property_category, convention, trn, td_per_bedroom, effective_from,
                                             emirate, currency, source: "profile"|"hotel"}
set_tax_profile(cur, hotel_slug, *, effective_from, property_category, convention="A", trn=None,
                td_per_bedroom=None, ctx=None)
configure_rule(cur, hotel_slug, *, fee_code, calc_mode, rate, effective_from, label, verified_by, base=("ROOM",),
               effective_to=None, property_category="*", vat_applies=True, cap_consecutive_nights=None,
               per_bedroom=False, params=None, source_url=None, ctx=None)   # OWNER_CONFIGURED
```

**Writes**
- Writes upsert and return the row.
- When `ctx` (a HotelCtx or Principal) is given, they record its user and write an audit row: `tax.override.set`, `tax.profile.set` or `tax.rule.configure`.

**Errors** (all `ClefsError`)
- 404 `fee_rule_not_found`
- 422 `invalid_dates`
- 422 `invalid_convention`
- 422 `invalid_property_category`
- 422 `verified_by_required`
- 422 `emirate_required`

## HTTP

`clefs.core.tax` mounts **no routes**. The owner-facing tax API belongs to Profit Guard (02 spec 3.8: `GET /tax/fee-rules`, `GET|PUT /tax/profile`, `POST /tax/overrides`, and the `td-ledger` routes under the module's prefix), so permissions and the UI banner live with the module. Its handlers wrap:

| Route | Function |
|---|---|
| `GET /tax/fee-rules` | `list_fee_rules` |
| `GET /tax/profile` | `get_tax_profile` |
| `PUT /tax/profile` | `set_tax_profile` |
| `POST /tax/overrides` | `set_override` |
| Banner | `notices` |

Quotes (Inquiry Guard) call `compose_stay`. `core.stays.reservation_breakdown` builds its lines from `reservation_nights` and calls `compose_stay` in the reservation's currency (so do the nightly fee columns): a USD booking is never charged the AED 10 TD as USD 10.
