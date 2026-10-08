"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Everything the journeys did for one stay, in time order: each planned
   step (sent, skipped and why, deferred), guest replies (summaries are
   masked as transcript for the viewer's role), free-form messages, the
   arrival details the guest gave and their feedback. Read-only.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { useApi } from "../../../../../../../lib/api";
import { useFormat } from "../../../../../../../lib/format";
import { useHotel } from "../../../../../../../lib/hotel";
import { Badge, Card, EmptyState, Skeleton } from "../../../../../../../ui";
import { gstHref, humanize, jrnApi, statusTone, stepLabel } from "../../../lib";
import type { Timeline } from "../../../types";

interface Entry {
  at: string | null;
  key: string;
  dot: "gold" | "muted";
  head: React.ReactNode;
  sub?: React.ReactNode;
}

function entries(t: Timeline, fmtTime: (ts: string | null) => string): Entry[] {
  const out: Entry[] = [];
  for (const p of t.plans) {
    const at = p.last_attempt_at ?? p.due_at;
    out.push({
      at, key: `p${p.id}`, dot: ["sent", "delivered", "read", "replied"].includes(p.status) ? "gold" : "muted",
      head: <><strong>{stepLabel(p.step)}</strong>{p.seq > 1 ? ` #${p.seq}` : ""} <Badge tone={statusTone(p.status)}>{humanize(p.status)}</Badge>
        {p.channel && <Badge>{p.channel}</Badge>}</>,
      sub: [
        p.skip_reason && `Held back: ${humanize(p.skip_reason)}`,
        p.due_at && `Due ${fmtTime(p.due_at)}`,
        p.valid_until && `until ${fmtTime(p.valid_until)}`,
        p.error_code && `Error ${p.error_code}`,
        p.category_at_send && `Sent as ${p.category_at_send.toLowerCase()}`,
        p.direct_code && `Code ${p.direct_code}`,
      ].filter(Boolean).join(" · "),
    });
  }
  for (const r of t.replies) {
    out.push({ at: r.created_at, key: `r${r.id}`, dot: "gold",
      head: <><strong>Guest replied</strong> <Badge tone="info">{humanize(r.outcome)}</Badge>{r.llm_used ? <Badge>AI read</Badge> : null}</>,
      sub: r.summary_en ?? (r.summary_en === null ? "Summary hidden for your role" : undefined) });
  }
  for (const m of t.messages) {
    out.push({ at: m.sent_at, key: `m${m.id}`, dot: "muted", head: <>Message: {humanize(m.kind)} <Badge>{m.channel}</Badge></> });
  }
  if (t.prefs?.submitted_at) {
    out.push({ at: t.prefs.submitted_at, key: "prefs", dot: "gold", head: <strong>Guest shared arrival details</strong>,
      sub: [t.prefs.arrival_time && `Arriving ${t.prefs.arrival_time}`, t.prefs.occasion, t.prefs.requests].filter(Boolean).join(" · ") || undefined });
  }
  if (t.feedback?.submitted_at) {
    out.push({ at: t.feedback.submitted_at, key: "fb", dot: "gold",
      head: <><strong>Feedback</strong>{t.feedback.overall != null && <Badge tone={t.feedback.overall <= 2 ? "warn" : "ok"}>{t.feedback.overall}/5</Badge>}
        {t.feedback.followup_ref && <Badge tone="info">Follow-up filed for the GM</Badge>}</>,
      sub: [t.feedback.went_well && `Went well: ${t.feedback.went_well}`, t.feedback.improve && `Improve: ${t.feedback.improve}`].filter(Boolean).join(" · ") || undefined });
  }
  // Offsets may differ between rows, so compare instants, not strings.
  const t0 = (e: Entry) => (e.at ? Date.parse(e.at) || 0 : Number.MAX_SAFE_INTEGER);
  return out.sort((a, b) => t0(a) - t0(b));
}

export default function TimelineView({ reservationId }: { reservationId: number | null }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const res = useApi<Timeline>(reservationId ? jrnApi(slug, `/reservations/${reservationId}/timeline`) : null, [slug, reservationId]);

  if (reservationId === null) return <EmptyState title="Not a reservation number" />;
  if (res.error) return <EmptyState title="Could not load the timeline" body={res.error.message} />;
  const list = res.data ? entries(res.data, ts => (ts ? fmt.dateTime(ts) : "-")) : [];

  return (
    <Card title={`Reservation #${reservationId}`} subtitle="Every journey step, reply and message for this stay, oldest first."
      actions={<Link className="gst-small" href={gstHref(slug, "/journeys")}>All journeys</Link>}>
      {res.loading && !res.data ? <Skeleton lines={6} /> : list.length === 0 ? (
        <EmptyState title="Nothing planned for this stay yet" body="Steps are planned when the booking reaches Clefs from the PMS." />
      ) : (
        <ol className="gst-tl">
          {list.map(e => (
            <li key={e.key}>
              <span className={`gst-tl__dot${e.dot === "muted" ? " gst-tl__dot--muted" : ""}`} aria-hidden="true" />
              <div>
                <div className="gst-tl__head">{e.head}<span className="gst-small gst-muted">{e.at ? fmt.dateTime(e.at) : ""}</span></div>
                {e.sub ? <div className="gst-tl__sub">{e.sub}</div> : null}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
