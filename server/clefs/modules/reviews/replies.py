"""
Reply drafts (03 spec 4.1; 04 spec 1.4 autonomy rows).

Policy, enforced here whoever drafted the text:
- same language as the review, under 120 words, no em dash, no admission of
  liability, no public compensation offer, and an offline contact on every
  negative reply;
- the default path is a Copy button; posting through GBP needs an explicit
  per-reply approval click, or the autonomy dial when the GBP source carries
  auto_reply_consent AND the review is 4-5 stars with no negative theme and
  severity no higher than low. 1-3 star replies always need approval.

A model draft that fails a check after sanitising is replaced by the
template, so the console never offers a reply that breaks the policy.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from clefs import ai
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.modules.reviews import taxonomy

MAX_WORDS = 119  # "under 120 words"
TASK = "reviews.draft_reply"
EM_DASH = "\u2014"
EN_DASH = "\u2013"

# ─── Policy checks ───

_LIABILITY = re.compile(
    r"\b(our fault|we are (?:fully )?(?:liable|responsible)|we accept (?:full )?(?:responsibility|liability)|"
    r"negligen\w*|admit\w*|legal(?:ly)?|lawsuit|court)\b|خطأنا|مسؤوليتنا|نعترف|إهمال",
    re.IGNORECASE,
)
_COMPENSATION = re.compile(
    r"\b(refund\w*|compensat\w*|reimburs\w*|free (?:night|stay|upgrade|meal|breakfast)|complimentary|"
    r"discount\w*|voucher\w*|upgrade\w*|credit (?:note|back)|money back|on us)\b|تعويض|استرداد|مجان\w*|خصم|قسيمة",
    re.IGNORECASE,
)


def word_count(text: str) -> int:
    return len((text or "").split())


def sanitize(text: str) -> str:
    """Dashes the house style bans become commas or hyphens; whitespace is
    collapsed; the text is cut at a sentence end under the word limit."""
    t = (text or "").replace(f" {EM_DASH} ", ", ").replace(EM_DASH, ", ").replace(f" {EN_DASH} ", ", ")
    t = t.replace(EN_DASH, "-")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    words = t.split()
    if len(words) > MAX_WORDS:
        cut = " ".join(words[:MAX_WORDS])
        end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("؟"), cut.rfind("?"))
        t = cut[: end + 1] if end > len(cut) // 2 else cut.rstrip(",;:") + "."
    return t


def check(text: str, *, language: str | None, review_language: str | None, negative: bool,
          contact: str | None) -> dict:
    """{ok, words, em_dash, liability[], compensation[], language_ok, has_contact}."""
    t = text or ""
    lang_ok = True
    if review_language:
        want_ar = review_language.startswith("ar")
        lang_ok = taxonomy.looks_arabic(t) == want_ar and (language or review_language)[:2] == review_language[:2]
    has_contact = (not negative) or bool(contact and contact.lower() in t.lower()) or bool(
        re.search(r"contact|email|call|تواصل|اتصل", t, re.IGNORECASE))
    out = {
        "words": word_count(t),
        "em_dash": EM_DASH in t,
        "liability": sorted({m.group(0).lower() for m in _LIABILITY.finditer(t)}),
        "compensation": sorted({m.group(0).lower() for m in _COMPENSATION.finditer(t)}),
        "language_ok": lang_ok,
        "has_contact": has_contact,
    }
    out["ok"] = (out["words"] < 120 and not out["em_dash"] and not out["liability"] and not out["compensation"]
                 and lang_ok and has_contact)
    return out


# ─── Templates (AI off, or a model draft that failed the checks) ───


# Titles a review site shows in place of a first name ("Mr T.", "Mrs Khan").
# WHY: the first token was taken as the name, so drafts opened "Dear Mr,"
# and the Arabic one "عزيزي Ms،" (QA OWN-10).
_HONORIFICS = {"mr", "mrs", "ms", "miss", "mx", "dr", "sir", "madam", "madame", "prof", "sheikh", "sheikha",
               "herr", "frau", "mme", "mlle", "sr", "sra", "srta", "signor", "signora"}
_FEMININE = {"mrs", "ms", "miss", "madam", "madame", "sheikha", "frau", "mme", "mlle", "sra", "srta", "signora"}


def _name_parts(author: str | None) -> tuple[str | None, str | None, str | None]:
    """(honorific, first name, surname) of a reviewer's display name. Only
    whole alphabetic words count: an initial ("T.") or a tag ("(DEMO)") is
    never a name."""
    words = [w.strip(".,;:") for w in re.split(r"\s+", (author or "").strip()) if w and not w.startswith("(")]
    words = [w for w in words if w]
    title = None
    if words and words[0].lower() in _HONORIFICS:
        title, words = words[0], words[1:]

    def ok(w: str | None) -> str | None:
        # "omar" reads as "Omar"; a Latin case change is a no-op for Arabic.
        return (w[:1].upper() + w[1:]) if w and w.isalpha() and len(w) > 1 else None

    if title:
        # After a title the remaining word is a surname ("Mrs Khan").
        return title, None, ok(words[-1] if words else None)
    return None, ok(words[0] if words else None), None


def _greeting(author: str | None, *, arabic: bool) -> str:
    """"Dear Omar," / "Dear Mrs Khan," / "Dear guest," and the Arabic
    equivalents. Arabic never carries a Latin title: with one, it greets
    the guest generically (feminine for a feminine title)."""
    title, first, surname = _name_parts(author)
    if arabic:
        if first:
            return f"عزيزي {first}،"
        return "عزيزتي الضيفة،" if title and title.lower() in _FEMININE else "عزيزي الضيف،"
    if first:
        return f"Dear {first},"
    if title and surname:
        return f"Dear {title.capitalize()} {surname},"
    return "Dear guest,"


def template(*, language: str | None, negative: bool, author: str | None, hotel_name: str, signer: str,
             contact: str, theme_label: str | None = None) -> tuple[str, str]:
    """(text, language). Arabic for an Arabic review, else English."""
    if (language or "").startswith("ar"):
        greet = _greeting(author, arabic=True)
        if negative:
            reach = f"على {contact}" if contact else "مباشرة"
            body = (f"شكرا لمشاركتنا ملاحظاتك. يؤسفنا أن إقامتك لم تكن كما توقعت، ونود معرفة المزيد. "
                    f"يرجى التواصل معنا {reach} لنتحدث معك شخصيا.")
        else:
            body = f"شكرا جزيلا على كلماتك الطيبة. يسعدنا أنك استمتعت بإقامتك في {hotel_name} ونتطلع لاستقبالك مجددا."
        return f"{greet}\n{body}\n{signer}", "ar"
    greet = _greeting(author, arabic=False)
    if negative:
        about = f" about {theme_label.lower()}" if theme_label else ""
        reach = f"at {contact}" if contact else "directly"
        body = (f"Thank you for taking the time to share your feedback{about}. We are sorry your stay did not "
                f"meet your expectations, and we would like to hear more. Please contact us {reach} so we can "
                f"speak with you personally.")
    else:
        body = (f"Thank you for your kind review. We are delighted you enjoyed your stay at {hotel_name} "
                f"and look forward to welcoming you back.")
    return f"{greet}\n{body}\n{signer}", "en"


# ─── Drafting ───


class ReplyDraft(BaseModel):
    text: str = Field(description="The reply, plain text, under 120 words, in the review's language")
    language: str = Field(description="ISO 639-1 code of the reply")


def _system(hotel_name: str, style: Any, negative: bool) -> str:
    contact = style.contact or "the hotel directly"
    signer = style.signer or f"The team at {hotel_name}"
    rules = [
        f"You write the public reply of {hotel_name} to one guest review.",
        "Write in the same language as the review.",
        "Under 120 words. Plain text. Never use the em dash character.",
        f"Tone: {style.tone}. Thank the guest and refer to one or two specifics they mentioned.",
        "Never admit fault, liability or negligence, and never mention legal matters.",
        "Never offer or mention refunds, compensation, discounts, upgrades, vouchers or anything free.",
        "Never repeat the guest's personal details or room number.",
        f"End with the signature: {signer}",
    ]
    if negative:
        rules.insert(5, f"Apologise that the stay fell short and invite the guest to contact {contact} offline.")
    return " ".join(rules)


def compose(cur: Any, hotel: Any, review: dict, derived: dict | None, style: Any) -> dict:
    """{text, language, checks, model}. Tries the model, sanitises, checks;
    falls back to the template when AI is off or the draft breaks policy."""
    negative = bool(derived and derived.get("negative"))
    signer = style.signer or f"The team at {hotel.name}"
    lang = review.get("language") or ("ar" if taxonomy.looks_arabic(review.get("raw_text")) else "en")
    neg_themes = [t for t in (derived or {}).get("themes") or [] if t.get("polarity") == "negative"]
    theme_label = taxonomy.THEME_LABELS.get(neg_themes[0]["key"]) if neg_themes else None
    model = "template"
    text, out_lang = None, lang
    if review.get("raw_text") or review.get("title"):
        prompt = (f"Rating: {review.get('rating_norm_5') or 'none'} of 5\nLanguage: {lang}\n"
                  f"Title: {review.get('title') or ''}\nReview:\n{review.get('raw_text') or ''}")
        try:
            d = ai.structured(TASK, system=_system(hotel.name, style, negative), input=prompt, schema=ReplyDraft,
                              hotel_slug=hotel.slug, max_output_tokens=600)
            text, out_lang, model = sanitize(d.text), (d.language or lang)[:12], "llm"
        except (ai.AIDisabled, ai.AIError):
            text = None
    checks = check(text, language=out_lang, review_language=lang, negative=negative,
                   contact=style.contact) if text else {"ok": False}
    if not checks.get("ok"):
        rejected = checks if text else None
        text, out_lang = template(language=lang, negative=negative, author=review.get("author_display"),
                                  hotel_name=hotel.name, signer=signer, contact=style.contact,
                                  theme_label=theme_label)
        model = "template"
        checks = check(text, language=out_lang, review_language=lang if lang[:2] in ("en", "ar") else None,
                       negative=negative, contact=style.contact)
        if lang[:2] not in ("en", "ar"):
            checks["language_ok"] = False  # the template speaks en/ar only: flag it for the reviewer
        if rejected:
            checks["model_draft_rejected"] = {k: v for k, v in rejected.items() if k != "ok"}
    return {"text": text, "language": out_lang, "checks": checks, "model": model}


# ─── Storage ───

REPLY_COLS = ("id, hotel_slug, review_id, draft_text, language, status, needs_approval, edited, checks, action_id, "
              "approved_by, approved_at, posted_at, posted_via, platform_reply_id, model, created_at, updated_at")


def live_reply(cur: Any, hotel_slug: str, review_id: int) -> dict | None:
    cur.execute(f"SELECT {REPLY_COLS} FROM rev_replies WHERE hotel_slug = %s AND review_id = %s "
                "AND status <> 'discarded'", (hotel_slug, review_id))
    row = cur.fetchone()
    return dict(row) if row else None


def get_reply(cur: Any, hotel_slug: str, reply_id: int, *, for_update: bool = False) -> dict:
    cur.execute(f"SELECT {REPLY_COLS} FROM rev_replies WHERE hotel_slug = %s AND id = %s"
                + (" FOR UPDATE" if for_update else ""), (hotel_slug, reply_id))
    row = cur.fetchone()
    if not row:
        raise ClefsError(404, "Reply not found", "reply_not_found")
    return dict(row)


def save_draft(cur: Any, hotel_slug: str, review_id: int, draft: dict, *, needs_approval: bool,
               replace_edited: bool = False) -> dict:
    """Write the live draft. A reply already approved, copied or posted is
    returned untouched, and so is a person's edit unless replace_edited."""
    cur_row = live_reply(cur, hotel_slug, review_id)
    if cur_row and (cur_row["status"] != "draft" or (cur_row["edited"] and not replace_edited)):
        return cur_row
    if cur_row:
        cur.execute(
            f"""UPDATE rev_replies SET draft_text = %s, language = %s, checks = %s, model = %s, needs_approval = %s,
                       edited = FALSE, updated_at = now() WHERE id = %s RETURNING {REPLY_COLS}""",
            (draft["text"], draft["language"], Json(draft["checks"]), draft["model"], needs_approval, cur_row["id"]),
        )
    else:
        cur.execute(
            f"""INSERT INTO rev_replies (hotel_slug, review_id, draft_text, language, checks, model, needs_approval)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {REPLY_COLS}""",
            (hotel_slug, review_id, draft["text"], draft["language"], Json(draft["checks"]), draft["model"],
             needs_approval),
        )
    return dict(cur.fetchone())


