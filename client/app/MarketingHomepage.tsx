"use client";
import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { HOME_FAQS } from "./lib/faq";
import { BRAND } from "./lib/seo";
import Claim, { CLAIM_STYLES } from "./components/Claim";
import TryDemo from "./try/TryDemo";
import {
  LuMic, LuArrowRight, LuPlus, LuPlay, LuSquare, LuCheck, LuX, LuQrCode, LuBadgeCheck, LuUsers, LuLanguages,
  LuCamera, LuBellRing, LuSiren, LuHeartHandshake, LuBarChart3, LuPhoneCall, LuClock, LuSmartphone, LuUserCheck,
  LuBot, LuShieldCheck, LuHourglass, LuSparkles, LuEye, LuCloud, LuDatabase, LuKeyRound, LuImage, LuTimer,
  LuHotel, LuCpu, LuCalendarClock,
} from "react-icons/lu";

const DEMO_URL = BRAND.demoUrl;
const TAGLINE = "The AI front desk for hotels";

// ─── Page data ───────────────────────────────────────────────────────────────
// The site root, in the owner's order: the avatar hero, "Try it yourself"
// (TryDemo: the live demo hotel in Tokyo), what happens step by step, what to
// try when things go wrong, then the AI Builder Cup deck's story (the problem,
// why now, the nine features, the Google Cloud build, the benchmarks, where
// Clefs is going), pricing, trust, questions and the footer.
//
// Copy rule: a judge with two minutes. Plain, human English, short sentences
// and no system terms. Say "in your own language" (the claims registry bans
// the "any" form). Every market number renders through <Claim>, with its
// registry row and source link; Clefs' own prices and benchmarks carry
// `// claim: <id>`. check-claims.mjs enforces both.

type Item = { icon: typeof LuMic; t: string; d: string };

/* Section anchors on this page. The "Try it now" button sits beside them. */
const NAV: { href: string; label: string }[] = [
  { href: "#how", label: "How it works" },
  { href: "#why", label: "Why now" },
  { href: "#features", label: "Features" },
  { href: "#pricing", label: "Pricing" },
  { href: "#questions", label: "FAQ" },
];

/* Where each step shows up, so a judge knows which tab to look at. */
type Screen = "guest" | "staff" | "manager";
const SCREEN: Record<Screen, string> = {
  guest: "Guest page",
  staff: "Maria's phone",
  manager: "Manager dashboard",
};

// The deck's process slide, step for step, in plain words.
const STEPS: { t: string; d: string; on: Screen[] }[] = [
  { t: "You ask", on: ["guest"],
    d: "Scan the QR card in the room and ask by voice or text, in your own language. There is no app to download." },
  { t: "Gemini understands", on: ["guest"],
    d: "It works out what you need, how many, how urgent it is, and how you feel." },
  { t: "One job for each thing you ask", on: ["guest"],
    d: "“Two towels, and the room is too warm” becomes two jobs, for two teams." },
  { t: "The right person is sent", on: ["guest"],
    d: "The nearest free staff member gets the job, and you hear who is coming: “Maria is on her way, about 5 minutes.”" },
  { t: "Staff read it in their own language", on: ["staff"],
    d: "Maria reads the job in Tagalog, with your own words below it." },
  { t: "Done, with a photo", on: ["staff"],
    d: "Maria does the job and takes a photo. She can answer by voice if she needs to." },
  { t: "Gemini checks the photo", on: ["staff"],
    d: "The photo is checked against what you asked for, before the job can close." },
  { t: "Closed, and everyone knows", on: ["guest", "manager"],
    d: "You say all is well, and the manager's dashboard updates." },
];

// The deck's "when something goes wrong", as things a judge can try. The
// keys are ones check-claims.mjs reads as visible copy (text, body, note).
type Wrong = { icon: typeof LuMic; t: string; text: string; body: string; note?: string };
const WRONG: Wrong[] = [
  { icon: LuTimer, t: "Nobody takes the job",
    text: "Leave the new job on Maria's phone without accepting it.",
    body: "After 3 minutes the supervisor is told, and the job goes to the next free person. After 5 minutes the duty manager is told, and after 10 the general manager.",
    note: "No time to wait? On the manager dashboard, tap “Simulate 3 min” on the job." },
  { icon: LuCamera, t: "The photo is wrong",
    text: "On Maria's phone, finish the job with a photo of something else.",
    body: "Gemini says no. The job stays open, the supervisor is told, and Maria sees what is missing." },
  { icon: LuHeartHandshake, t: "The guest is unhappy",
    text: "On the guest page, answer “Not fixed”, or tell the assistant you are upset.",
    body: "The job opens again, and the manager is asked to approve a small gift to say sorry, before the guest writes a review." },
];

/* The deck's promise: what a hotel does not have to change. */
const PROOF = [
  { icon: LuSmartphone, t: "No app for guests." },
  { icon: LuCpu, t: "No new hardware." },
  { icon: LuHotel, t: "No change to the hotel's systems or phones." },
  { icon: LuUserCheck, t: "Staff stay in charge of every exception." },
];

// The deck's first slide: a stay every judge has had.
const LIVED = [
  "You call the front desk for towels. It rings out.",
  "You ask again. Nobody knows who took it.",
  "You check out and write the review.",
];

const PROBLEMS: Item[] = [
  { icon: LuPhoneCall, t: "Phone calls",
    d: "The guest calls the desk, the desk calls housekeeping, housekeeping calls back. A request can drop at every step." },
  { icon: LuLanguages, t: "Languages",
    d: "The guest asks in Korean, the desk writes it down in Japanese, the housekeeper reads Tagalog. Details get lost on the way." },
  { icon: LuClock, t: "Shift changes",
    d: "A request made at 22:55 is forgotten by the 23:00 handover. Nobody knows it was asked, so nobody follows up." },
];

