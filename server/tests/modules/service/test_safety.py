"""Safety classifier (03 spec 0.5, 1.5, 1.9): lexicon hits and false-positive
fixtures for every guest language, negative guards, script detection, the
upgrade-only LLM layer, the effect plan and the api.classify_safety door."""

from __future__ import annotations

import pytest

from clefs.modules.service import safety

# ─── Layer 1: hits in every language ───

HITS = [
    # (language, text, kind)
    ("en", "There is smoke coming out of the socket", "fire"),
    ("en", "my husband collapsed and is not breathing", "medical"),
    ("en", "a man in the corridor has a knife", "violence"),
    ("en", "we are stuck in the lift", "lift_entrapment"),
    ("en", "I can't find my son anywhere", "missing_child"),
    ("en", "someone stole my watch from the room", "theft"),
    ("en", "a staff member touched me inappropriately", "harassment"),
    ("en", "I lost my passport", "lost_document"),
    ("en", "I will call my bank and dispute the charge", "chargeback"),
    ("en", "I want a full refund", "refund"),
    ("ar", "يوجد حريق في الغرفة", "fire"),
    ("ar", "زوجي لا يتنفس نحتاج إسعاف", "medical"),
    ("ar", "والدخان في الممر يوجد دخان", "fire"),
    ("ar", "ضاع جوازي", "lost_document"),
    ("ar", "أبي المدير الآن", "complaint"),
    ("ar", "يوجد حريق؟", "fire"),  # punctuation after the word is not part of it
    ("ar", "حريق، بسرعة", "fire"),
    ("ur", "کمرے میں آگ۔", "fire"),  # the Urdu full stop
    ("hi", "कमरे में आग।", "fire"),  # the danda
    ("ru", "В номере пожар!", "fire"),
    ("ru", "мой сын пропал", "missing_child"),
    ("ru", "застряли в лифте", "lift_entrapment"),
    ("ru", "у меня украли телефон", "theft"),
    ("hi", "कमरे में आग लगी है", "fire"),
    ("hi", "मेरा बच्चा खो गया", "missing_child"),
    ("hi", "aag lagi hai", "fire"),
    ("hi", "मुझे रिफंड चाहिए", "refund"),
    ("ur", "کمرے میں آگ لگی ہے", "fire"),
    ("ur", "میرا بچہ گم ہو گیا", "missing_child"),
    ("ur", "پولیس کو بلائیں", "police"),
    ("zh", "房间着火了", "fire"),
    ("zh", "我的孩子不见了", "missing_child"),
    ("zh", "我的护照丢了", "lost_document"),
    ("zh", "我要投诉", "complaint"),
    ("de", "Es brennt im Zimmer", "fire"),
    ("de", "Mein Mann ist bewusstlos", "medical"),
    ("de", "Wir stecken im Aufzug fest", "lift_entrapment"),
    ("de", "Meine Uhr wurde gestohlen", "theft"),
    ("fr", "Au feu ! Il y a de la fumée", "fire"),
    ("fr", "Mon mari ne respire plus", "medical"),
    ("fr", "on m'a volé mon sac", "theft"),
    ("fr", "Je veux être remboursé", "refund"),
    # Japanese, Korean and Thai (JAPAC guests). Polite and plain forms both.
    ("ja", "部屋が火事です", "fire"),
    ("ja", "煙が出ています", "fire"),
    ("ja", "夫が倒れて意識がありません", "medical"),
    ("ja", "息ができません、救急車を呼んでください", "medical"),
    ("ja", "廊下にナイフを持った男がいます", "violence"),
    ("ja", "エレベーターに閉じ込められました", "lift_entrapment"),
    ("ja", "子供が見当たりません", "missing_child"),
    ("ja", "財布を盗まれました", "theft"),
    ("ja", "体を触られました", "harassment"),
    ("ja", "パスポートをなくしました", "lost_document"),
    ("ja", "警察を呼んでください", "police"),
    ("ja", "返金してください", "refund"),
    ("ja", "責任者を呼んでください", "complaint"),
    ("ko", "방에 불이 났어요", "fire"),
    ("ko", "가스 냄새가 나요", "fire"),
    ("ko", "남편이 쓰러졌어요", "medical"),
    ("ko", "숨을 쉴 수가 없어요", "medical"),
    ("ko", "복도에 칼을 든 남자가 있어요", "violence"),
    ("ko", "엘리베이터에 갇혔어요", "lift_entrapment"),
    ("ko", "아이가 없어졌어요", "missing_child"),
    ("ko", "누가 제 가방을 훔쳐갔어요", "theft"),
    ("ko", "여권을 잃어버렸어요", "lost_document"),
    ("ko", "환불해 주세요", "refund"),
    ("th", "ห้องไฟไหม้", "fire"),
    ("th", "ได้กลิ่นแก๊สในห้อง", "fire"),
    ("th", "สามีหมดสติ หายใจไม่ออก", "medical"),
    ("th", "มีผู้ชายถือมีดอยู่ที่ทางเดิน", "violence"),
    ("th", "ติดอยู่ในลิฟต์", "lift_entrapment"),
    ("th", "ลูกหายไป", "missing_child"),
    ("th", "กระเป๋าเงินถูกขโมย", "theft"),
    ("th", "พาสปอร์ตหาย", "lost_document"),
    ("th", "ขอคุยกับผู้จัดการ", "complaint"),
]


