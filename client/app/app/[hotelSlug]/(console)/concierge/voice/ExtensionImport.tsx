"use client";

/* Bulk mapping, the two ways 03 spec 3.6 step 3 names:
     - a CSV "extension,room[,kind[,location]]" (header optional), bad rows
       skipped and listed, good rows kept;
     - a rule "extension = prefix + room + suffix" over the room register,
       previewed first (dry run) so the manager sees 504 -> 2504 before any
       row is written. Replace drops only rows from the same source, so
       hand-added exceptions survive a re-import. */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { Button, Field, Input, Segmented, Textarea } from "../../../../../ui";
import { errText, vcApi } from "../lib";
import type { CsvResult, RuleResult, VoiceLine } from "../types";

export function ExtensionImport({ slug, line, onDone }: { slug: string; line: VoiceLine; onDone: () => void }) {
  const [mode, setMode] = useState<"rule" | "csv">("rule");
  const rule0 = line.extension_rule;
  const [prefix, setPrefix] = useState(rule0?.prefix ?? "");
  const [suffix, setSuffix] = useState(rule0?.suffix ?? "");
  const [strip, setStrip] = useState(rule0?.strip_room_prefix ?? "");
  const [useRoomExt, setUseRoomExt] = useState(!!rule0?.use_room_extension);
  const [csv, setCsv] = useState("");
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<RuleResult | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const base = vcApi(slug, `/lines/${line.id}/extensions`);
  const clean = (s: string) => s.replace(/[^0-9A-Za-z*#]/g, "").slice(0, 8);

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setMsg(null);
    try { await fn(); } catch (e) { setMsg({ tone: "bad", text: errText(e) }); } finally { setBusy(null); }
  }
  const ruleBody = (dry: boolean) => ({ prefix, suffix, strip_room_prefix: strip, use_room_extension: useRoomExt, replace, dry_run: dry });

  const previewRule = () => run("preview", async () => setPreview(await api.post<RuleResult>(`${base}/rule`, ruleBody(true))));
  const applyRule = () => run("apply", async () => {
    const r = await api.post<RuleResult>(`${base}/rule`, ruleBody(false));
    setPreview(null);
    setMsg({ tone: "ok", text: `${r.created ?? 0} added and ${r.updated ?? 0} updated across ${r.rooms} rooms.` });
    onDone();
  });
  const importCsv = () => run("csv", async () => {
    const r = await api.post<CsvResult>(`${base}/import`, { csv, replace });
    const skipped = r.skipped.slice(0, 5).map(s => `line ${s.line} (${s.reason})`).join(", ");
    setMsg({ tone: r.skipped_count ? "bad" : "ok",
      text: `${r.imported} imported.${r.skipped_count ? ` ${r.skipped_count} skipped: ${skipped}${r.skipped_count > 5 ? ", ..." : ""}` : ""}` });
    if (r.imported) { setCsv(""); onDone(); }
  });

  return (
    <div className="cx-stack" style={{ gap: 10 }}>
      <div className="cc-spread">
        <span className="cc-label">Map many at once</span>
        <Segmented size="sm" ariaLabel="Bulk method" value={mode} onChange={setMode}
          options={[{ value: "rule", label: "Rule" }, { value: "csv", label: "CSV" }]} />
      </div>
      {mode === "rule" ? (
        <>
          <div className="cc-fields" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))" }}>
            <Field label="Prefix" hint="2 makes 504 into 2504"><Input className="cc-mono" value={prefix} onChange={e => setPrefix(clean(e.target.value))} /></Field>
            <Field label="Suffix"><Input className="cc-mono" value={suffix} onChange={e => setSuffix(clean(e.target.value))} /></Field>
            <Field label="Drop from room" hint="Leading part of the room number to remove"><Input className="cc-mono" value={strip} onChange={e => setStrip(clean(e.target.value))} /></Field>
          </div>
          <label className="cc-check">
            <input type="checkbox" checked={useRoomExt} onChange={e => setUseRoomExt(e.target.checked)} />
            <span>Use the extension stored in the room register when a room has one</span>
          </label>
        </>
      ) : (
        <Field label="CSV" hint="extension,room per line, for example 2504,504. Add kind and location columns for venues.">
          <Textarea rows={5} className="cc-mono" value={csv} onChange={e => setCsv(e.target.value)} placeholder={"extension,room\n2504,504\n2505,505"} />
        </Field>
      )}
      <label className="cc-check">
        <input type="checkbox" checked={replace} onChange={e => setReplace(e.target.checked)} />
        <span>Replace earlier {mode === "rule" ? "rule" : "CSV"} rows (hand-added ones stay)</span>
      </label>
      <div className="cx-row">
        {mode === "rule" ? (
          <>
            <Button size="sm" variant="secondary" loading={busy === "preview"} disabled={!!busy} onClick={() => void previewRule()}>Preview</Button>
            <Button size="sm" loading={busy === "apply"} disabled={!!busy || !preview} onClick={() => void applyRule()}>Apply to {preview?.would_map ?? 0} rooms</Button>
          </>
        ) : (
          <Button size="sm" loading={busy === "csv"} disabled={!!busy || !csv.trim()} onClick={() => void importCsv()}>Import CSV</Button>
        )}
      </div>
      {preview && mode === "rule" && (
        <p className="cc-small cx-muted">
          {preview.would_map ?? 0} of {preview.rooms} rooms get an extension, for example{" "}
          <span className="cc-mono">{preview.preview.slice(0, 6).map(p => `${p.room} -> ${p.extension}`).join(", ")}</span>
        </p>
      )}
      {msg && <div className={msg.tone === "bad" ? "cc-callout" : "cc-callout cc-callout--info"} role="status">{msg.text}</div>}
    </div>
  );
}
