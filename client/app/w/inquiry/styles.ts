/* ═══════════════════════════════════════════════════════════════════════
   Inquiry widget stylesheet (prefix inq-w). It renders inside an iframe
   on the hotel's own website, so it cannot inherit the site's styles and
   must not look like Clefs: a neutral light card by default, `theme=dark`
   for dark sites, and the hotel's accent colour (validated hex) on the
   button and focus rings. The page background is transparent so the card
   sits on the host page. Logical properties mirror Arabic without a second
   stylesheet; inputs are 16 px so iOS does not zoom into the iframe.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Accent contrast ─────────────────────────────────────────────────────

/* The default accent: a darker gold than the first cut (#9a7b2f was 4.0:1
   under white 16 px text, below AA). #86692a gives 5.2:1 with white, and
   stays legible as a focus ring on the dark card. */
export const DEFAULT_ACCENT = "#86692a";

function channel(v: number): number {
  const c = v / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/** WCAG relative luminance of a #rrggbb colour (null when not that shape). */
export function luminance(hex: string): number | null {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return null;
  const [r, g, b] = m.slice(1).map((x) => channel(parseInt(x, 16)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio of two luminances. */
export function contrast(a: number, b: number): number {
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

/* The Send button's label colour for the hotel's accent. WHY computed: the
   accent comes from the hotel's site (?accent=), and a fixed white or #111
   label fails badly on a light gold (#e1af68: 2:1) or a navy on a dark
   theme (1.8:1). White while it reaches 4.5:1 (AA for 16 px text), else
   #111, else black for the narrow mid band where #111 just misses. */
export function accentInk(hex: string): string {
  const l = luminance(hex);
  if (l === null) return "#ffffff";
  if (contrast(l, 1) >= 4.5) return "#ffffff";
  const l111 = luminance("#111111") as number;
  return contrast(l, l111) >= 4.5 ? "#111111" : "#000000";
}

// ─── Stylesheet ──────────────────────────────────────────────────────────

export const wStyles = `
html, body { background: transparent !important; }
.inq-w { --w-bg: #ffffff; --w-ink: #1b1f24; --w-muted: #5b6470; --w-line: #d6dbe1; --w-field: #ffffff;
  --w-accent: ${DEFAULT_ACCENT}; --w-accent-ink: #ffffff; --w-error: #b42318; --w-ok: #1f7a4d;
  color: var(--w-ink); font-family: system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif; font-size: 15px;
  line-height: 1.5; padding: 2px; box-sizing: border-box; }
.inq-w[data-theme="dark"] { --w-bg: #141a22; --w-ink: #eef1f4; --w-muted: #a4adb8; --w-line: #333d49; --w-field: #1b232d;
  --w-error: #ff8a80; --w-ok: #7fd6a8; }
.inq-w[dir="rtl"] { font-family: 'Noto Kufi Arabic', 'Segoe UI', Tahoma, system-ui, sans-serif; }
.inq-w *, .inq-w *::before, .inq-w *::after { box-sizing: border-box; }
.inq-w__card { background: var(--w-bg); border: 1px solid var(--w-line); border-radius: 14px; padding: 18px 16px;
  display: flex; flex-direction: column; gap: 14px; max-width: 640px; margin: 0 auto; }
.inq-w__top { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.inq-w h1 { font-size: 19px; line-height: 1.3; margin: 0; font-weight: 650; }
.inq-w p { margin: 0; }
.inq-w__muted { color: var(--w-muted); font-size: 14px; }
.inq-w__small { color: var(--w-muted); font-size: 12.5px; }
.inq-w__lang { background: none; border: 1px solid var(--w-line); color: var(--w-ink); border-radius: 999px; padding: 6px 12px;
  min-height: 36px; font: inherit; font-size: 13px; cursor: pointer; flex: none; }
.inq-w__grid { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.inq-w__grid--3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.inq-w__full { grid-column: 1 / -1; }
@media (max-width: 420px) { .inq-w__grid { grid-template-columns: 1fr; } .inq-w__grid--3 { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.inq-w__field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.inq-w__label { font-size: 13.5px; font-weight: 550; }
.inq-w input[type="text"], .inq-w input[type="email"], .inq-w input[type="tel"], .inq-w input[type="date"],
.inq-w input[type="number"], .inq-w textarea {
  width: 100%; font: inherit; font-size: 16px; color: var(--w-ink); background: var(--w-field); border: 1px solid var(--w-line);
  border-radius: 10px; padding: 10px 11px; min-height: 44px; }
.inq-w textarea { resize: vertical; min-height: 84px; }
.inq-w input[type="tel"], .inq-w input[type="email"] { direction: ltr; text-align: start; }
.inq-w[dir="rtl"] input[type="tel"], .inq-w[dir="rtl"] input[type="email"] { text-align: right; }
.inq-w input:focus-visible, .inq-w textarea:focus-visible, .inq-w button:focus-visible, .inq-w a:focus-visible {
  outline: 2px solid var(--w-accent); outline-offset: 2px; }
.inq-w input[aria-invalid="true"] { border-color: var(--w-error); }
.inq-w__consents { display: flex; flex-direction: column; gap: 10px; border: 0; padding: 0; margin: 0; }
.inq-w__consents legend { font-size: 13.5px; font-weight: 550; margin-bottom: 8px; padding: 0; }
.inq-w__check { display: flex; gap: 10px; align-items: flex-start; font-size: 13.5px; line-height: 1.5; cursor: pointer; }
.inq-w__check input { width: 18px; height: 18px; margin-top: 2px; flex: none; accent-color: var(--w-accent); }
.inq-w__tag { display: inline-block; font-size: 11.5px; color: var(--w-muted); margin-inline-start: 6px; }
.inq-w__tag--req { color: var(--w-ink); font-weight: 600; text-decoration: underline; text-decoration-color: var(--w-accent);
  text-decoration-thickness: 2px; text-underline-offset: 3px; }
.inq-w__btn { background: var(--w-accent); color: var(--w-accent-ink); border: 0; border-radius: 10px; font: inherit; font-weight: 650;
  font-size: 16px; min-height: 48px; padding: 12px 18px; cursor: pointer; width: 100%; }
.inq-w__btn[disabled] { opacity: 0.6; cursor: default; }
.inq-w__btn--ghost { background: none; color: var(--w-ink); border: 1px solid var(--w-line); }
.inq-w__error { color: var(--w-error); font-size: 14px; }
.inq-w__ok { color: var(--w-ok); font-size: 30px; line-height: 1; }
.inq-w__hp { position: absolute !important; left: -10000px !important; top: auto; width: 1px; height: 1px; overflow: hidden; }
.inq-w a { color: inherit; }
`;
