"use client";

/* RoomsCsvImport: the room list as a spreadsheet, read in the browser.
   A room register has no money and no guest data, so it skips the server
   ingest pipeline (batches, templates, commit handlers) and goes straight
   to PUT /rooms after a preview. The preview says which rooms are new and
   which update an existing room, and lists every line it could not read,
   so nothing is silently dropped. Empty cells never blank a stored value. */

import { useState } from "react";
import { LuDownload } from "react-icons/lu";
import { Badge, Button, FileDrop } from "../../../../../ui";
import { roomsFromCsv, TEMPLATE_CSV, type CsvRooms, type RoomInput } from "./rooms";

export interface RoomsCsvImportProps {
  existing: ReadonlySet<string>;
  saving: boolean;
  /** Resolves true once saved; the preview then clears. */
  onImport: (rooms: RoomInput[]) => Promise<boolean>;
}

function downloadTemplate() {
  const url = URL.createObjectURL(new Blob([TEMPLATE_CSV], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "clefs-rooms-template.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

export function RoomsCsvImport({ existing, saving, onImport }: RoomsCsvImportProps) {
  const [parsed, setParsed] = useState<(CsvRooms & { name: string }) | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onFiles = async (files: File[]) => {
    const f = files[0];
    if (!f) return;
    setError(null);
    if (/\.(xlsx|xls)$/i.test(f.name)) {
      setError("Save the sheet as CSV first (File > Save as > CSV), then drop it here.");
      return;
    }
    try {
      setParsed({ ...roomsFromCsv(await f.text()), name: f.name });
    } catch {
      setError("The file could not be read as text.");
    }
  };

  const fresh = parsed?.rooms.filter(r => !existing.has(r.room_number)).length ?? 0;
  const updates = (parsed?.rooms.length ?? 0) - fresh;

  return (
    <div className="cx-stack" style={{ gap: 12 }}>
      <div className="cx-row" style={{ justifyContent: "space-between" }}>
        <p className="rms-note">
          Columns are matched by name: Room number (required), Floor, Wing, Bedrooms, Extension, Status.
        </p>
        <Button size="sm" variant="ghost" icon={<LuDownload aria-hidden="true" />} onClick={downloadTemplate}>Template</Button>
      </div>
      <FileDrop accept=".csv,.tsv,.txt,.xlsx,.xls" maxBytes={2 * 1024 * 1024} onFiles={files => void onFiles(files)}
                onReject={r => setError(r)} title="Drop your room list (CSV) here or tap to choose" />
      {error && <div className="rms-bad" role="alert">{error}</div>}

      {parsed && (
        <div className="cx-stack" style={{ gap: 10 }}>
          <div className="cx-row">
            <strong>{parsed.name}</strong>
            <Badge tone="ok">{fresh} new</Badge>
            {updates > 0 && <Badge tone="info">{updates} updates</Badge>}
            {parsed.errors.length > 0 && <Badge tone="bad">{parsed.errors.length} lines skipped</Badge>}
            {parsed.duplicates.length > 0 && <Badge tone="warn">{parsed.duplicates.length} repeated</Badge>}
          </div>
          <p className="rms-note">
            Read {Object.entries(parsed.columns).map(([k, h]) => `${k.replace(/_/g, " ")} from "${h}"`).join(", ") || "no known columns"}.
            {parsed.ignoredType && ` The "${parsed.ignoredType}" column is not imported: room types come from your PMS.`}
          </p>
          {parsed.errors.length > 0 && (
            <ul className="rms-errors">
              {parsed.errors.slice(0, 12).map(e => <li key={`${e.line}-${e.message}`}>Line {e.line}: {e.message}</li>)}
              {parsed.errors.length > 12 && <li>and {parsed.errors.length - 12} more</li>}
            </ul>
          )}
          {parsed.duplicates.length > 0 && (
            <p className="rms-note">Listed twice, first line kept: {parsed.duplicates.slice(0, 10).join(", ")}</p>
          )}
          <div className="cx-row" style={{ justifyContent: "flex-end" }}>
            <Button variant="ghost" onClick={() => setParsed(null)}>Clear</Button>
            <Button loading={saving} disabled={!parsed.rooms.length}
                    onClick={() => void onImport(parsed.rooms).then(ok => { if (ok) setParsed(null); })}>
              Import {parsed.rooms.length} {parsed.rooms.length === 1 ? "room" : "rooms"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
