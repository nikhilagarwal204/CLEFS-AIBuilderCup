"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Hash-chain status for the audit log. The nightly job (03:30 hotel time)
   verifies incrementally from a checkpoint and re-walks everything weekly;
   "Verify now" runs the same check on demand, "Full re-walk" ignores the
   checkpoint. A break names the first row whose hash does not match, which
   is where an investigator starts.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Button, Skeleton, useToast } from "../../../../../ui";
import { errText, shortHash, trustApi } from "../lib";
import type { ChainState, ChainVerifyResult } from "../types";

export default function ChainBanner({ slug, onOpenRow }: { slug: string; onOpenRow: (id: number) => void }) {
  const fmt = useFormat();
  const toast = useToast();
  const { data, loading, reload, error } = useApi<ChainState>(trustApi(slug, "/audit/chain"), [slug]);
  const [busy, setBusy] = useState<"" | "inc" | "full">("");

  async function verify(full: boolean) {
    setBusy(full ? "full" : "inc");
    try {
      const r = await api.post<ChainVerifyResult>(trustApi(slug, "/audit/chain/verify"), undefined, { query: { full } });
      if (r.status === "ok") toast.success("Chain intact", `${fmt.number(r.rows_checked)} rows checked.`);
      else toast.error("Chain break found", `First bad row #${r.broken_at_id ?? "?"}: ${r.reason ?? "hash mismatch"}`);
      reload();
    } catch (e) {
      toast.error("Verification failed", errText(e));
    } finally {
      setBusy("");
    }
  }

  if (loading && !data) return <Skeleton height={72} radius={14} />;
  if (error && !data) return null;
  if (!data) return null;

  const tone = data.status === "ok" ? "ok" : data.status === "broken" ? "bad" : "warn";
  const icon = data.status === "ok" ? "✓" : data.status === "broken" ? "!" : "?";
  return (
    <div className={`trs-banner trs-banner--${tone}`} role="status">
      <span className="trs-banner__icon" aria-hidden="true">{icon}</span>
      <div className="trs-banner__body">
        <div className="trs-banner__title">
          {data.status === "ok" && "Audit log intact: every row matches its hash and links to the one before."}
          {data.status === "broken" && "Audit log break detected. A row was altered or removed outside Clefs."}
          {data.status === "unverified" && "The audit log has not been verified yet."}
        </div>
        <div className="cx-muted trs-small">
          {data.verified_at ? `Last checked ${fmt.dateTime(data.verified_at)}` : "Checked nightly at 03:30"}
          {data.rows_checked !== null && ` · ${fmt.number(data.rows_checked)} rows`}
          {data.last_full_at && ` · last full walk ${fmt.date(data.last_full_at)}`}
          {data.last_hash && <> · head <span className="trs-mono" title={data.last_hash}>{shortHash(data.last_hash)}</span></>}
        </div>
        {data.status === "broken" && data.broken_at_id !== null && (
          <div className="trs-small" style={{ marginTop: 6 }}>
            First bad row{" "}
            <button type="button" className="cx-btn cx-btn--ghost cx-btn--sm" onClick={() => onOpenRow(data.broken_at_id!)}>
              #{data.broken_at_id}
            </button>
            {data.broken_reason ? ` (${data.broken_reason})` : ""}. The owner has been alerted.
          </div>
        )}
      </div>
      <div className="cx-row trs-noprint">
        <Button size="sm" loading={busy === "inc"} disabled={busy !== ""} onClick={() => verify(false)}>Verify now</Button>
        <Button size="sm" variant="ghost" loading={busy === "full"} disabled={busy !== ""} onClick={() => verify(true)}>
          Full re-walk
        </Button>
      </div>
    </div>
  );
}
