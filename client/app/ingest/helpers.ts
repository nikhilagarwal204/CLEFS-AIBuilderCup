/* Pure helpers shared by the ingest components and the pages that host
   them: labels, tones, the API paths and error text. No React here, so the
   settings pages and Profit Guard can import it without pulling in UI. */

import { ApiError, hotelPath } from "../lib/api";
import type { Tone } from "../ui";
import type { Batch, BatchStatus, GateStatus, RowStatus } from "./types";

// ─── Paths ───

export const ingestPath = (slug: string, p = "") => hotelPath(slug, `/ingest${p}`);
export const batchPath = (slug: string, id: number, p = "") => ingestPath(slug, `/batches/${id}${p}`);

// ─── Labels and tones ───

export const STATUS_LABEL: Record<BatchStatus, string> = {
  UPLOADED: "Reading",
  MAPPING_REQUIRED: "Needs mapping",
  EXTRACTING: "Extracting",
  REVIEW: "Ready to review",
  COMMITTED: "Imported",
  SUPERSEDED: "Replaced",
  FAILED: "Failed",
};

export const STATUS_TONE: Record<BatchStatus, Tone> = {
  UPLOADED: "info",
  MAPPING_REQUIRED: "warn",
  EXTRACTING: "info",
  REVIEW: "gold",
  COMMITTED: "ok",
  SUPERSEDED: "neutral",
  FAILED: "bad",
};

export const ROW_TONE: Record<RowStatus, Tone> = { OK: "ok", WARN: "warn", ERROR: "bad", DUP: "neutral", SKIP: "neutral" };
export const ROW_LABEL: Record<RowStatus, string> = { OK: "OK", WARN: "Check", ERROR: "Error", DUP: "Duplicate", SKIP: "Skipped" };

export const GATE_LABEL: Record<GateStatus, string> = {
  ok: "Lines match the printed total",
  mismatch: "Lines do not add up to the printed total",
  missing: "No printed total found",
  "n/a": "No printed total to check",
};

/** Human names for kinds; unknown kinds fall back to a title-cased code. */
export const KIND_LABEL: Record<string, string> = {
  OTA_STATEMENT: "OTA statement",
  OTA_PAYOUT: "OTA payout",
  VCC_REPORT: "Virtual card report",
  CREDIT_NOTE: "Credit note",
  PMS_RESERVATIONS: "PMS reservations",
  PMS_FOLIO: "PMS folio charges",
  BANK: "Bank statement",
  CONTRACT_PDF: "Contract",
  COMP_RATES: "Competitor rates",
  ISSUANCE: "Amenity issuance",
  OCCUPANCY: "Daily occupancy",
  ICS: "Calendar (ICS)",
  SUPPLIER_INVOICE: "Supplier invoice",
  PL_STATEMENT: "P&L statement",
  TRIAL_BALANCE: "Trial balance",
  BOOKING_ENGINE_ATTRIBUTION: "Booking engine attribution",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? titleCase(kind);
}

/** "check_in" -> "Check in", "gross_minor" -> "Gross" (minor units are a storage detail). */
export function fieldLabel(name: string): string {
  return titleCase(name.replace(/_minor$/, "").replace(/_pct$/, " %"));
}

export function titleCase(code: string): string {
  const s = code.replace(/_/g, " ").toLowerCase().trim();
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

// ─── Batch state ───

export function isOpen(b: Pick<Batch, "status">): boolean {
  return b.status === "UPLOADED" || b.status === "MAPPING_REQUIRED" || b.status === "EXTRACTING" || b.status === "REVIEW";
}

export function isPdf(b: Pick<Batch, "file_format">): boolean {
  return (b.file_format ?? "").toLowerCase() === "pdf";
}

export function gateBlocking(b: Batch): boolean {
  if (typeof b.gate_blocking === "boolean") return b.gate_blocking;
  return (b.gate_status === "mismatch" || b.gate_status === "missing") && !b.gate_override_at;
}

export function counts(b: Batch): Partial<Record<RowStatus, number>> {
  return b.row_counts ?? b.stats?.rows ?? {};
}

export function countOf(b: Batch, s: RowStatus): number {
  return counts(b)[s] ?? 0;
}

/** Rows a commit would write: OK and WARN (ERROR only with skip_errors, never DUP or SKIP). */
export function committableCount(b: Batch): number {
  return countOf(b, "OK") + countOf(b, "WARN");
}

/** First readable error on a FAILED batch. */
export function batchError(b: Batch): string | null {
  for (const e of b.errors ?? []) {
    if (typeof e === "string" && e) return e;
    if (e && typeof e === "object") {
      const m = e.message ?? e.detail ?? e.code;
      if (typeof m === "string" && m) return m;
    }
  }
  return null;
}

export function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return String((err as Error)?.message || err || "Something went wrong");
}

/** A cell value for display: objects as JSON, null as an em-less dash. */
export function cellText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "-";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}


// Pure (no imports) so node --test can load it; see summary.ts.
export { commitSummary } from "./summary";
