/* ═══════════════════════════════════════════════════════════════════════
   Guest dictionaries, statically bundled.

   All eleven ship in the page bundle on purpose: together they are a few
   KB gzipped, and a guest who switches language on a hotel's captive
   Wi-Fi must never wait on a lazy chunk. The list order is the order the
   LanguagePicker shows (after the hotel's own languages).
   ═══════════════════════════════════════════════════════════════════════ */

import en, { type GuestDict } from "./en";
import ar from "./ar";
import ru from "./ru";
import hi from "./hi";
import ur from "./ur";
import zh from "./zh";
import de from "./de";
import fr from "./fr";
import ja from "./ja";
import ko from "./ko";
import th from "./th";

export type { GuestDict };

/** The guest UI languages. Mirrors the server's concierge GUEST_LANGUAGES
    (and scripts/check-i18n.mjs, which checks each one against en.ts). */
export const GUEST_LANGUAGES = ["en", "ar", "ru", "hi", "ur", "zh", "de", "fr", "ja", "ko", "th"] as const;
export type GuestLanguage = (typeof GUEST_LANGUAGES)[number];

export const DICTS: Record<GuestLanguage, GuestDict> = { en, ar, ru, hi, ur, zh, de, fr, ja, ko, th };

export function isGuestLanguage(v: string | null | undefined): v is GuestLanguage {
  return !!v && (GUEST_LANGUAGES as readonly string[]).includes(v);
}
