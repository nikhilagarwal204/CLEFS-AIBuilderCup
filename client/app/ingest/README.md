# `client/app/ingest`: reusable import components

Client side of `core/ingest` (server `clefs/core/ingest/router.py`). The Settings > Imports page hosts these components for PMS exports. Profit Guard (OTA statements) and Finance (P&L, trial balance) can host them for their own kinds. The folder has no `page.tsx`, so Next never routes it.

## Use

```tsx
import { IngestStyles, UploadDropzone, BatchList, BatchDetail, type Batch } from "../../ingest";

<IngestStyles />                                   // once per surface (the ing- stylesheet)
<UploadDropzone kinds={["OTA_STATEMENT", "OTA_PAYOUT"]} purpose="profit.statement" onBatch={b => open(b)} />
<BatchList kinds={["OTA_STATEMENT"]} refreshKey={n} onOpen={open} />
<BatchDetail batchId={id} onChange={refresh} onClosed={close} />
```

| Export | What it does |
|---|---|
| `UploadDropzone` | Kit `FileDrop` → `uploadFile` (Files contract, ticketed, with progress) → `POST /ingest/batches`. The kind and source pickers are optional; the source list comes from `GET /ingest/sources`. `onBatch(batch, {existing})` is called with `existing` true when the same bytes were imported before. |
| `BatchList` | `GET /ingest/batches`, newest first, showing status, row counts and period. Several kinds are filtered in the browser, because the route takes one kind. |
| `BatchDetail` | Shows one batch through its lifecycle. It polls while the status is UPLOADED or EXTRACTING. It shows `MappingReview` for MAPPING_REQUIRED, and `RowsTable` or `PdfReview` for REVIEW. It holds the import button (with `skip_errors`) and the discard button. |
| `MappingReview` | `GET /mapping` shows one row per canonical field, with the chosen column, the AI confidence (`ConfidencePill`), the transform and the first sample value. Below that come the 5 sample rows, masked by role on the server. Its actions are "Ask AI" (`POST /mapping/suggest`), "Use AI proposal", re-reading the header row (`POST /options`), and save (`PUT /mapping`, which stores a template). |
| `RowsTable` | `GET /rows`, with a status filter and paging. Money values are shown and typed in major units. Row edits call `PATCH /rows/{id}` with `{values}` or `{skip}`. |
| `PdfReview` | The source PDF (fetched as a blob, because the download route sends `attachment`), the page links for each row, and the printed-total check. When the gate blocks, it offers an override with a reason (`POST /gate-override`, audited). |
| `useCanImport()` | Mirrors the server's write rule: `core.settings.manage`, or the finance or revenue role. |
| `parseCsv`, `findColumn` | A small RFC 4180 reader for money-free lists read in the browser, such as the room register. |
| `helpers`, `types` | Labels, tones, paths and wire types that mirror `batch_view` / `row_view` / `get_mapping`. |

## Routes consumed

All routes are under `/hotels/{slug}/ingest`. Reads need `core.settings.view`. Writes need `core.settings.manage`, or the finance or revenue role.

- `GET /sources`
- `POST /batches`, `GET /batches`, `GET /batches/{id}`, `DELETE /batches/{id}`
- `GET /batches/{id}/rows`, `PATCH /batches/{id}/rows/{row_id}`
- `GET /batches/{id}/mapping`, `PUT /batches/{id}/mapping`, `POST /batches/{id}/mapping/suggest`
- `POST /batches/{id}/options`, `POST /batches/{id}/reprocess`
- `POST /batches/{id}/gate-override`, `POST /batches/{id}/commit`
- `GET /templates`, `DELETE /templates/{id}`
- `GET /inbound-address`

Outside that prefix, the components also call `GET /hotels/{slug}/files/{id}` (the PDF preview) and the Files upload.

## Settings pages in this lane

- `settings/imports`: PMS exports. It covers PMS_RESERVATIONS, PMS_FOLIO, OCCUPANCY and BOOKING_ENGINE_ATTRIBUTION, plus the remembered layouts and the email-in address.
- `settings/rooms`: the room register (`GET|PUT /hotels/{slug}/rooms`), shown by floor, with single and range adds and a CSV import that runs in the browser.
- `settings/tax`: the tax profile, the 2026 announcement toggles with a banner, the ConventionPreview for A/B/C, and the read-only fee rules. It calls Profit Guard's tax routes (`/hotels/{slug}/profit/tax/{fee-rules?on,profile,overrides}`, 02 spec 3.8). A 404 renders a "served by Profit Guard" empty state. The preview's maths, in `settings/tax/convention.ts`, mirrors `core/tax/calc.py` exactly using BigInt. It reproduces the goldens A 396.49/39.65/30.53/23.33, B 398.86/39.89/27.92/23.33 and C 401.65/40.16/28.11/20.08.
