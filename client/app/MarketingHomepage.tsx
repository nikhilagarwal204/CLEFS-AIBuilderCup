"use client";
import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { HOME_FAQS } from "./lib/faq";
import { BRAND, OTA_AUDIT_LIVE, PRIMARY_LEAD } from "./lib/seo";
import { claimFlag } from "./lib/claims";
import Claim, { CLAIM_STYLES } from "./components/Claim";
import DeskHoursCalculator from "./components/DeskHoursCalculator";
import OtaLeakageCalculator from "./components/OtaLeakageCalculator";
import {
  LuMic, LuArrowRight, LuPlus, LuPlay, LuSquare, LuFileSearch, LuRadar, LuConciergeBell,
  LuBadgeCheck, LuUserCheck, LuSearchCheck, LuCheck,
} from "react-icons/lu";

const DEMO_URL = BRAND.demoUrl;

// ─── Page data ───────────────────────────────────────────────────────────────
// Canon 22 copy: "hotels", never "independent hotels only". Every market
// number goes through <Claim> (lib/claims.ts); check-claims.mjs enforces it.

type Feature = { t: string; flag?: string };
type Product = {
  id: string; name: string; icon: typeof LuRadar; href: string;
  title: string; d: string; features: Feature[];
};

/* Three products, each with its own page. The old five promises ("Answers in
   one second", "Any language", "Remembers your guests") were guest-assistant
   features; the owner buys profit and control, so those now live as features
   of Service Control, and "Remembers your guests" only returns when
   claims.guest_memory is on (Canon 16). */
const PRODUCTS: Product[] = [
  {
    id: "profit-guard", name: "Profit Guard", icon: LuFileSearch, href: "/profit-guard",
    title: "OTA payouts and rate integrity, checked line by line",
    d: "Clefs reads your OTA statements and reservations and checks each stay against your contract. Commission charged on the wrong base, missing payouts and rate-parity gaps each become one line, with the evidence and the amount.",
    features: [
      { t: "Every OTA statement line checked against your contract terms" },
      { t: "A dispute pack ready to send, with the evidence attached" },
      { t: "Recovered money tracked until it lands in your account" },
    ],
  },
  {
    id: "demand-radar", name: "Demand Radar", icon: LuRadar, href: "/demand-radar",
    title: "Local demand, spotted before it is obvious",
    d: "Events, exhibitions, holidays and competitor prices around your hotel, on one calendar. Clefs flags the dates where demand is building and suggests what to review, with the sources it used.",
    features: [
      { t: "UAE events and holidays matched to your location" },
      { t: "Competitor prices for the same dates" },
      { t: "Rate suggestions you approve; Clefs never changes a rate itself" },
    ],
  },
  {
    id: "service-control", name: "Service Control", icon: LuConciergeBell, href: "/service-control",
    title: "Every guest request tracked to done",
    d: "Guests ask by QR code or the room phone. Simple questions are answered at once. Everything else becomes a job for the right team, with the room number, an owner and a time limit.",
    features: [
      { t: "Arabic and English guest assistant, by voice or text" },
      { t: "Requests routed to the right department, with a time limit" },
      { t: "Response times you can review every week" },
      { t: "Remembers your guests across stays", flag: "claims.guest_memory" },
    ],
  },
];

const STEPS = [
  { n: "I",   t: "Connect your systems", d: "Share your OTA statements, a reservations export and your rate calendar. Your PMS and channel manager stay exactly as they are." },
  { n: "II",  t: "Read the morning brief", d: "Each morning the owner gets a short brief: the money at risk, the evidence behind each item, and the action that fixes it." },
  { n: "III", t: "Approve", d: "One tap approves, adjusts or dismisses. Refunds, rate changes and OTA changes always wait for a person." },
  { n: "IV",  t: "See verified value", d: "After an action runs, Clefs checks the result against your records and reports only the value it could verify." },
];

/* Section anchors on this page, plus two routes. `route` entries navigate
   away, so they never become the active anchor. */
const NAV: { href: string; label: string; route?: boolean }[] = [
  { href: "#experience", label: "Overview" },
  { href: "#products", label: "Products" },
  { href: "#how", label: "How it works" },
  { href: "/security", label: "Security", route: true },
  { href: "#questions", label: "Questions" },
  { href: "/pilot", label: "Pilot", route: true },
];

/* Product facts only, no market numbers: what every hotel gets on day one.
   "0 apps" and "any language" were retired (Canon 22, 03 section 6). */
const METRICS = [
  { val: "1", label: "morning brief for the owner",
    note: "Money at risk first, in the order it expires." },
  { val: "Every", label: "alert with its evidence",
    note: "The statement line, the contract term and the amount, side by side." },
  { val: "Every", label: "action approved by you",
    note: "You decide what may run on its own. Refunds and rates always wait for you." },
  { val: "AR + EN", label: "guest assistant",
    note: "Arabic and English, plus six more languages tested for your hotel before go-live." },
];

const PROOF = [
  { icon: LuSearchCheck, t: "Evidence for every alert." },
  { icon: LuUserCheck, t: "Human approval for every action." },
  { icon: LuBadgeCheck, t: "Verified value after execution." },
];

/* Illustrative only, and deliberately without amounts: a mock brief with
   invented AED figures would read as a claim about the visitor's hotel. */
const BRIEF_ITEMS = [
  { tag: "Profit Guard", t: "Commission charged above contract on 3 stays", a: "Approve dispute" },
  { tag: "Profit Guard", t: "Expedia price below your direct price for 12 Oct", a: "Approve fix" },
  { tag: "Demand Radar", t: "Exhibition nearby, 14 to 16 Nov: demand building", a: "Review rates" },
  { tag: "Service", t: "2 guest requests passed their time limit overnight", a: "See jobs" },
];

const FOOTER: { h: string; links: [string, string][] }[] = [
  { h: "Products", links: [["/profit-guard", "Profit Guard"], ["/demand-radar", "Demand Radar"], ["/service-control", "Service Control"]] },
  { h: "Service Control", links: [
    ["/ai-concierge-software", "AI concierge software"],
    ["/guest-request-management", "Guest request management"],
    ["/hotel-front-desk-automation", "Front desk automation"],
    ["/multilingual-guest-messaging", "Multilingual messaging"],
  ] },
  { h: "Start", links: [[PRIMARY_LEAD.href, PRIMARY_LEAD.short], ["/pilot", "Pilot"], ["/security", "Security"]] },
];

/** claimDate: the Dubai date the server render judged dated claims by (see
 *  app/page.tsx); undefined only outside that page, where Claim reads today. */
