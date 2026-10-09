"""
Service Control defaults: departments, request types, SLA seeds, the
escalation ladder, the recovery authority matrix and the UAE emergency
numbers (03 spec 1.1, 1.3, 1.5; Canon UAE defaults).

Global defaults live here rather than in NULL-hotel database rows: a default
revised after a review reaches every hotel that never overrode it, and the
rules are testable without a database. Hotels override any of it through the
svc_* config tables (config.py merges the two).
"""

from __future__ import annotations

from datetime import time

# ─── States (03 spec 1.2) ───

STATES = ("scheduled", "new", "assigned", "acknowledged", "in_progress", "completed", "cancelled", "reopened")
OPEN_STATES = ("scheduled", "new", "assigned", "acknowledged", "in_progress", "reopened")
TERMINAL_STATES = ("completed", "cancelled")
# Not yet acknowledged by a person: the ack ladder runs on these.
UNACKED_STATES = ("new", "assigned", "reopened")

# The legacy status every v2 state maps to, written in the same UPDATE, so
# the old staff page (which filters status !== "completed") never shows
# cancelled work as active.
LEGACY_STATUS = {
    "scheduled": "new",
    "new": "new",
    "assigned": "progress",
    "acknowledged": "progress",
    "in_progress": "progress",
    "reopened": "progress",
    "completed": "completed",
    "cancelled": "completed",
}

SEVERITIES = ("L1", "L2", "L3")
PRIORITY_RANK = {"high": 2, "medium": 1, "low": 0}

# ─── Sources ───
# Guest channels are rate limited per room in the database (03 spec 1.3
# step 3); staff channels are not.
GUEST_SOURCES = frozenset({"web", "voice", "sip", "whatsapp", "text", "legacy", "whatsapp_journey"})
STAFF_SOURCES = frozenset({"console", "device", "phone_desk", "staff", "inquiries", "j7_feedback", "journey_gate"})

# ─── Departments ───
# key -> name, Arabic name, the legacy category written to staff_requests.category
# (what staff_members.department and the old dashboards use), colour.

DEPARTMENTS: dict[str, dict] = {
    "housekeeping": {"name": "Housekeeping", "name_ar": "التدبير المنزلي", "legacy_category": "Housekeeping", "color": "#3b82f6", "sort": 10},
    "ird": {"name": "In-Room Dining", "name_ar": "خدمة الطعام في الغرف", "legacy_category": "Food & Beverage", "color": "#f59e0b", "sort": 20},
    "engineering": {"name": "Engineering", "name_ar": "الهندسة والصيانة", "legacy_category": "Maintenance", "color": "#ef4444", "sort": 30},
    "front_office": {"name": "Front Office", "name_ar": "مكتب الاستقبال", "legacy_category": "Front Desk", "color": "#8b5cf6", "sort": 40},
    "concierge": {"name": "Concierge", "name_ar": "الكونسيرج", "legacy_category": "Concierge", "color": "#10b981", "sort": 50},
    "security": {"name": "Security", "name_ar": "الأمن", "legacy_category": "Security", "color": "#111827", "sort": 60},
    "general": {"name": "General", "name_ar": "عام", "legacy_category": "General", "color": "#6b7280", "sort": 70},
}

# Free-text department names (staff_members.department, device scopes, the
# model's category) normalised to a key. Lowercased, "&" and "-" folded.
DEPARTMENT_ALIASES: dict[str, str] = {
    "housekeeping": "housekeeping", "hk": "housekeeping", "rooms division": "housekeeping", "laundry": "housekeeping",
    "ird": "ird", "in room dining": "ird", "in-room dining": "ird", "food and beverage": "ird", "food & beverage": "ird",
    "f&b": "ird", "fnb": "ird", "f and b": "ird", "room service": "ird", "kitchen": "ird", "restaurant": "ird",
    "engineering": "engineering", "maintenance": "engineering", "technical": "engineering",
    "front office": "front_office", "front desk": "front_office", "front_office": "front_office", "reception": "front_office",
    "guest relations": "front_office",
    "concierge": "concierge", "bell desk": "concierge", "spa": "concierge",
    "security": "security",
    "general": "general", "other": "general",
}

# Departments routed by zone: the floor match comes first (03 spec 1.4 step 2.1).
ZONED_DEPARTMENTS = frozenset({"housekeeping", "engineering"})


