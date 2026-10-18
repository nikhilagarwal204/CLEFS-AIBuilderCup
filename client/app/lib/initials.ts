/**
 * Avatar initials, one helper for the console account menu and the device
 * lock screen. Pure (no React, no imports) so node --test can load it.
 *
 * WHY a helper: both screens took the first character of the LAST
 * whitespace token, so demo names such as "Ahmed Khan (DEMO)" rendered as
 * "A(" (QA GS-12, PUB-4). A trailing parenthetical is a tag, not a surname,
 * and a token with no letter in it ("(DEMO)", "-", "2") never names anyone.
 */

// ─── Initials ────────────────────────────────────────────────────────────────

const HAS_LETTER = /\p{L}/u;

/** "Ahmed Khan" -> "AK", "Ahmed Khan (DEMO)" -> "AK", "Rana (DEMO)" -> "R",
    "" / null -> "?". Letters only, first and last name-like tokens. */
export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  const stripped = name.replace(/\s*[([][^)\]]*[)\]]\s*$/u, "").trim();
  const parts = stripped.split(/\s+/).filter((p) => HAS_LETTER.test(p));
  if (parts.length === 0) return "?";
  const first = (p: string) => Array.from(p).find((ch) => HAS_LETTER.test(ch)) ?? "";
  const out = first(parts[0]) + (parts.length > 1 ? first(parts[parts.length - 1]) : "");
  return out.toUpperCase() || "?";
}
