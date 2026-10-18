"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Clefs-verified profit (04 spec 6.7), the headline and first tile of the
   one-pager: the sum of verified action outcomes for the month, bucketed
   by the day each was verified in hotel time, per available room. The
   caveats sit right under the number, because an owner should never read
   it as "Clefs made your GOP move". There is no target, tier or milestone
   anywhere here (6.7), by design.
   ═══════════════════════════════════════════════════════════════════════ */

import { useApi } from "../../../../../lib/api";
import { Badge, Card, EmptyState, MoneyText, Skeleton } from "../../../../../ui";
import { finApi, monthKey, monthLabel, moduleLabel, moduleShares } from "../lib";
import type { VerifiedProfit } from "../types";
import { errText, Money } from "./bits";

export default function VerifiedPanel({ slug, month, publishedMinor, publishedValue }: {
  slug: string;
  /** "2026-08" or "2026-08-01". */
  month: string;
  /** The figure frozen into the published month, to flag later verifications. */
  publishedMinor?: number | null;
  publishedValue?: string | null;
}) {
  const key = monthKey(month);
  const res = useApi<VerifiedProfit>(finApi(slug, `/verified-profit?month=${key}`), [slug, key]);
  const v = res.data;
  const shares = moduleShares(v?.by_module);
  const drift = v && publishedMinor !== null && publishedMinor !== undefined && publishedMinor !== v.verified_minor;

  return (
    <Card gold title="Clefs-verified profit" subtitle={monthLabel(key)}>
      {res.loading ? (
        <Skeleton lines={3} />
      ) : res.error ? (
        <EmptyState title="Could not load verified profit" body={errText(res.error)} />
      ) : v ? (
        <div className="fin-hero">
          <div>
            <div className="fin-hero__label">Verified this month</div>
            <div className="fin-hero__value fin-num"><Money value={v.verified_value} currency={v.currency} /></div>
            <div className="fin-hero__sub">
              <Money value={v.verified_par} currency={v.currency} /> per available room
              {v.available_rn ? ` across ${v.available_rn.toLocaleString("en")} available room nights` : ""}
              {" "}from {v.actions} verified {v.actions === 1 ? "action" : "actions"}.
            </div>
            {drift && (
              <div className="fin-small" style={{ marginTop: 6 }}>
                <Badge tone="info">Updated</Badge> The published month froze <Money value={publishedValue ?? null} currency={v.currency} />;
                actions verified since then are included here.
              </div>
            )}
          </div>
          <ModuleBars shares={shares} currency={v.currency} />
        </div>
      ) : null}
      <ul className="fin-caveats" style={{ marginTop: 14 }}>
        <li>Counts only actions Clefs could check against your own records (OTA statements, the PMS, the ledger), dated by the day each was verified.</li>
        <li>A reversed or clawed-back action counts as zero, so it never inflates this figure.</li>
        <li>Human follow-ups, such as investigating a P&L anomaly, never count toward it.</li>
        <li>It is not a share of GOP: GOP moves for many reasons Clefs does not control.</li>
      </ul>
    </Card>
  );
}

function ModuleBars({ shares, currency }: { shares: ReturnType<typeof moduleShares>; currency: string }) {
  if (!shares.length) {
    return <div className="fin-muted">Nothing verified this month yet. Recoveries and upsells appear here once their outcome is confirmed.</div>;
  }
  return (
    <div className="fin-strip" aria-label="Verified profit by module">
      {shares.map(s => (
        <div key={s.module} style={{ display: "grid", gap: 4 }}>
          <div className="fin-row" style={{ fontSize: 13 }}>
            <span>{moduleLabel(s.module)}</span>
            <span className="fin-num"><MoneyText amount={s.minor} minor currency={currency} /></span>
          </div>
          <div className="fin-bar" aria-hidden="true"><i style={{ width: `${Math.round(s.share * 100)}%` }} /></div>
        </div>
      ))}
      {shares.some(s => s.minor < 0) && (
        <div className="fin-small">A module below zero had more reversals than new verified outcomes this month.</div>
      )}
    </div>
  );
}
