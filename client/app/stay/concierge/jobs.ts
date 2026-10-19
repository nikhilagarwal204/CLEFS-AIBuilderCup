/* ═══════════════════════════════════════════════════════════════════════
   The "Job sent to staff" card, as logic (05 spec 3, decision 13).

   A job tool result carries a card (notify_staff's ui.card): what the guest
   asked for, the room, who has it and when the job was created. The public
   tracker (GET /public/r/{token}) then refreshes it: the staff member's
   first name, the minutes, when it was accepted, the state, and whether the
   guest should now be asked "Is everything OK?".

   "Time taken" runs from the guest's request until a staff member accepts,
   then freezes at accepted_at - created_at, the number the console shows
   too ("Accepted in 00:41").

   Pure on purpose (type-only imports): node --test loads it straight from
   the .ts file (app/stay/__tests__/jobs.test.mjs).
   ═══════════════════════════════════════════════════════════════════════ */

import type { JobCard, TrackerStatus } from "./api";

export type JobPhase = "sent" | "accepted" | "on_the_way" | "done" | "confirm" | "reopened" | "cancelled";
type TFn = (key: string, vars?: Record<string, string | number>) => string;

/** Epoch ms from a time as the server sends it: epoch ms or seconds, a
    numeric string, or an ISO string. Null when it is none of those. */
export function toMs(value: unknown): number | null {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value <= 0) return null;
    return value > 1e11 ? value : value * 1000;
  }
  const text = String(value).trim();
  if (!text) return null;
  if (/^\d+(\.\d+)?$/.test(text)) return toMs(Number(text));
  // "2026-10-19 12:00:00+00:00" (a Python str()) parses in Chrome but not in Safari.
  const parsed = Date.parse(text.replace(" ", "T"));
  return Number.isFinite(parsed) ? parsed : null;
}

