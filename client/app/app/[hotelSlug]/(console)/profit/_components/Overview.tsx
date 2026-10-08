"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Profit Guard Overview (02 spec 3.10): four figures first (Found,
   Recovered and verified, Open, Due within 72 h), then where the money
   stands (RecoveryFunnel), what kind of leak it is (by detector), what is
   about to expire (DeadlineRail) and whether the pilot has paid for
   itself (PilotScorecard). Recovered is the sum of credit events in the
   ledger, so it only ever shows money an OTA statement or payout proved.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import Link from "next/link";
import { LuPlay, LuRefreshCw } from "react-icons/lu";
import { CHART_NEW, HBarChart } from "../../../../charts";
import { api, useApi } from "../../../../../lib/api";
import { currencyDigits, useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, StatTile, useToast } from "../../../../../ui";
import { detectorLabel, minorToNumber, otaLabel, pgApi, pgHref, STATUS_LABEL } from "../lib";
import type { Deadline, LedgerSummary, PilotScorecard as Scorecard } from "../types";
import { DataCard, ErrorNote, errorText, Money, useCanManage } from "./bits";
import { DeadlineRail } from "./DeadlineRail";
import { PilotScorecard } from "./PilotScorecard";
import { RecoveryFunnel } from "./RecoveryFunnel";