@pytest.mark.parametrize(("lang", "text", "kind"), HITS)
def test_lexicon_hits_in_every_language(lang, text, kind):
    v = safety.lexicon_verdict(text, language=lang)
    assert v["kind"] == kind, v
    assert v["level"] == safety.KIND_LEVEL[kind]
    assert v["source"] == "lexicon" and v["confidence"] == 1.0


# ─── Layer 1: false positives the guards and boundaries keep out ───

CLEAN = [
    ("en", "Can you turn on the fire tv please"),
    ("en", "Is the fire pit open tonight?"),
    ("en", "Where is the fire exit?"),
    ("en", "my phone died, do you have a charger"),
    ("en", "I'm dying for a coffee"),
    ("en", "can I smoke on the balcony"),
    ("en", "the smoked salmon was lovely"),
    ("en", "I need a knife for the birthday cake"),
    ("en", "drugs for my headache please, from the pharmacy"),
    ("en", "what brand is the shampoo"),
    ("en", "two towels and sparkling water please"),
    ("en", "can we take a break in the lounge"),
    ("en", "the bed collapsed"),
    ("en", "the AC died last night"),
    ("en", "where is the police station"),
    ("en", "is there a massage gun in the gym"),
    ("en", "this heat will kill me"),
    ("en", "can we watch Top Gun tonight"),
    ("en", "celery stalks in the salad"),
    ("en", "no complaints, great stay"),
    ("en", "is the deposit refundable"),
    ("en", "can you fire up the grill"),
    ("en", "I am going to die of boredom"),
    ("ar", "أريد معلومات عن خدمات الفندق"),
    ("ar", "أين مخرج الحريق؟"),
    ("ar", "الجوال مات، عندكم شاحن؟"),
    ("ar", "مشوي على النار في المطعم"),
    ("ar", "الجو حر موت"),  # Gulf "deadly hot": the Urdu lexicon reads Arabic script too
    ("ar", "ميت من الجوع"),  # "starving"
    ("ar", "المكيف لا يعمل"),
    ("ru", "в номере горит свет"),
    ("ru", "дракон на картине красивый"),
    ("ru", "искренне благодарю за сервис"),
    ("hi", "आगे जाइए, लिफ्ट बाईं ओर है"),
    ("hi", "हमारा कमरा साफ कर दीजिए"),
    ("ur", "ہمارا کمرہ صاف کریں"),
    ("zh", "热死了，空调坏了"),
    ("zh", "笑死了"),
    ("zh", "请问吸烟区在哪里"),
    ("de", "Das Licht brennt nicht"),
    ("de", "Gibt es heute ein Feuerwerk?"),
    ("de", "Haben Sie Feuer?"),
    ("fr", "Mon vol est à 8h demain"),
    ("fr", "un toast à l'avocat, s'il vous plaît"),
    ("fr", "le feu rouge devant l'hôtel"),
    ("fr", "le détecteur de fumée fait bip"),
    ("ja", "禁煙ルームですか？"),  # 煙 inside 禁煙 (non-smoking)
    ("ja", "近くの警察署はどこですか？"),
    ("ja", "火災の時の避難経路を教えてください"),
    ("ja", "燃えるゴミはどこに捨てればいいですか"),
    ("ja", "スリッパをもう一足ください"),  # not スリ (a pickpocket)
    ("ja", "ナイフとフォークをください"),
    ("ja", "押し入れに布団はありますか"),  # the futon closet, not a break-in
    ("ja", "蚊に刺されたので、かゆみ止めはありますか"),
    ("ja", "スマホの電池が死んだので充電器を貸してください"),
    ("ja", "お腹が空いて死にそうです"),
    ("ja", "近くのドラッグストアはどこですか"),
    ("ja", "クレームブリュレをひとつお願いします"),
    ("ja", "新宿駅で迷子になりました"),  # an adult lost in the station
    ("ja", "コンロの火が出ません"),
    ("ja", "リモコンの反応がありません"),
    ("ko", "체크아웃을 연기할 수 있나요?"),  # 연기 is also "postpone"
    ("ko", "방에 불이 안 켜져요"),  # 불 is also the light
    ("ko", "방에 불이 나갔어요"),
    ("ko", "불고기 맛집 추천해 주세요"),
    ("ko", "케이크 자를 칼 좀 가져다주세요"),
    ("ko", "총 금액이 얼마예요?"),  # 총 is also "total"
    ("ko", "근처 경찰서가 어디예요?"),
    ("ko", "핸드폰 배터리가 다 죽었어요"),
    ("ko", "배고파 죽겠어요"),
    ("ko", "마약김밥 파는 곳 있어요?"),
    ("ko", "고소한 커피 한 잔 주세요"),  # 고소 is also "nutty"
    ("ko", "휴대폰을 물에 빠뜨렸어요"),
    ("th", "ขอหมอนเพิ่มอีกใบ"),  # หมอน (a pillow) starts with หมอ (a doctor)
    ("th", "เรียกหมอนวดมาที่ห้องได้ไหม"),
    ("th", "ชักโครกตัน"),  # the toilet, not a seizure
    ("th", "ขอมีดตัดเค้กหน่อย"),
    ("th", "ตายแล้ว ลืมกุญแจไว้ในห้อง"),  # "oh no", nobody died
    ("th", "หิวจะตาย"),
    ("th", "ได้กลิ่นควันบุหรี่ในห้อง"),
    ("th", "สถานีตำรวจที่ใกล้ที่สุดอยู่ที่ไหน"),
    ("th", "กรณีไฟไหม้ต้องหนีทางไหน"),
    ("th", "ศาลเจ้าเมจิไปยังไง"),
    ("th", "ห้องติดลิฟต์เสียงดัง"),  # a room next to the lift
    ("th", "ขอสบู่ฆ่าเชื้อ"),
]