# ─── Transitions ───

EDITABLE = ("draft", "approved")


def _review_for(cur: Any, hotel_slug: str, reply: dict) -> dict:
    cur.execute("SELECT r.*, s.kind AS source_kind, s.auto_reply_consent FROM rev_reviews r "
                "LEFT JOIN rev_sources s ON s.id = r.source_id WHERE r.hotel_slug = %s AND r.id = %s",
                (hotel_slug, reply["review_id"]))
    return dict(cur.fetchone())


def _need(reply: dict, allowed: tuple[str, ...]) -> None:
    if reply["status"] not in allowed:
        raise ClefsError(409, f"Reply is {reply['status']}", "illegal_transition")


def edit(cur: Any, hotel_slug: str, reply_id: int, text: str, *, negative: bool, contact: str | None) -> dict:
    """A person's edit: sanitised and re-checked, and back to draft (an edit
    after approval needs approving again)."""
    reply = get_reply(cur, hotel_slug, reply_id, for_update=True)
    _need(reply, EDITABLE)
    review = _review_for(cur, hotel_slug, reply)
    clean = sanitize(text)
    if not clean:
        raise ClefsError(400, "Reply text is empty", "empty_reply")
    checks = check(clean, language=reply["language"], review_language=review.get("language"), negative=negative,
                   contact=contact)
    cur.execute(
        f"""UPDATE rev_replies SET draft_text = %s, checks = %s, edited = TRUE, status = 'draft', approved_by = NULL,
                   approved_at = NULL, updated_at = now() WHERE id = %s RETURNING {REPLY_COLS}""",
        (clean, Json(checks), reply_id),
    )
    return dict(cur.fetchone())


