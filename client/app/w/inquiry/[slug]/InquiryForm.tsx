"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The hotel's embeddable inquiry form (04 spec 4.3, web form channel).
   - Consent: the server's versioned wording, every box unticked; contact
     and transfer-abroad are required and checked separately (the server
     refuses each on its own, 422); WhatsApp and marketing opt-ins are
     optional and separate from the request.
   - Bots: a honeypot field and the signed render stamp from the config
     call; the server answers a bot with the same 202 and stores nothing.
   - Iframe: reports its height to /w/inquiry.js by postMessage (so the
     host page never shows a scrollbar) and pings "submitted" for the
     hotel's own conversion tracking. Nothing personal is in either message.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, api, useApi, withQuery } from "../../../lib/api";
import { embedderOrigin, originAllowed, type WidgetMessage } from "../embed";
import { asWLang, submitErrorText, W_STRINGS, type WLang, type WStrings } from "../i18n";
import { accentInk, DEFAULT_ACCENT, wStyles } from "../styles";
import FormFields, { EMPTY_FORM, type FormState } from "./FormFields";

// ─── Types ───────────────────────────────────────────────────────────────

export interface Attribution {
  page_url?: string;
  gclid?: string;
  utm?: Record<string, string>;
}

export interface ConsentLine {
  key: "consent_contact" | "consent_transfer_abroad" | "whatsapp_optin" | "consent_marketing_whatsapp" | "consent_marketing_email";
  purpose: string;
  required: boolean;
  text: string;
  version: string;
  language: string;
}

interface WidgetConfig {
  hotel: { slug: string; name: string; languages: string[] };
  language: string;
  consents: ConsentLine[];
  allowed_origins: string[];
  privacy_url: string | null;
  rendered_at: string;
  min_fill_s: number;
}

const widgetPath = (slug: string) => `/public/inquiries/${encodeURIComponent(slug)}`;

function post(msg: WidgetMessage) {
  // Height and "submitted" carry nothing personal, so any parent may read
  // them; the host's origin is not known reliably enough to target.
  if (typeof window !== "undefined" && window.parent !== window) window.parent.postMessage(msg, "*");
}

// ─── Form ────────────────────────────────────────────────────────────────

export default function InquiryForm({ slug, widgetKey, initialLang, theme, accent, frameId, attribution }: {
  slug: string;
  widgetKey: string;
  initialLang: string | null;
  theme: "light" | "dark";
  accent: string | null;
  frameId: string | null;
  attribution: Attribution;
}) {
  const [lang, setLang] = useState<WLang>(asWLang(initialLang));
  const t = W_STRINGS[lang];
  const cfg = useApi<WidgetConfig>(withQuery(`${widgetPath(slug)}/widget`, { k: widgetKey, lang }), [slug, widgetKey, lang]);

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [ticks, setTicks] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  // Framed-origin check. Only evaluated once the config has loaded, which
  // happens after hydration, so the server and first client render agree.
  const allowed = cfg.data ? originAllowed(currentEmbedder(), cfg.data.allowed_origins) : true;

  // Report the rendered height to the loader whenever it changes.
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => post({ source: "clefs-inquiry", type: "height", fid: frameId, height: Math.ceil(el.scrollHeight) + 4 }));
    ro.observe(el);
    return () => ro.disconnect();
  }, [frameId]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!cfg.data) return;
    const local = validate(form, ticks, cfg.data.consents, t);
    if (local) { setError(local); return; }
    setBusy(true);
    setError(null);
    try {
      await api.post(widgetPath(slug), buildBody(form, ticks, lang, widgetKey, cfg.data.rendered_at, attribution));
      setDone(true);
      post({ source: "clefs-inquiry", type: "submitted", fid: frameId });
    } catch (err) {
      setError(err instanceof ApiError ? submitErrorText(err.code, err.status, t) : t.error);
    } finally {
      setBusy(false);
    }
  }

  // The label colour follows the accent (hotel's or default), so the Send
  // button stays AA-readable whatever colour the hotel's site passes in.
  const effectiveAccent = accent ?? DEFAULT_ACCENT;
  const style = { "--w-accent": effectiveAccent, "--w-accent-ink": accentInk(effectiveAccent) } as React.CSSProperties;
  return (
    <div ref={rootRef} className="inq-w" data-theme={theme} dir={lang === "ar" ? "rtl" : "ltr"} lang={lang} style={style}
      data-clarity-mask="true">
      <style dangerouslySetInnerHTML={{ __html: wStyles }} />
      {!cfg.data && cfg.loading && <div className="inq-w__card"><p className="inq-w__muted">{t.loading}</p></div>}
      {!cfg.data && cfg.error && (
        <div className="inq-w__card" role="status">
          <p>{cfg.error.status === 404 ? t.notFound : cfg.error.status === 429 ? t.tooMany : t.error}</p>
          {cfg.error.status !== 404 && cfg.error.status !== 429 && (
            <button type="button" className="inq-w__btn inq-w__btn--ghost" onClick={cfg.reload}>{t.retry}</button>
          )}
        </div>
      )}
      {cfg.data && !allowed && <div className="inq-w__card" role="status"><p>{t.notEnabled}</p></div>}
      {cfg.data && allowed && done && (
        <div className="inq-w__card" role="status" aria-live="polite">
          <span className="inq-w__ok" aria-hidden="true">✓</span>
          <h1>{t.successTitle}</h1>
          <p className="inq-w__muted">{t.successBody}</p>
          <button type="button" className="inq-w__btn inq-w__btn--ghost"
            onClick={() => { setForm(EMPTY_FORM); setTicks({}); setDone(false); cfg.reload(); }}>{t.another}</button>
        </div>
      )}
      {cfg.data && allowed && !done && (
        <form className="inq-w__card" onSubmit={submit} noValidate>
          <div className="inq-w__top">
            <div>
              <h1>{t.title}</h1>
              <p className="inq-w__muted">{t.intro}</p>
            </div>
            <button type="button" className="inq-w__lang" lang={lang === "ar" ? "en" : "ar"}
              onClick={() => setLang(lang === "ar" ? "en" : "ar")}>{t.otherLang}</button>
          </div>
          <FormFields t={t} form={form} onChange={setForm} consents={cfg.data.consents} ticks={ticks}
            onTick={(k, v) => setTicks(prev => ({ ...prev, [k]: v }))} />
          {error && <p className="inq-w__error" role="alert">{error}</p>}
          <button type="submit" className="inq-w__btn" disabled={busy}>{busy ? t.sending : t.send}</button>
          {cfg.data.privacy_url && (
            <p className="inq-w__small">
              <a href={cfg.data.privacy_url} target="_blank" rel="noopener noreferrer">{t.privacy}</a>
            </p>
          )}
        </form>
      )}
    </div>
  );
}

