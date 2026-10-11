"""
0679_guests_consent_texts_v3: version "gst.v3" of the platform wording
(hotel_slug NULL) of every consent the guests module captures, in English
and Arabic (the languages gst.v1 and gst.v2 have) plus Japanese, Korean and
Thai (the JAPAC guest languages the concierge added).

Why a new version: gst.v1's transfer_abroad text (0675) was written for a
UAE deployment and named the UAE and Singapore. This deployment is neither:
the data is stored in Google Cloud's India region, and guest messages are
processed by Google Cloud's Gemini AI models, which may run outside the
guest's country, including in the United States. Consent must be informed,
so the wording says exactly that, without naming any one country as the
guest's own (the concierge's concierge-2, 0208, says the same for its
sheet). Evidence is never rewritten (core.consent refuses other wording
under an existing version), so the correction is a new version.

Same purposes as before, and the same rules around them (transfer_abroad is
required for every capture, contacts._require_transfer; marketing is asked
per channel). whatsapp_updates, marketing_whatsapp and marketing_email keep
gst.v1's meaning and profile_memory keeps gst.v2's (it discloses health
data); English and Arabic keep their exact words. identity.py pins gst.v3
for every one of these purposes (identity.CONSENT_TEXT_VERSION).

The gst.v1 and gst.v2 rows stay, as the evidence earlier consents point
at, and are retired (retired_at) so core.consent.current_consent_text
serves gst.v3 to every new capture, as 0651 did for gst.v1's profile text.
0651 and 0675 always run before this file (the runner applies versions in
ascending order), so there is no ordering case to converge. Rows are
inserted directly with record_consent_text's sha256, so the migration does
not depend on module import order (marketing_whatsapp and marketing_email
are registered at import), and an existing row is never rewritten.
"{hotel}" is replaced with the hotel's name when shown.

The Japanese, Korean and Thai texts were written for this prototype; have a
native speaker (and counsel) review them before a real hotel goes live: a
review is a new version, never an edit of this one.
"""

import hashlib

VERSION = "gst.v3"
OLD_VERSIONS = ("gst.v1", "gst.v2")

