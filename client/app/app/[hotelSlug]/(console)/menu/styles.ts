/* Menu console styles: one string injected once by page.tsx. Everything is
   ups- prefixed and built on --clefs-* tokens, so the kit (cx-) stays the
   single source of the look and nothing here leaks to other modules. */

export const upsStyles = `
.ups-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-bottom: 14px; }
.ups-toolbar > .cx-field { min-width: 160px; }
.ups-spacer { flex: 1 1 auto; }
.ups-item-name { font-weight: 600; color: var(--clefs-ink); }
.ups-item-ar { direction: rtl; unicode-bidi: plaintext; color: var(--clefs-ink-muted); font-size: 13px; }
.ups-sub { color: var(--clefs-ink-faint); font-size: 12px; }
.ups-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.ups-price { font-variant-numeric: tabular-nums; font-weight: 600; white-space: nowrap; }
.ups-thumb { width: 44px; height: 44px; border-radius: 10px; object-fit: cover; background: var(--clefs-surface-2);
  border: 1px solid var(--clefs-line-soft); flex-shrink: 0; }
.ups-form { display: grid; gap: 12px; grid-template-columns: 1fr; }
@media (min-width: 640px) { .ups-form--2 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.ups-form .ups-span { grid-column: 1 / -1; }
.ups-rtl { direction: rtl; text-align: right; }
.ups-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.ups-chip { border: 1px solid var(--clefs-line); background: transparent; color: var(--clefs-ink-muted);
  border-radius: 999px; padding: 5px 11px; font: inherit; font-size: 13px; cursor: pointer; min-height: 32px; }
.ups-chip[aria-pressed="true"] { border-color: var(--clefs-gold-500); color: var(--clefs-gold-300);
  background: var(--clefs-gold-wash); }
.ups-chip:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.ups-window { display: grid; gap: 8px; padding: 10px; border: 1px solid var(--clefs-line-soft); border-radius: 12px; }
.ups-window__row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ups-window__row .cx-input { width: 120px; }
.ups-check { display: inline-flex; gap: 8px; align-items: center; font-size: 14px; color: var(--clefs-ink); cursor: pointer; }
.ups-check input { width: 18px; height: 18px; accent-color: var(--clefs-gold-500); }
.ups-lines { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.ups-lines li { display: flex; justify-content: space-between; gap: 12px; font-size: 14px; }
.ups-totals { display: grid; gap: 4px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--clefs-line-soft); font-size: 13px; }
.ups-totals div { display: flex; justify-content: space-between; color: var(--clefs-ink-muted); }
.ups-totals .ups-grand { color: var(--clefs-ink); font-weight: 700; font-size: 15px; }
.ups-kv { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 14px; }
.ups-kv dt { color: var(--clefs-ink-faint); }
.ups-kv dd { margin: 0; color: var(--clefs-ink); }
.ups-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.ups-errors { max-height: 220px; overflow: auto; font-size: 13px; margin: 8px 0 0; padding-left: 18px; color: var(--clefs-ink-muted); }
.ups-csv-sample { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; white-space: pre;
  overflow-x: auto; padding: 10px; border-radius: 10px; background: var(--clefs-surface-2); color: var(--clefs-ink-muted); }
.ups-basket { display: grid; gap: 8px; }
.ups-basket__row { display: grid; grid-template-columns: 1fr 72px 32px; gap: 8px; align-items: center; }
.ups-funnel { display: grid; gap: 10px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 900px) { .ups-funnel { grid-template-columns: repeat(5, minmax(0, 1fr)); } }
.ups-live { width: 8px; height: 8px; border-radius: 50%; background: #2EAF6C; display: inline-block; }
`;
