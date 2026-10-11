"""
0208_concierge_consent_texts_v2: version "concierge-2" of the concierge
consent sheet's platform wording (hotel_slug NULL), in every guest language
(constants.GUEST_LANGUAGES: en, ar, ru, hi, ur, zh, de, fr, ja, ko, th).

Why a new version: concierge-1 (0202) was written for a UAE deployment and
its transfer_abroad text named the UAE and Singapore. This deployment is
neither: guest messages are processed by Google Cloud's Gemini AI models,
which may run outside the guest's country (including in the United States),
and the data is stored in Google Cloud's India region. Consent must be
informed, so the wording says exactly that, without naming any one country
as the guest's own. Evidence is never rewritten (core.consent refuses other
wording under an existing version), so the correction is a new version.

Same purposes and the same required flags as concierge-1 (those live in
constants: REQUIRED_CONSENTS, VOICE_REQUIRED_CONSENTS). recording and
ai_analytics keep concierge-1's meaning; English and Arabic keep its exact
words for them. constants.CONSENT_TEXT_VERSION names concierge-2, so the
stay context, the consent sheet, the WhatsApp question and the recorded
grants all use it.

The concierge-1 rows stay, unretired: consents already given point at them,
and a code rollback to concierge-1 still finds its wording. Rows are
inserted directly with record_consent_text's sha256, so the migration does
not depend on module import order, and an existing row is never rewritten.
"{hotel}" is replaced with the hotel's name when shown.

The translations were written for this prototype; have a native speaker
(and counsel) review them before a real hotel goes live: a review is a new
version, never an edit of this one.
"""

import hashlib

VERSION = "concierge-2"

