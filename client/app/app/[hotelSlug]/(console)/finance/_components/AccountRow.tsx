"use client";

/* One account in the mapping review: its name (right to left when Arabic),
   the proposed USALI line, where the proposal came from, its confidence and
   rationale, up to two alternatives, and the confirm control. The line
   picker lists the closed 45-key catalogue grouped by department, so a
   person can never type a key the server would refuse. */

import { useMemo, useState } from "react";
import { apiFetch } from "../../../../../lib/api";
import { Badge, Button, ConfidencePill, Field, Input, Select, useToast } from "../../../../../ui";
import {
  confidenceOf, deptLabel, DEPT_ORDER, finApi, learnedFromLabel, nameDir, needsOverrideReason,
} from "../lib";
import type { CatalogEntry, ConfirmOut, MappingAccount } from "../types";
import { errText } from "./bits";

export default function AccountRow({ slug, a, catalog, threshold, canConfirm, onConfirmed }: {
  slug: string;
  a: MappingAccount;
  catalog: readonly CatalogEntry[];
  threshold: number;
  canConfirm: boolean;
  onConfirmed: (out: ConfirmOut, what: string) => void;
}) {
  const toast = useToast();
  const [line, setLine] = useState<string>(a.usali_line ?? "");
  const [reason, setReason] = useState(a.override_reason ?? "");
  const [editing, setEditing] = useState(!a.locked);
  const [busy, setBusy] = useState(false);
  const conf = confidenceOf(a);
  const labelOf = useMemo(() => new Map(catalog.map(c => [c.key, c.label])), [catalog]);
  const needReason = needsOverrideReason(a, line || null, catalog);
  const changed = !!line && line !== a.usali_line;
  const dir = nameDir(a.language, a.name_raw);

  const confirm = async () => {
    if (!line) return;
    if (needReason && !reason.trim()) {
      toast.error("A reason is needed", "This account reads as below GOP. Say why it belongs on this line.");
      return;
    }
    setBusy(true);
    try {
      const item: Record<string, unknown> = { account_id: a.account_id };
      if (changed || !a.usali_line) item.usali_line = line;
      if (reason.trim()) item.override_reason = reason.trim();
      const out = await apiFetch<ConfirmOut>(finApi(slug, "/mapping/confirm"), { method: "POST", json: { items: [item] } });
      onConfirmed(out, `${a.name_raw} confirmed`);
    } catch (err) {
      toast.error("Could not confirm", errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={a.locked ? "fin-acct fin-acct--locked" : "fin-acct"}>
      <div className="fin-acct__top">
        <span>
          <span className="fin-acct__name" dir={dir} lang={a.language === "ar" ? "ar" : undefined}>{a.name_raw}</span>
          {a.code && <span className="fin-acct__code"> {a.code}</span>}
        </span>
        <span className="fin-tags">
          {a.locked ? <Badge tone="ok" dot>Confirmed{a.version && a.version > 1 ? ` v${a.version}` : ""}</Badge>
            : a.usali_line ? <Badge tone="gold">To confirm</Badge> : <Badge tone="bad">Unmapped</Badge>}
          <ConfidencePill value={conf} showValue />
        </span>
      </div>
      <div className="fin-tags fin-small">
        {a.usali_line && <b style={{ color: "var(--clefs-ink)" }}>{labelOf.get(a.usali_line) ?? a.usali_line}</b>}
        <span>{learnedFromLabel(a.learned_from)}</span>
        {a.parent_group_raw && <span dir="auto">Group: {a.parent_group_raw}</span>}
        {a.is_pass_through_tax && <Badge tone="info">Pass-through tax</Badge>}
        {a.data_origin === "xero" && <Badge title="Xero data is never shared with other hotels">Xero: this hotel only</Badge>}
        {!a.locked && conf !== null && conf < threshold && <Badge>Confirm on its own</Badge>}
      </div>
      {a.ai_rationale && <div className="fin-acct__why" dir="auto">Why: {a.ai_rationale}</div>}
      {a.override_reason && a.locked && <div className="fin-acct__why">Reason given: {a.override_reason}</div>}
      {(a.alternatives?.length ?? 0) > 0 && editing && (
        <div className="fin-tags fin-small">
          <span>Or:</span>
          {a.alternatives!.map(alt => (
            <Button key={alt.usali_line} size="sm" variant="ghost" onClick={() => setLine(alt.usali_line)}
              aria-pressed={line === alt.usali_line}>
              {labelOf.get(alt.usali_line) ?? alt.usali_line} ({Math.round(alt.confidence * 100)}%)
            </Button>
          ))}
        </div>
      )}
      {canConfirm && (editing ? (
        <div className="fin-acct__controls">
          <Field label="USALI line">
            <LinePicker value={line} onChange={setLine} catalog={catalog} ariaLabel={`USALI line for ${a.name_raw}`} />
          </Field>
          {needReason && (
            <Field label="Why above GOP?" required hint="This name reads as below GOP (rent, interest, depreciation or drawings).">
              <Input value={reason} onChange={e => setReason(e.target.value)} maxLength={500} placeholder="e.g. staff housing rent, part of rooms payroll" />
            </Field>
          )}
          <Button size="sm" loading={busy} disabled={!line || (needReason && !reason.trim())} onClick={confirm}>
            {a.locked ? "Save new version" : changed || !a.usali_line ? "Map and confirm" : "Confirm"}
          </Button>
          {a.locked && <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setLine(a.usali_line ?? ""); }}>Cancel</Button>}
        </div>
      ) : (
        <div><Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Change mapping</Button></div>
      ))}
    </div>
  );
}

// ─── The closed USALI catalogue as a grouped picker ──────────────────────

export function LinePicker({ value, onChange, catalog, ariaLabel }: {
  value: string;
  onChange: (v: string) => void;
  catalog: readonly CatalogEntry[];
  ariaLabel: string;
}) {
  const groups = useMemo(() => {
    const order = DEPT_ORDER as readonly string[];
    const by = new Map<string, CatalogEntry[]>();
    for (const c of catalog) by.set(c.dept, [...(by.get(c.dept) ?? []), c]);
    return Array.from(by.entries()).sort((x, y) => order.indexOf(x[0]) - order.indexOf(y[0]));
  }, [catalog]);
  return (
    <Select value={value} onChange={e => onChange(e.target.value)} aria-label={ariaLabel}>
      <option value="" disabled>Choose a line</option>
      {groups.map(([dept, entries]) => (
        <optgroup key={dept} label={deptLabel(dept)}>
          {entries.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
        </optgroup>
      ))}
    </Select>
  );
}
