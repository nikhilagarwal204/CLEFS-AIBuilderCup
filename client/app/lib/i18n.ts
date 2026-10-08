/* ═══════════════════════════════════════════════════════════════════════
   Tiny i18n helpers. The console is English-only for now; guest-facing
   dictionaries live in app/stay/i18n/* (owned by the Concierge lane) and use
   these helpers so RTL and fallback behave the same everywhere.
   ═══════════════════════════════════════════════════════════════════════ */

/** A dictionary: nested objects of strings, addressed as "a.b.c". */
export interface Dict {
  [key: string]: string | Dict;
}

/** Languages written right-to-left. Arabic and Urdu matter most for UAE guests. */
export const RTL_LOCALES: readonly string[] = ["ar", "fa", "he", "ur", "ps", "ku", "ckb", "dv", "sd", "ug", "yi"] as const;

function lookup(dict: Dict | undefined, key: string): string | undefined {
  if (!dict) return undefined;
  if (typeof dict[key] === "string") return dict[key] as string;
  let node: string | Dict | undefined = dict;
  for (const part of key.split(".")) {
    if (!node || typeof node === "string") return undefined;
    node = node[part];
  }
  return typeof node === "string" ? node : undefined;
}

/**
 * Look up `key` and interpolate {name} placeholders. Missing keys fall back
 * to `fallback` (typically the English dictionary), then to the key itself so
 * a gap is visible in QA instead of rendering an empty button.
 */
export function translate(
  dict: Dict | undefined,
  key: string,
  vars?: Record<string, string | number>,
  fallback?: Dict,
): string {
  const raw = lookup(dict, key) ?? lookup(fallback, key) ?? key;
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (m, name: string) => (name in vars ? String(vars[name]) : m));
}

/** Primary language subtag, lower-cased: "ar-AE" -> "ar". */
export function baseLang(locale: string): string {
  return (locale || "").toLowerCase().split(/[-_]/)[0];
}

export function dirFor(locale: string | null | undefined): "rtl" | "ltr" {
  return locale && RTL_LOCALES.includes(baseLang(locale)) ? "rtl" : "ltr";
}

/**
 * Choose the best supported locale for a visitor.
 * `accepted` is an Accept-Language header or navigator.languages; quality
 * values are honoured. Exact matches win, then a base-language match
 * ("ar-SA" -> "ar"), then `fallback` (default: the first supported locale).
 */
export function pickLocale(
  accepted: string | readonly string[] | null | undefined,
  supported: readonly string[],
  fallback: string = supported[0] ?? "en",
): string {
  if (!supported.length) return fallback;
  const wanted: string[] =
    typeof accepted === "string"
      ? accepted
          .split(",")
          .map((part, i) => {
            const [tag, ...params] = part.trim().split(";");
            const q = params.map(p => p.trim()).find(p => p.startsWith("q="));
            return { tag: tag.trim(), q: q ? Number(q.slice(2)) || 0 : 1, i };
          })
          .filter(x => x.tag && x.tag !== "*" && x.q > 0)
          .sort((a, b) => b.q - a.q || a.i - b.i)
          .map(x => x.tag)
      : [...(accepted ?? [])];

  const lowerSupported = supported.map(s => s.toLowerCase());
  for (const tag of wanted) {
    const exact = lowerSupported.indexOf(tag.toLowerCase());
    if (exact >= 0) return supported[exact];
  }
  for (const tag of wanted) {
    const base = baseLang(tag);
    const idx = lowerSupported.findIndex(s => baseLang(s) === base);
    if (idx >= 0) return supported[idx];
  }
  return fallback;
}
