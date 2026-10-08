"use client";

/**
 * The teaser (always shown): the headline, the statement's facts and the
 * number of findings per check. No finding row, booking or guest is ever
 * rendered here; those need the lead unlock.
 *
 * WHY the headline can be a count: only RECOVERABLE findings (L3, L12) reach
 * an AED figure. Commission on fees (L1, L1b) and lines above the assumed
 * percentage (L2) are lines to confirm with the hotel, so a statement holding
 * only those leads with how many lines need confirming, never with an amount
 * and never with "nothing found".
 */

import {
  TEASER_SUBLINE, confirmLines, confirmText, day, figureOk, moneyOf, otaLabel, rangeText, teaserLead, teaserRows,
} from "../../_view";
import type { Teaser } from "../../_types";

export default function TeaserCard({
  teaser, hotel, expiresAt,
}: { teaser: Teaser; hotel: string | null; expiresAt: string | null }) {
  const rows = teaserRows(teaser);
  const ccy = teaser.currency || "AED";
  const lead = teaserLead(teaser);
  const confirm = confirmLines(teaser);
  const who = hotel ? `${hotel}: ` : "";

  return (
    <section className="oa-card oa-card--gold" aria-labelledby="oa-teaser-h">
      <h2 id="oa-teaser-h" className="oa-sr">Summary</h2>
      {lead === "nothing" && <p className="oa-headline">No overcharges found in this statement.</p>}
      {/* A teaser from an API older than the public classes has no figure we
          may show (figureOk): it leads with its per-check rows instead. */}
      {lead === "found" && (
        <p className="oa-headline">
          {figureOk(teaser)
            ? <>{who}<em>{rangeText(teaser)}</em> in commission to look at</>
            : <>{who}Findings in this statement</>}
        </p>
      )}
      {lead === "confirm" && (
        <p className="oa-headline">
          {who}<em>{confirmText(confirm)}</em>
        </p>
      )}
      {lead !== "nothing" && <p className="oa-sub">{TEASER_SUBLINE}</p>}
      <dl className="oa-facts">
        <div><dt>OTA</dt><dd>{otaLabel(teaser.ota_code)}</dd></div>
        <div><dt>Stays</dt><dd>{teaser.period ? `${day(teaser.period.from)} to ${day(teaser.period.to)}` : "n/a"}</dd></div>
        <div><dt>Lines checked</dt><dd>{teaser.lines.toLocaleString("en-AE")}</dd></div>
        <div><dt>Commission charged</dt><dd>{moneyOf(teaser.commission, ccy)}</dd></div>
      </dl>
      {rows.length > 0 && (
        <ul className="oa-counts" aria-label="Findings by check">
          {rows.map((r) => (
            <li key={r.detector}><span>{r.title}</span><strong>{r.count}</strong></li>
          ))}
        </ul>
      )}
      {lead === "found" && confirm > 0 && (
        <p className="oa-sub">
          Plus <strong>{confirmText(confirm)}</strong>, not counted in the figure above.
        </p>
      )}
      {teaser.open_deadlines > 0 && (
        <p className="oa-sub">
          <strong>{teaser.open_deadlines}</strong> {teaser.open_deadlines === 1 ? "no-show is" : "no-shows are"} still
          inside the OTA&apos;s dispute window. The deadlines are in the full report.
        </p>
      )}
      {expiresAt && <p className="oa-note">This report and the file are deleted on {day(expiresAt)}.</p>}
    </section>
  );
}
