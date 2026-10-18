/**
 * The /security data-flow diagram: who sends data into Clefs, where it is
 * stored, and which flows leave the UAE. Inline SVG so it ships in the HTML,
 * scales without a request, and themes from the --clefs-* tokens.
 *
 * WHY every box names a country: the question a hotel's lawyer asks is "where
 * does guest data go", and the honest answer today has two foreign hops
 * (database in Singapore, AI in the United States). The gold dashed line is the
 * one guests consent to (transfer_abroad) before the assistant starts. Labels
 * come from lib/subprocessors.ts so the diagram and the table cannot disagree.
 *
 * On a phone the SVG keeps a legible minimum width inside its own scroll box,
 * and the <figcaption> repeats every flow in words for screen readers and for
 * anyone who would rather read than trace lines.
 */
import { RESIDENCY } from "../../lib/subprocessors";

// ─── Geometry ────────────────────────────────────────────────────────────────

type Box = { x: number; y: number; w: number; h: number; title: string; sub: string; tone?: "core" | "abroad" | "plain" };

const LEFT: Box[] = [
  { x: 16, y: 40, w: 190, h: 74, title: "Guests", sub: "QR code, room phone, WhatsApp" },
  { x: 16, y: 170, w: 190, h: 74, title: "Hotel team and owner", sub: "Console, floor devices, brief" },
  { x: 16, y: 330, w: 190, h: 74, title: "Website visitors", sub: "Marketing pages only" },
];

const CORE: Box = { x: 300, y: 70, w: 200, h: 170, title: "Clefs application", sub: "Hosted on Vercel", tone: "core" };

const RIGHT: Box[] = [
  { x: 594, y: 16, w: 190, h: 64, title: "Database", sub: RESIDENCY.database, tone: "abroad" },
  { x: 594, y: 96, w: 190, h: 64, title: "AI processing", sub: RESIDENCY.ai, tone: "abroad" },
  { x: 594, y: 176, w: 190, h: 64, title: "Messaging", sub: "WhatsApp, email, SMS" },
  { x: 594, y: 256, w: 190, h: 64, title: "Market data", sub: "SerpApi, Google: no guest data" },
  { x: 594, y: 336, w: 190, h: 64, title: "Site analytics", sub: "Clarity: visitors only" },
];

type Flow = { d: string; kind: "data" | "abroad" | "nopii" };

const midY = (b: Box) => b.y + b.h / 2;
const CORE_X2 = CORE.x + CORE.w;

const FLOWS: Flow[] = [
  // Inbound: guests and the hotel team talk to the application.
  { d: `M206 ${midY(LEFT[0])} L300 ${CORE.y + 40}`, kind: "data" },
  { d: `M206 ${midY(LEFT[1])} L300 ${CORE.y + 130}`, kind: "data" },
  // Outbound: stored, sent to AI under consent, messaged, market lookups.
  { d: `M${CORE_X2} ${CORE.y + 20} L594 ${midY(RIGHT[0])}`, kind: "abroad" },
  { d: `M${CORE_X2} ${CORE.y + 60} L594 ${midY(RIGHT[1])}`, kind: "abroad" },
  { d: `M${CORE_X2} ${CORE.y + 110} L594 ${midY(RIGHT[2])}`, kind: "data" },
  { d: `M${CORE_X2} ${CORE.y + 150} L594 ${midY(RIGHT[3])}`, kind: "nopii" },
  // Visitors reach Clarity directly from the browser, never through Clefs.
  { d: `M206 ${midY(LEFT[2])} L594 ${midY(RIGHT[4])}`, kind: "nopii" },
];

// ─── Pieces ──────────────────────────────────────────────────────────────────

