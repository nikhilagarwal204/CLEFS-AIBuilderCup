"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Send now (brief.manage): a fresh send_now run for this tier, to every
   recipient or as a test to the caller only. The answer says who got it
   on which channel, so a manager setting up WhatsApp sees at once whether
   the template went or the email fallback did. Audited server-side.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../../../lib/api";
import { Badge, Button, Modal, Segmented, useToast } from "../../../../ui";
import { brfApi, CADENCE_LABEL } from "./lib";
import type { Cadence, SendNowResult } from "./types";

const REASON: Record<string, string> = {
  no_recipients: "Nobody is set to receive the brief. Add recipients in Settings.",
  not_delivered: "No channel accepted it. Check each recipient's email and WhatsApp in Settings.",
  invalid_cadence: "That tier cannot be sent.",
};

export default function SendNowDialog({ slug, cadence, open, onClose, onSent }: {
  slug: string;
  cadence: Cadence;
  open: boolean;
  onClose: () => void;
  onSent: (briefId: number | null) => void;
}) {
  const toast = useToast();
  const [who, setWho] = useState<"me" | "all">("me");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SendNowResult | null>(null);

  const close = () => { setResult(null); onClose(); };

  const send = async () => {
    setBusy(true);
    try {
      const r = await api.post<SendNowResult>(brfApi(slug, "/send-now"), { cadence, only_me: who === "me" });
      setResult(r);
      if (r.ok) toast.success(r.status === "partial" ? "Sent to some recipients" : "Brief sent");
    } catch (err) {
      toast.error("Not sent", (err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={close} size="sm" dismissible={!busy} title={`Send the ${CADENCE_LABEL[cadence].toLowerCase()} brief now`}
           footer={result ? (
             <Button variant="primary" onClick={() => onSent(result.brief_id)}>{result.brief_id ? "Open this brief" : "Close"}</Button>
           ) : (
             <>
               <Button variant="ghost" onClick={close} disabled={busy}>Cancel</Button>
               <Button variant="primary" loading={busy} onClick={() => void send()}>{who === "me" ? "Send me a test" : "Send to everyone"}</Button>
             </>
           )}>
      {!result ? (
        <div className="cx-stack" style={{ gap: 12 }}>
          <Segmented ariaLabel="Recipients" value={who} onChange={setWho}
                     options={[{ value: "me", label: "Only me (test)" }, { value: "all", label: "All recipients" }]} />
          <p className="brf-muted" style={{ margin: 0 }}>
            A new brief is built from what Clefs knows right now and sent on each person&apos;s first working channel.
            The scheduled brief still goes out as usual.
          </p>
        </div>
      ) : (
        <div className="cx-stack" style={{ gap: 10 }}>
          {result.sent_to.length > 0 ? (
            <ul className="brf-pick" aria-label="Delivered to">
              {result.sent_to.map(s => (
                <li key={`${s.user_id}-${s.channel}`} className="brf-row" style={{ justifyContent: "space-between" }}>
                  <span>{s.name}</span><Badge tone="ok">{s.channel === "whatsapp" ? "WhatsApp" : s.channel}</Badge>
                </li>
              ))}
            </ul>
          ) : (
            <p style={{ margin: 0 }}>{REASON[result.reason ?? ""] ?? "Nobody received it."}</p>
          )}
          {result.reason && result.sent_to.length > 0 && <p className="brf-muted" style={{ margin: 0 }}>{REASON[result.reason]}</p>}
        </div>
      )}
    </Modal>
  );
}
