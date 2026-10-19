"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Request card v2 pieces (03 spec 1.8, the RequestCard row): state chip,
   SLA countdown, source and language badges, the "Original" toggle and the
   staff <select> that replaces the old free-text "Your Name" accept.

   Extracted here (not written into StaffDashboard) so the new Service pages
   and the classic dashboard render a request the same way.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { LuAlertTriangle, LuClock } from "react-icons/lu";
import { svcStyles } from "./styles";
import {
  LANGUAGE_LABELS, SOURCE_LABELS, countdownText, fetchFreeStaff, slaTarget, stateChip,
  type ServiceCard, type V2Fields,
} from "./serviceApi";
import {
  emergencyChip, escalationChip, etaChip, guestChip, liveView, moodChip, photoChip, photoSrc, photoState, staffOptionLabel,
  timeTaken, type Chip, type StaffCandidate,
} from "./deskLabels";

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

/** One chip from a deskLabels Chip. */
export function ChipView({ chip, className }: { chip: Chip | null; className?: string }) {
  if (!chip) return null;
  return <span className={`svc-chip svc-chip--${chip.tone}${className ? ` ${className}` : ""}`} title={chip.title}>{chip.label}</span>;
}

/** The emergency severity (L1, or emergency words) reads "Emergency", never a
    level: L1/L2/L3 stay off screen so they cannot be confused with the
    escalation ladder (05 spec 2.7). */
export function SeverityBadge({ req }: { req: Pick<V2Fields, "severity" | "sensitivity" | "request_type_key" | "emergency"> }) {
  return <ChipView chip={emergencyChip(req)} className="svc-chip--pulse" />;
}

/** Everything v2 on one line, for a card header. Renders nothing for a pre-v2 row.
    `withProofAlert`: the card shows a ProofAlert, so a rejected photo's chip
    stays short instead of repeating the reason. */
export function V2Chips({ req, now, withProofAlert = false }: { req: CardLike; now: number; withProofAlert?: boolean }) {
  if (!req.state && !req.source) return null;
  return (
    <span className="svc-chips">
      <SeverityBadge req={req} />
      <StateChip req={req} now={now} />
      <SlaCountdown req={req} now={now} />
      <ChipView chip={escalationChip(req)} />
      <ChipView chip={photoChip(req, withProofAlert)} />
      <ChipView chip={guestChip(req)} />
      <ChipView chip={moodChip(req.mood, req.frustration, true)} />
      <SourceBadge source={req.source} />
      <LanguageBadge language={req.language} />
      {(req.reopen_count ?? 0) > 0 && <span className="svc-chip svc-chip--warn">Reopened x{req.reopen_count}</span>}
    </span>
  );
}

// ─── Time taken, ETA and proof (05 spec 2.8, 2.13) ───

/** A clock that ticks every second, for the one chip that needs it. */
function LiveWait({ req }: { req: CardLike & { timestamp?: number | null } }) {
  const now = useNow(1_000);
  const t = timeTaken(req, now);
  if (!t) return null;
  return (
    <span className={`svc-chip svc-chip--${t.tone} svc-clock`} title={t.title} role="timer" aria-live="off">
      <LuClock aria-hidden="true" /> {t.label}
    </span>
  );
}

/** "Accepted in 00:41" (frozen) or "Waiting 03:12" (live): from the guest's
    request until a staff member accepts, the number the guest card shows. */
export function TimeTaken({ req }: { req: CardLike & { timestamp?: number | null } }) {
  const frozen = timeTaken(req, 0);
  if (frozen) {
    return (
      <span className={`svc-chip svc-chip--${frozen.tone} svc-clock`} title={frozen.title}>
        <LuClock aria-hidden="true" /> {frozen.label}
      </span>
    );
  }
  const s = req.state;
  if (s !== "new" && s !== "assigned") return null;
  return <LiveWait req={req} />;
}

export function EtaChip({ req, now }: { req: CardLike; now: number }) {
  return <ChipView chip={etaChip(req, now)} />;
}

