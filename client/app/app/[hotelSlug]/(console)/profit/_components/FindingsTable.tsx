"use client";

/* ═══════════════════════════════════════════════════════════════════════
   FindingsTable (02 spec 3.10): every finding with filters for status,
   check, OTA, class and dates. Rows open the detail (trace, evidence,
   decision). Recoverable findings of one OTA can be ticked and sent to a
   new dispute packet in one step; the server re-checks that they belong
   together (one OTA, one currency, none already in an open packet).
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, useApi, withQuery } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, ConfidencePill, CountdownChip, Field, Input, Select, Table, useToast, type Column } from "../../../../../ui";
import {
  CLASS_LABEL, DETECTOR_LABEL, detectorLabel, num, OPEN_STATUSES, OTA_LABEL, otaLabel, packetCandidates, pgApi, pgHref,
  STATUS_LABEL, STATUS_ORDER,
} from "../lib";
import type { Finding, FindingClass, Packet } from "../types";
import { ClassBadge, ErrorNote, errorText, Money, StatusBadge, useCanManage } from "./bits";

export interface FindingFilters {
  status: string;
  detector: string;
  ota: string;
  klass: string;
  from: string;
  to: string;
}

export const EMPTY_FILTERS: FindingFilters = { status: "open", detector: "", ota: "", klass: "", from: "", to: "" };

export default function FindingsTable({ initial }: { initial?: Partial<FindingFilters> }) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const router = useRouter();
  const canManage = useCanManage();
  const [filters, setFilters] = useState<FindingFilters>({ ...EMPTY_FILTERS, ...initial });
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [creating, setCreating] = useState(false);

  const path = withQuery(pgApi(slug, "/findings"), {
    status: filters.status === "open" ? OPEN_STATUSES.join(",") : filters.status || undefined,
    detector: filters.detector || undefined,
    ota: filters.ota || undefined,
    class: filters.klass || undefined,
    from: filters.from || undefined,
    to: filters.to || undefined,
    limit: 500,
  });
  const list = useApi<Finding[]>(path);
  const rows = useMemo(() => list.data ?? [], [list.data]);
  const pickedRows = rows.filter(r => picked.has(r.id));
  const check = packetCandidates(pickedRows);
  const totals = useMemo(() => {
    const by = new Map<string, number>();
    for (const r of rows) if (r.class !== "COMPLIANCE" && r.class !== "STRUCTURAL") by.set(r.currency, (by.get(r.currency) ?? 0) + r.amount_minor);
    return [...by.entries()];
  }, [rows]);

  const set = (k: keyof FindingFilters) => (v: string) => {
    setFilters(f => ({ ...f, [k]: v }));
    setPicked(new Set());
  };
  const toggle = (id: number) => setPicked(p => {
    const n = new Set(p);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  async function createPacket() {
    if (check.reason) return;
    setCreating(true);
    try {
      const p = await api.post<Packet>(pgApi(slug, "/disputes"), { finding_ids: check.ok.map(r => r.id) });
      toast({ tone: "ok", title: "Dispute packet drafted", body: "Review it, then approve and send." });
      router.push(pgHref(slug, `/disputes/${p.id}`));
    } catch (err) {
      toast({ tone: "bad", title: "Could not draft the packet", body: errorText(err) });
    } finally {
      setCreating(false);
    }
  }

  const columns: Column<Finding>[] = [
    ...(canManage ? [{
      key: "pick", header: "", width: 36, hideOnMobile: false,
      render: (r: Finding) => (
        <input type="checkbox" aria-label={`Select finding ${r.id}`} checked={picked.has(r.id)}
               onChange={() => toggle(r.id)} disabled={r.class !== "RECOVERABLE"} />
      ),
    }] : []),
    {
      key: "what", header: "Finding",
      render: r => (
        <div>
          <div className="pg-strong">{detectorLabel(r.detector)}</div>
          <div className="pg-faint">
            {otaLabel(r.ota_code)}{r.booking_ref ? `, ${r.booking_ref}` : ""}{r.group ? ` · ${r.group.replace(/^doc:/, "statement ").replace(/^month:/, "")}` : ""}
          </div>
        </div>
      ),
    },
    { key: "class", header: "Class", hideOnMobile: true, render: r => <ClassBadge klass={r.class} /> },
    {
      key: "amount", header: "Amount", align: "right",
      render: r => (
        <div>
          <Money minor={r.amount_minor} currency={r.currency} />
          {r.recovered_minor > 0 && <div className="pg-faint"><Money minor={r.recovered_minor} currency={r.currency} /> back</div>}
        </div>
      ),
    },
    { key: "conf", header: "Confidence", hideOnMobile: true, render: r => <ConfidencePill value={num(r.confidence)} /> },
    {
      key: "deadline", header: "Deadline",
      render: r => (r.deadline_at && OPEN_STATUSES.includes(r.status)
        ? <CountdownChip due={r.deadline_at} warnWithinHours={72} />
        : <span className="pg-faint">{r.deadline_at ? fmt.date(r.deadline_at) : "None"}</span>),
    },
    { key: "status", header: "Status", render: r => <StatusBadge status={r.status} /> },
  ];

  return (
    <>
      <Card>
        <div className="pg-toolbar">
          <Field label="Status">
            <Select value={filters.status} onChange={e => set("status")(e.target.value)}>
              <option value="open">Open (found, accepted, disputed)</option>
              <option value="">All</option>
              {STATUS_ORDER.map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
            </Select>
          </Field>
          <Field label="Check">
            <Select value={filters.detector} onChange={e => set("detector")(e.target.value)}>
              <option value="">All checks</option>
              {Object.keys(DETECTOR_LABEL).map(d => <option key={d} value={d}>{d}: {DETECTOR_LABEL[d]}</option>)}
            </Select>
          </Field>
          <Field label="OTA">
            <Select value={filters.ota} onChange={e => set("ota")(e.target.value)}>
              <option value="">All channels</option>
              {["BOOKING", "AGODA", "EXPEDIA"].map(o => <option key={o} value={o}>{OTA_LABEL[o]}</option>)}
            </Select>
          </Field>
          <Field label="Class">
            <Select value={filters.klass} onChange={e => set("klass")(e.target.value)}>
              <option value="">All classes</option>
              {(Object.keys(CLASS_LABEL) as FindingClass[]).map(c => <option key={c} value={c}>{CLASS_LABEL[c]}</option>)}
            </Select>
          </Field>
          <Field label="Found from"><Input type="date" value={filters.from} onChange={e => set("from")(e.target.value)} /></Field>
          <Field label="to"><Input type="date" value={filters.to} onChange={e => set("to")(e.target.value)} /></Field>
        </div>
      </Card>

      {canManage && picked.size > 0 && (
        <div className="pg-notice pg-notice--info" role="status">
          <div className="pg-between" style={{ width: "100%" }}>
            <span>
              {picked.size} selected{check.reason ? `. ${check.reason}` : ", "}
              {!check.reason && <Money minor={check.ok.reduce((a, r) => a + r.amount_minor - r.recovered_minor, 0)} currency={check.ok[0]?.currency} />}
              {!check.reason && " to claim"}
            </span>
            <span className="pg-row">
              <Button size="sm" variant="ghost" onClick={() => setPicked(new Set())}>Clear</Button>
              <Button size="sm" variant="primary" disabled={Boolean(check.reason)} loading={creating} onClick={createPacket}>
                Draft dispute packet
              </Button>
            </span>
          </div>
        </div>
      )}

      <ErrorNote error={list.error} />
      <Card title={`${rows.length} finding${rows.length === 1 ? "" : "s"}`}
            subtitle={totals.length ? totals.map(([c, m]) => fmt.money(m, { minor: true, currency: c })).join(" + ") + " (money classes)" : undefined}
            padded={false}>
        <Table columns={columns} rows={rows} rowKey={r => r.id} loading={list.loading}
               onRowClick={r => router.push(pgHref(slug, `/findings/${r.id}`))}
               empty="No findings match these filters." caption="Profit Guard findings" />
      </Card>
    </>
  );
}
