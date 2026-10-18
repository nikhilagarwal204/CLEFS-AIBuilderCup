> Design spec produced during planning (2026-09-27). Where it conflicts with `00-CANON.md`, the Canon wins.

# Clefs "money" modules: domain design

## 0. Main design decisions

1. **Three shared domain packages come first.** `stays` holds what the PMS says happened: reservations, nights, folio, occupancy, on-the-books (OTB). `ingest` turns files into canonical rows using alias maps, AI column mapping and saved templates. `uae_tax` holds date-effective fee rules and the decomposition math. Profit Guard, Demand Radar and Amenities all read `stays`. Only Profit Guard reads OTA statements.
2. **PMS data is treated as truth and OTA data as claims.** The two are never merged into one table. They are linked through `reservation_matches`.
3. **Every finding is idempotent** (unique fingerprint) and carries a calculation trace and evidence. Each finding is classed as RECOVERABLE, PREVENTABLE, STRUCTURAL or COMPLIANCE. Only RECOVERABLE findings that reach VERIFIED count toward "Verified results" and the pilot success threshold.
4. **Jobs must be resumable and short.** `server/vercel.json` routes everything to one Python function, so there are no long-running workers. Each job is a cursor-resumable unit of 20 seconds or less, driven by `/internal/cron/tick`. Job state lives in the foundation jobs registry.
5. **Money is BIGINT fils plus a CHAR(3) currency.** Arithmetic uses `Decimal` with ROUND_HALF_UP. When a gross amount is split into components, the rounding residual goes to room revenue so the parts always add up to the gross exactly. Rates are NUMERIC(18,8). Stay dates are DATE in the hotel's timezone. Existing request timestamps (BIGINT ms) are converted in adapters.
6. **`hotel_slug TEXT` stays the tenant key**, as in every existing table in `server/main.py`.

Module layout (the same for each module):

```
server/clefs/modules/<m>/
  __init__.py   # register(router, jobs, metrics, signal kinds, action types)
  migrations/0001_init.sql …
  models.py     # pydantic canonical models
  repo.py  service.py  routes.py  jobs.py  metrics.py  signals.py
  tests/ + tests/fixtures/
```

**Build order:**
- P0: `stays`, `ingest`, `uae_tax` and the demo seed.
- P1: Profit Guard and the public audit.
- P2: Owner Brief, WhatsApp and the action loop.
- P3: Demand Radar.
- P4: Amenities and Ask Clefs. Metrics are registered from P1 onward so Ask Clefs has data when it lands.

---

## 1. What the modules need from the foundation

- **Signals:** `emit_signal(hotel_slug, kind, dedupe_key, section, title, summary, impact_fils, currency, confidence, urgency_at, evidence_url, evidence, recommended_action{action_type, params, responsible_role}, source_module, source_ref)`.
  - Upsert on `(hotel_slug, dedupe_key)`.
  - Sections: MONEY_LOST, MONEY_AVAILABLE, SERVICE_RISK, REVIEWS, VERIFIED, COMPLIANCE. COMPLIANCE is never ranked into the top 5.
  - Statuses: OPEN, SNOOZED, RESOLVED, EXPIRED, SUPPRESSED.
- **Actions:** `register_action_type(key, verbs, param_schema, verifier, default_role, default_deadline)` and `create_action(signal_id, type, params, responsible, deadline_at)`. The state machine in section 6.3 needs an `action_transitions` log. If the foundation doesn't provide one, Owner Brief owns it.
- **Metrics:** `register_metric(MetricDef)` (schema in section 7).
- **Jobs:** `@job(key, schedule_cron_utc | per_hotel_local_time, max_runtime_s=20, cursor=True)`.
- **Files:** `files.put(bytes, kind, hotel_slug|None, retention_days)` returns `file_id`. Also needs signed URLs and a purge-by-expiry hook.
- **LLM:** `llm.structured(schema, input, tools=None|[web_search])`, returning parsed output plus citations and usage.
- **Notifications:** `notify.send(recipient, channel_order, template_key, vars, critical)`. WhatsApp internals are specified in section 6 and owned by Owner Brief. Email and SMS go through the foundation.
- **Settings:** `hotel_settings`, extended with:
  - emirate, `property_category` (HOTEL_1..5, APT_STANDARD/DELUXE/LUXURY, GUEST_HOUSE), `stacking_convention` A|B|C, TRN;
  - lat/lon and `corridor_tags[]`;
  - `subscription_fils_month`;
  - brief schedule (local time, timezone, recipients).

---

## 2. Shared domain packages

### 2.1 `server/clefs/modules/stays/` (operational core)

```sql
room_types(hotel_slug, code, name, bedrooms int default 1, max_occ, room_count, PK(hotel_slug, code))
rooms(hotel_slug, room_number, floor int, room_type_code, is_active, PK(hotel_slug, room_number))  -- joins guest_rooms by room_number
channels(hotel_slug, code BOOKING|AGODA|EXPEDIA|DIRECT_WEB|DIRECT_PHONE|WALKIN|CORPORATE|GDS|OTHER, name, default_collect_model)
reservations(id bigserial, hotel_slug, source_system PMS|CM|MANUAL|DEMO, pms_res_id, channel_code,
  ota_res_id, ota_res_id_norm, guest_name, guest_name_norm, guest_country, booked_at timestamptz,
  check_in date, check_out date, actual_check_out date, nights int, rooms_count int, room_type_code, rate_plan_code,
  status CONFIRMED|CANCELLED|NO_SHOW|IN_HOUSE|CHECKED_OUT, cancelled_at, cancel_fee_fils, no_show_fee_fils,
  collect_model OTA_COLLECT|HOTEL_COLLECT, payment_method VCC|CARD|CASH|BANK|UPC,
  gross_fils, room_revenue_fils, currency, segment LEISURE|CORPORATE|MICE|GROUP|CREW|LONG_STAY,
  adults, children, import_batch_id, updated_at, UNIQUE(hotel_slug, source_system, pms_res_id))
reservation_nights(reservation_id, stay_date, room_number, rate_fils, sc_fils, mf_fils, vat_fils, td_fils, stayed bool)
folio_charges(id, hotel_slug, reservation_id, posted_date, code ROOM|SC|MF|VAT|TD|FNB|MINIBAR|NOSHOW_FEE|CXL_FEE|REFUND|OTHER,
  qty, amount_fils, is_reversal, pms_txn_id, UNIQUE(hotel_slug, pms_txn_id))
occupancy_daily(hotel_slug, date, rooms_available, rooms_ooo, rooms_occupied, arrivals, departures, room_revenue_fils,
  source DERIVED|MANUAL|PMS|DEMO, is_complete bool, PK(hotel_slug, date))
occupancy_by_floor_daily(hotel_slug, date, floor, rooms_occupied)          -- needed by the Amenities module
otb_snapshots(hotel_slug, snapshot_date, stay_date, rooms_otb, revenue_otb_fils, by_channel jsonb, PK(...))
baseline_exclusions(hotel_slug|NULL, from_date, to_date, reason)  -- global row: 2026-03-01..2026-07-31 "conflict distortion"
guest_contacts(id, hotel_slug, name, phone_e164, email, country, last_stay_date,
  consent_marketing_at, consent_source, opted_out_at)                    -- Demand Radar campaign audience
```

**Matching OTA claims to PMS stays.** The result is stored in `reservation_matches(statement_line_id, reservation_id, method, score, confirmed_by)`.
- **Exact match:** normalize `ota_res_id` (strip spaces and prefixes, digits only for Booking and Agoda; Expedia itinerary or confirmation number) and compare against `ota_res_id_norm`.
- **Fuzzy score:**
  - Name: 0.45 × token-sort Jaro-Winkler, after transliteration, case folding and handling "SURNAME/GIVEN" ordering.
  - Check-in: 0.25 × (1.0 for the same day, 0.6 for ±1 day).
  - Nights: 0.15 × (1 if equal).
  - Amount: 0.15 × (1 if within 5%).
- **Thresholds:** 0.85 or higher auto-links. 0.65 to 0.85 goes to a review queue. Below 0.65 is UNMATCHED. An unmatched line that carries commission becomes an L3 candidate.

**Derivations:**
- `occupancy_daily` comes from `reservation_nights where stayed`. Manual overrides win.
- The OTB snapshot runs nightly because `reservations` only holds current state, and pace needs history.

### 2.2 `server/clefs/modules/ingest/`

```sql
import_batches(id, hotel_slug, kind OTA_STATEMENT|OTA_PAYOUT|VCC_REPORT|CREDIT_NOTE|PMS_RESERVATIONS|PMS_FOLIO|BANK|
  CONTRACT_PDF|COMP_RATES|ISSUANCE|OCCUPANCY|ICS|SUPPLIER_INVOICE, source_code, file_id, sha256,
  status UPLOADED|MAPPING_REQUIRED|EXTRACTING|REVIEW|COMMITTED|SUPERSEDED|FAILED,
  mapping_template_id, period_start, period_end, row_count, printed_total_fils, parsed_total_fils,
  via WEB|EMAIL|API|DEMO|PUBLIC_AUDIT, uploaded_by, errors jsonb, UNIQUE(hotel_slug, sha256))
import_rows(batch_id, row_no, raw jsonb, canonical jsonb, status OK|WARN|ERROR|DUP, messages text[], page int)
mapping_templates(id, hotel_slug|NULL(global), source_code, header_fingerprint, mapping jsonb, transforms jsonb,
  confirmed_by, confirmed_at, version, UNIQUE(hotel_slug, source_code, header_fingerprint, version))
```

**Pipeline:**
1. **Sniff the file.** Handle encodings (UTF-8, UTF-16 with BOM, cp1256), CSV delimiters, XLSX (openpyxl) and XLS (xlrd). Detect the header row as the first row with at least 60% non-numeric cells.
2. **Normalize headers.** Lowercase, strip punctuation, collapse spaces. The fingerprint is sha1 of the sorted normalized headers.
3. **Look up a mapping template** by hotel, source and fingerprint. If none exists, use the static alias map `ingest/aliases/<source>.yaml`. For example, `booking_res_statement.ota_res_id: ["reservation number","book number","booking number"]` and `check_in: ["arrival","check-in"]`. These aliases are best guesses: no OTA publishes its headers, so each must be validated on real samples.
4. **Check coverage.** If a required canonical field is still unmapped, call the LLM with the headers plus 5 sample rows (guest names masked). The structured output is `{mappings:[{canonical, header, transform: date_fmt|decimal_sep|sign_flip|pct_to_ratio, confidence}], unmapped:[]}`. The user confirms, and the confirmed mapping is saved as a template.
5. **PDFs:** LLM extraction into canonical rows, each with page and line references. Status is set to REVIEW. The gate is automatic: the sum of lines must equal `printed_total_fils` within AED 1, or the batch cannot be committed without a human override.
6. **Commit** writes the module tables. A re-uploaded corrected file supersedes the earlier batch rather than duplicating it.
7. **Email ingestion:** `POST /inbound/email/{inbound_token}` accepts Postmark-style JSON (`Attachments[].Content` base64, `Name`, `ContentType`). The token maps to a hotel. It is behind `INBOUND_EMAIL_ENABLED`.
8. **E-invoicing stub:** `ingest/adapters/pint_ae.py` exposes `parse_ubl(xml) -> list[CanonicalStatementLine]` and raises `NotImplementedError`. It already documents the future mapping (`cbc:ID`, `cac:InvoiceLine`, `cac:TaxTotal`) for UAE e-invoicing in 2027.

