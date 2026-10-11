"use client";

/* ═══════════════════════════════════════════════════════════════════════
   "Try it yourself": the live demo, the second section of the site root,
   right under the avatar hero (MarketingHomepage). Three numbered doors into
   the demo hotel, in the deck's order: the guest in Room 204 (the room's own
   QR link), Maria's phone (one tap opens her shift in a new tab), and the
   manager dashboard (one tap signs in as the duty manager, for that hotel
   only, for two hours).

   The server answers for demo hotels only (server/clefs/core/demo_access.py),
   so nothing here can reach a real hotel. A phone or dashboard tap opens its
   screen in a new tab: the tab is opened inside the click (no popup block),
   then pointed at the screen once the server has said yes.

   Copy rule: a judge has two minutes. Plain words, short sentences, and no
   system terms; the cast comes from the server with the deck's as fallback.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useState } from "react";
import QRCode from "qrcode";
import { LuCopy, LuExternalLink, LuLayoutDashboard, LuSmartphone, LuUser } from "react-icons/lu";
import { idbSet, KV } from "../device/lib/idb";
import { rememberHotel } from "../device/lib/lastHotel";
import {
  API, DEMO_HOTEL, absoluteUrl, castOf, deviceKeys, grantError, storedSessions,
  type ConsoleGrant, type DemoInfo, type StaffAppGrant,
} from "./demo";

type Door = "staff" | "console";

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

  // The QR code of the room's own link, for a judge's phone (desktop only).
  useEffect(() => {
    if (!info?.guest) return;
    const url = absoluteUrl(window.location.origin, info.guest.path);
    let live = true;
    QRCode.toString(url, { type: "svg", margin: 1, width: 116, color: { dark: "#070C14", light: "#F2EEE6" } })
      .then(svg => { if (live) { setQr(svg); setGuestUrl(url); } })
      .catch(() => { if (live) setGuestUrl(url); });
    return () => { live = false; };
  }, [info]);

  const open = useCallback(async (door: Door) => {
    if (busy) return;
    setBusy(door);
    setErrors(e => ({ ...e, [door]: undefined }));
    // Opened inside the click so no popup blocker stops it; pointed at the
    // screen once the server has opened Maria's shift or signed this browser in.
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
    } catch { /* the guest page button still works */ }
  }, [guestUrl]);

  const cast = castOf(info);

  return (
    <section id="try" className="tr cb-section" aria-labelledby="tr-title">
      <style dangerouslySetInnerHTML={{ __html: tryStyles }} />
      <div className="cb-container">
        <header className="cb-head tr-head">
          <p className="clefs-eyebrow">Live demo · about two minutes</p>
          <h2 id="tr-title" className="cb-head__title">Try it <em className="clefs-gilt">yourself.</em></h2>
          <p className="cb-head__desc tr-head__desc">
            {cast.hotel} is a demo hotel in {cast.city}, with sample guests and staff. Play all three parts.
            Each button opens a new tab, so you can follow one request from start to finish.
          </p>
          {loadError && (
            <div className="tr-alert" role="alert">
              {loadError}{" "}
              <button type="button" className="tr-link" onClick={() => { void load(); }}>Try again</button>
            </div>
          )}
        </header>

        <ol className="tr-doors" role="list">
          {/* ── 1. The guest ── */}
          <li className="tr-door">
            <div className="tr-door__top" aria-hidden="true">
              <span className="tr-door__num clefs-gilt">1</span>
              <span className="tr-door__icon"><LuUser size={20} /></span>
            </div>
            <h3 className="tr-door__title">Be the guest in Room {cast.room}</h3>
            <p className="tr-door__text">
              <span className="tr-wide">Open the guest page, or scan the code with your phone.</span>
              <span className="tr-narrow">Open the guest page.</span>{" "}
              Then ask for something, like &ldquo;Can I get two extra towels?&rdquo;
            </p>
            <div className="tr-qr tr-wide">
              {qr
                ? <span className="tr-qr__img" role="img" aria-label={`QR code for the Room ${cast.room} guest page`}
                        dangerouslySetInnerHTML={{ __html: qr }} />
                : <span className="tr-qr__ph" aria-hidden="true" />}
              <div className="tr-qr__side">
                <p className="tr-qr__cap">Scan with your phone camera to be the guest there.</p>
                <button type="button" className="tr-link tr-qr__copy" onClick={() => { void copy(); }} disabled={!guestUrl}>
                  <LuCopy size={13} aria-hidden="true" /> {copied ? "Link copied" : "Copy link"}
                </button>
              </div>
            </div>
            <div className="tr-door__actions">
              {info?.guest ? (
                <a href={info.guest.path} target="_blank" rel="noopener" className="cb-btn cb-btn--gold tr-btn">
                  Open guest page <LuExternalLink size={16} aria-hidden="true" />
                  <span className="tr-sr"> (opens a new tab)</span>
                </a>
              ) : (
                <span className="cb-btn cb-btn--gold tr-btn tr-btn--off" role="link" aria-disabled="true">
                  Open guest page
                </span>
              )}
            </div>
            <p className="tr-door__hint">
              Type or talk, in your own language. First, the page asks you to agree to chat with an AI.
            </p>
          </li>

          {/* ── 2. Maria's phone ── */}
          <li className="tr-door">
            <div className="tr-door__top" aria-hidden="true">
              <span className="tr-door__num clefs-gilt">2</span>
              <span className="tr-door__icon"><LuSmartphone size={20} /></span>
            </div>
            <h3 className="tr-door__title">Be {cast.staffFirst}, the housekeeper</h3>
            <p className="tr-door__text">
              See the job arrive on her phone in {cast.staffLanguage}, her own language. Accept it, then take a
              photo when it is done.
            </p>
            <div className="tr-door__actions">
              <button type="button" className="cb-btn cb-btn--gold tr-btn" onClick={() => { void open("staff"); }}
                      disabled={busy !== null} aria-busy={busy === "staff"}>
                {busy === "staff" ? "Opening…" : `Open ${cast.staffFirst}'s phone`}
                <LuExternalLink size={16} aria-hidden="true" />
                <span className="tr-sr"> (opens a new tab)</span>
              </button>
            </div>
            {errors.staff && <p className="tr-door__error" role="alert">{errors.staff}</p>}
            <p className="tr-door__hint">If asked, tap {cast.staffFirst} and enter {cast.pin}.</p>
          </li>

          {/* ── 3. The manager dashboard ── */}
          <li className="tr-door">
            <div className="tr-door__top" aria-hidden="true">
              <span className="tr-door__num clefs-gilt">3</span>
              <span className="tr-door__icon"><LuLayoutDashboard size={20} /></span>
            </div>
            <h3 className="tr-door__title">Be the manager</h3>
            <p className="tr-door__text">
              Watch the job on the live dashboard. If nobody takes it, see it move up to the supervisor. And
              approve a small gift for an unhappy guest.
            </p>
            <div className="tr-door__actions">
              <button type="button" className="cb-btn cb-btn--gold tr-btn" onClick={() => { void open("console"); }}
                      disabled={busy !== null} aria-busy={busy === "console"}>
                {busy === "console" ? "Opening…" : "Open manager dashboard"}
                <LuExternalLink size={16} aria-hidden="true" />
                <span className="tr-sr"> (opens a new tab)</span>
              </button>
            </div>
            {errors.console && <p className="tr-door__error" role="alert">{errors.console}</p>}
            <p className="tr-door__hint">You are signed in as {cast.manager}, the duty manager, for two hours.</p>
          </li>
        </ol>

        <p className="tr-fine">
          Everything here is a demo with sample data. Nothing reaches a real hotel, and the demo resets regularly.
        </p>
      </div>
    </section>
  );
}

