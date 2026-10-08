/* ═══════════════════════════════════════════════════════════════════════
   Device PWA stylesheet (svc-dv- prefix; Service module). Injected once by
   DeviceApp next to the kit's <UiStyles/>, whose --cx-* status tokens it
   reuses so "overdue red" means the same thing on the console and the floor.

   Built for gloves and hurry: every tap target is at least 48 px, the PIN
   pad is 72 px, and nothing depends on hover. Dark ground only (floor
   devices sit in dim corridors and back offices).
   ═══════════════════════════════════════════════════════════════════════ */

export const deviceStyles = `
.svc-dv { min-height: 100dvh; background: var(--clefs-slate-900); color: var(--clefs-ink); font-family: var(--clefs-sans);
  display: flex; flex-direction: column; -webkit-tap-highlight-color: transparent;
  padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }
.svc-dv * { box-sizing: border-box; }
.svc-dv button { font-family: inherit; }
.svc-dv :focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }

/* ── Top bar ── */
.svc-dv-bar { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 10px; padding: 10px 14px;
  background: rgba(7, 12, 20, 0.92); backdrop-filter: blur(8px); border-bottom: 1px solid var(--clefs-line); }
.svc-dv-bar__title { flex: 1; min-width: 0; }
.svc-dv-bar__who { font: 600 1rem/1.2 var(--clefs-sans); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.svc-dv-bar__sub { font-size: 0.75rem; color: var(--clefs-ink-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.svc-dv-dot { width: 10px; height: 10px; border-radius: 50%; flex: none; background: var(--cx-ok); }
.svc-dv-dot--polling { background: var(--cx-warn); }
.svc-dv-dot--offline, .svc-dv-dot--unpaired { background: var(--cx-bad); }
.svc-dv-iconbtn { min-width: 48px; min-height: 48px; border-radius: 12px; border: 1px solid var(--clefs-line);
  background: var(--cx-field); color: var(--clefs-ink); font: 600 0.8rem var(--clefs-sans); padding: 0 12px; cursor: pointer; }

/* ── Banners ── */
.svc-dv-banner { margin: 10px 14px 0; padding: 10px 12px; border-radius: 12px; font-size: 0.85rem; display: flex; gap: 10px;
  align-items: center; border: 1px solid var(--clefs-line); background: var(--cx-field); }
.svc-dv-banner--warn { border-color: rgba(var(--cx-warn-rgb), 0.4); background: rgba(var(--cx-warn-rgb), 0.1); }
.svc-dv-banner--bad { border-color: rgba(var(--cx-bad-rgb), 0.45); background: rgba(var(--cx-bad-rgb), 0.14); }
.svc-dv-banner > span { flex: 1; }

/* ── Centered screens (pair, lock) ── */
.svc-dv-center { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 24px 16px; gap: 18px; }
.svc-dv-brand { font: 500 0.72rem/1 var(--clefs-sans); letter-spacing: var(--clefs-tracking-eyebrow, 0.18em); text-transform: uppercase;
  color: var(--clefs-gold-400); }
.svc-dv-h1 { font: 500 1.6rem/1.2 var(--clefs-serif, var(--clefs-sans)); margin: 0; text-align: center; }
.svc-dv-lead { color: var(--clefs-ink-muted); text-align: center; max-width: 34ch; margin: 0; font-size: 0.95rem; }
.svc-dv-code { width: min(100%, 340px); font: 600 1.8rem/1 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: 0.3em;
  text-align: center; text-transform: uppercase; padding: 16px 12px; border-radius: 14px; color: var(--clefs-ink);
  background: var(--cx-field); border: 1px solid var(--cx-field-line, var(--clefs-line)); }
.svc-dv-error { color: var(--cx-bad); font-size: 0.9rem; text-align: center; min-height: 1.3em; }
.svc-dv-big { min-height: 56px; min-width: 200px; font-size: 1rem; }

/* ── Lock screen: roster tiles and the PIN pad ── */
.svc-dv-tiles { width: min(100%, 720px); display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
.svc-dv-tile { min-height: 88px; padding: 12px; border-radius: 14px; border: 1px solid var(--clefs-line); background: var(--cx-field);
  color: var(--clefs-ink); text-align: left; cursor: pointer; display: flex; flex-direction: column; gap: 4px; justify-content: space-between; }
.svc-dv-tile[aria-pressed="true"] { border-color: var(--clefs-gold-400); background: var(--clefs-gold-wash); }
.svc-dv-tile[disabled] { opacity: 0.5; cursor: not-allowed; }
.svc-dv-tile__name { font: 600 1rem/1.2 var(--clefs-sans); }
.svc-dv-tile__meta { font-size: 0.75rem; color: var(--clefs-ink-faint); }
.svc-dv-avatar { width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center; font: 700 0.85rem var(--clefs-sans);
  background: var(--clefs-gold-wash); color: var(--clefs-gold-300); border: 1px solid var(--clefs-line-gold); }
.svc-dv-search { width: min(100%, 720px); }
.svc-dv-pinwrap { display: flex; flex-direction: column; align-items: center; gap: 14px; }
.svc-dv-pindots { display: flex; gap: 12px; height: 18px; }
.svc-dv-pindot { width: 16px; height: 16px; border-radius: 50%; border: 2px solid var(--clefs-gold-400); }
.svc-dv-pindot--on { background: var(--clefs-gold-400); }
.svc-dv-pinpad { display: grid; grid-template-columns: repeat(3, 72px); gap: 12px; }
.svc-dv-key { width: 72px; height: 72px; border-radius: 50%; border: 1px solid var(--clefs-line); background: var(--cx-field);
  color: var(--clefs-ink); font: 500 1.6rem var(--clefs-sans); cursor: pointer; touch-action: manipulation; }
.svc-dv-key:active { background: var(--clefs-gold-wash); }
.svc-dv-key--text { font-size: 0.85rem; font-weight: 600; }
.svc-dv-shake { animation: svc-dv-shake 0.35s; }
@keyframes svc-dv-shake { 25% { transform: translateX(-8px); } 75% { transform: translateX(8px); } }

/* ── Queue ── */
.svc-dv-main { flex: 1; display: flex; flex-direction: column; padding: 10px 14px 120px; gap: 10px; max-width: 820px; width: 100%; margin: 0 auto; }
.svc-dv-tabs { display: flex; gap: 8px; overflow-x: auto; }
.svc-dv-tab { flex: none; min-height: 44px; padding: 0 14px; border-radius: 999px; border: 1px solid var(--clefs-line);
  background: var(--cx-field); color: var(--clefs-ink-muted); font: 600 0.85rem var(--clefs-sans); cursor: pointer; }
.svc-dv-tab[aria-selected="true"] { color: var(--clefs-slate-900); background: var(--clefs-gold-400); border-color: var(--clefs-gold-400); }
.svc-dv-list { display: flex; flex-direction: column; gap: 10px; }
.svc-dv-card { width: 100%; text-align: left; padding: 14px; border-radius: 14px; border: 1px solid var(--clefs-line);
  background: var(--cx-panel, var(--clefs-surface)); color: var(--clefs-ink); cursor: pointer; display: flex; gap: 12px; align-items: flex-start; }
.svc-dv-card--new { border-color: var(--clefs-gold-400); box-shadow: 0 0 0 1px var(--clefs-gold-400) inset; }
.svc-dv-card--urgent { border-color: rgba(var(--cx-bad-rgb), 0.7); background: rgba(var(--cx-bad-rgb), 0.12); }
.svc-dv-card--pending { opacity: 0.75; }
.svc-dv-room { flex: none; min-width: 64px; text-align: center; padding: 8px 6px; border-radius: 10px; background: var(--cx-field);
  font: 700 1.25rem/1 var(--clefs-sans); }
.svc-dv-room small { display: block; font: 500 0.65rem/1.4 var(--clefs-sans); color: var(--clefs-ink-faint); margin-top: 4px; }
.svc-dv-card__body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.svc-dv-card__title { font: 600 1rem/1.3 var(--clefs-sans); }
.svc-dv-card__desc { color: var(--clefs-ink-muted); font-size: 0.88rem; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.svc-dv-card__meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 0.75rem; color: var(--clefs-ink-faint); }
.svc-dv-empty { padding: 40px 12px; text-align: center; color: var(--clefs-ink-faint); }

/* ── Bottom sheets (task, handover) ── */
.svc-dv-scrim { position: fixed; inset: 0; z-index: 20; background: rgba(3, 6, 12, 0.72); display: flex; align-items: flex-end; justify-content: center; }
.svc-dv-sheet { width: min(100%, 720px); max-height: 92dvh; overflow: auto; background: var(--clefs-slate-850, #0B1320);
  border: 1px solid var(--clefs-line); border-bottom: 0; border-radius: 20px 20px 0 0; padding: 16px 16px calc(16px + env(safe-area-inset-bottom));
  display: flex; flex-direction: column; gap: 14px; }
.svc-dv-sheet--full { max-height: 100dvh; min-height: 100dvh; border-radius: 0; }
.svc-dv-sheet__head { display: flex; gap: 12px; align-items: flex-start; }
.svc-dv-sheet__title { flex: 1; min-width: 0; font: 600 1.2rem/1.3 var(--clefs-sans); margin: 0; }
.svc-dv-section { display: flex; flex-direction: column; gap: 8px; }
.svc-dv-section h3 { margin: 0; font: 600 0.75rem/1 var(--clefs-sans); letter-spacing: 0.12em; text-transform: uppercase; color: var(--clefs-ink-faint); }
.svc-dv-text { white-space: pre-wrap; line-height: 1.45; font-size: 0.95rem; }
.svc-dv-kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 0.85rem; }
.svc-dv-kv dt { color: var(--clefs-ink-faint); }
.svc-dv-kv dd { margin: 0; }
.svc-dv-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.svc-dv-actions .cx-btn { min-height: 56px; font-size: 1rem; }
.svc-dv-actions .svc-dv-wide { grid-column: 1 / -1; }
.svc-dv-timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: 0.8rem; color: var(--clefs-ink-muted); }
.svc-dv-timeline li { display: flex; gap: 8px; }
.svc-dv-timeline time { flex: none; color: var(--clefs-ink-faint); min-width: 44px; }
.svc-dv-list-plain { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; font-size: 0.88rem; }
.svc-dv-count { display: inline-flex; min-width: 22px; height: 22px; padding: 0 6px; border-radius: 999px; align-items: center; justify-content: center;
  font: 700 0.72rem var(--clefs-sans); background: var(--cx-field); border: 1px solid var(--clefs-line); }

/* ── LEARN card ── */
.svc-dv-learn { border: 1px solid var(--clefs-line-gold); background: var(--clefs-gold-wash); border-radius: 14px; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.svc-dv-learn__row { display: flex; gap: 12px; align-items: flex-start; min-height: 48px; padding: 6px 4px; cursor: pointer; }
.svc-dv-learn__row input { width: 26px; height: 26px; flex: none; accent-color: var(--clefs-gold-400); margin: 2px 0 0; }
.svc-dv-learn__letter { flex: none; width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center;
  font: 700 0.95rem var(--clefs-sans); background: var(--clefs-slate-900); color: var(--clefs-gold-300); }
.svc-dv-learn__hint { display: block; font-size: 0.78rem; color: var(--clefs-ink-muted); margin-top: 2px; }

/* ── SOS ── */
.svc-dv-sos { position: fixed; right: 16px; bottom: calc(16px + env(safe-area-inset-bottom)); z-index: 10; width: 76px; height: 76px;
  border-radius: 50%; border: 3px solid rgba(255, 255, 255, 0.85); background: #B42318; color: #fff; font: 800 1.05rem var(--clefs-sans);
  letter-spacing: 0.06em; cursor: pointer; touch-action: none; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none;
  box-shadow: 0 8px 24px rgba(180, 35, 24, 0.45); overflow: hidden; }
.svc-dv-sos__fill { position: absolute; inset: 0; background: rgba(255, 255, 255, 0.35); transform-origin: bottom; transform: scaleY(0); }
.svc-dv-sos__label { position: relative; }
.svc-dv-sos-active { position: fixed; inset: 0; z-index: 30; background: #7A1209; color: #fff; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 16px; padding: 24px; text-align: center; }
.svc-dv-sos-active h2 { margin: 0; font: 800 2rem/1.2 var(--clefs-sans); }
@media (prefers-reduced-motion: reduce) { .svc-dv-shake { animation: none; } }

.svc-dv-link { background: none; border: 0; padding: 8px 0; min-height: 40px; color: var(--clefs-gold-400); font: 600 0.85rem var(--clefs-sans);
  cursor: pointer; text-decoration: underline; text-underline-offset: 3px; }
`;
