/* ═══════════════════════════════════════════════════════════════════════
   Guests console stylesheet (prefix gst-). Injected once by
   guests/layout.tsx for every guests and journeys page. Colours come from
   --clefs-* tokens and the kit's --cx-ok/warn/bad/info so a chip means the
   same here as in Service. Mobile-first: one column under 720 px.
   ═══════════════════════════════════════════════════════════════════════ */

export const gstStyles = `
.gst-toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; }
.gst-toolbar > * { min-width: 0; }
.gst-grow { flex: 1 1 220px; }
.gst-small { font-size: 12.5px; }
.gst-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; overflow-wrap: anywhere; }
.gst-muted { color: var(--clefs-ink-muted); }
.gst-faint { color: var(--clefs-ink-faint); }
.gst-section-title { font-size: 12px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase;
  color: var(--clefs-ink-muted); margin: 18px 0 8px; }
.gst-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.gst-kv { display: grid; grid-template-columns: minmax(110px, 36%) 1fr; gap: 6px 12px; font-size: 13.5px; margin: 0; }
.gst-kv dt { color: var(--clefs-ink-muted); }
.gst-kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.gst-tile-link { text-decoration: none; color: inherit; display: block; }
.gst-tile-link:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 3px; border-radius: 14px; }

/* Returning-guest badge rows */
.gst-badges { display: flex; flex-direction: column; gap: 8px; }
.gst-badge-row { display: flex; gap: 12px; align-items: center; padding: 10px 12px; border-radius: 12px;
  border: 1px solid var(--clefs-line-soft); background: rgba(255,255,255,0.02); }
.gst-badge-row__room { font-weight: 700; font-size: 18px; min-width: 52px; color: var(--clefs-gold-300); }
.gst-badge-row__body { flex: 1; min-width: 0; }
.gst-badge-row__label { font-size: 14px; overflow-wrap: anywhere; }

/* Consent grid: one line per purpose */
.gst-consents { display: grid; gap: 6px; }
.gst-consent { display: flex; justify-content: space-between; gap: 10px; align-items: center; font-size: 13.5px;
  padding: 6px 0; border-bottom: 1px solid var(--clefs-line-soft); }
.gst-consent:last-child { border-bottom: 0; }

/* Check boxes (tick = explicit, never pre-ticked) */
.gst-checks { display: grid; gap: 8px; }
.gst-check { display: flex; gap: 10px; align-items: flex-start; font-size: 13.5px; line-height: 1.45; cursor: pointer; }
.gst-check input { margin-top: 3px; width: 16px; height: 16px; accent-color: var(--clefs-gold-400); flex: none; }
.gst-check--req span::after { content: " *"; color: var(--cx-bad, #e5484d); }

/* Template review: EN and AR side by side */
.gst-tpl-pair { display: grid; gap: 12px; grid-template-columns: 1fr; }
@media (min-width: 900px) { .gst-tpl-pair { grid-template-columns: 1fr 1fr; } }
.gst-tpl { border: 1px solid var(--clefs-line-soft); border-radius: 12px; padding: 12px; display: flex;
  flex-direction: column; gap: 8px; min-width: 0; }
.gst-tpl__body { white-space: pre-wrap; font-size: 14px; line-height: 1.55; background: rgba(255,255,255,0.03);
  border-radius: 10px; padding: 10px 12px; overflow-wrap: anywhere; }
.gst-tpl__body[dir="rtl"] { text-align: right; font-family: var(--clefs-sans); }
.gst-tpl__meta { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.gst-tpl__errors { margin: 0; padding-inline-start: 18px; color: var(--cx-bad, #e5484d); font-size: 12.5px; }
.gst-tpl__missing { border-style: dashed; color: var(--clefs-ink-faint); font-size: 13px; }

/* Funnel table cells */
.gst-num { font-variant-numeric: tabular-nums; }
.gst-skips { display: flex; flex-wrap: wrap; gap: 4px; }

/* Timeline */
.gst-tl { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0; }
.gst-tl li { display: grid; grid-template-columns: 14px 1fr; gap: 10px; padding-bottom: 14px; position: relative; }
.gst-tl li::before { content: ""; position: absolute; inset-inline-start: 6px; top: 14px; bottom: 0; width: 1px;
  background: var(--clefs-line-soft); }
.gst-tl li:last-child::before { display: none; }
.gst-tl__dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 5px; margin-inline-start: 1px;
  background: var(--clefs-gold-400); }
.gst-tl__dot--muted { background: var(--clefs-slate-600); }
.gst-tl__head { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; font-size: 14px; }
.gst-tl__sub { font-size: 12.5px; color: var(--clefs-ink-muted); margin-top: 2px; overflow-wrap: anywhere; }

/* Roster editor */
.gst-guest-line { display: grid; grid-template-columns: 1fr 110px 70px 32px; gap: 6px; align-items: end; }
@media (max-width: 520px) { .gst-guest-line { grid-template-columns: 1fr 1fr; } }
.gst-form-grid { display: grid; gap: 12px; grid-template-columns: 1fr; }
@media (min-width: 640px) { .gst-form-grid { grid-template-columns: 1fr 1fr; } }
.gst-form-grid__wide { grid-column: 1 / -1; }
.gst-note { font-size: 12.5px; color: var(--clefs-ink-muted); line-height: 1.5; }
.gst-warn-box { border: 1px solid rgba(245, 166, 35, 0.35); background: rgba(245, 166, 35, 0.07); border-radius: 10px;
  padding: 10px 12px; font-size: 13px; line-height: 1.5; }
`;
