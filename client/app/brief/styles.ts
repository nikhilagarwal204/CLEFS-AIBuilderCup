/* ═══════════════════════════════════════════════════════════════════════
   Mobile brief stylesheet (prefix brf-m). Phone-first and single column:
   the owner opens it from WhatsApp with one thumb, so every tap target is
   at least 48 px, the primary decision spans the card, and nothing sits
   side by side below 400 px. Amounts keep tabular figures.
   ═══════════════════════════════════════════════════════════════════════ */

export const mStyles = `
.brf-m { min-height: 100vh; min-height: 100dvh; background: var(--clefs-slate-900); color: var(--clefs-ink);
  font-family: var(--clefs-sans); padding: 20px 16px 64px; box-sizing: border-box; }
.brf-m *, .brf-m *::before, .brf-m *::after { box-sizing: border-box; }
.brf-m__wrap { max-width: 560px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }
.brf-m__hotel { margin: 0; font-family: var(--clefs-serif); font-size: 20px; color: var(--clefs-gold-300); line-height: 1.25; }
.brf-m__hi { margin: 0; color: var(--clefs-ink-muted); font-size: 14px; }
.brf-m h1 { margin: 0; font-size: 21px; line-height: 1.35; font-weight: 600; }
.brf-m p { margin: 0; line-height: 1.5; }
.brf-m__muted { color: var(--clefs-ink-muted); font-size: 14px; }
.brf-m__small { color: var(--clefs-ink-faint); font-size: 12.5px; }
.brf-m__stake { font-size: 28px; font-weight: 600; font-variant-numeric: tabular-nums; }
.brf-m__card { background: var(--clefs-surface, rgba(255,255,255,0.04)); border: 1px solid var(--clefs-line-soft);
  border-radius: 16px; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.brf-m__card--gold { border-color: var(--clefs-line-gold); }
.brf-m__card--overdue { border-left: 4px solid #D9534F; }
.brf-m__card--done { opacity: 0.85; }
.brf-m__top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.brf-m__no { display: inline-grid; place-items: center; min-width: 28px; height: 28px; border-radius: 999px;
  border: 1px solid var(--clefs-line-gold); color: var(--clefs-gold-300); font-weight: 600; font-size: 14px; }
.brf-m__pill { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; padding: 4px 10px; border-radius: 999px;
  border: 1px solid var(--clefs-line); color: var(--clefs-ink-muted); }
.brf-m__pill--bad { border-color: rgba(217, 83, 79, 0.6); color: #F2A5A2; }
.brf-m__pill--ok { border-color: rgba(76, 175, 120, 0.6); color: #9FD9B5; }
.brf-m__pill--gold { border-color: var(--clefs-line-gold); color: var(--clefs-gold-200); background: var(--clefs-gold-wash); }
.brf-m__title { margin: 0; font-size: 17px; font-weight: 600; line-height: 1.4; }
.brf-m__impact { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
.brf-m__est { font-size: 13px; font-weight: 400; color: var(--clefs-ink-faint); }
.brf-m__meta { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 13.5px; color: var(--clefs-ink-muted); }
.brf-m__btns { display: grid; gap: 8px; grid-template-columns: 1fr 1fr; }
.brf-m__btns > .brf-m__btn--wide { grid-column: 1 / -1; }
.brf-m__btn { min-height: 48px; border-radius: 12px; border: 1px solid var(--clefs-line); background: transparent;
  color: var(--clefs-ink); font: inherit; font-size: 15px; font-weight: 600; cursor: pointer; padding: 10px 14px; }
.brf-m__btn:disabled { opacity: 0.55; cursor: default; }
.brf-m__btn--primary { background: var(--clefs-gold-400); border-color: var(--clefs-gold-400); color: var(--clefs-ink-dark, #111); }
.brf-m__btn--quiet { border-color: var(--clefs-line-soft); color: var(--clefs-ink-muted); font-weight: 500; }
.brf-m__btn:focus-visible { outline: 2px solid var(--clefs-gold-300); outline-offset: 2px; }
.brf-m__form { display: flex; flex-direction: column; gap: 8px; padding: 12px; border-radius: 12px; border: 1px solid var(--clefs-line-soft); }
.brf-m__form label { font-size: 14px; color: var(--clefs-ink-muted); }
.brf-m__input { min-height: 48px; border-radius: 10px; border: 1px solid var(--clefs-line); background: rgba(255,255,255,0.03);
  color: var(--clefs-ink); font: inherit; font-size: 17px; padding: 10px 12px; width: 100%; font-variant-numeric: tabular-nums; }
.brf-m__err { color: #F2A5A2; font-size: 14px; }
.brf-m__ok { color: #9FD9B5; font-size: 14px; }
.brf-m__verified { padding: 12px 14px; border-radius: 12px; background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line-gold); font-size: 14px; }
.brf-m__section { margin: 6px 0 0; font-size: 12px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase;
  color: var(--clefs-ink-faint); font-weight: 600; }
.brf-m__skel { height: 120px; border-radius: 16px; background: rgba(255,255,255,0.05); }
@media (max-width: 400px) { .brf-m__btns { grid-template-columns: 1fr; } }
`;
