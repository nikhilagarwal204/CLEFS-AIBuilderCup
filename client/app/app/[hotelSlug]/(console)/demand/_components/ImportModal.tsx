"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One import dialog for the demand text files: ICS calendars
   (POST /events/import-ics), BAR sheets (POST /bar/import) and rate-shop
   exports (POST /comp-rates/import). The file is read in the browser and
   sent as JSON {content, filename}, which every one of those routes
   accepts; the 2 MB cap matches the server's 413. Pasting the text works
   too (a calendar copied from an email).
   ═══════════════════════════════════════════════════════════════════════ */

import { useState, type ReactNode } from "react";
import { api } from "../../../../../lib/api";
import { Button, Field, FileDrop, Modal, Textarea, useToast } from "../../../../../ui";
import { errorText } from "../_lib/paths";

export const IMPORT_MAX_BYTES = 2 * 1024 * 1024;

export default function ImportModal<R>({
  open, onClose, title, intro, endpoint, accept, extraBody, describe, onDone, children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  intro: ReactNode;
  endpoint: string;
  accept: string;
  extraBody?: Record<string, unknown>;
  /** One sentence for the success toast. */
  describe: (r: R) => string;
  onDone: (r: R) => void;
  /** Extra controls (e.g. a source picker) above the drop zone. */
  children?: ReactNode;
}) {
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const close = () => {
    if (busy) return;
    setFile(null);
    setText("");
    onClose();
  };

  const send = async () => {
    setBusy(true);
    try {
      const content = file ? await file.text() : text;
      const r = await api.post<R>(endpoint, { ...extraBody, content, ...(file ? { filename: file.name } : {}) });
      toast.success("Imported", describe(r));
      onDone(r);
      setFile(null);
      setText("");
      onClose();
    } catch (err) {
      toast.error("Import failed", errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      dismissible={!busy}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={!file && !text.trim()} onClick={send}>Import</Button>
        </>
      }
    >
      <div className="cx-stack" style={{ gap: 12 }}>
        <p className="dr-note">{intro}</p>
        {children}
        <FileDrop
          accept={accept}
          maxBytes={IMPORT_MAX_BYTES}
          disabled={busy}
          title={file ? file.name : "Drop the file here or tap to choose"}
          hint={file ? `${Math.max(1, Math.round(file.size / 1024))} KB. Tap to pick another.` : "Up to 2 MB"}
          onFiles={files => { setFile(files[0] ?? null); setText(""); }}
          onReject={reason => toast.error("File not accepted", reason)}
        />
        {!file && (
          <Field label="Or paste the contents">
            <Textarea code rows={5} value={text} disabled={busy} onChange={e => setText(e.target.value)} />
          </Field>
        )}
      </div>
    </Modal>
  );
}
