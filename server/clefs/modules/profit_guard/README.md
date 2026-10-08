# Profit Guard (`profit_guard`)

OTA leakage for hotels (02 spec 3, Canon 2 and K2). Owned by lane **C Profit Guard**; only that lane edits this directory (plus `tests/modules/profit_guard/`).

- Migrations `0300-0399` (`migrations/NNNN_profit_guard_<desc>.sql`); tables start with `pfg_`.
- Console routes: `/hotels/{slug}/profit-guard/...` (`router_data.py`); the tax routes stay on `/hotels/{slug}/profit/tax/...` (`router.py`) because the settings Tax page calls them there. Both segments are registered for the module.
- Permissions: `profit.view` (reads) and `profit.manage` (writes), both owner, gm, finance.
- Keys use `pg.` / `tax.` (registry `KEY_PREFIXES`). The contract extraction AI task is `pg.contract_extract` (`AI_MODEL_PG_CONTRACT_EXTRACT`).
- `api.py` is the public surface; other modules import only `clefs.modules.profit_guard.api` (frozen signatures).

## Files (data part)

| File | What |
|---|---|
| `models.py` | Vocabulary (OTA codes and aliases, base rules, statuses), canonical claims (`CanonicalStatementLine`, `CanonicalPayoutLine`, `CanonicalVcc`, `CanonicalBankCredit`, `CanonicalCreditNoteLine`), `CanonicalContractTerms` (strict Structured Outputs schema with `ClauseCitation`s), `Finding` and `CalcStep`, HTTP input models |
| `repo.py` | Owner policies, date-effective channel config, programs, reads of statements / payouts / bank credits / cards, rate evidence, `upsert_finding`, stay distribution cost and `channel_economics` |
| `contracts.py` | Default terms, `contract_on`, the Canon K2 commission base, `expected_commission`, AI extraction into a cited DRAFT, confirmation, retirement, VCC charge deadlines |
| `imports.py` | core/ingest commit handlers for `OTA_STATEMENT`, `CREDIT_NOTE`, `OTA_PAYOUT`, `VCC_REPORT`, `BANK`, `CONTRACT_PDF`; matching through `core.stays.match_claim`; bank-to-payout linking |
| `router_data.py` | HTTP routes below |
| `module.py` | Manifest; importing it registers the commit handlers |

## Tables

`0300_profit_guard_data.sql`: `pfg_ota_contracts`, `pfg_ota_programs`, `pfg_channel_config` (PK hotel, ota, configured_from), `pfg_owner_policies`, `pfg_ota_statements` (UNIQUE hotel, ota, doc_no), `pfg_ota_statement_lines` (UNIQUE statement, line_key), `pfg_ota_payouts`, `pfg_ota_payout_lines`, `pfg_bank_credits`, `pfg_virtual_cards`, `pfg_rate_evidence`.

`0301_profit_guard_findings.sql`: `pfg_findings` (UNIQUE hotel, fingerprint), `pfg_finding_events` (the recovery ledger), `pfg_dispute_packets`, `pfg_detector_tuning`, `pfg_channel_economics`, `pfg_lead_audits`, `pfg_td_ledger`, `pfg_index_samples`.

Money is `*_minor BIGINT` + `currency CHAR(3)`; percentages are `NUMERIC` percent (15.000), except `pfg_ota_statement_lines.commission_rate`, the ratio (0.15) core/ingest reads from a pct column. A line's `fx_rate` is hotel-currency units per 1 line-currency unit (NULL when no rate).

## Commission rules (02 spec 3.2, Canon K2)

- **Terms in force** = the CONFIRMED contract whose window holds the date (newest start wins), else the OTA's default terms (Booking 15% `ROOM_INCL_VAT` excluding MUNICIPALITYFEE and TOURISMFEE; Agoda 18% `RSR_TAX_INCL` excluding TOURISMFEE; Expedia 18% `ROOM_EXCL_TAX`), with `basis: "default"`. DRAFT contracts never count.
- **Base** from a core.tax `StayTaxBreakdown`: `ROOM_INCL_VAT` = room + SC + VAT, plus MF (and TD/TF/DEST) while the property has NOT configured them as charges on that date (`pfg_channel_config`, default: configured); `RSR_TAX_INCL` = everything but the excluded charge types (when configured); `ROOM_EXCL_TAX` / `NET_RATE` = room.
- **Percent** = contract percent + active `COMMISSION_UPLIFT` programs for the stay date, booker country and rate plan. One half-up rounding to minor units.
- Goldens (tests): Booking 3 nights x AED 350 net -> base 1,216.79, commission **182.52**; 2 nights x AED 400 -> base 927.08, commission **139.06**.

## Commit handlers (core/ingest)

Registered at import for the six kinds; a sandbox batch (hotel NULL, public audit) is refused with 409 `sandbox_batch`. Every handler upserts by natural key, so a re-import changes nothing and a corrected file (ingest `supersedes_batch_id`) replaces the earlier rows.

