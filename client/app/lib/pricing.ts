/**
 * Public pricing facts. Clefs quotes each hotel in AED; the site shows a number
 * only when one is decided here.
 *
 * `PILOT_PRICE_AED` is null until the founder sets a pilot price, and /pilot
 * renders a price line only when it is not null (03 section 6). Money follows
 * the platform rule: integer minor units (fils, 100 per dirham), never floats,
 * so the page and any server quote agree to the fil.
 */

// ─── Constants ───────────────────────────────────────────────────────────────

export const PRICING_CURRENCY = "AED" as const;

/** Pilot price in fils (minor units), or null when not published. */
export const PILOT_PRICE_AED: number | null = null;

/** Pilot length, in days. Day 1 is the day the first file is imported; Days 1-3 baseline, 4-13 live, 14 report. */
export const PILOT_DAYS = 14;

// The pilot letter's terms: the pilot is free, and the monthly price after it
// is written down with the hotel before Day 1, never published. /pilot and the
// FAQ both render this one sentence. It says "free", so a PILOT_PRICE_AED
// other than null would contradict it: change the two together.
export const PRICING_NOTE =
  `The ${PILOT_DAYS}-day pilot is free. Before Day 1 we write down with you what success means and the monthly price if you continue.`;

// ─── Formatting ──────────────────────────────────────────────────────────────

/** "AED 12,500" from minor units; fils shown only when the amount has them. */
export function formatAed(minor: number): string {
  const whole = minor % 100 === 0;
  const n = (minor / 100).toLocaleString("en-AE", {
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  });
  return `${PRICING_CURRENCY} ${n}`;
}

/** The pilot price line for /pilot, or null when no price is published. */
export function pilotPriceText(): string | null {
  return PILOT_PRICE_AED === null ? null : formatAed(PILOT_PRICE_AED);
}
