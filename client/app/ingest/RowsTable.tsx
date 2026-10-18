"use client";

/* RowsTable: the staged rows of one batch, filterable by status, with an
   inline editor while the batch is in REVIEW. Values are shown and typed in
   major units ("1,234.50"); the server re-reads only the edited fields
   through the field's type, so an untouched money field (already minor units)
   is never parsed twice. Skipping a row keeps it out of the commit without
   deleting it, so the audit trail still shows what the file said. */

import { useMemo, useState } from "react";
import { api, useApi } from "../lib/api";
import { minorToMajor } from "../lib/format";
import { useHotel } from "../lib/hotel";
import { Badge, Button, Segmented, Skeleton, useToast } from "../ui";
import { batchPath, cellText, errorText, fieldLabel, ROW_LABEL, ROW_TONE } from "./helpers";
import type { Batch, ImportRow, KindField, RowStatus, RowsResponse } from "./types";

type Filter = "ALL" | RowStatus;
const PAGE = 100;

export interface RowsTableProps {
  batch: Batch;
  /** The kind's canonical fields; derived from the rows when omitted. */
  fields?: readonly KindField[];
  editable?: boolean;
  /** Called with the refreshed batch after an edit (counts and totals move). */
  onBatch?: (batch: Batch) => void;
  /** PDF review: jump the viewer to a row's page. */
  onPage?: (page: number) => void;
}

interface PatchResult {
  row: ImportRow;
  batch: Batch;
}

// ─── Value display and editing ───

function display(v: unknown, type: string, currency: string): string {
  if (v === null || v === undefined || v === "") return "-";
  if (type === "money" && (typeof v === "number" || /^-?\d+$/.test(String(v)))) return minorToMajor(v as number, currency);
  if (type === "pct" && ratioOf(v) !== null) return `${Math.round(ratioOf(v)! * 10000) / 100}%`;
  return cellText(v);
}

/** A pct canonical is a ratio, and Decimals arrive as strings ("0.15"):
    both read as 15% (QA OWN-13 showed "0.15" in the review grid). */
function ratioOf(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v.trim())) return Number(v);
  return null;
}

