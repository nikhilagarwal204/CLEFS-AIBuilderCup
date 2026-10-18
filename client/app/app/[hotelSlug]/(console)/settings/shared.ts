/* Small helpers shared by the settings pages (not a route: no page.tsx). */

/** List endpoints may answer with a bare array or a Page<T> ({items}). Accept both. */
export function asList<T>(data: unknown, key = "items"): T[] {
  if (Array.isArray(data)) return data as T[];
  if (data && typeof data === "object") {
    const v = (data as Record<string, unknown>)[key] ?? (data as Record<string, unknown>).items;
    if (Array.isArray(v)) return v as T[];
  }
  return [];
}

/** "5, 6 ,7" -> ["5","6","7"] (trimmed, de-duplicated, empties dropped). */
export function splitList(s: string): string[] {
  return Array.from(new Set(s.split(",").map(x => x.trim()).filter(Boolean)));
}

export function joinList(a: readonly string[] | null | undefined): string {
  return (a ?? []).join(", ");
}

export const DEPARTMENT_SUGGESTIONS = [
  "Housekeeping", "Front Office", "Food & Beverage", "Engineering", "Concierge", "Security", "Spa", "Finance",
];
