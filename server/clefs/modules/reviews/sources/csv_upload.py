"""
CSV review imports (03 spec 4.1): Booking.com and Agoda extranet exports,
plus a generic layout. Presets map header aliases to fields; /10 scores are
stored with rating_scale 10 and normalised to /5 by store.norm_5.

Headers are matched case- and punctuation-insensitively, because the
extranets rename columns between export versions ("Review score" vs
"Score"). A row without a rating and without text is skipped, not an error.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from clefs.core.errors import ClefsError
from clefs.modules.reviews import store

MAX_ROWS = 5000


@dataclass(frozen=True)
class Preset:
    platform: str
    scale: int
    fields: dict[str, tuple[str, ...]]


_COMMON = {
    "external_id": ("review id", "id"),
    "posted_at": ("review date", "date", "date of review", "published", "created"),
    "author": ("guest name", "reviewer name", "reviewer", "name", "author"),
    "title": ("review title", "title", "headline"),
    "positive": ("positive review", "positive comments", "positive", "liked", "pros"),
    "negative": ("negative review", "negative comments", "negative", "disliked", "cons"),
    "text": ("review", "review text", "comment", "comments", "text"),
    "rating": ("review score", "overall score", "score", "overall rating", "rating"),
    "language": ("language", "review language", "lang"),
    "response": ("property reply", "hotel response", "response", "reply", "property response"),
    "stay_date": ("check-in date", "check in date", "arrival", "stay date", "travel date"),
    "seed": ("reservation number", "booking number", "booking id"),
}

PRESETS: dict[str, Preset] = {
    "booking": Preset("booking", 10, _COMMON),
    "agoda": Preset("agoda", 10, _COMMON),
    "expedia": Preset("expedia", 5, _COMMON),
    "generic": Preset("other", 5, _COMMON),
}

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
                 "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y")


def _key(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (h or "").replace("\ufeff", "").lower()).strip()


def _header_map(headers: list[str], preset: Preset) -> dict[str, str]:
    by_key = {_key(h): h for h in headers}
    out: dict[str, str] = {}
    for field, aliases in preset.fields.items():
        for a in aliases:
            if _key(a) in by_key:
                out[field] = by_key[_key(a)]
                break
    return out


def parse_date(s: Any) -> Any:
    s = str(s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _rating(s: Any) -> str | None:
    """'8,4' (European exports) and '8.4 / 10' both read 8.4."""
    m = re.search(r"\d+(?:[.,]\d+)?", str(s or ""))
    return m.group(0).replace(",", ".") if m else None


_LANG_NAMES = {"english": "en", "arabic": "ar", "french": "fr", "german": "de", "russian": "ru", "hindi": "hi",
               "chinese": "zh", "spanish": "es", "italian": "it", "urdu": "ur", "persian": "fa", "turkish": "tr"}


def _lang(s: Any) -> str | None:
    """'English' (Booking exports) and 'en-GB' both become a short code."""
    s = str(s or "").strip().lower()
    if not s:
        return None
    return _LANG_NAMES.get(s) or s.split("-")[0][:12]


# ─── Import ───


def _reader(text: str) -> csv.DictReader:
    text = text.lstrip("\ufeff")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return csv.DictReader(io.StringIO(text), dialect=dialect)


def _compose(row: dict, cols: dict[str, str]) -> tuple[str | None, str | None]:
    """(title, text). Booking/Agoda split praise and complaints into two
    columns; both are kept, labelled, so the analysis sees which is which."""
    get = lambda f: (row.get(cols[f]) or "").strip() if f in cols else ""  # noqa: E731
    parts = []
    if get("text"):
        parts.append(get("text"))
    if get("positive"):
        parts.append(f"Liked: {get('positive')}")
    if get("negative"):
        parts.append(f"Disliked: {get('negative')}")
    return (get("title") or None), ("\n".join(parts) or None)


def import_csv(cur: Any, hotel: Any, text: str, *, preset: str = "booking", source_id: int | None = None,
               scale: int | None = None, dry_run: bool = False) -> dict:
    """Import one export. Returns {preset, platform, rows, created, updated,
    skipped, errors[{row, error}], columns{field: header}, dry_run}."""
    if preset not in PRESETS:
        raise ClefsError(400, f"Unknown preset {preset!r}", "invalid_preset")
    p = PRESETS[preset]
    rating_scale = scale or p.scale
    reader = _reader(text)
    headers = list(reader.fieldnames or [])
    cols = _header_map(headers, p)
    if "rating" not in cols and "text" not in cols and "positive" not in cols and "negative" not in cols:
        raise ClefsError(400, "No rating or review text column found", "csv_columns_missing")
    if not dry_run and source_id is None:
        source_id = store.create_source(cur, hotel.slug, "csv", platform=p.platform,
                                        label=f"{preset.title()} CSV")["id"]
    out = {"preset": preset, "platform": p.platform, "rows": 0, "created": 0, "updated": 0, "skipped": 0,
           "errors": [], "columns": cols, "dry_run": dry_run, "source_id": source_id}
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        if out["rows"] >= MAX_ROWS:
            out["errors"].append({"row": i, "error": f"more than {MAX_ROWS} rows; the rest was not read"})
            break
        out["rows"] += 1
        title, body = _compose(row, cols)
        rating = _rating(row.get(cols["rating"])) if "rating" in cols else None
        if rating is None and not body:
            out["skipped"] += 1
            continue
        if rating is not None and float(rating) > rating_scale:
            out["errors"].append({"row": i, "error": f"score {rating} is above the {rating_scale}-point scale"})
            continue
        posted = parse_date(row.get(cols["posted_at"])) if "posted_at" in cols else None
        if posted is not None and posted.tzinfo is None:
            # Extranet dates are the hotel's local day: noon keeps them on it.
            posted = datetime.combine(posted.date(), time(12), tzinfo=hotel.tz)
        stay = parse_date(row.get(cols["stay_date"])) if "stay_date" in cols else None
        author = (row.get(cols["author"]) or "").strip() if "author" in cols else None
        ext = (row.get(cols["external_id"]) or "").strip() if "external_id" in cols else ""
        if not ext:
            seed = (row.get(cols["seed"]) or "").strip() if "seed" in cols else ""
            ext = store.synthetic_id(p.platform, seed or author, posted.isoformat() if posted else "", body)
        r = store.ReviewIn(
            platform=p.platform, external_id=ext, rating=rating, rating_scale=rating_scale,
            author_display=author or None, title=title, text=body,
            language=_lang(row.get(cols["language"])) if "language" in cols else None,
            posted_at=posted,
            has_owner_response=bool("response" in cols and (row.get(cols["response"]) or "").strip()),
            stay_date_guess=stay.date() if stay else None,
        )
        if dry_run:
            out["created"] += 1
            continue
        _, created = store.ingest(cur, hotel.slug, source_id, r)
        out["created" if created else "updated"] += 1
    return out