// The deck's nine features.
const FEATURES: Item[] = [
  { icon: LuQrCode, t: "Talk or type, in your own language",
    d: "From the QR card in the room, on the guest's own phone. Nothing to download." },
  { icon: LuBadgeCheck, t: "Approved answers only",
    d: "Gemini answers only from facts the hotel has approved, and passes everything else to a person." },
  { icon: LuUsers, t: "The right person, with a time",
    d: "Clefs picks the right free staff member and tells the guest when they will arrive." },
  { icon: LuLanguages, t: "Staff use their own language",
    d: "Each person reads the job and replies in the language they know. The guest reads the reply in theirs." },
  { icon: LuCamera, t: "Proof before closing",
    d: "Gemini checks the photo, then the guest confirms the fix." },
  { icon: LuBellRing, t: "Late jobs move up on their own",
    d: "No answer? The supervisor hears at 3 minutes, the duty manager at 5 and the general manager at 10." },
  { icon: LuSiren, t: "Safety first",
    d: "Emergency words go straight to the duty manager, with the room number and the guest's own words." },
  { icon: LuHeartHandshake, t: "Put right before the review",
    d: "A “not fixed” answer, or an upset guest, opens a case for the manager to put right while the guest is still there." },
  { icon: LuBarChart3, t: "Owner view",
    d: "Response times by team, floor and shift, from the same jobs the staff closed." },
];

// What the prototype runs on, as built (docs/DEPLOY.md).
const STACK: Item[] = [
  { icon: LuMic, t: "Gemini Live API", d: "Real-time voice, in the guest's own language." },
  { icon: LuSparkles, t: "Gemini Flash", d: "Understands each request, splits it into jobs, translates for staff and notices when a guest is upset." },
  { icon: LuEye, t: "Gemini vision", d: "Checks every proof photo against the request." },
  { icon: LuCloud, t: "Cloud Run", d: "Runs the guest page, the staff app, the manager dashboard and the API. Cloud Scheduler looks for late jobs every minute." },
  { icon: LuDatabase, t: "Cloud SQL", d: "Jobs, staff, rooms, and a record of who did what." },
  { icon: LuKeyRound, t: "Secret Manager", d: "Keeps every key and password out of the code." },
  { icon: LuImage, t: "Cloud Storage", d: "Holds the proof photos, and deletes them on its own after 7 days." },
];

// The deck's benchmarks slide. Measured on the prototype in scripted tests.
const BENCHMARKS = [
  { val: "1 s", label: "from the guest's last word to the voice reply" },
  { val: "94%", label: "of test requests understood correctly" }, // claim: clefs_proto_request_understanding
  { val: "98%", label: "of requests with two or more asks split into the right jobs" }, // claim: clefs_proto_request_splitting
  { val: "5/5", label: "translation quality, rated by native speakers" },
  { val: "91%", label: "of test photos rightly accepted or rejected" }, // claim: clefs_proto_photo_verification
  { val: "1.2 s", label: "from the request to the staff member's screen" },
  { val: "60% faster", label: "from request to a checked, finished job" }, // claim: clefs_proto_task_closure
];

// The deck's go-to-market slide.
const ROAD = [
  { h: "Now", t: "Pilots with hotels in India and the UAE." },
  { h: "Next", t: "Malaysia, Bali and the Maldives." },
  { h: "Later", t: "Japan, Korea and Thailand. That is why our demo hotel is in Tokyo." },
];

// The deck's pricing slide (claims.ts clefs_price_list_2026).
const PLANS = [
  { name: "Voice and text", val: "$3", note: "per room per month, minimum $99 a month" }, // claim: clefs_price_list_2026
  { name: "Text only", val: "$1.50", note: "per room per month, the same service without voice" }, // claim: clefs_price_list_2026
  { name: "Pilot", val: "Free", note: "for 30 days, with your own rooms and team" },
];
const NEEDS = ["QR cards for each room", "One shared staff tablet", "Staff names and roles"];
const NEVER = ["Access to your hotel system", "Financial files", "Card data", "Changes to your phones"];

// The deck's "built for trust", stated as how every hotel goes live.
const TRUST: Item[] = [
  { icon: LuUserCheck, t: "Guests agree first",
    d: "Nothing starts until the guest agrees to chat with an AI. Voice asks again before it records." },
  { icon: LuBot, t: "It says it is an AI",
    d: "The assistant always says it is an AI, and never pretends to be a person." },
  { icon: LuShieldCheck, t: "22 safety checks before going live",
    d: "Emergencies, trick questions, prices, refunds, routing and languages. A hotel goes live only when all 22 pass, with Gemini as the judge." },
  { icon: LuHourglass, t: "A 7-day trial run",
    d: "For a week, Clefs shows the team every alert it would send, without sending any. Then it goes live." },
];

const FOOTER: { h: string; links: [string, string][] }[] = [
  { h: "Clefs", links: [["#try", "Try it yourself"], ["#how", "How it works"], ["#pricing", "Pricing"], ["#questions", "Questions"]] },
  { h: "Learn more", links: [
    ["/ai-concierge-software", "AI concierge software"],
    ["/guest-request-management", "Guest request management"],
    ["/multilingual-guest-messaging", "Multilingual guest messaging"],
    ["/hotel-front-desk-automation", "Front desk automation"],
  ] },
];

