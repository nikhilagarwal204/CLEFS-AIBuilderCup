"""
Tokyo demo part "core_catalog": what the hotel tells guests and sells them.

- The concierge's knowledge base, approved, in English and Japanese: the
  structured facts (check-in 15:00, check-out 11:00, no car park, no pool,
  the breakfast buffet, Wi-Fi, pets), the FAQs a Shinjuku front desk
  answers every day (the station, both airports, taxis, takkyubin luggage
  forwarding, the coin laundry, the convenience store and ATMs, tax-free
  shopping, Japan's consumption tax and Tokyo's accommodation tax, quiet
  hours, visitors, plugs, pillows) and the safety policies (110 and 119,
  earthquakes, smoking). The knowledge base stores an English answer and
  one Arabic one per entry; the Japanese answers are entries of their own,
  tagged "ja", in the same sections, so the concierge and the console see
  both languages side by side.
- Outlets and the in-room dining menu in JPY, tax included, with pairings
  (Upsell), and Upsell's price settings for Japan: prices shown
  tax-inclusive with the 10% consumption tax inside them, no service charge
  or municipality fee.
- The amenity catalogue in JPY (ops.AMN_ITEMS: the room's consumables and
  the things a Japanese hotel lends: yukata, humidifier, pillows, chargers,
  Type A plug adapters, umbrellas). Amenities' own default catalogue is the
  UAE one, so the hotel is marked as seeded (amn_seed_runs) and the first
  console visit adds nothing.

Module doors as in demo_uae.core_catalog: concierge.propose_kb_entry for
every entry; the menu, the catalogue marker and the settings are written
where the console writes them.
"""

from __future__ import annotations

from typing import Any

from clefs.core.hotels import set_module_settings
from clefs.seed.demo_uae import core_catalog as uae
from clefs.seed.demo_uae.core_catalog import ALL_DAYS

PART_ORDER = 20
ACTOR = uae.ACTOR

# ─── Concierge knowledge base (English and Japanese) ───

# fact_key, value (what the normaliser reads), question_en, question_ja, answer_ja
KB_FACTS = (
    ("checkin_time", "15:00", "What time is check-in?", "チェックインは何時からですか？",
     "チェックインは15:00からです。お早く到着された場合は、お部屋のご用意ができるまでフロントでお荷物をお預かりします。"
     "アーリーチェックインは空室状況によります。"),
    ("checkout_time", "11:00", "What time is check-out?", "チェックアウトは何時ですか？",
     "チェックアウトは11:00までです。"),
    ("parking", "none", "Is there parking?", "駐車場はありますか？",
     "当ホテルに駐車場はございません。徒歩数分の場所に有料のコインパーキングがあります。最寄りの場所はフロントでご案内します。"),
    ("pool", "no", "Do you have a pool or a gym?", "プールやジムはありますか？",
     "当ホテルにプールとジムはございません。徒歩5分の新宿中央公園はランニングに人気です。"),
    ("breakfast", "yes", "Is breakfast available?", "朝食はありますか？",
     "はい。9階の「Sora Dining」にて、和洋朝食ビュッフェを6:30から10:00（最終入場9:30）までご用意しています。"
     "料金は大人2,200円、6歳から12歳のお子様1,100円（税込）、6歳未満のお子様は無料です。"),
    ("wifi", "free", "Is there Wi-Fi?", "Wi-Fiはありますか？",
     "はい、館内全域で無料Wi-Fiをご利用いただけます。ネットワーク名はAozora-Guest、パスワードはbluesky2026です。"),
    ("pets", "no", "Are pets allowed?", "ペットは同伴できますか？",
     "申し訳ございませんが、ペットはご遠慮いただいております。身体障害者補助犬（盲導犬、聴導犬、介助犬）は同伴いただけます。"),
)

