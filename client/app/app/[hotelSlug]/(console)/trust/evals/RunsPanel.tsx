"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Evaluation runs: the latest result, today's spend against the daily cap
   (runs are cost-capped per hotel per day, hotel-local), a "Run now"
   button for managers and the run history. Queued and running runs poll
   every 5 s until they settle, because the run itself is a worker task.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, StatTile, Table, useToast, type Column } from "../../../../../ui";
import { errText, ratePct, trustApi } from "../lib";
import type { EvalBudget, EvalRun } from "../types";
import RunDrawer from "./RunDrawer";

const STATUS_TONE = { queued: "neutral", running: "info", completed: "ok", failed: "bad", skipped: "neutral" } as const;

export default function RunsPanel({ slug, onRunChanged }: { slug: string; onRunChanged: () => void }) {
  const fmt = useFormat();
  const toast = useToast();
  const { can } = useSession();
  const canManage = can("trust.evals.manage");
  const runs = useApi<EvalRun[]>(trustApi(slug, "/evals/runs?limit=30"), [slug]);
  const budget = useApi<EvalBudget>(trustApi(slug, "/evals/budget"), [slug]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);

  const pending = (runs.data ?? []).some(r => r.status === "queued" || r.status === "running");
  const { reload: reloadRuns } = runs;
  const { reload: reloadBudget } = budget;
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => { reloadRuns(); reloadBudget(); }, 5000);
    return () => clearInterval(t);
  }, [pending, reloadRuns, reloadBudget]);

  // When a run settles, the readiness gate may have changed.
  const latestDone = (runs.data ?? []).find(r => r.status === "completed")?.id;
  useEffect(() => {
    if (latestDone !== undefined) onRunChanged();
  }, [latestDone, onRunChanged]);

  async function start() {
    setStarting(true);
    try {
      await api.post<EvalRun>(trustApi(slug, "/evals/runs"), { note: "Started from the Trust console" });
      toast.success("Run queued", "Results appear here in a few minutes.");
      reloadRuns();
    } catch (e) {
      toast.error("Couldn't start a run", errText(e));
    } finally {
      setStarting(false);
    }
  }

  const last = (runs.data ?? []).find(r => r.status === "completed");
  const columns: Column<EvalRun>[] = [
    { key: "id", header: "Run", render: r => <span className="cx-num">#{r.id}</span>, width: 70 },
    { key: "when", header: "When", render: r => fmt.dateTime(r.started_at ?? r.created_at) },
    { key: "trigger", header: "Trigger", hideOnMobile: true, render: r => r.trigger.replace(/_/g, " ") },
    { key: "status", header: "Status", render: r => <Badge tone={STATUS_TONE[r.status] ?? "neutral"}>{r.status}</Badge> },
    {
      key: "rate", header: "Pass rate", align: "right",
      render: r => (r.total ? <span className="cx-num">{ratePct(r.pass_rate)} <span className="cx-faint">({r.passed}/{r.total})</span></span> : "-"),
    },
    { key: "cost", header: "Cost", align: "right", hideOnMobile: true,
      render: r => (r.cost_usd != null ? <span className="cx-num">USD {Number(r.cost_usd).toFixed(2)}</span> : "-") },
  ];

  return (
    <>
      <div className="cx-grid cx-grid--3">
        <StatTile label="Latest pass rate" loading={runs.loading && !runs.data}
                  value={last ? ratePct(last.pass_rate) : "-"}
                  sub={last ? `Run #${last.id}, ${fmt.relative(last.finished_at ?? last.created_at)}` : "No completed run yet"} />
        <StatTile label="Spent today" loading={budget.loading && !budget.data}
                  value={budget.data ? `USD ${Number(budget.data.spent_today_usd).toFixed(2)}` : "-"}
                  sub={budget.data ? `Daily cap USD ${Number(budget.data.cap_usd).toFixed(2)}` : undefined} />
        <StatTile label="Schedule" value="04:00 nightly"
                  sub="Also after a KB approval (10-minute settle) or a prompt change." />
      </div>
      <Card
        title="Runs"
        padded={false}
        actions={canManage ? (
          <Button variant="primary" size="sm" loading={starting} disabled={pending} onClick={start}
                  title={pending ? "A run is already in progress" : undefined}>
            {pending ? "Run in progress" : "Run now"}
          </Button>
        ) : undefined}
      >
        {runs.error && !runs.data
          ? <EmptyState title="Couldn't load runs" body={runs.error.message} />
          : (
            <Table caption="Evaluation runs, newest first" columns={columns} rows={runs.data ?? []} rowKey={r => r.id}
                   loading={runs.loading} onRowClick={r => setOpenId(r.id)}
                   empty={<EmptyState title="No runs yet" body="Run the standard set to see how the concierge scores." />} />
          )}
      </Card>
      <RunDrawer slug={slug} runId={openId} onClose={() => setOpenId(null)} />
    </>
  );
}
