/**
 * Sub-processors, one list for every page that names them: /security (table and
 * data-flow diagram), /subprocessors, /dpa, the consent ticks on /google-check
 * and /free-ota-audit, and the guest privacy notice.
 *
 * HONESTY RULE (03 section 5.3): the residency statement says where data is
 * TODAY, in this deployment, not where it could be. This deployment runs on
 * Google Cloud in India, region asia-south1 (Mumbai): Cloud Run serves the
 * website, the console, the guest pages and the API, Cloud SQL for PostgreSQL
 * holds the data, and Cloud Storage keeps job completion photos, deleted after
 * 7 days. AI runs on Google's Gemini models through Vertex AI's global
 * endpoint, so a request may be processed outside India, including in the
 * United States, and guest voice runs through a Gemini Live relay in
 * us-central1. Secrets are in Secret Manager and logs in Cloud Logging.
 * Guests consent to processing outside their own country (`transfer_abroad`)
 * before the assistant starts: India is abroad for most of them too.
 *
 * Providers of modules this deployment switches off (CLEFS_DISABLED_MODULES)
 * or does not configure are listed as "when_enabled", with what a hotel turns
 * on before they are used; pages that say what is in use leave them out.
 *
 * Countries are where processing happens, not where the company is registered.
 * Update this file in the same change that adds or swaps a vendor.
 */

// ─── Types ───────────────────────────────────────────────────────────────────

/** What a vendor touches. `none` means no personal data leaves Clefs for it. */
export type PersonalData = "guest" | "staff" | "lead" | "visitor" | "none";

/** `in_use`: part of this deployment today. `when_enabled`: only once a hotel turns `enabled_by` on. */
export type SubprocessorStatus = "in_use" | "when_enabled";

/**
 * Whether processing can happen outside India, where this deployment runs and
 * stores data: `no`, `may` (a global service picks the place per request) or
 * `yes`.
 */
export type OutsideIndia = "no" | "may" | "yes";

export type Subprocessor = {
  name: string;
  purpose: string;
  /** Categories of personal data it receives. */
  data: PersonalData[];
  /** Where the processing happens. */
  location: string;
  outside_india: OutsideIndia;
  status: SubprocessorStatus;
  /** For `when_enabled`: the feature a hotel turns on before it is used. */
  enabled_by?: string;
  /** Which part of Clefs uses it. */
  used_by: "platform" | "guest_assistant" | "service" | "phone" | "messaging" | "demand_radar" | "visibility" | "marketing_site";
  url: string;
  note?: string;
};

// ─── Deployment ──────────────────────────────────────────────────────────────

const INDIA = "India (Mumbai, asia-south1)";

// ─── List ────────────────────────────────────────────────────────────────────

