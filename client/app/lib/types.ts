/* ═══════════════════════════════════════════════════════════════════════
   Shared API types for the Clefs console.

   These mirror the server contracts in server/clefs (CONTRACTS.md and
   docs/specs/00-CANON.md). Where the Canon and a design spec disagree, the
   Canon wins, so e.g. Action states follow Canon item 7 and Signal sections
   follow Canon item 6.

   Money on the wire: persisted as BIGINT minor units (fils) + CHAR(3)
   currency, and APIs emit Decimal STRINGS for major-unit amounts. Types below
   carry both forms where the server may send either (`*_minor` numbers and
   Decimal strings); render them through formatMoney / <MoneyText>, never
   with parseFloat arithmetic.

   Time on the wire: legacy endpoints use epoch milliseconds (number); new
   endpoints use ISO-8601 TIMESTAMPTZ strings. `Timestamp` accepts both.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Primitives ──────────────────────────────────────────────────────────

/** A Decimal serialised as a string, e.g. "1234.50". Never a float. */
export type DecimalString = string;

/** ISO-8601 string (new tables) or epoch milliseconds (legacy tables). */
export type Timestamp = string | number;

/** ISO 4217 code, e.g. "AED". */
export type CurrencyCode = string;

// ─── Roles and identity ──────────────────────────────────────────────────

/** Hotel membership roles. `clefs_admin` is NOT a role: it is a user flag. */
export type Role =
  | "owner"
  | "gm"
  | "finance"
  | "revenue"
  | "dept_head"
  | "front_desk"
  | "staff";

export const ROLES: readonly Role[] = [
  "owner", "gm", "finance", "revenue", "dept_head", "front_desk", "staff",
] as const;

export const ROLE_LABELS: Record<Role, string> = {
  owner: "Owner",
  gm: "General manager",
  finance: "Finance",
  revenue: "Revenue",
  dept_head: "Department head",
  front_desk: "Front desk",
  staff: "Staff",
};

export interface User {
  id: number;
  name: string;
  email: string | null;
  is_clefs_admin: boolean;
  phone_e164?: string | null;
  locale?: string | null;
}

export interface Membership {
  hotel_slug: string;
  hotel_name: string;
  role: Role;
}

/** A pending invitation to join a hotel. A manager who adds an email that
 *  already has a Clefs account creates one; only its owner accepts it. */
export interface Invite {
  membership_id: number;
  hotel_slug: string;
  hotel_name: string;
  role: Role;
  invited_at: Timestamp | null;
  invited_by_name?: string | null;
}

/** POST /auth/invites/{membership_id}/accept -> 200 */
export interface InviteAcceptResponse {
  hotel_slug: string;
  memberships: Membership[];
  invites: Invite[];
}

/** POST /auth/login -> 200 */
export interface LoginResponse {
  user: User;
  memberships: Membership[];
  /** Pending invitations to other hotels (empty when there are none). */
  invites?: Invite[];
}

/** GET /auth/me?hotel=<slug> */
export interface Me {
  /** null for anonymous access to a legacy_open_access hotel. */
  user: User | null;
  memberships: Membership[];
  hotel_slug: string | null;
  role: Role | null;
  /** Resolved server-side; the client never duplicates the matrix. */
  permissions: string[];
  /** True when the hotel is served without login (grandfathered pilots). */
  legacy_mode: boolean;
  /** Pending invitations for the signed-in user. */
  invites?: Invite[];
}

/** POST /auth/magic/exchange -> 200 */
export interface MagicExchangeResponse {
  next_path: string;
  user: User;
  hotel_slug: string;
}

export type PrincipalKind = "user" | "device" | "anonymous";

// ─── Hotel context ───────────────────────────────────────────────────────

export type HotelStatus = "active" | "pilot" | "demo" | "archived";

