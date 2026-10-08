"""
Privacy primitives shared by every module (04 spec F8). The Trust module owns
the engines (retention purge, DSAR export, the PII masking matrix); this file
owns the registry they read and the two keyed primitives nothing else may
re-implement.

    register_pii("guests", "gst_profiles", subject_columns={"phone_hmac": "hmac"},
                 ts_column="updated_at", retention_days=730, strategy="redact",
                 redact_columns=("name", "notes"))
    h = contact_hmac("demo-dubai", "phone", "+971 50 123 4567")   # bytes, stable per hotel
    h == contact_hmac("demo-dubai", "phone", "050 123 4567")      # national: the hotel's country (AE -> 971)
    blob = seal("+971501234567"); unseal(blob)                      # AES-256-GCM

Why a per-hotel HMAC instead of a plain hash: a phone number has so little
entropy that sha256(phone) is reversible by enumeration. HMAC with a secret
pepper is not, and including the hotel slug means two hotels' guest tables
cannot be joined on the hash (PDPL: purpose limitation per controller).

Why the hotel's country is the default for a national phone: PMS exports and
front-desk entries hold "050 123 4567", while WhatsApp always delivers
"+971501234567". Both must hash to the same bytes or a guest's journey reply
would never match their reservation. An explicit default_country_code still
wins; a hotel whose country has no entry in CALLING_CODES keeps national
numbers refused (the old behaviour) rather than guessing a trunk rule.
Digits that already carry a country code but lost their "+"
("971501234567") are recognised by NATIONAL_LENGTHS and never get the code
twice; digits that fit no reading are refused (invalid_phone).

Keys: CONTACT_HMAC_PEPPER and PII_ENC_KEY (base64 or hex of 32 bytes).
Outside production, missing keys are derived from SECRET_KEY so local runs and
tests work; production refuses (NotConfigured) rather than silently using a
derivable key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Literal

from clefs.core.errors import ClefsError, NotConfigured
from clefs.core.phone import normalize_phone
from clefs.settings import get_settings

Strategy = Literal["delete", "redact"]
ContactKind = Literal["phone", "email"]

SEAL_VERSION = 1
_NONCE_LEN = 12

# ─── PII registry ───


@dataclass(frozen=True)
class PiiSpec:
    module: str
    table: str
    subject_columns: dict[str, str]  # column -> kind (phone | email | name | room | hmac | free_text | ...)
    ts_column: str
    retention_days: int
    strategy: Strategy
    redact_columns: tuple[str, ...] = ()
    where: str | None = None  # conditional retention, e.g. "provenance <> 'pms_direct'"


_registry: dict[tuple[str, str], PiiSpec] = {}
_lock = threading.Lock()
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def register_pii(
    module: str,
    table: str,
    *,
    subject_columns: dict[str, str],
    ts_column: str,
    retention_days: int,
    strategy: Strategy,
    redact_columns: tuple[str, ...] | list[str] = (),
    where: str | None = None,
) -> PiiSpec:
    """Declare a table holding personal data. Registry only: Trust's retention
    job deletes or redacts rows older than retention_days (by ts_column), and
    its DSAR export finds a subject through subject_columns."""
    if strategy not in ("delete", "redact"):
        raise ValueError("strategy must be 'delete' or 'redact'")
    if strategy == "redact" and not redact_columns:
        raise ValueError("strategy='redact' needs redact_columns")
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    for ident in (table, ts_column, *subject_columns, *redact_columns):
        if not _IDENT_RE.match(ident):
            raise ValueError(f"not a plain SQL identifier: {ident!r}")
    if where is not None and (";" in where or "--" in where):
        raise ValueError("where must be a single SQL predicate")
    spec = PiiSpec(
        module=module,
        table=table,
        subject_columns=dict(subject_columns),
        ts_column=ts_column,
        retention_days=int(retention_days),
        strategy=strategy,
        redact_columns=tuple(redact_columns),
        where=where,
    )
    with _lock:
        _registry[(module, table)] = spec
    return spec


def pii_registry() -> list[PiiSpec]:
    with _lock:
        return sorted(_registry.values(), key=lambda s: (s.module, s.table))


# ─── Keys ───


def _decode_key(raw: str) -> bytes:
    raw = raw.strip()
    candidates: list[bytes] = []
    if re.fullmatch(r"[0-9a-fA-F]{64}", raw):
        candidates.append(binascii.unhexlify(raw))
    padded = raw + "=" * (-len(raw) % 4)
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            candidates.append(decode(padded))
        except (binascii.Error, ValueError):
            continue
    for key in candidates:
        if len(key) == 32:
            return key
    raise NotConfigured("PII encryption key (32 bytes, base64 or hex)")


def _derived(label: str) -> bytes:
    return hmac.new(get_settings().SECRET_KEY.encode("utf-8"), b"clefs:dev:" + label.encode(), hashlib.sha256).digest()


def _pepper() -> bytes:
    raw = get_settings().CONTACT_HMAC_PEPPER
    if raw:
        return raw.encode("utf-8")
    if get_settings().is_production:
        raise NotConfigured("CONTACT_HMAC_PEPPER")
    return _derived("contact_hmac")


def _enc_key() -> bytes:
    raw = get_settings().PII_ENC_KEY
    if raw:
        return _decode_key(raw)
    if get_settings().is_production:
        raise NotConfigured("PII_ENC_KEY")
    return _derived("pii_enc")


# ─── Contact HMAC ───

# ISO 3166-1 alpha-2 -> ITU calling code, used only to complete a national
# phone number with its hotel's country. Deliberately limited to countries
# whose national format is "<optional 0 trunk><subscriber>", which the
# normaliser strips: Italy (landlines keep the 0 in E.164), Russia and
# Kazakhstan (trunk 8) and the NANP countries (trunk 1) are left out, so a
# hotel there keeps national numbers refused instead of hashing a wrong one.
CALLING_CODES: dict[str, str] = {
    # GCC and the wider Middle East (Clefs' markets)
    "AE": "971", "SA": "966", "QA": "974", "KW": "965", "BH": "973", "OM": "968",
    "JO": "962", "LB": "961", "EG": "20", "TR": "90", "MA": "212",
    # South Asia (the Indian pilots and the main source markets)
    "IN": "91", "PK": "92", "BD": "880", "LK": "94", "NP": "977",
    # Europe
    "GB": "44", "IE": "353", "FR": "33", "DE": "49", "NL": "31", "CH": "41", "AT": "43", "BE": "32", "ES": "34",
    # Asia-Pacific and Africa
    "AU": "61", "NZ": "64", "SG": "65", "MY": "60", "TH": "66", "PH": "63", "ID": "62", "CN": "86", "HK": "852",
    "JP": "81", "KR": "82", "ZA": "27", "KE": "254", "NG": "234",
}


# Calling code -> the lengths a national significant number (the digits after
# the country code, trunk 0 removed) may have there (ITU-T E.164 national
# plans, mobile and geographic). WHY lengths at all: "971501234567" (a UAE
# number typed without its "+", common in PMS exports and web forms) and
# "919876543210" (an Indian one) both look national to a naive rule, and
# prepending the hotel's 971 would produce valid-looking E.164 that hashes to
# a stranger. Knowing that a UAE NSN is 8 or 9 digits lets the normaliser
# tell "already international" from "national" and refuse what is neither.
NATIONAL_LENGTHS: dict[str, tuple[int, ...]] = {
    "971": (8, 9), "966": (9,), "974": (8,), "965": (7, 8), "973": (8,), "968": (8,),
    "962": (8, 9), "961": (7, 8), "20": (8, 9, 10), "90": (10,), "212": (9,),
    "91": (10,), "92": (8, 9, 10), "880": (6, 7, 8, 9, 10), "94": (9,), "977": (8, 10),
    "44": (9, 10), "353": (7, 8, 9), "33": (9,), "49": tuple(range(5, 14)), "31": (9,), "41": (9,),
    "43": tuple(range(4, 14)), "32": (8, 9), "34": (9,),
    "61": (9,), "64": (8, 9, 10), "65": (8,), "60": (8, 9, 10), "66": (8, 9), "63": (8, 9, 10),
    "62": tuple(range(7, 13)), "86": tuple(range(7, 13)), "852": (8,),
    "81": (9, 10), "82": (8, 9, 10), "27": (9,), "254": (9,), "234": (8, 9, 10),
}


def _is_national(raw: str | None) -> bool:
    """No "+" and no "00": the input may need a country code. Only decides
    whether the hotel's country is looked up; _complete_national decides
    what the digits really are."""
    v = re.sub(r"[\s\-().]", "", (raw or "").strip())
    return bool(v) and not v.startswith(("+", "00"))


def _invalid_phone(detail: str) -> ClefsError:
    # The detail never quotes the number: it reaches API responses and logs.
    return ClefsError(400, detail, "invalid_phone")


def _complete_national(v: str, cc: str) -> str:
    """E.164 for digits typed without "+" / "00", given the default calling
    code `cc`. Three readings, and only an unambiguous one is accepted:

      "0501234567"   trunk 0 + NSN          -> +971501234567
      "501234567"    NSN without the trunk  -> +971501234567
      "971501234567" already international  -> +971501234567 (no second 971)

    Digits that fit none of them ("919876543210" at a UAE hotel: 12 digits
    is no UAE NSN and it does not start with 971), or both ("4930123456" at a
    German hotel), raise invalid_phone, as every national number did before
    hotels had a default country: a refused number is visible to the caller,
    a wrong hash silently splits a guest's consent and replies in two."""
    if not v.isdigit():
        raise _invalid_phone("phone must be in international format, e.g. +971501234567")
    lengths = NATIONAL_LENGTHS.get(cc)
    if v.startswith("0"):
        nsn = v[1:]
        if nsn and not nsn.startswith("0") and (lengths is None or len(nsn) in lengths):
            return "+" + cc + nsn
        raise _invalid_phone(f"phone is not a national number for +{cc}; include the country code")
    if lengths is None:
        # No length table (an explicit default outside NATIONAL_LENGTHS): a
        # number that already starts with the code cannot be told apart from
        # a national one, so it is refused rather than guessed.
        if v.startswith(cc):
            raise _invalid_phone("phone is ambiguous without a '+'; include the country code")
        return "+" + cc + v
    as_national = len(v) in lengths
    as_international = v.startswith(cc) and (len(v) - len(cc)) in lengths
    if as_international and not as_national:
        return "+" + v
    if as_national and not as_international:
        return "+" + cc + v
    if as_national:
        raise _invalid_phone("phone is ambiguous without a '+'; include the country code")
    raise _invalid_phone(f"phone is not a national number for +{cc}; include the country code")


