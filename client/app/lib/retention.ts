/**
 * Default retention, as published on /security and the guest privacy notice.
 *
 * WHY a copy of the server defaults instead of an API call: /security is a
 * public, crawlable page with no hotel context, and it states the DEFAULTS a
 * hotel starts with. The source of truth is server/clefs/modules/trust/
 * retention.py (BUILTIN) plus the lead-upload class; a hotel's owner can change
 * keep_days within the bounds shown here, and the console shows the hotel's
 * actual policy (GET /hotels/{slug}/trust/retention). Change both in one commit.
 *
 * The nightly purge runs at 02:30 in each hotel's own time zone (trust
 * retention.py, jobs.daily_at) and logs counts only, never contents
 * (trs_deletion_log). Job completion photos are Service's: a storage rule
 * deletes them after 7 days (service/proof.py KEEP_DAYS).
 */

// ─── Types ───────────────────────────────────────────────────────────────────

export type RetentionAction = "delete" | "anonymize";

export type RetentionRule = {
  data_class: string;
  label: string;
  /** Default keep period in days; null = kept until a stated event. */
  keep_days: number | null;
  /** Plain-language period for tables ("12 months", "Until consent is withdrawn"). */
  keep: string;
  action: RetentionAction;
  min_keep_days?: number;
  max_keep_days?: number;
  note?: string;
};

// ─── Defaults ────────────────────────────────────────────────────────────────

export const RETENTION: RetentionRule[] = [
  {
    data_class: "transcripts", label: "Guest assistant conversations",
    keep_days: 365, keep: "12 months", action: "delete",
  },
  {
    data_class: "request_free_text", label: "What guests wrote in service requests",
    keep_days: 365, keep: "12 months, then anonymized", action: "anonymize",
    note: "The request stays for service statistics; only the guest's words are removed.",
  },
  {
    data_class: "call_audio", label: "Phone call audio (only if recording is switched on)",
    keep_days: 30, keep: "30 days", action: "delete", max_keep_days: 90,
  },
  {
    data_class: "proof_photos", label: "Photos staff take to show a job is done",
    keep_days: 7, keep: "7 days", action: "delete", min_keep_days: 7, max_keep_days: 7,
    note: "Kept in Cloud Storage in India and deleted by a storage rule; a hotel cannot keep them longer.",
  },
  {
    data_class: "guest_profiles", label: "Guest profiles",
    keep_days: null, keep: "Until consent is withdrawn, or 24 months without a stay", action: "delete",
  },
  // The server's public_audit.RETENTION: the daily purge deletes the file, its
  // rows and the report 30 days after the upload. The 90 days here came from
  // an early spec and contradicted the audit page and the home FAQ.
  {
    data_class: "lead_upload", label: "Files uploaded for a free OTA audit",
    keep_days: 30, keep: "30 days", action: "delete",
  },
  {
    data_class: "device_session_logs", label: "Staff device shift sessions",
    keep_days: 180, keep: "6 months", action: "delete",
  },
  {
    data_class: "audit_log", label: "Audit log",
    keep_days: 730, keep: "2 years", action: "delete", min_keep_days: 365,
    note: "Removed oldest first, and only after its hash chain is verified.",
  },
  {
    data_class: "deletion_log", label: "Deletion log (counts only)",
    keep_days: 1095, keep: "3 years", action: "delete", min_keep_days: 365,
  },
  {
    data_class: "dsr_requests", label: "Closed privacy requests",
    keep_days: 1095, keep: "3 years", action: "delete", min_keep_days: 365,
  },
];

export const PURGE_SCHEDULE = "Every night at 02:30 hotel time";

export function retentionRule(dataClass: string): RetentionRule | undefined {
  return RETENTION.find((r) => r.data_class === dataClass);
}
