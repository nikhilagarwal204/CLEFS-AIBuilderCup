"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Account mapping review (04 spec 6.4 and 6.8 mapping/): every account in
   the chart of accounts against its USALI line, grouped by department,
   with where the proposal came from, how sure it is and why (the model's
   rationale cites the words that decided it). Arabic names render right
   to left. Every proposal needs a person: "Confirm all at 85% or more" in
   bulk, anything lower one at a time, and a written reason before an
   account that reads as below GOP (rent, interest, depreciation, drawings)
   may sit on a line GOP reads. A confirmed mapping is locked and reused
   every month; changing it later makes a new version.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { apiFetch, useApi } from "../../../../../lib/api";
import { useSession } from "../../../../../lib/session";
import { Button, Card, EmptyState, Segmented, Skeleton, useToast } from "../../../../../ui";
import { bulkEligible, finApi, finHref, groupByDept, monthLabel } from "../lib";
import type { ConfirmOut, MappingOut } from "../types";
import AccountRow from "./AccountRow";
import { errText } from "./bits";

type StatusFilter = "open" | "confirmed" | "all";

export default function MappingReview({ slug, batchId, initialStatus }: {
  slug: string;
  batchId?: number;
  initialStatus?: StatusFilter;
}) {
  const { can } = useSession();
  const toast = useToast();
  const canConfirm = can("finance.mapping.confirm");
  const canImport = can("finance.import");
  const [status, setStatus] = useState<StatusFilter>(initialStatus ?? "open");
  const [busy, setBusy] = useState<"bulk" | "propose" | null>(null);
  const q = `/mapping?status=${status}${batchId ? `&batch_id=${batchId}` : ""}`;
  const res = useApi<MappingOut>(can("finance.pl.view") ? finApi(slug, q) : null, [slug, q]);
  const data = res.data;
  const catalog = useMemo(() => data?.catalog ?? [], [data]);
  const threshold = data?.bulk_min_confidence ?? 0.85;
  const groups = useMemo(() => groupByDept(data?.accounts ?? [], catalog), [data, catalog]);
  const eligible = useMemo(() => bulkEligible(data?.accounts ?? [], threshold), [data, threshold]);
  const noProposal = (data?.accounts ?? []).filter(a => !a.usali_line).length;

  const afterConfirm = (out: ConfirmOut, what: string) => {
    const ready = out.open_batches.filter(b => b.status === "review").map(b => monthLabel(b.period_month, true));
    toast.success(what, ready.length ? `Ready to publish: ${ready.join(", ")}.` : undefined);
    res.reload();
  };

  const bulk = async () => {
    if (!eligible.length) return;
    if (!window.confirm(`Confirm ${eligible.length} proposals at ${Math.round(threshold * 100)}% confidence or more? They lock and are reused every month.`)) return;
    setBusy("bulk");
    try {
      const out = await apiFetch<ConfirmOut>(finApi(slug, "/mapping/confirm"), {
        method: "POST", json: { all_above: threshold, account_ids: eligible.map(a => a.account_id) },
      });
      afterConfirm(out, `${out.confirmed.length} accounts confirmed`);
    } catch (err) {
      toast.error("Could not confirm", errText(err));
    } finally {
      setBusy(null);
    }
  };

  const propose = async () => {
    setBusy("propose");
    try {
      const out = await apiFetch<{ queued: number }>(finApi(slug, `/mapping/propose${batchId ? `?batch_id=${batchId}` : ""}`), { method: "POST" });
      toast.info(out.queued ? `${out.queued} accounts sent for a proposal` : "Every account already has a proposal",
        out.queued ? "Proposals usually arrive within a minute. Reload to see them." : undefined);
    } catch (err) {
      toast.error("Could not ask for proposals", errText(err));
    } finally {
      setBusy(null);
    }
  };

  if (!can("finance.pl.view")) {
    return <Card><EmptyState title="No access to the mapping" body="The owner, GM and finance roles see the chart of accounts." /></Card>;
  }

  return (
    <>
      <Card
        title="How your accounts map to USALI"
        subtitle={batchId
          ? <>Accounts in one import. <a className="fin-link" href={finHref(slug, "/mapping")}>Show every account</a></>
          : "Rules, your earlier choices, patterns seen across hotels, then AI, in that order. Nothing counts until a person confirms it."}
      >
        <div className="fin-toolbar">
          <Segmented
            ariaLabel="Which accounts"
            value={status}
            onChange={setStatus}
            options={[{ value: "open", label: "To confirm" }, { value: "confirmed", label: "Confirmed" }, { value: "all", label: "All" }]}
          />
          <span className="fin-spacer" />
          {canImport && noProposal > 0 && (
            <Button variant="ghost" size="sm" loading={busy === "propose"} disabled={!!busy} onClick={propose}>
              Ask AI for {noProposal} missing
            </Button>
          )}
          {canConfirm && status !== "confirmed" && (
            <Button size="sm" loading={busy === "bulk"} disabled={!!busy || !eligible.length} onClick={bulk}>
              Confirm all at {Math.round(threshold * 100)}% or more ({eligible.length})
            </Button>
          )}
        </div>
      </Card>
      {res.loading && !data ? (
        <Skeleton lines={8} />
      ) : res.error ? (
        <Card><EmptyState title="Could not load the mapping" body={errText(res.error)} /></Card>
      ) : !groups.length ? (
        <Card>
          <EmptyState
            title={status === "open" ? "Nothing to confirm" : "No accounts yet"}
            body={status === "open" ? "Every account is mapped and confirmed. New accounts appear here after the next import." : "Accounts appear after the first P&L import."}
          />
        </Card>
      ) : (
        <Card>
          {groups.map(g => (
            <section key={g.dept} className="fin-group" aria-label={g.label}>
              <h3 className="fin-group__head"><span>{g.label}</span><span>{g.accounts.length}</span></h3>
              {g.accounts.map(a => (
                <AccountRow key={a.account_id} slug={slug} a={a} catalog={catalog} threshold={threshold}
                  canConfirm={canConfirm} onConfirmed={afterConfirm} />
              ))}
            </section>
          ))}
        </Card>
      )}
    </>
  );
}