# What guests hear for each fact (the value stays in fact_value).
FACT_SENTENCES = {
    "checkin_time": "Check-in is from 15:00 (3 pm). If you arrive earlier, the front desk keeps your luggage "
                    "until your room is ready; early check-in depends on availability.",
    "checkout_time": "Check-out is by 11:00 (11 am).",
    "parking": "The hotel has no car park. Paid coin parking is a few minutes' walk away; the front desk can show you "
               "the nearest one.",
    "pool": "There is no pool or gym at the hotel. Shinjuku Central Park, a 5-minute walk away, is a popular place to "
            "run.",
    "breakfast": "Yes. Sora Dining on the 9th floor serves a Japanese and Western breakfast buffet from 06:30 to 10:00 "
                 "(last entry 09:30): ¥2,200 per adult and ¥1,100 for children aged 6 to 12, tax included. Children "
                 "under 6 eat free.",
    "wifi": "Yes, free Wi-Fi everywhere in the hotel: network Aozora-Guest, password bluesky2026.",
    "pets": "Sorry, pets are not allowed. Registered assistance dogs (guide, hearing and service dogs) are welcome.",
}

# section, question_en, answer_en, question_ja, answer_ja
KB_FAQS = (
    ("transport", "How far is Shinjuku Station?",
     "Shinjuku Station's West Exit is about an 8-minute walk. The nearest subway station is Nishi-shinjuku on the "
     "Marunouchi Line, 3 minutes away, and Tochomae on the Toei Oedo Line is about 6 minutes.",
     "新宿駅まではどのくらいですか？",
     "新宿駅西口まで徒歩約8分です。最寄りの地下鉄駅は東京メトロ丸ノ内線の西新宿駅で徒歩3分、都営大江戸線の都庁前駅は徒歩約6分です。"),
    ("transport", "How do I get here from Narita Airport?",
     "Take the Narita Express (N'EX) direct to Shinjuku Station, about 80 to 90 minutes, or the Airport Limousine Bus "
     "to Shinjuku Station West Exit or Busta Shinjuku, about 85 to 120 minutes depending on traffic.",
     "成田空港からはどう行けばいいですか？",
     "成田エクスプレス（N'EX）で新宿駅まで約80〜90分、またはリムジンバスで新宿駅西口、バスタ新宿まで交通状況により約85〜120分です。"),
    ("transport", "How do I get here from Haneda Airport?",
     "The Airport Limousine Bus runs to Shinjuku in about 35 to 60 minutes. By train, take the Keikyu Line to "
     "Shinagawa and change to the JR Yamanote Line for Shinjuku, about 45 minutes in all.",
     "羽田空港からはどう行けばいいですか？",
     "リムジンバスで新宿まで約35〜60分です。電車の場合は京急線で品川へ行き、JR山手線に乗り換えて新宿まで、合計約45分です。"),
    ("transport", "How do I get a taxi?",
     "The front desk can call a taxi for you, usually within 10 minutes, or take one at the taxi rank at Shinjuku "
     "Station West Exit. Most Tokyo taxis take cards and transit IC cards, and the rear door opens by itself.",
     "タクシーはどう呼べばいいですか？",
     "フロントでタクシーをお呼びします（通常10分以内）。新宿駅西口のタクシー乗り場からもご乗車いただけます。"
     "東京のタクシーの多くはクレジットカードや交通系ICカードが使え、後部ドアは自動で開閉します。"),
    ("checkout", "Is late check-out possible?",
     "Late check-out is ¥1,500 per extra hour, up to 14:00, subject to availability. Please ask the front desk by "
     "09:00 on the day you leave.",
     "レイトチェックアウトはできますか？",
     "空室状況により、14:00まで1時間あたり1,500円でご利用いただけます。ご出発当日の9:00までにフロントへお申し付けください。"),
    ("general", "Can you keep my luggage or send it on?",
     "The front desk keeps luggage free of charge on the day you arrive or leave. We can also send bags by "
     "takkyubin, Japan's door-to-door delivery, to the airport or your next hotel: ask by 17:00 the day before. Most "
     "deliveries arrive the next day, and you pay the courier's fee at the front desk.",
     "荷物を預けたり、送ったりできますか？",
     "ご到着日、ご出発日のお荷物はフロントで無料でお預かりします。宅急便で空港や次のホテルへお送りすることもできます。"
     "前日17:00までにお申し付けください。多くは翌日に届き、送料はフロントでお支払いいただきます。"),
    ("general", "Is there a laundry?",
     "Yes, a coin laundry on the 3rd floor is open 24 hours: the washer is ¥300 and the dryer ¥100 for 30 minutes, "
     "with detergent added automatically.",
     "ランドリーはありますか？",
     "はい、3階にコインランドリーがあり、24時間ご利用いただけます。洗濯機は300円、乾燥機は30分100円で、洗剤は自動で投入されます。"),
    ("local", "Is there a convenience store nearby?",
     "Yes, a 24-hour convenience store is about one minute from the hotel entrance.",
     "近くにコンビニはありますか？",
     "はい、ホテルの入口から徒歩約1分の場所に24時間営業のコンビニがあります。"),
    ("local", "Where can I withdraw cash with a foreign card?",
     "ATMs in convenience stores and at post offices usually accept foreign cards; check for your card's logo on "
     "the machine. The nearest is in the convenience store one minute from the hotel.",
     "海外のカードで現金を引き出せる場所はありますか？",
     "コンビニや郵便局のATMの多くは海外発行のカードに対応しています。ATMにお手持ちのカードのマークがあるかご確認ください。"
     "最寄りはホテルから徒歩1分のコンビニです。"),
    ("local", "How does tax-free shopping work?",
     "Many shops offer tax-free shopping to visitors from abroad. You need your passport (the original, not a "
     "copy); look for the Tax-Free sign and ask the shop how the tax is removed or refunded.",
     "免税で買い物できますか？",
     "多くのお店で海外からの旅行者向けの免税販売があります。パスポート（原本、コピー不可）が必要です。"
     "Tax-Freeの表示があるお店で、免税または還付の方法をお尋ねください。"),
    ("policies", "Are taxes included in the price?",
     "Prices at the hotel include Japan's 10% consumption tax. Tokyo's accommodation tax is charged on top, per "
     "person per night: nothing below a room rate of ¥10,000 per person per night, ¥100 from ¥10,000 and ¥200 "
     "from ¥15,000.",
     "料金に税金は含まれていますか？",
     "ホテル内の料金には消費税10%が含まれています。東京都の宿泊税は別途、1人1泊あたり、宿泊料金が10,000円未満は不要、"
     "10,000円以上は100円、15,000円以上は200円です。"),
    ("policies", "Are there quiet hours?",
     "Please keep noise down between 22:00 and 07:00 so every guest can sleep.",
     "静かにすべき時間はありますか？",
     "皆さまが快適にお休みいただけるよう、22:00から7:00の間はお静かにお願いいたします。"),
    ("policies", "Can visitors come to my room?",
     "Only registered guests may enter the guest rooms, and parties in rooms are not allowed. Visitors are welcome "
     "to meet you in the lobby on 1F.",
     "部屋に友人を呼んでもいいですか？",
     "客室には宿泊登録されたお客様のみご入室いただけます。客室でのパーティーはご遠慮ください。"
     "ご来客の方とは1階ロビーでお会いいただけます。"),
    ("general", "What is in the room?",
     "Every room has a yukata (cotton robe), slippers, a kettle with green tea, a hair dryer, a fridge and a safe. "
     "A humidifier, other pillows and phone chargers are available on request.",
     "部屋には何がありますか？",
     "全室に浴衣、スリッパ、電気ケトルと緑茶、ドライヤー、冷蔵庫、セーフティボックスをご用意しています。"
     "加湿器、枕のお取り替え、スマートフォンの充電器はお申し付けください。"),
    ("general", "What plugs do you use?",
     "Japan uses Type A plugs with two flat pins, at 100 volts. Plug adapters and phone chargers can be borrowed "
     "free of charge at the front desk.",
     "コンセントの形は何ですか？",
     "日本のコンセントは平たい2本ピンのAタイプで、電圧は100ボルトです。変換プラグと充電器はフロントで無料でお貸しします。"),
    ("general", "Can I borrow an umbrella?",
     "Yes, umbrellas can be borrowed free of charge at the front desk.",
     "傘を借りられますか？",
     "はい、フロントで傘を無料でお貸しします。"),
    ("general", "Do you have other pillows?",
     "Yes. Besides the standard pillow you can ask for a low pillow, a firm buckwheat (sobagara) pillow or an extra "
     "pillow, brought to your room.",
     "枕の種類はありますか？",
     "はい。標準の枕のほか、低めの枕、硬めのそば殻枕、追加の枕をお部屋までお持ちします。"),
    ("general", "Do you have rooms for families?",
     "Our Family Rooms sleep up to four guests in two double beds.",
     "家族で泊まれる部屋はありますか？",
     "ファミリールームはダブルベッド2台で、最大4名様までご宿泊いただけます。"),
    ("dining", "Do you have room service?",
     "Yes, in-room dining is available from 06:30 to 23:00. Prices include tax.",
     "ルームサービスはありますか？",
     "はい、インルームダイニングを6:30から23:00までご利用いただけます。料金は税込です。"),
    ("general", "Is the front desk open all night?",
     "Yes, the front desk on 1F is open 24 hours. Dial 9 from the room phone.",
     "フロントは24時間開いていますか？",
     "はい、1階のフロントは24時間対応しております。お部屋の電話から9番におかけください。"),
)