def department_key(value: str | None) -> str | None:
    """A free-text department (legacy category, device scope, model output)
    as a department key, or None when it is not recognisable."""
    if not value:
        return None
    v = " ".join(str(value).strip().lower().replace("_", " ").split())
    if v.replace(" ", "_") in DEPARTMENTS:
        return v.replace(" ", "_")
    return DEPARTMENT_ALIASES.get(v)


def legacy_category(key: str | None) -> str:
    return DEPARTMENTS.get(key or "", DEPARTMENTS["general"])["legacy_category"]


# ─── Request types ───
# key -> label, Arabic label, department, default severity, amenity flag, the
# legacy `type` written for old dashboards.

REQUEST_TYPES: dict[str, dict] = {
    "towels": {"label_en": "Towels", "label_ar": "مناشف", "department_key": "housekeeping", "default_severity": "L3", "is_amenity": True, "legacy_type": "Towel"},
    "linen": {"label_en": "Linen", "label_ar": "بياضات", "department_key": "housekeeping", "default_severity": "L3", "is_amenity": True, "legacy_type": "Clean Sheets"},
    "amenities": {"label_en": "Amenities", "label_ar": "مستلزمات", "department_key": "housekeeping", "default_severity": "L3", "is_amenity": True, "legacy_type": "Cleaning"},
    "water": {"label_en": "Drinking water", "label_ar": "مياه الشرب", "department_key": "housekeeping", "default_severity": "L3", "is_amenity": True, "legacy_type": "Water"},
    "pillows": {"label_en": "Pillows", "label_ar": "وسائد", "department_key": "housekeeping", "default_severity": "L3", "is_amenity": True, "legacy_type": "Cleaning"},
    "extra_bed": {"label_en": "Extra bed", "label_ar": "سرير إضافي", "department_key": "housekeeping", "default_severity": "L3", "is_amenity": True, "legacy_type": "Cleaning"},
    "make_up_room": {"label_en": "Make up room", "label_ar": "تنظيف الغرفة", "department_key": "housekeeping", "default_severity": "L3", "is_amenity": False, "legacy_type": "Cleaning"},
    "turndown": {"label_en": "Turndown", "label_ar": "تجهيز المساء", "department_key": "housekeeping", "default_severity": "L3", "is_amenity": False, "legacy_type": "Cleaning"},
    "ird": {"label_en": "In-room dining", "label_ar": "طعام في الغرفة", "department_key": "ird", "default_severity": "L3", "is_amenity": False, "legacy_type": "In-Room Dining"},
    "breakfast": {"label_en": "Breakfast", "label_ar": "إفطار", "department_key": "ird", "default_severity": "L3", "is_amenity": False, "legacy_type": "In-Room Dining"},
    "drinks": {"label_en": "Drinks", "label_ar": "مشروبات", "department_key": "ird", "default_severity": "L3", "is_amenity": False, "legacy_type": "Food"},
    "luggage": {"label_en": "Luggage", "label_ar": "الأمتعة", "department_key": "concierge", "default_severity": "L3", "is_amenity": False, "legacy_type": "Luggage Assistance"},
    "concierge": {"label_en": "Concierge", "label_ar": "الكونسيرج", "department_key": "concierge", "default_severity": "L3", "is_amenity": False, "legacy_type": "Other"},
    "spa": {"label_en": "Spa booking", "label_ar": "حجز السبا", "department_key": "concierge", "default_severity": "L3", "is_amenity": False, "legacy_type": "Spa Booking"},
    "ac": {"label_en": "Air conditioning", "label_ar": "التكييف", "department_key": "engineering", "default_severity": "L2", "is_amenity": False, "legacy_type": "Maintenance"},
    "plumbing": {"label_en": "Plumbing / water supply", "label_ar": "السباكة", "department_key": "engineering", "default_severity": "L2", "is_amenity": False, "legacy_type": "Water Supply"},
    "electrical": {"label_en": "Electrical", "label_ar": "الكهرباء", "department_key": "engineering", "default_severity": "L2", "is_amenity": False, "legacy_type": "Maintenance"},
    "maintenance": {"label_en": "Maintenance", "label_ar": "صيانة", "department_key": "engineering", "default_severity": "L2", "is_amenity": False, "legacy_type": "Maintenance"},
    "wake_up": {"label_en": "Wake-up call", "label_ar": "مكالمة إيقاظ", "department_key": "front_office", "default_severity": "L3", "is_amenity": False, "legacy_type": "Wake-Up Call"},
    "billing": {"label_en": "Billing", "label_ar": "الفواتير", "department_key": "front_office", "default_severity": "L3", "is_amenity": False, "legacy_type": "Billing"},
    "checkout": {"label_en": "Checkout", "label_ar": "المغادرة", "department_key": "front_office", "default_severity": "L3", "is_amenity": False, "legacy_type": "Checkout"},
    "discount": {"label_en": "Discount", "label_ar": "خصم", "department_key": "front_office", "default_severity": "L3", "is_amenity": False, "legacy_type": "Discount"},
    "front_desk": {"label_en": "Front desk", "label_ar": "الاستقبال", "department_key": "front_office", "default_severity": "L3", "is_amenity": False, "legacy_type": "Front Desk"},
    "direct_conversation": {"label_en": "Talk to a person", "label_ar": "التحدث إلى موظف", "department_key": "front_office", "default_severity": "L2", "is_amenity": False, "legacy_type": "Direct Conversation"},
    # Seeded for J7 low ratings and journey feedback (04 spec 7.2).
    "guest_feedback": {"label_en": "Guest feedback", "label_ar": "ملاحظات الضيف", "department_key": "front_office", "default_severity": "L3", "is_amenity": False, "legacy_type": "Direct Conversation"},
    "complaint": {"label_en": "Complaint", "label_ar": "شكوى", "department_key": "front_office", "default_severity": "L2", "is_amenity": False, "legacy_type": "Direct Conversation"},
    "security": {"label_en": "Security", "label_ar": "الأمن", "department_key": "security", "default_severity": "L2", "is_amenity": False, "legacy_type": "Other"},
    "emergency": {"label_en": "Emergency", "label_ar": "طوارئ", "department_key": "security", "default_severity": "L1", "is_amenity": False, "legacy_type": "Direct Conversation"},
    "other": {"label_en": "Other", "label_ar": "أخرى", "department_key": "general", "default_severity": "L3", "is_amenity": False, "legacy_type": "Other"},
}

