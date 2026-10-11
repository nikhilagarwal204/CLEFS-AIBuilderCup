/* Settings > Tax for a hotel outside the UAE: the hotel's own tax setup as
   recorded on hotels.tax_profile (GET /hotels/{slug}/settings), in plain
   lines. core/tax keeps rules for the UAE fee stack only, so for any other
   country nothing is split out of a night's price and this is a record of
   the hotel's setup, not a calculation. The Tokyo demo hotel records
   {country, prices_include_tax, consumption_tax_pct, accommodation_tax:
   {authority, basis, bands: [{from, amount}]}}; other keys are shown as
   they are, and the UAE fallback keys (convention, property_category) are
   left out. Pure: no React, so node --test loads it directly. */

export interface TaxLine {
  label: string;
  /** One line, or several (accommodation tax bands). */
  value: string | string[];
}

type Money = (amount: number) => string;

// UAE fallback keys of hotels.tax_profile (core/tax rules.py) and the
// country, which the page states on its own.
const SKIP = new Set(["country", "convention", "property_category", "td_per_bedroom", "trn"]);

const BASIS: Record<string, string> = {
  per_person_per_night: "per person per night",
  per_room_per_night: "per room per night",
  per_night: "per night",
  per_stay: "per stay",
};

/** "consumption_tax_pct" -> "Consumption tax". */
export function humanize(key: string): string {
  const words = key.replace(/_pct$/, "").replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : key;
}

/** "10" -> "10%", "8.50" -> "8.5%"; null when it is not a number. */
function pct(v: unknown): string | null {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
  return Number.isFinite(n) ? `${Number(n.toFixed(4))}%` : null;
}

function num(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

/** Bands [{from, amount}] as "Under X: none", "X to under Y: Z", "Y and over: W". */
export function bandLines(bands: unknown, money: Money): string[] {
  if (!Array.isArray(bands)) return [];
  const rows = bands
    .map((b) => (b && typeof b === "object" ? { from: num((b as Record<string, unknown>).from), amount: num((b as Record<string, unknown>).amount) } : null))
    .filter((b): b is { from: number; amount: number } => !!b && b.from !== null && b.amount !== null)
    .sort((a, b) => a.from - b.from);
  return rows.map((b, i) => {
    const next = rows[i + 1];
    const range = next
      ? b.from <= 0 ? `Under ${money(next.from)}` : `${money(b.from)} to under ${money(next.from)}`
      : `${money(b.from)} and over`;
    return `${range}: ${b.amount === 0 ? "none" : money(b.amount)}`;
  });
}

/** The lines the page shows; [] when the hotel recorded nothing. */
export function localTaxLines(profile: unknown, money: Money): TaxLine[] {
  if (!profile || typeof profile !== "object" || Array.isArray(profile)) return [];
  const out: TaxLine[] = [];
  for (const [key, v] of Object.entries(profile as Record<string, unknown>)) {
    if (SKIP.has(key) || v === null || v === undefined || v === "") continue;
    if (key === "prices_include_tax" && typeof v === "boolean") {
      out.push({ label: "Prices include tax", value: v ? "Yes: every price already includes tax" : "No: tax is added to the price" });
    } else if (key.endsWith("_pct")) {
      const p = pct(v);
      if (p) out.push({ label: humanize(key), value: p });
    } else if (v && typeof v === "object" && !Array.isArray(v)) {
      const o = v as Record<string, unknown>;
      const who = typeof o.authority === "string" ? o.authority : null;
      const basis = typeof o.basis === "string" ? BASIS[o.basis] ?? o.basis.replace(/_/g, " ") : null;
      const bands = bandLines(o.bands, money);
      const head = [who, basis].filter(Boolean).join(", ");
      if (head || bands.length) out.push({ label: humanize(key), value: head ? [head, ...bands] : bands });
    } else if (typeof v === "string" || typeof v === "number") {
      out.push({ label: humanize(key), value: String(v) });
    } else if (typeof v === "boolean") {
      out.push({ label: humanize(key), value: v ? "Yes" : "No" });
    }
  }
  return out;
}

/** "Japan" for JP, the code itself when Intl has no name. */
export function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code.toUpperCase()) ?? code;
  } catch {
    return code;
  }
}
