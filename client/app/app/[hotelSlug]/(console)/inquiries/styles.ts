/* Inquiry Guard console styles: one string injected once by the console
   root. Every class is inq- prefixed and built on --clefs-* tokens, so the
   kit (cx-) stays the single source of the look and nothing leaks into
   other modules. Band colours pair with a text label (never colour alone). */

export const inqStyles = `
.inq-head-stats { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); margin-bottom: 14px; }
@media (min-width: 900px) { .inq-head-stats { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.inq-filters { display: grid; gap: 10px; margin-bottom: 14px; }
.inq-filters__row { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; }
.inq-filters__row > .cx-field { min-width: 150px; }
.inq-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.inq-chip { font: inherit; font-size: 13px; padding: 6px 12px; min-height: 34px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--clefs-line); background: none; color: var(--clefs-ink-muted); }
.inq-chip[aria-pressed="true"] { background: var(--clefs-gold-wash); border-color: var(--clefs-line-gold); color: var(--clefs-gold-200); }
.inq-chip:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.inq-chip-group { display: grid; gap: 6px; }
.inq-chip-group__label { font-size: 12px; color: var(--clefs-ink-faint); letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase; }
.inq-spacer { flex: 1 1 auto; }
.inq-ref { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; color: var(--clefs-ink); }
.inq-sub { display: block; color: var(--clefs-ink-faint); font-size: 12px; margin-top: 2px; }
.inq-muted { color: var(--clefs-ink-faint); font-size: 13px; }
.inq-est { color: var(--clefs-ink-faint); font-size: 11px; margin-inline-start: 4px; }
.inq-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.inq-row--between { justify-content: space-between; }
.inq-section { display: grid; gap: 10px; padding-top: 16px; margin-top: 16px; border-top: 1px solid var(--clefs-line-soft); }
.inq-section:first-child { border-top: 0; margin-top: 0; padding-top: 0; }
.inq-section > h3 { margin: 0; font-size: 12.5px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase;
  color: var(--clefs-ink-faint); font-weight: 600; }
.inq-kv { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 14px; margin: 0; }
.inq-kv dt { color: var(--clefs-ink-faint); }
.inq-kv dd { margin: 0; color: var(--clefs-ink); min-width: 0; overflow-wrap: anywhere; }
.inq-thread { display: grid; gap: 10px; max-height: 420px; overflow-y: auto; padding: 4px 2px; }
.inq-msg { max-width: 88%; padding: 10px 12px; border-radius: 12px; font-size: 14px; line-height: 1.5;
  border: 1px solid var(--clefs-line-soft); background: var(--clefs-surface-2); }
.inq-msg--out { justify-self: end; background: var(--clefs-gold-wash); border-color: var(--clefs-line-gold); }
.inq-msg--system { justify-self: center; max-width: 100%; background: none; border-style: dashed; color: var(--clefs-ink-muted); font-size: 13px; }
.inq-msg__body { white-space: pre-wrap; unicode-bidi: plaintext; overflow-wrap: anywhere; color: var(--clefs-ink); }
.inq-msg__meta { display: flex; flex-wrap: wrap; gap: 6px; font-size: 11.5px; color: var(--clefs-ink-faint); margin-top: 4px; }
.inq-evidence { margin: 0; padding: 6px 10px; border-inline-start: 2px solid var(--clefs-gold-600); color: var(--clefs-ink-muted);
  font-size: 13px; unicode-bidi: plaintext; background: rgba(255,255,255,0.02); }
.inq-evidence b { color: var(--clefs-ink-faint); font-weight: 500; margin-inline-end: 6px; }
.inq-form { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.inq-form > .inq-full { grid-column: 1 / -1; }
@media (max-width: 560px) { .inq-form { grid-template-columns: 1fr; } }
.inq-note { font-size: 13px; color: var(--clefs-ink-muted); padding: 10px 12px; border-radius: 10px;
  border: 1px solid var(--clefs-line-soft); background: rgba(255,255,255,0.02); }
.inq-note--warn { border-color: rgba(217, 149, 38, 0.45); color: var(--clefs-ink); }
.inq-note--bad { border-color: rgba(217, 83, 79, 0.5); color: var(--clefs-ink); }
.inq-list { margin: 0; padding-inline-start: 18px; display: grid; gap: 4px; font-size: 13.5px; color: var(--clefs-ink-muted); }
.inq-grid-wrap { overflow-x: auto; border: 1px solid var(--clefs-line-soft); border-radius: 12px; }
.inq-grid { width: 100%; border-collapse: collapse; font-size: 13.5px; min-width: 560px; }
.inq-grid th, .inq-grid td { padding: 8px 10px; text-align: start; border-bottom: 1px solid var(--clefs-line-soft); vertical-align: middle; }
.inq-grid th { font-size: 11.5px; font-weight: 600; color: var(--clefs-ink-faint); letter-spacing: 0.04em; text-transform: uppercase; }
.inq-grid tr:last-child td { border-bottom: 0; }
.inq-grid .inq-num { text-align: end; font-variant-numeric: tabular-nums; white-space: nowrap; }
.inq-rate { width: 110px; font: inherit; font-size: 14px; text-align: end; padding: 6px 8px; border-radius: 8px; min-height: 34px;
  color: var(--clefs-ink); background: rgba(255,255,255,0.04); border: 1px solid var(--clefs-line); font-variant-numeric: tabular-nums; }
.inq-rate:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 1px; }
.inq-rate[aria-invalid="true"] { border-color: #D9534F; }
.inq-band { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; white-space: nowrap; }
.inq-band::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--clefs-ink-faint); }
.inq-band--at_bar::before { background: #5CB85C; }
.inq-band--below_bar::before { background: #D99526; }
.inq-band--below_floor::before, .inq-band--above_ceiling::before, .inq-band--missing::before { background: #D9534F; }
.inq-band--no_guardrails::before { background: var(--clefs-ink-faint); }
.inq-bar { position: relative; height: 6px; border-radius: 3px; background: rgba(255,255,255,0.06); min-width: 90px; }
.inq-bar__band { position: absolute; top: 0; bottom: 0; background: rgba(var(--clefs-gold-rgb), 0.28); border-radius: 3px; }
.inq-bar__tick { position: absolute; top: -3px; width: 2px; height: 12px; background: var(--clefs-ink-muted); }
.inq-bar__rate { position: absolute; top: -3px; width: 8px; height: 12px; margin-inline-start: -4px; border-radius: 3px; background: var(--clefs-gold-300); }
.inq-taxes { width: 100%; border-collapse: collapse; font-size: 14px; }
.inq-taxes td { padding: 5px 0; }
.inq-taxes td:last-child { text-align: end; font-variant-numeric: tabular-nums; }
.inq-taxes tr.inq-total td { border-top: 1px solid var(--clefs-line); padding-top: 9px; font-weight: 600; color: var(--clefs-ink); }
.inq-code { margin: 0; padding: 10px 12px; border-radius: 10px; background: rgba(0,0,0,0.25); border: 1px solid var(--clefs-line-soft);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--clefs-ink); }
.inq-checklist { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.inq-checklist li { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border-radius: 12px;
  border: 1px solid var(--clefs-line-soft); background: var(--clefs-surface-2); }
.inq-checklist__mark { flex: none; width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; font-size: 12px;
  border: 1px solid var(--clefs-line); color: var(--clefs-ink-faint); }
.inq-checklist__mark--done { border-color: #5CB85C; color: #5CB85C; }
.inq-radio { display: grid; gap: 6px; }
.inq-radio label { display: flex; gap: 10px; align-items: center; font-size: 14px; padding: 6px 8px; border-radius: 8px; cursor: pointer; }
.inq-radio label:hover { background: rgba(255,255,255,0.03); }
.inq-radio input { accent-color: var(--clefs-gold-400); width: 16px; height: 16px; }
.inq-tabs-body { margin-top: 14px; }
`;