export interface Hotel {
  slug: string;
  name: string;
  city: string | null;
  country: string;
  emirate: string | null;
  timezone: string;
  currency: CurrencyCode;
  locale: string;
  guest_languages: string[];
  /** ISO weekdays, 1 = Monday ... 7 = Sunday. UAE default {6,7} (Sat-Sun). */
  weekend_days: number[];
  /** "HH:MM" or "HH:MM:SS" in hotel-local time. */
  brief_time_local: string;
  star_rating: number | null;
  rooms_count: number | null;
  assistant_name: string | null;
  status: HotelStatus | string;
}

/** GET /hotels/{slug}/context */
export interface HotelContext {
  hotel: Hotel;
  modules_enabled: string[];
  role: Role | null;
  permissions: string[];
  legacy_open_access: boolean;
  principal_kind: PrincipalKind;
}

/** PATCH /hotels/{slug}/settings body: any subset of the editable core columns. */
export type HotelSettingsPatch = Partial<Omit<Hotel, "slug">>;

/** GET/PUT /hotels/{slug}/module-settings/{module} */
export interface ModuleSettings {
  module: string;
  enabled: boolean;
  settings: Record<string, unknown>;
  /** Optional JSON Schema from the module's Pydantic model, for hints. */
  schema?: Record<string, unknown> | null;
  updated_at?: Timestamp | null;
}

// ─── Team and devices ────────────────────────────────────────────────────

/** active: a member; inactive: suspended; invited: waiting for the person to accept. */
export type TeamMemberStatus = "active" | "inactive" | "invited";

/** One row of GET /hotels/{slug}/team. An invited row shows the name the
 *  inviter typed, and null user_id / phone / last_login_at: a hotel never sees
 *  another hotel's data about a person before they accept. */
export interface TeamMember {
  /** membership id (the PATCH/DELETE key) */
  id: number;
  user_id: number | null;
  name: string;
  email: string | null;
  phone: string | null;
  role: Role;
  departments: string[];
  floors: string[];
  is_active: boolean;
  last_login_at?: Timestamp | null;
  status?: TeamMemberStatus;
  invited_at?: Timestamp | null;
}

/** POST /hotels/{slug}/team body. */
export interface TeamMemberCreate {
  email: string;
  name: string;
  phone?: string | null;
  role: Role;
  departments: string[];
  floors: string[];
}

/** PATCH /hotels/{slug}/team/{membership_id} body. */
export type TeamMemberPatch = Partial<Pick<TeamMember, "role" | "departments" | "floors" | "is_active" | "name" | "phone">>;

export type DeviceKind = "floor" | "department" | "desk" | "phone_console" | "personal";

export const DEVICE_KINDS: readonly DeviceKind[] = [
  "floor", "department", "desk", "phone_console", "personal",
] as const;

export interface Device {
  id: number;
  name: string;
  kind: DeviceKind;
  floors: string[];
  departments: string[];
  paired_at?: Timestamp | null;
  last_seen_at?: Timestamp | null;
  revoked_at?: Timestamp | null;
}

/** POST /hotels/{slug}/devices/pairing-codes body. */
export interface PairingCodeCreate {
  name: string;
  kind: DeviceKind;
  floors: string[];
  departments: string[];
}

/** POST /hotels/{slug}/devices/pairing-codes -> 200 */
export interface PairingCode {
  code: string;
  expires_at: Timestamp;
  pair_url: string;
}

/** POST /auth/devices/pair -> 200 */
export interface DevicePairResponse {
  device: Pick<Device, "id" | "name" | "kind" | "floors" | "departments">;
  hotel_slug: string;
}

/** GET /auth/devices/roster -> staff tiles for the device scope. */
export interface RosterTile {
  id: number;
  name: string;
  department: string | null;
  on_shift?: boolean;
  has_pin?: boolean;
}

/** POST /auth/devices/shift/start -> 200 */
export interface ShiftStartResponse {
  session_id: number;
  staff: { id: number; name: string; department: string | null };
  planned_end_at: Timestamp | null;
}

// ─── Realtime and files ──────────────────────────────────────────────────

/** POST /auth/stream-ticket and POST /files/upload-ticket -> 200 */
export interface Ticket {
  /** Path on the API origin including the ticket, e.g. "/hotels/x/stream?t=..." */
  url: string;
  expires_in: number;
}

