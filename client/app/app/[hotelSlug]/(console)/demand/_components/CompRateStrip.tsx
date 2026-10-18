"use client";

/* ═══════════════════════════════════════════════════════════════════════
   CompRateStrip: the night's competitor band (min, median, max) with the
   hotel's own BAR as a line on the same scale, then each competitor's rate.
   Every figure is public shopping data, never a contract rate, so the
   "Indicative" label sits on the strip and on the list (02 spec 4.2).
   Sold out is shown as a demand signal, never as a zero price.
   ═══════════════════════════════════════════════════════════════════════ */

import { useFormat } from "../../../../../lib/format";
import { Badge } from "../../../../../ui";
import { stripGeometry } from "../_lib/model";
import type { CompRateRow, NightBand } from "../_lib/types";

export default function CompRateStrip({
  band, rates, bar, barLabel, label = "Indicative",
}: {
  band: NightBand | null;
  rates: readonly CompRateRow[];
  /** Own BAR for the reference room type (Decimal string), if known. */
  bar: string | null;
  barLabel?: string;
  label?: string;
}) {
  const fmt = useFormat();
  const money = (v: string | null) => fmt.money(v, { maximumFractionDigits: 0, currency: band?.currency ?? undefined });
  const g = stripGeometry(band && band.priced > 0 ? band : null, bar);

  if (!g && !rates.length) {
    return (
      <p className="dr-note">
        No competitor rates for this night yet <span className="dr-label-ind">{label}</span>. Rates arrive with the daily
        rate check once a comp set is saved in Settings.
      </p>
    );
  }

  const bandLeft = g?.min ?? null;
  const bandRight = g?.max ?? null;
  return (
    <div>
      <div className="cx-row" style={{ justifyContent: "space-between" }}>
        <span className="dr-small dr-muted">
          {band && band.priced > 0 ? `${band.priced} priced` : "No priced competitors"}
          {band && band.sold_out > 0 ? `, ${band.sold_out} sold out` : ""}
          {band?.captured_at ? `, checked ${fmt.relative(band.captured_at)}` : ""}
        </span>
        <span className="dr-label-ind" title="Public shopping prices, not contract rates">{label}</span>
      </div>

      {g && (
        <div className="dr-strip" role="img"
             aria-label={`${label} competitor rates: low ${money(band?.min ?? null)}, median ${money(band?.median ?? null)}, high ${money(band?.max ?? null)}${bar ? `; your BAR ${money(bar)}` : ""}`}>
          <div className="dr-strip__rail" />
          {bandLeft !== null && bandRight !== null && (
            <div className="dr-strip__band" style={{ left: `${bandLeft}%`, width: `${Math.max(0.5, bandRight - bandLeft)}%` }} />
          )}
          {g.median !== null && <div className="dr-strip__tick" style={{ left: `${g.median}%` }} />}
          {g.bar !== null && <div className="dr-strip__bar" style={{ left: `${g.bar}%` }} />}
          {g.bar !== null && (
            <span className="dr-strip__lbl dr-strip__lbl--top" style={{ left: `${g.bar}%` }}>{barLabel ?? "Your BAR"} {money(bar)}</span>
          )}
          {g.min !== null && <span className="dr-strip__lbl dr-strip__lbl--bot" style={{ left: `${g.min}%` }}>{money(band?.min ?? null)}</span>}
          {g.median !== null && g.min !== g.median && g.max !== g.median && (
            <span className="dr-strip__lbl dr-strip__lbl--bot" style={{ left: `${g.median}%` }}>median {money(band?.median ?? null)}</span>
          )}
          {g.max !== null && g.max !== g.min && (
            <span className="dr-strip__lbl dr-strip__lbl--bot" style={{ left: `${g.max}%` }}>{money(band?.max ?? null)}</span>
          )}
        </div>
      )}

      {rates.length > 0 && (
        <div className="dr-rates" aria-label={`${label} competitor rates`}>
          {rates.map(r => (
            <div key={`${r.comp_id}-${r.source}`} className="dr-rates__row">
              <span title={r.competitor}>
                {r.competitor}
                {r.is_demo && <span className="dr-faint"> (demo)</span>}
              </span>
              <span className="cx-row" style={{ gap: 6 }}>
                {r.sold_out ? <Badge tone="bad">Sold out</Badge> : <b>{money(r.rate)}</b>}
                {r.fx_converted && <span className="dr-faint dr-small" title="Converted from the vendor's currency">fx</span>}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
