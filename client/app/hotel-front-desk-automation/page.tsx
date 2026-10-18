import type { Metadata } from "next";
import SolutionPage from "../components/SolutionPage";
import { SITE, jsonLd, organizationSchema, breadcrumbSchema, faqSchema } from "../lib/seo";
import type { Faq } from "../lib/seo";

const PATH = "/hotel-front-desk-automation";
const TITLE = "Hotel Front Desk Automation";

export const metadata: Metadata = {
  title: TITLE,
  description:
    "Take the routine calls off your hotel front desk. Guests scan one QR code instead of dialling extensions, and every real request becomes a tracked job for the right team.",
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, url: `${SITE}${PATH}`, type: "article" },
};

const FAQS: Faq[] = [
  {
    q: "What exactly does this automate?",
    a: "The repeated part of the job. Answering the same questions all day, and passing simple requests to other teams. It does not automate checking in a tired family, handling a complaint, or any decision that needs judgement. Those stay with your staff, and that is the point.",
  },
  {
    q: "Will I need fewer front desk staff?",
    a: "That is not how most hotels use it, and we would rather say so plainly. What changes is how your team spends the shift. A desk that is not answering the phone forty times a day can actually look after the person standing in front of it.",
  },
  {
    q: "What happens at night when nobody is at the desk?",
    a: "This is where the gap is biggest. At night a guest with a simple question either waits until morning or wakes someone up. Clefs answers at 3am the same way it answers at 3pm. Anything needing a person is held for the right team in the morning, not lost.",
  },
  {
    q: "Does it work with my booking system?",
    a: "Clefs works alongside your booking system. It is not a booking or rates system and does not replace one. If you want guest and room details to pass between the two, that is worth checking against your exact system on a demo, because every system is different.",
  },
];

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Service Control", path: "/service-control" }, { name: "Front desk automation", path: PATH }]),
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
        eyebrow="Front desk automation"
        h1="Hotel front desk automation"
        h1Accent="that protects the human part."
        intro="Every call to your desk interrupts whoever your staff were helping. Most of those calls are the same few questions. Clefs answers them instead, and your team keeps the work that really needs a person."
        parent={{ href: "/service-control", label: "Service Control" }}
        sections={[
          {
            h2: "What comes off your desk",
            body: [
              "The work that can be automated is the work with one correct answer that your hotel already knows. There is no judgement in a WiFi password, and no warmth lost by answering it instantly.",
            ],
            list: [
              { t: "Repeated questions", d: "WiFi, checkout and late checkout, gym and pool hours, breakfast, parking. Answered at once, at any hour." },
              { t: "Simple requests", d: "Towels, toiletries, room service, a maintenance report. Sent straight to the team instead of relayed by whoever picked up the phone." },
              { t: "Night cover", d: "The hours with nobody at the desk stop being hours when guests cannot get an answer." },
              { t: "Returning guests", d: "Past preferences appear automatically. Nobody has to look them up when the desk is busy." },
            ],
          },
          {
            h2: "What stays with your team",
            body: [
              "Welcoming guests. Complaints. Anything with feelings involved. Any moment where your hotel's character is what the guest is really paying for. None of this should be automated, and software that tries makes the stay worse.",
              "The test of good automation here is simple. Is the person at your desk more available to the guest in front of them than before? If yes, it is working.",
            ],
          },
          {
            h2: "What you will see change",
            body: [
              "Two things move first. How long a guest waits for an answer, and how much of the shift your desk spends on the phone. Because every request is tracked, you can measure both instead of guessing.",
              "The reports also settle old arguments. Which shift is really short-staffed, and which team is really the slow one.",
            ],
          },
        ]}
        faqs={FAQS}
        related={[
          { href: "/service-control", label: "Service Control", blurb: "One queue for every guest request, with an owner and a clock." },
          { href: "/ai-concierge-software", label: "AI concierge software", blurb: "The guest side: instant answers by voice or typing." },
          { href: "/guest-request-management", label: "Guest request management", blurb: "How requests are saved, sent to a team and tracked." },
          { href: "/multilingual-guest-messaging", label: "Multilingual guest messaging", blurb: "Guests ask in their language. Your team reads it in yours." },
        ]}
      />
    </>
  );
}