# section, question_en, answer_en, question_ja, answer_ja: the readiness
# gate's two sections the facts do not cover, plus earthquakes.
KB_POLICIES = (
    ("emergency", "What do I do in an emergency?",
     "Call the front desk on 9 from the room phone, staffed 24 hours. For fire or an ambulance call 119, and for "
     "the police call 110. Evacuation routes are shown on the back of your room door: in a fire, follow the green "
     "exit signs and do not use the lifts.",
     "緊急時はどうすればいいですか？",
     "お部屋の電話から9番でフロントへご連絡ください（24時間対応）。火事や救急は119番、警察は110番です。"
     "避難経路はお部屋のドアの裏に表示しています。火災の際は緑色の非常口サインに従い、エレベーターは使用しないでください。"),
    ("emergency", "What should I do in an earthquake?",
     "Stay calm and protect your head, under the desk if you can, away from windows. Do not use the lifts or rush "
     "outside, and follow staff instructions and the hotel announcements. An emergency kit with a flashlight and "
     "drinking water is in your room's closet.",
     "地震のときはどうすればいいですか？",
     "落ち着いて、できれば机の下などで頭を守り、窓から離れてください。エレベーターを使ったり、慌てて外に出たりせず、"
     "スタッフの指示と館内放送に従ってください。懐中電灯と飲料水の入った非常用品は、お部屋のクローゼットにあります。"),
    ("smoking", "Is smoking allowed?",
     "All rooms are non-smoking, including e-cigarettes and heated tobacco. You can smoke in the smoking room on 1F "
     "next to the lobby. Smoking in a guest room is charged a ¥30,000 cleaning fee.",
     "たばこは吸えますか？",
     "全客室禁煙です（電子たばこ、加熱式たばこを含む）。1階ロビー横の喫煙室をご利用ください。"
     "客室で喫煙された場合、清掃料として30,000円を申し受けます。"),
)

