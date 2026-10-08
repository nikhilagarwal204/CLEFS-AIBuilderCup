"use client";

/**
 * UAE OTA Leakage Index on /free-ota-audit (review completeness-09; PLAN:
 * built from consented audits and "published only when n is at least 30,
 * with its methodology"). The figures come live from GET
 * /public/leakage-index; below the minimum the server sends no figure and
 * this section renders NOTHING (no placeholder, no count), so the page
 * reads the same until the index exists. The claim registry entry
 * (claims.ts "uae_ota_leakage_index") stays the static gate for any
 * marketing copy; data-claim ties this render to it.
 */

import { useEffect, useState } from "react";
import { getLeakageIndex } from "./_lib";
import { METHOD_ORDER, OTA_LABEL, day, indexVisible, rateText } from "./_view";
import type { LeakageIndex as Index } from "./_types";

export default function LeakageIndex() {
  const [ix, setIx] = useState<Index | null>(null);

  useEffect(() => {
    let live = true;
    // A failure is silent: the index is optional, the audit is the page.
    getLeakageIndex().then((r) => { if (live) setIx(r); }).catch(() => undefined);
    return () => { live = false; };
  }, []);

  if (!ix || !indexVisible(ix)) return null;
  const period = ix.period ? `${day(ix.period.from)} to ${day(ix.period.to)}` : null;

  return (
    <section className="oa-card oa-card--wide" aria-labelledby="oa-index-h" data-claim={ix.claim_id}>
      <h2 id="oa-index-h" className="oa-h">{ix.title}</h2>
      <p className="oa-sub">
        Median leak rate <strong>{rateText(ix.median_leak_rate)}</strong> of the commission charged (middle half{" "}
        {rateText(ix.p25_leak_rate)} to {rateText(ix.p75_leak_rate)}); {rateText(ix.share_with_leak)} of statements
        had at least one finding. Based on {ix.n} consented audits{period ? `, ${period}` : ""}.
      </p>
      {!!ix.by_ota?.length && (
        <ul className="oa-limits">
          {ix.by_ota.map((o) => (
            <li key={o.ota_code}>
              <strong>{OTA_LABEL[o.ota_code] ?? o.ota_code}</strong>
              median {rateText(o.median_leak_rate)}, {o.n} audits
            </li>
          ))}
        </ul>
      )}
      <details>
        <summary>Methodology</summary>
        <dl>
          {METHOD_ORDER.filter(([k]) => ix.methodology?.[k]).map(([k, h]) => (
            <div key={k}><dt><strong>{h}</strong></dt><dd className="oa-note">{ix.methodology[k]}</dd></div>
          ))}
        </dl>
      </details>
    </section>
  );
}
