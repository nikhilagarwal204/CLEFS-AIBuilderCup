/**
 * The guest-facing voice assistant persona.
 *
 * Defaults to the platform brand, but each property can rename it from the
 * database (`hotels.assistant_name`) - so a hotel can have its concierge
 * introduce itself however fits their service language. This is the fallback
 * when unset.
 */
export const DEFAULT_ASSISTANT_NAME = "Clefs";

/** Anything carrying an optional per-hotel assistant name. */
interface HasAssistantName {
  assistant_name?: string | null;
}

export function resolveAssistantName(hotel: HasAssistantName | null | undefined): string {
  return hotel?.assistant_name?.trim() || DEFAULT_ASSISTANT_NAME;
}
