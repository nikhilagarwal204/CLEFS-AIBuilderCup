"use client";

/* ═══════════════════════════════════════════════════════════════════════
   P&L upload (04 spec 6.3): the Files contract first (uploadFile with
   purpose finance.statement, ticketed and with progress), then
   POST /finance/imports {file_id, statement_kind, period_month?}. Ingest
   maps the file and, when it mapped cleanly, finance commits it at once:
   one batch per month column. A file whose columns ingest could not read
   stays as a pending upload, finished on the ingest screen.

   A single-month P&L with no month column and no Month picked comes back
   400 period_month_required and leaves nothing pending (QA OWN-07). The
   stored file is kept here, so picking the Month re-posts the same file_id
   instead of uploading the bytes again.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { ApiError, apiFetch, uploadFile } from "../../../../../lib/api";
import {
  Button, Card, Field, FileDrop, Input, ProgressBar, Segmented, useToast,
} from "../../../../../ui";
import { codeText, finApi, monthLabel } from "../lib";
import type { ImportOut, StatementKind } from "../types";
import { errText } from "./bits";

const ACCEPT = ".xlsx,.xls,.csv,.pdf,application/pdf,text/csv";

export default function UploadPanel({ slug, onImported, compact = false }: {
  slug: string;
  onImported?: (out: ImportOut) => void;
  compact?: boolean;
}) {
  const toast = useToast();
  // uploadFile directly (not useUpload): its error reaches this catch
  // synchronously instead of landing in state one render later.
  const [progress, setProgress] = useState<number | null>(null);
  const [kind, setKind] = useState<StatementKind>("pl");
  const [month, setMonth] = useState("");
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<ImportOut | null>(null);
  // An uploaded P&L the server sent back for its month: re-posted by id.
  const [waiting, setWaiting] = useState<{ id: string; name: string } | null>(null);
  const needMonth = kind === "trial_balance" && !month;

  const postImport = async (fileId: string, name: string) => {
    try {
      const out = await apiFetch<ImportOut>(finApi(slug, "/imports"), {
        method: "POST",
        json: { file_id: fileId, statement_kind: kind, period_month: month || undefined },
      });
      setWaiting(null);
      setLast(out);
      const n = out.finance_batches.length;
      if (n) toast.success(`${n} ${n === 1 ? "month" : "months"} imported`, "Review the account mapping, then publish.");
      else if (out.core_batch.commit_error) toast.info("Upload needs a look", "Some rows need fixing before the import.");
      else toast.info("Upload received", "Match the file's columns to finish the import.");
      onImported?.(out);
    } catch (err) {
      if (err instanceof ApiError && err.code === "period_month_required" && kind === "pl") {
        setWaiting({ id: fileId, name });
        toast.info("Pick the month", "This P&L has no month column. Choose the month it covers, then import it.");
        return;
      }
      toast.error("Could not import the statement", errText(err));
    }
  };

  const onFiles = async (files: File[]) => {
    const file = files[0];
    if (!file || needMonth) return;
    setBusy(true);
    setLast(null);
    setWaiting(null);
    try {
      setProgress(0);
      const ref = await uploadFile(slug, file, "finance.statement", setProgress);
      setProgress(100);
      await postImport(ref.id, file.name);
    } catch (err) {
      toast.error("Could not import the statement", errText(err));
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  const retryWithMonth = async () => {
    if (!waiting || !month) return;
    setBusy(true);
    try {
      await postImport(waiting.id, waiting.name);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title={compact ? "Upload a P&L" : "Upload your P&L"}
      subtitle="Excel, CSV or PDF from your accountant, Tally or any system. A file with several month columns becomes one month each."
    >
      <div className="fin-form">
        <div className="fin-toolbar">
          <Field label="Statement">
            <Segmented
              ariaLabel="Statement kind"
              value={kind}
              onChange={k => { setKind(k); setWaiting(null); }}
              options={[{ value: "pl", label: "Profit and loss" }, { value: "trial_balance", label: "Trial balance" }]}
            />
          </Field>
          <Field
            label="Month"
            hint={kind === "trial_balance" ? "Required: the month the trial balance closes."
              : waiting ? "Required: this file has no month column." : "Needed unless the file has a month column."}
            required={kind === "trial_balance" || waiting !== null}
          >
            <Input type="month" value={month} onChange={e => setMonth(e.target.value)} aria-label="Statement month" />
          </Field>
        </div>
        <FileDrop
          accept={ACCEPT}
          disabled={busy || needMonth}
          onFiles={onFiles}
          onReject={reason => toast.error("File too large", reason)}
          title={needMonth ? "Pick the month first" : "Drop the statement here or tap to choose"}
          hint="Arabic and English account names both work. Amounts stay in your hotel's currency."
        />
        {busy && progress !== null && <ProgressBar value={progress} />}
        {waiting && (
          <div className="fin-notice" role="status">
            <b>{waiting.name}</b> has no month column. Pick the month it covers above, then import it. The file is
            already uploaded, so nothing is sent twice.
            <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Button size="sm" variant="primary" onClick={retryWithMonth} disabled={!month || busy} loading={busy}>
                {month ? `Import as ${monthLabel(`${month}-01`, true)}` : "Pick the month first"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setWaiting(null)}>Cancel</Button>
            </div>
          </div>
        )}
        {last && <ImportResult out={last} onClear={() => setLast(null)} />}
      </div>
    </Card>
  );
}

function ImportResult({ out, onClear }: { out: ImportOut; onClear: () => void }) {
  const months = out.finance_batches.map(b => monthLabel(b.period_month, true));
  const err = out.core_batch.commit_error;
  return (
    <div className={err ? "fin-notice fin-notice--bad" : "fin-notice"} role="status">
      {months.length > 0 ? (
        <>Imported {months.join(", ")}. Open <b>Account mapping</b> to confirm new accounts, then publish from <b>P&L imports</b>.</>
      ) : err ? (
        <>The file was read but not imported: {codeText(err.code ?? undefined, "some rows need fixing")} Open it under pending uploads to fix the rows.</>
      ) : (
        <>The file is waiting for its columns to be matched. Open it under pending uploads on the P&L imports page.</>
      )}
      <div style={{ marginTop: 8 }}><Button size="sm" variant="ghost" onClick={onClear}>Dismiss</Button></div>
    </div>
  );
}
