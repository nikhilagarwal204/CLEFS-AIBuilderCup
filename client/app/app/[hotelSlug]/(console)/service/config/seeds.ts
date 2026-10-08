/* Global SLA and authority seeds, mirrored from service/constants.py for
   display while a hotel has no overrides (or the config route is not live).
   The server stays authoritative: these never decide a due time. */

import type { CompensationRule, SlaPolicy } from "../_components/configApi";

const pol = (scope_type: SlaPolicy["scope_type"], scope_key: string, ack_min: number, resolve_min: number, due_local: string | null = null): SlaPolicy =>
  ({ scope_type, scope_key, ack_min, resolve_min, due_local, active: true });

export const SEED_SLA: SlaPolicy[] = [
  pol("request_type", "towels", 3, 15),
  pol("request_type", "linen", 3, 15),
  pol("request_type", "amenities", 3, 15),
  pol("request_type", "water", 3, 10),
  pol("request_type", "pillows", 5, 20),
  pol("request_type", "extra_bed", 5, 30),
  pol("request_type", "ird", 2, 30),
  pol("request_type", "breakfast", 2, 25),
  pol("request_type", "drinks", 2, 15),
  pol("request_type", "luggage", 2, 10),
  pol("request_type", "ac", 5, 60),
  pol("request_type", "plumbing", 5, 30),
  pol("request_type", "make_up_room", 5, 45),
  pol("request_type", "turndown", 5, 45, "21:00"),
  pol("department", "housekeeping", 5, 30),
  pol("department", "ird", 2, 30),
  pol("department", "engineering", 5, 60),
  pol("department", "front_office", 3, 15),
  pol("department", "concierge", 5, 30),
  pol("department", "security", 1, 15),
  pol("department", "general", 5, 30),
  pol("severity", "L1", 2, 15),
  pol("default", "", 5, 30),
];

export const DEFAULT_LADDER = [
  { after_min: 0, to: "assignee" },
  { after_min: 3, to: "supervisor" },
  { after_min: 5, to: "duty_manager" },
  { after_min: 10, to: "gm" },
];

export const GESTURE_KINDS = ["amenity", "fnb_credit", "late_checkout", "room_night", "upgrade", "discount", "other"] as const;

/** Authority seeds in minor units of the hotel currency (AED 100 = 10000). */
export function seedCompensation(currency: string): CompensationRule[] {
  return [
    { role_key: "staff", max_minor: 10_000, currency, allowed_kinds: ["amenity", "fnb_credit"] },
    { role_key: "supervisor", max_minor: 30_000, currency, allowed_kinds: ["amenity", "fnb_credit", "late_checkout"] },
    { role_key: "duty_manager", max_minor: 100_000, currency,
      allowed_kinds: ["amenity", "fnb_credit", "late_checkout", "room_night", "upgrade", "discount"] },
    { role_key: "gm", max_minor: null, currency, allowed_kinds: [...GESTURE_KINDS] },
  ];
}

export const ROLE_LABELS: Record<string, string> = {
  staff: "Staff", supervisor: "Supervisor", duty_manager: "Duty manager", gm: "General manager",
  assignee: "Assignee",
};

export const SCOPE_LABELS: Record<SlaPolicy["scope_type"], string> = {
  request_type: "Request type", department_severity: "Department + level", department: "Department",
  severity: "Level", default: "Hotel default",
};
