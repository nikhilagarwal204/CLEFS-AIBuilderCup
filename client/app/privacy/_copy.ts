/**
 * Guest-facing privacy copy for /privacy/[hotelSlug] (the per-hotel notice)
 * and /privacy/request (the rights form), in English and Arabic.
 *
 * WHY a local dictionary and not lib/i18n: these are public, crawlable-or-not
 * server pages reached from a QR or a link with no session and no hotel
 * locale loaded yet, and the wording is legal text that must be reviewed as
 * one block per language. Arabic renders RTL (dirFor), numbers stay Latin
 * digits so the due date matches the email receipt.
 *
 * The request types match server/clefs/modules/trust/dsr.py TYPES.
 */

export type PrivacyLang = "en" | "ar";

export const DSR_TYPES = ["access", "rectify", "erase", "restrict", "object", "portability"] as const;
export type DsrType = (typeof DSR_TYPES)[number];

export function pickLang(v: string | string[] | undefined | null): PrivacyLang {
  const s = Array.isArray(v) ? v[0] : v;
  return s && s.toLowerCase().startsWith("ar") ? "ar" : "en";
}

// ─── Request form ────────────────────────────────────────────────────────────

export const FORM_COPY = {
  en: {
    title: "Make a privacy request",
    intro: "Ask a hotel that uses Clefs to show, correct, delete or send you the personal data it holds about you, or to stop using it. The hotel answers within {days} days.",
    hotel: "Hotel",
    hotelCode: "Hotel code",
    hotelCodeHelp: "The code in the link your hotel gave you (clefs.world/stay/<code>/...).",
    hotelUnknown: "We could not find that hotel. Check the code in your hotel's link.",
    hotelRequired: "Enter your hotel's code. It is in the link your hotel gave you.",
    type: "What would you like?",
    types: {
      access: "See the data the hotel holds about me",
      rectify: "Correct my data",
      erase: "Delete my data",
      restrict: "Stop using my data for marketing and analytics",
      object: "Object to how my data is used",
      portability: "Send me my data in a file",
    } as Record<DsrType, string>,
    who: "How can the hotel find your records?",
    whoHelp: "Give at least one: an email, a phone number, or a room number with your stay date. The hotel uses these only to find and answer your request.",
    name: "Full name",
    email: "Email",
    phone: "Phone (with country code)",
    room: "Room number",
    stayDate: "A date during your stay",
    reservation: "Booking reference (optional)",
    details: "Anything that helps (optional)",
    submit: "Send request",
    sending: "Sending...",
    sentTitle: "Request received",
    sent: "The hotel will answer within {days} days. If you gave an email address, a receipt is on its way. It contains no personal details.",
    errIdentifier: "Add an email, a phone number, or a room number and stay date so the hotel can find your records.",
    errRate: "Too many requests from this connection. Please try again in an hour.",
    errGeneric: "Your request could not be sent right now. Please try again later, or contact the hotel directly.",
    noticeLink: "Read the hotel's privacy notice",
  },
  ar: {
    title: "تقديم طلب خصوصية",
    intro: "اطلب من فندق يستخدم Clefs أن يعرض لك بياناتك الشخصية التي يحتفظ بها، أو يصححها، أو يحذفها، أو يرسلها إليك، أو يتوقف عن استخدامها. يرد الفندق خلال {days} يومًا.",
    hotel: "الفندق",
    hotelCode: "رمز الفندق",
    hotelCodeHelp: "الرمز الموجود في الرابط الذي أرسله لك الفندق (clefs.world/stay/<الرمز>/...).",
    hotelUnknown: "لم نعثر على هذا الفندق. تحقق من الرمز في رابط الفندق.",
    hotelRequired: "أدخل رمز الفندق. تجده في الرابط الذي أرسله لك الفندق.",
    type: "ماذا تريد؟",
    types: {
      access: "الاطلاع على البيانات التي يحتفظ بها الفندق عني",
      rectify: "تصحيح بياناتي",
      erase: "حذف بياناتي",
      restrict: "إيقاف استخدام بياناتي للتسويق والتحليلات",
      object: "الاعتراض على طريقة استخدام بياناتي",
      portability: "إرسال بياناتي إليّ في ملف",
    } as Record<DsrType, string>,
    who: "كيف يجد الفندق سجلاتك؟",
    whoHelp: "أدخل واحدًا على الأقل: بريدًا إلكترونيًا، أو رقم هاتف، أو رقم الغرفة مع تاريخ الإقامة. يستخدمها الفندق فقط للعثور على طلبك والرد عليه.",
    name: "الاسم الكامل",
    email: "البريد الإلكتروني",
    phone: "الهاتف (مع رمز الدولة)",
    room: "رقم الغرفة",
    stayDate: "تاريخ خلال إقامتك",
    reservation: "رقم الحجز (اختياري)",
    details: "أي معلومات مفيدة (اختياري)",
    submit: "إرسال الطلب",
    sending: "جارٍ الإرسال...",
    sentTitle: "تم استلام الطلب",
    sent: "سيرد الفندق خلال {days} يومًا. إذا أدخلت بريدًا إلكترونيًا فسيصلك إيصال لا يحتوي على أي بيانات شخصية.",
    errIdentifier: "أضف بريدًا إلكترونيًا أو رقم هاتف أو رقم الغرفة مع تاريخ الإقامة حتى يجد الفندق سجلاتك.",
    errRate: "طلبات كثيرة من هذا الاتصال. يرجى المحاولة بعد ساعة.",
    errGeneric: "تعذر إرسال طلبك الآن. يرجى المحاولة لاحقًا أو التواصل مع الفندق مباشرة.",
    noticeLink: "اقرأ إشعار الخصوصية الخاص بالفندق",
  },
} as const;

