"use client";

/* ═══════════════════════════════════════════════════════════════════════
   UI kit: domain pieces for the detect -> explain -> approve -> verify loop.

   Every module renders its findings through SignalCard / EvidencePanel /
   ActionBar so an owner learns ONE way to read "what is wrong, how sure are
   we, why do we think so, what do you want to do". The buttons only ask;
   the server's decide() enforces permissions and the autonomy dial.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState, type ReactNode } from "react";
import { LuChevronDown, LuChevronUp, LuMoreHorizontal, LuExternalLink } from "react-icons/lu";
import { API_PREFIX, hotelPath } from "../lib/api";
import { useFormat, type MoneyOptions } from "../lib/format";
import { useHotelOptional } from "../lib/hotel";
import { useSessionOptional } from "../lib/session";
import {
  SIGNAL_SECTION_LABELS, TERMINAL_ACTION_STATUSES,
  type ActionStatus, type DecideBody, type Decision, type Evidence, type Signal,
} from "../lib/types";
import { Badge, Button, Card, cx, type Tone } from "./core";
import { CountdownChip, Sparkline } from "./charts-extra";
import { Field, Textarea } from "./forms";
import { Modal } from "./overlay";

// ─── MoneyText ───────────────────────────────────────────────────────────

export function MoneyText({
  amount, currency, minor = false, compact = false, signed = false, tone = "none", className, title,
}: {
  amount: number | string | bigint | null | undefined;
  /** Defaults to the hotel's currency. */
  currency?: string | null;
  /** The amount is integer minor units (fils). */
  minor?: boolean;
  compact?: boolean;
  /** Always show + / -. */
  signed?: boolean;
  /** "auto" colours gains green and losses red (always paired with the sign);
      "cost" inverts it for leakage and cost figures, where a positive amount
      is money lost (red) and a negative one a saving (green). */
  tone?: "none" | "auto" | "cost";
  className?: string;
  title?: string;
}) {
  const fmt = useFormat();
  const opts: MoneyOptions & { currency?: string | null } = {
    currency, minor, compact, signDisplay: signed ? "exceptZero" : "auto",
  };
  const text = fmt.money(amount, opts);
  const n = amount === null || amount === undefined || amount === "" ? 0 : Number(amount);
  const good = tone === "cost" ? n < 0 : n > 0;
  const toneClass = tone !== "none" && Number.isFinite(n) && n !== 0 ? (good ? "cx-money--pos" : "cx-money--neg") : null;
  // Compact figures hide precision; the exact amount is one hover away.
  const full = compact ? fmt.money(amount, { ...opts, compact: false }) : undefined;
  return <span className={cx("cx-money", toneClass, className)} title={title ?? full}>{text}</span>;
}

// ─── ConfidencePill ──────────────────────────────────────────────────────

export function confidenceBand(value: number): { label: string; tone: Tone; bars: number } {
  if (value >= 0.8) return { label: "High", tone: "ok", bars: 3 };
  if (value >= 0.5) return { label: "Medium", tone: "warn", bars: 2 };
  return { label: "Low", tone: "neutral", bars: 1 };
}

export function ConfidencePill({ value, showValue = false }: { value: number | null | undefined; showValue?: boolean }) {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  const v = Math.max(0, Math.min(1, value));
  const band = confidenceBand(v);
  const pct = Math.round(v * 100);
  return (
    <Badge tone={band.tone} title={`Confidence ${pct}%`}>
      <span className="cx-conf">
        <span className="cx-conf__bars" aria-hidden="true">
          {[1, 2, 3].map(i => <i key={i} data-on={i <= band.bars} />)}
        </span>
        {band.label} confidence{showValue ? ` (${pct}%)` : ""}
      </span>
    </Badge>
  );
}

// ─── EvidencePanel ───────────────────────────────────────────────────────

/** Only http(s) links are rendered as links; anything else (javascript:, data:) is shown as text. */
function safeHref(v: unknown): string | null {
  if (typeof v !== "string") return null;
  try {
    const u = new URL(v, "https://placeholder.invalid");
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return v;
  } catch {
    return null;
  }
}

