"""Guest languages (03 spec 2.6) for JAPAC guests: Japanese, Korean and Thai
join the eight, and every list keyed by guest language covers all of them:
the greetings, the concierge-2 consent texts (0208), the WhatsApp consent
question and the safety lexicons. A Tokyo hotel gets Japan's emergency
numbers on its emergency card and in its prompt."""

from __future__ import annotations

import hashlib
import importlib.util
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import pytest

from clefs.modules.concierge import constants as C
from clefs.modules.concierge import prompt, sessions, whatsapp
from tests.modules.concierge.test_session_mint import GRANTS, ephemeral_by_default, minted  # noqa: F401 (fixtures)

ALL = ("en", "ar", "ru", "hi", "ur", "zh", "de", "fr", "ja", "ko", "th")
TOKYO_LANGS = ["en", "ja", "ko", "zh", "th", "hi", "fr", "de", "ru", "ar", "ur"]
MIGRATIONS = Path(C.__file__).parent / "migrations"


def _load(file: str):
    spec = importlib.util.spec_from_file_location(f"cc_{file[:4]}_for_tests", MIGRATIONS / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _migration_texts() -> dict[str, dict[str, str]]:
    mod = _load("0208_concierge_consent_texts_v2.py")
    assert mod.VERSION == "concierge-2"
    return mod.TEXTS


@pytest.fixture
def hotel_tokyo(cur):
    """A Tokyo hotel with the demo's eleven guest languages, concierge live."""
    from clefs.core import hotels, rooms
    from tests.modules.concierge.conftest import switch_live

    h = hotels.create_hotel(
        cur, slug="test-tokyo", name="Test Hotel Tokyo", city="Tokyo", country="JP", emirate=None,
        timezone="Asia/Tokyo", currency="JPY", locale="ja-JP", guest_languages=TOKYO_LANGS, status="pilot",
    )
    rooms.upsert_rooms(cur, h.slug, [{"room_number": "204", "floor": "2"}, {"room_number": "205", "floor": "2"}])
    switch_live(cur, h.slug, True)
    return h


# ─── The list ───


def test_japanese_korean_and_thai_are_guest_languages():
    assert C.GUEST_LANGUAGES == ALL
    assert set(C.LANGUAGE_NAMES) == set(ALL)
    assert (C.LANGUAGE_NAMES["ja"], C.LANGUAGE_NAMES["ko"], C.LANGUAGE_NAMES["th"]) == ("Japanese", "Korean", "Thai")


def test_every_per_language_map_covers_every_guest_language():
    from clefs.modules.service import safety

    for name, keys in (("prompt._PARTS", prompt._PARTS), ("prompt._INTRO", prompt._INTRO),
                       ("whatsapp.BUTTONS", whatsapp.BUTTONS), ("whatsapp.INTRO", whatsapp.INTRO),
                       ("whatsapp.DECLINED", whatsapp.DECLINED), ("whatsapp.THANKS", whatsapp.THANKS)):
        assert set(keys) == set(ALL), name
    assert set(ALL) <= set(safety.LANGUAGES)
    assert set(_migration_texts()) == set(C.CONSENT_PURPOSES)
    for purpose, by_lang in _migration_texts().items():
        assert set(by_lang) == set(ALL), purpose


# ─── Greetings ───


def test_japac_greetings_are_polite_and_place_the_name_right(cur, hotel_tokyo, clock):
    clock.freeze(datetime(2026, 10, 21, 0, 30, tzinfo=timezone.utc))  # 09:30 in Tokyo
    g = lambda lang, first=None: prompt.greeting_text(hotel_tokyo, name="Clefs", language=lang, first_name=first)  # noqa: E731
    assert g("ja", "Omar") == "Omar様、おはようございます。Test Hotel TokyoのAIコンシェルジュ、Clefsです。何かお手伝いできることはございますか？"
    assert g("ja-JP") == "おはようございます。Test Hotel TokyoのAIコンシェルジュ、Clefsです。何かお手伝いできることはございますか？"
    assert g("ko", "Omar") == "Omar님, 좋은 아침입니다! 저는 Test Hotel Tokyo의 AI 컨시어지 Clefs입니다. 무엇을 도와드릴까요?"
    assert g("th", "Omar") == "สวัสดีตอนเช้าค่ะ คุณ Omar ดิฉัน Clefs ผู้ช่วย AI ของ Test Hotel Tokyo ค่ะ มีอะไรให้ช่วยไหมคะ"
    # Chinese keeps its full-width mark with no stray space; English and French are as before.
    assert g("zh").startswith("早上好！我是Clefs，")
    assert g("en", "Omar").startswith("Good morning, Omar! I'm Clefs")
    assert g("fr").startswith("Bonjour ! Je suis Clefs")
    clock.freeze(datetime(2026, 10, 21, 11, 0, tzinfo=timezone.utc))  # 20:00 in Tokyo
    assert g("ja").startswith("こんばんは。") and g("th").startswith("สวัสดีตอนเย็นค่ะ")
    for lang in ALL:
        assert "—" not in g(lang, "Omar"), lang


# ─── Consent: concierge-2 ───

ABROAD_NAMES = {
    # The countries the transfer_abroad text must name, in each language.
    "en": ("United States", "India"), "ar": ("الولايات المتحدة", "الهند"), "ru": ("США", "Индии"),
    "hi": ("अमेरिका", "भारत"), "ur": ("امریکہ", "بھارت"), "zh": ("美国", "印度"), "de": ("USA", "Indien"),
    "fr": ("États-Unis", "Inde"), "ja": ("米国", "インド"), "ko": ("미국", "인도"), "th": ("สหรัฐอเมริกา", "อินเดีย"),
}
# What concierge-1 named and this deployment must not: the UAE and Singapore.
UAE_SINGAPORE = (
    "UAE", "Emirates", "Singapore", "الإمارات", "سنغافورة", "ОАЭ", "Эмират", "Сингапур", "अमीरात", "सिंगापुर",
    "امارات", "سنگاپور", "阿联酋", "新加坡", "VAE", "Emirate", "Singapur", "EAU", "Émirats", "Singapour",
    "首長国", "シンガポール", "아랍에미리트", "싱가포르", "เอมิเรตส์", "สิงคโปร์",
)


def test_concierge_2_is_seeded_in_every_guest_language_and_concierge_1_stays(cur):
    assert C.CONSENT_TEXT_VERSION == "concierge-2"
    cur.execute("SELECT purpose, language, body, sha256 FROM consent_texts WHERE hotel_slug IS NULL AND version = 'concierge-2'")
    rows = {(r["purpose"], r["language"]): r for r in cur.fetchall()}
    assert set(rows) == set(product(C.CONSENT_PURPOSES, ALL))
    texts = _migration_texts()
    for (purpose, lang), r in rows.items():
        assert r["body"] == texts[purpose][lang]
        assert r["sha256"] == hashlib.sha256(r["body"].encode("utf-8")).hexdigest()
    # The old wording is evidence earlier grants point at: kept, not retired.
    cur.execute("SELECT purpose, language FROM consent_texts WHERE hotel_slug IS NULL AND version = 'concierge-1' "
                "AND retired_at IS NULL")
    assert {(r["purpose"], r["language"]) for r in cur.fetchall()} == set(product(C.CONSENT_PURPOSES, ("en", "ar")))


def test_concierge_2_wording_is_truthful_and_neutral():
    texts = _migration_texts()
    banned_vendors = [["open", "ai"], ["chat", "g", "pt"], ["g", "pt"]]  # built from pieces, never spelled here
    for purpose, by_lang in texts.items():
        for lang, body in by_lang.items():
            where = f"{purpose}/{lang}"
            assert "—" not in body, where
            for name in UAE_SINGAPORE:
                assert name not in body, f"{where} names {name}"
            for parts in banned_vendors:
                assert "".join(parts) not in body.lower(), where
            if purpose in C.REQUIRED_CONSENTS:
                assert "{hotel}" in body and "Gemini" in body and "Google Cloud" in body, where
    for lang, (us, india) in ABROAD_NAMES.items():
        assert us in texts["transfer_abroad"][lang] and india in texts["transfer_abroad"][lang], lang
    # concierge-1 did name them; where its facts still hold (the transcript
    # kept 12 months, analytics without a name) English and Arabic keep its words.
    old = _load("0202_concierge_consent_texts.py")
    assert "Singapore" in old.TEXTS["transfer_abroad"]["en"] and old.VERSION == "concierge-1"
    for purpose, lang in product(("recording", "ai_analytics"), ("en", "ar")):
        assert texts[purpose][lang] == old.TEXTS[purpose][lang], (purpose, lang)


def test_the_sheet_serves_concierge_2_in_the_guests_language(cur, hotel_tokyo):
    for lang in (*ALL, "ja-JP", "ko-KR", "th-TH"):
        texts = sessions.consent_texts(cur, hotel_tokyo.slug, lang)
        assert set(texts) == set(C.CONSENT_PURPOSES), lang
        base = lang.split("-")[0]
        for purpose, t in texts.items():
            assert (t["version"], t["language"]) == ("concierge-2", base), (lang, purpose)
            assert t["required"] is (purpose in C.REQUIRED_CONSENTS)
            assert t["required_for_voice"] is (purpose in C.VOICE_REQUIRED_CONSENTS)
            assert "{hotel}" not in t["text"]
        assert "Test Hotel Tokyo" in texts["transfer_abroad"]["text"], lang
    # A language with no wording reads English, as before.
    assert {t["language"] for t in sessions.consent_texts(cur, hotel_tokyo.slug, "es").values()} == {"en"}


def test_a_japanese_guest_agrees_to_the_japanese_wording(client, cur, hotel_tokyo, minted):  # noqa: F811
    body = {"hotel": hotel_tokyo.slug, "target": {"room": "204"}, "lang": "ja", "mode": "voice", "consents": GRANTS}
    r = client.post("/public/concierge/sessions", json=body)
    assert r.status_code == 200, r.text
    cur.execute(
        "SELECT c.purpose, c.text_version, t.language FROM consents c JOIN consent_texts t ON t.id = c.text_id "
        "WHERE c.hotel_slug = %s",
        (hotel_tokyo.slug,),
    )
    rows = cur.fetchall()
    assert {r["purpose"] for r in rows} == set(GRANTS)
    assert {(r["text_version"], r["language"]) for r in rows} == {("concierge-2", "ja")}


# ─── A Tokyo hotel ───


def test_tokyo_context_speaks_japanese_and_carries_japans_numbers(client, hotel_tokyo):
    body = client.get(f"/public/stay/{hotel_tokyo.slug}/204/context", params={"lang": "ja"}).json()
    assert body["language"] == "ja" and body["consent_version"] == "concierge-2"
    assert body["hotel"]["languages"] == TOKYO_LANGS
    assert "AIコンシェルジュ" in body["greeting_text"]
    assert body["consent_texts"]["ai_processing"]["language"] == "ja"
    assert body["emergency_numbers"] == [
        {"number": "119", "kind": "fire_ambulance"}, {"number": "110", "kind": "police"}, {"number": "118", "kind": "coast_guard"},
    ]
    for lang in ("ko", "th"):
        other = client.get(f"/public/stay/{hotel_tokyo.slug}/204/context", params={"lang": lang}).json()
        assert other["language"] == lang and other["consent_texts"]["transfer_abroad"]["language"] == lang


def test_tokyo_prompt_names_japans_numbers_and_the_polite_register(cur, hotel_tokyo):
    from clefs.modules.concierge import api

    spec = api.build_surface_session(hotel_tokyo.slug, surface="web_room", room="204", language="ja", cur=cur)
    text = spec.instructions
    assert "The guest chose Japanese" in text and "desu/masu" in text
    assert "119 (fire and ambulance) for anything medical" in text and "110 (police)" in text and "118 coast guard" in text
    assert "999" not in text and "—" not in text


def test_tokyo_emergency_card_leads_with_the_number_for_the_kind(cur, hotel_tokyo):
    from clefs.modules.concierge import tools

    assert [n["number"] for n in tools.emergency_numbers(cur, hotel_tokyo.slug, "medical")] == ["119", "110", "118"]
    assert tools.emergency_numbers(cur, hotel_tokyo.slug, "fire")[0]["number"] == "119"
    assert tools.emergency_numbers(cur, hotel_tokyo.slug, "violence")[0]["number"] == "110"


def test_a_phone_line_set_to_a_japac_language_opens_in_it():
    from clefs.modules.voice import calls

    for lang in ("ja", "ko", "th"):
        assert calls.LANGUAGE_NAMES[lang] == C.LANGUAGE_NAMES[lang]
    block = calls.phone_block(hotel_name="Test Hotel Tokyo", language="ja-JP", room="204", room_from_extension=True,
                              outside_line_prefix=None, transport="sideband")
    assert block.splitlines()[1].startswith("Open in Japanese:")


# ─── WhatsApp consent question ───


def test_whatsapp_question_fits_in_every_language(cur, hotel_tokyo):
    for lang in ALL:
        yes, no = whatsapp.BUTTONS[lang]
        assert 0 < len(yes) <= 20 and 0 < len(no) <= 20, lang  # WhatsApp's reply button limit
        body = whatsapp.consent_body(cur, hotel_tokyo.slug, lang)
        assert len(body) < 1000, lang  # never cut by consent_body's 1000-character cap
        texts = sessions.consent_texts(cur, hotel_tokyo.slug, lang)
        assert texts["ai_processing"]["text"] in body and texts["transfer_abroad"]["text"] in body, lang
        assert "Test Hotel Tokyo" in body and "—" not in body


@pytest.mark.parametrize(("text", "lang"), [
    ("チェックアウトは何時ですか？", "ja"),
    ("部屋が暑いです", "ja"),
    ("체크아웃은 몇 시예요?", "ko"),
    ("เช็กเอาต์กี่โมง", "th"),
    ("退房时间是几点？", "zh"),
    ("Во сколько выезд?", "ru"),
    ("चेकआउट कितने बजे है?", "hi"),
    ("میرا کمرہ گرم ہے", "ur"),
    ("غرفتي حارة", "ar"),
    ("What time is checkout?", "en"),
    ("Um wie viel Uhr ist der Check-out?", "en"),  # Latin script reads as English
    ("", "en"),
])
def test_whatsapp_guesses_the_language_from_the_script(text, lang):
    assert whatsapp.guess_language(text) == lang
