"use client";

/* ═══════════════════════════════════════════════════════════════════════
   /try: the live prototype for reviewers (05 spec section 5). Three doors
   into the demo hotel, in the deck's order: the guest in Room 204 (the
   room's keyed QR link), Maria's staff app (one tap pairs this browser as
   her phone and opens her shift), and the manager console (one tap signs
   this browser in as the duty manager, for that hotel only, 2 hours).

   The server answers for demo hotels only (server/clefs/core/demo_access.py),
   so nothing here can reach a real hotel. A staff-app or console tap opens
   its screen in a new tab: the tab is opened inside the click (no popup
   block), then pointed at the screen once the server has said yes.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import QRCode from "qrcode";
import {
  LuArrowRight, LuBellRing, LuCamera, LuCopy, LuExternalLink, LuHeartHandshake, LuHourglass, LuLayoutDashboard,
  LuShieldCheck, LuSmartphone, LuUser,
} from "react-icons/lu";
import { idbSet, KV } from "../device/lib/idb";
import { rememberHotel } from "../device/lib/lastHotel";
import {
  API, DEMO_HOTEL, absoluteUrl, deviceKeys, floorsLine, grantError, languageName, shadowLine, storedSessions, testsLine,
  type ConsoleGrant, type DemoInfo, type StaffAppGrant,
} from "./demo";

type Door = "staff" | "console";

// The deck's process slide, and which screen shows each step.
const FLOW: { t: string; where: string }[] = [
  { t: "Speak or type the request, in your language.", where: "Guest" },
  { t: "Gemini understands the intent, the items, the urgency and your mood.", where: "Guest" },
  { t: "One job per ask: try “two towels, and the AC is too warm”.", where: "Guest" },
  { t: "Dispatch with ETA: “Maria is on her way, about 5 minutes”.", where: "Guest" },
  { t: "Maria gets the job in her language, with your own words below it.", where: "Staff app" },
  { t: "She accepts it, does it and takes a photo.", where: "Staff app" },
  { t: "Gemini vision checks the photo against the request.", where: "Staff app" },
  { t: "Closed: you confirm, and the manager's dashboard updates.", where: "Guest and console" },
];

const WRONG = [
  { icon: LuBellRing, t: "Leave the job unaccepted", d: "The supervisor hears at 3 minutes, the duty manager at 5 and the GM at 10. The console shows each step." },
  { icon: LuCamera, t: "Send a photo of something else", d: "Gemini vision rejects it, the job stays open and the staff app says why." },
  { icon: LuHeartHandshake, t: "Answer “Not fixed” on the guest page", d: "The job reopens and the duty manager gets a recovery gesture to approve." },
];

async function postJson<T>(path: string): Promise<{ ok: true; data: T } | { ok: false; status: number; code?: string }> {
  try {
    const res = await fetch(`${API}${path}`, {
      method: "POST", credentials: "same-origin",
      headers: { Accept: "application/json", "Content-Type": "application/json" }, body: "{}",
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, status: res.status, code: (body as { code?: string }).code };
    return { ok: true, data: body as T };
  } catch {
    return { ok: false, status: 0 };
  }
}

export default function TryDemo() {
  const slug = DEMO_HOTEL;
  const [info, setInfo] = useState<DemoInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [guestUrl, setGuestUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState<Door | null>(null);
  const [errors, setErrors] = useState<Partial<Record<Door, string>>>({});
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await fetch(`${API}/public/demo/${encodeURIComponent(slug)}`, { headers: { Accept: "application/json" } });
      if (!res.ok) {
        setLoadError(grantError(res.status));
        return;
      }
      setInfo((await res.json()) as DemoInfo);
    } catch {
      setLoadError(grantError(0));
    }
  }, [slug]);

  // A fetch on mount: state is set only after the answer arrives.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);

  // The QR code of the keyed room link, for a reviewer's own phone.
  useEffect(() => {
    if (!info?.guest) return;
    const url = absoluteUrl(window.location.origin, info.guest.path);
    let live = true;
    QRCode.toString(url, { type: "svg", margin: 1, width: 168, color: { dark: "#070C14", light: "#F2EEE6" } })
      .then(svg => { if (live) { setQr(svg); setGuestUrl(url); } })
      .catch(() => { if (live) setGuestUrl(url); });
    return () => { live = false; };
  }, [info]);

  const open = useCallback(async (door: Door) => {
    if (busy) return;
    setBusy(door);
    setErrors(e => ({ ...e, [door]: undefined }));
    // Opened inside the click so no popup blocker stops it; pointed at the
    // screen once the server has paired or signed this browser in.
    const tab = window.open("", "_blank");
    const go = (path: string) => {
      if (tab && !tab.closed) tab.location.href = path;
      else window.location.assign(path);
    };
    try {
      if (door === "staff") {
        const r = await postJson<StaffAppGrant>(`/public/demo/${encodeURIComponent(slug)}/staff-app`);
        if (!r.ok) throw r;
        const keys = deviceKeys(r.data.hotel_slug);
        await idbSet(KV, keys.device, r.data.device);
        await idbSet(KV, keys.sessions, storedSessions(r.data.session, Date.now()));
        rememberHotel(r.data.hotel_slug);
        go(r.data.path);
      } else {
        const r = await postJson<ConsoleGrant>(`/public/demo/${encodeURIComponent(slug)}/console`);
        if (!r.ok) throw r;
        go(r.data.path);
      }
    } catch (err) {
      tab?.close();
      const e = err as { status?: number; code?: string };
      setErrors(prev => ({ ...prev, [door]: grantError(e.status ?? 0, e.code) }));
    } finally {
      setBusy(null);
    }
  }, [busy, slug]);

  const copy = useCallback(async () => {
    if (!guestUrl) return;
    try {
      await navigator.clipboard.writeText(guestUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* the link is on screen to copy by hand */ }
  }, [guestUrl]);

  const staff = info?.staff;
  const hotelName = info?.hotel.name ?? "the demo hotel";
  const room = info?.guest?.room ?? "204";

  return (
    <div className="tr clefs-ground">
      <style dangerouslySetInnerHTML={{ __html: tryStyles }} />
      <header className="tr-bar">
        <Link href="/" className="tr-bar__brand" aria-label="Clefs home">
          <img src="/brand/clefs-key.webp" alt="" className="tr-bar__mark" width={520} height={1031} />
          <span className="tr-bar__word clefs-gilt">CLEFS</span>
        </Link>
        <Link href="/story" className="tr-bar__back">Why Clefs <LuArrowRight size={15} aria-hidden="true" /></Link>
      </header>

      <main className="tr-main clefs-above">
        <section className="tr-hero">
          <p className="clefs-eyebrow">Live prototype</p>
          <h1 className="tr-hero__title">Try Clefs <em className="clefs-gilt">in two minutes.</em></h1>
          <p className="tr-hero__sub">
            {hotelName} is a demo hotel with sample data. Play all three roles: the guest in Room {room},
            {staff ? ` ${staff.name} from ${staff.department ?? "Housekeeping"},` : " the room attendant,"} and
            the duty manager. Give each its own tab, or use a second phone for the guest.
          </p>
          {info?.gate && (
            <ul className="tr-gate" aria-label="Quality gate">
              <li><LuShieldCheck size={16} aria-hidden="true" /> {testsLine(info.gate.tests)}</li>
              <li><LuHourglass size={16} aria-hidden="true" /> {shadowLine(info.gate.shadow)}</li>
            </ul>
          )}
          {loadError && (
            <div className="tr-alert" role="alert">
              {loadError} <button type="button" className="tr-link" onClick={() => { void load(); }}>Try again</button>
            </div>
          )}
        </section>

        <section className="tr-cards" aria-label="The three screens">
          {/* ── 1. The guest ── */}
          <article className="tr-card">
            <span className="tr-card__step">1</span>
            <span className="tr-card__icon" aria-hidden="true"><LuUser size={20} /></span>
            <h2 className="tr-card__title">Be the guest in Room {room}</h2>
            <p className="tr-card__text">
              Scan the code with your phone, or open the page here. Agree to the AI processing, tap the mic
              and ask, in your language: &ldquo;Can we get extra towels?&rdquo;
            </p>
            <div className="tr-qr" aria-hidden={!qr}>
              {qr ? <span className="tr-qr__img" dangerouslySetInnerHTML={{ __html: qr }} /> : <span className="tr-qr__ph" />}
            </div>
            <div className="tr-card__actions">
              {info?.guest ? (
                <a href={info.guest.path} target="_blank" rel="noopener" className="cb-btn cb-btn--gold">
                  <LuExternalLink size={15} aria-hidden="true" /> Open the guest page
                </a>
              ) : (
                <span className="cb-btn cb-btn--gold tr-disabled" aria-disabled="true">Open the guest page</span>
              )}
              <button type="button" className="cb-btn cb-btn--ghost" onClick={() => { void copy(); }} disabled={!guestUrl}>
                <LuCopy size={15} aria-hidden="true" /> {copied ? "Copied" : "Copy link"}
              </button>
            </div>
            <p className="tr-card__fine">The link is the room&apos;s own QR card, so the page knows you are in Room {room}.</p>
          </article>

          {/* ── 2. Maria's staff app ── */}
          <article className="tr-card">
            <span className="tr-card__step">2</span>
            <span className="tr-card__icon" aria-hidden="true"><LuSmartphone size={20} /></span>
            <h2 className="tr-card__title">Open {staff ? `${staff.first_name}'s` : "Maria's"} staff app</h2>
            <p className="tr-card__text">
              One tap pairs this browser as {staff?.name ?? "Maria Santos"}&apos;s phone: {staff?.department ?? "Housekeeping"},{" "}
              {floorsLine(staff?.floors ?? ["1", "2", "3", "4"])}. Her jobs arrive in {languageName(staff?.languages?.[0] ?? "fil")},
              with the guest&apos;s own words below. Accept the job, then mark it done with a photo.
            </p>
            <div className="tr-card__actions">
              <button type="button" className="cb-btn cb-btn--gold" onClick={() => { void open("staff"); }} disabled={busy !== null}>
                <LuSmartphone size={15} aria-hidden="true" /> {busy === "staff" ? "Pairing..." : `Open ${staff?.first_name ?? "Maria"}'s staff app`}
              </button>
            </div>
            {errors.staff && <p className="tr-card__error" role="alert">{errors.staff}</p>}
            <p className="tr-card__fine">
              If the app asks who is starting, tap {staff?.first_name ?? "Maria"} and enter PIN {staff?.pin ?? "2046"}.
            </p>
          </article>

          {/* ── 3. The manager console ── */}
          <article className="tr-card">
            <span className="tr-card__step">3</span>
            <span className="tr-card__icon" aria-hidden="true"><LuLayoutDashboard size={20} /></span>
            <h2 className="tr-card__title">Open the manager console</h2>
            <p className="tr-card__text">
              One tap signs this browser in as {info?.console.name ?? "Omar Khalid"}, the duty manager, for 2 hours.
              Watch the job and its timer, the escalation ladder and the owner view, and approve recovery gestures.
            </p>
            <div className="tr-card__actions">
              <button type="button" className="cb-btn cb-btn--gold" onClick={() => { void open("console"); }} disabled={busy !== null}>
                <LuLayoutDashboard size={15} aria-hidden="true" /> {busy === "console" ? "Signing in..." : "Open the manager console"}
              </button>
            </div>
            {errors.console && <p className="tr-card__error" role="alert">{errors.console}</p>}
            <p className="tr-card__fine">The console sees this demo hotel only, and cannot change its settings or team.</p>
          </article>
        </section>

        <section className="tr-flow" aria-labelledby="tr-flow-h">
          <h2 id="tr-flow-h" className="tr-h2">What happens, step by step</h2>
          <ol className="tr-steps">
            {FLOW.map((s, i) => (
              <li key={s.t} className="tr-step">
                <span className="tr-step__n clefs-gilt" aria-hidden="true">{i + 1}</span>
                <span className="tr-step__t">{s.t}</span>
                <span className="tr-step__where">{s.where}</span>
              </li>
            ))}
          </ol>
        </section>

        <section className="tr-flow" aria-labelledby="tr-wrong-h">
          <h2 id="tr-wrong-h" className="tr-h2">Then make something go wrong</h2>
          <div className="tr-wrong">
            {WRONG.map(w => {
              const Icon = w.icon;
              return (
                <div key={w.t} className="tr-wrong__item">
                  <span className="tr-wrong__icon" aria-hidden="true"><Icon size={17} /></span>
                  <div>
                    <p className="tr-wrong__t">{w.t}</p>
                    <p className="tr-wrong__d">{w.d}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="tr-story" aria-label="The story behind Clefs">
          <div>
            <p className="tr-story__t">Why hotels need this</p>
            <p className="tr-story__d">The problem, the nine features, how it is built on Google Cloud with Gemini, pricing and trust.</p>
          </div>
          <Link href="/story" className="tr-story__btn">Read the story <LuArrowRight size={15} aria-hidden="true" /></Link>
        </section>

        <p className="tr-fine">
          Every tap here works on the demo hotel only: it pairs a new browser as a staff phone, or opens a
          2-hour duty manager session, and never reaches a real hotel. The demo data is reset regularly.
        </p>
      </main>
    </div>
  );
}

const tryStyles = `
  .tr-story {
    margin: 40px 0 0; padding: 22px 24px; border: 1px solid rgba(201, 162, 39, 0.35); border-radius: 18px;
    display: flex; align-items: center; justify-content: space-between; gap: 18px; flex-wrap: wrap;
    background: rgba(201, 162, 39, 0.06);
  }
  .tr-story__t { margin: 0 0 4px; font-family: var(--clefs-serif); font-size: 1.25rem; }
  .tr-story__d { margin: 0; opacity: 0.75; font-size: 0.95rem; max-width: 560px; }
  .tr-story__btn {
    display: inline-flex; align-items: center; gap: 8px; padding: 11px 18px; border-radius: 999px;
    border: 1px solid rgba(201, 162, 39, 0.6); color: inherit; text-decoration: none; font-weight: 600; white-space: nowrap;
  }
  .tr-story__btn:hover { background: rgba(201, 162, 39, 0.12); }
  .tr { min-height: 100vh; color: var(--clefs-ink); font-family: var(--clefs-sans); overflow-x: clip; }
  .tr h1, .tr h2, .tr-step__n { font-family: var(--clefs-serif); font-weight: 500; }
  .tr em { font-style: italic; }

  .tr-bar {
    position: relative; z-index: 2; max-width: 1180px; margin: 0 auto;
    padding: 22px clamp(16px, 5vw, 48px); display: flex; align-items: center; justify-content: space-between; gap: 16px;
  }
  .tr-bar__brand { display: flex; align-items: center; gap: 12px; text-decoration: none; }
  .tr-bar__mark { height: 32px; width: auto; }
  .tr-bar__word { font-family: var(--clefs-serif); font-size: 1.25rem; letter-spacing: 0.16em; }
  .tr-bar__back { display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem; color: var(--clefs-ink-muted); text-decoration: none; }
  .tr-bar__back:hover { color: var(--clefs-gold-400); }

  .tr-main { max-width: 1180px; margin: 0 auto; padding: 18px clamp(16px, 5vw, 48px) 90px; }
  .tr-hero { text-align: center; max-width: 820px; margin: 0 auto clamp(34px, 5vw, 56px); }
  .tr-hero .clefs-eyebrow { display: block; margin-bottom: 16px; }
  .tr-hero__title { font-size: clamp(2.1rem, 5vw, 3.4rem); line-height: 1.1; margin: 0 0 18px; }
  .tr-hero__sub { font-size: 1rem; line-height: 1.75; color: var(--clefs-ink-muted); margin: 0 auto; max-width: 60ch; }
  .tr-gate { list-style: none; margin: 24px auto 0; padding: 0; display: flex; flex-wrap: wrap; justify-content: center; gap: 10px 22px; }
  .tr-gate li {
    display: inline-flex; align-items: center; gap: 8px; padding: 8px 14px; border-radius: 999px; font-size: 0.86rem;
    border: 1px solid rgba(var(--hc-success-rgb), 0.3); background: rgba(var(--hc-success-rgb), 0.07); color: var(--clefs-ink);
  }
  .tr-gate svg { color: var(--hc-success); flex: none; }
  .tr-alert { margin: 20px auto 0; padding: 12px 16px; border-radius: 8px; max-width: 560px; font-size: 0.9rem;
              border: 1px solid rgba(var(--hc-urgent-rgb), 0.4); background: rgba(var(--hc-urgent-rgb), 0.08); }
  .tr-link { background: none; border: 0; padding: 0; color: var(--clefs-gold-400); text-decoration: underline; cursor: pointer; font: inherit; }

  .tr-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: clamp(14px, 2vw, 22px); }
  .tr-card {
    position: relative; display: flex; flex-direction: column; gap: 14px;
    padding: clamp(24px, 3vw, 32px); border-radius: 10px;
    border: 1px solid var(--clefs-line); background: linear-gradient(180deg, rgba(225,175,104,0.06), rgba(255,255,255,0.015));
  }
  .tr-card__step { position: absolute; top: 18px; right: 22px; font-family: var(--clefs-serif); font-size: 2.4rem; line-height: 1; color: rgba(225,175,104,0.22); }
  .tr-card__icon {
    display: grid; place-items: center; width: 42px; height: 42px; border-radius: 50%;
    color: var(--clefs-gold-500); background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line-gold);
  }
  .tr-card__title { font-size: 1.45rem; line-height: 1.25; margin: 0; }
  .tr-card__text { margin: 0; font-size: 0.94rem; line-height: 1.7; color: var(--clefs-ink-muted); }
  .tr-card__actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: auto; }
  .tr-card__actions .cb-btn { white-space: normal; text-align: center; justify-content: center; line-height: 1.35; }
  .tr-card__actions .cb-btn[disabled], .tr-disabled { opacity: 0.55; pointer-events: none; }
  .tr-card__fine { margin: 0; font-size: 0.8rem; line-height: 1.55; color: var(--clefs-ink-faint); }
  .tr-card__error { margin: 0; font-size: 0.86rem; color: var(--hc-urgent); }
  .tr-qr { display: flex; justify-content: center; }
  .tr-qr__img svg, .tr-qr__ph { display: block; width: 168px; height: 168px; border-radius: 8px; }
  .tr-qr__ph { background: rgba(255,255,255,0.05); }

  .tr-flow { margin-top: clamp(44px, 6vw, 72px); }
  .tr-h2 { font-size: clamp(1.4rem, 2.6vw, 1.9rem); margin: 0 0 18px; text-align: center; }
  .tr-steps { list-style: none; margin: 0 auto; padding: 0; max-width: 860px; display: grid; gap: 2px; }
  .tr-step {
    display: grid; grid-template-columns: 34px 1fr auto; gap: 14px; align-items: baseline;
    padding: 13px 4px; border-bottom: 1px solid var(--clefs-line-soft);
  }
  .tr-step__n { font-size: 1.3rem; }
  .tr-step__t { font-size: 0.95rem; line-height: 1.55; }
  .tr-step__where { font-size: 0.72rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--clefs-gold-400); white-space: nowrap; }
  .tr-wrong { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: clamp(12px, 2vw, 20px); }
  .tr-wrong__item { display: grid; grid-template-columns: auto 1fr; gap: 12px; padding: 18px; border-radius: 8px;
                    border: 1px solid var(--clefs-line-soft); background: var(--clefs-surface); }
  .tr-wrong__icon { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 50%;
                    color: var(--hc-warning); background: rgba(var(--hc-warning-rgb), 0.08); border: 1px solid rgba(var(--hc-warning-rgb), 0.3); }
  .tr-wrong__t { margin: 0 0 6px; font-weight: 600; font-size: 0.95rem; }
  .tr-wrong__d { margin: 0; font-size: 0.88rem; line-height: 1.6; color: var(--clefs-ink-muted); }
  .tr-fine { margin: clamp(40px, 5vw, 60px) auto 0; max-width: 70ch; text-align: center; font-size: 0.8rem; line-height: 1.6; color: var(--clefs-ink-faint); }

  @media (max-width: 1000px) {
    .tr-cards, .tr-wrong { grid-template-columns: 1fr; max-width: 560px; margin-left: auto; margin-right: auto; }
  }
  @media (max-width: 560px) {
    .tr-step { grid-template-columns: 28px 1fr; }
    .tr-step__where { grid-column: 2; }
    .tr-card__actions .cb-btn { width: 100%; }
  }
`;
