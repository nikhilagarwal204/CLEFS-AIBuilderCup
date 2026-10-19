import type { Metadata } from "next";
import TryDemo from "./try/TryDemo";

// The site root is the live prototype: the deck's Final Product Link opens
// straight into the three screens (guest, staff app, manager console) of the
// demo hotel, the deck's process step by step, and a link to /story (the
// deck's story: problem, features, pricing, trust). /try redirects here.

const TITLE = "Clefs: the AI guest service desk, live";
const DESCRIPTION =
  "Ask once, in your language, and it gets done. Be the guest in Room 204, open Maria's staff app and sign in as the duty manager: the Clefs AI guest service desk, built on Google Cloud with Gemini, live on a demo hotel.";

export const metadata: Metadata = {
  // `absolute` so the root template's "| Clefs" suffix is not appended twice.
  title: { absolute: TITLE },
  description: DESCRIPTION,
  alternates: { canonical: "/" },
  openGraph: { title: TITLE, description: DESCRIPTION, url: "/", siteName: "Clefs", locale: "en_US", type: "website" },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION },
};

export default function Page() {
  return <TryDemo />;
}