# Where each fact's entry (and its Japanese twin) is filed.
FACT_SECTIONS = {"checkin_time": "checkout", "checkout_time": "checkout", "parking": "parking", "pool": "pool",
                 "breakfast": "breakfast", "wifi": "wifi", "pets": "pets"}
JA_TAGS = ["ja"]


def seed_kb(ctx: Any) -> dict:
    """Each entry in English through concierge.api (a fact carries its key
    and value), its Japanese twin beside it in the same section, then every
    one approved by the demo owner."""
    api = ctx.module_api("concierge")
    if api is None:
        return {"kb_entries": 0}
    ids: list[int] = []
    for key, value, q_en, q_ja, a_ja in KB_FACTS:
        ids.append(uae.kb_entry(ctx, api, question_en=q_en, answer_en=value, fact_key=key,
                                sentence=FACT_SENTENCES[key]))
        ids.append(uae.kb_entry(ctx, api, question_en=q_ja, answer_en=a_ja, section=FACT_SECTIONS[key],
                                tags=JA_TAGS))
    for section, q_en, a_en, q_ja, a_ja in KB_FAQS + KB_POLICIES:
        ids.append(uae.kb_entry(ctx, api, question_en=q_en, answer_en=a_en, section=section))
        ids.append(uae.kb_entry(ctx, api, question_en=q_ja, answer_en=a_ja, section=section, tags=JA_TAGS))
    uae.approve_kb(ctx, ids)
    ctx.ids["kb_entries"] = ids
    return {"kb_entries": len(ids)}