/** "00:41", "12:05"; from 100 minutes on, "1:42:05". */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor((Number.isFinite(ms) ? ms : 0) / 1000));
  const pad = (n: number) => String(n).padStart(2, "0");
  if (total >= 6000) {
    const h = Math.floor(total / 3600);
    return `${h}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
  }
  return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
}

/** Service's internal states, in the tracker's four words. */
export function trackerState(state: string | null | undefined): TrackerStatus["state"] | null {
  switch (state) {
    case "received": case "assigned": case "on_the_way": case "done":
      return state;
    case "new": case "scheduled": case "reopened":
      return "received";
    case "acknowledged":
      return "assigned";
    case "in_progress":
      return "on_the_way";
    case "completed": case "cancelled": case "closed":
      return "done";
    default:
      return null;
  }
}

const present = <T,>(v: T | null | undefined): v is T => v !== null && v !== undefined && v !== "";

/** The card as it stands now: the snapshot the tool returned, refreshed by
    whatever the tracker knows (the tracker is newer, so it wins). */
export function mergeJob(card: JobCard | null | undefined, status: Partial<TrackerStatus> | null | undefined): JobCard {
  const c: Partial<JobCard> = card ?? {};
  const s: Partial<TrackerStatus> = status ?? {};
  return {
    item_label: c.item_label ?? s.item_label ?? s.request_type_label ?? null,
    quantity: c.quantity ?? s.quantity ?? null,
    room: c.room ?? s.room ?? null,
    staff_first_name: present(s.staff_first_name) ? s.staff_first_name : c.staff_first_name ?? null,
    department_label: present(s.department_label) ? s.department_label : c.department_label ?? null,
    eta_minutes: present(s.eta_minutes) ? s.eta_minutes : c.eta_minutes ?? null,
    created_at: c.created_at ?? s.created_at_ms ?? null,
    accepted_at: present(s.accepted_at) ? s.accepted_at : c.accepted_at ?? null,
    state: s.state ?? c.state ?? null,
    request_type: c.request_type ?? null,
    department_key: c.department_key ?? null,
  };
}

/** Time taken: frozen at acceptance (server times, the console's number),
    else running from the guest's ask (or, after a reload, the job's
    creation). `endedAt`: when a job finished without an explicit accept,
    the clock stops there instead of running on. */
export function timeTaken(job: JobCard, now: number, askedAt?: number | null, endedAt?: number | null): { ms: number; frozen: boolean } {
  const created = toMs(job.created_at);
  const accepted = toMs(job.accepted_at) ?? toMs(endedAt);
  if (accepted !== null && created !== null) return { ms: Math.max(0, accepted - created), frozen: true };
  if (accepted !== null) return { ms: Math.max(0, accepted - (askedAt ?? accepted)), frozen: true };
  return { ms: Math.max(0, now - (askedAt ?? created ?? now)), frozen: false };
}

/** Where the job is, for the card's pill. "reopened": the guest said "Not
    fixed" and nobody has picked it up again yet. */
export function jobPhase(job: JobCard, status: Partial<TrackerStatus> | null | undefined, reopened = false): JobPhase {
  if (status?.cancelled) return "cancelled";
  if (status?.needs_guest_confirmation) return "confirm";
  const state = status?.state ?? trackerState(job.state);
  if (state === "done") return status?.can_confirm && status.needs_guest_confirmation === undefined ? "confirm" : "done";
  if (state === "on_the_way") return "on_the_way";
  if (reopened) return "reopened";
  return toMs(job.accepted_at) !== null ? "accepted" : "sent";
}

/** A job whose card has nothing left to change: no more polling for it.
    Done means settled once the guest answered, the job closed, or (on a
    server without needs_guest_confirmation) no answer is being asked for. */
export function isSettled(status: Partial<TrackerStatus>): boolean {
  if (status.cancelled) return true;
  if (status.state !== "done" || status.needs_guest_confirmation) return false;
  if (status.guest_confirmation !== null && status.guest_confirmation !== undefined) return true;
  if (toMs(status.closed_at) !== null) return true;
  if (status.needs_guest_confirmation === false) return true;
  return !status.can_confirm;
}

/** A label in the guest's language for a dictionary key, else null. */
function known(t: TFn, key: string): string | null {
  const out = t(key);
  return out === key ? null : out;
}

/** "Extra towels (2)": the item in the guest's language when the page
    knows the request type, else the server's label. */
export function askedFor(t: TFn, job: JobCard): string {
  const label = (job.request_type && known(t, `jobTypes.${job.request_type}`)) || job.item_label || t("tracker.request");
  return job.quantity && job.quantity > 1 ? `${label} (${job.quantity})` : label;
}

/** The team, in the guest's language when the page knows it. */
export function teamName(t: TFn, job: JobCard): string | null {
  return (job.department_key && known(t, `departments.${job.department_key}`)) || job.department_label || null;
}

/** "Maria · Housekeeping", or the team alone while nobody is named. */
export function staffLine(t: TFn, job: JobCard): string | null {
  const team = teamName(t, job);
  if (job.staff_first_name) return team ? `${job.staff_first_name} · ${team}` : job.staff_first_name;
  return team;
}

/** What the page writes when a chip filed the job without the model:
    "Of course. Housekeeping has been notified. Maria is on the way, about
    5 minutes." in the guest's language, saying only what is known. */
export function sentMessage(t: TFn, job: JobCard | null | undefined, handoff = false): string {
  if (!job) return t(handoff ? "chat.frontDesk" : "chat.requestSent");
  const name = job.staff_first_name;
  if (handoff) return name ? t("chat.frontDeskNamed", { name }) : t("chat.frontDesk");
  const team = teamName(t, job);
  const minutes = job.eta_minutes && job.eta_minutes > 0 ? Math.round(job.eta_minutes) : null;
  if (team && name && minutes) return t("chat.jobSentNamed", { department: team, name, minutes });
  if (team && minutes) return t("chat.jobSentTeam", { department: team, minutes });
  if (team) return t("chat.jobSentDept", { department: team });
  return t("chat.requestSent");
}
