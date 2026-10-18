"use client";

/* ═══════════════════════════════════════════════════════════════════════
   StateTimeline: one action's life in one list (GET /brief/actions/{id}/
   timeline): every state change from the foundation's action_events, each
   brief that surfaced it (and whether it was pinned Overdue), and each
   escalation level that fired with who was nudged on which channel. It is
   what an owner reads when asking "who knew, and when".
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import type { ReactNode } from "react";
import { useFormat } from "../../../../lib/format";
import { Badge, Button, EmptyState, Skeleton } from "../../../../ui";
import type { ApiError } from "../../../../lib/api";
import { brfHref, CADENCE_LABEL, eventLabel, statusLabel, VIA_LABEL } from "./lib";
import type { TimelineEntry } from "./types";

const LEVEL_TEXT: Record<number, string> = {
  1: "Deadline passed: the assignee was reminded",
  2: "4 hours late: escalated to the GM",
  3: "A day late: escalated to the owner and pinned Overdue",
};

function Entry({ slug, e }: { slug: string; e: TimelineEntry }) {
  const fmt = useFormat();
  let what: ReactNode;
  let detail: ReactNode = null;
  if (e.kind === "event") {
    what = eventLabel(e.event);
    const bits = [
      e.actor_label || (e.actor_type === "dial" ? "Autonomy dial" : e.actor_type === "system" ? "Clefs" : null),
      e.via ? `via ${VIA_LABEL[e.via] ?? e.via}` : null,
      e.from_status !== e.to_status && e.to_status ? `${statusLabel(e.from_status)} to ${statusLabel(e.to_status)}` : null,
    ].filter(Boolean);
    detail = bits.join(" · ");
  } else if (e.kind === "surfaced") {
    what = <>In the {CADENCE_LABEL[e.cadence]?.toLowerCase() ?? e.cadence} brief as item {e.item_no} {e.pinned && <Badge tone="bad">Overdue</Badge>}</>;
    detail = <Link className="cx-link" href={brfHref(slug, `?run=${e.run_id}`)}>Open the brief of {fmt.date(e.brief_date + "T12:00:00Z", "medium")}</Link>;
  } else {
    what = LEVEL_TEXT[e.level] ?? `Escalation level ${e.level}`;
    detail = e.notified.length
      ? e.notified.map((n, i) => (
        <Badge key={i} tone={n.ok ? "ok" : "bad"}>{n.channel ?? "?"}{n.ok ? "" : `: ${n.status ?? "failed"}`}</Badge>
      ))
      : "Nobody could be reached";
  }
  return (
    <li data-kind={e.kind}>
      <span className="brf-timeline__what">{what}</span>
      {detail && <span className="brf-muted" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>{detail}</span>}
      <span className="brf-timeline__when">{fmt.dateTime(e.at, "medium")}</span>
    </li>
  );
}

export default function StateTimeline({ slug, entries, loading, error, onRetry }: {
  slug: string;
  entries: TimelineEntry[] | undefined;
  loading: boolean;
  error?: ApiError;
  onRetry: () => void;
}) {
  if (loading && !entries) return <Skeleton lines={4} />;
  if (error && !entries) {
    return <EmptyState title="Could not load the history" body={error.message} action={<Button onClick={onRetry}>Try again</Button>} />;
  }
  if (!entries?.length) return <p className="brf-muted">No history yet.</p>;
  // Newest first: the question is usually "what happened last".
  const rows = [...entries].reverse();
  return (
    <ol className="brf-timeline" aria-label="Action history">
      {rows.map((e, i) => <Entry key={`${e.kind}-${e.at}-${i}`} slug={slug} e={e} />)}
    </ol>
  );
}
