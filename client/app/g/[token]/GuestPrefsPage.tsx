"use client";

/* ═══════════════════════════════════════════════════════════════════════
   /g/[token] client view. Routes by the token's purpose:
   - prefs    -> PrefsForm (J1: arrival details + optional own contact)
   - consent  -> TransferConsent (J5: the one data-transfer tick)
   - feedback -> replaced by /g/f/[token] (a feedback link opened here)
   Expired and used links say so plainly instead of showing a form.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../lib/api";
import { ConsentTick, errorText, GuestShell, journeyApi, LoadError, StateCard, usePageConfig, type PageConfig } from "../shared";
import type { GLang, GStrings } from "../i18n";
import PrefsForm from "./PrefsForm";

export default function GuestPrefsPage({ token }: { token: string }) {
  const router = useRouter();
  const cfg = usePageConfig(token);
  const { t, lang, data } = cfg;

  useEffect(() => {
    if (data?.purpose === "feedback") router.replace(`/g/f/${encodeURIComponent(token)}`);
  }, [data?.purpose, router, token]);

  let body: React.ReactNode;
  if (cfg.error) body = <LoadError err={cfg.error} t={t} onRetry={cfg.reload} />;
  else if (!data || data.purpose === "feedback") body = <StateCard title={t.loading} />;
  else if (data.state === "used") body = <StateCard title={t.used} />;
  else if (data.state !== "valid") body = <StateCard title={t.expired} />;
  else if (data.purpose === "consent") body = <TransferConsent token={token} cfg={data} lang={lang} t={t} />;
  else body = <PrefsForm key={token} token={token} cfg={data} lang={lang} t={t} />;

  return (
    <GuestShell lang={lang} t={t} hotelName={data?.hotel.name} onLang={cfg.setLang}>
      {body}
    </GuestShell>
  );
}

// ─── J5 transfer tick ────────────────────────────────────────────────────

function TransferConsent({ token, cfg, lang, t }: { token: string; cfg: PageConfig; lang: GLang; t: GStrings }) {
  const text = cfg.consent_texts?.transfer_abroad;
  const [ticked, setTicked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function confirm() {
    if (!ticked || !text) return;
    setBusy(true);
    setErr(null);
    try {
      await api.post(journeyApi(token, "/consent"), { transfer_abroad: true, text_version: text.version, language: lang });
      setDone(true);
    } catch (e) {
      setErr(errorText(e, t));
    } finally {
      setBusy(false);
    }
  }

  if (done) return <StateCard title={t.consentDone} />;
  if (!text) return <StateCard title={t.error} />;
  return (
    <section className="gst-g__card">
      <h1>{t.consentTitle}</h1>
      <p className="gst-g__muted">{t.consentIntro}</p>
      <ConsentTick text={text} checked={ticked} onChange={setTicked} />
      {err && <p className="gst-g__error" role="alert">{err}</p>}
      <button type="button" className="gst-g__btn" disabled={!ticked || busy} onClick={confirm}>{t.confirm}</button>
    </section>
  );
}
