/* Visibility console styles: one string injected once by VisibilityConsole.
   Every class is vis- prefixed and built on --clefs-* tokens, so the kit
   (cx-) stays the single source of the look and nothing leaks out. */

export const visStyles = `
.vis-stack { display: grid; gap: 16px; }
.vis-tiles { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 900px) { .vis-tiles { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.vis-two { display: grid; gap: 16px; }
@media (min-width: 1000px) { .vis-two { grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); } }
.vis-label { display: inline-flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 12px;
  color: var(--clefs-ink-faint); line-height: 1.5; }
.vis-disclosure { margin: 0 0 12px; font-size: 13px; line-height: 1.6; color: var(--clefs-ink-muted);
  border-inline-start: 3px solid var(--clefs-gold-600); padding: 8px 12px; background: var(--clefs-gold-wash);
  border-radius: 0 8px 8px 0; }
.vis-muted { color: var(--clefs-ink-faint); font-size: 13px; }
.vis-note { margin: 8px 0 0; color: var(--clefs-ink-faint); font-size: 12px; line-height: 1.5; }
.vis-line { margin: 0; font-size: 15px; line-height: 1.6; color: var(--clefs-ink); }
.vis-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.vis-spacer { flex: 1 1 auto; }
.vis-num { font-variant-numeric: tabular-nums; }
.vis-breach { color: #E5484D; font-weight: 600; }
.vis-prices { margin: 6px 0 0; padding: 0; list-style: none; display: grid; gap: 2px; font-size: 12px;
  color: var(--clefs-ink-muted); }
.vis-prices li { display: flex; justify-content: space-between; gap: 12px; }
.vis-prices .vis-off { color: var(--clefs-gold-400); font-weight: 600; }
.vis-setup { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.vis-setup li { display: grid; grid-template-columns: 20px minmax(0, 1fr); gap: 10px; align-items: start; }
.vis-setup__box { width: 16px; height: 16px; margin-top: 3px; border: 1.5px solid var(--clefs-line);
  border-radius: 4px; }
.vis-setup strong { color: var(--clefs-ink); font-weight: 600; font-size: 14px; }
.vis-setup span { display: block; color: var(--clefs-ink-faint); font-size: 13px; }
.vis-cells { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); }
.vis-cell { display: grid; gap: 4px; padding: 12px 14px; border: 1px solid var(--clefs-line-soft);
  border-radius: 12px; background: var(--clefs-surface-2); }
.vis-cell__head { font-size: 12px; color: var(--clefs-ink-faint); }
.vis-cell__value { font-size: 20px; font-weight: 600; color: var(--clefs-ink); font-variant-numeric: tabular-nums; }
.vis-cell__value--thin { font-size: 14px; font-weight: 500; color: var(--clefs-ink-muted); }
.vis-ci { position: relative; height: 6px; border-radius: 3px; background: var(--clefs-line-soft); }
.vis-ci__band { position: absolute; top: 0; bottom: 0; border-radius: 3px; background: rgba(220, 171, 74, 0.35); }
.vis-ci__dot { position: absolute; top: -2px; width: 10px; height: 10px; margin-left: -5px; border-radius: 50%;
  background: var(--clefs-gold-400); }
.vis-sov { display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: var(--clefs-line-soft); }
.vis-sov__seg { height: 100%; min-width: 2px; }
.vis-legend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 8px 0 0; padding: 0; list-style: none;
  font-size: 12px; color: var(--clefs-ink-muted); }
.vis-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-inline-end: 6px;
  vertical-align: -1px; }
.vis-facts { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.vis-fact { display: grid; gap: 6px; padding: 12px 14px; border: 1px solid var(--clefs-line-soft); border-radius: 12px;
  background: var(--clefs-surface-2); }
.vis-fact__head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.vis-fact__title { font-weight: 600; color: var(--clefs-ink); }
.vis-quote { margin: 0; padding-inline-start: 10px; border-inline-start: 2px solid var(--clefs-line);
  color: var(--clefs-ink-muted); font-size: 13px; unicode-bidi: plaintext; }
.vis-answer { white-space: pre-wrap; margin: 0; font-size: 14px; line-height: 1.65; color: var(--clefs-ink);
  unicode-bidi: plaintext; }
.vis-answer mark { background: rgba(229, 72, 77, 0.22); color: inherit; border-radius: 3px; padding: 0 2px; }
.vis-cites { margin: 0; padding: 0; list-style: none; display: grid; gap: 6px; font-size: 13px; }
.vis-disclosure a { color: var(--clefs-gold-400); text-decoration: underline; text-underline-offset: 2px; white-space: nowrap; }
.vis-cites a { color: var(--clefs-gold-400); text-decoration: underline; text-underline-offset: 2px; word-break: break-word; }
.vis-meta { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 12px; margin: 0; font-size: 13px; }
.vis-meta dt { color: var(--clefs-ink-faint); }
.vis-meta dd { margin: 0; color: var(--clefs-ink); word-break: break-word; }
.vis-section { display: grid; gap: 8px; padding-top: 14px; margin-top: 14px; border-top: 1px solid var(--clefs-line-soft); }
.vis-section h3 { margin: 0; font-size: 12px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase;
  color: var(--clefs-ink-faint); font-weight: 600; }
.vis-bar { height: 8px; border-radius: 4px; background: var(--clefs-line-soft); overflow: hidden; }
.vis-bar__fill { height: 100%; background: var(--clefs-gold-400); }
.vis-bar__fill--over { background: #E5484D; }
.vis-form { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
.vis-checks { display: grid; gap: 8px; font-size: 14px; }
.vis-checks label { display: flex; gap: 8px; align-items: flex-start; }
.vis-checks input { margin-top: 3px; accent-color: var(--clefs-gold-600); }
.vis-link { background: none; border: 0; padding: 0; font: inherit; color: var(--clefs-gold-400); cursor: pointer;
  text-decoration: underline; text-underline-offset: 2px; text-align: start; }
.vis-link:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.vis-scroll { overflow-x: auto; }
`;
