"use client";

/* OTA review exports (Booking.com, Agoda, Expedia) or any CSV. The file is
   read in the browser and sent as text, which keeps the import to one
   request; "Check file" runs the same import as a dry run so the owner sees
   which columns matched before anything is written. Re-importing the same
   export is safe: reviews are unique per platform and external id. */

import { useState } from "react";
import { apiFetch, hotelPath } from "../../../../lib/api";
import { Badge, Button, Card, Field, FileDrop, Select, Textarea, useToast } from "../../../../ui";
import { errText } from "./lib";
import type { ImportReport } from "./types";

const MAX_BYTES = 5 * 1024 * 1024;

const PRESETS = [
  { value: "booking", label: "Booking.com export" },
  { value: "agoda", label: "Agoda export" },
  { value: "expedia", label: "Expedia export" },
  { value: "generic", label: "Other CSV" },
];

export default function ImportPanel({ slug, onImported }: { slug: string; onImported: () => void }) {
  const toast = useToast();
  const [preset, setPreset] = useState("booking");
  const [scale, setScale] = useState("10");
  const [csv, setCsv] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "check" | "import">(null);
  const [report, setReport] = useState<ImportReport | null>(null);

  const pick = async (files: File[]) => {
    const f = files[0];
    if (!f) return;
    if (f.size > MAX_BYTES) { toast.error("File too large", "Review CSVs are limited to 5 MB."); return; }
    setCsv(await f.text());
    setFileName(f.name);
    setReport(null);
  };

  const run = async (dryRun: boolean) => {
    setBusy(dryRun ? "check" : "import");
    try {
      const r = await apiFetch<ImportReport>(hotelPath(slug, "/reviews/import"), {
        method: "POST",
        json: { preset, csv, dry_run: dryRun, scale: preset === "generic" ? Number(scale) : undefined },
      });
      setReport(r);
      if (!dryRun) {
        toast.success("Reviews imported", `${r.created} new, ${r.updated} updated, ${r.skipped} skipped.`);
        onImported();
      }
    } catch (err) {
      toast.error(dryRun ? "Could not check the file" : "Import failed", errText(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="Import reviews from a file"
      subtitle="Download the review export from the Booking.com, Agoda or Expedia extranet and drop it here.">
      <div className="rev-form">
        <div className="rev-toolbar" style={{ marginBottom: 0 }}>
          <Field label="File type">
            <Select value={preset} onChange={e => { setPreset(e.target.value); setReport(null); }} options={PRESETS} />
          </Field>
          {preset === "generic" && (
            <Field label="Score scale">
              <Select value={scale} onChange={e => setScale(e.target.value)}
                options={[{ value: "5", label: "Out of 5" }, { value: "10", label: "Out of 10" }]} />
            </Field>
          )}
        </div>
        <FileDrop accept=".csv,text/csv" maxBytes={MAX_BYTES} onFiles={files => void pick(files)}
          title={fileName ? `Selected: ${fileName}` : "Drop a CSV here or tap to choose"}
          onReject={reason => toast.error("File not accepted", reason)} />
        <Field label="Or paste CSV text">
          <Textarea code rows={4} value={csv} onChange={e => { setCsv(e.target.value); setFileName(null); setReport(null); }} />
        </Field>
        <div className="rev-actions">
          <Button variant="secondary" disabled={!csv.trim() || busy !== null} loading={busy === "check"}
            onClick={() => void run(true)}>Check file</Button>
          <Button disabled={!csv.trim() || busy !== null} loading={busy === "import"}
            onClick={() => void run(false)}>Import</Button>
        </div>

        {report && (
          <div className="rev-notice" role="status">
            <div className="rev-checks" style={{ marginBottom: 6 }}>
              <Badge tone="info">{report.dry_run ? "Check only, nothing saved" : "Imported"}</Badge>
              <Badge>{report.rows} rows</Badge>
              <Badge tone="ok">{report.created} new</Badge>
              <Badge>{report.updated} updated</Badge>
              {report.skipped > 0 && <Badge tone="warn">{report.skipped} skipped</Badge>}
            </div>
            <div className="rev-muted">
              Matched columns: {Object.entries(report.columns).map(([k, v]) => `${k} = "${v}"`).join(", ") || "none"}
            </div>
            {report.errors.length > 0 && (
              <ul className="rev-errors">
                {report.errors.slice(0, 50).map(e => <li key={e.row}>Row {e.row}: {e.error}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
