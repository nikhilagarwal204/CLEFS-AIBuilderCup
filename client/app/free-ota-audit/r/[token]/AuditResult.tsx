"use client";

/**
 * The report page body: loads the audit by token, shows the teaser, the lead
 * unlock form while locked, and the full report once unlocked. The state is
 * decided by _view.viewOf, so the breakdown and the AI snapshot can only
 * render from an unlocked audit.
 *
 * WHY poll while the AI snapshot runs: the snapshot is queued on the first
 * unlock and its runs finish in the worker a minute or two later. A light
 * poll (every 20 s, at most 15 times) fills the section in without a reload.
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { UiStyles } from "../../../ui/core";
import { OTA_AUDIT_PATH } from "../../../lib/seo";
import { errorShape, getAudit } from "../../_lib";
import { FAILURE_TEXT, failureOf, viewOf } from "../../_view";
import type { AuditView, Failure } from "../../_types";
import { OA_STYLES } from "../../styles";
import TeaserCard from "./TeaserCard";
import LeadForm from "./LeadForm";
import Report from "./Report";
import AiSnapshotSection from "./AiSnapshotSection";

const POLL_MS = 20_000;
const POLL_MAX = 15;

export default function AuditResult({ token }: { token: string }) {
  const [audit, setAudit] = useState<AuditView | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [polls, setPolls] = useState(0);
  // Bumped to re-read the audit (the snapshot poll); the effect below owns the fetch.
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    getAudit(token)
      .then((a) => { if (live) { setAudit(a); setFailure(null); } })
      .catch((err) => {
        // A failed poll keeps the report on screen; only the first load shows the failure.
        if (live && tick === 0) setFailure(failureOf(errorShape(err), true));
      });
    return () => { live = false; };
  }, [token, tick]);

  const view = useMemo(() => viewOf(audit, failure), [audit, failure]);
  const snapshotRunning = view.kind === "unlocked" && view.snapshot.kind === "running";

  useEffect(() => {
    if (!snapshotRunning || polls >= POLL_MAX) return;
    const t = window.setTimeout(() => { setPolls((n) => n + 1); setTick((n) => n + 1); }, POLL_MS);
    return () => window.clearTimeout(t);
  }, [snapshotRunning, polls]);

  return (
    <div className="oa">
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: OA_STYLES }} />
      {view.kind === "loading" && <p className="oa-msg" role="status">Loading your audit...</p>}

      {view.kind === "failure" && (
        <div className="oa-card">
          <p className="oa-alert" role="alert">{FAILURE_TEXT[view.failure]}</p>
          <p className="oa-sub"><Link href={OTA_AUDIT_PATH}>Upload a statement</Link></p>
        </div>
      )}

      {view.kind === "empty" && (
        <div className="oa-card">
          <p className="oa-sub">This audit has no result. <Link href={OTA_AUDIT_PATH}>Upload the statement again</Link>.</p>
        </div>
      )}

      {(view.kind === "teaser" || view.kind === "unlocked") && (
        <TeaserCard teaser={view.teaser} hotel={view.kind === "unlocked" ? view.hotel : null} expiresAt={audit?.expires_at ?? null} />
      )}

      {view.kind === "teaser" && (
        <LeadForm token={token} teaser={view.teaser} onUnlocked={(a) => { setAudit(a); setPolls(0); }} />
      )}

      {view.kind === "unlocked" && (
        <>
          <Report teaser={view.teaser} full={view.full} groups={view.groups} />
          <AiSnapshotSection snapshot={view.snapshot} hotel={view.hotel} />
        </>
      )}
    </div>
  );
}
