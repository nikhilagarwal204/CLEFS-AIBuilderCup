"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Journeys overview (04 spec 3.10): the step funnel (planned, sent,
   delivered, replied, skipped by reason), the OTA direct-contact capture
   rate, opt-outs, message cost and the dial level per step. With no
   approved template every step only observes, and the page says so first.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import Link from "next/link";
import { useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, EmptyState, Segmented, StatTile, Table, type Column } from "../../../../../ui";
import { dialTone, gstHref, humanize, jrnApi, MARKETING_STEPS, STEPS, stepLabel } from "../lib";
import type { FunnelRow, JourneysOverview, StepKey } from "../types";

type Days = 7 | 30 | 90;
type Row = FunnelRow & { step: StepKey };

export default function JourneysOverviewPage() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const [days, setDays] = useState<Days>(30);
  const res = useApi<JourneysOverview>(withQuery(jrnApi(slug, "/overview"), { days }), [slug, days]);
  const d = res.data;
  const loading = res.loading && !d;

  if (res.error) return <EmptyState title="Could not load journeys" body={res.error.message} />;

  const rows: Row[] = d ? STEPS.map(s => ({ ...d.funnel[s], step: s })) : [];
  const optTotal = d ? Object.values(d.optouts).reduce((a, b) => a + b, 0) : 0;

  const cols: Column<Row>[] = [
    { key: "step", header: "Step", render: r => (
      <span className="cx-row" style={{ gap: 6 }}>
        <strong>{stepLabel(r.step)}</strong>
        {MARKETING_STEPS.includes(r.step) && <Badge tone="gold">Offer</Badge>}
      </span>
    ) },
    { key: "dial", header: "Dial", render: r => {
      const chip = d?.dial[r.step];
      if (!chip) return <span className="gst-faint gst-small">Gate</span>;
      const lvl = chip.effective_level || chip.level || "off";
      return <Badge tone={dialTone(lvl)} title={chip.level !== lvl ? `Set to ${chip.level}, capped at ${lvl}` : undefined}>{humanize(lvl)}</Badge>;
    } },
    { key: "planned", header: "Planned", align: "right", render: r => <span className="gst-num">{fmt.number(r.planned)}</span> },
    { key: "observed", header: "Observed", align: "right", hideOnMobile: true, render: r => <span className="gst-num">{fmt.number(r.observed + r.proposed)}</span> },
    { key: "sent", header: "Sent", align: "right", render: r => <span className="gst-num">{fmt.number(r.sent)}</span> },
    { key: "delivered", header: "Delivered", align: "right", render: r => <span className="gst-num">{fmt.number(r.delivered)}</span> },
    { key: "replied", header: "Replied", align: "right", render: r => <span className="gst-num">{fmt.number(r.replied)}</span> },
    { key: "skipped", header: "Skipped", render: r => {
      const e = Object.entries(r.skipped).sort((a, b) => b[1] - a[1]);
      return e.length ? <span className="gst-skips">{e.map(([k, n]) => <Badge key={k}>{humanize(k)} {n}</Badge>)}</span> : <span className="gst-faint">-</span>;
    } },
    { key: "opt", header: "Opt-outs", align: "right", hideOnMobile: true, render: r => <span className="gst-num">{fmt.number(d?.optouts[r.step] ?? 0)}</span> },
  ];

  return (
    <>
      {d?.all_observing && (
        <EmptyState
          title="All steps are observing: approve templates to start sending"
          body="Each journey message needs your approval (and Meta's, for WhatsApp) before anything reaches a guest. Until then Clefs only records what it would have sent."
          action={<Button href={gstHref(slug, "/journeys/templates")}>Review templates</Button>}
        />
      )}
      <div className="cx-grid cx-grid--4">
        <StatTile label="Direct contacts from OTA stays" loading={loading}
          value={d?.capture.rate != null ? fmt.pct(d.capture.rate) : "-"}
          sub={d ? `${fmt.number(d.capture.captured)} of ${fmt.number(d.capture.ota_stays)} OTA stays gave their own contact` : undefined} />
        <StatTile label="Opt-outs" loading={loading} value={fmt.number(optTotal)} sub={`Last ${days} days, by step below`} />
        <StatTile label="Message cost" loading={loading} value={d ? fmt.money(d.cost.amount, { currency: d.cost.currency }) : "-"}
          sub="WhatsApp charges at the published rate card" />
        <StatTile label="Approved templates" loading={loading} value={d ? fmt.number(d.templates_approved) : "-"}
          sub={<Link href={gstHref(slug, "/journeys/templates")}>Review templates</Link>} />
      </div>
      <Card title="Step funnel" subtitle="Journey messages planned and sent in the window. A skipped message is one the rules held back."
        actions={<Segmented<Days> ariaLabel="Window" size="sm" value={days} onChange={setDays}
          options={[{ value: 7, label: "7 d" }, { value: 30, label: "30 d" }, { value: 90, label: "90 d" }]} />}>
        <Table columns={cols} rows={rows} rowKey={r => r.step} loading={loading} caption="Journey step funnel" empty="No journey activity yet." />
        <p className="gst-note" style={{ marginTop: 10 }}>
          Offers (pre-arrival and rebooking) go only to guests with a separate yes to offers, 09:00-18:00 hotel time.
          The feedback step shows every guest the same Google and Tripadvisor links; it never filters by rating.
        </p>
      </Card>
    </>
  );
}
