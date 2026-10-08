/* Ask Clefs console styles: one string injected once by AskConsole. Every
   class is ask- prefixed and built on --clefs-* tokens, so the kit (cx-)
   stays the single source of the look and nothing leaks to other modules.
   Status colour always pairs with a word (Badge), never colour alone. */

export const askStyles = `
.ask-layout { display: grid; gap: 18px; grid-template-columns: minmax(0, 1fr); grid-template-areas: "main" "side"; }
@media (min-width: 1080px) {
  .ask-layout { grid-template-columns: minmax(0, 1fr) 300px; grid-template-areas: "main side"; align-items: start; }
  .ask-side { position: sticky; top: 16px; }
}
.ask-main { grid-area: main; display: grid; gap: 16px; min-width: 0; grid-template-columns: minmax(0, 1fr); }
.ask-side { grid-area: side; display: grid; gap: 14px; min-width: 0; grid-template-columns: minmax(0, 1fr); }
/* Every grid here pins its one track to minmax(0, 1fr): an implicit auto
   track grows to the table's nowrap width and pushes the page sideways on
   a phone; with this the table scrolls inside its own box instead. */

/* Composer */
.ask-composer { display: grid; gap: 10px; grid-template-columns: minmax(0, 1fr); }
.ask-composer__box { display: flex; gap: 10px; align-items: flex-end; }
.ask-composer__box .cx-textarea { flex: 1; min-height: 52px; max-height: 220px; resize: none; line-height: 1.45; }
/* The kit keeps fields at 16px below 768px on purpose: iOS Safari zooms the
   page whenever a focused field is smaller. Only a wide screen gets 15px. */
@media (min-width: 768px) {
  .ask-composer__box .cx-textarea { font-size: 15px; }
}
.ask-composer__foot { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; justify-content: space-between;
  font-size: 12px; color: var(--clefs-ink-faint); }
.ask-composer__count--over { color: var(--cx-bad); font-weight: 600; }
.ask-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.ask-chip { appearance: none; border: 1px solid var(--clefs-line); background: var(--clefs-surface-2); color: var(--clefs-ink-muted);
  border-radius: 999px; padding: 6px 12px; font: inherit; font-size: 13px; cursor: pointer; text-align: left;
  transition: border-color 0.15s, color 0.15s; }
.ask-chip:hover, .ask-chip:focus-visible { border-color: var(--clefs-line-gold); color: var(--clefs-ink); }
.ask-chip:disabled { opacity: 0.5; cursor: default; }

/* Thread and answer card */
.ask-thread { display: grid; gap: 16px; margin: 0; padding: 0; list-style: none; grid-template-columns: minmax(0, 1fr); }
.ask-thread > li { min-width: 0; }
.ask-card { display: grid; gap: 14px; grid-template-columns: minmax(0, 1fr); min-width: 0; }
.ask-card--focus { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.ask-card__q { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: flex-start; justify-content: space-between; }
.ask-card__question { margin: 0; font-size: 15px; line-height: 1.45; color: var(--clefs-ink-muted); font-weight: 500;
  overflow-wrap: anywhere; }
.ask-card__question::before { content: "\\201C"; color: var(--clefs-gold-300); margin-right: 2px; }
.ask-card__question::after { content: "\\201D"; color: var(--clefs-gold-300); margin-left: 2px; }
.ask-card__tags { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.ask-card__text { margin: 0; font-family: var(--clefs-serif); font-size: 19px; line-height: 1.45; color: var(--clefs-ink);
  white-space: pre-wrap; overflow-wrap: anywhere; }
.ask-card__note { margin: 0; font-size: 13px; color: var(--clefs-ink-muted); line-height: 1.5; }
.ask-card__foot { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; justify-content: space-between;
  border-top: 1px solid var(--clefs-line-soft); padding-top: 12px; }
.ask-card__actions { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.ask-card__meta { font-size: 12px; color: var(--clefs-ink-faint); font-variant-numeric: tabular-nums; }
.ask-result { display: grid; gap: 12px; min-width: 0; grid-template-columns: minmax(0, 1fr); }
.ask-result + .ask-result { border-top: 1px dashed var(--clefs-line-soft); padding-top: 14px; }
.ask-result__title { margin: 0; font-size: 12px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase;
  color: var(--clefs-ink-faint); font-weight: 600; }

/* KPI */
.ask-kpi { display: flex; flex-wrap: wrap; gap: 6px 18px; align-items: baseline; }
.ask-kpi__value { font-size: 34px; font-weight: 600; color: var(--clefs-ink); font-variant-numeric: tabular-nums; line-height: 1.1; }
.ask-kpi__cmp { font-size: 13px; color: var(--clefs-ink-muted); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ask-delta { display: inline-flex; align-items: center; gap: 4px; border-radius: 999px; padding: 2px 8px; font-size: 12px;
  font-weight: 600; font-variant-numeric: tabular-nums; border: 1px solid var(--clefs-line); color: var(--clefs-ink-muted); }
.ask-delta--good { color: var(--cx-ok); border-color: rgba(var(--cx-ok-rgb), 0.35); }
.ask-delta--bad { color: var(--cx-bad); border-color: rgba(var(--cx-bad-rgb), 0.35); }
.ask-chart-note { font-size: 12px; color: var(--clefs-ink-faint); margin: 0; }

/* Table */
.ask-table { width: 100%; overflow-x: auto; border: 1px solid var(--clefs-line-soft); border-radius: 12px; }
.ask-table table { width: 100%; border-collapse: collapse; font-size: 13px; }
.ask-table th, .ask-table td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--clefs-line-soft); white-space: nowrap; }
.ask-table th { font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; color: var(--clefs-ink-faint); font-weight: 600;
  background: var(--clefs-surface-2); position: sticky; top: 0; }
.ask-table td { color: var(--clefs-ink); }
.ask-table td.ask-num, .ask-table th.ask-num { text-align: right; font-variant-numeric: tabular-nums; }
.ask-table tbody tr:last-child td { border-bottom: 0; }
.ask-table tfoot td { font-weight: 600; border-top: 1px solid var(--clefs-line); border-bottom: 0; }
.ask-table__more { display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap;
  font-size: 12px; color: var(--clefs-ink-faint); padding: 8px 2px 0; }

/* Trace disclosure */
.ask-trace { border: 1px solid var(--clefs-line-soft); border-radius: 12px; background: var(--clefs-surface-2); }
.ask-trace > summary { cursor: pointer; list-style: none; padding: 10px 14px; font-size: 13px; color: var(--clefs-ink-muted);
  display: flex; gap: 8px; align-items: center; }
.ask-trace > summary::-webkit-details-marker { display: none; }
.ask-trace > summary::before { content: "\\25B8"; color: var(--clefs-gold-300); transition: transform 0.15s; }
.ask-trace[open] > summary::before { transform: rotate(90deg); }
.ask-trace__body { padding: 0 14px 14px; display: grid; gap: 12px; }
.ask-trace__list { display: grid; grid-template-columns: minmax(96px, max-content) minmax(0, 1fr); gap: 6px 14px; margin: 0; font-size: 13px; }
.ask-trace__list dt { color: var(--clefs-ink-faint); }
.ask-trace__list dd { margin: 0; color: var(--clefs-ink); overflow-wrap: anywhere; }
.ask-trace__mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.ask-trace__desc { margin: 0; font-size: 13px; color: var(--clefs-ink-muted); line-height: 1.5; }
.ask-trace__calls { margin: 0; padding-left: 18px; font-size: 12px; color: var(--clefs-ink-muted); display: grid; gap: 4px; }

/* Feedback */
.ask-fb { display: inline-flex; gap: 4px; align-items: center; }
.ask-fb__btn[aria-pressed="true"] { border-color: var(--clefs-line-gold); color: var(--clefs-gold-300); }
.ask-fb__form { display: grid; gap: 8px; width: 100%; }

/* Sidebar */
.ask-side__list { display: grid; gap: 2px; margin: 0; padding: 0; list-style: none; }
.ask-side__item { display: flex; gap: 6px; align-items: center; border-radius: 10px; padding: 2px 2px 2px 0; }
.ask-side__item:hover, .ask-side__item--active { background: var(--clefs-surface-2); }
.ask-side__open { appearance: none; border: 0; background: none; color: var(--clefs-ink); font: inherit; font-size: 13px; text-align: left;
  flex: 1; min-width: 0; padding: 8px 10px; cursor: pointer; border-radius: 10px; display: grid; gap: 2px; }
.ask-side__open:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: -2px; }
.ask-side__title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ask-side__sub { font-size: 11px; color: var(--clefs-ink-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ask-side__tools { display: flex; gap: 2px; flex-shrink: 0; }
.ask-side__empty { font-size: 13px; color: var(--clefs-ink-faint); margin: 0; line-height: 1.5; }
.ask-side__wa { display: grid; gap: 8px; font-size: 13px; color: var(--clefs-ink-muted); line-height: 1.5; }
.ask-side__wa p { margin: 0; }
`;
