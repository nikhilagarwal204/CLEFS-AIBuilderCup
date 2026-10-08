# core/ingest

Files become canonical rows (Canon: core/ingest; 02 spec 2.2; 04 spec D4).

A file stored through the Files contract (`POST /hotels/{slug}/files`) becomes
one `import_batches` row. Ingest sniffs it, maps its headers to the kind's
canonical fields, stages every row in `import_rows` next to its raw values,
and on commit hands the rows to the handler registered for the kind. Core
never imports a module: modules register their commit handler at import.

## Pipeline

1. **Sniff** (`sniff.py`). Format from magic bytes, then name and content type:
   CSV/TSV/TXT, XLSX (openpyxl, read-only, values only), XLS (xlrd), PDF, ICS,
   XML. Text encodings: a BOM decides (UTF-8, UTF-16 LE/BE); else UTF-16
   without a BOM by its NUL pattern; else UTF-8; else cp1256 (Arabic Windows
   exports). Delimiter from `, ; \t |` by the most consistent column count
   (csv.Sniffer misreads semicolon files with decimal commas). Header row =
   the first of the top 30 rows whose non-numeric cells fill at least 60% of
   the table width (and number at least two), so a title row never passes.
   Trailing "Total" rows are kept aside as the printed total for the gate.
2. **Fingerprint** (`sniff.header_fingerprint`). Headers normalized
   (lowercase, punctuation stripped, spaces collapsed), sorted, sha1.
3. **Map** (`mapping.py`, `aliases.py`). In order:
   a saved `mapping_templates` row for (hotel, source, fingerprint), then a
   global template (hotel_slug NULL), then the static alias map
   `aliases/<source>.yaml` plus `aliases/generic.yaml` and the field's own
   name. Every alias file is marked UNVERIFIED: no OTA publishes its headers.
4. **Coverage.** When a required field is still unmapped and AI is on, the
   headers and at most 5 sample rows go to `clefs.ai.structured` (task
   `ingest.map_columns`) with guest names, contact values and card numbers
   replaced by `<masked>`. The proposal is stored as `mapping_suggestion`; it
   is never applied without a person (`PUT .../mapping`), and the confirmed
   mapping is saved as a template so next month maps itself.
5. **PDF** (`pdf.py`). Text per page (pypdf), then `clefs.ai.structured`
   (task `ingest.extract_pdf`) returns canonical rows with page and line
   references plus the printed total. Status REVIEW; the printed-total gate
   applies (below). At most 30 pages and 120k characters (`pdf_too_large`);
   a scan with no text layer fails with `pdf_no_text`; AI off fails the batch.
6. **Stage** (`stage.py`, `transforms.py`). Each row is coerced per field type
   (money to minor units of the batch currency, dates day-first unless
   `date_fmt` says otherwise, `pct_to_ratio`, `sign_flip`, `decimal_sep`).
   Row status OK, WARN, ERROR, DUP (same natural key twice in the file) or
   SKIP (a person skipped it). A multi-month P&L becomes one row per account
   per month column.
7. **Commit** (`commit.py`). Gate checked, rows handed to the kind's handler
   inside the request transaction, the superseded batch retired, contact
   columns redacted from `import_rows.raw`. A handler error rolls everything
   back and the batch stays in REVIEW.

## Printed-total gate

Kinds with a `total_field` (OTA_STATEMENT: `commission_minor`, OTA_PAYOUT:
`partner_payout_minor`, VCC_REPORT, CREDIT_NOTE, PMS_FOLIO, BANK,
SUPPLIER_INVOICE) compare the sum of their OK and WARN rows with the printed
total: the last "Total" row of a table file, or the total the PDF prints.
Tolerance is one major unit (AED 1 = 100 fils).

| `gate_status` | Meaning | Blocks commit |
|---|---|---|
| `ok` | lines add up | no |
| `mismatch` | lines differ from the printed total | yes |
| `missing` | a PDF with no printed total found | yes |
| `n/a` | no total field, or a table file with no Total row | no |

A blocking gate is lifted by editing rows until it reads `ok`, or by
`POST .../gate-override {reason}` (audited; `gate_blocking` becomes false).

