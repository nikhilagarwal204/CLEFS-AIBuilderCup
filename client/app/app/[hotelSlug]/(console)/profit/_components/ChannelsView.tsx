"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Net ADR by channel (02 spec 3.6, 3.10 channels/): what a room night on
   each channel really leaves the hotel after taxes passed through,
   commission, payment costs, FX and cancellations. A WaterfallChart walks
   one channel from Gross to Net over the chosen months; the columns show
   that channel's Net ADR month by month; the table compares channels.
   Figures come from the stored monthly rollup (hotel currency).
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { LuRefreshCw } from "react-icons/lu";
import { CHART_NEW, ColumnChart } from "../../../../charts";
import { api, useApi, withQuery } from "../../../../../lib/api";
import { currencyDigits, useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, Segmented, Table, useToast, WaterfallChart, type Column } from "../../../../../ui";
import { channelTotals, minorToNumber, monthLabel, num, otaLabel, pgApi, type ChannelTotal } from "../lib";
import type { EconomicsMonthly } from "../types";
import { DataCard, errorText, Money, useCanManage } from "./bits";

const RANGES = [
  { value: 3, label: "3 months" },
  { value: 6, label: "6 months" },
  { value: 12, label: "12 months" },
] as const;

/** YYYY-MM of `months - 1` months before the month of `dayKey`. */
function monthsBack(dayKey: string, months: number): string {
  const [y, m] = dayKey.split("-").map(Number);
  const idx = y * 12 + (m - 1) - (months - 1);
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

export default function ChannelsView() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const canManage = useCanManage();
  const [range, setRange] = useState<3 | 6 | 12>(6);
  const today = fmt.dayKey(new Date());
  const to = today.slice(0, 7);
  const from = monthsBack(today, range);
  const q = useApi<EconomicsMonthly>(withQuery(pgApi(slug, "/channel-economics/monthly"), { from, to }));
  const [pick, setPick] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ccy = q.data?.currency ?? fmt.currency;
  const digits = currencyDigits(ccy);
  const rows = useMemo(() => q.data?.rows ?? [], [q.data]);
  const totals = useMemo(() => channelTotals(rows), [rows]);
  const channel = totals.find(t => t.channel === pick) ?? totals[0] ?? null;
  const months = useMemo(() => [...new Set(rows.map(r => r.month))].sort(), [rows]);
  const money = (v: number) => fmt.money(v, { currency: ccy, compact: true });

  async function rollup() {
    setBusy(true);
    try {
      await api.post(pgApi(slug, "/channel-economics/rollup"), {});
      toast({ tone: "ok", title: "Channel figures recomputed for last and this month" });
      q.reload();
    } catch (err) {
      toast({ tone: "bad", title: "Could not recompute", body: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const adrColumns = months.map(m => {
    const r = rows.find(x => x.month === m && x.channel === channel?.channel);
    return { label: monthLabel(m), tick: monthLabel(m, true), values: [Math.round(minorToNumber(r?.net_adr_minor ?? 0, digits))] };
  });

  const columns: Column<ChannelTotal>[] = [
    { key: "ch", header: "Channel", render: t => <span className="pg-strong">{otaLabel(t.channel)}</span> },
    { key: "rn", header: "Room nights", align: "right", render: t => fmt.number(t.room_nights) },
    { key: "gadr", header: "Gross ADR", align: "right", hideOnMobile: true, render: t => <Money minor={t.gross_adr_minor} currency={ccy} /> },
    { key: "nadr", header: "Net ADR", align: "right", render: t => <span className="pg-strong"><Money minor={t.net_adr_minor} currency={ccy} /></span> },
    { key: "ret", header: "Kept", align: "right", render: t => (t.retention === null ? "n/a" : fmt.pct(t.retention, { ratio: true })) },
    {
      key: "comm", header: "Commission", align: "right", hideOnMobile: true,
      render: t => (
        <div>
          <Money minor={t.commission_minor} currency={ccy} />
          {t.room_rev_minor > 0 && <div className="pg-faint">{fmt.pct((100 * t.commission_minor) / t.room_rev_minor, { digits: 1 })} of room revenue</div>}
        </div>
      ),
    },
    {
      key: "cot", header: "On taxes", align: "right", hideOnMobile: true,
      render: t => (t.commission_on_tax_minor ? <Money minor={t.commission_on_tax_minor} currency={ccy} /> : <span className="pg-faint">None</span>),
    },
    {
      key: "prog", header: "Discounts given", align: "right", hideOnMobile: true,
      render: t => (t.program_cost_minor ? <Money minor={t.program_cost_minor} currency={ccy} /> : <span className="pg-faint">None</span>),
    },
  ];

  // Depth is a 0..1 ratio weighted by gross (economics.py); the latest month with one.
  const latestDepth = num(rows.filter(r => r.channel === channel?.channel && r.avg_discount_depth !== null).at(-1)?.avg_discount_depth);
  const cotKinds = [...new Set(rows.filter(r => r.commission_on_tax_minor > 0).map(r => r.commission_on_tax_kind))];

  return (
    <>
      <div className="pg-between">
        <Segmented ariaLabel="Months shown" options={RANGES.map(r => ({ value: r.value, label: r.label }))} value={range}
                   onChange={v => setRange(v as 3 | 6 | 12)} />
        {canManage && (
          <Button size="sm" icon={<LuRefreshCw aria-hidden="true" />} loading={busy} onClick={rollup}>Recompute</Button>
        )}
      </div>

      <DataCard title="Channels compared" subtitle={`${monthLabel(from)} to ${monthLabel(to)}, by stayed night, in ${ccy}.`}
                loading={q.loading} error={q.error} empty={!totals.length}
                emptyTitle="No channel figures yet"
                emptyBody="They are computed nightly from imported stays and OTA statements.">
        <Table columns={columns} rows={totals} rowKey={t => t.channel} onRowClick={t => setPick(t.channel)} caption="Net ADR by channel" />
        {cotKinds.length > 0 && (
          <p className="pg-faint" style={{ margin: "10px 0 0" }}>
            {cotKinds.includes("structural") && "Agoda charges commission on the tax-inclusive rate by contract; it is shown here and in negotiation, never disputed. "}
            {cotKinds.includes("fee_config") && "Booking.com commission on fees happens while the fees are not set up as charges in the extranet; the fix is on Findings (L1b)."}
          </p>
        )}
      </DataCard>

      {channel && (
        <>
          <Segmented ariaLabel="Channel" size="sm" value={channel.channel} onChange={v => setPick(String(v))}
                     options={totals.map(t => ({ value: t.channel, label: otaLabel(t.channel) }))} />
          <div className="pg-channels">
            <Card title={`${otaLabel(channel.channel)}: Gross to Net`}
                  subtitle={`${fmt.number(channel.room_nights)} room nights. Net ADR ${fmt.money(channel.net_adr_minor, { minor: true, currency: ccy })}.`}>
              <WaterfallChart ariaLabel={`Waterfall from gross to net for ${otaLabel(channel.channel)}`} format={money}
                              steps={channel.waterfall.map(s => ({ label: s.label.replace("Taxes and fees passed through", "Taxes and fees"),
                                                                   kind: s.kind, value: minorToNumber(s.value_minor, digits) }))} />
            </Card>
            <Card title={`${otaLabel(channel.channel)}: Net ADR by month`} subtitle="Per stayed room night.">
              <ColumnChart data={adrColumns} series={[{ label: "Net ADR", color: CHART_NEW }]} valueFormat={money}
                           ariaLabel={`Net ADR by month for ${otaLabel(channel.channel)}`} emptyNote="No stays in these months." />
              {latestDepth !== null && (
                <p className="pg-faint" style={{ margin: "8px 0 0" }}>
                  Average discount below the public rate, latest month: {fmt.pct(latestDepth, { ratio: true })}.
                </p>
              )}
            </Card>
          </div>
        </>
      )}
    </>
  );
}
