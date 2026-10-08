/* ═══════════════════════════════════════════════════════════════════════
   /api/live-session: a same-origin proxy to POST /public/concierge/sessions.

   This route used to mint voice keys itself, for anyone, with
   whatever prompt and tools the browser sent, and it logged every key
   (finding spec-voice-token-mint-open). Now the FastAPI server builds
   the prompt and tools, checks consent and rate limits, and mints. This
   route only forwards the body, the caller's IP (for the server's per-IP
   limits: X-Clefs-Client-IP behind CLEFS_PROXY_SECRET, see below) and the
   answer.

   - No GET: the anonymous minting endpoint is gone (Next answers 405).
   - A body that still carries session/instructions/tools/tool_choice is an
     old cached page: 409 client_outdated here, without a server round trip
     (the server enforces the same rule).
   - Nothing is logged: not the body, not the response, never a key.
   ═══════════════════════════════════════════════════════════════════════ */

import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

// Server-only origin, the same one next.config.mjs rewrites /api/clefs to.
const CLEFS_API_ORIGIN = (process.env.CLEFS_API_ORIGIN || "http://localhost:8000").replace(/\/+$/, "");
const OUTDATED_FIELDS = ["session", "instructions", "tools", "tool_choice"] as const;
const FORWARD_HEADERS = ["x-forwarded-for", "x-real-ip", "user-agent", "accept-language"] as const;
const MAX_BODY_BYTES = 16_384;
// Shared with the API project. WHY: on Vercel the API overwrites
// X-Forwarded-For / X-Real-IP with the connecting address, which for this
// hop is the web function's egress IP, so every guest on the platform would
// share one per-IP mint budget (30/h) and the 31st session of the hour gets
// 429. With the secret set, the real caller rides in X-Clefs-Client-IP and
// the API trusts it only when X-Clefs-Proxy-Key matches. Server-only env:
// never NEXT_PUBLIC_, never logged.
const PROXY_SECRET = process.env.CLEFS_PROXY_SECRET || "";
const IP_RE = /^[0-9a-fA-F:.[\]]{2,64}$/;

/** The browser's address as this web function saw it. On Vercel the edge
    sets X-Real-IP / X-Forwarded-For itself (a client cannot spoof them on
    the web project), so the first hop is the guest. Anything that does not
    look like an address is dropped rather than forwarded. */
function callerIp(h: Headers): string | null {
  for (const raw of [h.get("x-real-ip"), (h.get("x-forwarded-for") || "").split(",")[0]]) {
    const v = (raw || "").trim();
    if (v && IP_RE.test(v)) return v;
  }
  return null;
}

function error(status: number, code: string, detail: string) {
  return NextResponse.json({ detail, code }, { status, headers: { "Cache-Control": "no-store" } });
}

export async function POST(req: NextRequest) {
  const raw = await req.text().catch(() => "");
  if (raw.length > MAX_BODY_BYTES) return error(413, "body_too_large", "Request body too large");
  let body: Record<string, unknown>;
  try {
    body = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
  } catch {
    return error(400, "invalid_json", "Body must be JSON");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return error(400, "invalid_json", "Body must be a JSON object");
  if (OUTDATED_FIELDS.some((f) => f in body)) {
    return error(409, "client_outdated", "This page is out of date. Please refresh it.");
  }

  const headers = new Headers({ "Content-Type": "application/json", Accept: "application/json" });
  for (const h of FORWARD_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const ip = callerIp(req.headers);
  if (PROXY_SECRET && ip) {
    headers.set("x-clefs-client-ip", ip);
    headers.set("x-clefs-proxy-key", PROXY_SECRET);
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${CLEFS_API_ORIGIN}/public/concierge/sessions`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return error(502, "upstream_unreachable", "The assistant is not reachable right now");
  }

  // Pass the server's answer through untouched, including its {detail, code}
  // errors (409 consent_required, 429 session_rate_limited, ...).
  const text = await upstream.text();
  return new NextResponse(text || "{}", {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
      "Cache-Control": "no-store",
    },
  });
}
