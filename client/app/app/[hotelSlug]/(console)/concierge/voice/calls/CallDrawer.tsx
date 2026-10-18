"use client";

/* One phone call: facts, transcript and tool calls. The transcript is
   whatever the server returns for the viewer's role (null when masked);
   the console additionally hides it for a role the matrix denies, so an
   older server that forgot to mask never shows words to the wrong role. */

import { useApi } from "../../../../../../lib/api";
import { useFormat } from "../../../../../../lib/format";
import { useSession } from "../../../../../../lib/session";
import { Badge, Drawer, Skeleton } from "../../../../../../ui";
import { CALL_TONE, LANGUAGE_NAMES, durationText, errText, transcriptPolicy, vcApi } from "../../lib";
import type { VoiceCall } from "../../types";
import { toCallDetail } from "../voiceApi";

export function CallDrawer({ slug, call, onClose }: { slug: string; call: VoiceCall; onClose: () => void }) {
  const fmt = useFormat();
  const { role, isAdmin } = useSession();
  const policy = transcriptPolicy(role, isAdmin);
  const raw = useApi<unknown>(vcApi(slug, `/calls/${call.id}`), [call.id]);
  const detail = { ...raw, data: toCallDetail(raw.data) };
  const c = detail.data ?? call;
  const turns = policy === "full" ? detail.data?.transcript ?? null : null;

  return (
    <Drawer open onClose={onClose} wide title={`Call ${c.room ? `from room ${c.room}` : c.from_user ? `from ext ${c.from_user}` : ""}`}>
      <div className="cx-stack">
        <div className="cx-row">
          <Badge tone={CALL_TONE[c.status] ?? "neutral"} dot>{c.status}</Badge>
          {c.emergencies > 0 && <Badge tone="bad">Emergency</Badge>}
          {c.transferred && <Badge tone="info">Transferred to front desk</Badge>}
        </div>
        <dl className="cc-kv">
          <dt>Started</dt><dd>{fmt.dateTime(c.started_at)}</dd>
          <dt>Length</dt><dd>{durationText(c.duration_s) || (c.ended_at ? "" : "in progress or unknown")}</dd>
          <dt>Room known by</dt><dd>{c.room_confirmed_by === "extension" ? "Mapped extension (verified)" : c.room_confirmed_by === "spoken" ? "Caller said it (staff confirm at the door)" : "Not known"}</dd>
          <dt>Language</dt><dd>{c.language ? LANGUAGE_NAMES[c.language] ?? c.language : ""}</dd>
          <dt>Requests filed</dt><dd>{c.requests_created}</dd>
          {c.end_reason && <><dt>Ended</dt><dd>{c.end_reason.replaceAll("_", " ")}</dd></>}
          {c.reject_reason && <><dt>Refused</dt><dd>{c.reject_reason.replaceAll("_", " ")} (486 busy, the PBX rang its fallback)</dd></>}
          {c.prompt_version && <><dt>Prompt</dt><dd className="cc-mono">{c.prompt_version}</dd></>}
        </dl>

        <span className="cc-label">Transcript</span>
        {detail.error ? <p className="cc-sub">{errText(detail.error)}</p>
          : detail.loading && !detail.data ? <Skeleton lines={5} />
            : policy === "none" || turns === null ? <p className="cc-hidden">Hidden for your role, or not recorded.</p>
              : turns.length === 0 ? <p className="cc-sub">Nothing was said, or the caller did not consent to recording.</p>
                : (
                  <div className="cc-thread">
                    {turns.map((t, i) => (
                      <div key={i} className={`cc-turn${t.role === "user" ? " cc-turn--user" : ""}`}>
                        <div className="cc-turn__text" dir="auto">{t.message}</div>
                        <span className="cc-turn__meta">{t.role === "user" ? "Caller" : "Concierge"} {fmt.time(t.timestamp)}</span>
                      </div>
                    ))}
                  </div>
                )}

        {detail.data?.tool_calls && detail.data.tool_calls.length > 0 && (
          <>
            <span className="cc-label">What the AI did</span>
            {detail.data.tool_calls.map(t => (
              <div key={t.id} className="cc-spread cc-small">
                <span className="cc-mono">{t.name}</span>
                <span className="cx-muted">{String((t.result as { status?: unknown } | null)?.status ?? "")}</span>
              </div>
            ))}
          </>
        )}
      </div>
    </Drawer>
  );
}
