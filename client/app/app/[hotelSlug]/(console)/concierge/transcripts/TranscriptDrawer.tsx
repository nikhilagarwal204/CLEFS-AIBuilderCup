"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One conversation: every turn in order, the tools the concierge used
   (what it filed, never the arguments), and the session facts staff need
   to judge it (surface, verified room, prompt version). The words arrive
   already masked by the server (pii class "transcript"): a role without
   access gets message null and sees a placeholder, never a client-side
   guess at the matrix.
   ═══════════════════════════════════════════════════════════════════════ */

import { hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Drawer, Skeleton } from "../../../../../ui";
import { errText } from "../lib";
import type { TranscriptDetail, TranscriptSummary } from "../types";
import { SAFETY_TONE, SOURCE_LABEL } from "./labels";

const ROLE_LABEL: Record<string, string> = { user: "Guest", assistant: "Concierge" };

export function TranscriptDrawer({ slug, conv, onClose }: {
  slug: string;
  conv: TranscriptSummary | null;
  onClose: () => void;
}) {
  const fmt = useFormat();
  const detail = useApi<TranscriptDetail>(
    conv ? hotelPath(slug, `/concierge/transcripts/${encodeURIComponent(conv.id)}`) : null, [slug, conv?.id]);
  const d = detail.data && conv && detail.data.conversation.id === conv.id ? detail.data : undefined;
  const title = conv ? `${conv.room ? `Room ${conv.room}` : "No room"}, ${fmt.dateTime(conv.started_at)}` : "";

  return (
    <Drawer open={!!conv} onClose={onClose} title={title}>
      {detail.error ? <div className="cc-callout" role="alert">{errText(detail.error)}</div>
        : !d ? <Skeleton lines={6} /> : (
          <div className="cx-stack">
            <div className="cx-row">
              <Badge>{SOURCE_LABEL[d.conversation.source] ?? d.conversation.source}</Badge>
              <Badge>{d.turns.length} turns</Badge>
              {d.conversation.safety_level !== "none" && (
                <Badge tone={SAFETY_TONE[d.conversation.safety_level]} dot>{d.conversation.safety_level}</Badge>
              )}
              {d.conversation.verified === false && <Badge tone="warn">Room not verified</Badge>}
            </div>
            {d.visibility === "none" && (
              <div className="cc-callout cc-callout--info">Your role can see when this conversation happened, but not what was said.</div>
            )}
            <div className="cc-thread">
              {d.turns.map(t => (
                <div key={t.id} className={`cc-turn${t.role === "user" ? " cc-turn--user" : ""}${
                  t.safety_level && t.safety_level !== "none" ? " cc-turn--flagged" : ""}`}>
                  <div className="cc-turn__text" dir="auto">
                    {t.message ?? <span className="cc-hidden">Hidden for your role</span>}
                  </div>
                  <span className="cc-turn__meta">
                    {ROLE_LABEL[t.role] ?? t.role} {fmt.time(t.at)}
                    {t.safety_level && t.safety_level !== "none" ? `, flagged ${t.safety_level}` : ""}
                  </span>
                </div>
              ))}
            </div>
            {d.tools.length > 0 && (
              <div>
                <h3 className="cc-small cx-muted">What the concierge did</h3>
                <ul className="cc-tools">
                  {d.tools.map((t, i) => (
                    <li key={`${t.name}-${i}`} className="cx-row">
                      <span className="cc-mono">{t.name}</span>
                      <Badge tone={t.status === "ok" ? "ok" : "warn"}>{t.outcome ?? t.status}</Badge>
                      {t.request_ref && <span className="cc-small">request #{t.request_ref}</span>}
                      {t.order_ref && <span className="cc-small">order #{t.order_ref}</span>}
                      <span className="cc-small cx-muted">{fmt.time(t.at)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {d.conversation.prompt_version && (
              <p className="cc-small cx-muted">Prompt version <span className="cc-mono">{d.conversation.prompt_version.slice(0, 12)}</span></p>
            )}
          </div>
        )}
    </Drawer>
  );
}