def hotel_calling_code(hotel_slug: str, *, cur: Any = None) -> str | None:
    """The calling code of the hotel's country ("971" for AE), or None for an
    unknown hotel or an unlisted country. Reads through core.hotels' 60 s
    cache. Pass `cur` from request code: without it this opens its own
    (read-only) db() block, which request code should not nest."""
    from clefs.core.hotels import find_hotel

    if cur is not None:
        hotel = find_hotel(cur, hotel_slug)
    else:
        from clefs import db

        with db.db(readonly=True) as c:
            hotel = find_hotel(c, hotel_slug)
    if hotel is None:
        return None
    return CALLING_CODES.get((getattr(hotel, "country", None) or "").strip().upper())


def normalize_contact(kind: ContactKind, raw: str, *, default_country_code: str | None = None) -> str:
    """Phones to E.164; emails trimmed and lowercased. With
    `default_country_code` (e.g. "971"), input without "+" / "00" goes
    through _complete_national: a national number gets the code, digits that
    already carry it do not get it twice, and anything else is refused."""
    if kind == "email":
        v = (raw or "").strip().lower()
        if "@" not in v or len(v) > 320:
            raise ClefsError(400, "not an email address", "invalid_email")
        return v
    if kind == "phone":
        v = re.sub(r"[\s\-().]", "", (raw or "").strip())
        cc = (default_country_code or "").strip().lstrip("+")
        if cc and v and not v.startswith(("+", "00")):
            v = _complete_national(v, cc)
        p = normalize_phone(v)
        if p is None:
            raise ClefsError(400, "phone is empty", "invalid_phone")
        return p
    raise ValueError(f"unknown contact kind {kind!r}")


