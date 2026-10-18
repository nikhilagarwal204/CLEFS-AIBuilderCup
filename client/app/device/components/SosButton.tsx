"use client";

/* Staff SOS (03 spec 1.5): a 2-second long-press, so a pocket or a brushed
   screen never fires it. It goes through the outbox (so it is sent the
   moment the network allows, and exactly once), creates a staff_sos
   incident with the device's floor and the staff id, and pages Security and
   the duty manager. Only a supervisor's PIN can cancel it.

   The press is pointer-based (touch, pen, mouse) with a keyboard fallback:
   holding Space or Enter for the same 2 seconds. */

import { useEffect, useRef, useState } from "react";
import { Button, Input, Select } from "../../ui";
import type { RosterStaff } from "../lib/deviceApi";

export const SOS_HOLD_MS = 2_000;

export function SosButton({ onFire }: { onFire: () => void }) {
  const [progress, setProgress] = useState(0);
  const start = useRef<number | null>(null);
  const raf = useRef<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stop = () => {
    start.current = null;
    if (raf.current) cancelAnimationFrame(raf.current);
    if (timer.current) clearTimeout(timer.current);
    raf.current = null;
    timer.current = null;
    setProgress(0);
  };

  // The fill is cosmetic and runs on animation frames; the SOS itself fires
  // on a timer, because browsers throttle frames (battery saver, a busy
  // tab) and a safety alarm must not wait for the compositor.
  const tick = (ts: number) => {
    if (start.current === null) return;
    setProgress(Math.min(1, Math.max(0, (ts - start.current) / SOS_HOLD_MS)));
    raf.current = requestAnimationFrame(tick);
  };

  const fire = () => {
    stop();
    try { navigator.vibrate?.([400, 100, 400]); } catch { /* iOS */ }
    onFire();
  };

  /** `t0` is the event's timeStamp (same clock as the frame timestamps). */
  const begin = (t0: number) => {
    if (start.current !== null) return;
    start.current = t0;
    timer.current = setTimeout(fire, SOS_HOLD_MS);
    try { navigator.vibrate?.(30); } catch { /* iOS */ }
    raf.current = requestAnimationFrame(tick);
  };

  useEffect(() => () => {
    if (raf.current) cancelAnimationFrame(raf.current);
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return (
    <button
      type="button"
      className="svc-dv-sos"
      aria-label="SOS: hold for 2 seconds to call Security and the duty manager"
      onPointerDown={e => { e.currentTarget.setPointerCapture?.(e.pointerId); begin(e.timeStamp); }}
      onPointerUp={stop}
      onPointerCancel={stop}
      onLostPointerCapture={stop}
      onContextMenu={e => e.preventDefault()}
      onKeyDown={e => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); begin(e.timeStamp); } }}
      onKeyUp={e => { if (e.key === " " || e.key === "Enter") stop(); }}
      onBlur={stop}
    >
      <span className="svc-dv-sos__fill" style={{ transform: `scaleY(${progress})` }} aria-hidden="true" />
      <span className="svc-dv-sos__label">{progress > 0 ? "HOLD" : "SOS"}</span>
    </button>
  );
}

/** Full-screen state after an SOS: what happened, and the supervisor cancel. */
export function SosActive({
  sent, supervisors, onCancel, onDismiss, error,
}: {
  /** False while it waits in the outbox (offline). */
  sent: boolean;
  supervisors: RosterStaff[];
  onCancel: (supervisorId: number, pin: string) => void;
  /** Hide the overlay (the SOS stays open). */
  onDismiss: () => void;
  error: string | null;
}) {
  const [sup, setSup] = useState<number | "">(supervisors[0]?.id ?? "");
  const [pin, setPin] = useState("");
  return (
    <div className="svc-dv-sos-active" role="alertdialog" aria-labelledby="svc-dv-sos-title" aria-live="assertive">
      <h2 id="svc-dv-sos-title">SOS {sent ? "sent" : "waiting for signal"}</h2>
      <p style={{ margin: 0, maxWidth: "36ch", fontSize: "1.05rem" }}>
        {sent
          ? "Security and the duty manager have been paged with your floor. Stay where it is safe."
          : "No connection yet. It will send by itself the moment the device is back online. If you can, call Security on the house phone."}
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, width: "min(100%, 320px)", marginTop: 12 }}>
        <strong>Supervisor cancel</strong>
        <Select value={sup} onChange={e => setSup(e.target.value ? Number(e.target.value) : "")} aria-label="Supervisor">
          {supervisors.length === 0 && <option value="">No supervisor on this device</option>}
          {supervisors.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </Select>
        <Input type="password" inputMode="numeric" autoComplete="off" placeholder="Supervisor PIN" value={pin}
          onChange={e => setPin(e.target.value.replace(/\D/g, "").slice(0, 6))} aria-label="Supervisor PIN" />
        {error && <div role="alert" style={{ fontSize: "0.9rem" }}>{error}</div>}
        <Button disabled={!sup || pin.length < 4} onClick={() => { if (sup) onCancel(sup, pin); setPin(""); }}>Cancel SOS</Button>
        <Button variant="ghost" onClick={onDismiss} style={{ color: "#fff" }}>Back to the queue (SOS stays open)</Button>
      </div>
    </div>
  );
}
