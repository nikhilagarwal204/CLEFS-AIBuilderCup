/**
 * Sub-processors, one list for every page that names them: /security (table and
 * data-flow diagram), /subprocessors, the consent ticks on /google-check and
 * /free-ota-audit ("processed outside the UAE by Clefs' sub-processors (list)")
 * and the guest privacy notice.
 *
 * HONESTY RULE (03 section 5.3): the residency statement says where data is
 * TODAY, not where it could be. The database is Neon in Singapore; AI
 * processing is in the United States under the guest's `transfer_abroad`
 * consent. UAE hosting (AWS me-central-1 or Azure UAE North) is available when a
 * hotel's contract requires it, and moving the database alone does not remove
 * the AI transfer, so the copy says that too. PDPL executive-regulation detail
 * is for counsel to confirm.
 *
 * Countries are where processing happens, not where the company is registered.
 * Update this file in the same change that adds or swaps a vendor.
 */

// ─── Types ───────────────────────────────────────────────────────────────────

/** What a vendor touches. `none` means no personal data leaves Clefs for it. */
export type PersonalData = "guest" | "staff" | "lead" | "visitor" | "none";

export type Subprocessor = {
  name: string;
  purpose: string;
  /** Categories of personal data it receives. */
  data: PersonalData[];
  /** Where the processing happens. */
  location: string;
  /** True when using it moves personal data outside the UAE. */
  transfer_abroad: boolean;
  /** Which part of Clefs uses it. */
  used_by: "platform" | "guest_assistant" | "phone" | "messaging" | "demand_radar" | "visibility" | "marketing_site";
  url: string;
  note?: string;
};

// ─── List ────────────────────────────────────────────────────────────────────

export const SUBPROCESSORS: Subprocessor[] = [
  {
    name: "Neon", purpose: "Primary database (PostgreSQL) for all hotel data",
    data: ["guest", "staff", "lead"], location: "Singapore", transfer_abroad: true,
    used_by: "platform", url: "https://neon.tech",
    note: "UAE hosting is available when a hotel's contract requires it.",
  },
  {
    name: "Vercel", purpose: "Hosting for the website, console and guest pages",
    data: ["guest", "staff", "visitor"], location: "Global edge network; United States", transfer_abroad: true,
    used_by: "platform", url: "https://vercel.com",
  },
  {
    name: "Google (Gemini API)", purpose: "AI processing for the guest assistant, phone assistant, brief and document reading",
    data: ["guest", "lead"], location: "United States", transfer_abroad: true,
    used_by: "guest_assistant", url: "https://ai.google.dev",
    note: "Guests consent to this transfer before the assistant starts. Paid-tier Gemini API terms: prompts are not used to train Google's models.",
  },
  {
    name: "Meta (WhatsApp Business Platform)", purpose: "WhatsApp messages to guests and owners",
    data: ["guest", "staff", "lead"], location: "United States and Ireland", transfer_abroad: true,
    used_by: "messaging", url: "https://business.whatsapp.com",
  },
  {
    name: "Resend", purpose: "Email: briefs, receipts and sign-in links, and mail forwarded to Clefs",
    data: ["guest", "staff", "lead"], location: "United States", transfer_abroad: true,
    used_by: "messaging", url: "https://resend.com",
  },
  {
    name: "Twilio", purpose: "SMS and phone numbers",
    data: ["guest", "staff"], location: "United States", transfer_abroad: true,
    used_by: "phone", url: "https://www.twilio.com",
  },
  {
    // notify.send_sms routes every +91 recipient here (sms_provider_for), and
    // service escalations text staff and duty managers with room and task
    // detail; UAE hotels often have staff on Indian numbers. 03 spec 5 plans
    // to replace it, but until then the transfer disclosure must name it.
    name: "MSG91", purpose: "SMS to Indian (+91) phone numbers, such as staff alerts",
    data: ["guest", "staff"], location: "India", transfer_abroad: true,
    used_by: "messaging", url: "https://msg91.com",
    note: "Used only for +91 numbers; other SMS goes through Twilio.",
  },
  {
    name: "SerpApi", purpose: "Google Hotels prices and positions for your hotel and competitors",
    data: ["none"], location: "United States", transfer_abroad: false,
    used_by: "demand_radar", url: "https://serpapi.com",
    note: "Receives hotel names and dates only. A third-party service; prices are indicative.",
  },
  {
    name: "Google", purpose: "Places and Business Profile data for your own listing",
    data: ["none"], location: "United States", transfer_abroad: false,
    used_by: "visibility", url: "https://developers.google.com/maps",
  },
  {
    name: "Perplexity", purpose: "AI answer checks for how assistants describe your hotel",
    data: ["none"], location: "United States", transfer_abroad: false,
    used_by: "visibility", url: "https://www.perplexity.ai",
    note: "Sends questions about hotels only, never guest data.",
  },
  {
    name: "Microsoft Clarity", purpose: "Session analytics (clicks and scrolls) on the marketing website only",
    data: ["visitor"], location: "United States", transfer_abroad: true,
    used_by: "marketing_site", url: "https://clarity.microsoft.com",
    note: "Not loaded in the console, guest pages or staff devices.",
  },
];

// ─── Residency statement ─────────────────────────────────────────────────────

export const RESIDENCY = {
  database: "Singapore (Neon)",
  ai: "United States (Google Gemini API)",
  uaeOption: "AWS me-central-1 or Azure UAE North, when a hotel's contract requires it",
  statement:
    "Today our database runs in Singapore and AI processing runs in the United States. UAE hosting is available when your contract requires it. Moving the database does not stop AI processing abroad, so guests are asked for consent to that transfer before the assistant starts.",
} as const;

/** Vendors that receive personal data, for consent ticks and notices. */
export function personalDataSubprocessors(): Subprocessor[] {
  return SUBPROCESSORS.filter((s) => !s.data.includes("none"));
}
