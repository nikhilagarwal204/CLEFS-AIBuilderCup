"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Audit entry drawer: who did what, the field-level diff (the server walks
   before/after and returns {path, op, before, after}), and the row's place
   in the hash chain. PII inside before/after is already redacted to field
   names plus hashes by audit.write(), so nothing here needs masking.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button, Drawer, EmptyState, Skeleton } from "../../../../../ui";
import { shortHash, trustApi } from "../lib";
import type { AuditDetail, DiffEntry } from "../types";

function show(v: unknown): string {
  if (v === undefined) return "(not set)";
  if (v === null) return "null";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

const OP_TONE = { added: "ok", removed: "bad", changed: "info" } as const;

function DiffRow({ d }: { d: DiffEntry }) {
  return (
    <div className="trs-diff__row">
      <div className="trs-diff__path">
        <span className="trs-mono">{d.path || "(whole value)"}</span>
        <Badge tone={OP_TONE[d.op] ?? "neutral"}>{d.op}</Badge>
      </div>
      <div className="trs-diff__vals">
        {d.op !== "added" && (
          <div className="trs-diff__val trs-diff__val--before trs-mono" aria-label="Before">{show(d.before)}</div>
        )}
        {d.op !== "removed" && (
          <div className="trs-diff__val trs-diff__val--after trs-mono" aria-label="After">{show(d.after)}</div>
        )}
      </div>
    </div>
  );
}

export default function AuditDrawer({ slug, id, onClose }: { slug: string; id: number | null; onClose: () => void }) {
  const fmt = useFormat();
  const { data, error, loading } = useApi<AuditDetail>(id === null ? null : trustApi(slug, `/audit/${id}`), [slug, id]);
  const [raw, setRaw] = useState(false);
  const row = data && data.id === id ? data : undefined;

  return (
    <Drawer open={id !== null} onClose={onClose} wide title={row ? row.action : "Audit entry"}>
      {loading && !row && <Skeleton lines={6} />}
      {error && <EmptyState title="Couldn't open this entry" body={error.message} />}
      {row && (
        <div className="cx-stack">
          <dl className="trs-kv">
            <dt>When</dt><dd>{fmt.dateTime(row.at, "medium")}</dd>
            <dt>Who</dt>
            <dd>
              {row.actor_label || row.actor_id || "-"} <span className="cx-faint">({row.actor_type})</span>
            </dd>
            <dt>Action</dt><dd className="trs-mono">{row.action}</dd>
            <dt>Target</dt>
            <dd>{row.entity_type ? <span className="trs-mono">{row.entity_type} #{row.entity_id ?? "-"}</span> : "-"}</dd>
            <dt>IP address</dt><dd className="trs-mono">{row.ip || "-"}</dd>
            <dt>Request</dt><dd className="trs-mono">{row.request_id || "-"}</dd>
            <dt>Entry id</dt><dd className="trs-mono">{row.id}</dd>
          </dl>

          <div>
            <div className="trs-section-title">What changed</div>
            {row.diff.length === 0
              ? <p className="cx-muted trs-small">No field changes recorded for this entry.</p>
              : <div className="trs-diff">{row.diff.map((d, i) => <DiffRow key={`${d.path}-${i}`} d={d} />)}</div>}
          </div>

          {row.meta && Object.keys(row.meta).length > 0 && (
            <div>
              <div className="trs-section-title">Context</div>
              <pre className="trs-json trs-mono">{show(row.meta)}</pre>
            </div>
          )}

          <div>
            <div className="trs-section-title">Hash chain</div>
            <dl className="trs-kv">
              <dt>This row</dt>
              <dd>
                {row.hash_ok === null ? <Badge>Not hashed</Badge>
                  : row.hash_ok ? <Badge tone="ok" dot>Hash matches</Badge>
                  : <Badge tone="bad" dot>Hash mismatch</Badge>}
              </dd>
              <dt>Row hash</dt><dd className="trs-mono" title={row.row_hash ?? ""}>{shortHash(row.row_hash)}</dd>
              <dt>Previous</dt><dd className="trs-mono" title={row.prev_hash ?? ""}>{shortHash(row.prev_hash)}</dd>
            </dl>
          </div>

          <div>
            <Button size="sm" variant="ghost" onClick={() => setRaw(r => !r)} aria-expanded={raw}>
              {raw ? "Hide raw before and after" : "Show raw before and after"}
            </Button>
            {raw && (
              <div className="trs-diff__vals" style={{ marginTop: 8 }}>
                <pre className="trs-json trs-mono" aria-label="Before">{show(row.before)}</pre>
                <pre className="trs-json trs-mono" aria-label="After">{show(row.after)}</pre>
              </div>
            )}
          </div>
        </div>
      )}
    </Drawer>
  );
}
