"use client";

/* ConventionPreview: type a guest-paid price for one night and see how it
   splits into room revenue and each fee under conventions A, B and C, with
   the rules in force on the chosen date. The point is the decision the owner
   makes in the profile above: the same AED 500 is AED 396.49 of room
   revenue under A and 401.65 under C, and every Net ADR, OTA commission
   check and Tourism Dirham figure downstream follows that choice. */

import { useMemo, useState } from "react";
import { currencyDigits, useFormat } from "../../../../../lib/format";
import { Badge, Field, Input } from "../../../../../ui";
import { FEE_LABEL, type TaxProfile } from "./api";
import {
  CONVENTIONS, decompose, normalizeRules, parseMajor, PERCENT_ORDER, rateUnits, ratesOn, tdMinor,
  type Breakdown, type FeeRule, type ResolvedRates,
} from "./convention";

export interface ConventionPreviewProps {
  rules: readonly FeeRule[];
  profile: TaxProfile | null;
  currency: string;
  /** Hotel-local business date (YYYY-MM-DD), the default stay date. */
  today: string;
}

const NO_RATES: ResolvedRates = { fees: [], tdRate: null, tdCap: null, tdPerBedroom: false, winners: [] };

export function ConventionPreview({ rules, profile, currency, today }: ConventionPreviewProps) {
  const fmt = useFormat();
  const [gross, setGross] = useState("500");
  const [day, setDay] = useState(today);
  const [units, setUnits] = useState("1");
  const [tdLiable, setTdLiable] = useState(true);
  const digits = currencyDigits(currency);
  const mine = profile?.convention ?? "A";

  // Rates are normalized here as well as at the page's fetch because Profit
  // Guard's TaxView hands its rules in raw. Every winning rate is parsed once
  // up front: one the mirror cannot read degrades to a note below instead of
  // throwing mid-render and taking the whole tax page down (OWN-06).
  const { rates, bad } = useMemo(() => {
    try {
      const r = ratesOn(normalizeRules(rules), day || today, profile?.property_category ?? null, profile?.td_per_bedroom ?? null);
      for (const f of r.fees) rateUnits(f.rate);
      if (r.tdRate) rateUnits(r.tdRate);
      return { rates: r, bad: null };
    } catch (e) {
      return { rates: NO_RATES, bad: e instanceof Error ? e.message : String(e) };
    }
  }, [rules, day, today, profile]);
  const grossMinor = parseMajor(gross, digits);
  const n = Math.max(1, Math.min(20, Number(units) || 1));
  const td = tdLiable ? tdMinor(rates.tdRate, rates.tdPerBedroom ? n : 1, digits) : BigInt(0);

  const results = useMemo<Breakdown[] | null>(() => {
    // No split at all beats a split that silently leaves an unreadable fee out.
    if (bad || grossMinor === null || grossMinor <= td) return null;
    return CONVENTIONS.map(c => decompose(grossMinor, td, rates.fees, c));
  }, [bad, grossMinor, td, rates]);

  // Only the components some rule actually charges on this date.
  const shown = PERCENT_ORDER.filter(c => rates.fees.some(f => f.code === c));
  const money = (v: bigint) => fmt.money(v, { minor: true, currency });

  return (
    <div className="cx-stack" style={{ gap: 14 }}>
      <div className="tax-preview-inputs">
        <Field label={`Guest pays for one night (${currency})`}>
          <Input inputMode="decimal" value={gross} onChange={e => setGross(e.target.value.replace(/[^\d.,]/g, ""))} />
        </Field>
        <Field label="Stay date">
          <Input type="date" value={day} onChange={e => setDay(e.target.value)} />
        </Field>
        {rates.tdPerBedroom && (
          <Field label="Bedrooms (TD per bedroom)">
            <Input inputMode="numeric" value={units} onChange={e => setUnits(e.target.value.replace(/[^\d]/g, ""))} />
          </Field>
        )}
      </div>
      <label className="tax-switch">
        <input type="checkbox" checked={tdLiable} onChange={e => setTdLiable(e.target.checked)} />
        The guest stayed this night (a no-show or night 31 onwards owes no Tourism Dirham)
      </label>

      {bad && (
        <p className="tax-bad" role="alert">A fee rule has a rate the preview cannot read ({bad}). The fee rules table below still lists it.</p>
      )}
      {!bad && !rates.fees.length && !rates.tdRate && (
        <p className="tax-note">No fee rules apply on this date. Set the emirate and category in the hotel profile first.</p>
      )}
      {rates.tdRate === null && rates.fees.length > 0 && (
        <p className="tax-note">No Tourism Dirham rate for this hotel: set the property category below.</p>
      )}

      {results ? (
        <div className="tax-cmp">
          <table>
            <thead>
              <tr>
                <th scope="col">Component</th>
                {results.map(r => (
                  <th key={r.convention} scope="col" data-mine={r.convention === mine || undefined}>
                    {r.convention} {r.convention === mine && <Badge tone="gold">Yours</Badge>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Room revenue</td>
                {results.map(r => <td key={r.convention} data-mine={r.convention === mine || undefined}>{money(r.room)}</td>)}
              </tr>
              {shown.map(c => (
                <tr key={c}>
                  <td>{FEE_LABEL[c] ?? c}</td>
                  {results.map(r => <td key={r.convention} data-mine={r.convention === mine || undefined}>{money(r.parts[c])}</td>)}
                </tr>
              ))}
              <tr>
                <td>{FEE_LABEL.TD}</td>
                {results.map(r => <td key={r.convention} data-mine={r.convention === mine || undefined}>{money(r.td)}</td>)}
              </tr>
              <tr data-total>
                <td>Guest pays</td>
                {results.map(r => <td key={r.convention} data-mine={r.convention === mine || undefined}>{money(r.gross)}</td>)}
              </tr>
              <tr data-sub>
                <td>Price per 1 of room revenue (before TD)</td>
                {results.map(r => <td key={r.convention} data-mine={r.convention === mine || undefined}>{r.factor}</td>)}
              </tr>
            </tbody>
          </table>
        </div>
      ) : !bad && (
        <p className="tax-note">
          {grossMinor === null ? "Type a price to see the split." : "The price must be more than the Tourism Dirham fee."}
        </p>
      )}
      <p className="tax-note">
        Each fee is rounded once to the smallest {currency} unit and room revenue takes the rounding remainder,
        exactly as Clefs splits imported stays.
      </p>
    </div>
  );
}