const tryStyles = `
  /* Reached from the hero's main button: a shorter top than the other
     sections, so the doors' buttons sit on the first screen after the jump. */
  .tr.cb-section { padding-top: clamp(80px, 6vw, 88px); scroll-margin-top: 0; }
  .tr .cb-container { max-width: 1240px; }
  .tr .cb-head { margin-bottom: clamp(28px, 3.5vw, 44px); }
  .tr-head__desc { max-width: 58ch; text-wrap: pretty; }
  .tr-sr {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
    clip: rect(0 0 0 0); white-space: nowrap; border: 0;
  }
  .tr-alert {
    margin: 22px auto 0; padding: 12px 16px; border-radius: 8px; max-width: 560px; font-size: 0.92rem; line-height: 1.55;
    border: 1px solid rgba(var(--hc-urgent-rgb), 0.4); background: rgba(var(--hc-urgent-rgb), 0.08); color: var(--clefs-ink);
  }
  .tr-link {
    display: inline-flex; align-items: center; gap: 6px; background: none; border: 0; padding: 4px 2px;
    color: var(--clefs-gold-400); text-decoration: underline; text-underline-offset: 3px; cursor: pointer; font: inherit;
  }
  .tr-link[disabled] { opacity: 0.5; cursor: default; }

  /* The three doors: a row on desktop, a stack on phones and tablets. */
  .tr-doors {
    list-style: none; margin: 0; padding: 0;
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: clamp(14px, 2vw, 22px);
  }
  .tr-door {
    display: flex; flex-direction: column; gap: 14px; min-width: 0;
    padding: clamp(22px, 3vw, 30px); border-radius: 12px;
    border: 1px solid var(--clefs-line); border-top: 2px solid var(--clefs-gold-600);
    background: linear-gradient(180deg, rgba(225,175,104,0.07), rgba(255,255,255,0.015));
    transition: border-color .3s var(--ease-out);
  }
  .tr-door:focus-within { border-color: var(--clefs-line-gold); }
  .tr-door__top { display: flex; align-items: center; justify-content: space-between; }
  .tr-door__num { font-family: var(--clefs-serif); font-size: 2.5rem; line-height: 1; }
  .tr-door__icon {
    display: grid; place-items: center; width: 42px; height: 42px; border-radius: 50%;
    color: var(--clefs-gold-500); background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line-gold);
  }
  .tr-door__title { font-family: var(--clefs-serif); font-weight: 500; font-size: clamp(1.25rem, 1.8vw, 1.4rem); line-height: 1.25; margin: 0; }
  .tr-door__text { margin: 0; font-size: 1rem; line-height: 1.65; color: var(--clefs-ink); opacity: 0.86; text-wrap: pretty; }
  .tr-door__actions { margin-top: auto; padding-top: 6px; }
  .tr-btn {
    width: 100%; min-height: 54px; padding: 14px 18px; font-size: 0.95rem;
    white-space: normal; text-align: center; line-height: 1.3;
  }
  .tr-btn[disabled] { opacity: 0.7; cursor: progress; transform: none; }
  .tr-btn--off { opacity: 0.5; cursor: default; pointer-events: none; }
  .tr-door__hint { margin: 0; font-size: 0.88rem; line-height: 1.55; color: var(--clefs-ink-muted); }
  .tr-door__error { margin: 0; font-size: 0.9rem; line-height: 1.5; color: var(--hc-urgent); }

  /* The QR code is for a desktop visitor's phone: on a phone it is hidden,
     along with the words that point at it. */
  .tr-qr { display: flex; align-items: center; gap: 16px; }
  .tr-qr__img, .tr-qr__ph { flex: none; }
  .tr-qr__img svg, .tr-qr__ph { display: block; width: 116px; height: 116px; border-radius: 8px; }
  .tr-qr__ph { background: rgba(255,255,255,0.05); }
  .tr-qr__side { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-width: 0; }
  .tr-qr__cap { margin: 0; font-size: 0.86rem; line-height: 1.5; color: var(--clefs-ink-muted); }
  .tr-qr__copy { font-size: 0.86rem; }
  .tr-narrow { display: none; }

  .tr-fine {
    margin: clamp(26px, 4vw, 40px) auto 0; max-width: 64ch; text-align: center;
    font-size: 0.86rem; line-height: 1.6; color: var(--clefs-ink-muted);
  }

  /* Side by side, the three buttons line up: each hint holds two lines. */
  @media (min-width: 1001px) {
    .tr-door__hint { min-height: 3.1em; }
  }
  @media (max-width: 1000px) {
    .tr-doors { grid-template-columns: 1fr; max-width: 560px; margin: 0 auto; }
  }
  @media (max-width: 760px), (pointer: coarse) {
    .tr-wide { display: none; }
    .tr-narrow { display: inline; }
  }
`;
