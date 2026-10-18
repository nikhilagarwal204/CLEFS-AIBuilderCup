/* History wording for the floor device's task sheet. Pure, and only a
   type import, so node --test can load it (cardView imports runtime values
   through an extensionless path Node cannot resolve). */

import type { FeedEvent } from "./deviceApi";

// ─── History: who did it ───

/** Kinds a guest really performs; any other guest-attributed event is the
    system acting on the guest's behalf (the auto-reassign after a reopen). */
const GUEST_OWN = new Set(["created", "note", "merged", "reopened", "reopen", "cancelled", "cancel", "confirmed"]);

/**
 * The name for a history line. WHY: the server stores the acting staff
 * member in actor_staff_id but writes the DEVICE name in actor_label for
 * device actions, so "Ahmed acked" read "QA gs desk 2: Acked", and the
 * automatic reassign after a guest reopen read "guest: Assigned" (QA GS-11).
 * A known staff id wins; a guest label on a non-guest move reads "Auto".
 */
export function eventWho(ev: FeedEvent, staffName?: (id: number) => string | undefined): string {
  if (ev.actor_staff_id != null) {
    const n = staffName?.(ev.actor_staff_id);
    if (n) return n;
  }
  const guestish = ev.actor_type === "guest" || (!ev.actor_type && ev.actor_label === "guest");
  if (guestish) {
    const own = GUEST_OWN.has(ev.kind) || GUEST_OWN.has(ev.action ?? "") || GUEST_OWN.has(ev.to_state ?? "");
    return own ? "Guest" : "Auto";
  }
  if (ev.actor_staff_id != null && ev.actor_type === "device") return "Staff";
  return ev.actor_label || (ev.actor_staff_id ? "Staff" : "System");
}
