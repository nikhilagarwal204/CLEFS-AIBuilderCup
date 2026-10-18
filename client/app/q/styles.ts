/* ═══════════════════════════════════════════════════════════════════════
   Quote page stylesheet (prefix inq-q). Phone-first: most guests open the
   link from WhatsApp. Logical properties (inline-start/end, text-align:
   start/end) so the Arabic page mirrors without a second stylesheet;
   amounts keep tabular figures in both directions. Tap targets are at
   least 44 px. Prints cleanly (a guest may attach it to an expense claim).
   ═══════════════════════════════════════════════════════════════════════ */

export const qStyles = `
.inq-q { min-height: 100vh; min-height: 100dvh; background: var(--clefs-slate-900); color: var(--clefs-ink);
  font-family: var(--clefs-sans); padding: 24px 16px 56px; box-sizing: border-box; }
.inq-q[dir="rtl"] { font-family: 'Noto Kufi Arabic', 'Segoe UI', Tahoma, var(--clefs-sans); }
.inq-q *, .inq-q *::before, .inq-q *::after { box-sizing: border-box; }
.inq-q__wrap { max-width: 640px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
.inq-q__top { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.inq-q__hotel { margin: 0; font-family: var(--clefs-serif); font-size: 21px; color: var(--clefs-gold-300); line-height: 1.25; }
.inq-q__hotel small { display: block; font-family: var(--clefs-sans); font-size: 13px; color: var(--clefs-ink-muted); margin-top: 2px; }
.inq-q__lang { background: none; border: 1px solid var(--clefs-line); color: var(--clefs-ink); border-radius: 999px;
  padding: 8px 14px; min-height: 40px; font: inherit; font-size: 14px; cursor: pointer; flex: none; }
.inq-q__card { background: var(--clefs-surface, rgba(255,255,255,0.04)); border: 1px solid var(--clefs-line-soft);
  border-radius: 16px; padding: 18px 16px; display: flex; flex-direction: column; gap: 12px; }
.inq-q__card--gold { border-color: var(--clefs-line-gold); }
.inq-q h1 { font-size: 22px; line-height: 1.3; margin: 0; font-weight: 600; }
.inq-q h2 { font-size: 15px; margin: 0; font-weight: 600; color: var(--clefs-ink); }
.inq-q p { margin: 0; line-height: 1.55; }
.inq-q__muted { color: var(--clefs-ink-muted); font-size: 14px; }
.inq-q__small { color: var(--clefs-ink-faint); font-size: 12.5px; }
.inq-q__pill { display: inline-flex; align-items: center; gap: 6px; align-self: flex-start; font-size: 13px; padding: 5px 12px;
  border-radius: 999px; border: 1px solid var(--clefs-line-gold); color: var(--clefs-gold-200); background: var(--clefs-gold-wash); }
.inq-q__pill--warn { border-color: rgba(217, 149, 38, 0.5); color: #F0C27A; background: rgba(217, 149, 38, 0.1); }
.inq-q__stay { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.inq-q__stay div { display: flex; flex-direction: column; gap: 2px; }
.inq-q__stay span { font-size: 12.5px; color: var(--clefs-ink-faint); }
.inq-q__stay strong { font-size: 16px; font-weight: 600; }
.inq-q__table { width: 100%; border-collapse: collapse; font-size: 14.5px; }
.inq-q__table th { font-size: 12px; font-weight: 500; color: var(--clefs-ink-faint); text-align: start; padding: 0 0 6px; }
.inq-q__table td { padding: 7px 0; border-top: 1px solid var(--clefs-line-soft); vertical-align: top; }
.inq-q__table .inq-q__amt { text-align: end; font-variant-numeric: tabular-nums; white-space: nowrap; padding-inline-start: 12px; }
.inq-q__table th.inq-q__amt { padding-inline-start: 12px; }
.inq-q__table tr.inq-q__total td { border-top: 1px solid var(--clefs-line); font-weight: 700; font-size: 17px; padding-top: 10px; }
.inq-q__sub { display: block; font-size: 12.5px; color: var(--clefs-ink-faint); margin-top: 2px; }
.inq-q__notes { margin: 0; padding-inline-start: 18px; display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--clefs-ink-muted); }
.inq-q__btn { display: flex; align-items: center; justify-content: center; gap: 8px; background: var(--clefs-gold-400);
  color: var(--clefs-slate-950); border: 0; border-radius: 12px; font: inherit; font-weight: 600; font-size: 16px; min-height: 50px;
  padding: 12px 18px; cursor: pointer; width: 100%; text-decoration: none; text-align: center; }
.inq-q__btn[disabled], .inq-q__btn[aria-disabled="true"] { opacity: 0.55; cursor: default; }
.inq-q__btn--ghost { background: none; color: var(--clefs-ink); border: 1px solid var(--clefs-line); }
.inq-q button:focus-visible, .inq-q a:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.inq-q__error { color: #ff8a8a; font-size: 14px; }
.inq-q__terms { white-space: pre-wrap; font-size: 14px; color: var(--clefs-ink-muted); line-height: 1.6; }
.inq-q__skel { height: 18px; border-radius: 8px; background: rgba(255,255,255,0.06); }
.inq-q__foot { text-align: center; font-size: 12px; color: var(--clefs-ink-faint); }
@media print {
  .inq-q { background: #fff; color: #111; padding: 0; }
  .inq-q__card { border-color: #ccc; background: none; }
  .inq-q__lang, .inq-q__btn, .inq-q__actions { display: none !important; }
  .inq-q__hotel { color: #111; }
  .inq-q__muted, .inq-q__small, .inq-q__sub, .inq-q__table th { color: #555; }
}
`;
