"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Pilots (03 spec 5.5): time-boxed trials with success criteria agreed up
   front, a baseline captured at creation, a live scorecard and a printable
   ROI report. The list is the index; everything else is on the pilot page
   (/trust/pilots/{id}) so a pilot has one shareable URL.
   ═══════════════════════════════════════════════════════════════════════ */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Table, type Column } from "../../../../../ui";
import { dayLabel, moduleLabel, PILOT_STATUS, ratePct, trustApi, trustHref } from "../lib";
import type { Pilot } from "../types";
import PilotForm from "./PilotForm";

export default function PilotsPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const router = useRouter();
  const allowed = can("trust.pilots.view");
  const canManage = can("trust.pilots.manage");
  const list = useApi<Pilot[]>(allowed ? trustApi(slug, "/pilots") : null, [slug]);
  const [creating, setCreating] = useState(false);

  if (!allowed) {
    return <Card><EmptyState title="Pilots are for owners, GMs and finance" body="Ask the hotel owner if you need access." /></Card>;
  }

  const open = (p: Pilot) => router.push(trustHref(slug, `/pilots/${p.id}`));

  const cols: Column<Pilot>[] = [
    { key: "name", header: "Pilot", render: p => <strong style={{ fontWeight: 600 }}>{p.name}</strong> },
    { key: "modules", header: "Modules", hideOnMobile: true, render: p => p.modules.map(moduleLabel).join(", ") || "-" },
    { key: "dates", header: "Dates", render: p => `${dayLabel(p.start_date, fmt.locale)} to ${dayLabel(p.end_date, fmt.locale)}` },
    {
      key: "score", header: "Score", align: "right", hideOnMobile: true,
      render: p => (p.outcome?.score != null ? ratePct(p.outcome.score) : "-"),
    },
    {
      key: "price", header: "Price after pilot", align: "right", hideOnMobile: true,
      render: p => (p.conversion_price_minor != null
        ? `${fmt.money(p.conversion_price_minor, { minor: true, currency: p.currency })}${p.terms?.billing_period === "year" ? " / yr" : " / mo"}`
        : "-"),
    },
    { key: "status", header: "Status", render: p => <Badge tone={PILOT_STATUS[p.status]?.tone ?? "neutral"}>{PILOT_STATUS[p.status]?.label ?? p.status}</Badge> },
  ];

  return (
    <>
      <Card
        title="Pilots"
        subtitle="Agree what success means before the pilot starts. The scorecard compares each criterion to the baseline captured on day one."
        actions={canManage ? <Button size="sm" onClick={() => setCreating(true)}>New pilot</Button> : undefined}
      >
        {list.error ? (
          <EmptyState title="Could not load pilots" body={list.error.message}
                      action={<Button variant="secondary" size="sm" onClick={list.reload}>Try again</Button>} />
        ) : (
          <Table columns={cols} rows={list.data ?? []} rowKey={p => p.id} onRowClick={open}
                 loading={list.loading && !list.data} caption="Pilots"
                 empty={<EmptyState title="No pilots yet"
                                    body={canManage ? "Create one with its dates, modules and success criteria." : "Pilots your team sets up appear here."} />} />
        )}
      </Card>
      {creating && (
        <PilotForm slug={slug} onClose={() => setCreating(false)}
                   onSaved={p => { setCreating(false); router.push(trustHref(slug, `/pilots/${p.id}`)); }} />
      )}
    </>
  );
}
