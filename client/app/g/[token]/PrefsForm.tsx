"use client";

/* ═══════════════════════════════════════════════════════════════════════
   J1 preferences form (04 spec 3.4):
   - arrival time, occasion and requests, all optional;
   - optionally the guest's OWN phone or email, with separate, unticked
     boxes rendered from the server's versioned consent texts;
   - the data-transfer box is required whenever a contact is given;
   - marketing boxes appear only when the server says so (direct bookings;
     never for OTA bookings, Canon 19), and only for the channel typed.
   The versions of the texts shown are posted back with the ticks.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api } from "../../lib/api";
import type { GLang, GStrings } from "../i18n";
import { ConsentTick, errorText, journeyApi, type PageConfig } from "../shared";

type Kind = "phone" | "email";
const ORDER = ["transfer_abroad", "whatsapp_updates", "marketing_whatsapp", "marketing_email"] as const;
type Purpose = (typeof ORDER)[number];
const FOR_KIND: Record<Kind, readonly Purpose[]> = {
  phone: ["transfer_abroad", "whatsapp_updates", "marketing_whatsapp"],
  email: ["transfer_abroad", "marketing_email"],
};

function stayDates(cfg: PageConfig, lang: GLang): string | null {
  const { check_in: a, check_out: b } = cfg.stay ?? { check_in: null, check_out: null };
  if (!a || !b) return null;
  const f = new Intl.DateTimeFormat(lang === "ar" ? "ar-AE" : "en-GB", { day: "numeric", month: "long", timeZone: "UTC" });
  return `${f.format(new Date(`${String(a).slice(0, 10)}T12:00:00Z`))} - ${f.format(new Date(`${String(b).slice(0, 10)}T12:00:00Z`))}`;
}

export default function PrefsForm({ token, cfg, lang, t }: { token: string; cfg: PageConfig; lang: GLang; t: GStrings }) {
  const [arrival, setArrival] = useState(cfg.prefs?.arrival_time ?? "");
  const [occasion, setOccasion] = useState(cfg.prefs?.occasion ?? "");
  const [requests, setRequests] = useState(cfg.prefs?.requests ?? "");
  const [kind, setKind] = useState<Kind>("phone");
  const [contact, setContact] = useState("");
  // Ticks belong to the wording on screen: switching language shows a
  // different text version, so earlier ticks no longer count.
  const [tickState, setTickState] = useState<{ lang: GLang; ticks: Partial<Record<Purpose, boolean>> }>({ lang, ticks: {} });
  const ticks = tickState.lang === lang ? tickState.ticks : {};
  const setTicks = (next: Partial<Record<Purpose, boolean>>) => setTickState({ lang, ticks: next });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<null | { contact: boolean }>(null);

  const texts = cfg.consent_texts ?? {};
  // Only boxes the server sent (it drops marketing for OTA stays) and that
  // fit the channel being given.
  const shown = FOR_KIND[kind].filter(p => texts[p]);
  const dates = stayDates(cfg, lang);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    const value = contact.trim();
    if (value && !ticks.transfer_abroad) {
      setErr(t.needTransfer);
      return;
    }
    const consents: Record<string, boolean> = {};
    const versions: Record<string, string> = {};
    if (value) {
      for (const p of shown) {
        consents[p] = !!ticks[p];
        if (ticks[p]) versions[p] = texts[p].version;
      }
    }
    setBusy(true);
    try {
      const r = await api.post<{ ok: boolean; contact_saved: boolean }>(journeyApi(token, "/prefs"), {
        arrival_time: arrival.trim() || null,
        occasion: occasion.trim() || null,
        requests: requests.trim() || null,
        language: lang,
        contact: value ? { kind, value } : null,
        consents,
        consent_text_versions: versions,
      });
      setSaved({ contact: r.contact_saved });
      setContact("");
      setTicks({});
    } catch (e2) {
      setErr(errorText(e2, t));
    } finally {
      setBusy(false);
    }
  }

  if (saved) {
    return (
      <section className="gst-g__card" role="status">
        <h1 className="gst-g__ok">{t.saved}</h1>
        {saved.contact && <p className="gst-g__muted">{t.savedContact}</p>}
        <button type="button" className="gst-g__btn gst-g__btn--ghost" onClick={() => setSaved(null)}>{t.editAgain}</button>
      </section>
    );
  }

  return (
    <form className="gst-g__card" onSubmit={submit} noValidate>
      <h1>{t.prefsTitle}</h1>
      <p className="gst-g__muted">{t.prefsIntro}</p>
      {dates && <p><span className="gst-g__muted">{t.stayDates}: </span><bdi>{dates}</bdi></p>}

      <label className="gst-g__field">
        <span className="gst-g__label">{t.arrivalTime}</span>
        <input type="time" value={arrival} onChange={e => setArrival(e.target.value)} />
        <span className="gst-g__hint">{t.arrivalHint}</span>
      </label>
      <label className="gst-g__field">
        <span className="gst-g__label">{t.occasion}</span>
        <input type="text" maxLength={200} value={occasion} placeholder={t.occasionHint} onChange={e => setOccasion(e.target.value)} />
      </label>
      <label className="gst-g__field">
        <span className="gst-g__label">{t.requests}</span>
        <textarea maxLength={1000} value={requests} placeholder={t.requestsHint} onChange={e => setRequests(e.target.value)} />
      </label>

      <fieldset className="gst-g__card" style={{ margin: 0, padding: "14px 12px" }}>
        <legend className="gst-g__label" style={{ padding: "0 4px" }}>{t.contactTitle}</legend>
        <p className="gst-g__muted">{t.contactIntro}{cfg.marketing_boxes === false ? ` ${t.contactOtaNote}` : ""}</p>
        <div className="gst-g__seg" role="group" aria-label={t.contactTitle}>
          {(["phone", "email"] as Kind[]).map(k => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => { setKind(k); setTicks({}); }}>
              {k === "phone" ? t.phone : t.email}
            </button>
          ))}
        </div>
        <label className="gst-g__field">
          <span className="gst-g__label">{kind === "phone" ? t.contactValuePhone : t.contactValueEmail}</span>
          <input type={kind === "phone" ? "tel" : "email"} inputMode={kind === "phone" ? "tel" : "email"}
            autoComplete={kind === "phone" ? "tel" : "email"} maxLength={200} value={contact}
            placeholder={kind === "phone" ? "+971 50 123 4567" : "name@example.com"} onChange={e => setContact(e.target.value)} />
        </label>
        {shown.map(p => (
          <ConsentTick key={p} text={texts[p]} checked={!!ticks[p]}
            onChange={v => setTicks({ ...ticks, [p]: v })}
            requiredLabel={p === "transfer_abroad" ? t.required : undefined} />
        ))}
      </fieldset>

      {err && <p className="gst-g__error" role="alert">{err}</p>}
      <button type="submit" className="gst-g__btn" disabled={busy}>{t.save}</button>
    </form>
  );
}
