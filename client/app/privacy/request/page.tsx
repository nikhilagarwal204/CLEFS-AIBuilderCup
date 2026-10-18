import type { Metadata } from "next";
import Link from "next/link";
import DocPage from "../../security/_shell/DocPage";
import RequestForm from "./RequestForm";
import { FORM_COPY, fill, pickLang } from "../_copy";

/**
 * /privacy/request?hotel=<slug>&lang=ar: the public rights form (03 section
 * 5.3). Not indexed and disallowed in robots.ts: it is reached from a hotel's
 * guest notice, its QR pages or its emails, and a search result for it would
 * only send people to a form with no hotel attached.
 */

export const metadata: Metadata = {
  title: "Privacy request",
  description: "Ask a hotel that uses Clefs to show, correct, delete or send you your personal data.",
  robots: { index: false, follow: false },
};

type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function Page({ searchParams }: Props) {
  const sp = await searchParams;
  const lang = pickLang(sp.lang);
  const raw = Array.isArray(sp.hotel) ? sp.hotel[0] : sp.hotel;
  // Slugs are lowercase words and hyphens; anything else is treated as absent
  // so a crafted link cannot put arbitrary text into the form.
  const hotel = raw && /^[a-z0-9][a-z0-9-]{0,79}$/i.test(raw) ? raw.toLowerCase() : null;
  const t = FORM_COPY[lang];
  const otherLang = lang === "ar" ? "en" : "ar";
  const switchHref = `/privacy/request?${new URLSearchParams({ ...(hotel ? { hotel } : {}), lang: otherLang })}`;

  return (
    <DocPage
      eyebrow={lang === "ar" ? "طلب خصوصية" : "Privacy request"}
      title={t.title}
      intro={
        <>
          <p>{fill(t.intro, { days: 30 })}</p>
          <p>
            <Link href={switchHref} hrefLang={otherLang}>{lang === "ar" ? "English" : "العربية"}</Link>
            {hotel && (
              <>
                {" · "}
                <Link href={`/privacy/${hotel}${lang === "ar" ? "?lang=ar" : ""}`}>{t.noticeLink}</Link>
              </>
            )}
          </p>
        </>
      }
      sections={[]}
      toc={false}
      parent={{ href: "/security", label: "Security" }}
      dir={lang === "ar" ? "rtl" : "ltr"}
      lang={lang}
    >
      <RequestForm hotelSlug={hotel} lang={lang} />
    </DocPage>
  );
}