export default function MarketingHomepage({ claimDate }: { claimDate?: string } = {}) {
  const [scrolled, setScrolled] = useState(false);
  const [activeNav, setActiveNav] = useState("#experience");
  const [menuOpen, setMenuOpen] = useState(false);
  const [heroAvatarState, setHeroAvatarState] = useState<"video" | "still">("still");
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 40);
    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  /* The greeting is play-on-request, never autoplay.
     Three reasons it stopped being automatic:
       1. Chrome and Safari block audio until the visitor has engaged with the
          domain, so for most first-time visitors it simply never played. The
          old fallback listened for ANY click on window, which meant the voice
          fired when someone clicked "Book a demo" - startling, and never the
          moment we wanted it heard.
       2. The new greeting runs past three seconds. Audio that autoplays for
          longer than that needs its own stop control to satisfy WCAG 1.4.2.
       3. A user gesture always permits playback, including on iOS. Asking for
          the tap is what actually gets the voice heard. */
  /* ── Greeting playback + avatar mouth gating ──────────────────────────────
     The mouth is driven by the LIVE RMS ENVELOPE of the greeting audio, never
     by the audio's duration. The avatar clip is 8.76s and the greeting is
     ~4.2s, so playing the video for "as long as the audio lasts" kept the mouth
     moving through the pause after "Namaste" and then froze it mid-shape when
     the audio ended. Gating on energy means the mouth stops within a frame of
     the last sample and holds still through pauses.

     `smoothingTimeConstant = 0` is required: the 0.8 default low-passes the
     envelope and adds ~150ms of decay before silence is detectable, which is
     exactly the lag being fixed here. Same approach as the concierge bot. */
  const greetingRef  = useRef<HTMLAudioElement | null>(null);
  const audioCtxRef  = useRef<AudioContext | null>(null);
  const analyserRef  = useRef<AnalyserNode | null>(null);
  const srcNodeRef   = useRef<MediaElementAudioSourceNode | null>(null);
  const rafRef       = useRef<number | null>(null);
  const mouthOpenRef = useRef(false);
  const [mouthOpen, setMouthOpen] = useState(false);
  const lastLoudRef  = useRef(0);

  const SILENCE_RMS = 0.015;  // below this the greeting is between words
  /* Only used by the live-envelope fallback below. A blind hangover cannot win
     here: this recording has a 400ms breath inside "Welcome ... to Clefs", so
     any value short enough to end the clip promptly also freezes the avatar
     mid-sentence. The timeline path exists precisely to escape that trade. */
  const HANGOVER_MS = 180;

  /* A silence shorter than this is a breath inside a phrase, not the end of one.
     Measured against this recording: the internal gaps are 32, 56, 144, 152 and
     400ms, and the one real pause is 1064ms, so anything from ~450ms to ~1s
     merges every phrase correctly while still holding that pause. */
  const BRIDGE_MS = 450;
  /* Lets the mouth settle a frame past the last sample instead of cutting on it. */
  const SEGMENT_TAIL_MS = 60;

  /* Speech spans of the greeting, in ms, with in-phrase gaps already merged.
     Null until decoded, and stays null if decoding is unavailable. */
  const segmentsRef = useRef<{ a: number; b: number }[] | null>(null);

  /* Why precompute at all, when the envelope is right there at playback time?
     Because a live envelope has no look-ahead. Faced with 400ms of silence it
     cannot tell "a breath, more words coming" from "the clip has ended", so it
     must guess with a hangover and will be wrong one way or the other. Decoding
     the file first turns that guess into a fact: gaps get bridged, and the mouth
     still stops on the last sample rather than running into the trailing
     silence. The bot cannot do this - it never knows what is coming - which is
     why it leans on the server's end-of-audio event instead. */
  const buildSegments = (buf: AudioBuffer) => {
    const ch = buf.getChannelData(0);
    const win = Math.max(64, Math.round(buf.sampleRate * 0.008));
    const hopMs = (win / buf.sampleRate) * 1000;
    const segs: { a: number; b: number }[] = [];
    const loud: boolean[] = [];
    for (let p = 0; p + win <= ch.length; p += win) {
      let s = 0;
      for (let i = 0; i < win; i++) { const v = ch[p + i]; s += v * v; }
      loud.push(Math.sqrt(s / win) > SILENCE_RMS);
    }
    for (let i = 0; i < loud.length; i++) {
      if (!loud[i]) continue;
      const a = i * hopMs;
      while (i + 1 < loud.length && loud[i + 1]) i++;
      const b = (i + 1) * hopMs;
      const last = segs[segs.length - 1];
      if (last && a - last.b < BRIDGE_MS) last.b = b;
      else segs.push({ a, b });
    }
    return segs;
  };

  const loadSegments = async (ctx: AudioContext) => {
    if (segmentsRef.current) return;
    try {
      const res = await fetch("/assistant_greeting.mp3");
      const buf = await ctx.decodeAudioData(await res.arrayBuffer());
      segmentsRef.current = buildSegments(buf);
    } catch {
      // Leave null: playback falls back to the live envelope.
    }
  };

  const setMouth = (open: boolean) => {
    const v = videoRef.current;
    if (!v || open === mouthOpenRef.current) return;
    mouthOpenRef.current = open;
    setMouthOpen(open);
    // Deliberately does NOT rewind. Resetting to frame 0 on every pause made
    // each phrase restart from the same pose, so the avatar visibly snapped back
    // between segments - read as "speaks, stops, speaks" far more than the pause
    // itself did. Resuming where it left off keeps the motion continuous.
    if (open) v.play().catch(() => {});
    else v.pause();
  };

  const stopGreeting = () => {
    if (rafRef.current !== null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    const a = greetingRef.current;
    if (a) { a.pause(); a.currentTime = 0; }
    const v = videoRef.current;
    // Reset to the first frame and hide with no fade, so the still portrait
    // underneath takes over instantly instead of a frozen open mouth dissolving.
    if (v) { v.pause(); v.currentTime = 0; }
    mouthOpenRef.current = false;
    setMouthOpen(false);
    setHeroAvatarState("still");
  };

  const trackEnvelope = () => {
    const an = analyserRef.current;
    const a = greetingRef.current;
    if (!a) return;
    const buf = an ? new Uint8Array(an.fftSize) : null;
    const loop = () => {
      const segs = segmentsRef.current;
      if (segs) {
        // Preferred path: the whole envelope is known, so the mouth follows the
        // phrase structure exactly and stops on the last sample.
        const t = a.currentTime * 1000;
        setMouth(segs.some((s) => t >= s.a && t < s.b + SEGMENT_TAIL_MS));
      } else if (an && buf) {
        an.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) { const d = (buf[i] - 128) / 128; sum += d * d; }
        const rms = Math.sqrt(sum / buf.length);
        const now = performance.now();
        if (rms > SILENCE_RMS) lastLoudRef.current = now;
        setMouth(now - lastLoudRef.current < HANGOVER_MS);
      }
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
  };

  const playGreeting = async () => {
    let a = greetingRef.current;
    if (!a) {
      a = new Audio("/assistant_greeting.mp3");
      a.preload = "auto";
      a.onended = stopGreeting;
      greetingRef.current = a;
    }

    // A MediaElementAudioSourceNode may be created only once per element, and
    // routing through WebAudio means we must also connect to the destination or
    // the greeting plays silently.
    if (!srcNodeRef.current) {
      try {
        const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        const ctx = new Ctx();
        const src = ctx.createMediaElementSource(a);
        const an = ctx.createAnalyser();
        an.fftSize = 1024;
        an.smoothingTimeConstant = 0;
        src.connect(an);
        an.connect(ctx.destination);
        audioCtxRef.current = ctx; srcNodeRef.current = src; analyserRef.current = an;
      } catch {
        // WebAudio unavailable: fall back to duration-based motion below.
      }
    }
    if (audioCtxRef.current?.state === "suspended") {
      try { await audioCtxRef.current.resume(); } catch {}
    }
    // Decoded once and cached. The file is ~25KB, and it is already in cache
    // from the <audio> preload, so this costs nothing on the second play.
    if (audioCtxRef.current) await loadSegments(audioCtxRef.current);

    a.currentTime = 0;
    try {
      await a.play();
    } catch {
      setHeroAvatarState("still");
      return false;
    }
    setHeroAvatarState("video");
    lastLoudRef.current = performance.now();
    // The timeline works without an analyser, so only fall back to duration-based
    // motion when neither is available.
    if (segmentsRef.current || analyserRef.current) trackEnvelope();
    else setMouth(true);   // last resort: mouth runs for the audio's duration
    return true;
  };

  /* Autoplay first, gesture second.
     Chrome and Safari reject audible autoplay until the visitor has engaged
     with the domain, so a plain play() call succeeds only for returning
     visitors. When it is refused we arm a one-shot gesture handler instead of
     giving up. The handler deliberately ignores clicks on links and buttons:
     the previous version fired on ANY window click, so the voice would start
     just as someone clicked "Book a demo" and left for Calendly.
     Once per tab session, so returning to the homepage is not a re-greeting. */
  useEffect(() => {
    let armed = true;
    if (sessionStorage.getItem("clefs-greeted") === "1") return;

    const markPlayed = () => { try { sessionStorage.setItem("clefs-greeted", "1"); } catch {} };

    const onGesture = (e: Event) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest("a, button")) return;   // let their own action happen first
      if (!armed) return;
      armed = false;
      disarm();
      void playGreeting().then((ok) => { if (ok) markPlayed(); });
    };
    const disarm = () => {
      window.removeEventListener("pointerdown", onGesture);
      window.removeEventListener("keydown", onGesture);
    };

    void playGreeting().then((ok) => {
      if (ok) { armed = false; markPlayed(); return; }
      window.addEventListener("pointerdown", onGesture, { passive: true });
      window.addEventListener("keydown", onGesture);
    });

    return () => { armed = false; disarm(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleGreeting = () => {
    if (heroAvatarState === "video") stopGreeting();
    else void playGreeting();
  };

  // Never let the voice keep talking into a hidden tab.
  useEffect(() => {
    const onHide = () => { if (document.hidden) stopGreeting(); };
    document.addEventListener("visibilitychange", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      greetingRef.current?.pause();
      void audioCtxRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    const mq = window.matchMedia("(min-width: 1001px)");
    const onWide = () => { if (mq.matches) setMenuOpen(false); };
    window.addEventListener("keydown", onKey);
    mq.addEventListener("change", onWide);
    return () => { window.removeEventListener("keydown", onKey); mq.removeEventListener("change", onWide); };
  }, [menuOpen]);

  /* Reveal on scroll.

     Two deliberate safeguards, because the failure mode of a scroll reveal is
     an invisible page - much worse than no animation at all:
       1. the markup ships at full opacity and is only dimmed once JS has
          confirmed it can un-dim it, so no-JS and prefers-reduced-motion both
          render a complete page;
       2. a hard timeout reveals everything regardless, so a browser that
          never delivers an intersection callback still shows the content. */
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const targets = Array.from(document.querySelectorAll<HTMLElement>("[data-reveal]"));
    targets.forEach((el) => el.classList.add("cb-reveal"));

    const show = (el: Element) => el.classList.add("cb-reveal--in");
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => {
        if (e.isIntersecting) { show(e.target); io.unobserve(e.target); }
      }),
      { rootMargin: "0px 0px -12% 0px", threshold: 0.06 }
    );
    targets.forEach((el) => io.observe(el));

    const failsafe = window.setTimeout(() => { targets.forEach(show); io.disconnect(); }, 4000);
    return () => { window.clearTimeout(failsafe); io.disconnect(); };
  }, []);

  return (
    <div className="cb clefs-ground">
      <style dangerouslySetInnerHTML={{ __html: brandStyles + mkStyles + CLAIM_STYLES }} />

      {/* ── Nav ── */}
      <nav className={`cb-nav ${scrolled ? "cb-nav--scrolled" : ""}`}>
        <div className="cb-nav__inner">
          <a href="#experience" className="cb-nav__brand" aria-label="Clefs home">
            <img src="/brand/clefs-key.webp" alt="" className="cb-nav__mark" width={520} height={1031} />
            <span className="cb-nav__word clefs-gilt">CLEFS</span>
          </a>
          <div className="cb-nav__links">
            {NAV.map((n) => n.route ? (
              <Link key={n.href} href={n.href} className="cb-nav__link">{n.label}</Link>
            ) : (
              <a key={n.href} href={n.href}
                 className={`cb-nav__link ${activeNav === n.href ? "cb-nav__link--active" : ""}`}
                 onClick={() => setActiveNav(n.href)}>{n.label}</a>
            ))}
          </div>
          <div className="cb-nav__end">
            <Link href={DEMO_URL} className="cb-btn cb-btn--gold cb-btn--sm" target="_blank" rel="noopener noreferrer">
              Book a demo
            </Link>
            <button
              type="button"
              className="cb-burger"
              aria-expanded={menuOpen}
              aria-controls="cb-menu"
              aria-label={menuOpen ? "Close menu" : "Open menu"}
              onClick={() => setMenuOpen((o) => !o)}
            >
              <span className={`cb-burger__box ${menuOpen ? "cb-burger__box--x" : ""}`} aria-hidden="true">
                <span /><span /><span />
              </span>
            </button>
          </div>
        </div>

        <div id="cb-menu" className={`cb-menu ${menuOpen ? "cb-menu--open" : ""}`} hidden={!menuOpen}>
          {NAV.map((n) => n.route ? (
            <Link key={n.href} href={n.href} className="cb-menu__link" onClick={() => setMenuOpen(false)}>{n.label}</Link>
          ) : (
            <a key={n.href} href={n.href} className="cb-menu__link"
               onClick={() => { setActiveNav(n.href); setMenuOpen(false); }}>{n.label}</a>
          ))}
        </div>
      </nav>

      {/* ── Hero ─────────────────────────────────────────────────────────────
          The mark is repeated at 1200px behind the copy as a watermark, the way
          a hotel embosses its monogram on stationery. It is the whole reason
          the page needs no stock lobby photograph. */}
      <header id="experience" className="cb-hero">
        <img src="/brand/clefs-key.webp" alt="" aria-hidden="true" className="cb-hero__watermark" fetchPriority="low" decoding="async" />

        <div className="cb-hero__inner clefs-above">
          <div className="cb-hero__copy">
            {/* Canon 22: the eyebrow says "hotels", not "independent hotels";
                a chain-property GM reads the same page and runs the same demo.
                The avatar stays as the brand's face, but the promise is now
                profit control: the owner is the buyer. */}
            <p className="cb-hero__eyebrow">
              <span className="cb-hero__eyebrow-rule" />
              For hotels in the UAE
            </p>
            <h1 className="cb-hero__title">
              Find the profit leaks your{" "}
              <em className="clefs-gilt clefs-gilt--live">hotel systems miss.</em>
            </h1>
            <p className="cb-hero__sub">
              Clefs audits OTA payouts and rate integrity, spots local demand before it
              becomes obvious, and sends your owner the exact actions to approve, every morning.
            </p>
            {/* Primary is the lead magnet (Canon 23): one statement is a small,
                concrete ask, where a demo is a meeting. */}
            <div className="cb-hero__btns">
              <Link href={PRIMARY_LEAD.href} className="cb-btn cb-btn--gold">
                <LuFileSearch size={17} aria-hidden="true" /> {PRIMARY_LEAD.label}
              </Link>
              <Link href="/demand-preview" className="cb-btn cb-btn--ghost">
                See the next 30 days of demand around my hotel
              </Link>
            </div>
          </div>

          <div className="cb-hero__stage">
            <div className="cb-hero__rings" aria-hidden="true">
              <span className="cb-hero__ring cb-hero__ring--1" />
              <span className="cb-hero__ring cb-hero__ring--2" />
            </div>
            <div className="cb-hero__portrait">
              <img src="/female.webp" alt="The Clefs AI concierge" className="cb-hero__portrait-img" fetchPriority="high" decoding="async" width={512} height={512} />
              <video
                ref={videoRef}
                src="/avataar_female_noaudio.mp4"
                poster="/female.webp"
                // No autoPlay: the greeting effect above drives playback via
                // videoRef.play() once heroAvatarState flips to "video", and the
                // initial state is "still". autoPlay only made browsers eagerly
                // fetch ~790KB up front, competing with the portrait for LCP.
                // The poster is the same still frame, so a brief buffer shows no seam.
                preload="metadata"
                muted
                playsInline
                loop={false}
                aria-hidden="true"
                className={`cb-hero__portrait-video ${mouthOpen ? "cb-hero__portrait-video--on" : ""}`}
              />
            </div>
            <button
              type="button"
              onClick={toggleGreeting}
              className={`cb-hero__badge cb-hero__badge--btn ${heroAvatarState === "video" ? "cb-hero__badge--on" : ""}`}
              aria-label={heroAvatarState === "video" ? "Stop the Clefs greeting" : "Hear the Clefs greeting"}
            >
              {heroAvatarState === "video"
                ? <LuSquare size={11} aria-hidden="true" className="cb-hero__badge-icon" />
                : <LuPlay size={11} aria-hidden="true" className="cb-hero__badge-icon" />}
              {heroAvatarState === "video" ? "Playing" : "Hear Clefs"}
            </button>
          </div>
        </div>

        <a href="#products" className="cb-hero__scroll" aria-label="Scroll to content">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="M7 10l5 5 5-5" /></svg>
        </a>
      </header>

      {/* ── Proof bar ── The three promises every product keeps. */}
      <section className="mk-proof" aria-label="How Clefs works with you">
        <ul className="mk-proof__list">
          {PROOF.map((p) => {
            const Icon = p.icon;
            return (
              <li key={p.t} className="mk-proof__item">
                <Icon size={18} aria-hidden="true" className="mk-proof__icon" />
                <span>{p.t}</span>
              </li>
            );
          })}
        </ul>
      </section>

      {/* ── Products ── */}
      <section id="products" className="cb-section">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">What Clefs does</p>
            <h2 className="cb-head__title">
              Three products,<br />
              <em className="clefs-gilt">one morning brief.</em>
            </h2>
            <p className="cb-head__desc">
              Each product finds something worth money or a guest&apos;s goodwill, shows you the
              evidence, and waits for your approval.
            </p>
          </header>

          <div className="mk-products">
            {PRODUCTS.map((p) => {
              const Icon = p.icon;
              // Canon 16: a flagged feature renders only once the product does it.
              const features = p.features.filter((f) => !f.flag || claimFlag(f.flag));
              return (
                <article key={p.id} id={p.id} className="mk-product" data-reveal>
                  <div className="mk-product__copy">
                    <p className="mk-product__name">
                      <span className="mk-product__icon" aria-hidden="true"><Icon size={18} /></span>
                      {p.name}
                    </p>
                    <h3 className="mk-product__title">{p.title}</h3>
                    <p className="mk-product__text">{p.d}</p>
                    <ul className="mk-product__list">
                      {features.map((f) => (
                        <li key={f.t}><LuCheck size={15} aria-hidden="true" /> {f.t}</li>
                      ))}
                    </ul>
                    <Link href={p.href} className="mk-product__link">
                      More about {p.name} <LuArrowRight size={15} aria-hidden="true" />
                    </Link>
                  </div>
                  <div className="mk-product__aside">{productAside(p.id, claimDate)}</div>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── How it works ── The main visual is the owner's brief, not the
          avatar: the owner is the reader who decides. */}
      <section id="how" className="cb-section cb-section--alt">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">How it works</p>
            <h2 className="cb-head__title">Connect. Read. Approve. <em className="clefs-gilt">Verify.</em></h2>
            <p className="cb-head__desc">Four steps, and the owner stays in charge of every one.</p>
          </header>

          <div className="cb-showcase">
            <ol className="cb-steps">
              {STEPS.map((s, i) => (
                <li key={s.n} className="cb-step" data-reveal style={{ transitionDelay: `${i * 90}ms` }}>
                  <span className="cb-step__num clefs-gilt" aria-hidden="true">{s.n}</span>
                  <div>
                    <h3 className="cb-step__title">{s.t}</h3>
                    <p className="cb-step__desc">{s.d}</p>
                  </div>
                </li>
              ))}
            </ol>

            <figure className="mk-brief" data-reveal aria-label="Example of an owner brief">
              <div className="mk-brief__head">
                <span className="mk-brief__title">Owner brief</span>
                <span className="mk-brief__when">Example, 07:00</span>
              </div>
              <ul className="mk-brief__items">
                {BRIEF_ITEMS.map((b) => (
                  <li key={b.t} className="mk-brief__item">
                    <span className="mk-brief__tag">{b.tag}</span>
                    <span className="mk-brief__text">{b.t}</span>
                    <span className="mk-brief__meta">Evidence attached</span>
                    <span className="mk-brief__act">{b.a}</span>
                  </li>
                ))}
              </ul>
              <figcaption className="mk-brief__foot">
                Illustration. Your brief shows your own items and amounts.
              </figcaption>
            </figure>
          </div>
        </div>
      </section>

      {/* ── Facts ── Product facts, then two calculators that run on the
          visitor's own numbers. No market statistic appears here. */}
      <section id="facts" className="cb-section">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">What you get</p>
            <h2 className="cb-head__title">What changes for your hotel</h2>
            <p className="cb-head__desc">The same four things on day one, for every hotel.</p>
          </header>
          <div className="cb-metrics">
            {METRICS.map((m, i) => (
              <div key={m.label} className="cb-metric" data-reveal style={{ transitionDelay: `${i * 70}ms` }}>
                <div className="cb-metric__val clefs-gilt">{m.val}</div>
                <div className="cb-metric__label">{m.label}</div>
                <p className="cb-metric__note">{m.note}</p>
              </div>
            ))}
          </div>

          <div className="mk-calc">
            <h3 className="mk-calc__title">What is one point of OTA leakage worth to you?</h3>
            <p className="mk-calc__desc">Type two numbers from your own statements and contract.</p>
            <OtaLeakageCalculator />
          </div>

          <div className="mk-calc">
            <h3 className="mk-calc__title">How many hours does your desk spend on the phone?</h3>
            <p className="mk-calc__desc">Service Control answers the repeat questions. Work out what they cost you.</p>
            <DeskHoursCalculator />
          </div>
        </div>
      </section>

      {/* ── Questions ───────────────────────────────────────────────────────
          Rendered from HOME_FAQS, the same array that feeds the FAQPage schema
          in page.tsx. Google requires marked-up FAQ content to be visible on
          the page; keeping one source is what guarantees it stays that way.
          <details> is deliberate: the answers are in the HTML for crawlers with
          no JS, and the accordion still works with scripting disabled. */}
      <section id="questions" className="cb-section cb-section--alt">
        <div className="cb-container cb-container--narrow">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">Questions</p>
            <h2 className="cb-head__title">
              What hotel owners <em className="clefs-gilt">ask us</em>
            </h2>
          </header>
          <div className="cb-faq" data-reveal>
            {HOME_FAQS.map((f) => (
              <details key={f.q} className="cb-faq__item" name="clefs-faq">
                <summary className="cb-faq__q">
                  <span>{f.q}</span>
                  <LuPlus className="cb-faq__icon" size={17} aria-hidden="true" />
                </summary>
                <div className="cb-faq__a"><p>{f.a}</p></div>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA ── Primary is the audit (Canon 23); the demo is the fallback. */}
      <section className="cb-cta">
        <div className="cb-cta__inner" data-reveal>
          <img src="/brand/clefs-key.webp" alt="" aria-hidden="true" className="cb-cta__mark" loading="lazy" decoding="async" />
          {/* The copy follows the lead magnet that exists (OTA_AUDIT_LIVE in
              lib/seo.ts): the audit once Profit Guard ships it, the Google
              check until then, so the promise matches the page it opens. */}
          {OTA_AUDIT_LIVE ? (
            <>
              <h2 className="cb-cta__title">
                Start with{" "}
                <span className="cb-nowrap"><em className="clefs-gilt">one</em> OTA statement.</span>
              </h2>
              <p className="cb-cta__desc">
                Upload one statement. Clefs checks every line and shows you what looks wrong, with
                the calculation. Lines that depend on your contract are marked for you to confirm.
                There is no cost and nothing to install.
              </p>
            </>
          ) : (
            <>
              <h2 className="cb-cta__title">
                Start with{" "}
                <span className="cb-nowrap"><em className="clefs-gilt">one</em> Google check.</span>
              </h2>
              <p className="cb-cta__desc">
                See whether your official site price shows on Google Hotels next to the OTAs, and
                whether the prices match. There is no cost and nothing to install.
              </p>
            </>
          )}
          <div className="mk-cta__btns">
            <Link href={PRIMARY_LEAD.href} className="cb-btn cb-btn--gold cb-btn--lg">
              {PRIMARY_LEAD.label} <LuArrowRight size={17} aria-hidden="true" />
            </Link>
            <a href={DEMO_URL} className="cb-btn cb-btn--ghost cb-btn--lg" target="_blank" rel="noopener noreferrer">
              <LuMic size={17} aria-hidden="true" /> Book a demo
            </a>
          </div>
        </div>
      </section>

      {/* ── Footer ── The four older SEO pages keep their URLs (ranking
          equity) and are grouped under Service Control. */}
      <footer className="cb-footer">
        <div className="cb-footer__inner">
          <div className="cb-footer__brand">
            <img src="/brand/clefs-key.webp" alt="" className="cb-footer__mark" loading="lazy" />
            <div>
              <div className="cb-footer__word clefs-gilt">CLEFS</div>
              <div className="cb-footer__tag">{BRAND.tagline}</div>
            </div>
          </div>
          <nav className="mk-footer__groups" aria-label="Footer">
            {FOOTER.map((g) => (
              <div key={g.h} className="mk-footer__group">
                <p className="mk-footer__h">{g.h}</p>
                {g.links.map(([href, label]) => <Link key={href} href={href}>{label}</Link>)}
                {g.h === "Start" && (
                  <a href={DEMO_URL} target="_blank" rel="noopener noreferrer">Book a demo</a>
                )}
                {/* Opt-in channel: rendered only when NEXT_PUBLIC_WHATSAPP_NUMBER is set (lib/seo.ts). */}
                {g.h === "Start" && BRAND.whatsappUrl && (
                  <a href={BRAND.whatsappUrl} target="_blank" rel="noopener noreferrer">WhatsApp</a>
                )}
              </div>
            ))}
          </nav>
          <p className="cb-footer__copy">© {new Date().getFullYear()} Clefs<br />Made for hotels in the UAE.</p>
        </div>
      </footer>
    </div>
  );
}

/* The right-hand column of each product block. Profit Guard carries the only
   market numbers on the page, and each one renders through <Claim> so its
   source, population and expiry travel with it (Canon 21). Service Control
   keeps the guest phone and job ticket demo that used to be the main visual. */
function productAside(id: string, claimDate?: string) {
  if (id === "profit-guard") {
    return (
      <div className="mk-context" data-reveal>
        <p className="mk-context__h">Why it matters now</p>
        <Claim id="jll_uae_revpar_ytd_jun_2026" variant="stat" today={claimDate} />
        <Claim id="cloudbeds_2025_ota_direct_share" variant="stat" today={claimDate} />
        <Claim id="cloudbeds_2025_cancellations" variant="stat" today={claimDate} />
        <p className="mk-context__note">
          When revenue falls, every payout error matters more. Clefs checks yours, line by line.
        </p>
      </div>
    );
  }
  if (id === "demand-radar") {
    return (
      <figure className="mk-radar" data-reveal aria-label="Example of demand signals">
        {[
          ["Exhibition at a nearby venue", "Demand building", "high"],
          ["Public holiday weekend", "Watch pickup", "mid"],
          ["Competitor prices rising for your dates", "Review rates", "mid"],
          ["Quiet midweek, no events found", "Hold rates", "low"],
        ].map(([t, a, lvl]) => (
          <div key={t} className={`mk-radar__row mk-radar__row--${lvl}`}>
            <span className="mk-radar__dot" aria-hidden="true" />
            <span className="mk-radar__t">{t}</span>
            <span className="mk-radar__a">{a}</span>
          </div>
        ))}
        <figcaption className="mk-radar__cap">Illustration. Each signal links to its source.</figcaption>
      </figure>
    );
  }
  return (
    <div className="cb-stage" data-reveal>
     <div className="cb-stage__inner">
      <div className="cb-phone">
        <div className="cb-phone__bar">
          <img src="/brand/clefs-key.webp" alt="" className="cb-phone__mark" />
          <span className="cb-phone__name">Clefs</span>
          <span className="cb-phone__status"><span className="cb-phone__dot" />Listening</span>
        </div>
        <div className="cb-phone__body">
          <div className="cb-phone__glow" aria-hidden="true" />
          <div className="cb-phone__avatar">
            <img src="/female.webp" alt="" loading="lazy" decoding="async" />
          </div>
          <p className="cb-phone__guest">&ldquo;Can we get extra towels?&rdquo;</p>
          <p className="cb-phone__reply">&ldquo;Of course. Housekeeping has been notified. Maria is on her way.&rdquo;</p>
        </div>
        <div className="cb-phone__foot">
          <span className="cb-phone__mic"><LuMic size={15} /></span>
        </div>
      </div>

      <div className="cb-ticket">
        <div className="cb-ticket__head">
          <span>Job sent to staff</span>
          <span className="cb-ticket__pill">Sent</span>
        </div>
        <dl className="cb-ticket__rows">
          {[["Guest asked for", "Extra towels (2)"], ["Room", "204"], ["Staff member", "Maria · Housekeeping"], ["Time taken", "00:41"]].map(([k, v]) => (
            <div key={k} className="cb-ticket__row">
              <dt>{k}</dt><dd>{v}</dd>
            </div>
          ))}
        </dl>
      </div>
     </div>
    </div>
  );
}

const brandStyles = `

  .cb-hero__badge--btn {
    border: 1px solid var(--clefs-line); background: var(--clefs-surface);
    font-family: var(--clefs-sans); cursor: pointer;
    transition: border-color .3s var(--ease-out), color .3s var(--ease-out),
                background .3s var(--ease-out);
  }
  .cb-hero__badge--btn:hover { border-color: var(--clefs-line-gold); color: var(--clefs-gold-400); }
  .cb-hero__badge--on { border-color: var(--clefs-line-gold); color: var(--clefs-gold-400); }
  .cb-hero__badge-icon { flex: none; }

  /* ── FAQ ── */
  .cb-container--narrow { max-width: 820px; }
  .cb-faq { border-top: 1px solid var(--clefs-line-soft); }
  .cb-faq__item { border-bottom: 1px solid var(--clefs-line-soft); }
  .cb-faq__q {
    display: flex; align-items: center; justify-content: space-between; gap: 24px;
    padding: 22px 2px; cursor: pointer; list-style: none;
    font-family: var(--clefs-serif); font-size: 1.12rem; line-height: 1.45;
    color: var(--clefs-ink); transition: color .2s ease;
  }
  .cb-faq__q::-webkit-details-marker { display: none; }
  .cb-faq__q:hover { color: var(--clefs-gold-400); }
  .cb-faq__icon { flex: none; color: var(--clefs-gold-600); transition: transform .25s ease; }
  .cb-faq__item[open] .cb-faq__icon { transform: rotate(45deg); }
  .cb-faq__a {
    padding: 0 2px 24px; max-width: 68ch;
    color: var(--clefs-ink-muted); font-size: 0.98rem; line-height: 1.78;
  }
  .cb-faq__a p { margin: 0; }
  @media (prefers-reduced-motion: reduce) { .cb-faq__icon { transition: none; } }

  .cb {
    color: var(--clefs-ink);
    font-family: var(--clefs-sans);
    min-height: 100vh;
    overflow-x: clip;
  }
  .cb h1, .cb h2, .cb h3, .cb .cb-step__num, .cb-metric__val, .cb-nav__word, .cb-footer__word {
    font-family: var(--clefs-serif);
    font-weight: 500;           /* luxury serif reads lighter than 700 */
  }
  .cb em { font-style: italic; }

  /* Reveal. The class is added by JS, so no-JS keeps everything visible. */
  .cb-reveal { opacity: 0; transform: translateY(22px); }
  .cb-reveal--in {
    opacity: 1; transform: none;
    transition: opacity .9s var(--ease-out), transform .9s var(--ease-out);
  }
  @media (prefers-reduced-motion: reduce) {
    .cb-reveal { opacity: 1; transform: none; }
  }

  /* ══ NAV ══ */
  .cb-nav {
    position: fixed; inset: 0 0 auto 0; z-index: 100;
    padding: 22px 0;
    transition: padding .45s var(--ease-out), background .45s var(--ease-out),
                border-color .45s var(--ease-out), backdrop-filter .45s var(--ease-out);
    border-bottom: 1px solid transparent;
  }
  .cb-nav--scrolled {
    padding: 11px 0;
    background: rgba(7,12,20,0.82);
    backdrop-filter: blur(22px) saturate(1.4);
    -webkit-backdrop-filter: blur(22px) saturate(1.4);
    border-bottom-color: var(--clefs-line);
  }
  .cb-nav__inner {
    max-width: 1240px; margin: 0 auto; padding: 0 clamp(20px, 5vw, 48px);
    display: flex; align-items: center; justify-content: space-between; gap: 24px;
  }
  .cb-nav__brand { display: flex; align-items: center; gap: 12px; text-decoration: none; }
  .cb-nav__mark { height: 34px; width: auto; display: block; }
  .cb-nav__word { font-size: 1.3rem; letter-spacing: 0.16em; text-indent: 0.16em; line-height: 1; }
  .cb-nav__links { display: flex; gap: 34px; }
  .cb-nav__link {
    position: relative; text-decoration: none;
    color: var(--clefs-ink-muted); font-size: 0.82rem; font-weight: 500;
    letter-spacing: 0.04em; padding-bottom: 5px;
    transition: color .3s var(--ease-out);
  }
  .cb-nav__link::after {
    content: ''; position: absolute; left: 0; right: 100%; bottom: 0; height: 1px;
    background: var(--clefs-gold-500); transition: right .4s var(--ease-out);
  }
  .cb-nav__link:hover { color: var(--clefs-ink); }
  .cb-nav__link:hover::after, .cb-nav__link--active::after { right: 0; }
  .cb-nav__link--active { color: var(--clefs-gold-500); }
  .cb-nav__end { display: flex; align-items: center; gap: 12px; }

  /* Burger. Hidden on desktop, where the inline links do the job. */
  .cb-burger {
    display: none; align-items: center; justify-content: center;
    width: 42px; height: 42px; padding: 0; cursor: pointer;
    background: transparent; border: 1px solid rgba(255,255,255,0.14); border-radius: 3px;
    color: var(--clefs-ink);
    transition: border-color .3s var(--ease-out);
  }
  .cb-burger:hover { border-color: var(--clefs-line-gold); }
  .cb-burger__box { position: relative; width: 18px; height: 12px; display: block; }
  .cb-burger__box span {
    position: absolute; left: 0; width: 100%; height: 1.5px; background: currentColor;
    transition: transform .35s var(--ease-out), opacity .2s linear, top .35s var(--ease-out);
  }
  .cb-burger__box span:nth-child(1) { top: 0; }
  .cb-burger__box span:nth-child(2) { top: 5px; }
  .cb-burger__box span:nth-child(3) { top: 10px; }
  .cb-burger__box--x span:nth-child(1) { top: 5px; transform: rotate(45deg); }
  .cb-burger__box--x span:nth-child(2) { opacity: 0; }
  .cb-burger__box--x span:nth-child(3) { top: 5px; transform: rotate(-45deg); }

  .cb-menu {
    display: none; flex-direction: column;
    margin-top: 11px; padding: 6px clamp(20px, 5vw, 48px) 14px;
    background: rgba(7,12,20,0.96);
    backdrop-filter: blur(22px); -webkit-backdrop-filter: blur(22px);
    border-top: 1px solid var(--clefs-line-soft);
  }
  .cb-menu__link {
    padding: 15px 0; text-decoration: none; font-size: 0.95rem;
    color: var(--clefs-ink-muted); border-bottom: 1px solid var(--clefs-line-soft);
  }
  .cb-menu__link:last-child { border-bottom: 0; }
  .cb-menu__link:active { color: var(--clefs-gold-500); }

  /* ══ HERO ══ */
  .cb-hero {
    position: relative;
    min-height: 100svh;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    padding: 150px clamp(20px, 5vw, 48px) 110px;
    overflow: hidden;
  }
  /* The mark blown up past the top and bottom edges, the way a hotel embosses
     its monogram on stationery. Deliberately visible rather than a whisper -
     at 4% it just read as dirt on the stone. The vertical fade is what keeps
     the crop from looking like a mistake, and the portrait sitting in front of
     it is what gives the hero depth. */
  .cb-hero__watermark {
    position: absolute; z-index: 0; pointer-events: none;
    right: -3vw; top: 50%; transform: translateY(-50%);
    height: 168vh; width: auto;
    opacity: 0.085;
    mask-image: linear-gradient(180deg, transparent 4%, #000 26%, #000 68%, transparent 94%);
    -webkit-mask-image: linear-gradient(180deg, transparent 4%, #000 26%, #000 68%, transparent 94%);
  }
  .cb-hero__inner {
    width: 100%; max-width: 1240px;
    display: grid; grid-template-columns: 1.08fr 0.92fr;
    gap: clamp(32px, 5vw, 72px); align-items: center;
  }
  .cb-hero__eyebrow {
    display: flex; align-items: center; gap: 14px;
    font-family: var(--clefs-sans);
    font-size: 0.74rem; font-weight: 600; letter-spacing: 0.24em; text-transform: uppercase;
    color: var(--clefs-gold-600); margin-bottom: 26px;
  }
  .cb-hero__eyebrow-rule { width: 46px; height: 1px; background: linear-gradient(90deg, transparent, var(--clefs-gold-600)); }
  /* Sized down from the old two-word h1: the promise is now a full sentence
     and must hold at three lines on a phone. */
  .cb-hero__title {
    font-size: clamp(2.3rem, 4.8vw, 3.8rem);
    line-height: 1.08; letter-spacing: -0.015em;
    margin: 0 0 26px;
  }
  .cb-hero__sub {
    font-size: clamp(1rem, 1.2vw, 1.12rem); line-height: 1.75;
    color: var(--clefs-ink-muted); max-width: 33em; margin: 0 0 38px;
  }
  .cb-hero__btns { display: flex; gap: 14px; flex-wrap: wrap; }

  /* Hero portrait */
  .cb-hero__stage {
    position: relative;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
  }
  .cb-hero__rings { position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none; }
  .cb-hero__ring {
    position: absolute; border-radius: 50%;
    width: clamp(250px, 33vw, 380px); height: clamp(250px, 33vw, 380px);
    border: 1px dashed rgba(225,175,104,0.16);
    animation: cbSpin 34s linear infinite;
  }
  .cb-hero__ring--2 {
    width: clamp(300px, 39vw, 450px); height: clamp(300px, 39vw, 450px);
    border-style: solid; border-color: rgba(255,255,255,0.05);
    animation-duration: 48s; animation-direction: reverse;
  }
  .cb-hero__portrait {
    position: relative; overflow: hidden; border-radius: 50%;
    width: clamp(210px, 27vw, 320px); height: clamp(210px, 27vw, 320px);
    border: 1px solid var(--clefs-line-gold);
    box-shadow: 0 0 0 7px rgba(225,175,104,0.05), 0 40px 90px -30px rgba(0,0,0,0.9),
                0 0 70px -18px rgba(225,175,104,0.32);
    background: var(--clefs-slate-800);
    animation: cbFloat 7s ease-in-out infinite;
  }
  .cb-hero__portrait-img, .cb-hero__portrait-video {
    position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover;
  }
  /* Fades out over 150ms rather than cutting. The cut was there to stop a frozen
     open mouth dissolving on screen, but that was a symptom of the mouth
     stopping late; now that it stops on the last sample, a short dissolve to the
     closed-mouth still reads as the mouth closing, which is what it should be.
     In fast so a phrase starts crisply, out gently so it settles. */
  .cb-hero__portrait-video { opacity: 0; transition: opacity .15s ease; pointer-events: none; }
  .cb-hero__portrait-video--on { opacity: 1; transition: opacity .09s ease; }
  .cb-hero__badge {
    position: relative; margin: 30px 0 0;
    display: inline-flex; align-items: center; gap: 9px;
    padding: 8px 18px; border-radius: 100px;
    background: rgba(7,12,20,0.6);
    border: 1px solid var(--clefs-line);
    backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--clefs-gold-500);
  }
  .cb-hero__scroll {
    position: relative; margin-top: 64px;
    color: var(--clefs-ink-faint); animation: cbBounce 2.6s ease-in-out infinite;
  }
  .cb-hero__scroll:hover { color: var(--clefs-gold-500); }

  /* ══ SECTIONS ══ */
  .cb-section { position: relative; z-index: 1; padding: clamp(88px, 11vw, 150px) 0; }
  .cb-section--alt {
    background: linear-gradient(180deg, transparent, rgba(255,255,255,0.018) 12%, rgba(255,255,255,0.018) 88%, transparent);
    border-top: 1px solid var(--clefs-line-soft);
    border-bottom: 1px solid var(--clefs-line-soft);
  }
  .cb-container { max-width: 1140px; margin: 0 auto; padding: 0 clamp(20px, 5vw, 48px); }
  .cb-head { text-align: center; max-width: 980px; margin: 0 auto clamp(48px, 6vw, 84px); }
  .cb-head .clefs-eyebrow { display: block; margin-bottom: 18px; }
  .cb-head__title {
    font-size: clamp(1.9rem, 3.7vw, 3rem); line-height: 1.16; letter-spacing: -0.01em; margin: 0;
  }
  .cb-head__desc {
    font-size: 1rem; line-height: 1.75; color: var(--clefs-ink-muted);
    margin: 22px auto 0; max-width: 52ch;
  }

  /* ══ PRODUCTS, PROOF BAR, BRIEF, CALCULATORS ══ (see mkStyles) */

  /* ══ HOW IT WORKS ══ */
  .cb-showcase {
    display: grid; grid-template-columns: 1fr 1fr; gap: clamp(36px, 6vw, 76px); align-items: center;
  }
  .cb-steps { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
  .cb-step {
    display: grid; grid-template-columns: 62px 1fr; gap: 20px; align-items: start;
    padding: 26px 0; border-bottom: 1px solid var(--clefs-line-soft);
  }
  .cb-step:last-child { border-bottom: 0; }
  .cb-step__num { font-size: 2rem; line-height: 1; letter-spacing: 0.04em; }
  .cb-step__title { font-size: 1.2rem; margin: 0 0 9px; }
  .cb-step__desc { font-size: 0.92rem; line-height: 1.72; color: var(--clefs-ink-muted); margin: 0; }

  .cb-stage { position: relative; display: grid; place-items: center; min-height: 540px; }
  /* Exactly the phone's outer width, so the ticket's overlap is fixed in px
     rather than a function of however wide the column happens to be. */
  .cb-stage__inner { position: relative; width: 278px; }
  .cb-phone {
    position: relative; z-index: 2;
    width: 262px; height: 470px; display: flex; flex-direction: column; overflow: hidden;
    border-radius: 38px; border: 8px solid #131A26;
    background: var(--clefs-slate-950);
    box-shadow: 0 50px 90px -30px rgba(0,0,0,0.95), 0 0 0 1px rgba(255,255,255,0.06);
  }
  .cb-phone__bar {
    display: flex; align-items: center; gap: 8px; padding: 15px 14px;
    background: rgba(15,23,32,0.9); border-bottom: 1px solid var(--clefs-line-soft);
  }
  .cb-phone__mark { height: 20px; width: auto; }
  .cb-phone__name { font-family: var(--clefs-serif); font-size: 0.9rem; color: var(--clefs-gold-500); letter-spacing: 0.06em; }
  .cb-phone__status { margin-left: auto; display: flex; align-items: center; gap: 5px; font-size: 0.58rem; letter-spacing: 0.1em; text-transform: uppercase; color: var(--hc-success); }
  .cb-phone__dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
  .cb-phone__body {
    flex: 1; position: relative; overflow: hidden;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 14px; padding: 22px 18px; text-align: center;
    background: radial-gradient(circle at 50% 26%, rgba(225,175,104,0.09), transparent 62%);
  }
  .cb-phone__glow {
    position: absolute; top: 52px; width: 104px; height: 104px; border-radius: 50%;
    border: 1px solid rgba(225,175,104,0.28); animation: cbRipple 2.6s ease-out infinite;
  }
  .cb-phone__avatar {
    position: relative; width: 88px; height: 88px; border-radius: 50%; overflow: hidden;
    border: 1px solid var(--clefs-line-gold); box-shadow: 0 0 24px -4px rgba(225,175,104,0.4);
    animation: cbFloat 5s ease-in-out infinite;
  }
  .cb-phone__avatar img { width: 100%; height: 100%; object-fit: cover; }
  .cb-phone__guest { font-size: 0.7rem; font-style: italic; color: var(--clefs-ink-faint); margin: 6px 0 0; }
  .cb-phone__reply { font-size: 0.76rem; line-height: 1.55; color: var(--clefs-gold-400); margin: 0; }
  .cb-phone__foot {
    display: grid; place-items: center; padding: 15px;
    background: rgba(15,23,32,0.85); border-top: 1px solid var(--clefs-line-soft);
  }
  .cb-phone__mic {
    display: grid; place-items: center; width: 38px; height: 38px; border-radius: 50%;
    background: linear-gradient(160deg, var(--clefs-gold-300), var(--clefs-gold-500));
    color: var(--clefs-ink-dark); box-shadow: 0 0 18px -3px rgba(225,175,104,0.55);
  }

  .cb-ticket {
    position: absolute; z-index: 3; left: 166px; bottom: -12px; width: 254px;
    padding: 17px 18px; border-radius: 14px;
    background: rgba(11,18,27,0.82);
    border: 1px solid var(--clefs-line);
    backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
    box-shadow: var(--clefs-shadow-3);
    animation: cbFloat 6s ease-in-out infinite .8s;
  }
  .cb-ticket__head {
    display: flex; align-items: center; justify-content: space-between; margin-bottom: 13px;
    font-size: 0.62rem; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--clefs-gold-500);
  }
  .cb-ticket__pill {
    padding: 3px 8px; border-radius: 4px; letter-spacing: 0.08em;
    background: rgba(52,199,123,0.12); color: var(--hc-success); border: 1px solid rgba(52,199,123,0.22);
  }
  .cb-ticket__rows { margin: 0; display: flex; flex-direction: column; gap: 8px; }
  .cb-ticket__row { display: flex; justify-content: space-between; gap: 12px; font-size: 0.72rem; }
  .cb-ticket__row dt { color: var(--clefs-ink-faint); margin: 0; }
  .cb-ticket__row dd { color: var(--clefs-ink); margin: 0; font-weight: 500; }

  /* ══ METRICS ══ */
  .cb-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: clamp(12px, 2vw, 22px); }
  .cb-metric {
    text-align: center; padding: clamp(34px, 4vw, 52px) 18px;
    border: 1px solid var(--clefs-line-soft); border-radius: 4px;
    background: var(--clefs-surface);
    transition: border-color .45s var(--ease-out), background .45s var(--ease-out), transform .45s var(--ease-out);
  }
  .cb-metric:hover { border-color: var(--clefs-line); background: var(--clefs-gold-wash); transform: translateY(-3px); }
  .cb-metric__val { font-size: clamp(2.1rem, 3.6vw, 3rem); line-height: 1; margin-bottom: 12px; }
  .cb-metric__label { font-size: 0.8rem; color: var(--clefs-ink-muted); letter-spacing: 0.03em; }
  .cb-metric__note {
    margin: 12px auto 0; font-size: 0.83rem; line-height: 1.62;
    color: var(--clefs-ink-faint); max-width: 27ch;
  }

  /* ══ CTA ══ */
  .cb-cta {
    position: relative; z-index: 1; text-align: center;
    padding: clamp(88px, 11vw, 150px) clamp(20px, 5vw, 48px);
    border-top: 1px solid var(--clefs-line-soft);
    background: radial-gradient(70% 130% at 50% 0%, rgba(225,175,104,0.09), transparent 62%);
  }
  .cb-cta__inner { max-width: 660px; margin: 0 auto; }
  .cb-nowrap { white-space: nowrap; }
  .cb-cta__mark { height: 92px; width: auto; margin-bottom: 30px; opacity: .95; }
  .cb-cta__title { font-size: clamp(1.9rem, 3.7vw, 3rem); line-height: 1.16; margin: 0 0 20px; }
  .cb-cta__desc { font-size: 1rem; line-height: 1.75; color: var(--clefs-ink-muted); margin: 0 auto 40px; max-width: 46ch; }

  /* ══ FOOTER ══ */
  .cb-footer {
    position: relative; z-index: 1;
    border-top: 1px solid var(--clefs-line-soft);
    background: rgba(3,6,12,0.7);
    padding: 54px clamp(20px, 5vw, 48px);
  }
  .cb-footer__inner {
    max-width: 1140px; margin: 0 auto;
    display: flex; align-items: center; justify-content: space-between; gap: 30px; flex-wrap: wrap;
  }
  .cb-footer__brand { display: flex; align-items: center; gap: 14px; }
  .cb-footer__mark { height: 46px; width: auto; }
  .cb-footer__word { font-size: 1.2rem; letter-spacing: 0.16em; text-indent: 0.16em; line-height: 1.2; }
  .cb-footer__tag { font-size: 0.68rem; letter-spacing: 0.14em; text-transform: uppercase; color: var(--clefs-ink-faint); margin-top: 5px; }
  .cb-footer__links { display: flex; gap: 26px; flex-wrap: wrap; }
  .cb-footer__links a {
    font-size: 0.82rem; color: var(--clefs-ink-faint); text-decoration: none;
    transition: color .3s var(--ease-out);
  }
  .cb-footer__links a:hover { color: var(--clefs-gold-500); }
  .cb-footer__copy { font-size: 0.74rem; line-height: 1.7; color: rgba(242,238,230,0.22); text-align: right; margin: 0; }

  /* ══ ANIMATION ══ */
  @keyframes cbSpin   { to { transform: rotate(360deg); } }
  @keyframes cbFloat  { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-10px); } }
  @keyframes cbBounce { 0%,100% { transform: translateY(0); } 50% { transform: translateY(7px); } }
    100% { box-shadow: 0 0 0 0 rgba(52,199,123,0); }
  }
  @keyframes cbRipple { 0% { transform: scale(.94); opacity: 1; } 100% { transform: scale(1.5); opacity: 0; } }

  /* ══ RESPONSIVE ══ */
  @media (max-width: 1000px) {
    .cb-nav__links { display: none; }
    .cb-burger { display: inline-flex; }
    .cb-menu--open { display: flex; }
    /* The bar keeps its glass background once the sheet is open, otherwise the
       links sit over the hero with nothing behind them. */
    .cb-nav:has(.cb-menu--open) {
      background: rgba(7,12,20,0.9);
      backdrop-filter: blur(22px); -webkit-backdrop-filter: blur(22px);
      border-bottom-color: var(--clefs-line);
    }
    .cb-hero { padding-top: 128px; }
    .cb-hero__inner { grid-template-columns: 1fr; gap: 52px; text-align: center; }
    .cb-hero__copy { display: flex; flex-direction: column; align-items: center; }
    .cb-hero__eyebrow { justify-content: center; }
    .cb-hero__btns { justify-content: center; }
    .cb-hero__stage { order: -1; }
    .cb-hero__watermark { right: 50%; transform: translate(50%, -50%); opacity: .035; }
    .cb-showcase { grid-template-columns: 1fr; }
    .cb-stage { min-height: 0; }
    .cb-stage__inner { width: auto; display: flex; flex-direction: column; align-items: center; gap: 24px; }
    .cb-ticket { position: static; width: min(300px, 92vw); animation: none; }
    .cb-metrics { grid-template-columns: repeat(2, 1fr); }
    .cb-footer__inner { flex-direction: column; text-align: center; }
    .cb-footer__copy { text-align: center; }
  }
  @media (max-width: 620px) {
    .cb-metrics { grid-template-columns: 1fr; gap: 10px; }
    .cb-metric {
      display: grid; grid-template-columns: auto 1fr; align-items: baseline;
      gap: 4px 18px; text-align: left; padding: 20px 20px;
    }
    .cb-metric__val { margin-bottom: 0; font-size: 1.9rem; grid-row: span 2; }
    .cb-metric__note { margin: 4px 0 0; max-width: none; }
    .cb-hero__btns { flex-direction: column; width: 100%; }
    .cb-hero__btns .cb-btn { width: 100%; }
    .cb-metrics { grid-template-columns: 1fr; }
  }
`;

/* Styles for the owner-autopilot sections (products, proof bar, brief card,
   calculators, grouped footer). Kept apart from brandStyles so the hero and
   avatar CSS above stays untouched by this rewrite. */
const mkStyles = `
  /* The hero grid column must be allowed to shrink below its content: the
     secondary CTA is a sentence, and globals.css keeps .cb-btn on one line. */
  .cb-hero__copy { min-width: 0; }

  /* ══ PROOF BAR ══ */
  .mk-proof { position: relative; z-index: 1; border-top: 1px solid var(--clefs-line-soft); border-bottom: 1px solid var(--clefs-line-soft); background: rgba(255,255,255,0.015); }
  .mk-proof__list {
    list-style: none; margin: 0 auto; padding: 22px clamp(20px, 5vw, 48px); max-width: 1140px;
    display: flex; justify-content: center; flex-wrap: wrap; gap: 14px clamp(22px, 4vw, 52px);
  }
  .mk-proof__item { display: inline-flex; align-items: center; gap: 10px; font-size: 0.92rem; color: var(--clefs-ink); }
  .mk-proof__icon { color: var(--clefs-gold-500); flex: none; }

  /* ══ PRODUCTS ══ */
  .mk-products { display: flex; flex-direction: column; gap: clamp(56px, 8vw, 110px); }
  .mk-product {
    display: grid; grid-template-columns: 1.05fr 1fr; gap: clamp(32px, 6vw, 76px); align-items: center;
    scroll-margin-top: 96px;
  }
  .mk-product:nth-child(even) .mk-product__copy { order: 2; }
  .mk-product__name {
    display: inline-flex; align-items: center; gap: 10px; margin: 0 0 16px;
    font-size: 0.74rem; letter-spacing: 0.16em; text-transform: uppercase; color: var(--clefs-gold-400);
  }
  .mk-product__icon {
    display: grid; place-items: center; width: 36px; height: 36px; border-radius: 50%;
    color: var(--clefs-gold-500); background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line);
  }
  .mk-product__title { font-size: clamp(1.45rem, 2.6vw, 2.05rem); line-height: 1.22; margin: 0 0 16px; }
  .mk-product__text { font-size: 0.97rem; line-height: 1.75; color: var(--clefs-ink-muted); margin: 0 0 20px; max-width: 54ch; }
  .mk-product__list { list-style: none; margin: 0 0 24px; padding: 0; display: grid; gap: 10px; }
  .mk-product__list li { display: flex; align-items: flex-start; gap: 10px; font-size: 0.92rem; line-height: 1.55; color: var(--clefs-ink); }
  .mk-product__list svg { color: var(--clefs-gold-500); flex: none; margin-top: 3px; }
  .mk-product__link {
    display: inline-flex; align-items: center; gap: 8px; font-size: 0.9rem;
    color: var(--clefs-gold-400); text-decoration: none; border-bottom: 1px solid var(--clefs-line-gold); padding-bottom: 3px;
  }
  .mk-product__link:hover { color: var(--clefs-gold-500); }
  .mk-product__aside { min-width: 0; }

  /* Market context (Profit Guard aside): every number here is a <Claim>. */
  .mk-context {
    display: grid; gap: 24px; padding: clamp(22px, 3vw, 34px);
    border: 1px solid var(--clefs-line); border-radius: 6px; background: rgba(255,255,255,0.02);
  }
  .mk-context__h { margin: 0; font-size: 0.72rem; letter-spacing: 0.16em; text-transform: uppercase; color: var(--clefs-ink-faint); }
  .mk-context__note { margin: 0; font-size: 0.86rem; line-height: 1.65; color: var(--clefs-ink-muted); }

  /* Demand signals mock (Demand Radar aside). */
  .mk-radar { margin: 0; display: grid; gap: 10px; padding: clamp(20px, 3vw, 30px); border: 1px solid var(--clefs-line); border-radius: 6px; background: rgba(255,255,255,0.02); }
  .mk-radar__row { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--clefs-line-soft); }
  .mk-radar__row:last-of-type { border-bottom: 0; }
  .mk-radar__dot { width: 9px; height: 9px; border-radius: 50%; background: var(--clefs-ink-faint); }
  .mk-radar__row--high .mk-radar__dot { background: var(--clefs-gold-400); box-shadow: 0 0 10px rgba(242,198,125,0.6); }
  .mk-radar__row--mid .mk-radar__dot { background: var(--clefs-gold-600); }
  .mk-radar__t { font-size: 0.9rem; color: var(--clefs-ink); }
  .mk-radar__a { font-size: 0.76rem; color: var(--clefs-gold-400); white-space: nowrap; }
  .mk-radar__cap { font-size: 0.74rem; color: var(--clefs-ink-faint); margin-top: 6px; }

  /* ══ OWNER BRIEF CARD ══ */
  .mk-brief {
    margin: 0; padding: clamp(20px, 3vw, 28px); border-radius: 8px;
    border: 1px solid var(--clefs-line-gold); background: linear-gradient(180deg, rgba(225,175,104,0.06), rgba(255,255,255,0.015));
    box-shadow: 0 30px 80px -40px rgba(0,0,0,0.8);
  }
  .mk-brief__head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 14px; }
  .mk-brief__title { font-family: var(--clefs-serif); font-size: 1.35rem; color: var(--clefs-ink); }
  .mk-brief__when { font-size: 0.74rem; letter-spacing: 0.08em; color: var(--clefs-ink-faint); }
  .mk-brief__items { list-style: none; margin: 0; padding: 0; display: grid; gap: 1px; }
  .mk-brief__item {
    display: grid; grid-template-columns: 1fr auto; gap: 4px 14px; align-items: center;
    padding: 14px 0; border-top: 1px solid var(--clefs-line-soft);
  }
  .mk-brief__tag { grid-column: 1 / -1; font-size: 0.66rem; letter-spacing: 0.14em; text-transform: uppercase; color: var(--clefs-gold-400); }
  .mk-brief__text { font-size: 0.93rem; line-height: 1.5; color: var(--clefs-ink); }
  .mk-brief__meta { grid-row: 3; font-size: 0.74rem; color: var(--clefs-ink-faint); }
  .mk-brief__act {
    grid-column: 2; grid-row: 2 / span 2; align-self: center; white-space: nowrap;
    font-size: 0.76rem; padding: 7px 12px; border-radius: 999px;
    border: 1px solid var(--clefs-line-gold); color: var(--clefs-gold-400); background: var(--clefs-gold-wash);
  }
  .mk-brief__foot { margin-top: 14px; font-size: 0.74rem; color: var(--clefs-ink-faint); }

  /* ══ CALCULATORS ══ */
  .mk-calc { margin-top: clamp(56px, 7vw, 88px); padding-top: clamp(36px, 5vw, 56px); border-top: 1px solid var(--clefs-line-soft); }
  .mk-calc__title { font-size: clamp(1.3rem, 2.3vw, 1.75rem); line-height: 1.25; margin: 0 0 10px; }
  .mk-calc__desc { font-size: 0.93rem; line-height: 1.7; color: var(--clefs-ink-muted); margin: 0; max-width: 60ch; }
  .mk-calc .dhc, .mk-calc .mk-olc { margin-top: 28px; }

  /* ══ CTA + FOOTER ══ */
  .mk-cta__btns { display: flex; justify-content: center; flex-wrap: wrap; gap: 14px; }
  .mk-footer__groups { display: flex; flex-wrap: wrap; gap: 20px clamp(28px, 5vw, 64px); }
  .mk-footer__group { display: flex; flex-direction: column; gap: 9px; min-width: 150px; }
  .mk-footer__h { margin: 0 0 4px; font-size: 0.66rem; letter-spacing: 0.16em; text-transform: uppercase; color: var(--clefs-ink-muted); }
  .mk-footer__group a { font-size: 0.82rem; color: var(--clefs-ink-faint); text-decoration: none; transition: color .3s var(--ease-out); }
  .mk-footer__group a:hover { color: var(--clefs-gold-500); }
  /* Three link groups push the copyright onto its own row; give it the full
     width instead of leaving it right-aligned under the brand. */
  .cb-footer__inner .cb-footer__copy { flex-basis: 100%; text-align: left; padding-top: 18px; border-top: 1px solid var(--clefs-line-soft); }

  @media (max-width: 1000px) {
    .mk-product { grid-template-columns: 1fr; }
    .mk-product:nth-child(even) .mk-product__copy { order: 0; }
    .mk-footer__groups { justify-content: center; text-align: left; }
    .cb-footer__inner .cb-footer__copy { text-align: center; }
  }
  @media (max-width: 620px) {
    .mk-proof__list { flex-direction: column; align-items: flex-start; }
    .cb-hero__btns .cb-btn, .mk-cta__btns .cb-btn { white-space: normal; text-align: center; line-height: 1.35; justify-content: center; }
    .mk-cta__btns { flex-direction: column; }
    .mk-cta__btns .cb-btn { width: 100%; justify-content: center; }
    .mk-brief__item { grid-template-columns: 1fr; }
    .mk-brief__act { grid-column: 1; grid-row: auto; justify-self: start; }
    .mk-brief__meta { grid-row: auto; }
    .mk-footer__groups { flex-direction: column; align-items: center; text-align: center; }
    .mk-footer__group { align-items: center; }
  }
`;
