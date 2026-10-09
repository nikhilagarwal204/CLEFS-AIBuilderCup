/* ═══════════════════════════════════════════════════════════════════════
   Service desk wording (05 spec, the AI Builder Cup prototype): what the
   manager console says about a job's escalation, priority, proof photo,
   guest answer, time taken, ETA and the guest's mood, what the "Needs your
   approval" panel says about a recovery case, and how the live link reads.

   Pure functions over the rows as the server sends them, with no React and
   no imports, so Node's own test runner loads this file directly (type
   stripping, no bundler; see __tests__/deskLabels.test.mjs). Every reader
   tolerates the shapes in flight: escalation_level as a ladder step name
   (supervisor | duty_manager | gm) or the older 0-3 number, instants as
   epoch milliseconds or ISO strings, and fields an older server leaves out.
   ═══════════════════════════════════════════════════════════════════════ */

export type Tone = "neutral" | "info" | "gold" | "ok" | "warn" | "bad";

export interface Chip {
  label: string;
  tone: Tone;
  title?: string;
}

/** The request-row fields read here. All optional: older rows lack most. */
export interface DeskRow {
  state?: string | null;
  status?: string | null;
  timestamp?: number | string | null;
  priority?: string | null;
  severity?: string | null;
  sensitivity?: string | null;
  request_type_key?: string | null;
  escalation_level?: number | string | null;
  acknowledged_at?: number | string | null;
  resolved_at?: number | string | null;
  eta_minutes?: number | null;
  eta_at?: number | string | null;
  proof_status?: string | null;
  proof_reason?: string | null;
  proof_photo_url?: string | null;
  guest_confirmation?: string | null;
  guest_confirmed_at?: number | string | null;
  closed_at?: number | string | null;
  closure_reason?: string | null;
  mood?: string | null;
  frustration?: number | string | null;
  emergency?: boolean | null;
}

// ─── Small readers ───

const OPEN_STATES = new Set(["scheduled", "new", "assigned", "acknowledged", "in_progress", "reopened"]);

/** Epoch ms from epoch ms, epoch seconds or an ISO string; null when unknown. */
export function toMs(v: unknown): number | null {
  if (typeof v === "number") {
    if (!Number.isFinite(v) || v <= 0) return null;
    return v < 1e11 ? v * 1000 : v;
  }
  if (typeof v === "string" && v.trim()) {
    const s = v.trim();
    if (/^\d+(\.\d+)?$/.test(s)) return toMs(Number(s));
    const t = Date.parse(s);
    return Number.isFinite(t) ? t : null;
  }
  return null;
}

/** The v2 state, or the legacy status mapped onto it (serviceApi.effectiveState's rule). */
export function stateOf(r: Pick<DeskRow, "state" | "status">): string {
  if (r.state) return r.state;
  if (r.status === "completed") return "completed";
  if (r.status === "progress") return "assigned";
  return "new";
}

export function isOpenRow(r: Pick<DeskRow, "state" | "status">): boolean {
  return OPEN_STATES.has(stateOf(r));
}

function humanize(key: string): string {
  const s = key.replace(/[_-]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function clip(text: string, max: number): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max - 3).trimEnd()}...` : t;
}

/** A stopwatch reading: "00:41", "12:05", "1:02:03", and "3d 4h" past a day. */
export function clockText(ms: number): string {
  const total = Math.max(0, Math.floor((Number.isFinite(ms) ? ms : 0) / 1000));
  const d = Math.floor(total / 86_400);
  const h = Math.floor((total % 86_400) / 3_600);
  const m = Math.floor((total % 3_600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}:${pad(m)}:${pad(s)}`;
  return `${pad(m)}:${pad(s)}`;
}

// ─── Priority and emergencies ───

/** URGENT means priority high (05 spec 2.11); the CSS upper-cases it. */
export function priorityLabel(priority: string | null | undefined): string {
  if (priority === "high") return "Urgent";
  if (priority === "medium") return "Medium";
  if (priority === "low") return "Low";
  return priority ? humanize(priority) : "Normal";
}

