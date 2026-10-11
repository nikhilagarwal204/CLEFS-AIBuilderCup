/* ═══════════════════════════════════════════════════════════════════════
   Guest concierge language: resolution, lookup, direction and fonts.

   Resolution order (03 spec 2.6): ?lang > localStorage > profile >
   navigator.languages > en. There is no [lang] route segment because the
   QR URLs are already printed and mounted in rooms.

   "Explicit" means the guest (or the QR) chose: ?lang or a stored pick.
   Only an explicit language becomes a transcription hint, since a browser
   language says little about what the guest will actually speak.
   ═══════════════════════════════════════════════════════════════════════ */

import { baseLang, dirFor, translate, type Dict } from "../../lib/i18n";
import { DICTS, GUEST_LANGUAGES, isGuestLanguage, type GuestLanguage } from "../i18n";

export { GUEST_LANGUAGES, isGuestLanguage };
export type { GuestLanguage };

const LANG_KEY = "clefs.stay.lang";

// ─── Resolution ──────────────────────────────────────────────────────────

function norm(v: string | null | undefined): GuestLanguage | null {
  const b = baseLang(v || "");
  return isGuestLanguage(b) ? b : null;
}

export function readUrlLang(): GuestLanguage | null {
  if (typeof window === "undefined") return null;
  return norm(new URLSearchParams(window.location.search).get("lang"));
}

// Storage can throw (Safari private mode, blocked site data): never fatal.
export function readStoredLang(): GuestLanguage | null {
  try {
    return norm(window.localStorage.getItem(LANG_KEY));
  } catch {
    return null;
  }
}

export function storeLang(lang: GuestLanguage): void {
  try {
    window.localStorage.setItem(LANG_KEY, lang);
  } catch {
    /* per-viewer convenience only */
  }
}

export interface ResolvedLanguage {
  lang: GuestLanguage;
  /** Chosen by the URL or the guest, not guessed from the browser. */
  explicit: boolean;
}

export function resolveGuestLanguage(profile?: string | null): ResolvedLanguage {
  const url = readUrlLang();
  if (url) return { lang: url, explicit: true };
  const stored = typeof window !== "undefined" ? readStoredLang() : null;
  if (stored) return { lang: stored, explicit: true };
  const fromProfile = norm(profile);
  if (fromProfile) return { lang: fromProfile, explicit: false };
  if (typeof navigator !== "undefined") {
    for (const tag of navigator.languages || [navigator.language]) {
      const hit = norm(tag);
      if (hit) return { lang: hit, explicit: false };
    }
  }
  return { lang: "en", explicit: false };
}

// ─── Lookup ──────────────────────────────────────────────────────────────

export type TFn = (key: string, vars?: Record<string, string | number>) => string;

export function makeT(lang: GuestLanguage): TFn {
  const dict = DICTS[lang] as unknown as Dict;
  const fallback = DICTS.en as unknown as Dict;
  return (key, vars) => translate(dict, key, vars, fallback);
}

export function langDir(lang: string): "rtl" | "ltr" {
  return dirFor(lang);
}

/** The endonym, for the picker ("العربية", "Deutsch"). */
export function langName(lang: GuestLanguage): string {
  return DICTS[lang].meta.name;
}

// ─── Fonts ───────────────────────────────────────────────────────────────
// Inter and Playfair have no Arabic, Nastaliq, Devanagari, CJK, Hangul or
// Thai glyphs; without these the OS fallback renders mixed, often clipped,
// text. Japanese needs its own CJK font: the Simplified Chinese one draws
// many kanji with the Chinese glyph shapes.

const FONTS: Partial<Record<GuestLanguage, { family: string; css: string }>> = {
  ar: { family: "'Noto Sans Arabic'", css: "Noto+Sans+Arabic:wght@400;600;700" },
  ur: { family: "'Noto Nastaliq Urdu'", css: "Noto+Nastaliq+Urdu:wght@400;700" },
  hi: { family: "'Noto Sans Devanagari'", css: "Noto+Sans+Devanagari:wght@400;600;700" },
  zh: { family: "'Noto Sans SC'", css: "Noto+Sans+SC:wght@400;600;700" },
  ja: { family: "'Noto Sans JP'", css: "Noto+Sans+JP:wght@400;600;700" },
  ko: { family: "'Noto Sans KR'", css: "Noto+Sans+KR:wght@400;600;700" },
  th: { family: "'Noto Sans Thai'", css: "Noto+Sans+Thai:wght@400;600;700" },
};

/** Loads the language's webfont once (a <link> in <head>). Safe to call often. */
export function ensureLangFont(lang: GuestLanguage): void {
  const f = FONTS[lang];
  if (!f || typeof document === "undefined") return;
  const id = `cc-font-${lang}`;
  if (document.getElementById(id)) return;
  const link = document.createElement("link");
  link.id = id;
  link.rel = "stylesheet";
  link.href = `https://fonts.googleapis.com/css2?family=${f.css}&display=swap`;
  document.head.appendChild(link);
}

/** A font-family stack that puts the language's script font first. */
export function langFontStack(lang: GuestLanguage): string {
  const f = FONTS[lang];
  return f ? `${f.family}, 'Inter', sans-serif` : "'Inter', sans-serif";
}
