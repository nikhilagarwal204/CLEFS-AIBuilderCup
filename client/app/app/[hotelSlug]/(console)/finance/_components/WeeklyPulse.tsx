"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Weekly pulse (04 spec 6.1): needs no P&L, so revenue managers and a
   hotel that never uploads one still get it. Tiles for the last complete
   ISO week against the week before, a 13-week NRevPAR strip, and OTA cost
   per occupied room split by channel. Any ratio whose denominator is 0 or
   missing reads "n/a", and the strip skips weeks with no available rooms.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo } from "react";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import {
  Badge, Card, EmptyState, LineChart, Sparkline, StatTile, Table, type Column,
} from "../../../../../ui";
import { changeDelta, channelLabel, dayFromEpoch, finApi, num, plottableWeeks } from "../lib";
import type { ChannelCost, Pulse, WeeklyOut } from "../types";
import { errText, Money } from "./bits";

export default function WeeklyPulse({ slug }: { slug: string }) {
  const fmt = useFormat();
  const res = useApi<WeeklyOut>(finApi(slug, "/weekly"), [slug]);
  const weeks = useMemo(() => res.data?.weeks ?? [], [res.data]);
  const latest = res.data?.latest ?? null;
  const prev = weeks.length > 1 ? weeks[weeks.length - 2] : null;
  const ccy = res.data?.currency;
  const plot = useMemo(() => plottableWeeks(weeks), [weeks]);
  const spark = (k: keyof Pulse) => plot.map(w => num(w[k] as string | null));
  const weekLabel = latest ? `Week of ${fmt.date(latest.week_start, "medium")}` : "Last complete week";

  if (res.error) {
    return (
      <Card title="Weekly pulse">
        <EmptyState title="Could not load the weekly pulse" body={errText(res.error)} />
      </Card>
    );
  }

  return (
    <>
      <div className="fin-row">
        <div className="fin-muted">
          {weekLabel}. From your PMS nights and Profit Guard channel costs; no P&L needed.
          {latest?.sc_as_revenue === false && " Service charge is treated as a staff pass-through (Profit Guard setting)."}
        </div>
      </div>
      <div className="fin-tiles">
        <StatTile
          loading={res.loading}
          label="NRevPAR"
          value={<Money value={latest?.nrevpar} currency={ccy} />}
          sub="Rooms revenue after distribution cost, per available room"
          delta={changeDelta(latest?.nrevpar, prev?.nrevpar, true, "vs prior week")}
          sparkline={<Sparkline values={spark("nrevpar")} ariaLabel="NRevPAR, 13 weeks" />}
        />
        <StatTile
          loading={res.loading}
          label="RevPAR"
          value={<Money value={latest?.revpar} currency={ccy} />}
          delta={changeDelta(latest?.revpar, prev?.revpar, true, "vs prior week")}
          sparkline={<Sparkline values={spark("revpar")} ariaLabel="RevPAR, 13 weeks" />}
        />
        <StatTile
          loading={res.loading}
          label="OTA cost per occupied room"
          value={<Money value={latest?.ota_cost_por} currency={ccy} />}
          sub="Commission and fees across every OTA"
          delta={changeDelta(latest?.ota_cost_por, prev?.ota_cost_por, false, "vs prior week")}
        />
        <StatTile
          loading={res.loading}
          label="Cancellation leakage"
          value={<Money value={latest?.cancellation_leakage} currency={ccy} />}
          sub={<>Recovery cost <Money value={latest?.recovery_cost} currency={ccy} /></>}
          delta={changeDelta(latest?.cancellation_leakage, prev?.cancellation_leakage, false, "vs prior week")}
        />
      </div>
      <NRevparStrip weeks={plot} loading={res.loading} currency={ccy} />
      <ChannelTable pulse={latest} loading={res.loading} currency={ccy} />
    </>
  );
}

// ─── 13-week NRevPAR strip ───────────────────────────────────────────────

function NRevparStrip({ weeks, loading, currency }: { weeks: Pulse[]; loading: boolean; currency?: string }) {
  const fmt = useFormat();
  const series = [
    { key: "nrevpar", label: "NRevPAR", points: weeks.map(w => ({ x: w.week_start, y: num(w.nrevpar) })) },
    { key: "revpar", label: "RevPAR", dashed: true, points: weeks.map(w => ({ x: w.week_start, y: num(w.revpar) })) },
  ];
  return (
    <Card title="NRevPAR, last 13 weeks" subtitle="The gap to RevPAR is what distribution costs you per available room.">
      {loading ? null : (
        <LineChart
          series={series}
          height={220}
          ariaLabel="NRevPAR and RevPAR by week"
          yFormat={v => fmt.money(v, { currency, compact: true })}
          xFormat={dayFromEpoch}
          emptyNote="No week with available rooms yet. The pulse fills in once occupancy arrives from the PMS."
        />
      )}
    </Card>
  );
}

// ─── OTA cost by channel ─────────────────────────────────────────────────

function ChannelTable({ pulse, loading, currency }: { pulse: Pulse | null; loading: boolean; currency?: string }) {
  const rows = [...(pulse?.channels ?? [])].sort((a, b) => (b.cost_minor ?? 0) - (a.cost_minor ?? 0));
  const cols: Column<ChannelCost>[] = [
    {
      key: "channel", header: "Channel",
      render: c => (
        <span className="fin-tags">
          {channelLabel(c.channel_code)}
          {c.is_ota ? <Badge tone="gold">OTA</Badge> : <Badge>Direct</Badge>}
        </span>
      ),
    },
    { key: "rn", header: "Room nights", align: "right", render: c => <span className="fin-num">{c.room_nights ?? "n/a"}</span> },
    { key: "cost", header: "Cost", align: "right", render: c => <Money value={c.cost} currency={currency} /> },
    { key: "per", header: "Per room night", align: "right", render: c => <Money value={c.cost_per_rn} currency={currency} /> },
  ];
  return (
    <Card
      title="Distribution cost by channel"
      subtitle={pulse ? <>OTA <Money value={pulse.ota_cost} currency={currency} />, direct <Money value={pulse.direct_cost} currency={currency} />, occupied room nights {pulse.occupied_rn ?? "n/a"}</> : undefined}
    >
      <Table
        columns={cols}
        rows={rows}
        rowKey={c => c.channel_code}
        loading={loading}
        caption="Distribution cost per channel for the last complete week"
        empty="No channel costs for this week. They come from Profit Guard once OTA statements or reservations with a channel arrive."
      />
    </Card>
  );
}
