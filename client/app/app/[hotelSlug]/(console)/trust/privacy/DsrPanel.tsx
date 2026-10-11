"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Privacy request queue. Sorted by due date on the server, so the request
   closest to the 30-day PDPL deadline is always the first row. Overdue rows
   carry a red badge; the days-left column counts hotel-local days.
   Identifiers arrive already masked for the viewer's role, so a GM sees
   "•••• 1234" where the owner sees the full phone number.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button, Card, EmptyState, Select, Table, type Column } from "../../../../../ui";
import { DSR_STATUS, DSR_TYPE_LABEL, trustApi } from "../lib";
import { dsrPerson } from "../pure";
import type { Dsr } from "../types";
import DsrDrawer from "./DsrDrawer";
import NewDsrModal from "./NewDsrModal";

const STATUS_FILTER = [
  { value: "open", label: "Open" },
  { value: "all", label: "All" },
  { value: "completed", label: "Completed" },
  { value: "rejected", label: "Rejected" },
  { value: "cancelled", label: "Cancelled" },
] as const;

export default function DsrPanel({ slug }: { slug: string }) {
  const fmt = useFormat();
  const [status, setStatus] = useState<string>("open");
  const [openId, setOpenId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const list = useApi<Dsr[]>(`${trustApi(slug, "/dsr")}?status=${status}&limit=200`, [slug, status]);

  const rows = list.data ?? [];
  const overdue = rows.filter(r => r.overdue).length;
  const flagged = rows.filter(r => r.suspected_flood).length;

  const cols: Column<Dsr>[] = [
    { key: "id", header: "#", width: 56, render: r => <span className="trs-mono">{r.id}</span> },
    {
      key: "type", header: "Request",
      render: r => (
        <span className="cx-row" style={{ gap: 6 }}>
          <span>{DSR_TYPE_LABEL[r.type] ?? r.type}</span>
          {r.suspected_flood && (
            <Badge tone="warn" title="Arrived in an unusual burst of form submissions. Verify or reject it like any other.">
              Burst
            </Badge>
          )}
        </span>
      ),
    },
    {
      // identifiers_hint is an object ({name, email, phone, room, ...}); it
      // went straight into JSX once and crashed the whole page (React #31).
      // dsrPerson() only ever hands back plain strings.
      key: "who", header: "Person",
      render: r => {
        const p = dsrPerson(r);
        return (
          <span style={{ display: "inline-flex", flexDirection: "column", minWidth: 0 }}>
            <span>{p.primary}</span>
            {p.secondary && <span className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>{p.secondary}</span>}
          </span>
        );
      },
    },
    { key: "channel", header: "Channel", hideOnMobile: true, render: r => r.channel.replace(/_/g, " ") },
    { key: "received", header: "Received", hideOnMobile: true, render: r => fmt.date(r.received_at, "medium") },
    { key: "due", header: "Due", render: r => <DueCell r={r} dueLabel={fmt.date(r.due_at, "medium")} /> },
    {
      key: "status", header: "Status",
      render: r => <Badge tone={DSR_STATUS[r.status]?.tone ?? "neutral"}>{DSR_STATUS[r.status]?.label ?? r.status}</Badge>,
    },
  ];

  return (
    <>
      <Card
        title="Privacy requests"
        subtitle={overdue
          ? `${overdue} past the 30-day deadline. Answer these first.`
          : flagged
            ? `${flagged} arrived in an unusual burst and are marked. Check each one; reject those you cannot verify.`
            : "Guests may ask for a copy, a correction, erasure or a stop to processing. Each request is due 30 days after it arrives."}
        actions={
          <div className="cx-row" style={{ gap: 8 }}>
            <Select aria-label="Status" value={status} onChange={e => setStatus(e.target.value)}
                    options={STATUS_FILTER.map(o => ({ value: o.value, label: o.label }))} />
            <Button size="sm" onClick={() => setCreating(true)}>Log a request</Button>
          </div>
        }
      >
        {list.error ? (
          <EmptyState title="Could not load requests" body={list.error.message}
                      action={<Button variant="secondary" size="sm" onClick={list.reload}>Try again</Button>} />
        ) : (
          <Table
            columns={cols}
            rows={rows}
            rowKey={r => r.id}
            loading={list.loading && !list.data}
            onRowClick={r => setOpenId(r.id)}
            caption="Privacy requests by due date"
            empty={<EmptyState title={status === "open" ? "No open requests" : "Nothing here"}
                               body="Requests from the public privacy form and ones you log here appear in this queue." />}
          />
        )}
      </Card>
      <DsrDrawer slug={slug} id={openId} onClose={() => setOpenId(null)} onChanged={list.reload} />
      <NewDsrModal slug={slug} open={creating} onClose={() => setCreating(false)}
                   onCreated={d => { setCreating(false); list.reload(); setOpenId(d.id); }} />
    </>
  );
}

function DueCell({ r, dueLabel }: { r: Dsr; dueLabel: string }) {
  const closed = r.status === "completed" || r.status === "rejected" || r.status === "cancelled";
  if (closed) return <span>{dueLabel}</span>;
  if (r.overdue) return <Badge tone="bad" title={dueLabel}>Overdue</Badge>;
  const left = r.days_left ?? null;
  const tone = left !== null && left <= 5 ? "warn" : "neutral";
  return (
    <span className="cx-row" style={{ gap: 6 }}>
      <span>{dueLabel}</span>
      {left !== null && <Badge tone={tone}>{left === 1 ? "1 day" : `${left} days`}</Badge>}
    </span>
  );
}
