"use client";

/* Handover at login (03 spec 1.6): computed on read, and the queue stays
   closed until the incoming person acknowledges it. It shows open and
   overdue work in scope, out-of-order rooms, open Engineering jobs, VIP and
   returning arrivals today, the last 24 h of incidents, lost-and-found from
   the last 72 h, and the outgoing shift's notes.

   If the handover cannot load (offline, server down), the person may still
   open the queue: that ack is recorded as "unavailable" so nobody is locked
   out of their shift by a network blip. */

import { formatTime } from "../../lib/format";
import { Badge, Button } from "../../ui";
import type { Card, Handover } from "../lib/deviceApi";
import { cardTitle, roomLabel } from "../lib/cardView";

function Section({ title, n, children }: { title: string; n: number; children: React.ReactNode }) {
  if (!n) return null;
  return (
    <div className="svc-dv-section">
      <h3>{title} <span className="svc-dv-count">{n}</span></h3>
      {children}
    </div>
  );
}

function CardLines({ cards }: { cards: Card[] }) {
  return (
    <ul className="svc-dv-list-plain">
      {cards.slice(0, 12).map(c => <li key={c.id}><strong>{roomLabel(c)}</strong> {cardTitle(c)}{c.assigned_to ? ` · ${c.assigned_to}` : ""}</li>)}
      {cards.length > 12 && <li>and {cards.length - 12} more in the queue</li>}
    </ul>
  );
}

export function HandoverSheet({
  staffName, handover, loading, error, timezone, locale, onAck, onRetry,
}: {
  staffName: string;
  handover: Handover | null;
  loading: boolean;
  error: string | null;
  timezone: string;
  locale: string;
  onAck: (unavailable: boolean) => void;
  onRetry: () => void;
}) {
  const h = handover;
  const nothing = h && !h.open.length && !h.overdue.length && !h.rooms_out_of_order.length && !h.engineering_open.length
    && !h.arrivals.length && !h.incidents.length && !h.lost_found.length && !h.notes.length;

  return (
    <div className="svc-dv-scrim" role="presentation">
      <div className="svc-dv-sheet svc-dv-sheet--full" role="dialog" aria-modal="true" aria-labelledby="svc-dv-ho-title">
        <div className="svc-dv-sheet__head">
          <h2 id="svc-dv-ho-title" className="svc-dv-sheet__title">
            Handover for {staffName}
            <span style={{ display: "block", fontSize: "0.8rem", color: "var(--clefs-ink-faint)", fontWeight: 400, marginTop: 4 }}>
              Read this before your queue opens.
            </span>
          </h2>
        </div>

        {loading && <div className="svc-dv-empty">Loading the handover...</div>}
        {error && !loading && (
          <div className="svc-dv-banner svc-dv-banner--warn" style={{ margin: 0 }}>
            <span>{error}</span>
            <Button size="sm" onClick={onRetry}>Retry</Button>
          </div>
        )}

        {h && !loading && (
          <>
            {!h.from_server && (
              <div className="svc-dv-banner" style={{ margin: 0 }}>
                <span>Showing open work only. Notes, arrivals and incidents appear here once the handover service is on.</span>
              </div>
            )}
            {nothing && <div className="svc-dv-empty">Nothing handed over. A quiet start.</div>}
            <Section title="Notes from the last shift" n={h.notes.length}>
              <ul className="svc-dv-list-plain">
                {h.notes.map(n => <li key={n.id}>{n.body}{n.author ? <em> ({n.author})</em> : null}</li>)}
              </ul>
            </Section>
            <Section title="Overdue" n={h.overdue.length}><CardLines cards={h.overdue} /></Section>
            <Section title="Open requests" n={h.open.length}><CardLines cards={h.open} /></Section>
            <Section title="Open engineering jobs" n={h.engineering_open.length}><CardLines cards={h.engineering_open} /></Section>
            <Section title="Rooms out of order" n={h.rooms_out_of_order.length}>
              <ul className="svc-dv-list-plain">
                {h.rooms_out_of_order.map(r => <li key={r.room}><strong>{r.room}</strong> {r.status ?? ""}{r.note ? ` · ${r.note}` : ""}</li>)}
              </ul>
            </Section>
            <Section title="VIP and returning arrivals today" n={h.arrivals.length}>
              <ul className="svc-dv-list-plain">
                {h.arrivals.map((a, i) => (
                  <li key={`${a.room}-${i}`}>
                    <strong>{a.room ?? "TBA"}</strong> {a.name ?? "Guest"}{" "}
                    {a.vip ? <Badge tone="gold">VIP</Badge> : null} {a.returning ? <Badge tone="info">Returning</Badge> : null}
                  </li>
                ))}
              </ul>
            </Section>
            <Section title="Incidents, last 24 hours" n={h.incidents.length}>
              <ul className="svc-dv-list-plain">
                {h.incidents.map(x => (
                  <li key={x.id}>
                    {x.kind.replace(/_/g, " ")}{x.room ? ` · ${x.room}` : x.floor ? ` · floor ${x.floor}` : ""}
                    {x.opened_at ? ` · ${formatTime(x.opened_at, timezone, locale)}` : ""}{x.status ? ` · ${x.status}` : ""}
                  </li>
                ))}
              </ul>
            </Section>
            <Section title="Lost and found, last 72 hours" n={h.lost_found.length}>
              <ul className="svc-dv-list-plain">
                {h.lost_found.map(x => <li key={x.id}>{x.description}{x.location || x.room ? ` · ${x.location ?? x.room}` : ""}</li>)}
              </ul>
            </Section>
          </>
        )}

        <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 10 }}>
          <Button variant="primary" className="svc-dv-big" block disabled={loading || (!h && !error)} onClick={() => onAck(!h)}>
            {h ? "I have read the handover" : "Open my queue without it"}
          </Button>
        </div>
      </div>
    </div>
  );
}
