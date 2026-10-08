"use client";

/* Lock screen (03 spec 1.6): staff tiles for the device's scope, then a PIN
   pad. The PIN submit is also the user gesture that unlocks audio, so the
   alert loop can start the moment the queue opens.

   The foundation verifies the PIN (5 wrong tries lock a person on this
   device for 5 minutes; 15 across devices lock them everywhere) and opens
   or returns the device session with planned_end_at = shift end + grace. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "../../lib/api";
import { initials } from "../../lib/initials";
import { Button, Input } from "../../ui";
import { startShift, type DeviceInfo, type RosterStaff, type ShiftSession } from "../lib/deviceApi";

const PIN_MIN = 4;
const PIN_MAX = 6;

function pinError(err: unknown): string {
  if (!(err instanceof ApiError)) return "Could not log in. Try again.";
  switch (err.code) {
    // The server's wording carries the tries left ("Wrong PIN (4 tries left)").
    case "invalid_pin": return err.message || "Wrong PIN. Try again.";
    case "pin_locked": return err.message || "Too many wrong PINs. Try again in a few minutes.";
    case "pin_not_set": return "No PIN yet. Ask your manager to set one in the console.";
    case "staff_not_found": return "This person is not on this device's team.";
    default: return err.status === 0 ? "No connection. Your PIN needs the network once per shift." : err.message;
  }
}

export function LockScreen({
  device, roster, loading, onGesture, onUnlocked, onRefresh,
}: {
  device: DeviceInfo | null;
  roster: RosterStaff[];
  loading: boolean;
  /** Runs inside the PIN submit (audio unlock). */
  onGesture: () => void;
  onUnlocked: (session: ShiftSession) => void;
  onRefresh: () => void;
}) {
  const [picked, setPicked] = useState<RosterStaff | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shake, setShake] = useState(false);
  const [query, setQuery] = useState("");

  const tiles = useMemo(() => {
    const q = query.trim().toLowerCase();
    return [...roster]
      .filter(s => !q || s.name.toLowerCase().includes(q) || (s.department ?? "").toLowerCase().includes(q))
      .sort((a, b) => Number(b.on_shift) - Number(a.on_shift) || a.name.localeCompare(b.name));
  }, [roster, query]);

  const submit = useCallback(async (value: string) => {
    if (!picked || busy) return;
    onGesture();
    if (value.length < PIN_MIN) { setError(`PINs are ${PIN_MIN} to ${PIN_MAX} digits.`); return; }
    setBusy(true);
    setError(null);
    try {
      const session = await startShift(picked.id, value);
      setPin("");
      setPicked(null);
      onUnlocked(session);
    } catch (err) {
      setError(pinError(err));
      setPin("");
      setShake(true);
      setTimeout(() => setShake(false), 400);
    } finally {
      setBusy(false);
    }
  }, [picked, busy, onGesture, onUnlocked]);

  const press = useCallback((k: string) => {
    if (busy) return;
    setError(null);
    if (k === "back") { setPin(p => p.slice(0, -1)); return; }
    if (k === "enter") { void submit(pin); return; }
    const next = (pin + k).slice(0, PIN_MAX);
    setPin(next);
    if (next.length === PIN_MAX) void submit(next);
  }, [busy, pin, submit]);

  // Hardware keyboards and the numeric row work too.
  useEffect(() => {
    if (!picked) return;
    const onKey = (e: KeyboardEvent) => {
      if (/^[0-9]$/.test(e.key)) press(e.key);
      else if (e.key === "Backspace") press("back");
      else if (e.key === "Enter") press("enter");
      else if (e.key === "Escape") { setPicked(null); setPin(""); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [picked, press]);

  if (picked) {
    return (
      <div className="svc-dv-center">
        <div className="svc-dv-avatar" style={{ width: 64, height: 64, fontSize: "1.2rem" }} aria-hidden="true">{initials(picked.name)}</div>
        <h1 className="svc-dv-h1">{picked.name}</h1>
        <p className="svc-dv-lead">Enter your PIN</p>
        <div className={`svc-dv-pinwrap${shake ? " svc-dv-shake" : ""}`}>
          <div className="svc-dv-pindots" role="status" aria-label={`${pin.length} digits entered`}>
            {Array.from({ length: Math.max(PIN_MIN, pin.length) }, (_, i) => (
              <span key={i} className={`svc-dv-pindot${i < pin.length ? " svc-dv-pindot--on" : ""}`} />
            ))}
          </div>
          <div className="svc-dv-error" role="alert">{error}</div>
          <div className="svc-dv-pinpad">
            {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map(k => (
              <button key={k} type="button" className="svc-dv-key" onClick={() => press(k)} disabled={busy}>{k}</button>
            ))}
            <button type="button" className="svc-dv-key svc-dv-key--text" onClick={() => press("back")} aria-label="Delete digit">Del</button>
            <button type="button" className="svc-dv-key" onClick={() => press("0")} disabled={busy}>0</button>
            <button type="button" className="svc-dv-key svc-dv-key--text" onClick={() => press("enter")} disabled={busy || pin.length < PIN_MIN}>
              {busy ? "..." : "OK"}
            </button>
          </div>
        </div>
        <Button variant="ghost" onClick={() => { setPicked(null); setPin(""); setError(null); }}>Not you? Back</Button>
      </div>
    );
  }

  return (
    <div className="svc-dv-center" style={{ justifyContent: "flex-start" }}>
      <div className="svc-dv-brand">{device?.name ?? "Floor device"}</div>
      <h1 className="svc-dv-h1">Who is starting?</h1>
      {roster.length > 12 && (
        <div className="svc-dv-search">
          <Input placeholder="Search name or department" value={query} onChange={e => setQuery(e.target.value)} aria-label="Search staff" />
        </div>
      )}
      <div className="svc-dv-tiles">
        {tiles.map(s => (
          <button
            key={s.id}
            type="button"
            className="svc-dv-tile"
            disabled={!s.has_pin}
            onClick={() => { setPicked(s); setPin(""); setError(null); }}
            title={s.has_pin ? undefined : "No PIN yet: ask your manager to set one"}
          >
            <span className="svc-dv-avatar" aria-hidden="true">{initials(s.name)}</span>
            <span className="svc-dv-tile__name">{s.name}</span>
            <span className="svc-dv-tile__meta">
              {[s.department, s.on_shift ? "On shift here" : null, s.has_pin ? null : "No PIN"].filter(Boolean).join(" · ")}
            </span>
          </button>
        ))}
      </div>
      {!loading && roster.length === 0 && (
        <div className="svc-dv-empty">
          Nobody is set up for this device yet. A manager adds staff and PINs in the console.
          <div style={{ marginTop: 12 }}><Button onClick={onRefresh}>Refresh</Button></div>
        </div>
      )}
    </div>
  );
}
