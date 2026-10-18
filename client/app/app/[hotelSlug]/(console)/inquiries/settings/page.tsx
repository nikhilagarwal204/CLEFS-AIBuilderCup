import InquirySettings from "./InquirySettings";

/* /inquiries/settings (04 spec 4.11): channels, the website widget,
   payment providers, reply and quote rules, and approval of the WhatsApp
   quote templates. `?tab=` picks the tab (links from the empty-state
   checklist and the lead-fetch health banner land on the right one). */

type SP = Record<string, string | string[] | undefined>;

export default async function InquirySettingsPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  return <InquirySettings initialTab={typeof sp.tab === "string" ? sp.tab : undefined} />;
}
