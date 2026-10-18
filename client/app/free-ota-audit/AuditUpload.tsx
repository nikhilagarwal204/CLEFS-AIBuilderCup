"use client";

/**
 * /free-ota-audit upload (02 spec 3.11, Canon 23): one statement, the OTA
 * (or "detect it"), a required processing tick, and a required transfer
 * tick for every format (the database is in Singapore; a PDF's text is also
 * read by the AI provider in the United States). The limits are shown
 * BEFORE the file is chosen, and a file over them is refused in the browser
 * so nobody waits on a 4 MB upload the server will reject. On success the
 * visitor moves to /free-ota-audit/r/<token>, where the teaser and the lead
 * unlock live, so the result has a URL that survives a reload.
 */

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button, UiStyles } from "../ui/core";
import { Field, Select } from "../ui/forms";
import { FileDrop } from "../ui/upload";
import { OTA_AUDIT_PATH } from "../lib/seo";
import { errorShape, uploadAudit } from "./_lib";
import {
  ACCEPT, CONSENT_PROCESSING, CONSENT_TRANSFER, FAILURE_TEXT, MAX_BYTES, failureOf, precheckFile,
} from "./_view";
import type { Failure, OtaCode } from "./_types";
import { OA_STYLES } from "./styles";

// ─── Component ───────────────────────────────────────────────────────────────

type Phase = "idle" | "uploading" | "checking";

export default function AuditUpload() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [ota, setOta] = useState<OtaCode | "">("");
  const [consent, setConsent] = useState(false);
  const [transfer, setTransfer] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const busy = phase !== "idle";

  function choose(files: File[]) {
    const f = files[0];
    if (!f) return;
    setFailure(null);
    const why = precheckFile(f.name, f.size);
    setMessage(why);
    setFile(why ? null : f);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setMessage(null);
    setFailure(null);
    if (!file) { setMessage("Choose the statement file first."); return; }
    if (!consent) { setMessage("Tick the box so Clefs can process the statement."); return; }
    // Every format leaves the UAE (database in Singapore; a PDF also goes to
    // the AI provider in the US), so the transfer tick is always required.
    if (!transfer) { setMessage(FAILURE_TEXT.transfer); return; }
    setPhase("uploading");
    setProgress(0);
    try {
      const res = await uploadAudit(file, ota, (pct) => {
        setProgress(pct);
        // The bytes are sent; the server now parses and runs the checks.
        if (pct >= 100) setPhase("checking");
      }, transfer);
      setPhase("checking");
      router.push(`${OTA_AUDIT_PATH}/r/${encodeURIComponent(res.token)}`);
    } catch (err) {
      setFailure(failureOf(errorShape(err)));
      setPhase("idle");
    }
  }

  return (
    <div className="oa" id="upload">
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: OA_STYLES }} />
      <form className="oa-card oa-card--gold" onSubmit={onSubmit} noValidate aria-busy={busy}>
        <h2 className="oa-h">Upload one statement</h2>
        <ul className="oa-limits" aria-label="Limits">
          <li><strong>CSV, Excel or PDF</strong>Booking.com, Agoda or Expedia statement</li>
          <li><strong>Up to 4 MB</strong>and 5,000 rows, or a PDF of 10 pages</li>
          <li><strong>Three audits a day</strong>from one connection</li>
          <li><strong>Deleted after 30 days</strong>file, rows and report</li>
        </ul>

        <Field label="Which OTA is it from?">
          <Select value={ota} onChange={(e) => setOta(e.target.value as OtaCode | "")} disabled={busy}>
            <option value="">Detect it from the file</option>
            <option value="BOOKING">Booking.com</option>
            <option value="AGODA">Agoda</option>
            <option value="EXPEDIA">Expedia</option>
          </Select>
        </Field>

        {file ? (
          <div className="oa-file">
            <span>{file.name}</span>
            <Button type="button" variant="ghost" onClick={() => setFile(null)} disabled={busy}>Change file</Button>
          </div>
        ) : (
          <FileDrop
            onFiles={choose}
            accept={ACCEPT}
            maxBytes={MAX_BYTES}
            disabled={busy}
            title="Drop the statement here or tap to choose"
            hint="The monthly commission statement or invoice export from the extranet"
            onReject={() => setMessage(FAILURE_TEXT.too_large)}
          />
        )}

        <fieldset className="oa-ticks">
          <legend className="oa-sr">Consent</legend>
          <label>
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} disabled={busy} />
            <span>{CONSENT_PROCESSING} (required)</span>
          </label>
          <label>
            <input type="checkbox" checked={transfer} onChange={(e) => setTransfer(e.target.checked)} disabled={busy} />
            <span>
              {CONSENT_TRANSFER} (<Link href="/subprocessors" target="_blank">sub-processors</Link>) (required)
            </span>
          </label>
        </fieldset>

        {phase === "uploading" && (
          <div className="oa-progress" role="progressbar" aria-label="Upload" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
            <i style={{ width: `${progress}%` }} />
          </div>
        )}
        {phase === "checking" && <p className="oa-msg" role="status">Checking every line of the statement...</p>}
        {message && <p className="oa-msg" role="alert">{message}</p>}
        {failure && <p className="oa-alert" role="alert">{FAILURE_TEXT[failure]}</p>}

        <Button type="submit" variant="primary" loading={busy} disabled={busy}>Run the free audit</Button>
        <p className="oa-note">
          No account and no extranet login needed. We ask for your name and hotel only after you see
          the result. See <Link href="/security">security</Link> and the <Link href="/subprocessors">sub-processors</Link>.
        </p>
      </form>
    </div>
  );
}
