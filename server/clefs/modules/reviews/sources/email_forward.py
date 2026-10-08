"""
Forwarded review emails (03 spec 4.1, 04 spec F4): the hotel forwards
platform notifications ("You have a new guest review") to
<hotels.inbound_email_alias>+reviews@in.clefs.world.

Per-sender parsers read the platform and the score; the review body comes
from a JSON-schema LLM extraction when AI is on, else from the notification
text itself. Only mail from the hotel's own team, a platform domain or an
address the owner listed is accepted: the alias is guessable, and a stranger
must not be able to plant reviews (and trigger drafts and signals). The From
address is only believed when SPF or DKIM passed for its own domain (a DMARC
pass says exactly that).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from clefs import ai, ratelimit
from clefs.modules.reviews import store

# Registrable domains only: a sender passes on an exact match or a subdomain
# of one. Never a bare prefix like "tripadvisor.": "tripadvisor.attacker.net"
# and "evil-tripadvisor.example" are anyone's to register.
TRIPADVISOR_DOMAINS = (
    "tripadvisor.com", "tripadvisor.co.uk", "tripadvisor.ae", "tripadvisor.com.au", "tripadvisor.ca",
    "tripadvisor.in", "tripadvisor.ie", "tripadvisor.co.nz", "tripadvisor.co.za", "tripadvisor.com.sg",
    "tripadvisor.com.my", "tripadvisor.com.ph", "tripadvisor.com.hk", "tripadvisor.de", "tripadvisor.fr",
    "tripadvisor.es", "tripadvisor.it", "tripadvisor.nl", "tripadvisor.pt", "tripadvisor.se",
    "tripadvisor.ch", "tripadvisor.at", "tripadvisor.be", "tripadvisor.dk", "tripadvisor.jp",
    "tripadvisor.cn", "tripadvisor.com.br", "tripadvisor.com.mx", "tripadvisor.com.tr",
    "tripadvisor.com.eg", "tripadvisor.com.sa",
)
PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    "booking": ("booking.com",),
    "tripadvisor": TRIPADVISOR_DOMAINS,
    "agoda": ("agoda.com", "agoda.net"),
    "expedia": ("expedia.com", "expediapartnercentral.com", "hotels.com"),
    "google": ("google.com", "business.google.com"),
}
SCALE = {"booking": 10, "agoda": 10, "tripadvisor": 5, "expedia": 5, "google": 5, "other": 5}
DAILY_CAP = 200


class EmailReviewExtract(BaseModel):
    """What the model reads out of one forwarded notification."""

    is_review: bool
    platform: Literal["booking", "tripadvisor", "agoda", "expedia", "google", "other"]
    rating: float | None = Field(description="The guest's score as shown, on the platform's own scale")
    rating_scale: Literal[5, 10]
    author: str | None
    title: str | None
    text: str | None = Field(description="The guest's review text only, verbatim, without email boilerplate")
    posted_date: str | None = Field(description="YYYY-MM-DD when the email states it")
    language: str | None = Field(description="ISO 639-1 code of the review text")


# ─── Platform and score heuristics ───


# Text hints for naming the platform only (never a trust decision): a
# forwarded body says "tripadvisor.xx" for any of its country sites.
_DETECT_HINTS: dict[str, tuple[str, ...]] = {"tripadvisor": ("tripadvisor.",)}


def detect_platform(*texts: str | None) -> str:
    hay = " ".join(t or "" for t in texts).lower()
    for platform, domains in PLATFORM_DOMAINS.items():
        if any(d in hay for d in (*domains, *_DETECT_HINTS.get(platform, ()))):
            return platform
    return "other"


_SCORE_10 = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*(?:/|out of)\s*10\b", re.IGNORECASE)
_SCORE_5 = re.compile(r"\b([1-5](?:[.,]\d)?)\s*(?:/|out of|of)\s*5\b|\b([1-5])\s*(?:stars?|bubbles?)\b", re.IGNORECASE)


def parse_score(text: str, platform: str) -> tuple[float | None, int]:
    """(rating, scale) from the notification text: '8.4/10', 'rated 4 of 5
    bubbles', '5 stars', or a row of star characters."""
    if SCALE.get(platform, 5) == 10:
        m = _SCORE_10.search(text)
        if m:
            return float(m.group(1).replace(",", ".")), 10
    m = _SCORE_5.search(text)
    if m:
        return float((m.group(1) or m.group(2)).replace(",", ".")), 5
    stars = re.search(r"(\u2605{1,5})", text)
    if stars:
        return float(len(stars.group(1))), 5
    m = _SCORE_10.search(text)
    if m:
        return float(m.group(1).replace(",", ".")), 10
    return None, SCALE.get(platform, 5)


