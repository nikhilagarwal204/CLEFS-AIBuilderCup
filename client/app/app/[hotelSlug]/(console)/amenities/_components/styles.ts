/* Amenities console styles: one string injected once by AmenitiesShell.
   Every class is amn- prefixed and built on --clefs-* tokens, so the kit
   (cx-) stays the single source of the look and nothing leaks to other
   modules. The quick-entry grid is phone-first: 48px targets, a sticky
   submit bar and no horizontal scroll at 360px. */

export const amnStyles = `
.amn-nav { margin-bottom: 16px; }
.amn-tiles { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); margin-bottom: 16px; }
@media (min-width: 900px) { .amn-tiles { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.amn-grid2 { display: grid; gap: 16px; grid-template-columns: minmax(0, 1fr); margin-bottom: 16px; }
@media (min-width: 1000px) { .amn-grid2 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.amn-stack { display: grid; gap: 16px; }
.amn-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-bottom: 14px; }
.amn-toolbar > .cx-field { min-width: 150px; }
.amn-spacer { flex: 1 1 auto; }
.amn-muted { color: var(--clefs-ink-faint); font-size: 13px; }
.amn-note { color: var(--clefs-ink-muted); font-size: 13px; line-height: 1.5; margin: 8px 0 0; }
.amn-banner { display: flex; gap: 10px; align-items: flex-start; padding: 10px 14px; margin-bottom: 14px;
  border-radius: 12px; border: 1px solid var(--clefs-line-soft); background: var(--clefs-surface-2);
  color: var(--clefs-ink-muted); font-size: 13px; line-height: 1.5; }
.amn-banner svg { flex: none; margin-top: 2px; color: var(--clefs-gold-500); }
.amn-leak { color: #E0795A; font-weight: 600; }
.amn-save { color: var(--clefs-ink-muted); }
.amn-link { color: inherit; text-decoration: none; font-weight: 600; }
.amn-link:hover, .amn-link:focus-visible { color: var(--clefs-gold-400); text-decoration: underline; }
.amn-sr { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
  clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.amn-mono { font-variant-numeric: tabular-nums; }
.amn-rowtitle { display: grid; gap: 2px; min-width: 0; }
.amn-rowtitle small { color: var(--clefs-ink-faint); font-size: 12px; }
.amn-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.amn-kv { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 13px; margin: 0; }
.amn-kv dt { color: var(--clefs-ink-faint); }
.amn-kv dd { margin: 0; color: var(--clefs-ink); }
.amn-form { display: grid; gap: 12px; grid-template-columns: minmax(0, 1fr); }
@media (min-width: 640px) { .amn-form { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.amn-form .amn-span2 { grid-column: 1 / -1; }
.amn-check { display: flex; gap: 8px; align-items: center; font-size: 14px; color: var(--clefs-ink); }
.amn-check input { width: 18px; height: 18px; accent-color: var(--clefs-gold-500); }

/* Grouped columns (requested / issued / expected) */
.amn-gcol { position: relative; margin-top: 12px; }
.amn-gcol svg { display: block; width: 100%; overflow: visible; }
.amn-gcol__tick { fill: var(--clefs-ink-faint); font-size: 11px; }
.amn-gcol__grid { stroke: rgba(255,255,255,0.06); }
.amn-gcol__bar { transition: opacity 0.15s; }
.amn-gcol__hit { fill: transparent; cursor: default; }

/* Quick entry */
.amn-qe { display: grid; gap: 14px; }
.amn-qe__ctx { display: grid; gap: 10px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 720px) { .amn-qe__ctx { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.amn-qe__ctx .amn-span2 { grid-column: span 2; }
.amn-qe__search { width: 100%; }
.amn-qe__cats { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 2px; scrollbar-width: none; }
.amn-qe__cats::-webkit-scrollbar { display: none; }
.amn-qe__grid { display: grid; gap: 10px; grid-template-columns: minmax(0, 1fr); list-style: none; margin: 0; padding: 0; }
@media (min-width: 560px) { .amn-qe__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (min-width: 1100px) { .amn-qe__grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.amn-qe__item { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 14px;
  border: 1px solid var(--clefs-line-soft); background: var(--clefs-surface-2); min-height: 68px; }
.amn-qe__item[data-on] { border-color: var(--clefs-gold-500); background: var(--clefs-gold-wash); }
.amn-qe__name { flex: 1 1 auto; min-width: 0; display: grid; gap: 2px; }
.amn-qe__name b { font-size: 15px; font-weight: 600; color: var(--clefs-ink); overflow-wrap: anywhere; }
.amn-qe__name span { font-size: 12px; color: var(--clefs-ink-faint); unicode-bidi: plaintext; }
/* Focus and scroll-into-view must not park a stepper under the sticky bar
   (and the phone tab bar below it): WCAG 2.4.11, focus not obscured. */
.amn-qe__item, .amn-step button, .amn-step input { scroll-margin-bottom: 170px; scroll-margin-top: 80px; }
.amn-step { display: inline-flex; align-items: center; gap: 4px; flex: none; }
.amn-step button { width: 44px; height: 44px; border-radius: 12px; border: 1px solid var(--clefs-line);
  background: var(--clefs-slate-850); color: var(--clefs-ink); font-size: 22px; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center; cursor: pointer; touch-action: manipulation; }
.amn-step button:disabled { opacity: 0.35; cursor: default; }
.amn-step button:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.amn-step input { width: 48px; height: 44px; text-align: center; border-radius: 10px; border: 1px solid var(--clefs-line-soft);
  background: transparent; color: var(--clefs-ink); font: inherit; font-size: 17px; font-weight: 600;
  font-variant-numeric: tabular-nums; }
.amn-qe__bar { position: sticky; bottom: 8px; z-index: 30; display: flex; gap: 10px; align-items: center;
  padding: 12px 14px; border-radius: 16px; background: var(--clefs-slate-800);
  border: 1px solid var(--clefs-line); box-shadow: var(--clefs-shadow-3); }
/* The console's phone tab bar is fixed at the bottom below 768px: sit above it. */
@media (max-width: 767.98px) { .amn-qe__bar { bottom: calc(var(--cx-bottom, 64px) + 8px); } }
.amn-qe__sum { flex: 1 1 auto; min-width: 0; font-size: 14px; color: var(--clefs-ink-muted); }
.amn-qe__sum b { color: var(--clefs-ink); font-size: 17px; }
.amn-recent { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.amn-recent li { display: flex; gap: 10px; align-items: center; justify-content: space-between; font-size: 13px;
  padding: 8px 10px; border-radius: 10px; background: var(--clefs-surface-2); }
.amn-recent li[data-void] { opacity: 0.5; text-decoration: line-through; }

/* Benchmarks band */
.amn-band { position: relative; height: 10px; border-radius: 5px; background: rgba(255,255,255,0.06); margin: 6px 0; }
.amn-band__range { position: absolute; top: 0; bottom: 0; border-radius: 5px; background: rgba(89,151,226,0.45); }
.amn-band__you { position: absolute; top: -4px; width: 3px; height: 18px; border-radius: 2px; background: var(--clefs-gold-400); }
`;
