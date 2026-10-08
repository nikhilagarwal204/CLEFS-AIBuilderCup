import type { Metadata } from "next";
import SolutionPage from "../components/SolutionPage";
import { BRAND, PRIMARY_LEAD, SITE, jsonLd, organizationSchema, breadcrumbSchema, faqSchema } from "../lib/seo";
import type { Faq } from "../lib/seo";

// ─── Page facts ──────────────────────────────────────────────────────────────

const PATH = "/service-control";
const TITLE = "Service Control: Guest Requests Tracked from Asked to Done";
const DESCRIPTION =
  "Service Control puts every guest request, from the room QR code, the in-room phone, WhatsApp and the front desk, in one queue with an owner, a timer and an escalation path, and reports what was fixed and how fast.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, description: DESCRIPTION, url: `${SITE}${PATH}`, type: "article" },
};

const FAQS: Faq[] = [
  {
    q: "Where do requests come from?",
    a: "The QR code in the room (voice or text), the in-room menu, the in-room phone assistant, the front desk phone console, WhatsApp, and staff who log a request themselves. They all land in the same queue.",
  },
  {
    q: "Which languages can guests use?",
    a: "Arabic, English and six more, tested for your hotel before go-live. Your team reads each request in their own language.",
  },
  {
    q: "What happens if nobody picks a request up?",
    a: "The alert repeats on the team device until someone acknowledges it. If the acknowledgement or the fix runs past the time you set for that category, it escalates to the supervisor, then the manager on duty, then the GM on WhatsApp.",
  },
  {
    q: "What about emergencies?",
    a: "Some requests never wait in a queue. Medical, fire, gas, security and harassment reports go straight to the people you name, and the assistant gives the guest the UAE emergency numbers at once.",
  },
  {
    q: "Do staff need their own phones?",
    a: "No. A shared device per floor or department works, with each person signing in to their shift with a PIN. Handover notes carry across shifts.",
  },
];

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Service Control", path: PATH }]),
  {
    "@type": "WebPage",
    "@id": `${SITE}${PATH}#webpage`,
    url: `${SITE}${PATH}`,
    name: TITLE,
    description: DESCRIPTION,
    about: { "@id": `${SITE}/#software` },
    inLanguage: "en",
  },
  faqSchema(FAQS, PATH),
]);

// ─── Page ────────────────────────────────────────────────────────────────────

export default function Page() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <SolutionPage
        eyebrow="Service Control"
        h1="Every guest request, tracked from asked to done."
        h1Accent="One queue. One owner. One clock."
        intro={DESCRIPTION}
        // The demo, not /pilot: the pilot is Brief and Profit Guard (Inquiry
        // Guard on request) and has no Service Control, so "How the pilot
        // works" sent a Service Control reader to a pilot without it.
        cta={{
          primary: { href: BRAND.demoUrl, label: "Book a demo", external: true },
          secondary: { href: PRIMARY_LEAD.href, label: PRIMARY_LEAD.label },
        }}
        sections={[
          {
            h2: "One queue for every channel",
            body: [
              "Guests ask in the way that suits them: the QR code in the room, the room phone, WhatsApp or the front desk. Each request becomes one job with the room number, the category, the words the guest used and a name against it.",
              "Simple questions (WiFi, breakfast hours, checkout) are answered at once from what your hotel has approved. Anything that needs a person goes to the right team on the right floor.",
            ],
            list: [
              { t: "Floor and department routing", d: "Housekeeping on floor 4 sees floor 4. Maintenance sees faults, graded by urgency." },
              { t: "Acknowledge, then fix", d: "Two clocks per request: how fast someone took it, and how fast it was done." },
              { t: "Escalation that does not sleep", d: "Repeating alerts until acknowledged, then supervisor, manager on duty and GM on WhatsApp." },
              { t: "The guest sees progress", d: "A status tracker on the guest's phone, with resolved or not resolved, and a reopen button." },
            ],
          },
          {
            h2: "When something goes wrong",
            body: [
              "Sensitive requests follow hard routes you set once: medical, fire, security and harassment reach named people immediately, with the UAE emergency numbers given to the guest.",
              "Service recovery follows your authority matrix. A supervisor can offer what a supervisor may offer, and anything larger waits for approval.",
            ],
          },
          {
            h2: "What the owner sees",
            body: [
              "First response, completion, reopened requests and missed deadlines by team, floor, shift, room type and language. The morning brief lists the service risks next to the money ones, so one read covers both.",
            ],
          },
          {
            h2: "Service Control guides",
            body: ["Four short guides go deeper on the parts hotels ask about most."],
            list: [
              { t: "AI concierge software", d: "What a concierge does, and what most hotels need instead." },
              { t: "Guest request management", d: "How requests are recorded, routed and closed." },
              { t: "Front desk automation", d: "What comes off the desk, and what stays with your staff." },
              { t: "Multilingual guest messaging", d: "Guests ask in their language, your team reads it in theirs." },
            ],
          },
        ]}
        faqs={FAQS}
        related={[
          { href: "/ai-concierge-software", label: "AI concierge software", blurb: "What a concierge does, and what most hotels need instead." },
          { href: "/guest-request-management", label: "Guest request management", blurb: "How requests are recorded, routed and closed." },
          { href: "/hotel-front-desk-automation", label: "Front desk automation", blurb: "What comes off your desk, and what stays with your staff." },
          { href: "/multilingual-guest-messaging", label: "Multilingual guest messaging", blurb: "Arabic, English and more, read by your team in theirs." },
        ]}
      />
    </>
  );
}
