"use client";

/* PdfReview: the source page beside the lines the AI extracted from it,
   plus the printed-total gate (02 spec 2.2 step 5). A PDF statement whose
   lines do not add up to its printed total within one major unit cannot be
   imported until someone fixes the lines or overrides the gate with a
   reason; the override is audited on the server.
   The PDF is fetched as a blob and shown from an object URL: the download
   route answers Content-Disposition: attachment, which an <iframe src> of
   the API URL would turn into a download instead of a preview. */

import { useEffect, useState } from "react";
import { api, apiUrl, hotelPath } from "../lib/api";
import { useHotel } from "../lib/hotel";
import { Badge, Button, Field, Input, MoneyText, useToast } from "../ui";
import { batchPath, errorText, fieldLabel, GATE_LABEL, gateBlocking } from "./helpers";
import { RowsTable } from "./RowsTable";
import type { Batch, GateStatus, KindField } from "./types";

export interface PdfReviewProps {
  batch: Batch;
  fields?: readonly KindField[];
  editable?: boolean;
  onBatch?: (batch: Batch) => void;
}

// ─── The page viewer ───

function usePdfBlob(slug: string, fileId: string | null): { url: string | null; error: string | null } {
  const [state, setState] = useState<{ url: string | null; error: string | null }>({ url: null, error: null });
  useEffect(() => {
    if (!slug || !fileId) return;
    let live = true;
    let made: string | null = null;
    fetch(apiUrl(hotelPath(slug, `/files/${encodeURIComponent(fileId)}`)), { credentials: "same-origin", cache: "no-store" })
      .then(async res => {
        if (!res.ok) throw new Error(res.status === 403 ? "You cannot open the original file." : "The original file could not be loaded.");
        const blob = new Blob([await res.blob()], { type: "application/pdf" });
        made = URL.createObjectURL(blob);
        if (live) setState({ url: made, error: null });
      })
      .catch(err => { if (live) setState({ url: null, error: String((err as Error).message || err) }); });
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [slug, fileId]);
  return state;
}

function PdfViewer({ slug, fileId, page }: { slug: string; fileId: string | null; page: number }) {
  const { url, error } = usePdfBlob(slug, fileId);
  if (error) return <div className="ing-warn">{error}</div>;
  if (!url) return <div className="ing-pdf__frame" aria-busy="true" />;
  // key={page}: most PDF viewers ignore a fragment change on a loaded frame.
  return <iframe key={page} className="ing-pdf__frame" title={`Page ${page} of the uploaded file`} src={`${url}#page=${page}`} />;
}

// ─── The totals gate ───

function TotalsCheck({ batch, editable, onBatch }: { batch: Batch; editable: boolean; onBatch?: (b: Batch) => void }) {
  const { slug } = useHotel();
  const toast = useToast();
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const gate = (batch.gate_status ?? "n/a") as GateStatus;
  const printed = batch.printed_total_minor;
  const parsed = batch.parsed_total_minor;
  const diff = printed != null && parsed != null ? parsed - printed : null;
  const blocking = gateBlocking(batch);

  const override = async () => {
    setSaving(true);
    try {
      const b = await api.post<Batch>(batchPath(slug, batch.id, "/gate-override"), { reason: reason.trim() });
      toast.success("Gate overridden", "The import can go ahead; the reason is on the audit log.");
      setReason("");
      onBatch?.(b);
    } catch (err) {
      toast.error("Couldn't override the gate", errorText(err));
    } finally {
      setSaving(false);
    }
  };

  const tone = gate === "ok" ? "ok" : blocking ? "bad" : "neutral";
  return (
    <div className="cx-stack" style={{ gap: 10 }}>
      <div className="ing-totals">
        <div className="ing-totals__cell">
          <div className="ing-totals__label">Printed total</div>
          <div className="ing-totals__value">{printed != null ? <MoneyText amount={printed} minor currency={batch.currency} /> : "Not found"}</div>
        </div>
        <div className="ing-totals__cell">
          <div className="ing-totals__label">Sum of lines{batch.total_field ? ` (${fieldLabel(batch.total_field).toLowerCase()})` : ""}</div>
          <div className="ing-totals__value">{parsed != null ? <MoneyText amount={parsed} minor currency={batch.currency} /> : "-"}</div>
        </div>
        <div className="ing-totals__cell">
          <div className="ing-totals__label">Difference</div>
          <div className="ing-totals__value">{diff != null ? <MoneyText amount={diff} minor signed currency={batch.currency} /> : "-"}</div>
        </div>
      </div>
      <div className="cx-row">
        <Badge tone={tone} dot>{GATE_LABEL[gate] ?? gate}</Badge>
        {batch.gate_override_at && <Badge tone="warn">Overridden: {batch.gate_override_reason}</Badge>}
      </div>
      {blocking && editable && (
        <div className="ing-bad">
          <p style={{ margin: "0 0 8px" }}>
            Fix the lines on the right until they add up, or import anyway with a reason (for example
            &quot;printed total includes last month&apos;s adjustment&quot;).
          </p>
          <div className="cx-row" style={{ alignItems: "flex-end" }}>
            <Field label="Reason to override" className="ing-grow">
              <Input value={reason} maxLength={1000} onChange={e => setReason(e.target.value)} />
            </Field>
            <Button variant="danger" loading={saving} disabled={reason.trim().length < 3} onClick={override}>Override gate</Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Component ───

export function PdfReview({ batch, fields, editable = false, onBatch }: PdfReviewProps) {
  const { slug } = useHotel();
  const [page, setPage] = useState(1);
  return (
    <div className="ing-pdf">
      <div className="cx-stack" style={{ gap: 8 }}>
        <div className="cx-row" style={{ justifyContent: "space-between" }}>
          <span className="ing-note">Page {page}</span>
          <div className="cx-row">
            <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}>Previous page</Button>
            <Button size="sm" variant="ghost" onClick={() => setPage(p => p + 1)}>Next page</Button>
          </div>
        </div>
        <PdfViewer slug={slug} fileId={batch.file_id} page={page} />
      </div>
      <div className="cx-stack" style={{ gap: 14 }}>
        <TotalsCheck batch={batch} editable={editable} onBatch={onBatch} />
        <RowsTable batch={batch} fields={fields} editable={editable} onBatch={onBatch} onPage={setPage} />
      </div>
    </div>
  );
}
