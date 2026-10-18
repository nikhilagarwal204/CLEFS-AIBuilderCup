/* ═══════════════════════════════════════════════════════════════════════
   Wire types for core/ingest (server/clefs/core/ingest/router.py) and the
   small pure helpers every ingest surface shares. Shapes mirror the
   router's batch_view / row_view / get_mapping payloads exactly; anything
   the server may omit is optional so an older API never crashes a screen.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Enums (mirror kinds.py) ───

export type BatchStatus =
  | "UPLOADED" | "MAPPING_REQUIRED" | "EXTRACTING" | "REVIEW" | "COMMITTED" | "SUPERSEDED" | "FAILED";
export type RowStatus = "OK" | "WARN" | "ERROR" | "DUP" | "SKIP";
/** "n/a" for files without a printed total; mismatch and missing block a commit. */
export type GateStatus = "ok" | "mismatch" | "missing" | "n/a";

export const OPEN_STATUSES: readonly BatchStatus[] = ["UPLOADED", "MAPPING_REQUIRED", "EXTRACTING", "REVIEW"];
export const GATE_BLOCKING: readonly string[] = ["mismatch", "missing"];

// ─── Payloads ───

export interface KindField {
  name: string;
  type: string;
  required: boolean;
}

export interface KindInfo {
  kind: string;
  fields: KindField[];
  total_field: string | null;
  /** False while no module has registered a commit handler for the kind. */
  committable: boolean;
}

export interface SourceInfo {
  source_code: string;
  label: string;
  kind: string;
  ota_code: string | null;
  /** The alias map is a best guess nobody has checked on a real export yet. */
  unverified: boolean;
}

export interface SourcesResponse {
  sources: SourceInfo[];
  kinds: KindInfo[];
}

export interface BatchError {
  code?: string;
  message?: string;
  detail?: string;
  [k: string]: unknown;
}

export interface Batch {
  id: number;
  hotel_slug: string | null;
  kind: string;
  source_code: string | null;
  ota_code: string | null;
  file_id: string | null;
  filename: string | null;
  file_format: string | null;
  status: BatchStatus;
  mapping: Record<string, string> | null;
  transforms: Record<string, Record<string, unknown>> | null;
  mapping_source: string | null;
  headers: string[] | null;
  header_row: number | null;
  sheet_name: string | null;
  period_start: string | null;
  period_end: string | null;
  currency: string | null;
  row_count: number | null;
  stats: { rows?: Partial<Record<RowStatus, number>>; [k: string]: unknown } | null;
  printed_total_minor: number | null;
  parsed_total_minor: number | null;
  total_field: string | null;
  gate_status: GateStatus | null;
  gate_override_by: number | null;
  gate_override_reason: string | null;
  gate_override_at: string | null;
  via: string | null;
  supersedes_batch_id: number | null;
  superseded_by: number | null;
  committed_at: string | null;
  commit_result: Record<string, unknown> | null;
  errors: (BatchError | string)[] | null;
  created_at: string;
  updated_at: string | null;
  has_suggestion?: boolean;
  gate_blocking?: boolean;
  row_counts?: Partial<Record<RowStatus, number>>;
}

export interface ImportRow {
  id: number;
  row_no: number;
  raw: Record<string, unknown>;
  canonical: Record<string, unknown>;
  status: RowStatus;
  messages: string[] | null;
  page: number | null;
  line_ref: string | null;
  edited_by: number | null;
  edited_at: string | null;
}

export interface RowsResponse {
  batch_id: number;
  total: number;
  rows: ImportRow[];
}

export interface SuggestedColumn {
  canonical: string;
  header: string;
  transform: Record<string, unknown> | null;
  confidence: number;
}

export interface MappingSuggestion {
  mappings: SuggestedColumn[];
  unmapped: string[];
  mapping: Record<string, string>;
  transforms: Record<string, Record<string, unknown>>;
  model_unmapped?: string[];
}

export interface MappingInfo {
  batch_id: number;
  kind: string;
  source_code: string | null;
  headers: string[];
  mapping: Record<string, string>;
  transforms: Record<string, Record<string, unknown>>;
  mapping_source: string | null;
  suggestion: MappingSuggestion | null;
  fields: KindField[];
  missing: string[];
  samples: Record<string, unknown>[];
  unverified_aliases: boolean;
}

export interface MappingTemplate {
  id: number;
  hotel_slug: string | null;
  kind?: string;
  source_code: string;
  header_fingerprint?: string;
  version?: number;
  active?: boolean;
  confirmed_at?: string | null;
  last_used_at?: string | null;
  [k: string]: unknown;
}