@pytest.mark.parametrize(("lang", "text"), CLEAN)
def test_false_positive_fixtures_stay_clean(lang, text):
    assert safety.lexicon_verdict(text, language=lang)["level"] == "none"


def test_every_language_covers_every_kind():
    for lang in safety.LANGUAGES:
        lex = safety.lexicon(lang)
        assert lex is not None, lang
        assert {r.kind for r in lex.kinds} == set(safety.KIND_LEVEL), lang


def test_every_guest_language_has_a_lexicon():
    from clefs.modules.concierge.constants import GUEST_LANGUAGES

    assert set(GUEST_LANGUAGES) <= set(safety.LANGUAGES)


def test_kana_keeps_the_chinese_lexicon_off_japanese_text():
    """Japanese writes kanji the Chinese lexicon knows (警察, 火災) inside
    questions it has no guards for; a text with kana is read in Japanese."""
    assert "zh" not in safety.languages_for("近くの警察署はどこですか", "en")
    assert safety.lexicon_verdict("近くの警察署はどこですか", language="en")["level"] == "none"
    assert safety.lexicon_verdict("火災の時はどうすればいいですか")["level"] == "none"
    # ... while Chinese, and Japanese without a tag, still fire
    assert safety.lexicon_verdict("我要报警", language="ja")["kind"] == "police"
    assert safety.lexicon_verdict("警察を呼んでください")["kind"] == "police"
    assert safety.lexicon_verdict("房间着火了", language="ja")["kind"] == "fire"


