"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Amenities overview (02 spec 5.3): 7- and 30-day variance in money, the
   cost per occupied room (CPOR) against what standards and requests would
   have cost, the top leaking items and requested vs issued vs expected per
   item. Below that, the grouped report (item, floor, shift, staff).

   Variance is issued minus expected, so a POSITIVE figure is the leak and
   is drawn in the leak colour, never the "gain" green MoneyText would pick.
   Days without an occupancy row are excluded server side; the banner says
   how many so a short month never reads as a good month.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import Link from "next/link";
import { LuClipboardList, LuInfo, LuRefreshCw } from "react-icons/lu";
import { ApiError, api, hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Button, Card, Skeleton, StatTile, useToast } from "../../../../../ui";
import { ShareMeter, CHART_NEW, CHART_PROGRESS } from "../../../../charts";
import AmenitiesShell, { AmnEmpty, amnHref, PERM_ISSUE, PERM_MANAGE, PERM_VIEW } from "./AmenitiesShell";
import GroupedColumns from "./GroupedColumns";
import GroupReportCard from "./GroupReport";
import { CHART_EXPECTED, coverageNote, num } from "./lib";
import type { Minibar, Overview as OverviewData } from "./types";

export default function Overview() {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const canView = can(PERM_VIEW);
  const [version, setVersion] = useState(0);
  const [rolling, setRolling] = useState(false);

  const ov = useApi<OverviewData>(canView ? hotelPath(slug, "/amenities/overview") : null, [slug, version]);
  const mb = useApi<Minibar>(canView ? hotelPath(slug, "/amenities/minibar") : null, [slug, version]);

  // A manager can settle the last 14 days now instead of waiting for 03:30,
  // e.g. right after importing a month of issuance.
  const rollup = async () => {
    setRolling(true);
    try {
      const r = await api.post<{ rollup?: { days?: number } }>(hotelPath(slug, "/amenities/rollup"), {});
      toast.success("Figures refreshed", `${r.rollup?.days ?? 14} days recalculated.`);
      setVersion(v => v + 1);
    } catch (err) {
      toast.error("Could not refresh", err instanceof ApiError ? err.message : undefined);
    } finally {
      setRolling(false);
    }
  };

  const actions = (
    <>
      {can(PERM_MANAGE) && (
        <Button size="sm" variant="ghost" icon={<LuRefreshCw />} loading={rolling} onClick={rollup}>Recalculate</Button>
      )}
      {can([PERM_ISSUE, PERM_MANAGE]) && (
        <Button size="sm" variant="primary" icon={<LuClipboardList />} href={amnHref(slug, "/issue")}>Log issuance</Button>
      )}
    </>
  );

  return (
    <AmenitiesShell active="overview" need={[PERM_VIEW]} actions={actions}>
      {ov.error ? (
        <Card><AmnEmpty title="Could not load amenities" body={ov.error.message}
                        action={<Button onClick={ov.reload}>Try again</Button>} /></Card>
      ) : (
        <>
          <Banners data={ov.data} slug={slug} />
          <Tiles data={ov.data} minibar={mb.data} loading={ov.loading} />
          <div className="amn-grid2">
            <TopLeaking data={ov.data} loading={ov.loading} slug={slug} />
            <RequestedVsIssued data={ov.data} loading={ov.loading} />
          </div>
          <GroupReportCard />
        </>
      )}
    </AmenitiesShell>
  );
}

// ─── Banners ─────────────────────────────────────────────────────────────

function Banners({ data, slug }: { data?: OverviewData; slug: string }) {
  if (!data) return null;
  const cover = coverageNote(data.days_covered_30d, data.days_missing_occupancy_30d);
  return (
    <>
      {cover && (
        <div className="amn-banner" role="status"><LuInfo aria-hidden="true" /><span>{cover} Last 30 days to {data.end}.</span></div>
      )}
      {data.catalog.needs_review > 0 && (
        <div className="amn-banner" role="status">
          <LuInfo aria-hidden="true" />
          <span>
            {data.catalog.needs_review} catalog item{data.catalog.needs_review === 1 ? "" : "s"} still carry default costs
            and standards. Figures use them until you confirm.{" "}
            <Link className="amn-link" href={amnHref(slug, "/catalog")}>Review the catalog</Link>
          </span>
        </div>
      )}
    </>
  );
}

// ─── Tiles ───────────────────────────────────────────────────────────────