### 2.3 `server/clefs/modules/uae_tax/`

```sql
fee_rules(id, emirate DXB|AUH|SHJ|AJM|RAK|FUJ|UAQ, property_category|'*', fee_code SC|MF|TF|TD|VAT|DEST,
  calc_mode PERCENT|PER_ROOM_NIGHT, rate numeric(18,8), base text[], vat_applies bool, cap_consecutive_nights int,
  per_bedroom bool, effective_from date, effective_to date, rule_status VERIFIED|ANNOUNCED_UNVERIFIED|OWNER_CONFIGURED,
  params jsonb /* e.g. remit_deferral_months */, source_url, verified_by, verified_at)
hotel_tax_overrides(hotel_slug, fee_rule_id, apply bool, effective_from, effective_to, acknowledged_by, acknowledged_at)
td_ledger(hotel_slug, month date, liable_room_nights, td_expected_fils, td_collected_fils, td_remitted_fils,
  remitted_on, remit_reference, due_date, status OPEN|REMITTED|MISMATCH, PK(hotel_slug, month))
fx_rates(date, base, quote, rate numeric(18,8), source PEG|ECB|MANUAL, PK(date, base, quote))  -- USD/AED = 3.6725 PEG
```

**Seed rules:**
- **Dubai:** SC 10% on base [ROOM]; MF 7% on [ROOM, SC]; VAT 5% on [ROOM, SC, MF]; TD per room-night at 20/15/10/10/7 by star rating, and 20/15/10 for Luxury/Deluxe/Standard hotel apartments, and 7 for guest houses. TD is capped at 30 nights, has `vat_applies=false`, and is charged only on stayed nights.
- **Abu Dhabi:** TF 4% and MF 4%. There is no per-night fee from September 2023. SC is OWNER_CONFIGURED.
- **Other emirates:** empty until the owner configures them (`verified_by` is required).
- **2026 Dubai announcements:** the TD/fee exemption and the 3-month remittance deferral are rows with status ANNOUNCED_UNVERIFIED. They are applied only when the hotel has an override row with `apply=true`. The UI shows a banner whenever such a row intersects the next 90 days.

**Decomposition** is `decompose(gross_fils, stay_date, hotel) -> {R, SC, MF, VAT, TD}`, with `base = G − TD`:
- **Convention A:** `R = base/((1+s)(1+m)(1+v))`, SC = s·R, MF = m·(R+SC), VAT = v·(R+SC+MF). The Dubai factor is 1.23585.
- **Convention B:** `R = base/((1+s+m)(1+v))`, SC = s·R, MF = m·R, VAT = v·(R+SC+MF). The factor is 1.2285.
- **Convention C:** `R = base/(1+s+m+v)`, and SC, MF and VAT are each a rate times R. The factor is 1.22.
- The hotel's convention overrides the rule's `base[]`: B forces the MF base to [ROOM], and C forces every base to [ROOM].

**TD liability** = Σ over stayed nights of `units × rate(category, date)`, where:
- `units = rooms_count × (bedrooms if per_bedroom else 1)`;
- the night index is counted across back-to-back reservations for the same guest and room with no gap, and capped at 30.

**TD remittance due date** = the 15th of the following month. If an applied deferral rule exists, the deferral months are added.

**Golden values (AED 500 gross, 3-star, so TD = 10):**

| Convention | R | SC | MF | VAT | Check |
|---|---|---|---|---|---|
| A | 396.49 | 39.65 | 30.53 | 23.33 | sums to 490.00 |
| B | 398.86 | 39.89 | 27.92 | 23.33 | |
| C | 401.65 | 40.16 | 28.11 | 20.08 | the 0.01 residual goes to R |

---

## 3. Profit Guard (OTA leakage): `server/clefs/modules/profit_guard/`

### 3.1 Tables

```sql
ota_contracts(id, hotel_slug, ota_code, entity_name, entity_country, effective_from, effective_to,
  base_commission_pct numeric(6,3), commission_base_rule ROOM_INCL_VAT|RSR_TAX_INCL|NET_RATE|ROOM_EXCL_TAX,
  excluded_charge_types text[] /* MUNICIPALITYFEE, TOURISMFEE */, collect_models text[],
  dispute_rules jsonb /* [{kind:HOURS_AFTER_CHECKOUT,h:48},{kind:BUSINESS_DAYS_AFTER_INVOICE,d:14},{kind:DAY_OF_NEXT_MONTH,day:4}] */,
  vcc_rules jsonb /* {charge_within_days_after_checkout:365} | {expires_days_after_checkout:180} | {upc_charge_within_h:24} */,
  clauses jsonb /* [{ref:"GDT v2601 2.3.2(ii)", text, page}] */, source_file_id, extracted_by LLM|MANUAL, confirmed_by, confirmed_at)
ota_programs(id, hotel_slug, ota_code, program GENIUS|MOBILE|COUNTRY|EARLY_BOOKER|PREFERRED|PREFERRED_PLUS|
  VISIBILITY_BOOSTER|AGODA_VIP|AGX|OTHER, kind DISCOUNT|COMMISSION_UPLIFT|PER_BOOKING_COST, pct, fixed_fils,
  booker_countries text[], stay_from, stay_to, rate_plans text[], stackable bool, source MANUAL|EXTRANET_EXPORT)
channel_config(hotel_slug, ota_code, mf_configured_as_charge bool, td_configured_as_charge bool, configured_from date,
  card_fee_pct, payments_fee_pct)
owner_policies(hotel_slug, max_total_discount_pct default 25, parity_tolerance_pct default 1.0,
  min_finding_fils default 2500, confidence_floor default 0.5)
ota_statements(id, hotel_slug, ota_code, doc_kind COMMISSION_INVOICE|RES_STATEMENT|PAYOUT_STATEMENT|CREDIT_NOTE|DEBIT_NOTE,
  doc_no, doc_date, related_doc_no, period_start, period_end, currency, total_fils, batch_id, UNIQUE(hotel_slug, ota_code, doc_no))
ota_statement_lines(id, statement_id, hotel_slug, ota_code, ota_res_id, ota_res_id_norm, guest_name, check_in, check_out,
  room_nights, line_status OK|NO_SHOW|CANCELLED|MODIFIED|ADJUSTMENT, collect_model, gross_fils, commissionable_fils,
  commission_pct, commission_fils, charges jsonb /* [{type, amount_fils, is_already_collected_from_guest,
  is_included_total_partner_payout}] */, booker_country, programs text[], currency, fx_rate, import_row_id)
ota_payouts(id, hotel_slug, ota_code, payout_ref, payout_date, payout_type GROSS|NET, status, currency, amount_fils,
  bank_transfer_reference, batch_id)
ota_payout_lines(payout_id, ota_res_id_norm, total_amount_paid_fils, commissionable_fils, commission_fils,
  charges_fils, partner_payout_fils, line_kind BOOKING|REFUND|CHARGEBACK|ADJUSTMENT, event_date)
bank_credits(id, hotel_slug, value_date, currency, amount_fils, aed_amount_fils, reference, counterparty, matched_payout_id)
virtual_cards(id, hotel_slug, ota_code, vcc_id, ota_res_id_norm, reservation_id, activation_date, expiration_date,
  charge_deadline date /* min(expiry, rule-derived) */, initial_fils, current_balance_fils, currency,
  status AVAILABLE|FUNDED|NOT_LOADED|PARTIALLY_CHARGED|FULLY_CHARGED|CANCELLED|EXPIRED, last_seen_batch_id)
rate_evidence(id, hotel_slug, captured_at, channel, stay_date, los, adults, room_label, plan_label, price_fils,
  currency, taxes_included bool, device DESKTOP|MOBILE, login_state LOGGED_OUT|LOGGED_IN|MEMBER, booker_country,
  search_url, screenshot_file_id, source MANUAL|SERPAPI|FEED, comparable_key /* date|los|adults|room_map|device|login */)
pg_findings(id, hotel_slug, detector, fingerprint, ota_code, reservation_id, statement_line_id, subject_ref,
  class RECOVERABLE|PREVENTABLE|STRUCTURAL|COMPLIANCE, amount_fils, currency, recovered_fils default 0,
  confidence numeric(4,3), calc jsonb /* ordered formula steps with inputs */, evidence jsonb /* row ids, file ids, clause refs */,
  deadline_at, status DETECTED|ACCEPTED|DISPUTED|RECOVERED|REJECTED|UNRESOLVED|DISMISSED,
  dismiss_reason, signal_id, action_id, dispute_packet_id, first_run_id, last_run_id, UNIQUE(hotel_slug, fingerprint))
pg_finding_events(finding_id, at, from_status, to_status, actor, channel, note, amount_fils, evidence jsonb)  -- the recovery ledger
dispute_packets(id, hotel_slug, ota_code, finding_ids bigint[], total_fils, status DRAFT|APPROVED|SUBMITTED|ACKNOWLEDGED|
  CREDITED|PARTIAL|REJECTED, html_file_id, pdf_file_id, extranet_text, email_subject, email_body,
  approved_by, approved_at, submitted_at, follow_up_at, action_id)
detector_tuning(hotel_slug, detector, min_amount_fils, confidence_floor, prior_a, prior_b, suppressed_until, params jsonb)
channel_economics(hotel_slug, month, channel, room_nights, gross_fils, room_rev_fils, sc_fils, pass_through_tax_fils,
  commission_fils, commission_on_tax_fils, payment_cost_fils, fx_loss_fils, cancellation_cost_fils, program_cost_fils,
  net_fils, avg_discount_depth, PK(hotel_slug, month, channel))
lead_audits(id uuid, token_hash, created_at, expires_at, ip_hash, file_id, ota_code, status, report jsonb,
  lead_name, hotel_name, whatsapp_e164, email, consent_contact bool, notified_founder_at)
```

### 3.2 Canonical models (`models.py`)

- `CanonicalStatementLine`, `CanonicalPayoutLine`, `CanonicalVcc`, `CanonicalContractTerms` (the LLM structured-output schema for contract PDFs), `Finding`, `CalcStep{label, formula, inputs{}, result_fils}`.

**Commission base rule:**
- **Booking.com (GDT v2601 cl. 2.3.2(ii)):** `expected_base = gross − Σcharges[type ∈ excluded_charge_types]`, applied only when `channel_config.*_configured_as_charge` is true for the stay date. Service charge stays in the base.
- **Agoda:** `base = RSR` (tax-inclusive, after promotions). Commission landing on taxes is STRUCTURAL: it is shown in net ADR and contract negotiation, never disputed.
- **Expedia Hotel Collect:** `ROOM_EXCL_TAX` unless the contract says otherwise.

### 3.3 Detectors (`detectors/l01_*.py … t07_*.py`)

Every detector uses the same function signature: `run(ctx, scope) -> list[FindingDraft]`. Each draft carries amount, class, a base confidence, a deadline and a fingerprint.