function renderValue(v: unknown): ReactNode {
  if (v === null || v === undefined) return null;
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  return <pre className="cx-evidence__pre">{JSON.stringify(v, null, 2)}</pre>;
}

export function EvidencePanel({
  evidence, hotelSlug, empty = "No evidence attached.",
}: {
  evidence: readonly Evidence[];
  /** Needed to link file evidence; defaults to the current hotel. */
  hotelSlug?: string;
  empty?: ReactNode;
}) {
  const ctxSlug = useHotelOptional()?.slug;
  const slug = hotelSlug ?? ctxSlug;
  if (!evidence.length) return <div className="cx-faint" style={{ fontSize: "0.82rem" }}>{empty}</div>;
  return (
    <div className="cx-evidence">
      {evidence.map((e, i) => {
        const ref = e.ref ?? {};
        let body: ReactNode = null;
        switch (e.kind) {
          case "quote":
            body = <blockquote className="cx-evidence__quote" style={{ margin: 0 }}>{String(e.value ?? "")}</blockquote>;
            break;
          case "link": {
            const href = safeHref(ref.url ?? e.value);
            body = href
              ? <a className="cx-link" href={href} target="_blank" rel="noopener noreferrer">{String(e.value ?? href)} <LuExternalLink aria-hidden="true" size={12} /></a>
              : renderValue(e.value);
            break;
          }
          case "file": {
            const fileId = typeof ref.file_id === "string" || typeof ref.file_id === "number" ? String(ref.file_id) : null;
            const page = typeof ref.page === "number" ? ref.page : null;
            body = fileId && slug
              ? (
                <a className="cx-link" target="_blank" rel="noopener noreferrer"
                   href={`${API_PREFIX}${hotelPath(slug, `/files/${encodeURIComponent(fileId)}`)}${page ? `#page=${page}` : ""}`}>
                  {String(e.value ?? "Open file")}{page ? `, page ${page}` : ""} <LuExternalLink aria-hidden="true" size={12} />
                </a>
              )
              : renderValue(e.value);
            break;
          }
          case "series": {
            const nums = Array.isArray(e.value) ? (e.value as unknown[]).map(x => (typeof x === "number" ? x : null)) : [];
            body = nums.length > 1 ? <Sparkline values={nums} width={160} height={32} ariaLabel={e.label} /> : renderValue(e.value);
            break;
          }
          case "calc":
            body = <pre className="cx-evidence__pre">{typeof e.value === "string" ? e.value : JSON.stringify(e.value, null, 2)}</pre>;
            break;
          default:
            body = renderValue(e.value);
        }
        return (
          <div key={i} className="cx-evidence__item">
            <span className="cx-evidence__kind">{e.kind}</span>
            <span className="cx-evidence__label">{e.label}</span>
            {body !== null && <div className={e.kind === "quote" || e.kind === "calc" ? undefined : "cx-evidence__value"} style={{ gridColumn: 2 }}>{body}</div>}
          </div>
        );
      })}
    </div>
  );
}

// ─── ActionBar ───────────────────────────────────────────────────────────

export const DECIDE_PERMISSION = "core.actions.decide";

const DECISION_LABELS: Record<Decision, string> = {
  approve: "Approve",
  adjust: "Adjust",
  assign: "Assign",
  dispute: "Dispute",
  remind: "Remind",
  ignore: "Ignore",
  snooze: "Snooze",
};

const SNOOZE_OPTIONS = [
  { label: "Snooze 1 day", hours: 24 },
  { label: "Snooze 3 days", hours: 72 },
  { label: "Snooze 1 week", hours: 168 },
];

export type DecideHandler = (decision: Decision, extra?: Omit<DecideBody, "decision">) => void | Promise<void>;

export interface AssignPickerApi {
  assign(userId: number, note?: string): void;
  close(): void;
}

export function ActionBar({
  onDecide, decisions = ["approve", "adjust", "assign", "dispute", "remind", "ignore", "snooze"],
  permission = DECIDE_PERMISSION, canDecide, busy = null, status, onAdjust, assignPicker, size = "sm",
}: {
  onDecide: DecideHandler;
  /** Which verbs this action type supports, in display order. */
  decisions?: readonly Decision[];
  /** Permission(s) required; checked through useSession().can(). */
  permission?: string | readonly string[];
  /** Override the permission check (e.g. from a device principal). */
  canDecide?: boolean;
  /** The decision currently in flight (its button spins; all are disabled). */
  busy?: Decision | null;
  /** Current action state: terminal states disable the bar. */
  status?: ActionStatus;
  /** Open the caller's adjust UI instead of deciding "adjust" directly. */
  onAdjust?: () => void;
  /** Slot rendering a staff/user picker inside the Assign dialog. */
  assignPicker?: (api: AssignPickerApi) => ReactNode;
  size?: "sm" | "md";
}) {
  const session = useSessionOptional();
  const allowed = canDecide ?? (session ? session.can(permission) : false);
  const terminal = status ? TERMINAL_ACTION_STATUSES.has(status) : false;
  const disabled = !allowed || terminal || busy !== null;
  const why = !allowed ? "You do not have permission to decide on this." : terminal ? "This action is closed." : undefined;

  const [menuOpen, setMenuOpen] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [noteFor, setNoteFor] = useState<"dispute" | "remind" | null>(null);
  const [note, setNote] = useState("");

  const has = (d: Decision) => decisions.includes(d);
  const primary = (["approve", "adjust", "assign", "dispute", "remind"] as const).filter(has);
  const overflow = (["ignore", "snooze"] as const).filter(has);

  const click = (d: Decision) => {
    if (disabled) return;
    if (d === "adjust" && onAdjust) return onAdjust();
    if (d === "assign" && assignPicker) return setAssignOpen(true);
    if (d === "dispute" || d === "remind") { setNote(""); setNoteFor(d); return; }
    void onDecide(d);
  };

  return (
    <div className="cx-actionbar" title={why}>
      {primary.map(d => (
        <Button key={d} size={size} variant={d === "approve" ? "primary" : d === "remind" ? "ghost" : "secondary"}
                disabled={disabled} loading={busy === d} aria-disabled={disabled || undefined} onClick={() => click(d)}>
          {DECISION_LABELS[d]}
        </Button>
      ))}
      {overflow.length > 0 && (
        <div className="cx-actionbar__more">
          <Button size={size} variant="ghost" icon={<LuMoreHorizontal />} aria-label="More decisions" aria-haspopup="menu"
                  aria-expanded={menuOpen} disabled={disabled} loading={busy === "ignore" || busy === "snooze"}
                  onClick={() => setMenuOpen(o => !o)} />
          {menuOpen && (
            <div className="cx-menu" role="menu" onMouseLeave={() => setMenuOpen(false)}>
              {has("snooze") && SNOOZE_OPTIONS.map(o => (
                <button key={o.hours} type="button" role="menuitem" className="cx-menu__item"
                        onClick={() => {
                          setMenuOpen(false);
                          void onDecide("snooze", { snooze_until: new Date(Date.now() + o.hours * 3_600_000).toISOString() });
                        }}>
                  {o.label}
                </button>
              ))}
              {has("snooze") && has("ignore") && <div className="cx-menu__sep" />}
              {has("ignore") && (
                <button type="button" role="menuitem" className="cx-menu__item"
                        onClick={() => { setMenuOpen(false); void onDecide("ignore"); }}>
                  Ignore
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {assignPicker && (
        <Modal open={assignOpen} onClose={() => setAssignOpen(false)} title="Assign to" size="sm">
          {assignPicker({
            close: () => setAssignOpen(false),
            assign: (userId, n) => {
              setAssignOpen(false);
              void onDecide("assign", { assignee_user_id: userId, note: n });
            },
          })}
        </Modal>
      )}

      <Modal
        open={noteFor !== null}
        onClose={() => setNoteFor(null)}
        size="sm"
        title={noteFor === "dispute" ? "Dispute this" : "Send a reminder"}
        footer={
          <>
            <Button variant="ghost" onClick={() => setNoteFor(null)}>Cancel</Button>
            <Button variant="primary" disabled={noteFor === "dispute" && !note.trim()}
                    onClick={() => {
                      const d = noteFor!;
                      setNoteFor(null);
                      void onDecide(d, note.trim() ? { note: note.trim() } : undefined);
                    }}>
              {noteFor === "dispute" ? "Submit dispute" : "Send reminder"}
            </Button>
          </>
        }
      >
        <Field label={noteFor === "dispute" ? "Why is this wrong?" : "Note (optional)"} required={noteFor === "dispute"}
               hint={noteFor === "dispute" ? "Recorded on the action and used to tune this detector." : undefined}>
          <Textarea value={note} onChange={e => setNote(e.target.value)} rows={4} />
        </Field>
      </Modal>
    </div>
  );
}

// ─── SignalCard ──────────────────────────────────────────────────────────

export function SignalCard({
  signal, onDecide, busy = null, actionStatus, assignPicker, onAdjust, footer, defaultExpanded = false, hotelSlug,
}: {
  signal: Signal;
  /** Omit to render read-only (no ActionBar). */
  onDecide?: DecideHandler;
  busy?: Decision | null;
  actionStatus?: ActionStatus;
  assignPicker?: (api: AssignPickerApi) => ReactNode;
  onAdjust?: () => void;
  /** Extra module-specific content under the summary. */
  footer?: ReactNode;
  defaultExpanded?: boolean;
  hotelSlug?: string;
}) {
  const [open, setOpen] = useState(defaultExpanded);
  const hasImpact = signal.impact_minor !== null && signal.impact_minor !== undefined
    || (signal.impact_amount !== null && signal.impact_amount !== undefined);
  const rec = signal.recommended_action;

  return (
    <Card as="article" className={cx("cx-signal", `cx-signal--${signal.section}`)}>
      <div className="cx-signal__top">
        <div className="cx-row" style={{ gap: 6 }}>
          <Badge tone="neutral">{SIGNAL_SECTION_LABELS[signal.section] ?? signal.section}</Badge>
          <ConfidencePill value={signal.confidence} />
        </div>
        {signal.due_at && <CountdownChip due={signal.due_at} />}
      </div>

      <h3 className="cx-signal__title">{signal.title}</h3>

      {hasImpact && (
        <div className="cx-signal__impact">
          {signal.impact_minor !== null && signal.impact_minor !== undefined
            ? <MoneyText amount={signal.impact_minor} minor currency={signal.currency} />
            : <MoneyText amount={signal.impact_amount} currency={signal.currency} />}
        </div>
      )}

      {signal.summary && <p className="cx-signal__summary">{signal.summary}</p>}

      <div className="cx-signal__meta">
        {rec && <span>Suggested: <b style={{ color: "var(--clefs-ink)", fontWeight: 600 }}>{rec.label}</b></span>}
        {(signal.owner_role || rec?.owner_role) && <span>Owner: {String(signal.owner_role ?? rec?.owner_role).replace(/_/g, " ")}</span>}
        {signal.occurrences && signal.occurrences > 1 ? <span>Seen {signal.occurrences} times</span> : null}
      </div>

      {footer}

      {signal.evidence.length > 0 && (
        <div>
          <button type="button" className="cx-link" onClick={() => setOpen(o => !o)} aria-expanded={open}>
            {open ? "Hide" : "Show"} evidence ({signal.evidence.length}) {open ? <LuChevronUp aria-hidden="true" /> : <LuChevronDown aria-hidden="true" />}
          </button>
          {open && <div style={{ marginTop: 8 }}><EvidencePanel evidence={signal.evidence} hotelSlug={hotelSlug} /></div>}
        </div>
      )}

      {onDecide && (
        <ActionBar onDecide={onDecide} busy={busy} status={actionStatus} assignPicker={assignPicker} onAdjust={onAdjust} />
      )}
    </Card>
  );
}
