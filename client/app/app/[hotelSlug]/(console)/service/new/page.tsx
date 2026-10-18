import type { Metadata } from "next";
import PhoneEntryPage from "./PhoneEntryPage";

/* /app/[hotelSlug]/service/new: the keyboard-first phone-entry console
   (03 spec 1.8). The client component owns everything; this server entry
   only names the tab. */
export const metadata: Metadata = { title: "Phone entry · Service" };

export default function Page() {
  return <PhoneEntryPage />;
}
