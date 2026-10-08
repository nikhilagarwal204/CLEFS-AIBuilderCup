"use client";

/* The floor queue (03 spec 1.6/1.8): "Mine" first, then the team's open work
   in this device's scope, most urgent on top (the server orders L1 first,
   then the earliest ack due). A card with actions still waiting in the
   outbox shows "Sending..." and its optimistic state, so an offline tap is
   visible at once and never looks lost. */

import { useMemo, useState } from "react";
import { Badge } from "../../ui";
import { isUrgent, UNACKED_STATES, type Card } from "../lib/deviceApi";
import {
  ageText, cardTitle, descriptionOf, languageLabel, roomLabel, slaText, stateLabel, stateTone,
} from "../lib/cardView";

export type QueueTab = "mine" | "team" | "open";

export function Queue({
  cards, staffIds, meId, deviceId, pendingIds, now, onOpen, loading,
}: {
  cards: Card[];
  /** Everyone on shift on this device (the shared-device pool). */
  staffIds: number[];
  meId: number;
  deviceId: number | null;
  /** Request ids with actions still in the outbox. */
  pendingIds: Set<number>;
  now: number;
  onOpen: (c: Card) => void;
  loading: boolean;
}) {
  const mine = useMemo(
    () => cards.filter(c => c.assigned_staff_id === meId || (deviceId != null && c.assigned_device_id === deviceId && c.assigned_staff_id == null)),
    [cards, meId, deviceId],
  );
  const unassigned = useMemo(() => cards.filter(c => c.state === "new" || c.state === "reopened"), [cards]);
  const [tab, setTab] = useState<QueueTab | null>(null);
  const active: QueueTab = tab ?? (mine.length ? "mine" : "team");
  const list = active === "mine" ? mine : active === "open" ? unassigned : cards;

  const tabs: { key: QueueTab; label: string; n: number }[] = [
    { key: "mine", label: "Mine", n: mine.length },
    { key: "open", label: "Unassigned", n: unassigned.length },
    { key: "team", label: "All in my area", n: cards.length },
  ];

  return (
    <>
      <div className="svc-dv-tabs" role="tablist" aria-label="Queue">
        {tabs.map(t => (
          <button key={t.key} type="button" role="tab" aria-selected={active === t.key} className="svc-dv-tab" onClick={() => setTab(t.key)}>
            {t.label} <span className="svc-dv-count">{t.n}</span>
          </button>
        ))}
      </div>
      <div className="svc-dv-list">
        {list.map(c => (
          <QueueCard
            key={c.id}
            card={c}
            now={now}
            pending={pendingIds.has(c.id)}
            forMe={UNACKED_STATES.includes(c.state) && (c.assigned_staff_id === meId || staffIds.includes(c.assigned_staff_id ?? -1))}
            onOpen={onOpen}
          />
        ))}
        {!loading && list.length === 0 && (
          <div className="svc-dv-empty">
            {active === "mine" ? "Nothing assigned to you right now." : "No open requests in this area."}
          </div>
        )}
        {loading && list.length === 0 && <div className="svc-dv-empty">Loading the queue...</div>}
      </div>
    </>
  );
}

function QueueCard({ card: c, now, pending, forMe, onOpen }: {
  card: Card; now: number; pending: boolean; forMe: boolean; onOpen: (c: Card) => void;
}) {
  const sla = slaText(c, now);
  const urgent = isUrgent(c);
  const lang = languageLabel(c.language);
  const cls = ["svc-dv-card", urgent && "svc-dv-card--urgent", !urgent && forMe && c.state === "assigned" && "svc-dv-card--new",
    pending && "svc-dv-card--pending"].filter(Boolean).join(" ");
  return (
    <button type="button" className={cls} onClick={() => onOpen(c)}>
      <span className="svc-dv-room">
        {roomLabel(c)}
        {c.floor ? <small>Floor {c.floor}</small> : null}
      </span>
      <span className="svc-dv-card__body">
        <span className="svc-dv-card__title">
          {c.sensitivity === "sensitive" ? "Sensitive matter" : cardTitle(c)}
        </span>
        {c.sensitivity !== "sensitive" && descriptionOf(c) && <span className="svc-dv-card__desc">{descriptionOf(c)}</span>}
        <span className="svc-dv-card__meta">
          <Badge tone={stateTone(c.state)}>{stateLabel(c.state)}</Badge>
          {urgent && <Badge tone="bad" dot>{c.sensitivity === "emergency" ? "Emergency" : c.severity === "L1" ? "L1 urgent" : "Urgent"}</Badge>}
          {sla && <Badge tone={sla.tone}>{sla.text}</Badge>}
          {(c.reopen_count ?? 0) > 0 && <Badge tone="warn">Reopened x{c.reopen_count}</Badge>}
          {lang && <Badge tone="info">{lang}</Badge>}
          {pending && <Badge tone="neutral">Sending...</Badge>}
          <span>{c.assigned_to ? `${c.assigned_to} · ` : ""}{ageText(c, now)}</span>
        </span>
      </span>
    </button>
  );
}