# The legacy `type` strings the dashboards, /pending and the concierge send.
LEGACY_TYPE_TO_KEY: dict[str, str] = {
    "towel": "towels",
    "towels": "towels",
    "clean sheets": "linen",
    "cleaning": "make_up_room",
    "in-room dining": "ird",
    "food": "ird",
    "water supply": "plumbing",
    "water": "water",
    "wake-up call": "wake_up",
    "maintenance": "maintenance",
    "luggage assistance": "luggage",
    "spa booking": "spa",
    "billing": "billing",
    "direct conversation": "direct_conversation",
    "checkout": "checkout",
    "discount": "discount",
    "front desk": "front_desk",
    "other": "other",
    "service request": "other",
}

# Keyword refinement inside a broad type (a "Maintenance" request about the
# AC is an AC request: its own SLA and the heat rule). Lowercased substrings.
TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ac", ("air con", "aircon", "air-con", "air condition", "a/c", " ac ", "ac not", "ac is", "hvac", "too hot",
            "cooling", "thermostat", "مكيف", "التكييف", "كونديشن")),
    ("plumbing", ("leak", "toilet", "flush", "drain", "shower", "no water", "hot water", "tap", "sink")),
    ("electrical", ("no power", "no electricity", "light", "socket", "power cut", "tripped")),
    ("towels", ("towel", "منشفة", "مناشف")),
    ("pillows", ("pillow", "وسادة")),
    ("extra_bed", ("extra bed", "rollaway", "baby cot", "crib")),
    ("turndown", ("turndown", "turn down")),
    ("breakfast", ("breakfast",)),
    ("luggage", ("luggage", "bags", "suitcase")),
)

AC_TYPES = frozenset({"ac"})

# ─── Proof before closure (05 spec, decision 8) ───
# Types with physical work in the guest's room: "Mark as Done" uploads a
# photo and Gemini checks it against the job. Information, a desk call or a
# wake-up call need none. minibar and laundry are hotel-defined types in the
# demo cast; a hotel can switch any type on or off (svc_request_types.needs_proof).
PROOF_TYPES = frozenset({
    "towels", "linen", "pillows", "extra_bed", "amenities", "water", "make_up_room", "turndown",
    "maintenance", "ac", "plumbing", "electrical", "minibar", "laundry",
})