/** The priority-* class the classic dashboard styles (anything else reads as medium). */
export function priorityClass(priority: string | null | undefined): "high" | "medium" | "low" {
  return priority === "high" || priority === "low" ? priority : "medium";
}

/** Emergency words or an L1 request: shown as "Emergency", never as a level.
    The server also flags a card `emergency` (its sensitivity). */
export function isEmergency(r: Pick<DeskRow, "severity" | "sensitivity" | "request_type_key"> & { emergency?: boolean | null }): boolean {
  return r.emergency === true || r.severity === "L1" || r.sensitivity === "emergency" || r.request_type_key === "emergency";
}

/** The urgent path (emergency, sensitive or L1): people are paged at once, not by the 3/5/10 ladder. */
export function isUrgentPath(r: Pick<DeskRow, "severity" | "sensitivity">): boolean {
  return r.severity === "L1" || r.sensitivity === "emergency" || r.sensitivity === "sensitive";
}

export function emergencyChip(r: DeskRow): Chip | null {
  if (!isEmergency(r)) return null;
  return { label: "Emergency", tone: "bad", title: "Emergency: the duty manager is paged at once" };
}

// ─── Escalation ladder (05 spec 2.7) ───

export type EscalationStep = "supervisor" | "duty_manager" | "gm" | "security";

/** Who hears about a job nobody accepted, and after how many minutes. */
export const ESCALATION_LADDER = {
  supervisor: { label: "Supervisor", afterMin: 3 },
  duty_manager: { label: "Duty manager", afterMin: 5 },
  gm: { label: "GM", afterMin: 10 },
} as const;

const STEP_BY_LEVEL: Record<number, EscalationStep> = { 1: "supervisor", 2: "duty_manager", 3: "gm" };

/** escalation_level as sent (a step name, or the older 0-3 level) as a ladder step; null below the first step. */
export function escalationStep(level: unknown): EscalationStep | null {
  if (typeof level === "number") {
    if (!Number.isFinite(level) || level < 1) return null;
    return STEP_BY_LEVEL[Math.min(3, Math.floor(level))];
  }
  if (typeof level !== "string") return null;
  const k = level.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (!k) return null;
  if (/^\d+$/.test(k)) return escalationStep(Number(k));
  if (k === "supervisor" || k === "duty_manager" || k === "gm" || k === "security") return k;
  if (k === "general_manager") return "gm";
  if (k === "dutymanager" || k === "manager") return "duty_manager";
  return null;
}

/** "Supervisor (3 min)", "Duty manager (5 min)", "GM (10 min)". Never "L1/L2/L3":
    that scale is the emergency severity, shown as "Emergency". */
export function escalationLabel(level: unknown): string | null {
  const step = escalationStep(level);
  if (!step) return null;
  if (step === "security") return "Security";
  const s = ESCALATION_LADDER[step];
  return `${s.label} (${s.afterMin} min)`;
}

/** The card's escalation chip. On the urgent path nobody waits for the ladder,
    so the chip names who was paged instead of a minute mark. */
export function escalationChip(r: DeskRow): Chip | null {
  const step = escalationStep(r.escalation_level);
  if (!step) return null;
  if (step === "security" || isUrgentPath(r)) {
    // The urgent ladder's level 1 is Security (sensitive cases), not a supervisor.
    const sensitive = r.sensitivity === "emergency" || r.sensitivity === "sensitive";
    const label = step === "gm" ? "Duty manager and GM paged"
      : step === "duty_manager" ? "Duty manager paged"
      : step === "security" || sensitive ? "Security paged"
      : "Supervisor paged";
    return { label, tone: "bad", title: "Urgent: paged at once, without waiting for the 3, 5 and 10 minute ladder" };
  }
  const s = ESCALATION_LADDER[step];
  const who = step === "gm" ? "the GM" : `the ${s.label.toLowerCase()}`;
  return {
    label: `${s.label} (${s.afterMin} min)`,
    tone: step === "gm" ? "bad" : "warn",
    title: `Nobody accepted the job within ${s.afterMin} minutes, so ${who} was alerted`,
  };
}

