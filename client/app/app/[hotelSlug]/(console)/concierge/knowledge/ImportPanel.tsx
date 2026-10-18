"use client";

/* Import old knowledge. A hotel from before the structured KB has one
   free-text blob (hotels.knowledge_base) that the concierge reads verbatim.
   Import splits it (AI when on, paragraphs otherwise) into answers that
   wait for approval; approving the import approves them all and switches the
   hotel to "approved answers only". Pasted text or a .txt/.md file takes the
   same path, for a fact sheet a GM already has.

   A queued or running import is polled every 4 s until it settles, so the
   manager sees the answers appear without reloading. */

import { useEffect, useRef, useState } from "react";
import { LuUpload } from "react-icons/lu";
import { api } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button, Card, Field, Segmented, Textarea, useToast } from "../../../../../ui";
import { ccApi, errText } from "../lib";
import type { KbImport, KbMode, KbOverview } from "../types";

const IMPORT_TONE: Record<string, "ok" | "warn" | "bad" | "info" | "neutral"> = {
  queued: "info", running: "info", done: "warn", failed: "bad", approved: "ok",
};
const IMPORT_LABEL: Record<string, string> = {
  queued: "Queued", running: "Splitting", done: "Ready to approve", failed: "Failed", approved: "Approved",
};
const TEXT_MAX = 200_000;

export function ImportPanel({ slug, overview, onChanged }: { slug: string; overview: KbOverview; onChanged: () => void }) {
  const toast = useToast();
  const fmt = useFormat();
  const [busy, setBusy] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const settling = overview.imports.some(i => i.status === "queued" || i.status === "running");

  // Poll while an import is still being split.
  useEffect(() => {
    if (!settling) return;
    const t = window.setInterval(onChanged, 4000);
    return () => window.clearInterval(t);
  }, [settling, onChanged]);

  async function go(label: string, fn: () => Promise<unknown>, done: string) {
    setBusy(label);
    setErr(null);
    try {
      await fn();
      toast.success(done);
      onChanged();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(null);
    }
  }

  const start = (source: "legacy_text" | "text") =>
    go(source, () => api.post<KbImport>(ccApi(slug, "/kb/imports"), source === "text" ? { source, text } : { source }),
      "Import started. The answers appear below as waiting for approval.").then(() => source === "text" && setText(""));

  function readFile(f: File | undefined) {
    if (!f) return;
    if (!/\.(txt|md|csv)$/i.test(f.name) && !f.type.startsWith("text/")) {
      setErr("Choose a plain text file (.txt or .md). For a PDF, copy its text and paste it here.");
      return;
    }
    f.text().then(t => setText(t.slice(0, TEXT_MAX))).catch(() => setErr("Could not read that file."));
  }

  const setMode = (m: KbMode) => go("mode", () => api.put(ccApi(slug, "/kb/mode"), { kb_mode: m }),
    m === "structured" ? "Guests now hear approved answers only." : "Guests hear the old free text again.");

  return (
    <Card title="Import and knowledge mode"
      subtitle="Only approved answers reach guests in approved-answers mode. The old free text is read verbatim, unreviewed.">
      <div className="cx-stack">
        <div className="cc-spread">
          <span className="cc-small">What guests hear</span>
          <Segmented ariaLabel="Knowledge mode" value={overview.kb_mode} onChange={v => void setMode(v)}
            options={[{ value: "structured", label: "Approved answers" }, { value: "legacy_text", label: "Old free text", disabled: !overview.has_legacy_text }]} />
        </div>
        {overview.has_legacy_text && (
          <div className="cc-spread">
            <span className="cc-sub" style={{ margin: 0 }}>Split the hotel&apos;s old free text into answers to approve.</span>
            <Button variant="secondary" loading={busy === "legacy_text"} disabled={!!busy} onClick={() => void start("legacy_text")}>
              Import old knowledge
            </Button>
          </div>
        )}
        <Field label="Or paste a fact sheet" hint="Plain text, up to 200,000 characters. Each paragraph becomes an answer to review.">
          <Textarea rows={4} value={text} onChange={e => setText(e.target.value.slice(0, TEXT_MAX))} />
        </Field>
        <div className="cx-row">
          <Button variant="secondary" loading={busy === "text"} disabled={!!busy || !text.trim()} onClick={() => void start("text")}>Import text</Button>
          <Button variant="ghost" icon={<LuUpload />} onClick={() => fileRef.current?.click()}>Load a text file</Button>
          <input ref={fileRef} type="file" accept=".txt,.md,.csv,text/plain" hidden onChange={e => readFile(e.target.files?.[0])} />
        </div>
        {err && <div className="cc-callout" role="alert">{err}</div>}
        {overview.imports.length > 0 && (
          <div className="cx-stack" style={{ gap: 8 }}>
            <span className="cc-label">Recent imports</span>
            {overview.imports.map(i => (
              <div key={i.id} className="cc-spread cc-small">
                <span className="cx-row">
                  <Badge tone={IMPORT_TONE[i.status] ?? "neutral"} dot>{IMPORT_LABEL[i.status] ?? i.status}</Badge>
                  <span>{i.source === "legacy_text" ? "Old free text" : "Pasted text"}</span>
                  <span className="cx-muted">{i.entries_created ?? 0} answers, {fmt.relative(i.created_at)}</span>
                  {i.error && <span className="cx-muted">{i.error}</span>}
                </span>
                {i.status === "done" && (
                  <Button size="sm" loading={busy === `a${i.id}`} disabled={!!busy}
                    onClick={() => void go(`a${i.id}`, () => api.post(ccApi(slug, `/kb/imports/${i.id}/approve`)),
                      "All answers from this import are live, and guests hear approved answers only.")}>
                    Approve all
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