export type StreamStatus = "live" | "reconnecting" | "polling";

export interface FileRef {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
}

// ─── Signals (Canon item 6) ──────────────────────────────────────────────

export type SignalSection =
  | "money_lost"
  | "money_available"
  | "service_risk"
  | "reviews"
  | "verified"
  | "compliance";

export const SIGNAL_SECTION_LABELS: Record<SignalSection, string> = {
  money_lost: "Money lost",
  money_available: "Money available",
  service_risk: "Service risk",
  reviews: "Reviews",
  verified: "Verified",
  compliance: "Compliance",
};

export type SignalStatus =
  | "open"
  | "surfaced"
  | "decided"
  | "snoozed"
  | "resolved"
  | "expired"
  | "ignored"
  | "superseded"
  | "suppressed";

export type EvidenceKind = "metric" | "row" | "file" | "quote" | "link" | "calc" | "series";

export interface Evidence {
  kind: EvidenceKind;
  label: string;
  value?: unknown;
  /** {"file_id","page"} | {"table","id"} | {"metric","query"} | {"url"} */
  ref?: Record<string, unknown> | null;
}

export interface RecommendedAction {
  /** "<module>.<verb>", a registered action type. */
  type: string;
  label: string;
  params?: Record<string, unknown>;
  owner_role?: Role | string | null;
  due_in_hours?: number | null;
  expected_value?: DecimalString | null;
}

/** Brief cadence (Canon 18). */
export type SignalCadence = "daily" | "weekly" | "monthly";

export const SIGNAL_CADENCES: readonly SignalCadence[] = ["daily", "weekly", "monthly"] as const;

/** Still waiting for a person (the server's OPEN_STATUSES, the list default). */
export const OPEN_SIGNAL_STATUSES: readonly SignalStatus[] = ["open", "surfaced"] as const;

/** Core signal kinds the autonomy dial raises (compliance, weekly). Their
 *  dedupe_key is "<kind>:<action_type>:<autonomy event id>" and subject_id is
 *  that event id, so a console can tie one to its dial row. */
export const AUTONOMY_PROMOTION_KIND = "core.autonomy_promotion";
export const AUTONOMY_DEMOTED_KIND = "core.autonomy_demoted";

/* One row of GET /hotels/{slug}/signals, exactly clefs.core.signals.to_api:
   every column of SIGNAL_COLUMNS plus impact_amount, rankable and (on list
   rows) action_id. Fields the server always emits are required; a column
   that can be NULL is typed `| null` rather than optional, so a consumer
   cannot mistake "not sent" for "no value". */
export interface Signal {
  id: number;
  hotel_slug: string;
  module: string;
  kind: string;
  dedupe_key: string;
  section: SignalSection;
  /** Which brief carries it (Canon 18). NOT NULL on the server, default "daily". */
  cadence: SignalCadence;
  /** False for compliance items, which never rank into the daily top 5 (Canon 6). */
  rankable: boolean;
  title: string;
  summary: string | null;
  severity: 1 | 2 | 3 | 4;
  /** Minor units (fils). Canon item 6. When set, `currency` is set too (a DB CHECK). */
  impact_minor: number | null;
  /** The same impact as a Decimal string in major units (null without an impact). */
  impact_amount: DecimalString | null;
  impact_kind: "recoverable" | "revenue_upside" | "cost_leak" | "risk" | "service" | string | null;
  /** ISO 4217, trimmed from the CHAR(3) column; null when there is no impact. */
  currency: CurrencyCode | null;
  /** 0..1 */
  confidence: number;
  evidence: Evidence[];
  recommended_action: RecommendedAction | null;
  owner_role: Role | string | null;
  owner_user_id: number | null;
  due_at: Timestamp | null;
  subject_type: string | null;
  subject_id: string | null;
  status: SignalStatus;
  snoozed_until?: Timestamp | null;
  occurrences: number;
  first_seen_at: Timestamp;
  last_seen_at: Timestamp;
  surfaced_at?: Timestamp | null;
  resolved_at?: Timestamp | null;
  /** Why it closed ("accepted", "dismissed", "dial_changed", "demoted", ...). */
  resolution?: string | null;
  expires_at?: Timestamp | null;
  created_at?: Timestamp;
  updated_at?: Timestamp;
  /** The live action for this signal (list rows only), if one exists. */
  action_id?: number | null;
}