// ─── Time taken and ETA ───

/** The card's "time taken" (05 spec 2.13): from the guest's request until a
    staff member accepts it. Live while nobody has, then frozen. */
export function timeTaken(r: DeskRow, now: number): (Chip & { live: boolean }) | null {
  if (!r.state) return null; // pre-v2 rows never had an acceptance clock
  const start = toMs(r.timestamp);
  if (start === null) return null;
  const s = stateOf(r);
  const accepted = toMs(r.acknowledged_at);
  if (accepted !== null && accepted >= start && s !== "reopened") {
    const took = accepted - start;
    return {
      label: `Accepted in ${clockText(took)}`, tone: took <= 3 * 60_000 ? "ok" : "warn", live: false,
      title: "Time taken: from the guest's request until a staff member accepted it",
    };
  }
  if ((s === "new" || s === "assigned") && now > 0) {
    const waited = Math.max(0, now - start);
    return {
      label: `Waiting ${clockText(waited)}`,
      tone: waited >= 10 * 60_000 ? "bad" : waited >= 3 * 60_000 ? "warn" : "neutral",
      live: true,
      title: "Time taken so far: nobody has accepted the job yet",
    };
  }
  return null;
}

/** "ETA 5 min" while the job is open, as the guest was told; "(late)" once it has passed. */
export function etaChip(r: DeskRow, now: number): Chip | null {
  const raw = r.eta_minutes;
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw < 0) return null;
  const s = stateOf(r);
  if (!OPEN_STATES.has(s) || s === "scheduled") return null;
  const mins = Math.round(raw);
  const due = toMs(r.eta_at);
  const late = due !== null && now > 0 && now > due;
  return {
    label: late ? `ETA ${mins} min (late)` : `ETA ${mins} min`,
    tone: late ? "warn" : "info",
    title: "The arrival time the guest was given",
  };
}

// ─── Proof before closure (05 spec 2.8) ───

/* The server stores verified | rejected | unverified (service migration 0131):
   "unverified" means the automatic check could not run, so the job stays
   open and the supervisor is asked to look at the photo; the console reads it
   as "Checking photo". The other spellings are kept for older payloads. */
const PHOTO_CHECKING = new Set(["unverified", "checking", "verifying", "processing", "uploaded", "uploading", "submitted"]);
const PHOTO_VERIFIED = new Set(["verified", "match", "matched", "ok", "passed"]);
const PHOTO_REJECTED = new Set(["rejected", "mismatch", "failed", "not_matched"]);

export type PhotoState = "checking" | "verified" | "rejected";

export function photoState(r: Pick<DeskRow, "proof_status" | "proof_photo_url">): PhotoState | null {
  const p = (r.proof_status ?? "").trim().toLowerCase();
  if (PHOTO_VERIFIED.has(p)) return "verified";
  if (PHOTO_REJECTED.has(p)) return "rejected";
  if (PHOTO_CHECKING.has(p) || (p === "pending" && Boolean(r.proof_photo_url))) return "checking";
  return null;
}

/** "Checking photo", "Photo verified", "Photo rejected: <reason>". `short`
    drops the reason from a rejected chip where a ProofAlert already says it. */