# ─── Outlets and menu (Upsell) ───

MENU_CURRENCY = "JPY"
# key, name_en, name_ar, kind, hours, delivers_to_room
OUTLETS = (
    ("ird", "In-Room Dining", None, "in_room_dining", [{"from": "06:30", "to": "23:00", "days_mask": ALL_DAYS}], True),
    ("sora-dining", "Sora Dining (9F)", None, "restaurant",
     [{"from": "06:30", "to": "22:00", "days_mask": ALL_DAYS}], False),
)

# outlet, sku, category, category_ar, name_en, name_ar, desc_en, price JPY (tax included), dietary, prep_min,
# from, to
MENU = (
    ("ird", "IRD-BRK-01", "Breakfast", None, "Japanese breakfast set", None,
     "Grilled salmon, rice, miso soup, tamagoyaki omelette, pickles, green tea", 2400, [], 20, "06:30", "10:30"),
    ("ird", "IRD-BRK-02", "Breakfast", None, "Continental breakfast", None,
     "Croissant, toast, butter, jam, yoghurt, fruit, coffee or tea", 1800, ["vegetarian"], 15, "06:30", "10:30"),
    ("ird", "IRD-BRK-03", "Breakfast", None, "Onigiri set", None,
     "Two rice balls (salmon and kombu), miso soup, green tea", 1200, [], 10, "06:30", "10:30"),
    ("ird", "IRD-MN-01", "Mains", None, "Tonkotsu ramen", None,
     "Pork bone broth, chashu pork, soft-boiled egg, spring onion, nori", 1600, ["contains_pork"], 20, None, None),
    ("ird", "IRD-MN-02", "Mains", None, "Katsu curry rice", None,
     "Breaded pork cutlet, Japanese curry, rice, pickles", 1800, ["contains_pork"], 25, None, None),
    ("ird", "IRD-MN-03", "Mains", None, "Chicken teriyaki don", None,
     "Rice bowl with teriyaki chicken, egg and nori", 1500, [], 20, None, None),
    ("ird", "IRD-MN-04", "Mains", None, "Club sandwich", None,
     "Chicken, bacon, egg, lettuce, tomato, with fries", 1700, ["contains_pork"], 20, None, None),
    ("ird", "IRD-MN-05", "Mains", None, "Vegetable udon", None,
     "Thick wheat noodles in kombu broth, seasonal vegetables, tofu", 1300, ["vegan"], 15, None, None),
    ("ird", "IRD-MN-06", "Mains", None, "Wagyu hamburg steak", None,
     "Wagyu beef patty, demi-glace sauce, rice, salad", 2600, [], 25, None, None),
    ("ird", "IRD-SD-01", "Sides", None, "French fries", None, "Skin-on fries, sea salt", 600,
     ["vegan", "gluten_free"], 10, None, None),
    ("ird", "IRD-SD-02", "Sides", None, "Edamame", None, "Salted green soybeans", 500, ["vegan", "gluten_free"], 5,
     None, None),
    ("ird", "IRD-SD-03", "Sides", None, "Miso soup", None, "Tofu, wakame, spring onion, bonito dashi", 300, [], 5,
     None, None),
    ("ird", "IRD-DS-01", "Desserts", None, "Matcha ice cream", None, "Uji green tea ice cream", 550,
     ["vegetarian", "gluten_free"], 5, None, None),
    ("ird", "IRD-DS-02", "Desserts", None, "Seasonal fruit plate", None, "Japanese seasonal cut fruit", 900,
     ["vegan", "gluten_free"], 10, None, None),
    ("ird", "IRD-BV-01", "Drinks", None, "Orange juice", None, "Chilled, 300 ml", 450, ["vegan", "gluten_free"], 2,
     None, None),
    ("ird", "IRD-BV-02", "Drinks", None, "Matcha latte", None, "Green tea with steamed milk, hot or iced", 600,
     ["vegetarian", "gluten_free"], 5, None, None),
    ("ird", "IRD-BV-03", "Drinks", None, "Coffee (pot)", None, "Freshly brewed, two cups", 600,
     ["vegan", "gluten_free"], 5, None, None),
    ("ird", "IRD-BV-04", "Drinks", None, "Soft drink", None, "Cola, ginger ale or green tea, 500 ml bottle", 350,
     ["vegan", "gluten_free"], 2, None, None),
    ("sora-dining", "SD-BUF-01", "Buffet", None, "Breakfast buffet (Japanese and Western)", None,
     "Served at Sora Dining, 9F. Children aged 6 to 12: ¥1,100; under 6 free", 2200, [], 0, "06:30", "10:00"),
    ("sora-dining", "SD-DIN-01", "Dinner", None, "Seasonal dinner set", None,
     "Served at Sora Dining, 9F: sashimi, tempura, rice, miso soup, dessert", 3800, ["contains_shellfish"], 0,
     "17:30", "22:00"),
)

