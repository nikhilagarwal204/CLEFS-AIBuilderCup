"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The guest's quote page (04 spec 4.6): the stay, each night's rate, the
   UAE tax lines exactly as the hotel's quote composed them, validity, the
   payment schedule and the terms; then "Accept" and, once a payment link
   exists, "Pay". English and Arabic (RTL).

   GET /public/inquiries/quotes/{token}?lang= never returns a contact, BAR,
   floor, ceiling or the pricing trace, so nothing internal can leak here.
   Accepting creates the first-deposit payment-link action on the server;
   the link itself arrives a moment later (the dial and the provider), so
   the page re-reads a few times after accepting until `pay_url` shows.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { ApiError, api, useApi, withQuery } from "../../lib/api";
import { asLang, fill, Q_STRINGS, type QLang, type QStrings } from "../i18n";
import { qStyles } from "../styles";
import QuoteView from "./QuoteView";

// ─── Types ───────────────────────────────────────────────────────────────

export interface PublicQuote {
  hotel: {
    slug: string; name: string; city: string | null; star_rating: number | null; locale: string | null;
    timezone?: string | null;
  };
  ref: string;
  version: number;
  status: "draft" | "awaiting_approval" | "approved" | "sent" | "accepted" | "expired" | "superseded" | "withdrawn";
  language: QLang;
  valid_until: string;
  currency: string;
  stay: { check_in: string; check_out: string; nights: number; rooms: number };
  lines: { stay_date: string; room_type_code: string | null; room_type_name: string | null; rooms: number; rate_value: string }[];
  taxes: {
    room: string; service_charge: string; municipality_fee: string; vat: string; tourism_dirham: string;
    tourism_dirham_label: string; td_nights_charged: number; total: string;
  };
  fee_notices: string[];
  deposit_schedule: {
    seq: number; purpose: "deposit" | "full" | "balance"; label: string; due_on: string | null;
    amount_value: string; includes_td_value: string; td_note: string | null;
  }[];
  cancellation: string | null;
  group_terms: { cutoff_date: string; attrition_pct: string; min_rooms_after_attrition: number; rooming_list_due: string; label: string } | null;
  inclusions: string | null;
  can_accept: boolean;
  accepted: boolean;
  noindex: true;
  pay_url: string | null;
}

interface AcceptResult {
  ok: boolean;
  status: "accepted";
  payment_action_id?: number | null;
  payment_action_status?: string | null;
  payment_reason?: string | null;
  replayed?: boolean;
}

const quotePath = (token: string, p = "") => `/public/inquiries/quotes/${encodeURIComponent(token)}${p}`;

/** Re-read after accepting: every 5 s, six times (the link usually lands
    within seconds; after half a minute the message says "shortly"). */
const POLL_MS = 5_000;
const POLL_TIMES = 6;

// ─── Page ────────────────────────────────────────────────────────────────

export default function QuotePage({ token, initialLang }: { token: string; initialLang: QLang | null }) {
  const [chosen, setChosen] = useState<QLang | null>(initialLang);
  const [nonce, setNonce] = useState(0);
  const res = useApi<PublicQuote>(withQuery(quotePath(token), { lang: chosen ?? undefined }), [token, chosen, nonce]);
  const lang: QLang = chosen ?? asLang(res.data?.language);
  const t = Q_STRINGS[lang];
  const q = res.data;

  const [accepting, setAccepting] = useState(false);
  const [acceptError, setAcceptError] = useState<string | null>(null);
  const [result, setResult] = useState<AcceptResult | null>(null);
  const [polls, setPolls] = useState(0);

  // After accepting, re-read until the payment link shows (or we give up).
  const waitingForLink = !!result && !result.payment_reason && !q?.pay_url && polls < POLL_TIMES;
  useEffect(() => {
    if (!waitingForLink) return;
    const timer = setTimeout(() => { setPolls(n => n + 1); setNonce(n => n + 1); }, POLL_MS);
    return () => clearTimeout(timer);
  }, [waitingForLink, polls]);

  async function accept() {
    setAccepting(true);
    setAcceptError(null);
    try {
      const out = await api.post<AcceptResult>(quotePath(token, "/accept"));
      setResult(out);
      setPolls(0);
      setNonce(n => n + 1);
    } catch (err) {
      setAcceptError(err instanceof ApiError && err.status === 429 ? t.tooMany : t.acceptError);
      // A 409 (expired, replaced) means the page is stale: re-read it.
      if (err instanceof ApiError && err.status === 409) setNonce(n => n + 1);
    } finally {
      setAccepting(false);
    }
  }

  return (
    <main className="inq-q" dir={lang === "ar" ? "rtl" : "ltr"} lang={lang} data-clarity-mask="true">
      <style dangerouslySetInnerHTML={{ __html: qStyles }} />
      <div className="inq-q__wrap">
        <div className="inq-q__top">
          <p className="inq-q__hotel">
            {q?.hotel.name ?? ""}
            {q && (q.hotel.city || q.hotel.star_rating) && (
              <small>{[q.hotel.star_rating ? fill(t.stars, { n: q.hotel.star_rating }) : null, q.hotel.city].filter(Boolean).join(" · ")}</small>
            )}
          </p>
          <button type="button" className="inq-q__lang" lang={lang === "ar" ? "en" : "ar"}
            onClick={() => setChosen(lang === "ar" ? "en" : "ar")}>{t.otherLang}</button>
        </div>
        {!q && res.loading && <Loading />}
        {!q && res.error && <LoadError err={res.error} t={t} onRetry={res.reload} />}
        {q && (
          <QuoteView q={q} t={t} lang={lang} accepting={accepting} acceptError={acceptError}
            result={result} waitingForLink={waitingForLink} onAccept={accept} />
        )}
      </div>
    </main>
  );
}

// ─── States ──────────────────────────────────────────────────────────────

function Loading() {
  return (
    <section className="inq-q__card" aria-busy="true">
      <div className="inq-q__skel" style={{ width: "60%", height: 24 }} />
      <div className="inq-q__skel" />
      <div className="inq-q__skel" style={{ width: "80%" }} />
      <div className="inq-q__skel" style={{ width: "40%" }} />
    </section>
  );
}

/** Guest-facing words for a failed load (never the raw server text). A bad
    link and the rate limit are final; a network drop or a 5xx can retry. */
function LoadError({ err, t, onRetry }: { err: ApiError; t: QStrings; onRetry: () => void }) {
  const invalid = err.status === 404;
  const limited = err.status === 429;
  return (
    <section className="inq-q__card" role="status">
      <h1>{invalid ? t.invalid : limited ? t.tooMany : t.error}</h1>
      {invalid && <p className="inq-q__muted">{t.invalidBody}</p>}
      {!invalid && !limited && <button type="button" className="inq-q__btn inq-q__btn--ghost" onClick={onRetry}>{t.retry}</button>}
    </section>
  );
}
