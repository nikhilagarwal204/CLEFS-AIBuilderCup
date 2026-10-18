import type { Metadata } from "next";
import LoginForm from "./LoginForm";

// Sign-in is a utility page: never indexed, never cached by a CDN.
export const metadata: Metadata = {
  title: "Sign in",
  robots: { index: false, follow: false, nocache: true },
};

/* `next` is read here on the server and handed down as a prop, so the form
   does not need useSearchParams (which would force a Suspense boundary and
   a client-side bailout for the whole page). */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  const sp = await searchParams;
  const next = typeof sp.next === "string" ? sp.next : undefined;
  return <LoginForm next={next} />;
}
