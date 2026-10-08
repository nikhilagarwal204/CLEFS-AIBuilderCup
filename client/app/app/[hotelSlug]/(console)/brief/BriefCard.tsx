"use client";

/* ═══════════════════════════════════════════════════════════════════════
   BriefCard: one brief item. Reads top to bottom the way the owner
   decides: how much (MoneyText), how sure (ConfidencePill), by when
   (CountdownChip), why we think so (evidence link), who owns it, then the
   ActionBar. The bar only asks; core decide() enforces the role and the
   autonomy dial. An item with no action yet (a signal nobody proposed a
   fix for) gets the same bar through the brief's signal route: Approve
   proposes the recommended action and approves it, Assign proposes and
   assigns, Snooze and Ignore close the signal (signalDecisionsFor).
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import { LuExternalLink, LuHistory } from "react-icons/lu";
import { SIGNAL_SECTION_LABELS, type ActionStatus, type DecideBody, type Decision, type SignalSection } from "../../../../lib/types";
import {
  ActionBar, Badge, Card, ConfidencePill, CountdownChip, MoneyText, cx,
} from "../../../../ui";
import AssignSheet from "./AssignSheet";
import {
  brfHref, decisionsFor, isInternalHref, OPEN_SIGNAL, PERM, roleLabel, signalDecisionsFor, statusLabel, statusTone,
} from "./lib";
import type { BriefItem } from "./types";

/* core.actions.decide is owner/GM only, yet finance, revenue and department
   heads decide their own types through each type's decide_perm, which the
   client cannot see. So anyone who can read the brief gets the buttons and
   core decide() answers a 403 in words ("Your role cannot make this decision"). */
const DECIDE_PERMS = [PERM.decide, PERM.view] as const;

export type CardDecide = (item: BriefItem, decision: Decision, extra?: Omit<DecideBody, "decision">) => void | Promise<void>;

const SIGNAL_CLOSED_LABEL: Record<string, string> = {
  snoozed: "Snoozed", ignored: "Dismissed", decided: "Decided", resolved: "Resolved", expired: "Expired",
  superseded: "Superseded", suppressed: "Suppressed",
};

export default function BriefCard({
  slug, item, status, signalStatus, onDecide, onAdjust, busy, readOnly = false, focus = false,
}: {
  slug: string;
  item: BriefItem;
  /** The action's live state when known (after a decision), else the item's snapshot. */
  status?: ActionStatus | null;
  /** A signal-only item's live signal status after a snooze or dismiss. */
  signalStatus?: string | null;
  onDecide: CardDecide;
  onAdjust: (item: BriefItem) => void;
  busy: Decision | null;
  /** Preview and past runs still decide on live actions; a viewer without
      decide rights sees the bar disabled with the reason on hover. */
  readOnly?: boolean;
  focus?: boolean;
}) {
  const st = status ?? item.action_status;
  const type = item.recommended_action?.type ?? item.detector;
  const signalOnly = item.action_id === null && item.signal_id !== null;
  const decisions = signalOnly ? signalDecisionsFor(item, signalStatus) : decisionsFor(st, type);
  const closedSignal = signalOnly && signalStatus && !OPEN_SIGNAL.has(signalStatus)
    ? SIGNAL_CLOSED_LABEL[signalStatus] ?? signalStatus : null;
  const overdue = item.kind === "overdue";
  const section = item.section_label || (SIGNAL_SECTION_LABELS[item.section as SignalSection] ?? item.section.replace(/_/g, " "));
  const ev = item.evidence_url;

  return (
    <Card as="article" className={cx("brf-card", overdue && "brf-card--overdue", focus && "brf-card--focus")}>
      <div className="brf-card__top">
        <div className="brf-card__tags">
          <span className="brf-card__no" aria-label={`Item ${item.item_no}`}>{item.item_no}</span>
          {overdue ? <Badge tone="bad" dot>Overdue</Badge> : <Badge tone="neutral">{section}</Badge>}
          <ConfidencePill value={item.confidence} showValue />
          {st && <Badge tone={statusTone(st)}>{statusLabel(st)}</Badge>}
          {closedSignal && <Badge tone="neutral">{closedSignal}</Badge>}
        </div>
        {item.deadline_at && <CountdownChip due={item.deadline_at} prefix="Due" />}
      </div>

      <h3 className="brf-card__title">{item.title}</h3>

      {item.impact_minor !== null && item.impact_minor !== undefined && (
        <div className="brf-card__impact">
          <MoneyText amount={item.impact_minor} minor currency={item.currency} />
          {/* 04 spec 4.5 / 02 spec 4.3: an estimate is labelled as one. */}
          {/* Verified money is banked, never "at stake" (brief.at_stake_minor). */}
          <small>{item.section === "verified" ? "verified" : item.impact_estimated ? "est. at stake" : "at stake"}</small>
        </div>
      )}

      <div className="brf-card__meta">
        {item.recommended_action?.label && <span>Suggested: <b>{String(item.recommended_action.label)}</b></span>}
        {(item.responsible_label || item.owner_role) && (
          <span>Owner: <b>{item.responsible_label || roleLabel(item.owner_role)}</b>
            {item.responsible_label && item.owner_role
              && item.responsible_label.toLowerCase() !== roleLabel(item.owner_role).toLowerCase()
              ? ` (${roleLabel(item.owner_role)})` : ""}</span>
        )}
      </div>

      <div className="brf-card__foot">
        <div className="brf-row" style={{ gap: 14 }}>
          {ev && (isInternalHref(ev)
            ? <Link className="cx-link" href={ev}>Evidence</Link>
            : <a className="cx-link" href={ev} target="_blank" rel="noopener noreferrer">Evidence <LuExternalLink aria-hidden="true" size={12} /></a>)}
          {item.action_id !== null && (
            <Link className="cx-link" href={brfHref(slug, `/actions/${item.action_id}`)}>
              <LuHistory aria-hidden="true" size={13} /> History
            </Link>
          )}
        </div>
        {decisions.length > 0 ? (
          <ActionBar
            decisions={decisions}
            status={signalOnly ? undefined : st ?? undefined}
            busy={busy}
            canDecide={readOnly ? false : undefined}
            permission={DECIDE_PERMS}
            onDecide={(d, extra) => onDecide(item, d, extra)}
            onAdjust={signalOnly ? undefined : () => onAdjust(item)}
            assignPicker={api => (
              <AssignSheet slug={slug} api={api} suggestUserId={item.responsible_user_id} suggestRole={item.owner_role} />
            )}
          />
        ) : (
          <span className="brf-muted">{item.section === "verified" ? "Verified, nothing to decide"
            : closedSignal ?? (item.action_id === null ? "No action proposed yet" : "Nothing to decide")}</span>
        )}
      </div>
    </Card>
  );
}
