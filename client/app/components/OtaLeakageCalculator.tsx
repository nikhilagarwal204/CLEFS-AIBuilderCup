"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { OTA_AUDIT_LIVE, PRIMARY_LEAD } from "../lib/seo";

/**
 * "What is one point of OTA leakage worth to my hotel?"
 *
 * SAME DESIGN RULE as DeskHoursCalculator: every input is the hotel's own number
 * and the output is arithmetic the owner can redo on paper. It asserts NO
 * leakage rate (03 section 6): no public UAE figure exists for how much of OTA
 * revenue is lost to payout or parity errors, and the proprietary Leakage Index
 * stays hidden until its sample is large enough (claims.ts, K5). So the result
 * is expressed per percentage point, and the only way to learn the real rate is
 * the free audit of one statement.
 *
 * Inputs start EMPTY on purpose. A pre-filled revenue or commission figure would
 * be a number we chose, sitting on screen as if it described his hotel.
 */

const parse = (s: string) => {
  const n = parseFloat(s.replace(/[^0-9.]/g, ""));
  return Number.isFinite(n) && n > 0 ? n : null;
};
const fmt = (n: number) => Math.round(n).toLocaleString("en-AE");

export default function OtaLeakageCalculator() {
  const id = useId();
  const [revenue, setRevenue] = useState("");   // monthly OTA room revenue, AED
  const [commission, setCommission] = useState(""); // contracted commission, percent

  const monthly = parse(revenue);
  const pct = parse(commission);
  const rateOk = pct !== null && pct < 100;
  const annual = monthly !== null ? monthly * 12 : null;
  const perPoint = annual !== null ? annual / 100 : null;
  const commissionYear = annual !== null && rateOk ? annual * (pct as number) / 100 : null;

  return (
    <div className="mk-olc" data-reveal>
      <style dangerouslySetInnerHTML={{ __html: olcStyles }} />

      <div className="mk-olc__grid">
        <label className="mk-olc__field" htmlFor={`${id}-rev`}>
          <span className="mk-olc__label">Room revenue your hotel books through OTAs in a normal month</span>
          <span className="mk-olc__row">
            <span className="mk-olc__cur">AED</span>
            <input
              id={`${id}-rev`} type="text" inputMode="decimal" value={revenue}
              onChange={(e) => setRevenue(e.target.value)}
              placeholder="From your OTA statements" className="mk-olc__input"
            />
          </span>
          <span className="mk-olc__hint">Add up one month of statements from every OTA you use.</span>
        </label>

        <label className="mk-olc__field" htmlFor={`${id}-com`}>
          <span className="mk-olc__label">Commission in your OTA contract</span>
          <span className="mk-olc__row">
            <input
              id={`${id}-com`} type="text" inputMode="decimal" value={commission}
              onChange={(e) => setCommission(e.target.value)}
              placeholder="From your contract" className="mk-olc__input mk-olc__input--sm"
            />
            <span className="mk-olc__cur">%</span>
          </span>
          <span className="mk-olc__hint">Use the rate your contract states, not what you think you pay.</span>
        </label>
      </div>

      <div className="mk-olc__out" aria-live="polite">
        {perPoint === null ? (
          <p className="mk-olc__empty">Type your monthly OTA room revenue to see the result.</p>
        ) : (
          <div className="mk-olc__outRow">
            <div>
              <div className="mk-olc__big clefs-gilt">AED {fmt(perPoint)}</div>
              <div className="mk-olc__cap">
                a year for every percentage point of OTA revenue lost to payout or parity errors
              </div>
            </div>
            {commissionYear !== null && (
              <div>
                <div className="mk-olc__big clefs-gilt">AED {fmt(commissionYear)}</div>
                <div className="mk-olc__cap">a year in OTA commission at your contracted rate</div>
              </div>
            )}
          </div>
        )}

        <p className="mk-olc__note">
          This is multiplication on your own numbers. We do not assume how much your hotel
          loses, because no reliable figure for UAE hotels exists yet.
          {OTA_AUDIT_LIVE && " The audit checks one real statement, line by line, and shows the actual amount."}
        </p>
        <Link href={PRIMARY_LEAD.href} className="cb-btn cb-btn--gold mk-olc__cta">{PRIMARY_LEAD.label}</Link>
      </div>
    </div>
  );
}

const olcStyles = `
  .mk-olc { margin-top: 44px; text-align: left; }
  .mk-olc__grid { display: grid; gap: 26px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
  .mk-olc__field { display: block; }
  .mk-olc__label { display: block; font-size: 0.82rem; line-height: 1.45; color: var(--clefs-ink-muted); margin-bottom: 10px; }
  .mk-olc__row { display: flex; align-items: center; gap: 10px; }
  .mk-olc__cur { font-size: 0.8rem; letter-spacing: 0.12em; color: var(--clefs-gold-600); flex: none; }
  .mk-olc__input {
    width: 100%; min-width: 0; padding: 12px 14px; border-radius: 4px;
    background: rgba(255,255,255,0.04); color: var(--clefs-ink);
    border: 1px solid var(--clefs-line); font-family: var(--clefs-serif); font-size: 1.3rem;
  }
  .mk-olc__input::placeholder { font-family: var(--clefs-sans); font-size: 0.86rem; color: var(--clefs-ink-faint); }
  .mk-olc__input:focus { outline: none; border-color: var(--clefs-line-gold); }
  .mk-olc__input--sm { max-width: 180px; }
  .mk-olc__hint { display: block; margin-top: 8px; font-size: 0.78rem; line-height: 1.55; color: var(--clefs-ink-faint); }
  .mk-olc__out { margin-top: 34px; padding-top: 28px; border-top: 1px solid var(--clefs-line); }
  .mk-olc__empty { margin: 0; font-size: 0.9rem; color: var(--clefs-ink-muted); }
  .mk-olc__outRow { display: grid; gap: 22px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .mk-olc__big { font-family: var(--clefs-serif); font-size: clamp(2rem, 4.6vw, 2.8rem); line-height: 1; overflow-wrap: anywhere; }
  .mk-olc__cap { margin-top: 8px; font-size: 0.86rem; line-height: 1.6; color: var(--clefs-ink-muted); max-width: 34ch; }
  .mk-olc__note { margin: 26px 0 0; font-size: 0.8rem; line-height: 1.7; color: var(--clefs-ink-faint); max-width: 62ch; }
  .mk-olc__cta { margin-top: 22px; }
`;
