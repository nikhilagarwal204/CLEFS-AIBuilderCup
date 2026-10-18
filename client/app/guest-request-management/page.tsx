import type { Metadata } from "next";
import SolutionPage from "../components/SolutionPage";
import { SITE, jsonLd, organizationSchema, breadcrumbSchema, faqSchema } from "../lib/seo";
import type { Faq } from "../lib/seo";

const PATH = "/guest-request-management";
const TITLE = "Hotel Guest Request Management System";

export const metadata: Metadata = {
  title: TITLE,
  description:
    "Capture every hotel guest request, assign it to the right department automatically, and track time-to-resolve until it is closed. Nothing lost in a group chat.",
  alternates: { canonical: PATH },
  openGraph: { title: `${TITLE} | Clefs`, url: `${SITE}${PATH}`, type: "article" },
};

const FAQS: Faq[] = [
  {
    q: "What is a guest request management system?",
    a: "It turns something a guest asked for into a job with a name on it and a finish time. Without one, requests live in people's heads, on paper, or in group chats. That is why they get forgotten. With one, every request has a clear status and one person responsible.",
  },
  {
    q: "How does a request reach the right team?",
    a: "Clefs reads what the guest asked for and sends it to the right team. Towels go to housekeeping. A drink goes to the kitchen. A broken air conditioner goes to maintenance. Nobody has to sit in the middle and decide, which is where most delays and mistakes happen.",
  },
  {
    q: "How do you stop a request being forgotten?",
    a: "Every request becomes a job with a room number, a staff name and a running timer. It stays on the screen until someone marks it done. Because the timer is visible while the job is open, a supervisor can see at a glance if something is taking too long.",
  },
  {
    q: "What reports do I get?",
    a: "You see how fast your team replies, how long jobs take to finish, and how much each team is handling. These are the numbers you need for a monthly review. They are collected automatically, so nobody has to sit down and prepare them.",
  },
  {
    q: "Can this replace our staff WhatsApp group?",
    a: "For guest requests, yes. A WhatsApp message scrolls away. Two people assume the other one did it. Afterwards there is no record of how long anything took. Turning requests into tracked jobs is what lets you prove your team is fast, not just believe it.",
  },
];

const schema = jsonLd([
  organizationSchema(),
  breadcrumbSchema([{ name: "Service Control", path: "/service-control" }, { name: "Guest request management", path: PATH }]),
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
        eyebrow="Guest request management"
        h1="Hotel guest request management"
        h1Accent="so nothing gets forgotten."
        intro="A request taken on the phone lives in somebody\u2019s memory until they pass it on. That is where it gets lost, and a lost towel request becomes a bad review. Clefs saves every request the moment the guest makes it, sends it to the right team, and keeps a timer on it until someone marks it done."
        parent={{ href: "/service-control", label: "Service Control" }}
        sections={[
          {
            h2: "From asked to done",
            body: [
              "The steps are short and everyone can see them. A guest asks. The request is saved with the room number. It goes to the right team. One staff member owns it. They mark it done. Each step is on the screen, not in someone's memory.",
            ],
            list: [
              { t: "Saved at once", d: "The request is recorded as the guest makes it, in their words, with the room number attached." },
              { t: "Sent automatically", d: "Clefs reads the request and sends it to the team that can actually do it." },
              { t: "One name on it", d: "Every job shows who is responsible, so it is never spread across a whole shift." },
              { t: "Timed while open", d: "A timer runs on every open job. You can see a slow job before the guest complains." },
              { t: "Recorded when closed", d: "Finished jobs are saved. That is what makes your monthly numbers real." },
            ],
          },
          {
            h2: "The real problem is not the work",
            body: [
              "Most hotels do not have a problem doing the work. Carrying towels to room 204 is easy. The problem is passing the message along. Shifts change. Someone tells someone else. Two people each think the other one did it.",
              "Giving every request one owner and a timer is a small change. It is also the one that guests notice. And it ends the argument later about whether the message was ever passed on, because the record is there.",
            ],
          },
          {
            h2: "What you see as a manager",
            body: [
              "Your screen shows what is open right now, who has it, and how long it has been waiting. Over time you build the numbers you are asked for: how fast your team replies, how long jobs take, and which team is busiest.",
              "That last one often surprises owners. Until requests were tracked, there was no way to know where the real delay was.",
            ],
          },
        ]}
        faqs={FAQS}
        related={[
          { href: "/service-control", label: "Service Control", blurb: "One queue for every guest request, with an owner and a clock." },
          { href: "/ai-concierge-software", label: "AI concierge software", blurb: "The guest side: instant answers by voice or typing." },
          { href: "/hotel-front-desk-automation", label: "Front desk automation", blurb: "What comes off your desk, and what stays with your staff." },
          { href: "/multilingual-guest-messaging", label: "Multilingual guest messaging", blurb: "Guests ask in their language. Your team reads it in yours." },
        ]}
      />
    </>
  );
}