// ─── Actions (Canon item 7) ──────────────────────────────────────────────

export type ActionStatus =
  // main path
  | "proposed"
  | "approved"
  | "assigned"
  | "in_progress"
  | "done"
  | "verified"
  | "not_verified"
  // side states
  | "snoozed"
  | "rejected"
  | "cancelled"
  /** The autonomy dial logged it and nothing ran. */
  | "observed"
  /** Canon K1: the executor declined, or failed on its last retry. */
  | "failed"
  /** Canon K1: still proposed or approved at expires_at. */
  | "expired"
  // dispute path
  | "submitted"
  | "awaiting_counterparty"
  | "rejected_by_counterparty"
  | "unresolved";

export const ACTION_STATUS_LABELS: Record<ActionStatus, string> = {
  proposed: "Proposed",
  approved: "Approved",
  assigned: "Assigned",
  in_progress: "In progress",
  done: "Done",
  verified: "Verified",
  not_verified: "Not verified",
  snoozed: "Snoozed",
  rejected: "Rejected",
  cancelled: "Cancelled",
  observed: "Observed",
  failed: "Failed",
  expired: "Expired",
  submitted: "Submitted",
  awaiting_counterparty: "Awaiting counterparty",
  rejected_by_counterparty: "Rejected by counterparty",
  unresolved: "Unresolved",
};

/** States from which no further decision is expected (the server's TERMINAL). */
export const TERMINAL_ACTION_STATUSES: ReadonlySet<ActionStatus> = new Set<ActionStatus>([
  "verified", "not_verified", "rejected", "cancelled", "unresolved", "observed", "failed", "expired",
]);

export type Decision =
  | "approve"
  | "adjust"
  | "assign"
  | "dispute"
  | "remind"
  | "ignore"
  | "snooze";

/** POST /hotels/{slug}/actions/{id}/decide body (routes_contracts.DecideBody). */
export interface DecideBody {
  decision: Decision;
  params_patch?: Record<string, unknown>;
  assignee_user_id?: number;
  assignee_role?: Role | string;
  /** Up to 2000 characters. */
  note?: string;
  /** Only with decision "snooze" (default +24 h). ISO timestamp. */
  snooze_until?: string;
}

/** POST /hotels/{slug}/actions/{id}/done body. */
export interface DoneBody {
  note?: string;
  result?: Record<string, unknown>;
}

/** POST /hotels/{slug}/actions body (routes_contracts.CreateActionBody). */
export interface CreateActionBody {
  type: string;
  title: string;
  params?: Record<string, unknown>;
  signal_id?: number;
  expected_value?: DecimalString;
  currency?: CurrencyCode;
  owner_role?: Role | string;
  due_at?: string;
  assignee_user_id?: number;
  dedupe_key?: string;
  /** Canon K1. Must carry a timezone (the server refuses a naive time). */
  expires_at?: string;
}

/** What POST /hotels/{slug}/actions answers when the dial is `off`: nothing
 *  was written, so there is no id. */
export interface SuppressedAction {
  id: null;
  status: "suppressed";
  reason: "dial_off" | string;
  /** The action type that was suppressed. */
  type: string;
  created: false;
}

/** POST /hotels/{slug}/actions -> 201. `created` is false when a dedupe_key
 *  matched an existing action, which is returned instead. */
export type CreateActionResult = (Action & { created: boolean }) | SuppressedAction;

/** "dial": approved by the autonomy dial, not a person. */
export type DecidedVia = "console" | "whatsapp" | "email" | "api" | "system" | "dial";

/** Where an action is surfaced: the brief (approve level), the console only (suggest), or nowhere. */
export type ActionSurface = "brief" | "console" | "none";

