"""
Review theme taxonomy (03 spec 4.1) and the department each theme routes to.

Department keys are Service's (service.api.REQUEST_TYPE_FOR_DEPARTMENT), so a
theme joins a request in service_requests_v1 whatever name the request's
department carries: the v2 key ("engineering") or the legacy label
("Maintenance"). The keyword lexicon is the fallback when AI is switched off
and a cross-check on the model's themes; it is deliberately small (English and
Gulf Arabic) because the model does the real work.
"""

from __future__ import annotations

import re

# ─── Themes ───

THEME_DEPARTMENT: dict[str, str] = {
    "cleanliness": "housekeeping",
    "housekeeping": "housekeeping",
    "hvac": "engineering",
    "plumbing": "engineering",
    "maintenance": "engineering",
    "wifi": "engineering",
    "noise": "front_office",
    "staff": "front_office",
    "check_in": "front_office",
    "billing": "front_office",
    "parking": "front_office",
    "breakfast": "ird",
    "ird": "ird",
    "pool": "general",
    "value": "general",
    "safety": "security",
}
THEMES: tuple[str, ...] = tuple(THEME_DEPARTMENT)

THEME_LABELS: dict[str, str] = {
    "cleanliness": "Cleanliness", "housekeeping": "Housekeeping", "hvac": "Air conditioning",
    "plumbing": "Plumbing", "maintenance": "Maintenance", "wifi": "Wi-Fi", "noise": "Noise",
    "staff": "Staff", "check_in": "Check-in", "billing": "Billing", "parking": "Parking",
    "breakfast": "Breakfast", "ird": "In-room dining", "pool": "Pool", "value": "Value", "safety": "Safety",
}

# Every name a department goes by in service_requests_v1.department,
# lowercased: the v2 keys plus the legacy staff_requests.category labels.
DEPARTMENT_ALIASES: dict[str, frozenset[str]] = {
    "housekeeping": frozenset({"housekeeping"}),
    "engineering": frozenset({"engineering", "maintenance"}),
    "ird": frozenset({"ird", "food & beverage", "f&b", "in-room dining", "room service", "kitchen"}),
    "front_office": frozenset({"front_office", "front desk", "front office", "reception"}),
    "concierge": frozenset({"concierge"}),
    "security": frozenset({"security"}),
    "general": frozenset({"general", "other"}),
}


def department_names(departments: set[str] | list[str]) -> list[str]:
    """All lowercased aliases of the given department keys (for SQL ANY)."""
    out: set[str] = set()
    for d in departments:
        out |= DEPARTMENT_ALIASES.get(d, frozenset({d.lower()}))
    return sorted(out)


# ─── Keyword lexicon (fallback analysis) ───

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cleanliness": ("dirty", "stain", "dust", "hair in", "filthy", "unclean", "not clean", "smell", "smelly",
                    "mould", "mold", "cockroach", "وسخ", "غير نظيف", "رائحة"),
    "hvac": ("air con", "aircon", "a/c", "air conditioning", "air-conditioning", "the ac", "ac was", "ac not",
             "too hot", "heating", "مكيف", "تكييف"),
    "noise": ("noise", "noisy", "loud", "construction", "thin walls", "ضوضاء", "إزعاج", "ازعاج"),
    "plumbing": ("shower", "toilet", "leak", "drain", "hot water", "water pressure", "flush", "تسريب", "حمام"),
    "wifi": ("wifi", "wi-fi", "internet", "واي فاي", "انترنت", "إنترنت"),
    "breakfast": ("breakfast", "buffet", "فطور", "إفطار", "افطار"),
    "ird": ("room service", "in-room dining", "in room dining", "خدمة الغرف"),
    "staff": ("staff", "rude", "friendly", "helpful", "receptionist", "manager", "موظف", "الموظفين"),
    "check_in": ("check-in", "check in", "checkin", "waited", "queue", "تسجيل الدخول"),
    "billing": ("bill", "charged", "overcharged", "invoice", "deposit", "refund", "فاتورة"),
    "value": ("expensive", "overpriced", "value for money", "not worth", "غالي"),
    "pool": ("pool", "مسبح"),
    "parking": ("parking", "valet", "موقف"),
    "housekeeping": ("housekeeping", "towel", "turndown", "sheets", "linen", "مناشف"),
    "maintenance": ("broken", "not working", "didn't work", "did not work", "repair", "معطل", "خربان"),
    "safety": ("unsafe", "theft", "stolen", "injury", "injured", "fire alarm", "سرقة", "خطر"),
}

_NEGATIVE_CUES = (
    "not ", "no ", "never", "dirty", "broken", "rude", "bad", "worst", "terrible", "awful", "poor",
    "disappoint", "slow", "noisy", "didn't", "did not", "wasn't", "was not", "horrible", "unacceptable",
    "overcharged", "stolen", "cold food", "لم ", "لا ", "سيء", "سيئ", "مزعج",
)

# A sentence with praise and no negative cue is positive even in a 1-star
# review ("the staff were lovely, the room was filthy" splits in two).
_POSITIVE_CUES = (
    "friendly", "helpful", "great", "excellent", "amazing", "lovely", "clean", "perfect", "wonderful",
    "comfortable", "delicious", "recommend", "رائع", "ممتاز", "نظيف", "لطيف",
)

_SENTENCE_SPLIT =re.compile(r"(?<=[.!?؟\n])\s+")


def keyword_themes(text: str, rating_norm_5: float | None) -> list[dict]:
    """[{key, department, polarity, summary_en}] from the lexicon. Polarity
    per sentence: a negative cue word makes it negative; otherwise a 4-5 star
    review reads positive and a 1-2 star one negative."""
    found: dict[str, str] = {}
    for sentence in _SENTENCE_SPLIT.split(text or ""):
        low = f" {sentence.lower()} "
        neg = any(cue in low for cue in _NEGATIVE_CUES)
        pos = not neg and any(cue in low for cue in _POSITIVE_CUES)
        for key, words in _KEYWORDS.items():
            if not any(w in low for w in words):
                continue
            if pos:
                pol = "positive"
            elif neg or (rating_norm_5 is not None and rating_norm_5 <= 2):
                pol = "negative"
            elif rating_norm_5 is not None and rating_norm_5 >= 4:
                pol = "positive"
            else:
                pol = "neutral"
            # A theme mentioned both ways counts as negative: the owner
            # needs to hear the complaint, not the average.
            if found.get(key) != "negative":
                found[key] = pol
    return [{"key": k, "department": THEME_DEPARTMENT[k], "polarity": p, "summary_en": THEME_LABELS[k]}
            for k, p in found.items()]


# ─── Room hint ───

_ROOM_RE = re.compile(
    r"(?:\broom|\brm\.?|\bsuite|غرفة|الغرفة|غرفه)\s*(?:no\.?|number|num\.?|#|رقم)?\s*(\d{2,5}[A-Za-z]?)\b",
    re.IGNORECASE,
)


def room_hint(text: str | None) -> str | None:
    """'room 504' -> '504'. Only a number after the word room/suite/غرفة:
    a bare number in a review is usually a price or a date."""
    m = _ROOM_RE.search(text or "")
    return m.group(1).upper() if m else None


def looks_arabic(text: str | None) -> bool:
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if not letters:
        return False
    arabic = sum(1 for ch in letters if "\u0600" <= ch <= "\u06ff")
    return arabic / len(letters) > 0.3
