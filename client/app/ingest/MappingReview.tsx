"use client";

/* MappingReview: which column of the file is which canonical field.
   The server has already tried a saved template, then the static alias map,
   then (when a required field was still open) an AI proposal. This screen
   shows the result field by field with the AI confidence and the value the
   chosen column holds in the first sample row, so a person confirms with
   evidence instead of trusting a guess. Saving stores the layout as a
   template: the next export with the same headers maps itself.
   Sample values arrive masked by role from the server (guest names, contacts). */

import { useMemo, useState } from "react";
import { LuSparkles } from "react-icons/lu";
import { api, useApi } from "../lib/api";
import { useHotel } from "../lib/hotel";
import { Badge, Button, ConfidencePill, Field, Input, Select, Skeleton, useToast } from "../ui";
import { batchPath, cellText, errorText, fieldLabel } from "./helpers";
import type { Batch, MappingInfo, SuggestedColumn } from "./types";

type Transforms = Record<string, Record<string, unknown>>;

export interface MappingReviewProps {
  batchId: number;
  /** Called with the re-staged batch after a save (it moves to REVIEW when every required field is mapped). */
  onSaved?: (batch: Batch) => void;
  readOnly?: boolean;
}

const SOURCE_LABEL: Record<string, string> = {
  template: "Saved layout",
  alias: "Known export layout",
  ai: "AI proposal",
  manual: "Mapped by hand",
};

/* Where an already-mapped column came from, when no AI confidence exists for it. */
const FIELD_ORIGIN: Record<string, string> = {
  template: "From your saved layout",
  alias: "Known column name",
  manual: "Confirmed",
  ai: "AI, confirmed",
};

// ─── Pure helpers ───

function transformText(t: Record<string, unknown> | undefined): string | null {
  if (!t) return null;
  const parts: string[] = [];
  if (typeof t.date_fmt === "string") parts.push(`dates ${t.date_fmt}`);
  if (typeof t.decimal_sep === "string") parts.push(t.decimal_sep === "," ? "comma decimals" : "dot decimals");
  if (t.sign_flip) parts.push("sign flipped");
  if (t.pct_to_ratio) parts.push("percent to ratio");
  return parts.length ? parts.join(", ") : null;
}

/** Headers mapped to more than one field: a commit would read one column twice. */
function duplicateHeaders(mapping: Record<string, string>): Set<string> {
  const seen = new Map<string, number>();
  for (const h of Object.values(mapping)) if (h) seen.set(h, (seen.get(h) ?? 0) + 1);
  return new Set([...seen].filter(([, n]) => n > 1).map(([h]) => h));
}

function clean(mapping: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(mapping).filter(([, h]) => !!h));
}

// ─── Component ───