# ─── Sender authentication ───
# The allowlist below reads the From header, which anyone can write. So the
# From domain must first be vouched for by SPF or DKIM, and the pass must be
# ALIGNED with it (DMARC's relaxed rule: same organisational domain). An
# unaligned pass proves nothing: an attacker's own server passes SPF for
# attacker.net while claiming From: noreply@booking.com. A DMARC pass is the
# receiving server's own finding of such an aligned pass, and the only one
# Resend's verdicts can show (they name no SPF or DKIM domain to align).

# Second-level labels under a two-letter country code that are not
# registrable by themselves (tripadvisor.co.uk, hotel.com.au).
_PUBLIC_SLD = frozenset({"co", "com", "net", "org", "ac", "gov", "edu", "ne", "or", "gen", "ltd", "plc"})


def _org_domain(domain: str) -> str:
    labels = [x for x in domain.strip().strip(".").lower().split(".") if x]
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _PUBLIC_SLD:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _aligned(a: str | None, b: str | None) -> bool:
    return bool(a and b) and _org_domain(a) == _org_domain(b)  # type: ignore[arg-type]


def _header(email: Any, name: str) -> str:
    want = name.lower()
    headers = getattr(email, "headers", None) or {}
    return next((str(v) for k, v in headers.items() if k.lower() == want), "")


def _domain_of(addr: str) -> str:
    return addr.strip().strip("<>\"'").rsplit("@", 1)[-1].lower()


def _spf_domain(email: Any) -> str | None:
    """The envelope-from domain SPF checked (a Received-SPF envelope-from=, or
    an Authentication-Results smtp.mailfrom=)."""
    m = re.search(r"envelope-from=\s*\"?<?([^\s;\"<>]+)", _header(email, "Received-SPF"), re.IGNORECASE)
    if not m:
        m = re.search(r"\bspf=pass\b[^;]*?smtp\.mailfrom=\s*\"?<?([^\s;\"<>]+)",
                      _header(email, "Authentication-Results"), re.IGNORECASE)
    return _domain_of(m.group(1)) if m else None


def _dkim_pass_domains(email: Any) -> list[str]:
    """header.d (or header.i) of every dkim=pass result in Authentication-Results."""
    out = []
    for part in _header(email, "Authentication-Results").split(";"):
        if re.search(r"\bdkim=pass\b", part, re.IGNORECASE):
            m = re.search(r"header\.[di]=\s*@?([^\s;]+)", part, re.IGNORECASE)
            if m:
                out.append(_domain_of(m.group(1)))
    return out


def _dmarc_pass(email: Any, domain: str) -> bool:
    """dmarc=pass in Authentication-Results, for this From domain when the
    result names one (header.from=)."""
    for part in _header(email, "Authentication-Results").split(";"):
        if re.search(r"\bdmarc=pass\b", part, re.IGNORECASE):
            m = re.search(r"header\.from=\s*([^\s;]+)", part, re.IGNORECASE)
            if m is None or _aligned(_domain_of(m.group(1)), domain):
                return True
    return False


def sender_authenticated(email: Any) -> bool:
    """SPF or DKIM passed for the From address's own organisational domain,
    or DMARC passed for it."""
    s = (email.sender or "").strip().lower()
    if "@" not in s:
        return False
    domain = _domain_of(s)
    if _dmarc_pass(email, domain):
        return True
    if getattr(email, "spf", None) == "pass" and _aligned(_spf_domain(email), domain):
        return True
    return any(_aligned(d, domain) for d in _dkim_pass_domains(email))


