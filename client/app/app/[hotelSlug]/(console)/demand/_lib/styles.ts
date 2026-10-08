/* ═══════════════════════════════════════════════════════════════════════
   Demand Radar console stylesheet (prefix dr-). Injected once by
   demand/layout.tsx for every demand page. Colours come from --clefs-*
   tokens, the kit's --cx-ok/warn/bad/info and the kit HEAT_RAMP (passed in
   as inline backgrounds), so a busy night looks the same here as in any
   other heatmap. Mobile-first: the calendar keeps 7 columns down to 320 px.
   ═══════════════════════════════════════════════════════════════════════ */

const base = `
.dr-toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; }
.dr-toolbar > * { min-width: 0; }
.dr-grow { flex: 1 1 200px; }
.dr-small { font-size: 12.5px; }
.dr-muted { color: var(--clefs-ink-muted); }
.dr-faint { color: var(--clefs-ink-faint); }
.dr-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; overflow-wrap: anywhere; }
.dr-section-title { font-size: 12px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase;
  color: var(--clefs-ink-muted); margin: 18px 0 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.dr-section-title:first-child { margin-top: 0; }
.dr-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.dr-kv { display: grid; grid-template-columns: minmax(110px, 38%) 1fr; gap: 6px 12px; font-size: 13.5px; margin: 0; }
.dr-kv dt { color: var(--clefs-ink-muted); }
.dr-kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.dr-note { font-size: 12.5px; color: var(--clefs-ink-muted); line-height: 1.5; margin: 0; }
.dr-label-ind { font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; color: var(--clefs-gold-300);
  border: 1px solid var(--clefs-line-gold); border-radius: 999px; padding: 1px 7px; white-space: nowrap; }
.dr-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.dr-item { border: 1px solid var(--clefs-line-soft); border-radius: 12px; padding: 10px 12px;
  background: rgba(255,255,255,0.02); min-width: 0; }
.dr-item__head { display: flex; gap: 8px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; }
.dr-item__title { font-weight: 600; font-size: 14.5px; overflow-wrap: anywhere; }
.dr-item__meta { font-size: 12.5px; color: var(--clefs-ink-muted); margin-top: 4px; display: flex; gap: 4px 12px; flex-wrap: wrap; }
.dr-item--dim { opacity: 0.6; }
.dr-history { margin: 8px 0 0; padding-left: 16px; font-size: 12.5px; color: var(--clefs-ink-muted); }
.dr-history li { margin: 2px 0; }
.dr-checks { display: grid; gap: 8px; }
.dr-check { display: flex; gap: 10px; align-items: flex-start; font-size: 13.5px; line-height: 1.45; cursor: pointer; }
.dr-check input { margin-top: 3px; width: 16px; height: 16px; accent-color: var(--clefs-gold-400); flex: none; }
.dr-copy { white-space: pre-wrap; font-size: 14px; line-height: 1.55; background: rgba(255,255,255,0.03);
  border: 1px solid var(--clefs-line-soft); border-radius: 10px; padding: 10px 12px; overflow-wrap: anywhere; margin: 0; }
`;

const calendar = `
.dr-cal { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 4px; }
.dr-cal__dow { font-size: 11px; color: var(--clefs-ink-faint); text-align: center; padding-bottom: 2px; }
.dr-cal__dow[data-weekend] { color: var(--clefs-gold-300); }
.dr-cal__month { grid-column: 1 / -1; font-size: 12px; letter-spacing: var(--clefs-tracking-eyebrow);
  text-transform: uppercase; color: var(--clefs-ink-muted); margin: 8px 0 2px; }
.dr-cell { position: relative; min-height: 64px; border-radius: 10px; border: 1px solid transparent; padding: 5px 6px;
  display: flex; flex-direction: column; gap: 3px; text-align: left; font: inherit; color: var(--clefs-ink);
  cursor: pointer; min-width: 0; overflow: hidden; }
.dr-cell[data-weekend] { box-shadow: inset 0 0 0 1px rgba(225,175,104,0.28); }
.dr-cell[data-empty] { background: rgba(255,255,255,0.03); color: var(--clefs-ink-muted); }
.dr-cell[data-dark] { color: #16202D; }
.dr-cell:hover { border-color: var(--clefs-line-gold); }
.dr-cell:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.dr-cell[aria-pressed="true"] { border-color: var(--clefs-gold-300); box-shadow: 0 0 0 2px rgba(225,175,104,0.45); }
.dr-cell__top { display: flex; justify-content: space-between; align-items: baseline; gap: 4px; }
.dr-cell__day { font-size: 12px; font-weight: 600; }
.dr-cell__di { font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }
.dr-cell__badges { display: flex; flex-wrap: wrap; gap: 2px; margin-top: auto; }
.dr-ev { font-size: 9.5px; font-weight: 700; line-height: 1; padding: 2px 4px; border-radius: 5px;
  background: rgba(22,32,45,0.78); color: var(--clefs-gold-200); border: 1px solid rgba(225,175,104,0.35); }
.dr-ev[data-unverified] { border-style: dashed; color: var(--clefs-ink-muted); }
.dr-ev--more { background: transparent; border-color: transparent; color: inherit; }
.dr-cell__rec { position: absolute; top: 5px; right: 5px; width: 7px; height: 7px; border-radius: 50%;
  background: var(--cx-info, #5AA9E6); box-shadow: 0 0 0 2px rgba(22,32,45,0.6); }
.dr-cell__rec[data-status="PROPOSED"] { background: var(--clefs-gold-300); }
.dr-cell__rec[data-status="APPROVED"], .dr-cell__rec[data-status="ADJUSTED"] { background: var(--cx-ok, #34C77B); }
/* The sold-out marker sits on its own dark chip (like .dr-ev) so its
   legibility never depends on the heat fill underneath: #FF8A8D on
   rgba(22,32,45,0.9) measures 6.1:1 over Peak up to 7.0:1 over Quiet,
   where bare red text on the ramp dropped to 1.3:1 on Normal nights. */
.dr-cell__soldout { align-self: flex-start; max-width: 100%; font-size: 9.5px; font-weight: 700; line-height: 1.2;
  padding: 1px 4px; border-radius: 4px; background: rgba(22,32,45,0.9); color: #FF8A8D;
  border: 1px solid rgba(255,138,141,0.4); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dr-legend { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; font-size: 12px; color: var(--clefs-ink-muted); margin-top: 10px; }
.dr-legend i { display: inline-block; width: 14px; height: 10px; border-radius: 3px; vertical-align: middle; margin-right: 4px; }
.dr-legend .dr-dot { width: 8px; height: 8px; border-radius: 50%; }
@media (max-width: 520px) {
  .dr-cell { min-height: 48px; padding: 4px; }
  .dr-cell__di { font-size: 13px; }
  .dr-ev:not(:first-child) { display: none; }
}
`;

