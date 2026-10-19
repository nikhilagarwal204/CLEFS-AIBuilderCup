/* ═══════════════════════════════════════════════════════════════════════
   One stylesheet for the concierge's new guest components (cc- prefix).
   Injected once by ClefsConcierge next to its existing injectStyles, so
   the avatar CSS stays untouched. Logical properties (inset-inline,
   padding-inline, text-align: start) make each block mirror correctly
   when its container carries dir="rtl"; the root never does.
   ═══════════════════════════════════════════════════════════════════════ */

export const CC_STYLES = `
.cc-icon-btn{background:rgba(255,255,255,0.05);border:1px solid rgba(225,175,104,0.1);border-radius:8px;min-width:36px;height:36px;padding:0 8px;cursor:pointer;color:rgba(225,175,104,0.55);display:inline-flex;align-items:center;justify-content:center;gap:4px;font:600 11px var(--clefs-sans);letter-spacing:.5px;-webkit-tap-highlight-color:transparent}
.cc-icon-btn:hover,.cc-icon-btn:focus-visible{color:var(--hc-gold);border-color:rgba(225,175,104,0.3);outline:none}
.cc-pill{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:999px;background:rgba(225,175,104,0.1);border:1px solid rgba(225,175,104,0.25);color:var(--clefs-gold-400);font:600 11px var(--clefs-sans);letter-spacing:.5px;cursor:pointer}
.cc-pill-dot{width:6px;height:6px;border-radius:50%;background:var(--clefs-gold-400);box-shadow:0 0 8px rgba(242,198,125,.8)}
.cc-banner{max-width:480px;width:calc(100% - 16px);padding:8px 14px;border-radius:12px;background:rgba(225,175,104,0.07);border:1px solid rgba(225,175,104,0.16);color:var(--clefs-ink-muted);font:400 12px/1.5 var(--clefs-sans);text-align:center}
.cc-backdrop{position:absolute;inset:0;z-index:300;background:rgba(3,6,10,0.72);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);display:flex;align-items:flex-end;justify-content:center;animation:fadeIn .25s ease}
.cc-backdrop-page{position:fixed}
.cc-sheet{width:100%;max-width:520px;max-height:88dvh;overflow-y:auto;background:linear-gradient(180deg,var(--clefs-slate-850),var(--clefs-slate-900));border:1px solid var(--clefs-line-gold);border-bottom:none;border-radius:20px 20px 0 0;padding:22px 20px max(20px,env(safe-area-inset-bottom));color:var(--clefs-ink);box-shadow:var(--clefs-shadow-2);animation:fadeUp .3s ease;text-align:start}
.cc-sheet h2{margin:0 0 6px;font:600 20px var(--clefs-serif);color:var(--hc-gold);letter-spacing:.5px}
.cc-sheet p{margin:0 0 14px;font:400 13px/1.6 var(--clefs-sans);color:var(--clefs-ink-muted)}
.cc-purpose{display:flex;gap:12px;align-items:flex-start;padding:12px 0;border-top:1px solid rgba(225,175,104,0.1)}
.cc-purpose input{margin-top:3px;width:18px;height:18px;accent-color:#E1AF68;flex-shrink:0}
.cc-purpose-text{font:400 13px/1.55 var(--clefs-sans);color:var(--clefs-ink)}
.cc-tag{display:inline-block;margin-inline-start:6px;padding:1px 7px;border-radius:999px;font:600 10px var(--clefs-sans);letter-spacing:.4px;vertical-align:middle;border:1px solid rgba(225,175,104,0.25);color:var(--clefs-gold-400)}
.cc-tag-opt{color:var(--clefs-ink-faint);border-color:rgba(242,238,230,0.15)}
.cc-note{font:400 12px/1.5 var(--clefs-sans);color:var(--clefs-ink-faint);margin:6px 0 0}
.cc-actions{display:flex;flex-direction:column;gap:8px;margin-top:16px}
.cc-btn{border-radius:999px;padding:13px 18px;font:700 13px var(--clefs-sans);letter-spacing:1px;cursor:pointer;border:1px solid rgba(225,175,104,0.3);background:transparent;color:var(--hc-gold);-webkit-tap-highlight-color:transparent}
.cc-btn:disabled{opacity:.4;cursor:default}
.cc-btn-primary{background:linear-gradient(135deg,#E1AF68 0%,#F2C67D 50%,#E1AF68 100%);color:var(--hc-slate);border-color:rgba(231,211,163,0.3)}
.cc-btn-quiet{border-color:transparent;color:var(--clefs-ink-faint);font-weight:500}
.cc-lang-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:8px}
.cc-lang{padding:12px;border-radius:12px;border:1px solid rgba(225,175,104,0.14);background:rgba(255,255,255,0.03);color:var(--clefs-ink);font:500 15px var(--clefs-sans);cursor:pointer;text-align:center}
.cc-lang[aria-pressed="true"]{border-color:var(--hc-gold);background:rgba(225,175,104,0.12);color:var(--hc-gold)}
.cc-tracker{width:calc(100% - 16px);max-width:480px;display:flex;flex-direction:column;gap:8px;text-align:start}
.cc-track{padding:10px 12px;border-radius:12px;background:rgba(12,27,21,0.55);border:1px solid rgba(225,175,104,0.14)}
.cc-track-head{display:flex;justify-content:space-between;gap:8px;font:600 12px var(--clefs-sans);color:var(--clefs-ink)}
.cc-track-state{color:var(--clefs-gold-400);font-weight:600}
.cc-steps{display:flex;gap:4px;margin-top:8px}
.cc-step{flex:1;height:3px;border-radius:2px;background:rgba(242,238,230,0.12)}
.cc-step-on{background:var(--hc-gold)}
.cc-track-q{display:flex;align-items:center;gap:8px;margin-top:8px;font:400 12px var(--clefs-sans);color:var(--clefs-ink-muted)}
.cc-mini{padding:4px 12px;border-radius:999px;border:1px solid rgba(225,175,104,0.3);background:transparent;color:var(--hc-gold);font:600 11px var(--clefs-sans);cursor:pointer}
.cc-link{color:var(--clefs-gold-400);font:500 12px var(--clefs-sans);text-decoration:underline;text-underline-offset:3px}
.cc-mini-alert{border-color:rgba(255,180,94,0.45);color:#FFC77A}
.cc-mini:disabled{opacity:.45;cursor:default}
.cc-job{padding:13px 15px;border-radius:14px;background:rgba(11,18,27,0.82);border:1px solid rgba(225,175,104,0.18);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);box-shadow:0 10px 30px rgba(0,0,0,0.35);text-align:start;animation:fadeUp .3s ease}
.cc-job-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:11px;font:700 10px var(--clefs-sans);letter-spacing:.14em;text-transform:uppercase;color:var(--clefs-gold-500)}
.cc-job-pill{flex-shrink:0;padding:3px 8px;border-radius:4px;letter-spacing:.08em;background:rgba(52,199,123,0.12);color:var(--hc-success);border:1px solid rgba(52,199,123,0.22)}
.cc-job-pill-on_the_way,.cc-job-pill-confirm{background:rgba(225,175,104,0.12);color:var(--hc-gold);border-color:rgba(225,175,104,0.3)}
.cc-job-pill-reopened{background:rgba(255,180,94,0.12);color:#FFC77A;border-color:rgba(255,180,94,0.3)}
.cc-job-pill-cancelled{background:rgba(242,238,230,0.06);color:var(--clefs-ink-faint);border-color:rgba(242,238,230,0.15)}
.cc-job-rows{margin:0;display:flex;flex-direction:column;gap:7px}
.cc-job-row{display:flex;justify-content:space-between;gap:12px;font:400 12px/1.4 var(--clefs-sans)}
.cc-job-row dt{color:var(--clefs-ink-faint);margin:0;flex-shrink:0}
.cc-job-row dd{color:var(--clefs-ink);margin:0;font-weight:500;text-align:end;min-width:0;overflow-wrap:anywhere}
.cc-job-time{font-variant-numeric:tabular-nums}
.cc-job-msg{margin:9px 0 0;padding:8px 10px;border-radius:10px;background:rgba(225,175,104,0.07);border:1px solid rgba(225,175,104,0.12);font:400 12px/1.5 var(--clefs-sans);color:var(--clefs-ink)}
.cc-job-msg b{color:var(--clefs-gold-400);font-weight:600;margin-inline-end:6px}
.cc-job-note{margin:8px 0 0;font:400 12px/1.5 var(--clefs-sans);color:var(--clefs-ink-muted)}
.cc-job-q{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:11px;padding-top:10px;border-top:1px solid rgba(225,175,104,0.12);font:500 12px var(--clefs-sans);color:var(--clefs-ink)}
.cc-job-q span{margin-inline-end:auto}
.cc-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.cc-said{display:flex;justify-content:flex-end;margin-bottom:8px;animation:fadeIn .3s ease}
.cc-said p{max-width:85%;margin:0;padding:8px 13px;border-radius:14px 14px 4px 14px;background:rgba(242,238,230,0.08);border:1px solid rgba(242,238,230,0.1);color:var(--clefs-ink);font:400 13px/1.5 var(--clefs-sans);text-align:start;overflow-wrap:anywhere}
[dir="rtl"] .cc-said p{border-radius:14px 14px 14px 4px}
.cc-emergency{width:calc(100% - 16px);max-width:480px;padding:14px 16px;border-radius:14px;background:rgba(120,20,20,0.35);border:1px solid rgba(255,107,107,0.55);color:#FFE3E3;text-align:start;animation:fadeUp .3s ease;box-shadow:0 0 30px rgba(255,80,80,0.18)}
.cc-emergency h3{margin:0 0 4px;font:700 15px var(--clefs-sans);letter-spacing:.3px}
.cc-emergency p{margin:0 0 10px;font:400 12px/1.5 var(--clefs-sans);color:rgba(255,227,227,0.85)}
.cc-calls{display:flex;flex-wrap:wrap;gap:8px}
.cc-call{display:inline-flex;flex-direction:column;align-items:flex-start;padding:8px 12px;border-radius:10px;background:#B3261E;color:#fff;text-decoration:none;font:700 15px var(--clefs-sans)}
.cc-call small{font:500 10px var(--clefs-sans);opacity:.85}
.cc-call-alt{background:rgba(255,255,255,0.08);border:1px solid rgba(255,227,227,0.25)}
`;
