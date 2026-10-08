"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Audit viewer (03 spec 5.1): filters, keyset paging, a diff drawer and
   CSV export for owner and GM. The filters live in the URL (?action=...)
   so a link from a signal or a colleague opens the same slice.
   Text filters settle for 400 ms before they query, so typing an actor id
   does not fire one request per key.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, useApi, type ApiError } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Field, Input, Select, Table, type Column } from "../../../../../ui";
import { downloadHref, errText, trustApi } from "../lib";
import type { AuditFacets, AuditPage, AuditRow } from "../types";
import AuditDrawer from "./AuditDrawer";
import ChainBanner from "./ChainBanner";

// ─── Filters ─────────────────────────────────────────────────────────────

const KEYS = ["actor_type", "actor_id", "action", "entity_type", "entity_id", "from", "to", "q"] as const;
type FilterKey = (typeof KEYS)[number];
type Filters = Record<FilterKey, string>;
const EMPTY: Filters = { actor_type: "", actor_id: "", action: "", entity_type: "", entity_id: "", from: "", to: "", q: "" };

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function toQuery(f: Filters): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  for (const k of KEYS) out[k] = f[k].trim() || undefined;
  return out;
}

const PAGE = 50;

// ─── Page ────────────────────────────────────────────────────────────────

export default function AuditPageView() {
  const { slug } = useHotel();
  const { can } = useSession();
  const allowed = can("trust.audit.view");
  // The route is dynamic ([hotelSlug]), so search params exist on the
  // server render too: the first paint already shows a deep link's filters.
  const params = useSearchParams();
  const [filters, setFilters] = useState<Filters>(() => {
    const f = { ...EMPTY };
    for (const k of KEYS) f[k] = params?.get(k) ?? "";
    return f;
  });
  const settled = useDebounced(filters, 400);
  const query = useMemo(() => toQuery(settled), [settled]);
  const qKey = JSON.stringify(query);
  const ready = allowed && settled === filters;

  // Mirror the settled filters into the URL without adding history entries.
  useEffect(() => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v) qs.set(k, v);
    const next = qs.toString();
    if (next !== window.location.search.replace(/^\?/, "")) {
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${next ? `?${next}` : ""}`);
    }
  }, [qKey, query]);

  const facets = useApi<AuditFacets>(allowed ? trustApi(slug, "/audit/facets") : null, [slug]);
  const first = useApi<AuditPage>(ready ? `${trustApi(slug, "/audit")}?${new URLSearchParams(
    Object.entries({ ...query, limit: String(PAGE) }).filter(([, v]) => v) as [string, string][],
  ).toString()}` : null, [slug, qKey]);

  // Older pages hang off the first page they were loaded after: when the
  // first page changes (new filters, reload) they no longer apply, which
  // is derived here rather than reset in an effect.
  const [extra, setExtra] = useState<{ base?: AuditPage; rows: AuditRow[]; cursor: number | null }>({ rows: [], cursor: null });
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreErr, setMoreErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const current = extra.base === first.data && first.data !== undefined;
  const cursor = current ? extra.cursor : first.data?.next_before_id ?? null;

  const loadMore = useCallback(async () => {
    if (cursor === null || !first.data) return;
    const base = first.data;
    setMoreBusy(true);
    setMoreErr(null);
    try {
      const page = await api.get<AuditPage>(trustApi(slug, "/audit"), { ...query, limit: PAGE, before_id: cursor });
      setExtra(x => ({ base, rows: [...(x.base === base ? x.rows : []), ...page.items], cursor: page.next_before_id }));
    } catch (e) {
      setMoreErr(errText(e));
    } finally {
      setMoreBusy(false);
    }
  }, [cursor, query, slug, first.data]);

  const rows = useMemo(() => [...(first.data?.items ?? []), ...(current ? extra.rows : [])], [first.data, current, extra.rows]);
  const set = (k: FilterKey) => (v: string) => setFilters(f => ({ ...f, [k]: v }));
  const active = KEYS.some(k => filters[k]);

  if (!allowed) {
    return <Card><EmptyState title="The audit log is for owners and GMs" body="Ask the hotel owner if you need access." /></Card>;
  }

  return (
    <>
      <ChainBanner slug={slug} onOpenRow={setOpenId} />
      <FiltersCard filters={filters} set={set} facets={facets.data} active={active}
                   onReset={() => setFilters({ ...EMPTY })}
                   exportHref={downloadHref(trustApi(slug, "/audit/export.csv"), query)} />
      <AuditTable rows={rows} loading={first.loading} error={first.error} onOpen={r => setOpenId(r.id)} />
      <div className="cx-row" style={{ justifyContent: "center" }}>
        {cursor !== null && (
          <Button onClick={loadMore} loading={moreBusy}>Load older entries</Button>
        )}
        {moreErr && <span className="cx-muted trs-small">{moreErr}</span>}
        {cursor === null && rows.length > 0 && !first.loading && (
          <span className="cx-faint trs-small">End of the log for these filters ({rows.length} entries).</span>
        )}
      </div>
      <AuditDrawer slug={slug} id={openId} onClose={() => setOpenId(null)} />
    </>
  );
}

// ─── Filters card ────────────────────────────────────────────────────────

const ACTOR_TYPES = ["user", "staff", "device", "guest", "ai", "system", "webhook"];

function FiltersCard({ filters, set, facets, active, onReset, exportHref }: {
  filters: Filters;
  set: (k: FilterKey) => (v: string) => void;
  facets?: AuditFacets;
  active: boolean;
  onReset: () => void;
  exportHref: string;
}) {
  const actorTypes = facets?.actor_types?.length ? facets.actor_types : ACTOR_TYPES;
  const opt = (xs: string[]) => [{ value: "", label: "Any" }, ...xs.map(x => ({ value: x, label: x }))];
  return (
    <Card
      title="Filters"
      subtitle="Action accepts a prefix ending in a dot or star, for example trust. or service.*"
      actions={
        <div className="cx-row">
          {active && <Button size="sm" variant="ghost" onClick={onReset}>Clear</Button>}
          {/* A plain link: the CSV streams through the /api/clefs proxy with the session cookie. */}
          <a className="cx-btn cx-btn--secondary cx-btn--sm" href={exportHref} download>Export CSV</a>
        </div>
      }
    >
      <div className="trs-filters">
        <Field label="Search" className="trs-filters__wide">
          <Input type="search" placeholder="Action, target or person" value={filters.q}
                 onChange={e => set("q")(e.target.value)} />
        </Field>
        <Field label="Action">
          <Input list="trs-audit-actions" placeholder="e.g. trust.dsr." value={filters.action}
                 onChange={e => set("action")(e.target.value)} />
          <datalist id="trs-audit-actions">
            {(facets?.actions ?? []).map(a => <option key={a} value={a} />)}
          </datalist>
        </Field>
        <Field label="Actor type">
          <Select value={filters.actor_type} onChange={e => set("actor_type")(e.target.value)} options={opt(actorTypes)} />
        </Field>
        <Field label="Actor id">
          <Input value={filters.actor_id} placeholder="User, device or staff id" onChange={e => set("actor_id")(e.target.value)} />
        </Field>
        <Field label="Target type">
          <Select value={filters.entity_type} onChange={e => set("entity_type")(e.target.value)}
                  options={opt(facets?.entity_types ?? [])} />
        </Field>
        <Field label="Target id">
          <Input value={filters.entity_id} onChange={e => set("entity_id")(e.target.value)} />
        </Field>
        <Field label="From (hotel date)">
          <Input type="date" value={filters.from} max={filters.to || undefined} onChange={e => set("from")(e.target.value)} />
        </Field>
        <Field label="To (inclusive)">
          <Input type="date" value={filters.to} min={filters.from || undefined} onChange={e => set("to")(e.target.value)} />
        </Field>
      </div>
    </Card>
  );
}

// ─── Table ───────────────────────────────────────────────────────────────

const ACTOR_TONE: Record<string, "neutral" | "info" | "gold" | "warn"> = {
  ai: "gold", system: "neutral", webhook: "neutral", device: "info", guest: "warn",
};

function AuditTable({ rows, loading, error, onOpen }: {
  rows: AuditRow[];
  loading: boolean;
  error?: ApiError;
  onOpen: (r: AuditRow) => void;
}) {
  const fmt = useFormat();
  const columns: Column<AuditRow>[] = [
    { key: "at", header: "When", width: 150, render: r => <span className="cx-num" title={r.at}>{fmt.dateTime(r.at)}</span> },
    {
      key: "actor", header: "Who",
      render: r => (
        <span className="cx-row" style={{ gap: 6 }}>
          <Badge tone={ACTOR_TONE[r.actor_type] ?? "neutral"}>{r.actor_type}</Badge>
          <span>{r.actor_label || r.actor_id || "-"}</span>
        </span>
      ),
    },
    { key: "action", header: "Action", render: r => <span className="trs-mono">{r.action}</span> },
    {
      key: "entity", header: "Target",
      render: r => (r.entity_type ? <span className="trs-mono">{r.entity_type} #{r.entity_id ?? "-"}</span> : <span className="cx-faint">-</span>),
    },
    { key: "ip", header: "IP", hideOnMobile: true, render: r => <span className="trs-mono">{r.ip || "-"}</span> },
    {
      key: "diff", header: "", align: "right", width: 90,
      render: r => (r.has_diff ? <Badge tone="info">diff</Badge> : null),
    },
  ];
  if (error && rows.length === 0) {
    return <Card><EmptyState title="Couldn't load the audit log" body={error.message} /></Card>;
  }
  return (
    <Card padded={false}>
      <Table
        caption="Audit log entries, newest first"
        columns={columns}
        rows={rows}
        rowKey={r => r.id}
        loading={loading}
        loadingRows={8}
        onRowClick={onOpen}
        empty={<EmptyState title="No entries match" body="Widen the dates or clear a filter." />}
      />
    </Card>
  );
}
