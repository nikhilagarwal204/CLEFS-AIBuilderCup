"use client";
import { useState, useRef, useEffect, useCallback, useMemo, memo } from "react";
import { LuMic, LuSquare, LuSend, LuKeyboard, LuSubtitles, LuX, LuShieldCheck } from "react-icons/lu";
import Image from "next/image";
import {
  fetchStayContext, startSession, endSession, withdrawByGuestRef, runToolCall, postTurnCall, chatCall, placeOrderViaSession, placeOrderViaMenu,
  type StayContext, type SessionInfo, type ConsentGrants, type ToolUi, type ToolResult, type EmergencyNumber, type SessionMode, type Transport,
  type PlacedOrder,
} from "./stay/concierge/api";
import FoodMenu, { type MenuOrderLine } from "./stay/FoodMenu";
import { resolveAssistantName } from "./lib/assistant";
import { ApiError } from "./lib/api";
import { GUEST_LANGUAGES, makeT, resolveGuestLanguage, storeLang, langDir, ensureLangFont, langFontStack, type GuestLanguage } from "./stay/concierge/i18n";
import { LivePeer, LiveChannel, LiveSender, armConnectProbe, isNetworkBlock } from "./stay/concierge/transport";
import * as stayStore from "./stay/concierge/storage";
import ConsentSheet from "./stay/concierge/ConsentSheet";
import LanguagePicker from "./stay/concierge/LanguagePicker";
import StatusTracker, { TrackerPill, useTrackerStatuses, type TrackedPatch } from "./stay/concierge/StatusTracker";
import GuestSheet from "./stay/concierge/GuestSheet";
import EmergencyCard from "./stay/concierge/EmergencyCard";
import DocumentMeta from "./stay/concierge/DocumentMeta";
import { CC_STYLES } from "./stay/concierge/styles";
import { offeredLanguages } from "./stay/concierge/languages";
import {
  readMouthFeatures,
  VisemeSelector,
  VisemeRenderer,
  loadVisemeRig,
  VISEME_FFT_SIZE,
} from "./lib/visemeRig";


// ─── Server-built sessions (03 spec 2.1, 2.6) ─────────────────
// The prompt, tools, model, voice and greeting are built by the Clefs
// server (POST /public/concierge/sessions via /api/live-session). The browser
// no longer assembles instructions: that is what let a hardcoded rupee menu
// and an IST greeting ship to UAE hotels, and let any page mint a key with
// any prompt. The page only picks the transport and the output modality.

/** The hotel fields the page renders. Everything else comes from the stay context. */
interface HotelView {
  name: string;
  city: string;
  assistant_name: string;
}

// Chips that file a request directly (no LLM round trip): the model used to
// be told to call notify_staff by a response instruction, which replaced the
// session prompt for that turn and could still be ignored. Room surfaces only.
const DIRECT_CHIP_REQUESTS: Record<string, { request_type: string; description_en: string; quantity?: number }> = {
  towels: { request_type: "towels", description_en: "Extra towels requested from the guest page.", quantity: 2 },
  water: { request_type: "water", description_en: "Bottled water requested from the guest page.", quantity: 2 },
  housekeeping: { request_type: "make_up_room", description_en: "Room cleaning requested from the guest page." },
  room_service_person: { request_type: "ird", description_en: "Guest asked for a room service staff member to come to the room." },
};

/** The server's greeting, spoken verbatim (hotel timezone, guest language). */
function greetingInstructionFor(ctx: StayContext | null, session: SessionInfo | null): string {
  const text = (ctx?.greeting_text || session?.greeting_text || "").replace(/"/g, "'");
  return `Say exactly: "${text}" Nothing else.`;
}

// Server chip labels arrive in English and Arabic (or as the hotel's own
// custom text); the other guest languages use the dictionary for known keys.
const SERVER_LOCALIZED_CHIP_LANGS = new Set(["en", "ar"]);

// Which avatar renders the mouth. The looping video is smooth but not synced;
// the viseme rig is synced but composites discrete photos. Set at build time via
// NEXT_PUBLIC_AVATAR_RIG=viseme to evaluate the rig; anything else is the video.
const AVATAR_USE_VISEME_RIG = process.env.NEXT_PUBLIC_AVATAR_RIG === "viseme";

const STATES = {
  IDLE: "idle",
  LISTENING: "listening",
  THINKING: "thinking",
  SPEAKING: "speaking",
};

// The greeting ("Good evening ...") is computed by the server in the
// hotel's own timezone and language; see greeting_text in the stay context.

const injectStyles = `
  @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap');

  /* Stone grain. Fixed rather than scrolling, and pointer-events:none so it
     can never intercept a tap on the mic. */
  .cx-grain {
    position: absolute; inset: 0; z-index: 0; pointer-events: none;
    background-image: url('/brand/slate-grain.png');
    background-size: 256px 256px;
    opacity: 0.42;
  }

  /* The key between two hairlines, under the assistant's name. */
  .cx-splash-mark {
    display: flex; align-items: center; justify-content: center; gap: 14px;
    margin-bottom: 16px;
  }
  .cx-splash-mark img { height: 40px; width: auto; opacity: 0.95; }
  .cx-splash-rule {
    width: clamp(34px, 12vw, 62px); height: 1px;
    background: linear-gradient(90deg, transparent, rgba(225,175,104,0.45), transparent);
  }

  @keyframes breathe {
    0%, 100% { transform: scale(1) translateZ(0); }
    50% { transform: scale(1.008) translateZ(0); }
  }
  /* Splash screen only - the concierge avatar no longer pulses while speaking. */
  @keyframes speakingPulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(225, 175, 104,0.3), inset 0 0 60px rgba(254,252,246,0); }
    50% { box-shadow: 0 0 40px 12px rgba(225, 175, 104,0.15), inset 0 0 60px rgba(254,252,246,0.08); }
  }
  @keyframes listeningRing {
    0%, 100% { box-shadow: 0 0 0 0 rgba(231,211,163,0.4); }
    50% { box-shadow: 0 0 30px 10px rgba(231,211,163,0.15); }
  }
  @keyframes subtleFloat {
    0%, 100% { transform: translateY(0) translateZ(0); }
    50% { transform: translateY(-3px) translateZ(0); }
  }
  @keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }
  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(16px) translateZ(0); }
    to { opacity: 1; transform: translateY(0) translateZ(0); }
  }
  @keyframes fadeDown {
    from { opacity: 0; transform: translateY(-8px) translateZ(0); }
    to { opacity: 1; transform: translateY(0) translateZ(0); }
  }
  @keyframes pulseRing {
    0% { transform: scale(1) translateZ(0); opacity: 0.6; }
    100% { transform: scale(1.8) translateZ(0); opacity: 0; }
  }
  @keyframes wave {
    from { height: 4px; }
    to { height: 28px; }
  }
  @keyframes shimmer {
    0% { background-position: -200% 0; }
    100% { background-position: 200% 0; }
  }
  @keyframes bgPan {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
  }
  @keyframes micPulse {
    0%, 100% { transform: scale(1) translateZ(0); }
    50% { transform: scale(1.06) translateZ(0); }
  }
  @keyframes speakWave {
    0% { opacity: 0.15; transform: scaleY(0.3) translateZ(0); }
    50% { opacity: 0.6; transform: scaleY(1) translateZ(0); }
    100% { opacity: 0.15; transform: scaleY(0.3) translateZ(0); }
  }
  @keyframes blink {
    0%, 49%, 100% { opacity: 1; }
    50%, 99% { opacity: 0; }
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  ::-webkit-scrollbar { width: 0; }

  /* Touch feedback for interactive elements on mobile */
  button { -webkit-tap-highlight-color: transparent; touch-action: manipulation; }
  input { -webkit-tap-highlight-color: transparent; }

  /* Prevent text selection on UI elements during interaction */
  .no-select { -webkit-user-select: none; user-select: none; }

  /* Quick action chips styling */
  .quick-chip {
    background: rgba(225, 175, 104,0.05);
    border: 1px solid rgba(225, 175, 104,0.12);
    border-radius: 20px;
    padding: 8px 14px;
    color: rgba(231,211,163,0.55);
    font-size: 11px;
    font-family: 'Inter', sans-serif;
    transition: all 0.25s ease;
    user-select: none;
    -webkit-user-select: none;
    -webkit-touch-callout: none;
    -webkit-tap-highlight-color: transparent;
  }
  
  @media (hover: hover) {
    .quick-chip:hover:not(:disabled) {
      background: rgba(225, 175, 104,0.12);
      color: #FDDA96;
      border-color: rgba(225, 175, 104,0.3);
      box-shadow: 0 2px 12px rgba(225, 175, 104,0.1);
    }
  }
  .quick-chip:active:not(:disabled) {
    background: rgba(225, 175, 104,0.22);
    color: #FDDA96;
    transform: scale(0.96);
  }

  /* Prominent Room Service Menu Button */
  .menu-btn {
    background: var(--hc-gold, #E1AF68);
    border: 1px solid var(--hc-gold, #E1AF68);
    border-radius: 20px;
    padding: 10px 18px;
    color: #0C1B15;
    font-size: 12px;
    font-weight: 700;
    font-family: 'Inter', sans-serif;
    box-shadow: 0 4px 15px rgba(225, 175, 104, 0.25);
    transition: all 0.25s ease;
    user-select: none;
    -webkit-user-select: none;
    -webkit-touch-callout: none;
    -webkit-tap-highlight-color: transparent;
  }

  @media (hover: hover) {
    .menu-btn:hover:not(:disabled) {
      background: #D9B85C;
      border-color: #D9B85C;
      box-shadow: 0 6px 20px rgba(225, 175, 104, 0.4);
    }
  }
  .menu-btn:active:not(:disabled) {
    background: #B9983C;
    border-color: #B9983C;
    transform: scale(0.96);
  }
`;

const AVATAR_GLOWS = {
  [STATES.IDLE]: "0 0 60px rgba(225, 175, 104,0.08)",
  [STATES.LISTENING]: "0 0 0 4px rgba(231,211,163,0.3), 0 0 50px rgba(231,211,163,0.15)",
  [STATES.THINKING]: "0 0 0 3px rgba(225, 175, 104,0.3), 0 0 40px rgba(225, 175, 104,0.12)",
  [STATES.SPEAKING]: "0 0 0 2px rgba(225,175,104,0.28), 0 0 46px rgba(225,175,104,0.13)",
};

const AVATAR_ANIMATIONS = {
  [STATES.IDLE]: "breathe 4s ease-in-out infinite",
  [STATES.LISTENING]: "listeningRing 1.5s ease-in-out infinite",
  [STATES.THINKING]: "breathe 1.5s ease-in-out infinite",
  // No pulse while speaking. A ring throbbing on a 1.2s loop reads as a toy and
  // has nothing to do with the voice; the face itself carries the motion.
  [STATES.SPEAKING]: "none",
};

// Dictionary keys, rendered through t() in the guest's language.
const STATUS_LABELS = {
  [STATES.IDLE]: "status.idle",
  [STATES.LISTENING]: "status.listening",
  [STATES.THINKING]: "status.thinking",
  [STATES.SPEAKING]: "status.speaking",
};

const STATUS_COLORS = {
  [STATES.IDLE]: "rgba(225, 175, 104,0.4)",
  [STATES.LISTENING]: "var(--hc-soft-gold)",
  [STATES.THINKING]: "var(--hc-gold)",
  [STATES.SPEAKING]: "var(--hc-gold)",
};

// VAD activation threshold, 0..1, default 0.5. Higher demands louder audio
// before a turn opens, which is what a hotel lobby needs: the failure that
// matters here is the concierge answering a passing conversation, not missing
// a word from a guest who is holding the phone and can simply repeat it.
const VOICE_TURN_DETECTION = {
  type: "server_vad",
  threshold: 0.65,
  prefix_padding_ms: 300,
  silence_duration_ms: 1000,
};

// Used once the guest has tapped the mic. This used to be 0.4 - i.e. *more*
// sensitive than before they engaged - which is exactly backwards: that is the
// window where the phone may be sitting on a table in a noisy room.
const ACTIVE_VOICE_TURN_DETECTION = {
  type: "server_vad",
  threshold: 0.65,
  prefix_padding_ms: 400,
  silence_duration_ms: 1000,
};

// How long the mic stays open with no guest speech before it closes itself.
// The clock only runs while the avatar is waiting: never while the assistant is
// answering (so the guest can barge in) and never mid-utterance. Five seconds is
// past the point where a guest composing a quick follow-up would lose their turn, and
// well short of a phone set down quietly streaming the room.
const LISTEN_IDLE_TIMEOUT_MS = 5000;
// Ceiling for the whole open mic. Ambient speech can re-trigger VAD without ever
// committing a turn, which would otherwise hold the idle timer off indefinitely.
// Reset by real interactions, so an active conversation never hits it.
const LISTEN_MAX_OPEN_MS = 60000;
// The ceiling defers while a turn is genuinely in progress, but not forever:
// this bounds an open mic at LISTEN_MAX_OPEN_MS + this, whatever the VAD does.
const LISTEN_MAX_DEFER_MS = 60000;
const LISTEN_CEILING_RECHECK_MS = 5000;
// How long the "mic paused" hint stays up after an automatic close.
const MIC_PAUSED_HINT_MS = 5000;

/**
 * True when a committed turn carries no speech worth answering.
 *
 * Deliberately conservative: it only rejects transcripts that cannot be a real
 * request - empty, or punctuation and stray marks with no letters in any script.
 * It does NOT filter short real words. The transcriber does hallucinate stock phrases
 * over silence ("Thank you.", "Thanks for watching!"), but those are also things
 * guests genuinely say, and ignoring a guest is a worse failure than one stray
 * reply. Noise reduction and the VAD threshold are what stop those turns opening.
 */
function isEmptyUtterance(transcript: string): boolean {
  const s = transcript.trim();
  if (!s) return true;
  // \p{L} covers every script the concierge answers in, not just Latin, so a
  // Hindi or Bengali reply is never mistaken for noise.
  return !/\p{L}/u.test(s);
}

// `language` is a transcription hint, passed only when the guest picked a
// language explicitly (?lang or the picker). A browser locale is not a hint:
// it says little about what the guest will actually speak, and a wrong hint
// makes The transcriber transcribe into the wrong script.
const buildLiveAudioConfig = (
  turnDetection: typeof VOICE_TURN_DETECTION | typeof ACTIVE_VOICE_TURN_DETECTION | null,
  language: string | null = null,
) => ({
  input: {
    transcription: {
      ...(language ? { language } : {}),
    },
    // Filters the input before it reaches VAD, so lobby chatter and room noise
    // are far less likely to open a turn at all. "near_field" is correct for a
    // guest holding their own phone; a fixed lobby kiosk with a distant mic
    // would want "far_field" instead.
    noise_reduction: { type: "near_field" },
    turn_detection: turnDetection,
  },
});

const SoundWave = memo(function SoundWave({ active, color = "var(--hc-gold)", barCount = 12 }: { active: boolean; color?: string; barCount?: number }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 3, height: 32, transform: "translateZ(0)" }}>
      {Array.from({ length: barCount }).map((_, i) => (
        <div
          key={i}
          style={{
            width: 3,
            borderRadius: 2,
            background: color,
            opacity: active ? 0.75 : 0.12,
            height: active ? undefined : 3,
            animation: active
              ? `wave 0.6s ease-in-out ${i * 0.05}s infinite alternate`
              : "none",
            transition: "opacity 0.3s",
            transform: "translateZ(0)",
          }}
        />
      ))}
    </div>
  );
});