export function photoChip(r: DeskRow, short = false): Chip | null {
  const st = photoState(r);
  if (st === "verified") return { label: "Photo verified", tone: "ok", title: "Gemini checked the completion photo against the request" };
  if (st === "checking") {
    const manual = (r.proof_status ?? "").trim().toLowerCase() === "unverified";
    return {
      label: "Checking photo", tone: "info",
      title: manual
        ? "The automatic check could not run, so the supervisor was asked to look at the photo. The job stays open."
        : "Gemini is checking the completion photo",
    };
  }
  if (st === "rejected") {
    const reason = (r.proof_reason ?? "").trim().replace(/[.\s]+$/, "");
    return {
      label: reason && !short ? `Photo rejected: ${clip(reason, 60)}` : "Photo rejected",
      tone: "bad",
      title: `${reason || "The photo does not show the job done"}. The job stays open and the supervisor was alerted.`,
    };
  }
  return null;
}

const CLOSURE_LABELS: Record<string, string> = {
  confirmed: "Closed: guest confirmed",
  guest_confirmed: "Closed: guest confirmed",
  fixed: "Closed: guest confirmed",
  no_reply: "Closed: no reply from the guest",
  no_answer: "Closed: no reply from the guest",
  timeout: "Closed: no reply from the guest",
  manager: "Closed by a manager",
  manager_override: "Closed by a manager",
  cancelled: "Closed: cancelled",
};

/** closure_reason in words ("no_reply" -> "Closed: no reply from the guest"). */
export function closureLabel(reason: string | null | undefined): string {
  const k = (reason ?? "").trim().toLowerCase();
  if (!k) return "Closed";
  return CLOSURE_LABELS[k] ?? `Closed: ${humanize(k).toLowerCase()}`;
}

const CONFIRMED_CLOSURES = new Set(["guest_confirmed", "confirmed", "fixed"]);

/** Where the guest's answer stands: "Waiting for guest", "Guest confirmed",
    "Not fixed - recovery opened", or the closure with its reason. An answer
    older than the latest completion is stale (the job was done again since).
    A cancelled job says so in its state chip, so its closure adds nothing. */
export function guestChip(r: DeskRow): Chip | null {
  if (toMs(r.closed_at) !== null) {
    const reason = (r.closure_reason ?? "").trim().toLowerCase();
    if (reason === "cancelled") return null;
    if (CONFIRMED_CLOSURES.has(reason) || (!reason && r.guest_confirmation === "yes")) {
      return { label: "Guest confirmed", tone: "ok", title: "Closed: the guest confirmed the fix" };
    }
    return { label: closureLabel(reason), tone: "neutral", title: "This job is closed" };
  }
  const s = stateOf(r);
  const answeredAt = toMs(r.guest_confirmed_at);
  const doneAt = toMs(r.resolved_at);
  const current = answeredAt !== null && (doneAt === null || answeredAt >= doneAt);
  if (r.guest_confirmation === "no" && (s !== "completed" || current)) {
    return { label: "Not fixed - recovery opened", tone: "bad", title: "The guest said it is not fixed: the job reopened and a recovery case is waiting for a manager" };
  }
  if (r.guest_confirmation === "yes" && (current || answeredAt === null)) {
    return { label: "Guest confirmed", tone: "ok", title: "The guest confirmed the fix" };
  }
  if (s === "completed") {
    return { label: "Waiting for guest", tone: "gold", title: "Done: the guest has been asked whether everything is OK" };
  }
  return null;
}

// ─── Mood and frustration (05 spec 2.2) ───

export const MOOD_LABELS: Record<string, string> = {
  calm: "Calm", happy: "Happy", neutral: "Neutral", confused: "Confused", impatient: "Impatient",
  annoyed: "Annoyed", angry: "Angry", distressed: "Distressed",
};

/** Frustration as 0..1 (a 0..100 score is scaled down); null when unknown. */
export function frustrationOf(v: unknown): number | null {
  const n = typeof v === "string" && v.trim() ? Number(v) : v;
  if (typeof n !== "number" || !Number.isFinite(n) || n < 0) return null;
  return n > 1 ? Math.min(1, n / 100) : n;
}