/** Meta template category an executor sent under (ExecResult.template_category). */
export type TemplateCategory = "UTILITY" | "MARKETING";

/** How a verified outcome relates to the action (only "caused" counts toward verified profit). */
export type Attribution = "caused" | "influenced" | "observed";

/** ExecResult.reversal_handle: how the executed effect could be undone.
 *  The server default is {kind: "none", reason: "not_reversible"}. */
export interface ReversalHandle {
  kind: "none" | string;
  reason?: string;
  [key: string]: unknown;
}

/** One row of action_events (clefs.core.actions.list_events). */
export interface ActionEvent {
  id: number;
  at: Timestamp;
  /** user | device | service | system | dial */
  actor_type: string;
  /** TEXT on the server: a user id, device id or service name as a string. */
  actor_id: string | null;
  actor_label: string | null;
  /** created | approve | adjust | ... | executed | execution_failed | dial_revoked | verify_pending | verification_reversed */
  event: string;
  from_status: ActionStatus | null;
  to_status: ActionStatus | null;
  via: DecidedVia | string | null;
  data: Record<string, unknown>;
}

/* One row of GET /hotels/{slug}/actions, exactly clefs.core.actions.to_api:
   every column of the actions table (SELECT *), plus expected_value /
   outcome_value as Decimal strings and `terminal`. Money is *_minor + currency
   in storage (Canon 2); render the Decimal strings, never float arithmetic. */
export interface Action {
  id: number;
  hotel_slug: string;
  signal_id: number | null;
  module: string;
  type: string;
  /** "dispute" once the dispute path starts. */
  subtype: string | null;
  title: string;
  params: Record<string, unknown>;
  status: ActionStatus;
  /** True for the server's terminal states (TERMINAL_ACTION_STATUSES). */
  terminal: boolean;
  /** The state a snoozed action returns to. */
  snoozed_from?: ActionStatus | null;
  snoozed_until?: Timestamp | null;
  surface: ActionSurface | null;
  dedupe_key?: string | null;
  decided_by: number | null;
  decided_at: Timestamp | null;
  decided_via: DecidedVia | null;
  /** False for a dial approval ("dial" via); feeds the promotion stats. */
  human_decided?: boolean;
  /** Approved with a params_patch (an "adjust"). */
  edited?: boolean;
  assignee_user_id: number | null;
  /** staff_members.id when a device's on-shift staff member holds it. */
  assignee_staff_id?: number | null;
  assignee_role: Role | string | null;
  owner_role?: Role | string | null;
  due_at: Timestamp | null;
  /** Canon K1: a quote's validity or a plan's valid_until. */
  expires_at: Timestamp | null;
  executed_at: Timestamp | null;
  execution_result: Record<string, unknown> | null;
  execution_attempts?: number;
  execution_error: string | null;
  verify_after: Timestamp | null;
  next_verify_at?: Timestamp | null;
  verify_until?: Timestamp | null;
  verified_at: Timestamp | null;
  verification: Record<string, unknown> | null;
  /** Decimal strings in major units, derived from the *_minor columns. */
  expected_value: DecimalString | null;
  outcome_value: DecimalString | null;
  /** The same amounts in minor units (fils). */
  expected_minor: number | null;
  outcome_minor: number | null;
  /** ISO 4217, trimmed from the CHAR(3) column; set whenever an amount is. */
  currency: CurrencyCode | null;
  outcome_confidence: number | null;
  /** True only for a verified outcome with attribution "caused". */
  counts_toward_verified_profit: boolean;
  attribution: Attribution | null;
  /** reverse_verification: the outcome went to 0; the status stays verified. */
  reversed_at: Timestamp | null;
  reversal_reason: string | null;
  // Autonomy-dial ledger (Canon: Autonomy dial; 04 spec 1.3), stamped at
  // creation and on a successful execution.
  dial_level_at_creation: AutonomyLevel | null;
  dial_level_at_execution: AutonomyLevel | null;
  policy_version: number | null;
  /** Why an auto_within_limits action fell back to approval. */
  limit_reasons: string[];
  /** "meta_name@version" of the WhatsApp template an executor sent. */
  template_id: string | null;
  template_category: TemplateCategory | null;
  /** "contract:reservation:<id>" | "consent:<id>": why a message was lawful. */
  consent_basis: string | null;
  reversal_handle: ReversalHandle | null;
  reminders_sent: number;
  last_reminded_at?: Timestamp | null;
  escalation_level?: number;
  escalated_at?: Timestamp | null;
  created_by?: number | null;
  created_via?: string | null;
  created_at: Timestamp;
  updated_at: Timestamp;
  /** GET /hotels/{slug}/actions/{id} only. */
  events?: ActionEvent[];
}

