"use client";

/* Payment links (4.8). Creating one is an `inquiries.send_payment_link`
   action (the dial decides; an approver's press approves it at once), and
   only a provider status check moves a link to paid, never a webhook
   alone. A manual link (bank transfer, a POS terminal) is marked paid by
   an approver with proof attached. Money is the server's Decimal strings. */

import { useState } from "react";
import { api, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, EmptyState, FileDrop, MoneyText, Skeleton, useToast, useUpload } from "../../../../ui";
import PaymentLinkForm from "./PaymentLinkForm";
import { useCopy } from "./SetupChecklist";
import { errorText, inqPath, LINK_STATUS_TONE } from "./lib";
import type { InquiryDetail, PaymentLink, Quote } from "./types";

const PURPOSE_TEXT: Record<PaymentLink["purpose"], string> = { deposit: "Deposit", full: "Full payment", balance: "Balance" };

export default function PaymentPanel({ row, onChanged }: { row: InquiryDetail; onChanged: () => void }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const [version, setVersion] = useState(0);
  const links = useApi<PaymentLink[]>(inqPath(slug, `/${row.id}/payment-links`), [version]);
  const quotes = useApi<Quote[]>(inqPath(slug, `/${row.id}/quotes`), [version]);
  const [creating, setCreating] = useState(false);
  // A new link is an action: its row appears once the executor has created
  // it at the provider (or once it is approved), not at the moment of asking.
  const [requested, setRequested] = useState(false);
  const refresh = () => { setVersion(v => v + 1); onChanged(); };

  // Links hang off a quote the guest has seen (sent or accepted).
  const payable = (quotes.data ?? []).find(q => q.status === "accepted" || q.status === "sent");

  if (links.loading && !links.data) return <Skeleton lines={4} height={16} />;
  if (links.error) {
    return <EmptyState title="Could not load payment links" body={links.error.message}
      action={<Button onClick={links.reload}>Try again</Button>} />;
  }
  const list = links.data ?? [];

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {requested && (
        <p className="inq-note" style={{ margin: 0 }}>
          Link requested. It shows here as soon as it has been created and sent (or once it is approved).
        </p>
      )}
      {list.length === 0 && !creating && !requested && (
        <p className="inq-muted" style={{ margin: 0 }}>
          {payable
            ? "No payment link yet. When the guest accepts the quote, the first deposit link is prepared automatically."
            : "Send a quote first: a payment link is always for a quote the guest has seen."}
        </p>
      )}
      {list.map(l => <LinkRow key={l.id} link={l} onChanged={refresh} />)}
      {payable && can("inquiries.quote") && (
        creating
          ? <PaymentLinkForm row={row} quote={payable} onCancel={() => setCreating(false)}
              onCreated={() => { setCreating(false); setRequested(true); refresh(); }} />
          : <div><Button onClick={() => setCreating(true)}>New payment link</Button></div>
      )}
    </div>
  );
}

// ─── One link ────────────────────────────────────────────────────────────

function LinkRow({ link, onChanged }: { link: PaymentLink; onChanged: () => void }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const copy = useCopy();
  const [busy, setBusy] = useState(false);
  const [proving, setProving] = useState(false);
  const upload = useUpload(slug, "inquiries.payment_evidence");
  const cancellable = link.status === "created" || link.status === "sent";
  const manualOpen = link.provider === "manual" && cancellable;

  async function cancel() {
    if (!window.confirm("Cancel this payment link? The guest will no longer be able to pay with it.")) return;
    setBusy(true);
    try {
      await api.post(inqPath(slug, `/payment-links/${link.id}/cancel`));
      toast.success("Link cancelled");
      onChanged();
    } catch (err) {
      toast.error("Not cancelled", errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function markPaid(files: File[]) {
    const ref = await upload.upload(files[0]);
    if (!ref) return;
    setBusy(true);
    try {
      await api.post(inqPath(slug, `/payment-links/${link.id}/mark-paid`), { evidence_file_id: ref.id });
      toast.success("Marked as paid");
      setProving(false);
      onChanged();
    } catch (err) {
      toast.error("Not marked paid", errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inq-note" style={{ display: "grid", gap: 8 }}>
      <div className="inq-row inq-row--between">
        <span className="inq-row">
          <strong>{PURPOSE_TEXT[link.purpose]}</strong>
          <MoneyText amount={link.amount_value} currency={link.currency} />
          <Badge tone={LINK_STATUS_TONE[link.status]}>{link.status.replace("_", " ")}</Badge>
          <span className="inq-muted">{link.provider}</span>
        </span>
        <span className="inq-muted">
          {link.paid_at ? `Paid ${fmt.dateTime(link.paid_at, "medium")}` : link.expires_at ? `Expires ${fmt.dateTime(link.expires_at, "medium")}` : ""}
        </span>
      </div>
      {link.status === "partially_paid" && link.paid_amount_value && (
        <span className="inq-muted">Received <MoneyText amount={link.paid_amount_value} currency={link.currency} /> of the amount.</span>
      )}
      {link.fee_value && (
        <span className="inq-muted">
          Payment fee <MoneyText amount={link.fee_value} currency={link.currency} />
          {link.fee_source === "settings" ? " (from your fee settings)" : ""}
        </span>
      )}
      <div className="inq-row">
        {link.url && cancellable && <Button size="sm" onClick={() => copy(link.url!, "Payment link copied")}>Copy link</Button>}
        {cancellable && can("inquiries.quote") && <Button size="sm" variant="ghost" loading={busy && !proving} onClick={cancel}>Cancel link</Button>}
        {manualOpen && can("inquiries.approve") && !proving && (
          <Button size="sm" onClick={() => setProving(true)}>Mark as paid</Button>
        )}
      </div>
      {proving && (
        <FileDrop onFiles={markPaid} accept="image/*,application/pdf" disabled={busy || upload.uploading}
          title="Attach proof of payment" hint="A bank transfer receipt or the POS slip (image or PDF)." />
      )}
      {upload.error && <span className="inq-muted" role="alert">{upload.error.message}</span>}
    </div>
  );
}
