"use client";

/* Pair screen (03 spec 1.6). A manager makes an 8-character, single-use code
   in the console (Service > Staff > Pair device); the QR opens
   /device/{slug}?code=... and this screen exchanges it at once. The server
   answers with the clefs_device cookie (HttpOnly), so nothing secret is ever
   stored by script or left in the URL: the code is stripped from the address
   bar before the exchange. */

import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import { Button } from "../../ui";
import { pairDevice, type PairResult } from "../lib/deviceApi";

const CODE_RE = /^[A-Z0-9]{6,12}$/;

function pairError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "Too many tries. Wait a minute, then try again.";
    if (err.status === 0) return "No connection. Check the Wi-Fi and try again.";
    if (err.status === 400 || err.status === 401 || err.status === 404 || err.status === 410) {
      return "That code is not valid or has expired. Ask your manager for a new one.";
    }
    return err.message;
  }
  return "Pairing failed. Try again.";
}

export function PairScreen({
  hotelSlug, initialCode, reason, onPaired, onContinue,
}: {
  hotelSlug: string;
  initialCode?: string | null;
  /** Why we are here again (revoked, cookie cleared). */
  reason?: string | null;
  onPaired: (res: PairResult) => void;
  /** Offered after a failed QR exchange: the device may already be paired
      (an old QR link reopened), so let it carry on with that pairing. */
  onContinue?: () => void;
}) {
  const [code, setCode] = useState((initialCode ?? "").toUpperCase());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const autoTried = useRef(false);

  const submit = async (value: string) => {
    const v = value.replace(/[\s-]/g, "").toUpperCase();
    if (!CODE_RE.test(v)) { setError("Enter the 8-character code from the manager's screen."); return; }
    setBusy(true);
    setError(null);
    try {
      onPaired(await pairDevice(v));
    } catch (err) {
      setError(pairError(err));
    } finally {
      setBusy(false);
    }
  };

  // A QR link carries the code: exchange it once, without a tap.
  useEffect(() => {
    if (autoTried.current || !initialCode) return;
    autoTried.current = true;
    void submit(initialCode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCode]);

  return (
    <div className="svc-dv-center">
      <div className="svc-dv-brand">Clefs floor device</div>
      <h1 className="svc-dv-h1">Pair this device</h1>
      <p className="svc-dv-lead">
        {reason || `Ask a manager for a pairing code for ${hotelSlug}. They make one in Service, under Staff, with "Pair device".`}
      </p>
      <form
        onSubmit={e => { e.preventDefault(); void submit(code); }}
        style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14, width: "100%" }}
      >
        <label htmlFor="svc-dv-code" className="cx-sr">Pairing code</label>
        <input
          id="svc-dv-code"
          className="svc-dv-code"
          value={code}
          onChange={e => setCode(e.target.value.toUpperCase().slice(0, 12))}
          autoComplete="one-time-code"
          autoCapitalize="characters"
          autoCorrect="off"
          spellCheck={false}
          inputMode="text"
          placeholder="ABCD-2345"
          aria-invalid={Boolean(error) || undefined}
          aria-describedby="svc-dv-pair-err"
        />
        <div id="svc-dv-pair-err" className="svc-dv-error" role="alert">{error}</div>
        <Button type="submit" variant="primary" loading={busy} className="svc-dv-big">Pair device</Button>
        {error && initialCode && onContinue && (
          <Button variant="ghost" onClick={onContinue}>Already paired? Continue</Button>
        )}
      </form>
      <p className="svc-dv-lead" style={{ fontSize: "0.8rem" }}>
        Android devices are best for floor units: iPhones only get background alerts after &quot;Add to Home Screen&quot;.
      </p>
    </div>
  );
}
