"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Demand heatmap: one cell per night coloured by the demand index, with
   event badges, a dot for an open or decided rate recommendation and a
   sold-out marker when competitors are full. The kit CalendarHeatmap has
   no badge slot and scales colours to the data max (a quiet month would
   look busy), so this surface draws its own cells on the kit HEAT_RAMP
   with fixed DI bands (model.heatLevel).
   FOUNDATION-REQUEST: a `renderBadges(day)` slot and a fixed `max` scale
   on ui CalendarHeatmap would let this reuse the kit component.
   ═══════════════════════════════════════════════════════════════════════ */

import { useFormat } from "../../../../../lib/format";
import { useHotelOptional } from "../../../../../lib/hotel";
import { HEAT_RAMP } from "../../../../../ui";
import { CATEGORY_LABEL, CATEGORY_SHORT, cellLabel, HEAT_LEVEL_LABEL, heatLevel, isoWeekday, leadPad } from "../_lib/model";
import type { CalendarCell } from "../_lib/types";

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export default function Heatmap({
  cells, selected, onSelect,
}: {
  cells: readonly CalendarCell[];
  selected: string | null;
  onSelect: (date: string) => void;
}) {
  const fmt = useFormat();
  const hotel = useHotelOptional()?.hotel;
  // Canon: the UAE weekend is Sat-Sun; the hotel's own setting wins.
  const weekend = new Set(hotel?.weekend_days?.length ? hotel.weekend_days : [6, 7]);
  if (!cells.length) return null;
  const first = cells[0].stay_date;

  return (
    <div>
      <div className="dr-cal" role="group" aria-label="Demand by night">
        {DOW.map((d, i) => (
          <div key={d} className="dr-cal__dow" data-weekend={weekend.has(i + 1) || undefined} aria-hidden="true">{d}</div>
        ))}
        {Array.from({ length: leadPad(first) }, (_, i) => <div key={`pad-${i}`} aria-hidden="true" />)}
        {cells.map((c, i) => {
          const level = heatLevel(c.demand_index);
          const day = Number(c.stay_date.slice(8));
          const month = i === 0 || day === 1 ? MONTHS[Number(c.stay_date.slice(5, 7)) - 1] : null;
          const extra = c.events.length > 2 ? c.events.length - 2 : 0;
          return (
            <button
              key={c.stay_date}
              type="button"
              className="dr-cell"
              data-weekend={weekend.has(isoWeekday(c.stay_date)) || undefined}
              data-empty={level === null || undefined}
              data-dark={level !== null && level >= 3 ? true : undefined}
              aria-pressed={selected === c.stay_date}
              aria-label={cellLabel(c, fmt.date(c.stay_date, "medium"))}
              title={c.events.map(e => `${e.title} (${CATEGORY_LABEL[e.category] ?? e.category})`).join("\n") || undefined}
              style={level === null ? undefined : { background: HEAT_RAMP[level] }}
              onClick={() => onSelect(c.stay_date)}
            >
              <span className="dr-cell__top" aria-hidden="true">
                <span className="dr-cell__day">{month ? `${day} ${month}` : day}</span>
                <span className="dr-cell__di">{c.demand_index ?? ""}</span>
              </span>
              {c.recommendation && <span className="dr-cell__rec" data-status={c.recommendation.status} aria-hidden="true" />}
              {c.comp && c.comp.sold_out > 0 && (
                <span className="dr-cell__soldout" aria-hidden="true">{c.comp.sold_out} sold out</span>
              )}
              {c.events.length > 0 && (
                <span className="dr-cell__badges" aria-hidden="true">
                  {c.events.slice(0, 2).map(e => (
                    <span key={e.event_id} className="dr-ev" data-unverified={e.verification === "UNVERIFIED" || undefined}>
                      {CATEGORY_SHORT[e.category] ?? "EV"}
                    </span>
                  ))}
                  {extra > 0 && <span className="dr-ev dr-ev--more">+{extra}</span>}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <div className="dr-legend" aria-hidden="true">
        {HEAT_LEVEL_LABEL.map((l, i) => (
          <span key={l}><i style={{ background: HEAT_RAMP[i] }} />{l}</span>
        ))}
        <span><i style={{ background: "rgba(255,255,255,0.03)", boxShadow: "inset 0 0 0 1px var(--clefs-line-soft)" }} />No data</span>
        <span><i className="dr-dot" style={{ background: "var(--clefs-gold-300)" }} />Rate suggestion</span>
        <span><i className="dr-dot" style={{ background: "var(--cx-ok, #34C77B)" }} />Decided</span>
        <span><span className="dr-ev">CF</span> Event (dashed: unverified)</span>
      </div>
    </div>
  );
}
