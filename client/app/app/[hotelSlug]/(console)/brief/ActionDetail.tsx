"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One action: what it is, where it stands, the decisions it still takes
   and its StateTimeline. Shared by the Actions drawer and the
   /brief/actions/{id} page (the evidence link a brief item falls back to),
   so a link from WhatsApp and a click in the list read the same.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useState } from "react";
import { useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import type { Action } from "../../../../lib/types";
import { ActionBar, Badge, Button, CountdownChip, EmptyState, MoneyText, Skeleton } from "../../../../ui";
import AdjustSheet from "./AdjustSheet";
import AssignSheet from "./AssignSheet";
import StateTimeline from "./StateTimeline";
import { brfApi, decisionsFor, PERM, roleLabel, statusLabel, statusTone } from "./lib";
import type { ActionTimeline } from "./types";
import { useDecide } from "./useDecide";

export default function ActionDetail({ slug, actionId, names, onChanged }: {
  slug: string;
  actionId: number;
  /** user_id -> name, for the assignee line (the action row carries ids only). */
  names?: ReadonlyMap<number, string>;
  onChanged?: (a: Action) => void;
}) {
  const fmt = useFormat();
  const tl = useApi<ActionTimeline>(brfApi(slug, `/actions/${actionId}/timeline`), [slug, actionId]);
  const [adjustOpen, setAdjustOpen] = useState(false);

  const reload = tl.reload;
  const changed = useCallback((a: Action) => { reload(); onChanged?.(a); }, [reload, onChanged]);
  const { busy, decide } = useDecide(slug, changed, () => reload());

  if (tl.loading && !tl.data) return <Skeleton lines={5} />;
  if (tl.error && !tl.data) {
    return <EmptyState title={tl.error.status === 404 ? "Action not found" : "Could not load this action"} body={tl.error.message}
                       action={<Button onClick={tl.reload}>Try again</Button>} />;
  }
  if (!tl.data) return null;
  const a = tl.data.action;
  const assignee = a.assignee_user_id !== null ? names?.get(a.assignee_user_id) ?? `Member #${a.assignee_user_id}` : null;
  const decisions = decisionsFor(a.status, a.type);

  return (
    <div className="cx-stack" style={{ gap: 16 }}>
      <div className="brf-card__tags">
        <Badge tone={statusTone(a.status)}>{statusLabel(a.status)}</Badge>
        {a.escalation_level ? <Badge tone="bad" dot>Escalation level {a.escalation_level}</Badge> : null}
        {a.due_at && !a.terminal && <CountdownChip due={a.due_at} prefix="Due" />}
      </div>
      <h3 className="brf-card__title">{a.title}</h3>
      <dl className="brf-kv">
        <dt>Type</dt><dd>{a.type}</dd>
        {a.expected_minor !== null && (<><dt>Expected</dt><dd><MoneyText amount={a.expected_minor} minor currency={a.currency} /></dd></>)}
        {a.outcome_minor !== null && (<><dt>Outcome</dt><dd><MoneyText amount={a.outcome_minor} minor currency={a.currency} />
          {a.counts_toward_verified_profit ? " (verified, caused)" : a.attribution ? ` (${a.attribution})` : ""}</dd></>)}
        {(assignee || a.assignee_role) && (<><dt>Assigned to</dt><dd>{assignee ?? roleLabel(a.assignee_role)}</dd></>)}
        {a.owner_role && (<><dt>Owner</dt><dd>{roleLabel(a.owner_role)}</dd></>)}
        {a.due_at && (<><dt>Deadline</dt><dd>{fmt.dateTime(a.due_at, "medium")}</dd></>)}
        {a.decided_at && (<><dt>Decided</dt><dd>{fmt.dateTime(a.decided_at, "medium")}{a.decided_via ? ` via ${a.decided_via}` : ""}</dd></>)}
      </dl>
      {decisions.length > 0 && (
        <ActionBar decisions={decisions} status={a.status} permission={[PERM.decide, PERM.view]}
                   busy={busy && busy.id === a.id ? busy.decision : null}
                   onDecide={(d, extra) => void decide(a.id, d, extra)}
                   onAdjust={() => setAdjustOpen(true)}
                   assignPicker={api => <AssignSheet slug={slug} api={api} suggestUserId={a.assignee_user_id} suggestRole={a.owner_role ?? a.assignee_role} />} />
      )}
      <section className="brf-group" aria-label="History">
        <h4 className="brf-group__title">History</h4>
        <StateTimeline slug={slug} entries={tl.data.timeline} loading={tl.loading} error={tl.error} onRetry={tl.reload} />
      </section>
      <AdjustSheet slug={slug} actionId={a.id} open={adjustOpen} onClose={() => setAdjustOpen(false)}
                   busy={busy?.decision === "adjust"}
                   onSubmit={(patch, note) => decide(a.id, "adjust", { params_patch: patch, note })} />
    </div>
  );
}
