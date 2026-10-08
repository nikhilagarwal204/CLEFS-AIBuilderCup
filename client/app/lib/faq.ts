import { PRICING_NOTE } from "./pricing";
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
 */
export const HOME_FAQS: Faq[] = [
  {
    q: "What is Clefs?",
    a: "Clefs finds the profit leaks your hotel systems miss. It checks OTA payouts and rates against your own records. It watches demand around your hotel. Every morning it sends the owner a short list of actions to approve, each with its evidence. It also runs guest requests for your team, with an Arabic and English guest assistant.",
  },
  {
    q: "Do I have to replace my PMS or channel manager?",
    a: "No. Clefs works next to the systems you already use. It reads your reservation exports, OTA statements and rate calendar. Your PMS and channel manager stay in charge of bookings and rates. Clefs never changes a rate or a booking on its own.",
  },
  {
    q: "How does the free OTA audit work?",
    a: "You upload one OTA statement, as a PDF, CSV or Excel file. Clefs checks each line and shows the reason and the calculation. The total is an estimate of commission charged on no-shows, cancellations and refunded amounts. Fee lines, and percentages above a default per OTA, are marked to confirm with you and left out of the total. The file is deleted after 30 days.",
  },
  {
    q: "Who approves the actions Clefs suggests?",
    a: "You do, or the person you choose. Every action comes with its evidence and the amount at stake. Refunds, rate changes and OTA changes always wait for a person to approve them. For routine tasks, you decide how much Clefs may do on its own, and you can take that back at any time. After an action runs, Clefs checks the result and shows you the value it verified.",
  },
  {
    q: "Where is my hotel's data stored?",
    a: "Today our database runs in Singapore, and AI processing runs in the United States. Hosting in the UAE is available when your contract requires it. Moving the database does not stop AI processing abroad, so guests are asked for consent to that transfer. Our security page lists every sub-processor and its country.",
  },
  {
    q: "Which languages does the guest assistant speak?",
    a: "Arabic, English and six more, tested for your hotel before go-live. Guests can speak or type. Your team always reads the request in their own language.",
  },
  {
    q: "How does the pilot work?",
    a: "The pilot runs for 14 days and is free. Day 1 is the day we import your first file. Days 1 to 3 set a baseline, Days 4 to 13 run live, and Day 14 is the report. Before Day 1 we write down with you what success means.",
  },
  {
    q: "Is Clefs only for independent hotels?",
    a: "No. Clefs works for any hotel in the UAE, branded or independent. A general manager of a chain property uses the same product and the same demo.",
  },
  {
    q: "How much does Clefs cost?",
    // The pilot letter's terms, the one sentence /pilot shows (lib/pricing.ts).
    // check-claims judges its value there (a *_NOTE constant), not here.
    a: PRICING_NOTE,
  },
];
