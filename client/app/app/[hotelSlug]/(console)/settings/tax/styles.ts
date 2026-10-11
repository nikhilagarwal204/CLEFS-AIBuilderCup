/* The tax- stylesheet for Settings > Tax (one string, injected by page.tsx). */

export const taxStyles = `
.tax-banner {
  display: flex; gap: 12px; align-items: flex-start; padding: 12px 14px; border-radius: var(--cx-radius);
  border: 1px solid rgba(var(--cx-warn-rgb), 0.4); background: rgba(var(--cx-warn-rgb), 0.09); color: var(--clefs-ink);
  font-size: 0.88rem; line-height: 1.55;
}
.tax-banner ul { margin: 6px 0 0; padding-left: 18px; }
.tax-banner a, .tax-src { color: var(--clefs-gold-400); text-decoration: underline; text-underline-offset: 2px; }
.tax-bad {
  border: 1px solid rgba(var(--cx-bad-rgb), 0.35); background: rgba(var(--cx-bad-rgb), 0.08); color: var(--clefs-ink);
  border-radius: var(--cx-radius-sm); padding: 10px 12px; font-size: 0.84rem;
}
.tax-note { font-size: 0.8rem; color: var(--clefs-ink-muted); line-height: 1.5; margin: 0; }
.tax-rate { font-variant-numeric: tabular-nums; font-weight: 600; }
.tax-sub { font-size: 0.76rem; color: var(--clefs-ink-muted); }
.tax-group {
  border: 1px solid var(--clefs-line-soft); border-radius: var(--cx-radius-sm); padding: 12px 14px;
  display: flex; flex-direction: column; gap: 8px;
}
.tax-group[data-applied] { border-color: rgba(var(--cx-warn-rgb), 0.5); }
.tax-switch { display: inline-flex; align-items: center; gap: 8px; font-size: 0.86rem; cursor: pointer; }
.tax-switch input { width: 18px; height: 18px; accent-color: var(--clefs-gold-500); }

/* LocalTax: a hotel outside the UAE, its recorded tax setup as label/value rows. */
.tax-local { margin: 0; display: grid; gap: 10px; }
.tax-local__row { display: grid; grid-template-columns: minmax(140px, 220px) 1fr; gap: 12px; font-size: 0.88rem; }
.tax-local__row dt { color: var(--clefs-ink-muted); }
.tax-local__row dd { margin: 0; color: var(--clefs-ink); }
.tax-local__row ul { margin: 0; padding-left: 18px; }
@media (max-width: 560px) { .tax-local__row { grid-template-columns: 1fr; gap: 2px; } }

/* ConventionPreview: three columns A/B/C, one line per component. */
.tax-preview-inputs { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.tax-preview-inputs > * { flex: 1 1 150px; min-width: 140px; }
.tax-cmp { overflow-x: auto; }
.tax-cmp table { width: 100%; border-collapse: collapse; font-size: 0.86rem; min-width: 420px; }
.tax-cmp th, .tax-cmp td { padding: 8px 10px; border-bottom: 1px solid var(--clefs-line-soft); text-align: right; }
.tax-cmp th:first-child, .tax-cmp td:first-child { text-align: left; }
.tax-cmp td { font-variant-numeric: tabular-nums; }
.tax-cmp th[data-mine], .tax-cmp td[data-mine] { background: rgba(var(--clefs-gold-rgb), 0.08); }
.tax-cmp tr[data-total] td { font-weight: 600; border-bottom: none; }
.tax-cmp tr[data-sub] td { color: var(--clefs-ink-muted); font-size: 0.8rem; }
`;
