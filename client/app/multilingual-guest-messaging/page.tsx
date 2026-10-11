import type { Metadata } from "next";
import SolutionPage from "../components/SolutionPage";
import { SITE, jsonLd, organizationSchema, breadcrumbSchema, faqSchema } from "../lib/seo";
import type { Faq } from "../lib/seo";

const PATH = "/multilingual-guest-messaging";
const TITLE = "Multilingual Guest Messaging for Hotels";

export const metadata: Metadata = {
  title: TITLE,
  description:
    "Hotel guests ask in Arabic, English and more, by voice or text, day and night. Your team reads every request in its own language. Multilingual coverage without multilingual staffing.",
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, url: `${SITE}${PATH}`, type: "article" },
};

const FAQS: Faq[] = [
  {
    q: "Which languages can my guests use?",
    a: "Eleven: English, Japanese, Korean, Chinese and Thai, plus Arabic, Hindi, Urdu, Russian, German and French, tested for your hotel before go-live. Guests ask by voice or by typing, and your team reads the request in your language. The real test for a hotel is simple: can a guest ask for something at 2am without finding a staff member who speaks their language? That is what this is built for.",
  },
  {
    q: "Do my staff need to speak the guest's language?",
    a: "No, and that is the whole point. The translation happens before the job reaches your team. What appears on their screen is a request they can read and act on straight away. You do not need to hire staff for every language your guests speak.",
  },
  {
    q: "Does voice work well for guests who do not speak English?",
    a: "Voice is often easier for them than typing. Many guests find their phone keyboard hard to use in their own script. Speaking is faster. Both voice and typing create the same job for your team, so guests can use whichever they prefer.",
  },
  {
    q: "Will this help our reviews from foreign guests?",
    a: "We do not promise review scores. What we see is that many lukewarm comments from foreign guests are not about the room but about how hard it was to ask for things. When asking becomes simple, guests stop holding back requests they could not explain before, and your team can fix them during the stay.",
  },
];

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Service Control", path: "/service-control" }, { name: "Multilingual guest messaging", path: PATH }]),
  {
    "@type": "WebPage",
    "@id": `${SITE}${PATH}#webpage`,
    url: `${SITE}${PATH}`,
    name: TITLE,
    about: { "@id": `${SITE}/#software` },
    inLanguage: "en",
  },
  faqSchema(FAQS, PATH),
]);

export default function Page() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: schema }} />
      <SolutionPage
        eyebrow="Multilingual guest messaging"
        h1="Multilingual guest messaging"
        h1Accent="Guests ask in their own language."
        intro="Picking up a phone to speak to a stranger in a second language is hard, so guests stop asking. Scanning a code and typing in their own language is not. Clefs takes the request either way, and your team gets a job they can read in yours."
        parent={{ href: "/service-control", label: "Service Control" }}
        sections={[
          {
            h2: "The problem starts before the complaint",
            body: [
              "Hotels usually measure language by whether staff can solve a problem in the end. Guests feel it much earlier. They decide whether asking is worth the effort at all.",
              "You never see that decision in your data. The guest who wanted an extra pillow and did not ask does not become a complaint. They become a slightly cooler review, months later, and you never learn why.",
            ],
          },
          {
            h2: "How it works",
            body: [
              "The guest scans the QR code in the room and speaks or types in their language. Clefs answers directly if it is a question. If a person is needed, it creates a job, and that job reaches your team in your language.",
            ],
            list: [
              { t: "Voice or typing", d: "Guests choose. Speaking is often easier than typing in their own script on a phone." },
              { t: "Day and night", d: "The late hours, when your desk is thinnest, are covered the same as any other time." },
              { t: "One language for staff", d: "Requests arrive translated and clearly laid out. Nobody on your team has to work out what was meant." },
              { t: "Nothing to install", d: "Nothing for the guest to download in a language they cannot read. The QR code opens straight into theirs." },
            ],
          },
          {
            h2: "Who gains most",
            body: [
              "Hotels with guests from many countries, and hotels running with few staff at night. In both cases the alternative is waking a specific staff member, or asking a guest to wait until morning.",
              "One honest limit. This removes the language barrier around requests and questions. It does not replace the warmth of talking to someone who truly speaks your guest's language. No software does.",
            ],
          },
        ]}
        faqs={FAQS}
        related={[
          { href: "/service-control", label: "Service Control", blurb: "One queue for every guest request, with an owner and a clock." },
          { href: "/ai-concierge-software", label: "AI concierge software", blurb: "The guest side: instant answers by voice or typing." },
          { href: "/guest-request-management", label: "Guest request management", blurb: "How requests are saved, sent to a team and tracked." },
          { href: "/hotel-front-desk-automation", label: "Front desk automation", blurb: "What comes off your desk, and what stays with your staff." },
        ]}
      />
    </>
  );
}
