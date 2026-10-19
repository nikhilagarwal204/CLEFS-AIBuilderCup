/**
 * Service desk wording (05 spec): the manager console names escalation steps
 * the way the deck does ("Supervisor (3 min)", never L1/L2/L3), shows URGENT
 * for priority high and "Emergency" for the emergency severity, walks a job
 * through its proof and guest stages, freezes "time taken" at acceptance,
 * and turns recovery cases into "Needs your approval" rows whatever field
 * the server used for the AI-suggested gesture. `npm test` picks it up;
 * alone, run it from this folder (the brackets in the path read as a glob):
 * node --test deskLabels.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  approvalItem, approvalItems, clockText, closureLabel, emergencyChip, escalationChip, escalationLabel,
  escalationStep, etaChip, eventText, graceStatus, guestChip, isEmergency, liveView, moodChip, needsApproval,
  photoChip, photoSrc, photoState, priorityClass, priorityLabel, staffOptionLabel, suggestionOf, timeTaken, toMs,
} from "../deskLabels.ts";

const T0 = Date.UTC(2026, 9, 19, 14, 0, 0);
const MIN = 60_000;

// ─── Escalation ───

test("ladder steps read as the deck's labels, from a step name or the older level number", () => {
  assert.equal(escalationLabel("supervisor"), "Supervisor (3 min)");
  assert.equal(escalationLabel("duty_manager"), "Duty manager (5 min)");
  assert.equal(escalationLabel("gm"), "GM (10 min)");
  assert.equal(escalationLabel(1), "Supervisor (3 min)");
  assert.equal(escalationLabel(2), "Duty manager (5 min)");
  assert.equal(escalationLabel(3), "GM (10 min)");
  assert.equal(escalationLabel("Duty Manager"), "Duty manager (5 min)");
  assert.equal(escalationLabel("2"), "Duty manager (5 min)");
});

test("no step yet means no chip, and nothing ever says L1, L2 or L3", () => {
  for (const level of [null, undefined, 0, "", "assignee", "nonsense"]) {
    assert.equal(escalationStep(level), null);
    assert.equal(escalationChip({ escalation_level: level }), null);
  }
  for (const level of [1, 2, 3, "supervisor", "duty_manager", "gm"]) {
    const chip = escalationChip({ escalation_level: level, severity: "L3" });
    assert.ok(chip);
    assert.doesNotMatch(`${chip.label} ${chip.title}`, /\bL[123]\b/);
  }
});

test("the GM step is the loud one; earlier steps warn", () => {
  assert.equal(escalationChip({ escalation_level: "supervisor" }).tone, "warn");
  assert.equal(escalationChip({ escalation_level: "duty_manager" }).tone, "warn");
  assert.equal(escalationChip({ escalation_level: "gm" }).tone, "bad");
  assert.match(escalationChip({ escalation_level: "supervisor" }).title, /within 3 minutes/);
});

test("on the urgent path the chip names who was paged, not a minute mark", () => {
  const chip = escalationChip({ escalation_level: 3, severity: "L1" });
  assert.equal(chip.label, "Duty manager and GM paged");
  assert.equal(escalationChip({ escalation_level: "security", sensitivity: "emergency" }).label, "Security paged");
});

// ─── Priority and emergencies ───

test("URGENT is priority high; emergencies get their own badge", () => {
  assert.equal(priorityLabel("high"), "Urgent");
  assert.equal(priorityLabel("medium"), "Medium");
  assert.equal(priorityLabel("low"), "Low");
  assert.equal(priorityLabel("normal"), "Normal");
  assert.equal(priorityClass("normal"), "medium");
  assert.equal(priorityClass("high"), "high");
  assert.equal(isEmergency({ severity: "L1" }), true);
  assert.equal(isEmergency({ sensitivity: "emergency" }), true);
  assert.equal(isEmergency({ request_type_key: "emergency" }), true);
  assert.equal(isEmergency({ severity: "L2", priority: "high" }), false);
  assert.equal(emergencyChip({ severity: "L1" }).label, "Emergency");
  assert.equal(emergencyChip({ severity: "L3" }), null);
});

// ─── Time taken and ETA ───

test("clock readings", () => {
  assert.equal(clockText(41_000), "00:41");
  assert.equal(clockText(12 * MIN + 5_000), "12:05");
  assert.equal(clockText(62 * MIN + 3_000), "1:02:03");
  assert.equal(clockText(3 * 86_400_000 + 4 * 3_600_000), "3d 4h");
  assert.equal(clockText(-5), "00:00");
});

test("time taken counts until a person accepts, then freezes", () => {
  const accepted = timeTaken({ state: "acknowledged", timestamp: T0, acknowledged_at: T0 + 41_000 }, T0 + 60 * MIN);
  assert.equal(accepted.label, "Accepted in 00:41");
  assert.equal(accepted.live, false);
  assert.equal(accepted.tone, "ok");
  const waiting = timeTaken({ state: "assigned", timestamp: T0 }, T0 + 4 * MIN + 2_000);
  assert.equal(waiting.label, "Waiting 04:02");
  assert.equal(waiting.live, true);
  assert.equal(waiting.tone, "warn");
  assert.equal(timeTaken({ state: "new", timestamp: T0 }, T0 + 11 * MIN).tone, "bad");
  // ISO instants work too; the clock has not started on the first render
  assert.equal(timeTaken({ state: "completed", timestamp: T0, acknowledged_at: new Date(T0 + 5 * MIN).toISOString() }, 0).label,
    "Accepted in 05:00");
  assert.equal(timeTaken({ state: "new", timestamp: T0 }, 0), null);
  // pre-v2 rows (no state) never had an acceptance clock
  assert.equal(timeTaken({ status: "progress", timestamp: T0, acknowledged_at: T0 + MIN }, T0), null);
});

test("ETA shows while the job is open and says when it has passed", () => {
  assert.equal(etaChip({ state: "assigned", eta_minutes: 5 }, T0).label, "ETA 5 min");
  assert.equal(etaChip({ state: "in_progress", eta_minutes: 5, eta_at: T0 - MIN }, T0).label, "ETA 5 min (late)");
  assert.equal(etaChip({ state: "completed", eta_minutes: 5 }, T0), null);
  assert.equal(etaChip({ state: "assigned", eta_minutes: null }, T0), null);
});

// ─── Proof and the guest's answer ───

test("photo states", () => {
  assert.equal(photoChip({ proof_status: "checking" }).label, "Checking photo");
  assert.equal(photoChip({ proof_status: "pending", proof_photo_url: "/x.jpg" }).label, "Checking photo");
  assert.equal(photoChip({ proof_status: "pending" }), null);
  assert.equal(photoChip({ proof_status: "verified" }).label, "Photo verified");
  const rejected = photoChip({ proof_status: "rejected", proof_reason: "No towels visible in the photo" });
  assert.equal(rejected.label, "Photo rejected: No towels visible in the photo");
  assert.equal(rejected.tone, "bad");
  assert.match(rejected.title, /supervisor was alerted/);
  assert.equal(photoChip({ proof_status: "rejected" }).label, "Photo rejected");
  // next to a ProofAlert (which carries the reason) the chip stays short
  assert.equal(photoChip({ proof_status: "rejected", proof_reason: "Wrong room" }, true).label, "Photo rejected");
  assert.equal(photoState({ proof_status: null }), null);
  // the server's "unverified": the automatic check could not run, a person looks
  const unverified = photoChip({ proof_status: "unverified" });
  assert.equal(unverified.label, "Checking photo");
  assert.match(unverified.title, /supervisor was asked to look/);
  // a reason that ends with a full stop is not doubled
  assert.match(photoChip({ proof_status: "rejected", proof_reason: "Wrong room." }).title, /^Wrong room\. The job/);
});

test("guest stages after the job is done", () => {
  assert.equal(guestChip({ state: "completed", resolved_at: T0 }).label, "Waiting for guest");
  assert.equal(guestChip({ state: "completed", resolved_at: T0, guest_confirmation: "yes", guest_confirmed_at: T0 + MIN }).label,
    "Guest confirmed");
  assert.equal(guestChip({ state: "reopened", guest_confirmation: "no", guest_confirmed_at: T0 + MIN }).label,
    "Not fixed - recovery opened");
  // done again after the "not fixed": waiting for a fresh answer
  assert.equal(guestChip({ state: "completed", resolved_at: T0 + 9 * MIN, guest_confirmation: "no", guest_confirmed_at: T0 + MIN }).label,
    "Waiting for guest");
  assert.equal(guestChip({ state: "in_progress" }), null);
});

test("closed jobs carry their closure reason", () => {
  assert.equal(guestChip({ state: "completed", closed_at: T0, closure_reason: "no_reply" }).label, "Closed: no reply from the guest");
  // the guest's yes closes the job at once: it reads as the confirmation
  const confirmed = guestChip({ state: "completed", closed_at: T0, closure_reason: "guest_confirmed", guest_confirmation: "yes" });
  assert.equal(confirmed.label, "Guest confirmed");
  assert.equal(confirmed.tone, "ok");
  assert.match(confirmed.title, /^Closed/);
  // a cancelled job says so in its state chip already
  assert.equal(guestChip({ state: "cancelled", closed_at: T0, closure_reason: "cancelled" }), null);
  assert.equal(closureLabel(null), "Closed");
  assert.equal(closureLabel("manager_override"), "Closed by a manager");
  assert.equal(closureLabel("some_new_reason"), "Closed: some new reason");
});

// ─── Mood ───

test("mood chips: frustration in percent, calm guests stay quiet when only the notable ones are wanted", () => {
  assert.equal(moodChip("annoyed", 0.8).label, "Guest annoyed, frustration 80%");
  assert.equal(moodChip("annoyed", 0.8).tone, "bad");
  assert.equal(moodChip("impatient", 0.2).tone, "warn");
  assert.equal(moodChip("calm", 0.1, true), null);
  assert.equal(moodChip(null, 75).label, "Frustration 75%");
  assert.equal(moodChip(null, null), null);
  assert.equal(moodChip("distressed", null).label, "Guest distressed");
});

// ─── Recovery approvals ───

test("the suggested gesture is read from whichever field carries it", () => {
  assert.equal(suggestionOf({ suggested_gesture: "Complimentary dessert" }), "Complimentary dessert");
  assert.equal(suggestionOf({ suggestion: { text: "Late checkout at 2 pm" } }), "Late checkout at 2 pm");
  assert.equal(suggestionOf({ gestures: [{ status: "given", kind: "amenity" }, { status: "pending_approval", kind: "late_checkout", value: "0" }] }),
    "Late checkout");
  assert.equal(suggestionOf({ gestures: [{ status: "pending_approval", kind: "fnb_credit", value: "150.00", currency: "AED" }] }),
    "Food and drink credit worth AED 150.00");
  assert.equal(suggestionOf({}), "");
});

test("only undecided not-fixed, frustration and never-arrived cases need approval", () => {
  assert.equal(needsApproval({ id: 1, status: "open", kind: "not_fixed", suggested_gesture: "Dessert" }), true);
  assert.equal(needsApproval({ id: 2, status: "open", reason: "not_resolved" }), true);
  assert.equal(needsApproval({ id: 3, status: "open", kind: "never_arrived" }), true);
  assert.equal(needsApproval({ id: 4, status: "open", kind: "frustration", decided_at: "2026-10-19T14:00:00Z" }), false);
  assert.equal(needsApproval({ id: 5, status: "resolved", kind: "not_fixed" }), false);
  assert.equal(needsApproval({ id: 6, status: "open", reason: "manual" }), false);
  assert.equal(needsApproval({ id: 7, status: "open", reason: "not_resolved", gestures: [{ status: "given" }] }), false);
  assert.equal(needsApproval({ id: 8, status: "open", reason: "complaint", gestures: [{ status: "pending_approval", kind: "amenity" }] }), true);
});

test("a panel row has the room, the kind in words, the guest's mood and the suggestion", () => {
  const item = approvalItem({
    id: 12, room: "204", kind: "frustration", mood: "angry", frustration: 0.9,
    suggested_gesture: "Send a fruit plate with a note", summary: "Third time asking for towels", opened_at: T0,
  });
  assert.deepEqual(
    { id: item.id, room: item.room, kind: item.kindLabel, mood: item.mood.label, suggestion: item.suggestion, summary: item.summary },
    { id: 12, room: "204", kind: "Guest frustrated", mood: "Guest angry, frustration 90%",
      suggestion: "Send a fruit plate with a note", summary: "Third time asking for towels" },
  );
  assert.equal(approvalItem({ room: "204" }), null);
  const fromRequest = approvalItem({ id: 3, reason: "not_resolved", request: { id: 9, room: "512", mood: "annoyed" } });
  assert.equal(fromRequest.room, "512");
  assert.equal(fromRequest.requestId, 9);
  assert.equal(fromRequest.kindLabel, "Not fixed");
});

test("a case as the recovery route sends it (case_view)", () => {
  const view = {
    id: 31, request_id: 77, room: "204", status: "open", reason: "not_resolved", kind: "not_fixed",
    approval_status: "pending", needs_approval: true, summary: "Guest says the towels never came",
    suggested_gesture: "We are sorry it was not right the first time. A fruit platter is on its way.",
    suggested_kind: "amenity", suggested_value_minor: 5000, suggested_value: "50.00", currency: "AED",
    suggestion_source: "ai", opened_at: "2026-10-19T14:00:00+00:00",
    request: { id: 77, room: "204", item_label: "Towels x2", state: "reopened", staff_first_name: "Maria" },
    gestures: [],
  };
  assert.equal(needsApproval(view), true);
  const item = approvalItem(view, id => (id === 77 ? { mood: "annoyed", frustration: 0.72 } : null));
  assert.equal(item.kindLabel, "Not fixed");
  assert.equal(item.value, "AED 50.00");
  assert.equal(item.item, "Towels x2");
  assert.equal(item.suggestionLabel, "AI-suggested gesture");
  assert.equal(item.mood.label, "Guest annoyed, frustration 72%");
  assert.equal(item.openedAt, Date.UTC(2026, 9, 19, 14, 0, 0));
  assert.equal(approvalItem({ ...view, suggestion_source: "default", suggested_value: null }).suggestionLabel,
    "Suggested gesture (standard)");
  assert.equal(approvalItem({ ...view, suggested_value: null }).value, null);
  // decided cases leave the panel, whatever their gestures say
  assert.equal(needsApproval({ ...view, needs_approval: false, approval_status: "approved" }), false);
  assert.equal(needsApproval({ ...view, needs_approval: undefined, approval_status: "dismissed" }), false);
  assert.equal(needsApproval({ id: 4, status: "open", reason: "complaint", approval_status: null }), false);
});

test("approval rows: undecided only, oldest first, from a bare list or {items}", () => {
  const cases = [
    { id: 2, status: "open", kind: "not_fixed", opened_at: T0 + MIN },
    { id: 1, status: "open", kind: "never_arrived", opened_at: T0 },
    { id: 3, status: "closed", kind: "not_fixed", opened_at: T0 - MIN },
  ];
  assert.deepEqual(approvalItems(cases).map(i => i.id), [1, 2]);
  assert.deepEqual(approvalItems({ items: cases }).map(i => i.id), [1, 2]);
  assert.deepEqual(approvalItems(null), []);
});

// ─── Choose staff ───

test("staff options read like the dispatcher ranks them", () => {
  assert.equal(staffOptionLabel({ staff_id: 1, name: "Maria Santos", open_jobs: 1, eta_minutes: 8 }), "Maria Santos · 1 open · ETA 8 min");
  assert.equal(staffOptionLabel({ staff_id: 1, name: "Maria Santos", open_jobs: 0, eta_minutes: 5, current: true }),
    "Maria Santos · free · ETA 5 min (has it now)");
  assert.equal(staffOptionLabel({ staff_id: 2, name: "Priya Sharma", reason: "not_on_shift" }), "Priya Sharma · not on shift");
  assert.equal(staffOptionLabel({ staff_id: 3, name: "Joy Dela Cruz", reason: "at_capacity", open_jobs: 4 }),
    "Joy Dela Cruz · at capacity (4 open)");
  assert.equal(staffOptionLabel({ staff_id: 4, name: "Ana", reason: "something_new" }), "Ana · something new");
});

// ─── Live link, timeline, photo source ───

test("live link pill", () => {
  assert.equal(liveView("live").label, "Live");
  assert.equal(liveView("reconnecting").label, "Reconnecting");
  assert.equal(liveView("polling").label, "Reconnecting");
  assert.equal(liveView(null).label, "Connecting");
});

test("a short drop does not flash Reconnecting; a long one does", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const shown = [];
  const g = graceStatus(s => shown.push(s), 2_500);
  g.onStatus("reconnecting"); // before the first live: at once
  g.onStatus("live");
  g.onStatus("reconnecting"); // a Cloud Run stream recycle
  t.mock.timers.tick(1_000);
  g.onStatus("live");
  t.mock.timers.tick(5_000);
  assert.deepEqual(shown, ["reconnecting", "live", "live"]);
  g.onStatus("polling"); // a real outage
  t.mock.timers.tick(2_500);
  assert.deepEqual(shown, ["reconnecting", "live", "live", "polling"]);
  g.onStatus("reconnecting");
  g.cancel(); // unmounted: nothing more is shown
  t.mock.timers.tick(5_000);
  assert.equal(shown.length, 4);
});

test("timeline lines", () => {
  assert.equal(eventText({ kind: "created", actor_label: "Clefs" }), "Request filed by Clefs");
  assert.equal(eventText({ kind: "transition", action: "ack", actor_label: "Maria Santos" }), "Accepted by Maria Santos");
  assert.equal(eventText({ kind: "escalated", action: "supervisor", payload: { level: 1, to: "supervisor", recipients: ["Ana Reyes"] } }),
    "Escalated: Supervisor (3 min), Ana Reyes");
  assert.equal(eventText({ kind: "escalated", payload: { level: 2, to: "duty_manager", urgent: true } }), "Paged at once: Duty manager");
  assert.equal(eventText({ kind: "escalated", payload: { level: 1, to: "supervisor", shadow: true } }),
    "Escalated: Supervisor (3 min) (shadow mode: nobody was messaged)");
  assert.equal(eventText({ kind: "proof", payload: { verified: false, reason: "Bed not made" } }),
    "Photo rejected: Bed not made. Supervisor alerted");
  // the service module's own kinds (proof.py, recovery.py, staff_lang.py, sla.py)
  assert.equal(eventText({ kind: "proof_rejected", payload: { status: "rejected", reason: "No towels in the photo." } }),
    "Photo rejected: No towels in the photo. Supervisor alerted");
  assert.match(eventText({ kind: "proof_unverified", payload: { status: "unverified" } }), /could not be checked/);
  assert.equal(eventText({ kind: "recovery", payload: { kind: "frustration", to: "duty_manager" } }),
    "Recovery case opened (guest frustrated). Duty manager alerted");
  assert.equal(eventText({ kind: "guest_message", actor_label: "Maria Santos", payload: { kind: "staff_reply", via: "voice", text_original: "Papunta na ako" } }),
    "Staff reply by Maria Santos (voice): Papunta na ako");
  assert.equal(eventText({ kind: "guest_message", payload: { kind: "gesture", text_original: "A fruit platter is on its way" } }),
    "Gesture sent to the guest: A fruit platter is on its way");
  assert.equal(eventText({ kind: "closed", payload: { closure_reason: "no_reply" } }), "Closed: no reply from the guest");
  assert.equal(eventText({ kind: "note", payload: { note: "Guest asked for two" } }), "Note: Guest asked for two");
  assert.equal(eventText({ kind: "sla_breach", action: "ack" }), "Acceptance target missed");
});

test("photo sources: signed URLs as they are, API paths through the proxy", () => {
  assert.equal(photoSrc("https://storage.googleapis.com/b/p.jpg?X-Goog-Signature=1"), "https://storage.googleapis.com/b/p.jpg?X-Goog-Signature=1");
  assert.equal(photoSrc("/hotels/demo/service/requests/4/proof-photo"), "/api/clefs/hotels/demo/service/requests/4/proof-photo");
  assert.equal(photoSrc("/api/clefs/files/abc"), "/api/clefs/files/abc");
  assert.equal(photoSrc("javascript:alert(1)"), null);
  assert.equal(photoSrc(""), null);
});

test("instants: epoch ms, epoch seconds and ISO strings", () => {
  assert.equal(toMs(T0), T0);
  assert.equal(toMs(T0 / 1000), T0);
  assert.equal(toMs(new Date(T0).toISOString()), T0);
  assert.equal(toMs("not a date"), null);
  assert.equal(toMs(0), null);
});