# item sku, suggested sku, kind, reason_en, reason_ar
PAIRINGS = (
    ("IRD-MN-01", "IRD-SD-02", "pairing", "Edamame with your ramen?", None),
    ("IRD-MN-03", "IRD-SD-03", "pairing", "Miso soup with your rice bowl?", None),
    ("IRD-MN-06", "IRD-SD-01", "pairing", "Fries with the hamburg steak?", None),
    ("IRD-MN-04", "IRD-BV-04", "pairing", "A cold soft drink with the club?", None),
    ("IRD-BRK-02", "IRD-BV-01", "pairing", "Orange juice with breakfast?", None),
    ("IRD-BRK-03", "IRD-BRK-01", "upgrade", "Try the full Japanese breakfast set", None),
    ("IRD-MN-02", "IRD-DS-01", "pairing", "Matcha ice cream to finish?", None),
    ("IRD-DS-01", "IRD-BV-02", "pairing", "A matcha latte with dessert?", None),
)

# Upsell's settings for Japan (upsell.settings.UpsellSettings): menu prices
# are what the guest pays and include the 10% consumption tax; there is no
# service charge or municipality fee, and no fee rule to read (core.tax
# holds UAE rules only), so the rates are the hotel's own.
UPSELL_SETTINGS = {"price_display": "inclusive", "fee_source": "manual", "service_charge_pct": "0",
                   "municipality_fee_pct": "0", "vat_pct": "10", "convention": "A"}


def seed_menu_settings(ctx: Any) -> dict:
    if ctx.module_api("upsell") is None:
        return {}
    set_module_settings(ctx.cur, ctx.slug, "upsell", dict(UPSELL_SETTINGS), actor=ACTOR)
    return {"consumption_tax_pct": UPSELL_SETTINGS["vat_pct"]}


# ─── Amenity catalogue ───


def seed_amenities(ctx: Any) -> dict:
    """The catalogue (ops.AMN_ITEMS) through the ops part's writer, and the
    seeding marker that keeps Amenities' UAE default catalogue out."""
    from clefs.seed.demo_uae import ops

    api = ctx.module_api("amenities")
    if api is None or not ctx.table_exists("amn_items"):
        return {"amenity_items": 0}
    items = ops.ensure_catalog(ctx, api)
    if ctx.table_exists("amn_seed_runs"):
        ctx.cur.execute(
            "INSERT INTO amn_seed_runs (hotel_slug, version, items) VALUES (%s, 1, 0) "
            "ON CONFLICT (hotel_slug) DO NOTHING",
            (ctx.slug,),
        )
    return {"amenity_items": len(items)}


# ─── Part entry ───


def seed_part(ctx: Any) -> dict:
    out: dict[str, Any] = {}
    for step in (seed_kb, uae.seed_menu, seed_menu_settings, seed_amenities):
        out.update(step(ctx))
    return out
