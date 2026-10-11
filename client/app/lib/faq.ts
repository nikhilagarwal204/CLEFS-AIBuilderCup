import type { Faq } from "./seo";

/**
 * Every FAQ on the site root renders from this ONE array - the visible
 * accordion and the FAQPage schema both read it, which is what stops them
 * drifting apart. Google issues structured-data manual actions when marked-up
 * FAQ content is not visible on the page.
 *
 * WRITING RULES. The readers are AI Builder Cup judges with two minutes, and
 * hotel owners and GMs, often reading English as a second language:
 *   - Answer in the first sentence. Explain after.
 *   - Short sentences. Under 15 words where possible, never past 25.
 *   - Everyday words. No idioms, and no system terms.
 *
 * CLAIM RULE, added after an audit: state nothing we cannot source. The hotel
 * telephone statistics in circulation ("60% of front desk calls are repetitive",
 * "200-400 inbound calls a day") recirculate across vendor blogs with no primary
 * study behind any of them, and the site's old "80% of routine calls deflected"
 * came from that same family. Product facts only, plus the invitation for the
 * hotel to measure its own number. Every market number lives in lib/claims.ts,
 * and check-claims.mjs fails the build on an untagged percentage or currency
 * amount here. The one price answer is tagged with Clefs' own price-list row
 * (claims.ts clefs_price_list_2026). Say "hotels", never "independent hotels
 * only", and "in their own language", never the "any" form (the registry
 * bans it).
 */
export const HOME_FAQS: Faq[] = [
  {
    q: "What is Clefs?",
    a: "Clefs is an AI front desk for hotels. A guest scans the QR card in the room and asks for anything, by voice or text, in their own language. Clefs gives the job to the right staff member, in that person's language, and closes it only when a photo shows it is done.",
  },
  {
    q: "Do guests need to download an app?",
    a: "No. The QR card opens a web page on the guest's own phone. The guest agrees to chat with an AI first, and the assistant always says it is an AI.",
  },
  {
    q: "Which languages does it speak?",
    a: "Guests can use 11 languages: English, Japanese, Korean, Chinese, Thai, Hindi, Arabic, Russian, Urdu, German and French. Each staff member reads the job in their own language, and their replies reach the guest in the guest's language.",
  },
  {
    q: "How does Clefs avoid making things up?",
    a: "It answers only from facts your hotel has approved, such as check-out time or the Wi-Fi password. If the answer is not there, it says so and passes the question to a person. It never promises a price, a refund or an exception.",
  },
  {
    q: "What happens when nobody takes a job?",
    a: "Clefs moves it up on its own. If nobody accepts the job, the supervisor is told after 3 minutes, and the job goes to the next free person. The duty manager is told after 5 minutes, and the general manager after 10. Emergency words reach the duty manager at once.",
  },
  {
    q: "How do we know a job was really done?",
    a: "Staff finish a job with a photo. Gemini checks the photo against the request before the job can close. Then the guest is asked if everything is fine. If not, the job opens again, and the duty manager is asked to approve a small gift to say sorry.",
  },
  {
    q: "What does a hotel need to start?",
    a: "QR cards for the rooms, one shared staff tablet, and your staff names and roles. Clefs does not need access to your hotel system, financial files or card data, and your phones stay as they are.",
  },
  {
    q: "How much does Clefs cost?",
    // claim: clefs_price_list_2026
    a: "$3 per room per month with voice and text, with a minimum of $99 a month. A text-only plan is $1.50 per room per month. The 30-day pilot is free.",
  },
  {
    q: "Is it safe to put in front of guests?",
    a: "Yes. Before going live, every hotel must pass 22 safety and accuracy checks, with Gemini as the judge. Then comes a 7-day trial run: Clefs shows the team every alert it would send, without sending any. Guests agree first, and staff stay in charge of every exception.",
  },
];
