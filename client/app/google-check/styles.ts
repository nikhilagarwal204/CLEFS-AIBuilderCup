/**
 * One CSS string for the Google check surface (landing flow and result page),
 * `gc-` prefixed under the marketing shell, on --clefs-* tokens.
 */
export const GC_STYLES = `
  .gc { display: grid; gap: 16px; margin: 8px 0 12px; }
  .gc-card { display: grid; gap: 16px; padding: 24px; border: 1px solid var(--clefs-line);
             border-radius: 14px; background: var(--clefs-surface); max-width: 720px; }
  .gc-card--gold { border-color: var(--clefs-line-gold); background: var(--clefs-gold-wash); }
  .gc-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .gc-ticks { border: 0; padding: 0; margin: 0; display: grid; gap: 10px; }
  .gc-ticks label { display: flex; gap: 10px; align-items: flex-start; font-size: 0.92rem; line-height: 1.55;
                    color: var(--clefs-ink-muted); cursor: pointer; }
  .gc-ticks input { margin-top: 4px; accent-color: var(--clefs-gold-600); flex: none; }
  .gc-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .gc-msg { margin: 0; color: var(--clefs-ink-muted); font-size: 0.94rem; }
  .gc-alert { margin: 0; padding: 12px 16px; border-radius: 10px; font-size: 0.94rem; max-width: 720px;
              border: 1px solid rgba(229, 72, 77, 0.4); background: rgba(229, 72, 77, 0.08); color: var(--clefs-ink); }
  .gc-picks { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
  .gc-pick { width: 100%; text-align: start; display: grid; gap: 4px; padding: 14px 16px; cursor: pointer;
             border: 1px solid var(--clefs-line); border-radius: 12px; background: var(--clefs-surface-2);
             color: var(--clefs-ink); font: inherit; }
  .gc-pick:hover:not(:disabled) { border-color: var(--clefs-line-gold); }
  .gc-pick span { color: var(--clefs-ink-muted); font-size: 0.88rem; }
  .gc-pick__busy { color: var(--clefs-gold-400) !important; }
  .gc-answer { font-family: var(--clefs-serif); font-size: clamp(1.5rem, 3vw, 2rem); margin: 0; }
  .gc-answer em { color: var(--clefs-gold-400); font-style: normal; }
  .gc-disclosure { margin: 0; font-size: 0.86rem; line-height: 1.6; color: var(--clefs-ink-muted);
                   border-inline-start: 3px solid var(--clefs-gold-600); padding: 8px 12px; background: var(--clefs-gold-wash); }
  .gc-rank { margin: 0; color: var(--clefs-ink-muted); }
  .gc-rank strong { color: var(--clefs-ink); font-size: 1.1rem; }
  .gc-note { margin: 0; font-size: 0.88rem; color: var(--clefs-ink-faint); line-height: 1.6; }
  .gc-official { color: var(--clefs-gold-400); font-weight: 600; }
  @media (max-width: 640px) { .gc-card { padding: 18px 16px; } }
`;
