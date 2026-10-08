"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The floor-device app (03 spec 1.6 and 1.8): pair -> lock (tiles + PIN)
   -> handover (must ack) -> queue, with the task sheet, SOS and LEARN on top.

   State that must survive a reload or a dead battery lives in IndexedDB:
   the device facts, open PIN sessions (with their handover acks), the feed
   cursor, LEARN ticks and the outbox. Everything else is re-read.

   Shared device model: several people can hold an open PIN session on one
   device (a housekeeping pantry tablet). One of them is "at the screen"
   (the X-Clefs-Staff on every call); Lock hands the screen back without
   ending anyone's shift; Log out ends the shift with a handover note.
   The chime rings for jobs of anyone on shift here, not just the person
   at the screen, so a job for Amina rings while Ravi is holding it.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import { DEFAULT_LOCALE, DEFAULT_TIMEZONE, formatTime } from "../../lib/format";
import { Button, Textarea, ToastProvider, UiStyles, useToast } from "../../ui";
import { alarmFor, DeviceAlerts } from "../lib/alerts";
import {
  endShift, getContext, getHandover, getQueue, getRequest, getRoster, inScope, OPEN_STATES,
  type Card, type DeviceInfo, type Handover, type HotelContext, type PairResult, type RosterStaff, type ShiftSession,
} from "../lib/deviceApi";
import { resetFeedCursor, startFeed, type FeedStatus } from "../lib/feed";
import { idbGet, idbSet, KV } from "../lib/idb";
import { forgetHotel, rememberHotel } from "../lib/lastHotel";
import { applyPending, pendingRequestIds } from "../lib/optimistic";
import { DeviceOutbox, type OutboxItem } from "../lib/outbox";
import { enablePush, notifyHidden, registerDeviceWorker } from "../lib/push";
import { deviceStyles } from "../lib/styles";
import { HandoverSheet } from "./HandoverSheet";
import type { Learn } from "./LearnCard";
import { LockScreen } from "./LockScreen";
import { PairScreen } from "./PairScreen";
import { Queue } from "./Queue";
import { SosActive, SosButton } from "./SosButton";
import { TaskSheet, type TaskActionOpts } from "./TaskSheet";

// ─── Constants and stored shapes ─────────────────────────────────────────

/** Shared devices lock after this long without a touch (03 spec 1.6: 10 min;
    the server's session_autologout ends the session itself). */
const IDLE_LOCK_MS = 10 * 60_000;
const QUEUE_SAFETY_POLL_MS = 60_000;
const CLOCK_TICK_MS = 15_000;

type Phase = "boot" | "pair" | "locked" | "handover" | "queue";

interface StoredSession extends ShiftSession {
  started_at: number;
  handover_acked: boolean;
}

type Sessions = Record<string, StoredSession>;

interface SosState { client_event_id: string; error: string | null; hidden: boolean }

const devKey = (slug: string) => `device:${slug}`;
const sessKey = (slug: string) => `sessions:${slug}`;

function plannedEnd(s: ShiftSession): number | null {
  if (s.planned_end_at == null) return null;
  const t = typeof s.planned_end_at === "number" ? s.planned_end_at : Date.parse(s.planned_end_at);
  return Number.isFinite(t) ? t : null;
}

function isSupervisor(s: RosterStaff): boolean {
  return /supervis|manager|duty|head|lead|captain|security/i.test(`${s.role ?? ""}`);
}

// ─── Component ───────────────────────────────────────────────────────────

