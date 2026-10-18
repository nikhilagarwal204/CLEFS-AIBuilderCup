"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Weekly verified-value reports: one row per finished ISO week (built by
   the Monday job, or on first read). Verified is money that actions
   caused and a verifier confirmed; identified is what the detectors first
   saw that week. No vanity metrics: the report is those two numbers and
   the decisions that connect them.
   ═══════════════════════════════════════════════════════════════════════ */

import { useRouter } from "next/navigation";
import { LuFileText } from "react-icons/lu";
import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, EmptyState, MoneyText, Table, type Column } from "../../../../../ui";
import { addDays, brfApi, brfHref, isoWeekOf } from "../lib";
import type { WeeklyReportRow } from "../types";

export default function WeeklyReports() {
  const { slug } = useHotel();
  const fmt = useFormat();
  const router = useRouter();
  const list = useApi<WeeklyReportRow[]>(brfApi(slug, "/reports/weekly?limit=26"), [slug]);
  // The last finished week in the hotel's own calendar (today's week is still open).
  const lastWeek = isoWeekOf(addDays(fmt.dayKey(new Date()), -7));
  const open = (week: string) => router.push(brfHref(slug, `/reports/weekly/${encodeURIComponent(week)}`));

  const columns: Column<WeeklyReportRow>[] = [
    { key: "week", header: "Week", render: r => <b style={{ color: "var(--clefs-ink)" }}>{r.period_key}</b> },
    { key: "period", header: "Dates", render: r => `${fmt.date(r.period_start + "T12:00:00Z", "medium")} to ${fmt.date(r.period_end + "T12:00:00Z", "medium")}` },
    { key: "verified", header: "Verified", align: "right", render: r => <MoneyText amount={r.verified_minor ?? 0} minor currency={r.currency} /> },
    { key: "identified", header: "Identified", align: "right", hideOnMobile: true,
      render: r => <MoneyText amount={r.identified_minor ?? 0} minor currency={r.currency} /> },
  ];

  const rows = list.data ?? [];
  const hasLast = rows.some(r => r.period_key === lastWeek);

  return (
    <>
      <Card title="Weekly verified-value report"
            subtitle="Every Monday: what Clefs verified last week, what it identified, and how fast decisions were made."
            actions={!hasLast ? <Button size="sm" variant="primary" icon={<LuFileText />} onClick={() => open(lastWeek)}>Open week {lastWeek}</Button> : undefined}>
        {list.error && !list.data ? (
          <EmptyState title="Could not load reports" body={list.error.message} action={<Button onClick={list.reload}>Try again</Button>} />
        ) : (
          <Table columns={columns} rows={rows} rowKey={r => r.period_key} loading={list.loading && !list.data}
                 onRowClick={r => open(r.period_key)} caption="Weekly reports"
                 empty="No report yet. The first one is built the Monday after your first full week." />
        )}
      </Card>
    </>
  );
}