const drawer = `
.dr-di { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
.dr-di__value { font-size: 34px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; color: var(--clefs-gold-300); }
.dr-di__parts { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 6px; flex: 1 1 260px; }
.dr-di__part { border: 1px solid var(--clefs-line-soft); border-radius: 10px; padding: 6px 8px; font-size: 12px; }
.dr-di__part b { display: block; font-size: 14px; font-variant-numeric: tabular-nums; }
.dr-di__part[data-missing] { opacity: 0.5; }
.dr-strip { position: relative; height: 34px; margin: 26px 4px 22px; }
.dr-strip__rail { position: absolute; left: 0; right: 0; top: 15px; height: 4px; border-radius: 2px; background: rgba(255,255,255,0.08); }
.dr-strip__band { position: absolute; top: 11px; height: 12px; border-radius: 6px; background: rgba(225,175,104,0.28);
  border: 1px solid rgba(225,175,104,0.55); }
.dr-strip__tick { position: absolute; top: 6px; width: 2px; height: 22px; margin-left: -1px; background: var(--clefs-gold-300); }
.dr-strip__bar { position: absolute; top: 0; width: 3px; height: 34px; margin-left: -1.5px; background: var(--cx-info, #5AA9E6); border-radius: 2px; }
.dr-strip__lbl { position: absolute; font-size: 11px; white-space: nowrap; transform: translateX(-50%); font-variant-numeric: tabular-nums; }
.dr-strip__lbl--top { top: -18px; color: var(--cx-info, #5AA9E6); font-weight: 600; }
.dr-strip__lbl--bot { top: 36px; color: var(--clefs-ink-muted); }
.dr-rates { display: grid; gap: 4px; font-size: 13px; }
.dr-rates__row { display: flex; justify-content: space-between; gap: 10px; border-bottom: 1px solid var(--clefs-line-soft); padding: 4px 0; }
.dr-rates__row:last-child { border-bottom: 0; }
.dr-rates__row span:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dr-pace { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.dr-pace__legend { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; }
.dr-pace__legend i { display: inline-block; width: 14px; height: 3px; border-radius: 2px; vertical-align: middle; margin-right: 6px; }
`;

const rec = `
.dr-rec { border: 1px solid var(--clefs-line-gold); border-radius: 14px; padding: 12px 14px;
  background: rgba(225,175,104,0.05); display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.dr-rec__head { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; align-items: center; }
.dr-rec__rates { display: flex; gap: 14px; align-items: baseline; flex-wrap: wrap; }
.dr-rec__now { color: var(--clefs-ink-muted); font-size: 13px; }
.dr-rec__to { font-size: 24px; font-weight: 700; color: var(--clefs-gold-300); font-variant-numeric: tabular-nums; }
.dr-rec__why { font-size: 13.5px; line-height: 1.55; margin: 0; }
.dr-slider { display: flex; flex-direction: column; gap: 4px; }
.dr-slider input[type=range] { width: 100%; accent-color: var(--clefs-gold-400); }
.dr-slider__ends { display: flex; justify-content: space-between; font-size: 11.5px; color: var(--clefs-ink-muted); font-variant-numeric: tabular-nums; }
.dr-slider__value { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.dr-slider__value input { width: 110px; }
.dr-rec__actions { display: flex; gap: 8px; flex-wrap: wrap; }
.dr-top { counter-reset: dr-top; }
.dr-top > li { counter-increment: dr-top; }
.dr-top__n::before { content: counter(dr-top); display: inline-grid; place-items: center; width: 24px; height: 24px;
  border-radius: 50%; border: 1px solid var(--clefs-line-gold); color: var(--clefs-gold-300); font-weight: 700; font-size: 12.5px; }
.dr-ghc__item { display: grid; grid-template-columns: 1fr auto; gap: 6px 12px; align-items: start;
  border-bottom: 1px solid var(--clefs-line-soft); padding: 10px 0; }
.dr-ghc__item:last-child { border-bottom: 0; }
.dr-share { display: grid; grid-template-columns: minmax(0, 1fr) 96px auto; gap: 8px; align-items: center; }
`;

export const drStyles = base + calendar + drawer + rec;