The tolerance in every comparison is `tol = max(100 fils, 0.2% × amount)`.

| ID | Rule (flag when) | Amount | Class / base confidence | Deadline |
|---|---|---|---|---|
| L1 | Booking: `reported_base − (gross − excluded_charges) > tol` while MF/TD are configured as charges. If they are *not* configured, emit L1b | L1: pct × excess. L1b: pct × Σ(MF+TD) over the last 90 days ÷ 3 per month | L1: RECOVERABLE 0.9 with price_breakdown, 0.7 inferred via `uae_tax`. L1b: PREVENTABLE 0.85 (action: configure the charges in the extranet). Agoda: STRUCTURAL | Dispute window |
| L2 | `effective_pct = commission/commissionable_base`; `expected = base_pct + Σ active uplifts` (Preferred, Preferred Plus, Visibility Booster for date and booker country, AGX); flag if `effective − expected > 0.1pp` | (effective − expected) × base | RECOVERABLE 0.85. 0.5 if Visibility Booster is active and booker country is unknown | Invoice + 14 business days |
| L3 | Line status OK or commission > 0, but the matched PMS reservation is NO_SHOW (no fee collected) or CANCELLED with the fee waived. Also an unmatched line with commission | commission − pct × fee actually collected | RECOVERABLE 0.9 (exact match), × match score | Booking: marking deadline; Expedia: the 4th |
| L4 | `actual_check_out < check_out` and the OTA still reports the original room nights | pct × Σ nightly rates of unstayed nights − early-departure fee | RECOVERABLE 0.8 | Same as L3 |
| L5 | OTA commissionable ≠ PMS folio room revenue, normalized through `uae_tax`, by more than tol | If OTA is higher: pct × difference. If PMS is higher: SERVICE_RISK signal (possible guest overcharge) | RECOVERABLE 0.75 | Dispute window |
| L6 | `depth = 1 − sell/public` where public is BAR for that date and room from `bar_calendar`; the stacked program depth is `1 − Π(1 − dᵢ)`; flag if depth > owner cap | (depth − cap) × public × nights | PREVENTABLE 0.8 (action: restrict stacking) | none |
| L7 | For the same `comparable_key`: `price_ch < min(direct, other OTAs) × (1 − tol%)`. Logged-in/member prices are excluded unless compared like-for-like | exposure = (direct − ota) × rooms booked on that channel for the date | PREVENTABLE 0.6; 0.75 with a screenshot | none |
| L8 | VCC balance > 0, status AVAILABLE/FUNDED/PARTIALLY_CHARGED, activation reached, `charge_deadline ≥ today`. Agoda UPC: 24h after issue | current balance | MONEY_AVAILABLE 0.95. Urgency tiers at 30/14/7/1 days | charge_deadline |
| L9 | `charge_deadline < today` and balance > 0 | balance | MONEY_LOST 0.9. Recoverable only via OTA support, confidence 0.3 | none |
| L10 | Line: `partner_payout ≠ total_paid − commission − Σcharges(included in payout)`. Payout: Σ lines ≠ amount. Bank: credit ≠ payout − expected bank fee, matched by reference, else by amount ±3 days | difference | RECOVERABLE 0.85 | none (aging) |
| L11 | `expected_aed = amount × ref_rate` (USD peg 3.6725; ECB for EUR); `loss = expected − credited − expected_fee`; flag if more than 0.5% or AED 50 | loss | PREVENTABLE 0.8 (action: change payout currency or bank) | none |
| L12 | Payout or statement refund line exists but commission was not reduced pro-rata | pct × refunded amount | RECOVERABLE 0.8 | Dispute window |
| L13 | Chargeback or dispute lines, aged in buckets 0–30/31–60/61–90/90+ | disputed amount | MONEY_LOST 0.7, urgency by age | none |
| L14 | Proactive, from PMS data before any statement arrives: a Booking-sourced NO_SHOW, early departure or modification not yet reported. `deadline = check_out 00:00 local + 48h` (conservative). Expedia Hotel Collect: the 4th of the following month at 12:00 local | expected commission at risk | MONEY_AVAILABLE 0.85. Alerts at T−24h and T−6h. After the deadline it becomes L3 with confidence × 0.5 | as stated |
| L15 | Monthly: Σ commission invoiced by an OTA entity with `entity_country ≠ AE` | 5% × commission (reverse-charge VAT to self-account) | COMPLIANCE, excluded from money totals | VAT return date |
| T1 | TD postings < rooms_count × stayed nights (charged per reservation, not per room) | under-collection liability | COMPLIANCE 0.9 | remittance |
| T2 | TD posted on continuous-stay night 31 or later (across back-to-back reservations) | over-collection (guest refund risk) | COMPLIANCE 0.9 | none |
| T3 | Posted TD rate ≠ rule rate for the category | difference × nights | COMPLIANCE 0.9 | none |
| T4 | Posted VAT ≈ v × (R+SC+MF+TD), i.e. VAT charged on TD | v × TD | COMPLIANCE 0.85 | VAT return |
| T5 | TD postings for a night > units | duplicate amount | RECOVERABLE (to guest) 0.95 | none |
| T6 | TD posted on a NO_SHOW | amount | COMPLIANCE 0.95 | none |
| T7 | `td_ledger`: collected ≠ remitted ± AED 1, or unremitted with due date in 5 days or less | difference | COMPLIANCE 0.9 | due date |
| TX1 | A fee was posted on a night inside an exemption window the owner has applied | amount | COMPLIANCE 0.9 | none |

**Fingerprint:** `sha1(detector|ota|ota_res_id_norm|statement doc_no or month)`. Re-imports update `last_run_id`, never duplicate. Findings are grouped into one signal per `(detector, ota, statement or month)`, so the brief says "12 Booking no-show commissions, Sep invoice, AED 1,020" rather than 12 lines. L8 and L14 emit one signal per item when urgency is 1 day or less.

### 3.4 Confidence and feedback tuning

`confidence = base × match_score × completeness × feedback`, where:
- `completeness = 1 − 0.15 × missing optional inputs`;
- `feedback = (a + accepted)/(a + b + accepted + dismissed_fp)`, a Beta prior (a=4, b=1) per hotel and detector, shrunk toward the global average across hotels.

Dismiss reasons are FALSE_POSITIVE_DATA, CONTRACT_EXCEPTION, ALREADY_HANDLED, TOO_SMALL, NOT_WORTH_DISPUTING, OTHER. A weekly job `pg.feedback_tune` applies them:
- **TOO_SMALL:** `min_amount = max(current, 1.2 × median dismissed amount)`.
- **CONTRACT_EXCEPTION:** opens a contract-edit prompt.
- **3 or more false positives in 30 days:** that detector is suppressed for the hotel for 14 days, and a founder-review signal is raised.

Findings below `confidence_floor` or `min_amount` are stored but not signalled.

**Pilot scorecard:** `valid = count(status ∈ ACCEPTED, DISPUTED, RECOVERED)`. Success means `valid ≥ 3` or `Σ amount_fils(valid, RECOVERABLE) ≥ 3 × subscription_fils_month × pilot_months`.

### 3.5 Recovery ledger and auto-verification

- **Status flow:** DETECTED → ACCEPTED (owner agrees) → DISPUTED (packet submitted) → RECOVERED, REJECTED or UNRESOLVED. Any open state can go to DISMISSED.
- **Trigger:** after each commit of a CREDIT_NOTE, OTA_STATEMENT or OTA_PAYOUT batch, `pg.auto_verify` looks for lines with the same `ota_res_id_norm` where commission was reduced or a negative adjustment exists. It adds up matched credits into `recovered_fils`.
- **Outcomes:** 95% or more recovered sets RECOVERED, with `verified_by=STATEMENT`, the line id as evidence, and a `pg.recovery.verified` signal to the VERIFIED section. A partial credit stays DISPUTED with a note.
- **Timeout:** 45 days after submission with no credit, and a later invoice issued, sets UNRESOLVED.

### 3.6 Net ADR by channel (`channel_economics`, nightly rollup)

Figures are per channel and month, allocated by stayed night:

```
RN = stayed room nights            Gross = Σ guest-paid
PassThrough = MF + VAT + TD        RoomRev = R  (+ SC if setting treat_sc_as_revenue, default true)
C = actual commission (statements) else expected from contract
CommissionOnTax = C − pct × base_ex_tax      (Agoda STRUCTURAL; Booking L1 cases)
PayCost = card/VCC fee % × charged + payments-by-Booking fee   FX = L11 losses
CxlCost = commission on non-stayed bookings + AGX per-booking costs
ProgramCost (info only) = Σ (public − sell) × nights
Net = RoomRev − C − PayCost − FX − CxlCost ;  NetADR = Net / RN ;  Retention = Net / Gross
```

Direct channels: C = 0, and PayCost = card fee plus the booking-engine fee (a setting).

### 3.7 Dispute packets

`POST /disputes` groups findings for one OTA into a packet containing:
- booking references, the contract clause (from `ota_contracts.clauses`), the calculation trace, evidence links and screenshots;
- the owner-approval stamp and follow-up status.

Output formats:
- **HTML:** rendered server-side, print-optimised.
- **PDF:** generated with `fpdf2` (pure Python, so it runs on Vercel), because WhatsApp needs a document.
- **Extranet text:** 1,000 characters or fewer, one template per OTA and detector. For example: "Reservation 4000012345: guest did not arrive (no-show) 12 Oct 2026; per GDT v2601 commission of AED 84.70 should not apply; please issue a credit note."
- **Email:** subject and body.

APPROVED requires the owner or finance role. The `pg.dispute_followup` job reminds every 10 business days while the packet is SUBMITTED with no credit.

### 3.8 API (under `/v1/hotels/{slug}/profit-guard`)

- **Imports:** `POST /imports` (multipart, kind, ota_code); `GET /imports/{id}`; `POST /imports/{id}/mapping` (confirm, save template); `POST /imports/{id}/review` (PDF row edits); `POST /imports/{id}/commit`.
- **Contracts and programs:** `POST /contracts/extract`, then `PUT /contracts/{id}` (confirm); CRUD on `/programs` and `/channel-config`.
- **Findings:** `GET /findings?status&detector&ota&class&from&to`; `GET /findings/{id}` (trace and evidence); `POST /findings/{id}/transition {to, note}`; `POST /findings/{id}/dismiss {reason, note}`.
- **Disputes:** `POST /disputes`; `GET /disputes/{id}`; `POST /disputes/{id}/approve | /submitted | /outcome`; `GET /disputes/{id}/print`; `GET /disputes/{id}.pdf`.
- **Other reads:** `GET /ledger`; `GET /channel-economics?from&to`; `GET /vcc?status`; `POST /vcc/{id}/charged`; `POST|GET /rate-evidence`; `GET /deadlines`; `GET /pilot-scorecard`; `POST /detectors/run`.
- **Tax:** `GET /tax/fee-rules`; `GET|PUT /tax/profile`; `POST /tax/overrides`; `GET /tax/td-ledger?month`; `PUT /tax/td-ledger/{month}` (remittance); `GET /tax/td-export.csv?month`. The export is an internal reconciliation file, not a DET upload format.
- **Global:** `POST /inbound/email/{token}`.
- **Public:** `POST /public/audits`; `POST /public/audits/{token}/lead`; `GET /public/audits/{token}`.