## Supersede, dedupe and redaction

- **Same bytes.** `UNIQUE(hotel_slug, sha256)`: uploading or forwarding the
  same file again returns the existing batch (HTTP 200, not 201). A FAILED
  batch is re-run with the new labels instead.
- **Corrected re-upload.** A new batch of the same kind and source that
  carries the same `doc_no` (else the same period) records
  `supersedes_batch_id` at review; on commit the older batch becomes
  SUPERSEDED (`superseded_by` set) and its contact columns are redacted. The
  commit handler receives the whole corrected file and must upsert, never
  append (Profit Guard and Finance key their rows by batch and natural key).
- **Contact redaction (04 spec D1).** After commit (and on supersede), every
  raw key that holds a contact value (headers mapped to a `contact` field,
  headers that read like email, phone, passport or card number, and the
  contact field names themselves) is removed from `import_rows.raw` and
  `canonical`. The daily `ingest.sweep` (03:45 UTC) does the same for
  FAILED and SUPERSEDED batches and for batches left uncommitted for 30
  days (`stats.contacts_redacted` marks them), deletes staged rows of closed
  batches after 1095 days, and deletes public-audit sandbox batches
  (`hotel_slug` NULL) after 30 days.

## Commit handlers (04 spec D4)

```python
from clefs.core.ingest import register_commit_handler

def commit_pl(cur, batch: dict, rows: list[dict]) -> dict | None: ...
register_commit_handler("PL_STATEMENT", commit_pl)  # at module import
```

- `batch` is the `import_batches` row as a dict (`id`, `hotel_slug`, `kind`,
  `source_code`, `ota_code`, `currency`, `period_month`, `period_start`,
  `period_end`, `doc_no`, `supersedes_batch_id`, `mapping`, `stats`, ...).
- `rows` are the canonical dicts of the OK and WARN rows in file order, each
  with `_row_id`, `_row_no` and (PDF) `_page` so a handler can cite evidence.
- The handler runs inside the commit transaction. Raise to abort (the batch
  stays in REVIEW). Return a JSON-able dict: it is stored as
  `commit_result` and shown in the console.
- Registered by core itself: `PMS_RESERVATIONS`, `PMS_FOLIO`, `OCCUPANCY`
  (core/stays writers) and `BOOKING_ENGINE_ATTRIBUTION` (UTM and promo
  columns on reservations, D2). Modules register the rest: Finance
  (`PL_STATEMENT`, `TRIAL_BALANCE`), Profit Guard (`OTA_STATEMENT`,
  `OTA_PAYOUT`, `VCC_REPORT`, `CREDIT_NOTE`, `BANK`, `CONTRACT_PDF`),
  Amenities (`ISSUANCE`, `SUPPLIER_INVOICE`), Demand Radar (`COMP_RATES`,
  `ICS`), Upsell (`MENU`). A kind with no handler stays in REVIEW (`no_commit_handler`).
- **Hooks for handlers that take choices** (added for MENU):
  - `commit_batch(..., options={...})` hands them to the handler as `batch["options"]["commit"]` and, on a real commit, stores them on the batch. They are namespaced so they never clobber the sniff options.
  - `commit_batch(..., dry_run=True)` runs every check and the handler inside a savepoint that is rolled back. The batch stays in REVIEW, nothing is audited, and the result is the batch plus `commit_result` and `dry_run: True`. The handler sees `batch["dry_run"]` and must not do work outside the database then.
  - `mark_row_error(cur, row_id, message)` flags a row the handler refused as ERROR with the reason. Use it with a per-row savepoint, as the core handlers do.
- **MENU** (0024 widens `import_batches.kind`): an F&B menu for Upsell. Every field is text, because the menu module parses its own cell formats (`AED 35`, `Mon-Fri`, `vegan;halal`). `name_en` and `price` are required. The alias map is `aliases/menu.yaml` (source `menu`); it has no `detect` terms, so a person labels the file and email never auto-detects one. Upsell registers the handler (`modules/upsell/importer.py`). Its commit options are `default_outlet`, `create_outlets` (default true) and `strict` (any refused row raises 409 `menu_rows_refused` and nothing is written).
- `registered_handlers()` lists `{kind: dotted handler name}`;
  `GET .../sources` reports `committable` per kind from it.

