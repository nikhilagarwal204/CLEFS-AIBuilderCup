"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Retention: how long each class of personal data is kept, what happens at
   the end (delete or anonymize), and the deletion log proving it ran.
   Owners edit the keep period inside the server's [min, max] bounds (the
   audit log keeps at least 365 days, call audio at most 90); the nightly
   job runs at 02:30 hotel time, and "Run now" does up to 20 s of it.
   The log holds counts only, never the deleted contents.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, Table, useToast, type Column } from "../../../../../ui";
import { errText, trustApi } from "../lib";
import type { DeletionLogRow, RetentionPolicy, RetentionRunResult } from "../types";

export default function RetentionPanel({ slug, canManage }: { slug: string; canManage: boolean }) {
  const fmt = useFormat();
  const toast = useToast();
  const policies = useApi<RetentionPolicy[]>(trustApi(slug, "/retention"), [slug]);
  const log = useApi<DeletionLogRow[]>(`${trustApi(slug, "/retention/log")}?limit=100`, [slug]);
  const [editing, setEditing] = useState<RetentionPolicy | null>(null);
  const [running, setRunning] = useState(false);

  async function runNow() {
    setRunning(true);
    try {
      const r = await api.post<RetentionRunResult>(trustApi(slug, "/retention/run"));
      const rows = Object.values(r.counts).reduce((a, b) => a + b, 0);
      if (r.failures.length) {
        toast.error(`${r.failures.length} data classes failed`, r.failures.map(f => `${f.data_class}: ${f.error}`).join("; "));
      } else {
        toast.success("Retention run finished", `${fmt.number(rows)} rows deleted or anonymized.`);
      }
      policies.reload();
      log.reload();
    } catch (e) {
      toast.error("Retention run failed", errText(e));
    } finally {
      setRunning(false);
    }
  }

  const cols: Column<RetentionPolicy>[] = [
    {
      key: "label", header: "Data",
      render: p => (
        <div>
          <div>{p.label}</div>
          <div className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>{p.tables.join(", ")}</div>
        </div>
      ),
    },
    {
      key: "keep", header: "Kept for", align: "right",
      render: p => (
        <span className="cx-row" style={{ gap: 6, justifyContent: "flex-end" }}>
          {daysLabel(p.keep_days)}
          {p.overridden && <Badge tone="gold" title={`Default ${daysLabel(p.default_keep_days)}`}>Custom</Badge>}
        </span>
      ),
    },
    { key: "action", header: "Then", render: p => (p.action === "anonymize" ? "Anonymize" : "Delete") },
    {
      key: "last", header: "Last run", hideOnMobile: true,
      render: p => (p.last_run ? `${fmt.relative(p.last_run.at)}, ${fmt.number(p.last_run.rows)} rows` : "Not yet"),
    },
    ...(canManage ? [{
      key: "edit", header: "", align: "right" as const,
      render: (p: RetentionPolicy) => <Button size="sm" variant="ghost" onClick={() => setEditing(p)}>Change</Button>,
    }] : []),
  ];

  const logCols: Column<DeletionLogRow>[] = [
    { key: "at", header: "When", render: r => fmt.dateTime(r.at) },
    { key: "class", header: "Data class", render: r => r.data_class },
    { key: "table", header: "Table", hideOnMobile: true, render: r => <span className="trs-mono">{r.table_name}</span> },
    { key: "action", header: "Action", render: r => r.action },
    { key: "rows", header: "Rows", align: "right", render: r => fmt.number(r.row_count) },
    {
      key: "crit", header: "Rule", hideOnMobile: true,
      render: r => (r.criteria?.older_than ? `older than ${fmt.date(r.criteria.older_than, "medium")}` : "-"),
    },
  ];

  return (
    <>
      <Card
        title="Retention"
        subtitle="Personal data is removed on a schedule so the hotel keeps only what it needs. The nightly run is at 02:30 hotel time."
        actions={canManage ? <Button size="sm" variant="secondary" loading={running} onClick={runNow}>Run now</Button> : undefined}
      >
        {policies.error ? (
          <EmptyState title="Could not load retention settings" body={policies.error.message} />
        ) : (
          <Table columns={cols} rows={policies.data ?? []} rowKey={p => p.data_class}
                 loading={policies.loading && !policies.data} caption="Retention by data class" />
        )}
      </Card>
      <Card title="Deletion log" subtitle="Counts only. What was removed is never copied here.">
        {log.error ? (
          <EmptyState title="Could not load the log" body={log.error.message} />
        ) : (
          <Table columns={logCols} rows={log.data ?? []} rowKey={r => r.id} loading={log.loading && !log.data}
                 empty={<EmptyState title="Nothing deleted yet" body="Rows appear after the first nightly run." />} />
        )}
      </Card>
      <EditModal slug={slug} policy={editing} onClose={() => setEditing(null)}
                 onSaved={() => { setEditing(null); policies.reload(); }} />
    </>
  );
}

function daysLabel(d: number): string {
  if (d % 365 === 0) return d === 365 ? "1 year" : `${d / 365} years`;
  if (d % 30 === 0 && d < 365) return d === 30 ? "30 days" : `${d / 30} months`;
  return `${d} days`;
}

// ─── Edit one policy ─────────────────────────────────────────────────────

function EditModal({ slug, policy, onClose, onSaved }: {
  slug: string;
  policy: RetentionPolicy | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lastKey, setLastKey] = useState<string | null>(null);

  // Seed the field when a different policy opens (render-time reset, no effect).
  const key = policy ? `${policy.data_class}:${policy.keep_days}` : null;
  if (key !== lastKey) {
    setLastKey(key);
    setValue(policy ? String(policy.keep_days) : "");
    setErr(null);
  }
  if (!policy) return null;

  const n = Number(value);
  const valid = Number.isInteger(n) && n >= policy.min_keep_days && n <= policy.max_keep_days;

  async function save(keep: number | null) {
    if (!policy) return;
    setBusy(true); setErr(null);
    try {
      await api.put(trustApi(slug, `/retention/${encodeURIComponent(policy.data_class)}`), { keep_days: keep });
      toast.success(keep === null ? "Reset to the default" : "Retention updated");
      onSaved();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={() => { if (!busy) onClose(); }}
      title={`Keep ${policy.label.toLowerCase()} for`}
      size="sm"
      footer={
        <>
          {policy.overridden && <Button variant="ghost" disabled={busy} onClick={() => save(null)}>Reset to default</Button>}
          <Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button>
          <Button loading={busy} disabled={!valid} onClick={() => save(n)}>Save</Button>
        </>
      }
    >
      <Field
        label="Days"
        htmlFor="ret-days"
        hint={`Between ${policy.min_keep_days} and ${policy.max_keep_days} days. Default ${policy.default_keep_days}. Then: ${policy.action}.`}
        error={value && !valid ? "Outside the allowed range." : err}
      >
        <Input id="ret-days" type="number" inputMode="numeric" min={policy.min_keep_days} max={policy.max_keep_days}
               value={value} invalid={!!value && !valid} onChange={e => setValue(e.target.value)} />
      </Field>
      {policy.note && <p className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>{policy.note}</p>}
      <p className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>
        A shorter period applies at the next nightly run and cannot be undone for data already removed.
      </p>
    </Modal>
  );
}
