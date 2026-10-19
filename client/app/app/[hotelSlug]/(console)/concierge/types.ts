/* ═══════════════════════════════════════════════════════════════════════
   Concierge console types: the JSON the concierge and voice console routes
   return (server/clefs/modules/concierge/README.md, voice/README.md). Kept
   in one file so every page reads the same shapes; nothing here is runtime.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Knowledge base ──────────────────────────────────────────────────────

export type KbKind = "fact" | "faq" | "policy" | "local_place" | "venue_note";
export type KbStatus = "pending" | "approved" | "rejected" | "archived";
export type KbMode = "legacy_text" | "structured";

export interface KbSection {
  id: number;
  key: string;
  title_en: string;
  title_ar: string | null;
  sort: number;
  required: boolean;
  approved: number;
  pending: number;
}

export interface KbImport {
  id: number;
  source: "legacy_text" | "text" | string;
  status: "queued" | "running" | "done" | "failed" | "approved" | string;
  method: string | null;
  text_chars: number | null;
  entries_created: number | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  approved_at: string | null;
}

export interface KbOverview {
  kb_mode: KbMode;
  sections: KbSection[];
  missing_required: string[];
  has_legacy_text: boolean;
  imports: KbImport[];
  open_unknowns: number;
}

export interface KbEntry {
  id: number;
  section_id: number | null;
  import_id: number | null;
  kind: KbKind;
  question_en: string | null;
  answer_en: string | null;
  question_ar: string | null;
  answer_ar: string | null;
  tags: string[] | null;
  fact_key: string | null;
  fact_value: string | null;
  location_slug: string | null;
  source: string | null;
  source_url: string | null;
  source_label: string | null;
  valid_from: string | null;
  valid_to: string | null;
  status: KbStatus;
  version: number;
  approved_by: number | null;
  approved_at: string | null;
  rejected_reason: string | null;
  created_at: string;
  updated_at: string;
}

/** The editable subset sent on POST/PATCH /kb/entries. */
export interface KbEntryDraft {
  section_id: number | null;
  kind: KbKind;
  question_en: string;
  answer_en: string;
  question_ar: string;
  answer_ar: string;
  tags: string[];
  fact_key: string;
  fact_value: string;
  location_slug: string;
  source_url: string;
  source_label: string;
  valid_from: string;
  valid_to: string;
}

export interface KbRevision {
  id: number;
  entry_id: number;
  version: number;
  change: string;
  snapshot: Partial<KbEntry>;
  actor_type: string | null;
  actor_id: string | number | null;
  at: string;
}

export interface KbUnknown {
  id: number;
  title: string;
  samples: string[];
  languages: string[];
  count: number;
  count_7d: number;
  first_at: string;
  last_at: string;
  status: "open" | "answered" | "dismissed" | "merged";
  kb_entry_id: number | null;
}

/** POST /kb/test (requested of the concierge server part; see lib.ts). */
export interface KbTestResult {
  text: string;
  prompt_version?: string;
  kb_entry_ids?: number[];
  cited?: number[];
  tool_calls?: { name: string; status?: string }[];
  /** Whether entry_id (approved only) was in the prompt; null without one. */
  entry_included?: boolean | null;
}

// ─── Prompt preview ──────────────────────────────────────────────────────

export type Surface = "web_room" | "web_location" | "phone" | "text_fallback" | "eval" | "whatsapp";

export interface PromptPreview {
  surface: Surface;
  prompt_version: string;
  model: string;
  voice: string | null;
  kb_mode: KbMode;
  greeting_text: string | null;
  instructions: string;
  tools: { type?: string; name?: string; description?: string; parameters?: unknown }[];
  kb_entry_ids: number[];
}

// ─── Venues, settings, transcripts ───────────────────────────────────────

export interface Venue {
  slug: string;
  name: string;
  type: string;
  custom_instructions: string | null;
  created_at?: string;
}

export interface Chip { key: string; label: string }

export interface ConciergeSettings {
  kb_mode?: KbMode | null;
  legacy_qr_personalization?: boolean;
  daily_session_cap?: number;
  voice?: string | null;
  chips?: Record<string, Chip[]>;
  [key: string]: unknown;
}

export interface ModuleSettingsView<S> {
  module: string;
  enabled: boolean;
  settings: S;
  updated_at: string | null;
}

export interface LogRow {
  id: number;
  room_number: string | null;
  session_id: string | null;
  role: string;
  message: string | null;
  timestamp: number;
}

// ─── Voice (in-room phone AI) ────────────────────────────────────────────

export type PbxKind = "grandstream_ucm" | "3cx" | "asterisk" | "freeswitch" | "fxo_gateway" | "bridge" | "other";

export interface AfterHours { enabled: boolean; start: string | null; end: string | null }

export interface VoiceLine {
  id: number;
  label: string;
  trunk_id: string | null;
  sip_uri: string | null;
  has_secret: boolean;
  line_secret_hint: string | null;
  allowed_sources: string[];
  pbx_kind: PbxKind;
  caller_id_mode: "extension" | "none";
  front_desk_uri: string | null;
  outside_line_prefix: string | null;
  default_language: string;
  model: string | null;
  voice: string | null;
  max_call_s: number;
  max_concurrent: number;
  tool_transport: "mcp" | "sideband";
  enabled: boolean;
  after_hours: AfterHours | Record<string, never>;
  extension_rule: ExtensionRule | null;
  extensions?: number;
  created_at: string;
  updated_at: string;
}

