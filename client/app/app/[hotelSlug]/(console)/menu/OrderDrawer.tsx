"use client";

/* One order: its lines at the prices quoted to the guest (snapshots, so a
   later menu edit never rewrites history), the fee split, status moves the
   server allows from here, and the payment record. Cancel and refund ask
   for a reason because the owner reads them later in the funnel. */

import { useState } from "react";
import { api, hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { Badge, Button, Drawer, EmptyState, Field, Input, Select, Skeleton, useToast } from "../../../../ui";
import { NEXT_STATUS, PAYMENTS, STATUS_LABEL, STATUS_TONE, errText, paymentLabel } from "./lib";
import type { MenuData, Order, OrderStatus, Payment } from "./types";

export default function OrderDrawer({ data, orderId, onClose, onChanged }: {
  data: MenuData;
  orderId: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const res = useApi<Order>(hotelPath(data.slug, `/menu/orders/${orderId}`), [orderId]);
  const o = res.data;
  const [busy, setBusy] = useState<string | null>(null);
  const [pending, setPending] = useState<OrderStatus | null>(null);
  const [reason, setReason] = useState("");
  const [payment, setPayment] = useState<Payment>("room_charge");

  const money = (s: string) => fmt.money(s, { currency: o?.currency });

  const move = async (status: OrderStatus) => {
    const needsReason = status === "cancelled" || status === "refunded";
    if (needsReason && pending !== status) { setPending(status); return; }
    setBusy(status);
    try {
      const next = await api.post<Order>(hotelPath(data.slug, `/menu/orders/${orderId}/status`), {
        status, reason: needsReason ? reason.trim() || null : null,
      });
      res.setData(next);
      setPending(null); setReason("");
      toast.success(`Order #${next.order_id} ${STATUS_LABEL[next.status].toLowerCase()}`);
      onChanged();
    } catch (err) {
      toast.error("Could not update the order", errText(err));
      res.reload();
    } finally {
      setBusy(null);
    }
  };

  const markPaid = async () => {
    setBusy("paid");
    try {
      const next = await api.post<Order>(hotelPath(data.slug, `/menu/orders/${orderId}/paid`), { payment });
      res.setData(next);
      toast.success("Payment recorded");
      onChanged();
    } catch (err) {
      toast.error("Could not record the payment", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const nexts = o ? NEXT_STATUS[o.status] : [];
  const canPay = o && !o.paid_at && o.status !== "cancelled" && o.status !== "refunded";

  return (
    <Drawer open onClose={onClose} title={o ? `Order #${o.order_id}, room ${o.room}` : "Order"}>
      {res.error && !o ? (
        <EmptyState title="Could not load the order" body={res.error.message} action={<Button onClick={res.reload}>Try again</Button>} />
      ) : !o ? (
        <Skeleton lines={6} height={16} />
      ) : (
        <div className="cx-stack">
          <div className="cx-row">
            <Badge tone={STATUS_TONE[o.status]} dot>{STATUS_LABEL[o.status]}</Badge>
            {!o.verified_room && <Badge tone="warn">Room not verified: confirm at the door</Badge>}
            {o.price_display === "plus_plus" && <Badge tone="neutral">Prices plus fees</Badge>}
          </div>
          <ul className="ups-lines">
            {o.lines.map(l => (
              <li key={l.id}>
                <span>
                  {l.qty}x {l.name}
                  {l.upsell && <>{" "}<Badge tone="gold" title="Added from a pairing suggestion">Suggested</Badge></>}
                  {l.notes && <span className="ups-sub"> ({l.notes})</span>}
                </span>
                <span className="ups-price">{money(l.line_total)}</span>
              </li>
            ))}
          </ul>
          <div className="ups-totals">
            <div><span>Subtotal</span><span>{money(o.subtotal)}</span></div>
            <div><span>Service charge</span><span>{money(o.service_charge)}</span></div>
            <div><span>Municipality fee</span><span>{money(o.municipality_fee)}</span></div>
            <div><span>VAT</span><span>{money(o.vat)}</span></div>
            <div className="ups-grand"><span>Total{o.price_display === "inclusive" ? " (fees included)" : ""}</span><span>{money(o.total)}</span></div>
          </div>
          <dl className="ups-kv">
            <dt>Placed</dt><dd>{fmt.dateTime(o.created_at, "medium")}</dd>
            <dt>Source</dt><dd>{o.source}{o.language ? ` · ${o.language.toUpperCase()}` : ""}</dd>
            {o.status_changed_at && <><dt>Last change</dt><dd>{fmt.relative(o.status_changed_at)}</dd></>}
            <dt>Payment</dt><dd>{o.paid_at ? `${paymentLabel(o.payment)}, ${fmt.dateTime(o.paid_at)}` : "Not recorded"}</dd>
            {o.request_id != null && <><dt>Service request</dt><dd>#{o.request_id}</dd></>}
            {o.notes && <><dt>Guest notes</dt><dd>{o.notes}</dd></>}
            {o.cancel_reason && <><dt>Reason</dt><dd>{o.cancel_reason}</dd></>}
          </dl>
          {data.canOrders && nexts.length > 0 && (
            <div className="cx-stack" style={{ gap: 8 }}>
              <span className="cx-label">Move to</span>
              <div className="ups-actions">
                {nexts.map(s => (
                  <Button key={s} size="sm" variant={s === "cancelled" || s === "refunded" ? "danger" : "secondary"}
                          loading={busy === s} disabled={busy !== null} onClick={() => move(s)}>
                    {STATUS_LABEL[s]}
                  </Button>
                ))}
              </div>
              {pending && (
                <div className="cx-stack" style={{ gap: 8 }}>
                  <Field label={`Reason for ${pending === "cancelled" ? "cancelling" : "refunding"}`} hint="Optional, kept with the order.">
                    <Input value={reason} maxLength={300} onChange={e => setReason(e.target.value)} />
                  </Field>
                  <div className="ups-actions">
                    <Button size="sm" onClick={() => { setPending(null); setReason(""); }}>Keep order</Button>
                    <Button size="sm" variant="danger" loading={busy === pending} onClick={() => move(pending)}>
                      Confirm {STATUS_LABEL[pending].toLowerCase()}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}
          {data.canOrders && canPay && (
            <div className="cx-row" style={{ alignItems: "flex-end" }}>
              <Field label="Record payment">
                <Select value={payment} onChange={e => setPayment(e.target.value as Payment)} options={PAYMENTS} />
              </Field>
              <Button onClick={markPaid} loading={busy === "paid"} disabled={busy !== null}>Mark paid</Button>
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}
