"use client";

/* UploadDropzone: the kit FileDrop plus the two calls every import makes.
   1. The bytes go up through the Files contract (uploadFile: ticketed,
      direct to the API origin, with progress), so a 20 MB statement never
      streams through the Next proxy.
   2. POST /ingest/batches turns the stored file into an import batch; the
      server sniffs, maps and stages it and answers with the batch.
   The host decides which kinds are offered (PMS exports on settings/imports,
   OTA files on Profit Guard); the source list comes from GET /ingest/sources
   so a new alias map appears here without a client change. */

import { useMemo, useState } from "react";
import { api, useApi } from "../lib/api";
import { useHotel } from "../lib/hotel";
import { Field, FileDrop, ProgressBar, Select, useToast, useUpload } from "../ui";
import { errorText, ingestPath, kindLabel } from "./helpers";
import type { Batch, SourcesResponse } from "./types";

export const DEFAULT_ACCEPT = ".csv,.tsv,.txt,.xlsx,.xls,.pdf,.ics";

export interface UploadDropzoneProps {
  /** Kinds the host offers; the first is the default. */
  kinds: readonly string[];
  /** Called with the batch the server made (or found: same bytes imported before). */
  onBatch: (batch: Batch, info: { existing: boolean }) => void;
  /** Files-contract purpose (retention and routing). */
  purpose?: string;
  accept?: string;
  title?: string;
  hint?: string;
  disabled?: boolean;
  /** Show the source picker (default true). Auto-detect stays the default choice. */
  showSource?: boolean;
}

export function UploadDropzone({
  kinds, onBatch, purpose = "ingest.upload", accept = DEFAULT_ACCEPT, title, hint, disabled = false,
  showSource = true,
}: UploadDropzoneProps) {
  const { slug } = useHotel();
  const toast = useToast();
  const upload = useUpload(slug, purpose);
  const sources = useApi<SourcesResponse>(slug ? ingestPath(slug, "/sources") : null, [slug]);
  const [kind, setKind] = useState<string>(kinds[0] ?? "");
  const [source, setSource] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const kindOptions = kinds.map(k => ({ value: k, label: kindLabel(k) }));
  const sourceOptions = useMemo(() => {
    const list = (sources.data?.sources ?? []).filter(s => s.kind === kind);
    return [
      { value: "", label: "Detect from the file" },
      ...list.map(s => ({ value: s.source_code, label: s.unverified ? `${s.label} (unverified layout)` : s.label })),
    ];
  }, [sources.data, kind]);
  const committable = sources.data?.kinds.find(k => k.kind === kind)?.committable;

  const onFiles = async (files: File[]) => {
    const file = files[0];
    if (!file || !kind) return;
    setError(null);
    const started = Date.now();
    const ref = await upload.upload(file);
    // On failure useUpload keeps the ApiError in its own state (rendered below).
    if (!ref) return;
    setCreating(true);
    try {
      const batch = await api.post<Batch>(ingestPath(slug, "/batches"), {
        file_id: ref.id, kind, source_code: source || null,
      });
      // The server answers 200 with the earlier batch for bytes it has seen;
      // apiFetch hides the status, so an older created_at tells us instead.
      const existing = Date.parse(batch.created_at) < started - 120_000;
      if (existing) toast.info("Already imported", `${ref.filename} was uploaded before; opening that import.`);
      onBatch(batch, { existing });
      upload.reset();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setCreating(false);
    }
  };

  const busy = upload.uploading || creating;
  return (
    <div className="ing-drop">
      <div className="ing-drop-meta">
        {kinds.length > 1 && (
          <Field label="What is this file?">
            <Select value={kind} options={kindOptions} disabled={busy || disabled}
                    onChange={e => { setKind(e.target.value); setSource(""); }} />
          </Field>
        )}
        {showSource && (
          <Field label="Exported from">
            <Select value={source} options={sourceOptions} disabled={busy || disabled}
                    onChange={e => setSource(e.target.value)} />
          </Field>
        )}
      </div>
      <FileDrop
        accept={accept}
        disabled={busy || disabled}
        onFiles={onFiles}
        onReject={reason => setError(reason)}
        title={title ?? (busy ? "Working on it..." : `Drop a ${kindLabel(kind).toLowerCase()} file here or tap to choose`)}
        hint={hint ?? "CSV, Excel or PDF, up to 25 MB. Nothing is saved until you review and import it."}
      >
        {upload.uploading && <div style={{ width: "100%", marginTop: 10 }}><ProgressBar value={upload.progress} /></div>}
        {creating && <p className="ing-note" style={{ marginTop: 8 }}>Reading the file and matching its columns...</p>}
      </FileDrop>
      {committable === false && (
        <p className="ing-note" style={{ marginTop: 8 }}>
          You can upload and review this kind now; importing it switches on when the module that uses it is enabled.
        </p>
      )}
      {(error || upload.error) && (
        <div className="ing-bad" role="alert" style={{ marginTop: 10 }}>{error ?? upload.error?.message}</div>
      )}
    </div>
  );
}