| Kind | Writes | `commit_result` |
|---|---|---|
| `OTA_STATEMENT` | one `pfg_ota_statements` row (doc_no printed, else `stmt:<source>:<period>`; `RES_STATEMENT`, PDF = `COMMISSION_INVOICE`) + lines keyed `<ota_res_id_norm>#<occurrence>`; lines missing from a corrected file are deleted | `{statement_id, doc_no, ota_code, lines, commission_minor, currency, matches{LINKED,REVIEW,UNMATCHED}, superseded_statements}` |
| `CREDIT_NOTE` | one `CREDIT_NOTE` statement per credit-note number; lines `ADJUSTMENT` with **negative** `commission_minor` (statement `total_minor` is the positive credit) | `{ota_code, credit_notes[{statement_id, doc_no, related_doc_no, lines, credited_minor, currency}], matches}` |
| `OTA_PAYOUT` | `pfg_ota_payouts` per payout reference (amount = sum of partner payout) + lines (`BOOKING/REFUND/CHARGEBACK/ADJUSTMENT`); then bank linking | `{ota_code, payouts[{payout_id, payout_ref, lines, amount_minor, currency}], matches, bank_links}` |
| `VCC_REPORT` | `pfg_virtual_cards` by (ota, vcc_id); `charge_deadline` = min(card expiry, contract rule from the matched stay's check-out: Booking 365 d, Expedia 180 d, Agoda UPC 24 h) | `{ota_code, cards, matches}` |
| `BANK` | positive credits only, deduped by a digest of date, amount, currency, reference, counterparty and description; hotel-currency value via core FX; then bank linking | `{credits, skipped_debits, superseded_credits, bank_links}` |
| `CONTRACT_PDF` | a DRAFT `pfg_ota_contracts` row from the clauses ingest extracted (AI with citations) | `{contract_id, status, ota_code, review_flags}` |

- OTA files need `ota_code` (409 `ota_required` at commit; named sources set it).
- Matching: every statement line, payout line and card goes through `core.stays.match_claim` (`line_kind` `ota_statement_line`, `ota_payout_line`, `virtual_card`); the result (`reservation_id`, `match_status`, `match_score`) is copied on the line. Human decisions on `/hotels/{slug}/stays/matches` survive re-imports.
- Bank linking: a payout's bank transfer reference or payout id inside the credit's reference or description, else the exact amount in the same currency within 3 days of the payout date when exactly one payout fits.

## Contracts: extraction then confirmation

1. The owner stores the document (`POST /hotels/{slug}/files`, purpose e.g. `profit.contract`), then `POST /profit-guard/contracts/extract {file_id, ota_code?}` (or imports it as a `CONTRACT_PDF` batch).
2. `clefs.ai.structured("pg.contract_extract", schema=CanonicalContractTerms)` reads the page texts (pypdf text layer, or a text file as one page). The model must cite a clause (`ref`, verbatim `text`, `page`, `field`) per term and leave unknowns empty.
3. The result is stored as a **DRAFT** with `extraction` (the raw reading) and `review_flags`: `<term>_missing` (the OTA default was filled in), `uncited:<term>`, `citation_page_out_of_range:<ref>` (citation dropped), `effective_to_before_from`, `low_confidence`.
4. A person confirms with `PUT /profit-guard/contracts/{id}` (`confirm` defaults to true). Confirming closes the open-ended confirmed contract it renews (its `effective_to` becomes the day before); any other overlap is 409 `contract_overlap`.

## Public API (`api.py`)

| Function | Returns |
|---|---|
| `record_ota_leak(...)` | `{finding_ref: "pfg_finding:<id>", commission_minor, currency, basis}`; basis `provided` (given), `statement_line` (invoiced for the matched stay, credit notes netted), else `expected`. Stores an internal-only `INQ` PREVENTABLE finding (idempotent per source, inquiry and reservation). None when the channel is not an OTA of the hotel (`channels.kind = 'ota'` or BOOKING/AGODA/EXPEDIA) or no commission can be established |
| `channel_economics(start, end)` | per channel and currency over stayed nights: `channel_code, channel, is_ota, currency, room_nights, gross_minor, room_rev_minor, sc_minor, pass_through_tax_minor, commission_minor, payment_cost_minor, cancellation_cost_minor, net_minor, net_adr_minor, commission_basis (statement/expected/mixed/none), gross, room_rev, commission, net, net_adr, retention, room_rev_fils, commission_fils` |
| `expected_commission(ota_code, breakdown, on)` | `{commission_minor, base_minor, pct (Decimal percent), currency, basis contract/default, contract_id, base_rule, base_components, uplifts, calc}`; None without terms or a room component |
| `reference_ota(as_of)` | `(ota_code, pct)`: the policy's `reference_ota`, else the OTA with the most room nights over the trailing 90 days |
| `distribution_cost_by_night`, `distribution_cost_for_reservation`, `cancellation_cost_by_date` | commission (invoiced, else expected) + payment and booking-engine fees from channel config, allocated by night; cancellation cost = commission invoiced on cancelled / no-show stays by hotel-local cancel date |
| `record_rate_evidence(evidence, emit_signal=False)` | id, 0 when incomplete |
| `parity_tolerance_pct`, `treat_sc_as_revenue` | owner policies (defaults 1.0 and True) |

## HTTP API (`/hotels/{slug}/profit-guard`)

Reads need `profit.view`, writes (W) `profit.manage`. Errors are `{detail, code}`. Money fields come as `<x>_minor` ints plus `<x>` Decimal strings; percentages as Decimal strings. Guest names are masked by role (core.pii `guest_name`).

| Method and path | W | Request | Response |
|---|---|---|---|
| GET `/imports` | | `?kind&status&limit(1-200)` | `Batch[]` (Profit Guard kinds only; core/ingest Batch shape) |
| POST `/imports` | W | `{file_id, kind? OTA_STATEMENT/CREDIT_NOTE/OTA_PAYOUT/VCC_REPORT/BANK/CONTRACT_PDF, source_code?, ota_code?, currency?}` (kind or source_code) | 201 `Batch` with `row_counts` (200 when the bytes were imported before); 400 `unknown_kind`, `source_kind_mismatch` |
| GET `/imports/{id}` | | | `Batch` with `row_counts`; 404 `batch_not_found` |
| POST `/imports/{id}/mapping` | W | `{mapping {canonical: header}, transforms?, save_template? true}` | `Batch` (restaged; template saved) |
| POST `/imports/{id}/review` | W | `{edits: [{row_id, values? {canonical: raw}, skip?}] (1-200)}` | `Batch` (totals and gate recomputed); 400 `invalid_row_edit` |
| POST `/imports/{id}/commit` | W | `{skip_errors? false}` | `Batch` COMMITTED with `commit_result` (table above); 409 `gate_blocked`, `rows_have_errors`, `already_committed`, `ota_required` |
| GET `/contracts` | | `?ota_code&status` | `{contracts: [Contract], defaults: [default terms]}` |
| GET `/contracts/{id}` | | | `Contract` |
| POST `/contracts/extract` | W | `{file_id, ota_code?}` | 201 DRAFT `Contract`; 409 `ai_unavailable`, 422 `ota_required` / `pdf_no_text`, 413 `too_many_pages` |
| POST `/contracts` | W | `ContractIn` | 201 `Contract` (MANUAL; confirmed unless `confirm: false`) |
| PUT `/contracts/{id}` | W | `ContractIn` | `Contract`; 409 `contract_overlap`, `contract_retired` |
| DELETE `/contracts/{id}` | W | | `Contract` RETIRED |
| GET `/programs` | | `?ota_code&active_only` | `Program[]` |
| POST `/programs` | W | `ProgramIn` | 201 `Program`; 400 `program_amount_required`, `invalid_range` |
| PUT `/programs/{id}` | W | `ProgramIn` | `Program`; 404 `program_not_found` |
| DELETE `/programs/{id}` | W | | 204 |
| GET `/channel-config` | | `?ota_code&on` | `{on, rows: [ChannelConfig], in_force: {ota: ChannelConfig + stored}}` |
| PUT `/channel-config` | W | `{ota_code, configured_from, mf_configured_as_charge?, td_configured_as_charge?, card_fee_pct?, payments_fee_pct?, booking_engine_fee_pct?, note?}` | the row (upsert by ota and date) |
| DELETE `/channel-config/{ota_code}/{configured_from}` | W | | 204; 404 `channel_config_not_found` |
| GET `/policies` | | | `{max_total_discount_pct, parity_tolerance_pct, min_finding_minor, min_finding, confidence_floor, treat_sc_as_revenue, reference_ota, stored, currency, updated_at}` |
| PUT `/policies` | W | any of `{max_total_discount_pct, parity_tolerance_pct, min_finding (Decimal string), confidence_floor, treat_sc_as_revenue, reference_ota}` | same as GET |
| GET `/statements` | | `?ota_code&doc_kind&limit` | `Statement[]` |
| GET `/statements/{id}` | | | `Statement` + `lines[]` (each with `reservation_id`, `match_status`, `match_score`) |
| GET `/payouts`, `/payouts/{id}` | | `?ota_code&limit` | `Payout[]`; `Payout` + `lines[]` |
| GET `/bank-credits` | | `?from&to` | `BankCredit[]` (`matched_payout_id`, `hotel_amount_minor`) |
| GET `/vcc` | | `?status&open=true` | `Card[]` by `charge_deadline`, each with `days_left` from the business date |
| POST `/vcc/{id}/charged` | W | `{amount? Decimal string}` (default: the balance) | `Card` (PARTIALLY_/FULLY_CHARGED); 400 `invalid_amount`, 404 `vcc_not_found` |
| POST `/rate-evidence` | W | `{channel, stay_date, price, currency?, los?, adults?, room_label?, plan_label?, taxes_included?, device? DESKTOP/MOBILE, login_state? LOGGED_OUT/LOGGED_IN/MEMBER, booker_country?, search_url?, screenshot_file_id?, captured_at?}` | 201 `RateEvidence` (`comparable_key` = date/los/adults/room/device/login, source MANUAL) |
| GET `/rate-evidence` | | `?from&to&channel` | `RateEvidence[]` |
| GET `/channel-economics` | | `?from&to` (default the 30 days to the business date, at most 400) | `{from, to, channels: [api.channel_economics rows]}`; 400 `invalid_range` |
| GET `/expected-commission` | | `?reservation_id&ota_code?` | `api.expected_commission` + Decimal strings and `calc[]`; 404 `reservation_not_found`, `no_terms` |

Tax (older prefix, `router.py`): `GET /hotels/{slug}/profit/tax/fee-rules?on`, `GET|PUT /profit/tax/profile`, `POST /profit/tax/overrides`.

`Contract` = `{id, hotel_slug, ota_code, entity_name, entity_country, effective_from, effective_to, base_commission_pct, commission_base_rule, excluded_charge_types[], collect_models[], dispute_rules[], vcc_rules{}, clauses[{ref, text, page, field}], source_file_id, import_batch_id, extracted_by LLM/MANUAL, extraction, review_flags[], status DRAFT/CONFIRMED/RETIRED, note, created_by, confirmed_by, confirmed_at, created_at, updated_at, is_default}`.

## Known limits (data part)

- The OTA alias maps live in core/ingest (`aliases/*.yaml`, all UNVERIFIED); this module adds none.
- Statement lines carry no `charges` breakdown yet (the OTA_STATEMENT canonical row has no charges field); L1 falls back to `core.tax` inference.
- Detectors, the recovery ledger routes, disputes, the TD ledger routes and the public audit are later parts of this lane; their tables already exist in `0301`.

## Detectors (02 spec 3.3-3.4; detectors part)

Files: `detectors/` (one file per detector, `base.py` context/draft/tolerance/deadlines, `data.py` shared reads, `store.py` persistence, `signals.py` grouping, `tuning.py` weekly rules, `jobs.py` scheduler, `router.py` HTTP) and `confidence.py`.

- **Entry point.** `detectors.run(cur, hotel_slug, *, trigger="manual", detectors=None, batch_id=None, ota_code=None, run_id=None) -> {run_id, trigger, detectors{id: {found, created, signalable}}, findings, created, signals{groups, items, resolved, proposed}, closed, side_signals}`, inside the caller's transaction. Every detector is `run(ctx, scope) -> list[FindingDraft]` (pure reads; storage happens once in `store.persist`). `detectors.refresh_signals_for(cur, hotel_slug, finding_ids)` rebuilds the signals of those findings' groups: call it after a person accepts, dismisses or recovers a finding.
- **Tolerance** `tol = max(100 minor, 0.2% x amount)`; a difference equal to tol never flags. **Fingerprint** `sha1(detector|ota|ota_res_id_norm|doc)`, doc = the statement doc_no, payout ref, `VCC:<id>`, stay date or `YYYY-MM`; L3 uses `M<check-out month>` so the L3 that L14 raises before a statement and the statement's own L3 are one finding. `subject_ref` is `<group>|<item>` (the signal group is read back by that prefix).
- **One explanation per booking:** L3 supersedes L1, L2, L4 and L5 on the same booking; L1 and L4 supersede L5. Drafts sharing a fingerprint in one run (a multi-room booking printed as several lines) merge into one finding with the amounts added.

| ID | Flags | Amount | Class, base conf. | Deadline | Signal kind |
|---|---|---|---|---|---|
| L1 | Booking, MF/TD configured as charges: reported base - (gross - excluded charges) > tol | pct x excess | RECOVERABLE 0.9 (charges listed), 0.7 (inferred via core.tax) | dispute window | pg.leak.detected |
| L1b | Booking, MF/TD not configured as charges | pct x Σ(MF+TD) 90 days / 3 | PREVENTABLE 0.85 | none | pg.config.fix_available |
| L1 (Agoda) | commission on taxes inside the RSR | pct x taxes | STRUCTURAL, never signalled | none | none |
| L2 | effective pct - (contract pct + active uplifts) > 0.1 pp | diff x base | RECOVERABLE 0.85 (0.5: country-scoped Visibility Booster, unknown booker) | invoice + 14 business days | pg.leak.detected |
| L3 | commission on a PMS no-show / waived cancellation, on the statement's own NO_SHOW/CANCELLED, or an unmatched line where the PMS has data (match 0.5) | commission - pct x fee collected | RECOVERABLE 0.9 x match | dispute window | pg.leak.detected |
| L4 | actual check-out earlier, OTA still reports the booked nights | commission x unstayed share of PMS rates - pct x fee | RECOVERABLE 0.8 | dispute window | pg.leak.detected |
| L5 | OTA commissionable above the PMS base (expected_commission over reservation_breakdown) by > tol; PMS higher raises `pg.folio.overcharge_risk` (SERVICE_RISK) instead | pct x diff | RECOVERABLE 0.75 | dispute window | pg.leak.detected |
| L6 | max(stacked program depth 1 - Π(1 - dᵢ), 1 - sell/BAR) > owner cap | (depth - cap) x public x nights | PREVENTABLE 0.8 | none | pg.config.fix_available |
| L7 | same comparable_key: OTA < min(direct, other OTAs) x (1 - tol%) | (direct - ota) x rooms booked on the channel | PREVENTABLE 0.6, 0.75 with screenshot; Visibility rows stored only | none | pg.parity.breach |
| L8 | card balance > 0, open status, activated, deadline >= today | balance | RECOVERABLE 0.95, tiers 30/14/7/1 days | end of charge_deadline | pg.vcc.expiring |
| L9 | deadline passed with balance (not CANCELLED) | balance | RECOVERABLE 0.9 (recovery via support 0.3 in the trace) | none | pg.vcc.expired |
| L10 | line / payout total / bank credit short (credit by reference, else the one 95-100% credit within ±3 days) | shortfall | RECOVERABLE 0.85 | none | pg.payout.shortfall |
| L11 | foreign payout credited in AED: expected (fx_rates, USD peg) - credited - fee > 0.5% or AED 50 | loss | PREVENTABLE 0.8 | none | pg.config.fix_available |
| L12 | refund (payout REFUND line or negative statement gross) without pro-rata commission reduction | pct x refunded - reductions - credit notes | RECOVERABLE 0.8 | dispute window | pg.leak.detected |
| L13 | chargebacks net of reversals, aged 0-30/31-60/61-90/90+ | disputed | RECOVERABLE 0.7, severity by age | none | pg.leak.detected |
| L14 | PMS Booking no-show / early departure (Expedia Hotel Collect too) with no statement line yet | expected commission at risk | RECOVERABLE 0.85; after the deadline an L3 at x 0.5 | Booking check-out 00:00 local + 48 h; Expedia HC the 4th 12:00 | pg.deadline.approaching |
| L15 | monthly commission from an entity whose confirmed contract says entity_country ≠ AE | 5% x commission | COMPLIANCE 0.9 | 28 days after quarter end | pg.compliance.vat_rcm |

- **Dispute window** (`base.dispute_deadline`): the contract's first applicable `dispute_rules` entry: `HOURS_AFTER_CHECKOUT` (check-out 00:00 local + h), `BUSINESS_DAYS_AFTER_INVOICE` (hotel weekend skipped, end of that day), `DAY_OF_NEXT_MONTH` (12:00 local; `applies_to: hotel_collect` only for Hotel Collect).
- **Confidence** (`confidence.py`): `base x match_score x completeness x feedback`, `completeness = 1 - 0.15 x missing optional inputs`, `feedback = (a + accepted) / (a + b + accepted + dismissed_fp)` with the Beta prior a=4, b=1 (per hotel and detector, overridable in `pfg_detector_tuning.prior_a/b`) centred on the other hotels' posterior mean (leave-one-out). Accepted = ACCEPTED, DISPUTED, RECOVERED; dismissed_fp = DISMISSED with FALSE_POSITIVE_DATA; last 365 days. The trace's last step is the confidence breakdown.
- **Signalling gate:** a finding is stored always and signalled only when confidence >= floor (tuning, else policy 0.5), amount >= min (max of policy 2500 and the tuned minimum), the detector is not suppressed, it is not STRUCTURAL and not from a Visibility parity row.
- **Grouping:** one signal per (detector, OTA, `doc:<no>` or `month:<YYYY-MM>`), dedupe key `pg:<det>:<ota>:<group>`, title like `3 Booking.com no-show commissions, statement INV-1, AED 472.50`, impact in the hotel currency, recommended action `profit.dispute` / `profit.config_fix` / `profit.charge_vcc` / `profit.mark_no_show` / `profit.parity_fix` / `profit.compliance_review` with `params.finding_ids`. L8 at 1 calendar day or less and L14 at 24 h or less get one signal per finding (`pg:<det>:<ota>:item:<fingerprint16>`, severity 4 in L14's last 6 h). A group with no open signalable finding resolves its signal. `pfg_findings.signal_id` points at the signal.
- **Proposed actions (`detectors/proposals.py`).** Every signal refresh keeps one live action of the recommended pg.* type on the signal (`actions.signal_id`; the brief reads it as the signal's `action_id`), with `params {finding_ids, detector, ota_code}` (+ `card_ids`, and `card_id` for one card, on `pg.vcc_charge`), `expected_minor` = the signal impact, `due_at` = the earliest deadline, dedupe key `<signal key>:action:<sha1(ids)[:12]>`. It covers only findings no other live action of that type covers (a packet's action, a neighbour signal's). An undecided (`proposed`) one follows the group (cancelled `regrouped`, re-proposed) and is cancelled `resolved` when the group empties; a decided one is never touched. A person's `ignore` (rejected) is never re-proposed for the same set. The brief's Dispute verb on a group's `pg.dispute` builds the packet from its findings and links it to that action (`api.dispute_packet` -> `disputes._packet_for_proposal`); a packet built by hand cancels overlapping group proposals (`packet_built`). `pg.dispute` without a packet verifies on the findings' ledger (never counting findings held by another action); `pg.vcc_charge` over several cards is verified with the sum of the charged cards once none is pending.
- **One netting per booking.** L1-L4 report each line's gross claim (`gross_minor`) and the booking-level deduction (`deduct_minor`: L3/L4's pct x fee); `base.net_once` adds up a booking's lines per document and subtracts the deduction and the booking credit once (the credit baseline is C, never k x C). When L2 also fires on a booking, L1 claims the excess at the contract percent (L2 claims the whole rate gap), so L1 + L2 equals the true overcharge. L12 takes the larger of the payout-refund and statement-refund totals per booking (never the sum) and nets `booking_credit(payouts=True)`.
- **Auto-close:** a full scan (no batch, OTA or date scope) closes DETECTED L7, L8 and L14 findings it no longer sees (DISMISSED, reason ALREADY_HANDLED, a ledger event by `detector:<id>`), and resolves their item signals.

### Scheduler and tuning

| Job | When | Runs |
|---|---|---|
| `pg.detect_on_commit` (task) | enqueued in the committing transaction of OTA_STATEMENT, CREDIT_NOTE, OTA_PAYOUT, BANK, VCC_REPORT batches (dedupe `pg.detect:<batch>`) | statement: L1-L6, L12, L15 (credit note: the same over the OTA's window); payout: L10-L13; bank: L10, L11; cards: L8, L9 |
| `pg.deadline_watch` | hourly | L8, L14 |
| `pg.nightly_detect` | 02:00 hotel-local | L1 (for L1b), L6, L7, L9, L13, L15 |
| `pg.feedback_tune` | Monday 03:30 hotel-local | `tuning.feedback_tune` |

- The commit hook wraps the live core/ingest handler of each kind once (`detectors.jobs.install_commit_hook()`, idempotent; call it again after anything re-registers the plain handlers).
- **feedback_tune:** TOO_SMALL dismissals (180 days) set `min_amount = max(current, 1.2 x median)`; CONTRACT_EXCEPTION dismissals (7 days) raise `pg.config.fix_available` `pg:contract_review:<ota>` with action `core.task` (Edit the contract); 3+ FALSE_POSITIVE_DATA dismissals in 30 days set `suppressed_until = now + 14 days` and raise `pg.detector.review` (compliance, weekly). Dismissal times come from `pfg_finding_events`.
- Signal kinds registered here (only when no other part registered them first): `pg.leak.detected`, `pg.deadline.approaching`, `pg.vcc.expiring`, `pg.vcc.expired`, `pg.payout.shortfall`, `pg.parity.breach`, `pg.config.fix_available`, `pg.compliance.vat_rcm`, `pg.folio.overcharge_risk`, `pg.detector.review`.

### HTTP API (`detectors/router.py`, prefix `/hotels/{slug}/profit-guard`)

- `POST /detectors/run` (`profit.manage`) `{detectors?: ["L1".."L15"] (max 20), ota_code?}` -> the run summary above; 400 `unknown_detector`. Audited as `profit.detectors_run`.
- `GET /detectors` (`profit.view`) -> `{currency, detectors: [{detector, label, signal_kind, feedback, accepted, dismissed_fp, min_amount_minor, min_amount, confidence_floor, suppressed_until, open_findings, open_amount_minor}]}` for L1, L1b, L2 ... L15.

### Known limits (detectors part)

- L7 exposure uses the rooms already booked on the channel for that date, so a breach before any booking is stored with amount 0 (below the minimum, not signalled).
- L14 covers no-shows and early departures; a date modification the OTA has not been told about is not detected (the OTA's booked dates are unknown before its statement).
- L15 needs a confirmed contract naming the entity country; default terms name none.
- L10 and L11 read the expected bank fee from `pfg_detector_tuning.params.bank_fee_minor` (default 0).
- The public audit sandbox (no hotel) is not run through these detectors: they read the pfg_* tables, which sandbox batches never reach.

## Recovery ledger, disputes, tax and economics (ledger-tax-disputes part)

### Files

- `ledger.py`: finding status moves, auto-verification of credits, the 45-day timeout, summaries, deadlines, the pilot scorecard; `router` (findings, ledger, deadlines, scorecard).
- `disputes.py`: dispute packets (items, HTML, PDF via fpdf2, extranet text, email), approval, submission, outcomes, sync with the ledger and the `pg.dispute` action, the follow-up job body, the brief's packet door; `router`.
- `tax_checks.py`: T1-T6 and TX1 from the folio against `clefs.core.tax`, T7 over `pfg_td_ledger`, the ledger rollup, remittances, the reconciliation CSV; `router` (prefix `/tax`).
- `economics.py`: the 3.6 monthly rollup into `pfg_channel_economics` and its reads; `router`.
- `signals.py`, `actions.py`, `metrics.py`, `jobs.py`: registrations. Importing `jobs.py` registers all of them and installs the auto-verify commit hook.

### Recovery ledger (02 spec 3.5)

- Status flow: DETECTED -> ACCEPTED -> DISPUTED -> RECOVERED | REJECTED | UNRESOLVED; DETECTED, ACCEPTED and DISPUTED can be DISMISSED (reason required, one of the 3.4 reasons). REJECTED can be disputed again, UNRESOLVED can still be RECOVERED by a late credit, DISMISSED can go back to DETECTED. Anything else is 409 `illegal_transition`.
- Every move is a `pfg_finding_events` row. Every credited amount is its own row with `channel = 'credit'` and `amount_minor` = the increment, so "recovered" is always a sum of evidence (the recovered metric and the Overview read these rows).
- **Auto-verify** (`ledger.auto_verify`), per booking (OTA + `ota_res_id_norm` from `subject_ref`) with an open commission finding (`repo.COMMISSION_DETECTORS`: L1-L5, L12; never a VCC, payout or chargeback finding, which a commission credit does not settle). Credit (`repo.booking_credit`, the same measure the detectors net off an amount) = the larger of:
  - statement credits: CREDIT_NOTE / adjustment lines with negative commission, plus the drop in invoiced commission since the dispute was submitted (the submission snapshots `invoiced_commission_minor` into each finding's DISPUTED event, so a corrected invoice re-imported over the original counts);
  - payout credits: REFUND / ADJUSTMENT payout lines with negative commission.
  The larger, not the sum, because one credit often appears in both. What earlier runs attributed is subtracted, and each finding only takes credit above its own `credit_baseline_minor`; the rest goes to the booking's findings in deadline order. 95% or more of a finding's amount sets RECOVERED (`verified_by` STATEMENT or PAYOUT, the lines as evidence); less is a "Partial credit" event: a REJECTED/UNRESOLVED claim goes back to DISPUTED, any other keeps its state (an unfiled claim is never marked DISPUTED, and its alert shows what is still owed).
- **One credit accounting:** a detector nets the booking's current credit off a finding's amount and says how much (`FindingDraft.credited_minor`); that becomes the finding's `credit_baseline_minor` when it is first stored and never changes. A re-run of an unfiled finding stores the gross claim less that first baseline (credit that arrived later is the ledger's recovery, noted as a "Credited after detection" trace step); a finding that is DISPUTED/RECOVERED/REJECTED/UNRESOLVED or has any recovered money keeps its amount and trace (`repo.upsert_finding`). A `pg.recovery.verified` signal (VERIFIED section) is rebuilt per OTA and hotel-local day from the credit events.
- **Timeout:** a DISPUTED finding with no credit 45 days after submission (packet `submitted_at`, else its last DISPUTED event) becomes UNRESOLVED once the OTA has issued a later invoice (COMMISSION_INVOICE, RES_STATEMENT or PAYOUT_STATEMENT dated after submission).
- Runs after each OTA_STATEMENT, CREDIT_NOTE and OTA_PAYOUT commit (task `pg.auto_verify`, enqueued in the committing transaction), daily, and on demand.
- **Pilot scorecard** (3.4): valid = ACCEPTED, DISPUTED or RECOVERED findings (from `pilot_started_on` when set). Success = valid >= 3, or valid RECOVERABLE amount >= 3 x `subscription_minor_month` x `pilot_months`. The terms are read from `hotel_module_settings['profit_guard'].settings` (`pilot_started_on`, `pilot_months` default 1, `subscription_minor_month`; unset price means only the count is judged).

### Dispute packets (02 spec 3.7)

- `create_packet` takes RECOVERABLE findings of one OTA and one currency that are DETECTED, ACCEPTED, REJECTED or UNRESOLVED and not in another open packet; the claim is what is still owed (amount - recovered).
- Outputs: print HTML (inline CSS, `@media print`, escaped), PDF (fpdf2 core fonts; non Latin-1 characters become `?`), extranet text of at most 1,000 characters (one template per detector, one ask per OTA: Booking "please issue a credit note.", Agoda "please correct it on the next invoice.", Expedia "please adjust it in the Partner Central reconciliation."; bookings that do not fit are counted as "+N more bookings"), email subject and body. HTML and PDF are stored as files (`purpose profit.dispute_packet`, 400 days) and re-rendered on approval with the stamp.
- Each item carries the booking reference, guest name, stay, statement document, the contract clause for the claim's field (confirmed contract in force on the stay, else the OTA's public terms; an internal "Canon" reference is never shown), the calculation trace (without the confidence step) and evidence links (signed file paths, 7 days).
- Lifecycle: DRAFT -> APPROVED (owner or finance role, a Clefs admin, or `profit.approve_disputes` when the manifest grants it; 403 `approval_role_required` otherwise) -> SUBMITTED -> ACKNOWLEDGED -> CREDITED | PARTIAL | REJECTED. Approval moves DETECTED findings to ACCEPTED and decides the action `dispute`; submission moves the findings to DISPUTED, calls `submit` + `await_counterparty` on the action and sets `follow_up_at` 10 business days on (Sat-Sun weekend). `send_to` emails the packet (HTML body, PDF attached) through `notify.send_email`.
- The packet status follows its findings (`sync_packets_for`): all RECOVERED -> CREDITED (the action is verified with the recovered total), any credit -> PARTIAL, all REJECTED/UNRESOLVED -> REJECTED (the action becomes rejected_by_counterparty, then unresolved when all are UNRESOLVED). A credit before submission cancels the action (`credited_before_submission`).
- An owner or finance approval of the `pg.dispute` action made elsewhere (the brief) is adopted by the daily follow-up job. `disputes.packet_for_action(cur, slug, action_id=)` returns `{file_id, filename, packet_id}` for the brief's WhatsApp document (see foundation requests: `api.dispute_packet`).

### Tourism Dirham checks and ledger (02 spec 3.3 T-series, 3.8 tax)

| ID | Flags (per reservation and hotel-local month) | Amount | Class, base |
|---|---|---|---|
| T1 | fewer TD units posted than liable (units = rooms x bedrooms when per-bedroom) | missing units x rate | COMPLIANCE 0.9, due at the remittance date |
| T2 | TD posted past night 30 of the stay chain (per room line, back-to-back included) | the over-collection | COMPLIANCE 0.9 |
| T3 | a posting that is not a multiple of the rate: per-unit amount off the rule | difference x units | COMPLIANCE 0.9 |
| T4 | VAT posted ~ v x (VAT base + TD) instead of v x base (tolerance 2 minor units + 1 per night) | v x TD | COMPLIANCE 0.85, due at the VAT return |
| T5 | more TD units posted than liable on a night with no capped units | the duplicate amount | RECOVERABLE (to the guest) 0.95 |
| T6 | TD posted on a NO_SHOW | the amount | COMPLIANCE 0.95 |
| T7 | ledger: remitted differs from collected by more than AED 1, or unremitted with 5 days or less to the due date | the gap / the amount owed | COMPLIANCE 0.9, due at the due date |
| TX1 | TD or MF posted on a night inside an announced exemption the owner applied | the amount | COMPLIANCE 0.9 |

- Findings use the detectors' storage (`detectors/store.persist`: fingerprint `sha1(det||res:<id>|YYYY-MM)`, confidence, trace) with `subject_ref = month:<YYYY-MM>|res:<id>` (T7: `|ledger`). One signal per (check, month): `tax:<det>:month:<YYYY-MM>`, kind `tax.td.anomaly` (T7: `tax.td.remittance_due`), compliance, gated by the confidence floor and suppression only (the owner's minimum amount does not hide a filing error). A re-scan that no longer sees a finding closes it (DISMISSED, ALREADY_HANDLED, "No longer detected"); it reopens if the posting comes back.
- `pfg_td_ledger` per month: `liable_room_nights` and `td_expected_minor` from `core.tax.td_month`, `td_collected_minor` = net TD postings in the folio (reversals subtract), `due_date` (the 15th of the next month plus any applied deferral), and the remittance (`td_remitted_minor`, `remitted_on`, `remit_reference`). Status OPEN, REMITTED (within AED 1) or MISMATCH.
- Reminders: `tax.td.remittance_due` per unremitted month with TD (`tax:td:remit:<YYYY-MM>`, severity 4 in the last day), raised on the 10th and 14th and resolved by a matching remittance. `tax.rule.unverified` (`tax:rule:unverified`) while `core.tax.notices` lists an announced, unverified rule in the next 90 days.
- The CSV export (`td-reconciliation-<slug>-<YYYY-MM>.csv`) is an internal reconciliation file, not a DET upload format: one row per reservation (liable and capped unit-nights, TD expected, TD posted, difference, Decimal strings) and a TOTAL row; cells starting with `= + - @` are prefixed with `'`.

### Channel economics rollup (02 spec 3.6)

- `pg.channel_economics_rollup` (03:00 local) stores last and this month per channel in `pfg_channel_economics` (hotel currency; rows in another currency are converted at month end, skipped without a rate; channels with nothing left that month are deleted).
- Built on `repo.channel_economics` (RN, gross, RoomRev with SC when `treat_sc_as_revenue`, C invoiced else expected, PayCost, CxlCost), plus:
  - `commission_on_tax = max(0, min(C - pct x base_ex_tax, pct x taxes inside the commissioned amount))`, pct = contract percent + uplifts. Base ex tax: ROOM_INCL_VAT = room + SC + VAT (K2), else room + SC. Booking.com: only MF/TD not configured as charges count as taxes in the price (L1b), and L1 findings for the month's stays are added (the OTA commissioned configured fees). Agoda (RSR_TAX_INCL): MF + VAT, minus fees excluded from the sell rate (TD). A rate overcharge is never called tax.
  - `fx_loss` = L11 findings grouped in the month; `cancellation_cost` += PER_BOOKING_COST programmes (AGX) per statement line carrying them; `program_cost` = Σ (public - sell) with public = sell / Π(1 - discount) over the line's DISCOUNT programmes; `avg_discount_depth` weighted by gross.
  - `net = room_rev - commission - payment_cost - fx_loss - cancellation_cost`.
- Reads add `net_adr`, `gross_adr`, `retention`, `effective_commission_pct`, `commission_on_tax_kind` (`structural` Agoda, `fee_config` Booking.com) and a `waterfall` (Gross -> taxes passed through -> SC when not revenue -> commission -> payment -> FX -> cancellations -> Net).

### Signals (this part)

| Kind | Section | Cadence | Dedupe key |
|---|---|---|---|
| `pg.dispute.ready_for_approval` | money_available | daily | `pg:dispute:<id>:approve` (due at the earliest dispute window) |
| `pg.dispute.follow_up` | money_available | daily | `pg:dispute:<id>:follow_up` |
| `pg.recovery.verified` | verified | weekly | `pg:recovered:<ota>:<local date>` |
| `pg.import.needs_review` | service_risk | weekly | `pg:import:needs_review` (a PG batch in MAPPING_REQUIRED/REVIEW over 24 h) |
| `tax.td.remittance_due` | compliance | daily | `tax:td:remit:<YYYY-MM>`, and T7 groups `tax:T7:month:<YYYY-MM>` |
| `tax.td.anomaly` | compliance | weekly | `tax:<T1..T6,TX1>:month:<YYYY-MM>` |
| `tax.rule.unverified` | compliance | weekly | `tax:rule:unverified` |

### Action types (`actions.py`; all human tasks, `decide_perm` `profit.manage`, ceiling approve)

| Type | Always approve | Verified when | Counts toward verified profit |
|---|---|---|---|
| `pg.dispute` | yes | its packet is CREDITED (or PARTIAL with no new credit for 30 days); REJECTED -> not verified | yes |
| `pg.mark_no_show` | yes | the statement shows the no-show or a net commission <= 0; an L3 finding for the booking -> not verified | yes (commission avoided) |
| `pg.vcc_charge` | yes | a VCC report committed after the decision shows FULLY_CHARGED, or a bank credit naming the card or booking (covering the balance) or a payout line for the booking dated after it; CANCELLED/EXPIRED/NOT_LOADED (checked first) or past its deadline -> not verified; a staff "charged" click alone stays pending | yes |
| `pg.config_fix` | yes | L1b: with MF and TD configured as charges, at least 90% of the next month's statement lines (stays from the fix date) have base <= gross - MF - TD within tol; value = pct x (MF + TD) over those lines; others: no new finding of the detector for the OTA for 30 days | no |
| `pg.payout_query` | no | all its findings RECOVERED; all REJECTED/UNRESOLVED/DISMISSED -> not verified | yes |
| `tax.td_remit` | no | the month's `pfg_td_ledger` row: remitted within AED 1 of collected, with a reference (the `tax.td.remittance_due` signals, reminder and T7, recommend it with `params.month`); off by more -> not verified | no (compliance) |

### Metrics (permission `profit.view`, money in the hotel currency)

`pg.leakage_detected_aed`, `pg.leakage_recovered_aed` (credit events), `pg.recovery_rate`, `pg.open_findings` (dimensions channel, detector, class, status); `pg.commission_paid_aed`, `pg.effective_commission_pct` (statement lines by check-in, channel); `pg.commission_on_tax_aed`, `pg.net_adr_aed`, `pg.gross_adr_aed`, `pg.net_retention_pct` (the rollup, channel); `pg.vcc_uncollected_aed` (by charge deadline); `tax.td_collected_aed`, `tax.td_remitted_aed` (the TD ledger by month). Internal-only, STRUCTURAL and COMPLIANCE findings never enter the money metrics.

### Jobs (this part)

| Job | When | Runs |
|---|---|---|
| `pg.auto_verify` (task) | each OTA_STATEMENT, CREDIT_NOTE, OTA_PAYOUT commit (dedupe `pg.verify:<batch>`) | `ledger.auto_verify` for the batch's OTA |
| `pg.auto_verify` | daily 04:30 local | every OTA, the 45-day timeout, `pg.import.needs_review` |
| `pg.channel_economics_rollup` | daily 03:00 local | `economics.nightly` |
| `pg.dispute_followup` | daily 09:00 local | `disputes.follow_up` (reminders, brief approvals adopted) |
| `tax.td_checks` | daily 02:10 local | T1-T6, TX1 for last and this month; T7 |
| `tax.td_deadline_watch` | hourly | T7 |
| `tax.td_ledger_rollup` | the 1st, 04:00 local | the closed month's ledger row and its checks |
| `tax.td_remit_reminder` | daily 09:05 local, acts on the 10th and 14th | `tax_checks.remit_reminder` |
| `tax.unverified_rule_banner` | Mondays 08:00 local | `tax.rule.unverified` |

### HTTP API (this part, prefix `/hotels/{slug}/profit-guard`)

Reads need `profit.view`, writes `profit.manage`. Money fields are `<x>_minor` ints with `<x>` Decimal strings; errors `{detail, code}`.

**Findings and ledger** (`ledger.router`)
- `GET /findings ?status=a,b&detector=L3,L14&ota=BOOKING&class=RECOVERABLE&from&to&limit(1-500)` -> `[finding]`, newest first; internal-only rows never listed. A finding: the `pfg_findings` row + `amount`, `recovered`, `group`, `booking_ref`, `hours_left`.
- `GET /findings/{id}` -> finding + `events[]` (the ledger) + `dispute_packet {id, status, total_minor, currency, submitted_at}`; 404 `finding_not_found`.
- `POST /findings/{id}/transition {to, note?, amount?}` -> finding. `amount` (Decimal string) is the credited total for RECOVERED (default the full amount). 409 `illegal_transition`; 400 `invalid_status`, `dismiss_reason_required` (DISMISSED goes through /dismiss). Audited `profit.finding_transition`.
- `POST /findings/{id}/dismiss {reason FALSE_POSITIVE_DATA|CONTRACT_EXCEPTION|ALREADY_HANDLED|TOO_SMALL|NOT_WORTH_DISPUTING|OTHER, note?}` -> finding; 409 when not open.
- `POST /findings/verify` -> `{bookings, credited_minor, recovered[], partial[], unresolved[], packets[]}` (runs auto-verify now).
- `GET /ledger ?from&to&limit` -> `{currency, found, recovered, open, due_72h_count, due_72h, compliance_open, funnel{STATUS: {count, amount}}, by_detector[{detector, count, amount}], other_currency_findings, events[]}`.
- `GET /deadlines ?days(1-120, default 14)` -> `[{kind finding|td_remittance, deadline_at, hours_left, amount_minor, ...}]` soonest first.
- `GET /pilot-scorecard` -> `{valid_findings, by_status, valid_recoverable, recovered, min_valid, amount_threshold, criteria{count, amount}, success, pilot_started_on, pilot_months, subscription_minor_month}`.

**Disputes** (`disputes.router`)
- `GET /disputes ?status=a,b&limit` -> `[packet]` (no items).
- `POST /disputes {finding_ids[1-200], note?}` -> 201 packet with `items[]`; 400 `no_findings`, `not_recoverable`, `mixed_ota`, `mixed_currency`, `too_many_findings`; 404 `finding_not_found`; 409 `finding_not_open`, `already_in_packet`.
- `GET /disputes/{id}` -> packet + `items[{id, detector, label, booking_ref, pms_res_id, guest_name (masked by role), check_in, check_out, doc_no, amount, clause_ref, clause_text, calc[], evidence[]}]`, `recovered`, `approved_by_name`, `can_approve`, `pdf_path`, `print_path`, `extranet_text`, `email_subject`, `email_body`, `html_file_id`, `pdf_file_id`.
- `GET /disputes/{id}/print` -> text/html (print-optimised). `GET /disputes/{id}.pdf` -> application/pdf (inline).
- `POST /disputes/{id}/approve` -> packet; 403 `approval_role_required` (owner or finance only), 409 `illegal_transition`.
- `POST /disputes/{id}/submitted {channel extranet|email|portal|other, reference?, note?, send_to?}` -> packet; 409 `not_approved`.
- `POST /disputes/{id}/outcome {outcome ACKNOWLEDGED|CREDITED|PARTIAL|REJECTED, amount? (PARTIAL), note?}` -> packet; 400 `invalid_outcome`, `amount_required`; 409 when not submitted.

**Tax** (`tax_checks.router`, prefix `/tax`)
- `GET /tax/td-ledger ?month=YYYY-MM | months(1-36)` -> `{currency, months[{month, liable_room_nights, td_expected, td_collected, td_remitted, gap, due_date, status, remitted_on, remit_reference}], notices[]}` (the named month, or last and this month, are refreshed first).
- `PUT /tax/td-ledger/{YYYY-MM} {amount, remitted_on, reference?}` -> ledger month; 400 `invalid_month`. Audited `tax.td_remittance`; re-runs T7.
- `GET /tax/td-export.csv ?month=YYYY-MM` -> text/csv attachment.
- `POST /tax/checks/run {months?: [YYYY-MM]}` -> `{run_id, months, findings, created, by_detector, closed, signals}`.

**Channel economics** (`economics.router`; the live range view stays `GET /channel-economics`)
- `GET /channel-economics/monthly ?from=YYYY-MM&to=YYYY-MM` (default the last 6 months, at most 36) -> `{currency, from, to, rows[{month, channel, room_nights, gross, room_rev, sc, pass_through_tax, commission, commission_on_tax, commission_on_tax_kind, payment_cost, fx_loss, cancellation_cost, program_cost, net, net_adr, gross_adr, retention, effective_commission_pct, avg_discount_depth, commission_basis, waterfall[]}], totals}`; 400 `invalid_range`, `invalid_month`.
- `POST /channel-economics/rollup {months?: [YYYY-MM]}` -> `{month: rows stored}`.

### Known limits (ledger-tax-disputes part)

- The routers, `jobs.py` (registrations and the commit hook) and the `profit.approve_disputes` permission need the manifest (`module.py`) to list them; until then the tests mount them themselves.
- A partial credit on a packet verifies its action only after 30 days without a new credit (core has no way to raise an outcome already recorded).
- T-series checks read the PMS folio (`folio_charges` with `stay_date`, else `posted_date`); a PMS that posts one TD line per stay (not per night) reads as T1 on the other nights.
- Auto-verify compares amounts in the finding's currency only; a credit note in another currency is not matched.
- The detectors' signals recommend `profit.*` action keys; this part registers the `pg.*` keys from 02 spec 3.7-3.9.

## Public OTA audit and Leakage Index (02 spec 3.11, Canon 23; public-audit part)

### Files

- `public_audit.py`: the anonymous upload, the sandbox checks, the teaser and lead unlock, the founder notification, the AI snapshot hand-off, `pg.public_audit_purge` and `router` (prefix `/public`, which also serves the index).
- `index.py`: `record_sample`, `aggregate`, `METHODOLOGY`, `MIN_N = 30`.
- Tables (created by `0301`): `pfg_lead_audits` (one row per audit; `token_hash` and `ip_hash` are hex sha256 / HMAC, never the token or the IP) and `pfg_index_samples`.

### Flow

1. `POST /public/audits` runs the checks in this order: the Content-Length cap, the rate limits, a byte-capped multipart parse, the processing consent, the MIME sniff, then the row or page cap. The file is stored with `files.put_file(hotel_slug=None, purpose="pg.public_audit", retention 30 days)` and staged by `core.ingest.create_batch(None, kind=OTA_STATEMENT, via=PUBLIC_AUDIT)`. The batch is never committed.
   - A layout the alias maps miss takes the AI mapping proposal, but only when that proposal covers every required field.
   - Any failure rolls back the file and the batch, so an unreadable upload leaves nothing stored. It still counts against the rate limit.
2. The sandbox checks run over the staged rows:
   - Defaults: Dubai, 3-star, convention A, and a default percentage per OTA, not a market rate: Booking 15%, Agoda 18%, Expedia 18%.
   - Fees are inferred with a hand-built Dubai 3-star `TaxProfile`: SC 10%, MF 7% on room + SC, VAT 5% on room + SC + MF, and TD AED 10 per room-night for the first 30 nights.
   - The formulas, tolerance and rounding are the detectors' own (`detectors/base.py`).

| Check | Rule in the sandbox | Class, confidence |
|---|---|---|
| L1 (`fees_configured`) | Booking, AED, status OK. Base = the printed commissionable amount, else the gross. Excess = base - (gross - MF - TD), and a finding needs excess > tol. Amount = applied pct x excess. | TO_CONFIRM 0.70 |
| L1b (`fees_not_configured`) | The same lines and amount. The commission is due under the contract until the fees are configured as charges. | TO_CONFIRM 0.70 |
| L1 (`agoda_tax_inclusive`) | Agoda, AED. Amount = pct x (MF + VAT) inside the rate (the TD is collected at the property). Informational only. | STRUCTURAL 0.70 |
| L2 | Effective pct - default pct > 0.1 pp (exactly 0.1 pp does not flag), and the amount > tol. Status OK only. Title: "Above the percentage this check assumed: check your contract and programme terms". | CHECK_CONTRACT 0.50 (contract and programmes unknown) |
| L3 (status-only) | Statement status NO_SHOW or CANCELLED with commission > tol. The fee collected is unknown and taken as 0. | RECOVERABLE 0.80 |
| L14 (view) | Every L3 whose dispute window is still open. The window is Booking check-out 00:00 Dubai + 48 h, or Expedia the 4th of the next month at 12:00 Dubai. Adds no money of its own. | n/a |
| L12 | Per booking: pct x the refunded amount (negative gross) - the commission already reduced (negative commission lines) > tol. pct is the booking's own commissioned lines in the statement; without one, the default percentage per OTA, and then the calc has an "Assumed percent" step and `missing: [contract]`. | RECOVERABLE 0.80 |

   - Not checked (listed in the report with the reason): L4, L5, L6, L7, L8, L9, L10, L11, L13, L15.
   - Lines in a second currency are counted and skipped. L1 runs on AED lines only.
3. The headline counts only what stands without the contract (the public classes of 2026-10-06; every upload is read as "not sure" until the form asks):
   - `found.low` = `found.high` = the RECOVERABLE findings (L3, L12). The pair stays for the clients that read one.
   - L1 and L1b are TO_CONFIRM: Dubai's 2026 relief may have exempted collecting MF and TD, and the charge setup is unknown. L2 is CHECK_CONTRACT: the default percentage is not the contract, and Preferred or Visibility Booster raise it legitimately.
   - Both keep their calc in the full report, "to confirm with you", and count only in `teaser.to_confirm`, the number of statement lines carrying one. They are never in `found`, `findings`, `by_detector`, the founder's figure or the Leakage Index.
   - Agoda's STRUCTURAL row is informational: in the full report, outside all of these (it depends on whether MF was collected).
   - There are no per-scenario totals. A report stored before the change (no `teaser.to_confirm`) is re-read under these classes by `current_report` in `view` and `unlock`; the purge removes the last one by 2026-11-06.
4. Guest names are reduced to initials when the report is built. The report never carries a full name for any caller.
5. The first unlock does three things:
   - Starts `visibility.api.start_ai_snapshot(subject={hotel_name, city (default Dubai), area, website, star}, requester_ref="pg.audit:<id>")` in a savepoint. `None` or an exception gives `{status: "unavailable"}`.
   - Emails the founder (`FOUNDER_NOTIFY_EMAIL`), including the lead's contact details and the number of lines to confirm.
   - Sends the founder a WhatsApp template (`FOUNDER_NOTIFY_WHATSAPP`, template `PG_AUDIT_LEAD_WA_TEMPLATE`, default `clefs_audit_lead`, params `[hotel, found and lines to confirm, findings]` (for example `AED 75.00, 2 lines to confirm`), platform sender, utility).
   - Notify purpose: `profit_guard.public_audit_lead`, idempotent per audit. A channel with no address set is skipped.
6. Index consent:
   - Ticked: `index.record_sample` adds one anonymised sample. It skips an audit that already has one, and a file another consented audit already contributed.
   - Unticked on a later lead post: the sample is deleted while the audit exists.
   - After the purge the sample stays with `audit_id` NULL.
   - `aggregate` re-sums each sample's leak from its `detector_counts` minors for today's `INDEX_DETECTORS` (L3, L12) and recomputes `leak_rate`, so a sample recorded before 2026-10-06 (L2 inside its leak) reads like a new one.
7. `pg.public_audit_purge` runs daily at 03:35 UTC, platform-wide. For each audit past `expires_at` (30 days) it deletes the lead row, discards the sandbox batch and its rows, tombstones the file, and calls `visibility.api.purge_ai_snapshot`. A file or batch still used by another live audit of the same bytes is kept.

### HTTP API (public, no auth)

Errors use the shape `{detail, code}`. Money is `{minor: int, amount: "Decimal string"}` in the statement's currency.

- `POST /public/audits`: `multipart/form-data` with these fields:
  - `file`: required. CSV/TSV (UTF-8, UTF-16 or cp1256), XLSX, XLS or PDF, sniffed from its bytes.
  - `consent_processing`: required, `"true"`.
  - `consent_transfer_abroad`: `"true"`, required for EVERY format (the statement is stored on the database in Singapore with its guest names; a PDF is also read by the AI provider in the United States); 422 `transfer_consent_required` before anything is stored. Both ticks are stored on `pfg_lead_audits` and in `report.consents` with their wording (`UPLOAD_CONSENT_TEXTS`, version `2026-09-29.2`).
  - With no `ota_code`, the OTA is detected from the headers: core's `detect` terms first, then `guess_source` (the OTA statement source whose field aliases match most, booking number and commission required, a tie names none).
  - `ota_code`: optional, `BOOKING | AGODA | EXPEDIA`. Without it the OTA is detected from the headers.
  - Response 201: `AuditView` plus `token`. The token is shown only in this response.
  - 400 `bad_request`: not multipart, or a bad Content-Length.
  - 413 `file_too_large` (over 4 MB: Vercel refuses a function body over 4.5 MB before FastAPI runs), `too_many_rows` (over 5,000), `too_many_pages` (PDF over 10 pages).
  - 415 `unsupported_file_type`.
  - 422 `consent_required`, `transfer_consent_required` (a PDF without the transfer tick; nothing is stored), `file_required`, `invalid_ota`, `empty_file`, `invalid_pdf`, `unrecognised_columns`, `no_rows`, or the ingest failure code (for example `pdf_no_text`, `no_header_row`, `ai_disabled`).
  - 429 `rate_limited`: 3 per IP hash per day, then 50 per day in total.
- `GET /public/audits/{token}`: `AuditView`. 404 `not_found` when the token is unknown or expired.
- `POST /public/audits/{token}/lead`: body `{name, hotel_name, email?, whatsapp? (E.164), consent_contact: true, consent_transfer_abroad: true, consent_index?: bool, city?, area?, website?, star?: 1-5}`. At least one of `email` or `whatsapp` is required. The contacts are sealed (`email_enc`, `whatsapp_enc` via `core.privacy.seal`, migration 0303); the daily purge seals any plaintext left from before. The transfer tick is stored as `consent_lead_transfer_abroad` and `report.consents.lead_transfer_abroad`.
  - Response: the unlocked `AuditView`.
  - 422 `consent_required`, `transfer_consent_required`, `invalid_input` (name 1-120 characters, hotel 2-160, email format, a national phone number), `not_ready`.
  - 404 `not_found`.
  - Posting again updates the lead and the index consent, and never notifies twice.
- `GET /public/leakage-index`:
  - Always: `{title, claim_id: "uae_ota_leakage_index", min_n: 30, n, published, methodology{source, measure, detectors, assumptions, statistic, publication, limitations}}`.
  - Below 30: `needed` is added. No figure is returned.
  - From 30: `as_of`, `period{from,to}`, `median_leak_rate`, `p25_leak_rate`, `p75_leak_rate` and `share_with_leak` (4-decimal strings), `detector_share{L3,L12}` (L2 is CHECK_CONTRACT: a line to confirm, not counted), `by_ota[{ota_code, n, median_leak_rate, p25_leak_rate, p75_leak_rate, share_with_leak}]` (only OTAs with n >= 30), and `suppressed_otas[]`.
  - Rendered on `/free-ota-audit` by `LeakageIndex.tsx` (median, IQR, n, period, per-OTA rows, methodology) only when `published` and `n >= min_n`; otherwise the section renders nothing.

`AuditView` fields:

- `status` (`READY`), `created_at`, `expires_at`, `unlocked`, `teaser`.
- `full`: the breakdown when unlocked, else null.
- `ai_snapshot`: the `get_ai_snapshot` answer or `{status: "unavailable"}` when unlocked, else null.
- `lead`: `{name, hotel_name}` when unlocked, else null. Contact details are never returned.
- `teaser`: `{ota_code, currency, lines, period{from,to}, commission, found{low, high} (equal: RECOVERABLE only), findings, by_detector{L3,L12}, to_confirm (lines, never an amount), open_deadlines}`.
- `full` contains:
  - `findings[]`: `{detector, class (RECOVERABLE | TO_CONFIRM | CHECK_CONTRACT | STRUCTURAL), scenario, title, amount_minor, amount, currency, confidence, missing[], row, page, reservation, guest (initials), check_in, check_out, status, deadline_at, window_open, calc[]}`.
  - `structural` (Agoda's informational sum, never shown as a figure), `deadlines[]` (the L14 view), `status_counts`, `skipped{other_currency_lines, ota_unknown}`, `assumptions`, `not_checked[{detector, reason}]` and `notes[]`.

### Known limits (public-audit part)

- `module.py` must list `public_audit.router` and import `public_audit` for the routes and the purge job to exist in the app. Until it does, the tests mount the router themselves.
- `FOUNDER_NOTIFY_EMAIL`, `FOUNDER_NOTIFY_WHATSAPP` and `PG_AUDIT_LEAD_WA_TEMPLATE` are read through `env()` and are not yet Settings fields. The WhatsApp template must be approved in Meta before the founder receives it.
- The contact and index consents are stored on the lead row, with their wording version, in `report.consents`. The foundation `consents` table requires a hotel, and a lead has none.
- Fee inference is Dubai 3-star, convention A, AED only. An Abu Dhabi statement is still read with Dubai rates. The report says so.
- The sandbox runs no PMS, payout, card or bank check. The deeper audit needs onboarding.