## HTTP API (`/hotels/{slug}/ingest/...`)

Mounted through `app.CORE_ROUTER_HOOKS`. Reads need `core.settings.view`.
Writes (marked W) also need `core.settings.manage` (owner, gm) or the
`finance` or `revenue` role, else 403 `forbidden`. Guest names and contact
values in rows and samples are masked by role through `clefs.core.pii`.
Errors are `{detail, code}` (codes listed below).

`Batch` is `{id, hotel_slug, kind, source_code, ota_code, file_id, filename,
file_format, encoding, status, mapping_template_id, mapping {canonical:
header}, transforms {canonical: {date_fmt|decimal_sep|sign_flip|
pct_to_ratio}}, mapping_source template|alias|ai|manual, headers[],
header_row, header_fingerprint, sheet_name, period_start, period_end,
period_month, doc_no, currency, row_count, stats{}, printed_total_minor,
parsed_total_minor, total_field, gate_status, gate_blocking, gate_override_by,
gate_override_reason, gate_override_at, via WEB|EMAIL|API|DEMO|PUBLIC_AUDIT,
auto_commit, uploaded_by, supersedes_batch_id, superseded_by, committed_by,
committed_at, commit_result, errors[], has_suggestion, created_at,
updated_at, row_counts? {OK|WARN|ERROR|DUP|SKIP: n}}`. Status is one of
UPLOADED, MAPPING_REQUIRED, EXTRACTING, REVIEW, COMMITTED, SUPERSEDED, FAILED.

| Method and path | W | Body / query | Response |
|---|---|---|---|
| GET `/sources` | | | `{sources: [{source_code, label, kind, ota_code, unverified}], kinds: [{kind, fields: [{name, type, required}], total_field, committable}]}` |
| POST `/batches` | W | `{file_id, kind?, source_code?, ota_code? BOOKING\|AGODA\|EXPEDIA, period_month? (TRIAL_BALANCE: required), currency?, sheet_name?, header_row?}` | 201 `Batch` (processed synchronously), 200 `Batch` when the bytes were imported before |
| GET `/batches` | | `?kind&status&limit(1-200, 50)&before_id` | `Batch[]` newest first |
| GET `/batches/{id}` | | | `Batch` with `row_counts` |
| GET `/batches/{id}/rows` | | `?status&limit(1-1000, 200)&offset` | `{batch_id, total, rows: [{id, row_no, raw{}, canonical{}, status, messages[], page, line_ref, edited_by, edited_at}]}` |
| GET `/batches/{id}/mapping` | | | `{batch_id, kind, source_code, headers, mapping, transforms, mapping_source, suggestion, fields, missing[], samples[<=5, masked], unverified_aliases}` |
| PUT `/batches/{id}/mapping` | W | `{mapping {canonical: header}, transforms?, save_template? (true)}` | `Batch` restaged from the file, `mapping_source` manual; saves a template |
| POST `/batches/{id}/mapping/suggest` | W | | `Batch` with `has_suggestion`; 409 `ai_unavailable` / `not_tabular` |
| POST `/batches/{id}/options` | W | `{sheet_name?, header_row?}` | `Batch` re-run from its file |
| POST `/batches/{id}/reprocess` | W | | `Batch` re-run (saved template, alias map, AI) |
| PATCH `/batches/{id}/rows/{row_id}` | W | `{values? {canonical: raw value}, skip? bool}` (one required, else 400) | `{row, batch}`; totals and gate recomputed |
| POST `/batches/{id}/gate-override` | W | `{reason (3-1000)}` | `Batch` with `gate_blocking` false (audited) |
| POST `/batches/{id}/commit` | W | `{skip_errors? false, options? {} (handed to the kind's handler, <= 4000 chars of JSON, else 400 `options_too_large`), dry_run? false}` | `Batch` COMMITTED with `commit_result`; with `dry_run`, the `Batch` still in REVIEW plus `dry_run: true` and the would-be `commit_result` |
| DELETE `/batches/{id}` | W | | 204; uncommitted batches only (409 `batch_closed`) |
| GET `/templates` | | `?include_inactive` | `[{id, hotel_slug, kind, source_code, header_fingerprint, headers, mapping, transforms, origin, confirmed_by, confirmed_at, version, active, uses, last_used_at, created_at}]` (own and global) |
| DELETE `/templates/{id}` | W | | `{ok: true}` (deactivates; a hotel cannot retire a global one: 404) |
| GET `/inbound-address` | | | `{address: "<alias>+ingest@in.clefs.world" \| null, hints: [source_code]}` |
| GET `/email-senders` | | | `{email_senders: [str]}` (addresses and `@domain`s whose mail may auto-commit) |
| PUT `/email-senders` | `core.settings.manage` | `{email_senders: [str] (max 50)}` | `{email_senders}` lowercased, deduped; 400 `invalid_sender`; audited as `core.module_settings.update` on `hotel_module_settings[ingest]` |