export default function ClefsConcierge({ roomNumber = null, hotelSlug = null, location = null }: { roomNumber?: string | number | null; hotelSlug?: string | null; location?: string | null }) {
  //  Core RTC refs
  const pcRef = useRef<LivePeer | null>(null);
  const dcRef = useRef<LiveChannel | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const textBufferRef = useRef("");
  const processingBufferRef = useRef(false);
  // Incremented on every interrupt/new-response so stale typing timeouts self-abort
  const textGenerationRef = useRef(0);
  // Suppress text/transcript display while a tool call is in flight
  const isFunctionCallActiveRef = useRef(false);
  // Dedup: track call_ids already handled + a short-window key to stop the model
  // from looping the same logical request (same room+type) within 10 seconds.
  const handledCallIdsRef = useRef<Set<string>>(new Set());
  const recentNotifyKeyRef = useRef<{ key: string; at: number } | null>(null);

  // Silence detection refs (BUG 1 fix)
  const remoteAudioContextRef = useRef<AudioContext | null>(null);
  const remoteAnalyserRef = useRef<AnalyserNode | null>(null);
  const silenceStartRef = useRef<number | null>(null);
  const silenceDetectionFrameRef = useRef<number | null>(null);
  const speechDetectionFrameRef = useRef<number | null>(null);
  const avatarStateRef = useRef(STATES.IDLE);

  // Lip-motion gating. The mouth must move only while sound is actually
  // audible, so it is driven by the live envelope of the remote audio track.
  // The transcript stream can NOT drive it: deltas arrive before the audio
  // they describe, which makes the avatar mouth words ahead of the voice.
  const mouthActiveRef = useRef(false);
  // Set false by output_audio_buffer.started, true by .stopped/.cleared. While
  // false, a silent stretch is a pause between words; once true, the server has
  // no more audio to send, so silence is the real end of the turn.
  const audioBufferDoneRef = useRef(true);
  // Whether this deployment actually emits those events at all. If it does not,
  // the tracker falls back to a longer silence before ending a turn.
  const audioBufferEventsSeenRef = useRef(false);
  // Whether a reply is currently being generated. Cancelling when none is in
  // flight just makes the server emit an error event into the console.
  const responseInFlightRef = useRef(false);
  // Restores background playback after the fade-out, so the decoder stays warm
  // on iOS without the frozen mouth twitching back to life mid-fade.
  const idlePlaybackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set by the envelope tracker; lets an interrupt freeze the mouth on the same
  // frame the audio is cut, instead of waiting for the next animation frame.
  const setMouthActiveRef = useRef<((active: boolean) => void) | null>(null);

  // Viseme rig. Present only when public/avatar/visemes.json has been deployed;
  // without it the avatar falls back to the looping video exactly as before, so
  // dropping the assets in is the entire switch-over.
  const visemeCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const visemeRigRef = useRef<{
    selector: VisemeSelector;
    renderer: VisemeRenderer;
  } | null>(null);
  const [visemeReady, setVisemeReady] = useState(false);

  // Promise that resolves when the data channel is truly open
  const dcOpenPromiseRef = useRef<Promise<void> | null>(null);

  // AudioContext for silent stream in text mode (no mic needed)
  const silentAudioContextRef = useRef<AudioContext | null>(null);

  // Dynamic mode tracking (can change mid-session via keyboard toggle)
  const currentModeRef = useRef<"voice" | "text">("voice");
  // Tracks whether the initial greeting is playing
  const isGreetingPhaseRef = useRef(false);
  // Ref mirror of isRecording state (for use in rAF loops)
  const isRecordingRef = useRef(false);
  // Auto-close timers for an open mic. The idle one is reset by any guest
  // speech; the ceiling one only by a real completed interaction.
  const listenIdleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const listenMaxOpenTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const micPausedHintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // True between VAD speech_started and speech_stopped. The mic must never close
  // while the guest is mid-sentence, however long the sentence runs.
  const guestSpeakingRef = useRef(false);
  // RTCRtpSender for the audio track - lets us replaceTrack() after pre-warm
  const audioSenderRef = useRef<LiveSender | null>(null);

  //  State
  const [avatarState, setAvatarState] = useState(STATES.IDLE);
  const [isRecording, setIsRecording] = useState(false);
  // Shown briefly after the mic closes itself, so a guest who walks back never
  // finds a dead mic with no explanation.
  const [micPausedHint, setMicPausedHint] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [responseText, setResponseText] = useState("");
  const [displayedText, setDisplayedText] = useState("");
  const [showSubtitle, setShowSubtitle] = useState(true);
  const [hasInteracted, setHasInteracted] = useState(false);
  const [showTextInput, setShowTextInput] = useState(false);
  const [textInput, setTextInput] = useState("");
  const [inputMode, setInputMode] = useState("voice");
  const [videoReady, setVideoReady] = useState(false);
  const [hasGreeted, setHasGreeted] = useState(false);
  const [sessionStarted, setSessionStarted] = useState(false);
  const [viewMode, setViewMode] = useState<"splash" | "concierge" | "menu">("splash");

  // ─── Stay context, language and session (03 spec 2.6) ───
  // The QR target: a room number, or a venue slug in location mode.
  const target = String(roomNumber ?? location ?? "public");
  const [ctx, setCtx] = useState<StayContext | null>(null);
  const ctxRef = useRef<StayContext | null>(null);
  const [lang, setLang] = useState<GuestLanguage>("en");
  const langRef = useRef<GuestLanguage>("en");
  // True when the language came from ?lang or the picker; only then is it
  // sent as the transcription hint (see buildLiveAudioConfig).
  const explicitLangRef = useRef(false);
  const t = useMemo(() => makeT(lang), [lang]);
  // The server session (id, bearer token, key, greeting). The session id
  // comes from the server; it is never derived from the room number.
  const sessionRef = useRef<SessionInfo | null>(null);
  // "http" once the ICE probe fails or the Live endpoint is blocked:
  // text chat over POST .../chat, and the avatar never enters SPEAKING.
  const transportRef = useRef<Transport>("live");
  const [transport, setTransport] = useState<Transport>("live");
  // ConsentSheet choices, sent with the next non-prewarm mint only.
  const pendingGrantsRef = useRef<ConsentGrants | null>(null);
  // What to run once the ConsentSheet is answered; null while it is closed.
  const [consentFor, setConsentFor] = useState<null | ((mode: "voice" | "text") => void)>(null);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const [privacyState, setPrivacyState] = useState<"idle" | "busy" | "done" | "forgotten" | "failed">("idle");
  const [emergency, setEmergency] = useState<EmergencyNumber[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tracked, setTracked] = useState<stayStore.TrackedItem[]>([]);
  const [trackerOpen, setTrackerOpen] = useState(false);
  // Lets a transport fallback release anyone awaiting the data channel.
  const dcOpenResolveRef = useRef<(() => void) | null>(null);
  const iceProbeCancelRef = useRef<(() => void) | null>(null);
  // The mode the live peer connection was opened in. A "text" connection has
  // a silent input and no remote audio playback, so the mic cannot join it.
  const rtcModeRef = useRef<SessionMode | null>(null);

  const hotelData: HotelView | null = ctx
    ? { name: ctx.hotel.name, city: ctx.hotel.city || "", assistant_name: ctx.hotel.assistant_name }
    : null;
  // Guest-facing persona name (per-hotel, DB-configurable). Not the Clefs brand.
  const assistantName = resolveAssistantName(hotelData);
  const [hotelLoading, setHotelLoading] = useState(!!hotelSlug);
  const [hotelError, setHotelError] = useState<string | null>(hotelSlug ? null : "not_found");

  const fadeOutTimeoutRef = useRef<NodeJS.Timeout | number | null>(null);

  const MAX_RETRIES = 2;

  useEffect(() => {
    langRef.current = lang;
    ensureLangFont(lang);
  }, [lang]);

  // One stay-context read replaces the old hotel, location and guest-roster
  // fetches: hotel, room verification, greeting, chips, features, consent
  // wording and emergency numbers, all in the guest's language.
  useEffect(() => {
    if (!hotelSlug) return; // hotelError starts as "not_found"
    let cancelled = false;
    const k = new URLSearchParams(window.location.search).get("k");
    const resolved = resolveGuestLanguage();
    explicitLangRef.current = resolved.explicit;
    fetchStayContext(hotelSlug, target, {
      k,
      lang: resolved.lang,
      stayToken: stayStore.getStayToken(hotelSlug, target),
      kind: location ? "location" : "room",
    })
      .then((c) => {
        if (cancelled) return;
        ctxRef.current = c;
        langRef.current = resolved.lang;
        setCtx(c);
        setLang(resolved.lang);
        if (c.stay_session_key) stayStore.setStayToken(hotelSlug, target, c.stay_session_key);
        setTracked(stayStore.getTracked(hotelSlug, target));
        setHotelLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setLang(resolved.lang); // the error screen speaks the guest's language too
        setHotelError(err instanceof ApiError && err.status === 404 ? "not_found" : "server_error");
        setHotelLoading(false);
      });
    return () => { cancelled = true; };
  }, [hotelSlug, target, location]);

  useEffect(() => {
    return () => {
      // Cancel all animation frame loops
      if (silenceDetectionFrameRef.current) cancelAnimationFrame(silenceDetectionFrameRef.current);
      if (speechDetectionFrameRef.current) cancelAnimationFrame(speechDetectionFrameRef.current);
      // Close audio contexts
      remoteAudioContextRef.current?.close().catch(() => {});
      silentAudioContextRef.current?.close().catch(() => {});
      // Close Live
      pcRef.current?.close();
      dcRef.current?.close();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      window.speechSynthesis.cancel();
    };
  }, []);

  //  Video preloading effect - mark ready once the actual rendered <video> loads
  //  (Old approach created a detached element that never helped on mobile.)
  useEffect(() => {
    // If the ref is already attached and has data, mark ready immediately
    const v = videoRef.current;
    if (v && v.readyState >= 2) {
      setVideoReady(true);
      return;
    }
    // Otherwise we rely on the onLoadedData handler on the rendered <video>
    // Set videoReady true as a fallback after 3 seconds even if the video
    // hasn't fully loaded (so the component doesn't stay hidden forever)
    const fallbackTimer = setTimeout(() => {
      setVideoReady(true);
    }, 3000);
    return () => clearTimeout(fallbackTimer);
  }, []);

  // Helper: ensure video is playing (safe to call anytime).
  // On mobile, play() may be rejected outside user-gesture context, so
  // we retry a few times with increasing delays as a fallback.
  const ensureVideoPlaying = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    const tryPlay = (attempt: number) => {
      if (!v.paused) return; // already playing
      v.play().catch(() => {
        // Retry up to 3 times with increasing delay (100, 300, 600ms)
        if (attempt < 3) {
          setTimeout(() => tryPlay(attempt + 1), (attempt + 1) * 200);
        }
      });
    };
    tryPlay(0);
  }, []);

  // ── Mic auto-close ──────────────────────────────────────────────────────
  // An open mic that never closes keeps capturing the room long after the guest
  // has finished. Closing it the moment the assistant answers would be worse:
  // it would cut off barge-ins and immediate follow-ups. So the mic stays open
  // through the answer and the pause after it, and only a genuine stretch of
  // silence closes it. The session itself stays warm, so the next tap is instant.

  const clearListenIdleTimer = useCallback(() => {
    if (listenIdleTimerRef.current) {
      clearTimeout(listenIdleTimerRef.current);
      listenIdleTimerRef.current = null;
    }
  }, []);

  const clearAllListenTimers = useCallback(() => {
    clearListenIdleTimer();
    if (listenMaxOpenTimerRef.current) {
      clearTimeout(listenMaxOpenTimerRef.current);
      listenMaxOpenTimerRef.current = null;
    }
  }, [clearListenIdleTimer]);

  const stopListening = useCallback((auto: boolean) => {
    clearAllListenTimers();
    if (streamRef.current) {
      streamRef.current.getAudioTracks().forEach((t) => { t.enabled = false; });
    }
    isRecordingRef.current = false;
    guestSpeakingRef.current = false;
    setIsRecording(false);
    // Never yank the avatar out of a reply that is still playing.
    setAvatarState((prev) => (prev === STATES.SPEAKING ? prev : STATES.IDLE));
    if (!auto) return;
    setMicPausedHint(true);
    if (micPausedHintTimerRef.current) clearTimeout(micPausedHintTimerRef.current);
    micPausedHintTimerRef.current = setTimeout(() => setMicPausedHint(false), MIC_PAUSED_HINT_MS);
  }, [clearAllListenTimers]);

  // Start (or restart) the countdown. `resetCeiling` marks a real interaction -
  // the mic opening, or the assistant finishing a reply - as opposed to mere
  // voice activity, which must not be able to hold the mic open forever.
  const armListenTimers = useCallback((resetCeiling: boolean) => {
    if (!isRecordingRef.current) return;
    clearListenIdleTimer();
    listenIdleTimerRef.current = setTimeout(() => {
      listenIdleTimerRef.current = null;
      stopListening(true);
    }, LISTEN_IDLE_TIMEOUT_MS);

    if (!resetCeiling && listenMaxOpenTimerRef.current) return;
    if (listenMaxOpenTimerRef.current) clearTimeout(listenMaxOpenTimerRef.current);
    // Absolute stop. Without it, a VAD that reports speech_started without a
    // matching speech_stopped would defer the ceiling forever - the exact stuck
    // mic this is here to prevent.
    const hardDeadline = Date.now() + LISTEN_MAX_OPEN_MS + LISTEN_MAX_DEFER_MS;
    const onCeiling = () => {
      // Cutting someone off mid-sentence, or mid-answer, is far worse than
      // holding the mic a little longer, so the ceiling waits its turn.
      const busy =
        guestSpeakingRef.current ||
        avatarStateRef.current === STATES.SPEAKING ||
        avatarStateRef.current === STATES.THINKING;
      if (busy && Date.now() < hardDeadline) {
        listenMaxOpenTimerRef.current = setTimeout(onCeiling, LISTEN_CEILING_RECHECK_MS);
        return;
      }
      listenMaxOpenTimerRef.current = null;
      stopListening(true);
    };
    listenMaxOpenTimerRef.current = setTimeout(onCeiling, LISTEN_MAX_OPEN_MS);
  }, [clearListenIdleTimer, stopListening]);

  // Reachable from inside startLive's closures without widening its deps.
  const armListenTimersRef = useRef<((resetCeiling: boolean) => void) | null>(null);
  const clearListenIdleTimerRef = useRef<(() => void) | null>(null);
  useEffect(() => {
    armListenTimersRef.current = armListenTimers;
    clearListenIdleTimerRef.current = clearListenIdleTimer;
  }, [armListenTimers, clearListenIdleTimer]);

  // Safety net: while the assistant is thinking or talking, the guest is not
  // being waited on, so the idle clock must not be running.
  useEffect(() => {
    if (avatarState === STATES.SPEAKING || avatarState === STATES.THINKING) {
      clearListenIdleTimer();
    }
  }, [avatarState, clearListenIdleTimer]);

  // Interrupt the current in-flight response: cancel server generation,
  // silence the Live audio output immediately, and clear all text state.
  // Call this before sending a new chip/question mid-response.
  const interruptResponse = useCallback(() => {
    // Tell the Live API to stop generating the current response
    if (dcRef.current?.readyState === "open") {
      dcRef.current.send(JSON.stringify({ type: "response.cancel" }));
    }
    // Mute the audio element so buffered audio doesn't keep playing.
    // It will be unmuted when response.created fires for the new response.
    if (audioRef.current) {
      audioRef.current.muted = true;
    }
    // Freeze the mouth on the same frame the audio is cut, and let the envelope
    // tracker settle the state itself. It is deliberately left running: cancelling
    // it here killed it for the rest of the session, since it is only ever started
    // from pc.ontrack - after one interrupt the avatar could never leave SPEAKING.
    audioBufferDoneRef.current = true;
    setMouthActiveRef.current?.(false);
    silenceStartRef.current = null;
    // Invalidate the current text-typing timeout chain
    textGenerationRef.current++;
    textBufferRef.current = "";
    processingBufferRef.current = false;
    setResponseText("");
    setDisplayedText("");
    setTranscript("");
  }, []);

  // Keep avatarStateRef in sync with avatarState (for use in rAF loops)
  // Video runs continuously in background (opacity 0.01) to prevent mobile Safari
  // decode stutters. We still call play() as a safeguard when entering SPEAKING,
  // because iOS browsers can suspend even muted autoplay videos.
  useEffect(() => {
    avatarStateRef.current = avatarState;
    if (avatarState === STATES.SPEAKING) {
      ensureVideoPlaying();
    }
  }, [avatarState, ensureVideoPlaying]);

  // Load the viseme rig only when explicitly enabled. It is off by default: the
  // looping video is the production avatar. The rig's assets stay deployed so it
  // can be switched on for evaluation without a redeploy of the artwork.
  useEffect(() => {
    if (!AVATAR_USE_VISEME_RIG) return;
    let cancelled = false;
    (async () => {
      try {
        const { manifest, base, sheet } = await loadVisemeRig("/avatar/visemes.json");
        const canvas = visemeCanvasRef.current;
        if (cancelled || !canvas) return;
        // Back the canvas at device resolution; a circular avatar shows a soft
        // mouth edge immediately at CSS pixels on a retina screen.
        const dpr = Math.min(3, window.devicePixelRatio || 1);
        const rect = canvas.getBoundingClientRect();
        canvas.width = Math.round((rect.width || 220) * dpr);
        canvas.height = Math.round((rect.height || 220) * dpr);
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        const renderer = new VisemeRenderer(ctx, manifest, base, sheet);
        const selector = new VisemeSelector(manifest.visemes);
        visemeRigRef.current = { selector, renderer };
        // Paint the resting face at once, so the rig does not pop in blank on
        // the first word.
        renderer.draw(manifest.visemes[0]?.index ?? 0, true);
        setVisemeReady(true);
      } catch {
        // No assets deployed, or they failed to load: keep the video.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // While the avatar is hidden the loop is left running, because iOS Safari
  // stalls on the next play() if the decoder has gone cold. It is only restarted
  // after the fade-out, so a frozen mouth never twitches back to life on screen.
  useEffect(() => {
    if (avatarState === STATES.SPEAKING || visemeReady) return;
    const t = setTimeout(() => {
      if (!mouthActiveRef.current) videoRef.current?.play().catch(() => { });
    }, 240);
    idlePlaybackTimerRef.current = t;
    return () => clearTimeout(t);
  }, [avatarState, visemeReady]);

  // Auto-recover if mobile browser pauses/suspends the video element.
  // This is common on iOS Safari when the video is at low opacity or
  // when the tab regains focus. We listen for 'pause' events and
  // restart playback if the avatar should be speaking.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const handlePause = () => {
      // Only fight pauses the browser imposed on us. A pause we asked for - a gap
      // between words, or the end of a turn - has to be allowed to stand, or the
      // mouth would restart the instant the envelope tracker freezes it.
      if (mouthActiveRef.current) {
        v.play().catch(() => {});
      }
    };
    v.addEventListener("pause", handlePause);
    return () => {
      v.removeEventListener("pause", handlePause);
    };
  }, [videoReady]);

  // Resume video when returning to the tab (iOS suspends video on tab switch)
  useEffect(() => {
    const onVisible = () => {
      // Never resume a mouth that was deliberately frozen mid-turn.
      if (document.visibilityState !== "visible") return;
      if (mouthActiveRef.current || avatarStateRef.current !== STATES.SPEAKING) {
        ensureVideoPlaying();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [ensureVideoPlaying]);

  // Keep isRecordingRef in sync (for rAF silence detection)
  useEffect(() => {
    isRecordingRef.current = isRecording;
  }, [isRecording]);

  const stopLive = useCallback(() => {

    // Clean up silence detection (BUG 1 fix)
    if (silenceDetectionFrameRef.current) {
      cancelAnimationFrame(silenceDetectionFrameRef.current);
      silenceDetectionFrameRef.current = null;
    }
    if (speechDetectionFrameRef.current) {
      cancelAnimationFrame(speechDetectionFrameRef.current);
      speechDetectionFrameRef.current = null;
    }
    if (remoteAudioContextRef.current) {
      remoteAudioContextRef.current.close().catch(() => { });
      remoteAudioContextRef.current = null;
      remoteAnalyserRef.current = null;
    }
    silenceStartRef.current = null;
    if (idlePlaybackTimerRef.current) {
      clearTimeout(idlePlaybackTimerRef.current);
      idlePlaybackTimerRef.current = null;
    }
    setMouthActiveRef.current = null;
    mouthActiveRef.current = false;
    audioBufferDoneRef.current = true;
    if (listenIdleTimerRef.current) {
      clearTimeout(listenIdleTimerRef.current);
      listenIdleTimerRef.current = null;
    }
    if (listenMaxOpenTimerRef.current) {
      clearTimeout(listenMaxOpenTimerRef.current);
      listenMaxOpenTimerRef.current = null;
    }
    if (micPausedHintTimerRef.current) {
      clearTimeout(micPausedHintTimerRef.current);
      micPausedHintTimerRef.current = null;
    }
    setMicPausedHint(false);
    dcOpenPromiseRef.current = null;
    iceProbeCancelRef.current?.();
    iceProbeCancelRef.current = null;
    if (silentAudioContextRef.current) {
      silentAudioContextRef.current.close().catch(() => { });
      silentAudioContextRef.current = null;
    }

    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.srcObject = null;
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }

    if (dcRef.current) {
      dcRef.current.close();
      dcRef.current = null;
    }

    if (pcRef.current) {
      pcRef.current.close();
      pcRef.current = null;
    }

    setIsRecording(false);
    setAvatarState(STATES.IDLE);
    setIsLoading(false);
  }, []);

  // ─── Tracker, ui and follow-ups ───

  const addTracked = useCallback((token: string, kind: "request" | "order") => {
    if (!hotelSlug) return;
    setTracked((prev) => {
      if (prev.some((i) => i.token === token)) return prev;
      const next = [...prev, { token, kind, at: Date.now() }];
      stayStore.saveTracked(hotelSlug, target, next);
      return next;
    });
    setTrackerOpen(true);
  }, [hotelSlug, target]);

  const patchTracked = useCallback((token: string, patch: TrackedPatch) => {
    if (!hotelSlug) return;
    setTracked((prev) => {
      const next = patch === null
        ? prev.filter((i) => i.token !== token)
        : prev.map((i) => (i.token === token ? { ...i, ...patch } : i));
      stayStore.saveTracked(hotelSlug, target, next);
      return next;
    });
  }, [hotelSlug, target]);

  const { statuses: trackerStatuses, setStatuses: setTrackerStatuses } = useTrackerStatuses(tracked, patchTracked);

  /** Acts on a server `ui` hint: emergency card, tracker entry, tracker open. */
  const handleUi = useCallback((ui: ToolUi | null | undefined) => {
    if (!ui) return;
    if (ui.kind === "emergency") {
      setEmergency(ui.numbers?.length ? ui.numbers : ctxRef.current?.emergency_numbers ?? []);
      return;
    }
    if (ui.kind === "tracker") {
      setTrackerOpen(true);
      return;
    }
    if (ui.token) addTracked(ui.token, ui.kind === "order" ? "order" : "request");
  }, [addTracked]);

  /** Speak (or write) the server's follow-up over the live data channel. */
  const speakFollowup = useCallback((instructions: string, interrupt: boolean) => {
    const dc = dcRef.current;
    if (dc?.readyState !== "open") return;
    if (interrupt && responseInFlightRef.current) interruptResponse();
    dc.send(JSON.stringify({ type: "response.create", response: { instructions } }));
  }, [interruptResponse]);

  // Was logConversation. Turns go to the session's /turns route, which logs
  // them only if the guest allowed recording and runs the safety double
  // check on guest turns: an emergency comes back as ui + a follow-up.
  const postTurn = useCallback((role: "user" | "assistant", text: string, itemId?: string | null) => {
    const s = sessionRef.current;
    if (!s || !text.trim()) return;
    postTurnCall(s.session_id, s.session_token, {
      role, text, item_id: itemId, language: explicitLangRef.current ? langRef.current : null,
    })
      .then((r) => {
        if (r.ui) handleUi(r.ui);
        if (role === "user" && r.escalated && r.followup_instructions) speakFollowup(r.followup_instructions, true);
      })
      .catch(() => {}); // fire-and-forget: never blocks the conversation
  }, [handleUi, speakFollowup]);

  // Generic tool dispatch: every tool runs on the server, bound to the
  // session's room. The client relays arguments and output, handles `ui`,
  // and sends the server's follow-up with response.create.
  const runTool = useCallback(async (name: string, rawArgs: string, callId: string) => {
    const s = sessionRef.current;
    let result: ToolResult;
    try {
      if (!s) throw new Error("no_session");
      result = await runToolCall(s.session_id, s.session_token, name, rawArgs, callId);
    } catch (err) {
      result = {
        status: "error",
        code: err instanceof ApiError ? err.code || `http_${err.status}` : "network_error",
        followup_instructions:
          "The request could not be sent. In one short sentence, apologise and suggest calling the front desk. Do not call any tools.",
      };
    }
    handleUi(result.ui);
    const dc = dcRef.current;
    if (dc?.readyState !== "open") return;
    // The model sees the result, not the client-only ui hint or the follow-up
    // (which becomes this turn's response instructions instead).
    const followup = result.followup_instructions;
    const output: Record<string, unknown> = { ...result };
    delete output.followup_instructions;
    delete output.ui;
    dc.send(JSON.stringify({
      type: "conversation.item.create",
      item: { type: "function_call_output", call_id: callId, output: JSON.stringify(output) },
    }));
    isFunctionCallActiveRef.current = false;
    dc.send(JSON.stringify(followup ? { type: "response.create", response: { instructions: followup } } : { type: "response.create" }));
  }, [handleUi]);

  // ─── Minting and transport ───

  const describeStartError = useCallback((err: unknown): string => {
    if (err instanceof ApiError) {
      if (err.code === "client_outdated") return t("errors.outdated");
      // concierge_not_live: the hotel has not passed the readiness gate yet.
      if (err.code === "ai_disabled" || err.code === "concierge_not_live") return t("errors.aiOff");
      if (err.status === 429) return t("errors.busy");
    }
    return t("errors.connection");
  }, [t]);

  /** POST /api/live-session (proxy to the server's session route). */
  const mintSession = useCallback(async (mode: SessionMode, via: Transport): Promise<SessionInfo> => {
    const c = ctxRef.current;
    if (!c || !hotelSlug) throw new Error("context_missing");
    const grants = mode === "prewarm" ? null : pendingGrantsRef.current;
    try {
      const info = await startSession({
        hotel: hotelSlug,
        target: c.room ? { room: String(c.room.number) } : { location: c.location?.slug ?? target },
        k: new URLSearchParams(window.location.search).get("k"),
        stay_token: stayStore.getStayToken(hotelSlug, target),
        lang: explicitLangRef.current ? langRef.current : null,
        mode,
        transport: via,
        guest_ref: stayStore.getGuestRef(hotelSlug),
        ...(grants ? { consents: grants } : {}),
      });
      pendingGrantsRef.current = null;
      stayStore.setGuestRef(hotelSlug, info.guest_ref);
      if (info.stay_token) stayStore.setStayToken(hotelSlug, target, info.stay_token);
      sessionRef.current = info;
      return info;
    } catch (err) {
      // Consent this browser remembers but the server does not have (a new
      // guest_ref, a withdrawal elsewhere): forget it so the sheet shows again.
      if (err instanceof ApiError && err.code === "consent_required") stayStore.clearConsent(hotelSlug);
      throw err;
    }
  }, [hotelSlug, target]);

  // Live is not getting through (ICE timeout or failure, or the Live
  // endpoint is blocked): tear it down and continue as HTTP text chat. The
  // server session stays valid, so /chat reuses its bearer token.
  const fallbackToHttp = useCallback(() => {
    if (transportRef.current === "http") return;
    transportRef.current = "http";
    setTransport("http");
    dcOpenResolveRef.current?.();
    dcOpenResolveRef.current = null;
    stopLive();
    currentModeRef.current = "text";
    setInputMode("text");
    setShowTextInput(true);
    setNotice(t("errors.voiceFallback"));
  }, [stopLive, t]);

  const startLive = useCallback(
    async (retryCount = 0, mode: "voice" | "text" | "chip" | "prewarm" = "voice", shouldGreet = false) => {
      return new Promise<void>((resolve, reject) => {
        // Already on the HTTP text path: there is nothing to negotiate.
        if (isLoading || pcRef.current || transportRef.current === "http") {
          resolve();
          return;
        }

        // Browser feature detection. No Live at all: text chat over HTTP
        // still works, so fall back instead of failing.
        if ((typeof WebSocket === "undefined" || typeof AudioContext === "undefined")) {
          if (mode === "prewarm") {
            reject(new Error("Live voice not supported"));
            return;
          }
          fallbackToHttp();
          setError(t("errors.noWebRtc"));
          resolve();
          return;
        }
        if (mode === "voice" && (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)) {
          setError(t("errors.noMic"));
          reject(new Error("getUserMedia not supported"));
          return;
        }

        const isVoice = mode === "voice";
        const isText = mode === "text";
        const isChip = mode === "chip";
        const isPrewarm = mode === "prewarm";

        setIsLoading(true);
        rtcModeRef.current = mode;
        setIsRecording(false); // Mic starts muted; user clicks mic to enable
        setInputMode(isVoice ? "voice" : "text");
        currentModeRef.current = (isVoice || isPrewarm) ? "voice" : "text";
        isFunctionCallActiveRef.current = false;
        handledCallIdsRef.current.clear();
        recentNotifyKeyRef.current = null;
        setError(null);
        setAvatarState(STATES.IDLE); // Always start IDLE; greeting or user action transitions
        setResponseText("");

        const pc = new LivePeer();
        pcRef.current = pc;

        const audioEl = new Audio();
        audioEl.autoplay = true;
        audioRef.current = audioEl;

        // In text/chip mode, skip mic entirely - create a silent audio stream.
        // This guarantees zero audio data reaches the server, completely
        // eliminating any possibility of VAD triggering from ambient noise.
        const getStream = !isVoice
          ? (() => {
            const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
            silentAudioContextRef.current = ctx;
            const dest = ctx.createMediaStreamDestination();
            return Promise.resolve(dest.stream);
          })()
          : navigator.mediaDevices.getUserMedia({ audio: true });

        getStream
          .then((stream) => {
            streamRef.current = stream;

            stream.getTracks().forEach((track) => {
              const sender = pc.addTrack(track, stream);
              // Store audio sender so START CONVERSATION can replaceTrack() after pre-warm
              if (track.kind === "audio") audioSenderRef.current = sender;
            });

            // Mute mic initially - user clicks mic button to enable listening
            if (isVoice) {
              stream.getAudioTracks().forEach(t => { t.enabled = false; });
            }

            if (isVoice || isChip || isPrewarm) {
              pc.ontrack = (e) => {
                const remoteStream = e.streams[0];
                audioEl.srcObject = remoteStream;
                audioEl.play().catch((err) => {
                  if (mode !== "prewarm") {
                    // Audio playback may fail on mobile before user gesture
                  }
                });

                // BUG 1 fix: Monitor remote audio stream volume to detect
                // when AI stops speaking. MediaStream onended never fires
                // because the stream stays open for the entire Live session.
                try {
                  const remoteAudioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
                  const remoteSource = remoteAudioCtx.createMediaStreamSource(remoteStream);
                  const remoteAnalyser = remoteAudioCtx.createAnalyser();
                  // Sized for the viseme rig's centroid: 512 bins gives ~47Hz
                  // resolution, enough to separate a rounded vowel from a spread
                  // one. RMS is unaffected by the larger window.
                  remoteAnalyser.fftSize = VISEME_FFT_SIZE;
                  // The default smoothing (0.8) low-passes the envelope and takes
                  // ~150ms to decay past a silence threshold after the voice has
                  // already stopped. That decay was a large part of the lag that
                  // left the avatar mouthing into silence, so: no smoothing.
                  remoteAnalyser.smoothingTimeConstant = 0;
                  remoteSource.connect(remoteAnalyser);

                  remoteAudioContextRef.current = remoteAudioCtx;
                  remoteAnalyserRef.current = remoteAnalyser;

                  // Waveform RMS, not a frequency-bin average: it is the actual
                  // amplitude of what the speaker is playing right now, so it
                  // tracks speech onsets and stops sample-accurately.
                  const timeDomain = new Uint8Array(remoteAnalyser.fftSize);
                  const ON_RMS = 0.010;   // speech is comfortably above this
                  const OFF_RMS = 0.006;  // hysteresis, so held vowels don't chatter
                  // Hangover before the mouth freezes mid-turn. Generous on
                  // purpose: 110ms rode through stop-consonants but not breaths,
                  // so the avatar froze and restarted inside single sentences and
                  // read as stop-start rather than speech. This costs nothing at
                  // the end of a turn, because once the server confirms the audio
                  // is finished the cut drops to END_HANGOVER_MS below - the
                  // asymmetry the marketing hero has to reconstruct by decoding
                  // its clip up front, since it has no such signal.
                  const GAP_HANGOVER_MS = 350;
                  // Once the server has finished sending, only the jitter buffer is
                  // left to drain, so the turn can be cut almost immediately.
                  const END_HANGOVER_MS = 40;
                  // Only used if the server sends no output_audio_buffer events:
                  // long enough not to end a turn on a pause between sentences.
                  const FALLBACK_END_MS = 320;

                  let lastLoudAt = 0;

                  // Runs the video only while sound is audible. Everything else
                  // (opacity, status label, glow) stays on avatarState so it does
                  // not flicker between sentences.
                  const setMouthActive = (active: boolean) => {
                    if (mouthActiveRef.current === active) return;
                    mouthActiveRef.current = active;
                    // The rig reads this ref, but renders the mouth itself, so
                    // the video stays parked once the assets are in.
                    if (visemeRigRef.current) return;
                    const v = videoRef.current;
                    if (!v) return;
                    if (idlePlaybackTimerRef.current) {
                      clearTimeout(idlePlaybackTimerRef.current);
                      idlePlaybackTimerRef.current = null;
                    }
                    if (active) {
                      v.play().catch(() => { });
                      return;
                    }
                    // Freeze on the spot. Background playback is resumed later, and
                    // only once the avatar has faded out, by the effect below.
                    v.pause();
                  };
                  setMouthActiveRef.current = setMouthActive;

                  // Frequency bins for the viseme rig's vowel-colour signal. Only
                  // read when the rig is actually loaded, so the video path costs
                  // exactly what it did before.
                  const visemeFreq = new Uint8Array(remoteAnalyser.frequencyBinCount);
                  const sampleRate = remoteAudioCtx.sampleRate;

                  const trackSpeechEnvelope = () => {
                    silenceDetectionFrameRef.current = requestAnimationFrame(trackSpeechEnvelope);

                    remoteAnalyser.getByteTimeDomainData(timeDomain);
                    let sum = 0;
                    for (let i = 0; i < timeDomain.length; i++) {
                      const s = (timeDomain[i] - 128) / 128;
                      sum += s * s;
                    }
                    const rms = Math.sqrt(sum / timeDomain.length);
                    const now = performance.now();

                    // Drive the viseme rig when its assets are present. It is
                    // ticked every frame, silence included, so the mouth closes
                    // down its release curve instead of snapping shut. Uses the
                    // previous frame's gate, which the rig's own attack smoothing
                    // absorbs entirely.
                    const rig = visemeRigRef.current;
                    if (rig) {
                      const features = readMouthFeatures(
                        remoteAnalyser, timeDomain, visemeFreq, sampleRate,
                      );
                      rig.selector.update(features, mouthActiveRef.current, now);
                      // Continuous blend, never a discrete shape: see blend().
                      rig.renderer.drawBlend(rig.selector.blend());
                    }

                    if (rms > ON_RMS || (rms > OFF_RMS && mouthActiveRef.current)) {
                      lastLoudAt = now;
                      setMouthActive(true);
                      if (avatarStateRef.current !== STATES.SPEAKING) {
                        setAvatarState(STATES.SPEAKING);
                      }
                      return;
                    }

                    const quietFor = now - lastLoudAt;
                    const trustEvents = audioBufferEventsSeenRef.current;
                    const bufferDone = audioBufferDoneRef.current;

                    // The mouth freezes on any real gap - that is simply what
                    // speech looks like - and cuts harder once the server has
                    // confirmed there is no more audio coming.
                    // Without server events the turn ends on FALLBACK_END_MS, so
                    // the mouth must not outlast it - otherwise it keeps moving
                    // into the fade-out.
                    const mouthHangover = trustEvents
                      ? (bufferDone ? END_HANGOVER_MS : GAP_HANGOVER_MS)
                      : Math.min(GAP_HANGOVER_MS, FALLBACK_END_MS);
                    if (quietFor >= mouthHangover) {
                      setMouthActive(false);
                    }

                    // The turn itself ends only when the server says the audio is
                    // finished, so the status label and glow do not blink between
                    // sentences. If this deployment sends no buffer events, fall
                    // back to a silence too long to be a pause.
                    const turnEnded = trustEvents
                      ? bufferDone && quietFor >= END_HANGOVER_MS
                      : quietFor >= FALLBACK_END_MS;

                    if (turnEnded && avatarStateRef.current === STATES.SPEAKING) {
                      setAvatarState(isRecordingRef.current ? STATES.LISTENING : STATES.IDLE);
                      if (isGreetingPhaseRef.current) {
                        isGreetingPhaseRef.current = false;
                      }
                      // The reply has landed and the guest now has the floor, so
                      // this is where the idle clock starts - not when the mic
                      // was opened, and not while the assistant was still talking.
                      armListenTimersRef.current?.(true);
                    }
                  };

                  silenceDetectionFrameRef.current = requestAnimationFrame(trackSpeechEnvelope);
                } catch (err) {
                  // Remote audio analysis setup may fail in some browsers
                }
              };

              // Local mic speech detection (keeps existing behavior)
              const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
              const source = audioContext.createMediaStreamSource(stream);
              const analyser = audioContext.createAnalyser();
              source.connect(analyser);

              const dataArray = new Uint8Array(analyser.frequencyBinCount);
              let lastSpeechCheck = 0;
              const detectSpeech = () => {
                // Throttled detection: only check if not already in a high-priority state
                if (avatarStateRef.current === STATES.IDLE || avatarStateRef.current === STATES.LISTENING) {
                  const now = Date.now();
                  // Check every ~100ms for speech to save CPU
                  if (now - lastSpeechCheck > 100) {
                    lastSpeechCheck = now;
                    analyser.getByteFrequencyData(dataArray);
                    const volume = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;

                    if (volume > 25 && avatarStateRef.current === STATES.IDLE) {
                      setAvatarState(STATES.LISTENING);
                    }
                  }
                }

                speechDetectionFrameRef.current = requestAnimationFrame(detectSpeech);
              };

              detectSpeech();
            }

            // ICE probe (03 spec 2.6): hotel Wi-Fi often blocks the UDP that
            // Live needs. If ICE neither connects nor opens the data channel
            // in time, continue as HTTP text chat instead of hanging.
            iceProbeCancelRef.current?.();
            iceProbeCancelRef.current = armConnectProbe(() => dcRef.current, () => {
              iceProbeCancelRef.current = null;
              if (pcRef.current !== pc) return; // a newer connection owns the page
              fallbackToHttp();
              resolve();
            });

            const dc = pc.createDataChannel("events");
            dcRef.current = dc;

            // Create a promise that resolves ONLY when the data channel opens.
            // This is the single source of truth for "ready to send messages".
            let resolveDcOpen: () => void;
            dcOpenPromiseRef.current = new Promise<void>((res) => {
              resolveDcOpen = res;
              dcOpenResolveRef.current = res;
            });

            // The server minted this session with its own prompt, tools, model
            // and voice. The client only sets the output modality and the input
            // audio (turn detection, noise reduction, the transcription hint);
            // it never sends instructions or tools.
            const outputModalities = isText ? ["text"] : ["audio"];

            if (!ctxRef.current) {
              reject(new Error("Stay context not available"));
              return;
            }

            const builtSessionConfig = {
              type: "live",
              output_modalities: outputModalities,
              audio: buildLiveAudioConfig(
                isText ? null : VOICE_TURN_DETECTION,
                explicitLangRef.current ? langRef.current : null,
              ),
            };

            dc.onopen = () => {
              iceProbeCancelRef.current?.();
              iceProbeCancelRef.current = null;
              // Reinforce the modality and input audio only. Instructions and
              // tools were fixed server-side at mint time and are never re-sent.
              dc.send(
                JSON.stringify({
                  type: "session.update",
                  session: builtSessionConfig,
                })
              );

              if (isVoice && !hasGreeted) {
                setHasGreeted(true);
                isGreetingPhaseRef.current = true;

                dc.send(JSON.stringify({
                  type: "response.create",
                  response: { instructions: greetingInstructionFor(ctxRef.current, sessionRef.current) },
                }));
              }

              // Voice + prewarm: resolve immediately (VAD is the default)
              if (isVoice || isPrewarm) {
                setIsLoading(false);
                resolveDcOpen();
                resolve();
              }
              // Text/chip mode: will resolve when session.updated confirms
              // audio.input.turn_detection: null has been applied by the server
            };

            dc.onerror = (err) => {
              console.error("Data channel error:", err);
              reject(err);
            };

            dc.onmessage = async (event) => {
              const msg = JSON.parse(event.data);

              // Live-only lifecycle events for the model's audio track. These are
              // the authoritative bounds of audible output - the transcript stream
              // runs ahead of the sound, so it must never gate the mouth.
              if (msg.type === "output_audio_buffer.started") {
                audioBufferEventsSeenRef.current = true;
                audioBufferDoneRef.current = false;
              }
              if (
                msg.type === "output_audio_buffer.stopped" ||
                msg.type === "output_audio_buffer.cleared"
              ) {
                audioBufferEventsSeenRef.current = true;
                audioBufferDoneRef.current = true;
              }

              // Safety net: a tool-call or text-only turn never opens an audio
              // buffer, so nothing else would release the gap hangover.
              if (msg.type === "response.done") {
                responseInFlightRef.current = false;
                audioBufferDoneRef.current = true;
              }

              if (msg.type === "response.created") {
                responseInFlightRef.current = true;
                isFunctionCallActiveRef.current = false;
                // Unmute in case a chip interrupt muted it, and invalidate old typing chain
                if (audioRef.current) audioRef.current.muted = false;
                textGenerationRef.current++;
                textBufferRef.current = "";
                processingBufferRef.current = false;
                setResponseText("");
                setDisplayedText("");
              }

              // When function call arguments start streaming, suppress text display
              if (msg.type === "response.function_call_arguments.delta") {
                isFunctionCallActiveRef.current = true;
              }

              if (currentModeRef.current === "voice" && msg.type === "response.output_audio_transcript.delta" && !isFunctionCallActiveRef.current) {
                // Transcript deltas arrive BEFORE the audio they describe, so they
                // must not start the avatar - the envelope tracker owns SPEAKING and
                // starts it on the first audible sample. This is only a fallback for
                // when remote-audio analysis failed to initialise at all.
                if (!remoteAnalyserRef.current && avatarStateRef.current !== STATES.SPEAKING) {
                  setAvatarState(STATES.SPEAKING);
                }
                setResponseText((prev) => prev + msg.delta);

                // Add new text to buffer
                textBufferRef.current += msg.delta;

                // Start processing the buffer if not already running
                if (!processingBufferRef.current) {
                  processingBufferRef.current = true;
                  // Capture current generation so this chain self-aborts if interrupted
                  const myGeneration = textGenerationRef.current;

                  const processBuffer = () => {
                    // Stale chain - a new response or interrupt has started
                    if (textGenerationRef.current !== myGeneration) return;
                    if (textBufferRef.current.length > 0) {
                      // Batch processing: if buffer is large, take more characters at once
                      const batchSize = textBufferRef.current.length > 10 ? 3 : 1;
                      const nextChars = textBufferRef.current.slice(0, batchSize);
                      textBufferRef.current = textBufferRef.current.slice(batchSize);

                      setDisplayedText((prev) => prev + nextChars);

                      const lastChar = nextChars[nextChars.length - 1];
                      const delay = lastChar === "." || lastChar === "?" ? 250 : lastChar === "," ? 120 : 30;
                      setTimeout(processBuffer, delay);
                    } else {
                      processingBufferRef.current = false;
                    }
                  };

                  //  REDUCED START DELAY: Changed from 1000ms to 400ms
                  setTimeout(processBuffer, 400);
                }
              }

              //  Text mode: handle text responses (uses ref for dynamic keyboard toggle)
              if (currentModeRef.current === "text" && (msg.type === "response.text.delta" || msg.type === "response.output_text.delta") && !isFunctionCallActiveRef.current) {
                // Transition from THINKING once text starts arriving
                setAvatarState((prev) => prev === STATES.THINKING ? STATES.IDLE : prev);
                setResponseText((prev) => prev + msg.delta);
                setDisplayedText((prev) => prev + msg.delta);
              }

              // Text/chip mode: session config confirmed - safe to send messages
              if (!isVoice && msg.type === "session.updated") {
                resolveDcOpen();
                resolve();
              }

              // Debug: log session.updated confirmation and any errors
              if (msg.type === "session.updated") {
                console.log("SESSION.UPDATED confirmed by server", msg.session?.instructions ? "(instructions present)" : "(no instructions)");
              }
              if (msg.type === "error") {
                console.error("LIVE API ERROR:", msg.error);
              }

              // Text mode: response complete → back to idle
              if (currentModeRef.current === "text" && msg.type === "response.done") {
                setAvatarState(STATES.IDLE);
              }

              // Turn logging (postTurn) - fire-and-forget, zero impact on session
              // Last line of defence against answering the room. If VAD opened a
              // turn but the transcript carries no actual words, nobody spoke to
              // us: kill the reply rather than let the concierge improvise at a
              // guest who is not there.
              if (msg.type === "conversation.item.input_audio_transcription.completed") {
                const heard = (msg.transcript ?? "").trim();
                if (isEmptyUtterance(heard)) {
                  if (responseInFlightRef.current) interruptResponse();
                  setAvatarState(isRecordingRef.current ? STATES.LISTENING : STATES.IDLE);
                } else {
                  postTurn("user", heard, msg.item_id);
                }
              }
              if (msg.type === "response.output_audio_transcript.done" && msg.transcript?.trim() && !isFunctionCallActiveRef.current) {
                postTurn("assistant", msg.transcript.trim(), msg.item_id);
              }
              if ((msg.type === "response.text.done" || msg.type === "response.output_text.done") && msg.text?.trim() && !isFunctionCallActiveRef.current) {
                postTurn("assistant", msg.text.trim(), msg.item_id);
              }

              // Tool calls: generic dispatch to the server (runTool). A repeated
              // call_id is skipped here; the server also dedupes by call_id.
              if (msg.type === "response.function_call_arguments.done") {
                const { name, arguments: args, call_id } = msg;
                if (call_id && handledCallIdsRef.current.has(call_id)) return;
                if (call_id) handledCallIdsRef.current.add(call_id);
                await runTool(String(name), typeof args === "string" ? args : JSON.stringify(args ?? {}), String(call_id || ""));
              }

              // Clear response when user starts speaking again
              if (msg.type === "input_audio_buffer.speech_started") {
                setResponseText("");
                setDisplayedText(""); //  Clear displayed text immediately on new speech
                setTranscript("");
                setAvatarState(STATES.LISTENING);
                // The guest is mid-sentence: the mic must not close under them,
                // however long they take. The ceiling timer keeps running, so
                // ambient chatter alone still cannot hold the mic open.
                guestSpeakingRef.current = true;
                clearListenIdleTimerRef.current?.();
              }

              if (msg.type === "input_audio_buffer.speech_stopped") {
                guestSpeakingRef.current = false;
                // Deliberately no idle timer here: the assistant's reply may take
                // a while (tool calls), and the turn-end handler re-arms it once
                // the guest actually has the floor back. The ceiling covers the
                // case where no reply ever comes.
              }
            };

            Promise.resolve()
              // Mint via /api/live-session, which proxies to the server's
              // session route (prompt, tools, consent, limits): a single-use
              // Gemini Live token in client_secret.value.
              .then(() => mintSession(mode, "live"))
              .then((data) => {
                if (!data?.client_secret?.value) throw new Error("No token");
                const model = (data.client_secret as { model?: string }).model || data.model;
                return pc.connect(data.client_secret.value, model).catch((netErr: unknown) => {
                  // The network refused the Live endpoint itself: a blocking
                  // guest Wi-Fi. Text chat over HTTP still works.
                  throw isNetworkBlock(netErr) ? "Live blocked" : netErr;
                });
              })
              .then(() => {
                setIsLoading(false);
                // Don't resolve here - resolve happens in dc.onopen
                // to guarantee the data channel is actually open
              })
              .catch((err) => {
                if (err === "Retry initiated") return;
                if (err === "Live blocked") {
                  fallbackToHttp();
                  resolve();
                  return;
                }
                // A silent prewarm never shows an error; the START tap retries.
                if (!isPrewarm) setError(describeStartError(err));
                stopLive();
                reject(err);
              });
          })
          .catch((err) => {
            if (!isPrewarm) setError(!isVoice ? t("errors.audioFailed") : t("errors.micDenied"));
            setIsLoading(false);
            stopLive();
            reject(err);
          });
      });
    },
    [isLoading, hasGreeted, stopLive, interruptResponse, postTurn, runTool, mintSession, fallbackToHttp, describeStartError, t]
  );

  // Pre-warm Live connection while splash screen is visible.
  // Uses a silent audio stream (no mic permission needed) so the full
  // SDP handshake completes before the user clicks START CONVERSATION.
  // START CONVERSATION then just swaps in the real mic via replaceTrack().
  // Only for a guest whose voice consent is already stored for the current
  // wording: a prewarm runs without a gesture, so it may not collect consent,
  // and a first-time guest must see the ConsentSheet before anything is minted.
  // Never while concierge.live is off: the mint would only 409 (QA GS-1).
  const prewarmAllowed = !!(ctx && hotelSlug && ctx.features.assistant !== false
    && stayStore.consentAllowsVoice(stayStore.getStoredConsent(hotelSlug, ctx.consent_version))
    && stayStore.getGuestRef(hotelSlug));
  // One attempt per page load: a failed prewarm flips isLoading, which would
  // otherwise re-run this effect and burn the guest's per-IP mint quota.
  const prewarmTriedRef = useRef(false);
  useEffect(() => {
    if (prewarmAllowed && !prewarmTriedRef.current && viewMode === "splash" && !pcRef.current && !isLoading && transportRef.current === "live") {
      prewarmTriedRef.current = true;
      startLive(0, "prewarm").catch(() => {});
    }
  }, [prewarmAllowed, viewMode, isLoading, startLive]);

  // ─── Consent gate ───
  // Nothing is minted until the guest has consented for the mode they use:
  // text needs ai_processing + transfer_abroad, voice also needs recording.

  const consentOk = useCallback((mode: "voice" | "text") => {
    const c = hotelSlug && ctxRef.current ? stayStore.getStoredConsent(hotelSlug, ctxRef.current.consent_version) : null;
    return mode === "voice" ? stayStore.consentAllowsVoice(c) : stayStore.consentAllowsText(c);
  }, [hotelSlug]);

  /** Run `action` now when consent covers `want`, else after the ConsentSheet. */
  const withConsent = useCallback((want: "voice" | "text", action: (mode: "voice" | "text") => void) => {
    if (consentOk(want)) {
      action(want);
      return;
    }
    setConsentFor(() => action);
  }, [consentOk]);

  // The transport can change under an await (the ICE probe), so read it
  // through a function rather than letting TypeScript narrow the ref.
  const onHttp = () => transportRef.current === "http";

  // The transcription hint for session.update calls (explicit picks only).
  const langHint = () => (explicitLangRef.current ? langRef.current : null);

  // Keyboard toggle: switch between voice and text mode on the existing session
  const handleKeyboardToggle = useCallback(() => {
    const enteringTextMode = !showTextInput;
    setShowTextInput(enteringTextMode);

    if (enteringTextMode) {
      // ── Switch to text mode ──
      currentModeRef.current = "text";
      // Mute mic
      if (streamRef.current) {
        streamRef.current.getAudioTracks().forEach(t => { t.enabled = false; });
      }
      isRecordingRef.current = false;
      setIsRecording(false);
      clearAllListenTimers();
      // Mute AI audio output (user wants written answers, no voice)
      if (audioRef.current) {
        audioRef.current.muted = true;
      }
      // Update session to text-only output
      if (dcRef.current?.readyState === "open") {
        dcRef.current.send(JSON.stringify({
          type: "session.update",
          session: {
            type: "live",
            output_modalities: ["text"],
            audio: buildLiveAudioConfig(null, langHint()),
          },
        }));
      } else if (!pcRef.current && !isLoading && transportRef.current === "live" && consentOk("text")) {
        // No session yet - start a text-mode session as fallback
        startLive(0, "text").catch(() => {});
      }
    } else if (transportRef.current === "http") {
      // Voice cannot run on this network: stay in text and say why.
      setShowTextInput(true);
      setNotice(t("errors.voiceFallback"));
    } else {
      // ── Switch back to voice mode ──
      currentModeRef.current = "voice";
      // Unmute AI audio output
      if (audioRef.current) {
        audioRef.current.muted = false;
      }
      // Update session back to voice output
      if (dcRef.current?.readyState === "open") {
        dcRef.current.send(JSON.stringify({
          type: "session.update",
          session: {
            type: "live",
            output_modalities: ["audio"],
            audio: buildLiveAudioConfig(ACTIVE_VOICE_TURN_DETECTION, langHint()),
          },
        }));
      }
      // Mic stays muted until user clicks mic button
    }
  }, [showTextInput, isLoading, startLive, clearAllListenTimers, consentOk, t]);

  // ─── HTTP text chat (fallback transport) ───
  // The reply streams into the response bubble. The avatar shows THINKING
  // until the first words, then IDLE: it never enters SPEAKING here, since
  // there is no audio track and the mouth is gated on live audio only.
  const sendHttp = async (text: string, retried = false): Promise<void> => {
    setAvatarState(STATES.THINKING);
    setResponseText("");
    setDisplayedText("");
    try {
      const s = sessionRef.current ?? (await mintSession("text", "http"));
      let acc = "";
      const reply = await chatCall(s.session_id, s.session_token, text, langHint(), (delta) => {
        if (!acc) setAvatarState(STATES.IDLE);
        acc += delta;
        setResponseText(acc);
        setDisplayedText(acc);
      });
      const final = reply.text || acc || (reply.handoff ? t("chat.handoff") : "");
      setResponseText(final);
      setDisplayedText(final);
      reply.tool_calls?.forEach((c) => handleUi(c.ui));
      handleUi(reply.ui);
    } catch (err) {
      // An expired or ended session: mint a fresh text session, once.
      if (!retried && err instanceof ApiError && err.status === 401) {
        sessionRef.current = null;
        return sendHttp(text, true);
      }
      setError(describeStartError(err));
    } finally {
      setAvatarState(STATES.IDLE);
    }
  };

  /** A typed question or a chip: over the live data channel, or over HTTP. */
  const sendUserText = async (text: string, mode: "text" | "chip"): Promise<void> => {
    try {
      if (transportRef.current === "live" && !pcRef.current) await startLive(0, mode);
      if (transportRef.current === "live" && dcOpenPromiseRef.current) await dcOpenPromiseRef.current;
    } catch (err) {
      setAvatarState(STATES.IDLE);
      setError(describeStartError(err));
      return;
    }
    if (transportRef.current === "http") return sendHttp(text);
    const dc = dcRef.current;
    if (dc?.readyState !== "open") {
      setAvatarState(STATES.IDLE);
      setError(t("errors.notReady"));
      return;
    }
    dc.send(JSON.stringify({
      type: "conversation.item.create",
      item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
    }));
    // No per-response instructions: they would replace the server prompt
    // (knowledge, menu, policy) for this turn.
    dc.send(JSON.stringify({ type: "response.create" }));
    // Typed text has no transcription event, so it is logged here.
    postTurn("user", text);
  };

  const handleTextSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault?.();
    if (!textInput.trim()) return;

    const userText = textInput;
    setHasInteracted(true);
    setTranscript(userText);
    setResponseText("");
    setDisplayedText("");
    setTextInput("");
    setAvatarState(consentOk("text") ? STATES.THINKING : STATES.IDLE);
    withConsent("text", () => {
      setAvatarState(STATES.THINKING);
      void sendUserText(userText, "text");
    });
  };

  const avatarGlow = AVATAR_GLOWS[avatarState];
  const avatarAnimation = AVATAR_ANIMATIONS[avatarState];
  // A mic that closed itself has to say so, or the guest talks into a dead one
  // and concludes the concierge is broken.
  const statusLabel = micPausedHint && avatarState === STATES.IDLE
    ? t("status.micPaused")
    : transport === "http" && avatarState === STATES.IDLE
      ? t("status.textOnly")
      : showTextInput && avatarState === STATES.IDLE
        ? t("status.typeBelow")
        : t(STATUS_LABELS[avatarState]);
  const statusColor = micPausedHint && avatarState === STATES.IDLE
    ? "var(--hc-soft-gold)"
    : STATUS_COLORS[avatarState];

  useEffect(() => {
    if (avatarState === STATES.IDLE && responseText === "") {
      // Start fade-out animation
      if (fadeOutTimeoutRef.current) {
        clearTimeout(fadeOutTimeoutRef.current);
      }

      fadeOutTimeoutRef.current = setTimeout(() => {
        setDisplayedText("");
      }, 400); // 400ms fade-out duration
    }
  }, [avatarState, responseText]);

  // ─── Start (splash) ───
  // Called straight from the START click or the ConsentSheet's Continue:
  // iOS unlocks audio and video playback only inside a user gesture, so the
  // unlock runs before anything is awaited.
  const beginSession = async (mode: "voice" | "text") => {
    setSessionStarted(true);
    setViewMode("concierge");

    // Unlock iOS Safari Audio & Video Engines (Requires user gesture context)
    if (audioRef.current) {
      audioRef.current.play().catch(() => {});
    }
    if (videoRef.current) {
      videoRef.current.play().catch(() => {});
    }
    if (remoteAudioContextRef.current && remoteAudioContextRef.current.state === "suspended") {
      remoteAudioContextRef.current.resume().catch(() => {});
    }

    // Text only (recording declined, or voice cannot get through): the
    // greeting is written, not spoken.
    const greetInText = () => {
      const g = ctxRef.current?.greeting_text || "";
      setHasGreeted(true);
      setResponseText(g);
      setDisplayedText(g);
    };
    if (mode === "text" || transportRef.current === "http") {
      currentModeRef.current = "text";
      setInputMode("text");
      setShowTextInput(true);
      if (!hasGreeted) greetInText();
      return;
    }

    try {
      if (pcRef.current) {
        // Session pre-warmed - just swap in the real mic track
        const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });

        // Replace the silent placeholder track with the real mic
        if (audioSenderRef.current) {
          await audioSenderRef.current.replaceTrack(micStream.getAudioTracks()[0]);
        }
        // Mic starts muted; user clicks mic button to start listening
        micStream.getAudioTracks().forEach(tr => { tr.enabled = false; });

        // Clean up the silent AudioContext
        if (silentAudioContextRef.current) {
          silentAudioContextRef.current.close().catch(() => {});
          silentAudioContextRef.current = null;
        }
        streamRef.current = micStream;

        // Wait for data channel if negotiation is still in flight
        if (dcOpenPromiseRef.current) await dcOpenPromiseRef.current;
        if (onHttp()) {
          greetInText();
          return;
        }

        // Send the server's greeting now that the real session is ready
        if (!hasGreeted && dcRef.current?.readyState === "open") {
          setHasGreeted(true);
          isGreetingPhaseRef.current = true;
          dcRef.current.send(JSON.stringify({
            type: "response.create",
            response: { instructions: greetingInstructionFor(ctxRef.current, sessionRef.current) },
          }));
        }
      } else {
        // Pre-warm not ready (or no stored consent before) - cold start
        await startLive(0, "voice", false);
        if (onHttp() && !hasGreeted) greetInText();
      }
    } catch (err) {
      console.error("Failed to start concierge:", err);
      // Microphone refused: typing still works, so stay on the concierge.
      if ((err as { name?: string })?.name === "NotAllowedError") {
        setError(t("errors.micDenied"));
        currentModeRef.current = "text";
        setShowTextInput(true);
        return;
      }
      setSessionStarted(false);
      setViewMode("splash");
    }
  };

  const onStartClick = () => {
    // concierge.live is off (QA GS-1): every mint would answer 409
    // concierge_not_live, so say "call the front desk" now instead of
    // walking the guest through the ConsentSheet first. The concierge view
    // still offers the emergency card, the tracker and WhatsApp.
    if (ctxRef.current?.features.assistant === false) {
      const off = t("errors.aiOff");
      setSessionStarted(true);
      setViewMode("concierge");
      setResponseText(off);
      setDisplayedText(off);
      return;
    }
    if (consentOk("voice")) void beginSession("voice");
    else if (consentOk("text")) void beginSession("text");
    else setConsentFor(() => (m: "voice" | "text") => void beginSession(m));
  };

  // ─── Chips ───

  /** A request chip files notify_staff directly and confirms from the server. */
  const fileDirectRequest = async (req: (typeof DIRECT_CHIP_REQUESTS)[string], label: string) => {
    try {
      // A tool call needs only a session token: mint a cheap HTTP session
      // (no Live key) when none is live yet.
      const s = sessionRef.current ?? (await mintSession("chip", "http"));
      const result = await runToolCall(s.session_id, s.session_token, "notify_staff", {
        ...req, label, original_text: label, language: langRef.current,
      });
      handleUi(result.ui);
      const dc = dcRef.current;
      const ok = ["created", "merged", "escalated"].includes(result.status);
      if (dc?.readyState === "open" && result.followup_instructions) {
        dc.send(JSON.stringify({
          type: "conversation.item.create",
          item: { type: "message", role: "user", content: [{ type: "input_text", text: label }] },
        }));
        dc.send(JSON.stringify({ type: "response.create", response: { instructions: result.followup_instructions } }));
        return;
      }
      const msg = ok ? t("chat.requestSent") : t("chat.failed");
      setResponseText(msg);
      setDisplayedText(msg);
      setAvatarState(STATES.IDLE);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) sessionRef.current = null;
      setAvatarState(STATES.IDLE);
      setError(err instanceof ApiError && err.status !== 401 ? describeStartError(err) : t("chat.failed"));
    }
  };

  const handleChip = (chip: { key: string; label: string }, label: string) => {
    // Unlock video playback via user gesture (critical for mobile)
    ensureVideoPlaying();
    if (chip.key === "room_service" && ctxRef.current?.features.menu) {
      setViewMode("menu");
      return;
    }
    setHasInteracted(true);
    setTranscript(label);
    setResponseText("");
    setDisplayedText("");
    const direct = ctxRef.current?.room ? DIRECT_CHIP_REQUESTS[chip.key] : undefined;
    withConsent("text", () => {
      setAvatarState(STATES.THINKING);
      if (direct) {
        void fileDirectRequest(direct, label);
        return;
      }
      // A tapped question is answered aloud when voice is allowed (as before);
      // a text-only guest gets a written answer.
      const spoken = transportRef.current === "live" && consentOk("voice");
      if (spoken && currentModeRef.current === "text") {
        currentModeRef.current = "voice";
        setShowTextInput(false);
        if (audioRef.current) audioRef.current.muted = false;
        if (dcRef.current?.readyState === "open") {
          dcRef.current.send(JSON.stringify({
            type: "session.update",
            session: { type: "live", output_modalities: ["audio"], audio: buildLiveAudioConfig(ACTIVE_VOICE_TURN_DETECTION, langHint()) },
          }));
        }
      }
      void sendUserText(label, spoken ? "chip" : "text");
    });
  };

  const chipLabel = (chip: { key: string; label: string }) => {
    if (SERVER_LOCALIZED_CHIP_LANGS.has(lang) && chip.label) return chip.label;
    const key = `chips.${chip.key}`;
    const local = t(key);
    return local !== key ? local : chip.label;
  };

  // ─── Menu order ───
  // One order per basket. With a live session: the place_order tool (quote,
  // then confirm), so the server writes the follow-up and the session knows
  // the order. Without one: the menu's own public quote and order. Throws on
  // failure, which FoodMenu turns into an error with a retry.
  const placeMenuOrder = async (lines: MenuOrderLine[]) => {
    const basket = lines.map((l) => ({ menu_item_id: l.menu_item_id, qty: l.qty, notes: l.notes }));
    let placed: PlacedOrder | null = null;
    const s = sessionRef.current;
    if (s) {
      try {
        placed = await placeOrderViaSession(s.session_id, s.session_token, basket);
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 401)) throw err;
        sessionRef.current = null; // expired: fall through to the menu route
      }
    }
    if (!placed) {
      const room = ctxRef.current?.room?.number;
      if (!hotelSlug || !room) throw new Error("room_required");
      const k = new URLSearchParams(window.location.search).get("k");
      placed = await placeOrderViaMenu(hotelSlug, String(room), k, basket, langRef.current);
    }
    if (placed.tracking_token) addTracked(placed.tracking_token, "order");
    const dc = dcRef.current;
    if (placed.followup_instructions && dc?.readyState === "open") {
      dc.send(JSON.stringify({
        type: "conversation.item.create",
        item: {
          type: "message", role: "user",
          content: [{ type: "input_text", text: `[Menu order placed: ${lines.map((l) => `${l.qty}x ${l.name}`).join(", ")}]` }],
        },
      }));
      speakFollowup(placed.followup_instructions, false);
    } else {
      setResponseText(t("chat.orderPlaced"));
      setDisplayedText(t("chat.orderPlaced"));
    }
  };

  // ─── Language and privacy ───

  const pickLanguage = (next: GuestLanguage) => {
    storeLang(next);
    explicitLangRef.current = true;
    langRef.current = next;
    setLang(next);
    // Greeting, chips and consent wording come back in the new language.
    if (hotelSlug) {
      fetchStayContext(hotelSlug, target, {
        k: new URLSearchParams(window.location.search).get("k"),
        lang: next,
        stayToken: stayStore.getStayToken(hotelSlug, target),
        kind: location ? "location" : "room",
      })
        .then((c) => {
          ctxRef.current = c;
          setCtx(c);
        })
        .catch(() => {});
    }
    // A live session gets the new transcription hint.
    const dc = dcRef.current;
    if (dc?.readyState === "open") {
      dc.send(JSON.stringify({
        type: "session.update",
        session: {
          type: "live",
          audio: buildLiveAudioConfig(currentModeRef.current === "text" ? null : ACTIVE_VOICE_TURN_DETECTION, next),
        },
      }));
    }
  };

  // The Privacy button: end the session with withdraw_consent (the server
  // withdraws every purpose and stops transcripts), then forget this
  // browser's consent and guest_ref so a new start asks again.
  // WHY the outcomes: "done" only when the server confirmed a withdrawal
  // (the live session's end, or, with no live session or one already
  // ended, the device's guest_ref). A network error or 5xx is "failed"
  // with a retry, keeping the session and guest_ref so the retry can still
  // reach the server: a false success on a PDPL control is worse than an
  // error. With nothing server-side to address, the sheet says only that
  // this device forgot the consent.
  const withdrawConsent = async () => {
    setPrivacyState("busy");
    const s = sessionRef.current;
    const ref = hotelSlug ? stayStore.getGuestRef(hotelSlug) : null;
    const gone = (err: unknown) => err instanceof ApiError && [401, 404, 409, 410].includes(err.status);
    let confirmed = false;
    let failed = false;
    if (s) {
      try {
        await endSession(s.session_id, s.session_token, true);
        confirmed = true;
      } catch (err) {
        failed = !gone(err);
      }
    }
    if (!confirmed && ref && hotelSlug) {
      try {
        await withdrawByGuestRef(hotelSlug, ref);
        confirmed = true;
      } catch {
        failed = true;
      }
    }
    stopLive();
    if (!confirmed && failed) {
      setPrivacyState("failed");
      return;
    }
    if (hotelSlug) {
      stayStore.clearConsent(hotelSlug);
      stayStore.clearGuestRef(hotelSlug);
    }
    sessionRef.current = null;
    setHasGreeted(false);
    setPrivacyState(confirmed ? "done" : "forgotten");
  };

  const closePrivacy = () => {
    setPrivacyOpen(false);
    if (privacyState === "failed") {
      setPrivacyState("idle");
      return;
    }
    if (privacyState === "done" || privacyState === "forgotten") {
      setPrivacyState("idle");
      setSessionStarted(false);
      setShowTextInput(false);
      setResponseText("");
      setDisplayedText("");
      setViewMode("splash");
    }
  };

  const dirAttr = langDir(lang);
  const trackerActive = tracked.filter((i) => {
    const st = trackerStatuses[i.token];
    return st && !st.cancelled && st.state !== "done";
  }).length;

  // Hotel loading / error states shown before the main UI
  if (hotelLoading) {
    return (
      <div className="full-height gpu-layer" style={{ width: "100%", height: "100dvh", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--hc-slate)" }}>
        <div style={{ color: "rgba(225, 175, 104,0.7)", fontFamily: "'Inter', sans-serif", fontSize: 14, letterSpacing: "2px" }}>
          {t("splash.loading")}
        </div>
      </div>
    );
  }

  if (hotelError) {
    return (
      <div className="full-height gpu-layer" style={{ width: "100%", height: "100dvh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", background: "var(--hc-slate)", gap: 24 }}>
        <div style={{ fontFamily: "'Playfair Display', serif", fontSize: 28, color: "var(--hc-gold)", letterSpacing: "4px" }}>CLEFS</div>
        <div style={{ color: "rgba(232,228,220,0.6)", fontFamily: "'Inter', sans-serif", fontSize: 15, textAlign: "center", maxWidth: 320, lineHeight: 1.7 }}>
          {hotelError === "not_found" ? t("splash.notFound") : t("splash.loadFailed")}
        </div>
        <a
          href="/"
          style={{ color: "var(--hc-gold)", fontFamily: "'Inter', sans-serif", fontSize: 13, letterSpacing: "2px", textDecoration: "none", borderBottom: "1px solid rgba(225, 175, 104,0.3)", paddingBottom: 2 }}
        >
          {t("splash.home")}
        </a>
      </div>
    );
  }

  if (!hotelData) {
    return (
      <div className="full-height gpu-layer" style={{ width: "100%", height: "100dvh", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--hc-slate)" }}>
        <div style={{ color: "rgba(225, 175, 104,0.7)", fontFamily: "'Inter', sans-serif", fontSize: 14, letterSpacing: "2px" }}>
          {t("splash.loading")}
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        width: "100%",
        height: "100dvh",
        display: "flex",
        flexDirection: "column",
        backgroundColor: "var(--clefs-slate-900)",
        backgroundImage:
          "radial-gradient(120% 70% at 50% -10%, rgba(225,175,104,0.07), transparent 62%)," +
          "linear-gradient(180deg, var(--clefs-slate-850) 0%, var(--clefs-slate-900) 55%, var(--clefs-slate-950) 100%)",
        position: "relative",
        overflow: "hidden",
        fontFamily: langFontStack(lang),
      }}
    >
      <style dangerouslySetInnerHTML={{ __html: injectStyles }} />
      <style dangerouslySetInnerHTML={{ __html: CC_STYLES }} />
      {/* Stone grain, matching the marketing site and the staff dashboard.
          Non-interactive and behind everything the guest can touch. */}
      <div className="cx-grain" aria-hidden="true" />

      {/* Splash Screens */}
      {viewMode === "splash" && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 100,
            background: "linear-gradient(180deg, var(--clefs-slate-850) 0%, var(--clefs-slate-900) 50%, var(--clefs-slate-950) 100%)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            animation: "fadeIn 0.8s ease",
          }}
        >
          {/* Background for splash */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              backgroundImage: "url('/hotel_lobby_bg.png')",
              backgroundSize: "cover",
              backgroundPosition: "center",
              filter: "brightness(0.22) saturate(0.5) blur(4px)",
              opacity: 0.6,
            }}
          />
          {/* Warm hospitality overlay */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "radial-gradient(ellipse at 50% 60%, rgba(225, 175, 104,0.06) 0%, transparent 70%), linear-gradient(180deg, transparent 0%, rgba(139,107,62,0.04) 100%)",
            }}
          />

          {/* The splash covers the header, so it carries its own language
              button: a guest must be able to switch before starting (QA GS-8). */}
          <div
            dir={dirAttr}
            style={{
              position: "absolute",
              top: "max(12px, env(safe-area-inset-top))",
              insetInlineEnd: "max(16px, env(safe-area-inset-right))",
              zIndex: 120,
            }}
          >
            <LanguagePicker t={t} lang={lang} preferred={ctx?.hotel.languages} onPick={pickLanguage} />
          </div>

          <div style={{ position: "relative", zIndex: 110, textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", padding: "0 24px" }}>
            <div
              style={{
                width: "min(160px, 35vw)",
                height: "min(160px, 35vw)",
                borderRadius: "50%",
                overflow: "hidden",
                border: "4px solid var(--hc-gold, #E1AF68)",
                boxShadow: sessionStarted ? "0 0 60px rgba(225, 175, 104, 0.4)" : "0 0 30px rgba(225, 175, 104, 0.12), 0 0 80px rgba(139,107,62,0.08)",
                marginBottom: "clamp(16px, 4vh, 32px)",
                animation: sessionStarted ? "speakingPulse 2s infinite" : "breathe 4s ease-in-out infinite",
                transition: "all 0.5s ease",
                background: "#000",
                transform: "translateZ(0)",
                position: "relative",
              }}
            >
              <Image
                src="/female.png"
                alt={assistantName}
                fill
                priority
                sizes="(max-width: 768px) 35vw, 160px"
                style={{
                  objectFit: "cover",
                  opacity: (avatarState === STATES.SPEAKING) ? 0.3 : 1,
                  transition: "opacity 0.5s ease",
                }}
              />
              {(avatarState === STATES.SPEAKING) && (
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <div style={{ width: "100%", height: "100%", background: "radial-gradient(circle, rgba(225, 175, 104,0.2) 0%, transparent 70%)" }} />
                </div>
              )}
            </div>

            <h1
              style={{
                fontFamily: "'Playfair Display', serif",
                fontSize: "clamp(32px, 8vw, 42px)",
                color: "var(--hc-gold)",
                letterSpacing: "8px",
                marginBottom: 12,
                textShadow: "0 2px 10px rgba(0,0,0,0.5)",
              }}
            >
              {assistantName.toUpperCase()}
            </h1>
            <div className="cx-splash-mark" aria-hidden="true">
              <span className="cx-splash-rule" />
              <img src="/brand/clefs-key.webp" alt="" />
              <span className="cx-splash-rule" />
            </div>
            <p
              dir={dirAttr}
              lang={lang}
              style={{
                color: "rgba(232, 228, 220, 0.8)",
                fontSize: "clamp(14px, 3.5vw, 16px)",
                letterSpacing: "2px",
                marginBottom: "clamp(24px, 6vh, 48px)",
                maxWidth: 400,
                lineHeight: 1.6,
                fontWeight: 300,
              }}
            >
              {t("splash.tagline")} <br />
              <span style={{ fontWeight: 600, color: "var(--hc-gold)" }}>{hotelData!.name}</span>
            </p>

            <button
              dir={dirAttr}
              // Opens the ConsentSheet unless consent is already stored; the
              // sheet's Continue then calls beginSession() inside its own click.
              onClick={onStartClick}
              style={{
                background: "linear-gradient(135deg, #E1AF68 0%, #F2C67D 50%, #E1AF68 100%)",
                border: "1px solid rgba(231,211,163,0.3)",
                borderRadius: 40,
                padding: "18px clamp(32px, 8vw, 52px)",
                color: "var(--hc-slate)",
                fontSize: "clamp(13px, 3.2vw, 15px)",
                fontWeight: 700,
                letterSpacing: "3px",
                cursor: "pointer",
                transition: "all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)",
                boxShadow: "0 10px 30px rgba(225, 175, 104, 0.3), 0 0 0 1px rgba(231,211,163,0.1)",
                WebkitTapHighlightColor: "transparent",
              }}
              onMouseOver={(e) => {
                e.currentTarget.style.transform = "scale(1.05) translateY(-4px)";
                e.currentTarget.style.boxShadow = "0 15px 40px rgba(225, 175, 104, 0.5)";
              }}
              onMouseOut={(e) => {
                e.currentTarget.style.transform = "scale(1) translateY(0)";
                e.currentTarget.style.boxShadow = "0 10px 30px rgba(225, 175, 104, 0.3)";
              }}
            >
              {t("splash.start")}
            </button>
            {/* An unkeyed legacy QR ("absent") is by design, not a scan to
                redo: say what it means instead (QA PUB-3). Only a bad key
                ("invalid") asks the guest to scan again. */}
            {ctx?.room && !ctx.verified && (
              <p className="cc-banner" dir={dirAttr} lang={lang} style={{ marginTop: 20 }}>
                {ctx.key_status === "absent" ? t("splash.legacyRoom") : t("splash.limited")}
              </p>
            )}
          </div>
        </div>
      )}

      {/* ConsentSheet: before any mint, including the prewarm */}
      {consentFor && ctx && (
        <ConsentSheet
          t={t}
          lang={lang}
          assistantName={assistantName}
          texts={ctx.consent_texts}
          initial={hotelSlug ? stayStore.getStoredConsent(hotelSlug, ctx.consent_version)?.grants : null}
          onCancel={() => setConsentFor(null)}
          onContinue={(grants, mode) => {
            if (hotelSlug) stayStore.storeConsent(hotelSlug, ctx.consent_version, grants);
            pendingGrantsRef.current = grants;
            const run = consentFor;
            setConsentFor(null);
            run(mode);
          }}
        />
      )}

      {/* Privacy: stop and withdraw consent */}
      {privacyOpen && (
        <GuestSheet onClose={closePrivacy} labelledBy="cc-privacy-title" dir={dirAttr} lang={lang}>
          <h2 id="cc-privacy-title">{t("privacy.title")}</h2>
          <p role={privacyState === "failed" ? "alert" : undefined} aria-live="polite">
            {privacyState === "done"
              ? t("privacy.withdrawn")
              : privacyState === "forgotten"
                ? t("privacy.forgotten")
                : privacyState === "failed"
                  ? t("privacy.failed")
                  : t("privacy.body")}
          </p>
          <div className="cc-actions">
            {privacyState !== "done" && privacyState !== "forgotten" && (
              <button type="button" className="cc-btn" disabled={privacyState === "busy"} onClick={() => void withdrawConsent()}>
                {privacyState === "busy"
                  ? t("privacy.withdrawing")
                  : privacyState === "failed"
                    ? t("privacy.retry")
                    : t("privacy.withdraw")}
              </button>
            )}
            <button type="button" className="cc-btn cc-btn-quiet" onClick={closePrivacy}>
              {t("privacy.close")}
            </button>
          </div>
        </GuestSheet>
      )}

      {/* Food Ordering Menu Overlay */}
      {viewMode === "menu" && (
        <div style={{ position: "absolute", inset: 0, zIndex: 150, background: "var(--hc-slate)" }}>
          <FoodMenu
            hotelName={hotelData?.name || "The Hotel"}
            assistantName={assistantName}
            hotelSlug={hotelSlug}
            room={ctx?.room?.number ?? null}
            currency={ctx?.hotel.currency}
            locale={ctx?.hotel.locale}
            lang={lang}
            onBack={() => setViewMode("concierge")}
            onPlaceOrder={placeMenuOrder}
          />
        </div>
      )}

      {/* Background */}

      <div
        style={{
          position: "absolute",
          inset: 0,
          zIndex: 0,
          backgroundImage: "url('/hotel_lobby_bg.png')",
          backgroundSize: "cover",
          backgroundPosition: "center",
          filter: "brightness(0.18) saturate(0.6) blur(2px)",
          pointerEvents: "none",
          WebkitTouchCallout: "none",
          userSelect: "none",
        } as React.CSSProperties}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          zIndex: 1,
          background:
            "radial-gradient(ellipse at 50% 35%, rgba(225, 175, 104,0.04) 0%, transparent 50%), radial-gradient(ellipse at 50% 80%, rgba(139,107,62,0.03) 0%, transparent 60%), linear-gradient(180deg, rgba(8,18,16,0.4) 0%, rgba(8,18,16,0.7) 50%, rgba(8,18,16,0.95) 100%)",
        }}
      />

      {/* <html lang> and the tab title follow the guest (QA GS-10). */}
      <DocumentMeta lang={lang} title={ctx?.hotel.name ?? null} />

      {/* Header (dir here, never on the root: see the RTL note in 03 spec 2.6) */}
      <header
        dir={dirAttr}
        style={{
          position: "relative",
          zIndex: 20,
          padding: "max(12px, env(safe-area-inset-top)) max(16px, env(safe-area-inset-right)) 12px max(16px, env(safe-area-inset-left))",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            opacity: 0.85,
            // Shrinks (and clips a long hotel name) before the header buttons do.
            minWidth: 0,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: "linear-gradient(135deg, #2D6A4F, #1B4332)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              border: "1px solid rgba(225, 175, 104,0.2)",
            }}
          >
            <span
              style={{
                fontFamily: "'Playfair Display', serif",
                fontSize: 13,
                fontWeight: 700,
                color: "var(--hc-gold)",
              }}
            >
              {hotelData!.name.split(/\s+/).filter(w => /^[A-Z]/.test(w)).slice(0, 2).map(w => w[0]).join("")}
            </span>
          </div>
          <div>
            <div
              style={{
                fontFamily: "'Playfair Display', serif",
                fontSize: 13,
                fontWeight: 600,
                color: "var(--hc-gold)",
                letterSpacing: "1.5px",
              }}
            >
              {hotelData!.name.split(" ").slice(0, 2).join(" ").toUpperCase()}
            </div>
            <div
              style={{
                fontSize: 8,
                color: "rgba(225, 175, 104,0.35)",
                letterSpacing: "2px",
              }}
            >
              {hotelData!.city.toUpperCase()}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          {ctx?.features.tracker && tracked.length > 0 && (
            <TrackerPill
              t={t}
              active={trackerActive}
              expanded={trackerOpen && tracked.length > 0}
              controls="cc-tracker-list"
              onClick={() => setTrackerOpen((o) => !o)}
            />
          )}
          <LanguagePicker t={t} lang={lang} preferred={ctx?.hotel.languages} onPick={pickLanguage} />
          <button
            type="button"
            className="cc-icon-btn"
            onClick={() => {
              setPrivacyState("idle");
              setPrivacyOpen(true);
            }}
            aria-label={t("header.privacy")}
            title={t("header.privacy")}
          >
            <LuShieldCheck size={16} />
          </button>
          <button
            onClick={() => setShowSubtitle(!showSubtitle)}
            aria-label={showSubtitle ? t("header.captionsHide") : t("header.captionsShow")}
            aria-pressed={showSubtitle}
            style={{
              background: "rgba(255,255,255,0.05)",
              border: "1px solid rgba(225, 175, 104,0.1)",
              borderRadius: 8,
              width: 36,
              height: 36,
              cursor: "pointer",
              fontSize: 14,
              color: showSubtitle ? "var(--hc-gold)" : "rgba(225, 175, 104,0.3)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
            title={showSubtitle ? t("header.captionsHide") : t("header.captionsShow")}
          >
            <LuSubtitles size={18} />
          </button>

          <button
            onClick={handleKeyboardToggle}
            aria-label={showTextInput ? t("header.voiceMode") : t("header.textMode")}
            aria-pressed={showTextInput}
            style={{
              background: showTextInput ? "rgba(225, 175, 104,0.12)" : "rgba(255,255,255,0.05)",
              border: showTextInput ? "1px solid rgba(225, 175, 104,0.25)" : "1px solid rgba(225, 175, 104,0.1)",
              borderRadius: 8,
              width: 36,
              height: 36,
              cursor: "pointer",
              fontSize: 14,
              color: showTextInput ? "var(--hc-gold)" : "rgba(225, 175, 104,0.3)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              transition: "all 0.3s ease",
            }}
            title={showTextInput ? t("header.voiceMode") : t("header.textMode")}
          >
            <LuKeyboard size={18} />
          </button>
        </div>
      </header>

      {/* Avatar Section */}
      <div
        style={{
          flex: 1,
          position: "relative",
          zIndex: 10,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "flex-start",
          paddingTop: "clamp(6px, 1.5vh, 20px)",
          overflow: "hidden",
          minHeight: 0,
        }}
      >
        {/* Avatar Container */}
        <div
          style={{
            position: "relative",
            width: "min(200px, 26dvh, 48vw)",
            height: "min(200px, 26dvh, 48vw)",
            marginTop: 0,
            flexShrink: 0,
          }}
        >
          {/* Speaking rings */}
          {avatarState === STATES.SPEAKING &&
            [0, 1, 2].map((i) => (
              <div
                key={i}
                style={{
                  position: "absolute",
                  inset: -(12 + i * 14),
                  borderRadius: "50%",
                  border: "1.5px solid rgba(254,252,246,0.2)",
                  animation: `pulseRing ${1.6 + i * 0.3}s ease-out ${i * 0.4}s infinite`,
                }}
              />
            ))}

          {/* Listening rings */}
          {avatarState === STATES.LISTENING &&
            [0, 1].map((i) => (
              <div
                key={i}
                style={{
                  position: "absolute",
                  inset: -(10 + i * 16),
                  borderRadius: "50%",
                  border: "1.5px solid rgba(231,211,163,0.25)",
                  animation: `pulseRing ${1.2 + i * 0.4}s ease-out ${i * 0.3}s infinite`,
                }}
              />
            ))}

          {/* Avatar Image */}
          <div
            style={{
              width: "100%",
              height: "100%",
              borderRadius: "50%",
              overflow: "hidden",
              boxShadow: avatarGlow,
              animation: avatarAnimation,
              border: "3px solid rgba(225, 175, 104,0.15)",
              transition: "box-shadow 0.5s ease",
              position: "relative",
              background: "transparent",
              transform: "translateZ(0)",
              backfaceVisibility: "hidden",
              WebkitBackfaceVisibility: "hidden",
            }}
          >
            <Image
              src="/female.png"
              alt={assistantName}
              fill
              priority
              sizes="(max-width: 768px) 50vw, 200px"
              style={{
                objectFit: "cover",
                opacity: 1, // Keep image as base layer/fallback
                transition: "opacity 0.5s ease",
              }}
            />

            {/* Video avatar - always rendered (not gated on videoReady) so
                the ref is available immediately for user-gesture play() calls.
                Uses opacity 0.01 when not speaking instead of 0.001 to prevent
                iOS Safari from treating it as invisible and suspending decode. */}
              <video
                ref={videoRef}
                autoPlay
                muted
                playsInline
                {...{"webkit-playsinline": ""} as any}
                disablePictureInPicture
                preload="auto"
                loop
                poster="/female.png"
                className="gpu-layer"
                onLoadedData={() => {
                  // Mark video as ready and start playback
                  setVideoReady(true);
                  ensureVideoPlaying();
                }}
                onCanPlay={() => {
                  // Additional hook: iOS sometimes fires canplay but not loadeddata
                  if (!videoReady) setVideoReady(true);
                  ensureVideoPlaying();
                }}
                style={{
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  opacity: visemeReady ? 0 : avatarState === STATES.SPEAKING ? 1 : 0.01,
                  // Fast, symmetric cut. The old 0.5s ease meant the avatar was
                  // still visibly mouthing for half a second after the voice ended.
                  transition: "opacity 160ms linear",
                  position: "absolute",
                  inset: 0,
                  willChange: "opacity"
                }}
              >
                <source src="/avataar_female_noaudio.mp4" type="video/mp4" />
                <source src="/avataar_female-compressed.webm" type="video/webm" />
              </video>

            {/* Viseme rig. Sits above the video and the still, and is only ever
                opaque once its assets have loaded, so a failed fetch leaves the
                existing avatar untouched rather than showing a blank circle. */}
            <canvas
              ref={visemeCanvasRef}
              aria-hidden
              style={{
                position: "absolute",
                inset: 0,
                width: "100%",
                height: "100%",
                opacity: visemeReady ? 1 : 0,
                pointerEvents: "none",
              }}
            />

            {avatarState === STATES.LISTENING && (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  background:
                    "radial-gradient(circle at center, rgba(231,211,163,0.06), transparent 70%)",
                }}
              />
            )}

            {avatarState === STATES.THINKING && (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  background:
                    "linear-gradient(135deg, rgba(225, 175, 104,0.04), transparent, rgba(225, 175, 104,0.04))",
                  backgroundSize: "200% 200%",
                  animation: "bgPan 2s ease infinite",
                }}
              />
            )}
          </div>
        </div>

        {/* Name & Status */}
        <div style={{ textAlign: "center", marginTop: 12 }}>
          <div
            className="gold-gradient-text"
            style={{
              fontFamily: "'Playfair Display', serif",
              fontSize: 28,
              letterSpacing: "6px",
              fontWeight: 700,
              textAlign: "center"
            }}
          >
            {assistantName.toUpperCase()}
          </div>
          <div
            style={{
              fontSize: 11,
              letterSpacing: "2px",
              color: statusColor,
              marginTop: 4,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 6,
              transition: "color 0.3s",
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: statusColor,
                display: "inline-block",
                boxShadow:
                  avatarState !== STATES.IDLE
                    ? `0 0 10px ${statusColor}`
                    : "none",
              }}
            />
            {statusLabel}
          </div>
        </div>

        {/* Emergency numbers: shown whatever the captions setting */}
        {emergency && emergency.length > 0 && (
          <div style={{ marginTop: "clamp(6px, 1.5vh, 12px)", width: "100%", display: "flex", justifyContent: "center" }}>
            <EmergencyCard t={t} lang={lang} numbers={emergency} onDismiss={() => setEmergency(null)} />
          </div>
        )}

        {/* Response Area */}
        {showSubtitle && (
          <div
            dir={dirAttr}
            lang={lang}
            style={{
              marginTop: "clamp(6px, 1.5vh, 12px)",
              maxWidth: 560,
              width: "calc(100% - 32px)",
              maxHeight: "clamp(90px, 22vh, 200px)",
              overflowY: "auto",
              animation: "fadeUp 0.3s ease",
            }}
          >
            {transcript && (
              <div
                style={{
                  textAlign: "center",
                  marginBottom: 8,
                  fontSize: 12,
                  color: "rgba(232,228,220,0.4)",
                  fontStyle: "italic",
                  animation: "fadeIn 0.3s ease",
                }}
              >
                &quot;{transcript}&quot;
              </div>
            )}

            {responseText && !isLoading && (
              <div
                style={{
                  background: "rgba(12,27,21,0.5)",
                  backdropFilter: "blur(16px)",
                  WebkitBackdropFilter: "blur(16px)",
                  borderRadius: 16,
                  padding: "14px 20px",
                  textAlign: "center",
                  fontSize: 14,
                  lineHeight: 1.65,
                  color: "var(--hc-soft-gold, #FDDA96)",
                  border: "1px solid rgba(225, 175, 104,0.12)",
                  animation: "fadeUp 0.4s ease",
                  opacity: displayedText ? 1 : 0,
                  transition: "opacity 0.4s ease",
                  boxShadow: "0 4px 24px rgba(0,0,0,0.2), inset 0 1px 0 rgba(225, 175, 104,0.06)",
                }}
              >
                {displayedText}
                {avatarState === STATES.SPEAKING && displayedText && (
                  <span
                    style={{
                      display: "inline-block",
                      width: "2px",
                      height: "1em",
                      background: "var(--hc-gold)",
                      marginLeft: "4px",
                      animation: "blink 0.7s infinite",
                    }}
                  />
                )}
              </div>
            )}

            {isLoading && (
              <div
                style={{
                  display: "flex",
                  justifyContent: "center",
                  padding: "12px 0",
                  animation: "fadeIn 0.3s ease",
                }}
              >
                <div style={{ display: "flex", gap: 6 }}>
                  {[0, 1, 2].map((j) => (
                    <div
                      key={j}
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: "var(--hc-gold)",
                        animation: `subtleFloat 1s ease-in-out ${j * 0.2}s infinite`,
                        opacity: 0.5,
                      }}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* The guest's requests and orders (polls /public/r/{token}) */}
        {trackerOpen && tracked.length > 0 && (
          <div id="cc-tracker-list" style={{ marginTop: 8, width: "100%", display: "flex", justifyContent: "center", maxHeight: "clamp(110px, 26vh, 240px)", overflowY: "auto" }}>
            <StatusTracker
              t={t}
              lang={lang}
              items={tracked}
              statuses={trackerStatuses}
              onStatus={(token, st) => setTrackerStatuses((prev) => ({ ...prev, [token]: st }))}
              onPatch={patchTracked}
              whatsappNumber={ctx?.features.whatsapp ? ctx.hotel.whatsapp_number : null}
            />
          </div>
        )}

        {/* Sound Waves */}
        {avatarState === STATES.SPEAKING && (
          <div
            style={{
              marginTop: 12,
              animation: "fadeIn 0.3s ease",
            }}
          >
            <SoundWave active={true} color="var(--hc-gold)" barCount={16} />
          </div>
        )}

        {avatarState === STATES.LISTENING && (
          <div
            style={{
              marginTop: 12,
              animation: "fadeIn 0.3s ease",
            }}
          >
            <SoundWave active={true} color="var(--hc-soft-gold)" barCount={12} />
          </div>
        )}
      </div>

      {/* Bottom Controls */}
      <div
        dir={dirAttr}
        lang={lang}
        style={{
          position: "relative",
          zIndex: 20,
          padding: "12px max(16px, env(safe-area-inset-right)) max(16px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left))",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 10,
        }}
      >
        {error && (
          <div
            style={{
              padding: "8px 16px",
              borderRadius: 10,
              background: "rgba(231,211,163,0.1)",
              border: "1px solid rgba(231,211,163,0.15)",
              color: "var(--hc-soft-gold)",
              fontSize: 11,
              animation: "fadeUp 0.3s ease",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            {error}
            <button
              onClick={() => setError(null)}
              aria-label={t("errors.dismiss")}
              style={{
                background: "none",
                border: "none",
                color: "rgba(231,211,163,0.5)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
              }}
            >
              <LuX size={14} />
            </button>
          </div>
        )}

        {notice && (
          <div className="cc-banner" role="status">
            {notice}
          </div>
        )}

        {showTextInput && (
          <form
            onSubmit={handleTextSubmit}
            style={{
              width: "calc(100% - 16px)",
              maxWidth: 480,
              display: "flex",
              background: "rgba(225, 175, 104,0.03)",
              backdropFilter: "blur(20px)",
              WebkitBackdropFilter: "blur(20px)",
              border: "1px solid rgba(225, 175, 104,0.15)",
              borderRadius: 24,
              overflow: "hidden",
              animation: "fadeUp 0.3s ease",
              boxShadow: "0 8px 32px rgba(0,0,0,0.3), inset 0 1px 0 rgba(225, 175, 104,0.06)",
            }}
          >
            <input
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              placeholder={t("input.placeholder", { name: assistantName })}
              autoFocus
              enterKeyHint="send"
              autoComplete="off"
              autoCorrect="off"
              aria-label={t("input.aria", { name: assistantName })}
              style={{
                flex: 1,
                minWidth: 0,
                padding: "14px 16px",
                border: "none",
                background: "transparent",
                color: "#E8E4DC",
                fontSize: 16,
                outline: "none",
                fontFamily: "'Inter', sans-serif",
              }}
            />
            <button
              type="submit"
              disabled={!textInput.trim() || isLoading}
              aria-label={t("input.send")}
              style={{
                padding: "0 16px",
                border: "none",
                background: "transparent",
                color: textInput.trim() ? "var(--hc-gold)" : "rgba(225, 175, 104,0.15)",
                cursor: textInput.trim() ? "pointer" : "default",
                fontSize: 18,
                display: "flex",
                alignItems: "center",
                transition: "all 0.3s ease",
                flexShrink: 0,
              }}
            >
              <LuSend size={20} />
            </button>
          </form>
        )}

        <div style={{ position: "relative" }}>
          {isRecording && (
            <div
              style={{
                position: "absolute",
                inset: -6,
                borderRadius: "50%",
                border: "2px solid rgba(231,211,163,0.3)",
                animation: "pulseRing 1.5s ease-out infinite",
              }}
            />
          )}
          <button
            aria-label={isRecording ? t("input.micStop") : t("input.micStart")}
            onContextMenu={(e) => e.preventDefault()}
            onClick={() => {
              ensureVideoPlaying();
              if (isLoading) return;

              // Voice cannot get through on this network: stay in text.
              if (onHttp()) {
                setShowTextInput(true);
                setNotice(t("errors.voiceFallback"));
                return;
              }

              // The mic needs recording consent. A guest who declined it can
              // grant it now; the running text session was minted without it,
              // so a fresh voice session carries the new consent.
              if (!consentOk("voice")) {
                withConsent("voice", (m) => {
                  if (m !== "voice") return;
                  if (pcRef.current) stopLive();
                  startLive(0, "voice").catch(() => {});
                });
                return;
              }

              // ── First tap with no session (or a text-only one): start voice ──
              if (!pcRef.current || rtcModeRef.current === "text") {
                if (pcRef.current) stopLive();
                startLive(0, "voice").catch(() => {});
                return;
              }

              if (isRecording) {
                // ── Toggle OFF: mute mic, go IDLE ──
                stopListening(false);
              } else {
                // ── Toggle ON: switch to voice if in text mode, unmute mic ──
                if (currentModeRef.current === "text") {
                  currentModeRef.current = "voice";
                  setShowTextInput(false);
                  if (audioRef.current) audioRef.current.muted = false;
                  if (dcRef.current?.readyState === "open") {
                    dcRef.current.send(JSON.stringify({
                      type: "session.update",
                      session: {
                        type: "live",
                        output_modalities: ["audio"],
                        audio: buildLiveAudioConfig(ACTIVE_VOICE_TURN_DETECTION, langHint()),
                      },
                    }));
                  }
                }
                if (streamRef.current) {
                  streamRef.current.getAudioTracks().forEach(t => { t.enabled = true; });
                }
                // Set the ref before arming: armListenTimers checks it, and the
                // effect that mirrors isRecording has not run yet at this point.
                isRecordingRef.current = true;
                setIsRecording(true);
                setMicPausedHint(false);
                setAvatarState(STATES.LISTENING);
                armListenTimers(true);
              }
            }}
            style={{
              width: 76,
              height: 76,
              borderRadius: "50%",
              border: "none",
              cursor: isLoading ? "wait" : "pointer",
              background: isRecording
                ? "linear-gradient(135deg, #FF4B2B, #FF416C)"
                : "linear-gradient(135deg, #E1AF68 0%, #A68A3D 100%)",
              color: "white",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: isRecording
                ? "0 0 40px rgba(255,75,43,0.4), inset 0 2px 4px rgba(255,255,255,0.2)"
                : "0 12px 30px rgba(225, 175, 104,0.25), 0 4px 12px rgba(0,0,0,0.3), inset 0 2px 4px rgba(255,255,255,0.15)",
              transition: "all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)",
              transform: isRecording ? "scale(1.1)" : "scale(1)",
              animation: isRecording
                ? "micPulse 1.2s ease-in-out infinite"
                : "none",
              opacity: isLoading ? 0.5 : 1,
              userSelect: "none",
              WebkitUserSelect: "none",
              WebkitTouchCallout: "none",
              WebkitTapHighlightColor: "transparent",
            }}
          >
            {isRecording ? <LuSquare size={30} fill="white" /> : <LuMic size={30} />}
          </button>
        </div>

        {/* Quick question chips - faded out while the assistant is speaking for
            breathing room, but kept mounted. Unmounting them collapsed their
            height out of this column, which shunted the mic button down and back
            up again on every reply. */}
        <div
          role="group"
          aria-label={t("input.chips")}
          aria-hidden={avatarState === STATES.SPEAKING}
          style={{
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "center",
            gap: 6,
            maxWidth: 520,
            width: "100%",
            marginTop: 4,
            padding: "0 4px",
            animation: "fadeUp 0.5s ease 0.3s both",
            opacity: avatarState === STATES.SPEAKING ? 0 : 1,
            pointerEvents: avatarState === STATES.SPEAKING ? "none" : "auto",
            transition: "opacity 0.25s ease",
          }}
        >
          {(ctx?.chips ?? []).map((chip) => {
            const label = chipLabel(chip);
            return (
              <button
                key={chip.key || label}
                disabled={avatarState === STATES.THINKING}
                // Faded-out chips are still in the DOM, so keep them off the tab
                // order rather than leaving invisible focus stops behind.
                tabIndex={avatarState === STATES.SPEAKING ? -1 : undefined}
                className={chip.key === "room_service" ? "menu-btn" : "quick-chip"}
                onClick={() => handleChip(chip, label)}
                onContextMenu={(e) => e.preventDefault()}
                style={{
                  cursor: (avatarState === STATES.SPEAKING || avatarState === STATES.THINKING) ? "default" : "pointer",
                  opacity: (avatarState === STATES.SPEAKING || avatarState === STATES.THINKING) ? 0.4 : 1,
                }}
              >
                {label}
              </button>
            );
          })}
        </div>

        <div
          style={{
            fontSize: 8,
            letterSpacing: "1.5px",
            color: "rgba(225, 175, 104,0.15)",
            textAlign: "center",
            textTransform: "uppercase",
          }}
        >
          {/* The hotel's languages, as the picker lists them (QA GS-9). */}
          {t("footer.languages", { n: offeredLanguages(ctx?.hotel.languages, GUEST_LANGUAGES, lang).length })}
        </div>
      </div>
    </div>
  );
}
