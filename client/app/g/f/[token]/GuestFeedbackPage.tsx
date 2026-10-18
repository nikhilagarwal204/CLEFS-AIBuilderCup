"use client";

/* ═══════════════════════════════════════════════════════════════════════
   J7 feedback page (04 spec 3.9). No review gating, no incentives:
   - the review line ("Prefer to post a public review? Google · Tripadvisor")
     sits ABOVE the survey for every guest;
   - the survey is short and every answer optional;
   - the server answers every submission identically ({completion_key:
     "thanks", review_links}); the thank-you view renders the same
     ReviewLinks with the same props and copy whatever was answered. The
     rating never reaches any branch that decides what the guest sees.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../../lib/api";
import type { GLang, GStrings } from "../../i18n";
import { errorText, GuestShell, journeyApi, LoadError, ReviewLinks, StateCard, usePageConfig, type ReviewLinksT } from "../../shared";

interface Completion {
  completion_key: string;
  review_links: ReviewLinksT;
}

export default function GuestFeedbackPage({ token }: { token: string }) {
  const router = useRouter();
  const cfg = usePageConfig(token);
  const { t, lang, data } = cfg;
  const [completion, setCompletion] = useState<Completion | null>(null);

  // A prefs or consent link opened under /g/f/ belongs on /g/.
  useEffect(() => {
    if (data && data.purpose !== "feedback") router.replace(`/g/${encodeURIComponent(token)}`);
  }, [data, router, token]);

  const links = completion?.review_links ?? data?.review_links;
  let body: React.ReactNode;
  if (cfg.error) body = <LoadError err={cfg.error} t={t} onRetry={cfg.reload} />;
  else if (!data || data.purpose !== "feedback") body = <StateCard title={t.loading} />;
  else if (completion || data.submitted) body = <ThankYou token={token} t={t} links={links} />;
  else if (data.state !== "valid") body = <StateCard title={t.expired} action={<ReviewLinks links={links} token={token} t={t} />} />;
  else body = <Survey token={token} lang={lang} t={t} links={links} onDone={setCompletion} />;

  return (
    <GuestShell lang={lang} t={t} hotelName={data?.hotel.name} onLang={cfg.setLang}>
      {body}
    </GuestShell>
  );
}

// ─── Thank-you view ──────────────────────────────────────────────────────

function ThankYou({ token, t, links }: { token: string; t: GStrings; links: ReviewLinksT | undefined }) {
  return (
    <section className="gst-g__card" role="status">
      <h1 className="gst-g__ok">{t.thanksTitle}</h1>
      <p className="gst-g__muted">{t.thanksBody}</p>
      <ReviewLinks links={links} token={token} t={t} />
    </section>
  );
}

// ─── Survey ──────────────────────────────────────────────────────────────

function Survey({
  token, lang, t, links, onDone,
}: {
  token: string;
  lang: GLang;
  t: GStrings;
  links: ReviewLinksT | undefined;
  onDone: (c: Completion) => void;
}) {
  const [overall, setOverall] = useState<number | null>(null);
  const [wentWell, setWentWell] = useState("");
  const [improve, setImprove] = useState("");
  const [contactOk, setContactOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<Completion>(journeyApi(token, "/feedback"), {
        overall, went_well: wentWell.trim() || null, improve: improve.trim() || null, contact_ok: contactOk, language: lang,
      });
      onDone(r);
    } catch (e2) {
      setErr(errorText(e2, t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="gst-g__card" onSubmit={submit}>
      <h1>{t.fbTitle}</h1>
      <ReviewLinks links={links} token={token} t={t} />
      <p className="gst-g__muted">{t.fbIntro}</p>

      <div className="gst-g__field">
        <span className="gst-g__label" id="gst-g-overall">{t.overall}</span>
        <div className="gst-g__stars" role="radiogroup" aria-labelledby="gst-g-overall">
          {[1, 2, 3, 4, 5].map(n => (
            <button key={n} type="button" role="radio" aria-checked={overall === n} className="gst-g__star"
              aria-label={t.star.replace("{n}", String(n))} onClick={() => setOverall(overall === n ? null : n)}>
              {n}
            </button>
          ))}
        </div>
      </div>
      <label className="gst-g__field">
        <span className="gst-g__label">{t.wentWell}</span>
        <textarea maxLength={2000} value={wentWell} onChange={e => setWentWell(e.target.value)} />
      </label>
      <label className="gst-g__field">
        <span className="gst-g__label">{t.improve}</span>
        <textarea maxLength={2000} value={improve} onChange={e => setImprove(e.target.value)} />
      </label>
      <label className="gst-g__check">
        <input type="checkbox" checked={contactOk} onChange={e => setContactOk(e.target.checked)} />
        <span>{t.contactOk}</span>
      </label>

      {err && <p className="gst-g__error" role="alert">{err}</p>}
      <button type="submit" className="gst-g__btn" disabled={busy}>{t.send}</button>
    </form>
  );
}