/**
 * `today` is the date the server render judged the claims by (app/page.tsx).
 * The hydration render must use the same one, or a claim that crosses its
 * review date between the two renders would make React report a mismatch.
 */
export default function MarketingHomepage({ today }: { today: string }) {
  const [scrolled, setScrolled] = useState(false);
  const [activeNav, setActiveNav] = useState("");
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
      // Only while the avatar is on screen: a tap further down is someone
      // reading the page, not someone asking to hear her.
      const r = videoRef.current?.getBoundingClientRect();
      if (!r || r.bottom <= 0 || r.top >= window.innerHeight) return;
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
      <nav className={`cb-nav ${scrolled ? "cb-nav--scrolled" : ""}`} aria-label="Main">
        <div className="cb-nav__inner">
          <a href="#top" className="cb-nav__brand" aria-label="Clefs, back to the top">
            <img src="/brand/clefs-key.webp" alt="" className="cb-nav__mark" width={520} height={1031} />
            <span className="cb-nav__word clefs-gilt">CLEFS</span>
          </a>
          <div className="cb-nav__links">
            {NAV.map((n) => (
              <a key={n.href} href={n.href}
                 className={`cb-nav__link ${activeNav === n.href ? "cb-nav__link--active" : ""}`}
                 onClick={() => setActiveNav(n.href)}>{n.label}</a>
            ))}
          </div>
          <div className="cb-nav__end">
            <a href="#try" className="cb-btn cb-btn--gold cb-btn--sm">Try it now</a>
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
          {NAV.map((n) => (
            <a key={n.href} href={n.href} className="cb-menu__link"
               onClick={() => { setActiveNav(n.href); setMenuOpen(false); }}>{n.label}</a>
          ))}
        </div>
      </nav>

      <main>
      {/* ── Hero ─────────────────────────────────────────────────────────────
          The mark is repeated at 1200px behind the copy as a watermark, the way
          a hotel embosses its monogram on stationery. The promise is the deck's
          title, and the line under it says what Clefs is in one sentence. */}
      <header id="top" className="cb-hero">
        <img src="/brand/clefs-key.webp" alt="" aria-hidden="true" className="cb-hero__watermark" fetchPriority="low" decoding="async" />

        <div className="cb-hero__inner clefs-above">
          <div className="cb-hero__copy">
            <p className="cb-hero__eyebrow">
              <span className="cb-hero__eyebrow-rule" aria-hidden="true" />
              AI front desk for hotels&nbsp;· Built with Gemini
            </p>
            <h1 className="cb-hero__title">
              Ask once, <em className="clefs-gilt clefs-gilt--live">in your language,</em>{" "}
              and it gets done.
            </h1>
            <p className="cb-hero__sub">
              Hotel guests ask for anything from their own phone, in their own language, and the right staff
              member gets it done, with a photo to prove it.
            </p>
            <div className="cb-hero__btns">
              <a href="#try" className="cb-btn cb-btn--gold">
                <LuPlay size={16} aria-hidden="true" /> Try it yourself
              </a>
              <a href="#how" className="cb-btn cb-btn--ghost">
                How it works
              </a>
            </div>
            <p className="mk-hero__note">
              A live demo hotel in Tokyo. No sign-up, and it takes about two minutes.
            </p>
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

        <a href="#try" className="cb-hero__scroll" aria-label="Go to Try it yourself">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="M7 10l5 5 5-5" /></svg>
        </a>
      </header>

      {/* ── Try it yourself ── The live demo hotel: be the guest, be Maria,
          be the manager (app/try/TryDemo.tsx renders section#try). */}
      <TryDemo />

      {/* ── What happens, step by step ── The deck's eight steps, each with the
          screen it shows on, next to the guest's phone and the job card. */}
      <section id="how" className="cb-section cb-section--alt" aria-labelledby="how-title">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">How it works</p>
            <h2 id="how-title" className="cb-head__title">What happens, <em className="clefs-gilt">step by step.</em></h2>
            <p className="cb-head__desc">The guest asks once. Everything after that happens without a phone call.</p>
          </header>

          <div className="cb-showcase">
            <ol className="cb-steps mk-steps">
              {STEPS.map((s, i) => (
                <li key={s.t} className="cb-step" data-reveal style={{ transitionDelay: `${i * 60}ms` }}>
                  <span className="cb-step__num clefs-gilt" aria-hidden="true">{i + 1}</span>
                  <div>
                    <h3 className="cb-step__title">{s.t}</h3>
                    <p className="cb-step__desc">{s.d}</p>
                    <p className="mk-on">
                      <span className="mk-sr">Where you see it: </span>
                      {s.on.map((k) => <span key={k} className="mk-on__tag">{SCREEN[k]}</span>)}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
            <GuestPhone />
          </div>
        </div>
      </section>

      {/* ── Try what happens when things go wrong ── The deck's three cases. */}
      <section id="wrong" className="cb-section" aria-labelledby="wrong-title">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">When things go wrong</p>
            <h2 id="wrong-title" className="cb-head__title">Try what happens <em className="clefs-gilt">when things go wrong.</em></h2>
            <p className="cb-head__desc">Clefs acts first, before the guest has to ask again.</p>
          </header>
          <div className="mk-cards mk-cards--3">
            {WRONG.map((w, i) => <WrongCard key={w.t} item={w} delay={i * 80} />)}
          </div>
        </div>
      </section>

      {/* ── What a hotel does not have to change ── */}
      <section className="mk-proof" aria-label="What a hotel does not have to change">
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

      {/* ── The problem ── The deck's first slide, then the three gaps. */}
      <section id="problem" className="cb-section" aria-labelledby="problem-title">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">The problem</p>
            <h2 id="problem-title" className="cb-head__title">
              More guests, fewer staff.<br />
              <em className="clefs-gilt">Requests get lost.</em>
            </h2>
            <p className="cb-head__desc">They fall between phone calls, languages and shift changes.</p>
          </header>
          <div className="mk-lived" data-reveal>
            <div className="mk-lived__col">
              <h3 className="mk-lived__h">You have lived this as a guest</h3>
              <ol className="mk-lived__list">
                {LIVED.map((l) => <li key={l}>{l}</li>)}
              </ol>
            </div>
            <div className="mk-lived__col">
              <h3 className="mk-lived__h">Behind the desk</h3>
              <p className="mk-lived__p">Fewer staff, guests from everywhere, and no record of who did what.</p>
            </div>
          </div>
          <div className="mk-cards mk-cards--3">
            {PROBLEMS.map((p, i) => <ItemCard key={p.t} item={p} delay={i * 80} />)}
          </div>
          <p className="mk-answer" data-reveal>
            Clefs gets each one done, <em className="clefs-gilt">and proves it.</em>
          </p>
        </div>
      </section>

      {/* ── Why now ── The deck's opportunity slide: four registry claims,
          each with its source. An expired claim renders nothing, and its
          card hides (.mk-stat:empty). */}
      <section id="why" className="cb-section cb-section--alt" aria-labelledby="why-title">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">Why now</p>
            <h2 id="why-title" className="cb-head__title">Record guests, <em className="clefs-gilt">too few staff.</em></h2>
            <p className="cb-head__desc">
              Japan shows it clearly: record numbers of visitors, and hotels that cannot find enough staff. Yet
              guests still want a person to greet them.
            </p>
          </header>
          <ul className="mk-stats">
            <li className="mk-stat" data-reveal><Claim id="jnto_japan_visitors_2025" variant="stat" today={today} /></li>
            <li className="mk-stat" data-reveal style={{ transitionDelay: "70ms" }}><Claim id="jta_lodging_short_of_staff_2026" variant="stat" today={today} /></li>
            <li className="mk-stat" data-reveal style={{ transitionDelay: "140ms" }}><Claim id="mews_human_welcome_2026" variant="stat" today={today} /></li>
            <li className="mk-stat" data-reveal style={{ transitionDelay: "210ms" }}><Claim id="state_of_hotel_ai_productivity_2026" variant="stat" today={today} /></li>
          </ul>
          <p className="mk-answer" data-reveal>
            Gemini answers the simple things at once, <em className="clefs-gilt">so staff have time for the human welcome.</em>
          </p>
        </div>
      </section>

      {/* ── Features ── */}
      <section id="features" className="cb-section" aria-labelledby="features-title">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">What Clefs does</p>
            <h2 id="features-title" className="cb-head__title">Nine things <em className="clefs-gilt">every request gets.</em></h2>
          </header>
          <div className="mk-cards mk-cards--3">
            {FEATURES.map((f, i) => <ItemCard key={f.t} item={f} delay={(i % 3) * 70} />)}
          </div>
        </div>
      </section>

      {/* ── Built on Google Cloud ── */}
      <section id="stack" className="cb-section cb-section--alt" aria-labelledby="stack-title">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">Built on Google Cloud</p>
            <h2 id="stack-title" className="cb-head__title">Gemini from the first word <em className="clefs-gilt">to the last photo.</em></h2>
            <p className="cb-head__desc">The whole prototype runs on Google Cloud.</p>
          </header>
          <div className="mk-stack">
            {STACK.map((s, i) => {
              const Icon = s.icon;
              return (
                <div key={s.t} className="mk-stack__item" data-reveal style={{ transitionDelay: `${(i % 4) * 60}ms` }}>
                  <span className="mk-stack__icon" aria-hidden="true"><Icon size={18} /></span>
                  <div>
                    <h3 className="mk-stack__t">{s.t}</h3>
                    <p className="mk-stack__d">{s.d}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── Benchmarks ── The deck's slide, labelled for what it is. */}
      <section id="benchmarks" className="cb-section" aria-labelledby="benchmarks-title">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">Benchmarks</p>
            <h2 id="benchmarks-title" className="cb-head__title">Measured on <em className="clefs-gilt">the prototype.</em></h2>
            <p className="cb-head__desc">
              Scripted tests on the prototype, not results from a hotel. A pilot measures each hotel&apos;s own
              numbers, with its own rooms and team.
            </p>
          </header>
          <div className="cb-metrics mk-bench">
            {BENCHMARKS.map((m, i) => (
              <div key={m.label} className="cb-metric" data-reveal style={{ transitionDelay: `${(i % 4) * 70}ms` }}>
                <div className="cb-metric__val clefs-gilt">{m.val}</div>
                <div className="cb-metric__label">{m.label}</div>
              </div>
            ))}
          </div>
          <p className="mk-footnote">Measured on the Clefs prototype in October 2026, as shown in our AI Builder Cup deck.</p>
        </div>
      </section>

      {/* ── Where we are going ── The deck's go-to-market slide. */}
      <section id="pilots" className="cb-section cb-section--alt mk-pilots" aria-labelledby="pilots-title">
        <div className="cb-container cb-container--narrow" data-reveal>
          <p className="clefs-eyebrow">Where we are going</p>
          <h2 id="pilots-title" className="cb-head__title">From India and the UAE, <em className="clefs-gilt">into Asia Pacific.</em></h2>
          <p className="mk-pilots__text">
            Clefs is built with hotel teams. Front desk staff, housekeepers and duty managers shaped every screen,
            from the QR card in the room to the photo at the end.
          </p>
          <ol className="mk-road">
            {ROAD.map((r) => (
              <li key={r.h} className="mk-road__item">
                <span className="mk-road__h">{r.h}</span>
                <span className="mk-road__t">{r.t}</span>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ── Pricing ── The deck's pricing slide. */}
      <section id="pricing" className="cb-section" aria-labelledby="pricing-title">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">Pricing</p>
            <h2 id="pricing-title" className="cb-head__title">Per room, <em className="clefs-gilt">per month.</em></h2>
            <p className="cb-head__desc">Start with a free 30-day pilot. No new hardware, and nothing in your systems to change.</p>
          </header>
          <div className="mk-plans">
            {PLANS.map((p, i) => (
              <div key={p.name} className={`mk-plan ${i === 0 ? "mk-plan--main" : ""}`} data-reveal style={{ transitionDelay: `${i * 70}ms` }}>
                <h3 className="mk-plan__name">{p.name}</h3>
                <p className="mk-plan__val clefs-gilt">{p.val}</p>
                <p className="mk-plan__note">{p.note}</p>
              </div>
            ))}
          </div>
          <div className="mk-needs" data-reveal>
            <div className="mk-needs__col">
              <h3 className="mk-needs__h">What a hotel needs</h3>
              <ul className="mk-needs__list">
                {NEEDS.map((n) => <li key={n}><LuCheck size={15} aria-hidden="true" /> {n}</li>)}
              </ul>
            </div>
            <div className="mk-needs__col">
              <h3 className="mk-needs__h">Never needed</h3>
              <ul className="mk-needs__list mk-needs__list--never">
                {NEVER.map((n) => <li key={n}><LuX size={15} aria-hidden="true" /> {n}</li>)}
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* ── Built for trust ── How every hotel goes live, in plain words. */}
      <section id="trust" className="cb-section cb-section--alt" aria-labelledby="trust-title">
        <div className="cb-container">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">Built for trust</p>
            <h2 id="trust-title" className="cb-head__title">Safe to put <em className="clefs-gilt">in front of guests.</em></h2>
            <p className="cb-head__desc">Staff stay in charge of every exception. Clefs earns its way live.</p>
          </header>
          <div className="mk-cards mk-cards--4">
            {TRUST.map((t, i) => <ItemCard key={t.t} item={t} delay={i * 70} />)}
          </div>
        </div>
      </section>

      {/* ── Questions ───────────────────────────────────────────────────────
          Rendered from HOME_FAQS, the same array that feeds the FAQPage schema
          in app/page.tsx. Google requires marked-up FAQ content to be visible
          on the page; keeping one source is what guarantees it stays that way.
          <details> is deliberate: the answers are in the HTML for crawlers with
          no JS, and the accordion still works with scripting disabled. */}
      <section id="questions" className="cb-section" aria-labelledby="questions-title">
        <div className="cb-container cb-container--narrow">
          <header className="cb-head" data-reveal>
            <p className="clefs-eyebrow">Questions</p>
            <h2 id="questions-title" className="cb-head__title">
              What hotels <em className="clefs-gilt">ask us</em>
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

      {/* ── CTA ── Back to the live demo; a call is the fallback. */}
      <section className="cb-cta" aria-labelledby="cta-title">
        <div className="cb-cta__inner" data-reveal>
          <img src="/brand/clefs-key.webp" alt="" aria-hidden="true" className="cb-cta__mark" loading="lazy" decoding="async" />
          <h2 id="cta-title" className="cb-cta__title">
            See it work, <span className="cb-nowrap"><em className="clefs-gilt">in two minutes.</em></span>
          </h2>
          <p className="cb-cta__desc">
            Be the guest in Room 204 and ask for towels. Watch the job reach Maria&apos;s phone in her
            language, then follow it on the manager dashboard.
          </p>
          <div className="mk-cta__btns">
            <a href="#try" className="cb-btn cb-btn--gold cb-btn--lg">
              Try it yourself <LuArrowRight size={17} aria-hidden="true" />
            </a>
            <a href={DEMO_URL} className="cb-btn cb-btn--ghost cb-btn--lg" target="_blank" rel="noopener noreferrer">
              <LuCalendarClock size={17} aria-hidden="true" /> Book a demo
            </a>
          </div>
        </div>
      </section>
      </main>

      {/* ── Footer ── */}
      <footer className="cb-footer">
        <div className="cb-footer__inner">
          <div className="cb-footer__brand">
            <img src="/brand/clefs-key.webp" alt="" className="cb-footer__mark" loading="lazy" />
            <div>
              <div className="cb-footer__word clefs-gilt">CLEFS</div>
              <div className="cb-footer__tag">{TAGLINE}</div>
            </div>
          </div>
          <nav className="mk-footer__groups" aria-label="Footer">
            {FOOTER.map((g) => (
              <div key={g.h} className="mk-footer__group">
                <p className="mk-footer__h">{g.h}</p>
                {g.links.map(([href, label]) => href.startsWith("#")
                  ? <a key={href} href={href}>{label}</a>
                  : <Link key={href} href={href}>{label}</Link>)}
              </div>
            ))}
            <div className="mk-footer__group">
              <p className="mk-footer__h">Talk to us</p>
              <a href={DEMO_URL} target="_blank" rel="noopener noreferrer">Book a demo</a>
            </div>
          </nav>
          <p className="cb-footer__copy">© {new Date().getFullYear()} Clefs<br />Built with Gemini on Google Cloud.</p>
        </div>
      </footer>
    </div>
  );
}

/* One card of a grid: the problem, the features, trust. */
function ItemCard({ item, delay = 0 }: { item: Item; delay?: number }) {
  const Icon = item.icon;
  return (
    <article className="mk-card" data-reveal style={{ transitionDelay: `${delay}ms` }}>
      <span className="mk-card__icon" aria-hidden="true"><Icon size={18} /></span>
      <h3 className="mk-card__t">{item.t}</h3>
      <p className="mk-card__d">{item.d}</p>
    </article>
  );
}

/* One "when things go wrong" card: what to try on the demo, then what Clefs does. */
function WrongCard({ item, delay = 0 }: { item: Wrong; delay?: number }) {
  const Icon = item.icon;
  return (
    <article className="mk-card mk-card--warn mk-wrong" data-reveal style={{ transitionDelay: `${delay}ms` }}>
      <span className="mk-card__icon" aria-hidden="true"><Icon size={18} /></span>
      <h3 className="mk-card__t">{item.t}</h3>
      <p className="mk-wrong__k">Try this</p>
      <p className="mk-card__d">{item.text}</p>
      <p className="mk-wrong__k">What happens</p>
      <p className="mk-card__d">{item.body}</p>
      {item.note && <p className="mk-wrong__tip">{item.note}</p>}
    </article>
  );
}

/* The deck's guest screen and the job card staff get: the moment the demo
   opens on (Room 204, Maria, about 5 minutes). Illustration. */
function GuestPhone() {
  return (
    <div className="cb-stage" data-reveal aria-hidden="true">
     <div className="cb-stage__inner">
      <div className="cb-phone">
        <div className="cb-phone__bar">
          <img src="/brand/clefs-key.webp" alt="" className="cb-phone__mark" />
          <span className="cb-phone__name">Clefs</span>
          <span className="cb-phone__status"><span className="cb-phone__dot" />Listening</span>
        </div>
        <div className="cb-phone__body">
          <div className="cb-phone__glow" />
          <div className="cb-phone__avatar">
            <img src="/female.webp" alt="" loading="lazy" decoding="async" />
          </div>
          <p className="cb-phone__guest">&ldquo;Can I get two extra towels?&rdquo;</p>
          <p className="cb-phone__reply">&ldquo;Of course. Housekeeping has been told. Maria is on her way, about 5 minutes.&rdquo;</p>
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
  /* <summary> is not in the global focus rule's list, so it gets its own ring. */
  .cb-faq__q:focus-visible { outline: 2px solid var(--clefs-gold-500); outline-offset: 3px; border-radius: 4px; }
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
  /* In-page links (the hero's "Try it yourself", the nav) glide unless the
     visitor asked for less motion. */
  @media (prefers-reduced-motion: no-preference) {
    html:has(.cb) { scroll-behavior: smooth; }
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
    text-wrap: balance;
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
    text-wrap: pretty;
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
    margin: 22px auto 0; max-width: 52ch; text-wrap: pretty;
  }

  /* ══ PROOF BAR, CARDS, STATS, PRICING ══ (see mkStyles) */

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
    .cb-hero__btns .cb-btn { width: 100%; min-height: 52px; }
    .cb-metrics { grid-template-columns: 1fr; }

    /* Phones: the avatar stays first but small, so the promise, the one-line
       explanation and the buttons fit on the first screen, and the next
       section follows straight on instead of a full screen of hero. */
    .cb-hero { min-height: 0; padding: 92px 20px 52px; }
    .cb-hero__inner { gap: 26px; }
    .cb-hero__portrait { width: 140px; height: 140px; }
    .cb-hero__ring { width: 176px; height: 176px; }
    .cb-hero__ring--2 { width: 204px; height: 204px; }
    .cb-hero__badge { margin-top: 16px; }
    .cb-hero__eyebrow { margin-bottom: 16px; letter-spacing: 0.16em; }
    .cb-hero__eyebrow-rule { display: none; }
    .cb-hero__title { font-size: 2.15rem; margin-bottom: 18px; }
    .cb-hero__sub { margin-bottom: 26px; }
    .cb-hero__scroll { display: none; }
  }
`;

/* Styles for the deck's sections (problem cards, steps, features, stack,
   benchmarks, pricing, trust, pilots, grouped footer). Kept apart from
   brandStyles so the hero, avatar, phone and ticket CSS above stays as is. */
const mkStyles = `
  /* The hero grid column must be allowed to shrink below its content: the
     buttons are sentences, and globals.css keeps .cb-btn on one line. */
  .cb-hero__copy { min-width: 0; }
  .mk-hero__note { margin: 22px 0 0; font-size: 0.9rem; line-height: 1.6; color: var(--clefs-ink-muted); max-width: 34em; }

  /* ══ PROOF BAR ══ */
  .mk-proof { position: relative; z-index: 1; border-top: 1px solid var(--clefs-line-soft); border-bottom: 1px solid var(--clefs-line-soft); background: rgba(255,255,255,0.015); }
  .mk-proof__list {
    list-style: none; margin: 0 auto; padding: 22px clamp(20px, 5vw, 48px); max-width: 1140px;
    display: flex; justify-content: center; flex-wrap: wrap; gap: 14px clamp(22px, 4vw, 52px);
  }
  .mk-proof__item { display: inline-flex; align-items: center; gap: 10px; font-size: 0.92rem; color: var(--clefs-ink); }
  .mk-proof__icon { color: var(--clefs-gold-500); flex: none; }

  /* ══ CARDS (problem, what goes wrong, features, trust) ══ */
  .mk-cards { display: grid; gap: clamp(14px, 2vw, 22px); }
  .mk-cards--3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .mk-cards--4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .mk-card {
    padding: clamp(22px, 3vw, 30px); border-radius: 6px;
    border: 1px solid var(--clefs-line-soft); background: var(--clefs-surface);
    transition: border-color .45s var(--ease-out), background .45s var(--ease-out);
  }
  .mk-card:hover { border-color: var(--clefs-line); background: var(--clefs-gold-wash); }
  .mk-card__icon {
    display: grid; place-items: center; width: 38px; height: 38px; border-radius: 50%; margin-bottom: 16px;
    color: var(--clefs-gold-500); background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line);
  }
  .mk-card--warn .mk-card__icon { color: var(--hc-warning); border-color: rgba(var(--hc-warning-rgb), 0.3); background: rgba(var(--hc-warning-rgb), 0.06); }
  .mk-card__t { font-size: 1.12rem; line-height: 1.3; margin: 0 0 10px; }
  .mk-card__d { margin: 0; font-size: 0.92rem; line-height: 1.7; color: var(--clefs-ink-muted); }
  .mk-answer {
    margin: clamp(40px, 5vw, 60px) auto 0; text-align: center;
    font-family: var(--clefs-serif); font-size: clamp(1.4rem, 2.6vw, 2rem); line-height: 1.3;
  }

  /* Steps: single-digit numbers sit in a narrower column. */
  .mk-steps .cb-step { grid-template-columns: 40px 1fr; padding: 18px 0; }
  .mk-steps .cb-step__num { font-size: 1.6rem; }

  /* ══ GOOGLE CLOUD ══ */
  .mk-stack { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px clamp(18px, 3vw, 36px); max-width: 980px; margin: 0 auto; }
  .mk-stack__item { display: grid; grid-template-columns: auto 1fr; gap: 14px; align-items: start; padding: 16px 0; border-bottom: 1px solid var(--clefs-line-soft); }
  .mk-stack__icon {
    display: grid; place-items: center; width: 36px; height: 36px; border-radius: 50%;
    color: var(--clefs-gold-500); background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line);
  }
  .mk-stack__t { margin: 0 0 4px; font-weight: 600; font-size: 0.98rem; color: var(--clefs-ink); }
  .mk-stack__d { margin: 0; font-size: 0.9rem; line-height: 1.6; color: var(--clefs-ink-muted); }

  /* ══ BENCHMARKS ══ */
  .mk-bench { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .mk-bench .cb-metric { padding: clamp(26px, 3vw, 40px) 14px; }
  .mk-footnote { margin: 22px auto 0; text-align: center; font-size: 0.84rem; line-height: 1.6; color: var(--clefs-ink-muted); max-width: 60ch; }

  /* ══ PRICING ══ */
  .mk-plans { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: clamp(14px, 2vw, 22px); }
  .mk-plan {
    text-align: center; padding: clamp(28px, 3.5vw, 44px) 20px; border-radius: 6px;
    border: 1px solid var(--clefs-line-soft); background: var(--clefs-surface);
  }
  .mk-plan--main { border-color: var(--clefs-line-gold); background: linear-gradient(180deg, rgba(225,175,104,0.07), rgba(255,255,255,0.015)); }
  .mk-plan__name { margin: 0 0 14px; font-size: 0.74rem; letter-spacing: 0.16em; text-transform: uppercase; color: var(--clefs-gold-400); }
  .mk-plan__val { margin: 0 0 12px; font-family: var(--clefs-serif); font-size: clamp(2.2rem, 4vw, 3rem); line-height: 1; }
  .mk-plan__note { margin: 0 auto; font-size: 0.9rem; line-height: 1.6; color: var(--clefs-ink-muted); max-width: 26ch; }
  .mk-needs {
    display: grid; grid-template-columns: 1fr 1fr; gap: clamp(18px, 4vw, 48px);
    margin-top: clamp(36px, 5vw, 56px); padding-top: clamp(28px, 4vw, 40px); border-top: 1px solid var(--clefs-line-soft);
  }
  .mk-needs__h { margin: 0 0 14px; font-size: 0.74rem; letter-spacing: 0.16em; text-transform: uppercase; color: var(--clefs-ink-muted); }
  .mk-needs__list { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
  .mk-needs__list li { display: flex; align-items: center; gap: 10px; font-size: 0.95rem; color: var(--clefs-ink); }
  .mk-needs__list svg { color: var(--hc-success); flex: none; }
  .mk-needs__list--never svg { color: var(--clefs-ink-faint); }

  /* ══ WHERE WE ARE GOING ══ */
  .mk-pilots .cb-container--narrow { text-align: center; }
  .mk-pilots .clefs-eyebrow { display: block; margin-bottom: 18px; }
  .mk-pilots__text { margin: 22px auto 0; font-size: 1rem; line-height: 1.75; color: var(--clefs-ink-muted); max-width: 56ch; }
  .mk-road {
    list-style: none; margin: 30px auto 0; padding: 0; max-width: 620px; text-align: left;
    display: grid; gap: 0; border-top: 1px solid var(--clefs-line-soft);
  }
  .mk-road__item {
    display: grid; grid-template-columns: 76px 1fr; gap: 16px; align-items: baseline;
    padding: 15px 2px; border-bottom: 1px solid var(--clefs-line-soft);
  }
  .mk-road__h { font-size: 0.74rem; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase; color: var(--clefs-gold-400); }
  .mk-road__t { font-size: 1rem; line-height: 1.6; color: var(--clefs-ink); }

  /* Labels that are headings for structure but read as small caps. */
  .cb .mk-plan__name, .cb .mk-needs__h { font-family: var(--clefs-sans); font-weight: 600; }
  .cb .mk-stack__t { font-family: var(--clefs-sans); }

  /* Screen-reader-only text. */
  .mk-sr {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
    clip: rect(0 0 0 0); white-space: nowrap; border: 0;
  }

  /* ══ STEPS: which screen each step shows on ══ */
  .mk-on { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 0; }
  .mk-on__tag {
    display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 0.72rem; font-weight: 600; letter-spacing: 0.04em;
    color: var(--clefs-gold-400); background: var(--clefs-gold-wash); border: 1px solid var(--clefs-line);
  }

  /* ══ WHEN THINGS GO WRONG: try this, then what happens ══ */
  .mk-wrong { display: flex; flex-direction: column; }
  .mk-wrong .mk-card__d { color: var(--clefs-ink); opacity: 0.86; }
  .mk-wrong__k {
    margin: 14px 0 4px; font-size: 0.7rem; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase;
    color: var(--clefs-gold-400);
  }
  .mk-wrong .mk-card__t + .mk-wrong__k { margin-top: 2px; }
  .mk-wrong__tip {
    margin: 16px 0 0; padding: 10px 12px; border-radius: 6px; font-size: 0.86rem; line-height: 1.55;
    color: var(--clefs-ink); background: var(--clefs-gold-wash); border: 1px dashed var(--clefs-line-gold);
  }

  /* ══ THE PROBLEM: the stay every guest has had ══ */
  .mk-lived {
    display: grid; grid-template-columns: 1.25fr 1fr; gap: clamp(14px, 2vw, 22px);
    margin: 0 0 clamp(14px, 2vw, 22px);
  }
  .mk-lived__col {
    padding: clamp(22px, 3vw, 30px); border-radius: 6px;
    border: 1px solid var(--clefs-line-soft); background: var(--clefs-surface);
  }
  .mk-lived__h { font-size: 1.2rem; line-height: 1.3; margin: 0 0 14px; }
  .mk-lived__list { margin: 0; padding: 0 0 0 1.3em; display: grid; gap: 8px; }
  .mk-lived__list li { font-size: 1rem; line-height: 1.6; color: var(--clefs-ink); padding-left: 4px; }
  .mk-lived__list li::marker { color: var(--clefs-gold-500); font-family: var(--clefs-serif); }
  .mk-lived__p { margin: 0; font-size: 1rem; line-height: 1.7; color: var(--clefs-ink); opacity: 0.86; }

  /* ══ WHY NOW: four sourced numbers ══ */
  .mk-stats {
    list-style: none; margin: 0; padding: 0;
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: clamp(12px, 2vw, 20px);
  }
  .mk-stat {
    padding: clamp(22px, 3vw, 30px) clamp(18px, 2vw, 24px); border-radius: 6px;
    border: 1px solid var(--clefs-line-soft); background: var(--clefs-surface);
  }
  .mk-stat:empty { display: none; }
  .mk-stat .mk-claim--stat { gap: 10px; }
  .mk-stat .mk-claim__val { font-size: clamp(2.1rem, 3.6vw, 2.8rem); color: var(--clefs-gold-400); }
  .mk-stat .mk-claim__label { font-size: 0.95rem; line-height: 1.6; color: var(--clefs-ink); opacity: 0.86; }
  /* The source link is small type; give a thumb more to hit. */
  .mk-stat .mk-claim__src a { display: inline-block; padding: 6px 4px; margin: -6px -4px; }

  /* ══ CTA + FOOTER ══ */
  .mk-cta__btns { display: flex; justify-content: center; flex-wrap: wrap; gap: 14px; }
  .mk-footer__groups { display: flex; flex-wrap: wrap; gap: 20px clamp(28px, 5vw, 64px); }
  .mk-footer__group { display: flex; flex-direction: column; gap: 9px; min-width: 150px; }
  .mk-footer__h { margin: 0 0 4px; font-size: 0.66rem; letter-spacing: 0.16em; text-transform: uppercase; color: var(--clefs-ink-muted); }
  .mk-footer__group a { font-size: 0.86rem; color: var(--clefs-ink-muted); text-decoration: none; transition: color .3s var(--ease-out); }
  .mk-footer__group a:hover { color: var(--clefs-gold-500); }
  /* Three link groups push the copyright onto its own row; give it the full
     width instead of leaving it right-aligned under the brand. */
  .cb-footer__inner .cb-footer__copy { flex-basis: 100%; text-align: left; padding-top: 18px; border-top: 1px solid var(--clefs-line-soft); }

  @media (max-width: 1000px) {
    .mk-cards--3, .mk-cards--4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .mk-bench, .mk-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .mk-plans { grid-template-columns: 1fr; max-width: 460px; margin: 0 auto; }
    .mk-lived { grid-template-columns: 1fr; }
    .mk-footer__groups { justify-content: center; text-align: left; }
    .cb-footer__inner .cb-footer__copy { text-align: center; }
  }
  @media (max-width: 620px) {
    .mk-cards--3, .mk-cards--4, .mk-stack, .mk-needs, .mk-stats { grid-template-columns: 1fr; }
    .mk-bench { grid-template-columns: 1fr; }
    .mk-proof__list { flex-direction: column; align-items: flex-start; }
    .cb-hero__btns .cb-btn, .mk-cta__btns .cb-btn { white-space: normal; text-align: center; line-height: 1.35; justify-content: center; }
    .mk-cta__btns { flex-direction: column; }
    .mk-cta__btns .cb-btn { width: 100%; justify-content: center; }
    .mk-road__item { grid-template-columns: 1fr; gap: 4px; }
    .mk-footer__groups { flex-direction: column; align-items: center; text-align: center; }
    .mk-footer__group { align-items: center; }
  }
`;
