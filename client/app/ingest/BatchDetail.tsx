"use client";

/* BatchDetail: one import from upload to commit. It picks the screen the
   batch's status calls for (mapping, tabular review, PDF review, result)
   and owns the two terminal actions, import and discard. While the server
   is still reading or extracting it polls, because extraction runs as a
   task and there is no stream event for it. */

import { useEffect, useState } from "react";
import { api, useApi } from "../lib/api";
import { useFormat } from "../lib/format";
import { useHotel } from "../lib/hotel";
import { useSession } from "../lib/session";
import { Badge, Button, Skeleton, useToast } from "../ui";
import {
  batchError, batchPath, commitSummary, committableCount, countOf, errorText, gateBlocking, ingestPath, isPdf,
  kindLabel, STATUS_LABEL, STATUS_TONE,
} from "./helpers";
import { MappingReview } from "./MappingReview";
import { PdfReview } from "./PdfReview";
import { RowsTable } from "./RowsTable";
import type { Batch, SourcesResponse } from "./types";

const POLL_MS = 3_000;
/** Roles the server lets upload and commit besides core.settings.manage (ingest router _manage). */
const IMPORT_ROLES = ["finance", "revenue"];

/** Mirrors the server's write check so read-only viewers never see buttons that 403. */
export function useCanImport(): boolean {
  const { can, role } = useSession();
  return can("core.ingest.manage") || can("core.settings.manage") || (!!role && IMPORT_ROLES.includes(String(role)));
}

export interface BatchDetailProps {
  batchId: number;
  /** Any change the host list should reflect (status, counts). */
  onChange?: (batch: Batch) => void;
  /** After a discard. */
  onClosed?: () => void;
}

function Steps({ batch }: { batch: Batch }) {
  const pdf = isPdf(batch);
  const steps = pdf ? ["Upload", "Extract", "Review", "Import"] : ["Upload", "Columns", "Review", "Import"];
  const at = batch.status === "COMMITTED" ? 3 : batch.status === "REVIEW" ? 2
    : batch.status === "MAPPING_REQUIRED" || batch.status === "EXTRACTING" ? 1 : 0;
  return (
    <div className="ing-steps" aria-label="Import progress">
      {steps.map((s, i) => <span key={s} data-on={i === at || undefined}>{i + 1}. {s}{i < steps.length - 1 ? "  ›" : ""}</span>)}
    </div>
  );
}

