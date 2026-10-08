/* ═══════════════════════════════════════════════════════════════════════
   Guest journey page stylesheet (prefix gst-g-). Phone-first: most guests
   open these from WhatsApp. Uses --clefs-* tokens from globals.css; logical
   properties (inline-start/end) so the Arabic layout mirrors without a
   second stylesheet. Tap targets are at least 44 px.
   ═══════════════════════════════════════════════════════════════════════ */

export const gPageStyles = `
.gst-g { min-height: 100vh; min-height: 100dvh; background: var(--clefs-slate-900); color: var(--clefs-ink);
  font-family: var(--clefs-sans); padding: 24px 16px 48px; box-sizing: border-box; }
.gst-g[dir="rtl"] { font-family: 'Noto Kufi Arabic', 'Segoe UI', Tahoma, var(--clefs-sans); }
.gst-g *, .gst-g *::before, .gst-g *::after { box-sizing: border-box; }
.gst-g__wrap { max-width: 520px; margin: 0 auto; display: flex; flex-direction: column; gap: 18px; }
.gst-g__top { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.gst-g__hotel { font-family: var(--clefs-serif); font-size: 20px; color: var(--clefs-gold-300); margin: 0; }
.gst-g__lang { background: none; border: 1px solid var(--clefs-line); color: var(--clefs-ink); border-radius: 999px;
  padding: 8px 14px; min-height: 40px; font: inherit; font-size: 14px; cursor: pointer; }
.gst-g__card { background: var(--clefs-surface, rgba(255,255,255,0.04)); border: 1px solid var(--clefs-line-soft);
  border-radius: 16px; padding: 18px 16px; display: flex; flex-direction: column; gap: 14px; }
.gst-g h1 { font-size: 22px; line-height: 1.3; margin: 0; font-weight: 600; }
.gst-g h2 { font-size: 16px; margin: 0; font-weight: 600; }
.gst-g p { margin: 0; line-height: 1.55; }
.gst-g__muted { color: var(--clefs-ink-muted); font-size: 14px; }
.gst-g__field { display: flex; flex-direction: column; gap: 6px; }
.gst-g__label { font-size: 14px; font-weight: 500; }
.gst-g__hint { font-size: 12.5px; color: var(--clefs-ink-muted); }
.gst-g input[type="text"], .gst-g input[type="tel"], .gst-g input[type="email"], .gst-g input[type="time"], .gst-g textarea {
  width: 100%; font: inherit; font-size: 16px; color: var(--clefs-ink); background: rgba(255,255,255,0.05);
  border: 1px solid var(--clefs-line); border-radius: 10px; padding: 11px 12px; min-height: 44px; }
.gst-g textarea { resize: vertical; min-height: 88px; }
.gst-g input:focus-visible, .gst-g textarea:focus-visible, .gst-g button:focus-visible, .gst-g a:focus-visible {
  outline: 2px solid var(--clefs-gold-400); outline-offset: 2px; }
.gst-g input[type="tel"], .gst-g input[type="email"] { direction: ltr; text-align: start; }
.gst-g[dir="rtl"] input[type="tel"], .gst-g[dir="rtl"] input[type="email"] { text-align: right; }
.gst-g__seg { display: inline-flex; border: 1px solid var(--clefs-line); border-radius: 999px; overflow: hidden; align-self: flex-start; }
.gst-g__seg button { background: none; border: 0; color: var(--clefs-ink-muted); font: inherit; font-size: 14px;
  padding: 8px 16px; min-height: 40px; cursor: pointer; }
.gst-g__seg button[aria-pressed="true"] { background: var(--clefs-gold-wash, rgba(212,175,55,0.15)); color: var(--clefs-gold-200); }
.gst-g__check { display: flex; gap: 12px; align-items: flex-start; font-size: 14px; line-height: 1.5; cursor: pointer; }
.gst-g__check input { width: 20px; height: 20px; margin-top: 2px; flex: none; accent-color: var(--clefs-gold-400); }
.gst-g__req { color: var(--clefs-gold-300); font-size: 12.5px; display: block; margin-top: 2px; }
.gst-g__btn { background: var(--clefs-gold-400); color: var(--clefs-slate-950); border: 0; border-radius: 12px; font: inherit;
  font-weight: 600; font-size: 16px; min-height: 48px; padding: 12px 18px; cursor: pointer; width: 100%; }
.gst-g__btn[disabled] { opacity: 0.55; cursor: default; }
.gst-g__btn--ghost { background: none; color: var(--clefs-ink); border: 1px solid var(--clefs-line); }
.gst-g__error { color: #ff8a8a; font-size: 14px; }
.gst-g__ok { color: var(--clefs-gold-200); }
.gst-g__stars { display: flex; gap: 8px; flex-wrap: wrap; }
.gst-g__star { min-width: 48px; min-height: 48px; border-radius: 12px; border: 1px solid var(--clefs-line); background: none;
  color: var(--clefs-ink); font: inherit; font-size: 18px; cursor: pointer; }
.gst-g__star[aria-checked="true"] { background: var(--clefs-gold-400); color: var(--clefs-slate-950); border-color: transparent; }
.gst-g__reviews { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 14px; color: var(--clefs-ink-muted); }
.gst-g__reviews a { color: var(--clefs-gold-200); text-decoration: underline; text-underline-offset: 3px; padding: 6px 2px;
  min-height: 32px; display: inline-flex; align-items: center; }
.gst-g__sep { color: var(--clefs-ink-faint); }
.gst-g__foot { font-size: 12px; color: var(--clefs-ink-faint); text-align: center; }
`;
