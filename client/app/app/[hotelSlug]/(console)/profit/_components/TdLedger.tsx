"use client";

/* Tourism Dirham ledger by month (02 spec 3.8 tax): what the stays made
   the hotel liable for, what the folio actually collected, what was
   remitted to DET and when it is due. A remittance is recorded here
   (REMITTED within AED 1, else MISMATCH, which the T7 check flags). The
   CSV is an internal reconciliation file, not a DET upload format. */

import { useState } from "react";
import { LuDownload } from "react-icons/lu";
import { API_PREFIX, api } from "../../../../../lib/api";
import { currencyDigits, useFormat } from "../../../../../lib/format";
import { Badge, Button, Field, Input, Modal, Table, useToast, type Column, type Tone } from "../../../../../ui";
import { majorToMinor, minorToDecimal, monthLabel, pgApi } from "../lib";
import type { TdLedger as Ledger, TdMonth } from "../types";
import { errorText, Money } from "./bits";

const STATUS_TONE: Record<string, Tone> = { OPEN: "gold", REMITTED: "ok", MISMATCH: "bad" };
const STATUS_TEXT: Record<string, string> = { OPEN: "Not remitted", REMITTED: "Remitted", MISMATCH: "Mismatch" };

export function TdLedger({
  slug, ledger, canManage, onChange,
}: {
  slug: string;
  ledger: Ledger;
  canManage: boolean;
  onChange: () => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const ccy = ledger.currency;
  const digits = currencyDigits(ccy);
  const [editing, setEditing] = useState<TdMonth | null>(null);
  const [amount, setAmount] = useState("");
  const [on, setOn] = useState("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const amountMinor = majorToMinor(amount, digits);

  function openRemit(m: TdMonth) {
    setEditing(m);
    setAmount(minorToDecimal(m.td_remitted_minor || m.td_collected_minor || 0, digits));
    setOn(m.remitted_on ?? fmt.dayKey(new Date()));
    setRef(m.remit_reference ?? "");
  }

  async function save() {
    if (!editing || amountMinor === null || !on) return;
    setBusy(true);
    try {
      await api.put(pgApi(slug, `/tax/td-ledger/${editing.month}`), {
        amount: minorToDecimal(amountMinor, digits), remitted_on: on, reference: ref.trim() || undefined,
      });
      toast({ tone: "ok", title: `Remittance for ${monthLabel(editing.month)} recorded` });
      setEditing(null);
      onChange();
    } catch (err) {
      toast({ tone: "bad", title: "Could not record the remittance", body: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<TdMonth>[] = [
    { key: "month", header: "Month", render: m => <span className="pg-strong">{monthLabel(m.month)}</span> },
    { key: "rn", header: "Liable nights", align: "right", hideOnMobile: true, render: m => fmt.number(m.liable_room_nights ?? null) },
    { key: "exp", header: "Expected", align: "right", hideOnMobile: true, render: m => <Money minor={m.td_expected_minor} currency={ccy} /> },
    { key: "col", header: "Collected", align: "right", render: m => <Money minor={m.td_collected_minor} currency={ccy} /> },
    { key: "rem", header: "Remitted", align: "right", render: m => <Money minor={m.td_remitted_minor} currency={ccy} /> },
    {
      key: "gap", header: "Gap", align: "right",
      render: m => (m.gap_minor ? <Money minor={m.gap_minor} currency={ccy} signed tone="auto" /> : <span className="pg-faint">None</span>),
    },
    { key: "due", header: "Due", render: m => (m.due_date ? fmt.date(m.due_date) : <span className="pg-faint">n/a</span>) },
    { key: "status", header: "Status", render: m => <Badge tone={STATUS_TONE[m.status] ?? "neutral"} dot>{STATUS_TEXT[m.status] ?? m.status}</Badge> },
    {
      key: "act", header: "", align: "right",
      render: m => (
        <span className="pg-row" style={{ justifyContent: "flex-end", gap: 6 }}>
          <a className="cx-btn cx-btn--ghost cx-btn--sm" aria-label={`Download the ${monthLabel(m.month)} reconciliation CSV`}
             href={`${API_PREFIX}${pgApi(slug, `/tax/td-export.csv?month=${m.month}`)}`}>
            <LuDownload aria-hidden="true" /> CSV
          </a>
          {canManage && <Button size="sm" onClick={() => openRemit(m)}>{m.td_remitted_minor ? "Edit" : "Record"}</Button>}
        </span>
      ),
    },
  ];

  return (
    <>
      <Table columns={columns} rows={ledger.months} rowKey={m => m.month} caption="Tourism Dirham ledger"
             empty="No Tourism Dirham months yet. They appear once PMS folios are imported." />
      <Modal open={editing !== null} onClose={() => setEditing(null)} dismissible={!busy} size="sm"
             title={editing ? `Remittance for ${monthLabel(editing.month)}` : "Remittance"}
             footer={<>
               <Button variant="ghost" onClick={() => setEditing(null)} disabled={busy}>Cancel</Button>
               <Button variant="primary" loading={busy} disabled={amountMinor === null || !on} onClick={save}>Save</Button>
             </>}>
        <div className="cx-stack">
          {editing && (
            <p className="pg-muted" style={{ margin: 0 }}>
              Collected in the folio: <Money minor={editing.td_collected_minor} currency={ccy} />. A remittance within AED 1 of it
              closes the month.
            </p>
          )}
          <Field label={`Amount remitted (${ccy})`} error={amount && amountMinor === null ? "Enter an amount like 1,250.00" : undefined}>
            <Input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} />
          </Field>
          <Field label="Remitted on"><Input type="date" value={on} onChange={e => setOn(e.target.value)} /></Field>
          <Field label="DET reference" hint="Optional."><Input value={ref} maxLength={200} onChange={e => setRef(e.target.value)} /></Field>
        </div>
      </Modal>
    </>
  );
}