def test_guard_masks_only_its_own_phrase():
    v = safety.lexicon_verdict("the fire pit is nice but there is a fire in the kitchen", language="en")
    assert v["kind"] == "fire"


def test_script_picks_the_lexicon_when_the_language_tag_is_wrong_or_missing():
    assert safety.lexicon_verdict("حريق في الغرفة")["kind"] == "fire"
    assert safety.lexicon_verdict("房间着火了", language="en")["kind"] == "fire"
    assert safety.lexicon_verdict("AC broken, کمرے میں آگ", language="en")["kind"] == "fire"
    # Latin text is read as German only when the guest speaks German
    assert safety.lexicon_verdict("what brand is it", language=None)["level"] == "none"
    assert safety.lexicon_verdict("Brand im Zimmer", language="de-AT")["kind"] == "fire"
    # Kana, Hangul and Thai script pick their lexicons too
    assert safety.lexicon_verdict("部屋が火事です", language="en")["kind"] == "fire"
    assert safety.lexicon_verdict("엘리베이터에 갇혔어요", language=None)["kind"] == "lift_entrapment"
    assert safety.lexicon_verdict("AC broken, ไฟไหม้", language="en")["kind"] == "fire"


def test_english_rendering_is_read_too():
    v = safety.lexicon_verdict("xyz", language="tl", text_en="there is a fire")
    assert v["kind"] == "fire"


def test_most_severe_kind_wins():
    v = safety.lexicon_verdict("I want a refund, and there is smoke in the room", language="en")
    assert (v["level"], v["kind"]) == ("emergency", "fire")


# ─── Layer 2: the LLM may only upgrade ───


def test_llm_upgrades_when_the_lexicons_find_nothing(fake_ai):
    fake_ai.push({"level": "emergency", "kind": "medical", "confidence": 0.9})
    v = safety.classify("my wife's lips are turning blue", language="en")
    assert v == {"level": "emergency", "kind": "medical", "source": "llm", "confidence": 0.9}
    assert fake_ai.calls[0].task == "service.safety" and fake_ai.calls[0].timeout_s == safety.LLM_TIMEOUT_S


def test_llm_below_confidence_or_none_changes_nothing(fake_ai):
    fake_ai.push({"level": "emergency", "kind": "fire", "confidence": 0.5},
                 {"level": "none", "kind": "none", "confidence": 0.99})
    assert safety.classify("hmm", language="en")["level"] == "none"
    assert safety.classify("hmm", language="en")["level"] == "none"


def test_llm_never_runs_over_a_deterministic_emergency(fake_ai):
    v = safety.classify("fire in the kitchen", language="en")
    assert v["source"] == "lexicon" and fake_ai.calls == []


def test_llm_can_lift_a_handoff_but_not_lower_it(fake_ai):
    fake_ai.push({"level": "sensitive", "kind": "harassment", "confidence": 0.8},
                 {"level": "none", "kind": "none", "confidence": 1.0})
    assert safety.classify("I want to complain, the waiter keeps asking for my room number", language="en")["kind"] == "harassment"
    v = safety.classify("I want to complain", language="en")
    assert (v["level"], v["source"]) == ("handoff", "lexicon")