export function MappingReview({ batchId, onSaved, readOnly = false }: MappingReviewProps) {
  const { slug } = useHotel();
  const toast = useToast();
  const info = useApi<MappingInfo>(slug ? batchPath(slug, batchId, "/mapping") : null, [slug, batchId]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [transforms, setTransforms] = useState<Transforms>({});
  const [saveTemplate, setSaveTemplate] = useState(true);
  const [saving, setSaving] = useState(false);
  const [asking, setAsking] = useState(false);
  const [headerRow, setHeaderRow] = useState("");

  // A fresh payload (first load, AI re-ask) resets the draft to the server's
  // view. Adjusted during render (React's "reset state on prop change"
  // pattern) rather than in an effect, so there is no stale-draft frame.
  const data = info.data;
  const [seen, setSeen] = useState<MappingInfo | undefined>(undefined);
  if (data && data !== seen) {
    setSeen(data);
    setDraft({ ...data.mapping });
    setTransforms({ ...data.transforms });
  }

  const suggestionFor = useMemo(() => {
    const m = new Map<string, SuggestedColumn>();
    for (const s of data?.suggestion?.mappings ?? []) m.set(`${s.canonical}\u0000${s.header}`, s);
    return m;
  }, [data]);

  if (info.loading && !data) return <Skeleton lines={6} />;
  if (info.error) return <div className="ing-bad" role="alert">{info.error.message}</div>;
  if (!data) return null;

  const dupes = duplicateHeaders(draft);
  const missing = data.fields.filter(f => f.required && !draft[f.name]).map(f => f.name);
  const firstSample = data.samples[0] ?? {};
  const headerOptions = [{ value: "", label: "Not in this file" }, ...data.headers.map(h => ({ value: h, label: h }))];
  const aiOpen = (data.suggestion?.mappings ?? []).filter(s => draft[s.canonical] !== s.header).length;
  const dirty = JSON.stringify(clean(draft)) !== JSON.stringify(clean(data.mapping));

  const setField = (field: string, header: string) => {
    setDraft(d => ({ ...d, [field]: header }));
    // A transform belongs to the column it was proposed for.
    setTransforms(t => {
      const next = { ...t };
      delete next[field];
      const s = header ? suggestionFor.get(`${field}\u0000${header}`) : undefined;
      if (s?.transform) next[field] = s.transform;
      return next;
    });
  };

  const applyAi = () => {
    const s = data.suggestion;
    if (!s) return;
    setDraft(d => ({ ...s.mapping, ...clean(d) }));
    setTransforms(t => ({ ...s.transforms, ...t }));
  };

  const askAi = async () => {
    setAsking(true);
    try {
      await api.post<Batch>(batchPath(slug, batchId, "/mapping/suggest"));
      info.reload();
    } catch (err) {
      toast.error("AI mapping unavailable", errorText(err));
    } finally {
      setAsking(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const mapping = clean(draft);
      const tf = Object.fromEntries(Object.entries(transforms).filter(([f]) => mapping[f]));
      const b = await api.put<Batch>(batchPath(slug, batchId, "/mapping"), {
        mapping, transforms: tf, save_template: saveTemplate,
      });
      toast.success("Columns saved", b.status === "REVIEW" ? "The rows are ready to review." : undefined);
      onSaved?.(b);
      info.reload();
    } catch (err) {
      toast.error("Couldn't save the mapping", errorText(err));
    } finally {
      setSaving(false);
    }
  };

  const rereadHeader = async () => {
    const n = Number(headerRow);
    if (!Number.isInteger(n) || n < 1) return;
    try {
      const b = await api.post<Batch>(batchPath(slug, batchId, "/options"), { header_row: n - 1 });
      onSaved?.(b);
      info.reload();
    } catch (err) {
      toast.error("Couldn't re-read the file", errorText(err));
    }
  };

  return (
    <div className="cx-stack" style={{ gap: 14 }}>
      <div className="cx-row" style={{ justifyContent: "space-between" }}>
        <div className="cx-row">
          {data.mapping_source && <Badge tone="info">{SOURCE_LABEL[data.mapping_source] ?? data.mapping_source}</Badge>}
          {missing.length > 0
            ? <Badge tone="bad">{missing.length} required {missing.length === 1 ? "field" : "fields"} open</Badge>
            : <Badge tone="ok">Every required field is mapped</Badge>}
        </div>
        {!readOnly && (
          <div className="cx-row">
            {data.suggestion && aiOpen > 0 && (
              <Button size="sm" variant="secondary" onClick={applyAi}>Use AI proposal</Button>
            )}
            <Button size="sm" variant="ghost" icon={<LuSparkles aria-hidden="true" />} loading={asking} onClick={askAi}>
              {data.suggestion ? "Ask AI again" : "Ask AI to map"}
            </Button>
          </div>
        )}
      </div>

      {data.unverified_aliases && (
        <div className="ing-warn">
          This export layout has not been checked against a real file yet. Look at each sample value before you save.
        </div>
      )}

      <div className="ing-map" role="list" aria-label="Column mapping">
        {data.fields.map(f => {
          const header = draft[f.name] ?? "";
          const s = header ? suggestionFor.get(`${f.name}\u0000${header}`) : undefined;
          const tf = transformText(transforms[f.name]);
          return (
            <div key={f.name} className="ing-map__row" role="listitem"
                 data-missing={(f.required && !header) || undefined}>
              <div>
                <div className="ing-map__field">
                  {fieldLabel(f.name)}{f.required && <span className="ing-req" aria-label="required">*</span>}
                </div>
                <div className="ing-map__type">{f.type}</div>
              </div>
              <Select aria-label={`Column for ${fieldLabel(f.name)}`} value={header} options={headerOptions}
                      disabled={readOnly} invalid={dupes.has(header)}
                      onChange={e => setField(f.name, e.target.value)} />
              <div>
                {s
                  ? <ConfidencePill value={s.confidence} showValue />
                  : header
                    ? <span className="ing-note">{data.mapping[f.name] === header ? FIELD_ORIGIN[data.mapping_source ?? ""] ?? "Mapped" : "Your choice"}</span>
                    : null}
                {tf && <div className="ing-note">{tf}</div>}
              </div>
              <div className="ing-map__sample" title={header ? cellText(firstSample[header]) : undefined}>
                {header ? cellText(firstSample[header]) : ""}
              </div>
            </div>
          );
        })}
      </div>

      {dupes.size > 0 && (
        <div className="ing-warn">One column is used for two fields: {[...dupes].join(", ")}. Pick a different column for one of them.</div>
      )}

      {data.samples.length > 0 && (
        <div>
          <div className="ing-note" style={{ marginBottom: 6 }}>First {data.samples.length} rows of the file</div>
          <div className="ing-scroll">
            <table className="ing-grid">
              <thead><tr>{data.headers.map(h => <th key={h} scope="col">{h}</th>)}</tr></thead>
              <tbody>
                {data.samples.map((row, i) => (
                  <tr key={i}>{data.headers.map(h => <td key={h} title={cellText(row[h])}>{cellText(row[h])}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!readOnly && (
        <div className="cx-row" style={{ justifyContent: "space-between", alignItems: "flex-end" }}>
          <div className="cx-row" style={{ alignItems: "flex-end" }}>
            <Field label="Headers are on row">
              <Input inputMode="numeric" style={{ width: 90 }} value={headerRow} placeholder="auto"
                     onChange={e => setHeaderRow(e.target.value.replace(/[^\d]/g, ""))} />
            </Field>
            <Button size="sm" variant="ghost" disabled={!headerRow} onClick={rereadHeader}>Re-read</Button>
          </div>
          <div className="cx-row">
            <label className="cx-row ing-note" style={{ gap: 6 }}>
              <input type="checkbox" checked={saveTemplate} onChange={e => setSaveTemplate(e.target.checked)} />
              Remember this layout for next time
            </label>
            {/* Confirming an unchanged proposal is still a save: it re-stages
                the rows and stores the template. */}
            <Button loading={saving} disabled={missing.length > 0 || dupes.size > 0} onClick={save}>
              {dirty ? "Save columns" : "Confirm columns"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