### 3.9 Jobs, signals, metrics

**Jobs:**
- `pg.detect_on_commit`: event-enqueued.
- `pg.deadline_watch`: hourly (L8, L14, T7).
- `pg.nightly_detect`: 02:00 local (L9, L13, L6, L7, T-series).
- `pg.auto_verify`: on commit and daily.
- `pg.channel_economics_rollup`: 03:00 local.
- `pg.dispute_followup`: daily.
- `pg.feedback_tune`: weekly.
- `pg.public_audit_purge`: daily.
- `tax.td_ledger_rollup`: on the 1st.
- `tax.td_remit_reminder`: on the 10th and 14th.
- `tax.unverified_rule_banner`: weekly.

**Signal kinds:**

| Kind | Section |
|---|---|
| `pg.leak.detected` | MONEY_LOST |
| `pg.deadline.approaching` | MONEY_AVAILABLE |
| `pg.vcc.expiring` | MONEY_AVAILABLE |
| `pg.vcc.expired` | MONEY_LOST |
| `pg.payout.shortfall` | MONEY_LOST |
| `pg.parity.breach` | MONEY_AVAILABLE |
| `pg.config.fix_available` | MONEY_AVAILABLE |
| `pg.dispute.ready_for_approval` | MONEY_AVAILABLE |
| `pg.recovery.verified` | VERIFIED |
| `pg.import.needs_review` | SERVICE_RISK |
| `pg.compliance.vat_rcm` | COMPLIANCE |
| `tax.td.remittance_due` | COMPLIANCE |
| `tax.td.anomaly` | COMPLIANCE |
| `tax.rule.unverified` | COMPLIANCE |

**Metrics:**
- Money: `pg.leakage_detected_aed`, `pg.leakage_recovered_aed`, `pg.recovery_rate`, `pg.open_findings`, `pg.commission_paid_aed`, `pg.effective_commission_pct`, `pg.commission_on_tax_aed`, `pg.net_adr_aed`, `pg.gross_adr_aed`, `pg.net_retention_pct`, `pg.vcc_uncollected_aed`.
- Tax: `tax.td_collected_aed`, `tax.td_remitted_aed`.
- Dimensions: channel, detector, class, status, month, stay_date.

### 3.10 Client (`client/app/app/[hotelSlug]/profit-guard/`)

- `page.tsx` **Overview:**
  - KPI tiles: Found, Recovered (verified), Open, Due within 72h.
  - `RecoveryFunnel`: HBar stacked by status.
  - Leakage by detector (HBarChart).
  - `DeadlineRail` with `CountdownChip`s.
  - `PilotScorecard`.
- `imports/`:
  - `UploadDropzone` and batch list.
  - `MappingReview`: header, canonical-field select, AI confidence, 5 sample rows.
  - `PdfReview`: page image beside the editable extracted rows, plus a totals check.
- `findings/`: `FindingsTable` with filters. `findings/[id]` shows the `CalcTrace` stepper, an `EvidenceList` and an `ActionBar` (Accept, Dispute, Dismiss with reason).
- `disputes/[id]`: packet preview and approve. `disputes/[id]/print` is the print route.
- `channels/`: `WaterfallChart` (Gross → pass-through → commission → fees → Net per channel) and monthly Net ADR columns.
- `vcc/`: sorted by deadline, with countdowns and a "mark charged" action.
- `tax/`: TD ledger by month, the unverified-rule banner, a read-only fee-rule table showing `verified_by`, and a `ConventionPreview` (enter a gross, see the breakdown under A/B/C).
- `contracts/`: extraction review with clause citations.
- `parity/`: `RateEvidenceForm` (screenshot upload, device, login state) and a parity grid.
- **Chart-kit additions** to the foundation UI kit, extending `client/app/app/charts.tsx`: `WaterfallChart`, `LineChart` or `Sparkline`, `HeatmapCalendar`, `CountdownChip`, `ConfidenceDots`, `Money` (fils to "AED 1,234.50", tabular numbers). Two new mark colours, CHART_LOSS and CHART_GAIN, should be stepped into the same OKLCH band the existing palette comment describes.

### 3.11 Public "Audit one OTA statement" lead magnet

- **Routes:** `client/app/audit/page.tsx` (indexable marketing page) and `client/app/audit/r/[token]/page.tsx` (noindex report).
- **Flow:** an anonymous upload runs the same `ingest` and detectors in a sandbox context (`hotel_slug=NULL`) with default assumptions: Dubai, 3-star, convention A, Booking 15% / Agoda 18% / Expedia 18%. Charge configuration is unknown, so L1 is reported under both scenarios. Detectors needing PMS data (L3 to L5, L14) run only from the statement's own status columns.
- **Report:** a teaser shows the total and counts. The full breakdown unlocks after lead capture (name, hotel, WhatsApp or email, consent). Guest names are always masked.
- **Limits:** 3 audits per IP hash per day and 50 per day globally; 10 MB; 5,000 rows; PDF up to 10 pages; MIME sniffing.
- **Retention:** file, rows and report are deleted at 30 days by `pg.public_audit_purge`.
- **Founder notification:** `notify.send` by email and WhatsApp.

### 3.12 Test fixtures and cases (`profit_guard/tests/fixtures/`)

- **Fixtures:**
  - `booking_statement_v1.csv`, `_v2_utf16.tsv` and `_v3_comma_decimal.csv` (three header variants);
  - `agoda_finance.xlsx`, `expedia_hc_invoice.pdf` (synthetic), `booking_payout.json` (Payments by Booking.com shape);
  - `credit_note_booking.csv`, `bank_statement.csv`.
- **Cases:**
  - Alias detection per variant.
  - LLM mapping mocked, with the template reused on second upload.
  - PDF totals gate.
  - Idempotent re-import (no new findings).
  - Superseded batch.
  - For every detector: a positive case, a negative case and a boundary case (for example exactly 0.1pp does not flag).
  - Fuzzy match: "AL MANSOURI/AHMED" vs "Ahmed Al Mansouri".
  - A multi-room booking under one `ota_res_id`.
  - L14 deadline across month-end and timezone (checkout 23:00 Dubai is 19:00 UTC).
  - Auto-verify full and partial credit.
  - Tax golden values; TD cap across back-to-back reservations; no-show TD exempt; exemption window only when applied.
  - Feedback tuning raises the minimum amount.

### 3.13 Edge cases

- Statements in EUR or USD.
- Modified bookings producing several lines.
- Credit notes that reference earlier invoices.
- Stays that cross months (allocate by night).
- Agoda Property Collect producing a negative payout because of the ePass commission offset.
- A bank credit that bundles several payouts (subset-sum match with a limit of 6 payouts).
- A hotel changing stacking convention mid-period (the tax profile is date-effective).
- Arabic-script names (transliterate before matching).
- Legitimate Genius + Mobile stacking.
- AGX costs appearing as separate lines.

---

## 4. Demand Radar: `server/clefs/modules/demand_radar/`

### 4.1 Tables

```sql
venues(code pk, name, lat, lon, emirate, corridor_tags text[], capacity, feed_available bool, last_verified_at, source_url)
  -- DWTC(25.2285,55.2866)[DWTC_SZR,RED_LINE,DEIRA_BURDUBAI,AL_BARSHA]; DEC(Expo City)[DEC_SOUTH]; ADNEC; EXPO_CENTRE_SHJ[SHARJAH,DEIRA_BURDUBAI];
  -- COCA_COLA_ARENA; ETIHAD_ARENA[YAS]; YAS_MARINA[YAS]  (coordinates approximate; verify)
events(id uuid, canonical_key, title, category CONFERENCE|EXPO|SPORTS|CONCERT|FESTIVAL|PUBLIC_HOLIDAY|SCHOOL_HOLIDAY|
  RELIGIOUS|SOURCE_MARKET_HOLIDAY|COMMUNITY|CITYWIDE, venue_codes text[], lat, lon, start_date, end_date,
  status SCHEDULED|RESCHEDULED|POSTPONED|CANCELLED, original_start, original_end, attendance_est, attendance_source,
  phq_rank, phq_local_rank, predicted_accom_spend_usd, moon_dependent bool, date_uncertainty_days int,
  verification VERIFIED|UNVERIFIED, confidence, source_urls jsonb, first_seen_at, last_checked_at, hotel_slug NULL|slug)
event_sources(event_id, source PREDICTHQ|TICKETMASTER|CURATED|AI_WEB|MANUAL|ICS, external_id, payload jsonb, fetched_at,
  UNIQUE(source, external_id))
event_changes(event_id, at, field, old, new, source)
holidays(country_code, name, start_date, end_date, kind PUBLIC|SCHOOL|RELIGIOUS, moon_dependent, uncertainty_days, source_url)
source_markets(hotel_slug, country_code, share_pct)   -- default: W.Europe 20, S.Asia 17, GCC 16, CIS 14, MENA 10, NE/SE Asia 9
demand_features_daily(hotel_slug, date, feature, value, PK(...))   -- PredictHQ Features API
hotel_event_relevance(hotel_slug, event_id, distance_km, travel_band, corridor_match bool, score, expected_rn_uplift,
  dismissed bool, dismiss_reason, features jsonb, computed_at, PK(hotel_slug, event_id))
event_class_prefs(hotel_slug, class_key /* category | category:venue */, useful int, dismissed int, muted bool)
comp_set(hotel_slug, comp_id, name, property_token, lat, lon, stars, rooms, is_demo)
comp_rates(hotel_slug, comp_id, stay_date, los, adults, captured_at, source SERPAPI|MAKCORPS|XOTELO|CSV_LIGHTHOUSE|CSV_RATEGAIN,
  lowest_fils, currency, sold_out bool, by_source jsonb, indicative bool default true)
bar_calendar(hotel_slug, stay_date, room_type_code, bar_fils, source MANUAL|CSV|RATE_EVIDENCE, captured_at)
rate_guardrails(hotel_slug, room_type_code, month, dow|NULL, floor_fils, ceiling_fils, max_step_pct default 15)
demand_days(hotel_slug, stay_date, demand_index, components jsonb, top_event_ids uuid[], computed_at)
rate_recommendations(id, hotel_slug, stay_date, room_type_code, current_bar_fils, recommended_fils, floor_fils, ceiling_fils,
  change_pct, levers jsonb, rationale text, driver_event_ids uuid[], confidence, impact_fils,
  status PROPOSED|APPROVED|ADJUSTED|REJECTED|SUPERSEDED|EXPIRED|APPLIED, action_id, baseline jsonb, outcome jsonb)
dr_guidance(id, hotel_slug, event_id, kind STAFFING|KB_UPDATE|PRE_ARRIVAL_OFFER, payload jsonb, status, action_id)
campaign_drafts(id, hotel_slug, event_id, channel WHATSAPP|INSTAGRAM|EMAIL, audience jsonb, copy jsonb,
  suggested_send_at, status DRAFT|APPROVED|EXPORTED)
ghc_checklist(hotel_slug, item_code, status TODO|DONE|NA, note, updated_at)
```

