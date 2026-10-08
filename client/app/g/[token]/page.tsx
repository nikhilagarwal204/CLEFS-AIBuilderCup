import GuestPrefsPage from "./GuestPrefsPage";

/* /g/[token]: the J1 preferences page (arrival details, own contact and
   separate consent ticks) and, for a J5 "consent" token, the single
   data-transfer tick. Metadata (noindex, no-referrer) comes from g/layout. */
export default async function GuestTokenPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <GuestPrefsPage token={token} />;
}
