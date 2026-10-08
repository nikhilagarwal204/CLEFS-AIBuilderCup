/* ═══════════════════════════════════════════════════════════════════════
   EN/AR strings for the guest journey pages. Consent wording is NOT here:
   tick boxes render the versioned texts the server returns (04 spec 3.4),
   so the version posted back is exactly the one the guest read. These are
   only labels, headings and state messages. No incentive text anywhere,
   and nothing on the feedback page varies with the rating given.
   ═══════════════════════════════════════════════════════════════════════ */

export type GLang = "en" | "ar";

export interface GStrings {
  langName: string;
  otherLang: string;
  loading: string;
  invalid: string;
  expired: string;
  used: string;
  tooMany: string;
  error: string;
  retry: string;
  // Preferences page
  prefsTitle: string;
  prefsIntro: string;
  stayDates: string;
  arrivalTime: string;
  arrivalHint: string;
  occasion: string;
  occasionHint: string;
  requests: string;
  requestsHint: string;
  contactTitle: string;
  contactIntro: string;
  contactOtaNote: string;
  phone: string;
  email: string;
  contactValuePhone: string;
  contactValueEmail: string;
  required: string;
  needTransfer: string;
  save: string;
  saved: string;
  savedContact: string;
  editAgain: string;
  // Consent (J5 transfer tick) page
  consentTitle: string;
  consentIntro: string;
  confirm: string;
  consentDone: string;
  // Feedback page
  fbTitle: string;
  fbIntro: string;
  reviewLine: string;
  overall: string;
  star: string;
  wentWell: string;
  improve: string;
  contactOk: string;
  send: string;
  thanksTitle: string;
  thanksBody: string;
  google: string;
  tripadvisor: string;
}

const en: GStrings = {
  langName: "English",
  otherLang: "العربية",
  loading: "Loading...",
  invalid: "This link is not valid.",
  expired: "This link has expired.",
  used: "This link has already been used. Thank you.",
  tooMany: "Too many attempts. Please try again in a few minutes.",
  error: "Something went wrong. Please try again.",
  retry: "Try again",
  prefsTitle: "Help us prepare for your stay",
  prefsIntro: "Everything here is optional. Share only what helps.",
  stayDates: "Your stay",
  arrivalTime: "Expected arrival time",
  arrivalHint: "An estimate is fine.",
  occasion: "Special occasion",
  occasionHint: "A birthday, an anniversary...",
  requests: "Requests",
  requestsHint: "Room preferences, extra bed, dietary needs...",
  contactTitle: "Stay in touch during your stay (optional)",
  contactIntro: "Give us your own number or email if you would like messages about this stay. Each box below is a separate choice.",
  contactOtaNote: "We will use it only for messages about this stay.",
  phone: "Phone",
  email: "Email",
  contactValuePhone: "Mobile number with country code",
  contactValueEmail: "Email address",
  required: "Required to keep your contact",
  needTransfer: "To keep your contact, please tick the data-transfer box. Otherwise leave the contact empty.",
  save: "Save",
  saved: "Thank you. Your details are saved.",
  savedContact: "Your contact is saved with the choices you ticked.",
  editAgain: "Edit",
  consentTitle: "One last step",
  consentIntro: "To keep your number and send you what you asked for, we need your agreement below.",
  confirm: "Confirm",
  consentDone: "Thank you. You can reply STOP at any time.",
  fbTitle: "How was your stay?",
  fbIntro: "It takes one minute. Every answer is optional.",
  reviewLine: "Prefer to post a public review?",
  overall: "Overall",
  star: "{n} out of 5",
  wentWell: "What went well?",
  improve: "What should we improve?",
  contactOk: "You may contact me about my feedback.",
  send: "Send",
  thanksTitle: "Thank you",
  thanksBody: "Your feedback has reached the hotel team.",
  google: "Google",
  tripadvisor: "Tripadvisor",
};

const ar: GStrings = {
  langName: "العربية",
  otherLang: "English",
  loading: "جارٍ التحميل...",
  invalid: "هذا الرابط غير صالح.",
  expired: "انتهت صلاحية هذا الرابط.",
  used: "تم استخدام هذا الرابط من قبل. شكراً لك.",
  tooMany: "محاولات كثيرة. يرجى المحاولة بعد بضع دقائق.",
  error: "حدث خطأ. يرجى المحاولة مرة أخرى.",
  retry: "حاول مرة أخرى",
  prefsTitle: "ساعدنا في التحضير لإقامتك",
  prefsIntro: "كل ما هنا اختياري. شاركنا ما يفيد فقط.",
  stayDates: "إقامتك",
  arrivalTime: "موعد الوصول المتوقع",
  arrivalHint: "التقدير يكفي.",
  occasion: "مناسبة خاصة",
  occasionHint: "عيد ميلاد، ذكرى زواج...",
  requests: "طلبات",
  requestsHint: "تفضيلات الغرفة، سرير إضافي، احتياجات غذائية...",
  contactTitle: "ابقَ على تواصل خلال إقامتك (اختياري)",
  contactIntro: "أعطنا رقمك أو بريدك الإلكتروني إذا رغبت في رسائل حول هذه الإقامة. كل خانة أدناه اختيار منفصل.",
  contactOtaNote: "سنستخدمه فقط للرسائل المتعلقة بهذه الإقامة.",
  phone: "الهاتف",
  email: "البريد الإلكتروني",
  contactValuePhone: "رقم الجوال مع رمز الدولة",
  contactValueEmail: "البريد الإلكتروني",
  required: "مطلوب للاحتفاظ ببيانات التواصل",
  needTransfer: "للاحتفاظ ببيانات التواصل، يرجى تحديد خانة نقل البيانات. وإلا اترك خانة التواصل فارغة.",
  save: "حفظ",
  saved: "شكراً لك. تم حفظ بياناتك.",
  savedContact: "تم حفظ بيانات التواصل مع الخيارات التي حددتها.",
  editAgain: "تعديل",
  consentTitle: "خطوة أخيرة",
  consentIntro: "للاحتفاظ برقمك وإرسال ما طلبته، نحتاج إلى موافقتك أدناه.",
  confirm: "تأكيد",
  consentDone: "شكراً لك. يمكنك الرد بكلمة STOP في أي وقت.",
  fbTitle: "كيف كانت إقامتك؟",
  fbIntro: "يستغرق ذلك دقيقة واحدة. كل الإجابات اختيارية.",
  reviewLine: "هل تفضّل نشر تقييم علني؟",
  overall: "التقييم العام",
  star: "{n} من 5",
  wentWell: "ما الذي أعجبك؟",
  improve: "ما الذي يمكننا تحسينه؟",
  contactOk: "يمكنكم التواصل معي بخصوص ملاحظاتي.",
  send: "إرسال",
  thanksTitle: "شكراً لك",
  thanksBody: "وصلت ملاحظاتك إلى فريق الفندق.",
  google: "Google",
  tripadvisor: "Tripadvisor",
};

export const G_STRINGS: Record<GLang, GStrings> = { en, ar };

export function asLang(v: string | null | undefined): GLang {
  return (v || "").toLowerCase().startsWith("ar") ? "ar" : "en";
}
