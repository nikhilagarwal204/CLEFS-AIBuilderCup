/* Finance console styles: one string injected once by the finance layout.
   Every class is fin- prefixed and built on --clefs-* tokens, so the kit
   (cx-) stays the single source of the look and nothing leaks to other
   modules. Tiles reflow from 2 columns on a phone to 4 on a desk. */

export const finStyles = `
.fin-tiles { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 900px) { .fin-tiles { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.fin-tiles--3 { grid-template-columns: repeat(1, minmax(0, 1fr)); }
@media (min-width: 700px) { .fin-tiles--3 { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.fin-grid2 { display: grid; gap: 16px; grid-template-columns: 1fr; }
@media (min-width: 1000px) { .fin-grid2 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.fin-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; }
.fin-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; }
.fin-toolbar > .cx-field { min-width: 150px; }
.fin-spacer { flex: 1 1 auto; }
.fin-muted { color: var(--clefs-ink-faint); font-size: 13px; line-height: 1.5; }
.fin-small { font-size: 12px; color: var(--clefs-ink-faint); }
.fin-num { font-variant-numeric: tabular-nums; }
.fin-hero { display: grid; gap: 14px; grid-template-columns: 1fr; align-items: center; }
@media (min-width: 800px) { .fin-hero { grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); } }
.fin-hero__value { font-family: var(--clefs-serif); font-size: clamp(30px, 5vw, 44px); line-height: 1.1; color: var(--clefs-gold-300); }
.fin-hero__label { font-size: 12px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase; color: var(--clefs-ink-faint); }
.fin-hero__sub { color: var(--clefs-ink-muted); font-size: 14px; margin-top: 4px; }
.fin-caveats { margin: 0; padding-left: 18px; color: var(--clefs-ink-faint); font-size: 12.5px; line-height: 1.55; display: grid; gap: 4px; }
.fin-kv { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 14px; margin: 0; }
.fin-kv dt { color: var(--clefs-ink-faint); }
.fin-kv dd { margin: 0; color: var(--clefs-ink); font-variant-numeric: tabular-nums; }
.fin-chip { display: inline-flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 12.5px; color: var(--clefs-ink-muted); }
.fin-notice { padding: 12px 14px; border-radius: 12px; border: 1px solid var(--clefs-line-gold); background: var(--clefs-gold-wash);
  color: var(--clefs-ink); font-size: 14px; line-height: 1.5; }
.fin-notice--bad { border-color: rgba(217, 83, 79, 0.45); background: rgba(217, 83, 79, 0.08); }
.fin-list { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.fin-item { display: grid; gap: 6px; padding: 12px 14px; border: 1px solid var(--clefs-line-soft); border-radius: 12px;
  background: var(--clefs-surface-2); }
.fin-item__head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; }
.fin-item__title { font-weight: 600; color: var(--clefs-ink); font-size: 14px; }
.fin-check { display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; align-items: start; padding: 8px 0;
  border-bottom: 1px solid var(--clefs-line-soft); }
.fin-check:last-child { border-bottom: 0; }
.fin-check__label { font-weight: 600; color: var(--clefs-ink); font-size: 14px; }
.fin-check__detail { grid-column: 2; color: var(--clefs-ink-muted); font-size: 13px; line-height: 1.5; }
.fin-strip { display: grid; gap: 6px; }
.fin-group { display: grid; gap: 8px; }
.fin-group + .fin-group { margin-top: 18px; }
.fin-group__head { display: flex; gap: 8px; align-items: center; justify-content: space-between; margin: 0;
  font-size: 13px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase; color: var(--clefs-ink-faint); font-weight: 600; }
.fin-acct { display: grid; gap: 8px; padding: 12px 14px; border: 1px solid var(--clefs-line-soft); border-radius: 12px;
  background: var(--clefs-surface-2); }
.fin-acct--locked { opacity: 0.85; }
.fin-acct__top { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; justify-content: space-between; }
.fin-acct__name { font-weight: 600; color: var(--clefs-ink); font-size: 15px; unicode-bidi: plaintext; }
.fin-acct__code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--clefs-ink-faint); }
.fin-acct__why { color: var(--clefs-ink-muted); font-size: 13px; line-height: 1.5; unicode-bidi: plaintext; }
.fin-acct__controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; }
.fin-acct__controls .cx-field { min-width: 220px; flex: 1 1 220px; }
.fin-tags { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.fin-conn { display: grid; gap: 8px; padding: 14px; border: 1px solid var(--clefs-line-soft); border-radius: 12px; background: var(--clefs-surface-2); }
.fin-conn__name { font-weight: 600; color: var(--clefs-ink); }
.fin-bar { position: relative; height: 8px; border-radius: 999px; background: var(--clefs-line-soft); overflow: hidden; }
.fin-bar > i { position: absolute; inset: 0 auto 0 0; border-radius: 999px; background: var(--clefs-gold-500); }
.fin-sticky { position: sticky; bottom: 0; z-index: 2; padding: 10px 0; background: linear-gradient(transparent, var(--clefs-slate-950) 30%); }
.fin-amount-neg { color: var(--clefs-ink-muted); }
.fin-form { display: grid; gap: 12px; }
.fin-form-grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
.fin-link { color: var(--clefs-gold-300); text-decoration: underline; text-underline-offset: 2px; }
`;
