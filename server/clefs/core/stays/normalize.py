"""Normalisation of the two keys that link an OTA claim to a PMS stay (02 spec
2.1): the OTA reservation id and the guest name.

Pure functions, no database. Both keys are stored on `reservations`
(`ota_res_id_norm`, `guest_name_norm`) at write time, so matching compares
normalised values only and a statement line is normalised the same way.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz.distance import JaroWinkler

# ─── OTA reservation ids ───

# Booking and Agoda ids are plain digits; exports decorate them ("BDC-",
# "Booking# ", "AG 12 345 678"), so everything but the digits is dropped.
DIGIT_ONLY_OTAS = frozenset({"BOOKING", "AGODA"})
# Channel-manager and PMS prefixes seen in exports, stripped before comparing.
_ID_PREFIXES = ("BOOKINGCOM", "BOOKING", "BDC", "AGODA", "AGD", "EXPEDIA", "EXP", "ITINERARY", "ITIN", "CONF", "RES")


def normalize_ota_res_id(ota_code: str | None, raw: str | None) -> str | None:
    """The comparable form of an OTA reservation id, or None when nothing is left.

    Booking and Agoda: digits only. Expedia (itinerary or confirmation number)
    and anything else: upper-case letters and digits with known prefixes
    stripped, so "EXP-7214 5530" and "72145530" meet."""
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if not text:
        return None
    if (ota_code or "").upper() in DIGIT_ONLY_OTAS:
        digits = re.sub(r"\D", "", text)
        return digits.lstrip("0") or None
    compact = re.sub(r"[^A-Z0-9]", "", text)
    for prefix in _ID_PREFIXES:
        # Only a prefix followed by a digit is stripped: "RESORT7" stays whole.
        if compact.startswith(prefix) and compact[len(prefix) : len(prefix) + 1].isdigit():
            compact = compact[len(prefix) :]
            break
    return compact or None


# ─── Guest names ───

# Titles and honorifics PMS and OTA exports put in front of names.
_TITLES = frozenset(
    {"mr", "mrs", "ms", "miss", "mstr", "master", "dr", "prof", "sir", "madam", "mme", "sheikh", "shaikh",
     "sheikha", "shk", "hh", "he", "eng", "capt"}
)
# Particles that belong to the next token ("Al Maktoum" == "Al-Maktoum" == "Almaktoum").
_PARTICLES = frozenset({"al", "el", "bin", "ibn", "bint", "abu", "abd", "van", "von", "de", "da", "di", "du", "le",
                        "la", "del", "der", "den", "dos", "das", "mc", "mac", "st"})

# Basic Arabic letters to Latin (one common romanisation). Names written in
# Arabic on one side and Latin on the other then land close enough for the
# fuzzy score to send them to review instead of UNMATCHED.
_ARABIC = {
    "ا": "a", "أ": "a", "إ": "i", "آ": "a", "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh",
    "د": "d", "ذ": "dh", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z",
    "ع": "a", "غ": "gh", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h", "و": "w",
    "ي": "y", "ى": "a", "ة": "a", "ء": "", "ؤ": "w", "ئ": "y", "ـ": "",
}


def transliterate(text: str) -> str:
    """Latin, lower-case, no diacritics ("Zoë Müller" -> "zoe muller")."""
    mapped = "".join(_ARABIC.get(ch, ch) for ch in text)
    decomposed = unicodedata.normalize("NFKD", mapped)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def name_tokens(name: str | None) -> list[str]:
    """Tokens of a guest name: transliterated and folded, titles dropped,
    particles joined to the next token. "SURNAME/GIVEN" and "SURNAME, Given"
    need no special case: callers sort the tokens."""
    if not name:
        return []
    text = transliterate(str(name))
    raw = [t for t in re.split(r"[^a-z0-9]+", text) if t]
    out: list[str] = []
    pending = ""
    for tok in raw:
        # Titles lead ("Mr Jane") or trail ("DOE/JANE MS", airline style).
        if tok in _TITLES and not pending:
            continue
        if tok in _PARTICLES:
            pending += tok
            continue
        out.append(pending + tok)
        pending = ""
    if pending:
        out.append(pending)
    if not out:  # a name made only of titles keeps them
        out = [t for t in raw if t not in _PARTICLES] or raw
    return out


def normalize_name(name: str | None) -> str | None:
    """The stored `guest_name_norm`: sorted tokens joined by one space."""
    tokens = sorted(name_tokens(name))
    return " ".join(tokens) or None


def name_similarity(a: str | None, b: str | None) -> float:
    """Token-sort Jaro-Winkler in [0, 1] (02 spec 2.1; reused by Inquiry Guard).

    Accepts raw or already-normalised names. A name whose tokens are all in
    the other (a missing middle name, 2 tokens or more) scores at least 0.95."""
    ta, tb = sorted(name_tokens(a)), sorted(name_tokens(b))
    if not ta or not tb:
        return 0.0
    score = float(JaroWinkler.normalized_similarity(" ".join(ta), " ".join(tb)))
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) >= 2 and set(short) <= set(long_):
        score = max(score, 0.95)
    return round(score, 4)