def _policy_block(reply: dict) -> None:
    """A draft that breaks the hard rules (em dash, liability, compensation,
    length) cannot be approved; language and contact are warnings only."""
    c = reply.get("checks") or {}
    hard = [k for k in ("liability", "compensation") if c.get(k)]
    if c.get("em_dash"):
        hard.append("em_dash")
    if (c.get("words") or 0) >= 120:
        hard.append("too_long")
    if hard:
        raise ClefsError(422, "The reply breaks the reply policy: " + ", ".join(hard), "reply_policy")


def approve(cur: Any, hotel_slug: str, reply_id: int, *, user_id: int | None) -> dict:
    reply = get_reply(cur, hotel_slug, reply_id, for_update=True)
    if reply["status"] == "approved":
        return reply
    _need(reply, ("draft",))
    _policy_block(reply)
    cur.execute(
        f"""UPDATE rev_replies SET status = 'approved', approved_by = %s, approved_at = %s, updated_at = now()
            WHERE id = %s RETURNING {REPLY_COLS}""",
        (user_id, clock.now(), reply_id),
    )
    return dict(cur.fetchone())


def _answered(cur: Any, review_id: int) -> None:
    cur.execute("UPDATE rev_reviews SET has_owner_response = TRUE, owner_response_at = COALESCE(owner_response_at, %s) "
                "WHERE id = %s", (clock.now(), review_id))


