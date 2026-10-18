/* ═══════════════════════════════════════════════════════════════════════
   Guest concierge strings: English, the master dictionary (03 spec 2.6).

   Every other language file is typed as GuestDict, so tsc fails on a
   missing key, and scripts/check-i18n.mjs fails CI on a missing, extra or
   empty key and on a {placeholder} that a translation dropped.

   Keep this file a plain object literal of strings: check-i18n.mjs reads
   the literal without a TypeScript compiler.
   ═══════════════════════════════════════════════════════════════════════ */

const en = {
  meta: { name: "English" },
  status: {
    idle: "Tap the mic to speak",
    listening: "Listening…",
    thinking: "Thinking…",
    speaking: "Speaking…",
    micPaused: "Mic paused · tap to speak again",
    typeBelow: "Type your question below",
    textOnly: "Voice is unavailable here · type below",
  },
  splash: {
    tagline: "Your personal AI concierge at",
    start: "START CONVERSATION",
    loading: "LOADING…",
    notFound: "We could not find this hotel. Please scan the QR code in your room again.",
    loadFailed: "We could not load the hotel. Please check the link or try again.",
    home: "GO TO HOMEPAGE",
    limited: "Limited mode: scan the QR code in your room for help with your stay.",
    /** An unkeyed legacy room QR (key_status "absent"): works by design,
        requests are marked verified_room=false (03 spec 397), not an error. */
    legacyRoom: "Staff will confirm your room at the door for anything you request here.",
  },
  header: {
    captionsShow: "Show captions",
    captionsHide: "Hide captions",
    textMode: "Switch to text mode",
    voiceMode: "Switch to voice mode",
    language: "Language",
    privacy: "Privacy",
  },
  input: {
    placeholder: "Ask {name} anything...",
    aria: "Type your question to {name}",
    send: "Send message",
    micStart: "Tap to speak",
    micStop: "Tap to stop listening",
    chips: "Quick questions",
  },
  footer: { languages: "AI assistant · {n} languages" },
  errors: {
    noWebRtc: "This browser cannot use voice chat. Please open this page in Chrome, Safari, Firefox or Edge.",
    noMic: "This browser cannot use the microphone. Please switch to typing instead.",
    micDenied: "Microphone access was denied. You can type your question instead.",
    audioFailed: "Audio could not start. Please try again.",
    connection: "Connection failed. Please try again.",
    notReady: "Connection not ready. Please try again.",
    outdated: "This page is out of date. Please refresh it.",
    busy: "The assistant is busy right now. Please try again in a few minutes.",
    aiOff: "The assistant is not available right now. Please call the front desk.",
    voiceFallback: "Voice is not available on this network, so answers will appear as text.",
    orderFailed: "Your order did not go through. Please try again.",
    dismiss: "Dismiss error",
  },
  consent: {
    title: "Before we start",
    intro: "{name} is an AI assistant. Please review how your conversation is handled.",
    required: "Required",
    optional: "Optional",
    forVoice: "Needed for voice",
    voiceNote: "Voice needs recording. Without it you can still type.",
    continue: "Continue",
    textOnly: "Continue with text only",
    cancel: "Not now",
    agree: "I agree",
  },
  privacy: {
    title: "Your privacy",
    body: "You can stop the assistant and withdraw your consent at any time. Transcripts stop immediately.",
    withdraw: "Stop and withdraw consent",
    withdrawing: "Withdrawing…",
    withdrawn: "Done. Your consent is withdrawn and nothing more is recorded.",
    forgotten: "This device has forgotten your consent. Nothing more is recorded from here.",
    failed: "We could not reach the hotel, so your consent is not withdrawn yet. Please try again.",
    retry: "Try again",
    close: "Close",
  },
  menu: {
    eyebrow: "In-Room Dining",
    title: "Specialties",
    intro: "Handpicked dishes crafted by our chefs for a perfect in-room experience.",
    add: "ADD",
    unavailable: "Not available now",
    placing: "PLACING ORDER...",
    place: "PLACE ORDER",
    itemsOne: "{n} item added",
    itemsMany: "{n} items added",
    plusFees: "+ service charge, fees and VAT",
    received: "Order Received!",
    receivedBody: "Your meal is being prepared by our chefs and will be delivered to your room shortly.",
    back: "Back to {name}",
    help: "Need help? Ask {name} verbally anytime.",
    loadFail: "We couldn't load the menu.",
    retry: "Try again",
    orderFail: "Your order didn't go through.",
    empty: "The menu isn't available right now.",
    closed: "Ordering is paused right now. Please ask the front desk.",
    pairTitle: "Goes well with",
    minutes: "{m} min",
    other: "More",
  },
  tracker: {
    title: "Your requests",
    open: "{n} active",
    received: "Received",
    assigned: "Assigned",
    on_the_way: "On the way",
    done: "Done",
    cancelled: "Cancelled",
    resolvedQ: "Resolved?",
    yes: "Yes",
    no: "No",
    thanks: "Thank you for letting us know.",
    reopened: "Sorry about that. We have reopened it and the team is on it.",
    whatsapp: "Get updates on WhatsApp",
    order: "Order",
    request: "Request",
    close: "Close",
    show: "Show requests",
  },
  emergency: {
    title: "Emergency help",
    body: "If anyone is in danger, call now. Hotel staff have been alerted.",
    call: "Call {number}",
    dismiss: "Close",
    kinds: {
      police: "Police",
      ambulance: "Ambulance",
      civil_defence: "Fire and civil defence",
      coast_guard: "Coast guard",
      search_rescue: "Search and rescue",
      electricity_water: "Electricity and water",
      fire: "Fire",
      other: "Emergency",
    },
  },
  chat: {
    requestSent: "Done. The team has your request and you can follow it below.",
    orderPlaced: "Thank you, your order is placed. You can follow it below.",
    handoff: "A member of our team will help you with this shortly.",
    failed: "Sorry, that did not go through. Please try again or call the front desk.",
  },
  chips: {
    towels: "Extra towels",
    water: "Bottled water",
    housekeeping: "Clean my room",
    wifi: "Wi-Fi details",
    checkout: "Check-out time",
    room_service: "Order food",
    front_desk: "Talk to the front desk",
  },
  language: { label: "Choose language", close: "Close" },
};

type Widen<T> = { [K in keyof T]: T[K] extends string ? string : Widen<T[K]> };

/** The shape every guest dictionary must match, key for key. */
export type GuestDict = Widen<typeof en>;

export default en as GuestDict;
