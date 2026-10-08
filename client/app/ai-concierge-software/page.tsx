import type { Metadata } from "next";
import SolutionPage from "../components/SolutionPage";
import { SITE, jsonLd, organizationSchema, breadcrumbSchema, faqSchema } from "../lib/seo";
import type { Faq } from "../lib/seo";

const PATH = "/ai-concierge-software";
const TITLE = "AI Concierge Software for Hotels";

export const metadata: Metadata = {
  title: TITLE,
  description:
    "Looking for AI concierge software? Most hotels need something simpler. One QR code in the room, guests answered in Arabic, English and more, and every request tracked to done.",
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, url: `${SITE}${PATH}`, type: "article" },
};

const FAQS: Faq[] = [
  {
    q: "What is the difference between an AI concierge and this?",
    a: "A concierge plans travel, books restaurants and arranges tickets. Many hotels do not have that department at all. What every hotel does have is a front desk and a room phone handling the everyday requests. Clefs is built for those. It can suggest places nearby, but it does not book travel.",
  },
  {
    q: "So can Clefs do concierge work?",
    a: "Partly, and we are clear about the line. It answers questions about your hotel and the area around it. It does not make bookings with outside vendors. If your hotel has a concierge, those requests are passed to them. If it does not, they go to your front desk with the room number attached.",
  },
  {
    q: "Why do people search for AI concierge software?",
    a: "Because it is the phrase the software industry uses. Hoteliers often arrive looking for it and find that what they actually need is simpler: a faster way for guests to ask for things, and a way to make sure every request reaches the right person and gets closed.",
  },
  {
    q: "How long does setup take?",
    a: "We load what your hotel already knows: WiFi, hours, rules, menus, and which team handles what. Then you print the QR codes for your rooms. There is no new system to move to and nothing for guests to install. The main thing we need is your information.",
  },
];

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Service Control", path: "/service-control" }, { name: TITLE, path: PATH }]),
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
        eyebrow="AI concierge software"
        h1="Looking for AI concierge software?"
        h1Accent="Here is what hotels usually need instead."
        intro="A concierge plans travel and books restaurants, and most hotels do not have one. What every hotel has is a front desk taking the same requests all day. Clefs is built for those. Guests scan a code and ask, and every request is tracked to done."
        parent={{ href: "/service-control", label: "Service Control" }}
        sections={[
          {
            h2: "What a concierge actually does",
            body: [
              "A hotel concierge arranges things outside the hotel. Restaurant tables, tickets, cars, tours. It is a specialist role, and it exists mostly in large luxury properties.",
              "The requests that fill a normal day are different. WiFi passwords, checkout times, extra towels, room service, a tap that is leaking. Those go to the front desk or the telephone operator, not to a concierge.",
            ],
          },
          {
            h2: "What Clefs does instead",
            body: [
              "Your guest scans one QR code in the room and asks for anything, by voice or by typing, in their own language. Simple questions are answered at once from what your hotel has told it.",
              "Anything that needs a person becomes a job on your team's screen, with the room number, the request and a name against it. Your staff mark it done, and you can see how long it took.",
            ],
            list: [
              { t: "One code, not a list of extensions", d: "Guests stop working out whether to dial reception, housekeeping or room service." },
              { t: "Nobody on hold", d: "The question is answered at once, or it becomes a job with an owner." },
              { t: "Arabic, English and more", d: "Guests ask in theirs. Your team reads it in yours." },
              { t: "Every request tracked", d: "Room number, staff name and a timer, from asked to finished." },
              { t: "Works outside rooms too", d: "A QR code in the lounge, at the pool or in the spa knows where it is." },
            ],
          },
          {
            h2: "Where the line is",
            body: [
              "Clefs will recommend places near your hotel. It will not book them. It does not sell rooms, take payment or manage rates, and it will not replace your booking system.",
              "If your property has a concierge, requests of that kind are handed to them. If it does not, they reach your front desk with the guest's room number already attached. Either way nothing is lost in the middle.",
            ],
          },
        ]}
        faqs={FAQS}
        related={[
          { href: "/service-control", label: "Service Control", blurb: "One queue for every guest request, with an owner and a clock." },
          { href: "/hotel-front-desk-automation", label: "Front desk automation", blurb: "What comes off your desk, and what stays with your staff." },
          { href: "/guest-request-management", label: "Guest request management", blurb: "How requests are saved, sent to a team and tracked." },
          { href: "/multilingual-guest-messaging", label: "Multilingual guest messaging", blurb: "Guests ask in their language. Your team reads it in yours." },
        ]}
      />
    </>
  );
}
