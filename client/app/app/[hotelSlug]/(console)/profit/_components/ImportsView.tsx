"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Profit Guard imports (02 spec 3.10 imports/): upload an OTA statement,
   credit note, payout report, virtual card report or bank statement, then
   review and import it with the shared core/ingest components (column
   mapping with AI confidence and 5 sample rows, the PDF review with the
   page beside the extracted rows and the printed-total check).

   Upload goes through Profit Guard's own route (POST /profit-guard/imports)
   rather than the generic dropzone because it takes the OTA: a statement
   whose layout does not name its OTA would otherwise fail at commit with
   ota_required. Review and commit reuse the ingest BatchDetail unchanged.
   Contracts are uploaded on the Contracts page (they need extraction).
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { BatchDetail, BatchList, DEFAULT_ACCEPT, IngestStyles, type Batch } from "../../../../../ingest";
import { Button, Card, Field, FileDrop, ProgressBar, Select, useToast, useUpload } from "../../../../../ui";
import { OTA_LABEL, pgApi } from "../lib";
import { errorText, useCanManage } from "./bits";

const KINDS = [
  { value: "OTA_STATEMENT", label: "OTA statement or commission invoice", ota: true },
  { value: "CREDIT_NOTE", label: "OTA credit note", ota: true },
  { value: "OTA_PAYOUT", label: "OTA payout report", ota: true },
  { value: "VCC_REPORT", label: "Virtual card report", ota: true },
  { value: "BANK", label: "Bank statement", ota: false },
] as const;

export const PG_KINDS = ["OTA_STATEMENT", "CREDIT_NOTE", "OTA_PAYOUT", "VCC_REPORT", "BANK", "CONTRACT_PDF"] as const;

function ProfitUploader({ onBatch }: { onBatch: (b: Batch) => void }) {
  const { slug } = useHotel();
  const toast = useToast();
  const upload = useUpload(slug, "profit.statement");
  const [kind, setKind] = useState<string>("OTA_STATEMENT");
  const [ota, setOta] = useState<string>("BOOKING");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needsOta = KINDS.find(k => k.value === kind)?.ota ?? false;

  async function onFiles(files: File[]) {
    const file = files[0];
    if (!file) return;
    setError(null);
    const started = Date.now();
    const ref = await upload.upload(file);
    if (!ref) return;
    setCreating(true);
    try {
      const b = await api.post<Batch>(pgApi(slug, "/imports"), {
        file_id: ref.id, kind, ota_code: needsOta ? ota : undefined,
      });
      if (Date.parse(b.created_at) < started - 120_000) {
        toast({ tone: "info", title: "Already imported", body: `${ref.filename} was uploaded before; opening that import.` });
      }
      onBatch(b);
      upload.reset();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setCreating(false);
    }
  }

  const busy = upload.uploading || creating;
  return (
    <div className="cx-stack">
      <div className="pg-toolbar">
        <Field label="What is this file?">
          <Select value={kind} disabled={busy} onChange={e => setKind(e.target.value)}>
            {KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
          </Select>
        </Field>
        {needsOta && (
          <Field label="OTA">
            <Select value={ota} disabled={busy} onChange={e => setOta(e.target.value)}>
              {["BOOKING", "AGODA", "EXPEDIA"].map(o => <option key={o} value={o}>{OTA_LABEL[o]}</option>)}
            </Select>
          </Field>
        )}
      </div>
      <FileDrop accept={DEFAULT_ACCEPT} disabled={busy} onFiles={onFiles} onReject={r => setError(r)}
                title={busy ? "Working on it..." : "Drop the file here or tap to choose"}
                hint="CSV, Excel or PDF, up to 25 MB. Nothing is saved until you review and import it.">
        {upload.uploading && <div style={{ width: "100%", marginTop: 10 }}><ProgressBar value={upload.progress} /></div>}
        {creating && <p className="pg-faint" style={{ marginTop: 8 }}>Reading the file and matching its columns...</p>}
      </FileDrop>
      {(error || upload.error) && <div className="pg-error" role="alert">{error ?? upload.error?.message}</div>}
    </div>
  );
}

export default function ImportsView({ initialBatch }: { initialBatch?: number }) {
  const canManage = useCanManage();
  const [open, setOpen] = useState<number | null>(initialBatch ?? null);
  const [refresh, setRefresh] = useState(0);
  const bump = () => setRefresh(n => n + 1);

  return (
    <>
      <IngestStyles />
      <div className="pg-grid2">
        {canManage && (
          <Card title="Upload" subtitle="Monthly statements and payouts are what the checks read. Forwarding the OTA's email works too.">
            <ProfitUploader onBatch={b => { setOpen(b.id); bump(); }} />
          </Card>
        )}
        <Card title="Imports" subtitle="Newest first. Open one to review and import it.">
          <BatchList kinds={PG_KINDS} refreshKey={refresh} onOpen={b => setOpen(b.id)}
                     emptyTitle="No imports yet" emptyBody="Upload last month's Booking.com statement to start." />
        </Card>
      </div>
      {open !== null && (
        <Card title={`Import #${open}`} actions={<Button size="sm" variant="ghost" onClick={() => setOpen(null)}>Close</Button>}>
          <BatchDetail key={open} batchId={open} onChange={bump} onClosed={() => { setOpen(null); bump(); }} />
        </Card>
      )}
    </>
  );
}
