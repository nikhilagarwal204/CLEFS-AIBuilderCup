/* Inquiry widget strings, English and Arabic. The consent wording is NOT
   here: it comes from the server with its version (consents.texts), so the
   version posted back is exactly the text the guest read. */

export type WLang = "en" | "ar";

export const asWLang = (v: string | null | undefined): WLang => ((v || "").toLowerCase().startsWith("ar") ? "ar" : "en");

const en = {
  otherLang: "العربية",
  title: "Plan your stay",
  intro: "Tell us your dates and we will reply with availability and our best direct rate.",
  name: "Your name",
  email: "Email",
  phone: "Mobile (with country code)",
  contactHint: "Give an email or a mobile number, or both.",
  checkIn: "Check-in",
  checkOut: "Check-out",
  rooms: "Rooms",
  adults: "Adults",
  children: "Children",
  message: "Anything else we should know?",
  messagePh: "Occasion, room preference, arrival time...",
  required: "Required",
  optional: "Optional",
  consentsTitle: "Your consent",
  send: "Send inquiry",
  sending: "Sending...",
  successTitle: "Thank you, we have your inquiry",
  successBody: "The hotel team will reply soon with availability and a quote.",
  another: "Send another inquiry",
  privacy: "How we use your details",
  needContact: "Please give an email or a mobile number.",
  needConsent: "Please tick both required boxes so the hotel can reply.",
  badPhone: "Please check the mobile number and include the country code (for example +971).",
  badEmail: "Please check the email address.",
  badDates: "Check-out must be after check-in.",
  tooMany: "Too many requests from this connection. Please try again in an hour.",
  notEnabled: "This form is not enabled on this website.",
  notFound: "This inquiry form is not available.",
  error: "Something went wrong. Please try again.",
  retry: "Try again",
  loading: "Loading...",
};

export type WStrings = typeof en;

const ar: WStrings = {
  otherLang: "English",
  title: "خطط لإقامتك",
  intro: "أخبرنا بتواريخ إقامتك وسنرد عليك بالتوفر وأفضل سعر للحجز المباشر.",
  name: "اسمك",
  email: "البريد الإلكتروني",
  phone: "رقم الجوال (مع رمز الدولة)",
  contactHint: "أدخل بريداً إلكترونياً أو رقم جوال، أو كليهما.",
  checkIn: "تاريخ الوصول",
  checkOut: "تاريخ المغادرة",
  rooms: "الغرف",
  adults: "البالغون",
  children: "الأطفال",
  message: "هل هناك ما تود إخبارنا به؟",
  messagePh: "مناسبة خاصة، تفضيل الغرفة، وقت الوصول...",
  required: "مطلوب",
  optional: "اختياري",
  consentsTitle: "موافقتك",
  send: "إرسال الاستفسار",
  sending: "جارٍ الإرسال...",
  successTitle: "شكراً لك، لقد استلمنا استفسارك",
  successBody: "سيرد عليك فريق الفندق قريباً بالتوفر وعرض السعر.",
  another: "إرسال استفسار آخر",
  privacy: "كيف نستخدم بياناتك",
  needContact: "يرجى إدخال بريد إلكتروني أو رقم جوال.",
  needConsent: "يرجى تحديد المربعين المطلوبين حتى يتمكن الفندق من الرد.",
  badPhone: "يرجى التحقق من رقم الجوال وإضافة رمز الدولة (مثل ‎+971).",
  badEmail: "يرجى التحقق من البريد الإلكتروني.",
  badDates: "يجب أن يكون تاريخ المغادرة بعد تاريخ الوصول.",
  tooMany: "طلبات كثيرة من هذا الاتصال. يرجى المحاولة بعد ساعة.",
  notEnabled: "هذا النموذج غير مفعل على هذا الموقع.",
  notFound: "نموذج الاستفسار هذا غير متاح.",
  error: "حدث خطأ ما. يرجى المحاولة مرة أخرى.",
  retry: "حاول مرة أخرى",
  loading: "جارٍ التحميل...",
};

export const W_STRINGS: Record<WLang, WStrings> = { en, ar };

/** Server error code -> the guest's words. */
export function submitErrorText(code: string | undefined, status: number, t: WStrings): string {
  if (status === 429) return t.tooMany;
  switch (code) {
    case "consent_contact_required":
    case "consent_transfer_abroad_required":
      return t.needConsent;
    case "contact_required": return t.needContact;
    case "invalid_phone": return t.badPhone;
    case "invalid_email": return t.badEmail;
    case "invalid_dates": return t.badDates;
    case "invalid_widget_key": return t.notEnabled;
    default: return t.error;
  }
}