Error codes: 400 `invalid_field`, `invalid_mapping`, `invalid_row_edit`,
`invalid_value`, `mapping_incomplete`, `not_tabular`, `period_month_required`,
`reason_required`, `source_kind_mismatch`, `unknown_kind`, `unknown_sheet`,
`unknown_source`, `unreadable_file`; 403 `forbidden`; 404 `batch_not_found`,
`row_not_found`, `template_not_found`; 409 `ai_unavailable`,
`already_committed`, `batch_closed`, `gate_blocked`, `gate_not_blocking`,
`no_commit_handler`, `not_ready`, `not_tabular`, `nothing_to_commit`,
`rows_have_errors`; 413 `too_many_pages`, `too_many_rows`, `pdf_too_large`;
415 `unsupported_format`; 422 `no_header_row`, `pdf_no_text`,
`xml_not_supported`. Processing errors do not fail the request: the batch
goes FAILED with `errors: [{code, detail}]`.

## Inbound email

Statements forwarded to `<hotels.inbound_email_alias>+ingest[-<hint>]@in.clefs.world`
arrive through the foundation webhook `POST /webhooks/email/resend` (Svix
signature, `INBOUND_EMAIL_ENABLED`, MessageID dedupe, attachments stored as
files; see CONTRACTS `clefs.notify`). `inbound.on_ingest_email`
(`@notify.on_inbound_email("ingest")`) turns each importable attachment
(CSV, TSV, TXT, XLSX, XLS, PDF, ICS, XML) into a batch with `via=EMAIL`,
`auto_commit` set by sender trust (below), and queues task `ingest.process` so Resend gets its
200 before any AI call. The message is handled when at least one batch was
created or already existed.

- `<hint>` names a source (`booking_res_statement`, `pms_folio`) or a kind
  (`occupancy`, `pms_reservations`); without one, table files are recognised
  by their headers (`aliases.detect_source`) and PDFs from an OTA sender
  domain (booking.com, agoda.com, expedia.com, expediagroup.com:
  UNVERIFIED) are taken as that OTA's statement.