def mark_copied(cur: Any, hotel_slug: str, reply_id: int, *, user_id: int | None) -> dict:
    """The default path: a person copied the text and posted it by hand."""
    reply = get_reply(cur, hotel_slug, reply_id, for_update=True)
    if reply["status"] in ("copied", "posted"):
        return reply
    _need(reply, EDITABLE)
    _policy_block(reply)
    now = clock.now()
    cur.execute(
        f"""UPDATE rev_replies SET status = 'copied', posted_via = 'copy', posted_at = %s,
                   approved_by = COALESCE(approved_by, %s), approved_at = COALESCE(approved_at, %s),
                   updated_at = now() WHERE id = %s RETURNING {REPLY_COLS}""",
        (now, user_id, now, reply_id),
    )
    row = dict(cur.fetchone())
    _answered(cur, reply["review_id"])
    return row


def discard(cur: Any, hotel_slug: str, reply_id: int) -> dict:
    reply = get_reply(cur, hotel_slug, reply_id, for_update=True)
    _need(reply, EDITABLE)
    cur.execute(f"UPDATE rev_replies SET status = 'discarded', updated_at = now() WHERE id = %s RETURNING {REPLY_COLS}",
                (reply_id,))
    return dict(cur.fetchone())


# ─── Posting through GBP ───


def gbp_target(cur: Any, hotel_slug: str, review: dict) -> str | None:
    """The GBP review name to reply to, or None when this review cannot be
    posted through the API (not a GBP-synced Google review, or Google is not
    connected): the Copy path then applies."""
    from clefs.core import oauth

    ref = review.get("platform_ref") or ""
    if review.get("platform") != "google" or review.get("source_kind") != "gbp" or "/reviews/" not in ref:
        return None
    conn = oauth.get_connection(cur, hotel_slug, "google_business")
    if not conn or conn.get("status") != "active":
        return None
    return ref


def post(cur: Any, hotel_slug: str, reply_id: int, *, user_id: int | None, via: str = "gbp") -> dict:
    """updateReply on Google. `via` 'gbp' is a person's click, 'dial' the
    autonomy dial (replies.py callers check the gate before either)."""
    from clefs.modules.reviews.sources import gbp

    reply = get_reply(cur, hotel_slug, reply_id, for_update=True)
    if reply["status"] == "posted":
        return reply
    _need(reply, EDITABLE)
    _policy_block(reply)
    review = _review_for(cur, hotel_slug, reply)
    target = gbp_target(cur, hotel_slug, review)
    if not target:
        raise ClefsError(409, "This review cannot be posted through Google; copy the reply instead", "not_postable")
    res = gbp.post_reply(cur, hotel_slug, target, reply["draft_text"])
    now = clock.now()
    cur.execute(
        f"""UPDATE rev_replies SET status = 'posted', posted_via = %s, posted_at = %s, platform_reply_id = %s,
                   approved_by = COALESCE(approved_by, %s), approved_at = COALESCE(approved_at, %s),
                   updated_at = now() WHERE id = %s RETURNING {REPLY_COLS}""",
        (via, now, f"{target}/reply" if res is not None else None, user_id, now, reply_id),
    )
    row = dict(cur.fetchone())
    _answered(cur, reply["review_id"])
    return row