def contact_hmac(
    hotel_slug: str,
    kind: ContactKind,
    raw: str,
    *,
    default_country_code: str | None = None,
    cur: Any = None,
) -> bytes:
    """HMAC-SHA256(pepper, "<hotel>:<kind>:<normalized>"): the only way any
    module turns a phone or email into a joinable key (04 spec F8).

    Phones become E.164. A national number ("050 123 4567") takes
    `default_country_code` when given, else the hotel's country
    (hotel_calling_code); the hotel is looked up only for national input, so
    the common "+971..." path never touches the database. Emails are trimmed
    and lowercased."""
    if kind == "phone" and default_country_code is None and _is_national(raw):
        default_country_code = hotel_calling_code(hotel_slug, cur=cur)
    normalized = normalize_contact(kind, raw, default_country_code=default_country_code)
    return hmac.new(_pepper(), f"{hotel_slug}:{kind}:{normalized}".encode("utf-8"), hashlib.sha256).digest()


def contact_ref(hotel_slug: str, kind: ContactKind, raw: str, **kw: Any) -> str:
    """contact_hmac as hex: the subject_ref stored by core.consent."""
    return contact_hmac(hotel_slug, kind, raw, **kw).hex()


# ─── Sealing (AES-256-GCM) ───


def _aesgcm():  # noqa: ANN202 - optional dependency
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - cryptography is in requirements.txt
        raise NotConfigured("PII sealing (cryptography package)") from exc
    return AESGCM(_enc_key())


