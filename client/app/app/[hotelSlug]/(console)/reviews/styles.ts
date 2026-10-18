/* Reviews console styles: one string injected once by ReviewsConsole. Every
   class is rev- prefixed and built on --clefs-* tokens, so the kit (cx-)
   stays the single source of the look and nothing leaks to other modules. */

export const revStyles = `
.rev-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-bottom: 14px; }
.rev-toolbar > .cx-field { min-width: 150px; }
.rev-spacer { flex: 1 1 auto; }
.rev-tiles { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 900px) { .rev-tiles { grid-template-columns: repeat(5, minmax(0, 1fr)); } }
.rev-list { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.rev-card { display: grid; gap: 8px; padding: 14px 16px; border: 1px solid var(--clefs-line-soft); border-radius: 14px;
  background: var(--clefs-surface-2); cursor: pointer; text-align: left; font: inherit; color: inherit; width: 100%; }
.rev-card:hover { border-color: var(--clefs-line); }
.rev-card:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.rev-card--neg { border-left: 3px solid #D9534F; }
.rev-card__head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.rev-card__who { font-weight: 600; color: var(--clefs-ink); }
.rev-card__meta { color: var(--clefs-ink-faint); font-size: 12px; }
.rev-card__text { color: var(--clefs-ink-muted); font-size: 14px; line-height: 1.5; display: -webkit-box;
  -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; unicode-bidi: plaintext; }
.rev-card__title { color: var(--clefs-ink); font-weight: 600; font-size: 14px; unicode-bidi: plaintext; }
.rev-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.rev-stars { display: inline-flex; gap: 1px; color: var(--clefs-gold-400); font-size: 13px; letter-spacing: 1px; }
.rev-stars__off { color: var(--clefs-line); }
.rev-body { white-space: pre-wrap; color: var(--clefs-ink); font-size: 15px; line-height: 1.6; unicode-bidi: plaintext; }
.rev-muted { color: var(--clefs-ink-faint); font-size: 13px; }
.rev-section { display: grid; gap: 8px; padding-top: 14px; margin-top: 14px; border-top: 1px solid var(--clefs-line-soft); }
.rev-section h3 { margin: 0; font-size: 13px; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase;
  color: var(--clefs-ink-faint); font-weight: 600; }
.rev-kv { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 14px; margin: 0; }
.rev-kv dt { color: var(--clefs-ink-faint); }
.rev-kv dd { margin: 0; color: var(--clefs-ink); }
.rev-checks { display: flex; flex-wrap: wrap; gap: 6px; }
.rev-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.rev-draft { white-space: pre-wrap; font-size: 14px; line-height: 1.55; color: var(--clefs-ink); padding: 12px;
  border-radius: 12px; background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line-gold); unicode-bidi: plaintext; }
.rev-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; }
.rev-grid2 { display: grid; gap: 16px; grid-template-columns: 1fr; }
@media (min-width: 1000px) { .rev-grid2 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.rev-form { display: grid; gap: 12px; }
.rev-check { display: inline-flex; gap: 8px; align-items: center; font-size: 14px; color: var(--clefs-ink); cursor: pointer; }
.rev-check input { width: 18px; height: 18px; accent-color: var(--clefs-gold-500); }
.rev-code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; padding: 2px 6px;
  border-radius: 6px; background: var(--clefs-surface-2); color: var(--clefs-ink); word-break: break-all; }
.rev-notice { padding: 12px 14px; border-radius: 12px; border: 1px solid var(--clefs-line-gold); background: var(--clefs-gold-wash);
  color: var(--clefs-ink); font-size: 14px; line-height: 1.5; }
.rev-notice--bad { border-color: rgba(217, 83, 79, 0.45); background: rgba(217, 83, 79, 0.08); }
.rev-errors { max-height: 200px; overflow: auto; font-size: 13px; margin: 8px 0 0; padding-left: 18px; color: var(--clefs-ink-muted); }
.rev-comp { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; align-items: center; padding: 10px 0;
  border-bottom: 1px solid var(--clefs-line-soft); }
.rev-comp:last-child { border-bottom: 0; }
.rev-comp__name { font-weight: 600; color: var(--clefs-ink); }
.rev-comp--own .rev-comp__name { color: var(--clefs-gold-300); }
.rev-comp__score { font-variant-numeric: tabular-nums; font-weight: 700; color: var(--clefs-ink); text-align: right; }
.rev-legend { display: flex; flex-wrap: wrap; gap: 6px; }
`;
