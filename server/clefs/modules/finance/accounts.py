"""
The hotel's chart of accounts as its books name it (fin_coa_accounts).

An account is keyed by (hotel, source system, code, normalised name). Upload
formats share one source system ("upload"), so an XLSX month and a CSV month
of the same books reuse one account; Tally, Xero, QuickBooks and Zoho keep
their own. A coded account renamed in the books keeps its row (same code):
the rename is reported so mapping proposes a new version (04 spec 6.4).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

SOURCE_SYSTEM = {"upload_xlsx": "upload", "upload_csv": "upload", "upload_pdf": "upload",
                 "tally_upload": "tally", "xero": "xero", "qbo": "qbo", "zoho": "zoho"}

_AR = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")
_DIACRITICS = re.compile(r"[ً-ٰٟـ]")  # harakat, superscript alef, tatweel
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


# ─── Names ───


def norm_name(name: str | None) -> str:
    """Lowercase, NFKC, Arabic letter variants folded (alef forms, ta
    marbuta, alef maqsura), harakat dropped, punctuation to spaces, spaces
    collapsed. "Room Revenue / إيرادات الغرف" -> "room revenue ايرادات الغرف"."""
    s = unicodedata.normalize("NFKC", str(name or "")).lower()
    s = _DIACRITICS.sub("", s)
    s = s.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي"}))
    s = s.replace("&", " and ")
    s = _PUNCT.sub(" ", s).replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def language_of(name: str | None) -> str:
    s = str(name or "")
    ar, en = bool(_AR.search(s)), bool(_LATIN.search(s))
    return "mixed" if ar and en else "ar" if ar else "en"


# ─── Upsert ───


def upsert_account(cur: Any, hotel_slug: str, *, source: str, code: str | None, name: str,
                   group_raw: str | None = None, type_raw: str | None = None,
                   batch_id: int | None = None) -> tuple[int, bool]:
    """(account_id, changed). `changed` is True when an existing account's
    name or group differs from what the books now say: its mapping then needs
    a new version and a fresh confirmation."""
    system = SOURCE_SYSTEM.get(source, source)
    code = (str(code).strip() or None) if code is not None else None
    nn = norm_name(name)
    if not nn:
        raise ValueError("account name is empty")
    cur.execute(
        """SELECT * FROM fin_coa_accounts WHERE hotel_slug = %s AND source_system = %s
             AND code IS NOT DISTINCT FROM %s AND name_norm = %s""",
        (hotel_slug, system, code, nn),
    )
    row = cur.fetchone()
    if row is None and code is not None:
        # Same code, new name: the books renamed the account.
        cur.execute(
            """SELECT * FROM fin_coa_accounts WHERE hotel_slug = %s AND source_system = %s AND code = %s
               ORDER BY last_batch_id DESC NULLS LAST, id DESC LIMIT 1""",
            (hotel_slug, system, code),
        )
        row = cur.fetchone()
    if row is None:
        cur.execute(
            """INSERT INTO fin_coa_accounts (hotel_slug, source_system, code, name_raw, name_norm, language,
                   parent_group_raw, type_raw, first_batch_id, last_batch_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (hotel_slug, system, code, str(name).strip(), nn, language_of(name), group_raw, type_raw,
             batch_id, batch_id),
        )
        return int(cur.fetchone()["id"]), False
    changed = row["name_norm"] != nn or (group_raw is not None and norm_name(row["parent_group_raw"])
                                          != norm_name(group_raw))
    cur.execute(
        """UPDATE fin_coa_accounts SET name_raw = %s, name_norm = %s, language = %s,
               parent_group_raw = COALESCE(%s, parent_group_raw), type_raw = COALESCE(%s, type_raw),
               last_batch_id = COALESCE(%s, last_batch_id)
           WHERE id = %s""",
        (str(name).strip(), nn, language_of(name), group_raw, type_raw, batch_id, row["id"]),
    )
    return int(row["id"]), changed


def get_account(cur: Any, hotel_slug: str, account_id: int) -> dict | None:
    cur.execute("SELECT * FROM fin_coa_accounts WHERE hotel_slug = %s AND id = %s", (hotel_slug, account_id))
    return cur.fetchone()