function BoxEl({ b }: { b: Box }) {
  const cls = b.tone === "core" ? "mk-df-box mk-df-box--core" : b.tone === "abroad" ? "mk-df-box mk-df-box--abroad" : "mk-df-box";
  const cx = b.x + b.w / 2;
  const cy = b.y + b.h / 2;
  return (
    <g>
      <rect className={cls} x={b.x} y={b.y} width={b.w} height={b.h} rx={12} />
      <text className="mk-df-title" x={cx} y={cy - 5} textAnchor="middle">{b.title}</text>
      <text className="mk-df-sub" x={cx} y={cy + 15} textAnchor="middle">{b.sub}</text>
    </g>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function DataFlow() {
  return (
    <figure className="mk-df">
      <style dangerouslySetInnerHTML={{ __html: DF_STYLES }} />
      <div className="mk-df__scroll">
        <svg viewBox="0 0 800 420" role="img" aria-labelledby="mk-df-t mk-df-d" className="mk-df__svg">
          <title id="mk-df-t">How data moves through Clefs</title>
          <desc id="mk-df-d">
            {/* One template string: a JSX line break after an expression
                collapses to nothing, which read "(Neon)and sends" (QA PUB-6). */}
            {`Guests and the hotel team send data to the Clefs application. It stores data in ${RESIDENCY.database} `}
            and sends guest messages for AI processing in {RESIDENCY.ai} under the guest&apos;s consent.
          </desc>
          <defs>
            <marker id="mk-df-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0 0 L10 5 L0 10 z" className="mk-df-arrowhead" />
            </marker>
          </defs>
          {FLOWS.map((f) => (
            <path key={f.d} d={f.d} className={`mk-df-line mk-df-line--${f.kind}`} markerEnd="url(#mk-df-arrow)" />
          ))}
          {LEFT.map((b) => <BoxEl key={b.title} b={b} />)}
          <BoxEl b={CORE} />
          <text className="mk-df-sub" x={CORE.x + CORE.w / 2} y={CORE.y + CORE.h - 34} textAnchor="middle">Access control, masking,</text>
          <text className="mk-df-sub" x={CORE.x + CORE.w / 2} y={CORE.y + CORE.h - 16} textAnchor="middle">audit log, retention</text>
          {RIGHT.map((b) => <BoxEl key={b.title} b={b} />)}
        </svg>
      </div>
      <ul className="mk-df__legend">
        <li><span className="mk-df-key mk-df-key--data" /> Personal data, inside Clefs and its processors</li>
        <li><span className="mk-df-key mk-df-key--abroad" /> Personal data leaving the UAE (guests consent before the assistant starts)</li>
        <li><span className="mk-df-key mk-df-key--nopii" /> No guest personal data</li>
      </ul>
      <figcaption className="mk-df__caption">
        Guests and your team use Clefs through the web and the room phone. Clefs stores hotel data in a
        database in {RESIDENCY.database}, sends the words a guest types or speaks to {RESIDENCY.ai} for AI
        processing, and sends messages through WhatsApp, email and SMS providers. Market lookups send hotel
        names and dates only. Website analytics run on the marketing pages only, never in the console or
        the guest pages.
      </figcaption>
    </figure>
  );
}

// ─── Styles ──────────────────────────────────────────────────────────────────

const DF_STYLES = `
  .mk-df { margin: 8px 0 20px; }
  .mk-df__scroll { overflow-x: auto; border: 1px solid var(--clefs-line); border-radius: 14px;
                   background: var(--clefs-slate-850); padding: 12px; }
  .mk-df__svg { display: block; width: 100%; min-width: 640px; height: auto; }
  .mk-df-box { fill: var(--clefs-surface-2); stroke: var(--clefs-line); stroke-width: 1; }
  .mk-df-box--core { fill: var(--clefs-gold-wash); stroke: var(--clefs-line-gold); }
  .mk-df-box--abroad { stroke: var(--clefs-gold-600); stroke-dasharray: 5 4; }
  .mk-df-title { fill: var(--clefs-ink); font-family: var(--clefs-sans); font-size: 14px; font-weight: 600; }
  .mk-df-sub { fill: var(--clefs-ink-muted); font-family: var(--clefs-sans); font-size: 12px; }
  .mk-df-line { fill: none; stroke-width: 1.6; }
  .mk-df-line--data { stroke: var(--clefs-ink-faint); }
  .mk-df-line--abroad { stroke: var(--clefs-gold-400); stroke-dasharray: 7 5; stroke-width: 2; }
  .mk-df-line--nopii { stroke: var(--clefs-ink-faint); stroke-dasharray: 2 5; }
  .mk-df-arrowhead { fill: var(--clefs-ink-faint); }
  .mk-df__legend { list-style: none; padding: 0; margin: 14px 0 10px; display: grid; gap: 8px;
                   font-size: 0.88rem; color: var(--clefs-ink-muted); }
  .mk-df__legend li { display: flex; align-items: center; gap: 10px; }
  .mk-df-key { display: inline-block; width: 30px; border-top: 2px solid var(--clefs-ink-faint); flex: none; }
  .mk-df-key--abroad { border-top: 2px dashed var(--clefs-gold-400); }
  .mk-df-key--nopii { border-top: 2px dotted var(--clefs-ink-faint); }
  .mk-df__caption { font-size: 0.9rem; line-height: 1.7; color: var(--clefs-ink-muted); max-width: 72ch; }
`;
