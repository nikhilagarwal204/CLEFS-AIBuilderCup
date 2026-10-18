/* ═══════════════════════════════════════════════════════════════════════
   Concierge console stylesheet (prefix cc-). Injected once by
   concierge/layout.tsx for every Concierge and Phone page. Colours are
   --clefs-* tokens plus the kit's --cx-ok/warn/bad/info, so "live for
   guests" is the same green as a met SLA in Service. Mobile-first: every
   two-column block collapses to one column under 760px.
   ═══════════════════════════════════════════════════════════════════════ */

export const ccStyles = `
.cc-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; overflow-wrap: anywhere; }
.cc-small { font-size: 12.5px; }
.cc-label { font-size: 12px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase; color: var(--clefs-ink-muted); }
.cc-h3 { font-size: 15px; font-weight: 600; margin: 0; }
.cc-sub { color: var(--clefs-ink-muted); font-size: 13.5px; line-height: 1.5; margin: 4px 0 0; }
.cc-spread { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.cc-grow { flex: 1 1 auto; min-width: 0; }

/* Knowledge editor: section rail + entries */
.cc-kb { display: grid; gap: 16px; grid-template-columns: 1fr; }
@media (min-width: 960px) { .cc-kb { grid-template-columns: 260px minmax(0, 1fr); } }
.cc-rail { display: flex; flex-direction: column; gap: 4px; }
@media (max-width: 959px) { .cc-rail { flex-direction: row; overflow-x: auto; padding-bottom: 6px; } }
.cc-rail__btn { display: flex; align-items: center; justify-content: space-between; gap: 8px; text-align: left;
  background: transparent; border: 1px solid transparent; color: var(--clefs-ink); border-radius: 10px;
  padding: 8px 10px; font: inherit; font-size: 13.5px; cursor: pointer; white-space: nowrap; }
.cc-rail__btn:hover { background: var(--clefs-surface-2); }
.cc-rail__btn[aria-current="true"] { background: var(--clefs-gold-wash); border-color: var(--clefs-line-gold); }
.cc-rail__meta { display: flex; gap: 4px; align-items: center; color: var(--clefs-ink-muted); font-size: 12px; }
.cc-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; flex: none; }
.cc-dot--ok { background: var(--cx-ok); } .cc-dot--warn { background: var(--cx-warn); } .cc-dot--bad { background: var(--cx-bad); }

.cc-entry { border: 1px solid var(--clefs-line-soft); border-radius: 12px; padding: 12px 14px; display: flex;
  flex-direction: column; gap: 8px; background: var(--clefs-surface); }
.cc-entry__q { font-weight: 600; font-size: 14px; }
.cc-entry__a { font-size: 13.5px; color: var(--clefs-ink); white-space: pre-wrap; overflow-wrap: anywhere; }
.cc-entry__ar { direction: rtl; text-align: right; font-size: 13.5px; color: var(--clefs-ink-muted); white-space: pre-wrap; }
.cc-entry__foot { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }

/* EN / AR side by side. The AR column is dir=rtl on the controls
   themselves; the grid only lays them out. */
.cc-bilingual { display: grid; gap: 14px; grid-template-columns: 1fr; }
@media (min-width: 760px) { .cc-bilingual { grid-template-columns: 1fr 1fr; } }
.cc-bilingual__col { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.cc-rtl { direction: rtl; text-align: right; }
.cc-fields { display: grid; gap: 10px; grid-template-columns: 1fr; }
@media (min-width: 640px) { .cc-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.cc-fields__wide { grid-column: 1 / -1; }

/* Test answer bubble */
.cc-bubble { border-radius: 12px; padding: 10px 12px; font-size: 13.5px; white-space: pre-wrap; overflow-wrap: anywhere; }
.cc-bubble--guest { background: var(--clefs-surface-2); align-self: flex-end; max-width: 90%; }
.cc-bubble--ai { background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line-gold); max-width: 95%; }

/* Unknowns queue */
.cc-samples { margin: 0; padding-left: 18px; font-size: 13px; color: var(--clefs-ink-muted); display: flex; flex-direction: column; gap: 3px; }

/* Prompt preview and code blocks */
.cc-pre { margin: 0; padding: 12px; background: var(--clefs-slate-950); color: var(--clefs-ink); border: 1px solid var(--clefs-line-soft);
  border-radius: 10px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; line-height: 1.55;
  white-space: pre-wrap; overflow-wrap: anywhere; max-height: 560px; overflow: auto; }
.cc-pre--short { max-height: 280px; }

/* Transcripts */
.cc-thread { display: flex; flex-direction: column; gap: 8px; }
.cc-turn { display: flex; flex-direction: column; gap: 2px; max-width: 92%; }
.cc-turn--user { align-self: flex-end; align-items: flex-end; }
.cc-turn__text { border-radius: 12px; padding: 8px 11px; font-size: 13.5px; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--clefs-surface-2); }
.cc-turn--user .cc-turn__text { background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line-gold); }
.cc-turn__meta { font-size: 11.5px; color: var(--clefs-ink-faint); }
.cc-hidden { font-style: italic; color: var(--clefs-ink-faint); }

/* Key-value lists */
.cc-kv { display: grid; grid-template-columns: minmax(120px, 36%) 1fr; gap: 6px 12px; font-size: 13.5px; margin: 0; }
.cc-kv dt { color: var(--clefs-ink-muted); }
.cc-kv dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }

/* Phone setup guide */
.cc-steps { counter-reset: ccstep; display: flex; flex-direction: column; gap: 14px; margin: 0; padding: 0; list-style: none; }
.cc-step { counter-increment: ccstep; position: relative; padding-left: 38px; }
.cc-step::before { content: counter(ccstep); position: absolute; left: 0; top: 0; width: 26px; height: 26px; border-radius: 50%;
  display: grid; place-items: center; font-size: 13px; font-weight: 700; background: var(--clefs-gold-wash);
  border: 1px solid var(--clefs-line-gold); color: var(--clefs-gold-400); }
.cc-check { display: flex; gap: 10px; align-items: flex-start; font-size: 13.5px; padding: 6px 0; }
.cc-check input { margin-top: 3px; accent-color: var(--clefs-gold-500); }
.cc-callout { border: 1px solid rgba(var(--cx-warn-rgb), 0.34); background: rgba(var(--cx-warn-rgb), 0.08); border-radius: 12px;
  padding: 12px 14px; font-size: 13.5px; line-height: 1.5; }
.cc-callout--info { border-color: rgba(var(--cx-info-rgb), 0.3); background: rgba(var(--cx-info-rgb), 0.07); }
.cc-secret { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 10px 12px; border-radius: 10px;
  border: 1px dashed var(--clefs-line-gold); background: var(--clefs-gold-wash); }

/* Chips editor */
.cc-chips { display: flex; flex-direction: column; gap: 8px; }
.cc-chip-row { display: grid; gap: 8px; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) auto; align-items: center; }
.cc-lang-grid { display: flex; flex-wrap: wrap; gap: 8px 16px; }
.cc-lang-grid label { display: flex; gap: 6px; align-items: center; font-size: 13.5px; }

/* Go-live readiness checks */
.cc-checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.cc-checks__item { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: 13.5px; padding: 6px 0;
  border-bottom: 1px solid var(--clefs-line); }
.cc-checks__item:last-child { border-bottom: 0; }
.cc-checks__item svg { flex: none; width: 17px; height: 17px; color: var(--cx-ok); }
.cc-checks__item--bad svg { color: var(--cx-bad); }

/* Transcripts */
.cc-filters { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.cc-filters > * { min-width: 0; }
.cc-flag { display: inline-flex; gap: 6px; align-items: center; font-size: 13.5px; }
.cc-flag input { accent-color: var(--clefs-gold-500); }
.cc-turn--flagged .cc-turn__text { border: 1px solid rgba(var(--cx-bad-rgb), 0.4); }
.cc-tools { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.cc-more { display: flex; justify-content: center; padding: 12px; }
`;
