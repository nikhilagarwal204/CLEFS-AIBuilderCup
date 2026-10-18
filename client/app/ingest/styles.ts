/* The ing- stylesheet: one string for every ingest surface (dropzone,
   mapping, PDF review, batch list). Hosts inject it once through
   <IngestStyles />; duplicates are inert. Only --clefs-* and --cx-* tokens,
   so it follows the console theme. */

export const ingStyles = `
.ing-drop-meta { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; margin-bottom: 12px; }
.ing-drop-meta > * { min-width: 180px; flex: 1 1 180px; }
.ing-note { font-size: 0.8rem; color: var(--clefs-ink-muted); line-height: 1.5; margin: 0; }
.ing-warn {
  border: 1px solid rgba(var(--cx-warn-rgb), 0.35); background: rgba(var(--cx-warn-rgb), 0.08);
  color: var(--clefs-ink); border-radius: var(--cx-radius-sm); padding: 10px 12px; font-size: 0.84rem; line-height: 1.5;
}
.ing-bad {
  border: 1px solid rgba(var(--cx-bad-rgb), 0.35); background: rgba(var(--cx-bad-rgb), 0.08);
  color: var(--clefs-ink); border-radius: var(--cx-radius-sm); padding: 10px 12px; font-size: 0.84rem; line-height: 1.5;
}
.ing-okbox {
  border: 1px solid rgba(var(--cx-ok-rgb), 0.3); background: rgba(var(--cx-ok-rgb), 0.07);
  color: var(--clefs-ink); border-radius: var(--cx-radius-sm); padding: 10px 12px; font-size: 0.84rem; line-height: 1.5;
}

/* Mapping grid: canonical field | source column | confidence | sample. */
.ing-map { display: flex; flex-direction: column; gap: 8px; }
.ing-map__row {
  display: grid; gap: 8px 12px; align-items: center; padding: 10px 12px;
  border: 1px solid var(--clefs-line-soft); border-radius: var(--cx-radius-sm); background: var(--cx-field);
  grid-template-columns: 1fr;
}
.ing-map__row[data-missing] { border-color: rgba(var(--cx-bad-rgb), 0.45); }
@media (min-width: 820px) {
  .ing-map__row { grid-template-columns: minmax(150px, 1fr) minmax(180px, 1.3fr) 150px minmax(120px, 1.2fr); }
}
.ing-map__field { font-weight: 600; font-size: 0.88rem; }
.ing-map__type { font-size: 0.72rem; color: var(--clefs-ink-faint); text-transform: uppercase; letter-spacing: 0.06em; }
.ing-map__sample { font-size: 0.8rem; color: var(--clefs-ink-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ing-req { color: var(--cx-bad); margin-left: 3px; }

/* Sample rows: a small horizontally scrolling table. */
.ing-scroll { overflow-x: auto; border: 1px solid var(--clefs-line-soft); border-radius: var(--cx-radius-sm); }
.ing-grid { border-collapse: collapse; font-size: 0.8rem; min-width: 100%; }
.ing-grid th, .ing-grid td {
  padding: 7px 10px; border-bottom: 1px solid var(--clefs-line-soft); text-align: left; white-space: nowrap;
  max-width: 260px; overflow: hidden; text-overflow: ellipsis;
}
.ing-grid th { color: var(--clefs-ink-muted); font-weight: 600; background: rgba(255, 255, 255, 0.02); }
.ing-grid td.ing-num, .ing-grid th.ing-num { text-align: right; font-variant-numeric: tabular-nums; }
.ing-grid tr[data-status="ERROR"] td { background: rgba(var(--cx-bad-rgb), 0.06); }
.ing-grid tr[data-status="WARN"] td { background: rgba(var(--cx-warn-rgb), 0.05); }
.ing-grid tr[data-status="SKIP"] td, .ing-grid tr[data-status="DUP"] td { opacity: 0.55; }
.ing-grid input {
  width: 100%; min-width: 90px; background: var(--cx-field); color: var(--clefs-ink);
  border: 1px solid var(--cx-field-line); border-radius: 6px; padding: 4px 6px; font: inherit;
}
.ing-msgs { font-size: 0.74rem; color: var(--clefs-ink-muted); white-space: normal; min-width: 160px; }

/* PDF review: the page beside the extracted lines. */
.ing-pdf { display: grid; gap: 14px; grid-template-columns: 1fr; }
@media (min-width: 1100px) { .ing-pdf { grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); } }
.ing-pdf__frame {
  width: 100%; height: 70vh; min-height: 420px; border: 1px solid var(--clefs-line-soft);
  border-radius: var(--cx-radius-sm); background: var(--clefs-slate-900);
}
.ing-totals { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
.ing-totals__cell { border: 1px solid var(--clefs-line-soft); border-radius: var(--cx-radius-sm); padding: 10px 12px; }
.ing-totals__label { font-size: 0.74rem; color: var(--clefs-ink-muted); text-transform: uppercase; letter-spacing: 0.06em; }
.ing-totals__value { font-size: 1.05rem; font-weight: 600; font-variant-numeric: tabular-nums; margin-top: 4px; }

/* Batch list. */
.ing-batch-title { font-weight: 600; overflow-wrap: anywhere; }
.ing-batch-sub { font-size: 0.78rem; color: var(--clefs-ink-muted); }
.ing-counts { display: flex; gap: 6px; flex-wrap: wrap; }
.ing-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.ing-steps { display: flex; gap: 6px; flex-wrap: wrap; font-size: 0.78rem; color: var(--clefs-ink-faint); }
.ing-steps span[data-on] { color: var(--clefs-gold-400); font-weight: 600; }
.ing-grow { flex: 1 1 240px; }
`;