TEXTS: dict[str, dict[str, str]] = {
    # Required before the AI runs at all.
    "ai_processing": {
        "en": (
            "I agree that {hotel} may use an AI assistant, which runs on Google Cloud's Gemini AI models, to understand "
            "and answer my questions and requests and to pass them to hotel staff."
        ),
        "ar": (
            "أوافق على أن يستخدم {hotel} مساعدا بالذكاء الاصطناعي يعمل بنماذج Gemini للذكاء الاصطناعي من Google Cloud، "
            "لفهم أسئلتي وطلباتي والرد عليها وإحالتها إلى موظفي الفندق."
        ),
        "ru": (
            "Я даю согласие на использование отелем {hotel} ИИ-ассистента на базе моделей ИИ Gemini от Google Cloud, "
            "чтобы понимать мои вопросы и просьбы, отвечать на них и передавать их сотрудникам отеля."
        ),
        "hi": (
            "मैं सहमत हूँ कि {hotel} मेरे सवालों और अनुरोधों को समझने, उनका जवाब देने और उन्हें होटल स्टाफ़ तक पहुँचाने "
            "के लिए ऐसे AI सहायक का उपयोग कर सकता है जो Google Cloud के Gemini AI मॉडल पर चलता है।"
        ),
        "ur": (
            "میں اس بات سے متفق ہوں کہ {hotel} میرے سوالات اور درخواستوں کو سمجھنے، ان کا جواب دینے اور انہیں ہوٹل کے "
            "عملے تک پہنچانے کے لیے ایسا AI اسسٹنٹ استعمال کر سکتا ہے جو Google Cloud کے Gemini AI ماڈلز پر چلتا ہے۔"
        ),
        "zh": "我同意{hotel}使用基于 Google Cloud Gemini 人工智能模型的 AI 助手，来理解和回答我的问题与请求，并转达给酒店员工。",
        "de": (
            "Ich bin damit einverstanden, dass {hotel} einen KI-Assistenten nutzt, der mit den Gemini-KI-Modellen von "
            "Google Cloud arbeitet, um meine Fragen und Anliegen zu verstehen, zu beantworten und an das Hotelpersonal "
            "weiterzugeben."
        ),
        "fr": (
            "J'accepte que {hotel} utilise un assistant IA, qui fonctionne avec les modèles d'IA Gemini de Google Cloud, "
            "pour comprendre mes questions et demandes, y répondre et les transmettre au personnel de l'hôtel."
        ),
        "ja": (
            "{hotel}が、Google CloudのGemini AIモデルで動作するAIアシスタントを使って、私の質問や依頼を理解し、回答し、"
            "ホテルスタッフに伝えることに同意します。"
        ),
        "ko": (
            "본인은 {hotel}에서 Google Cloud의 Gemini AI 모델로 작동하는 AI 어시스턴트를 사용하여 본인의 질문과 요청을 "
            "이해하고 답변하며 호텔 직원에게 전달하는 것에 동의합니다."
        ),
        "th": (
            "ข้าพเจ้ายินยอมให้ {hotel} ใช้ผู้ช่วย AI ที่ทำงานด้วยโมเดล AI Gemini ของ Google Cloud "
            "เพื่อทำความเข้าใจและตอบคำถามและคำขอของข้าพเจ้า รวมถึงส่งต่อให้พนักงานโรงแรม"
        ),
    },
    # Express consent to processing outside the guest's country. Required:
    # the models run where Google Cloud runs them, and the data is stored in
    # India, which is abroad for most guests.
    "transfer_abroad": {
        "en": (
            "I agree that {hotel} and its service providers may process my data outside my country: Google Cloud's "
            "Gemini AI models may process my messages and voice in other countries, including the United States, and "
            "my data is stored in Google Cloud's India region."
        ),
        "ar": (
            "أوافق على أن يعالج {hotel} ومزودو خدماته بياناتي خارج بلدي: فقد تعالج نماذج Gemini للذكاء الاصطناعي من "
            "Google Cloud رسائلي وصوتي في دول أخرى، منها الولايات المتحدة، وتخزن بياناتي في منطقة Google Cloud في الهند."
        ),
        "ru": (
            "Я даю согласие на обработку моих данных отелем {hotel} и его поставщиками услуг за пределами моей страны: "
            "модели ИИ Gemini от Google Cloud могут обрабатывать мои сообщения и голос в других странах, в том числе в "
            "США, а мои данные хранятся в регионе Google Cloud в Индии."
        ),
        "hi": (
            "मैं सहमत हूँ कि {hotel} और उसके सेवा प्रदाता मेरे डेटा को मेरे देश के बाहर प्रोसेस कर सकते हैं: Google Cloud "
            "के Gemini AI मॉडल मेरे संदेशों और आवाज़ को अमेरिका सहित दूसरे देशों में प्रोसेस कर सकते हैं, और मेरा डेटा "
            "भारत में Google Cloud के क्षेत्र में संग्रहीत होता है।"
        ),
        "ur": (
            "میں اس بات سے متفق ہوں کہ {hotel} اور اس کے سروس فراہم کنندگان میرا ڈیٹا میرے ملک سے باہر پروسیس کر سکتے "
            "ہیں: Google Cloud کے Gemini AI ماڈلز میرے پیغامات اور آواز کو امریکہ سمیت دوسرے ممالک میں پروسیس کر سکتے "
            "ہیں، اور میرا ڈیٹا بھارت میں Google Cloud کے ریجن میں محفوظ ہوتا ہے۔"
        ),
        "zh": (
            "我同意{hotel}及其服务提供商在我所在国家/地区以外处理我的数据：Google Cloud 的 Gemini 人工智能模型可能在"
            "包括美国在内的其他国家/地区处理我的消息和语音，我的数据存储在 Google Cloud 的印度区域。"
        ),
        "de": (
            "Ich bin damit einverstanden, dass {hotel} und seine Dienstleister meine Daten außerhalb meines Landes "
            "verarbeiten: Die Gemini-KI-Modelle von Google Cloud können meine Nachrichten und meine Stimme in anderen "
            "Ländern verarbeiten, auch in den USA, und meine Daten werden in der Google-Cloud-Region in Indien gespeichert."
        ),
        "fr": (
            "J'accepte que {hotel} et ses prestataires traitent mes données en dehors de mon pays : les modèles d'IA "
            "Gemini de Google Cloud peuvent traiter mes messages et ma voix dans d'autres pays, notamment aux États-Unis, "
            "et mes données sont stockées dans la région Google Cloud en Inde."
        ),
        "ja": (
            "{hotel}およびその委託先が、私のデータを自国以外で処理することに同意します。Google CloudのGemini AIモデルが"
            "米国を含む他の国で私のメッセージや音声を処理する場合があり、私のデータはGoogle Cloudのインドのリージョンに"
            "保存されます。"
        ),
        "ko": (
            "본인은 {hotel} 및 그 서비스 제공업체가 본인의 데이터를 본인의 국가 밖에서 처리하는 것에 동의합니다. "
            "Google Cloud의 Gemini AI 모델이 미국을 포함한 다른 국가에서 본인의 메시지와 음성을 처리할 수 있으며, "
            "본인의 데이터는 Google Cloud의 인도 리전에 저장됩니다."
        ),
        "th": (
            "ข้าพเจ้ายินยอมให้ {hotel} และผู้ให้บริการของโรงแรมประมวลผลข้อมูลของข้าพเจ้านอกประเทศของข้าพเจ้า "
            "โดยโมเดล AI Gemini ของ Google Cloud อาจประมวลผลข้อความและเสียงของข้าพเจ้าในประเทศอื่น รวมถึงสหรัฐอเมริกา "
            "และข้อมูลของข้าพเจ้าจัดเก็บอยู่ในภูมิภาคอินเดียของ Google Cloud"
        ),
    },
    # Required for voice. A text-only guest may decline: no transcript is kept.
    "recording": {
        "en": "Keep a transcript of my conversation with the assistant for up to 12 months so the hotel can check its service.",
        "ar": "الاحتفاظ بنص محادثتي مع المساعد لمدة تصل إلى 12 شهرا حتى يتمكن الفندق من مراجعة خدمته.",
        "ru": "Хранить текстовую запись моего разговора с ассистентом до 12 месяцев, чтобы отель мог проверять качество обслуживания.",
        "hi": "होटल अपनी सेवा की जाँच कर सके, इसके लिए सहायक के साथ मेरी बातचीत का लिखित रिकॉर्ड 12 महीने तक रखा जाए।",
        "ur": "ہوٹل اپنی سروس کا جائزہ لے سکے، اس کے لیے اسسٹنٹ کے ساتھ میری گفتگو کا تحریری ریکارڈ 12 ماہ تک رکھا جائے۔",
        "zh": "将我与助手的对话文字记录保存最长 12 个月，以便酒店检查其服务。",
        "de": "Ein Transkript meines Gesprächs mit dem Assistenten bis zu 12 Monate aufbewahren, damit das Hotel seinen Service überprüfen kann.",
        "fr": "Conserver la transcription de ma conversation avec l'assistant jusqu'à 12 mois, afin que l'hôtel puisse vérifier son service.",
        "ja": "ホテルがサービスを確認できるよう、アシスタントとの会話の文字記録を最長12か月間保存すること。",
        "ko": "호텔이 서비스를 점검할 수 있도록 어시스턴트와의 대화 기록을 최대 12개월간 보관합니다.",
        "th": "เก็บบันทึกการสนทนาระหว่างข้าพเจ้ากับผู้ช่วยไว้ไม่เกิน 12 เดือน เพื่อให้โรงแรมตรวจสอบการบริการได้",
    },
    # Optional.
    "ai_analytics": {
        "en": "Use my conversation, without my name, to improve the assistant's answers.",
        "ar": "استخدام محادثتي، دون اسمي، لتحسين إجابات المساعد.",
        "ru": "Использовать мой разговор без моего имени для улучшения ответов ассистента.",
        "hi": "सहायक के जवाब बेहतर बनाने के लिए मेरी बातचीत का उपयोग मेरे नाम के बिना किया जाए।",
        "ur": "اسسٹنٹ کے جوابات بہتر بنانے کے لیے میری گفتگو کو میرے نام کے بغیر استعمال کیا جائے۔",
        "zh": "在不含我姓名的情况下，使用我的对话来改进助手的回答。",
        "de": "Mein Gespräch ohne meinen Namen nutzen, um die Antworten des Assistenten zu verbessern.",
        "fr": "Utiliser ma conversation, sans mon nom, pour améliorer les réponses de l'assistant.",
        "ja": "氏名を除いた私の会話を、アシスタントの回答の改善に利用すること。",
        "ko": "본인의 이름을 제외한 대화 내용을 어시스턴트의 답변 개선에 활용합니다.",
        "th": "ใช้บทสนทนาของข้าพเจ้าโดยไม่ระบุชื่อ เพื่อปรับปรุงคำตอบของผู้ช่วย",
    },
}


def up(cur):
    for purpose, by_lang in TEXTS.items():
        for language, body in by_lang.items():
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            cur.execute(
                """
                SELECT sha256 FROM consent_texts
                WHERE hotel_slug IS NULL AND purpose = %s AND version = %s AND language = %s
                """,
                (purpose, VERSION, language),
            )
            if cur.fetchone() is not None:
                continue  # immutable: an existing version is never rewritten
            cur.execute(
                """
                INSERT INTO consent_texts (hotel_slug, purpose, version, language, body, sha256)
                VALUES (NULL, %s, %s, %s, %s, %s)
                """,
                (purpose, VERSION, language, body, digest),
            )
