"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Pieces shared by /g/[token] and /g/f/[token]:
   - usePageConfig: GET /public/journeys/{token}?lang= (rate-limited per IP
     on the server; the token's purpose decides what the page shows);
   - GuestShell: hotel name, EN/AR switch, dir/lang on the page root;
   - ConsentTick: one unticked box whose text is the server's versioned
     wording (never hardcoded), so the version posted back is what was read;
   - ReviewLinks: THE review-link line. It takes the configured links, the
     token and the language, and nothing about the guest's answers, so it
     cannot be made rating-dependent (Google and Tripadvisor both ban
     review gating). Above the survey and on the thank-you view it gets the
     same props.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState, type ReactNode } from "react";
import { ApiError, apiUrl, useApi, withQuery } from "../lib/api";
import { G_STRINGS, asLang, type GLang, type GStrings } from "./i18n";
import { gPageStyles } from "./styles";

// ─── Types ───────────────────────────────────────────────────────────────

export interface ConsentText {
  version: string;
  language: string;
  body: string;
}

export interface ReviewLinksT {
  google_url: string | null;
  tripadvisor_url: string | null;
}

export interface PageConfig {
  purpose: "prefs" | "feedback" | "consent";
  state: "valid" | "expired" | "used" | "invalid";
  language: string;
  hotel: { slug: string; name: string; city: string | null };
  /** Only while the link is valid: a dead link shows nothing of the stay. */
  stay?: { check_in: string | null; check_out: string | null };
  marketing_boxes?: boolean;
  consent_texts?: Record<string, ConsentText>;
  prefs?: { arrival_time: string | null; requests: string | null; occasion: string | null; language: string | null } | null;
  submitted?: boolean;
  review_links?: ReviewLinksT;
}

// ─── Data ────────────────────────────────────────────────────────────────

export const journeyApi = (token: string, p = "") => `/public/journeys/${encodeURIComponent(token)}${p}`;

export function usePageConfig(token: string) {
  const [chosen, setChosen] = useState<GLang | null>(null);
  const res = useApi<PageConfig>(withQuery(journeyApi(token), { lang: chosen ?? undefined }), [token, chosen]);
  const lang: GLang = chosen ?? asLang(res.data?.language);
  return { ...res, lang, t: G_STRINGS[lang], setLang: setChosen };
}

/** A guest-facing message for a failed call (never the raw server text). */
export function errorText(err: unknown, t: GStrings): string {
  if (err instanceof ApiError) {
    if (err.status === 404) return t.invalid;
    if (err.status === 410) return err.code === "link_used" ? t.used : t.expired;
    if (err.status === 429) return t.tooMany;
  }
  return t.error;
}

/** Whether trying again can help: 404 (bad link), 410 (dead link) and 429
    (rate limit) are final answers; a network drop or a 5xx is not. */
export function canRetry(err: unknown): boolean {
  return !(err instanceof ApiError && [404, 410, 429].includes(err.status));
}

/** The load-error card: guests arrive from WhatsApp, often on mobile data,
    so a transient failure offers a retry instead of a dead end. */
export function LoadError({ err, t, onRetry }: { err: unknown; t: GStrings; onRetry: () => void }) {
  const action = canRetry(err)
    ? <button type="button" className="gst-g__btn" onClick={onRetry}>{t.retry}</button>
    : undefined;
  return <StateCard title={errorText(err, t)} action={action} />;
}

// ─── Layout ──────────────────────────────────────────────────────────────

export function GuestShell({
  lang, t, hotelName, onLang, children,
}: {
  lang: GLang;
  t: GStrings;
  hotelName?: string;
  onLang?: (l: GLang) => void;
  children: ReactNode;
}) {
  return (
    <main className="gst-g" dir={lang === "ar" ? "rtl" : "ltr"} lang={lang} data-clarity-mask="true">
      <style dangerouslySetInnerHTML={{ __html: gPageStyles }} />
      <div className="gst-g__wrap">
        <div className="gst-g__top">
          <p className="gst-g__hotel">{hotelName ?? ""}</p>
          {onLang && (
            <button type="button" className="gst-g__lang" lang={lang === "ar" ? "en" : "ar"}
              onClick={() => onLang(lang === "ar" ? "en" : "ar")}>{t.otherLang}</button>
          )}
        </div>
        {children}
      </div>
    </main>
  );
}

export function StateCard({ title, body, action }: { title: string; body?: string; action?: ReactNode }) {
  return (
    <section className="gst-g__card" role="status">
      <h1>{title}</h1>
      {body && <p className="gst-g__muted">{body}</p>}
      {action}
    </section>
  );
}

// ─── Consent tick ────────────────────────────────────────────────────────

export function ConsentTick({
  text, checked, onChange, requiredLabel,
}: {
  text: ConsentText;
  checked: boolean;
  onChange: (v: boolean) => void;
  requiredLabel?: string;
}) {
  return (
    <label className="gst-g__check" dir={text.language === "ar" ? "rtl" : "ltr"} lang={text.language}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
      <span>
        {text.body}
        {requiredLabel && <span className="gst-g__req">{requiredLabel}</span>}
      </span>
    </label>
  );
}

// ─── Review links ────────────────────────────────────────────────────────

/** Same props wherever it renders; renders nothing when no link is set.
    Links go through the click counter (302 to the hotel's page); the
    layout's no-referrer policy keeps the token out of the review site. */
export function ReviewLinks({ links, token, t }: { links: ReviewLinksT | undefined; token: string; t: GStrings }) {
  if (!links || (!links.google_url && !links.tripadvisor_url)) return null;
  const href = (platform: "google" | "tripadvisor") => apiUrl(journeyApi(token, `/go/${platform}`));
  return (
    <p className="gst-g__reviews">
      <span>{t.reviewLine}</span>
      {links.google_url && <a href={href("google")} target="_blank" rel="noopener noreferrer">{t.google}</a>}
      {links.google_url && links.tripadvisor_url && <span className="gst-g__sep" aria-hidden="true">·</span>}
      {links.tripadvisor_url && <a href={href("tripadvisor")} target="_blank" rel="noopener noreferrer">{t.tripadvisor}</a>}
    </p>
  );
}