// ─── The autonomy dial (Canon item 17, 04 spec section 1) ────────────────

/** In rank order: a level is "higher" when it sits later in this list. */
export type AutonomyLevel =
  | "off"
  | "observe"
  | "suggest"
  | "approve"
  | "auto_within_limits"
  | "auto";

export const AUTONOMY_LEVELS: readonly AutonomyLevel[] = [
  "off", "observe", "suggest", "approve", "auto_within_limits", "auto",
] as const;

/** Levels that let the dial approve and execute with no person: setting one
 *  (or accepting a promotion) needs core.autonomy.raise_auto, owners only. */
export const AUTO_AUTONOMY_LEVELS: ReadonlySet<AutonomyLevel> = new Set<AutonomyLevel>(["auto_within_limits", "auto"]);

export const AUTONOMY_LEVEL_LABELS: Record<AutonomyLevel, string> = {
  off: "Off",
  observe: "Observe",
  suggest: "Suggest",
  approve: "Approve",
  auto_within_limits: "Auto within limits",
  auto: "Auto",
};

/** What each level does in create_action (04 spec 1.1), for hints. */
export const AUTONOMY_LEVEL_HINTS: Record<AutonomyLevel, string> = {
  off: "Switched off: nothing is created.",
  observe: "Logged only; never runs.",
  suggest: "A draft waits in the console.",
  approve: "One tap in the brief, then it runs.",
  auto_within_limits: "Runs by itself when the limits pass; otherwise it waits for approval.",
  auto: "Runs by itself.",
};

/** Position in AUTONOMY_LEVELS (-1 for an unknown level). */
export function autonomyRank(level: AutonomyLevel | string): number {
  return AUTONOMY_LEVELS.indexOf(level as AutonomyLevel);
}

/** Promotion statistics over the trailing 30 days (04 spec 1.5). */
export interface AutonomyStats {
  window_days: number;
  decisions: number;
  approved_unedited: number;
  /** 0..1, rounded to 4 places. */
  approval_rate: number;
  /** 50+ human decisions with at least 95% approved unedited. */
  meets_threshold: boolean;
}

/** An open promotion suggestion on a dial row (its promotion_suggested event
 *  has status "open"; at most one per hotel and type). Accept or dismiss it by
 *  event_id. Accepting needs core.autonomy.manage up to approve and
 *  core.autonomy.raise_auto above it (dial.ts acceptPermFor). */
export interface AutonomySuggestion {
  event_id: number;
  to_level: AutonomyLevel;
  at: Timestamp;
}

/** One property of a limits_model JSON Schema (Pydantic model_json_schema). */
export interface JsonSchemaProperty {
  type?: "integer" | "number" | "boolean" | "string" | "array" | "object" | "null" | string;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: unknown[];
  items?: JsonSchemaProperty;
  /** Optional fields come as anyOf [{type: X}, {type: "null"}]. */
  anyOf?: JsonSchemaProperty[];
}

export interface LimitsSchema {
  title?: string;
  type?: string;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
}

