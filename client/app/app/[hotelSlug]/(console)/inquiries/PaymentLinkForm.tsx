"use client";

/* New payment link for a sent or accepted quote (POST /{id}/payment-links).
   The usual choice is a milestone from the quote's own deposit schedule, so
   the amount and the Tourism Dirham split match what the guest was shown;
   "another amount" exists for a guest who asks to pay differently. The
   provider is the hotel's connected one unless "manual" is picked (a bank
   transfer or POS link typed here, marked paid later with proof). */

import { useState } from "react";
import { api } from "../../../../lib/api";
import { currencyDigits, useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { Button, Field, Input, Select, useToast } from "../../../../ui";
import { errorText, inqPath, majorToMinor } from "./lib";
import type { InquiryDetail, Quote } from "./types";

type Purpose = "deposit" | "full" | "balance";

export default function PaymentLinkForm({ row, quote, onCancel, onCreated }: {
  row: InquiryDetail;
  quote: Quote;
  onCancel: () => void;
  onCreated: () => void;
}) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const digits = currencyDigits(quote.currency);
  const [milestone, setMilestone] = useState<string>(String(quote.deposit_schedule[0]?.seq ?? "custom"));
  const [purpose, setPurpose] = useState<Purpose>("deposit");
  const [amount, setAmount] = useState("");
  const [manual, setManual] = useState(false);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const m = quote.deposit_schedule.find(x => String(x.seq) === milestone);
  const customMinor = milestone === "custom" ? majorToMinor(amount, digits) : null;
  const urlOk = !manual || /^https:\/\/\S+$/i.test(url.trim());
  const ready = (m || (customMinor !== null && customMinor > 0)) && urlOk;

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const body = {
        quote_version: quote.version,
        purpose: m ? m.purpose : purpose,
        amount_minor: m ? m.amount_minor : customMinor,
        milestone: m ? m.seq : undefined,
        provider: manual ? "manual" : undefined,
        url: manual ? url.trim() : undefined,
      };
      const res = await api.post<{ action?: { status?: string } }>(inqPath(slug, `/${row.id}/payment-links`), body);
      if (res.action?.status === "proposed") toast.info("Waiting for approval", "The link goes out once it is approved.");
      else toast.success("Payment link on its way");
      onCreated();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inq-note" style={{ display: "grid", gap: 12 }}>
      <Field label="What for">
        <Select value={milestone} onChange={e => setMilestone(e.target.value)}>
          {quote.deposit_schedule.map(x => (
            <option key={x.seq} value={String(x.seq)}>
              {x.label}: {fmt.money(x.amount_value, { currency: quote.currency })}
              {x.due_on ? ` (due ${fmt.date(`${x.due_on}T12:00:00Z`)})` : ""}
            </option>
          ))}
          <option value="custom">Another amount</option>
        </Select>
      </Field>
      {milestone === "custom" && (
        <div className="inq-form">
          <Field label="Purpose">
            <Select value={purpose} onChange={e => setPurpose(e.target.value as Purpose)}>
              <option value="deposit">Deposit</option>
              <option value="full">Full payment</option>
              <option value="balance">Balance</option>
            </Select>
          </Field>
          <Field label={`Amount (${quote.currency})`} error={amount && customMinor === null ? "Use digits, for example 504.34" : undefined}>
            <Input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} />
          </Field>
        </div>
      )}
      <label className="inq-row">
        <input type="checkbox" checked={manual} onChange={e => setManual(e.target.checked)} />
        Manual link (bank transfer or POS), marked paid by hand
      </label>
      {manual && (
        <Field label="Payment page address" error={url && !urlOk ? "Use a full https:// address" : undefined}>
          <Input type="url" value={url} maxLength={2000} placeholder="https://" onChange={e => setUrl(e.target.value)} />
        </Field>
      )}
      {error && <p className="inq-note inq-note--bad" role="alert" style={{ margin: 0 }}>{error}</p>}
      <div className="inq-row">
        <Button variant="primary" loading={busy} disabled={!ready} onClick={create}>Create and send link</Button>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
      </div>
    </div>
  );
}
