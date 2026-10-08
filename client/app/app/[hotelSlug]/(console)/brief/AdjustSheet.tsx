"use client";

/* ═══════════════════════════════════════════════════════════════════════
   AdjustSheet: change what an action will do, then approve it (decide
   "adjust" with a params_patch). Loads the action fresh (the brief item
   carries no params) and offers:
   - the one bounded field when the action declares bounds (params.adjust,
     min_/max_<f>_minor or floor/ceiling: the same convention the WhatsApp
     flow and the mobile brief use), entered in major units and checked
     against the bounds before sending;
   - otherwise the action's plain scalar params, with *_minor money fields
     shown in major units. Nested params stay as they are (console only).
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import type { Action } from "../../../../lib/types";
import { Button, EmptyState, Field, Input, Modal, Select, Skeleton, Textarea } from "../../../../ui";
import { actionApi, adjustBounds, minorToInput, parseMajorToMinor } from "./lib";

type Scalar = string | number | boolean;

const YES_NO = [{ value: "true", label: "Yes" }, { value: "false", label: "No" }] as const;

const editable = (k: string, v: unknown): v is Scalar =>
  (typeof v === "string" || typeof v === "number" || typeof v === "boolean")
  && !k.startsWith("min_") && !k.startsWith("max_") && k !== "floor_minor" && k !== "ceiling_minor" && !k.endsWith("_id");

const labelOf = (k: string) => k.replace(/_minor$/, "").replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());

export default function AdjustSheet({ slug, actionId, open, onClose, onSubmit, busy }: {
  slug: string;
  actionId: number | null;
  open: boolean;
  onClose: () => void;
  onSubmit: (patch: Record<string, unknown>, note?: string) => unknown;
  busy?: boolean;
}) {
  const fmt = useFormat();
  const action = useApi<Action>(open && actionId !== null ? actionApi(slug, actionId) : null, [slug, actionId, open]);
  const a = action.data && action.data.id === actionId ? action.data : undefined;
  const ccy = a?.currency || fmt.currency;
  const bounds = useMemo(() => (a ? adjustBounds(a.params, ccy) : null), [a, ccy]);
  const fields = useMemo(() => Object.entries(a?.params ?? {}).filter(([k, v]) => editable(k, v)) as [string, Scalar][], [a]);

  // Draft values keyed by field; seeded lazily from the loaded action.
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const initial = (k: string, v: Scalar) =>
    typeof v === "number" && k.endsWith("_minor") ? minorToInput(v, ccy) : String(v);
  const value = (k: string, v: Scalar) => draft[k] ?? initial(k, v);

  const close = () => { setDraft({}); setNote(""); setErr(null); onClose(); };

  const submit = async () => {
    if (!a) return;
    const patch: Record<string, unknown> = {};
    if (bounds) {
      const cur = a.params[bounds.field];
      const raw = draft[bounds.field] ?? (typeof cur === "number" ? minorToInput(cur, ccy) : "");
      const minor = parseMajorToMinor(raw, bounds.currency);
      if (minor === null) return setErr("Enter an amount, for example 455 or 455.50.");
      if (minor < bounds.min_minor || minor > bounds.max_minor) {
        return setErr(`Keep it between ${fmt.money(bounds.min_minor, { minor: true, currency: bounds.currency })} and ${fmt.money(bounds.max_minor, { minor: true, currency: bounds.currency })}.`);
      }
      patch[bounds.field] = minor;
    } else {
      for (const [k, v] of fields) {
        const raw = draft[k];
        if (raw === undefined || raw === initial(k, v)) continue;
        if (typeof v === "boolean") patch[k] = raw === "true";
        else if (typeof v === "number" && k.endsWith("_minor")) {
          const minor = parseMajorToMinor(raw, ccy);
          if (minor === null) return setErr(`${labelOf(k)}: enter an amount.`);
          patch[k] = minor;
        } else if (typeof v === "number") {
          const n = Number(raw);
          if (!Number.isFinite(n)) return setErr(`${labelOf(k)}: enter a number.`);
          patch[k] = n;
        } else patch[k] = raw;
      }
      if (!Object.keys(patch).length) return setErr("Change at least one value, or use Approve.");
    }
    setErr(null);
    // A refused decision (null or false) keeps the sheet open with the
    // values typed; the caller has already said why in a toast.
    const out = await onSubmit(patch, note.trim() || undefined);
    if (out !== null && out !== false) close();
  };

  return (
    <Modal open={open} onClose={close} title="Adjust, then approve" size="sm" dismissible={!busy}
           footer={a ? (
             <>
               <Button variant="ghost" onClick={close} disabled={busy}>Cancel</Button>
               <Button variant="primary" loading={busy} onClick={() => void submit()}>Approve with changes</Button>
             </>
           ) : undefined}>
      {!a && action.loading && <Skeleton lines={3} height={36} />}
      {!a && action.error && <EmptyState title="Could not load this item" body={action.error.message} />}
      {a && (
        <div className="cx-stack" style={{ gap: 12 }}>
          <p className="brf-muted" style={{ margin: 0 }}>{a.title}</p>
          {bounds ? (
            <Field label={labelOf(bounds.label)} error={err ?? undefined}
                   hint={`Between ${fmt.money(bounds.min_minor, { minor: true, currency: bounds.currency })} and ${fmt.money(bounds.max_minor, { minor: true, currency: bounds.currency })}.`}>
              <Input inputMode="decimal" autoFocus value={draft[bounds.field] ?? (typeof a.params[bounds.field] === "number" ? minorToInput(a.params[bounds.field] as number, ccy) : "")}
                     onChange={e => setDraft(d => ({ ...d, [bounds.field]: e.target.value }))} />
            </Field>
          ) : fields.length === 0 ? (
            <EmptyState title="Nothing to adjust here" body="This item has no settings to change. Approve it as it is, or assign it." />
          ) : (
            <>
              {fields.map(([k, v]) => (
                <Field key={k} label={k.endsWith("_minor") ? `${labelOf(k)} (${ccy})` : labelOf(k)}>
                  {typeof v === "boolean" ? (
                    <Select value={value(k, v)} options={YES_NO}
                            onChange={e => setDraft(d => ({ ...d, [k]: e.target.value }))} />
                  ) : (
                    <Input inputMode={typeof v === "number" ? "decimal" : undefined} value={value(k, v)}
                           onChange={e => setDraft(d => ({ ...d, [k]: e.target.value }))} />
                  )}
                </Field>
              ))}
              {err && <p className="cx-error-text" role="alert" style={{ margin: 0 }}>{err}</p>}
            </>
          )}
          <Field label="Note (optional)">
            <Textarea value={note} onChange={e => setNote(e.target.value)} rows={2} maxLength={2000} />
          </Field>
        </div>
      )}
    </Modal>
  );
}