- Sender trust (`inbound.may_auto_commit`): the alias is guessable, so a
  mailed batch gets `auto_commit=true` only when the From domain is proven
  (DMARC pass, or an SPF or DKIM pass whose domain aligns with From;
  relaxed alignment) and the address is vouched for (an active member of an
  active user, or on the hotel's `GET|PUT /email-senders` list). Anything
  else still becomes a batch, but a person commits it. The verdicts come
  only from `notify.inbound.trusted_headers`: a sender can write their own
  `Authentication-Results: x; dmarc=pass`, so a verdict counts only when
  exactly one copy names our verifier (`INBOUND_EMAIL_SPF_VERIFIERS`,
  `INBOUND_EMAIL_AUTHSERV_IDS`). With Resend that is the stamp the inbound
  converter writes from Resend's API; it names no domain, so in practice a
  DMARC pass is what proves From.
- Auto-commit happens only when the batch asked for it, a saved template
  mapped the file, the gate does not block, no row is in error and a
  handler exists; otherwise the batch waits in REVIEW for a person.

## Alias maps (`aliases/*.yaml`)

Every file is `{source_code (= file name), label, kind, ota_code?,
unverified: true, detect: [terms], fields: {canonical: [aliases]},
transforms: {canonical: {date_fmt|decimal_sep|sign_flip|pct_to_ratio}}}`,
validated at import against the kind schemas (a typo fails the tests, not an
upload). `generic.yaml` holds per-kind spellings every source also accepts.
All aliases are UNVERIFIED guesses to be checked against real exports.

| Source | Kind |
|---|---|
| `booking_res_statement` | OTA_STATEMENT (Booking.com reservation statement) |
| `booking_payouts` | OTA_PAYOUT (Booking.com payouts) |
| `agoda_finance` | OTA_STATEMENT (Agoda finance report) |
| `expedia_reservations` | OTA_STATEMENT (Expedia Partner Central reservations) |
| `pms_reservations` | PMS_RESERVATIONS |
| `pms_folio` | PMS_FOLIO |
| `pms_occupancy` | OCCUPANCY |
| `issuance` | ISSUANCE |
| `booking_engine_attribution` | BOOKING_ENGINE_ATTRIBUTION |
| `pl_statement` | PL_STATEMENT |
| `trial_balance` | TRIAL_BALANCE |
| `generic` | every kind (fallback) |

## Kinds and canonical rows

`kinds.SCHEMAS` is the source of truth (`GET .../sources` serves it). Money
fields end in `_minor` (integer minor units of the batch currency, the
hotel's currency unless the file or upload says otherwise). D4 additions:

- `PL_STATEMENT`: `{code?, name, group_raw, type_raw, section, period_month,
  amount_minor}`, income positive, expenses negative. A sheet with several
  month columns ("Jan 2025", "2025-02", ...) becomes one row per account per
  month; `amount_minor` then needs no mapping.
- `TRIAL_BALANCE`: `{code?, name, group_raw?, type_raw?, debit_minor,
  credit_minor}` for the `period_month` given at upload (required).
- `BOOKING_ENGINE_ATTRIBUTION`: `{booking_ref, utm_source, utm_medium,
  utm_campaign, utm_term, utm_content, promo_code, landing_url}`.

## Python API (`from clefs.core.ingest import ...`)

- `create_batch(cur, hotel_slug, *, file_id, kind=None, source_code=None,
  ota_code=None, via="WEB", uploaded_by=None, period_month=None,
  currency=None, options=None, auto_commit=False, process=True,
  use_ai=True) -> dict` (`created` flag). `hotel_slug=None` is the public
  audit sandbox (02 spec 3.11, `via=PUBLIC_AUDIT`).
- `process_batch(cur, batch_id, *, hotel_slug, use_ai=True)`,
  `confirm_mapping(...)`, `set_options(...)`, `edit_row(...)`,
  `get_batch`, `list_batches`, `list_rows`, `row_counts`, `committable_rows`.
- `commit_batch(cur, hotel_slug, batch_id, *, actor=None,
  skip_errors=False)`, `maybe_auto_commit`, `override_gate`,
  `discard_batch`, `redact_contacts`.
- `register_commit_handler(kind, fn)`, `unregister_commit_handler(kind)`
  (tests), `commit_handler(kind)`, `registered_handlers()`,
  `schema_for(kind)`, `KINDS`, `SCHEMAS`.
- `sniff_file(data, *, filename=None, content_type=None, sheet_name=None,
  header_row=None) -> Table`, `normalize_header`, `header_fingerprint`,
  `detect_source`, `resolve_aliases(headers, kind, source_code)`,
  `load_maps`, `find_template`, `save_template`, `list_templates`,
  `suggest_mapping` (AI; None when AI is off or fails).
- `adapters.pint_ae.parse_ubl(xml)`: PINT AE (UAE e-invoicing, 2027) stub;
  raises NotImplementedError and documents the UBL mapping. An XML upload
  goes FAILED with `xml_not_supported`.

## Jobs

- `ingest.process` (task): runs a queued batch (email path), links
  supersede, tries auto-commit.
- `ingest.sweep` (daily 03:45 UTC, platform-wide): stale contact redaction,
  staged-row retention, sandbox cleanup.
