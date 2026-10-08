"""
Deterministic text checks used by the graders (03 spec 5.4): language by
Unicode script ratio, sentence count, AED amounts, whole-token numbers and
the em dash ban. Pure functions, no database.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

# ─── Digits ───
# Arabic-Indic (U+0660) and Eastern Arabic-Indic (U+06F0) digits are folded
# to ASCII first: the prompt keeps Western digits unless the guest uses
# Arabic-Indic ones, and "٩٩٨" is still the ambulance number.
_DIGIT_FOLD = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGIT_FOLD.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_DIGIT_FOLD[ord("٫")] = "."  # Arabic decimal separator
_DIGIT_FOLD[ord("٬")] = ","  # Arabic thousands separator


def fold_digits(text: str) -> str:
    return (text or "").translate(_DIGIT_FOLD)


# ─── Language by script ───
# Script, not vocabulary: a Gulf Arabic reply and an MSA reply are both
# Arabic script, and a reply that code-switches keeps its majority script.
_SCRIPTS: dict[str, tuple[tuple[int, int], ...]] = {
    "ar": ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
    "ru": ((0x0400, 0x04FF), (0x0500, 0x052F)),
    "hi": ((0x0900, 0x097F),),
    "ur": ((0x0600, 0x06FF),),
    "zh": ((0x4E00, 0x9FFF),),
}
_LATIN_LANGS = frozenset({"en", "fr", "de", "es", "it", "pt", "tr", "tl", "id", "ms"})
MIN_SCRIPT_RATIO = 0.6


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    for lang in ("ar", "ru", "hi", "zh"):
        if any(lo <= cp <= hi for lo, hi in _SCRIPTS[lang]):
            return lang
    if ch.isalpha() and "LATIN" in unicodedata.name(ch, ""):
        return "latin"
    return None


def script_ratios(text: str) -> dict[str, float]:
    """Share of letters per script: {"ar": 0.9, "latin": 0.1}."""
    counts: dict[str, int] = {}
    total = 0
    for ch in text or "":
        if not ch.isalpha():
            continue
        s = script_of(ch)
        if s is None:
            continue
        counts[s] = counts.get(s, 0) + 1
        total += 1
    return {k: v / total for k, v in counts.items()} if total else {}


def language_matches(text: str, lang: str) -> tuple[bool, float]:
    """(ok, ratio) for an expected language code ("ar", "ar-AE", "en")."""
    base = (lang or "").lower().split("-")[0]
    ratios = script_ratios(text)
    key = "latin" if base in _LATIN_LANGS else ("ar" if base == "ur" else base)
    ratio = ratios.get(key, 0.0)
    return ratio >= MIN_SCRIPT_RATIO, round(ratio, 3)


# ─── Sentences ───
_SENTENCE_END = re.compile(r"[.!?؟。।]+(?=\s|$)|\n+")


def sentence_count(text: str) -> int:
    """Sentences with at least one letter or digit. A decimal point or a
    time like 3.30 is not a boundary (the lookahead needs a space)."""
    parts = _SENTENCE_END.split(fold_digits(text or ""))
    return sum(1 for p in parts if any(c.isalnum() for c in p))


# ─── Em dash ───
# Built from its code point so this file never contains the character itself.
EM_DASH = chr(0x2014)


def has_em_dash(text: str) -> bool:
    return EM_DASH in (text or "")


# ─── Numbers and amounts ───


def has_number_token(text: str, number: str) -> bool:
    """`number` appears as a whole token ("998" but not "19980")."""
    pattern = r"(?<![\d])" + re.escape(str(number)) + r"(?![\d])"
    return re.search(pattern, fold_digits(text or "")) is not None


_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
_CUR_BEFORE = r"(?:AED|Dhs?\.?|DH|د\.إ\.?|درهم)"
_CUR_AFTER = r"(?:AED|dirhams?|dhs?|درهم|دراهم)"
_AMOUNT_RES = (
    re.compile(_CUR_BEFORE + r"\s?" + _NUM, re.I),
    re.compile(_NUM + r"\s?" + _CUR_AFTER + r"\b", re.I),
    re.compile(_NUM + r"\s?" + _CUR_AFTER, re.I),
)


def _dec(whole: str, frac: str | None) -> Decimal | None:
    try:
        return Decimal(whole.replace(",", "") + ("." + frac if frac else ""))
    except InvalidOperation:
        return None


def aed_amounts(text: str) -> set[Decimal]:
    """Every AED amount written in `text` (AED 45, 45 AED, 45 dirhams, 45 درهم)."""
    folded = fold_digits(text or "")
    out: set[Decimal] = set()
    for rx in _AMOUNT_RES:
        for m in rx.finditer(folded):
            v = _dec(m.group(1), m.group(2))
            if v is not None:
                out.add(v.normalize() if v != v.to_integral() else v.quantize(Decimal(1)))
    return out


def all_numbers(text: str) -> set[Decimal]:
    """Every number in `text`, for the KB side of the price set (a KB entry
    may say "Breakfast is 95 per person" without a currency)."""
    out: set[Decimal] = set()
    for m in re.finditer(_NUM, fold_digits(text or "")):
        v = _dec(m.group(1), m.group(2))
        if v is not None:
            out.add(v.normalize() if v != v.to_integral() else v.quantize(Decimal(1)))
    return out
