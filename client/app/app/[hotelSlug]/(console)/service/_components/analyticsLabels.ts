/* ═══════════════════════════════════════════════════════════════════════
   Group labels for the Owner view (Response times): what an owner reads
   for each metrics dimension value. The metrics engine groups by raw keys
   (department "ird" or a legacy category such as "Food & Beverage", floor
   "4", shift "evening"); an owner should read "In-Room Dining", "Floor 4"
   and "Evening (15:00-23:00)". The deck says "team", so the department
   dimension is labelled Team.

   Pure and import-free so Node's test runner loads it directly
   (__tests__/analyticsLabels.test.mjs). The caller passes the hotel's own
   departments, shift templates and request types when it has them; the
   seeds below cover a hotel whose config routes have not answered.
   ═══════════════════════════════════════════════════════════════════════ */

export type LabelGroup =
  | "staff" | "floor" | "shift" | "department" | "room_type" | "language" | "stay_stage" | "source" | "request_type";

export interface LabelContext {
  departments?: readonly { key: string; name: string; legacy_category?: string | null }[];
  shifts?: readonly { key: string; name: string; start_time?: string | null; end_time?: string | null }[];
  requestTypes?: readonly { key: string; label_en: string }[];
  languages?: Readonly<Record<string, string>>;
  sources?: Readonly<Record<string, string>>;
}

/** Column and selector names. "Team" is the deck's word for a department. */
export const GROUP_TITLES: Record<LabelGroup, string> = {
  staff: "Staff", floor: "Floor", shift: "Shift", department: "Team", room_type: "Room type",
  language: "Language", stay_stage: "Stay stage", source: "Channel", request_type: "Request type",
};

/** service/constants.py DEPARTMENTS, for hotels whose config has not loaded. */
const SEED_TEAMS: readonly { key: string; name: string; legacy_category: string }[] = [
  { key: "housekeeping", name: "Housekeeping", legacy_category: "Housekeeping" },
  { key: "ird", name: "In-Room Dining", legacy_category: "Food & Beverage" },
  { key: "engineering", name: "Engineering", legacy_category: "Maintenance" },
  { key: "front_office", name: "Front Office", legacy_category: "Front Desk" },
  { key: "concierge", name: "Concierge", legacy_category: "Concierge" },
  { key: "security", name: "Security", legacy_category: "Security" },
  { key: "general", name: "General", legacy_category: "General" },
];

const SEED_SHIFTS: Record<string, string> = { morning: "Morning", evening: "Evening", night: "Night" };

const STAY_STAGES: Record<string, string> = {
  pre_arrival: "Before arrival", arrival: "Arrival day", in_house: "In house", in_stay: "In house",
  departure: "Departure day", post_stay: "After checkout", post_checkout: "After checkout",
};

const NONE = new Set(["", "(none)", "null", "none", "undefined"]);

function words(key: string): string {
  return key.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
}

/** "front_office" -> "Front Office". */
export function titleCase(key: string): string {
  return words(key).replace(/(^|\s)(\S)/g, (_m, space: string, ch: string) => space + ch.toUpperCase());
}

/** "pre_arrival" -> "Pre arrival". */
function sentenceCase(key: string): string {
  const w = words(key);
  return w ? w.charAt(0).toUpperCase() + w.slice(1) : w;
}

/** A team name for a department key or a legacy category, never a raw key. */
export function teamLabel(key: string, departments?: LabelContext["departments"]): string {
  const k = key.trim();
  const lower = k.toLowerCase();
  const norm = lower.replace(/[\s-]+/g, "_");
  for (const list of [departments ?? [], SEED_TEAMS]) {
    const hit = list.find(d => d.key.toLowerCase() === norm)
      ?? list.find(d => (d.legacy_category ?? "").toLowerCase() === lower)
      ?? list.find(d => d.name.toLowerCase() === lower);
    if (hit) return hit.name;
  }
  return titleCase(k);
}

/** "4" -> "Floor 4"; a name that already says floor or level is kept. */
export function floorLabel(key: string): string {
  const k = key.trim();
  if (/^(floor|level)\b/i.test(k)) return k;
  return `Floor ${k}`;
}

function hhmm(t: string | null | undefined): string {
  return (t ?? "").slice(0, 5);
}

/** "evening" -> "Evening (15:00-23:00)" from the hotel's shift templates;
    `compact` leaves the hours out (a narrow chart label). */
export function shiftLabel(key: string, shifts?: LabelContext["shifts"], compact = false): string {
  const k = key.trim();
  const t = shifts?.find(s => s.key === k);
  if (t) {
    const span = !compact && hhmm(t.start_time) && hhmm(t.end_time) ? ` (${hhmm(t.start_time)}-${hhmm(t.end_time)})` : "";
    return `${t.name}${span}`;
  }
  return SEED_SHIFTS[k.toLowerCase()] ?? sentenceCase(k);
}

/** The label an owner reads for one dimension value. `compact` keeps it
    short for a chart's label column (no shift hours). */
export function groupLabel(group: LabelGroup, key: string | null | undefined, ctx: LabelContext = {}, compact = false): string {
  const k = (key ?? "").trim();
  if (NONE.has(k.toLowerCase())) return "Not recorded";
  switch (group) {
    case "department":
      return teamLabel(k, ctx.departments);
    case "floor":
      return floorLabel(k);
    case "shift":
      return shiftLabel(k, ctx.shifts, compact);
    case "language":
      return ctx.languages?.[k.toLowerCase()] ?? k.toUpperCase();
    case "source":
      return ctx.sources?.[k] ?? sentenceCase(k);
    case "request_type":
      return ctx.requestTypes?.find(t => t.key === k)?.label_en ?? sentenceCase(k);
    case "stay_stage":
      return STAY_STAGES[k.toLowerCase()] ?? sentenceCase(k);
    case "room_type":
      return k.replace(/_/g, " ");
    case "staff":
    default:
      return k;
  }
}

/** Labels that stay unique when two keys read the same (a team key and its
    legacy category both read "In-Room Dining"): later ones carry their key. */
export function distinctLabels<T extends { key: string; label: string }>(rows: readonly T[]): T[] {
  const seen = new Set<string>();
  return rows.map(r => {
    const label = seen.has(r.label) ? `${r.label} (${r.key})` : r.label;
    seen.add(label);
    return label === r.label ? r : { ...r, label };
  });
}

/** A chart's value label: "6.6m", "45m", "1h 5m" (fits a narrow column). */
export function shortMinutes(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  if (v < 1) return "<1m";
  if (v < 10) return `${Math.round(v * 10) / 10}m`;
  if (v < 60) return `${Math.round(v)}m`;
  return `${Math.floor(v / 60)}h ${Math.round(v % 60)}m`;
}

/** Rows for one owner-view bar chart: the slowest first response on top, at most `limit`. */
export function rankBySlowest<T extends { values: Record<string, number | null | undefined> }>(
  rows: readonly T[], metric: string, limit = 8,
): T[] {
  return rows
    .filter(r => typeof r.values[metric] === "number" && Number.isFinite(r.values[metric] as number))
    .sort((a, b) => (b.values[metric] as number) - (a.values[metric] as number))
    .slice(0, limit);
}