/** "Guest annoyed, frustration 80%". `notableOnly` drops calm, happy and neutral guests. */
export function moodChip(mood: unknown, frustration: unknown, notableOnly = false): Chip | null {
  const m = typeof mood === "string" ? mood.trim().toLowerCase() : "";
  const f = frustrationOf(frustration);
  if (!m && f === null) return null;
  const tone: Tone = (f !== null && f >= 0.7) || m === "angry" || m === "distressed" ? "bad"
    : (f !== null && f >= 0.4) || m === "impatient" || m === "annoyed" || m === "confused" ? "warn"
    : "neutral";
  if (notableOnly && tone === "neutral") return null;
  const word = m ? (MOOD_LABELS[m] ?? humanize(m)).toLowerCase() : "";
  const pct = f !== null ? `frustration ${Math.round(f * 100)}%` : "";
  const label = word ? `Guest ${word}${pct ? `, ${pct}` : ""}` : humanize(pct);
  return { label, tone, title: "Gemini reads the guest's mood and frustration from each message" };
}

// ─── Recovery cases: the "Needs your approval" panel (05 spec 2.12) ───

export const RECOVERY_KIND_LABELS: Record<string, string> = {
  not_fixed: "Not fixed",
  frustration: "Guest frustrated",
  never_arrived: "Never arrived",
  complaint: "Complaint",
  post_checkout: "After checkout",
  manual: "Opened by staff",
};

/** The panel lists these; a manual case with nothing to approve stays off it. */
const APPROVAL_KINDS = new Set(["not_fixed", "frustration", "never_arrived"]);

const REASON_KIND: Record<string, string> = {
  not_resolved: "not_fixed", not_fixed: "not_fixed", frustration: "frustration", frustrated: "frustration",
  never_arrived: "never_arrived", complaint: "complaint", post_checkout: "post_checkout", manual: "manual",
};

const GESTURE_KIND_LABELS: Record<string, string> = {
  amenity: "Amenity", fnb_credit: "Food and drink credit", late_checkout: "Late checkout", room_night: "Room night",
  upgrade: "Upgrade", discount: "Discount", other: "Gesture",
};

const DECIDED = new Set(["given", "approved", "rejected", "dismissed", "cancelled", "declined", "done"]);

type Bag = Record<string, unknown>;

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

function firstText(...vals: unknown[]): string {
  for (const v of vals) {
    const s = str(v);
    if (s) return s;
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const o = v as Bag;
      const inner = str(o.text) || str(o.description) || str(o.label) || str(o.title);
      if (inner) return inner;
    }
  }
  return "";
}

/** A gesture row of the older recovery API as words: "Late checkout worth AED 150.00". */
export function gestureText(g: Bag): string {
  const desc = str(g.description);
  if (desc) return desc;
  const kind = GESTURE_KIND_LABELS[str(g.kind)] ?? (str(g.kind) ? humanize(str(g.kind)) : "Gesture");
  const value = str(g.value) || (typeof g.value === "number" ? String(g.value) : "");
  const ccy = str(g.currency);
  return value && Number(value) > 0 ? `${kind} worth ${ccy ? `${ccy} ` : ""}${value}` : kind;
}

function pendingGestures(c: Bag): Bag[] {
  const list = Array.isArray(c.gestures) ? (c.gestures as unknown[]) : [];
  return list.filter((g): g is Bag => Boolean(g) && typeof g === "object" && (g as Bag).status === "pending_approval");
}

/** The AI-suggested gesture of a case, in whichever field the server put it. */
export function suggestionOf(c: Bag): string {
  const direct = firstText(c.suggested_gesture, c.gesture_suggestion, c.ai_gesture, c.proposed_gesture, c.suggestion, c.gesture);
  if (direct) return direct;
  const pending = pendingGestures(c)[0];
  return pending ? gestureText(pending) : "";
}

export function recoveryKind(c: Bag): string {
  const k = str(c.kind).toLowerCase() || str(c.reason).toLowerCase();
  return REASON_KIND[k] ?? (k || "manual");
}

