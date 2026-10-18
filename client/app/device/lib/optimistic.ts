/* What the queue should look like with the outbox applied on top of the
   last server copy. A tap offline shows its result at once ("On the way"),
   and the alarm stops for an ack that is still waiting to be sent: the
   person has seen the job, which is what the alarm asks for. The server
   copy wins again as soon as the item is sent (it leaves the outbox). */

import type { Card } from "./deviceApi";
import type { OutboxItem } from "./outboxCore";

const TO_STATE: Record<string, string> = {
  assign: "assigned",
  ack: "acknowledged",
  start: "in_progress",
  complete: "completed",
  decline: "assigned",
  handoff: "assigned",
};

export function applyPending(cards: Card[], pending: OutboxItem[]): Card[] {
  const byId = new Map<number, OutboxItem[]>();
  for (const it of pending) {
    if (it.kind !== "transition" || it.request_id == null) continue;
    const list = byId.get(it.request_id) ?? [];
    list.push(it);
    byId.set(it.request_id, list);
  }
  if (!byId.size) return cards;
  const out: Card[] = [];
  for (const c of cards) {
    const items = byId.get(c.id);
    if (!items) { out.push(c); continue; }
    let next: Card = { ...c };
    for (const it of items) {
      const to = it.action ? TO_STATE[it.action] : undefined;
      if (!to) continue;
      next = { ...next, state: to };
      if (it.action === "assign") next.assigned_staff_id = it.staff_id;
      if (it.action === "ack" && !next.assigned_staff_id) next.assigned_staff_id = it.staff_id;
      // Declined / handed off: routing picks someone else; it is not ours now.
      if (it.action === "decline" || it.action === "handoff") { next.assigned_staff_id = null; next.assigned_to = null; }
    }
    if (next.state !== "completed" && next.state !== "cancelled") out.push(next);
  }
  return out;
}

export function pendingRequestIds(pending: OutboxItem[]): Set<number> {
  return new Set(pending.filter(p => p.request_id != null).map(p => p.request_id as number));
}
