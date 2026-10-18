import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/* ─── Console auth hint (Next 16 "proxy", formerly middleware) ───────────────

   OPTIMISTIC ONLY. This never redirects and never blocks. Hotels with
   legacy_open_access (the existing Indian pilots) must keep loading their
   dashboard with no login at all, and only the API knows which hotels those
   are. So the proxy just tells the render layer "no credential cookie seen"
   via a request header, and ConsoleShell / SessionProvider make the real
   decision from GET /auth/me.

   The cookies are HttpOnly and opaque; presence is all we can (or should)
   check here. Validation happens server-side on every API call. */

const SESSION_COOKIE = "clefs_session";
const DEVICE_COOKIE = "clefs_device";
const ANON_HEADER = "x-clefs-anon";

export function proxy(request: NextRequest) {
  const hasCredential =
    request.cookies.has(SESSION_COOKIE) || request.cookies.has(DEVICE_COOKIE);

  // Strip any client-supplied copy first: the header must only ever mean
  // "the proxy saw no cookie", never something a caller can forge.
  const headers = new Headers(request.headers);
  headers.delete(ANON_HEADER);
  if (!hasCredential) headers.set(ANON_HEADER, "1");

  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: ["/app/:path*"],
};
