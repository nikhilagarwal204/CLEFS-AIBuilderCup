"use client";

/**
 * The full report (unlocked only): the findings that count (L3, L12), grouped
 * by detector, each row with its booking, masked guest, stay, amount and
 * calculation; then the lines to confirm with the hotel (fees, percentages
 * above the assumed one) with the same rows and calculation but no total;
 * then Agoda's tax-inclusive rate, for information; the open deadlines, what
 * was not checked and the assumptions. Guests arrive already reduced to
 * initials by _view.groupFindings.
 *
 * WHY Agoda's row has its own card: see _view.reportCards. Under "To confirm
 * with you" it also carried an "If confirmed" amount on a charge the contract
 * allows.
 *
 * WHY no fee-scenario totals and no Agoda sum: each was an AED figure resting
 * on what the statement cannot show (whether the municipality fee and Tourism
 * Dirham were collected, how they are set up, the contract). The rows keep
 * both readings and their calculation for the founder's review with the hotel.
 */

import { Badge, Button, type Tone } from "../../../ui/core";
import {
  TEASER_SUBLINE, confirmLines, confirmText, day, hoursLeft, money, moneyMinor, reportCards, when,
  type DetectorGroup,
} from "../../_view";
import type { Finding, FullReport, Teaser } from "../../_types";

// ─── Labels ──────────────────────────────────────────────────────────────────

const MISSING: Record<string, string> = {
  fee_collected: "whether a no-show or cancellation fee was collected",
  contract: "your contract's commission",
  programs: "the programmes you joined (for example Preferred or Visibility Booster)",
  charge_configuration: "how the fees are set up on your extranet",
  price_breakdown: "whether the municipality fee and Tourism Dirham were collected in the price",
};

const STATUS: Record<string, string> = {
  OK: "Stayed", NO_SHOW: "No-show", CANCELLED: "Cancelled", MODIFIED: "Modified",
};

const CONFIDENCE = (c: string): { tone: Tone; text: string } => {
  const n = Number(c);
  if (!Number.isFinite(n)) return { tone: "neutral", text: "Estimate" };
  if (n >= 0.75) return { tone: "ok", text: "Likely" };
  if (n >= 0.5) return { tone: "warn", text: "Worth checking" };
  return { tone: "neutral", text: "Possible" };
};

// A line to confirm carries no likelihood word: the contract decides it.
const TO_CONFIRM_BADGE = { tone: "info", text: "To confirm" } as const;
const INFO_BADGE = { tone: "neutral", text: "For information" } as const;

// ─── Pieces ──────────────────────────────────────────────────────────────────

function Calc({ f }: { f: Finding }) {
  if (!f.calc?.length && !f.missing?.length) return null;
  return (
    <details className="oa-calc">
      <summary>How it was worked out</summary>
      <ul className="oa-calc">
        {f.calc.map((s, i) => (
          <li key={i}>
            {s.label}: {s.formula}
            {typeof s.result_minor === "number" ? ` = ${moneyMinor(s.result_minor, f.currency, f.amount)}` : s.result ? ` = ${s.result}` : ""}
          </li>
        ))}
        {f.missing?.length > 0 && <li>Could not see: {f.missing.map((m) => MISSING[m] ?? m.replace(/_/g, " ")).join("; ")}.</li>}
      </ul>
    </details>
  );
}

function Deadline({ f }: { f: Finding }) {
  if (!f.deadline_at) return null;
  const h = hoursLeft(f.deadline_at);
  if (f.window_open && h !== null && h >= 0) {
    return <Badge tone={h <= 72 ? "bad" : "warn"} dot>Dispute by {when(f.deadline_at)}</Badge>;
  }
  return <Badge tone="neutral">Window closed {day(f.deadline_at)}</Badge>;
}

// What the amount column means in each section: a counted finding, an amount
// only if the hotel confirms the line, or a figure shown for information.
const AMOUNT_HEAD: Record<DetectorGroup["section"], string> = {
  found: "Amount", confirm: "If confirmed", info: "Not counted",
};

