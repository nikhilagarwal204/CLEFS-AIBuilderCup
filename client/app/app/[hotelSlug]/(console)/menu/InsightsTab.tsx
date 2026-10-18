"use client";

/* Upsell funnel: suggestions shown, accepted (ordered), paid, fulfilled and
   refunded, over hotel-local days. The per-item table is the useful part:
   it tells the owner which pairing to keep and which to drop. Values are
   integer minor units from the server, formatted with the hotel currency. */

import { useState } from "react";
import { hotelPath, useApi, withQuery } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import {
  Button, Card, DateRange, EmptyState, StatTile, Table, lastNDays, type Column, type DateRangeValue,
} from "../../../../ui";
import type { Funnel, FunnelState, MenuData } from "./types";

type ItemRow = Funnel["items"][number];

const STATES: { key: FunnelState; label: string; sub: string }[] = [
  { key: "shown", label: "Suggested", sub: "Pairings shown to guests" },
  { key: "accepted", label: "Accepted", sub: "Added to an order" },
  { key: "paid", label: "Paid", sub: "Payment recorded" },
  { key: "fulfilled", label: "Delivered", sub: "Order delivered" },
  { key: "refunded", label: "Refunded", sub: "Refunded after delivery" },
];

export default function InsightsTab({ data }: { data: MenuData }) {
  const fmt = useFormat();
  const [range, setRange] = useState<DateRangeValue>(() => lastNDays(30, fmt.tz));
  const path = withQuery(hotelPath(data.slug, "/menu/funnel"), { start: range.start, end: range.end });
  const res = useApi<Funnel>(path, [path]);
  const f = res.data;

  const columns: Column<ItemRow>[] = [
    { key: "name", header: "Suggested item", render: r => <span className="ups-item-name">{r.name}</span> },
    { key: "shown", header: "Shown", align: "right", render: r => fmt.number(r.shown) },
    { key: "accepted", header: "Accepted", align: "right", render: r => fmt.number(r.accepted) },
    {
      key: "rate", header: "Accept rate", align: "right",
      render: r => (r.accept_rate == null ? <span className="cx-faint">n/a</span> : fmt.pct(r.accept_rate / 100)),
    },
    {
      key: "value", header: "Accepted value", align: "right",
      render: r => <span className="ups-price">{fmt.money(r.accepted_value_minor, { minor: true, currency: f?.currency })}</span>,
    },
    { key: "refunded", header: "Refunded", align: "right", hideOnMobile: true, render: r => fmt.number(r.refunded) },
  ];

  return (
    <div className="cx-stack">
      <div className="cx-row">
        <DateRange value={range} onChange={setRange} presets={[7, 30, 90]} />
      </div>
      {res.error && !f ? (
        <Card>
          <EmptyState title="Could not load the funnel" body={res.error.message} action={<Button onClick={res.reload}>Try again</Button>} />
        </Card>
      ) : (
        <>
          <div className="ups-funnel">
            {STATES.map(s => (
              <StatTile
                key={s.key}
                label={s.label}
                loading={res.loading && !f}
                value={f ? fmt.number(f.states[s.key]?.count ?? 0) : ""}
                sub={f && s.key !== "shown"
                  ? fmt.money(f.states[s.key]?.value_minor ?? 0, { minor: true, currency: f.currency })
                  : s.sub}
              />
            ))}
          </div>
          <Card
            title="By suggested item"
            subtitle={f?.accept_rate != null ? `Overall accept rate ${fmt.pct(f.accept_rate / 100)}.` : "How often each suggestion turns into an order."}
          >
            <Table
              columns={columns}
              rows={f?.items ?? []}
              rowKey={r => r.item_id}
              loading={res.loading && !f}
              empty={<EmptyState title="No suggestions shown in this period"
                                 body="Add pairings in the Pairings tab. Guests see them on the room menu and from the concierge." />}
            />
          </Card>
        </>
      )}
    </div>
  );
}
