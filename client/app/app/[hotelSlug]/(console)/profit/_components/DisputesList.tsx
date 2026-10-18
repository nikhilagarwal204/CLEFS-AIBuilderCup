"use client";

/* Dispute packets, newest first (02 spec 3.7). A packet groups the claims
   against one OTA; drafts wait for an owner or finance approval, then are
   sent through the extranet or email and followed up every 10 business
   days until a statement shows the credit. New packets start from the
   findings list (tick findings of one OTA). */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, Segmented, Table, type Column } from "../../../../../ui";
import { otaLabel, pgApi, pgHref } from "../lib";
import type { Packet } from "../types";
import { ErrorNote, Money, PacketBadge } from "./bits";

const VIEWS = [
  { value: "open", label: "In progress", status: "DRAFT,APPROVED,SUBMITTED,ACKNOWLEDGED,PARTIAL" },
  { value: "done", label: "Closed", status: "CREDITED,REJECTED" },
  { value: "all", label: "All", status: undefined },
] as const;

export default function DisputesList() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const router = useRouter();
  const [view, setView] = useState<(typeof VIEWS)[number]["value"]>("open");
  const status = VIEWS.find(v => v.value === view)?.status;
  const list = useApi<Packet[]>(withQuery(pgApi(slug, "/disputes"), { status, limit: 200 }));

  const columns: Column<Packet>[] = [
    { key: "id", header: "Packet", render: p => <span className="pg-strong">#{p.id}</span> },
    { key: "ota", header: "OTA", render: p => otaLabel(p.ota_code) },
    { key: "n", header: "Claims", align: "right", hideOnMobile: true, render: p => p.finding_ids.length },
    { key: "total", header: "Claimed", align: "right", render: p => <Money minor={p.total_minor} currency={p.currency} /> },
    { key: "status", header: "Status", render: p => <PacketBadge status={p.status} /> },
    {
      key: "when", header: "Updated", hideOnMobile: true,
      render: p => <span className="pg-faint">
        {p.submitted_at ? `Sent ${fmt.date(p.submitted_at)}` : p.approved_at ? `Approved ${fmt.date(p.approved_at)}` : `Drafted ${fmt.date(p.created_at)}`}
      </span>,
    },
    {
      key: "follow", header: "Follow up", hideOnMobile: true,
      render: p => (p.follow_up_at && ["SUBMITTED", "ACKNOWLEDGED", "PARTIAL"].includes(p.status)
        ? fmt.date(p.follow_up_at) : <span className="pg-faint">n/a</span>),
    },
  ];

  return (
    <>
      <div className="pg-between">
        <Segmented ariaLabel="Packet status" options={VIEWS.map(v => ({ value: v.value, label: v.label }))} value={view} onChange={setView} />
        <Button size="sm" href={pgHref(slug, "/findings?status=DETECTED")}>Pick findings to dispute</Button>
      </div>
      <ErrorNote error={list.error} />
      <Card padded={false}>
        <Table columns={columns} rows={list.data ?? []} rowKey={p => p.id} loading={list.loading}
               onRowClick={p => router.push(pgHref(slug, `/disputes/${p.id}`))}
               empty="No dispute packets here. Tick recoverable findings of one OTA on the findings list to draft one."
               caption="Dispute packets" />
      </Card>
    </>
  );
}