# ─── ETA (05 spec, decision 6) ───
# Minutes = base for the request type + 3 per open job ahead in the holder's
# queue + 1 per floor between the room and the holder's floors, capped at 45.
ETA_BASE_MIN: dict[str, int] = {
    "towels": 5, "water": 5, "linen": 5,
    "amenities": 7, "pillows": 7, "extra_bed": 7, "minibar": 7, "laundry": 7,
    "make_up_room": 20, "turndown": 20,
    "maintenance": 15, "ac": 15, "plumbing": 15, "electrical": 15,
    "ird": 25, "breakfast": 25, "drinks": 25,
}
ETA_DEFAULT_MIN = 10
ETA_PER_JOB_MIN = 3
ETA_PER_FLOOR_MIN = 1
ETA_CAP_MIN = 45

# ─── SLA seeds (03 spec 1.3, minutes: ack / resolve) ───

SLA_REQUEST_TYPE: dict[str, tuple[int, int]] = {
    "towels": (3, 15),
    "linen": (3, 15),
    "amenities": (3, 15),
    "water": (3, 10),
    "pillows": (5, 20),
    "extra_bed": (5, 30),
    "ird": (2, 30),
    "breakfast": (2, 25),
    "drinks": (2, 15),
    "luggage": (2, 10),
    "ac": (5, 60),
    "plumbing": (5, 30),
    "make_up_room": (5, 45),
    "turndown": (5, 45),  # before 21:00 local the due time is 21:00 (window)
}
# Window-based types: resolve due at this local time when requested before it.
SLA_DUE_LOCAL: dict[str, time] = {"turndown": time(21, 0)}
SLA_DEPARTMENT: dict[str, tuple[int, int]] = {
    "housekeeping": (5, 30),
    "ird": (2, 30),
    "engineering": (5, 60),
    "front_office": (3, 15),
    "concierge": (5, 30),
    "security": (1, 15),
    "general": (5, 30),
}
# L1 means respond within 15 minutes and notify the GM at creation.
SLA_SEVERITY: dict[str, tuple[int, int]] = {"L1": (2, 15)}
SLA_DEFAULT: tuple[int, int] = (5, 30)

# The ack ladder (Plan: 0/3/5/10). Level 0 is local (the device chime repeats);
# level 1 the supervisor (device alert, WhatsApp without a live session);
# level 2 the duty manager (WhatsApp, SMS fallback); level 3 the GM
# (WhatsApp, plus the SLA breach signal).
DEFAULT_LADDER: tuple[dict, ...] = (
    {"after_min": 0, "to": "assignee"},
    {"after_min": 3, "to": "supervisor"},
    {"after_min": 5, "to": "duty_manager"},
    {"after_min": 10, "to": "gm"},
)
LADDER_ROLES = ("assignee", "supervisor", "duty_manager", "gm")
# What a request row calls the highest ladder step it reached (05 spec 3:
# escalation_level is supervisor | duty_manager | gm | null, never L1-L3,
# which is the severity scale).
ESCALATION_LABEL = {1: "supervisor", 2: "duty_manager", 3: "gm"}
LADDER_CHANNELS: dict[str, tuple[str, ...]] = {
    "assignee": (),
    "supervisor": ("whatsapp",),
    "duty_manager": ("whatsapp", "sms"),
    "gm": ("whatsapp", "sms", "email"),
}

# ─── Priority keywords (VIP tiers that lift a request to at least medium) ───
VIP_TIERS = frozenset({"globalist", "explorist", "platinum", "titanium", "ambassador", "diamond", "gold", "vip", "black"})
VIP_TAGS = frozenset({"vip", "returning", "repeat", "loyal", "celebrity", "owner_guest"})

# ─── Shift templates (03 spec 1.1 "0004") ───

SHIFT_TEMPLATES: tuple[dict, ...] = (
    {"key": "morning", "name": "Morning", "department_key": None, "start_time": time(7), "end_time": time(15), "break_min": 30},
    {"key": "evening", "name": "Evening", "department_key": None, "start_time": time(15), "end_time": time(23), "break_min": 30},
    {"key": "night", "name": "Night", "department_key": None, "start_time": time(23), "end_time": time(7), "break_min": 30},
    {"key": "hk_day", "name": "Housekeeping day", "department_key": "housekeeping", "start_time": time(8), "end_time": time(17), "break_min": 60},
    {"key": "hk_eve", "name": "Housekeeping evening", "department_key": "housekeeping", "start_time": time(15), "end_time": time(0), "break_min": 30},
)
SHIFT_ROLES = ("attendant", "supervisor", "duty_manager")

# ─── Service recovery (03 spec 1.5) ───

