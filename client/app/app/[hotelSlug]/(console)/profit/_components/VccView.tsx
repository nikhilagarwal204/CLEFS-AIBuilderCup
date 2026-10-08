"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Virtual cards (02 spec 3.3 L8/L9, 3.10 vcc/): money the OTA has already
   paid onto a card that the hotel has not charged yet, soonest deadline
   first. The deadline is the earlier of the card's expiry and the
   contract rule (Booking 365 days after check-out, Expedia 180 days,
   Agoda UPC 24 hours), with the 30/14/7/1-day tiers the alerts use.
   "Mark charged" records the charge after it is run on the terminal.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { api, useApi, withQuery } from "../../../../../lib/api";
import { currencyDigits, useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, Field, Input, Modal, Segmented, StatTile, Table, useToast, type Column } from "../../../../../ui";
import {
  CARD_STATUS_LABEL, cardTier, cardTierTone, CHARGEABLE_CARD, majorToMinor, minorToDecimal, otaLabel, pgApi,
} from "../lib";
import type { VirtualCard } from "../types";
import { ErrorNote, errorText, Money, useCanManage } from "./bits";

function DaysLeft({ days }: { days: number }) {
  const tier = cardTier(days);
  const text = days < 0 ? `Expired ${-days} d ago` : days === 0 ? "Last day today" : `${days} d left`;
  return <Badge tone={cardTierTone(days)} dot title={tier ? `Inside the ${tier}-day alert tier` : undefined}>{text}</Badge>;
}

export default function VccView() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const canManage = useCanManage();
  const [view, setView] = useState<"open" | "all">("open");
  const q = useApi<VirtualCard[]>(withQuery(pgApi(slug, "/vcc"), { open: view === "open" ? true : undefined }));
  const [card, setCard] = useState<VirtualCard | null>(null);
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);

  const rows = useMemo(() => [...(q.data ?? [])].sort((a, b) => a.charge_deadline.localeCompare(b.charge_deadline)), [q.data]);
  const chargeable = rows.filter(r => CHARGEABLE_CARD.includes(r.status) && r.current_balance_minor > 0 && r.days_left >= 0);
  const byCcy = (list: VirtualCard[]) => {
    const m = new Map<string, number>();
    for (const r of list) m.set(r.currency, (m.get(r.currency) ?? 0) + r.current_balance_minor);
    return [...m.entries()];
  };
  const within = (d: number) => byCcy(chargeable.filter(r => r.days_left <= d));
  const moneyList = (list: [string, number][]) =>
    list.length ? list.map(([c, v]) => fmt.money(v, { minor: true, currency: c, compact: true })).join(" + ") : fmt.money(0, { currency: fmt.currency });

  const digits = currencyDigits(card?.currency ?? fmt.currency);
  const amountMinor = majorToMinor(amount, digits);

  async function charge() {
    if (!card || amountMinor === null || amountMinor <= 0) return;
    setBusy(true);
    try {
      const full = amountMinor >= card.current_balance_minor;
      await api.post(pgApi(slug, `/vcc/${card.id}/charged`), full ? {} : { amount: minorToDecimal(amountMinor, digits) });
      toast({ tone: "ok", title: full ? "Card marked as charged" : "Partial charge recorded" });
      setCard(null);
      q.reload();
    } catch (err) {
      toast({ tone: "bad", title: "Could not record the charge", body: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<VirtualCard>[] = [
    {
      key: "card", header: "Card",
      render: r => (
        <div>
          <div className="pg-strong">{otaLabel(r.ota_code)} {r.vcc_id}</div>
          <div className="pg-faint">{r.ota_res_id ? `Booking ${r.ota_res_id}` : "No booking matched"}</div>
        </div>
      ),
    },
    { key: "bal", header: "Balance", align: "right", render: r => <Money minor={r.current_balance_minor} currency={r.currency} /> },
    {
      key: "deadline", header: "Charge by",
      render: r => (
        <div>
          <div>{fmt.date(r.charge_deadline, "medium")}</div>
          {r.deadline_basis && <div className="pg-faint">{r.deadline_basis.replace(/_/g, " ").toLowerCase()}</div>}
        </div>
      ),
    },
    { key: "left", header: "Countdown", render: r => (r.current_balance_minor > 0 ? <DaysLeft days={r.days_left} /> : <span className="pg-faint">n/a</span>) },
    { key: "status", header: "Status", hideOnMobile: true, render: r => CARD_STATUS_LABEL[r.status] ?? r.status },
    {
      key: "act", header: "", align: "right",
      render: r => (canManage && CHARGEABLE_CARD.includes(r.status) && r.current_balance_minor > 0 ? (
        <Button size="sm" variant={r.days_left <= 7 ? "primary" : "secondary"}
                onClick={() => { setCard(r); setAmount(minorToDecimal(r.current_balance_minor, currencyDigits(r.currency))); }}>
          Mark charged
        </Button>
      ) : null),
    },
  ];

  return (
    <>
      <div className="pg-tiles">
        <StatTile label="Ready to charge" loading={q.loading} value={moneyList(byCcy(chargeable))}
                  sub={`${chargeable.length} card${chargeable.length === 1 ? "" : "s"}`} />
        <StatTile label="Due within 7 days" loading={q.loading} value={moneyList(within(7))} />
        <StatTile label="Due within 30 days" loading={q.loading} value={moneyList(within(30))} />
        <StatTile label="Expired with balance" loading={q.loading}
                  value={moneyList(byCcy(rows.filter(r => r.days_left < 0 && r.current_balance_minor > 0 && r.status !== "CANCELLED")))}
                  sub="Only OTA support can recover these" />
      </div>
      <div className="pg-between">
        <Segmented ariaLabel="Cards shown" value={view} onChange={v => setView(v as "open" | "all")}
                   options={[{ value: "open", label: "With a balance" }, { value: "all", label: "All cards" }]} />
        <span className="pg-faint">Import the OTA&apos;s virtual card report on Imports to refresh balances.</span>
      </div>
      <ErrorNote error={q.error} />
      <Card padded={false}>
        <Table columns={columns} rows={rows} rowKey={r => r.id} loading={q.loading} caption="Virtual cards by charge deadline"
               empty="No virtual cards with a balance. Nothing is waiting to be charged." />
      </Card>

      <Modal open={card !== null} onClose={() => setCard(null)} dismissible={!busy} size="sm"
             title={card ? `Mark ${otaLabel(card.ota_code)} card ${card.vcc_id} charged` : "Mark charged"}
             footer={<>
               <Button variant="ghost" onClick={() => setCard(null)} disabled={busy}>Cancel</Button>
               <Button variant="primary" loading={busy} disabled={amountMinor === null || amountMinor <= 0} onClick={charge}>Save</Button>
             </>}>
        {card && (
          <div className="cx-stack">
            <p className="pg-muted" style={{ margin: 0 }}>
              Run the charge on your card terminal first. Balance on the card: <Money minor={card.current_balance_minor} currency={card.currency} />.
            </p>
            <Field label={`Amount charged (${card.currency})`}
                   hint="The full balance marks the card charged; less records a partial charge."
                   error={amount && (amountMinor === null || amountMinor <= 0) ? "Enter an amount like 450.00" : undefined}>
              <Input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} />
            </Field>
          </div>
        )}
      </Modal>
    </>
  );
}