TEXTS: dict[str, dict[str, str]] = {
    # Meta's opt-in for utility messages: names the hotel, says WhatsApp.
    "whatsapp_updates": {
        "en": "Send me messages about my stay at {hotel} on WhatsApp.",
        "ar": "أرسلوا لي رسائل حول إقامتي في {hotel} عبر واتساب.",
        "ja": "{hotel}での滞在に関するメッセージをWhatsAppで送ってください。",
        "ko": "{hotel} 숙박에 관한 메시지를 WhatsApp으로 보내 주세요.",
        "th": "ส่งข้อความเกี่ยวกับการเข้าพักของข้าพเจ้าที่ {hotel} ทาง WhatsApp",
    },
    # Marketing is separate per channel. The STOP keyword is the Latin word
    # in every language (optout.STOP_ALL; Arabic also has its own words).
    "marketing_whatsapp": {
        "en": "Send me offers from {hotel} on WhatsApp. I can reply STOP at any time.",
        "ar": "أرسلوا لي عروض {hotel} عبر واتساب. يمكنني إرسال إيقاف في أي وقت.",
        "ja": "{hotel}からのお得な情報をWhatsAppで送ってください。いつでもSTOPと返信して停止できます。",
        "ko": "{hotel}의 혜택 안내를 WhatsApp으로 보내 주세요. 언제든지 STOP이라고 답장하여 중단할 수 있습니다.",
        "th": "ส่งข้อเสนอจาก {hotel} ทาง WhatsApp ข้าพเจ้าตอบกลับว่า STOP เพื่อหยุดรับได้ทุกเมื่อ",
    },
    "marketing_email": {
        "en": "Send me offers from {hotel} by email. I can unsubscribe at any time.",
        "ar": "أرسلوا لي عروض {hotel} عبر البريد الإلكتروني. يمكنني إلغاء الاشتراك في أي وقت.",
        "ja": "{hotel}からのお得な情報をメールで送ってください。いつでも配信を停止できます。",
        "ko": "{hotel}의 혜택 안내를 이메일로 보내 주세요. 언제든지 수신을 거부할 수 있습니다.",
        "th": "ส่งข้อเสนอจาก {hotel} ทางอีเมล ข้าพเจ้ายกเลิกการรับได้ทุกเมื่อ",
    },
    # Express consent to storage and processing outside the guest's country.
    # Required for every capture: the data is stored in India, which is
    # abroad for most guests, and the AI models run where Google Cloud runs
    # them.
    "transfer_abroad": {
        "en": (
            "I agree that {hotel} and its service providers may store and process my contact details and messages "
            "outside my country: my data is stored in Google Cloud's India region, and Google Cloud's Gemini AI "
            "models may process my messages in other countries, including the United States."
        ),
        "ar": (
            "أوافق على أن يقوم {hotel} ومزودو خدماته بتخزين ومعالجة بيانات الاتصال الخاصة بي ورسائلي خارج بلدي: "
            "تُخزَّن بياناتي في منطقة Google Cloud في الهند، وقد تعالج نماذج Gemini للذكاء الاصطناعي من Google Cloud "
            "رسائلي في دول أخرى، منها الولايات المتحدة."
        ),
        "ja": (
            "{hotel}およびその委託先が、私の連絡先とメッセージを自国以外で保存・処理することに同意します。"
            "私のデータはGoogle Cloudのインドのリージョンに保存され、Google CloudのGemini AIモデルが米国を含む"
            "他の国で私のメッセージを処理する場合があります。"
        ),
        "ko": (
            "본인은 {hotel} 및 그 서비스 제공업체가 본인의 연락처와 메시지를 본인의 국가 밖에서 저장하고 처리하는 "
            "것에 동의합니다. 본인의 데이터는 Google Cloud의 인도 리전에 저장되며, Google Cloud의 Gemini AI 모델이 "
            "미국을 포함한 다른 국가에서 본인의 메시지를 처리할 수 있습니다."
        ),
        "th": (
            "ข้าพเจ้ายินยอมให้ {hotel} และผู้ให้บริการของโรงแรมจัดเก็บและประมวลผลข้อมูลติดต่อและข้อความของข้าพเจ้า"
            "นอกประเทศของข้าพเจ้า โดยข้อมูลของข้าพเจ้าจัดเก็บอยู่ในภูมิภาคอินเดียของ Google Cloud และโมเดล AI "
            "Gemini ของ Google Cloud อาจประมวลผลข้อความของข้าพเจ้าในประเทศอื่น รวมถึงสหรัฐอเมริกา"
        ),
    },
    # 03 spec 2.7: a guest profile exists only with this consent. It names
    # the health data a profile keeps (allergies), as gst.v2 does.
    "profile_memory": {
        "en": (
            "Remember my preferences at {hotel} for my future stays, including any allergies or dietary needs "
            "I share, which are health information. I can ask {hotel} to forget them at any time."
        ),
        "ar": (
            "تذكروا تفضيلاتي في {hotel} لإقاماتي القادمة، بما في ذلك أي حساسية أو احتياجات غذائية أشاركها، "
            "وهي معلومات صحية. يمكنني أن أطلب من {hotel} حذفها في أي وقت."
        ),
        "ja": (
            "今後の滞在のために、{hotel}で私の好みを記憶してください。私が伝えるアレルギーや食事上の配慮"
            "（健康に関する情報）も含みます。{hotel}にはいつでも削除を依頼できます。"
        ),
        "ko": (
            "향후 숙박을 위해 {hotel}에서 본인의 선호 사항을 기억해 주세요. 여기에는 본인이 알려 주는 알레르기나 "
            "식이 요구 사항(건강 정보)도 포함됩니다. 언제든지 {hotel}에 삭제를 요청할 수 있습니다."
        ),
        "th": (
            "โปรดจดจำความชอบของข้าพเจ้าที่ {hotel} สำหรับการเข้าพักครั้งต่อไป รวมถึงอาการแพ้หรือข้อจำกัดด้านอาหาร"
            "ที่ข้าพเจ้าแจ้งไว้ ซึ่งเป็นข้อมูลด้านสุขภาพ ข้าพเจ้าขอให้ {hotel} ลบข้อมูลเหล่านี้ได้ทุกเมื่อ"
        ),
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
    # Superseded, never deleted: consents already given keep pointing at them.
    cur.execute(
        """
        UPDATE consent_texts SET retired_at = NOW()
        WHERE hotel_slug IS NULL AND purpose = ANY(%s) AND version = ANY(%s) AND retired_at IS NULL
        """,
        (list(TEXTS), list(OLD_VERSIONS)),
    )
