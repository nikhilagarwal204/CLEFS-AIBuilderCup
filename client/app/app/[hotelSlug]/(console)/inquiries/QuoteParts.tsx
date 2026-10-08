"use client";

/* Presentational parts of the quote editor: the per-night grid with each
   night's guardrail band, the tax lines, and the terms. No fetching here;
   QuoteEditor owns state and calls. */

import { useFormat } from "../../../../lib/format";
import { Badge, MoneyText } from "../../../../ui";
import { lineKey, majorToMinor, rateBand, type RateBand } from "./lib";
import type { Quote, QuoteLine } from "./types";

const BAND_TEXT: Record<RateBand, string> = {
  at_bar: "At or above BAR",
  below_bar: "Below BAR",
  below_floor: "Below floor",
  above_ceiling: "Above ceiling",
  missing: "Rate needed",
  no_guardrails: "No guardrails",
};

const SOURCE_TEXT: Record<QuoteLine["source"], string> = {
  guardrails: "Recommended",
  staff: "Edited",
  manual: "Typed",
};

// ─── Night grid ──────────────────────────────────────────────────────────

export function NightGrid({ quote, typed, editable, digits, onType, toInput }: {
  quote: Quote;
  typed: Record<string, string>;
  editable: boolean;
  digits: number;
  onType: (key: string, value: string) => void;
  toInput: (minor: number | null) => string;
}) {
  const fmt = useFormat();
  const lines = [...quote.lines].sort((a, b) =>
    a.stay_date.localeCompare(b.stay_date) || (a.room_type_code ?? "").localeCompare(b.room_type_code ?? ""));
  const mixed = new Set(lines.map(l => l.room_type_code ?? "")).size > 1;

  return (
    <div className="inq-grid-wrap">
      <table className="inq-grid">
        <thead>
          <tr>
            <th scope="col">Night</th>
            {mixed && <th scope="col">Room</th>}
            <th scope="col" className="inq-num">Rooms</th>
            <th scope="col" className="inq-num">BAR</th>
            <th scope="col">Floor to ceiling</th>
            <th scope="col" className="inq-num">Rate</th>
            <th scope="col">Check</th>
          </tr>
        </thead>
        <tbody>
          {lines.map(ln => {
            const k = lineKey(ln);
            const t = typed[k];
            const shown = t ?? toInput(ln.rate_minor);
            const rate = t === undefined ? ln.rate_minor : majorToMinor(t, digits);
            const band = rateBand(ln, rate);
            const bad = t !== undefined && rate === null;
            return (
              <tr key={k}>
                <td>
                  {fmt.date(`${ln.stay_date}T12:00:00Z`, "medium")}
                  {ln.demand_index != null && ln.demand_index >= 80 && <span className="inq-sub">High demand ({ln.demand_index})</span>}
                </td>
                {mixed && <td>{ln.room_type_code ?? "Default"}</td>}
                <td className="inq-num">{ln.rooms}</td>
                <td className="inq-num">{ln.bar_value ? <MoneyText amount={ln.bar_value} currency={quote.currency} /> : "-"}</td>
                <td><BandBar line={ln} rate={rate} currency={quote.currency} /></td>
                <td className="inq-num">
                  {editable ? (
                    <input className="inq-rate" inputMode="decimal" aria-label={`Rate for ${ln.stay_date}`}
                      aria-invalid={bad || band === "below_floor" || band === "above_ceiling" || undefined}
                      value={shown} placeholder="0.00" onChange={e => onType(k, e.target.value)} />
                  ) : ln.rate_value ? <MoneyText amount={ln.rate_value} currency={quote.currency} /> : "-"}
                </td>
                <td>
                  <span className={`inq-band inq-band--${band}`}>{BAND_TEXT[band]}</span>
                  <span className="inq-sub">{SOURCE_TEXT[ln.source] ?? ln.source}{ln.below_floor_reason ? `: ${ln.below_floor_reason}` : ""}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Floor-to-ceiling band with BAR as a tick and the rate as a marker. The
    numbers are in the title (and BAR has its own column), so the picture
    never carries meaning alone. */
function BandBar({ line, rate, currency }: { line: QuoteLine; rate: number | null; currency: string }) {
  const fmt = useFormat();
  const pts = [line.floor_minor, line.ceiling_minor, line.bar_minor, rate].filter((v): v is number => v != null && v > 0);
  if (line.floor_minor == null && line.ceiling_minor == null) return <span className="inq-muted">Not available</span>;
  const lo = Math.min(...pts) * 0.95;
  const hi = Math.max(...pts) * 1.05;
  const pos = (v: number) => `${((v - lo) / (hi - lo || 1)) * 100}%`;
  const money = (v: number | null) => (v == null ? "none" : fmt.money(v, { minor: true, currency }));
  const title = `Floor ${money(line.floor_minor)}, BAR ${money(line.bar_minor)}, ceiling ${money(line.ceiling_minor)}`;
  const from = line.floor_minor ?? lo;
  const to = line.ceiling_minor ?? hi;
  return (
    <div className="inq-bar" title={title} role="img" aria-label={title}>
      <span className="inq-bar__band" style={{ left: pos(from), width: `calc(${pos(to)} - ${pos(from)})` }} />
      {line.bar_minor != null && <span className="inq-bar__tick" style={{ left: pos(line.bar_minor) }} />}
      {rate != null && rate > 0 && <span className="inq-bar__rate" style={{ left: pos(rate) }} />}
    </div>
  );
}

// ─── Tax lines ───────────────────────────────────────────────────────────

/** The composed lines as the server stored them (one compose_stay call).
    While rates are being typed they are the SAVED totals, and say so. */
export function TaxLines({ quote, dirty }: { quote: Quote; dirty: boolean }) {
  const c = quote.currency;
  const rows: [string, string, string?][] = [
    ["Room", quote.room_value],
    ["Service charge", quote.sc_value],
    ["Municipality fee", quote.mf_value],
    ["VAT", quote.vat_value],
    ["Tourism Dirham", quote.td_value,
      quote.td_nights_charged ? `${quote.td_nights_charged} night${quote.td_nights_charged === 1 ? "" : "s"} charged` : undefined],
  ];
  return (
    <section className="inq-section">
      <h3>Taxes and total{dirty ? " (saved rates; save to update)" : ""}</h3>
      <table className="inq-taxes">
        <tbody>
          {rows.filter(([, v]) => v !== undefined && v !== null).map(([label, v, sub]) => (
            <tr key={label}>
              <td>{label}{sub && <span className="inq-sub">{sub}</span>}</td>
              <td><MoneyText amount={v} currency={c} /></td>
            </tr>
          ))}
          <tr className="inq-total">
            <td>Total</td>
            <td><MoneyText amount={quote.total_value} currency={c} /></td>
          </tr>
        </tbody>
      </table>
      {quote.fee_notices.length > 0 && (
        <ul className="inq-list">{quote.fee_notices.map((n, i) => <li key={i}>{n}</li>)}</ul>
      )}
    </section>
  );
}

// ─── Terms ───────────────────────────────────────────────────────────────

export function QuoteTerms({ quote }: { quote: Quote }) {
  const fmt = useFormat();
  const lang = quote.language === "ar" ? "ar" : "en";
  const cancellation = quote.cancellation_terms?.[lang] ?? quote.cancellation_terms?.en;
  const inclusions = quote.inclusions?.[lang] ?? quote.inclusions?.en;
  const g = quote.group_terms;
  return (
    <section className="inq-section">
      <h3>Payment schedule and terms</h3>
      <table className="inq-taxes">
        <tbody>
          {quote.deposit_schedule.map(m => (
            <tr key={m.seq}>
              <td>
                {m.label}
                <span className="inq-sub">
                  {m.due_on ? `Due ${fmt.date(`${m.due_on}T12:00:00Z`, "medium")}` : "Due on acceptance"}
                  {m.td_note ? `. ${m.td_note.en}` : ""}
                </span>
              </td>
              <td><MoneyText amount={m.amount_value} currency={quote.currency} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {g && (
        <dl className="inq-kv">
          <dt>Group terms</dt><dd>{g.label}</dd>
          <dt>Cut-off</dt><dd>{fmt.date(`${g.cutoff_date}T12:00:00Z`, "medium")}</dd>
          <dt>Attrition</dt><dd>{g.attrition_pct}% (at least {g.min_rooms_after_attrition} rooms)</dd>
          <dt>Rooming list</dt><dd>By {fmt.date(`${g.rooming_list_due}T12:00:00Z`, "medium")}</dd>
        </dl>
      )}
      {cancellation && <p className="inq-muted" style={{ margin: 0 }} dir="auto"><strong>Cancellation: </strong>{cancellation}</p>}
      {inclusions && <p className="inq-muted" style={{ margin: 0 }} dir="auto"><strong>Includes: </strong>{inclusions}</p>}
      {quote.has_public_link && <Badge tone="info">The guest has a link to this quote</Badge>}
    </section>
  );
}