export function fill(s: string, vars: Record<string, string | number>): string {
  return s.replace(/\{(\w+)\}/g, (_, k: string) => String(vars[k] ?? ""));
}

// ─── Guest notice ────────────────────────────────────────────────────────────

/** Retention rows a guest cares about, by lib/retention.ts data_class. */
export const GUEST_RETENTION_CLASSES = ["transcripts", "request_free_text", "proof_photos", "call_audio", "guest_profiles"] as const;

export const NOTICE_COPY = {
  en: {
    eyebrow: "Guest privacy notice",
    title: "Privacy notice for guests of {hotel}",
    intro: "How {hotel} uses your personal data when you use the in-room assistant, the room phone assistant, WhatsApp or the guest request pages.",
    fallbackHotel: "this hotel",
    whoH: "Who is responsible",
    who: "{hotel} is responsible for your personal data (the controller). Clefs runs the guest assistant and request system for the hotel and processes your data only on the hotel's instructions.",
    whatH: "What is collected",
    what: [
      "What you ask for and what you write or say to the assistant.",
      "Your room number and the time of each request.",
      "For some requests, a photo hotel staff take to show the job in your room is done.",
      "Contact details, only if you give them.",
      "Your consent choices, with the wording and language you saw.",
      "Call details and, only if the hotel records calls and you agree, the call audio.",
    ],
    whyH: "Why, and on what basis",
    why: "To answer your questions and send your requests to the right team. Everything beyond that runs only with your consent, which you can withdraw at any time: AI processing, processing outside your country, keeping a transcript, call recording, remembering your preferences for a later stay, and offers by message.",
    whereH: "Where your data is processed",
    where: "Your data is stored by Google Cloud in India (the Mumbai region), and photos of completed jobs are deleted there after 7 days. The assistant uses Google's Gemini AI models, which may process your messages and voice in other countries, including the United States. Because your data may be processed outside your country, the assistant asks for your consent before it starts. Without it you can still ask the front desk directly.",
    keepH: "How long it is kept",
    keepIntro: "After these periods the data is deleted, or anonymized where noted.",
    rightsH: "Your rights",
    rights: "You can ask to see, correct, delete or receive a copy of your data, to restrict its use, or to object to it. The hotel answers within 30 days.",
    rightsCta: "Make a privacy request",
    processorsH: "Service providers",
    processors: "The providers that process data for Clefs are listed on the sub-processors page.",
    processorsLink: "See the sub-processors",
    langSwitch: "العربية",
    notFound: "We could not find this hotel.",
  },
  ar: {
    eyebrow: "إشعار الخصوصية للضيوف",
    title: "إشعار الخصوصية لضيوف {hotel}",
    intro: "كيف يستخدم {hotel} بياناتك الشخصية عند استخدام المساعد داخل الغرفة، أو مساعد هاتف الغرفة، أو واتساب، أو صفحات طلبات الضيوف.",
    fallbackHotel: "هذا الفندق",
    whoH: "الجهة المسؤولة",
    who: "{hotel} هو المسؤول عن بياناتك الشخصية (المتحكم). تدير Clefs مساعد الضيوف ونظام الطلبات لصالح الفندق، وتعالج بياناتك وفق تعليمات الفندق فقط.",
    whatH: "البيانات التي يتم جمعها",
    what: [
      "ما تطلبه وما تكتبه أو تقوله للمساعد.",
      "رقم غرفتك ووقت كل طلب.",
      "في بعض الطلبات، صورة يلتقطها موظفو الفندق لإثبات إنجاز المهمة في غرفتك.",
      "بيانات الاتصال، فقط إذا قدمتها.",
      "اختياراتك بشأن الموافقة، مع النص واللغة اللذين عُرضا عليك.",
      "تفاصيل المكالمات، وتسجيلها الصوتي فقط إذا كان الفندق يسجل المكالمات ووافقت على ذلك.",
    ],
    whyH: "الغرض والأساس القانوني",
    why: "للإجابة عن أسئلتك وإرسال طلباتك إلى الفريق المناسب. وكل ما يتجاوز ذلك يتم فقط بموافقتك، ويمكنك سحبها في أي وقت: المعالجة بالذكاء الاصطناعي، والمعالجة خارج بلدك، والاحتفاظ بنص المحادثة، وتسجيل المكالمات، وتذكر تفضيلاتك لإقامة لاحقة، والعروض عبر الرسائل.",
    whereH: "مكان معالجة بياناتك",
    where: "تُخزَّن بياناتك لدى Google Cloud في الهند (منطقة مومباي)، وتُحذف هناك صور المهام المنجزة بعد 7 أيام. ويستخدم المساعد نماذج Gemini للذكاء الاصطناعي من Google، وقد تعالج رسائلك وصوتك في دول أخرى، منها الولايات المتحدة. ولأن بياناتك قد تُعالج خارج بلدك، يطلب المساعد موافقتك قبل أن يبدأ. ويمكنك دون ذلك التواصل مع الاستقبال مباشرة.",
    keepH: "مدة الاحتفاظ",
    keepIntro: "بعد هذه المدد تُحذف البيانات، أو تُجهّل هويتها حيث ذُكر ذلك.",
    rightsH: "حقوقك",
    rights: "يمكنك طلب الاطلاع على بياناتك أو تصحيحها أو حذفها أو الحصول على نسخة منها، أو تقييد استخدامها، أو الاعتراض عليه. يرد الفندق خلال 30 يومًا.",
    rightsCta: "تقديم طلب خصوصية",
    processorsH: "مزودو الخدمة",
    processors: "المزودون الذين يعالجون البيانات لصالح Clefs مدرجون في صفحة المعالجين الفرعيين.",
    processorsLink: "عرض المعالجين الفرعيين",
    langSwitch: "English",
    notFound: "لم نعثر على هذا الفندق.",
  },
} as const;

/** Arabic retention wording by data_class; English comes from lib/retention.ts. */
export const RETENTION_AR: Record<(typeof GUEST_RETENTION_CLASSES)[number], { label: string; keep: string }> = {
  transcripts: { label: "محادثات المساعد", keep: "12 شهرًا" },
  request_free_text: { label: "ما كتبته في طلبات الخدمة", keep: "12 شهرًا، ثم تُجهّل هويته" },
  proof_photos: { label: "صور يلتقطها الموظفون لإثبات إنجاز المهمة", keep: "7 أيام" },
  call_audio: { label: "تسجيل المكالمات (فقط إذا كان التسجيل مفعّلًا)", keep: "30 يومًا" },
  guest_profiles: { label: "ملف الضيف", keep: "حتى سحب الموافقة، أو 24 شهرًا دون إقامة" },
};
