"use client";

/* The USALI summary table and the checks panel, shared by the one-pager
   (a published month) and the import review (a batch before publishing).
   Revenue lines are income, every other line a cost (shown negative), in
   USALI schedule order; the server already sorted and signed them. */

import { Badge, Card, Table, type Column } from "../../../../../ui";
import { checkStatus, deptLabel } from "../lib";
import type { Check, Dec, UsaliRow } from "../types";
import { Money } from "./bits";

// ─── USALI summary ───────────────────────────────────────────────────────

export interface Subtotal { label: string; value: Dec | undefined; strong?: boolean }

export function UsaliTable({ rows, currency, subtotals = [], loading = false, title = "USALI summary" }: {
  rows: readonly UsaliRow[];
  currency?: string;
  subtotals?: readonly Subtotal[];
  loading?: boolean;
  title?: string;
}) {
  const cols: Column<UsaliRow>[] = [
    { key: "line", header: "Line", render: r => <span>{r.label}</span> },
    { key: "dept", header: "Department", hideOnMobile: true, render: r => <span className="cx-faint">{deptLabel(r.dept)}</span> },
    {
      key: "amount", header: "Amount", align: "right",
      render: r => (
        <span className={r.amount_minor < 0 ? "fin-num fin-amount-neg" : "fin-num"}>
          <Money value={r.amount} currency={currency} />
        </span>
      ),
    },
  ];
  return (
    <Card title={title} subtitle="Your accounts rolled up to USALI 12th edition lines through the confirmed mapping.">
      <Table
        columns={cols}
        rows={rows}
        rowKey={r => r.usali_line}
        loading={loading}
        caption="P&L by USALI line"
        empty="No mapped lines yet."
      />
      {subtotals.length > 0 && (
        <dl className="fin-kv" style={{ marginTop: 14 }}>
          {subtotals.map(s => (
            <div key={s.label} style={{ display: "contents" }}>
              <dt>{s.label}</dt>
              <dd style={s.strong ? { fontWeight: 700 } : undefined}><Money value={s.value} currency={currency} /></dd>
            </div>
          ))}
        </dl>
      )}
    </Card>
  );
}

// ─── Checks ──────────────────────────────────────────────────────────────

export function ChecksPanel({ checks, title = "Checks before publish" }: { checks: readonly Check[]; title?: string }) {
  const blocking = checks.filter(c => c.status === "fail" && c.blocking).length;
  const warns = checks.filter(c => c.status === "warn").length;
  const sub = blocking
    ? `${blocking} blocking ${blocking === 1 ? "check" : "checks"} must pass before this month can be published.`
    : warns
      ? `${warns} ${warns === 1 ? "check needs" : "checks need"} a look; none blocks publishing.`
      : "Every check passed.";
  return (
    <Card title={title} subtitle={checks.length ? sub : undefined}>
      {checks.length === 0 ? (
        <div className="fin-muted">No checks recorded for this month.</div>
      ) : (
        <div>
          {checks.map(c => {
            const st = checkStatus(c.status);
            return (
              <div key={c.key} className="fin-check">
                <Badge tone={st.tone} dot>{st.label}</Badge>
                <span className="fin-check__label">{c.label}{c.blocking && c.status === "fail" ? " (blocks publishing)" : ""}</span>
                {c.detail && <span className="fin-check__detail">{c.detail}</span>}
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