# ─── Sender allowlist ───


def sender_allowed(cur: Any, hotel_slug: str, sender: str | None, extra: list[str]) -> bool:
    s = (sender or "").strip().lower()
    if "@" not in s:
        return False
    domain = s.rsplit("@", 1)[1]
    if any(domain == d or domain.endswith("." + d) for ds in PLATFORM_DOMAINS.values() for d in ds):
        return True
    for e in extra:
        e = e.strip().lower()
        if e and (s == e or (e.startswith("@") and domain == e[1:])):
            return True
    cur.execute(
        """SELECT 1 FROM users u JOIN hotel_memberships m ON m.user_id = u.id
           WHERE m.hotel_slug = %s AND m.is_active AND lower(u.email) = %s LIMIT 1""",
        (hotel_slug, s),
    )
    return cur.fetchone() is not None


# ─── Extraction and ingest ───

_SYSTEM = (
    "You read one hotel review notification email (often forwarded by hotel staff) and extract the guest "
    "review it contains. Copy the review text verbatim in its original language; drop greetings, links, "
    "footers and platform boilerplate. If the email is not a guest review notification, set is_review false."
)


def _plain(email: Any) -> str:
    body = email.text or ""
    if not body and email.html:
        body = re.sub(r"<[^>]+>", " ", email.html)
        body = re.sub(r"&nbsp;|&#160;", " ", body)
    return re.sub(r"[ \t]+", " ", body).strip()[:20000]


def extract(email: Any) -> tuple[store.ReviewIn | None, str]:
    """(review, how) where how is 'llm' | 'heuristic' | 'not_a_review'."""
    from clefs.modules.reviews.sources.csv_upload import parse_date

    body = _plain(email)
    platform = detect_platform(email.sender, email.subject, body)
    try:
        x = ai.structured("reviews.email_extract", system=_SYSTEM,
                          input=f"Subject: {email.subject or ''}\n\n{body}", schema=EmailReviewExtract,
                          hotel_slug=email.hotel_slug, max_output_tokens=1500)
    except (ai.AIDisabled, ai.AIError):
        x = None
    if x is not None:
        if not x.is_review:
            return None, "not_a_review"
        plat = x.platform if x.platform != "other" else platform
        posted = parse_date(x.posted_date)
        return store.ReviewIn(
            platform=plat, external_id=store.synthetic_id(plat, x.author, x.posted_date, (x.text or "")[:200]),
            rating=x.rating, rating_scale=x.rating_scale, author_display=x.author, title=x.title, text=x.text,
            language=x.language, posted_at=posted if posted and posted.tzinfo else None,
        ), "llm"
    rating, scale = parse_score(f"{email.subject or ''}\n{body}", platform)
    if rating is None and platform == "other":
        return None, "not_a_review"
    text = (email.stripped_reply or body)[:4000]
    return store.ReviewIn(
        platform=platform, external_id=store.synthetic_id(platform, email.message_id), rating=rating,
        rating_scale=scale, title=email.subject, text=text,
    ), "heuristic"


def handle(email: Any) -> dict:
    """The inbound-email handler body (registered in jobs.py)."""
    from clefs.modules.reviews.settings import get_settings

    cur = email.cur
    cfg = get_settings(cur, email.hotel_slug)
    # Authentication before the allowlist: the allowlist trusts From.
    if not sender_authenticated(email):
        return {"handled": False, "reason": "sender_unauthenticated"}
    if not sender_allowed(cur, email.hotel_slug, email.sender, cfg.forward_senders):
        return {"handled": False, "reason": "sender_not_allowed"}
    if not ratelimit.hit(cur, f"reviews:email:{email.hotel_slug}", limit=DAILY_CAP, window_s=86400):
        return {"handled": False, "reason": "rate_limited"}
    review, how = extract(email)
    if review is None:
        return {"handled": False, "reason": how}
    src = store.ensure_source(cur, email.hotel_slug, "email", label="Forwarded emails")
    row, created = store.ingest(cur, email.hotel_slug, src["id"], review)
    return {"handled": True, "review_id": row["id"], "created": created, "via": how}