export const SUBPROCESSORS: Subprocessor[] = [
  // In use: Google Cloud, project region asia-south1.
  {
    name: "Google Cloud Run", purpose: "Runs the website, the console, the guest pages and the API",
    data: ["guest", "staff", "visitor"], location: INDIA, outside_india: "no", status: "in_use",
    used_by: "platform", url: "https://cloud.google.com/run",
  },
  {
    name: "Google Cloud SQL for PostgreSQL", purpose: "The database for all hotel data",
    data: ["guest", "staff", "lead"], location: INDIA, outside_india: "no", status: "in_use",
    used_by: "platform", url: "https://cloud.google.com/sql",
  },
  {
    name: "Google Cloud Storage", purpose: "Photos staff take to show a job is done",
    data: ["guest", "staff"], location: INDIA, outside_india: "no", status: "in_use",
    used_by: "service", url: "https://cloud.google.com/storage",
    note: "A storage rule deletes each photo 7 days after it is taken.",
  },
  {
    name: "Google Vertex AI (Gemini models)",
    purpose: "AI for the guest assistant: understanding requests, answering from the hotel's approved information, translating jobs for staff and checking completion photos",
    data: ["guest", "staff"],
    location: "Vertex AI global endpoint: may be processed outside India, including in the United States",
    outside_india: "may", status: "in_use",
    used_by: "guest_assistant", url: "https://cloud.google.com/vertex-ai",
    note: "Guests consent to processing outside their country before the assistant starts.",
  },
  {
    name: "Google Vertex AI (Gemini Live)", purpose: "Guest voice conversations with the assistant",
    data: ["guest"], location: "United States (us-central1)", outside_india: "yes", status: "in_use",
    used_by: "guest_assistant", url: "https://cloud.google.com/vertex-ai",
    note: "Voice runs through a Gemini Live relay in us-central1.",
  },
  {
    name: "Google Cloud Logging", purpose: "Service logs: errors and request records, to run and secure the service",
    data: ["guest", "staff", "visitor"], location: "Google Cloud: may be stored outside India",
    outside_india: "may", status: "in_use",
    used_by: "platform", url: "https://cloud.google.com/logging",
  },
  {
    name: "Google Secret Manager", purpose: "Keys and passwords the service uses",
    data: ["none"], location: "Google Cloud", outside_india: "no", status: "in_use",
    used_by: "platform", url: "https://cloud.google.com/secret-manager",
    note: "Holds keys only, never guest data.",
  },
  {
    name: "Microsoft Clarity", purpose: "Session analytics (clicks and scrolls) on the marketing website only",
    data: ["visitor"], location: "United States", outside_india: "yes", status: "in_use",
    used_by: "marketing_site", url: "https://clarity.microsoft.com",
    note: "Not loaded in the console, guest pages or staff devices.",
  },

  // Used only when a hotel turns the feature on (off in this deployment).
  {
    name: "Meta (WhatsApp Business Platform)", purpose: "WhatsApp messages to guests and staff",
    data: ["guest", "staff", "lead"], location: "United States and Ireland", outside_india: "yes",
    status: "when_enabled", enabled_by: "WhatsApp messaging",
    used_by: "messaging", url: "https://business.whatsapp.com",
  },
  {
    name: "Resend", purpose: "Email: receipts, sign-in links and mail forwarded to Clefs",
    data: ["guest", "staff", "lead"], location: "United States", outside_india: "yes",
    status: "when_enabled", enabled_by: "email",
    used_by: "messaging", url: "https://resend.com",
  },
  {
    name: "Twilio", purpose: "SMS and phone numbers",
    data: ["guest", "staff"], location: "United States", outside_india: "yes",
    status: "when_enabled", enabled_by: "SMS or the room phone line",
    used_by: "phone", url: "https://www.twilio.com",
  },
  {
    // notify.send_sms routes every +91 recipient here (sms_provider_for), and
    // service escalations text staff and duty managers with room and task
    // detail. The transfer disclosure must name it wherever SMS is on.
    name: "MSG91", purpose: "SMS to Indian (+91) phone numbers, such as staff alerts",
    data: ["guest", "staff"], location: "India", outside_india: "no",
    status: "when_enabled", enabled_by: "SMS",
    used_by: "messaging", url: "https://msg91.com",
    note: "Used only for +91 numbers; other SMS goes through Twilio.",
  },
  {
    name: "SerpApi", purpose: "Google Hotels prices and positions for your hotel and competitors",
    data: ["none"], location: "United States", outside_india: "yes",
    status: "when_enabled", enabled_by: "Demand Radar",
    used_by: "demand_radar", url: "https://serpapi.com",
    note: "Receives hotel names and dates only. A third-party service; prices are indicative.",
  },
  {
    name: "Google", purpose: "Places and Business Profile data for your own listing",
    data: ["none"], location: "United States", outside_india: "yes",
    status: "when_enabled", enabled_by: "Visibility",
    used_by: "visibility", url: "https://developers.google.com/maps",
  },
  {
    name: "Perplexity", purpose: "AI answer checks for how assistants describe your hotel",
    data: ["none"], location: "United States", outside_india: "yes",
    status: "when_enabled", enabled_by: "Visibility",
    used_by: "visibility", url: "https://www.perplexity.ai",
    note: "Sends questions about hotels only, never guest data.",
  },
];

// ─── Residency statement ─────────────────────────────────────────────────────

export const RESIDENCY = {
  hosting: `Google Cloud Run, ${INDIA}`,
  database: `Cloud SQL for PostgreSQL, ${INDIA}`,
  photos: `Cloud Storage, ${INDIA}, deleted after 7 days`,
  ai: "Google Gemini models through Vertex AI's global endpoint, which may process data outside India, including in the United States",
  voice: "a Gemini Live relay in the United States (us-central1)",
  operations: "Secret Manager for keys and Cloud Logging for service logs, in the same Google Cloud project",
  /** Short labels for the data-flow diagram boxes. */
  short: {
    hosting: "Cloud Run, Mumbai, India",
    database: "Cloud SQL, India",
    photos: "Cloud Storage, India, 7 days",
    ai: "Gemini, global: may be US",
    voice: "Gemini Live, United States",
  },
  statement:
    "Clefs runs on Google Cloud in India. The website, the console, the guest pages and the API run on Cloud Run, and hotel data is stored in Cloud SQL for PostgreSQL, both in the Mumbai region (asia-south1). Photos that staff take to show a job is done are kept in Cloud Storage in the same region and deleted after 7 days. AI runs on Google's Gemini models through Vertex AI's global endpoint, so a guest's messages may be processed outside India, including in the United States, and guest voice runs through a Gemini Live relay in the United States. For most guests all of this is processing outside their own country, so the assistant asks for their consent before it starts.",
} as const;

/** The providers this deployment uses today. */
export function inUseSubprocessors(): Subprocessor[] {
  return SUBPROCESSORS.filter((s) => s.status === "in_use");
}

/** The providers a hotel switches on with a feature, unused until then. */
export function optionalSubprocessors(): Subprocessor[] {
  return SUBPROCESSORS.filter((s) => s.status === "when_enabled");
}

/** In-use vendors that receive personal data, for consent ticks and notices. */
export function personalDataSubprocessors(): Subprocessor[] {
  return inUseSubprocessors().filter((s) => !s.data.includes("none"));
}
