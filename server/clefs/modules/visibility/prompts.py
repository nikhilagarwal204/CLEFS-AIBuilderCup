"""
AI radar prompts (04 spec 5.5 "Prompts", 5.9 snapshot prompts).

Templates are deterministic so weeks stay comparable: the same inputs give
the same text, and only a change to a template's inputs (the area, a
landmark, an event, the star rating) creates a new `version` of that
prompt_key. Retired versions keep their runs (history is never rewritten).

Weekly set, 24 prompts:
- home market (the hotel's country and city): 10 EN discovery (5 generic,
  3 persona, 2 event), 1 EN brand, 5 AR (MSA) discovery (generic 1-3,
  persona 1-2), 4 EN fact checks (check-in time, parking, pool, stars)
- source markets: 2 EN discovery prompts for each of the top 2
  guest_country values over the trailing 180 days (or profile.ai_markets)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

DISCOVERY = ("generic", "persona", "event")

# ─── Shapes ───


@dataclass(frozen=True)
class PromptSpec:
    prompt_key: str
    text: str
    language: str  # en | ar
    market: str  # ISO2, upper
    city: str | None  # the localisation city (user_location), not the hotel's
    intent: str  # generic | persona | event | fact_check | brand
    fact_key: str | None = None
    event_id: str | None = None


@dataclass(frozen=True)
class Inputs:
    hotel_name: str
    city: str
    country: str
    area: str | None = None
    star: int | None = None
    landmarks: tuple[str, ...] = ()
    events: tuple[dict, ...] = ()  # [{event_id, name}] from demand.api.upcoming_events
    source_markets: tuple[tuple[str, str], ...] = ()  # ((ISO2, main city), ...)


# Main city per source market: the engine locale city (Gemini prompt locale, AI Mode location) for its prompts.
MARKET_CITIES = {
    "GB": "London", "SA": "Riyadh", "IN": "Mumbai", "DE": "Berlin", "RU": "Moscow", "US": "New York",
    "CN": "Beijing", "FR": "Paris", "KW": "Kuwait City", "QA": "Doha", "BH": "Manama", "OM": "Muscat",
    "EG": "Cairo", "PK": "Karachi", "IT": "Milan", "ES": "Madrid", "NL": "Amsterdam", "CH": "Zurich",
    "KZ": "Almaty", "AU": "Sydney", "CA": "Toronto", "JO": "Amman", "LB": "Beirut", "PH": "Manila",
    "NG": "Lagos", "ZA": "Johannesburg", "TR": "Istanbul", "IR": "Tehran", "PL": "Warsaw", "IL": "Tel Aviv",
}
CITY_AR = {
    "dubai": "دبي", "abu dhabi": "أبوظبي", "sharjah": "الشارقة",
    "ras al khaimah": "رأس الخيمة",
    "ajman": "عجمان", "fujairah": "الفجيرة", "umm al quwain": "أم القيوين", "al ain": "العين",
}

# ─── Templates ───


def _where(i: Inputs) -> str:
    return f"{i.area}, {i.city}" if i.area else i.city


def _near(i: Inputs) -> str:
    return i.landmarks[0] if i.landmarks else (i.area or "the city centre")


def _stars(i: Inputs) -> str:
    return f"{i.star}-star " if i.star else ""


def _en_generic(i: Inputs) -> list[str]:
    return [
        f"What are the best {_stars(i)}hotels in {_where(i)}?",
        f"Recommend a hotel in {i.city} near {_near(i)}",
        f"Which hotels in {_where(i)} offer the best value for money?",
        f"Where should I stay in {i.city} for my first visit?",
        f"Best hotels in {_where(i)} with a pool",
    ]


def _en_persona(i: Inputs) -> list[str]:
    return [
        f"Where should I stay in {_where(i)} for a short business trip?",
        f"Best family-friendly hotels in {i.city} near {_near(i)}",
        f"A romantic hotel in {i.city} for a couple's weekend: what do you recommend?",
    ]


def _ar(i: Inputs) -> list[tuple[str, str]]:
    city = CITY_AR.get(i.city.strip().lower(), i.city)
    where = f"{i.area}، {city}" if i.area else city
    stars = f"ذات {i.star} نجوم " if i.star else ""
    near = i.landmarks[0] if i.landmarks else (i.area or "وسط المدينة")
    return [
        ("generic", f"ما هي أفضل الفنادق {stars}في {where}؟"),
        ("generic", f"أنصحني بفندق في {city} قريب من {near}"),
        ("generic", f"ما الفنادق في {where} التي تقدم أفضل قيمة مقابل السعر؟"),
        ("persona", f"أين أقيم في {where} لرحلة عمل قصيرة؟"),
        ("persona", f"ما أفضل الفنادق المناسبة للعائلات في {city}؟"),
    ]


FACT_PROMPTS = (
    ("checkin_time", "What time is check-in at {name}, {city}?"),
    ("parking", "Does {name} in {city} have parking, and is it free?"),
    ("pool", "Does {name} in {city} have a swimming pool, and is breakfast included?"),
    ("star_rating", "How many stars does {name} in {city} have?"),
)


# ─── The weekly set (pure) ───


def build(i: Inputs) -> list[PromptSpec]:
    home = (i.country or "AE").upper()[:2]
    out: list[PromptSpec] = []
    for n, text in enumerate(_en_generic(i), 1):
        out.append(PromptSpec(f"{home.lower()}.en.generic.{n}", text, "en", home, i.city, "generic"))
    for n, text in enumerate(_en_persona(i), 1):
        out.append(PromptSpec(f"{home.lower()}.en.persona.{n}", text, "en", home, i.city, "persona"))
    events = list(i.events)[:2]
    for n in (1, 2):
        ev = events[n - 1] if n <= len(events) else None
        if ev:
            text = (f"Where should I stay in {i.city} during {ev['name']}?" if n == 1
                    else f"Hotels near {_near(i)} for {ev['name']} in {i.city}")
            out.append(PromptSpec(f"{home.lower()}.en.event.{n}", text, "en", home, i.city, "event",
                                  event_id=str(ev.get("event_id")) if ev.get("event_id") else None))
        else:
            # No scored event in the next 60 days: a seasonal discovery prompt
            # keeps the home-market sample at 10 prompts (n=40 per engine).
            text = (f"Best hotels in {i.city} for a weekend getaway" if n == 1
                    else f"Best hotels in {_where(i)} for a long stay")
            out.append(PromptSpec(f"{home.lower()}.en.seasonal.{n}", text, "en", home, i.city, "generic"))
    out.append(PromptSpec(f"{home.lower()}.en.brand", f"Is {i.hotel_name} in {i.city} a good hotel?", "en", home,
                          i.city, "brand"))
    per_intent: dict[str, int] = {}
    for intent, text in _ar(i):
        per_intent[intent] = n = per_intent.get(intent, 0) + 1
        out.append(PromptSpec(f"{home.lower()}.ar.{intent}.{n}", text, "ar", home, i.city, intent))
    for key, tpl in FACT_PROMPTS:
        out.append(PromptSpec(f"{home.lower()}.en.fact.{key}", tpl.format(name=i.hotel_name, city=i.city), "en",
                              home, i.city, "fact_check", fact_key=key))
    generic, persona = _en_generic(i), _en_persona(i)
    for market, mcity in list(i.source_markets)[:2]:
        m = market.upper()[:2]
        out.append(PromptSpec(f"{m.lower()}.en.generic.1", generic[0], "en", m, mcity, "generic"))
        out.append(PromptSpec(f"{m.lower()}.en.persona.2", persona[1], "en", m, mcity, "persona"))
    return out


def snapshot_prompts(*, hotel_name: str, city: str, area: str | None, star: int | None) -> list[PromptSpec]:
    """The 5 public-snapshot prompts (5.9), verbatim. 1-3 are discovery (they
    count mentions), 4 is brand, 5 is fact check (both quoted, not counted)."""
    area_ = area or city
    best = f"Best {star}-star hotels in {area_}, {city}" if star else f"Best hotels in {area_}, {city}"
    return [
        PromptSpec("snap.1", best, "en", "AE", city, "generic"),
        PromptSpec("snap.2", f"Where should I stay in {area_}, {city} for a short business trip?", "en", "AE", city,
                   "persona"),
        PromptSpec("snap.3", f"Recommend hotels near {area_} in {city}", "en", "AE", city, "generic"),
        PromptSpec("snap.4", f"Is {hotel_name} in {city} a good hotel?", "en", "AE", city, "brand"),
        PromptSpec("snap.5", f"What time is check-in at {hotel_name}, {city}, and does it have parking?", "en", "AE",
                   city, "fact_check", fact_key="checkin_time"),
    ]


# ─── Inputs from the database ───


def source_markets(cur: Any, hotel: Any, profile: dict) -> list[tuple[str, str]]:
    """profile.ai_markets ([{market, city?}]) when the owner set them, else the
    top 2 guest_country values over the trailing 180 days (the home country
    excluded: it already has its own prompts)."""
    from clefs.core.time import local_date

    home = (hotel.country or "AE").upper()
    chosen = [(str(m.get("market") or "").upper()[:2], m.get("city")) for m in profile.get("ai_markets") or []
              if isinstance(m, dict) and m.get("market")]
    if not chosen:
        since = local_date(hotel) - timedelta(days=180)
        cur.execute(
            """
            SELECT UPPER(LEFT(guest_country, 2)) AS market, COUNT(*) AS n FROM reservations
            WHERE hotel_slug = %s AND check_in >= %s AND guest_country IS NOT NULL AND guest_country <> ''
              AND status <> 'CANCELLED' AND UPPER(LEFT(guest_country, 2)) <> %s
            GROUP BY 1 ORDER BY n DESC, market LIMIT 2
            """,
            (hotel.slug, since, home),
        )
        chosen = [(r["market"], None) for r in cur.fetchall()]
    return [(m, c or MARKET_CITIES.get(m, m)) for m, c in chosen if len(m) == 2 and m != home][:2]


def inputs_for(cur: Any, hotel: Any, profile: dict) -> Inputs:
    from clefs.modules.demand import api as demand_api
    from clefs.modules.visibility import xmod

    events = xmod.call(cur, [], demand_api.upcoming_events, hotel.slug, days=60) or []
    events = sorted([e for e in events if e.get("name")], key=lambda e: (-(e.get("score") or 0), str(e.get("start"))))
    return Inputs(
        hotel_name=hotel.name, city=hotel.city or "", country=hotel.country or "AE", area=profile.get("area"),
        star=int(hotel.star_rating) if getattr(hotel, "star_rating", None) else None,
        landmarks=tuple(profile.get("landmarks") or ()), events=tuple(events[:2]),
        source_markets=tuple(source_markets(cur, hotel, profile)),
    )


# ─── Versioning (visibility.ai_prompts_refresh) ───


def refresh(cur: Any, hotel: Any, profile: dict | None = None) -> dict:
    """Bring vis_ai_prompts in line with this week's templates: an unchanged
    prompt keeps its row, a changed one is retired and re-inserted as
    version + 1, and a key no longer generated is retired."""
    from clefs.core import clock
    from clefs.modules.visibility import store

    profile = profile or store.get_profile(cur, hotel.slug)
    specs = build(inputs_for(cur, hotel, profile))
    now = clock.now()
    cur.execute("SELECT id, prompt_key, version, text, language, market, city, intent, fact_key, "
                "event_id::text AS event_id FROM vis_ai_prompts WHERE hotel_slug = %s AND active", (hotel.slug,))
    active = {r["prompt_key"]: dict(r) for r in cur.fetchall()}
    created = kept = 0
    for s in specs:
        cur_row = active.pop(s.prompt_key, None)
        want = {k: getattr(s, k) or None for k in ("text", "language", "market", "city", "intent", "fact_key")}
        want["event_id"] = _uuid_or_none(s.event_id)  # compared as stored, or a non-UUID id re-versions weekly
        same = cur_row is not None and all((str(cur_row.get(k) or "").strip() or None) == v for k, v in want.items())
        if same:
            kept += 1
            continue
        if cur_row is not None:
            cur.execute("UPDATE vis_ai_prompts SET active = FALSE, retired_at = %s WHERE id = %s", (now, cur_row["id"]))
        cur.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM vis_ai_prompts "
                    "WHERE hotel_slug = %s AND prompt_key = %s", (hotel.slug, s.prompt_key))
        version = cur.fetchone()["v"]
        cur.execute(
            """
            INSERT INTO vis_ai_prompts (hotel_slug, prompt_key, version, text, language, market, city, intent,
                                        fact_key, event_id, active, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
            """,
            (hotel.slug, s.prompt_key, version, s.text, s.language, s.market, s.city, s.intent, s.fact_key,
             _uuid_or_none(s.event_id), now),
        )
        created += 1
    for row in active.values():
        cur.execute("UPDATE vis_ai_prompts SET active = FALSE, retired_at = %s WHERE id = %s", (now, row["id"]))
    return {"prompts": len(specs), "created": created, "kept": kept, "retired": len(active)}


def active_prompts(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute("SELECT id, prompt_key, version, text, language, market, city, intent, fact_key "
                "FROM vis_ai_prompts WHERE hotel_slug = %s AND active ORDER BY prompt_key", (hotel_slug,))
    return [dict(r) for r in cur.fetchall()]


def _uuid_or_none(v: str | None) -> str | None:
    import uuid

    try:
        return str(uuid.UUID(str(v))) if v else None
    except ValueError:
        return None  # a Demand event id that is not a UUID: kept out of the UUID column, the text still differs
