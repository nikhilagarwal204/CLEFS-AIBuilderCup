import type { Faq } from "./seo";

/**
 * Every FAQ on the site renders from this ONE array - the visible accordion and
 * the FAQPage schema both read it, which is what stops them drifting apart.
 * Google issues structured-data manual actions when marked-up FAQ content is not
 * visible on the page.
 *
 * WRITING RULES. The reader is a hotel owner or GM, often reading English as a
 * second language, and is not a software buyer by trade:
 *   - Answer in the first sentence. Explain after.
 *   - Short sentences. Under 15 words where possible, never past 25.
 *   - Everyday words. No idioms.
 *
 * CLAIM RULE, added after an audit: state nothing we cannot source. The hotel
 * telephone statistics in circulation ("60% of front desk calls are repetitive",
 * "200-400 inbound calls a day") recirculate across vendor blogs with no primary
 * study behind any of them, and the site's old "80% of routine calls deflected"
 * came from that same family. This link is sent by the founder directly to GMs,
 * so one "where is that from?" ends a meeting. Product facts only, plus the
 * invitation for the hotel to measure its own number.
 *
 * UAE REWRITE (Canon 21, 22; 04 section 2.3): every market number now lives in
 * lib/claims.ts and renders through <Claim>, and check-claims.mjs fails the
 * build on an untagged percentage or currency amount here. These answers carry
 * no market statistics at all: they are product facts, stated for hotels in the
 * UAE, branded or not. Say "hotels", never "independent hotels only".
 *
 * AI BUILDER CUP (05 spec): the homepage tells the guest service desk story,
 * so these answers do too. The one price answer is tagged with Clefs' own
 * price-list row (claims.ts clefs_price_list_2026). Say "in your language",
 * never "any language" (the registry bans it).
 */
export const HOME_FAQS: Faq[] = [
  {
    q: "What is Clefs?",
    a: "Clefs is an AI guest service desk for hotels. A guest scans the QR card in the room and asks once, by voice or text, in their language. Clefs turns each ask into a job for the right free staff member, in that person's language, and closes it only with proof.",
  },
  {
    q: "Do guests need to download an app?",
    a: "No. The QR card in the room opens a web page on the guest's own phone. The guest agrees to the AI processing first, and the assistant says it is an AI.",
  },
  {
    q: "Which languages does it speak?",
    a: "Eight guest languages today: English, Arabic, Russian, Hindi, Urdu, Chinese, German and French. Each staff member reads the job in their own language, and their replies reach the guest in the guest's language.",
  },
  {
    q: "How does Clefs avoid making up answers?",
    a: "It answers only from facts your hotel approved, such as check-out time or the Wi-Fi password. If the answer is not there, it says so and passes the question to a person. It never promises a price, a refund or an exception.",
  },
  {
    q: "What happens when nobody takes a job?",
    a: "Clefs climbs the ladder on its own. If no one accepts the job, the supervisor is told after 3 minutes, the duty manager after 5 and the GM after 10, and a late job moves to the next free person. Emergency words page the duty manager at once.",
  },
  {
    q: "How do we know a job was really done?",
    a: "Staff mark a job done with a photo. Gemini checks the photo against the request before the job can close. Then the guest is asked if everything is fine. If not, the job reopens and the duty manager is asked to approve a gesture before the guest writes a review.",
  },
  {
    q: "What does the hotel need to start?",
    a: "QR cards for the rooms, one shared staff tablet, and your staff names and roles. Clefs does not need access to your PMS, financial files or card data, and your phones stay as they are.",
  },
  {
    q: "How much does Clefs cost?",
    // claim: clefs_price_list_2026
    a: "USD 3 per room per month with voice and text, and at least USD 99 a month. A text-only plan is USD 1.50 per room per month. The 30-day pilot is free.",
  },
  {
    q: "Is it safe to put in front of guests?",
    a: "Before going live, every hotel must pass 22 safety and accuracy tests, with Gemini as the judge, and run 7 days in shadow mode, where the desk works out every escalation without messaging anyone. Guests give consent first, and staff stay in charge of every exception.",
  },
];
