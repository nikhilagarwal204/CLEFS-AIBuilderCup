"""
0651_guests_profile_consent_text: profile_memory wording that discloses the
health data a profile stores (03 spec 2.7; PDPL: consent must be informed).

gst.v1 (0675) said only "remember my preferences", yet a profile keeps
allergies in preferences.health, which is health data. A guest cannot give
informed consent to what the text does not name, so gst.v2 names it, and
gst.v1 is retired (retired_at) so core.consent.current_consent_text serves
gst.v2 to every new capture. Consents already recorded against gst.v1 keep
their text row: evidence is never rewritten, only superseded.

Order independence. The runner applies every unapplied version in ascending
order, so on a fresh database this file runs BEFORE 0675, and on a database
that already ran 0675 it runs after. Both paths must end the same way:
- gst.v1 profile_memory rows are inserted here (exact 0675 bodies, same
  sha256) already retired when absent; 0675 then finds them and skips them;
- when 0675 already inserted them, they are retired here.
The other gst.v1 purposes are untouched.
"""

import hashlib

PURPOSE = "profile_memory"
OLD_VERSION = "gst.v1"
NEW_VERSION = "gst.v2"

# Byte-identical to 0675's profile_memory texts (their sha256 must match).
OLD_TEXTS: dict[str, str] = {
    "en": "Remember my preferences at {hotel} for my next stay.",
    "ar": "تذكروا تفضيلاتي في {hotel} لإقامتي القادمة.",
}

NEW_TEXTS: dict[str, str] = {
    "en": (
        "Remember my preferences at {hotel} for my future stays, including any allergies or dietary needs "
        "I share, which are health information. I can ask {hotel} to forget them at any time."
    ),
    "ar": (
        "تذكروا تفضيلاتي في {hotel} لإقاماتي القادمة، بما في ذلك أي حساسية أو احتياجات غذائية أشاركها، "
        "وهي معلومات صحية. يمكنني أن أطلب من {hotel} حذفها في أي وقت."
    ),
}


def _sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _insert_if_absent(cur, version: str, language: str, body: str, *, retired: bool) -> None:
    cur.execute(
        """
        SELECT id FROM consent_texts
        WHERE hotel_slug IS NULL AND purpose = %s AND version = %s AND language = %s
        """,
        (PURPOSE, version, language),
    )
    if cur.fetchone() is not None:
        return  # immutable: an existing version is never rewritten
    cur.execute(
        """
        INSERT INTO consent_texts (hotel_slug, purpose, version, language, body, sha256, retired_at)
        VALUES (NULL, %s, %s, %s, %s, %s, CASE WHEN %s THEN NOW() END)
        """,
        (PURPOSE, version, language, body, _sha(body), retired),
    )


def up(cur):
    for language, body in OLD_TEXTS.items():
        _insert_if_absent(cur, OLD_VERSION, language, body, retired=True)
    cur.execute(
        """
        UPDATE consent_texts SET retired_at = NOW()
        WHERE hotel_slug IS NULL AND purpose = %s AND version = %s AND retired_at IS NULL
        """,
        (PURPOSE, OLD_VERSION),
    )
    for language, body in NEW_TEXTS.items():
        _insert_if_absent(cur, NEW_VERSION, language, body, retired=False)
