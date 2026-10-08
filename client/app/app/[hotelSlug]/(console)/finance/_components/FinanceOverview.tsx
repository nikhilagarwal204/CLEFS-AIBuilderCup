"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Finance overview (04 spec 6.8). With finance.pl.view and a published
   month: verified profit first, the one-pager tiles, the top anomalies,
   the checks, the USALI summary, then the weekly pulse. With no published
   month yet: the upload call to action plus the weekly pulse only. A
   revenue manager (finance.view / finance.weekly.view, no P&L access) sees
   the weekly pulse only, as the spec's access rule says.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { ApiError, useApi } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Button, Card, EmptyState, Field, Select, Skeleton } from "../../../../../ui";
import { finApi, finHref, monthKey, monthLabel, type OAuthReturn } from "../lib";
import type { Monthly } from "../types";
import AnomalyList from "./AnomalyList";
import { errText, useOAuthReturnToast } from "./bits";
import OnePager from "./OnePager";
import { ChecksPanel, UsaliTable } from "./PLTables";
import UploadPanel from "./UploadPanel";
import VerifiedPanel from "./VerifiedPanel";
import WeeklyPulse from "./WeeklyPulse";

export default function FinanceOverview({ initialMonth, oauthReturn }: {
  initialMonth?: string;
  oauthReturn?: OAuthReturn;
}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const canPL = can("finance.pl.view");
  const canWeekly = can(["finance.weekly.view", "finance.view"]);
  useOAuthReturnToast(oauthReturn);

  if (!canPL && !canWeekly) {
    return (
      <Card>
        <EmptyState title="No access to finance" body="Ask the owner to give your role finance access." />
      </Card>
    );
  }
  return (
    <>
      {canPL && <MonthlySection slug={slug} initialMonth={initialMonth} />}
      <section className="cx-stack" aria-label="Weekly pulse">
        <h2 className="fin-group__head" style={{ marginTop: canPL ? 8 : 0 }}>Weekly pulse</h2>
        <WeeklyPulse slug={slug} />
      </section>
      {!canPL && (
        <div className="fin-muted">
          The monthly P&L one-pager is visible to the owner, GM and finance roles.
        </div>
      )}
    </>
  );
}

// ─── The published month ─────────────────────────────────────────────────

function MonthlySection({ slug, initialMonth }: { slug: string; initialMonth?: string }) {
  const { can } = useSession();
  const [month, setMonth] = useState<string>(() =>
    initialMonth && /^\d{4}-\d{2}$/.test(initialMonth) ? initialMonth : "latest");
  // Published months (newest last) feed the picker and the prior-month deltas.
  const trend = useApi<{ months: Monthly[] }>(finApi(slug, "/trend?months=36"), [slug]);
  const res = useApi<Monthly>(finApi(slug, `/monthly/${month}`), [slug, month]);
  const m = res.data;
  const published = useMemo(() => [...(trend.data?.months ?? [])].reverse(), [trend.data]);
  const prev = useMemo(() => {
    if (!m) return null;
    const i = published.findIndex(p => monthKey(p.month) === monthKey(m.month));
    return i >= 0 ? published[i + 1] ?? null : null;
  }, [m, published]);
  const labels = useMemo(() => Object.fromEntries((m?.usali ?? []).map(r => [r.usali_line, r.label])), [m]);
  const notPublished = res.error instanceof ApiError && res.error.code === "not_published";

  if (res.loading && !m) return <Skeleton lines={6} />;
  if (notPublished && month === "latest") return <EmptyMonthly slug={slug} canImport={can("finance.import")} />;
  if (res.error && !notPublished) {
    return <Card><EmptyState title="Could not load the P&L" body={errText(res.error)} /></Card>;
  }

  return (
    <section className="cx-stack" aria-label="Monthly P&L">
      <div className="fin-row">
        <h2 className="fin-group__head" style={{ margin: 0 }}>
          Monthly P&L{m ? `, ${monthLabel(m.month)}` : ""}
        </h2>
        {published.length > 1 && (
          <Field label="Month">
            <Select
              aria-label="Published month"
              value={m ? monthKey(m.month) : month}
              onChange={e => setMonth(e.target.value)}
            >
              {published.map(p => <option key={p.month} value={monthKey(p.month)}>{monthLabel(p.month)}</option>)}
            </Select>
          </Field>
        )}
      </div>
      {notPublished || !m ? (
        <Card><EmptyState title="That month is not published" body="Pick another month, or publish it from P&L imports." /></Card>
      ) : (
        <>
          <VerifiedPanel slug={slug} month={m.month} publishedMinor={m.verified_profit_minor} publishedValue={m.verified_profit} />
          <OnePager m={m} prev={prev} />
          <div className="fin-grid2">
            <AnomalyList slug={slug} anomalies={m.anomalies ?? []} canAct={can("finance.act")} labels={labels} />
            <ChecksPanel checks={m.checks ?? []} title="Checks at publish" />
          </div>
          <UsaliTable
            rows={m.usali ?? []}
            currency={m.currency}
            subtotals={[
              { label: "Total operating revenue", value: m.total_revenue },
              { label: "Departmental profit", value: m.dept_profit },
              { label: "Undistributed expenses", value: m.undistributed },
              { label: "GOP", value: m.gop, strong: true },
              { label: "EBITDA", value: m.ebitda },
              { label: "Net profit", value: m.net_profit, strong: true },
            ]}
          />
        </>
      )}
    </section>
  );
}

function EmptyMonthly({ slug, canImport }: { slug: string; canImport: boolean }) {
  return (
    <section className="cx-stack" aria-label="Monthly P&L">
      <Card>
        <EmptyState
          title="No P&L published yet"
          body="Upload last month's P&L (or connect Xero or QuickBooks) and Clefs maps it to USALI lines, checks it against your PMS, and shows GOP, GOPPAR and what Clefs verifiably added. Until then, the weekly pulse below needs no P&L."
          action={<Button href={finHref(slug, "/imports")} variant="secondary">Imports and connections</Button>}
        />
      </Card>
      {canImport && <UploadPanel slug={slug} compact />}
    </section>
  );
}
