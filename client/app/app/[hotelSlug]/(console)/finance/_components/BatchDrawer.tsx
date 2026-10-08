"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One imported month before (or after) publishing: the eight checks, a
   preview of the headline figures (normalised when the month is booked
   net), the USALI roll-up and every account line through the mapping.
   Publish is enabled only in status `review` (every blocking check passed);
   the server re-checks and answers 409 not_publishable otherwise.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { apiFetch, useApi } from "../../../../../lib/api";
import {
  Badge, Button, ConfidencePill, Drawer, Skeleton, Table, useToast, type Column,
} from "../../../../../ui";
import {
  batchStatus, finApi, finHref, learnedFromLabel, monthLabel, nameDir, num, pctText, sourceLabel,
} from "../lib";
import type { Batch, BatchDetail, BatchLine, Dec, Monthly } from "../types";
import { errText, Money } from "./bits";
import { ChecksPanel, UsaliTable } from "./PLTables";

export default function BatchDrawer({ slug, batchId, canImport, canPublish, onClose, onChanged }: {
  slug: string;
  batchId: number | null;
  canImport: boolean;
  canPublish: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<"refresh" | "publish" | null>(null);
  const res = useApi<BatchDetail>(batchId === null ? null : finApi(slug, `/imports/${batchId}`), [slug, batchId]);
  const b = res.data && res.data.id === batchId ? res.data : undefined;
  const st = batchStatus(b?.status);

  const refresh = async () => {
    if (batchId === null) return;
    setBusy("refresh");
    try {
      await apiFetch<Batch>(finApi(slug, `/imports/${batchId}/refresh`), { method: "POST" });
      toast.success("Checks re-run");
      res.reload();
      onChanged();
    } catch (err) {
      toast.error("Could not re-run the checks", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const publish = async () => {
    if (batchId === null || !b) return;
    if (!window.confirm(`Publish ${monthLabel(b.period_month)}? The one-pager, Ask Clefs and the brief will read these figures.`)) return;
    setBusy("publish");
    try {
      const out = await apiFetch<{ batch: Batch; monthly: Monthly | null }>(finApi(slug, `/imports/${batchId}/publish`), { method: "POST" });
      toast.success(`${monthLabel(out.batch.period_month)} published`, "The one-pager and the anomaly scan are up to date.");
      res.reload();
      onChanged();
    } catch (err) {
      toast.error("Could not publish", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const footer = b && (
    <div className="fin-toolbar" style={{ justifyContent: "flex-end", width: "100%" }}>
      <Button variant="ghost" href={finHref(slug, `/mapping?batch_id=${b.id}`)}>Review mapping</Button>
      {canImport && b.status !== "published" && b.status !== "superseded" && (
        <Button variant="secondary" loading={busy === "refresh"} disabled={!!busy} onClick={refresh}>Re-run checks</Button>
      )}
      {canPublish && b.status !== "published" && b.status !== "superseded" && (
        <Button loading={busy === "publish"} disabled={!!busy || b.status !== "review"} onClick={publish}
          title={b.status !== "review" ? "Every blocking check has to pass first" : undefined}>
          Publish month
        </Button>
      )}
    </div>
  );

  return (
    <Drawer wide open={batchId !== null} onClose={onClose} title={b ? `${monthLabel(b.period_month)} P&L` : "P&L import"} footer={footer || undefined}>
      {!b ? (
        res.error ? <div className="fin-notice fin-notice--bad">{errText(res.error)}</div> : <Skeleton lines={8} />
      ) : (
        <div className="cx-stack">
          <div className="fin-tags">
            <Badge tone={st.tone} dot>{st.label}</Badge>
            <Badge>{b.statement_kind === "trial_balance" ? "Trial balance" : "Profit and loss"}</Badge>
            <Badge>{sourceLabel(b.source)}</Badge>
            {b.booked_net && <Badge tone="warn">Booked net</Badge>}
            {b.unmapped > 0 && <Badge tone="gold">{b.unmapped} unmapped</Badge>}
          </div>
          <dl className="fin-kv">
            <dt>Net profit in the file</dt><dd><Money value={b.source_net_profit} currency={b.currency} /></dd>
            <dt>Net profit as mapped</dt><dd><Money value={b.mapped_net_profit} currency={b.currency} /></dd>
            {b.reconciling_minor ? (
              <>
                <dt>Excluded (pass-through and balance sheet)</dt>
                <dd><Money value={b.reconciling ?? null} currency={b.currency} /></dd>
              </>
            ) : null}
            <SummaryRows s={b.summary} currency={b.currency} />
          </dl>
          <ChecksPanel checks={b.checks} />
          <UsaliTable rows={b.usali} currency={b.currency} title="USALI roll-up (as reported)" />
          <LinesTable lines={b.lines} currency={b.currency} />
        </div>
      )}
    </Drawer>
  );
}

// ─── Pieces ──────────────────────────────────────────────────────────────

function SummaryRows({ s, currency }: { s: Record<string, unknown>; currency: string }) {
  const dec = (k: string) => (typeof s[k] === "string" ? (s[k] as string) : null) as Dec;
  return (
    <>
      <dt>Total revenue</dt><dd><Money value={dec("total_revenue")} currency={currency} /></dd>
      <dt>GOP</dt><dd><Money value={dec("gop")} currency={currency} /> ({pctText(dec("gop_margin_display"))})</dd>
      <dt>GOPPAR</dt><dd><Money value={dec("goppar")} currency={currency} /></dd>
      <dt>Labour</dt><dd>{pctText(dec("labour_pct_display"))}</dd>
    </>
  );
}

function LinesTable({ lines, currency }: { lines: BatchLine[]; currency: string }) {
  const cols: Column<BatchLine>[] = [
    {
      key: "name", header: "Account",
      render: l => (
        <span>
          <span dir={nameDir(null, l.name_raw)} style={{ unicodeBidi: "plaintext" }}>{l.name_raw}</span>
          {l.code && <span className="fin-acct__code"> {l.code}</span>}
        </span>
      ),
    },
    {
      key: "line", header: "USALI line",
      render: l => l.usali_line
        ? <span className="fin-tags">{l.usali_line}{!l.locked && <Badge tone="gold">To confirm</Badge>}</span>
        : <Badge tone="bad">Unmapped</Badge>,
    },
    {
      key: "how", header: "From", hideOnMobile: true,
      render: l => <span className="fin-tags"><span className="fin-small">{learnedFromLabel(l.learned_from)}</span><ConfidencePill value={num(l.ai_confidence)} /></span>,
    },
    { key: "amt", header: "Amount", align: "right", render: l => <span className="fin-num"><Money value={l.amount} currency={currency} /></span> },
  ];
  return (
    <div>
      <h3 className="fin-group__head">Account lines ({lines.length})</h3>
      <Table columns={cols} rows={lines} rowKey={l => l.account_id} caption="Account lines in this import" empty="No lines." />
    </div>
  );
}