/** POST /voice/lines and POST /voice/lines/{id}/rotate-secret: the secret is shown once. */
export interface LineWithSecret { line: VoiceLine; secret: string }

export interface VoiceExtension {
  id: number;
  voice_line_id: number;
  extension: string;
  room: string | null;
  location_slug: string | null;
  kind: "room" | "location" | "staff" | "other";
  source: "manual" | "csv" | "rule";
  created_at: string;
}

export interface ExtensionRule {
  prefix: string;
  suffix: string;
  strip_room_prefix: string;
  use_room_extension: boolean;
}

export interface RuleResult {
  rule: ExtensionRule;
  rooms: number;
  would_map?: number;
  created?: number;
  updated?: number;
  preview: { room: string; extension: string }[];
}

export interface CsvResult { imported: number; skipped: { line: number; extension: string | null; reason: string }[]; skipped_count: number }

export interface WebhookEvent {
  webhook_id: string;
  source: string;
  event_type: string | null;
  call_id: string | null;
  voice_line_id: number | null;
  trunk_id: string | null;
  outcome: string;
  detail: Record<string, unknown>;
  received_at: string;
}

export type CallStatus = "incoming" | "rejected" | "accepting" | "accepted" | "active" | "ended" | "failed" | "stale";

export interface VoiceCall {
  id: number;
  voice_line_id: number | null;
  provider_call_id: string;
  status: CallStatus;
  reject_code: number | null;
  reject_reason: string | null;
  from_user: string | null;
  extension_matched: boolean;
  room: string | null;
  location_slug: string | null;
  room_confirmed_by: "extension" | "spoken" | "none";
  tool_transport: string | null;
  session_id: string | null;
  language: string | null;
  prompt_version: string | null;
  started_at: string;
  accepted_at: string | null;
  ended_at: string | null;
  duration_s: number | null;
  end_reason: string | null;
  transferred: boolean;
  requests_created: number;
  emergencies: number;
}

export interface CallTurn { role: string; message: string | null; timestamp: number | string }

export interface CallToolCall {
  id: number;
  name: string;
  transport: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown> | null;
  created_at?: string;
}

/** GET /voice/calls/{id}: the call plus its (role-masked) transcript and tool calls. */
export interface VoiceCallDetail extends VoiceCall {
  transcript?: CallTurn[] | null;
  tool_calls?: CallToolCall[];
}

// ─── Transcripts (GET /concierge/transcripts, masked by role on the server) ──

export type TranscriptSource = "web_room" | "web_location" | "phone" | "text_fallback" | "whatsapp" | "legacy";
export type SafetyLevel = "emergency" | "sensitive" | "handoff" | "none";
/** What the viewer may read of the words (the pii matrix cell, decided by the server). */
export type TranscriptVisibility = "full" | "none";

export interface TranscriptSummary {
  id: string;
  session_id: string | null;
  room: string | null;
  source: TranscriptSource;
  language: string | null;
  started_at: string | null;
  last_at: string | null;
  turns: number;
  guest_turns: number;
  safety_level: SafetyLevel;
  /** First guest message, contacts shortened; null when the role may not read it. */
  preview: string | null;
}

export interface TranscriptPage {
  conversations: TranscriptSummary[];
  next_before: string | null;
  visibility: TranscriptVisibility;
}

export interface TranscriptTurn {
  id: number;
  role: string;
  message: string | null;
  language: string | null;
  safety_level: SafetyLevel | null;
  source: string | null;
  item_id: string | null;
  at: string | null;
}

export interface TranscriptTool {
  name: string;
  status: string;
  outcome: string | null;
  request_ref: string | null;
  order_ref: string | null;
  at: string;
}

export interface TranscriptDetail {
  conversation: TranscriptSummary & {
    surface: string | null;
    verified: boolean | null;
    prompt_version: string | null;
    ended_at: string | null;
    end_reason: string | null;
  };
  turns: TranscriptTurn[];
  tools: TranscriptTool[];
  visibility: TranscriptVisibility;
}

// ─── Go-live (GET|PUT /concierge/live, trust.api.readiness) ──────────────

export interface ReadinessCheck { key: string; label?: string; ok: boolean; detail?: string | null }

export interface Readiness {
  ready: boolean;
  overridden?: boolean;
  checks?: ReadinessCheck[];
  run_id?: string | number | null;
  prompt_version?: string | null;
  evaluated_at?: string | null;
  /** trust.api.readiness: the active owner override (trs_readiness_overrides row), when one is in force. */
  override?: { id: number; reason: string | null; failing?: unknown; run_id?: number | null; created_by?: number | null;
    created_at: string; expires_at: string } | null;
}

export interface LiveState {
  live: boolean;
  /** "demo": the synthetic demo hotel, switched on by the demo seed without the gate. */
  source: "setting" | "grandfathered" | "demo" | "default";
  readiness: Readiness;
}
