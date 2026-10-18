import {
  getClaim,
  claimBlocker,
  claimLabel,
  claimOrder,
  claimSource,
  claimToday,
  type ClaimSurface,
} from "../lib/claims";

/**
 * <Claim id="..." /> renders a registry statistic: the value, its caption and a
 * footnote link to the source. It is the ONLY way a market number reaches
 * marketing copy (04 section 2.1); check-claims.mjs validates every `id` used here
 * against the registry, the surface and the expiry at build time.
 *
 * No hooks and no "use client": it renders identically inside the homepage's
 * client tree and inside server pages such as SolutionPage, so the number is in
 * the initial HTML for crawlers.
 *
 * It renders NOTHING when the claim may not show right now (flag off, sample
 * too small, expired, wrong surface). Callers therefore never write fallback
 * copy with a number in it; a sentence built around a claim must read correctly
 * without it, or be gated by `claimText(id) !== ""`.
 */

export type ClaimProps = {
  id: string;
  /** The surface this render sits on; checked against `allowed_surfaces`. */
  surface?: ClaimSurface;
  /** "inline" flows in a sentence; "stat" stacks a large value over its caption. */
  variant?: "inline" | "stat";
  className?: string;
  /**
   * The Dubai date (YYYY-MM-DD) to judge expiry and tense by. A client tree
   * that is also server-rendered (the homepage) MUST pass the date its server
   * render used: otherwise the server HTML (render date) and the hydration
   * render (the visitor's date) disagree once a claim crosses its review_by or
   * label_after date, and React reports a hydration mismatch. Server-only
   * callers may omit it; their page must then set `revalidate` so the date is
   * re-read after a build instead of being frozen at `next build`.
   */
  today?: string;
};

export default function Claim({ id, surface = "home", variant = "inline", className = "", today: at }: ClaimProps) {
  const c = getClaim(id);
  if (!c) return null;
  const today = at ?? claimToday();
  if (claimBlocker(c, surface, today)) return null;

  const label = claimLabel(c, today);
  const order = claimOrder(c, today);
  const source = claimSource(c);
  const cls = `mk-claim mk-claim--${variant} ${className}`.trim();

  // The footnote opens the publisher's page in a new tab: a GM checking the
  // number mid-demo should not lose the page he was reading.
  const foot = (
    <sup className="mk-claim__src">
      <a href={c.url} target="_blank" rel="noopener noreferrer" title={source}
         aria-label={`Source: ${source}`}>source</a>
    </sup>
  );

  if (variant === "stat") {
    return (
      <span className={cls} data-claim={c.claim_id}>
        {order !== "label_only" && <strong className="mk-claim__val">{c.value}</strong>}
        <span className="mk-claim__label">{label}{foot}</span>
      </span>
    );
  }

  return (
    <span className={cls} data-claim={c.claim_id}>
      {order === "label_only" && <span className="mk-claim__label">{label}</span>}
      {order === "label_first" && (
        <><span className="mk-claim__label">{label}: </span><strong className="mk-claim__val">{c.value}</strong></>
      )}
      {order === "value_first" && (
        <><strong className="mk-claim__val">{c.value}</strong> <span className="mk-claim__label">{label}</span></>
      )}
      {foot}
    </span>
  );
}

/** Injected once by each host surface (homepage brandStyles, SolutionPage). */
export const CLAIM_STYLES = `
  .mk-claim { font: inherit; color: inherit; }
  .mk-claim__val { font-weight: 600; color: var(--clefs-gold-400); }
  .mk-claim__src { font-size: 0.68em; margin-left: 3px; line-height: 0; }
  .mk-claim__src a { color: var(--clefs-ink-faint); text-decoration: underline; text-underline-offset: 2px; }
  .mk-claim__src a:hover { color: var(--clefs-gold-400); }
  .mk-claim--stat { display: flex; flex-direction: column; gap: 6px; }
  .mk-claim--stat .mk-claim__val {
    font-family: var(--clefs-serif); font-weight: 400;
    font-size: clamp(1.6rem, 3.4vw, 2.15rem); line-height: 1.1;
  }
  .mk-claim--stat .mk-claim__label { font-size: 0.84rem; line-height: 1.55; color: var(--clefs-ink-muted); }
`;
