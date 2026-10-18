"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One privacy request: who asked, what for, and the three steps the PDPL
   workflow allows. Verify first (the server refuses to execute an
   unverified request, 409 dsr_not_verified), then execute or close.
   Erase is irreversible, so it needs an explicit tick that sends
   confirm: true; rectify needs a note saying what was corrected.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, apiUrl, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button, Drawer, EmptyState, Field, Select, Skeleton, Textarea, useToast } from "../../../../../ui";
import { DSR_STATUS, DSR_TYPE_LABEL, errText, trustApi, VERIFY_METHODS } from "../lib";
import type { Dsr, SignedLink, VerifyMethod } from "../types";

// ─── Drawer ──────────────────────────────────────────────────────────────

export default function DsrDrawer({ slug, id, onClose, onChanged }: {
  slug: string;
  id: number | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const fmt = useFormat();
  const res = useApi<Dsr>(id ? trustApi(slug, `/dsr/${id}`) : null, [slug, id]);
  const d = res.data && res.data.id === id ? res.data : undefined;

  const updated = (next: Dsr) => { res.setData(next); onChanged(); };

  return (
    <Drawer open={id !== null} onClose={onClose} wide
            title={d ? `${DSR_TYPE_LABEL[d.type] ?? d.type} request #${d.id}` : "Privacy request"}>
      {res.error ? (
        <EmptyState title="Could not load this request" body={res.error.message} />
      ) : !d ? (
        <Skeleton lines={8} />
      ) : (
        <div className="cx-stack" style={{ gap: 14 }}>
          <div className="cx-row" style={{ gap: 8, flexWrap: "wrap" }}>
            <Badge tone={DSR_STATUS[d.status]?.tone ?? "neutral"}>{DSR_STATUS[d.status]?.label ?? d.status}</Badge>
            {d.overdue && <Badge tone="bad">Overdue</Badge>}
            {!d.overdue && d.days_left !== null && !isClosed(d) && <Badge tone="neutral">{d.days_left} days left</Badge>}
          </div>
          <dl className="trs-kv">
            <dt>Received</dt><dd>{fmt.dateTime(d.received_at, "medium")} via {d.channel.replace(/_/g, " ")}</dd>
            <dt>Due</dt><dd>{fmt.date(d.due_at, "long")}</dd>
            {d.language && <><dt>Reply language</dt><dd>{d.language}</dd></>}
            {d.handled_by && <><dt>Handled by</dt><dd>{d.handled_by}</dd></>}
            {d.completed_at && <><dt>Closed</dt><dd>{fmt.dateTime(d.completed_at, "medium")}</dd></>}
          </dl>

          <div>
            <div className="trs-section-title">Who is asking</div>
            <dl className="trs-kv">
              {Object.entries(d.identifiers ?? {}).filter(([, v]) => v).map(([k, v]) => (
                <FragmentKV key={k} k={k.replace(/_/g, " ")} v={String(v)} />
              ))}
            </dl>
          </div>
          {d.details && (
            <div>
              <div className="trs-section-title">What they asked for</div>
              <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{d.details}</p>
            </div>
          )}
          {d.verification?.method && (
            <div>
              <div className="trs-section-title">Verification</div>
              <p style={{ margin: 0 }}>
                {VERIFY_METHODS.find(m => m.value === d.verification?.method)?.label ?? d.verification.method}
                {d.verification.verified_by ? ` by ${d.verification.verified_by}` : ""}
                {d.verification.verified_at ? `, ${fmt.dateTime(d.verification.verified_at, "medium")}` : ""}
              </p>
              {d.verification.note && <p className="trs-small" style={{ margin: "4px 0 0" }}>{d.verification.note}</p>}
            </div>
          )}
          <ResultBlock slug={slug} d={d} />
          {d.note && (
            <div>
              <div className="trs-section-title">Note</div>
              <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{d.note}</p>
            </div>
          )}
          {/* Keyed by id and status: a new request, or the same one after a
              step, remounts with a clean form. */}
          {!isClosed(d) && <Workflow key={`${d.id}:${d.status}`} slug={slug} d={d} onUpdated={updated} />}
        </div>
      )}
    </Drawer>
  );
}

function isClosed(d: Dsr) {
  return d.status === "completed" || d.status === "rejected" || d.status === "cancelled";
}

function FragmentKV({ k, v }: { k: string; v: string }) {
  return <><dt style={{ textTransform: "capitalize" }}>{k}</dt><dd>{v}</dd></>;
}

// ─── Result (completed requests) ─────────────────────────────────────────

function ResultBlock({ slug, d }: { slug: string; d: Dsr }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  if (!d.result && !d.result_upload_id) return null;

  // The signed link is minted on click (7-day validity) rather than stored,
  // so an old drawer never offers an expired link.
  async function download() {
    setBusy(true);
    try {
      const link = await api.get<SignedLink>(trustApi(slug, `/dsr/${d.id}/download`));
      // Same tab on purpose: a new tab opened after an await is often popup
      // blocked, and a zip downloads without leaving this page.
      window.location.assign(apiUrl(link.url));
    } catch (e) {
      toast.error("Could not open the bundle", errText(e));
    } finally {
      setBusy(false);
    }
  }

  const counts = Object.entries(d.result ?? {}).filter(([, v]) => typeof v === "number" || typeof v === "string");
  return (
    <div>
      <div className="trs-section-title">What was done</div>
      {counts.length > 0 && (
        <dl className="trs-kv">
          {counts.map(([k, v]) => <FragmentKV key={k} k={k.replace(/_/g, " ")} v={String(v)} />)}
        </dl>
      )}
      {d.result_upload_id && (
        <div style={{ marginTop: 10 }}>
          <Button variant="secondary" size="sm" loading={busy} onClick={download}>Download the bundle for the guest</Button>
          <p className="trs-small" style={{ margin: "6px 0 0", color: "var(--clefs-ink-muted)" }}>
            A zip with subject.json and one CSV per section. Send it through a channel you have verified.
          </p>
        </div>
      )}
    </div>
  );
}

// ─── Workflow (verify, execute, close) ───────────────────────────────────

function Workflow({ slug, d, onUpdated }: { slug: string; d: Dsr; onUpdated: (d: Dsr) => void }) {
  const toast = useToast();
  const [method, setMethod] = useState<VerifyMethod>("matched_reservation");
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [closeAs, setCloseAs] = useState<"rejected" | "cancelled">("rejected");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const verified = d.status === "verified" || d.status === "in_progress";
  const needsNote = d.type === "rectify";
  const needsConfirm = d.type === "erase";

  async function run(step: "verify" | "execute" | "close") {
    setBusy(step); setErr(null);
    const body =
      step === "verify" ? { method, note: note.trim() || undefined }
      : step === "execute" ? { note: note.trim() || undefined, confirm: needsConfirm ? confirm : undefined }
      : { status: closeAs, note: note.trim() || undefined };
    try {
      const next = await api.post<Dsr>(trustApi(slug, `/dsr/${d.id}/${step}`), body);
      toast.success(step === "verify" ? "Identity verified" : step === "execute" ? "Request completed" : "Request closed");
      onUpdated(next);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="cx-card" style={{ padding: 14 }}>
      <div className="trs-section-title" style={{ marginTop: 0 }}>{verified ? "Act on the request" : "Step 1: verify who is asking"}</div>
      <div className="cx-stack" style={{ gap: 10 }}>
        {!verified && (
          <Field label="How you checked" htmlFor="dsr-method">
            <Select id="dsr-method" value={method} onChange={e => setMethod(e.target.value as VerifyMethod)} options={VERIFY_METHODS} />
          </Field>
        )}
        <Field
          label={verified && needsNote ? "What you corrected (required)" : "Note"}
          htmlFor="dsr-note"
          hint={verified ? "Kept with the request as evidence." : "For example, the reservation number you matched."}
        >
          <Textarea id="dsr-note" rows={2} value={note} maxLength={2000} onChange={e => setNote(e.target.value)} />
        </Field>
        {verified && needsConfirm && (
          <label className="cx-row" style={{ gap: 8, alignItems: "flex-start" }}>
            <input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} style={{ marginTop: 3 }} />
            <span>
              I understand erasure cannot be undone. Transcripts, request text and the stay&apos;s guest name become
              &quot;[erased]&quot;, contact rows are removed and every consent is withdrawn.
            </span>
          </label>
        )}
        {err && <div role="alert" style={{ color: "var(--cx-bad)" }}>{err}</div>}
        <div className="cx-row" style={{ gap: 8, flexWrap: "wrap" }}>
          {!verified ? (
            <Button loading={busy === "verify"} disabled={!!busy} onClick={() => run("verify")}>Mark verified</Button>
          ) : (
            <Button
              variant={needsConfirm ? "danger" : "primary"}
              loading={busy === "execute"}
              disabled={!!busy || (needsConfirm && !confirm) || (needsNote && !note.trim())}
              onClick={() => run("execute")}
            >
              {EXECUTE_LABEL[d.type]}
            </Button>
          )}
          <span style={{ flex: 1 }} />
          <Select aria-label="Close as" value={closeAs} onChange={e => setCloseAs(e.target.value as "rejected" | "cancelled")}
                  options={[{ value: "rejected", label: "Reject" }, { value: "cancelled", label: "Withdrawn by guest" }]} />
          <Button variant="secondary" loading={busy === "close"} disabled={!!busy} onClick={() => run("close")}>Close</Button>
        </div>
      </div>
    </div>
  );
}

const EXECUTE_LABEL: Record<Dsr["type"], string> = {
  access: "Build the data copy",
  portability: "Build the machine-readable export",
  rectify: "Record the correction",
  erase: "Erase this person's data",
  restrict: "Restrict marketing and AI analytics",
  object: "Stop processing (incl. profile memory)",
};