/** The completion photo as a thumbnail; opens the request detail. */
export function PhotoThumb({ req, room, onOpen, size = 44 }: {
  req: Pick<V2Fields, "proof_photo_url" | "proof_status">;
  room?: string;
  onOpen?: () => void;
  size?: number;
}) {
  const src = photoSrc(req.proof_photo_url);
  if (!src) return null;
  const st = photoState(req);
  const img = (
    // eslint-disable-next-line @next/next/no-img-element -- a short-lived signed URL; next/image would cache it
    <img src={src} alt={`Completion photo${room ? ` for room ${room}` : ""}`} width={size} height={size} loading="lazy" />
  );
  const cls = `svc-thumb${st === "rejected" ? " svc-thumb--bad" : st === "verified" ? " svc-thumb--ok" : ""}`;
  if (!onOpen) return <span className={cls}>{img}</span>;
  return (
    <button type="button" className={cls} onClick={onOpen} title="Open the request and its photo">{img}</button>
  );
}

/** A rejected photo is the one proof state a manager must not miss: the job
    stays open and the supervisor has been alerted (05 spec 2.8). */
export function ProofAlert({ req }: { req: Pick<V2Fields, "proof_status" | "proof_reason" | "proof_photo_url"> }) {
  if (photoState(req) !== "rejected") return null;
  const reason = (req.proof_reason ?? "").trim().replace(/[.\s]+$/, "");
  return (
    <div className="svc-proof-alert" role="status">
      <LuAlertTriangle aria-hidden="true" />
      <span>
        <strong>Photo rejected{reason ? `: ${reason}` : ""}.</strong> The job stays open and the supervisor was alerted.
      </span>
    </div>
  );
}

// ─── Live link ───

/** "Live" / "Reconnecting" for the dashboard's stream (lib/api StreamStatus).
    Pair it with graceStatus() so a drop of a second or two (Cloud Run
    recycles long streams) does not flash the pill. */
export function LivePill({ status }: { status: string | null }) {
  const v = liveView(status);
  return (
    <span className={`svc-live svc-live--${v.tone}`} title={v.title} role="status" aria-live="polite">
      <span className="svc-live__dot" aria-hidden="true" />
      {v.label}
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

    With a request id it asks the server for the "Choose staff" list (05 spec
    decision 5: on shift, in the job's team, below their cap, ranked exactly
    as dispatch ranks them, with each person's open jobs and ETA) the first
    time the manager points at or focuses the list. Until that answers, or on
    a server without the route, online people in the department sort first. */
export function StaffAssign({
  staff, department, busy, label = "Assign", onAssign, hotelSlug, requestId,
}: {
  staff: readonly StaffOption[];
  department?: string | null;
  busy?: boolean;
  label?: string;
  onAssign: (staffId: number) => void;
  hotelSlug?: string;
  requestId?: number;
}) {
  const [picked, setPicked] = useState<string>("");
  const [ranked, setRanked] = useState<{ free: StaffCandidate[]; others: StaffCandidate[] } | null>(null);
  const [asked, setAsked] = useState(false);
  const askServer = () => {
    if (asked || !hotelSlug || requestId === undefined) return;
    setAsked(true);
    fetchFreeStaff(hotelSlug, requestId)
      .then(out => setRanked({ free: out.free ?? [], others: out.others ?? [] }))
      .catch(() => { /* keep the local list */ });
  };
  const active = staff.filter(s => s.is_active !== false);
  const rank = (s: StaffOption) =>
    (s.is_online ? 0 : 2) + (department && s.department === department ? 0 : 1);
  const sorted = [...active].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
  if (!sorted.length && !ranked) return <span className="svc-faint">Add staff to assign</span>;
  return (
    <span className="svc-assign" onPointerEnter={askServer}>
      <select aria-label={label === "Reassign" ? "Reassign to" : "Assign to"} value={picked}
              onChange={e => setPicked(e.target.value)} onFocus={askServer} disabled={busy}>
        <option value="">Choose staff</option>
        {ranked ? (
          <>
            <optgroup label={ranked.free.length ? "Free now, best first" : "Nobody is free right now"}>
              {ranked.free.map(c => (
                <option key={c.staff_id} value={String(c.staff_id)} disabled={Boolean(c.current)}>{staffOptionLabel(c)}</option>
              ))}
            </optgroup>
            {ranked.others.length > 0 && (
              <optgroup label="Not free">
                {ranked.others.map(c => (
                  <option key={c.staff_id} value={String(c.staff_id)} disabled={Boolean(c.current)}>{staffOptionLabel(c)}</option>
                ))}
              </optgroup>
            )}
          </>
        ) : sorted.map(s => (
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
