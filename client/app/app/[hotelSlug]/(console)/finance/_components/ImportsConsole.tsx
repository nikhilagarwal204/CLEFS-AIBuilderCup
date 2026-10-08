"use client";

/* ═══════════════════════════════════════════════════════════════════════
   P&L imports (04 spec 6.3 and 6.8 imports/): upload a statement, connect
   Xero or QuickBooks, and walk every imported month from "mapping needed"
   to "published". Uploads whose columns ingest could not read wait under
   "Pending uploads" and open in the shared ingest review (client/app/
   ingest), whose commit runs the finance handler like a clean upload.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useState } from "react";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Card, Drawer, EmptyState, Table, type Column } from "../../../../../ui";
import { BatchDetail as IngestBatchDetail, IngestStyles } from "../../../../../ingest";
import { batchStatus, finApi, monthLabel, sourceLabel, type OAuthReturn } from "../lib";
import type { Batch, CoreBatch, ImportsList } from "../types";
import BatchDrawer from "./BatchDrawer";
import { errText, Money, useOAuthReturnToast } from "./bits";
import Connectors from "./Connectors";
import UploadPanel from "./UploadPanel";

export default function ImportsConsole({ oauthReturn, initialBatch }: { oauthReturn?: OAuthReturn; initialBatch?: number }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const canPL = can("finance.pl.view");
  const canImport = can("finance.import");
  const [version, setVersion] = useState(0);
  const [openId, setOpenId] = useState<number | null>(initialBatch ?? null);
  const [coreId, setCoreId] = useState<number | null>(null);
  const bump = useCallback(() => setVersion(v => v + 1), []);
  useOAuthReturnToast(oauthReturn);

  const list = useApi<ImportsList>(canPL ? finApi(slug, "/imports?limit=100") : null, [slug, version]);

  if (!canPL) {
    return <Card><EmptyState title="No access to P&L imports" body="The owner, GM and finance roles see imported P&Ls." /></Card>;
  }

  const cols: Column<Batch>[] = [
    { key: "month", header: "Month", render: b => <b>{monthLabel(b.period_month)}</b> },
    { key: "status", header: "Status", render: b => { const s = batchStatus(b.status); return <Badge tone={s.tone} dot>{s.label}</Badge>; } },
    {
      key: "kind", header: "Source", hideOnMobile: true,
      render: b => <span className="fin-small">{b.statement_kind === "trial_balance" ? "Trial balance" : "P&L"}, {sourceLabel(b.source)}</span>,
    },
    {
      key: "flags", header: "Needs", hideOnMobile: true,
      render: b => (
        <span className="fin-tags">
          {b.unmapped > 0 && <Badge tone="gold">{b.unmapped} unmapped</Badge>}
          {b.blocking && <Badge tone="bad">Blocking check</Badge>}
          {b.booked_net && <Badge tone="warn">Booked net</Badge>}
        </span>
      ),
    },
    { key: "np", header: "Net profit", align: "right", render: b => <Money value={b.source_net_profit} currency={b.currency} /> },
    { key: "when", header: "Imported", hideOnMobile: true, render: b => <span className="fin-small">{fmt.date(b.created_at, "medium")}</span> },
  ];

  const pending = list.data?.pending_uploads ?? [];
  return (
    <>
      <IngestStyles />
      {canImport && <UploadPanel slug={slug} onImported={bump} />}
      <Connectors slug={slug} canConnect={can("finance.connect")} onSynced={bump} />
      {pending.length > 0 && <PendingUploads rows={pending} onOpen={setCoreId} />}
      <Card title="Imported months" subtitle="Newest first. Open a month to see its checks and publish it.">
        {list.error ? (
          <EmptyState title="Could not load imports" body={errText(list.error)} />
        ) : (
          <Table
            columns={cols}
            rows={list.data?.imports ?? []}
            rowKey={b => b.id}
            loading={list.loading && !list.data}
            onRowClick={b => setOpenId(b.id)}
            caption="Imported P&L months"
            empty="Nothing imported yet. Upload a P&L above or connect your accounting system."
          />
        )}
      </Card>
      <BatchDrawer
        slug={slug}
        batchId={openId}
        canImport={canImport}
        canPublish={can("finance.publish")}
        onClose={() => setOpenId(null)}
        onChanged={bump}
      />
      <Drawer wide open={coreId !== null} onClose={() => setCoreId(null)} title="Finish the upload">
        {coreId !== null && (
          <IngestBatchDetail batchId={coreId} onChange={bump} onClosed={() => { setCoreId(null); bump(); }} />
        )}
      </Drawer>
    </>
  );
}

function PendingUploads({ rows, onOpen }: { rows: CoreBatch[]; onOpen: (id: number) => void }) {
  const fmt = useFormat();
  const cols: Column<CoreBatch>[] = [
    { key: "file", header: "File", render: r => r.filename ?? `Upload ${r.id}` },
    { key: "status", header: "Waiting for", render: r => <Badge tone="gold">{r.status === "MAPPING_REQUIRED" ? "Column matching" : r.status === "REVIEW" ? "Row review" : "Reading"}</Badge> },
    { key: "when", header: "Uploaded", hideOnMobile: true, render: r => <span className="fin-small">{fmt.relative(r.created_at)}</span> },
  ];
  return (
    <Card title="Pending uploads" subtitle="Files that need their columns matched or rows fixed before they become months. A single-month P&L waiting without a month: upload it again with the Month set and it replaces the pending copy.">
      <Table columns={cols} rows={rows} rowKey={r => r.id} onRowClick={r => onOpen(r.id)} caption="Pending P&L uploads" />
    </Card>
  );
}
