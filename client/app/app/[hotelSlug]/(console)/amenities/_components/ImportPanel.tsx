"use client";

/* CSV/XLSX import of issuance logs or supplier invoices (02 spec 5.3,
   issue/). Three steps on the core ingest pipeline: upload the file, open
   an amenities batch (the server maps the headers and parses the rows),
   then commit. Rows whose item cannot be matched are skipped and listed so
   the manager can add an alias and re-upload; a corrected re-upload of the
   same file replaces the earlier batch rather than doubling it. */

import { useState } from "react";
import { ApiError, api, hotelPath } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, Card, FileDrop, ProgressBar, Segmented, useToast, useUpload } from "../../../../../ui";
import type { ImportBatch } from "./types";

type Kind = "ISSUANCE" | "SUPPLIER_INVOICE";

const KIND_HINT: Record<Kind, string> = {
  ISSUANCE: "Columns: issued_at, item (name, key, SKU or alias), qty, floor, shift, staff_name, reason.",
  SUPPLIER_INVOICE: "Columns: invoice_no, invoice_date, item, qty, unit cost, line amount. Unit costs update from the invoice date.",
};

function errorList(errors: unknown): string[] {
  if (!errors) return [];
  if (Array.isArray(errors)) return errors.map(e => (typeof e === "string" ? e : JSON.stringify(e))).slice(0, 8);
  if (typeof errors === "object") return Object.entries(errors as Record<string, unknown>).map(([k, v]) => `${k}: ${String(v)}`).slice(0, 8);
  return [String(errors)];
}

export default function ImportPanel({ onCommitted }: { onCommitted?: () => void }) {
  const { slug } = useHotel();
  const toast = useToast();
  const up = useUpload(slug, "import");
  const [kind, setKind] = useState<Kind>("ISSUANCE");
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async (files: File[]) => {
    const file = files[0];
    if (!file) return;
    setBatch(null);
    const ref = await up.upload(file);
    if (!ref) return;
    setBusy(true);
    try {
      setBatch(await api.post<ImportBatch>(hotelPath(slug, "/amenities/issuance/import"), { file_id: ref.id, kind }));
    } catch (err) {
      toast.error("Could not read the file", err instanceof ApiError ? err.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    if (!batch) return;
    setBusy(true);
    try {
      const b = await api.post<ImportBatch>(hotelPath(slug, `/amenities/issuance/import/${batch.id}/commit`));
      setBatch(b);
      const r = b.commit_result;
      toast.success("Import committed",
        kind === "ISSUANCE" ? `${r?.written ?? 0} lines written, ${r?.skipped ?? 0} skipped.`
          : `${r?.costs_written ?? 0} unit costs updated, ${r?.skipped ?? 0} skipped.`);
      onCommitted?.();
    } catch (err) {
      toast.error("Import not committed", err instanceof ApiError ? err.message : undefined);
    } finally {
      setBusy(false);
    }
  };

  const reset = () => { setBatch(null); up.reset(); };
  const committed = Boolean(batch?.commit_result);
  const errs = errorList(batch?.errors);
  const unmatched = batch?.commit_result?.unmatched_rows ?? [];

  return (
    <Card title="Import from a file" subtitle="CSV or Excel exported from a store or housekeeping system">
      <div className="amn-toolbar">
        <Segmented ariaLabel="File type" value={kind} onChange={k => { setKind(k); reset(); }}
                   options={[{ value: "ISSUANCE", label: "Issuance log" }, { value: "SUPPLIER_INVOICE", label: "Supplier invoice" }]} />
      </div>
      <p className="amn-note" style={{ marginTop: 0, marginBottom: 10 }}>{KIND_HINT[kind]}</p>
      {!batch && (
        <FileDrop accept=".csv,.xlsx,.xls,text/csv" disabled={up.uploading || busy} onFiles={start}
                  onReject={reason => toast.error("File not accepted", reason)}
                  hint="Up to 25 MB. Nothing is written until you commit." />
      )}
      {up.uploading && <ProgressBar value={up.progress} />}
      {up.error && <p className="amn-note" role="alert">{up.error.message}</p>}
      {batch && (
        <div className="amn-stack" style={{ gap: 10 }}>
          <dl className="amn-kv">
            <dt>File</dt><dd>{batch.filename ?? "Upload"}</dd>
            <dt>Rows</dt>
            <dd>
              {batch.row_count ?? 0}
              {batch.row_counts?.ERROR ? `, ${batch.row_counts.ERROR} with errors` : ""}
              {batch.row_counts?.DUP ? `, ${batch.row_counts.DUP} duplicates` : ""}
            </dd>
            <dt>Status</dt>
            <dd><Badge tone={committed ? "ok" : batch.status === "FAILED" ? "bad" : "info"}>{batch.status.toLowerCase().replace(/_/g, " ")}</Badge>
              {(batch.gate_status === "mismatch" || batch.gate_status === "missing") && (
                <> <Badge tone="warn">printed total {batch.gate_status}</Badge></>
              )}
            </dd>
          </dl>
          {batch.status === "MAPPING_REQUIRED" && (
            <p className="amn-note" role="status">
              Some columns were not recognised. Rename the headers to the names above and upload the file again.
            </p>
          )}
          {errs.length > 0 && <ul className="amn-note" role="alert">{errs.map(e => <li key={e}>{e}</li>)}</ul>}
          {unmatched.length > 0 && (
            <div className="amn-banner" role="status">
              <span>
                {unmatched.length} row{unmatched.length === 1 ? "" : "s"} skipped because the item was not recognised
                ({unmatched.slice(0, 5).map(u => `row ${u.row_no}: ${u.item ?? "blank"}`).join("; ")}
                {unmatched.length > 5 ? "; ..." : ""}). Add the name as an alias in the catalog and upload the file again.
              </span>
            </div>
          )}
          <div className="amn-toolbar" style={{ marginBottom: 0 }}>
            {!committed && batch.status === "REVIEW" && (
              <Button variant="primary" onClick={commit} loading={busy}>Commit {batch.row_count ?? ""} rows</Button>
            )}
            <Button variant="ghost" onClick={reset} disabled={busy}>{committed ? "Import another file" : "Cancel"}</Button>
          </div>
        </div>
      )}
    </Card>
  );
}