function Tiles({ data, minibar, loading }: { data?: OverviewData; minibar?: Minibar; loading: boolean }) {
  const fmt = useFormat();
  const leak = (v: string | null | undefined) => {
    const n = num(v);
    return <span className={n > 0 ? "amn-leak" : undefined}>{fmt.money(n, { signDisplay: "exceptZero" })}</span>;
  };
  const c = data?.cpor_30d;
  const gap = num(c?.gap);
  return (
    <div className="amn-tiles">
      <StatTile label="Issued vs expected, 7 days" loading={loading} value={leak(data?.variance_7d)}
                sub={varianceSub(data?.variance_7d)} />
      <StatTile label="Issued vs expected, 30 days" loading={loading} value={leak(data?.variance_30d)}
                sub={data ? `${varianceSub(data.variance_30d)}, ${data.days_covered_30d} days` : undefined} />
      <StatTile
        label="Cost per occupied room" loading={loading}
        value={c?.cpor ? fmt.money(c.cpor, { maximumFractionDigits: 2, minimumFractionDigits: 2 }) : "No data"}
        sub={c?.expected_cpor ? `expected ${fmt.money(c.expected_cpor, { maximumFractionDigits: 2, minimumFractionDigits: 2 })} over ${fmt.number(c.orn)} room-nights` : "30 days"}
        delta={c?.gap && gap !== 0 ? {
          value: fmt.money(gap, { signDisplay: "exceptZero", maximumFractionDigits: 2, minimumFractionDigits: 2 }),
          direction: gap > 0 ? "up" : "down", good: gap < 0, label: "vs expected",
        } : undefined}
      />
      <StatTile
        label="Minibar refills not billed, 7 days" loading={loading && !minibar}
        value={minibar?.has_folio ? leak(minibar.unbilled_value) : "No folio feed"}
        sub={minibar?.has_folio
          ? `${minibar.unbilled ?? 0} of ${minibar.refills} refills (${minibar.unbilled_share_pct ?? 0}%)`
          : "Import PMS folios to compare refills with charges"}
      />
    </div>
  );
}

/** A negative total usually means standard items are not being logged
 *  (nothing issued against a daily standard), not that the hotel saved. */
function varianceSub(v: string | null | undefined): string {
  const n = num(v);
  if (n > 0) return "above standards and logged requests";
  if (n < 0) return "below expected: check items not being logged";
  return "on expected";
}

// ─── Top leaking items ───────────────────────────────────────────────────
// A bar list rather than the kit HBarChart: each row links to the item's
// trend and carries money, which HBarChart would print as a bare number.

function TopLeaking({ data, loading, slug }: { data?: OverviewData; loading: boolean; slug: string }) {
  const fmt = useFormat();
  const rows = data?.top_leaking ?? [];
  const max = Math.max(0, ...rows.map(r => num(r.variance_value)));
  return (
    <Card title="Top leaking items" subtitle="Last 30 days, money issued above expected">
      {loading && !data ? (
        <Skeleton lines={5} height={18} />
      ) : rows.length === 0 ? (
        <AmnEmpty title="Nothing above expected"
                  body="No item was issued above its standard plus logged requests in the last 30 days." />
      ) : (
        <ol className="amn-recent" aria-label="Items issued above expected">
          {rows.map(r => (
            <li key={r.item_id} style={{ display: "block" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                <Link className="amn-link" href={amnHref(slug, `/items/${r.item_id}`)}>{r.name}</Link>
                <span className="amn-leak amn-mono">{fmt.money(r.variance_value, { signDisplay: "exceptZero" })}</span>
              </div>
              <div className="amn-muted">
                {fmt.number(num(r.issued))} issued vs {fmt.number(num(r.expected), 1)} expected
                {r.variance_pct ? ` (${num(r.variance_pct) > 0 ? "+" : ""}${r.variance_pct}%)` : ""}
              </div>
              <ShareMeter value={num(r.variance_value)} max={max} color="#E0795A"
                          title={`${r.name}: ${fmt.money(r.variance_value)} above expected`} />
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

// ─── Requested vs issued vs expected ─────────────────────────────────────

const RIE_SERIES = [
  { label: "Requested", color: CHART_NEW },
  { label: "Issued", color: CHART_PROGRESS },
  { label: "Expected", color: CHART_EXPECTED },
] as const;

function RequestedVsIssued({ data, loading }: { data?: OverviewData; loading: boolean }) {
  const fmt = useFormat();
  const chart = (data?.chart ?? []).map(c => ({
    label: c.name,
    values: [num(c.requested), num(c.issued), num(c.expected)],
  }));
  return (
    <Card title="Requested, issued and expected" subtitle="Units per item, last 30 days">
      {loading && !data ? (
        <Skeleton height={200} />
      ) : (
        <GroupedColumns data={chart} series={RIE_SERIES} ariaLabel="Requested, issued and expected units per item"
                        format={v => fmt.number(v)}
                        emptyNote="No issuance or occupancy in the last 30 days yet." />
      )}
      <p className="amn-note">Expected is the room standard times occupied rooms (or arrivals), plus what guests asked for.</p>
    </Card>
  );
}