LEARN_STEPS = ("listen", "empathize", "apologize", "resolve", "notify")
GESTURE_KINDS = ("amenity", "fnb_credit", "late_checkout", "room_night", "upgrade", "discount", "other")
AUTHORITY_ROLES = ("staff", "supervisor", "duty_manager", "gm")
NEXT_ROLE_UP = {"staff": "supervisor", "supervisor": "duty_manager", "duty_manager": "gm", "gm": None}
# Default authority in major units of the hotel currency (AED for the UAE
# seeds): staff 100 {amenity, fnb_credit}; supervisor 300 plus late
# checkout; duty manager adds room nights and upgrades; the GM is unlimited.
DEFAULT_AUTHORITY: dict[str, dict] = {
    "staff": {"max_major": 100, "allowed_kinds": ["amenity", "fnb_credit"]},
    "supervisor": {"max_major": 300, "allowed_kinds": ["amenity", "fnb_credit", "late_checkout"]},
    "duty_manager": {"max_major": 1000, "allowed_kinds": ["amenity", "fnb_credit", "late_checkout", "room_night", "upgrade", "discount"]},
    "gm": {"max_major": None, "allowed_kinds": list(GESTURE_KINDS)},
}
# Console roles as authority roles (a shift role on today's roster can lift a
# device user higher: a supervisor shift is "supervisor").
MEMBERSHIP_AUTHORITY = {"owner": "gm", "gm": "gm", "dept_head": "supervisor", "front_desk": "staff", "staff": "staff"}
# Who approves above-authority gestures in the console (actions.owner_role).
APPROVER_OWNER_ROLE = {"supervisor": "dept_head", "duty_manager": "gm", "gm": "gm"}

COMPLAINT_CATEGORIES: dict[str, dict] = {
    "cleanliness": {"label_en": "Cleanliness", "label_ar": "النظافة", "department_key": "housekeeping"},
    "noise": {"label_en": "Noise", "label_ar": "الضوضاء", "department_key": "front_office"},
    "maintenance": {"label_en": "Something broken", "label_ar": "عطل", "department_key": "engineering"},
    "food": {"label_en": "Food and drink", "label_ar": "الطعام والشراب", "department_key": "ird"},
    "staff": {"label_en": "Staff behaviour", "label_ar": "سلوك الموظفين", "department_key": "front_office"},
    "billing": {"label_en": "Billing", "label_ar": "الفواتير", "department_key": "front_office"},
    "wait": {"label_en": "Waiting too long", "label_ar": "الانتظار الطويل", "department_key": "front_office"},
    "other": {"label_en": "Other", "label_ar": "أخرى", "department_key": "front_office"},
}

# ─── UAE emergency numbers (Canon UAE defaults) ───
# Canon lists these six; the 03 spec's extra 992 is left out because the
# Canon wins where they differ. Confirm per emirate before launch.
UAE_EMERGENCY_NUMBERS: tuple[dict, ...] = (
    {"number": "999", "label": "Police", "label_ar": "الشرطة"},
    {"number": "998", "label": "Ambulance", "label_ar": "الإسعاف"},
    {"number": "997", "label": "Civil Defence (fire)", "label_ar": "الدفاع المدني"},
    {"number": "996", "label": "Coast Guard", "label_ar": "خفر السواحل"},
    {"number": "995", "label": "Search and Rescue", "label_ar": "البحث والإنقاذ"},
    {"number": "991", "label": "Electricity and water emergency", "label_ar": "طوارئ الكهرباء والماء"},
)
# The numbers the EmergencyCard leads with for each emergency kind.
EMERGENCY_PRIMARY: dict[str, tuple[str, ...]] = {
    "medical": ("998", "999"),
    "fire": ("997", "999"),
    "sparks_flooding": ("997", "991"),
    "violence": ("999",),
    "missing_child": ("999",),
    "death": ("999", "998"),
    "lift_entrapment": ("997",),
    "drugs": ("999",),
}

# ─── Guest tracker ───
TRACKER_STATES = ("received", "assigned", "on_the_way", "done")
TRACKER_STATE = {
    "scheduled": "received",
    "new": "received",
    "reopened": "received",
    "assigned": "assigned",
    # acknowledged = a person has it; "on my way" is the start action
    "acknowledged": "assigned",
    "in_progress": "on_the_way",
    "completed": "done",
    "cancelled": "done",
}
TRACKER_TEMPLATE = "request_status_v1"
