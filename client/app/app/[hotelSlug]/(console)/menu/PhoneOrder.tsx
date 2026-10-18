"use client";

/* Phone order to the desk. Same two steps as a guest: price the basket
   (a quote the server re-prices from the DB and binds to this staff member
   for 10 minutes), then confirm that exact quote. Any basket change drops
   the quote so the confirmed total is always the one on screen. */

import { useMemo, useState } from "react";
import { ApiError, api, hotelPath } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { Badge, Button, Drawer, Field, Input, Select, Textarea, useToast } from "../../../../ui";
import { PAYMENTS, errText } from "./lib";
import type { MenuData, Order, Payment, Quote } from "./types";

interface Line { item_id: string; qty: number; notes: string }

export default function PhoneOrder({ data, onClose, onPlaced }: {
  data: MenuData;
  onClose: () => void;
  onPlaced: (o: Order) => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const [room, setRoom] = useState("");
  const [lines, setLines] = useState<Line[]>([{ item_id: "", qty: 1, notes: "" }]);
  const [notes, setNotes] = useState("");
  const [payment, setPayment] = useState<Payment>("room_charge");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [busy, setBusy] = useState<"quote" | "order" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const orderable = useMemo(() => {
    const outlets = new Map(data.outlets.map(o => [o.id, o]));
    return data.items
      .filter(i => i.active && outlets.get(i.outlet_id)?.active)
      .sort((a, b) => a.name_en.localeCompare(b.name_en))
      .map(i => ({ value: String(i.id), label: `${i.name_en} · ${fmt.money(i.price_minor, { minor: true, currency: i.currency })}` }));
  }, [data.items, data.outlets, fmt]);

  const edit = (next: Line[]) => { setLines(next); setQuote(null); setError(null); };
  const setLine = (i: number, patch: Partial<Line>) => edit(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const filled = lines.filter(l => l.item_id && l.qty > 0);
  const ready = room.trim() !== "" && filled.length > 0;

  const price = async () => {
    if (!ready) return;
    setBusy("quote"); setError(null);
    try {
      const q = await api.post<Quote>(hotelPath(data.slug, "/menu/quote"), {
        room: room.trim(),
        lines: filled.map(l => ({ item_id: Number(l.item_id), qty: l.qty, notes: l.notes.trim() || undefined })),
      });
      setQuote(q);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(null);
    }
  };

  const confirm = async () => {
    if (!quote) return;
    setBusy("order"); setError(null);
    try {
      const o = await api.post<Order>(hotelPath(data.slug, "/menu/orders"), {
        quote_id: quote.quote_id, room: room.trim(), notes: notes.trim() || null, payment,
      });
      toast.success(`Order #${o.order_id} placed`, "Sent to the Service queue as a Food request.");
      onPlaced(o);
    } catch (err) {
      // An expired or stale quote is not a dead end: drop it and ask for a fresh price.
      if (err instanceof ApiError && err.code?.startsWith("quote_")) setQuote(null);
      setError(errText(err));
    } finally {
      setBusy(null);
    }
  };

  const addSuggestion = (itemId: number) => {
    if (lines.some(l => l.item_id === String(itemId))) return;
    edit([...lines.filter(l => l.item_id), { item_id: String(itemId), qty: 1, notes: "" }]);
  };

  const money = (s: string) => fmt.money(s, { currency: quote?.currency });

  return (
    <Drawer
      open
      onClose={onClose}
      title="New phone order"
      footer={
        <div className="ups-actions" style={{ width: "100%" }}>
          <span className="ups-spacer" />
          <Button onClick={onClose} disabled={busy !== null}>Cancel</Button>
          {quote ? (
            <Button variant="primary" onClick={confirm} loading={busy === "order"}>Place order · {money(quote.total)}</Button>
          ) : (
            <Button variant="primary" onClick={price} loading={busy === "quote"} disabled={!ready}>Get price</Button>
          )}
        </div>
      }
    >
      <div className="cx-stack">
        <Field label="Room" required>
          <Input value={room} onChange={e => { setRoom(e.target.value); setQuote(null); }} inputMode="numeric" maxLength={12} style={{ maxWidth: 140 }} />
        </Field>
        <div className="cx-field" role="group" aria-labelledby="ups-po-items">
          <span className="cx-label" id="ups-po-items">Items</span>
          <div className="ups-basket">
            {lines.map((l, i) => (
              <div key={i} className="cx-stack" style={{ gap: 6 }}>
                <div className="ups-basket__row">
                  <Select aria-label="Item" value={l.item_id} placeholder="Choose an item" options={orderable}
                          onChange={e => setLine(i, { item_id: e.target.value })} />
                  <Input aria-label="Quantity" type="number" min={1} max={20} inputMode="numeric" value={l.qty}
                         onChange={e => setLine(i, { qty: Math.max(1, Math.min(20, Number(e.target.value) || 1)) })} />
                  <Button size="sm" variant="ghost" aria-label="Remove line" disabled={lines.length === 1}
                          onClick={() => edit(lines.filter((_, j) => j !== i))}>x</Button>
                </div>
                {l.item_id && (
                  <Input aria-label="Line notes" placeholder="Notes for the kitchen (optional)" value={l.notes} maxLength={200}
                         onChange={e => setLine(i, { notes: e.target.value })} />
                )}
              </div>
            ))}
          </div>
          <div><Button size="sm" onClick={() => edit([...lines, { item_id: "", qty: 1, notes: "" }])}>Add another item</Button></div>
        </div>
        {quote && <QuoteSummary quote={quote} money={money} onAdd={addSuggestion} />}
        {quote && (
          <div className="ups-form ups-form--2">
            <Field label="Payment">
              <Select value={payment} onChange={e => setPayment(e.target.value as Payment)} options={PAYMENTS} />
            </Field>
            <Field label="Order notes" hint="Delivery or allergy notes for staff.">
              <Textarea rows={2} value={notes} maxLength={500} onChange={e => setNotes(e.target.value)} />
            </Field>
          </div>
        )}
        {error && <div className="cx-error-text" role="alert">{error}</div>}
      </div>
    </Drawer>
  );
}

// ─── Quote summary ───────────────────────────────────────────────────────

function QuoteSummary({ quote, money, onAdd }: { quote: Quote; money: (s: string) => string; onAdd: (id: number) => void }) {
  const fmt = useFormat();
  return (
    <div className="cx-stack" style={{ gap: 10 }}>
      <ul className="ups-lines">
        {quote.lines.map(l => (
          <li key={l.item_id}><span>{l.qty}x {l.name}</span><span className="ups-price">{money(l.line_total)}</span></li>
        ))}
      </ul>
      <div className="ups-totals">
        {quote.taxes.map(t => (
          <div key={t.code}><span>{t.label}{t.included ? " (included)" : ""}</span><span>{money(t.amount)}</span></div>
        ))}
        <div className="ups-grand"><span>Total</span><span>{money(quote.total)}</span></div>
        <div className="ups-sub">Price held until {fmt.time(quote.expires_at)}.</div>
      </div>
      {quote.suggestions.length > 0 && (
        <div className="cx-stack" style={{ gap: 6 }}>
          <span className="cx-label">Suggest to the guest</span>
          {quote.suggestions.map(s => (
            <div key={`${s.for_item_id}-${s.item_id}`} className="cx-row">
              <Badge tone={s.kind === "upgrade" ? "gold" : "info"}>{s.kind === "upgrade" ? "Upgrade" : "Pairing"}</Badge>
              <span>{s.name} · {fmt.money(s.price, { currency: s.currency })}</span>
              {s.reason && <span className="ups-sub">{s.reason}</span>}
              <Button size="sm" variant="ghost" onClick={() => onAdd(s.item_id)}>Add</Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