/** One row of GET /hotels/{slug}/autonomy (clefs.core.actions.get_autonomy). */
export interface AutonomySetting {
  action_type: string;
  label: string;
  module: string;
  /** The hotel's setting (or the type's default). */
  level: AutonomyLevel;
  /** What applies now: the lowest of level, max_level and the type's gate. */
  effective_level: AutonomyLevel;
  default_level: AutonomyLevel;
  /** The type's ceiling ("approve" for refunds, folio, rate and OTA changes). */
  max_level: AutonomyLevel;
  always_approve: boolean;
  limits: Record<string, unknown>;
  /** JSON Schema of the limits, when the type has a limits_model. */
  limits_schema: LimitsSchema | null;
  /** 0 until the hotel has a row; bumped on every change or demotion. */
  policy_version: number;
  source: "hotel" | "default";
  reason: string | null;
  /** users.id of whoever last set it (null while on the default, and for system demotions). */
  updated_by: number | null;
  updated_at: Timestamp | null;
  stats: AutonomyStats;
  open_suggestion: AutonomySuggestion | null;
}

/** PUT /hotels/{slug}/autonomy/{action_type} body. 422 above_ceiling /
 *  invalid_limits / invalid_level; 403 without raise_auto when it raises the
 *  level into an auto level or loosens a limit (tightening and lowering need
 *  only core.autonomy.manage); 409 <code> when the type's level_check objects. */
export interface AutonomyUpdate {
  level: AutonomyLevel;
  limits?: Record<string, unknown>;
  /** Up to 500 characters, kept on the row and in the audit log. */
  reason?: string;
}

/** action_autonomy_events.kind */
export type AutonomyEventKind =
  | "seed"
  | "set"
  | "promotion_suggested"
  | "promotion_accepted"
  | "promotion_dismissed"
  | "demoted";

/** What lowered a dial (report_incident kinds, plus a manual demote). */
export type DemotionTrigger = "guest_complaint" | "optout_spike" | "meta_quality" | "eval_regression" | "manual";

/** action_autonomy_events.status: set on promotion_suggested rows only. */
export type AutonomySuggestionStatus = "open" | "accepted" | "dismissed" | "superseded";

/** One row of GET /hotels/{slug}/autonomy/{action_type}/events (newest first;
 *  clefs.core.actions.list_autonomy_events). Evidence is never sent. */
export interface AutonomyEvent {
  id: number;
  kind: AutonomyEventKind;
  /** null on a seed (there was no row before it). */
  from_level: AutonomyLevel | null;
  to_level: AutonomyLevel | null;
  /** Set on demotions. */
  trigger: DemotionTrigger | null;
  policy_version: number | null;
  /** audit.actor_fields: "user" (actor_id = users.id), "service" (a job or
   *  the dial), "system" (no actor, e.g. a seed), or "device". */
  actor_type: string | null;
  actor_id: string | null;
  at: Timestamp;
  status: AutonomySuggestionStatus | null;
  /** On promotion_accepted / promotion_dismissed: the suggestion answered. */
  resolves_event_id: number | null;
}

// ─── Metrics (foundation E3) ─────────────────────────────────────────────

export type MetricUnit = "count" | "money" | "percent" | "minutes" | "ratio" | "nights" | "rate";
export type MetricGrain = "total" | "day" | "week" | "month";

export interface MetricRow {
  /** Bucket start as an ISO date for grain != "total". */
  bucket?: string | null;
  /** Group-by dimension values keyed by dimension key. */
  group?: Record<string, string | null>;
  value: number | null;
  compare_value?: number | null;
}

export interface MetricResult {
  metric: string;
  label?: string;
  unit: MetricUnit;
  currency?: CurrencyCode | null;
  grain: MetricGrain;
  start: string;
  end: string;
  group_by?: string[];
  rows: MetricRow[];
  total?: number | null;
  compare?: "prev_period" | "prev_year" | null;
  compare_total?: number | null;
  /** True when the 500-row cap cut the result. */
  truncated?: boolean;
}

// ─── Pagination and errors ───────────────────────────────────────────────

export interface Page<T> {
  items: T[];
  next_cursor?: string | null;
  total?: number | null;
}

/** Every API error body: {detail, code?}. FastAPI 422s put an array in detail. */
export interface ApiErrorBody {
  detail?: string | { loc?: (string | number)[]; msg?: string; type?: string }[] | unknown;
  code?: string;
}
