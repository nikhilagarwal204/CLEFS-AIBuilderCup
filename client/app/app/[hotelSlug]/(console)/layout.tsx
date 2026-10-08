import type { ReactNode } from "react";
import { headers } from "next/headers";
import { HotelProvider } from "../../../lib/hotel";
import { SessionProvider } from "../../../lib/session";
import { ToastProvider } from "../../../ui";
import ConsoleShell from "./ConsoleShell";

/* The console for one hotel. Providers wrap in dependency order: the session
   (who) and the hotel context (what is enabled here) both load in parallel,
   the toast host sits inside them so any page can report a save, and the
   shell renders the chrome. The (console) group keeps this layout off the
   legacy /app/[hotelSlug]/staff/[staffId] phone page, which must stay a
   bare, login-free screen for housekeeping staff. */
export default async function ConsoleLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ hotelSlug: string }>;
}) {
  const { hotelSlug } = await params;
  // Set by proxy.ts when no credential cookie was seen. A hint only: the
  // shell still asks /auth/me, because legacy hotels are open without one.
  const anonHint = (await headers()).get("x-clefs-anon") === "1";

  return (
    <SessionProvider hotelSlug={hotelSlug}>
      <HotelProvider slug={hotelSlug}>
        <ToastProvider>
          <ConsoleShell hotelSlug={hotelSlug} anonHint={anonHint}>
            {children}
          </ConsoleShell>
        </ToastProvider>
      </HotelProvider>
    </SessionProvider>
  );
}
