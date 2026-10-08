"use client";

/* ActionBar for one finding (02 spec 3.4-3.5): Accept, Dispute, Dismiss
   with a reason, and, once disputed, record what the OTA did. Every
   dismissal reason feeds the weekly tuning (TOO_SMALL raises the minimum,
   three false positives pause the check), so the reason is required.
   Dispute drafts a one-finding packet, or opens the packet it is already
   in. The buttons only ask; the server's status machine decides. */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../../../../lib/api";
import { currencyDigits } from "../../../../../lib/format";
import { Button, Field, Input, Modal, Select, Textarea, useToast } from "../../../../../ui";
import { DISMISS_REASONS, findingMoves, majorToMinor, minorToDecimal, owedMinor, pgApi, pgHref } from "../lib";
import type { DismissReason, FindingDetail, Packet } from "../types";
import { errorText } from "./bits";

type Busy = null | "accept" | "dispute" | "dismiss" | "reopen" | "recovered" | "rejected";

export function FindingActionBar({
  slug, finding, onChange,
}: {
  slug: string;
  finding: FindingDetail;
  onChange: (f: FindingDetail) => void;
}) {
  const toast = useToast();
  const router = useRouter();
  const [busy, setBusy] = useState<Busy>(null);
  const [dismissOpen, setDismissOpen] = useState(false);
  const [reason, setReason] = useState<DismissReason | "">("");
  const [note, setNote] = useState("");
  const [recOpen, setRecOpen] = useState(false);
  const digits = currencyDigits(finding.currency);
  const [amount, setAmount] = useState(minorToDecimal(finding.amount_minor, digits));

  const packetOpen = Boolean(finding.dispute_packet && !["CREDITED", "REJECTED"].includes(finding.dispute_packet.status));
  const moves = findingMoves(finding, packetOpen);

  async function run(kind: Busy, fn: () => Promise<FindingDetail | void>, ok: string) {
    setBusy(kind);
    try {
      const out = await fn();
      if (out) onChange(out);
      toast({ tone: "ok", title: ok });
      return true;
    } catch (err) {
      toast({ tone: "bad", title: "That did not work", body: errorText(err) });
      return false;
    } finally {
      setBusy(null);
    }
  }

  const move = (to: string, body: Record<string, unknown> = {}) =>
    api.post<FindingDetail>(pgApi(slug, `/findings/${finding.id}/transition`), { to, ...body });

  async function dispute() {
    if (finding.dispute_packet && packetOpen) {
      router.push(pgHref(slug, `/disputes/${finding.dispute_packet.id}`));
      return;
    }
    await run("dispute", async () => {
      const p = await api.post<Packet>(pgApi(slug, "/disputes"), { finding_ids: [finding.id] });
      router.push(pgHref(slug, `/disputes/${p.id}`));
    }, "Dispute packet drafted");
  }

  async function dismiss() {
    if (!reason) return;
    const ok = await run("dismiss", () => api.post<FindingDetail>(pgApi(slug, `/findings/${finding.id}/dismiss`), {
      reason, note: note.trim() || undefined,
    }), "Finding dismissed");
    if (ok) { setDismissOpen(false); setNote(""); setReason(""); }
  }

  const recMinor = majorToMinor(amount, digits);
  async function recovered() {
    if (recMinor === null) return;
    const ok = await run("recovered", () => move("RECOVERED", {
      amount: minorToDecimal(recMinor, digits), note: note.trim() || undefined,
    }), "Recorded as recovered");
    if (ok) { setRecOpen(false); setNote(""); }
  }

  const none = !moves.accept && !moves.dispute && !moves.dismiss && !moves.reopen && !moves.markRecovered;
  if (none) return <div className="pg-faint">Nothing left to decide on this finding.</div>;

  return (
    <>
      <div className="pg-row">
        {moves.accept && (
          <Button variant="secondary" loading={busy === "accept"}
                  onClick={() => run("accept", () => move("ACCEPTED"), "Accepted")}>Accept</Button>
        )}
        {(moves.dispute || (finding.dispute_packet && packetOpen)) && (
          <Button variant="primary" loading={busy === "dispute"} onClick={dispute}>
            {finding.dispute_packet && packetOpen ? "Open dispute packet" : "Dispute"}
          </Button>
        )}
        {moves.markRecovered && (
          <Button variant="secondary" onClick={() => { setAmount(minorToDecimal(owedMinor(finding) || finding.amount_minor, digits)); setRecOpen(true); }}>
            Mark recovered
          </Button>
        )}
        {moves.markRejected && (
          <Button variant="ghost" loading={busy === "rejected"}
                  onClick={() => run("rejected", () => move("REJECTED"), "Recorded as rejected by the OTA")}>
            OTA rejected it
          </Button>
        )}
        {moves.reopen && (
          <Button variant="secondary" loading={busy === "reopen"}
                  onClick={() => run("reopen", () => move("DETECTED"), "Reopened")}>Reopen</Button>
        )}
        {moves.dismiss && <Button variant="ghost" onClick={() => setDismissOpen(true)}>Dismiss</Button>}
      </div>
      <Modal open={dismissOpen} onClose={() => setDismissOpen(false)} title="Dismiss this finding" size="sm"
             dismissible={busy !== "dismiss"}
             footer={(
               <>
                 <Button variant="ghost" onClick={() => setDismissOpen(false)} disabled={busy === "dismiss"}>Cancel</Button>
                 <Button variant="danger" disabled={!reason} loading={busy === "dismiss"} onClick={dismiss}>Dismiss</Button>
               </>
             )}>
        <div className="cx-stack">
          <p className="pg-muted" style={{ margin: 0 }}>
            The reason teaches the checks: &quot;too small&quot; raises the minimum amount, and three false positives in 30 days
            pause this check for two weeks.
          </p>
          <Field label="Reason" required>
            <Select value={reason} onChange={e => setReason(e.target.value as DismissReason | "")}>
              <option value="">Choose a reason</option>
              {DISMISS_REASONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
            </Select>
          </Field>
          <Field label="Note" hint="Optional. What should the next person know?">
            <Textarea rows={3} value={note} maxLength={2000} onChange={e => setNote(e.target.value)} />
          </Field>
        </div>
      </Modal>

      <Modal open={recOpen} onClose={() => setRecOpen(false)} title="Record a recovery" size="sm"
             dismissible={busy !== "recovered"}
             footer={(
               <>
                 <Button variant="ghost" onClick={() => setRecOpen(false)} disabled={busy === "recovered"}>Cancel</Button>
                 <Button variant="primary" disabled={recMinor === null} loading={busy === "recovered"} onClick={recovered}>Save</Button>
               </>
             )}>
        <div className="cx-stack">
          <p className="pg-muted" style={{ margin: 0 }}>
            Clefs verifies credits from the next statement or payout on its own. Record one here only when the OTA paid it
            another way; the amount is the total credited for this finding.
          </p>
          <Field label={`Credited total (${finding.currency})`} error={recMinor === null ? "Enter an amount like 84.70" : undefined}>
            <Input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} />
          </Field>
          <Field label="Note" hint="Optional, e.g. the credit note number.">
            <Textarea rows={2} value={note} maxLength={2000} onChange={e => setNote(e.target.value)} />
          </Field>
        </div>
      </Modal>
    </>
  );
}
