"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Profit Guard > Tax (02 spec 3.10 tax/): the unverified-rule banner, the
   Tourism Dirham ledger by month, the price-split ConventionPreview and
   the read-only fee rules with who verified each.

   The preview and the fee-rule table are the Settings > Tax components,
   reused as they are (their maths mirrors core/tax/calc.py with BigInt and
   is golden-tested; a second copy here could only drift). The profile and
   the 2026 announcement toggles stay on Settings > Tax, linked below.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import Link from "next/link";
import { LuAlertTriangle, LuPlay } from "react-icons/lu";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, useToast } from "../../../../../ui";
import type { TaxProfile } from "../../settings/tax/api";
import { ConventionPreview } from "../../settings/tax/ConventionPreview";
import { announcementsIn, type FeeRule } from "../../settings/tax/convention";
import { FeeRulesTable } from "../../settings/tax/FeeRulesTable";
import { taxStyles } from "../../settings/tax/styles";
import { pgApi, pgTaxApi } from "../lib";
import type { TdLedger as Ledger } from "../types";
import { DataCard, errorText, useCanManage } from "./bits";
import { TdLedger } from "./TdLedger";

const BANNER_DAYS = 90;

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function rulesOf(data: unknown): FeeRule[] {
  if (Array.isArray(data)) return data as FeeRule[];
  const v = (data as { rules?: unknown } | null)?.rules;
  return Array.isArray(v) ? (v as FeeRule[]) : [];
}

/** Announced-but-unverified rules in the next 90 days, plus any notice the
    ledger rollup raised. Shown whether applied or not: an unverified rate
    is a guess until DET confirms it. */
function UnverifiedBanner({ rules, notices, today, slug }: { rules: FeeRule[]; notices: string[]; today: string; slug: string }) {
  const fmt = useFormat();
  const hits = announcementsIn(rules, today, addDays(today, BANNER_DAYS));
  if (!hits.length && !notices.length) return null;
  return (
    <div className="pg-notice" role="status">
      <LuAlertTriangle aria-hidden="true" size={20} color="var(--cx-warn)" style={{ flex: "none", marginTop: 2 }} />
      <div>
        <strong>Some tax rules in force soon are announced but not verified.</strong>
        <ul>
          {hits.map(r => (
            <li key={r.id}>
              {r.label ?? r.rule_key} from {fmt.date(r.effective_from, "medium")}: {r.override?.apply ? "applied to your hotel" : "not applied"}.
              {r.source_url && <> <a className="cx-link" href={r.source_url} target="_blank" rel="noopener noreferrer">Source</a></>}
            </li>
          ))}
          {notices.map(n => <li key={n}>{n}</li>)}
        </ul>
        <div style={{ marginTop: 6 }}>
          Decide whether they apply on <Link className="cx-link" href={`/app/${encodeURIComponent(slug)}/settings/tax`}>Settings, Tax</Link>.
        </div>
      </div>
    </div>
  );
}

export default function TaxView() {
  const { slug, hotel } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const canManage = useCanManage();
  const today = fmt.dayKey(new Date());
  const ledger = useApi<Ledger>(pgApi(slug, "/tax/td-ledger?months=12"));
  const rulesRes = useApi<unknown>(pgTaxApi(slug, `/fee-rules?on=${today}`));
  const profile = useApi<TaxProfile>(pgTaxApi(slug, "/profile"));
  const rules = useMemo(() => rulesOf(rulesRes.data), [rulesRes.data]);
  const currency = profile.data?.currency || hotel?.currency || fmt.currency;
  const [running, setRunning] = useState(false);

  async function runChecks() {
    setRunning(true);
    try {
      const out = await api.post<{ findings: number; created: number }>(pgApi(slug, "/tax/checks/run"), {});
      toast({ tone: "ok", title: "Tax checks finished", body: `${out.created} new of ${out.findings} findings.` });
      ledger.reload();
    } catch (err) {
      toast({ tone: "bad", title: "Could not run the tax checks", body: errorText(err) });
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: taxStyles }} />
      <UnverifiedBanner rules={rules} notices={ledger.data?.notices ?? []} today={today} slug={slug} />
      <DataCard title="Tourism Dirham ledger" subtitle="By month: liable, collected in the folio, remitted to DET, and due."
                loading={ledger.loading} error={ledger.error} lines={5}
                actions={canManage ? (
                  <Button size="sm" icon={<LuPlay aria-hidden="true" />} loading={running} onClick={runChecks}>Run tax checks</Button>
                ) : undefined}>
        {ledger.data && <TdLedger slug={slug} ledger={ledger.data} canManage={canManage} onChange={ledger.reload} />}
      </DataCard>
      <div className="pg-faint">
        Tourism Dirham findings (under-collection, night 31 and later, wrong rate, VAT on the dirham, duplicates, no-shows) are on{" "}
        <Link className="cx-link" href={`/app/${encodeURIComponent(slug)}/profit/findings?class=COMPLIANCE&status=all`}>Findings</Link>.
      </div>
      <DataCard title="Price split preview" subtitle="One night's guest price under conventions A, B and C, with the rules in force that day."
                loading={rulesRes.loading && !rulesRes.data} error={rulesRes.error} lines={4}>
        <ConventionPreview rules={rules} profile={profile.data ?? null} currency={currency} today={today} />
      </DataCard>
      <DataCard title="Fee rules" subtitle="Read-only. Each rate shows its source and who verified it."
                loading={rulesRes.loading && !rulesRes.data} error={rulesRes.error} empty={!rules.length}
                emptyTitle="No fee rules on file" lines={4}>
        <FeeRulesTable rules={rules} category={profile.data?.property_category ?? null} on={today} currency={currency} />
      </DataCard>
    </>
  );
}
