/* ═══════════════════════════════════════════════════════════════════════
   Service console CSS (prefix svc-). One string, injected once per surface
   through <SvcStyles/>. Tokens only (--clefs-*, --cx-*): no raw brand hex, so
   the pages follow the console theme. Mobile-first; min-width adds room.

   Service is a full-bleed nav item (StaffDashboard brings its own sidebar),
   so the new pages carry their own gutter in .svc-page.
   ═══════════════════════════════════════════════════════════════════════ */

export const svcStyles = `
.svc-page { padding: 16px 16px 96px; max-width: 1180px; margin: 0 auto; min-width: 0; }
@media (min-width: 900px) { .svc-page { padding: 24px 28px 64px; } }
.svc-subnav { margin: 0 0 16px; }
.svc-stack { display: flex; flex-direction: column; gap: 16px; }
.svc-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.svc-grid-2 { display: grid; grid-template-columns: 1fr; gap: 16px; }
@media (min-width: 900px) { .svc-grid-2 { grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); } }
.svc-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@media (min-width: 900px) { .svc-stats { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.svc-muted { color: var(--clefs-ink-muted); font-size: 0.85rem; }
.svc-faint { color: var(--clefs-ink-faint); font-size: 0.78rem; }
.svc-caption { color: var(--clefs-ink-faint); font-size: 0.75rem; font-style: italic; }
.svc-kbd { display: inline-block; min-width: 1.4em; padding: 0 5px; border-radius: 5px; border: 1px solid var(--clefs-line);
  font: 600 0.72rem/1.5 var(--clefs-sans); color: var(--clefs-ink-muted); text-align: center; background: var(--cx-field); }

/* ── Chips on request cards (StaffDashboard and the new pages) ── */
.svc-chips { display: inline-flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.svc-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px;
  font: 600 0.7rem/1.5 var(--clefs-sans); letter-spacing: 0.02em; border: 1px solid var(--clefs-line); color: var(--clefs-ink-muted);
  background: var(--cx-field); white-space: nowrap; }
.svc-chip--info { color: var(--cx-info); border-color: rgba(var(--cx-info-rgb), 0.35); background: rgba(var(--cx-info-rgb), 0.1); }
.svc-chip--gold { color: var(--clefs-gold-400); border-color: var(--clefs-line-gold); background: var(--clefs-gold-wash); }
.svc-chip--ok { color: var(--cx-ok); border-color: rgba(var(--cx-ok-rgb), 0.35); background: rgba(var(--cx-ok-rgb), 0.1); }
.svc-chip--warn { color: var(--cx-warn); border-color: rgba(var(--cx-warn-rgb), 0.35); background: rgba(var(--cx-warn-rgb), 0.1); }
.svc-chip--bad { color: var(--cx-bad); border-color: rgba(var(--cx-bad-rgb), 0.4); background: rgba(var(--cx-bad-rgb), 0.12); }
.svc-chip--pulse { animation: svc-pulse 1.6s ease-in-out infinite; }
@keyframes svc-pulse { 50% { opacity: 0.55; } }
@media (prefers-reduced-motion: reduce) { .svc-chip--pulse { animation: none; } }
.svc-linkbtn { background: none; border: 0; padding: 0; color: var(--clefs-gold-400); font: 600 0.72rem var(--clefs-sans);
  cursor: pointer; text-decoration: underline; text-underline-offset: 2px; }
.svc-navlink { text-decoration: none; }
.svc-linkbtn:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.svc-original { margin-top: 6px; padding: 8px 10px; border-radius: 8px; border: 1px dashed var(--clefs-line);
  color: var(--clefs-ink-muted); font-size: 0.82rem; white-space: pre-wrap; }
.svc-chip svg { font-size: 0.8rem; flex-shrink: 0; }
.svc-clock { font-variant-numeric: tabular-nums; }

/* ── Proof photo, rejected-photo alert, live link (05 spec) ── */
.svc-thumb { display: inline-flex; padding: 0; border-radius: 9px; overflow: hidden; flex-shrink: 0; cursor: pointer;
  border: 1px solid var(--clefs-line); background: var(--cx-field); line-height: 0; }
.svc-thumb img { object-fit: cover; display: block; }
.svc-thumb--ok { border-color: rgba(var(--cx-ok-rgb), 0.55); }
.svc-thumb--bad { border-color: rgba(var(--cx-bad-rgb), 0.7); }
.svc-thumb:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.svc-proof-alert { display: flex; gap: 8px; align-items: flex-start; margin-top: 8px; padding: 8px 10px; border-radius: 10px;
  border: 1px solid rgba(var(--cx-bad-rgb), 0.5); background: rgba(var(--cx-bad-rgb), 0.12); color: var(--clefs-ink);
  font-size: 0.8rem; line-height: 1.45; }
.svc-proof-alert svg { color: var(--cx-bad); flex-shrink: 0; margin-top: 2px; }
.svc-live { display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px; border-radius: 999px; white-space: nowrap;
  font: 600 0.74rem/1.4 var(--clefs-sans); letter-spacing: 0.02em; border: 1px solid var(--clefs-line); color: var(--clefs-ink-muted); }
.svc-live__dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.svc-live--ok { color: var(--cx-ok); border-color: rgba(var(--cx-ok-rgb), 0.4); background: rgba(var(--cx-ok-rgb), 0.08); }
.svc-live--ok .svc-live__dot { box-shadow: 0 0 0 0 rgba(var(--cx-ok-rgb), 0.6); animation: svc-live-ping 2s ease-out infinite; }
.svc-live--warn { color: var(--cx-warn); border-color: rgba(var(--cx-warn-rgb), 0.4); background: rgba(var(--cx-warn-rgb), 0.08); }
@keyframes svc-live-ping { 0% { box-shadow: 0 0 0 0 rgba(var(--cx-ok-rgb), 0.55); } 80%, 100% { box-shadow: 0 0 0 7px rgba(var(--cx-ok-rgb), 0); } }
@media (prefers-reduced-motion: reduce) { .svc-live--ok .svc-live__dot { animation: none; } }

/* ── Emergency banner (05 spec: Safety first) ── */
.svc-emerg { display: flex; flex-direction: column; gap: 8px; margin-bottom: 18px; }
.svc-emerg__row { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; padding: 12px 16px; border-radius: 14px;
  border: 1px solid rgba(var(--cx-bad-rgb), 0.6); background: rgba(var(--cx-bad-rgb), 0.14); color: var(--clefs-ink); }
.svc-emerg__row svg { color: var(--cx-bad); font-size: 1.1rem; flex-shrink: 0; }
.svc-emerg__title { font-weight: 700; letter-spacing: 0.02em; color: #FF8A8D; }
.svc-emerg__text { flex: 1 1 220px; min-width: 0; font-size: 0.86rem; color: var(--clefs-ink-muted); overflow-wrap: anywhere; }

/* ── Needs your approval (05 spec 2.12) ── */
.svc-appr { padding: 18px 20px; margin-bottom: 22px; border-radius: 16px; border: 1px solid var(--clefs-line-gold);
  background: linear-gradient(180deg, rgba(225,175,104,0.07), rgba(255,255,255,0.02)); }
.svc-appr__head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: space-between; }
.svc-appr__title { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 1rem; color: var(--clefs-ink); margin: 0; }
.svc-appr__count { min-width: 22px; height: 22px; padding: 0 7px; border-radius: 999px; display: inline-grid; place-items: center;
  background: var(--clefs-gold-500); color: var(--clefs-ink-dark); font: 700 0.74rem/1 var(--clefs-sans); }
.svc-appr__list { list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.svc-appr__item { padding: 14px; border-radius: 12px; border: 1px solid var(--clefs-line-soft); background: rgba(3, 6, 12, 0.35);
  display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.svc-appr__top { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; }
.svc-appr__room { font-weight: 700; font-size: 0.98rem; }
.svc-appr__summary { font-size: 0.85rem; color: var(--clefs-ink-muted); overflow-wrap: anywhere; }
.svc-appr__gesture { display: flex; gap: 8px; align-items: flex-start; font-size: 0.9rem; color: var(--clefs-ink); overflow-wrap: anywhere; }
.svc-appr__gesture svg { color: var(--clefs-gold-400); flex-shrink: 0; margin-top: 3px; }
.svc-appr__label { display: block; font-size: 0.68rem; letter-spacing: 0.1em; text-transform: uppercase; color: var(--clefs-ink-faint); margin-bottom: 2px; }
.svc-appr__edit { width: 100%; min-height: 64px; padding: 8px 10px; border-radius: 9px; resize: vertical; font: 500 0.88rem/1.45 var(--clefs-sans);
  background: var(--cx-field); color: var(--clefs-ink); border: 1px solid var(--cx-field-line); }
.svc-appr__edit:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 1px; }
.svc-appr__actions { display: flex; flex-wrap: wrap; gap: 8px; }
.svc-appr__empty { margin: 8px 0 0; font-size: 0.84rem; color: var(--clefs-ink-faint); line-height: 1.5; }

/* ── Request detail drawer ── */
.svc-detail { display: flex; flex-direction: column; gap: 18px; }
.svc-detail__facts { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 8px 16px; margin: 0; font-size: 0.86rem; }
.svc-detail__facts dt { color: var(--clefs-ink-faint); }
.svc-detail__facts dd { margin: 0; color: var(--clefs-ink); overflow-wrap: anywhere; }
.svc-detail__photo { display: block; width: 100%; max-height: 360px; object-fit: contain; border-radius: 12px;
  border: 1px solid var(--clefs-line); background: rgba(0,0,0,0.25); }
.svc-detail__h { font-size: 0.7rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--clefs-ink-faint); margin: 0 0 8px; }
.svc-timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.svc-timeline li { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 10px; font-size: 0.84rem; color: var(--clefs-ink-muted); }
.svc-timeline time { color: var(--clefs-ink-faint); font-variant-numeric: tabular-nums; }
.svc-assign { display: inline-flex; gap: 6px; align-items: center; }
.svc-assign select { max-width: 150px; padding: 6px 8px; border-radius: 8px; font: 500 0.8rem var(--clefs-sans);
  background: var(--cx-field); color: var(--clefs-ink); border: 1px solid var(--cx-field-line); }
.svc-assign select:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 1px; }

/* ── Phone entry console ── */
.svc-pe { display: flex; flex-direction: column; gap: 14px; }
.svc-pe-room { font: 600 1.6rem/1.2 var(--clefs-sans); letter-spacing: 0.02em; }
.svc-pe-suggest { list-style: none; margin: 6px 0 0; padding: 4px; border-radius: 10px; border: 1px solid var(--clefs-line);
  background: var(--cx-panel); max-height: 220px; overflow: auto; }
.svc-pe-suggest li { padding: 8px 10px; border-radius: 8px; cursor: pointer; display: flex; gap: 8px; align-items: center; }
.svc-pe-suggest li[aria-selected="true"], .svc-pe-suggest li:hover { background: var(--clefs-gold-wash); }
.svc-pe-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.svc-pe-chip { display: inline-flex; gap: 8px; align-items: center; padding: 8px 12px; border-radius: 10px; cursor: pointer;
  border: 1px solid var(--clefs-line); background: var(--cx-field); color: var(--clefs-ink); font: 600 0.85rem var(--clefs-sans); }
.svc-pe-chip[aria-pressed="true"] { border-color: var(--clefs-gold-400); background: var(--clefs-gold-wash); color: var(--clefs-gold-300); }
.svc-pe-chip:focus-visible { outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.svc-pe-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
@media (min-width: 700px) { .svc-pe-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.svc-pe-item { display: flex; flex-direction: column; gap: 6px; padding: 10px; border-radius: 10px; border: 1px solid var(--clefs-line);
  background: var(--cx-field); text-align: left; color: var(--clefs-ink); cursor: pointer; font: 500 0.85rem var(--clefs-sans); }
.svc-pe-item[aria-pressed="true"] { border-color: var(--clefs-gold-400); background: var(--clefs-gold-wash); }
.svc-pe-item .svc-kbd { margin-inline-end: 6px; }
.svc-pe-qty { display: inline-flex; align-items: center; gap: 6px; }
.svc-pe-qty button { width: 26px; height: 26px; border-radius: 7px; border: 1px solid var(--clefs-line); background: transparent;
  color: var(--clefs-ink); cursor: pointer; }
.svc-dup { padding: 12px 14px; border-radius: 12px; border: 1px solid rgba(var(--cx-warn-rgb), 0.45);
  background: rgba(var(--cx-warn-rgb), 0.1); color: var(--clefs-ink); display: flex; flex-direction: column; gap: 8px; }
.svc-openjob { display: flex; gap: 8px; align-items: center; justify-content: space-between; padding: 8px 0;
  border-bottom: 1px solid var(--clefs-line-soft); }
.svc-openjob:last-child { border-bottom: 0; }

/* ── Tables (config, roster) ── */
.svc-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.svc-table th { text-align: left; font-weight: 600; color: var(--clefs-ink-muted); padding: 8px 6px; border-bottom: 1px solid var(--clefs-line); }
.svc-table td { padding: 6px; border-bottom: 1px solid var(--clefs-line-soft); vertical-align: middle; }
.svc-table input, .svc-table select { width: 100%; min-width: 60px; padding: 6px 8px; border-radius: 8px; font: 500 0.82rem var(--clefs-sans);
  background: var(--cx-field); color: var(--clefs-ink); border: 1px solid var(--cx-field-line); }
.svc-table input[type="checkbox"] { width: auto; min-width: 0; }
.svc-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.svc-roster td { min-width: 110px; }
.svc-shift { display: flex; flex-direction: column; gap: 2px; padding: 6px 8px; border-radius: 8px; margin-bottom: 4px;
  border: 1px solid var(--clefs-line); background: var(--cx-field); font-size: 0.75rem; }
.svc-shift--warn { border-color: rgba(var(--cx-warn-rgb), 0.5); }
.svc-shift--bad { border-color: rgba(var(--cx-bad-rgb), 0.6); }
.svc-flag { font-size: 0.72rem; color: var(--cx-warn); }
.svc-flag--bad { color: var(--cx-bad); }
`;
