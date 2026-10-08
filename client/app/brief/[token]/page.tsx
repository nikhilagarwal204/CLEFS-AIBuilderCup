import MobileBrief from "./MobileBrief";

/* /brief/[token]: the one-tap mobile brief. Rendered on the client so the
   API's per-IP rate limit sees the owner's phone, not the Next server, and
   so a link-preview bot (which runs no script) never reads the brief.
   Metadata (noindex, no-referrer) comes from brief/layout. */
export default async function BriefTokenPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <MobileBrief token={token} />;
}