def test_llm_failure_keeps_the_lexicon_verdict(fake_ai):
    from clefs.ai import AIError

    fake_ai.push(AIError("boom"))
    assert safety.classify("please send towels", language="en")["level"] == "none"
    fake_ai.push(TimeoutError())
    assert safety.classify("I want a refund", language="en")["level"] == "handoff"


def test_llm_kind_that_contradicts_its_level_keeps_the_level(fake_ai):
    fake_ai.push({"level": "emergency", "kind": "refund", "confidence": 0.95})
    v = safety.classify("strange noise", language="en")
    assert (v["level"], v["kind"]) == ("emergency", "other_emergency")


def test_use_llm_false_and_the_hotel_switch_skip_the_model(fake_ai, cur, hotel_uae):
    assert safety.classify("hmm", use_llm=False)["level"] == "none"
    cur.execute(
        "INSERT INTO hotel_module_settings (hotel_slug, module, enabled, settings) VALUES (%s, 'service', TRUE, %s) "
        "ON CONFLICT (hotel_slug, module) DO UPDATE SET settings = EXCLUDED.settings",
        (hotel_uae.slug, '{"safety_llm": false}'),
    )
    assert safety.classify("hmm", hotel_slug=hotel_uae.slug, cur=cur)["level"] == "none"
    assert fake_ai.calls == []


# ─── Effects and the api door ───


def test_effects_per_level():
    e = safety.effects({"level": "emergency", "kind": "medical"}, security_extension="5555")
    assert e["skip_ai"] and e["open_incident"] and e["route"] == "urgent"
    assert set(e["page"]) == {"security", "duty_manager", "gm"} and e["severity_floor"] == "L1"
    assert e["ui"]["kind"] == "emergency" and e["ui"]["numbers"] == ["998", "999"]
    assert e["ui"]["security_extension"] == "5555"
    assert [n["number"] for n in e["ui"]["directory"]] == ["999", "998", "997", "996", "995", "991"]
    s = safety.effects({"level": "sensitive", "kind": "theft"})
    assert s["ui"] is None and s["open_incident"] and "gm" not in s["page"]
    h = safety.effects({"level": "handoff", "kind": "refund"})
    assert h["route"] == "manager" and h["promise_nothing"] and not h["open_incident"]
    assert safety.effects({"level": "none"})["route"] == "normal"


def test_api_classify_safety_keeps_its_frozen_shape(fake_ai):
    from clefs.modules.service import api

    v = api.classify_safety("there is a fire", language="en")
    assert set(v) == {"level", "kind", "source", "confidence"} and v["level"] == "emergency"
    assert api.classify_safety("", language="en") == {"level": "none", "kind": None, "source": None, "confidence": 0.0}
    assert fake_ai.calls == []  # empty text never reaches the model


def test_api_classify_safety_never_raises(monkeypatch):
    from clefs.modules.service import api

    def boom(*a, **k):
        raise RuntimeError("bad lexicon")

    monkeypatch.setattr(safety, "classify", boom)
    assert api.classify_safety("fire in the room")["kind"] == "fire"  # falls back to the lexicons


def test_intake_files_an_emergency_as_l1_with_an_incident(cur, hotel_uae, fake_ai):
    from clefs.modules.service import api

    row = api.create_request(cur, hotel_uae.slug, room="101", type="Other",
                             description="smoke is coming from the bathroom", source="voice")
    assert (row["severity"], row["sensitivity"], row["sensitive_kind"], row["priority"]) == ("L1", "emergency", "fire", "high")
    cur.execute("SELECT kind, level FROM svc_incidents WHERE request_id = %s", (row["id"],))
    assert dict(cur.fetchone()) == {"kind": "fire", "level": "emergency"}