function editValue(v: unknown, type: string, currency: string): string {
  if (v === null || v === undefined) return "";
  if (type === "money" && typeof v === "number") return minorToMajor(v, currency);
  if (type === "pct" && ratioOf(v) !== null) return String(Math.round(ratioOf(v)! * 10000) / 100);
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

function deriveFields(rows: readonly ImportRow[]): KindField[] {
  const names = new Set<string>();
  for (const r of rows) for (const k of Object.keys(r.canonical ?? {})) names.add(k);
  return [...names].map(name => ({
    name, type: name.endsWith("_minor") ? "money" : name.endsWith("_pct") ? "pct" : "text", required: false,
  }));
}

// ─── Component ───

export function RowsTable({ batch, fields, editable = false, onBatch, onPage }: RowsTableProps) {
  const { slug, hotel } = useHotel();
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("ALL");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const path = slug ? batchPath(slug, batch.id, "/rows") : null;
  const q = `?limit=${PAGE}&offset=${offset}${filter === "ALL" ? "" : `&status=${filter}`}`;
  // batch.updated_at in the deps: a commit, re-map or edit elsewhere refetches.
  const rows = useApi<RowsResponse>(path ? path + q : null, [batch.updated_at]);
  const list = useMemo(() => rows.data?.rows ?? [], [rows.data]);
  const cols = useMemo(() => (fields?.length ? [...fields] : deriveFields(list)), [fields, list]);
  // Show every required field plus any optional field that has a value on this page.
  const shown = cols.filter(f => f.required || list.some(r => r.canonical?.[f.name] != null));
  const hasPage = list.some(r => r.page != null);
  const currencyOf = (r: ImportRow) => String(r.canonical?.currency || batch.currency || hotel?.currency || "AED");

  const c = batch.row_counts ?? batch.stats?.rows ?? {};
  const filters: { value: Filter; label: string }[] = [
    { value: "ALL", label: `All ${batch.row_count ?? ""}`.trim() },
    ...(["ERROR", "WARN", "OK", "DUP", "SKIP"] as RowStatus[])
      .filter(s => (c[s] ?? 0) > 0)
      .map(s => ({ value: s as Filter, label: `${ROW_LABEL[s]} ${c[s]}` })),
  ];

  const startEdit = (r: ImportRow) => {
    setEditing(r.id);
    setDraft(Object.fromEntries(shown.map(f => [f.name, editValue(r.canonical?.[f.name], f.type, currencyOf(r))])));
  };

  const patch = async (r: ImportRow, body: { values?: Record<string, string | null>; skip?: boolean }) => {
    setBusy(r.id);
    try {
      const res = await api.patch<PatchResult>(batchPath(slug, batch.id, `/rows/${r.id}`), body);
      rows.setData(prev => (prev
        ? { ...prev, rows: prev.rows.map(x => (x.id === r.id ? res.row : x)) }
        : { batch_id: batch.id, total: 1, rows: [res.row] }));
      onBatch?.(res.batch);
      setEditing(null);
    } catch (err) {
      toast.error("Couldn't change the row", errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const saveEdit = (r: ImportRow) => {
    const values: Record<string, string | null> = {};
    for (const f of shown) {
      const before = editValue(r.canonical?.[f.name], f.type, currencyOf(r));
      const now = (draft[f.name] ?? "").trim();
      if (now !== before) values[f.name] = now === "" ? null : now;
    }
    if (!Object.keys(values).length) { setEditing(null); return; }
    void patch(r, { values });
  };

  if (rows.loading && !rows.data) return <Skeleton lines={5} />;
  if (rows.error) return <div className="ing-bad" role="alert">{rows.error.message}</div>;
  // The route's total counts every status; under a filter the batch's own
  // per-status count is the page total.
  const total = filter === "ALL" ? rows.data?.total ?? 0 : c[filter] ?? list.length;

  return (
    <div className="cx-stack" style={{ gap: 10 }}>
      {filters.length > 1 && (
        <Segmented size="sm" ariaLabel="Filter rows" value={filter} options={filters}
                   onChange={v => { setFilter(v); setOffset(0); setEditing(null); }} />
      )}
      <div className="ing-scroll">
        <table className="ing-grid">
          <thead>
            <tr>
              <th scope="col" className="ing-num">#</th>
              <th scope="col">Status</th>
              {hasPage && <th scope="col">Page</th>}
              {shown.map(f => (
                <th key={f.name} scope="col" className={f.type === "money" || f.type === "int" ? "ing-num" : undefined}>
                  {fieldLabel(f.name)}
                </th>
              ))}
              <th scope="col">Notes</th>
              {editable && <th scope="col"><span className="cx-sr">Actions</span></th>}
            </tr>
          </thead>
          <tbody>
            {list.map(r => {
              const isEditing = editing === r.id;
              return (
                <tr key={r.id} data-status={r.status}>
                  <td className="ing-num">{r.row_no}</td>
                  <td><Badge tone={ROW_TONE[r.status]}>{ROW_LABEL[r.status]}</Badge></td>
                  {hasPage && (
                    <td>
                      {r.page != null && onPage
                        ? <Button size="sm" variant="ghost" onClick={() => onPage(r.page as number)}>p. {r.page}</Button>
                        : r.page ?? "-"}
                      {r.line_ref && <div className="ing-note">{r.line_ref}</div>}
                    </td>
                  )}
                  {shown.map(f => (
                    <td key={f.name} className={f.type === "money" || f.type === "int" ? "ing-num" : undefined}>
                      {isEditing
                        ? <input aria-label={fieldLabel(f.name)} value={draft[f.name] ?? ""}
                                 onChange={e => setDraft(d => ({ ...d, [f.name]: e.target.value }))} />
                        : display(r.canonical?.[f.name], f.type, currencyOf(r))}
                    </td>
                  ))}
                  <td className="ing-msgs">{(r.messages ?? []).join("; ")}</td>
                  {editable && (
                    <td>
                      <div className="ing-actions">
                        {isEditing ? (
                          <>
                            <Button size="sm" loading={busy === r.id} onClick={() => saveEdit(r)}>Save</Button>
                            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
                          </>
                        ) : (
                          <>
                            <Button size="sm" variant="ghost" onClick={() => startEdit(r)}>Edit</Button>
                            <Button size="sm" variant="ghost" loading={busy === r.id}
                                    onClick={() => void patch(r, { skip: r.status !== "SKIP" })}>
                              {r.status === "SKIP" ? "Include" : "Skip"}
                            </Button>
                          </>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {total > PAGE && (
        <div className="cx-row" style={{ justifyContent: "space-between" }}>
          <span className="ing-note">Rows {offset + 1} to {Math.min(offset + PAGE, total)} of {total}</span>
          <div className="cx-row">
            <Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => setOffset(o => Math.max(0, o - PAGE))}>Previous</Button>
            <Button size="sm" variant="ghost" disabled={offset + PAGE >= total} onClick={() => setOffset(o => o + PAGE)}>Next</Button>
          </div>
        </div>
      )}
    </div>
  );
}
