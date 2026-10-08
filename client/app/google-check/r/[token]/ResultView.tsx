"use client";

/**
 * /google-check/r/<token>: teaser, unlock form, then the parity table and the
 * organic position. Every state comes from viewOf() in ../../_view.ts, which
 * is what guarantees the vendor disclosure sits above parity and rank.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import Claim from "../../../components/Claim";
import { UiStyles } from "../../../ui/core";
import { formatMoney, formatPct } from "../../../lib/format";
import { FAILURE_TEXT, NO_MATCH_TEXT, failureOf, getCheck, type CheckResult, type Failure } from "../../_lib";
import { showsParityOrRank, viewOf, type View } from "../../_view";
import { GC_STYLES } from "../../styles";
import UnlockForm from "./UnlockForm";
import Picker from "./Picker";

const PRICE_NOTE =
  "Your booking-engine landing price and taxes must match the price Google shows (Google hotel price accuracy policy 6064419).";

// ─── Pieces ──────────────────────────────────────────────────────────────────

function Teaser({ official }: { official: boolean | null }) {
  return (
    <div className="gc-card gc-card--gold">
      <p className="gc-answer">
        Official site price on Google: <em>{official === null ? "unknown" : official ? "yes" : "no"}</em>
      </p>
      <p className="gc-note">
        <Claim id="google_third_party_rates_unavailable_2026_09_30" surface="google_check" />;{" "}
        <Claim id="triptease_free_booking_links_unaffected" surface="google_check" />.
      </p>
    </div>
  );
}

/** "12 Oct to 13 Oct" for the stay the table shows (the first date pair). */
const STAY_DAY = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Dubai", day: "numeric", month: "short" });
function stayText(stay: [string, string] | undefined): string | null {
  if (!stay) return null;
  const d = (iso: string) => {
    const t = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00Z` : iso);
    return Number.isNaN(t.getTime()) ? iso : STAY_DAY.format(t);
  };
  return `${d(stay[0])} to ${d(stay[1])}`;
}

function Unlocked({ v, stay }: { v: Extract<View, { kind: "unlocked" }>; stay: string | null }) {
  // Cheapest first, so the undercut (if any) is the first row a visitor reads.
  const rows = [...v.parity].sort((a, b) => a.price_minor - b.price_minor);
  const lowest = rows[0]?.price_minor;
  return (
    <div className="gc-card">
      {/* Disclosure first: 04 section 2.4 puts it above the table and the rank. */}
      {v.disclosure && <p className="gc-disclosure">{v.disclosure} {PRICE_NOTE}</p>}
      {v.parity.length > 0 && (
        <div className="mk-table-wrap">
          <table className="mk-table">
            {stay && <caption className="gc-note" style={{ textAlign: "start", paddingBottom: 8 }}>Prices on Google Hotels for a stay {stay}</caption>}
            <thead>
              <tr><th scope="col">Source</th><th scope="col">Channel</th><th scope="col">Price</th></tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={`${p.source}-${p.channel}-${p.price_minor}`}>
                  <td>{p.source}{p.official && <> <span className="gc-official">(official site)</span></>}</td>
                  <td>{p.channel || "Other"}</td>
                  <td>
                    {formatMoney(p.price_minor, p.currency, "en-AE", { minor: true })}
                    {p.price_minor === lowest && rows.length > 1 && <> <span className="gc-official">lowest</span></>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {v.gap_minor !== null && v.gap_minor <= 0 && (
        <p className="gc-rank">Your official price was at or below every other price Google showed for the checked stays.</p>
      )}
      {v.gap_minor !== null && v.gap_minor > 0 && v.parity[0] && (
        <p className="gc-rank">
          Largest gap between your official price and the lowest other price across the checked stays:{" "}
          <strong>{formatMoney(v.gap_minor, v.parity[0].currency, "en-AE", { minor: true })}</strong>
          {v.gap_pct !== null && <> ({formatPct(v.gap_pct)})</>}
        </p>
      )}
      {v.rank !== null && (
        <p className="gc-rank">
          Organic position on these dates, not a ranking guarantee: <strong>{v.rank}</strong>
        </p>
      )}
    </div>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function ResultView({ token }: { token: string }) {
  const [result, setResult] = useState<CheckResult | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);

  useEffect(() => {
    let live = true;
    getCheck(token)
      .then((r) => { if (live) setResult(r); })
      .catch((e: unknown) => { if (live) setFailure(failureOf(e, true)); });
    return () => { live = false; };
  }, [token]);

  const v = viewOf(result, failure);
  const again = <Link className="cb-btn cb-btn--ghost" href="/google-check">Run a new check</Link>;

  return (
    <div className="gc">
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: GC_STYLES }} />
      {v.kind === "loading" && <p className="gc-msg" role="status">Loading your check...</p>}
      {v.kind === "failure" && <><p className="gc-alert" role="alert">{FAILURE_TEXT[v.failure]}</p>{again}</>}
      {v.kind === "no_match" && <><p className="gc-alert" role="status">{NO_MATCH_TEXT}</p>{again}</>}
      {v.kind === "needs_pick" && (result?.candidates?.length
        ? <Picker token={token} candidates={result.candidates} onPicked={setResult} />
        : <><p className="gc-msg">This check is waiting for you to pick your hotel.</p>{again}</>)}
      {v.kind === "teaser" && (
        <>
          <Teaser official={v.official_present} />
          <UnlockForm token={token} onUnlocked={(r) => setResult((prev) => ({ ...(prev ?? r), ...r }))} />
        </>
      )}
      {v.kind === "unlocked" && (
        <>
          <Teaser official={v.official_present} />
          {showsParityOrRank(v)
            ? <Unlocked v={v} stay={stayText(result?.unlocked?.stay_dates?.[0])} />
            : <p className="gc-msg">Google showed no prices for these dates.</p>}
          {again}
        </>
      )}
    </div>
  );
}