export function recoveryKindLabel(kind: string): string {
  return RECOVERY_KIND_LABELS[kind] ?? humanize(kind);
}

/** True when a person still has to approve or dismiss this case. The server
    says so outright (needs_approval, approval_status pending | approved |
    dismissed); older payloads are judged by their status and gestures. */
export function needsApproval(c: Bag): boolean {
  if (typeof c.needs_approval === "boolean") return c.needs_approval;
  const approval = str(c.approval_status).toLowerCase();
  if (approval) return approval === "pending" && (!str(c.status) || str(c.status).toLowerCase() === "open");
  const status = str(c.status).toLowerCase();
  if (status && status !== "open" && status !== "pending" && status !== "pending_approval") return false;
  if (str(c.decision) || toMs(c.decided_at) !== null) return false;
  if (DECIDED.has(str(c.gesture_status).toLowerCase())) return false;
  const direct = firstText(c.suggested_gesture, c.gesture_suggestion, c.ai_gesture, c.proposed_gesture, c.suggestion, c.gesture);
  if (Array.isArray(c.gestures) && (c.gestures as unknown[]).length && !direct && !pendingGestures(c).length) {
    return false; // every gesture on the case is decided already
  }
  return APPROVAL_KINDS.has(recoveryKind(c)) || Boolean(direct) || pendingGestures(c).length > 0;
}

export interface ApprovalItem {
  id: number;
  room: string | null;
  kind: string;
  kindLabel: string;
  mood: Chip | null;
  suggestion: string;
  /** "Default" when the AI was off or slow and a standard gesture stands in. */
  suggestionLabel: string;
  /** What the gesture costs, in the hotel's currency ("AED 50.00"), when it costs anything. */
  value: string | null;
  /** The job it is about ("Towels x2"). */
  item: string | null;
  summary: string;
  openedAt: number | null;
  requestId: number | null;
}

/** The guest's mood as the dashboard's own copy of a request carries it. */
export type MoodLookup = (requestId: number) => { mood?: string | null; frustration?: number | string | null } | null | undefined;

/** One row of the panel, or null when the case has no usable id. */
export function approvalItem(c: Bag, moodOf?: MoodLookup): ApprovalItem | null {
  const id = typeof c.id === "number" ? c.id : Number(c.id);
  if (!Number.isFinite(id) || id <= 0) return null;
  const request = c.request && typeof c.request === "object" ? (c.request as Bag) : {};
  const kind = recoveryKind(c);
  const room = str(c.room) || str(request.room) || (typeof c.room === "number" ? String(c.room) : "");
  const requestId = typeof c.request_id === "number" ? c.request_id : typeof request.id === "number" ? request.id : null;
  const looked = requestId !== null && moodOf ? moodOf(requestId) ?? null : null;
  const value = str(c.suggested_value);
  const source = str(c.suggestion_source).toLowerCase();
  return {
    id,
    room: room || null,
    kind,
    kindLabel: recoveryKindLabel(kind),
    mood: moodChip(c.mood ?? c.guest_mood ?? c.last_mood ?? request.mood ?? looked?.mood,
                   c.frustration ?? c.max_frustration ?? c.frustration_score ?? request.frustration ?? looked?.frustration),
    suggestion: suggestionOf(c),
    suggestionLabel: source === "default" ? "Suggested gesture (standard)" : "AI-suggested gesture",
    value: value && Number(value) > 0 ? `${str(c.currency) ? `${str(c.currency)} ` : ""}${value}` : null,
    item: str(request.item_label) || null,
    summary: firstText(c.summary, c.reason_text, c.notes, c.description),
    openedAt: toMs(c.opened_at ?? c.created_at),
    requestId,
  };
}