### 4.2 Sources and adapters (`adapters/`)

- **PredictHQ Events** (`phq_events.py`), run daily and incrementally with `updated.gte=cursor`:
  `GET /v1/events/?within=60km@{lat},{lon}&active.gte={today}&active.lte={+180d}&category=conferences,expos,sports,concerts,festivals,performing-arts,community,public-holidays,school-holidays&state=active,predicted&rank.gte=40&limit=500`.
- **PredictHQ Features** (`phq_features.py`): `POST /v1/features/` with a 10 km radius, daily `phq_attendance_{conferences,expos,sports,concerts,festivals}` sums and `phq_rank_public_holidays`.
- **Ticketmaster** (`ticketmaster.py`): `countryCode=AE`, weekly, low trust (UAE coverage is thin).
- **Curated venues:** `seeds/venue_events_2026q4_2027.yaml`, holding every listed event with `source_url`, `last_verified_at` and the flags:
  - DSF ~13 Dec–19 Jan: UNVERIFIED.
  - Ramadan, the Eids and similar: `moon_dependent`, ±1 day.
  - ATM: two `event_changes` rows.
  - A quarterly `dr.curated_verify_reminder` job.
- **AI discovery** (`ai_web.py`): weekly Responses API call with `web_search`. Output schema: `[{title, venue, start, end, url, confidence}]`. At least one citation is required. Results are always UNVERIFIED with confidence capped at 0.6. They can drive a recommendation only when corroborated or confirmed by the owner.
- **Manual entry and ICS import** go through `ingest`, kind ICS.
- **Comp-rate adapters** implement `RateFeedAdapter.fetch(hotel, comp_ids, dates, los=1, adults=2, currency="AED") -> list[CompRate]`:
  - `SerpApiGoogleHotels` (default, uses the existing `SERPAPI_KEY`). One area query per date (`engine=google_hotels, q="hotels in Deira Dubai", check_in_date, check_out_date, currency=AED, gl=ae`) returns many comps at once; per-`property_token` calls are only a fallback. Cadence: daily for days 0–30, every 3 days for days 31–120, about 60 calls a day.
  - `CsvLighthouse` and `CsvRateGain` via `ingest`.
  - `Xotelo`: demo/prototype only; USD converted to AED and flagged.
  - `MakCorps` and `LighthouseApi`: stubs.
  - All UI labels say "Indicative". There is no Booking Demand API adapter, because its terms forbid price comparison.

**Dedup.** `canonical_key = norm(title without year, edition or "the") + venue + ISO week`. Two events merge when:
- they share a venue (or are within 1 km),
- their dates overlap by at least 1 day,
- and titles match (token-set ratio ≥ 0.8, or an acronym match such as GITEX).

Field precedence for dates is CURATED > PREDICTHQ > MANUAL > TICKETMASTER > AI_WEB. Attendance takes the maximum credible value, with provenance. A date change writes `event_changes`, sets RESCHEDULED, keeps the `original_*` dates, and emits `dr.event.changed` for any recommendation or action that referenced the event.

### 4.3 Algorithms (`relevance.py`, `demand_index.py`, `recommend.py`, `measure.py`)

**Stay nights affected by an event:**
- Conferences and expos on days [s, e]: nights s−1 to e−2 at weight 1.0, and night e−1 at 0.6.
- Sports and concerts: the event night.
- Festivals: every night, weighted Fri/Sat 1.0 and Sun–Thu 0.5.
- Midweek holidays on a Tue or Thu add the bridge day.
- Moon-dependent events widen the window by `uncertainty_days`.

**Relevance of event e to hotel h:**

```
score = w_class × decay(d) × corridor × scale × dow_fit × segment_fit
decay = exp(−d/λ), λ = 8 km (<10k attendance), 15 km (10k–100k), 25 km (>100k / citywide); travel-time bands override when set
corridor = 1.5 if hotel.corridor_tags ∩ venue.corridor_tags ≠ ∅ (dual-venue: max over venues) else 1.0
scale = min(1, log10(attendance)/5)  (fallback phq_rank/100)
dow_fit: MICE ×1.0 Mon–Thu nights, ×0.6 Fri/Sat; leisure/sports ×1.0 Fri/Sat, ×0.5 weekday; GCC holidays ×1.0 Thu/Fri
segment_fit = hotel share of matching segment (from reservations) / market default share, clipped [0.5, 1.5]
w_class = (useful + 2)/(useful + dismissed + 3)   -- learned from owner dismissals and realised lift; muted → 0
source-market holiday contribution = Σ market_share × kind_weight (school 1.0, religious 0.7, public 0.5)
```

**Calibration from the hotel's own pickup:**
- For each past event, `lift = occ_event_nights / baseline − 1`.
- The baseline is the same-DOW, same-season mean from 2024–2025, excluding `baseline_exclusions` (March to July 2026).
- Per class, `k = Σlift/Σscore`, and `expected_rn_uplift = k × score × capacity`.
- Hotels with fewer than 5 past events of a class use global priors, with confidence capped at 0.6.

**Demand index for stay date d:**

```
DI = 100 × logistic(0.35·season_z + 0.30·event_score + 0.20·pace_z + 0.15·comp_z)
season_z from baseline occupancy; pace_z = (OTB_d(L) − OTB_base(L))/σ at same lead time L;
comp_z = (median comp price_d / same-DOW median d±14 excl. event dates − 1)/σ, plus 0.1 per 10% comps sold out
```

**Rate recommendation** (never pushed to a channel manager):

```
Δraw = 0.10·(DI−50)/50 + 0.40·pace_gap + 0.5·comp_move     (comp_move capped ±20%)
Δ = clip(Δraw, −10%, +30%), per-decision step ≤ max_step_pct
rec = clamp(round_to_5_AED(BAR × (1+Δ)), floor, ceiling); emit only if |rec − BAR| ≥ max(AED 15, 3%)
confidence = 0.4·event_conf + 0.3·data_coverage + 0.3·calibration_fit
impact = min(capacity, OTB + expected_pickup) × (rec − BAR) × (1 − expected_commission_share)   -- "estimated opportunity"
```

**Levers:**
- If DI ≥ 80, lead time ≤ 21 days and OTB ≥ 70%: close discounted plans (Early Booker, Mobile, deep Genius tiers), set min-stay 2 when the event spans 2 or more nights, reduce OTA allotment by `ceil(0.1 × capacity)`, and push a direct offer.
- If DI ≤ 35: pre-arrival offer and open closed plans. Never go below the floor.
- A recommendation is SUPERSEDED when DI moves more than 15 points or its event changes, and EXPIRED at the stay date.

**"Why this matters"** is an LLM-written sentence built from structured facts only: events, distance, comp move, pace gap. Causal claims are forbidden by the prompt and by a lint (banned phrases: "will increase", "guaranteed").

**Measurement** (`dr.measure_outcomes`, stay date + 1):
- Observed: pickup after the decision vs baseline pickup at the same lead time on comparable nights; ADR, occupancy, RevPAR, and net revenue from `channel_economics`.
- Actionability is reported first: % decided within 48h, % applied, and time-to-decision.
- Outcomes are labelled "observed, not causal".
- An `APPLIED` state is verified when a later `bar_calendar` import or rate evidence shows the new BAR within ±2%.

**Top 5 weekly:** rank by impact × confidence, with at most 2 items per event.

**Graph actions from one event:**
- Rate recommendation.
- Pre-arrival offer draft.
- **Staffing guidance:** `rooms_to_clean = departures + stayovers × stayover_ratio(0.8)`; `attendants = ceil(rooms_to_clean / credits(14))`; compared with the roster, a gap emits `dr.staffing.gap`.
- **KB update suggestion:** metro route or traffic advice appended to `hotels.knowledge_base` after approval.
- **Campaign drafts:** WhatsApp, Instagram and email copy, generated with a structured LLM call. The audience is `guest_contacts` with marketing consent and a country in the event's source markets. `suggested_send_at` is clamped to 09:00–18:00 Asia/Dubai. Output is export-only (CSV plus copy).

**Google Hotel Center checklist:** the Google-sourced price feed sunsets on 30 September 2026, 3 days from today. The checklist items are: ARI feed partner, Free Booking Links, Hotel Center account, landing page, price accuracy. It emits `dr.ghc.deadline`.

### 4.4 API, jobs, signals, metrics, client

- **API** (under `/v1/hotels/{slug}/demand-radar`):
  - Calendar and events: `GET /calendar?days=30|60|90`; `GET /days/{date}`; `GET /events?from&to&category`; `POST /events` (manual); `POST /events/import-ics`; `POST /events/{id}/dismiss {reason, scope: event|class|venue}`.
  - Recommendations: `GET /recommendations?status`; `POST /recommendations/{id}/decide {verb, recommended_fils?}`; `GET /top5`; `GET /outcomes`.
  - Settings and outputs: CRUD `/comp-set`, `/guardrails`, `/source-markets`; `POST /bar/import`; `GET|POST /campaigns`; `GET /campaigns/{id}/export.csv`; `GET|PUT /ghc-checklist`.
- **Jobs:**
  - `dr.phq_events_sync` (daily 01:00 UTC, cursor); `dr.phq_features_sync` (daily); `dr.ticketmaster_sync` and `dr.ai_discovery` (weekly).
  - `dr.comp_rates_fetch` (04:00 local); `stays.otb_snapshot` (23:55 local).
  - `dr.demand_index`, then `dr.recommend` (after the snapshot).
  - `dr.weekly_top5` (Mon 06:00 local); `dr.measure_outcomes` (daily); `dr.curated_verify_reminder` (quarterly).
- **Signals:**

  | Kind | Section |
  |---|---|
  | `dr.opportunity` | MONEY_AVAILABLE |
  | `dr.pace.behind` | MONEY_AVAILABLE |
  | `dr.event.changed` | SERVICE_RISK, or MONEY if an approved recommendation depends on the event |
  | `dr.staffing.gap` | SERVICE_RISK |
  | `dr.ghc.deadline` | MONEY_AVAILABLE |
  | `dr.rec.applied_verified` | VERIFIED |

- **Metrics:** `dr.occupancy_pct`, `dr.adr_aed`, `dr.revpar_aed`, `dr.rooms_otb`, `dr.pickup_rooms`, `dr.demand_index`, `dr.comp_median_rate_aed`, `dr.rec_decision_rate`. Dimensions: stay_date, dow, month, room_type, channel, segment, event.
- **Client** (`client/app/app/[hotelSlug]/demand-radar/`):
  - `page.tsx`: a `HeatmapCalendar` with a 30/60/90 toggle, cells coloured by DI, and event badges.
  - `DayDrawer`: event list with source, confidence, "last checked" and change history; a `CompRateStrip` (min/median/max band plus own-BAR line, labelled "Indicative"); a pace-vs-baseline `Sparkline`; and a `RecommendationCard` with an `AdjustSlider` bounded to [floor, ceiling], lever checkboxes, "Why this matters", and Approve / Adjust / Reject.
  - `events/` (filters, dismiss-class menu, manual add, ICS import), `top5/`, `outcomes/`, `campaigns/` (copy and CSV export), and `settings/` (comp set, guardrails, bands, GHC checklist).
