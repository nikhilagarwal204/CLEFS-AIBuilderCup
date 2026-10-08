/* Quote page strings, English and Arabic (UAE guests; 04 spec 4.6). Kept
   flat and typed so a missing Arabic key is a compile error, not a blank
   button. Deposit labels arrive from the server in English ("First night",
   "25% deposit"); depositLabel() maps the known shapes and falls back to
   the server's words. */

export type QLang = "en" | "ar";

export const asLang = (v: string | null | undefined): QLang => (v === "ar" ? "ar" : "en");

const en = {
  otherLang: "العربية",
  title: "Your quote",
  quoteRef: "Quote {ref}",
  validUntil: "Valid until {date}",
  checkIn: "Check-in",
  checkOut: "Check-out",
  nights: "{n} nights",
  night1: "1 night",
  night2: "2 nights",
  rooms: "{n} rooms",
  room1: "1 room",
  room2: "2 rooms",
  nightly: "Nightly rates",
  night: "Night",
  roomType: "Room",
  rate: "Rate per room",
  priceBreakdown: "Price breakdown",
  room: "Room",
  serviceCharge: "Service charge",
  municipalityFee: "Municipality fee",
  vat: "VAT",
  tourismDirham: "Tourism Dirham",
  tdNights: "{n} nights charged",
  total: "Total",
  inclusive: "All prices in {currency}, including taxes and fees.",
  schedule: "Payment schedule",
  dueNow: "Due on acceptance",
  dueOn: "Due {date}",
  cancellation: "Cancellation",
  included: "Included",
  groupTerms: "Group terms",
  cutoff: "Rooms released after",
  attrition: "You may reduce rooms by up to {pct}% (keeping at least {min})",
  roomingList: "Rooming list due",
  accept: "Accept this quote",
  accepting: "Accepting...",
  acceptedTitle: "Quote accepted",
  acceptedBody: "Thank you. The hotel will send your payment link shortly; it will also appear on this page.",
  acceptedManual: "Thank you. The hotel will contact you about payment.",
  pay: "Pay securely",
  payHint: "You will be taken to the payment page of the hotel's payment provider.",
  expired: "This quote has expired",
  expiredBody: "Reply to the hotel's message and they will send you an updated quote.",
  superseded: "This quote was replaced",
  supersededBody: "The hotel sent you a newer version. Use the latest link in your messages.",
  withdrawn: "This quote is no longer available",
  notReady: "This quote is not ready yet",
  invalid: "This link is not valid",
  invalidBody: "Check that you opened the full link from the hotel's message.",
  tooMany: "Too many attempts. Please wait a few minutes and try again.",
  error: "Something went wrong. Please try again.",
  retry: "Try again",
  acceptError: "We could not accept the quote. Please try again or reply to the hotel's message.",
  firstNight: "First night",
  first30: "First 30 nights",
  balance: "Balance",
  full: "Full payment",
  pctDeposit: "{pct}% deposit",
  stars: "{n}-star hotel",
};

export type QStrings = typeof en;

const ar: QStrings = {
  otherLang: "English",
  title: "عرض السعر الخاص بك",
  quoteRef: "عرض السعر {ref}",
  validUntil: "صالح حتى {date}",
  checkIn: "تاريخ الوصول",
  checkOut: "تاريخ المغادرة",
  nights: "{n} ليالٍ",
  night1: "ليلة واحدة",
  night2: "ليلتان",
  rooms: "{n} غرف",
  room1: "غرفة واحدة",
  room2: "غرفتان",
  nightly: "الأسعار لكل ليلة",
  night: "الليلة",
  roomType: "الغرفة",
  rate: "السعر لكل غرفة",
  priceBreakdown: "تفاصيل السعر",
  room: "الغرفة",
  serviceCharge: "رسوم الخدمة",
  municipalityFee: "رسوم البلدية",
  vat: "ضريبة القيمة المضافة",
  tourismDirham: "رسم درهم السياحة",
  tdNights: "محتسب عن {n} ليالٍ",
  total: "الإجمالي",
  inclusive: "جميع الأسعار بعملة {currency} وتشمل الضرائب والرسوم.",
  schedule: "جدول الدفع",
  dueNow: "يستحق عند القبول",
  dueOn: "يستحق في {date}",
  cancellation: "سياسة الإلغاء",
  included: "يشمل",
  groupTerms: "شروط المجموعات",
  cutoff: "يتم تحرير الغرف بعد",
  attrition: "يمكنك تخفيض عدد الغرف حتى {pct}% (بحد أدنى {min})",
  roomingList: "موعد تسليم قائمة النزلاء",
  accept: "قبول عرض السعر",
  accepting: "جارٍ القبول...",
  acceptedTitle: "تم قبول عرض السعر",
  acceptedBody: "شكراً لك. سيرسل لك الفندق رابط الدفع قريباً، وسيظهر أيضاً على هذه الصفحة.",
  acceptedManual: "شكراً لك. سيتواصل معك الفندق بخصوص الدفع.",
  pay: "ادفع بأمان",
  payHint: "سيتم نقلك إلى صفحة الدفع لدى مزود خدمة الدفع الخاص بالفندق.",
  expired: "انتهت صلاحية عرض السعر",
  expiredBody: "رد على رسالة الفندق وسيرسل لك عرض سعر محدثاً.",
  superseded: "تم استبدال عرض السعر",
  supersededBody: "أرسل لك الفندق نسخة أحدث. استخدم أحدث رابط في رسائلك.",
  withdrawn: "عرض السعر هذا لم يعد متاحاً",
  notReady: "عرض السعر غير جاهز بعد",
  invalid: "هذا الرابط غير صالح",
  invalidBody: "تأكد من فتح الرابط كاملاً من رسالة الفندق.",
  tooMany: "محاولات كثيرة. يرجى الانتظار بضع دقائق ثم المحاولة مرة أخرى.",
  error: "حدث خطأ ما. يرجى المحاولة مرة أخرى.",
  retry: "حاول مرة أخرى",
  acceptError: "تعذر قبول عرض السعر. يرجى المحاولة مرة أخرى أو الرد على رسالة الفندق.",
  firstNight: "الليلة الأولى",
  first30: "أول 30 ليلة",
  balance: "المبلغ المتبقي",
  full: "الدفع الكامل",
  pctDeposit: "دفعة مقدمة {pct}%",
  stars: "فندق {n} نجوم",
};

export const Q_STRINGS: Record<QLang, QStrings> = { en, ar };

export function fill(s: string, vars: Record<string, string | number>): string {
  return s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m));
}

/** The server's English milestone label in the page's language. */
export function depositLabel(label: string, t: QStrings): string {
  if (label === "First night") return t.firstNight;
  if (label === "First 30 nights") return t.first30;
  if (label === "Balance") return t.balance;
  if (label === "Full payment") return t.full;
  const m = /^(\d+(?:\.\d+)?)% deposit$/.exec(label);
  return m ? fill(t.pctDeposit, { pct: m[1] }) : label;
}