/** The panel's rows: cases awaiting a decision, oldest first (the longest wait on top). */
export function approvalItems(cases: unknown, moodOf?: MoodLookup): ApprovalItem[] {
  const list = Array.isArray(cases) ? cases
    : cases && typeof cases === "object" && Array.isArray((cases as Bag).items) ? ((cases as Bag).items as unknown[])
    : [];
  return list
    .filter((c): c is Bag => Boolean(c) && typeof c === "object")
    .filter(needsApproval)
    .map(c => approvalItem(c, moodOf))
    .filter((x): x is ApprovalItem => x !== null)
    .sort((a, b) => (a.openedAt ?? 0) - (b.openedAt ?? 0) || a.id - b.id);
}

// ─── "Choose staff" (05 spec decision 5: the same ranking as dispatch) ───

/** One person in GET .../requests/{id}/free-staff ({free, others}). */
export interface StaffCandidate {
  staff_id: number;
  name: string;
  open_jobs?: number | null;
  eta_minutes?: number | null;
  floor_distance?: number | null;
  current?: boolean | null;
  /** Why someone in `others` is not free. */
  reason?: string | null;
}

export const NOT_FREE_LABELS: Record<string, string> = {
  other_department: "other team",
  off_duty: "off duty",
  not_on_shift: "not on shift",
  shift_role: "supervising this shift",
  at_capacity: "at capacity",
};

/** "Maria Santos · 1 open · ETA 5 min" for the free; "Priya Sharma · not on shift" for the rest. */
export function staffOptionLabel(c: StaffCandidate): string {
  const parts = [c.name];
  if (c.reason) {
    const why = NOT_FREE_LABELS[c.reason] ?? humanize(c.reason).toLowerCase();
    parts.push(c.reason === "at_capacity" && typeof c.open_jobs === "number" ? `${why} (${c.open_jobs} open)` : why);
  } else {
    if (typeof c.open_jobs === "number") parts.push(c.open_jobs === 0 ? "free" : `${c.open_jobs} open`);
    if (typeof c.eta_minutes === "number") parts.push(`ETA ${Math.round(c.eta_minutes)} min`);
  }
  return `${parts.join(" · ")}${c.current ? " (has it now)" : ""}`;
}

// ─── Live link ───

/** The header pill for the dashboard's live stream (lib/api StreamStatus). */
export function liveView(status: string | null | undefined): Chip {
  if (status === "live") return { label: "Live", tone: "ok", title: "Changes appear the moment they happen" };
  if (status === "polling") {
    return { label: "Reconnecting", tone: "warn", title: "The live link dropped. The list refreshes every 15 seconds while it reconnects." };
  }
  if (status === "reconnecting") return { label: "Reconnecting", tone: "warn", title: "The live link dropped. Reconnecting now." };
  return { label: "Connecting", tone: "neutral", title: "Opening the live link" };
}

/** An openHotelStream onStatus wrapper for the pill: "live" shows at once,
    a drop shows only if it lasts `graceMs` (Cloud Run recycles long streams
    every few minutes; a one-second reconnect should not flash "Reconnecting").
    Before the first "live", every status shows at once. */
export function graceStatus<S extends string>(
  show: (s: S) => void, graceMs = 2_500,
): { onStatus: (s: S) => void; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let everLive = false;
  const cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  return {
    cancel,
    onStatus: (s: S) => {
      cancel();
      if (s === "live") everLive = true;
      if (s === "live" || !everLive) show(s);
      else timer = setTimeout(() => show(s), graceMs);
    },
  };
}

// ─── Request timeline (the detail drawer) ───

const ACTION_TEXT: Record<string, string> = {
  assign: "Assigned", reassign: "Reassigned", ack: "Accepted", start: "On the way", complete: "Marked as done",
  cancel: "Cancelled", reopen: "Reopened", confirm: "Guest confirmed the fix", unassign: "Unassigned",
  release: "Released", decline: "Declined", handoff: "Handed over",
};

export interface TimelineEvent {
  kind?: string | null;
  action?: string | null;
  to_state?: string | null;
  actor_label?: string | null;
  payload?: Record<string, unknown> | null;
}

