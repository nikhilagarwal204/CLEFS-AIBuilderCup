"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Day drawer (02 spec 4.4): everything behind one night of the heatmap.
   - The demand index and which of its four inputs had data.
   - Rate suggestions (RecommendationCard with the bounded AdjustSlider),
     or the guardrails when there is none.
   - Events scored for the night: source, confidence, last checked and
     change history.
   - The indicative comp band against the own BAR (CompRateStrip).
   - Pace against comparable nights (Sparkline).
   - Staffing, KB and offer guidance drafted from the night's events.
   One GET /days/{date}; a decision or dismissal refetches it and tells the
   calendar to refresh its cell.
   ═══════════════════════════════════════════════════════════════════════ */

import { useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { Badge, Button, ConfidencePill, Drawer, EmptyState, LineChart, Skeleton, Sparkline, type LineSeries } from "../../../../../ui";
import CompRateStrip from "./CompRateStrip";
import EventItem from "./EventItem";
import RecommendationCard from "./RecommendationCard";
import { HEAT_LEVEL_LABEL, heatLevel, num, paceGap, paceSeries } from "../_lib/model";
import { drApi, errorText } from "../_lib/paths";
import type { DayResponse, Guidance, Night } from "../_lib/types";

export default function DayDrawer({
  date, onClose, onChanged,
}: {
  date: string | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { slug } = useHotel();
  const fmt = useFormat();
  const day = useApi<DayResponse>(date ? drApi(slug, `/days/${date}`) : null, [slug, date]);
  const d = day.data && day.data.stay_date === date ? day.data : undefined;

  const changed = () => {
    day.reload();
    onChanged();
  };

  return (
    <Drawer open={date !== null} onClose={onClose} wide title={date ? fmt.date(date, "long") : ""}>
      {day.error ? (
        <EmptyState title="Could not load this night" body={errorText(day.error)} action={<Button onClick={day.reload}>Try again</Button>} />
      ) : !d ? (
        <Skeleton lines={8} height={18} />
      ) : (
        <div className="cx-stack">
          <IndexSummary night={d.demand_index} />
          <RatesSection slug={slug} d={d} onChanged={changed} />
          <EventsSection slug={slug} d={d} onChanged={changed} />
          <CompSection d={d} />
          <PaceSection d={d} />
          <GuidanceSection items={d.guidance} />
        </div>
      )}
    </Drawer>
  );
}

// ─── Demand index ────────────────────────────────────────────────────────

function z(v: number | null | undefined): string {
  const n = num(v);
  return n === null ? "-" : `${n > 0 ? "+" : ""}${n.toFixed(1)}`;
}

function IndexSummary({ night }: { night: Night }) {
  const c = night.components;
  const empty = Boolean(c.signal_free);
  const level = empty ? null : heatLevel(night.demand_index);
  const parts: { key: keyof Night["components"]["has"]; label: string; value: string }[] = [
    { key: "season", label: "Season", value: z(c.season_z) },
    { key: "events", label: "Events", value: z(c.event_z) },
    { key: "pace", label: "Pace", value: z(c.pace_z) },
    { key: "comp", label: "Competitors", value: z(c.comp_z) },
  ];
  return (
    <section aria-label="Demand index">
      <div className="dr-section-title">Demand index</div>
      <div className="dr-di">
        <div>
          <div className="dr-di__value">{empty ? "-" : night.demand_index}</div>
          <div className="dr-small dr-muted">{level === null ? "No data" : HEAT_LEVEL_LABEL[level]}, {Math.round((night.coverage ?? 0) * 100)}% of inputs</div>
        </div>
        <div className="dr-di__parts">
          {parts.map(p => (
            <div key={p.key} className="dr-di__part" data-missing={!c.has?.[p.key] || undefined}
                 title={c.has?.[p.key] ? "Standard deviations from normal" : "No data for this input"}>
              {p.label}<b>{c.has?.[p.key] ? p.value : "no data"}</b>
            </div>
          ))}
        </div>
      </div>
      {night.computed_at === null && <p className="dr-note" style={{ marginTop: 6 }}>Computed live; the nightly run stores it.</p>}
    </section>
  );
}

// ─── Rates: suggestions or guardrails ────────────────────────────────────

function RatesSection({ slug, d, onChanged }: { slug: string; d: DayResponse; onChanged: () => void }) {
  const fmt = useFormat();
  const titles = Object.fromEntries(d.events.map(e => [e.id, e.title]));
  const g = d.guardrails;
  const money = (v: string | null | undefined) => fmt.money(v ?? null, { maximumFractionDigits: 0, currency: g?.currency });
  return (
    <section aria-label="Rate suggestion">
      <div className="dr-section-title">Rate suggestion</div>
      {d.recommendations.length > 0 ? (
        <div className="cx-stack" style={{ gap: 10 }}>
          {d.recommendations.map(r => (
            <RecommendationCard key={r.id} slug={slug} rec={r} eventTitles={titles} onDecided={onChanged} />
          ))}
        </div>
      ) : g ? (
        <div className="dr-item">
          <p className="dr-note" style={{ marginBottom: 6 }}>No rate change suggested for this night: the move would be too small to matter.</p>
          <dl className="dr-kv">
            <dt>Floor to ceiling</dt><dd>{money(g.floor)} to {money(g.ceiling)}</dd>
            <dt>Bounds from</dt>
            <dd>{g.source === "rate_guardrails" ? "Your guardrails" : g.source === "bar_only" ? "Your BAR (-10% to +30%)" : "Indicative competitor rates"}</dd>
            <dt>Confidence</dt><dd><ConfidencePill value={g.confidence} /></dd>
          </dl>
          {g.reasons.length > 0 && <p className="dr-note" style={{ marginTop: 6 }}>{g.reasons.map(r => r.replaceAll("_", " ")).join(", ")}</p>}
        </div>
      ) : (
        <p className="dr-note">No BAR, guardrails or competitor rates for this night yet, so there is nothing to suggest.</p>
      )}
    </section>
  );
}

// ─── Events ──────────────────────────────────────────────────────────────

function EventsSection({ slug, d, onChanged }: { slug: string; d: DayResponse; onChanged: () => void }) {
  const fmt = useFormat();
  return (
    <section aria-label="Events this night">
      <div className="dr-section-title">Events this night <Badge>{d.events.length}</Badge></div>
      {d.events.length ? (
        <ul className="dr-list">
          {d.events.map(e => (
            <EventItem
              key={e.id}
              slug={slug}
              event={e}
              dismissed={e.dismissed}
              onChanged={onChanged}
              extra={[
                `score ${fmt.number(e.night_score, 2)}`,
                e.distance_km !== null ? `${fmt.number(e.distance_km, 1)} km away` : null,
                e.corridor_match ? "same transport corridor" : null,
              ].filter(Boolean).join(", ")}
            />
          ))}
        </ul>
      ) : (
        <p className="dr-note">No scored events touch this night.</p>
      )}
    </section>
  );
}

// ─── Competitors ─────────────────────────────────────────────────────────

function CompSection({ d }: { d: DayResponse }) {
  const ref = d.reference_room_type;
  const bar = d.bar.find(b => b.room_type_code === ref) ?? d.bar[0] ?? null;
  return (
    <section aria-label="Competitor rates">
      <div className="dr-section-title">Competitor rates</div>
      <CompRateStrip band={d.comp.summary} rates={d.comp.rates} bar={bar?.bar ?? null} label={d.comp.label}
                     barLabel={bar ? `Your BAR (${bar.room_type_code})` : undefined} />
    </section>
  );
}

// ─── Pace ────────────────────────────────────────────────────────────────

function PaceSection({ d }: { d: DayResponse }) {
  const s = paceSeries(d.pace.own, d.pace.baseline);
  const gap = paceGap(d.pace.own, d.pace.baseline);
  const latest = [...s.own].reverse().find(v => v !== null) ?? null;
  // The Sparkline is the pace gap itself (own minus comparable nights at each
  // lead): one series on its own scale, so it cannot mislead the way two
  // independently scaled sparklines would. The LineChart puts both series on
  // one shared axis for the detail.
  const gapLine = s.leads.map((_, i) => (s.own[i] !== null && s.baseline[i] !== null ? (s.own[i] as number) - (s.baseline[i] as number) : null));
  const series: LineSeries[] = [
    { key: "own", label: "This night", points: s.leads.map((l, i) => ({ x: -l, y: s.own[i] })) },
    { key: "base", label: "Comparable nights", dashed: true, color: "rgba(242,238,230,0.45)", points: s.leads.map((l, i) => ({ x: -l, y: s.baseline[i] })) },
  ];
  return (
    <section aria-label="Pace">
      <div className="dr-section-title">Pace against comparable nights</div>
      {s.leads.length < 2 ? (
        <p className="dr-note">Not enough on-the-books snapshots for this night yet. They build up daily once reservations sync.</p>
      ) : (
        <>
          <div className="dr-pace">
            <Sparkline values={gapLine} width={160} height={36}
                       ariaLabel={`Pace gap by days before arrival${gap !== null ? `: now ${gap >= 0 ? "ahead by" : "behind by"} ${Math.abs(gap)} rooms` : ""}`} />
            <div className="dr-pace__legend">
              <span>{latest ?? "-"} rooms on the books now</span>
              {gap !== null && (
                <Badge tone={gap >= 0 ? "ok" : "warn"}>{gap >= 0 ? `${gap} rooms ahead` : `${Math.abs(gap)} rooms behind`}</Badge>
              )}
            </div>
          </div>
          <LineChart series={series} height={160} ariaLabel="Rooms on the books by days before arrival, this night against comparable nights"
                     xFormat={x => (x === 0 ? "arrival" : `${-x}d`)} yFormat={v => String(Math.round(v))} />
          <p className="dr-note">{d.pace.label}.</p>
        </>
      )}
    </section>
  );
}

// ─── Guidance drafted from the night's events ────────────────────────────

const GUIDANCE_LABEL: Record<Guidance["kind"], string> = {
  STAFFING: "Housekeeping staffing",
  KB_UPDATE: "Concierge travel advice",
  PRE_ARRIVAL_OFFER: "Pre-arrival offer",
};

function guidanceLine(g: Guidance): string {
  const p = g.payload ?? {};
  const n = (k: string) => num(p[k]);
  if (g.kind === "STAFFING") {
    const need = n("attendants_needed");
    const have = n("attendants_rostered");
    if (need !== null && have !== null) return `${need} room attendants needed, ${have} on the roster${(n("gap") ?? 0) > 0 ? `: ${n("gap")} short` : ""}.`;
    if (need !== null) return `${need} room attendants needed; nobody is rostered for that day yet.`;
  }
  if (g.kind === "KB_UPDATE" && typeof p.question_en === "string") {
    return `${p.question_en} ${typeof p.answer_en === "string" ? p.answer_en : ""}`.trim();
  }
  const text = p.headline ?? p.body;
  return typeof text === "string" ? text : "Drafted from this night's events.";
}

function GuidanceSection({ items }: { items: Guidance[] }) {
  const live = items.filter(g => g.status !== "DISMISSED" && g.status !== "EXPIRED");
  if (!live.length) return null;
  return (
    <section aria-label="Guidance">
      <div className="dr-section-title">Also prepared for this night</div>
      <ul className="dr-list">
        {live.map(g => (
          <li key={g.id} className="dr-item">
            <div className="dr-item__head">
              <span className="dr-item__title">{GUIDANCE_LABEL[g.kind] ?? g.kind}</span>
              <Badge tone={g.status === "PROPOSED" ? "gold" : "ok"}>{g.status.toLowerCase()}</Badge>
            </div>
            <p className="dr-note" style={{ marginTop: 4 }}>{guidanceLine(g)}</p>
          </li>
        ))}
      </ul>
      <p className="dr-note" style={{ marginTop: 6 }}>Approve these from the Brief or the actions list; nothing is sent from here.</p>
    </section>
  );
}
