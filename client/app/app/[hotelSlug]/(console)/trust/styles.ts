/* ═══════════════════════════════════════════════════════════════════════
   Trust console stylesheet (prefix trs-). Injected once by trust/layout.tsx.
   Colours are --clefs-* tokens plus the kit's --cx-ok/warn/bad/info, so a
   green tick means the same here as in Service. Mobile-first.

   The ROI report prints: @media print hides the console chrome (topbar,
   module tabs, bottom bar, our own tab strip and buttons) and flips the
   report to black on white, because owners hand the printout to a bank or
   a management company and the dark theme wastes toner.
   ═══════════════════════════════════════════════════════════════════════ */

export const trsStyles = `
.trs-filters { display: grid; gap: 10px; grid-template-columns: 1fr; }
@media (min-width: 720px) { .trs-filters { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (min-width: 1100px) { .trs-filters { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.trs-filters__wide { grid-column: 1 / -1; }
.trs-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; word-break: break-all; }
.trs-small { font-size: 12.5px; }
.trs-kv { display: grid; grid-template-columns: minmax(110px, 34%) 1fr; gap: 6px 12px; font-size: 13.5px; }
.trs-kv dt { color: var(--clefs-ink-muted); }
.trs-kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.trs-section-title { font-size: 13px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase;
  color: var(--clefs-ink-muted); margin: 18px 0 8px; }

/* Field diff in the audit drawer */
.trs-diff { display: flex; flex-direction: column; gap: 8px; }
.trs-diff__row { border: 1px solid var(--clefs-line-soft); border-radius: 10px; padding: 8px 10px; }
.trs-diff__path { font-weight: 600; font-size: 13px; display: flex; gap: 8px; align-items: center; }
.trs-diff__vals { display: grid; gap: 6px; grid-template-columns: 1fr; margin-top: 6px; }
@media (min-width: 560px) { .trs-diff__vals { grid-template-columns: 1fr 1fr; } }
.trs-diff__val { border-radius: 8px; padding: 6px 8px; white-space: pre-wrap; }
.trs-diff__val--before { background: rgba(var(--cx-bad-rgb), 0.08); }
.trs-diff__val--after { background: rgba(var(--cx-ok-rgb), 0.08); }
.trs-json { background: rgba(255,255,255,0.03); border: 1px solid var(--clefs-line-soft); border-radius: 10px;
  padding: 10px; max-height: 280px; overflow: auto; white-space: pre-wrap; }

/* Chain / readiness banner */
.trs-banner { display: flex; gap: 12px; align-items: flex-start; border-radius: 14px; padding: 14px 16px;
  border: 1px solid var(--clefs-line); background: var(--cx-panel); }
.trs-banner--ok { border-color: rgba(var(--cx-ok-rgb), 0.35); background: rgba(var(--cx-ok-rgb), 0.06); }
.trs-banner--bad { border-color: rgba(var(--cx-bad-rgb), 0.4); background: rgba(var(--cx-bad-rgb), 0.07); }
.trs-banner--warn { border-color: rgba(var(--cx-warn-rgb), 0.4); background: rgba(var(--cx-warn-rgb), 0.07); }
.trs-banner__icon { font-size: 22px; line-height: 1; flex: none; margin-top: 2px; }
.trs-banner__body { flex: 1; min-width: 0; }
.trs-banner__title { font-weight: 650; font-size: 15px; }

/* Readiness checklist */
.trs-checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.trs-check { display: flex; gap: 10px; align-items: flex-start; font-size: 14px; }
.trs-check__mark { flex: none; width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; }
.trs-check__mark--ok { background: rgba(var(--cx-ok-rgb), 0.16); color: var(--cx-ok); }
.trs-check__mark--bad { background: rgba(var(--cx-bad-rgb), 0.16); color: var(--cx-bad); }

/* Category pass-rate bars */
.trs-bars { display: flex; flex-direction: column; gap: 8px; }
.trs-bar { display: grid; grid-template-columns: minmax(110px, 38%) 1fr auto; gap: 10px; align-items: center; font-size: 13px; }
.trs-bar__track { height: 8px; border-radius: 99px; background: rgba(255,255,255,0.06); overflow: hidden; }
.trs-bar__fill { height: 100%; border-radius: 99px; background: var(--cx-ok); }
.trs-bar__fill--warn { background: var(--cx-warn); }
.trs-bar__fill--bad { background: var(--cx-bad); }

/* Transcript bubbles in the result drawer */
.trs-chat { display: flex; flex-direction: column; gap: 6px; }
.trs-msg { max-width: 88%; border-radius: 12px; padding: 7px 10px; font-size: 13.5px; white-space: pre-wrap; }
.trs-msg--user { align-self: flex-end; background: rgba(var(--clefs-gold-rgb), 0.12); }
.trs-msg--assistant { align-self: flex-start; background: rgba(255,255,255,0.05); }
.trs-msg__role { font-size: 11px; color: var(--clefs-ink-faint); display: block; margin-bottom: 2px; }

/* PII matrix */
.trs-matrix td, .trs-matrix th { text-align: center; }
.trs-matrix td:first-child, .trs-matrix th:first-child { text-align: left; }
.trs-cell { font-size: 12px; border-radius: 6px; padding: 2px 6px; display: inline-block; }
.trs-cell--full { color: var(--cx-ok); background: rgba(var(--cx-ok-rgb), 0.1); }
.trs-cell--none { color: var(--clefs-ink-faint); }
.trs-cell--part { color: var(--cx-warn); background: rgba(var(--cx-warn-rgb), 0.1); }

/* Overview tiles */
.trs-tile-link { color: inherit; text-decoration: none; display: block; }
.trs-tile-link:hover .cx-card, .trs-tile-link:focus-visible .cx-card { border-color: var(--clefs-line-gold); }

/* ROI report: a document, not a dashboard. Figures are links to the metric
   query that produced them; the footnote number keeps that visible on paper. */
.trs-report { max-width: 880px; margin: 0 auto; display: flex; flex-direction: column; gap: 18px; }
.trs-report__head { display: flex; flex-direction: column; gap: 4px; border-bottom: 1px solid var(--clefs-line); padding-bottom: 14px; }
.trs-report__title { font-family: var(--clefs-serif); font-size: 28px; line-height: 1.15; margin: 0; }
.trs-report__meta { color: var(--clefs-ink-muted); font-size: 13.5px; }
.trs-report h2 { font-size: 17px; margin: 0 0 10px; }
.trs-report table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.trs-report th, .trs-report td { padding: 7px 8px; border-bottom: 1px solid var(--clefs-line-soft); text-align: left; vertical-align: top; }
.trs-report th { font-weight: 600; color: var(--clefs-ink-muted); font-size: 12.5px; }
.trs-report td.trs-num, .trs-report th.trs-num { text-align: right; font-variant-numeric: tabular-nums; }
.trs-hero { display: grid; gap: 12px; grid-template-columns: 1fr; }
@media (min-width: 640px) { .trs-hero { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.trs-hero__fig { border: 1px solid var(--clefs-line); border-radius: 12px; padding: 12px 14px; }
.trs-hero__label { font-size: 12.5px; color: var(--clefs-ink-muted); }
.trs-hero__value { font-size: 24px; font-weight: 650; font-variant-numeric: tabular-nums; margin-top: 4px; }
.trs-fig { color: inherit; text-decoration: none; border-bottom: 1px dotted var(--clefs-gold-500); }
.trs-fig:hover { color: var(--clefs-gold-300); }
.trs-fn { font-size: 10px; vertical-align: super; color: var(--clefs-gold-400); margin-left: 2px; text-decoration: none; }
.trs-sources { font-size: 12px; color: var(--clefs-ink-muted); padding-left: 18px; margin: 0; }
.trs-sources li { margin-bottom: 3px; overflow-wrap: anywhere; }
.trs-caveats { margin: 0; padding-left: 18px; font-size: 13.5px; }
.trs-print-only { display: none; }

@media print {
  @page { size: A4; margin: 14mm; }
  .cx-topbar, .cx-modtabs, .cx-bottombar, .trs-noprint, .cx-page-head, .cx-tabs { display: none !important; }
  .trs-print-only { display: block; }
  html, body, .cx-shell, .cx-main { background: #fff !important; color: #111 !important; }
  .cx-main { padding: 0 !important; margin: 0 !important; max-width: none !important; }
  .trs-report { max-width: none; color: #111; }
  .trs-report * { color: #111 !important; box-shadow: none !important; }
  .trs-report .cx-card { background: #fff !important; border: 1px solid #ccc !important; break-inside: avoid; }
  .trs-report th, .trs-report td { border-bottom-color: #ddd !important; }
  .trs-hero__fig { border-color: #bbb !important; break-inside: avoid; }
  .trs-fig { border-bottom: none; }
  .trs-report .cx-badge { border: 1px solid #999 !important; background: none !important; }
}
`;