/** One line of the request timeline: "Accepted by Maria Santos", "Escalated: Supervisor (3 min)". */
export function eventText(ev: TimelineEvent): string {
  const kind = (ev.kind ?? "").toLowerCase();
  const action = (ev.action ?? "").toLowerCase();
  const p = ev.payload ?? {};
  const by = ev.actor_label ? ` by ${ev.actor_label}` : "";
  if (kind === "created") return `Request filed${by}`;
  if (kind === "transition") return `${ACTION_TEXT[action] ?? humanize(action || ev.to_state || "updated")}${by}`;
  if (kind === "escalated") {
    const step = escalationStep(p.to ?? action) ?? escalationStep(p.level);
    const people = Array.isArray(p.recipients) ? (p.recipients as unknown[]).filter(x => typeof x === "string").join(", ") : "";
    const shadow = p.shadow === true ? " (shadow mode: nobody was messaged)" : "";
    const tail = `${people ? `, ${people}` : ""}${shadow}`;
    if (p.urgent === true || step === "security") {
      const who = step === "security" ? "Security" : step ? ESCALATION_LADDER[step].label : "Duty manager";
      return `Paged at once: ${who}${tail}`;
    }
    const label = escalationLabel(step) ?? humanize(String(p.to ?? action ?? "the next step"));
    return `Escalated: ${label}${tail}`;
  }
  if (kind === "sla_breach") return action === "resolve" ? "Completion target missed" : "Acceptance target missed";
  if (kind === "note") return `Note: ${str(p.note) || "(empty)"}`;
  if (kind === "merged") return "A repeat request was merged into this one";
  if (kind.includes("proof") || kind.includes("photo")) {
    const status = str(p.status).toLowerCase() || kind.replace(/^proof_/, "");
    const reason = str(p.reason).replace(/[.\s]+$/, "");
    if (p.verified === true || PHOTO_VERIFIED.has(status)) return "Photo verified";
    if (p.verified === false || PHOTO_REJECTED.has(status)) {
      return `Photo rejected${reason ? `: ${reason}` : ""}. Supervisor alerted`;
    }
    if (status === "unverified") return "Photo could not be checked automatically. Supervisor asked to look";
    return "Photo uploaded";
  }
  if (kind === "recovery") {
    return `Recovery case opened (${recoveryKindLabel(str(p.kind) || "not_fixed").toLowerCase()}). Duty manager alerted`;
  }
  if (kind === "guest_message") {
    const text = str(p.text_original) || str(p.text_for_guest) || str(p.text);
    if (str(p.kind) === "gesture") return `Gesture sent to the guest: ${text}`;
    return `Staff reply${by}${str(p.via) === "voice" ? " (voice)" : ""}: ${text}`.trimEnd();
  }
  if (kind.includes("reply")) return `Staff reply${by}: ${str(p.text_original) || str(p.text) || ""}`.trimEnd();
  if (kind.includes("feedback")) return p.fixed === false ? "Guest said it is not fixed" : "Guest confirmed the fix";
  if (kind === "closed") return closureLabel(str(p.reason) || str(p.closure_reason));
  if (kind.includes("demo")) return `Demo: clock moved ${typeof p.minutes === "number" ? p.minutes : 3} min`;
  return `${humanize(kind || "update")}${action ? `: ${humanize(action).toLowerCase()}` : ""}${by}`;
}

// ─── Proof photo source ───

/** The proof photo URL as an <img> src. A signed https URL (Cloud Storage) is
    used as is; an API path goes through the same-origin proxy so the session
    cookie rides along. */
export function photoSrc(url: string | null | undefined, apiPrefix = "/api/clefs"): string | null {
  const u = (url ?? "").trim();
  if (!u) return null;
  if (/^(https?:|data:image\/|blob:)/i.test(u)) return u;
  if (u.startsWith(`${apiPrefix}/`)) return u;
  if (u.startsWith("/")) return `${apiPrefix}${u}`;
  return null;
}
