"use client";

/* Room register import (03 spec 1.7 "rooms/import": room, floor, wing,
   type, extension). Parsed and previewed in the browser, then written
   through the foundation register (PUT /hotels/{slug}/rooms, a partial
   upsert), so routing gets floors before the first request arrives. */

import { useState } from "react";
import { useSession } from "../../../../../lib/session";
import { Button, Card, Table, useToast, type Column } from "../../../../../ui";
import { errorText } from "../_components/serviceApi";
import { importRooms, parseRoomsCsv, type RoomImportRow } from "../_components/configApi";

const COLUMNS: Column<RoomImportRow>[] = [
  { key: "room_number", header: "Room" },
  { key: "floor", header: "Floor", render: r => r.floor ?? <span className="svc-faint">from number</span> },
  { key: "wing", header: "Wing", render: r => r.wing ?? "" },
  { key: "room_type_code", header: "Type", render: r => r.room_type_code ?? "" },
  { key: "extension", header: "Extension", render: r => r.extension ?? "" },
];

export default function RoomsImport({ slug }: { slug: string }) {
  const { can } = useSession();
  const toast = useToast();
  const [rows, setRows] = useState<RoomImportRow[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [fileName, setFileName] = useState("");
  const [saving, setSaving] = useState(false);
  const canWrite = can("core.settings.manage");

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { setErrors(["The file is over 2 MB; a room list is far smaller."]); return; }
    setFileName(file.name);
    const parsed = parseRoomsCsv(await file.text());
    setRows(parsed.rows);
    setErrors(parsed.errors);
  };

  const save = async () => {
    setSaving(true);
    try {
      const out = await importRooms(slug, rows);
      toast.success(`${out.count} rooms saved.`);
      setRows([]); setErrors([]); setFileName("");
    } catch (err) {
      toast.error("Could not save the rooms", errorText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="Rooms" subtitle="Upload a CSV with a header row: room, floor, wing, type, extension. Floors route housekeeping and engineering by zone.">
      <div className="svc-stack">
        <label className="svc-row">
          <input type="file" accept=".csv,text/csv,text/plain" disabled={!canWrite}
                 onChange={e => void onFile(e.target.files?.[0])} aria-label="Room list CSV" />
          {fileName && <span className="svc-faint">{fileName}</span>}
        </label>
        {!canWrite && <p className="svc-muted">Only an owner or GM can change the room register.</p>}
        {errors.length > 0 && (
          <ul className="svc-muted" style={{ margin: 0, paddingLeft: 18 }}>
            {errors.slice(0, 8).map(e => <li key={e}>{e}</li>)}
            {errors.length > 8 && <li>and {errors.length - 8} more</li>}
          </ul>
        )}
        {rows.length > 0 && (
          <>
            <Table columns={COLUMNS} rows={rows.slice(0, 50)} rowKey={r => r.room_number} caption="Rooms to import" />
            {rows.length > 50 && <p className="svc-faint">Showing 50 of {rows.length}.</p>}
            <div className="svc-row">
              <Button variant="primary" onClick={() => void save()} loading={saving}>Save {rows.length} rooms</Button>
              <Button variant="ghost" onClick={() => { setRows([]); setErrors([]); setFileName(""); }}>Discard</Button>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}
