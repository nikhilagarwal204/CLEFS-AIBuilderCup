/* Profit Guard console styles: one string injected once by the profit
   layout. Every class is pg- prefixed and built on --clefs-* tokens (and
   the kit's --cx-* radii and tone channels), so the kit stays the single
   source of the look. Grids reflow from one column on a phone. */

export const pgStyles = `
.pg-tiles { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 900px) { .pg-tiles { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.pg-grid2 { display: grid; gap: 16px; grid-template-columns: 1fr; }
@media (min-width: 1000px) { .pg-grid2 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.pg-grid-main { display: grid; gap: 16px; grid-template-columns: 1fr; }
@media (min-width: 1100px) { .pg-grid-main { grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); } }
.pg-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.pg-between { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; }
.pg-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; }
.pg-toolbar > .cx-field { min-width: 140px; }
.pg-spacer { flex: 1 1 auto; }
.pg-muted { color: var(--clefs-ink-muted); font-size: 0.86rem; line-height: 1.5; }
.pg-faint { color: var(--clefs-ink-faint); font-size: 0.8rem; line-height: 1.5; }
.pg-num { font-variant-numeric: tabular-nums; }
.pg-strong { font-weight: 600; color: var(--clefs-ink); }
.pg-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.8rem; }
.pg-kv { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 0.88rem; margin: 0; }
.pg-kv dt { color: var(--clefs-ink-faint); }
.pg-kv dd { margin: 0; color: var(--clefs-ink); font-variant-numeric: tabular-nums; min-width: 0; overflow-wrap: anywhere; }
.pg-notice {
  display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: var(--cx-radius);
  border: 1px solid rgba(var(--cx-warn-rgb), 0.4); background: rgba(var(--cx-warn-rgb), 0.09);
  color: var(--clefs-ink); font-size: 0.88rem; line-height: 1.55;
}
.pg-notice--info { border-color: var(--clefs-line-gold); background: var(--clefs-gold-wash); }
.pg-notice--bad { border-color: rgba(var(--cx-bad-rgb), 0.4); background: rgba(var(--cx-bad-rgb), 0.08); }
.pg-notice ul { margin: 6px 0 0; padding-left: 18px; }
.pg-error { color: var(--clefs-ink); font-size: 0.86rem; padding: 10px 12px; border-radius: var(--cx-radius-sm);
  border: 1px solid rgba(var(--cx-bad-rgb), 0.35); background: rgba(var(--cx-bad-rgb), 0.08); }

/* Funnel: one stacked bar, then a legend row per status. */
.pg-funnel__bar { display: flex; height: 16px; border-radius: 999px; overflow: hidden; background: var(--clefs-line-soft); }
.pg-funnel__bar > span { display: block; height: 100%; min-width: 2px; }
.pg-funnel__rows { display: grid; gap: 6px; margin-top: 12px; }
.pg-funnel__row { display: grid; grid-template-columns: 12px minmax(0, 1fr) auto auto; gap: 10px; align-items: center;
  font-size: 0.86rem; }
.pg-funnel__row i { width: 10px; height: 10px; border-radius: 3px; display: block; }
.pg-funnel__row .pg-num { text-align: right; }

/* Deadline rail: a vertical list of clocks, soonest first. */
.pg-rail { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.pg-rail__item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 12px; align-items: center;
  padding: 10px 12px; border-radius: var(--cx-radius-sm); border: 1px solid var(--clefs-line-soft);
  background: var(--clefs-surface-2, transparent); }
.pg-rail__item a { color: inherit; text-decoration: none; }
.pg-rail__item a:hover .pg-rail__title { text-decoration: underline; }
.pg-rail__title { font-size: 0.88rem; color: var(--clefs-ink); overflow-wrap: anywhere; }
.pg-rail__meta { font-size: 0.78rem; color: var(--clefs-ink-faint); grid-column: 1 / -1; }

/* Pilot scorecard criteria. */
.pg-criteria { display: grid; gap: 10px; }
.pg-criterion { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 10px; align-items: start; font-size: 0.88rem; }
.pg-meter { height: 8px; border-radius: 999px; background: var(--clefs-line-soft); overflow: hidden; margin-top: 6px; }
.pg-meter > span { display: block; height: 100%; background: var(--clefs-gold-400); border-radius: 999px; }

/* Calculation trace stepper. */
.pg-steps { list-style: none; margin: 0; padding: 0; display: grid; gap: 0; counter-reset: pgstep; }
.pg-step { position: relative; padding: 0 0 16px 34px; counter-increment: pgstep; }
.pg-step::before { content: counter(pgstep); position: absolute; left: 0; top: 0; width: 22px; height: 22px;
  border-radius: 50%; display: grid; place-items: center; font-size: 0.72rem; font-weight: 600;
  background: var(--clefs-gold-wash); color: var(--clefs-gold-300); border: 1px solid var(--clefs-line-gold); }
.pg-step::after { content: ""; position: absolute; left: 11px; top: 24px; bottom: 2px; width: 1px; background: var(--clefs-line-soft); }
.pg-step:last-child::after { display: none; }
.pg-step__head { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; justify-content: space-between; }
.pg-step__label { font-weight: 600; font-size: 0.9rem; color: var(--clefs-ink); }
.pg-step__formula { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.78rem;
  color: var(--clefs-ink-muted); margin-top: 2px; overflow-wrap: anywhere; }
.pg-step__inputs { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 6px; font-size: 0.8rem; color: var(--clefs-ink-faint); }
.pg-step__inputs b { color: var(--clefs-ink-muted); font-weight: 500; font-variant-numeric: tabular-nums; }
.pg-step__result { font-variant-numeric: tabular-nums; font-weight: 600; color: var(--clefs-gold-300); }

/* Clause citations and extranet text. */
.pg-clause { border-left: 3px solid var(--clefs-line-gold); padding: 6px 10px; margin: 6px 0 0;
  color: var(--clefs-ink-muted); font-size: 0.84rem; line-height: 1.5; background: var(--clefs-gold-wash);
  border-radius: 0 var(--cx-radius-sm) var(--cx-radius-sm) 0; }
.pg-clause cite { display: block; font-style: normal; font-size: 0.74rem; color: var(--clefs-ink-faint); margin-top: 4px; }
.pg-pre { white-space: pre-wrap; font: inherit; font-size: 0.84rem; line-height: 1.5; margin: 0; padding: 10px 12px;
  border-radius: var(--cx-radius-sm); border: 1px solid var(--clefs-line-soft); color: var(--clefs-ink); overflow-wrap: anywhere; }
.pg-item { border-top: 1px solid var(--clefs-line-soft); padding-top: 12px; margin-top: 12px; display: grid; gap: 8px; }
.pg-item:first-child { border-top: 0; padding-top: 0; margin-top: 0; }

/* Channel cards and the Net ADR grid. */
.pg-channels { display: grid; gap: 16px; grid-template-columns: 1fr; }
@media (min-width: 1100px) { .pg-channels { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.pg-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.pg-grid-table { border-collapse: collapse; width: 100%; font-size: 0.84rem; }
.pg-grid-table th, .pg-grid-table td { padding: 6px 8px; border-bottom: 1px solid var(--clefs-line-soft); text-align: right;
  font-variant-numeric: tabular-nums; white-space: nowrap; }
.pg-grid-table th:first-child, .pg-grid-table td:first-child { text-align: left; }
.pg-grid-table th { color: var(--clefs-ink-faint); font-weight: 500; }
.pg-cell--low { color: rgb(var(--cx-bad-rgb)); font-weight: 600; }

/* Print route: the frame fills the page under a slim toolbar. */
.pg-print-frame { width: 100%; min-height: 75vh; border: 1px solid var(--clefs-line-soft); border-radius: var(--cx-radius);
  background: #fff; }
.pg-thumb { max-width: 100%; max-height: 180px; border-radius: var(--cx-radius-sm); border: 1px solid var(--clefs-line-soft); }
`;
