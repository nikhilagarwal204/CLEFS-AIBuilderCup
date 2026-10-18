"use client";

/* CSV import. Always a dry run first: the owner sees how many rows would be
   created or updated and every bad row with its reason, then applies. The
   file is read in the browser and sent as text (the server caps it at
   2 MB / 2,000 rows), so a menu import never leaves a stored file behind. */

import { useState } from "react";
import { api, hotelPath } from "../../../../lib/api";
import { Badge, Button, Card, Field, FileDrop, Select, Textarea, useToast } from "../../../../ui";
import { errText } from "./lib";
import type { ImportReport, MenuData } from "./types";

const SAMPLE = `outlet,sku,category,name_en,name_ar,price,dietary,alcohol,prep_min,available_from,available_to,days
ird,B01,Breakfast,Shakshuka,شكشوكة,48.00,vegetarian,no,15,06:30,11:00,mon-sun
ird,M12,Mains,Wagyu burger,برجر واغيو,95.00,,no,25,,,
pool,D03,Drinks,Fresh lemon mint,ليمون بالنعناع,28.00,vegan,no,5,,,`;

const MAX_BYTES = 2 * 1024 * 1024;

export default function ImportTab({ data }: { data: MenuData }) {
  const toast = useToast();
  const [csv, setCsv] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [defaultOutlet, setDefaultOutlet] = useState("");
  const [createOutlets, setCreateOutlets] = useState(true);
  const [strict, setStrict] = useState(false);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [busy, setBusy] = useState<"dry" | "apply" | null>(null);

  const onFile = async (files: File[]) => {
    const f = files[0];
    if (!f) return;
    if (f.size > MAX_BYTES) { toast.error("File too large", "Menu CSVs are limited to 2 MB."); return; }
    setCsv(await f.text());
    setFileName(f.name);
    setReport(null);
  };

  const run = async (dryRun: boolean) => {
    if (!csv.trim()) return;
    setBusy(dryRun ? "dry" : "apply");
    try {
      const r = await api.post<ImportReport>(hotelPath(data.slug, "/menu/import"), {
        csv, default_outlet: defaultOutlet || null, create_outlets: createOutlets, dry_run: dryRun, strict,
      });
      setReport(r);
      if (!dryRun && r.applied) {
        toast.success("Menu imported", `${r.created} created, ${r.updated} updated.`);
        data.reloadItems();
        if (r.outlets_created.length) data.reloadOutlets();
      } else if (!dryRun) {
        toast.error("Nothing was imported", "Strict mode stops the whole file when any row fails.");
      }
    } catch (err) {
      toast.error(dryRun ? "Could not check the file" : "Import failed", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const checked = report?.dry_run === true;

  return (
    <div className="cx-stack">
      <Card title="Import a menu" subtitle="Upload a CSV exported from your POS or a spreadsheet. Existing items update by SKU, else by name.">
        <div className="cx-stack" style={{ gap: 12 }}>
          <FileDrop accept=".csv,text/csv,text/plain" maxBytes={MAX_BYTES} onFiles={files => { void onFile(files); }}
                    title={fileName ? `Loaded ${fileName}. Drop another to replace it.` : "Drop a CSV here or tap to choose"}
                    hint="UTF-8 CSV, up to 2,000 rows. Arabic text is fine."
                    onReject={reason => toast.error("File not accepted", reason)} />
          <Field label="Or paste CSV text">
            <Textarea code rows={6} value={csv} placeholder={SAMPLE}
                      onChange={e => { setCsv(e.target.value); setFileName(null); setReport(null); }} />
          </Field>
          <div className="ups-form ups-form--2">
            <Field label="Outlet for rows without one" hint="Leave blank if every row has an outlet column.">
              <Select value={defaultOutlet} onChange={e => { setDefaultOutlet(e.target.value); setReport(null); }}
                      placeholder="None" options={data.outlets.map(o => ({ value: o.key, label: `${o.name_en} (${o.key})` }))} />
            </Field>
            <div className="cx-stack" style={{ gap: 8, justifyContent: "center" }}>
              <label className="ups-check">
                <input type="checkbox" checked={createOutlets} onChange={e => { setCreateOutlets(e.target.checked); setReport(null); }} />
                Create outlets that do not exist yet
              </label>
              <label className="ups-check">
                <input type="checkbox" checked={strict} onChange={e => { setStrict(e.target.checked); setReport(null); }} />
                All or nothing (stop if any row is bad)
              </label>
            </div>
          </div>
          <div className="ups-actions">
            <Button onClick={() => run(true)} loading={busy === "dry"} disabled={!csv.trim() || busy !== null}>Check file</Button>
            <Button variant="primary" onClick={() => run(false)} loading={busy === "apply"}
                    disabled={!csv.trim() || busy !== null || !checked}
                    title={checked ? undefined : "Check the file first"}>
              Import
            </Button>
          </div>
        </div>
      </Card>
      {report && <ImportResult report={report} />}
      <Card title="Columns" subtitle="Headers are not case sensitive. name_en and price are required; outlet unless you pick one above.">
        <div className="ups-csv-sample">{SAMPLE}</div>
        <p className="ups-sub" style={{ marginTop: 8 }}>
          Also accepted: desc_en, desc_ar, category_ar, active, sort. Dietary tags are separated by | or ;
          (vegan, vegetarian, gluten_free, dairy_free, halal, spicy, contains_nuts, contains_pork, contains_shellfish).
          Days take daily, Mon-Fri or Fri-Sun; times are 24-hour hotel time. Alcohol takes yes or no.
        </p>
      </Card>
    </div>
  );
}

// ─── Result ──────────────────────────────────────────────────────────────

function ImportResult({ report }: { report: ImportReport }) {
  return (
    <Card title={report.dry_run ? "Check result" : "Import result"} gold={!report.dry_run && report.applied}>
      <div className="cx-row">
        <Badge tone="neutral">{report.rows} rows</Badge>
        <Badge tone="ok">{report.created} {report.dry_run ? "to create" : "created"}</Badge>
        <Badge tone="info">{report.updated} {report.dry_run ? "to update" : "updated"}</Badge>
        {report.errors.length > 0 && <Badge tone="bad">{report.errors.length} with problems</Badge>}
        {report.outlets_created.length > 0 && (
          <Badge tone="gold">New outlets: {report.outlets_created.join(", ")}</Badge>
        )}
      </div>
      {report.errors.length > 0 && (
        <ul className="ups-errors">
          {report.errors.slice(0, 200).map((e, i) => <li key={i}>Row {e.row}: {e.error}</li>)}
        </ul>
      )}
      {report.dry_run && report.errors.length === 0 && (
        <p className="cx-muted" style={{ marginTop: 10 }}>Everything checks out. Press Import to apply.</p>
      )}
    </Card>
  );
}
