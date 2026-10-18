"use client";

/* Live "last 20 webhook events" (03 spec 3.6 step 6). What the telephony bridge told
   Clefs about each call attempt and what Clefs answered: accepted,
   rejected (and why: unidentified line, off, over capacity, after hours),
   ended. During a test call the engineer watches this instead of reading
   server logs. Polls every 5 s while "Live" is on, and stops when the tab
   is hidden so an open console does not poll all night. */

import { useEffect, useMemo, useState } from "react";
import { LuRefreshCw } from "react-icons/lu";
import { withQuery } from "../../../../../../lib/api";
import { useFormat } from "../../../../../../lib/format";
import { Badge, Button, Card, Skeleton, Table, type Column, type Tone } from "../../../../../../ui";
import { errText, isMissingRoute, vcApi } from "../../lib";
import type { WebhookEvent } from "../../types";
import { useVoiceApi } from "../voiceApi";

const OUTCOME_TONE: Record<string, Tone> = {
  received: "info", accepting: "info", accepted: "ok", call_ended: "neutral", rejected: "warn",
  duplicate_call: "neutral", ignored: "neutral", invalid_event: "bad", accept_failed: "bad",
};

function reason(e: WebhookEvent): string {
  const d = e.detail ?? {};
  const r = (d.reason ?? d.reject_reason ?? d.error ?? "") as string;
  const ext = d.from_user ?? d.extension;
  return [r && String(r).replaceAll("_", " "), ext ? `from ${String(ext)}` : ""].filter(Boolean).join(", ");
}

export function WebhookPanel({ slug, lineId }: { slug: string; lineId: number | null }) {
  const fmt = useFormat();
  const [live, setLive] = useState(true);
  // GET /voice/webhook-events answers {events} for the whole hotel (no line
  // filter on the server), so the panel narrows to the chosen line here.
  const path = withQuery(vcApi(slug, "/webhook-events"), { limit: lineId == null ? 20 : 100 });
  const all = useVoiceApi<WebhookEvent[]>(path, "events", [path]);
  const rows = useMemo(
    () => (all.data ?? []).filter(e => lineId == null || e.voice_line_id == null || e.voice_line_id === lineId).slice(0, 20),
    [all.data, lineId],
  );
  const events = { ...all, data: all.data ? rows : undefined };
  const reload = events.reload;
  const missing = !!events.error && isMissingRoute(events.error);

  useEffect(() => {
    if (!live || missing) return;
    const t = window.setInterval(() => { if (document.visibilityState === "visible") reload(); }, 5000);
    return () => window.clearInterval(t);
  }, [live, missing, reload]);

  const columns: Column<WebhookEvent>[] = [
    { key: "at", header: "When", render: e => <span title={fmt.dateTime(e.received_at)}>{fmt.relative(e.received_at)}</span> },
    { key: "type", header: "Event", render: e => <span className="cc-mono">{(e.event_type ?? "").replace("call.", "")}</span> },
    { key: "outcome", header: "Clefs answered", render: e => <Badge tone={OUTCOME_TONE[e.outcome] ?? "neutral"} dot>{e.outcome.replaceAll("_", " ")}</Badge> },
    { key: "why", header: "Detail", hideOnMobile: true, render: e => <span className="cc-small">{reason(e)}</span> },
    { key: "call", header: "Call", hideOnMobile: true, render: e => <span className="cc-mono cx-faint">{e.call_id ? `...${e.call_id.slice(-8)}` : ""}</span> },
  ];

  return (
    <Card title="Webhook events" subtitle="The last 20 things the telephony bridge told Clefs about calls on this line, newest first."
      actions={<div className="cx-row">
        <label className="cc-check" style={{ padding: 0 }}><input type="checkbox" checked={live} onChange={e => setLive(e.target.checked)} /><span>Live</span></label>
        <Button size="sm" variant="ghost" icon={<LuRefreshCw />} aria-label="Refresh" onClick={reload} />
      </div>}>
      {missing ? <p className="cc-sub">The event feed appears once the phone module is deployed on this server.</p>
        : events.error ? <div className="cc-callout" role="alert">{errText(events.error)}</div>
          : events.loading && !events.data ? <Skeleton lines={4} />
            : <Table columns={columns} rows={events.data ?? []} rowKey={e => e.webhook_id} caption="Webhook events"
              empty="Nothing yet. Place a test call and the incoming event shows here within seconds." />}
    </Card>
  );
}
