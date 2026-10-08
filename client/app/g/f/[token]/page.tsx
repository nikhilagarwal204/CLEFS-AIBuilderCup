import GuestFeedbackPage from "./GuestFeedbackPage";

/* /g/f/[token]: the J7 stay-feedback page. Metadata (noindex, no-referrer)
   comes from g/layout. */
export default async function FeedbackTokenPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <GuestFeedbackPage token={token} />;
}
