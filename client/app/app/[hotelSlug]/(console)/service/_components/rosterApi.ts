/* ═══════════════════════════════════════════════════════════════════════
   Roster calls (03 spec 1.6, 1.7: GET/PUT roster?week, POST
   roster/validate). Shapes follow migration 0102 (svc_shift_templates,
   svc_shift_assignments) and labour.py (Guidance, not legal advice).
   ═══════════════════════════════════════════════════════════════════════ */

import { api, hotelPath } from "../../../../../lib/api";

export type ShiftRole = "attendant" | "supervisor" | "duty_manager";

export interface ShiftTemplate {
  id: number;
  key: string;
  name: string;
  department_key?: string | null;
  start_time: string;  // "07:00" or "07:00:00"
  end_time: string;
  crosses_midnight?: boolean;
  break_min?: number;
  active?: boolean;
}

export interface LabourFlag {
  staff_member_id?: number;
  business_date?: string;
  /** labour.py sends `code`; `rule` is kept for older payloads. */
  rule?: string;
  code?: string;
  /** "blocked" needs an override reason to save (midday outdoor ban). */
  severity?: "warning" | "blocked";
  level?: "warning" | "blocked";
  message?: string;
}

export interface Assignment {
  id?: number;
  staff_member_id: number;
  template_id?: number | null;
  business_date: string;
  start_at: string;  // ISO instant
  end_at: string;
  department_key?: string | null;
  floors: string[];
  shift_role: ShiftRole;
  status?: "planned" | "cancelled";
  labour_flags?: LabourFlag[];
  override_reason?: string | null;
}

export interface RosterWeek {
  week: string;
  templates: ShiftTemplate[];
  assignments: Assignment[];
  flags?: LabourFlag[];
  caption?: string;
}

export const CAPTION = "Guidance, not legal advice";

export function flagLevel(f: LabourFlag): "warning" | "blocked" {
  return (f.severity ?? f.level) === "blocked" ? "blocked" : "warning";
}

export const RULE_TEXT: Record<string, string> = {
  weekly_hours: "Over the weekly hours limit",
  daily_hours: "Over the daily hours limit",
  ramadan_hours: "Over Ramadan working hours",
  consecutive_days: "Too many days in a row without rest",
  midday_ban: "Outdoor work in the summer midday break",
};

export function flagText(f: LabourFlag): string {
  const key = f.code ?? f.rule ?? "";
  return f.message || RULE_TEXT[key] || key;
}

export async function loadRoster(slug: string, week: string): Promise<RosterWeek> {
  const r = await api.get<Partial<RosterWeek>>(hotelPath(slug, "/service/roster"), { week });
  return {
    week: r.week ?? (r as { week_start?: string }).week_start ?? week,
    templates: Array.isArray(r.templates) ? r.templates : [],
    assignments: Array.isArray(r.assignments) ? r.assignments : [],
    flags: Array.isArray(r.flags) ? r.flags : [],
    caption: r.caption ?? CAPTION,
  };
}

/** The hotel's shift templates (names and hours for the owner view's shift
    labels). An empty list when the route is closed or absent. */
export async function loadShiftTemplates(slug: string): Promise<ShiftTemplate[]> {
  try {
    const r = await api.get<unknown>(hotelPath(slug, "/service/shift-templates"), undefined, { silent401: true });
    return Array.isArray(r) ? (r as ShiftTemplate[]) : [];
  } catch {
    return [];
  }
}

export function validateRoster(slug: string, week: string, assignments: Assignment[]) {
  return api.post<{ flags?: LabourFlag[] }>(hotelPath(slug, "/service/roster/validate"), { week, assignments });
}

export function saveRoster(slug: string, week: string, assignments: Assignment[]) {
  return api.put<Partial<RosterWeek>>(hotelPath(slug, "/service/roster"), { week, assignments });
}
