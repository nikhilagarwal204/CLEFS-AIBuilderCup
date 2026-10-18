"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Actions by state (02 spec 6.4 client "actions/"): every action the
   hotel has, filtered by a state group, the overdue flag (deadline passed
   while approved, assigned or in progress) and type. A row opens the
   drawer with the decisions it still takes and its StateTimeline.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useMemo, useState } from "react";
import { useApi } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import type { Action } from "../../../../../lib/types";
import {
  Badge, Button, Card, CountdownChip, Drawer, EmptyState, Field, Input, MoneyText, Segmented, Table, type Column,
} from "../../../../../ui";
import ActionDetail from "../ActionDetail";
import { brfApi, roleLabel, statusLabel, statusTone } from "../lib";
import type { BriefAction, WaContact } from "../types";

type Group = "open" | "working" | "closed" | "all";

const GROUP_STATES: Record<Group, string | undefined> = {
  open: "proposed,snoozed",
  working: "approved,assigned,in_progress,submitted,awaiting_counterparty",
  closed: "done,verified,not_verified,rejected,cancelled,observed,failed,expired,rejected_by_counterparty,unresolved",
  all: undefined,
};

const GROUPS: readonly { value: Group; label: string }[] = [
  { value: "open", label: "Waiting for a decision" },
  { value: "working", label: "In work" },
  { value: "closed", label: "Closed" },
  { value: "all", label: "All" },
];

const PAGE = 50;

export default function ActionsList({ initialOverdue, initialOpenId }: { initialOverdue: boolean; initialOpenId: number | null }) {
  const { slug } = useHotel();
  const [group, setGroup] = useState<Group>(initialOverdue ? "working" : "open");
  const [overdue, setOverdue] = useState(initialOverdue);
  const [type, setType] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [openId, setOpenId] = useState<number | null>(initialOpenId);

  const qs = new URLSearchParams();
  if (GROUP_STATES[group] && !overdue) qs.set("state", GROUP_STATES[group]!);
  if (overdue) qs.set("overdue", "true");
  if (type.trim()) qs.set("type", type.trim());
  qs.set("limit", String(limit));
  const list = useApi<BriefAction[]>(brfApi(slug, `/actions?${qs.toString()}`), [slug, group, overdue, type, limit]);
  const contacts = useApi<WaContact[]>(brfApi(slug, "/wa/contacts"), [slug]);
  const names = useMemo(() => new Map((contacts.data ?? []).map(c => [c.user_id, c.name] as const)), [contacts.data]);

  const setList = list.setData;
  const onChanged = useCallback((a: Action) => {
    setList(prev => (prev ?? []).map(r => (r.id === a.id ? { ...r, ...a } : r)));
  }, [setList]);

  const columns: Column<BriefAction>[] = [
    { key: "title", header: "Action", render: r => (
      <div style={{ display: "grid", gap: 2 }}>
        <span style={{ color: "var(--clefs-ink)", fontWeight: 600 }}>{r.title}</span>
        <span className="brf-muted">{r.type}</span>
      </div>
    ) },
    { key: "status", header: "State", render: r => (
      <div className="brf-card__tags">
        <Badge tone={statusTone(r.status)}>{statusLabel(r.status)}</Badge>
        {r.overdue && <Badge tone="bad" dot>Overdue</Badge>}
      </div>
    ) },
    { key: "value", header: "Value", align: "right", render: r => (
      r.outcome_minor !== null ? <MoneyText amount={r.outcome_minor} minor currency={r.currency} />
        : r.expected_minor !== null ? <MoneyText amount={r.expected_minor} minor currency={r.currency} /> : "-"
    ) },
    { key: "due", header: "Deadline", render: r => (r.due_at && !r.terminal ? <CountdownChip due={r.due_at} prefix="Due" /> : "-") },
    { key: "who", header: "With", hideOnMobile: true, render: r => (
      r.assignee_user_id !== null ? names.get(r.assignee_user_id) ?? `Member #${r.assignee_user_id}`
        : r.assignee_role ? roleLabel(r.assignee_role) : r.owner_role ? roleLabel(r.owner_role) : "-"
    ) },
  ];

  const rows = list.data ?? [];

  return (
    <>
      <Card>
        <div className="brf-bar" style={{ alignItems: "flex-end" }}>
          <div className="brf-bar__left">
            <Segmented ariaLabel="State" value={group} onChange={g => { setGroup(g); setOverdue(false); setLimit(PAGE); }}
                       options={GROUPS.map(g => ({ ...g, disabled: overdue && g.value !== "working" }))} />
            <label className="brf-check">
              <input type="checkbox" checked={overdue} onChange={e => { setOverdue(e.target.checked); setLimit(PAGE); if (e.target.checked) setGroup("working"); }} />
              Overdue only
            </label>
          </div>
          <Field label="Type">
            <Input value={type} onChange={e => setType(e.target.value)} placeholder="e.g. pg.dispute" />
          </Field>
        </div>
      </Card>

      {list.error && !list.data ? (
        <EmptyState title="Could not load actions" body={list.error.message} action={<Button onClick={list.reload}>Try again</Button>} />
      ) : (
        <Table columns={columns} rows={rows} rowKey={r => r.id} loading={list.loading && !list.data}
               onRowClick={r => setOpenId(r.id)} caption="Actions"
               empty={overdue ? "Nothing is overdue. Every deadline is being met." : "No actions in this state."} />
      )}
      {rows.length >= limit && (
        <div className="brf-row" style={{ justifyContent: "center" }}>
          <Button variant="ghost" loading={list.loading} onClick={() => setLimit(l => l + PAGE)}>Show more</Button>
        </div>
      )}

      <Drawer open={openId !== null} onClose={() => setOpenId(null)} title="Action" wide>
        {openId !== null && <ActionDetail slug={slug} actionId={openId} names={names} onChanged={onChanged} />}
      </Drawer>
    </>
  );
}
