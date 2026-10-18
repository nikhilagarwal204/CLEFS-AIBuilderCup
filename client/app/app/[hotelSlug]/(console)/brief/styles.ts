/* Brief console styles: one string injected once by brief/layout. Every
   class is brf- prefixed and built on --clefs-* tokens, so the kit (cx-)
   stays the single source of the look and nothing leaks to other modules.
   Loss and overdue reds pair with a word or icon, never colour alone. */

export const brfStyles = `
.brf-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; }
.brf-bar__left, .brf-bar__right { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.brf-head { display: grid; gap: 14px; }
.brf-head__line { margin: 0; font-family: var(--clefs-serif); font-size: 22px; line-height: 1.3; color: var(--clefs-ink); }
.brf-head__meta { display: flex; flex-wrap: wrap; gap: 8px 14px; color: var(--clefs-ink-faint); font-size: 13px; }
.brf-tiles { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 900px) { .brf-tiles { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.brf-list { display: grid; gap: 12px; margin: 0; padding: 0; list-style: none; }
.brf-group { display: grid; gap: 10px; }
.brf-group__title { margin: 0; font-size: 12px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase;
  color: var(--clefs-ink-faint); font-weight: 600; display: flex; gap: 8px; align-items: center; }
.brf-card { display: grid; gap: 10px; }
.brf-card--overdue { border-left: 3px solid #D9534F; }
.brf-card--focus { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.brf-card__top { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; }
.brf-card__tags { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.brf-card__no { display: inline-grid; place-items: center; min-width: 26px; height: 26px; border-radius: 999px;
  border: 1px solid var(--clefs-line-gold); color: var(--clefs-gold-300); font-size: 13px; font-weight: 600;
  font-variant-numeric: tabular-nums; }
.brf-card__title { margin: 0; font-size: 16px; line-height: 1.4; color: var(--clefs-ink); font-weight: 600; }
.brf-card__impact { font-size: 24px; font-weight: 600; color: var(--clefs-ink); font-variant-numeric: tabular-nums; }
.brf-card__impact small { font-size: 12px; font-weight: 400; color: var(--clefs-ink-faint); margin-left: 6px; }
.brf-card__meta { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 13px; color: var(--clefs-ink-muted); }
.brf-card__meta b { color: var(--clefs-ink); font-weight: 600; }
.brf-card__foot { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between;
  padding-top: 10px; border-top: 1px solid var(--clefs-line-soft); }
.brf-muted { color: var(--clefs-ink-faint); font-size: 13px; }
.brf-verify { display: grid; gap: 12px; }
.brf-verify p { margin: 0; line-height: 1.5; }
.brf-verify__code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 22px; font-weight: 600;
  letter-spacing: 0.08em; padding: 10px 14px; border-radius: 10px; border: 1px solid var(--clefs-line-gold);
  background: var(--clefs-surface-2); color: var(--clefs-ink); user-select: all; text-align: center; }
.brf-verify > a.cx-btn { justify-self: start; }
.brf-verified { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 12px 14px; border-radius: 12px;
  background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line-gold); color: var(--clefs-ink); font-size: 14px; }
.brf-block { display: grid; gap: 8px; }
.brf-block ul { margin: 0; padding-left: 18px; display: grid; gap: 6px; font-size: 14px; color: var(--clefs-ink-muted); }
.brf-kv { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 14px; margin: 0; }
.brf-kv dt { color: var(--clefs-ink-faint); }
.brf-kv dd { margin: 0; color: var(--clefs-ink); font-variant-numeric: tabular-nums; }
.brf-grid2 { display: grid; gap: 16px; grid-template-columns: 1fr; }
@media (min-width: 1000px) { .brf-grid2 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.brf-timeline { list-style: none; margin: 0; padding: 0; display: grid; gap: 0; }
.brf-timeline li { position: relative; display: grid; gap: 2px; padding: 0 0 16px 22px; }
.brf-timeline li::before { content: ""; position: absolute; left: 5px; top: 6px; width: 9px; height: 9px; border-radius: 999px;
  background: var(--clefs-ink-faint); }
.brf-timeline li::after { content: ""; position: absolute; left: 9px; top: 18px; bottom: 0; width: 1px; background: var(--clefs-line-soft); }
.brf-timeline li:last-child::after { display: none; }
.brf-timeline li[data-kind="surfaced"]::before { background: var(--clefs-gold-400); }
.brf-timeline li[data-kind="escalation"]::before { background: #D9534F; }
.brf-timeline__what { color: var(--clefs-ink); font-size: 14px; font-weight: 600; }
.brf-timeline__when { color: var(--clefs-ink-faint); font-size: 12px; }
.brf-pick { display: grid; gap: 6px; max-height: 360px; overflow-y: auto; margin: 0; padding: 0; list-style: none; }
.brf-pick button { width: 100%; display: flex; justify-content: space-between; gap: 10px; align-items: center; text-align: left;
  padding: 10px 12px; border-radius: 10px; border: 1px solid var(--clefs-line-soft); background: var(--clefs-surface-2);
  color: var(--clefs-ink); font: inherit; font-size: 14px; cursor: pointer; min-height: 44px; }
.brf-pick button:hover { border-color: var(--clefs-line); }
.brf-pick button[aria-pressed="true"] { border-color: var(--clefs-gold-400); background: var(--clefs-gold-wash); }
.brf-pick small { color: var(--clefs-ink-faint); font-size: 12px; }
.brf-checks { display: grid; gap: 8px; }
.brf-check { display: flex; gap: 10px; align-items: center; font-size: 14px; color: var(--clefs-ink); cursor: pointer; }
.brf-check input { width: 18px; height: 18px; accent-color: var(--clefs-gold-400); }
.brf-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.brf-spacer { flex: 1 1 auto; }
.brf-order { display: flex; gap: 6px; align-items: center; }
.brf-report { background: #fff; color: #111; border-radius: 12px; padding: 16px; overflow-x: auto; }
.brf-bars { display: grid; gap: 8px; }
.brf-bars__row { display: grid; grid-template-columns: minmax(120px, 1fr) 2fr max-content; gap: 10px; align-items: center; font-size: 13px; }
.brf-bars__track { height: 8px; border-radius: 999px; background: var(--clefs-line-soft); overflow: hidden; }
.brf-bars__fill { height: 100%; border-radius: 999px; background: var(--clefs-gold-400); }
`;
