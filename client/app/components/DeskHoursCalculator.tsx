"use client";

import { useState } from "react";

/**
 * "How many hours is your desk on the phone?"
 *
 * DESIGN RULE, and the reason this is not like other vendors' ROI calculators:
 * every input is the HOTEL'S OWN NUMBER. We assert nothing.
 *
 * The competitor calculators in this category (HiJiffy's is the only public one)
 * are built on invented constants: a fixed labour rate, a fixed average handle
 * time, a fixed share of calls deemed automatable. A GM who changes one
 * assumption watches the answer collapse, and the number was never his to begin
 * with. Worse for us, no citable figure for hotel call volume exists - every
 * statistic in circulation traces back to vendor blogs with no methodology.
 *
 * So the output is HOURS, which is multiplication the GM can check on paper, and
 * the money line only appears if he types in his own staff cost. We never put a
 * salary figure on screen that we invented.
 *
 * UAE: money is AED, formatted en-AE (Western digits, comma thousands). The
 * currency is a text label, not a symbol, because the dirham sign is not yet
 * safe in every font the site loads.
 */

const fmt = (n: number) => Math.round(n).toLocaleString("en-AE");

export default function DeskHoursCalculator() {
  const [calls, setCalls] = useState(60);       // calls to the desk on a normal day
  const [minutes, setMinutes] = useState(3);    // average length of one call
  const [routine, setRoutine] = useState(50);   // share that are the same few questions
  const [cost, setCost] = useState("");         // optional: cost of one staff hour

  const hoursMonth = (calls * minutes * 30) / 60;
  const routineHours = hoursMonth * (routine / 100);
  const costNum = parseFloat(cost.replace(/[^0-9.]/g, ""));
  const money = !isNaN(costNum) && costNum > 0 ? routineHours * costNum : null;

  return (
    <div className="dhc" data-reveal>
      <style dangerouslySetInnerHTML={{ __html: styles }} />

      <div className="dhc__grid">
        <label className="dhc__field">
          <span className="dhc__label">Calls your front desk takes on a normal day</span>
          <input
            type="number" min={0} max={2000} value={calls}
            onChange={(e) => setCalls(Math.max(0, Number(e.target.value) || 0))}
            className="dhc__input" inputMode="numeric"
          />
          <span className="dhc__hint">Count them for one day. That is the only number we need from you.</span>
        </label>

        <label className="dhc__field">
          <span className="dhc__label">
            Average length of a call
            <strong className="dhc__val">{minutes} min</strong>
          </span>
          <input
            type="range" min={1} max={10} step={1} value={minutes}
            onChange={(e) => setMinutes(Number(e.target.value))}
            className="dhc__range"
          />
        </label>

        <label className="dhc__field">
          <span className="dhc__label">
            Share that are the same few questions
            <strong className="dhc__val">{routine}%</strong>
          </span>
          <input
            type="range" min={0} max={100} step={5} value={routine}
            onChange={(e) => setRoutine(Number(e.target.value))}
            className="dhc__range"
          />
          <span className="dhc__hint">WiFi, checkout time, gym hours, breakfast. Set this from what you hear at your own desk.</span>
        </label>
      </div>

      <div className="dhc__out">
        <div className="dhc__outRow">
          <div>
            <div className="dhc__big clefs-gilt">{fmt(hoursMonth)}</div>
            <div className="dhc__cap">hours a month your desk spends on the phone</div>
          </div>
          <div>
            <div className="dhc__big clefs-gilt">{fmt(routineHours)}</div>
            <div className="dhc__cap">of those on questions Clefs can answer</div>
          </div>
        </div>

        <div className="dhc__money">
          <label className="dhc__moneyLabel">
            <span>If you want the dirham figure, type what one staff hour costs you</span>
            <div className="dhc__moneyRow">
              <span className="dhc__cur">AED</span>
              <input
                type="text" value={cost} placeholder="Your cost"
                onChange={(e) => setCost(e.target.value)}
                className="dhc__input dhc__input--sm" inputMode="decimal"
                aria-label="Cost of one staff hour in AED"
              />
              <span className="dhc__per">per hour</span>
            </div>
          </label>
          {money !== null && (
            <p className="dhc__result">
              That is about <strong className="clefs-gilt">AED {fmt(money)}</strong> a month of staff time
              going into questions that have the same answer every time.
            </p>
          )}
        </div>

        {/* The honest footnote is the point of the whole component. */}
        <p className="dhc__note">
          Every number here is yours. We have not assumed anything, and we have not used an
          industry average, because no reliable one exists for hotel call volume. If the
          figure looks wrong, change the inputs until it matches your property.
        </p>
      </div>
    </div>
  );
}

const styles = `
  .dhc { margin-top: 44px; text-align: left; }
  .dhc__grid { display: grid; gap: 26px; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }
  .dhc__field { display: block; }
  .dhc__label {
    display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
    font-size: 0.82rem; line-height: 1.45; color: var(--clefs-ink-muted); margin-bottom: 10px;
  }
  .dhc__val { font-family: var(--clefs-serif); font-size: 1.05rem; color: var(--clefs-gold-400); flex: none; }
  .dhc__input {
    width: 100%; padding: 12px 14px; border-radius: 4px;
    background: rgba(255,255,255,0.04); color: var(--clefs-ink);
    border: 1px solid var(--clefs-line); font-family: var(--clefs-serif); font-size: 1.3rem;
  }
  .dhc__input:focus { outline: none; border-color: var(--clefs-line-gold); }
  .dhc__input--sm { font-size: 1.05rem; padding: 9px 12px; max-width: 130px; }
  .dhc__hint { display: block; margin-top: 8px; font-size: 0.78rem; line-height: 1.55; color: var(--clefs-ink-faint); }
  .dhc__range { width: 100%; accent-color: var(--clefs-gold-500); }

  .dhc__out { margin-top: 34px; padding-top: 28px; border-top: 1px solid var(--clefs-line); }
  .dhc__outRow { display: grid; gap: 22px; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
  .dhc__big { font-family: var(--clefs-serif); font-size: clamp(2.2rem, 5vw, 3.1rem); line-height: 1; }
  .dhc__cap { margin-top: 8px; font-size: 0.86rem; line-height: 1.6; color: var(--clefs-ink-muted); max-width: 30ch; }

  .dhc__money { margin-top: 30px; }
  .dhc__moneyLabel { display: block; font-size: 0.82rem; color: var(--clefs-ink-muted); }
  .dhc__moneyRow { display: flex; align-items: center; gap: 9px; margin-top: 10px; }
  .dhc__cur { font-family: var(--clefs-sans); font-size: 0.8rem; letter-spacing: 0.12em; color: var(--clefs-gold-600); }
  .dhc__per { font-size: 0.82rem; color: var(--clefs-ink-faint); }
  .dhc__result { margin: 16px 0 0; font-size: 1rem; line-height: 1.7; color: var(--clefs-ink); max-width: 52ch; }

  .dhc__note {
    margin: 26px 0 0; font-size: 0.8rem; line-height: 1.7;
    color: var(--clefs-ink-faint); max-width: 62ch;
  }

  @media (max-width: 620px) {
    .dhc__grid { gap: 22px; }
    .dhc__outRow { gap: 18px; }
  }
`;