function Group({ g }: { g: DetectorGroup }) {
  const counted = g.section === "found";
  const id = `oa-g-${g.section}-${g.detector}`;
  return (
    <section className="oa-group" aria-labelledby={id}>
      <div className="oa-group__head">
        <h3 id={id}>{g.title} ({g.count})</h3>
        {/* Only a counted group has a total: a line to confirm is never summed. */}
        {g.total && <span>{g.total}</span>}
      </div>
      {g.blurb && <p className="oa-sub">{g.blurb}</p>}
      <div className="oa-tbl-wrap">
        <table className="oa-tbl">
          <thead>
            <tr>
              <th>Booking</th><th>Guest</th><th>Stay</th><th>Status</th><th>Detail</th>
              <th style={{ textAlign: "end" }}>{AMOUNT_HEAD[g.section]}</th>
            </tr>
          </thead>
          <tbody>
            {g.findings.map((f, i) => {
              const conf = counted ? CONFIDENCE(f.confidence) : g.section === "info" ? INFO_BADGE : TO_CONFIRM_BADGE;
              return (
                <tr key={`${f.reservation ?? "row"}-${f.row ?? i}-${i}`}>
                  <td>{f.reservation ?? "n/a"}{f.row ? <div className="oa-note">Row {f.row}{f.page ? `, page ${f.page}` : ""}</div> : null}</td>
                  <td>{f.guest ?? "n/a"}</td>
                  <td>{f.check_in ? `${day(f.check_in)}${f.check_out ? ` to ${day(f.check_out)}` : ""}` : "n/a"}</td>
                  <td>{f.status ? STATUS[f.status] ?? f.status : "n/a"}</td>
                  <td>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      <Badge tone={conf.tone}>{conf.text}</Badge>
                      <Deadline f={f} />
                    </div>
                    <Calc f={f} />
                  </td>
                  <td className="oa-num">{money(f.amount, f.currency)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ─── Report ──────────────────────────────────────────────────────────────────

export default function Report({ teaser, full, groups }: { teaser: Teaser; full: FullReport; groups: DetectorGroup[] }) {
  const ccy = teaser.currency || "AED";
  const { counted, confirm: toConfirm, info } = reportCards(groups);
  const lines = confirmLines(teaser);
  const open = (full.deadlines ?? []).filter((d) => (hoursLeft(d.deadline_at) ?? -1) >= 0);
  const a = full.assumptions ?? {};

  return (
    <>
      <section className="oa-card oa-card--wide" aria-labelledby="oa-findings-h">
        <h2 id="oa-findings-h" className="oa-h">Findings by check</h2>
        {counted.length > 0 ? (
          counted.map((g) => <Group key={`${g.section}-${g.detector}`} g={g} />)
        ) : (
          <p className="oa-sub">
            {toConfirm.length > 0
              ? "No line counts as a finding without your contract and PMS. The lines below are to confirm with you."
              : "The checks found nothing to dispute in this statement."}
          </p>
        )}
        <p className="oa-note">Guest names are shown as initials only.</p>
      </section>

      {toConfirm.length > 0 && (
        <section className="oa-card oa-card--wide" aria-labelledby="oa-confirm-h">
          <h2 id="oa-confirm-h" className="oa-h">To confirm with you</h2>
          <p className="oa-sub">
            {lines > 0 && <><strong>{confirmText(lines)}</strong>, not added to any total. </>}
            {TEASER_SUBLINE}
          </p>
          {toConfirm.map((g) => <Group key={`${g.section}-${g.detector}`} g={g} />)}
        </section>
      )}

      {info.length > 0 && (
        <section className="oa-card oa-card--wide" aria-labelledby="oa-info-h">
          <h2 id="oa-info-h" className="oa-h">For information, not counted</h2>
          {info.map((g) => <Group key={`${g.section}-${g.detector}`} g={g} />)}
        </section>
      )}

      {open.length > 0 && (
        <section className="oa-card oa-card--gold" aria-labelledby="oa-dl-h">
          <h2 id="oa-dl-h" className="oa-h">Deadlines still open</h2>
          <ul className="oa-counts">
            {open.map((d, i) => (
              <li key={`${d.reservation ?? i}-${d.deadline_at}`}>
                <span>Booking {d.reservation ?? "n/a"}: report by {when(d.deadline_at)}</span>
                <strong>{money(d.amount, ccy)}</strong>
              </li>
            ))}
          </ul>
          <p className="oa-note">A no-show reported after the OTA&apos;s window is usually charged in full.</p>
        </section>
      )}

      <section className="oa-card oa-card--wide" aria-labelledby="oa-limits-h">
        <h2 id="oa-limits-h" className="oa-h">What this audit could not check</h2>
        {full.not_checked?.length > 0 && (
          <ul className="oa-list">
            {full.not_checked.map((n) => <li key={n.detector}>{n.reason}</li>)}
          </ul>
        )}
        <h3 className="oa-sub"><strong>Assumptions</strong></h3>
        <ul className="oa-list">
          <li>A {a.city ?? "Dubai"} hotel of {a.star ?? 3} stars.</li>
          {a.fees && <li>Fees: {a.fees}.</li>}
          {full.skipped?.other_currency_lines > 0 && <li>{full.skipped.other_currency_lines} lines in another currency were counted but not checked.</li>}
        </ul>
        {full.notes?.map((n, i) => <p key={i} className="oa-note">{n}</p>)}
      </section>

      <section className="oa-card oa-card--gold" aria-labelledby="oa-next-h">
        <h2 id="oa-next-h" className="oa-h">Check every statement, with your contract</h2>
        <p className="oa-sub">
          Profit Guard reads every statement against your contract, your PMS and your payouts, prepares the dispute
          pack for each finding and counts a recovery only when the credit shows up.
        </p>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Button href="/pilot" variant="primary">How the pilot works</Button>
          <Button href="/profit-guard" variant="ghost">About Profit Guard</Button>
        </div>
        <p className="oa-note">We will be in touch on the contact you gave. Questions: reply to that message.</p>
      </section>
    </>
  );
}