export default function Overview() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const toast = useToast();
  const canManage = useCanManage();
  const ledger = useApi<LedgerSummary>(pgApi(slug, "/ledger?limit=12"));
  const deadlines = useApi<Deadline[]>(pgApi(slug, "/deadlines?days=14"));
  const pilot = useApi<Scorecard>(pgApi(slug, "/pilot-scorecard"));
  const [busy, setBusy] = useState<"run" | "verify" | null>(null);

  const s = ledger.data;
  const ccy = s?.currency ?? fmt.currency;
  const digits = currencyDigits(ccy);

  const reloadAll = () => { ledger.reload(); deadlines.reload(); pilot.reload(); };

  async function runNow() {
    setBusy("run");
    try {
      const out = await api.post<{ findings: number; created: number }>(pgApi(slug, "/detectors/run"), {});
      toast({ tone: "ok", title: "Checks finished", body: `${out.created} new of ${out.findings} findings.` });
      reloadAll();
    } catch (err) {
      toast({ tone: "bad", title: "Could not run the checks", body: errorText(err) });
    } finally {
      setBusy(null);
    }
  }

  async function verifyNow() {
    setBusy("verify");
    try {
      const out = await api.post<{ recovered: unknown[]; credited_minor: number }>(pgApi(slug, "/findings/verify"), {});
      toast({
        tone: "ok", title: "Credits checked",
        body: `${out.recovered.length} finding${out.recovered.length === 1 ? "" : "s"} recovered, ${fmt.money(out.credited_minor, { minor: true, currency: ccy })} credited.`,
      });
      reloadAll();
    } catch (err) {
      toast({ tone: "bad", title: "Could not check credits", body: errorText(err) });
    } finally {
      setBusy(null);
    }
  }

  const byDetector = (s?.by_detector ?? []).slice(0, 8).map(d => ({
    label: detectorLabel(d.detector),
    values: [Math.round(minorToNumber(d.amount_minor, digits))],
    note: `${d.count}`,
  }));

  return (
    <>
      {canManage && (
        <div className="pg-row" style={{ justifyContent: "flex-end" }}>
          <Button size="sm" icon={<LuRefreshCw aria-hidden="true" />} loading={busy === "verify"} onClick={verifyNow}>
            Check for credits
          </Button>
          <Button size="sm" variant="primary" icon={<LuPlay aria-hidden="true" />} loading={busy === "run"} onClick={runNow}>
            Run all checks now
          </Button>
        </div>
      )}

      <ErrorNote error={ledger.error} />
      <div className="pg-tiles">
        <StatTile label="Found" loading={ledger.loading} value={<Money minor={s?.found_minor} currency={ccy} compact />}
                  sub="Recoverable and preventable, not dismissed" />
        <StatTile label="Recovered (verified)" loading={ledger.loading}
                  value={<Money minor={s?.recovered_minor} currency={ccy} compact />} sub="Proved by a statement or payout" />
        <StatTile label="Open" loading={ledger.loading} value={<Money minor={s?.open_minor} currency={ccy} compact />}
                  sub="Found, accepted or disputed" />
        <StatTile label="Due within 72 h" loading={ledger.loading}
                  value={<Money minor={s?.due_72h_minor} currency={ccy} compact />}
                  sub={s ? `${s.due_72h_count} finding${s.due_72h_count === 1 ? "" : "s"} to claim` : undefined} />
      </div>
      {s && (s.compliance_open > 0 || s.other_currency_findings > 0) && (
        <div className="pg-notice pg-notice--info">
          <div>
            {s.compliance_open > 0 && <>{s.compliance_open} compliance item{s.compliance_open === 1 ? "" : "s"} open (not counted as money). </>}
            {s.other_currency_findings > 0 && <>{s.other_currency_findings} finding{s.other_currency_findings === 1 ? " is" : "s are"} in another currency and listed separately. </>}
            <Link className="cx-link" href={pgHref(slug, "/findings")}>See findings</Link>
          </div>
        </div>
      )}
      <div className="pg-grid-main">
        <div className="cx-stack">
          <DataCard title="Where the money stands" subtitle="Amounts by status. Recovered only counts credits an OTA statement or payout proved."
                    loading={ledger.loading} error={ledger.error} lines={4}>
            {s && <RecoveryFunnel funnel={s.funnel} currency={ccy} />}
          </DataCard>
          <DataCard title="Leakage by check" subtitle="Open and closed, not dismissed. The small figure is the count."
                    loading={ledger.loading} error={ledger.error} lines={4}>
            {/* Totals, caps, ticks and tooltips read as money (format.ts), never a bare 48250. */}
            <HBarChart rows={byDetector} series={[{ label: "Amount", color: CHART_NEW }]}
                       valueFormat={v => fmt.money(v, { currency: ccy, compact: true })}
                       ariaLabel="Leakage by detector" emptyNote="Nothing found yet." />
          </DataCard>
          <DataCard title="Latest in the recovery ledger" loading={ledger.loading} error={ledger.error}
                    empty={!s?.events.length} emptyTitle="No ledger entries yet.">
            <ul className="pg-rail">
              {(s?.events ?? []).map(e => (
                <li key={e.id} className="pg-rail__item">
                  <Link href={pgHref(slug, `/findings/${e.finding_id}`)}>
                    <div className="pg-rail__title">
                      {detectorLabel(e.detector)}: {e.channel === "credit" ? "credit received" : (
                        <>{e.from_status ? `${STATUS_LABEL[e.from_status]} to ` : ""}{STATUS_LABEL[e.to_status] ?? e.to_status}</>
                      )}
                    </div>
                  </Link>
                  {e.amount_minor ? <Money minor={e.amount_minor} currency={e.currency} /> : <span />}
                  <div className="pg-rail__meta">
                    {fmt.dateTime(e.at)} · {otaLabel(e.ota_code)}{e.actor ? ` · ${e.actor}` : ""}{e.note ? ` · ${e.note}` : ""}
                  </div>
                </li>
              ))}
            </ul>
          </DataCard>
        </div>
        <div className="cx-stack">
          <DataCard title="Deadlines" subtitle="The next 14 days, soonest first." gold
                    loading={deadlines.loading} error={deadlines.error} lines={5}>
            <DeadlineRail slug={slug} items={deadlines.data ?? []} />
          </DataCard>
          <DataCard title="Pilot scorecard" loading={pilot.loading} error={pilot.error} lines={4}>
            {pilot.data && <PilotScorecard card={pilot.data} formatDate={d => fmt.date(d)} />}
          </DataCard>
          <Card title="Next steps">
            <div className="pg-muted">
              Import this month&apos;s OTA statements on <Link className="cx-link" href={pgHref(slug, "/imports")}>Imports</Link>,
              review what Clefs found on <Link className="cx-link" href={pgHref(slug, "/findings")}>Findings</Link>, and send the
              claims as one <Link className="cx-link" href={pgHref(slug, "/disputes")}>dispute packet</Link> per OTA.
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
