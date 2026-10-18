"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Request card v2 pieces (03 spec 1.8, the RequestCard row): state chip,
   SLA countdown, source and language badges, the "Original" toggle and the
   staff <select> that replaces the old free-text "Your Name" accept.

   Extracted here (not written into StaffDashboard) so the new Service pages
   and the classic dashboard render a request the same way.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { svcStyles } from "./styles";
import {
  LANGUAGE_LABELS, SOURCE_LABELS, countdownText, slaTarget, stateChip,
  type ServiceCard, type V2Fields,
} from "./serviceApi";

// ─── Styles ───

/** Inject the svc- CSS once per surface. Identical duplicates are inert. */
export function SvcStyles() {
  return <style dangerouslySetInnerHTML={{ __html: svcStyles }} />;
}

// ─── Clock ───

/** A shared "now" that ticks every 30 s, so countdowns move without each
    card owning a timer. Initialised in an effect, never during render. */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    tick();
    const t = setInterval(tick, intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

type CardLike = Pick<ServiceCard, "status"> & V2Fields;

// ─── Chips ───

export function StateChip({ req, now }: { req: CardLike; now: number }) {
  const chip = stateChip(req, now);
  return <span className={`svc-chip svc-chip--${chip.tone}`}>{chip.label}</span>;
}

/** "Ack 2m left" / "Fix 12m over". Hidden for pre-v2 rows (no due times). */
export function SlaCountdown({ req, now }: { req: CardLike; now: number }) {
  const target = slaTarget(req);
  if (!target || !now) return null;
  const c = countdownText(target.due, now);
  const tone = c.over ? "bad" : c.soon ? "warn" : "neutral";
  const label = target.kind === "ack" ? "Ack" : "Fix";
  return (
    <span className={`svc-chip svc-chip--${tone}${c.over ? " svc-chip--pulse" : ""}`}
          title={target.kind === "ack" ? "Time left to acknowledge" : "Time left to complete"}>
      {label} {c.text}
    </span>
  );
}

export function SourceBadge({ source }: { source?: string | null }) {
  if (!source || source === "legacy") return null;
  return <span className="svc-chip" title="Where the request came from">{SOURCE_LABELS[source] ?? source}</span>;
}

export function LanguageBadge({ language }: { language?: string | null }) {
  if (!language || language === "en") return null;
  const code = language.toLowerCase();
  return (
    <span className="svc-chip svc-chip--info" title={`Guest spoke ${LANGUAGE_LABELS[code] ?? code}`}>
      {code.toUpperCase()}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity?: string | null }) {
  if (severity !== "L1") return null;
  return <span className="svc-chip svc-chip--bad" title="Level 1: respond now">L1</span>;
}

/** Everything v2 on one line, for a card header. Renders nothing for a pre-v2 row. */
export function V2Chips({ req, now }: { req: CardLike; now: number }) {
  if (!req.state && !req.source) return null;
  return (
    <span className="svc-chips">
      <SeverityBadge severity={req.severity} />
      <StateChip req={req} now={now} />
      <SlaCountdown req={req} now={now} />
      <SourceBadge source={req.source} />
      <LanguageBadge language={req.language} />
      {(req.reopen_count ?? 0) > 0 && <span className="svc-chip svc-chip--warn">Reopened x{req.reopen_count}</span>}
      {(req.escalation_level ?? 0) > 0 && (
        <span className="svc-chip svc-chip--warn" title="Escalation level reached">Escalated L{req.escalation_level}</span>
      )}
      {req.guest_confirmation === "no" && <span className="svc-chip svc-chip--bad">Guest: not fixed</span>}
      {req.guest_confirmation === "yes" && <span className="svc-chip svc-chip--ok">Guest: fixed</span>}
    </span>
  );
}

// ─── Original text ───

/** Staff read the English; the guest's own words sit one tap away. An
    untranslated request (translation failed) shows the original already. */
export function OriginalToggle({ req }: { req: Pick<V2Fields, "original_text" | "language" | "translated"> & { description?: string } }) {
  const [open, setOpen] = useState(false);
  const original = req.original_text?.trim();
  if (!original || original === req.description?.trim()) return null;
  return (
    <div>
      <button type="button" className="svc-linkbtn" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        {open ? "Hide original" : `Original${req.language ? ` (${req.language.toUpperCase()})` : ""}`}
      </button>
      {open && <div className="svc-original" lang={req.language ?? undefined} dir="auto">{original}</div>}
    </div>
  );
}

// ─── Staff assignment ───

export interface StaffOption {
  id: number;
  name: string;
  department?: string;
  is_online?: boolean;
  is_active?: boolean;
}

/** The staff <select> that replaced the free-text "Your Name" accept: a name
    typed by hand never matched anyone, so the job was owned by nobody.
    Online people in the request's department sort first. */
export function StaffAssign({
  staff, department, busy, label = "Assign", onAssign,
}: {
  staff: readonly StaffOption[];
  department?: string | null;
  busy?: boolean;
  label?: string;
  onAssign: (staffId: number) => void;
}) {
  const [picked, setPicked] = useState<string>("");
  const active = staff.filter(s => s.is_active !== false);
  const rank = (s: StaffOption) =>
    (s.is_online ? 0 : 2) + (department && s.department === department ? 0 : 1);
  const sorted = [...active].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  if (!sorted.length) return <span className="svc-faint">Add staff to assign</span>;
  return (
    <span className="svc-assign">
      <select aria-label="Assign to" value={picked} onChange={e => setPicked(e.target.value)} disabled={busy}>
        <option value="">Choose staff</option>
        {sorted.map(s => (
          <option key={s.id} value={String(s.id)}>
            {s.name}{s.is_online ? "" : " (off)"}{s.department ? ` · ${s.department}` : ""}
          </option>
        ))}
      </select>
      <button type="button" className="btn-complete" style={{ padding: "6px 12px", fontSize: "0.8rem" }}
              disabled={!picked || busy} onClick={() => { if (picked) onAssign(Number(picked)); }}>
        {busy ? "..." : label}
      </button>
    </span>
  );
}
