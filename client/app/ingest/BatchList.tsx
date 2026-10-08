"use client";

/* BatchList: the import history of one surface, newest first. Every batch
   stays listed (committed, replaced, failed) because the history is the
   answer to "did last month's export go in?". Open batches sort to the top
   of the owner's attention through their status badge, not a re-order, so
   the list reads as a timeline. */

import { useMemo } from "react";
import { useApi } from "../lib/api";
import { useFormat } from "../lib/format";
import { useHotel } from "../lib/hotel";
import { Badge, Button, EmptyState, Table, type Column } from "../ui";
import { batchError, countOf, ingestPath, kindLabel, STATUS_LABEL, STATUS_TONE } from "./helpers";
import type { Batch } from "./types";

export interface BatchListProps {
  /** Only these kinds (all when omitted). */
  kinds?: readonly string[];
  onOpen?: (batch: Batch) => void;
  /** Bump to refetch (after an upload, commit or discard). */
  refreshKey?: number | string;
  limit?: number;
  emptyTitle?: string;
  emptyBody?: string;
}

export function BatchList({
  kinds, onOpen, refreshKey = 0, limit = 50, emptyTitle = "No imports yet",
  emptyBody = "Files you upload appear here with their status, so you can see what went in and when.",
}: BatchListProps) {
  const { slug } = useHotel();
  const fmt = useFormat();
  // One kind: let the server filter. Several: fetch a wider page and filter
  // here (the route takes a single kind), which keeps it to one request.
  const single = kinds?.length === 1 ? kinds[0] : null;
  const q = single ? `?kind=${encodeURIComponent(single)}&limit=${limit}` : `?limit=${kinds?.length ? 200 : limit}`;
  const res = useApi<Batch[]>(slug ? ingestPath(slug, `/batches${q}`) : null, [refreshKey]);
  const rows = useMemo(() => {
    const all = res.data ?? [];
    const filtered = kinds?.length ? all.filter(b => kinds.includes(b.kind)) : all;
    return filtered.slice(0, limit);
  }, [res.data, kinds, limit]);

  const columns: Column<Batch>[] = [
    {
      key: "file", header: "File",
      render: b => (
        <div>
          <div className="ing-batch-title">{b.filename || `Import #${b.id}`}</div>
          <div className="ing-batch-sub">
            {kindLabel(b.kind)}{b.source_code ? ` · ${b.source_code}` : ""}{b.via && b.via !== "WEB" ? ` · via ${b.via.toLowerCase()}` : ""}
          </div>
        </div>
      ),
    },
    {
      key: "status", header: "Status",
      render: b => (
        <div className="cx-stack" style={{ gap: 4 }}>
          <Badge tone={STATUS_TONE[b.status]} dot>{STATUS_LABEL[b.status] ?? b.status}</Badge>
          {b.status === "FAILED" && batchError(b) && <span className="ing-batch-sub">{batchError(b)}</span>}
          {b.supersedes_batch_id && b.status !== "SUPERSEDED" && <span className="ing-batch-sub">Replaces #{b.supersedes_batch_id}</span>}
        </div>
      ),
    },
    {
      key: "rows", header: "Rows",
      render: b => (
        <div className="ing-counts">
          <span className="cx-num">{b.row_count ?? 0}</span>
          {countOf(b, "ERROR") > 0 && <Badge tone="bad">{countOf(b, "ERROR")} errors</Badge>}
          {countOf(b, "WARN") > 0 && <Badge tone="warn">{countOf(b, "WARN")} to check</Badge>}
          {countOf(b, "DUP") > 0 && <Badge tone="neutral">{countOf(b, "DUP")} duplicates</Badge>}
        </div>
      ),
    },
    {
      key: "period", header: "Period", hideOnMobile: true,
      render: b => (b.period_start ? `${fmt.date(b.period_start)} to ${fmt.date(b.period_end ?? b.period_start)}` : "-"),
    },
    {
      key: "when", header: "Uploaded", hideOnMobile: true,
      render: b => <span title={fmt.dateTime(b.created_at)}>{fmt.relative(b.created_at)}</span>,
    },
    {
      key: "open", header: <span className="cx-sr">Open</span>, align: "right",
      // stopPropagation: the row is clickable too, and one click should open once.
      render: b => onOpen
        ? <Button size="sm" variant="ghost" onClick={e => { e.stopPropagation(); onOpen(b); }}>Open</Button>
        : null,
    },
  ];

  if (res.error) return <div className="ing-bad" role="alert">{res.error.message}</div>;
  return (
    <Table
      columns={columns}
      rows={rows}
      rowKey={b => b.id}
      onRowClick={onOpen}
      loading={res.loading && !res.data}
      caption="Import history"
      empty={<EmptyState title={emptyTitle} body={emptyBody} />}
    />
  );
}