- **Tests:**
  - GITEX arriving from PredictHQ, curated and AI sources merges into one event with 3 sources.
  - ATM rescheduled twice produces 2 change rows and keeps the original dates.
  - Distance decay; MICE night mapping; Hijri widening.
  - Guardrail clamp and `round5`; step cap; supersede and expire.
  - 2026 exclusion from the baseline.
  - Parsing of SerpApi, PredictHQ and Features fixture JSON.
  - The send-window clamp.
- **Edge cases:**
  - A cancelled event after an approved recommendation (signal to revert).
  - Sold-out comps are a strong signal, not a zero price.
  - An ADNEC event's spillover to Deira decays; F1 is citywide.
  - Ramadan modifier (lower Western leisure, higher at Eid for GCC).
  - A hotel with no history uses priors.

---

## 5. Amenity leakage: `server/clefs/modules/amenities/`

### 5.1 Tables

```sql
amenity_items(id, hotel_slug, sku, name, category BATH|DENTAL|SHAVING|SLIPPERS|WATER|TEA_COFFEE|MINIBAR|OTHER, uom,
  basis PER_OCCUPIED_ROOM_NIGHT|PER_ARRIVAL|PER_GUEST_NIGHT|ON_REQUEST, std_qty numeric(6,3), unit_cost_fils,
  is_single_use_plastic bool, excise_category NONE|SWEETENED|ENERGY, aliases text[], active)
amenity_cost_history(item_id, effective_from, unit_cost_fils /* incl. excise */, source SUPPLIER_INVOICE|MANUAL, invoice_ref)
stock_locations(id, hotel_slug, kind STORE|FLOOR_CLOSET|CART|DEPARTMENT|MINIBAR, floor, name)
par_levels(location_id, item_id, par_qty, days_of_supply, safety_stock, computed jsonb, updated_at)
issuance_log(id, hotel_slug, issued_at, business_date, shift MORNING|EVENING|NIGHT, item_id, qty /* negative = return */,
  from_location_id, to_location_id, to_floor, staff_member_id, staff_name, reason REPLENISH|GUEST_REQUEST|VIP|DAMAGE|RETURN,
  request_id /* staff_requests.id */, source MANUAL|CSV|API|DEMO, import_batch_id)
request_amenity_lines(request_id, hotel_slug, item_id, qty, match_method TOOL|ALIAS|LLM|MANUAL, confidence)
stock_counts(location_id, item_id, counted_at, qty, counted_by)
amenity_variance_daily(hotel_slug, business_date, item_id, floor, shift, basis_count, expected_std, requested,
  expected_total, issued, variance_units, variance_fils, PK(...))
benchmark_cohorts(cohort_key /* AE-DXB-HOTEL_3 */, item_category, month, hotels_count, p25, p50, p75 /* per 100 ORN */)
```

### 5.2 Algorithms

- **Expected usage** for item i, floor f, day d: `E = basis_count(d,f) × std_i + requested(d,f,i)`. `basis_count` comes from `occupancy_by_floor_daily` (occupied rooms, arrivals or guests). Requests are `request_amenity_lines` joined to `staff_requests.room`, which gives the floor via `rooms`.
- **Variance:** `V = issued − E` in units; `V_fils = V × unit_cost(d)`. Floor closets hold 1–2 days of stock, so alerts use a **rolling 7-day window**. With stock counts, consumption is exact: `opening + issued − closing`.
- **Spike rule:** alert when all three hold: `V7/E7 > 15%`, `V7_fils > AED 100`, and `z = (V7 − μ_8w)/σ_8w > 2`. There is also an occupancy-unexplained check: the residual from the regression `issued ~ α + β·occupied` exceeds 2σ.
- **On-request rate per 100 occupied room-nights (ORN):** `r = (req + prior × n0/100)/(ORN + n0) × 100`, with `n0 = 200` and priors per category (for example dental 6, shaving 3, slippers 10). A **logging-gap** alert fires when `issued_on_request > 1.5 × logged_requests` over 14 days.
- **Par** = `daily_usage × days_of_supply + safety_stock`, where:
  - `daily_usage = forecast_occ × std + r × forecast_occ/100`, with forecast occupancy from OTB plus pickup;
  - `safety = 1.65 × σ_daily × √lead_days`.
- **CPOR (cost per occupied room)** = Σ net issued fils / ORN, shown against expected CPOR.
- **Minibar:** unbilled = refills issued − `folio_charges.MINIBAR` quantity.
- **By staff and shift:** "issued per room serviced". The comparison is shown only with 14 or more days of data and 3 or more staff, uses neutral wording, and never assigns blame automatically. A counter-rotation note appears when one issuer accounts for more than 60% of the variance.
- **Benchmarks:** published only when `hotels_count ≥ 3` and no single hotel supplies more than 50% of the volume.
- **Request linking:** extend the `notify_staff` tool in `client/app/ClefsConcierge.tsx` (~line 1337) with optional `amenity_item` and `quantity`, and pass them in the `/pending` payload. In `server/main.py` (`set_pending` and `create_request`), call `amenities.link_request(row, data)`. Resolution order: tool value → alias match on type/subtype → a nightly LLM batch for unmatched Housekeeping requests.

### 5.3 API, jobs, signals, metrics, client, tests

- **API** (under `/v1/hotels/{slug}/amenities`): CRUD `/items`, `/locations`, `/par`; `POST /issuance` (single or bulk); `POST /issuance/import`; `GET /report?from&to&group=item|floor|shift|staff`; `GET /items/{id}/trend`; `GET /cpor`; `GET /benchmarks`; `POST /requests/{id}/items`; `POST /stock-counts`.
- **Jobs:** `am.variance_rollup` (03:30 local), `am.learn_rates` (weekly), `am.par_recompute` (weekly), `am.link_requests_llm` (nightly), `am.benchmark_rollup` (monthly, system scope).
- **Signals:**

  | Kind | Section |
  |---|---|
  | `am.variance.spike` | MONEY_LOST (impact = 7-day AED; monthly projection in the text only) |
  | `am.request_logging_gap` | SERVICE_RISK |
  | `am.stockout_risk` | SERVICE_RISK |
  | `am.sup_compliance` | COMPLIANCE |

- **Metrics:** `am.issued_units`, `am.expected_units`, `am.variance_units`, `am.variance_aed`, `am.cpor_aed`, `am.requests_per_100_orn`, `am.issued_per_100_orn`. Dimensions: item, category, floor, shift, staff, date.
- **Client** (`client/app/app/[hotelSlug]/amenities/`):
  - Overview: 7- and 30-day variance in AED, a CPOR tile, top leaking items (HBar), and a grouped column chart of requested vs issued vs expected.
  - `items/[id]`: trend `LineChart`, HBar by floor, by shift.
  - `issue/`: a mobile-first `IssuanceQuickEntry` (item grid with ± steppers, floor, shift, staff), plus CSV import.
  - `catalog/` and `par/`.
  - `benchmarks/`: empty state "Needs ≥3 hotels in cohort".
- **Tests:**
  - A closet buffer (two days issued on Monday) does not spike over 7 days.
  - Returns count as negative.
  - Alias linking ("Toothbrush" → DENTAL_KIT).
  - Shrinkage when ORN is small.
  - Benchmarks suppressed with 2 hotels.
  - Excise included in cost.
  - A missing occupancy day is excluded, not zero-filled.
- **Edge cases:** room moves, long stays (PER_ARRIVAL basis), VIP issuance excluded via reason, damage write-offs.

---

## 6. Owner Brief and the action loop: `server/clefs/modules/owner_brief/` (+ `whatsapp/` subpackage)

### 6.1 Ranking and assembly (`brief.py`)

Candidates are OPEN signals that are not snoozed and not COMPLIANCE. COMPLIANCE items go to a weekly digest instead.

```
score = I × C × U
I = min(1, ln(1+AED)/ln(1+R_ref)), R_ref = 2 × trailing-30d avg daily room revenue (fallback AED 20,000)
    zero-AED service items: I = severity weight (critical .9, high .7, medium .4); ≤2★ review within 48h: .5
C = signal confidence
U = 1.0 (≤24h), .85 (≤72h), .6 (≤7d), .4 (≤30d), .25 (no deadline)
```

