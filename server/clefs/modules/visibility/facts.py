"""
The approved KB as "truth" for fact checks (04 spec 5.4 lodging, 5.6 JSON-LD,
5.5 AI answers).

The AI section (below) extracts FactClaims from the sentences of an answer
that mention the hotel, compares each with the truth and upserts
vis_ai_facts by fingerprint, so the same wrong claim seen by two engines in
three weeks is one row with occurrences=6, not six rows.

concierge.api.kb_facts(keyed_only=True) gives normalised values ("15:00",
"paid", "true", "4"); star_rating falls back to hotels.star_rating. The stub
answer ([]) means every comparison is skipped (no lodging diff), never that
the web is wrong.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel

TIME_TOLERANCE_MIN = 30  # times differ when 30 minutes or more apart (5.5)


def kb_truth(cur: Any, hotel_slug: str) -> dict[str, str]:
    from clefs.core.hotels import get_hotel
    from clefs.modules.concierge import api as concierge_api
    from clefs.modules.visibility import xmod

    out: dict[str, str] = {}
    for f in xmod.call(cur, [], concierge_api.kb_facts, hotel_slug, keyed_only=True) or []:
        key, value = f.get("fact_key"), f.get("value")
        if key and value is not None and key not in out:
            out[key] = str(value).strip().lower()
    star = get_hotel(cur, hotel_slug).star_rating
    if "star_rating" not in out and star:
        out["star_rating"] = str(int(star))
    return out


def minutes(hhmm: str | None) -> int | None:
    if not hhmm:
        return None
    try:
        h, m = str(hhmm).strip()[:5].split(":")
        return int(h) * 60 + int(m)
    except (ValueError, TypeError):
        return None


def time_from_parts(hours: Any, mins: Any = 0) -> str | None:
    """A TimeOfDay ({hours, minutes}) as "HH:MM"; None when hours is absent."""
    if hours is None:
        return None
    return f"{int(hours):02d}:{int(mins or 0):02d}"


def differs(fact_key: str, ours: str | None, theirs: str | None) -> bool:
    """True when the web's value contradicts the KB. A missing value on
    either side is not a contradiction (nothing to compare)."""
    if ours is None or theirs is None:
        return False
    if fact_key in ("checkin_time", "checkout_time"):
        a, b = minutes(ours), minutes(theirs)
        return a is not None and b is not None and abs(a - b) >= TIME_TOLERANCE_MIN
    return str(ours).strip().lower() != str(theirs).strip().lower()


def as_bool(v: Any) -> str | None:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "yes", "1"):
        return "true"
    if s in ("false", "no", "0"):
        return "false"
    return None


# ─── AI answers: claims ───

FACT_KEYS = ("checkin_time", "checkout_time", "parking", "pool", "breakfast", "star_rating", "pets", "wifi",
             "price_band")
EXTRACT_TASK = "visibility.fact_extract"
SEVERITY = {"false_closed": 1, "wrong_value": 2, "wrong_star": 2, "false_absent": 2, "price_band_off": 3,
            "unverifiable": 4}
PRICE_BAND_TOLERANCE = 0.40  # +/-40% of the trailing-30-day ADR


class FactClaim(BaseModel):
    fact_key: Literal["checkin_time", "checkout_time", "parking", "pool", "breakfast", "star_rating", "pets", "wifi",
                      "price_band"]
    value: str
    quote: str


class FactClaims(BaseModel):
    facts: list[FactClaim]


CLAIMS_SYSTEM = (
    "Extract factual claims the text makes about the named hotel only. Allowed fact_key values: checkin_time, "
    "checkout_time, parking, pool, breakfast, star_rating, pets, wifi, price_band. Normalise value: times as "
    "HH:MM (24 h); parking, pool, breakfast, pets, wifi as one of yes, no, free, paid, included, closed; "
    "star_rating as a number; price_band as '<CURRENCY> <amount>' per night (the midpoint of a range). quote is "
    "the exact sentence. If the text says the hotel is closed, use value 'closed'. Return an empty list when the "
    "text states none of these."
)

_NEG = {"no", "false", "none", "not available", "unavailable"}
_POS = {"yes", "true", "free", "paid", "included", "available", "valet", "complimentary"}


def norm_claim(fact_key: str, value: str | None) -> str:
    v = (value or "").strip().lower()
    if fact_key in ("checkin_time", "checkout_time"):
        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", v)
        if m:
            h, mm, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
            if ap == "pm" and h < 12:
                h += 12
            if ap == "am" and h == 12:
                h = 0
            return f"{h:02d}:{mm:02d}"
        return "12:00" if "noon" in v else v
    if fact_key == "star_rating":
        m = re.search(r"\d", v)
        return m.group(0) if m else v
    if v in ("complimentary", "included for free"):
        return "free"
    return v


def fingerprint(hotel_slug: str, fact_key: str, claimed: str) -> str:
    return hashlib.sha1(f"{hotel_slug}|{fact_key}|{claimed}".encode()).hexdigest()


# ─── AI answers: comparators ───


def _amount(v: str) -> tuple[str | None, float | None]:
    m = re.search(r"([a-z]{3})?\s*([\d,]+(?:\.\d+)?)", v.lower())
    if not m:
        return None, None
    try:
        return (m.group(1) or "").upper() or None, float(m.group(2).replace(",", ""))
    except ValueError:
        return None, None


def compare(fact_key: str, claimed: str, truth: dict[str, str], *, currency: str | None = None) -> str | None:
    """The error type for one normalised claim, or None when it agrees (or
    cannot be compared, e.g. a price in another currency)."""
    if claimed == "closed":
        return "false_closed"
    ours = truth.get(fact_key)
    if ours is None:
        return "unverifiable"
    if fact_key in ("checkin_time", "checkout_time"):
        return "wrong_value" if differs(fact_key, ours, claimed) else None
    if fact_key == "star_rating":
        return "wrong_star" if claimed.isdigit() and ours.isdigit() and claimed != ours else None
    if fact_key == "price_band":
        ccy, amt = _amount(claimed)
        _, adr = _amount(ours)
        if amt is None or not adr or (ccy and currency and ccy != currency.upper()):
            return None
        return "price_band_off" if abs(amt - adr) > PRICE_BAND_TOLERANCE * adr else None
    ours_b = as_bool(ours) or ours
    if claimed in _NEG:
        # "no parking" when the KB says paid (or free) parking: the web denies
        # an amenity the hotel has (false_absent).
        return "false_absent" if (ours_b == "true" or ours in _POS) else None
    if claimed in _POS and (ours_b == "false" or ours in _NEG):
        return "wrong_value"
    if {claimed, ours} == {"free", "paid"}:
        return "wrong_value"
    return None


def trailing_adr(cur: Any, hotel: Any, days: int = 30) -> str | None:
    """The price_band truth: trailing-30-day ADR from core/stays (room rate
    only), as '<CCY> <amount>'. None when there were no stayed nights."""
    from datetime import timedelta

    from clefs.core.time import local_date

    today = local_date(hotel)
    cur.execute(
        """
        SELECT SUM(n.rate_minor) AS rev, SUM(n.rooms) AS nights FROM reservation_nights n
        JOIN reservations r ON r.id = n.reservation_id
        WHERE n.hotel_slug = %s AND n.stay_date >= %s AND n.stay_date < %s AND r.status <> 'CANCELLED'
          AND n.currency = %s
        """,
        (hotel.slug, today - timedelta(days=days), today, hotel.currency),
    )
    row = cur.fetchone() or {}
    if not row.get("nights"):
        return None
    from clefs.core import money

    adr = money.from_minor(int(row["rev"] or 0), hotel.currency) / int(row["nights"])
    return f"{hotel.currency} {adr:.2f}"


def ai_truth(cur: Any, hotel: Any) -> dict[str, str]:
    truth = kb_truth(cur, hotel.slug)
    if hotel.star_rating:
        truth["star_rating"] = str(int(hotel.star_rating))  # hotels.star_rating is the star truth (5.5)
    adr = trailing_adr(cur, hotel)
    if adr:
        truth["price_band"] = adr
    return truth


# ─── AI answers: detection over a batch (resumable) ───

_SENT = re.compile(r"(?<=[.!?؟])\s+|\n+")


def hotel_sentences(answer: str, names: list[str], *, whole: bool) -> str:
    """The sentences that mention the hotel. Brand and fact-check prompts are
    about the hotel, so their whole answer is used (it says "the hotel")."""
    from clefs.modules.visibility.parsers import normalise

    if whole:
        return answer[:6000]
    ns = [n for n in (normalise(x) for x in names) if len(n) >= 3]
    keep = [s for s in _SENT.split(answer or "") if s.strip() and any(n in normalise(s) for n in ns)]
    return " ".join(keep)[:6000]


def extract_claims(text: str, hotel_name: str, hotel_slug: str) -> list[FactClaim]:
    from clefs import ai

    if not text.strip():
        return []
    # Raises AIDisabled / AIError: the caller stops the chunk and leaves the
    # run unchecked, so a failing model never marks an answer as clean.
    out = ai.structured(EXTRACT_TASK, system=CLAIMS_SYSTEM, input=f"Hotel: {hotel_name}\n\nText:\n{text}",
                        schema=FactClaims, hotel_slug=hotel_slug, store=False, timeout_s=15.0, max_output_tokens=800)
    return list(out.facts)


def check_pending(cur: Any, hotel: Any, *, batch_id: int, limit: int) -> int:
    """Extract and compare claims for up to `limit` unchecked runs of the
    batch that mention the hotel (every intent). Runs that do not mention it
    are marked checked without an AI call. Returns the runs checked (fewer
    than `limit` means the batch is done, or the AI failed and stopped it)."""
    from clefs.core import clock
    from clefs.modules.visibility import store

    now = clock.now()
    cur.execute(
        """
        UPDATE vis_ai_runs SET facts_checked_at = %s WHERE batch_id = %s AND facts_checked_at IS NULL
          AND (status <> 'ok' OR (NOT COALESCE(self_mentioned, FALSE) AND prompt_intent NOT IN ('brand', 'fact_check')))
        """,
        (now, batch_id),
    )
    cur.execute(
        """
        SELECT id, engine, prompt_intent, answer_text, cited_domains FROM vis_ai_runs
        WHERE batch_id = %s AND facts_checked_at IS NULL AND status = 'ok' ORDER BY id LIMIT %s
        """,
        (batch_id, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return 0
    profile = store.get_profile(cur, hotel.slug)
    names = [hotel.name, *(profile.get("brand_aliases") or [])]
    truth = ai_truth(cur, hotel)
    processed = 0
    for r in rows:
        text = hotel_sentences(r["answer_text"] or "", names, whole=r["prompt_intent"] in ("brand", "fact_check"))
        try:
            claims = extract_claims(text, hotel.name, hotel.slug) if text else []
        except Exception:  # noqa: BLE001  (AIDisabled / AIError: stop this chunk, retry on the next rollup)
            break
        for c in claims:
            claimed = norm_claim(c.fact_key, c.value)
            err = compare(c.fact_key, claimed, truth, currency=hotel.currency)
            if err:
                record_fact(cur, hotel.slug, fact_key=c.fact_key, claimed=claimed, quote=c.quote,
                            truth_value=truth.get(c.fact_key), error_type=err, engine=r["engine"], run_id=r["id"],
                            cited_domains=list(r.get("cited_domains") or []))
        cur.execute("UPDATE vis_ai_runs SET facts_checked_at = %s WHERE id = %s", (now, r["id"]))
        processed += 1
    return processed


def record_fact(cur: Any, hotel_slug: str, *, fact_key: str, claimed: str, quote: str | None,
                truth_value: str | None, error_type: str, engine: str, run_id: int | None,
                cited_domains: list[str]) -> dict:
    """Upsert by fingerprint. A fixed error seen again is 'recurred'; a
    dismissed one stays dismissed (the owner said it is not wrong)."""
    from clefs.core import clock

    now = clock.now()
    fp = fingerprint(hotel_slug, fact_key, claimed)
    cur.execute(
        """
        INSERT INTO vis_ai_facts (hotel_slug, fingerprint, fact_key, claimed_value, claimed_quote, truth_value,
            truth_source, error_type, severity, engines, cited_domains, occurrences, run_ids, status, first_seen,
            last_seen)
        VALUES (%(h)s, %(fp)s, %(k)s, %(v)s, %(q)s, %(t)s, %(src)s, %(e)s, %(sev)s, ARRAY[%(eng)s]::text[],
                %(doms)s::text[], 1, %(runs)s::bigint[], 'open', %(now)s, %(now)s)
        ON CONFLICT (hotel_slug, fingerprint) DO UPDATE SET
            occurrences = vis_ai_facts.occurrences + 1, last_seen = EXCLUDED.last_seen,
            claimed_quote = COALESCE(EXCLUDED.claimed_quote, vis_ai_facts.claimed_quote),
            truth_value = EXCLUDED.truth_value, error_type = EXCLUDED.error_type, severity = EXCLUDED.severity,
            engines = ARRAY(SELECT DISTINCT unnest(vis_ai_facts.engines || EXCLUDED.engines)),
            cited_domains = ARRAY(SELECT DISTINCT unnest(vis_ai_facts.cited_domains || EXCLUDED.cited_domains)),
            run_ids = (vis_ai_facts.run_ids || EXCLUDED.run_ids)[GREATEST(1, cardinality(vis_ai_facts.run_ids) - 48):],
            status = CASE WHEN vis_ai_facts.status = 'fixed' THEN 'recurred' ELSE vis_ai_facts.status END
        RETURNING *
        """,
        {"h": hotel_slug, "fp": fp, "k": fact_key, "v": claimed, "q": (quote or "")[:500] or None, "t": truth_value,
         "src": "concierge_kb" if fact_key not in ("price_band", "star_rating") else
                ("stays_adr_30d" if fact_key == "price_band" else "hotel_settings"),
         "e": error_type, "sev": SEVERITY.get(error_type, 3), "eng": engine, "doms": cited_domains[:20],
         "runs": [run_id] if run_id else [], "now": now},
    )
    return dict(cur.fetchone())


def open_ai_facts(cur: Any, hotel_slug: str) -> list[dict]:
    """For visibility.api.open_fact_errors: [{fact_key, ours, theirs, source_url, seen_at}]."""
    cur.execute(
        """
        SELECT fact_key, truth_value, claimed_value, cited_domains, last_seen FROM vis_ai_facts
        WHERE hotel_slug = %s AND status IN ('open', 'recurred') AND error_type <> 'unverifiable'
        ORDER BY severity, last_seen DESC
        """,
        (hotel_slug,),
    )
    return [{"fact_key": r["fact_key"], "ours": r["truth_value"], "theirs": r["claimed_value"],
             "source_url": f"https://{r['cited_domains'][0]}" if r["cited_domains"] else None,
             "seen_at": r["last_seen"].isoformat() if r["last_seen"] else None, "source": "ai_answers"}
            for r in cur.fetchall()]
