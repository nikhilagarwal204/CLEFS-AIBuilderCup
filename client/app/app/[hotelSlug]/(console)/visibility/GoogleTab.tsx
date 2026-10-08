"use client";

/* Google tab (04 spec 5.2, 5.4, 5.8, 5.11): official price presence, the
   parity table with the SerpApi price list as evidence (vendor disclosure
   and "indicative" label above it), the organic position, the Business
   Profile block and the attribution line. */

import Link from "next/link";
import { useState } from "react";
import { hotelPath, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { Badge, Card, Sparkline, StatTile, Table, type Column } from "../../../../ui";
import type { GoogleView, ParityRow } from "./types";
import { noonUtc, num, vendorRiskHref } from "./model";
import { SetupChecklist, SourceLabel, TabState } from "./parts";
import GbpSection from "./GbpSection";

// ─── Parity table ────────────────────────────────────────────────────────

function ParityTable({ rows, view }: { rows: ParityRow[]; view: GoogleView }) {
  const fmt = useFormat();
  const [open, setOpen] = useState<string | null>(null);
  const key = (r: ParityRow) => `${r.check_in}:${r.check_out}`;
  const money = (v: number | null, cur: string) => (v === null ? "n/a" : fmt.money(v, { minor: true, currency: cur }));
  const cols: Column<ParityRow>[] = [
    { key: "stay", header: "Stay", render: r => `${fmt.date(noonUtc(r.check_in))} to ${fmt.date(noonUtc(r.check_out))}` },
    { key: "official", header: "Official site", align: "right",
      render: r => (r.official_present ? money(r.official_minor, r.currency) : <Badge tone="warn">Not shown</Badge>) },
    { key: "lowest", header: "Lowest other", align: "right",
      render: r => (r.lowest_minor === null ? "n/a" : (
        <span>{money(r.lowest_minor, r.currency)}<span className="vis-muted"> {r.lowest_source ?? ""}</span></span>
      )) },
    { key: "gap", header: "Gap", align: "right",
      render: r => (r.gap_minor === null ? "n/a" : (
        <span className={r.breach ? "vis-breach" : undefined}>
          {money(r.gap_minor, r.currency)} ({fmt.pct(num(r.gap_pct), { digits: 1 })})
        </span>
      )) },
    { key: "status", header: "Parity", render: r => (r.breach ? <Badge tone="bad" dot>Undercut</Badge>
      : r.official_present ? <Badge tone="ok" dot>Within tolerance</Badge> : <Badge tone="neutral">No official price</Badge>) },
    { key: "evidence", header: "Evidence", render: r => (
      <button type="button" className="vis-link" aria-expanded={open === key(r)}
        onClick={e => { e.stopPropagation(); setOpen(open === key(r) ? null : key(r)); }}>
        {open === key(r) ? "Hide prices" : `${r.prices.length} prices`}
      </button>
    ) },
  ];
  const shown = rows.find(r => key(r) === open);
  const riskHref = vendorRiskHref(view.labels.vendor_risk_url);
  return (
    <Card title="Parity on Google Hotels"
      subtitle={`A gap counts as an undercut above ${fmt.pct(num(view.tolerance_pct), { digits: 2 })} of your official price, on a like-for-like basis.`}>
      {/* Disclosure first: it must sit above the table whenever prices show. */}
      {/* The vendor-risk note (Canon 20) is linked from the disclosure itself. */}
      <p className="vis-disclosure">
        {view.labels.disclosure} {view.labels.price_accuracy}
        {riskHref && <> <Link href={riskHref}>Vendor risk note</Link></>}
      </p>
      <div className="vis-scroll">
        <Table columns={cols} rows={rows} rowKey={key} caption="Official price against the lowest other price on Google Hotels"
          empty="No Google Hotels checks yet; they run daily at 05:30." />
      </div>
      {shown && (
        <div className="vis-section">
          <h3>Prices Google showed for {fmt.date(noonUtc(shown.check_in))} ({shown.price_basis ?? "price basis unknown"})</h3>
          <ul className="vis-prices">
            {[...shown.prices].sort((a, b) => a.price_minor - b.price_minor).map(p => (
              <li key={`${p.source}-${p.price_minor}`}>
                <span className={p.official ? "vis-off" : undefined}>
                  {p.source}{p.official ? " (official site)" : ""}{p.channel ? ` · ${p.channel}` : ""}
                </span>
                <span className="vis-num">{fmt.money(p.price_minor, { minor: true, currency: p.currency })}</span>
              </li>
            ))}
          </ul>
          <p className="vis-note">Observed {fmt.dateTime(shown.observed_at)}. {view.labels.parity}</p>
        </div>
      )}
      <div style={{ marginTop: 10 }}><SourceLabel>Google Hotels data via SerpApi (third-party); prices indicative.</SourceLabel></div>
    </Card>
  );
}

// ─── Tab ─────────────────────────────────────────────────────────────────

export default function GoogleTab({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { slug } = useHotel();
  const g = useApi<GoogleView>(hotelPath(slug, "/visibility/google"), [slug]);
  return (
    <TabState loading={g.loading && !g.data} error={g.error} onRetry={g.reload}>
      {g.data && <GoogleBody view={g.data} onOpenSettings={onOpenSettings} />}
    </TabState>
  );
}

function GoogleBody({ view, onOpenSettings }: { view: GoogleView; onOpenSettings: () => void }) {
  return (
    <div className="vis-stack">
      <SetupChecklist title="Set up Google Hotels checks" codes={view.setup_missing} onOpenSettings={onOpenSettings} />
      <GoogleTiles view={view} />
      <ParityTable rows={view.parity} view={view} />
      <GbpSection onOpenSettings={onOpenSettings} />
      <AttributionLine view={view} />
    </div>
  );
}

// ─── Tiles and attribution ───────────────────────────────────────────────

function GoogleTiles({ view }: { view: GoogleView }) {
  const fmt = useFormat();
  const hist = view.rank_history;
  const last = [...hist].reverse().find(h => h.organic_rank !== null);
  const latest = view.presence.latest;
  return (
    <div className="vis-tiles">
      <StatTile label="Official price on Google"
        value={latest === null ? "n/a" : latest ? "Shown" : "Missing"}
        sub={latest === false ? "Google shows OTA prices only" : "latest check"} />
      {/* Counted per stay-date check, like the table below (QA OWN-16). */}
      <StatTile label="Official price shown (30 days)" value={fmt.pct(num(view.presence.rate_30d), { digits: 0 })}
        sub={view.presence.checks_30d != null
          ? `${view.presence.shown_30d ?? 0} of ${view.presence.checks_30d} stay-date checks`
          : `${hist.length} checked days`} />
      <StatTile label="Organic position" value={last ? `#${last.organic_rank}` : "n/a"}
        sub={last ? `of ${last.organic_total ?? "?"}, sponsored removed; not a ranking guarantee` : "No position yet"}
        sparkline={hist.length > 1 ? (
          <Sparkline values={hist.map(h => (h.organic_rank === null ? null : -h.organic_rank))}
            ariaLabel="Organic position over 30 days (higher is better)" />
        ) : undefined} />
      <StatTile label="Commission avoided (last month)"
        value={view.attribution ? fmt.money(view.attribution.commission_avoided_minor, { minor: true, currency: view.attribution.currency }) : "n/a"}
        sub={view.attribution ? `${view.attribution.direct_bookings} direct bookings from Google` : "Computed monthly"} />
    </div>
  );
}

function AttributionLine({ view }: { view: GoogleView }) {
  const a = view.attribution;
  if (!a) return null;
  return (
    <Card title="Google to direct bookings" subtitle="UTM-tagged bookings from your Business Profile and free booking links.">
      <p className="vis-line">{a.line}</p>
      <p className="vis-note">{a.label}</p>
    </Card>
  );
}
