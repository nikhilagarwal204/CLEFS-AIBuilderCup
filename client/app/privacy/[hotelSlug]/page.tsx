import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import DocPage, { type DocSection } from "../../security/_shell/DocPage";
import { retentionRule } from "../../lib/retention";
import { GUEST_RETENTION_CLASSES, NOTICE_COPY, RETENTION_AR, fill, pickLang, type PrivacyLang } from "../_copy";
import { privacyRequestHref } from "../links";

/**
 * /privacy/<hotelSlug>?lang=ar: the per-hotel guest privacy notice (03
 * section 5.3). Path note against the spec: 5.3 names /stay/<hotelSlug>/privacy;
 * that path is an alias that redirects here (app/stay/[hotelSlug]/privacy), so
 * the notice sits outside the noindex per-room /stay tree beside the rights
 * form. Guest surfaces link to it through app/privacy/links.ts
 * (privacyNoticeHref, with the link text in every guest language); the
 * rights form links back to it.
 *
 * Server-rendered from the public form context (GET /public/privacy/form),
 * the only anonymous endpoint that names a hotel without exposing anything
 * else about it. When the API cannot be reached the notice still renders with
 * a neutral "this hotel": a guest must never hit an error page when looking for
 * their rights. Only a definite 404 (unknown or archived hotel) is a 404 here.
 */

// ─── Data ────────────────────────────────────────────────────────────────────

type HotelInfo = { slug: string; name: string; guest_languages?: string[] };
type Lookup = { hotel: HotelInfo | null; missing: boolean };

const API = (process.env.CLEFS_API_ORIGIN || "http://localhost:8000").replace(/\/+$/, "");
const SLUG_RX = /^[a-z0-9][a-z0-9-]{0,79}$/;

async function lookup(slug: string): Promise<Lookup> {
  if (!SLUG_RX.test(slug)) return { hotel: null, missing: true };
  try {
    const res = await fetch(`${API}/public/privacy/form?hotel=${encodeURIComponent(slug)}`, {
      headers: { Accept: "application/json" },
      next: { revalidate: 3600 },
    });
    if (res.status === 404) return { hotel: null, missing: true };
    if (!res.ok) return { hotel: null, missing: false };
    const body = (await res.json()) as { hotel?: HotelInfo };
    return { hotel: body.hotel ?? null, missing: false };
  } catch {
    return { hotel: null, missing: false };
  }
}

type Props = {
  params: Promise<{ hotelSlug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export async function generateMetadata({ params, searchParams }: Props): Promise<Metadata> {
  const [{ hotelSlug }, sp] = await Promise.all([params, searchParams]);
  const lang = pickLang(sp.lang);
  const { hotel } = await lookup(hotelSlug.toLowerCase());
  const t = NOTICE_COPY[lang];
  return {
    title: fill(t.title, { hotel: hotel?.name ?? t.fallbackHotel }),
    description: fill(t.intro, { hotel: hotel?.name ?? t.fallbackHotel }),
    // Per-hotel legal notice: useful to the guest holding the link, not to search.
    robots: { index: false, follow: true },
  };
}

// ─── Sections ────────────────────────────────────────────────────────────────

function retentionItems(lang: PrivacyLang) {
  return GUEST_RETENTION_CLASSES.map((cls) => {
    if (lang === "ar") return { key: cls, ...RETENTION_AR[cls] };
    const r = retentionRule(cls);
    return { key: cls, label: r?.label ?? cls, keep: r?.keep ?? "" };
  });
}

function sections(lang: PrivacyLang, hotelName: string, slug: string): DocSection[] {
  const t = NOTICE_COPY[lang];
  return [
    { id: "who", h2: t.whoH, body: <p>{fill(t.who, { hotel: hotelName })}</p> },
    { id: "what", h2: t.whatH, body: <ul>{t.what.map((w) => <li key={w}>{w}</li>)}</ul> },
    { id: "why", h2: t.whyH, body: <p>{t.why}</p> },
    { id: "where", h2: t.whereH, body: <p>{t.where}</p> },
    {
      id: "keep",
      h2: t.keepH,
      body: (
        <>
          <p>{t.keepIntro}</p>
          <ul>
            {retentionItems(lang).map((r) => (
              <li key={r.key}><strong>{r.label}</strong>: {r.keep}</li>
            ))}
          </ul>
        </>
      ),
    },
    {
      id: "rights",
      h2: t.rightsH,
      body: (
        <>
          <p>{t.rights}</p>
          <p>
            <Link className="cb-btn cb-btn--gold" href={privacyRequestHref(slug, lang)}>
              {t.rightsCta}
            </Link>
          </p>
        </>
      ),
    },
    {
      id: "processors",
      h2: t.processorsH,
      body: <p>{t.processors} <Link href="/subprocessors">{t.processorsLink}</Link></p>,
    },
  ];
}

// ─── Page ────────────────────────────────────────────────────────────────────

export default async function Page({ params, searchParams }: Props) {
  const [{ hotelSlug }, sp] = await Promise.all([params, searchParams]);
  const slug = hotelSlug.toLowerCase();
  const lang = pickLang(sp.lang);
  const { hotel, missing } = await lookup(slug);
  if (missing) notFound();

  const t = NOTICE_COPY[lang];
  const name = hotel?.name ?? t.fallbackHotel;
  const switchHref = lang === "ar" ? `/privacy/${slug}` : `/privacy/${slug}?lang=ar`;

  return (
    <DocPage
      eyebrow={t.eyebrow}
      title={fill(t.title, { hotel: name })}
      intro={
        <>
          <p>{fill(t.intro, { hotel: name })}</p>
          <p><Link href={switchHref} hrefLang={lang === "ar" ? "en" : "ar"}>{t.langSwitch}</Link></p>
        </>
      }
      sections={sections(lang, name, slug)}
      toc={false}
      dir={lang === "ar" ? "rtl" : "ltr"}
      lang={lang}
    />
  );
}
