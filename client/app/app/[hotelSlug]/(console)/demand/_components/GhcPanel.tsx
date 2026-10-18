"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Google Hotel Center readiness (02 spec 4.3, Canon K5): Google's
   third-party rates for Hotel Ads end after 30 Sep 2026; free booking
   links are not affected. Each item is TODO, DONE or not applicable, with
   a note; saving refreshes the dr.ghc.deadline signal in the Brief.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Input, Select, Skeleton, useToast } from "../../../../../ui";
import { drApi, errorText, PERM } from "../_lib/paths";
import type { GhcChecklist, GhcStatus } from "../_lib/types";

const STATUS_LABEL: Record<GhcStatus, string> = { TODO: "To do", DONE: "Done", NA: "Not for us" };

function safeHref(u: string | null): string | null {
  if (!u) return null;
  try {
    const p = new URL(u);
    return p.protocol === "https:" ? p.href : null;
  } catch {
    return null;
  }
}

export default function GhcPanel({ slug }: { slug: string }) {
  const fmt = useFormat();
  const toast = useToast();
  const { can } = useSession();
  const ghc = useApi<GhcChecklist>(drApi(slug, "/ghc-checklist"), [slug]);
  const [edits, setEdits] = useState<Record<string, { status: GhcStatus; note: string }>>({});
  const [busy, setBusy] = useState(false);
  const manage = can(PERM.manage);
  const dirty = Object.keys(edits).length > 0;

  const save = async () => {
    setBusy(true);
    try {
      const items = Object.entries(edits).map(([item_code, v]) => ({ item_code, status: v.status, note: v.note || null }));
      ghc.setData(await api.put<GhcChecklist>(drApi(slug, "/ghc-checklist"), { items }));
      setEdits({});
      toast.success("Checklist saved");
    } catch (err) {
      toast.error("Could not save the checklist", errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const d = ghc.data;
  return (
    <Card
      gold
      title="Google Hotel Center readiness"
      subtitle="Google's third-party rates for Hotel Ads end after 30 September 2026. Free booking links are not affected, but only your own Hotel Center price feed puts an official-site price on Google."
      actions={d ? (
        <Badge tone={d.passed ? (d.open_items ? "bad" : "ok") : d.days_left <= 7 ? "warn" : "neutral"}>
          {d.passed ? (d.open_items ? "Deadline passed" : "Ready") : `${d.days_left} day${d.days_left === 1 ? "" : "s"} left`}
        </Badge>
      ) : undefined}
    >
      {ghc.error ? (
        <EmptyState title="Could not load the checklist" body={errorText(ghc.error)} action={<Button onClick={ghc.reload}>Try again</Button>} />
      ) : !d ? (
        <Skeleton lines={5} height={18} />
      ) : (
        <>
          {d.summary && <p className="dr-note" style={{ marginBottom: 8 }}>{d.summary}</p>}
          <div>
            {d.items.map(it => {
              const e = edits[it.code];
              const status = e?.status ?? it.status;
              const href = safeHref(it.source_url);
              return (
                <div key={it.code} className="dr-ghc__item">
                  <div style={{ minWidth: 0 }}>
                    <div className="dr-item__title">{it.label}</div>
                    {it.detail && <p className="dr-note">{it.detail}</p>}
                    <div className="dr-item__meta">
                      {href && <a className="cx-link" href={href} target="_blank" rel="noopener noreferrer">Google help</a>}
                      {it.updated_at && <span>Updated {fmt.relative(it.updated_at)}</span>}
                    </div>
                    {manage && (
                      <Input style={{ marginTop: 6 }} placeholder="Note (optional)" maxLength={500} value={e?.note ?? it.note ?? ""}
                             aria-label={`Note for ${it.label}`}
                             onChange={ev => setEdits(prev => ({ ...prev, [it.code]: { status, note: ev.target.value } }))} />
                    )}
                    {!manage && it.note && <p className="dr-note">{it.note}</p>}
                  </div>
                  {manage ? (
                    <Select value={status} aria-label={`Status of ${it.label}`}
                            onChange={ev => setEdits(prev => ({ ...prev, [it.code]: { status: ev.target.value as GhcStatus, note: e?.note ?? it.note ?? "" } }))}>
                      {(Object.keys(STATUS_LABEL) as GhcStatus[]).map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
                    </Select>
                  ) : (
                    <Badge tone={status === "DONE" ? "ok" : status === "NA" ? "neutral" : "warn"}>{STATUS_LABEL[status]}</Badge>
                  )}
                </div>
              );
            })}
          </div>
          {manage && (
            <div className="cx-row" style={{ marginTop: 10 }}>
              <Button variant="primary" loading={busy} disabled={!dirty} onClick={save}>Save checklist</Button>
              {dirty && <Button variant="ghost" disabled={busy} onClick={() => setEdits({})}>Discard changes</Button>}
            </div>
          )}
        </>
      )}
    </Card>
  );
}
