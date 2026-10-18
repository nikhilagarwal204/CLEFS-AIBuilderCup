/* ═══════════════════════════════════════════════════════════════════════
   Links to a hotel's guest privacy notice and rights form (03 spec 5.3).

   Owned by Trust, imported by the guest surfaces (the concierge ConsentSheet
   and header Privacy panel, the request StatusTracker, the /g journey pages)
   so every surface builds the same URL and says it in the guest's language.
   Pure data and string helpers: no React, safe in server and client code.

   The notice itself is written in English and Arabic (PrivacyLang); a guest
   reading in another language gets the English notice, but the link text is
   in their language so they can find it at all.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── URLs ────────────────────────────────────────────────────────────────────

/** The two languages the notice and the form are written in. */
const NOTICE_LANGS = new Set(["en", "ar"]);

function langParam(lang: string | null | undefined): string {
  const base = (lang || "en").toLowerCase().split(/[-_]/)[0];
  return NOTICE_LANGS.has(base) ? base : "en";
}

/** /privacy/<slug>?lang=..: the per-hotel notice. Also reachable at the spec
 *  path /stay/<slug>/privacy, which redirects here. */
export function privacyNoticeHref(hotelSlug: string, lang?: string | null): string {
  return `/privacy/${encodeURIComponent(hotelSlug)}?lang=${langParam(lang)}`;
}

/** /privacy/request?hotel=..: the public rights form (access, erase, ...). */
export function privacyRequestHref(hotelSlug: string, lang?: string | null): string {
  return `/privacy/request?hotel=${encodeURIComponent(hotelSlug)}&lang=${langParam(lang)}`;
}

// ─── Link text ───────────────────────────────────────────────────────────────
// The eight guest languages of the concierge (stay/i18n). Unknown codes fall
// back to English.

type LinkCopy = { notice: string; rights: string; footer: string };

export const PRIVACY_LINK_COPY: Record<string, LinkCopy> = {
  en: { notice: "Privacy notice", rights: "Your privacy rights", footer: "Your privacy" },
  ar: { notice: "إشعار الخصوصية", rights: "حقوقك في الخصوصية", footer: "خصوصيتك" },
  de: { notice: "Datenschutzhinweis", rights: "Ihre Datenschutzrechte", footer: "Ihre Privatsphäre" },
  fr: { notice: "Avis de confidentialité", rights: "Vos droits sur vos données", footer: "Votre vie privée" },
  hi: { notice: "गोपनीयता सूचना", rights: "आपके गोपनीयता अधिकार", footer: "आपकी गोपनीयता" },
  ru: { notice: "Уведомление о конфиденциальности", rights: "Ваши права на данные", footer: "Ваша конфиденциальность" },
  ur: { notice: "رازداری کا نوٹس", rights: "آپ کے رازداری کے حقوق", footer: "آپ کی رازداری" },
  zh: { notice: "隐私声明", rights: "您的隐私权利", footer: "您的隐私" },
};

export function privacyLinkCopy(lang?: string | null): LinkCopy {
  const base = (lang || "en").toLowerCase().split(/[-_]/)[0];
  return PRIVACY_LINK_COPY[base] ?? PRIVACY_LINK_COPY.en;
}
