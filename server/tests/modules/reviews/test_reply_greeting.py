"""Template reply salutations (QA OWN-10): a reviewer shown as "Mr T."
is not greeted "Dear Mr,", and an Arabic reply never carries a Latin
title ("عزيزي Ms،")."""

from __future__ import annotations

import pytest

from clefs.modules.reviews import replies


@pytest.mark.parametrize("author, en, ar", [
    ("Mr T. (DEMO)", "Dear guest,", "عزيزي الضيف،"),
    ("Ms T. (DEMO)", "Dear guest,", "عزيزتي الضيفة،"),
    ("Mrs Khan", "Dear Mrs Khan,", "عزيزتي الضيفة،"),
    ("Omar Haddad (DEMO)", "Dear Omar,", "عزيزي Omar،"),
    ("omar", "Dear Omar,", "عزيزي Omar،"),
    ("عمر", "Dear عمر,", "عزيزي عمر،"),
    ("J.", "Dear guest,", "عزيزي الضيف،"),
    (None, "Dear guest,", "عزيزي الضيف،"),
])
def test_greeting(author, en, ar):
    assert replies._greeting(author, arabic=False) == en
    assert replies._greeting(author, arabic=True) == ar


def test_template_uses_the_greeting_and_no_latin_title_in_arabic():
    text, lang = replies.template(language="ar", negative=False, author="Ms T. (DEMO)", hotel_name="Deira Crest",
                                  signer="The team", contact="")
    assert lang == "ar" and text.startswith("عزيزتي الضيفة،") and "Ms" not in text
    text, lang = replies.template(language="en", negative=True, author="Mr T. (DEMO)", hotel_name="Deira Crest",
                                  signer="The team", contact="care@example.com")
    assert lang == "en" and text.startswith("Dear guest,") and "Dear Mr," not in text
