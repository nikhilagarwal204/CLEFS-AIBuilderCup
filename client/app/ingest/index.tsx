/* Reusable ingest components (core/ingest client). Hosts: settings/imports
   (PMS exports), Profit Guard imports (OTA statements), Finance (P&L). Each
   host renders <IngestStyles /> once and composes these pieces.
   No "use client" here and no `export *`: every component file carries its
   own directive, and Next rejects `export *` inside a client boundary. */

import { ingStyles } from "./styles";

export function IngestStyles() {
  return <style dangerouslySetInnerHTML={{ __html: ingStyles }} />;
}

export { ingStyles } from "./styles";
export { UploadDropzone, DEFAULT_ACCEPT, type UploadDropzoneProps } from "./UploadDropzone";
export { MappingReview, type MappingReviewProps } from "./MappingReview";
export { PdfReview, type PdfReviewProps } from "./PdfReview";
export { RowsTable, type RowsTableProps } from "./RowsTable";
export { BatchList, type BatchListProps } from "./BatchList";
export { BatchDetail, useCanImport, type BatchDetailProps } from "./BatchDetail";
export { parseCsv, sniffDelimiter, normalizeHeader, findColumn, type CsvTable } from "./csv";
export {
  ingestPath, batchPath, STATUS_LABEL, STATUS_TONE, ROW_TONE, ROW_LABEL, GATE_LABEL, KIND_LABEL, kindLabel, fieldLabel,
  titleCase, isOpen, isPdf, gateBlocking, counts, countOf, committableCount, batchError, errorText, cellText,
} from "./helpers";
export {
  OPEN_STATUSES, GATE_BLOCKING,
  type Batch, type BatchError, type BatchStatus, type GateStatus, type ImportRow, type KindField, type KindInfo,
  type MappingInfo, type MappingSuggestion, type MappingTemplate, type RowStatus, type RowsResponse,
  type SourceInfo, type SourcesResponse, type SuggestedColumn,
} from "./types";
