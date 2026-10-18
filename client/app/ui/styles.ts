/* ═══════════════════════════════════════════════════════════════════════
   UI kit stylesheet, injected once by <UiStyles/> (ConsoleShell and the
   auth pages render it).

   Every class is prefixed cx- because StaffDashboard injects GLOBAL classes
   (.glass-card, .modal-box, .status-badge, .form-input ...) into the same
   page when it is mounted as the Service module; unprefixed kit classes
   would collide with them. Module CSS strings use their own prefixes
   (svc-, pfg-, dr- ...).

   Colours come from the --clefs-* tokens in globals.css. The design system
   has no status tokens yet, so --cx-ok/warn/bad/info are declared here with
   the same values the dashboard already uses for success/pending/urgent, so
   a green tick means the same thing in both surfaces. (The legacy --hc-*
   aliases are deliberately not referenced: they must not grow.)

   Mobile-first: base rules target a phone; min-width queries add room.
   ═══════════════════════════════════════════════════════════════════════ */

import { chartStyles } from "../app/charts";

/* chartStyles (.chart-tip, .chart-empty, .hbar-row ...) is appended because
   charts-extra reuses charts.tsx's Tip and row layout. When StaffDashboard is
   mounted it injects the same rules again; identical duplicates are inert. */
export const uiStyles = `
:root {
  --cx-ok: #34C77B;
  --cx-ok-rgb: 52, 199, 123;
  --cx-warn: #E9A93B;
  --cx-warn-rgb: 233, 169, 59;
  --cx-bad: #E5484D;
  --cx-bad-rgb: 229, 72, 77;
  --cx-info: #5B9BE8;
  --cx-info-rgb: 91, 155, 232;
  --cx-radius: 14px;
  --cx-radius-sm: 10px;
  --cx-panel: var(--clefs-slate-850);
  --cx-field: rgba(255, 255, 255, 0.04);
  --cx-field-line: rgba(255, 255, 255, 0.1);
}

.cx-root, .cx-root * { box-sizing: border-box; }
.cx-sr {
  position: absolute !important; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}
.cx-num { font-variant-numeric: tabular-nums; }
.cx-muted { color: var(--clefs-ink-muted); }
.cx-faint { color: var(--clefs-ink-faint); }
.cx-stack { display: flex; flex-direction: column; gap: 16px; }
.cx-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.cx-grid { display: grid; gap: 14px; grid-template-columns: 1fr; }
@media (min-width: 640px) { .cx-grid--2, .cx-grid--3, .cx-grid--4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (min-width: 1024px) {
  .cx-grid--3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .cx-grid--4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
}

@keyframes cx-fade { from { opacity: 0; } to { opacity: 1; } }
@keyframes cx-rise { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: none; } }
@keyframes cx-slide-in { from { transform: translateX(100%); } to { transform: none; } }
@keyframes cx-shimmer { to { background-position: -200% 0; } }
@keyframes cx-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .cx-root *, .cx-overlay, .cx-modal, .cx-drawer, .cx-toast { animation: none !important; transition: none !important; }
}

/* ─── Page header ─── */
.cx-page-head { display: flex; flex-direction: column; gap: 14px; margin-bottom: 22px; }
.cx-page-head__eyebrow {
  font-size: 0.68rem; letter-spacing: var(--clefs-tracking-eyebrow); text-transform: uppercase;
  color: var(--clefs-gold-700); margin-bottom: 6px;
}
.cx-page-head__title {
  font-family: var(--clefs-serif); font-weight: 500; letter-spacing: -0.005em;
  font-size: clamp(1.45rem, 5vw, 2.1rem); line-height: 1.15; color: var(--clefs-ink); margin: 0;
}
.cx-page-head__sub { color: var(--clefs-ink-muted); font-size: 0.9rem; line-height: 1.5; margin-top: 6px; max-width: 64ch; }
.cx-page-head__actions { display: flex; gap: 8px; flex-wrap: wrap; }
@media (min-width: 768px) {
  .cx-page-head { flex-direction: row; align-items: flex-end; justify-content: space-between; margin-bottom: 28px; }
}

/* ─── Card ─── */
.cx-card {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--clefs-line-soft);
  border-radius: 16px; min-width: 0; position: relative;
}
.cx-card--pad { padding: 16px; }
@media (min-width: 768px) { .cx-card--pad { padding: 20px 22px; } }
.cx-card__head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.cx-card__title { font-size: 0.95rem; font-weight: 600; color: var(--clefs-ink); letter-spacing: -0.01em; margin: 0; }
.cx-card__sub { font-size: 0.78rem; color: var(--clefs-ink-faint); margin-top: 4px; line-height: 1.45; }
.cx-card--gold::after {
  content: ''; position: absolute; inset: 0 0 auto 0; height: 1px; border-radius: 16px 16px 0 0;
  background: linear-gradient(90deg, transparent, var(--clefs-line-gold), transparent);
}

/* ─── Stat tile ─── */
.cx-stat { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.cx-stat__label { font-size: 0.7rem; letter-spacing: 0.13em; text-transform: uppercase; color: var(--clefs-ink-faint); }
.cx-stat__body { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; }
.cx-stat__value {
  font-family: var(--clefs-serif); font-weight: 500; font-size: 1.9rem; line-height: 1; color: var(--clefs-ink);
  font-variant-numeric: tabular-nums; min-width: 0; overflow-wrap: anywhere;
}
.cx-stat__spark { flex-shrink: 0; }
.cx-stat__foot { display: flex; align-items: center; gap: 8px; font-size: 0.76rem; color: var(--clefs-ink-faint); flex-wrap: wrap; }
.cx-delta { font-weight: 600; font-variant-numeric: tabular-nums; }
.cx-delta--good { color: var(--cx-ok); }
.cx-delta--bad { color: var(--cx-bad); }
.cx-delta--flat { color: var(--clefs-ink-muted); }

/* ─── Badge ─── */
.cx-badge {
  display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px;
  border-radius: 999px; font-size: 0.72rem; font-weight: 600; letter-spacing: 0.01em;
  white-space: nowrap; border: 1px solid transparent; line-height: 1;
}
.cx-badge__dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.cx-badge--neutral { color: var(--clefs-ink-muted); background: var(--clefs-surface-2); border-color: var(--clefs-line-soft); }
.cx-badge--gold { color: var(--clefs-gold-400); background: var(--clefs-gold-wash); border-color: var(--clefs-line); }
.cx-badge--ok { color: var(--cx-ok); background: rgba(var(--cx-ok-rgb), 0.1); border-color: rgba(var(--cx-ok-rgb), 0.28); }
.cx-badge--warn { color: var(--cx-warn); background: rgba(var(--cx-warn-rgb), 0.1); border-color: rgba(var(--cx-warn-rgb), 0.28); }
.cx-badge--bad { color: #FF8A8D; background: rgba(var(--cx-bad-rgb), 0.12); border-color: rgba(var(--cx-bad-rgb), 0.34); }
.cx-badge--info { color: #8AB8EF; background: rgba(var(--cx-info-rgb), 0.1); border-color: rgba(var(--cx-info-rgb), 0.28); }

/* ─── Button ─── */
.cx-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  min-height: 40px; padding: 0 16px; border-radius: var(--cx-radius-sm);
  font: 600 0.86rem/1 var(--clefs-sans); letter-spacing: 0.005em; white-space: nowrap;
  border: 1px solid transparent; cursor: pointer; text-decoration: none;
  transition: background 0.18s ease, border-color 0.18s ease, color 0.18s ease, transform 0.18s ease, opacity 0.18s;
  -webkit-tap-highlight-color: transparent;
}
.cx-btn:active:not([disabled]) { transform: translateY(1px); }
.cx-btn[disabled], .cx-btn[aria-disabled="true"] { opacity: 0.45; cursor: not-allowed; }
.cx-btn--primary { background: var(--clefs-gold-500); color: var(--clefs-ink-dark); }
.cx-btn--primary:hover:not([disabled]) { background: var(--clefs-gold-400); }
.cx-btn--secondary { background: var(--clefs-surface-2); color: var(--clefs-ink); border-color: var(--clefs-line-soft); }
.cx-btn--secondary:hover:not([disabled]) { border-color: var(--clefs-line-gold); color: var(--clefs-gold-300); }
.cx-btn--ghost { background: transparent; color: var(--clefs-ink-muted); }
.cx-btn--ghost:hover:not([disabled]) { background: var(--clefs-surface-2); color: var(--clefs-ink); }
.cx-btn--danger { background: rgba(var(--cx-bad-rgb), 0.12); color: #FF8A8D; border-color: rgba(var(--cx-bad-rgb), 0.34); }
.cx-btn--danger:hover:not([disabled]) { background: rgba(var(--cx-bad-rgb), 0.2); }
.cx-btn--sm { min-height: 32px; padding: 0 12px; font-size: 0.8rem; border-radius: 8px; }
.cx-btn--block { width: 100%; }
.cx-btn--icon { padding: 0; width: 40px; }
.cx-btn--sm.cx-btn--icon { width: 32px; }
.cx-spinner {
  width: 14px; height: 14px; border-radius: 50%; flex-shrink: 0;
  border: 2px solid currentColor; border-right-color: transparent; animation: cx-spin 0.7s linear infinite;
}

/* ─── Empty state / skeleton ─── */
.cx-empty {
  display: flex; flex-direction: column; align-items: center; text-align: center;
  gap: 10px; padding: 44px 20px; color: var(--clefs-ink-muted);
}
.cx-empty__icon {
  width: 52px; height: 52px; border-radius: 50%; display: grid; place-items: center;
  background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line); color: var(--clefs-gold-500); font-size: 1.3rem;
  margin-bottom: 4px;
}
.cx-empty__title { font-family: var(--clefs-serif); font-size: 1.2rem; font-weight: 500; color: var(--clefs-ink); margin: 0; }
.cx-empty__body { font-size: 0.86rem; line-height: 1.55; max-width: 44ch; }
.cx-empty__action { margin-top: 8px; }
.cx-skel {
  display: block; border-radius: 8px;
  background: linear-gradient(90deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.09) 50%, rgba(255,255,255,0.04) 100%);
  background-size: 200% 100%; animation: cx-shimmer 1.4s linear infinite;
}

/* ─── Tabs / segmented ─── */
.cx-tabs {
  display: flex; gap: 2px; border-bottom: 1px solid var(--clefs-line-soft);
  overflow-x: auto; scrollbar-width: none; margin-bottom: 20px;
}
.cx-tabs::-webkit-scrollbar { display: none; }
.cx-tab {
  display: inline-flex; align-items: center; gap: 8px; padding: 10px 12px; margin-bottom: -1px;
  background: none; border: 0; border-bottom: 2px solid transparent; cursor: pointer;
  color: var(--clefs-ink-muted); font: 500 0.86rem/1.2 var(--clefs-sans); white-space: nowrap; text-decoration: none;
}
.cx-tab:hover { color: var(--clefs-ink); }
.cx-tab[aria-selected="true"], .cx-tab[aria-current="page"] { color: var(--clefs-gold-400); border-bottom-color: var(--clefs-gold-500); }
.cx-tab__count {
  font-size: 0.7rem; font-weight: 600; padding: 2px 7px; border-radius: 999px;
  background: var(--clefs-surface-2); color: var(--clefs-ink-muted);
}
.cx-seg {
  display: inline-flex; padding: 3px; gap: 2px; border-radius: var(--cx-radius-sm);
  background: rgba(255,255,255,0.03); border: 1px solid var(--clefs-line-soft); max-width: 100%; overflow-x: auto;
}
.cx-seg__opt {
  border: 0; background: none; cursor: pointer; white-space: nowrap;
  padding: 7px 12px; border-radius: 7px; color: var(--clefs-ink-muted); font: 500 0.8rem/1 var(--clefs-sans);
}
.cx-seg__opt:hover { color: var(--clefs-ink); }
.cx-seg__opt[aria-pressed="true"] { background: var(--clefs-surface-2); color: var(--clefs-gold-400); box-shadow: inset 0 0 0 1px var(--clefs-line); }
.cx-seg--sm .cx-seg__opt { padding: 5px 9px; font-size: 0.74rem; }

/* ─── Forms ─── */
.cx-field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.cx-label { font-size: 0.78rem; font-weight: 600; color: var(--clefs-ink-muted); letter-spacing: 0.01em; }
.cx-label__req { color: var(--clefs-gold-500); margin-left: 3px; }
.cx-hint { font-size: 0.74rem; color: var(--clefs-ink-faint); line-height: 1.45; }
.cx-error-text { font-size: 0.76rem; color: #FF8A8D; line-height: 1.45; }
.cx-input, .cx-select, .cx-textarea {
  width: 100%; min-height: 44px; padding: 10px 12px; border-radius: var(--cx-radius-sm);
  background: var(--cx-field); border: 1px solid var(--cx-field-line); color: var(--clefs-ink);
  font: 400 16px/1.4 var(--clefs-sans); outline: none; transition: border-color 0.18s, background 0.18s;
  color-scheme: dark;
}
@media (min-width: 768px) { .cx-input, .cx-select, .cx-textarea { min-height: 40px; font-size: 0.9rem; } }
.cx-input::placeholder, .cx-textarea::placeholder { color: rgba(242,238,230,0.3); }
.cx-input:focus, .cx-select:focus, .cx-textarea:focus { border-color: var(--clefs-line-gold); background: rgba(255,255,255,0.06); }
.cx-input[aria-invalid="true"], .cx-select[aria-invalid="true"], .cx-textarea[aria-invalid="true"] { border-color: rgba(var(--cx-bad-rgb), 0.7); }
.cx-input[disabled], .cx-select[disabled], .cx-textarea[disabled] { opacity: 0.55; cursor: not-allowed; }
.cx-select option { background: var(--clefs-slate-850); }
.cx-textarea { resize: vertical; min-height: 96px; }
.cx-textarea--code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.82rem; line-height: 1.55; min-height: 280px; tab-size: 2; }
.cx-form-grid { display: grid; gap: 14px; grid-template-columns: 1fr; }
@media (min-width: 640px) { .cx-form-grid--2 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.cx-form-actions { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; margin-top: 18px; }
.cx-daterange { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.cx-daterange__inputs { display: flex; gap: 6px; align-items: center; }
.cx-daterange__inputs .cx-input { width: auto; min-width: 0; }

/* ─── Table (cards below 640px, via data-label) ─── */
.cx-table-wrap { width: 100%; }
.cx-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 0.86rem; }
.cx-table thead { display: none; }
.cx-table tr { display: block; border: 1px solid var(--clefs-line-soft); border-radius: 12px; padding: 6px 12px; margin-bottom: 10px; background: rgba(255,255,255,0.02); }
.cx-table td {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  padding: 7px 0; border-bottom: 1px solid rgba(255,255,255,0.04); color: var(--clefs-ink); text-align: right;
}
.cx-table td:last-child { border-bottom: 0; }
.cx-table td::before {
  content: attr(data-label); flex-shrink: 0; text-align: left;
  font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--clefs-ink-faint);
}
.cx-table td[data-label=""]::before { content: none; }
.cx-table--clickable tbody tr { cursor: pointer; }
.cx-table__empty td { justify-content: center; color: var(--clefs-ink-faint); }
.cx-table__empty td::before { content: none; }
@media (min-width: 640px) {
  .cx-table-wrap { overflow-x: auto; }
  .cx-table thead { display: table-header-group; }
  .cx-table tr { display: table-row; border: 0; padding: 0; margin: 0; background: none; }
  .cx-table th {
    text-align: left; font-weight: 600; font-size: 0.7rem; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--clefs-ink-faint); padding: 10px 12px; border-bottom: 1px solid var(--clefs-line-soft); white-space: nowrap;
  }
  .cx-table td { display: table-cell; padding: 12px; text-align: left; vertical-align: middle; }
  .cx-table td::before { content: none; }
  .cx-table tbody tr:hover td { background: rgba(255,255,255,0.025); }
  .cx-table th.cx-al-right, .cx-table td.cx-al-right { text-align: right; }
  .cx-table th.cx-al-center, .cx-table td.cx-al-center { text-align: center; }
  .cx-table .cx-hide-desktop { display: none; }
}
@media (max-width: 639.98px) { .cx-table .cx-hide-mobile { display: none; } }

/* ─── Overlays: modal (bottom sheet on phones) and drawer ─── */
.cx-overlay {
  position: fixed; inset: 0; z-index: 1200; background: rgba(0,0,0,0.66);
  -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px);
  display: flex; align-items: flex-end; justify-content: center; animation: cx-fade 0.18s ease;
}
.cx-modal {
  width: 100%; max-height: calc(100dvh - 24px); display: flex; flex-direction: column;
  background: var(--cx-panel); border: 1px solid var(--clefs-line-soft); border-radius: 18px 18px 0 0;
  box-shadow: var(--clefs-shadow-3); animation: cx-rise 0.22s var(--ease-out); color: var(--clefs-ink);
  padding-bottom: env(safe-area-inset-bottom);
}
@media (min-width: 640px) {
  .cx-overlay { align-items: center; padding: 16px; }
  .cx-modal { max-width: 520px; border-radius: 18px; max-height: calc(100dvh - 32px); padding-bottom: 0; }
  .cx-modal--sm { max-width: 400px; }
  .cx-modal--lg { max-width: 760px; }
}
.cx-modal__head, .cx-drawer__head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 16px 18px; border-bottom: 1px solid var(--clefs-line-soft);
}
.cx-modal__title, .cx-drawer__title { font-size: 1rem; font-weight: 600; margin: 0; }
.cx-modal__body, .cx-drawer__body { padding: 18px; overflow-y: auto; flex: 1; min-height: 0; }
.cx-modal__foot, .cx-drawer__foot {
  display: flex; justify-content: flex-end; gap: 8px; flex-wrap: wrap;
  padding: 14px 18px; border-top: 1px solid var(--clefs-line-soft);
}
.cx-overlay--drawer { justify-content: flex-end; align-items: stretch; padding: 0; }
.cx-drawer {
  width: 100%; height: 100dvh; display: flex; flex-direction: column;
  background: var(--cx-panel); border-left: 1px solid var(--clefs-line-soft);
  box-shadow: var(--clefs-shadow-3); animation: cx-slide-in 0.24s var(--ease-out); color: var(--clefs-ink);
  padding-bottom: env(safe-area-inset-bottom);
}
@media (min-width: 640px) { .cx-drawer { width: min(480px, 100vw); } .cx-drawer--wide { width: min(720px, 100vw); } }
.cx-close {
  background: none; border: 0; color: var(--clefs-ink-muted); cursor: pointer;
  width: 36px; height: 36px; border-radius: 8px; display: grid; place-items: center; font-size: 1.1rem;
}
.cx-close:hover { background: var(--clefs-surface-2); color: var(--clefs-ink); }

/* ─── Toasts ─── */
.cx-toasts {
  position: fixed; z-index: 1300; left: 12px; right: 12px;
  bottom: calc(var(--cx-bottom, 0px) + 12px + env(safe-area-inset-bottom));
  display: flex; flex-direction: column; gap: 8px; pointer-events: none;
}
@media (min-width: 640px) { .cx-toasts { left: auto; right: 20px; bottom: 20px; width: 380px; } }
.cx-toast {
  pointer-events: auto; display: flex; gap: 10px; align-items: flex-start;
  background: var(--clefs-slate-800); border: 1px solid var(--clefs-line-soft); border-left: 3px solid var(--clefs-gold-500);
  border-radius: 12px; padding: 12px 12px 12px 14px; box-shadow: var(--clefs-shadow-2);
  animation: cx-rise 0.22s var(--ease-out); color: var(--clefs-ink);
}
.cx-toast--ok { border-left-color: var(--cx-ok); }
.cx-toast--warn { border-left-color: var(--cx-warn); }
.cx-toast--bad { border-left-color: var(--cx-bad); }
.cx-toast--info { border-left-color: var(--cx-info); }
.cx-toast__text { flex: 1; min-width: 0; }
.cx-toast__title { font-size: 0.86rem; font-weight: 600; }
.cx-toast__body { font-size: 0.8rem; color: var(--clefs-ink-muted); margin-top: 3px; line-height: 1.45; overflow-wrap: anywhere; }

/* ─── Upload ─── */
.cx-drop {
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; text-align: center;
  padding: 26px 18px; border-radius: var(--cx-radius); cursor: pointer;
  border: 1.5px dashed var(--clefs-line-gold); background: rgba(255,255,255,0.02); color: var(--clefs-ink-muted);
  transition: background 0.18s, border-color 0.18s;
}
.cx-drop:hover, .cx-drop:focus-visible, .cx-drop[data-over="true"] { background: var(--clefs-gold-wash); border-color: var(--clefs-gold-500); }
.cx-drop[aria-disabled="true"] { opacity: 0.5; cursor: not-allowed; }
.cx-drop__title { color: var(--clefs-ink); font-weight: 600; font-size: 0.9rem; }
.cx-drop__hint { font-size: 0.76rem; color: var(--clefs-ink-faint); }
.cx-progress { height: 4px; border-radius: 2px; background: rgba(255,255,255,0.07); overflow: hidden; width: 100%; }
.cx-progress__bar { height: 100%; background: var(--clefs-gold-500); border-radius: 2px; transition: width 0.2s ease; }

/* ─── Money / confidence / evidence ─── */
.cx-money { font-variant-numeric: tabular-nums; white-space: nowrap; }
.cx-money--pos { color: var(--cx-ok); }
.cx-money--neg { color: #FF8A8D; }
/* Visually hidden, still read by screen readers (chart data tables, icon labels). */
.cx-sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.cx-conf { display: inline-flex; align-items: center; gap: 7px; }
.cx-conf__bars { display: inline-flex; gap: 2px; align-items: flex-end; }
.cx-conf__bars i { width: 3px; border-radius: 1px; background: rgba(255,255,255,0.14); display: block; }
.cx-conf__bars i:nth-child(1) { height: 6px; } .cx-conf__bars i:nth-child(2) { height: 9px; } .cx-conf__bars i:nth-child(3) { height: 12px; }
.cx-conf__bars i[data-on="true"] { background: currentColor; }
.cx-evidence { display: flex; flex-direction: column; gap: 2px; }
.cx-evidence__item {
  display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; align-items: start;
  padding: 9px 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 0.84rem;
}
.cx-evidence__item:last-child { border-bottom: 0; }
.cx-evidence__kind {
  font-size: 0.64rem; letter-spacing: 0.1em; text-transform: uppercase; color: var(--clefs-gold-700);
  padding: 3px 6px; border: 1px solid var(--clefs-line); border-radius: 5px; margin-top: 1px;
}
.cx-evidence__label { color: var(--clefs-ink-muted); }
.cx-evidence__value { grid-column: 2; color: var(--clefs-ink); overflow-wrap: anywhere; }
.cx-evidence__quote { grid-column: 2; font-style: italic; color: var(--clefs-ink); border-left: 2px solid var(--clefs-line-gold); padding-left: 10px; }
.cx-evidence__pre {
  grid-column: 2; margin: 0; font: 0.76rem/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--clefs-ink-muted);
  background: rgba(0,0,0,0.25); border-radius: 8px; padding: 8px 10px; overflow-x: auto; white-space: pre-wrap;
}
.cx-link { color: var(--clefs-gold-400); text-decoration: none; border-bottom: 1px solid var(--clefs-line); }
.cx-link:hover { color: var(--clefs-gold-300); border-bottom-color: var(--clefs-gold-500); }
button.cx-link { background: none; border-top: 0; border-left: 0; border-right: 0; padding: 0; cursor: pointer; font: inherit; }

/* ─── Signal card / action bar ─── */
.cx-signal { display: flex; flex-direction: column; gap: 12px; border-left: 3px solid var(--cx-accent, var(--clefs-line-gold)); }
.cx-signal--money_lost { --cx-accent: var(--cx-bad); }
.cx-signal--money_available { --cx-accent: var(--cx-ok); }
.cx-signal--service_risk { --cx-accent: var(--cx-warn); }
.cx-signal--reviews { --cx-accent: var(--cx-info); }
.cx-signal--verified { --cx-accent: var(--clefs-gold-500); }
.cx-signal--compliance { --cx-accent: rgba(242,238,230,0.4); }
.cx-signal__top { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.cx-signal__title { font-size: 1rem; font-weight: 600; line-height: 1.35; margin: 0; color: var(--clefs-ink); }
.cx-signal__impact { font-family: var(--clefs-serif); font-size: 1.5rem; line-height: 1; color: var(--clefs-ink); }
.cx-signal__meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 0.78rem; color: var(--clefs-ink-faint); }
.cx-signal__summary { font-size: 0.86rem; color: var(--clefs-ink-muted); line-height: 1.55; margin: 0; }
.cx-actionbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.cx-actionbar__more { position: relative; }
.cx-menu {
  position: absolute; z-index: 60; top: calc(100% + 6px); right: 0; min-width: 180px;
  background: var(--clefs-slate-800); border: 1px solid var(--clefs-line-soft); border-radius: 12px;
  box-shadow: var(--clefs-shadow-2); padding: 6px; display: flex; flex-direction: column; animation: cx-fade 0.14s ease;
}
.cx-menu__item {
  display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; border: 0; background: none; cursor: pointer;
  padding: 9px 10px; border-radius: 8px; color: var(--clefs-ink); font: 500 0.84rem/1.2 var(--clefs-sans); text-decoration: none;
}
.cx-menu__item:hover:not([disabled]) { background: var(--clefs-surface-2); }
.cx-menu__item[disabled] { opacity: 0.45; cursor: not-allowed; }
.cx-menu__sep { height: 1px; background: var(--clefs-line-soft); margin: 4px 2px; }

/* ─── Charts (charts-extra.tsx) ─── */
.cx-chart { position: relative; width: 100%; min-width: 0; }
.cx-chart svg { display: block; overflow: visible; }
.cx-legend { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; margin-top: 10px; }
.cx-legend span { display: inline-flex; align-items: center; gap: 6px; font-size: 0.74rem; color: var(--clefs-ink-muted); }
.cx-legend i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; flex-shrink: 0; }
.cx-legend i.cx-legend__line { height: 2px; width: 14px; border-radius: 1px; }
.cx-legend i.cx-legend__band { background: rgba(242,238,230,0.1); border: 1px dashed rgba(242,238,230,0.35); }
.cx-heat { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 3px; }
.cx-heat__dow { font-size: 0.66rem; color: var(--clefs-ink-faint); text-align: center; padding-bottom: 2px; letter-spacing: 0.04em; }
.cx-heat__dow[data-weekend="true"] { color: var(--clefs-gold-700); }
.cx-heat__cell {
  position: relative; aspect-ratio: 1 / 1; border-radius: 4px; border: 0; padding: 0; cursor: pointer;
  font: 500 0.66rem/1 var(--clefs-sans); color: rgba(242,238,230,0.72);
  display: flex; align-items: flex-start; justify-content: flex-end; padding: 3px 4px;
}
.cx-heat__cell[data-weekend="true"] { box-shadow: inset 0 0 0 1px rgba(225,175,104,0.28); }
.cx-heat__cell[data-empty="true"] {
  background-image: repeating-linear-gradient(135deg, rgba(255,255,255,0.05) 0 2px, transparent 2px 6px);
  cursor: default;
}
.cx-heat__cell[aria-pressed="true"] { outline: 2px solid var(--clefs-ink); outline-offset: 1px; }
.cx-heat__cell:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 1px; }
.cx-heat__pad { aspect-ratio: 1 / 1; }
.cx-heat__scale { display: flex; align-items: center; gap: 6px; margin-top: 10px; font-size: 0.72rem; color: var(--clefs-ink-faint); flex-wrap: wrap; }
.cx-heat__scale i { width: 16px; height: 10px; border-radius: 2px; display: inline-block; }
.cx-countdown { font-variant-numeric: tabular-nums; }

${chartStyles}
`;
