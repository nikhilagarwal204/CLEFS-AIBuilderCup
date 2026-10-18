"""
Engine-independent parsing (04 spec 5.5 steps 1, 2, 4, 5): list items,
name normalisation, entity resolution and cited-domain classes.

Every engine parser turns its raw body into a `Parsed` (answer text plus
citations), then `resolve` finds which hotels the answer names, in order.
Bump PARSER_VERSION whenever the output on the golden fixtures changes: the
radar starts a new baseline_epoch on a new parser version (5.12), so rates
never shift silently.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlparse

from rapidfuzz import fuzz

PARSER_VERSION = "2026.09.1"
MATCH_RATIO = 90  # token-set ratio >= 0.9 (5.5)

# ─── Shapes ───


@dataclass
class Citation:
    url: str
    title: str | None = None
    start_index: int | None = None
    end_index: int | None = None
    kind: str = "citation"  # citation (inline, url_citation) | source (searched but not cited inline)

    def as_dict(self) -> dict:
        return {"url": self.url, "title": self.title, "start_index": self.start_index,
                "end_index": self.end_index, "kind": self.kind, "domain": domain_of(self.url)}


@dataclass
class Parsed:
    answer_text: str
    citations: list[Citation] = field(default_factory=list)
    model_id: str | None = None
    searched: bool | None = None
    empty: bool = False


@dataclass
class Entity:
    """One hotel an answer names. `key` is 'self', a comp_id, or 'other'."""

    key: str
    name: str
    position: int  # 1-based order among hotel entities in the answer
    via: str  # name | domain | place_id | ai

    def as_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "position": self.position, "via": self.via}


# ─── Names ───

_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_ALEF = re.compile(r"[آأإٱ]")
_STOP = re.compile(r"\b(hotels?|resort|and|the|by|&|\(demo\)|demo|فندق|منتجع)\b", re.I)
_NON_WORD = re.compile(r"[^\w\s]", re.U)


def normalise(name: str | None) -> str:
    """lowercase, no diacritics or tashkeel, alef and taa marbuta unified,
    "hotel" and "(DEMO)" stripped, punctuation to spaces."""
    s = (name or "").lower().replace("(demo)", " ")
    s = _TASHKEEL.sub("", s)
    s = _ALEF.sub("ا", s).replace("ة", "ه").replace("ى", "ي")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = _NON_WORD.sub(" ", s)
    s = _STOP.sub(" ", s)
    return " ".join(s.split())


def same_name(a: str | None, b: str | None) -> bool:
    na, nb = normalise(a), normalise(b)
    return bool(na and nb) and fuzz.token_set_ratio(na, nb) >= MATCH_RATIO


# ─── Domains ───

_DOMAIN_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ota", ("booking.com", "agoda.", "expedia.", "hotels.com", "trip.com", "trivago.", "kayak.", "priceline.",
             "makemytrip.", "almosafer.", "rehlat.", "wego.", "holidayme.", "hrs.")),
    ("review", ("tripadvisor.", "yelp.", "trustpilot.", "holidaycheck.", "zoover.")),
    ("google", ("google.", "g.page", "goo.gl", "youtube.com")),
    ("ugc", ("reddit.com", "quora.com", "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com",
             "pinterest.", "medium.com", "blogspot.", "wordpress.com")),
    ("wiki", ("wikipedia.org", "wikivoyage.org", "wikitravel.org")),
)


def domain_of(url: str | None) -> str:
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def classify_domain(domain: str, *, official_hosts: set[str] | frozenset[str] = frozenset()) -> str:
    """ota | review | official | google | ugc | wiki | press_other (5.5).
    `official_hosts` are the hotel's (and, for comp wins, the comps') own
    sites; they win over every other class."""
    d = (domain or "").lower().removeprefix("www.")
    if not d:
        return "press_other"
    for h in official_hosts:
        h = (h or "").lower().removeprefix("www.")
        if h and (d == h or d.endswith("." + h)):
            return "official"
    for cls, needles in _DOMAIN_CLASSES:
        if any(n in d if n.endswith(".") else (d == n or d.endswith("." + n)) for n in needles):
            return cls
    return "press_other"


# ─── Step 1: list items ───

_ITEM = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•]|#{2,4})\s+(.+)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEAD_SPLIT = re.compile(r"\s(?:-|\u2013|\u2014|:|\|)\s|[:(,.،]")
_HOTELISH = re.compile(r"\b(hotel|resort|inn|suites?|residences?|palace|lodge|hostel|فندق|منتجع)\b", re.I)


@dataclass
class Item:
    text: str
    start: int
    end: int
    head: str  # the hotel-name part of the item (bold text, link text or first clause)
    listed: bool  # a numbered or bulleted item (vs a paragraph)


def split_items(answer: str) -> list[Item]:
    """Numbered/bulleted lines (and markdown headings) become items; with
    none, each paragraph is one. Offsets index into `answer`, so inline
    citations (start_index) can be mapped to the item they support."""
    items: list[Item] = []
    pos = 0
    for line in (answer or "").splitlines(keepends=True):
        m = _ITEM.match(line)
        if m:
            body = m.group(1).strip()
            items.append(Item(body, pos, pos + len(line), _head(body), True))
        pos += len(line)
    if items:
        return items
    pos = 0
    for para in re.split(r"(\n\s*\n)", answer or ""):
        if para.strip() and not re.fullmatch(r"\n\s*\n", para):
            items.append(Item(para.strip(), pos, pos + len(para), _head(para.strip()), False))
        pos += len(para)
    return items


def _head(body: str) -> str:
    b = _BOLD.search(body)
    if b:
        return (b.group(1) or b.group(2) or "").strip()
    link = _LINK.search(body)
    if link and link.start() < 5:
        return link.group(1).strip()
    return _HEAD_SPLIT.split(body, maxsplit=1)[0].strip()[:120]


_GENERIC_HEADS = frozenset({
    "location", "price", "prices", "pricing", "amenities", "tips", "budget", "luxury", "business", "families",
    "family", "overview", "summary", "why", "pros", "cons", "note", "notes", "best for", "rating", "reviews",
})


def _looks_like_hotel(item: Item) -> bool:
    """An unknown list head counts as a hotel when it says so (Hotel, Resort,
    فندق) or is a bold/linked Title Case name of 2-6 words that is not a
    section label ("Location", "Best for"). Precision over recall: a missed
    'other' only lowers the SoV denominator slightly."""
    head = item.head or ""
    if not head or len(head) > 80:
        return False
    if _HOTELISH.search(head):
        return True
    if head.strip().lower() in _GENERIC_HEADS or not (_BOLD.search(item.text) or _LINK.search(item.text)):
        return False
    words = head.split()
    return 2 <= len(words) <= 6 and all(w[:1].isupper() or not w[:1].isascii() for w in words if w[:1].isalpha())


def _phrase_in(alias_norm: str, text_norm: str) -> bool:
    return bool(alias_norm) and re.search(rf"(?<!\w){re.escape(alias_norm)}(?!\w)", text_norm) is not None


# ─── Step 2 and 4: entities and positions ───


@dataclass
class Known:
    """A hotel the radar can name: 'self' or a comp_id, with its aliases, its
    own website host and its Google place id."""

    key: str
    names: list[str]
    host: str | None = None
    place_id: str | None = None

    @property
    def norms(self) -> list[str]:
        return [n for n in (normalise(x) for x in self.names) if n]


def _match_known(item: Item, known: list[Known], cites: list[Citation]) -> tuple[Known, str] | None:
    head_n, text_n = normalise(item.head), normalise(item.text)
    for k in known:  # the head names the hotel: strongest evidence
        if any(fuzz.token_set_ratio(head_n, a) >= MATCH_RATIO and len(head_n) >= 3 for a in k.norms if a):
            return k, "name"
    for k in known:  # the full alias as a contiguous phrase anywhere in the item
        if any(_phrase_in(a, text_n) for a in k.norms if len(a) >= 4):
            return k, "name"
    for k in known:  # a citation anchored in this item points at the hotel's own site or place
        for c in cites:
            d = domain_of(c.url)
            if k.host and (d == k.host or d.endswith("." + k.host)):
                return k, "domain"
            if k.place_id and k.place_id in (c.url or ""):
                return k, "place_id"
    return None


def resolve(parsed: Parsed, known: list[Known], *, extracted_names: list[str] | None = None) -> list[Entity]:
    """Hotels the answer names, in answer order, one Entity per hotel.
    Unknown hotel-looking heads become 'other'. `extracted_names` (the AI
    fallback, step 3) is used only when nothing resolved from the text."""
    items = split_items(parsed.answer_text)
    out: list[Entity] = []
    seen: set[str] = set()

    def add(key: str, name: str, via: str) -> None:
        tag = key if key != "other" else "other:" + normalise(name)
        if tag in seen:
            return
        seen.add(tag)
        out.append(Entity(key=key, name=name, position=len(out) + 1, via=via))

    for it in items:
        cites = [c for c in parsed.citations if c.start_index is not None and it.start <= c.start_index < it.end]
        hit = _match_known(it, known, cites)
        if hit:
            add(hit[0].key, it.head or hit[0].names[0], hit[1])
        elif it.listed and _looks_like_hotel(it):
            add("other", it.head, "name")
    if not out and extracted_names:
        for name in extracted_names:
            k = next((k for k in known if any(same_name(name, a) for a in k.names)), None)
            add(k.key if k else "other", name, "ai")
    return out


def self_cited(parsed: Parsed, host: str | None) -> bool:
    if not host:
        return False
    h = host.lower().removeprefix("www.")
    return any(domain_of(c.url) == h or domain_of(c.url).endswith("." + h) for c in parsed.citations)