def seal(text: str) -> bytes:
    """Encrypt a PII value for storage: version byte, 12-byte nonce, ciphertext+tag."""
    nonce = os.urandom(_NONCE_LEN)
    ct = _aesgcm().encrypt(nonce, text.encode("utf-8"), bytes([SEAL_VERSION]))
    return bytes([SEAL_VERSION]) + nonce + ct


def unseal(blob: bytes | memoryview | None) -> str | None:
    if blob is None:
        return None
    b = bytes(blob)
    if len(b) < 1 + _NONCE_LEN + 16 or b[0] != SEAL_VERSION:
        raise ValueError("not a sealed value")
    try:
        return _aesgcm().decrypt(b[1 : 1 + _NONCE_LEN], b[1 + _NONCE_LEN :], bytes([SEAL_VERSION])).decode("utf-8")
    except NotConfigured:
        raise
    except Exception as exc:
        raise ValueError("sealed value failed authentication") from exc


# ─── Display masks ───


def mask_phone(p: str | None) -> str | None:
    """+971501234567 -> +971••••••567, 0501234567 -> •••••••567: the calling
    code plus at most the last 3 digits (enough for staff to recognise a
    guest). The same rule as pii.mask_contact_phone, so a phone looks the same
    wherever it is shown; the old rule revealed 8 of 10 digits of a
    national-format number."""
    if not p:
        return p
    from clefs.core import pii  # lazy: pii imports this module

    return pii.mask_contact_phone(p)


def mask_email(e: str | None) -> str | None:
    if not e or "@" not in e:
        return e
    local, domain = e.split("@", 1)
    return (local[:1] + "•••") + "@" + domain


# ─── Core tables holding PII ───
# Message bodies are personal data; the outbox and inbound log are operational
# records, so after 90 days their payloads are redacted, not the rows deleted
# (delivery stats and dedupe keep working).

# sender_hmac (0014) is how DSAR finds a subject's inbound rows without the
# raw number, and it is redacted with the payload: after 90 days nothing
# (window lookups, journey reply matching) reaches back that far.
register_pii(
    "core",
    "inbound_messages",
    subject_columns={"sender": "phone_or_email", "sender_hmac": "hmac"},
    ts_column="received_at",
    retention_days=90,
    strategy="redact",
    redact_columns=("payload", "sender_hmac"),
)
register_pii(
    "core",
    "outbound_messages",
    subject_columns={"recipient": "phone_or_email"},
    ts_column="created_at",
    retention_days=90,
    strategy="redact",
    redact_columns=("payload",),
)
register_pii(
    "core",
    "ai_calls",
    subject_columns={},
    ts_column="at",
    retention_days=30,
    strategy="redact",
    redact_columns=("input", "output"),
    where="input IS NOT NULL OR output IS NOT NULL",
)
