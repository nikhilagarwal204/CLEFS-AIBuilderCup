/* Shared labels for the transcripts list and drawer. */

import type { Tone } from "../../../../../ui";
import type { SafetyLevel, TranscriptSource } from "../types";

export const SOURCE_LABEL: Record<TranscriptSource, string> = {
  web_room: "Room page",
  web_location: "Venue page",
  phone: "Phone",
  text_fallback: "Text chat",
  whatsapp: "WhatsApp",
  legacy: "Old guest page",
};

export const SOURCE_OPTIONS = (Object.keys(SOURCE_LABEL) as TranscriptSource[]).map(v => ({ value: v, label: SOURCE_LABEL[v] }));

export const SAFETY_TONE: Record<SafetyLevel, Tone> = { emergency: "bad", sensitive: "warn", handoff: "info", none: "neutral" };
