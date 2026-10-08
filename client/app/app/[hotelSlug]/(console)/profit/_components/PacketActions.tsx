"use client";

/* The decisions on one dispute packet (02 spec 3.7):
   - Approve: owner or finance only (the server says so in can_approve);
     approval stamps the packet and accepts its findings.
   - Mark sent: how it went to the OTA (extranet, email, portal) and the
     OTA's case number; optionally Clefs emails it (HTML body, PDF).
   - Record outcome: acknowledged, credited, partly credited (amount) or
     rejected. Credits on the next statement are verified automatically,
     so this is for what arrives outside a statement. */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { currencyDigits } from "../../../../../lib/format";
import { Button, Field, Input, Modal, Select, Textarea, useToast } from "../../../../../ui";
import { majorToMinor, minorToDecimal, pgApi } from "../lib";
import type { Packet } from "../types";
import { errorText } from "./bits";

type Open = null | "sent" | "outcome";

export function PacketActions({ slug, packet, onChange }: { slug: string; packet: Packet; onChange: (p: Packet) => void }) {
  const toast = useToast();
  const [open, setOpen] = useState<Open>(null);
  const [busy, setBusy] = useState(false);
  const [channel, setChannel] = useState("extranet");
  const [reference, setReference] = useState("");
  const [sendTo, setSendTo] = useState("");
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState("CREDITED");
  const [amount, setAmount] = useState("");
  const digits = currencyDigits(packet.currency);
  const amountMinor = majorToMinor(amount, digits);

  async function call(path: string, body: unknown, ok: string) {
    setBusy(true);
    try {
      const p = await api.post<Packet>(pgApi(slug, `/disputes/${packet.id}${path}`), body);
      onChange(p);
      toast({ tone: "ok", title: ok });
      setOpen(null);
      setNote("");
    } catch (err) {
      toast({ tone: "bad", title: "That did not work", body: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const sentBody = () => ({
    channel, reference: reference.trim() || undefined, note: note.trim() || undefined,
    send_to: channel === "email" && sendTo.trim() ? sendTo.trim() : undefined,
  });
  const outcomeBody = () => ({
    outcome, note: note.trim() || undefined,
    amount: outcome === "PARTIAL" && amountMinor !== null ? minorToDecimal(amountMinor, digits) : undefined,
  });
  const s = packet.status;
  const canSend = s === "APPROVED";
  const canOutcome = s === "SUBMITTED" || s === "ACKNOWLEDGED" || s === "PARTIAL";

  return (
    <>
      <div className="pg-row">
        {s === "DRAFT" && (packet.can_approve ? (
          <Button variant="primary" loading={busy && open === null}
                  onClick={() => call("/approve", {}, "Approved. Send it to the OTA next.")}>Approve packet</Button>
        ) : <span className="pg-faint">Waiting for the owner or finance to approve.</span>)}
        {canSend && <Button variant="primary" onClick={() => setOpen("sent")}>Mark as sent</Button>}
        {canOutcome && <Button variant="secondary" onClick={() => setOpen("outcome")}>Record the OTA&apos;s answer</Button>}
      </div>

      <Modal open={open === "sent"} onClose={() => setOpen(null)} title="Mark as sent to the OTA" size="sm" dismissible={!busy}
             footer={<>
               <Button variant="ghost" onClick={() => setOpen(null)} disabled={busy}>Cancel</Button>
               <Button variant="primary" loading={busy} disabled={channel === "email" && !sendTo.trim()}
                       onClick={() => call("/submitted", sentBody(), "Marked as sent. Clefs will follow up.")}>Save</Button>
             </>}>
        <div className="cx-stack">
          <Field label="Sent through">
            <Select value={channel} onChange={e => setChannel(e.target.value)}>
              <option value="extranet">The OTA extranet (I pasted the text)</option>
              <option value="email">Email it from Clefs now</option>
              <option value="portal">A partner portal case</option>
              <option value="other">Other</option>
            </Select>
          </Field>
          {channel === "email" && (
            <Field label="Send to" hint="The OTA's finance or account manager address. The PDF is attached.">
              <Input type="email" value={sendTo} maxLength={320} onChange={e => setSendTo(e.target.value)} />
            </Field>
          )}
          <Field label="Case or ticket number" hint="Optional, helps the follow-up.">
            <Input value={reference} maxLength={200} onChange={e => setReference(e.target.value)} />
          </Field>
          <Field label="Note"><Textarea rows={2} value={note} maxLength={2000} onChange={e => setNote(e.target.value)} /></Field>
        </div>
      </Modal>

      <Modal open={open === "outcome"} onClose={() => setOpen(null)} title="What did the OTA say?" size="sm" dismissible={!busy}
             footer={<>
               <Button variant="ghost" onClick={() => setOpen(null)} disabled={busy}>Cancel</Button>
               <Button variant="primary" loading={busy} disabled={outcome === "PARTIAL" && (amountMinor === null || amountMinor <= 0)}
                       onClick={() => call("/outcome", outcomeBody(), "Recorded")}>Save</Button>
             </>}>
        <div className="cx-stack">
          <Field label="Answer">
            <Select value={outcome} onChange={e => setOutcome(e.target.value)}>
              <option value="ACKNOWLEDGED">They acknowledged it, credit pending</option>
              <option value="CREDITED">They credited all of it</option>
              <option value="PARTIAL">They credited part of it</option>
              <option value="REJECTED">They rejected it</option>
            </Select>
          </Field>
          {outcome === "PARTIAL" && (
            <Field label={`Credited amount (${packet.currency})`} error={amount && amountMinor === null ? "Enter an amount like 84.70" : undefined}>
              <Input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} />
            </Field>
          )}
          <Field label="Note"><Textarea rows={2} value={note} maxLength={2000} onChange={e => setNote(e.target.value)} /></Field>
          <p className="pg-faint" style={{ margin: 0 }}>
            A credit that shows on the next statement or payout is verified by Clefs without this step.
          </p>
        </div>
      </Modal>
    </>
  );
}