- Pick the top 5, with at most 3 per section and at most 2 per source detector. Ties break on the earliest deadline.
- The "Verified results" line (Σ verified value, 24h and month-to-date) is always appended and does not count toward the 5.
- Each item shows AED impact, confidence, evidence link, recommended action, responsible person (from the action type's default role, mapped to a named user or staff member), and deadline.
- **Service-risk producer** (`producers/service_risk.py`) reads the existing `staff_requests`. It emits `ops.sla_breach` when a request is new for more than 10 minutes, or in progress longer than the category SLA (Housekeeping 30, Maintenance 60, F&B 45 minutes).
- **Reviews producer:** a stub `rv.review.negative` adapter reuses the SerpAPI fetch in `review-intel/hotel_review_intel.py`.

```sql
brief_runs(id, hotel_slug, brief_date, kind DAILY|WEEKLY, scheduled_local, generated_at, status, delivery jsonb)
brief_items(id, brief_id, rank, section, signal_id, action_id, title, impact_fils, confidence, urgency, deadline_at,
  responsible_user_id|staff_member_id, evidence_url, item_no)
brief_links(token_hash, hotel_slug, user_id, brief_id, scope, expires_at /* 72h */, used_count, last_used_at)
action_transitions(action_id, at, from_state, to_state, verb, actor_user_id|contact_id, channel WEB|WHATSAPP|EMAIL|SYSTEM,
  params_diff jsonb, nonce, note)
```

### 6.2 WhatsApp (`whatsapp/`)

```sql
wa_accounts(id, hotel_slug|NULL, waba_id, phone_number_id, display_phone, token_secret_ref, app_secret_ref, graph_version, quality, tier)
wa_templates(id, account_id, name, language, category UTILITY|MARKETING, status, components jsonb, last_synced_at)
wa_contacts(id, hotel_slug, phone_e164, name, role, user_id, opted_in_at, opt_in_method, categories text[],
  opted_out_at, csw_expires_at, UNIQUE(hotel_slug, phone_e164))
wa_messages(id, account_id, hotel_slug, wamid unique, direction IN|OUT, contact_id, type, template_name, status,
  error_code, context_wamid, payload jsonb, brief_id, action_id, billable bool, pricing_category, created_at, status_at)
wa_outbox(id, account_id, contact_id, kind TEMPLATE|INTERACTIVE|TEXT|DOCUMENT, body jsonb, not_before, attempts,
  last_error, status QUEUED|SENT|FAILED|DEAD, dedupe_key unique)
wa_webhook_events(event_key pk /* messages[].id or statusid:status */, received_at, raw jsonb)
wa_sessions(contact_id pk, state /* AWAITING_ADJUST{action_id,min,max} | AWAITING_ASSIGN{…} */, expires_at)
wa_media(media_id, file_id, uploaded_at, expires_at /* +30d */)
```

- **Daily template `owner_brief_v1` (UTILITY):**
  - Header: "Clefs brief · {{1}}" (60 characters or fewer).
  - Body: "{{1}} decisions pending today, AED {{2}} at stake." followed by lines {{3}}…{{7}}, formatted as `"1) AED 1,020 · Booking no-show commission · 90% · by 14 Oct · Finance"`.
  - A budget allocator truncates titles so the body stays at 1,024 characters or fewer.
  - A lint strips promotional words from variables ("offer", "deal", "sale", "% off", "promo"), so "close discount plans" becomes "restrict rate plans". This keeps the template from being recategorized as marketing.
  - Buttons: quick reply "Approve #1" with payload `brief:{date}:1:approve:{nonce}`; quick reply "Review items" with `brief:{date}:list:{nonce}`; URL "Open brief" with a dynamic suffix token.
- **The tap opens the 24-hour customer service window**, then:
  - an interactive **list** of up to 5 rows (title ≤24, description ≤72, id `act:{action_id}:open:{nonce}`);
  - then **reply buttons** Approve / Assign / More (id `act:{id}:{verb}:{nonce}`);
  - "More" opens a list: Adjust, Dispute, Remind, Snooze 1d, Snooze 7d, Ignore (with a reason sub-list).
  - Adjust for a rate recommendation sets `wa_sessions` to "Reply with a rate between AED 380 and 520".
  - Dispute builds the packet and sends a DOCUMENT message with the PDF.
- **Nonce:** `base32(HMAC(secret, action_id|verb|brief_id|contact_id|exp))[:12]`. It is bound to the recipient and single-use for state-changing verbs (checked against `action_transitions.nonce`).
- **Webhook** (`GET|POST /webhooks/whatsapp`):
  - GET verifies `hub.verify_token` and echoes `hub.challenge`.
  - POST checks `X-Hub-Signature-256` against the HMAC-SHA256 of the raw body with the app secret, in constant time.
  - Dedupe: `INSERT … ON CONFLICT DO NOTHING` on `messages[].id`.
  - Every inbound message sets `csw_expires_at = ts + 24h`.
  - Routing: `button.payload` (checked against `context.id` = a stored outbound wamid), `interactive.button_reply.id` or `list_reply.id`, or text (session continuation, else Ask Clefs).
  - Statuses update `wa_messages`. The handler returns 200 quickly: it writes to the DB, enqueues to the outbox, and drains inline on a best-effort basis.
- **Outbox drain** (every minute, plus inline):
  - At most 1 message per recipient per 6 s.
  - Error 131056: `not_before = now + 6s × 2^n`.
  - Error 130429: account-wide backoff.
  - Error 131047 (outside the window): resend as a template.
  - Dead after 7 attempts.
- **Billing:** service messages are billable from 2026-10-01. Track `billable` and the `wa.service_msgs_month` metric, and keep each interaction to 3 messages or fewer.
- **Fallback** (`brief.delivery_fallback`, every 10 minutes): no DELIVERED status within 30 minutes, or a send failure, triggers the HTML email brief with magic links. SMS is used only for items with U = 1 and impact above threshold, and contains no URL (UAE rule), for example "Clefs: 2 urgent items, AED 4,120 at stake. Open Clefs." It uses a registered sender ID.
- **Template sync job:** daily. It alerts when a template is PAUSED, REJECTED or recategorized.

### 6.3 Action state machine (`actions_fsm.py`)

```
PROPOSED --approve--> APPROVED --assign--> ASSIGNED --start--> IN_PROGRESS --complete--> DONE --verify--> VERIFIED | NOT_VERIFIED
PROPOSED --adjust--> APPROVED (params_diff recorded; type-specific bounds enforced, e.g. rate ∈ [floor, ceiling])
PROPOSED --dispute--> APPROVED(subtype=dispute) → SUBMITTED → AWAITING_COUNTERPARTY → VERIFIED | REJECTED_BY_COUNTERPARTY → UNRESOLVED
PROPOSED|APPROVED|ASSIGNED --snooze(until)--> SNOOZED --timer--> previous state
PROPOSED --ignore(reason)--> REJECTED        any open --supersede/expire--> CANCELLED
remind: no state change; notifies assignee; logged
overdue = derived flag (deadline_at < now ∧ state ∈ {APPROVED, ASSIGNED, IN_PROGRESS}); escalation_level 0..3
```

- **Roles:** approve, adjust and dispute require owner, gm or finance (disputes) or revenue (rates). Assign requires gm or dept_head. Staff can only move their own tasks to IN_PROGRESS or DONE.
- **Escalation** (`brief.escalate`, every 15 minutes):
  - Level 1 at the deadline: remind the assignee (WhatsApp, else SMS via `staff_members.phone_number`).
  - Level 2 at +4h: notify the GM.
  - Level 3 at +24h: pin the item in the next brief as "Overdue" and notify the owner.

**Verifiers** (registered per action type):

| Action type | VERIFIED when | Verified value |
|---|---|---|
| `pg.dispute` | credit note or later statement line matches the finding | credited fils |
| `pg.mark_no_show` / `pg.report_change` | statement line shows NO_SHOW or reduced nights with commission 0 or reduced | commission avoided |
| `pg.vcc_charge` | VCC report shows FULLY_CHARGED, or a payout or bank credit is matched | amount charged |
| `pg.config_fix` | the next month's lines exclude MF/TD from the base for at least 90% of lines | measured monthly saving |
| `pg.payout_query` | a bank credit or adjustment covers the shortfall | amount |
| `dr.rate_change` / `dr.close_plans` | applied: BAR import or rate evidence shows the new rate ±2% | outcome recorded as observed, not causal, and excluded from "verified money" |
| `ops.staff_task` (restock, SLA follow-up) | the linked `staff_requests` row is completed (`resolved_at` set) | none (service) |
| `am.investigate_variance` | the next 14-day variance for the item and floor is below threshold | reduction in variance fils |
| `tax.td_remit` | `td_ledger` remitted = collected ± AED 1, with a reference | none (compliance) |
| `dr.campaign_export` | exported | none |

### 6.4 Weekly report, API, jobs, client, tests

- **Weekly report** (Mon 08:00 local): verified value by type; identified vs recovered; decisions made with median time-to-decision; overdue items; top open items. It deliberately contains no vanity metrics. Delivery is HTML email plus a WhatsApp `weekly_report_v1` UTILITY template with a DOCUMENT header (a fpdf2 PDF uploaded to media; the id is cached for 30 days).
- **API:**
  - `GET /v1/hotels/{slug}/brief/today|{date}`; `POST /brief/preview`; `POST /brief/send-now`; `GET|PUT /brief/settings`.
  - `GET /actions?state&type&overdue`; `POST /actions/{id}/decide {verb, params, reason}`; `GET /actions/{id}/timeline`; `GET /reports/weekly/{iso_week}`.
  - Contacts: `GET|POST /wa/contacts` (opt-in capture).
  - Global: `GET|POST /webhooks/whatsapp`.
  - Public (token): `GET /public/brief/{token}` and `POST /public/brief/{token}/actions/{id}/decide`.
- **Jobs:**
  - `brief.schedule_tick`: every 5 minutes. Generates and sends when local time has passed the hotel's scheduled time (default 07:00 Asia/Dubai, i.e. `0 3 * * *` UTC) and no run exists for today.
  - `brief.escalate`, `wa.outbox_drain`, `brief.delivery_fallback`, `brief.weekly_report`, `wa.template_sync`, `wa.media_refresh`.
- **Signals and metrics:**
  - Emits `brief.action.overdue` (SERVICE_RISK) and `brief.delivery.failed`.
  - Metrics: `brief.time_to_decision_h`, `brief.approval_rate`, `brief.overdue_count`, `actions.verified_value_aed`.
- **Client:**
  - `client/app/app/[hotelSlug]/brief/`: today's `BriefCard`s (impact, `ConfidenceDots`, `CountdownChip`, evidence link, owner) with an `ActionBar` (Approve / Adjust / Assign / Dispute / Remind / Snooze), plus an `AdjustSheet`, an `AssignSheet` (staff picker) and history.
  - `actions/`: list by state, overdue filter, `StateTimeline`.
  - `reports/weekly/`.
  - `settings/brief/`: time, recipients, opt-in.
  - Magic-link mobile page `client/app/brief/[token]/page.tsx`: noindex, single column, one-tap buttons. The token is exchanged for a short-lived, hotel-scoped cookie.
- **Tests:**
  - Deterministic ranking; section caps.
  - Worst-case body length ≤ 1,024; promo-word lint.
  - Payload parse and nonce (valid, replayed, other recipient).
  - Signature valid and invalid; duplicate `messages[].id`; `context.id` match.
  - Window logic (interactive inside, template outside).
  - 131056 backoff; email fallback.
  - Escalation timings in Asia/Dubai.
  - Illegal transitions rejected; magic-link expiry and hotel scope.

---

## 7. Ask Clefs: `server/clefs/modules/ask/`

**MetricDef** (registered by each module):

```python
MetricDef(key="pg.commission_paid_aed", label="OTA commission paid", unit="AED", money=True, min_role="finance",
  agg="SUM", time_field="l.check_out",
  sql="SELECT {dims} SUM(l.commission_fils) AS value FROM ota_statement_lines l "
      "WHERE l.hotel_slug=%(hotel)s AND l.check_out BETWEEN %(from)s AND %(to)s {filters} {group_by}",
  dimensions={"channel": "l.ota_code", "month": "date_trunc('month', l.check_out)::date"},
  filters={"channel": Filter("l.ota_code", "enum", values_sql="SELECT DISTINCT ota_code …")},
  synonyms=["commission", "OTA fees"])
# ratio metrics: RatioDef(num="pg.net_revenue_aed", den="stays.room_nights") computed server-side
```

- **Tools** (Gemini API function calling through google-genai, strict schemas; enums injected per hotel and role):
  - `query_metric(metric: enum, dimensions: enum[] ≤2, filters: [{field: enum, op: eq|in|gte|lte, value}], period: {preset: today|yesterday|last_7d|mtd|last_month|qtd|ytd|next_30d|next_90d} | {from, to ≤ 3y span}, compare_to: none|previous_period|same_period_last_year, sort, limit ≤ 50)`;
  - `list_metrics()`, `describe_metric(key)`, `answer_unsupported(reason, suggestions[])`.
  - At most 3 tool calls per question.
- **SQL safety:** the server builds SQL from whitelisted expressions only. Values are always bound parameters and enum values are checked against the distinct-values cache. User text never reaches SQL.
- **Grounding check:** every number in the answer must appear in the tool results (after formatting, with rounding tolerance). Otherwise the answer is regenerated once, then falls back to a table-only reply.
- **Output:** `{answer_text, table{columns, rows}, chart_spec{type: kpi|column|hbar|line, x, series[], unit}, trace{metric, dims, filters, period}}`.

```sql
ask_saved_views(id, hotel_slug, user_id, title, query jsonb /* tool args */, pinned, created_at)  -- re-run without LLM
ask_logs(id, hotel_slug, user_id, channel WEB|WHATSAPP, question, tool_calls jsonb, answer, unsupported bool,
  latency_ms, tokens, feedback smallint)
```

- **Guardrails:** no guest-level PII through Ask; metrics are filtered by role; 30 questions per hour per user.
- **WhatsApp:** free text from opted-in owner, gm or finance contacts, only inside the 24-hour window. The reply is text (up to 10 rows as aligned lines) plus a magic link to the console view.
- **API:** `POST /v1/hotels/{slug}/ask {question}`; `GET /ask/metrics`; `GET|POST /ask/views`; `POST /ask/views/{id}/run`; `POST /ask/{log_id}/feedback`.
- **Client** (`client/app/app/[hotelSlug]/ask/`): `AskComposer`; `AnswerCard` (text, table, a `ChartSpecRenderer` that maps to ColumnChart, HBarChart or LineChart, and a "How this was computed" disclosure showing the trace); a saved-views sidebar.
- **Eval set** (`ask/tests/evals/ask_clefs.yaml`), about 60 items run against the demo dataset. Examples:
  - "How much commission did Booking charge on taxes last month?" → `pg.commission_on_tax_aed{channel=BOOKING, last_month}`.
  - "Net ADR by channel in August" → `pg.net_adr_aed[channel]`.
  - "Which amenity leaked most this week?"
  - "Occupancy GITEX week vs last year."
  - "VCCs expiring in 14 days."
  - Must refuse: "Guest John's phone", "What will competitors charge next year?"
  - Targets: tool-argument exact match ≥ 90%, numeric grounding 100%, unsupported questions detected ≥ 95%.

---

## 8. Operational graph (table ownership)

| Domain | Tables | Owner |
|---|---|---|
| Property | `hotels`, `hotel_locations` (outlets), `guest_rooms` (existing); hotel settings | existing / foundation |
| Rooms and floors | `room_types`, `rooms` (floor), `occupancy_daily`, `occupancy_by_floor_daily` | stays |
| Guests and bookings | `reservations`, `reservation_nights`, `folio_charges`, `otb_snapshots`, `guest_contacts`, `channels`, `bar_calendar`* | stays (*demand_radar) |
| Staff | `staff_members`, `staff_shifts` (existing); SLA policies; users and roles | existing / foundation |
| Work | `staff_requests` (existing), `request_amenity_lines`, `issuance_log`, `stock_locations`, `par_levels`, `stock_counts`, `amenity_items` | existing / amenities |
| Money | `ota_contracts`, `ota_programs`, `channel_config`, `ota_statements(+lines)`, `reservation_matches`, `ota_payouts(+lines)`, `bank_credits`, `virtual_cards`, `rate_evidence`, `pg_findings(+events)`, `dispute_packets`, `channel_economics`, `lead_audits` | profit_guard |
| Taxes | `fee_rules`, `hotel_tax_overrides`, `td_ledger`, `fx_rates` | uae_tax |
| Market | `venues`, `events(+sources, changes)`, `holidays`, `source_markets`, `comp_set`, `comp_rates`, `demand_days`, `rate_guardrails`, `rate_recommendations`, `dr_guidance`, `campaign_drafts` | demand_radar |
| Decisions | `signals`, `actions` (foundation), `action_transitions`, `brief_runs/items/links`, `wa_*` | foundation / owner_brief |
| Knowledge | metric registry (foundation), `ask_saved_views`, `ask_logs` | ask |
| Ingestion | `import_batches`, `import_rows`, `mapping_templates` | ingest |

**Key edges:**
- reservation ↔ statement line (`reservation_matches`), payout line, VCC, and folio charges.
- request → amenity lines → item; issuance → location and floor → rooms.
- event → venue → corridor → `hotel_event_relevance` → demand day → recommendation.
- Every finding, recommendation or variance → signal → action → verification evidence (a statement line, payout, request or BAR row).

---

## 9. Synthetic UAE demo dataset

**Script:** `server/clefs/demo/seed_uae_demo.py --hotel demo-deira-crest --seed 20261001 --anchor 2026-09-27`.
- Uses Python `random.Random(seed)` only, with no wall-clock reads, so output is deterministic.
- Idempotent: it deletes only rows where `hotel_slug` is a demo slug.
- Sets `hotels.is_demo=true`. Every name carries "(DEMO)" and the UI shows a "Synthetic demo data" banner.
- Also writes `server/clefs/demo/expected_plants.json`, the manifest the detector tests assert against.

**Property:** "Al Rigga Crest Hotel (DEMO)", Deira, 3-star, 80 rooms.
- Lat/lon 25.2650, 55.3230; corridor tags `DEIRA_BURDUBAI`, `RED_LINE`; about 9 km from DWTC and about 7 km from Expo Centre Sharjah.
- 8 floors × 10 rooms: 40 STD_KING, 30 STD_TWIN, 10 FAM_2BR (bedrooms=2).
- Tax profile: Dubai, HOTEL_3, convention A, TRN demo.
- Contracts: Booking 15% (Preferred +3% active 1 Jun–31 Jul), Agoda 18%, Expedia 18% (Hotel Collect and Expedia Collect).
- Staff: 16 demo staff (HK, F&B, maintenance, front desk) with Dubai-typical names.

**Comp set:** Creekview Inn (3★, 2 km), Gold Souk Boutique (3★, 1.5 km), Port Saeed Suites (hotel apartments, 3 km), Trade Axis Hotel (4★, near DWTC), Barsha Metro Hotel (3★, 18 km), all "(DEMO)".

**Windows:**
- **History:** stays from 2026-06-01 to 2026-09-28 (120 days). About 2,600 reservations and about 6,200 room nights.
  - Channel mix: Booking 45%, Agoda 15%, Expedia 12%, Direct 20%, Corporate/Walk-in 8%.
  - ADR AED 240–330 in summer; occupancy 55–66%.
- **Forward OTB:** 2026-09-28 to 2027-01-31, booked before the anchor date, with pace building into CABSAT, GITEX (ADR about AED 650) and NYE.
- **Baseline:** `occupancy_daily` for 2024–2025 generated from `occ = 0.70 + 0.14·cos(2π(doy − 20)/365) + dow_effect + event_bumps`, with a Ramadan dip and NYE at 94%. The 2026 Mar–Jul distortion is reproduced and covered by `baseline_exclusions`.
- **Statements:** Booking monthly invoices and statements for Jun, Jul and Aug (CSV using header variants v1, v2, v3); an Agoda XLSX; an Expedia Hotel Collect PDF for August; Booking payouts JSON; a bank CSV; VCC reports; one credit note (verifies 2 August L3 findings); a PMS folio export.

**Planted cases** (each has a manifest entry: detector, ids, expected amount):

| Detector | Plant | Decoy (must not flag) |
|---|---|---|
| L1 / L1b | 25 August Booking lines with commission on full gross while MF/TD are configured as charges from 1 Aug; June and July unconfigured → L1b estimate | none |
| L2 | 6 August lines at 18% after Preferred ended 31 Jul | 5 July lines at 18% with Preferred active |
| L3 | 2 Booking and 1 Expedia HC no-shows charged; 1 Agoda; 1 waived cancellation charged | no-show with fee collected, commission on fee only |
| L4 | 3 early departures (5 nights booked, 3 stayed) unreported | 1 correctly reported |
| L5 | 3 bookings where the OTA price is AED 480 and PMS is AED 420 after modification | none |
| L6 | 2 bookings with Genius 15 + Mobile 10 + Early Booker 15 (depth 35.4%, cap 25%) | Genius 10 + Mobile 10 (19%) |
| L7 | 3 dates where Agoda is 6% below direct (logged-out desktop, placeholder screenshots) | a Genius logged-in price 10% lower |
| L8 / L9 | 7 Expedia EVCs expiring at +5/12/20/40 days; 3 Booking VCCs; 2 expired (from legacy checkouts in Mar 2026) | FULLY_CHARGED cards |
| L10 | 1 bank credit AED 1,240 short; 2 lines with wrong partner_payout | a bank fee within the expected amount |
| L11 | a USD payout converted at 3.61; a EUR payout with a 2.5% spread | USD at 3.6725 |
| L12 | 2 partial refunds of AED 600 without commission reversal | none |
| L13 | 3 chargebacks aged 20, 55 and 95 days | none |
| L14 | 2 Booking no-shows on the night of 26 Sep (deadline within 48h of the anchor); 1 Expedia HC modification due 4 Oct | none |
| L15 | monthly reverse-charge flags for Booking (NL), Agoda (SG), Expedia (non-AE) | none |
| T1–T7 | a group of 3 rooms × 4 nights with TD posted 4 times; a 45-night stay (25+20 back-to-back) with TD on all nights; 10 days at AED 15; one week of VAT on walk-in TD; 4 double posts; 3 no-shows with TD; August remitted AED 350 short | none |
| Amenities | dental kits on floor 6, night shift, at 3× expected for 10 days; slippers over-issued to one cart; shaving-kit logging gap (issued 2.2× logged); SUP water bottles flagged | a Monday two-day closet fill |
| Service | about 3,000 `staff_requests` with realistic timings; SLA breaches clustered on Friday evenings | none |
| Demand Radar | the seed calendar; PredictHQ, Features and SerpApi fixture payloads; comps +60% on GITEX nights, +15% for CABSAT, one comp sold out for GITEX; "Demo Summer Expo (DEMO)" at DWTC 18–20 Aug with visible pickup for calibration | none |

- **Expected totals:** roughly AED 9–12k RECOVERABLE across 120 days. Against a demo subscription of AED 1,500 a month, this clears the 3× pilot threshold.
- **Test target:** recall of 1.0 on plants, 0 findings on decoys, precision ≥ 0.9 overall.

---

## 10. Changes to existing code and open verification items

**Changes to existing code:**
- **`client/app/ClefsConcierge.tsx`:** add optional `amenity_item` and `quantity` to the `notify_staff` schema (~line 1337) and pass them in the `/pending` POST (~line 1584).
- **`server/main.py`:**
  - Call `amenities.link_request()` and `owner_brief.producers.service_risk.on_request_change()` from `create_request`, `set_pending` and `update_request_status`.
  - `get_staff_analytics` hard-codes `Asia/Kolkata`. It must use the hotel timezone for UAE hotels.
  - `send_sms_alert` forces a `91` prefix. UAE numbers need E.164 through the foundation notification service.
- **`client/app/app/[hotelSlug]/page.tsx`:** currently renders `StaffDashboard` directly. The foundation's console shell must turn it into the module hub.

**Verify before build** (these are held as date-effective, UNVERIFIED data, not code):
- the effective dates of the 2026 Dubai TD/fee exemption and the remittance deferral;
- DSF 2026–27 dates;
- the clause numbering for Booking GDT v2601;
- the Expedia and Agoda dispute windows for this specific contract (a contract PDF is required);
- the WhatsApp service-message billing and free tier from 2026-10-01;
- the OTA export header aliases, against real samples from pilot hotels.

### Critical Files for Implementation
- /Users/nikhilagarwal/MyCodes/Clefs/server/main.py
- /Users/nikhilagarwal/MyCodes/Clefs/client/app/ClefsConcierge.tsx
- /Users/nikhilagarwal/MyCodes/Clefs/client/app/app/charts.tsx
- /Users/nikhilagarwal/MyCodes/Clefs/review-intel/hotel_review_intel.py
- /Users/nikhilagarwal/MyCodes/Clefs/server/vercel.json
