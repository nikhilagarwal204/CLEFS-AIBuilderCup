# Profit Guard console (`/app/{slug}/profit`)

Client of `server/clefs/modules/profit_guard` (02 spec 3.10). Reads need `profit.view`; every write button is hidden without `profit.manage` (the server stays the judge). Styles are one `pg-` string injected by `layout.tsx`.

| Route | Component | Server routes (prefix `/hotels/{slug}/profit-guard` unless noted) |
|---|---|---|
| `/profit` | `Overview`: KPI tiles (Found, Recovered verified, Open, Due 72 h), `RecoveryFunnel`, leakage by check (`HBarChart`), `DeadlineRail` with `CountdownChip`, `PilotScorecard`, latest ledger events | `GET /ledger`, `GET /deadlines?days=14`, `GET /pilot-scorecard`, `POST /detectors/run`, `POST /findings/verify` |
| `/profit/findings` | `FindingsTable`: filters (status, check, OTA, class, dates; also `?status&detector&ota&class` in the URL), tick findings of one OTA to draft a packet | `GET /findings`, `POST /disputes` |
| `/profit/findings/[id]` | `FindingDetail`: `CalcTrace` stepper, `EvidenceList` (kit `EvidencePanel`), `FindingActionBar` (Accept, Dispute, Dismiss with reason, Mark recovered, OTA rejected, Reopen), history | `GET /findings/{id}`, `POST /findings/{id}/transition`, `POST /findings/{id}/dismiss`, `POST /disputes` |
| `/profit/disputes` | `DisputesList` | `GET /disputes?status` |
| `/profit/disputes/[id]` | `DisputeDetail`: claims with clause citations, extranet text (copy), email, `PacketActions` (approve, mark sent, record outcome), PDF link | `GET /disputes/{id}`, `POST /disputes/{id}/approve`, `/submitted`, `/outcome`, `GET /disputes/{id}.pdf` |
| `/profit/disputes/[id]/print` | `PrintView`: the server's print HTML in a frame, printed alone (the layout drops header and tabs here) | `GET /disputes/{id}/print` |
| `/profit/imports` | `ImportsView`: upload with kind and OTA, then the shared `client/app/ingest` `BatchList` and `BatchDetail` (MappingReview, PdfReview, commit); `?batch=<id>` opens one | `POST /imports`; core ingest routes for review and commit |
| `/profit/channels` | `ChannelsView`: channels compared, `WaterfallChart` Gross to Net, monthly Net ADR columns | `GET /channel-economics/monthly?from&to`, `POST /channel-economics/rollup` |
| `/profit/vcc` | `VccView`: cards by charge deadline, 30/14/7/1-day tiers, mark charged (full or partial) | `GET /vcc?open=true`, `POST /vcc/{id}/charged` |
| `/profit/tax` | `TaxView`: unverified-rule banner, `TdLedger` (remittance, CSV), ConventionPreview and fee rules reused from Settings > Tax | `GET /tax/td-ledger`, `PUT /tax/td-ledger/{month}`, `GET /tax/td-export.csv`, `POST /tax/checks/run`; `GET /hotels/{slug}/profit/tax/fee-rules`, `/profit/tax/profile` |
| `/profit/contracts` | `ContractsView`: read a PDF into a cited DRAFT, `ContractCard` with clause citations and review flags, `ContractEditor` to confirm, retire | `GET /contracts`, `POST /contracts/extract`, `PUT /contracts/{id}`, `DELETE /contracts/{id}` |
| `/profit/parity` | `ParityView`: `RateEvidenceForm` (screenshot upload, device, login state) and the like-for-like parity grid | `POST|GET /rate-evidence`, `GET /policies` |

- Money is integer minor units end to end (`lib.majorToMinor` parses typed amounts by string); `<Money>` formats in the hotel currency.
- `lib.ts` is pure (type-only imports) and tested by `__tests__/profitLib.test.mjs` (`npm test`, or `node --test profitLib.test.mjs` from that folder).
- Files: screenshots, statements and contracts go through the Files contract first (`purpose` `profit.rate_evidence`, `profit.statement`, `profit.contract`).