export function DeviceApp({ hotelSlug, pairCode }: { hotelSlug: string; pairCode: string | null }) {
  const toast = useToast();
  const [phase, setPhase] = useState<Phase>(pairCode ? "pair" : "boot");
  const [pairReason, setPairReason] = useState<string | null>(null);
  const [device, setDevice] = useState<DeviceInfo | null>(null);
  const [ctx, setCtx] = useState<HotelContext | null>(null);
  const [roster, setRoster] = useState<RosterStaff[]>([]);
  const [rosterLoading, setRosterLoading] = useState(true);
  const [sessions, setSessions] = useState<Sessions>({});
  const [activeId, setActiveId] = useState<number | null>(null);
  const [serverCards, setServerCards] = useState<Card[]>([]);
  const [queueLoading, setQueueLoading] = useState(true);
  const [pending, setPending] = useState<OutboxItem[]>([]);
  const [loopIds, setLoopIds] = useState<Set<number>>(() => new Set());
  const [feedStatus, setFeedStatus] = useState<FeedStatus>("live");
  const [openId, setOpenId] = useState<number | null>(null);
  const [handover, setHandover] = useState<Handover | null>(null);
  const [handoverState, setHandoverState] = useState<{ loading: boolean; error: string | null }>({ loading: false, error: null });
  const [sos, setSos] = useState<SosState | null>(null);
  const [logoutOpen, setLogoutOpen] = useState(false);
  const [logoutNote, setLogoutNote] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [soundOn, setSoundOn] = useState(false);

  const alertsRef = useRef<DeviceAlerts | null>(null);
  const outboxRef = useRef<DeviceOutbox | null>(null);
  const lastTouch = useRef(0);

  const tz = ctx?.hotel.timezone || DEFAULT_TIMEZONE;
  const locale = ctx?.hotel.locale || DEFAULT_LOCALE;
  const active = activeId != null ? sessions[String(activeId)] ?? null : null;
  const staffIds = useMemo(() => Object.values(sessions).map(s => s.staff.id), [sessions]);
  // History lines name the person (TaskSheet); stable so its Escape effect
  // does not re-subscribe on every render.
  const staffName = useMemo(() => {
    const byId = new Map(roster.map(s => [s.id, s.name] as const));
    return (id: number) => byId.get(id);
  }, [roster]);
  const closeTask = useCallback(() => setOpenId(null), []);

  // ─── Persistence ───

  const saveSessions = useCallback((next: Sessions) => {
    setSessions(next);
    void idbSet(KV, sessKey(hotelSlug), next);
  }, [hotelSlug]);

  // ─── Boot: am I paired to this hotel? ───

  const loadRoster = useCallback(async () => {
    setRosterLoading(true);
    try {
      const r = await getRoster();
      setRoster(r);
      return r;
    } finally {
      setRosterLoading(false);
    }
  }, []);

  const toPair = useCallback((reason: string | null) => {
    setPairReason(reason);
    setPhase("pair");
    setActiveId(null);
  }, []);

  const boot = useCallback(async () => {
    const [storedDevice, storedSessions] = await Promise.all([
      idbGet<DeviceInfo>(KV, devKey(hotelSlug)),
      idbGet<Sessions>(KV, sessKey(hotelSlug)),
    ]);
    if (storedDevice) setDevice(storedDevice);
    try {
      const c = await getContext(hotelSlug);
      setCtx(c);
      let r: RosterStaff[] | null = null;
      if (c.principal_kind !== "device" || c.role !== "device") {
        // /context resolves a console session BEFORE the device cookie, so a
        // browser where a manager is signed in (the presenter's laptop, a
        // floor tablet) reads as that user right after a good pair (QA GS-2).
        // The roster route reads only the device cookie (require_device), so
        // it is the device-scoped answer. storedDevice is keyed by this hotel
        // and written only by a pair for it, which keeps a cookie paired to
        // another hotel from passing.
        r = storedDevice && c.principal_kind === "user" ? await loadRoster().catch(() => null) : null;
        if (!r) {
          toPair(storedDevice ? "This device is no longer paired (it was removed, or its pairing was cleared). Ask a manager for a new code." : null);
          return;
        }
      }
      r = r ?? await loadRoster();
      // Keep only sessions the server still has open on this device.
      const open = new Set(r.filter(s => s.on_shift).map(s => s.id));
      const kept: Sessions = {};
      for (const [k, s] of Object.entries(storedSessions ?? {})) if (open.has(s.staff.id)) kept[k] = s;
      saveSessions(kept);
      rememberHotel(hotelSlug);
      setPhase("locked");
    } catch (err) {
      if (err instanceof ApiError && err.status === 0 && storedDevice) {
        // Offline at boot: trust what we stored; the outbox waits for signal.
        setSessions(storedSessions ?? {});
        setPhase("locked");
        return;
      }
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) { toPair(null); return; }
      toPair(err instanceof Error ? err.message : "Could not reach Clefs.");
    }
  }, [hotelSlug, loadRoster, saveSessions, toPair]);

  // boot, refreshQueue and loadHandover set state only after an await (a
  // fetch answer), which is the "subscribe, then setState in the callback"
  // pattern; the compiler cannot see through the async call, hence the
  // targeted disables below.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!pairCode) void boot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hotelSlug]);

  const onPaired = useCallback(async (res: PairResult) => {
    if (res.hotel_slug !== hotelSlug) {
      // The code was for another hotel: go there (the cookie is set already).
      // Store the device under THAT hotel first: boot there needs it when a
      // console session masks the device in /context (see boot).
      await idbSet(KV, devKey(res.hotel_slug), res.device);
      window.location.replace(`/device/${encodeURIComponent(res.hotel_slug)}`);
      return;
    }
    setDevice(res.device);
    await idbSet(KV, devKey(hotelSlug), res.device);
    await resetFeedCursor(hotelSlug);
    rememberHotel(hotelSlug);
    toast.success(`Paired as ${res.device.name}`);
    void boot();
  }, [hotelSlug, boot, toast]);

  // ─── Alerts and outbox (one of each per mounted app) ───

  useEffect(() => {
    const alerts = new DeviceAlerts();
    alertsRef.current = alerts;
    void registerDeviceWorker();
    return () => { alerts.dispose(); alertsRef.current = null; };
  }, []);

  const refreshQueueRef = useRef<() => void>(() => {});

  useEffect(() => {
    const ob = new DeviceOutbox(hotelSlug, {
      fresh: async (id, staffId) => {
        try { return (await getRequest(hotelSlug, id, staffId)).request; } catch { return null; }
      },
      onChange: items => setPending(items),
      onApplied: (_it, res) => {
        const req = (res.data as { request?: Card } | undefined)?.request;
        if (req?.id) setServerCards(cs => cs.map(c => (c.id === req.id ? req : c)));
      },
      onConflict: (_it, res) => { toast({ tone: "warn", title: res.message }); },
      onRejected: (it, res) => { toast({ tone: "bad", title: `Could not ${it.action ?? it.kind}`, body: res.message }); },
      onFlushed: out => { if (out.applied || out.conflicts || out.rejected) refreshQueueRef.current(); },
    });
    outboxRef.current = ob;
    void ob.pending().then(setPending);
    const stop = ob.start();
    return () => { stop(); outboxRef.current = null; };
  }, [hotelSlug, toast]);

  // ─── Sessions: unlock, lock, log out, idle ───

  const onGesture = useCallback(() => {
    alertsRef.current?.unlock();
    setSoundOn(true);
  }, []);

  const onUnlocked = useCallback((s: ShiftSession) => {
    const prev = sessions[String(s.staff.id)];
    const stored: StoredSession = {
      ...s,
      started_at: prev?.started_at ?? Date.now(),
      // A new session id means a new shift: its handover is unread.
      handover_acked: prev?.session_id === s.session_id ? prev.handover_acked : false,
    };
    saveSessions({ ...sessions, [String(s.staff.id)]: stored });
    setActiveId(s.staff.id);
    lastTouch.current = Date.now();
    void alertsRef.current?.keepAwake(true);
    void enablePush(s.staff.id);
    if (!stored.handover_acked) setHandoverState({ loading: true, error: null });
    setPhase(stored.handover_acked ? "queue" : "handover");
  }, [sessions, saveSessions]);

  const lock = useCallback(() => {
    setActiveId(null);
    setOpenId(null);
    setPhase("locked");
    void loadRoster().catch(() => undefined);
  }, [loadRoster]);

  const logout = useCallback(async () => {
    if (!active) return;
    const note = logoutNote.trim();
    try {
      await endShift(active.session_id, note || undefined);
    } catch (err) {
      // A 404 means the server already ended it (auto-logout, revoke): drop it
      // here too. Anything else (offline) keeps the session so the person can
      // try again; the server's auto-logout ends it at shift end regardless.
      if (!(err instanceof ApiError && err.status === 404)) {
        toast({ tone: "warn", title: "Could not log out", body: err instanceof Error ? err.message : undefined });
        return;
      }
    }
    const next = { ...sessions };
    delete next[String(active.staff.id)];
    saveSessions(next);
    setLogoutOpen(false);
    setLogoutNote("");
    if (!Object.keys(next).length) void alertsRef.current?.keepAwake(false);
    lock();
  }, [active, logoutNote, sessions, saveSessions, toast, lock]);

  // Idle lock and the clock tick (SLA countdowns, shift end).
  useEffect(() => {
    const touch = () => { lastTouch.current = Date.now(); };
    window.addEventListener("pointerdown", touch);
    window.addEventListener("keydown", touch);
    const t = setInterval(() => {
      const n = Date.now();
      setNow(n);
      // Shared devices only: a personal device stays with its owner.
      if (activeId != null && device?.kind !== "personal" && n - lastTouch.current > IDLE_LOCK_MS) lock();
    }, CLOCK_TICK_MS);
    return () => {
      window.removeEventListener("pointerdown", touch);
      window.removeEventListener("keydown", touch);
      clearInterval(t);
    };
  }, [activeId, device, lock]);

  // ─── Queue, feed and the alarm ───

  const signedIn = phase === "locked" || phase === "handover" || phase === "queue";

  const refreshQueue = useCallback(async () => {
    try {
      const cards = await getQueue(hotelSlug, activeId);
      setServerCards(cards.filter(c => OPEN_STATES.includes(c.state)));
    } catch (err) {
      if (err instanceof ApiError && err.code === "staff_not_on_shift" && activeId != null) {
        // The server ended this person's session (shift end + grace, idle,
        // revoke): forget it and hand the screen back.
        setSessions(prev => {
          const next = { ...prev };
          delete next[String(activeId)];
          void idbSet(KV, sessKey(hotelSlug), next);
          return next;
        });
        toast({ tone: "info", title: "Your session ended", body: "Log in again with your PIN to continue." });
        lock();
      } else if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        toPair("This device was unpaired. Ask a manager for a new code.");
      }
    } finally {
      setQueueLoading(false);
    }
  }, [hotelSlug, activeId, toPair, toast, lock]);

  useEffect(() => { refreshQueueRef.current = () => { void refreshQueue(); }; }, [refreshQueue]);

  // Feed events refetch the queue (debounced) and collect "loop" escalations.
  useEffect(() => {
    if (!signedIn) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // Through the ref, so a lock / unlock (a new refreshQueue) does not tear
    // down and reopen the feed and its SSE stream.
    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => refreshQueueRef.current(), 400);
    };
    const stop = startFeed(hotelSlug, {
      onEvents: evs => {
        const loops = evs.filter(e => e.kind === "escalated" && (e.payload as { alarm?: string } | null)?.alarm === "loop" && e.request_id);
        if (loops.length) setLoopIds(prev => new Set([...prev, ...loops.map(e => e.request_id as number)]));
        schedule();
      },
      onStatus: st => {
        setFeedStatus(st);
        if (st === "unpaired") toPair("This device was unpaired. Ask a manager for a new code.");
      },
    });
    refreshQueueRef.current();
    const safety = setInterval(() => refreshQueueRef.current(), QUEUE_SAFETY_POLL_MS);
    return () => { stop(); clearInterval(safety); if (timer) clearTimeout(timer); };
  }, [signedIn, hotelSlug, toPair]);

  // A new person at the screen: re-read the queue under their name.
  useEffect(() => { if (activeId != null) refreshQueueRef.current(); }, [activeId]);

  const scoped = useMemo(() => serverCards.filter(c => inScope(c, device)), [serverCards, device]);
  const cards = useMemo(() => applyPending(scoped, pending), [scoped, pending]);
  const pendingIds = useMemo(() => pendingRequestIds(pending), [pending]);

  // Level-triggered alarm: recomputed from the queue every time it changes.
  const alarm = useMemo(
    () => (staffIds.length ? alarmFor(cards, { staffIds, deviceId: device?.id ?? null, loopIds }) : "off"),
    [cards, staffIds, device, loopIds],
  );
  const prevAlarm = useRef<string>("off");
  useEffect(() => {
    alertsRef.current?.setAlarm(alarm);
    if (alarm !== "off" && prevAlarm.current === "off") {
      void notifyHidden(alarm === "urgent" ? "URGENT request" : "New job", "Open the device to acknowledge it.", "svc-alarm", alarm === "urgent");
    }
    prevAlarm.current = alarm;
  }, [alarm]);

  // ─── Handover (must be acknowledged before the queue opens) ───

  // Callers set {loading: true} first (unlock, Retry), so the effect below
  // only starts the fetch and never sets state synchronously.
  const loadHandover = useCallback(async () => {
    try {
      const h = await getHandover(hotelSlug, activeId, null);
      setHandover({ ...h, open: h.open.filter(c => inScope(c, device)), overdue: h.overdue.filter(c => inScope(c, device)) });
      setHandoverState({ loading: false, error: null });
    } catch (err) {
      setHandover(null);
      setHandoverState({ loading: false, error: err instanceof ApiError && err.status === 0
        ? "No connection, so the handover could not load."
        : "The handover could not load." });
    }
  }, [hotelSlug, activeId, device]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (phase === "handover") void loadHandover();
  }, [phase, loadHandover]);

  const ackHandover = useCallback((unavailable: boolean) => {
    if (!active) return;
    saveSessions({ ...sessions, [String(active.staff.id)]: { ...active, handover_acked: true } });
    void outboxRef.current?.enqueue({
      kind: "handover_ack", staff_id: active.staff.id, request_id: null,
      payload: {
        session_id: active.session_id,
        note_ids: handover?.notes.map(n => n.id) ?? [],
        // Counts and ids only, never guest words (0105 svc_handover_acks.summary).
        summary: unavailable || !handover ? { unavailable: true } : {
          open: handover.open.map(c => c.id), overdue: handover.overdue.map(c => c.id),
          ooo_rooms: handover.rooms_out_of_order.length, arrivals: handover.arrivals.length,
          incidents: handover.incidents.map(i => i.id), lost_found: handover.lost_found.length, from_server: handover.from_server,
        },
      },
    });
    setPhase("queue");
  }, [active, sessions, saveSessions, handover]);

  // ─── Actions (all through the outbox) ───

  const act = useCallback((c: Card, action: string, opts: TaskActionOpts = {}) => {
    if (!active) return;
    alertsRef.current?.snooze(3_000);
    void outboxRef.current?.enqueue({
      kind: "transition", action, request_id: c.id, base_version: c.version, staff_id: active.staff.id,
      payload: { note: opts.note, resolution_code: opts.resolution_code, target_staff_id: opts.self ? active.staff.id : undefined },
    });
    if (action === "complete" || action === "decline" || action === "handoff") setOpenId(null);
  }, [active]);

  const addNote = useCallback((c: Card, body: string) => {
    if (!active) return;
    void outboxRef.current?.enqueue({ kind: "note", request_id: c.id, base_version: c.version, staff_id: active.staff.id, payload: { body } });
    toast.info("Note saved");
  }, [active, toast]);

  const saveLearn = useCallback((c: Card, learn: Learn, caseId: number | null) => {
    if (!active) return;
    void outboxRef.current?.enqueue({
      kind: "learn", request_id: c.id, staff_id: active.staff.id,
      payload: { case_id: caseId ?? undefined, learn, reason: (c.reopen_count ?? 0) > 0 ? "not_resolved" : "complaint" },
    });
  }, [active]);

  const fireSos = useCallback(async () => {
    const who = active ?? Object.values(sessions)[0] ?? null;
    const item = await outboxRef.current?.enqueue({
      kind: "sos", request_id: null, staff_id: who?.staff.id ?? null,
      payload: { floor: device?.floors?.[0] ?? null, staff_name: who?.staff.name ?? null, device_name: device?.name ?? null },
    });
    if (item) setSos({ client_event_id: item.client_event_id, error: null, hidden: false });
  }, [active, sessions, device]);

  // The SOS counts as sent once it has left the outbox (enqueue reports the
  // pending list before it returns, so a fresh SOS is never "sent" early).
  const sosSent = Boolean(sos && !pending.some(p => p.client_event_id === sos.client_event_id));

  const cancelSos = useCallback(async (supervisorId: number, pin: string) => {
    if (!sos) return;
    try {
      const res = await fetch(`/api/clefs/device/service/sos/${encodeURIComponent(sos.client_event_id)}/cancel`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ supervisor_staff_id: supervisorId, pin }),
      });
      if (res.ok) { setSos(null); toast.success("SOS cancelled"); return; }
      // POST /device/service/sos/{id or client_event_id}/cancel answers with
      // a code: incident_not_found (still in the outbox), invalid_pin,
      // pin_locked, pin_not_set, supervisor_required, staff_not_found.
      const body = (await res.json().catch(() => ({}))) as { detail?: string; code?: string };
      const msg = body.code === "incident_not_found"
        ? "The SOS has not reached the server yet. Try again in a moment."
        : res.status === 404 || res.status === 405
          ? "This device cannot cancel an SOS. The duty manager closes it from the console."
          : body.detail || "Wrong supervisor PIN.";
      setSos({ ...sos, error: msg });
    } catch {
      setSos({ ...sos, error: "No connection. Try again, or ask the duty manager to close it." });
    }
  }, [sos, toast]);

  // ─── Render ───

  const openCard = openId != null ? cards.find(c => c.id === openId) ?? serverCards.find(c => c.id === openId) ?? null : null;
  const shiftEnd = active ? plannedEnd(active) : null;
  const shiftOver = shiftEnd != null && now > shiftEnd;
  const supervisors = roster.filter(s => s.has_pin && isSupervisor(s));
  const unsent = pending.length;
  const shell = (body: React.ReactNode) => (
    <div className="svc-dv">
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: deviceStyles }} />
      {body}
    </div>
  );

  if (phase === "boot") return shell(<div className="svc-dv-center"><p className="svc-dv-lead">Starting...</p></div>);
  if (phase === "pair") {
    return shell(<PairScreen hotelSlug={hotelSlug} initialCode={pairCode} reason={pairReason} onPaired={r => { void onPaired(r); }}
      onContinue={() => { setPhase("boot"); void boot(); }} />);
  }

  const sosLayer = (
    <>
      <SosButton onFire={() => { void fireSos(); }} />
      {sos && !sos.hidden && (
        <SosActive sent={sosSent} supervisors={supervisors} error={sos.error}
          onCancel={(id, pin) => { void cancelSos(id, pin); }} onDismiss={() => setSos({ ...sos, hidden: true })} />
      )}
    </>
  );

  if (phase === "locked" || !active) {
    return shell(
      <>
        {unsent > 0 && <div className="svc-dv-banner svc-dv-banner--warn"><span>{unsent} action(s) waiting to send.</span></div>}
        {alarm !== "off" && (
          <div className={`svc-dv-banner ${alarm === "urgent" ? "svc-dv-banner--bad" : "svc-dv-banner--warn"}`}>
            <span>{alarm === "urgent" ? "Urgent request waiting." : "A new job is waiting."} Log in to acknowledge it.</span>
          </div>
        )}
        <LockScreen device={device} roster={roster} loading={rosterLoading} onGesture={onGesture} onUnlocked={onUnlocked}
          onRefresh={() => { void loadRoster().catch(() => undefined); }} />
        {sosLayer}
      </>,
    );
  }

  return shell(
    <>
      <header className="svc-dv-bar">
        <span className={`svc-dv-dot svc-dv-dot--${feedStatus}`} title={`Feed: ${feedStatus}`} aria-label={`Connection ${feedStatus}`} />
        <div className="svc-dv-bar__title">
          <div className="svc-dv-bar__who">{active.staff.name}</div>
          <div className="svc-dv-bar__sub">
            {[device?.name, ctx?.hotel.name, shiftEnd ? `until ${formatTime(shiftEnd, tz, locale)}` : null].filter(Boolean).join(" · ")}
          </div>
        </div>
        <button type="button" className="svc-dv-iconbtn" onClick={lock}>Lock</button>
        <button type="button" className="svc-dv-iconbtn" onClick={() => setLogoutOpen(true)}>Log out</button>
      </header>

      {!soundOn && (
        <div className="svc-dv-banner svc-dv-banner--warn">
          <span>Sound is off.</span>
          <Button size="sm" onClick={onGesture}>Turn on sound</Button>
        </div>
      )}
      {feedStatus === "offline" && <div className="svc-dv-banner svc-dv-banner--bad"><span>Offline. Your taps are saved and will send when the signal is back.</span></div>}
      {unsent > 0 && feedStatus !== "offline" && <div className="svc-dv-banner"><span>Sending {unsent} action(s)...</span></div>}
      {shiftOver && (
        <div className="svc-dv-banner svc-dv-banner--warn">
          <span>Your shift has ended. Leave a handover note and log out.</span>
          <Button size="sm" onClick={() => setLogoutOpen(true)}>Hand over</Button>
        </div>
      )}
      {sos?.hidden && (
        <div className="svc-dv-banner svc-dv-banner--bad">
          <span>SOS {sosSent ? "is open" : "waiting for signal"}.</span>
          <Button size="sm" onClick={() => setSos({ ...sos, hidden: false })}>Show</Button>
        </div>
      )}

      <main className="svc-dv-main">
        <Queue cards={cards} staffIds={staffIds} meId={active.staff.id} deviceId={device?.id ?? null} pendingIds={pendingIds}
          now={now} onOpen={c => { alertsRef.current?.snooze(5_000); setOpenId(c.id); }} loading={queueLoading} />
      </main>

      {openCard && (
        <TaskSheet hotelSlug={hotelSlug} card={openCard} meId={active.staff.id} deviceId={device?.id ?? null}
          pending={pendingIds.has(openCard.id)} now={now} timezone={tz} locale={locale}
          onAction={act} onNote={addNote} onLearn={saveLearn} onClose={closeTask} staffName={staffName} />
      )}

      {phase === "handover" && (
        <HandoverSheet staffName={active.staff.name} handover={handover} loading={handoverState.loading} error={handoverState.error}
          timezone={tz} locale={locale} onAck={ackHandover} onRetry={() => { setHandoverState({ loading: true, error: null }); void loadHandover(); }} />
      )}

      {logoutOpen && (
        <div className="svc-dv-scrim" role="presentation" onClick={e => { if (e.target === e.currentTarget) setLogoutOpen(false); }}>
          <div className="svc-dv-sheet" role="dialog" aria-modal="true" aria-labelledby="svc-dv-lo-title">
            <h2 id="svc-dv-lo-title" className="svc-dv-sheet__title">End your shift</h2>
            <p style={{ margin: 0, color: "var(--clefs-ink-muted)" }}>
              Anything the next person should know? It shows in their handover.
            </p>
            <Textarea rows={4} value={logoutNote} onChange={e => setLogoutNote(e.target.value)} placeholder="e.g. 512 asked for a late check-out; iron in 318 is faulty" />
            {cards.some(c => c.assigned_staff_id === active.staff.id) && (
              <p style={{ margin: 0, color: "var(--cx-warn)", fontSize: "0.85rem" }}>
                You still hold open jobs. They stay assigned to you until a supervisor moves them.
              </p>
            )}
            <div className="svc-dv-actions">
              <Button onClick={() => setLogoutOpen(false)}>Back</Button>
              <Button variant="primary" onClick={() => { void logout(); }}>Log out</Button>
            </div>
          </div>
        </div>
      )}

      {sosLayer}
    </>,
  );
}

// ─── Root (toasts + stripping the pairing code from the address bar) ─────

export function DeviceRoot({ hotelSlug, pairCode }: { hotelSlug: string; pairCode: string | null }) {
  // The code is single use, but it should not sit in the history or be
  // re-submitted by a reload once exchanged.
  useEffect(() => {
    if (!pairCode) return;
    try { window.history.replaceState(null, "", window.location.pathname); } catch { /* ignore */ }
  }, [pairCode]);
  return (
    <ToastProvider>
      <DeviceApp hotelSlug={hotelSlug} pairCode={pairCode} />
    </ToastProvider>
  );
}