function currentEmbedder(): string | null {
  if (typeof window === "undefined") return null;
  const framed = window.parent !== window;
  return embedderOrigin(framed, window.location.ancestorOrigins, document.referrer);
}

// ─── Validation and body ─────────────────────────────────────────────────

/** What the browser can catch before a round trip; the server re-checks
    everything and its codes map to the same sentences. */
function validate(f: FormState, ticks: Record<string, boolean>, consents: ConsentLine[], t: WStrings): string | null {
  if (!f.email.trim() && !f.phone.trim()) return t.needContact;
  if (f.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.email.trim())) return t.badEmail;
  if (f.phone.trim() && f.phone.replace(/\D/g, "").length < 7) return t.badPhone;
  if (f.check_in && f.check_out && f.check_out <= f.check_in) return t.badDates;
  if (consents.some(c => c.required && !ticks[c.key])) return t.needConsent;
  return null;
}

const intOrUndef = (v: string) => (v.trim() === "" ? undefined : Math.max(0, Math.floor(Number(v))) || undefined);

function buildBody(
  f: FormState, ticks: Record<string, boolean>, lang: WLang, widgetKey: string, renderedAt: string, a: Attribution,
): Record<string, unknown> {
  return {
    name: f.name.trim() || undefined,
    email: f.email.trim() || undefined,
    phone: f.phone.trim() || undefined,
    check_in: f.check_in || undefined,
    check_out: f.check_out || undefined,
    rooms: intOrUndef(f.rooms),
    adults: intOrUndef(f.adults),
    children: f.children.trim() === "" ? undefined : Math.max(0, Math.floor(Number(f.children)) || 0),
    message: f.message.trim() || undefined,
    language: lang,
    consent_contact: !!ticks.consent_contact,
    consent_transfer_abroad: !!ticks.consent_transfer_abroad,
    whatsapp_optin: !!ticks.whatsapp_optin,
    consent_marketing_whatsapp: !!ticks.consent_marketing_whatsapp,
    consent_marketing_email: !!ticks.consent_marketing_email,
    utm: a.utm,
    gclid: a.gclid,
    page_url: a.page_url,
    widget_key: widgetKey,
    hp: f.hp,
    rendered_at: renderedAt,
  };
}
