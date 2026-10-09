import type { Metadata } from "next";
import TryDemo from "./TryDemo";

// /try: the live prototype for AI Builder Cup reviewers (05 spec section 5),
// linked from the homepage hero. The page itself is static; everything live
// (the keyed room link, the gate, the one-tap sign-ins) comes from
// /public/demo/{slug}, which answers for demo hotels only.

const TITLE = "Try the live prototype";
const DESCRIPTION =
  "Be the guest in Room 204, open Maria's staff app and sign in as the duty manager: the Clefs AI guest service desk, live on a demo hotel.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/try" },
  // Reviewers get the link; search engines do not need a page that pairs devices.
  robots: { index: false, follow: true },
  openGraph: { title: `${TITLE} | Clefs`, description: DESCRIPTION, url: "/try", siteName: "Clefs", type: "website" },
};

export default function Page() {
  return <TryDemo />;
}