export function BatchDetail({ batchId, onChange, onClosed }: BatchDetailProps) {
  const { slug, hotel } = useHotel();
  const toast = useToast();
  const fmt = useFormat();
  const canWrite = useCanImport();
  const res = useApi<Batch>(slug ? batchPath(slug, batchId) : null, [batchId]);
  const sources = useApi<SourcesResponse>(slug ? ingestPath(slug, "/sources") : null, [slug]);
  const [skipErrors, setSkipErrors] = useState(false);
  const [busy, setBusy] = useState<"commit" | "discard" | "retry" | null>(null);
  const [remap, setRemap] = useState(false);
  const batch = res.data;
  const working = batch?.status === "UPLOADED" || batch?.status === "EXTRACTING";
  const { reload } = res;

  useEffect(() => {
    if (!working) return;
    const t = setInterval(reload, POLL_MS);
    return () => clearInterval(t);
  }, [working, reload]);

  if (res.error) return <div className="ing-bad" role="alert">{res.error.message}</div>;
  if (!batch) return <Skeleton lines={6} />;

  const kind = sources.data?.kinds.find(k => k.kind === batch.kind);
  const fields = kind?.fields;
  const update = (b: Batch) => { res.setData(b); onChange?.(b); };
  const errors = countOf(batch, "ERROR");
  const ready = committableCount(batch);
  const blocked = gateBlocking(batch);
  const noHandler = kind ? !kind.committable : false;

  const commit = async () => {
    setBusy("commit");
    try {
      const b = await api.post<Batch>(batchPath(slug, batch.id, "/commit"), { skip_errors: skipErrors });
      update(b);
      toast.success("Imported", `${ready} ${ready === 1 ? "row" : "rows"} from ${b.filename ?? "the file"} are in.`);
    } catch (err) {
      toast.error("Couldn't import", errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const discard = async () => {
    if (!window.confirm("Discard this import? The file stays stored, but none of its rows are imported.")) return;
    setBusy("discard");
    try {
      await api.del(batchPath(slug, batch.id));
      toast.info("Import discarded");
      onClosed?.();
    } catch (err) {
      toast.error("Couldn't discard", errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const retry = async () => {
    setBusy("retry");
    try {
      update(await api.post<Batch>(batchPath(slug, batch.id, "/reprocess")));
    } catch (err) {
      toast.error("Still failing", errorText(err));
    } finally {
      setBusy(null);
    }
  };

  const tabular = !isPdf(batch) && batch.file_format !== "ics";
  return (
    <div className="cx-stack" style={{ gap: 16 }}>
      <div className="cx-stack" style={{ gap: 6 }}>
        <div className="cx-row" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="ing-batch-title">{batch.filename || `Import #${batch.id}`}</div>
            <div className="ing-batch-sub">
              {kindLabel(batch.kind)}{batch.source_code ? ` · ${batch.source_code}` : ""}
              {batch.sheet_name ? ` · sheet ${batch.sheet_name}` : ""}
            </div>
          </div>
          <Badge tone={STATUS_TONE[batch.status]} dot>{STATUS_LABEL[batch.status] ?? batch.status}</Badge>
        </div>
        {batch.status !== "FAILED" && batch.status !== "SUPERSEDED" && <Steps batch={batch} />}
      </div>

      {working && (
        <div className="ing-okbox" aria-live="polite">
          {batch.status === "EXTRACTING"
            ? "Reading the PDF and extracting its lines. This takes up to a minute for a long statement."
            : "Reading the file..."}
        </div>
      )}

      {batch.status === "FAILED" && (
        <div className="ing-bad" role="alert">
          <p style={{ margin: "0 0 8px" }}>{batchError(batch) ?? "This file could not be read."}</p>
          {canWrite && (
            <div className="cx-row">
              <Button size="sm" loading={busy === "retry"} onClick={retry}>Try again</Button>
              <Button size="sm" variant="ghost" loading={busy === "discard"} onClick={discard}>Discard</Button>
            </div>
          )}
        </div>
      )}

      {batch.status === "SUPERSEDED" && (
        <div className="ing-warn">
          A corrected file replaced this import{batch.superseded_by ? ` (#${batch.superseded_by})` : ""}. Its rows no longer count.
        </div>
      )}

      {batch.status === "MAPPING_REQUIRED" && (
        <>
          <MappingReview batchId={batch.id} readOnly={!canWrite} onSaved={update} />
          {canWrite && (
            <div className="cx-row">
              <Button variant="ghost" size="sm" loading={busy === "discard"} onClick={discard}>Discard this file</Button>
            </div>
          )}
        </>
      )}

      {batch.status === "REVIEW" && tabular && (remap
        ? <MappingReview batchId={batch.id} readOnly={!canWrite} onSaved={b => { update(b); setRemap(false); }} />
        : canWrite && (
          <div className="cx-row" style={{ justifyContent: "flex-end" }}>
            <Button size="sm" variant="ghost" onClick={() => setRemap(true)}>Change columns</Button>
          </div>
        ))}

      {batch.status === "REVIEW" && (isPdf(batch)
        ? <PdfReview batch={batch} fields={fields} editable={canWrite} onBatch={update} />
        : <RowsTable batch={batch} fields={fields} editable={canWrite} onBatch={update} />)}

      {batch.status === "COMMITTED" && (
        <>
          <div className="ing-okbox">
            Imported{batch.committed_at ? ` on ${fmt.date(batch.committed_at, "medium")}` : ""}.
            {batch.commit_result && Object.keys(batch.commit_result).length > 0 && (
              <span> {commitSummary(batch.commit_result, String(batch.currency || hotel?.currency || "AED"),
                (minor, cur) => fmt.money(minor, { minor: true, currency: cur }))}</span>
            )}
          </div>
          <RowsTable batch={batch} fields={fields} />
        </>
      )}

      {batch.status === "REVIEW" && canWrite && (
        <div className="cx-stack" style={{ gap: 8 }}>
          {noHandler && (
            <div className="ing-warn">Nothing in Clefs receives {kindLabel(batch.kind).toLowerCase()} rows yet, so this file can be reviewed but not imported.</div>
          )}
          {errors > 0 && (
            <label className="cx-row ing-note" style={{ gap: 6 }}>
              <input type="checkbox" checked={skipErrors} onChange={e => setSkipErrors(e.target.checked)} />
              Leave out the {errors} {errors === 1 ? "row" : "rows"} with errors and import the rest
            </label>
          )}
          <div className="cx-row" style={{ justifyContent: "space-between" }}>
            <Button variant="ghost" loading={busy === "discard"} onClick={discard}>Discard</Button>
            <Button loading={busy === "commit"} onClick={commit}
                    disabled={noHandler || blocked || ready === 0 || (errors > 0 && !skipErrors)}
                    title={blocked ? "The totals check blocks this import" : undefined}>
              Import {ready} {ready === 1 ? "row" : "rows"}
            </Button>
          </div>
        </div>
      )}

    </div>
  );
}
