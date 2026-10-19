/* ═══════════════════════════════════════════════════════════════════════
   Guest concierge API client (03 spec 2.2 to 2.6).

   Every call goes through the same-origin /api/clefs proxy (apiFetch),
   except minting, which goes through /api/live-session so an old cached page
   that still posts a browser-built prompt gets a clean 409 there. Session
   routes carry the session bearer token; the room is bound server-side
   from that token, so nothing here can name a different room.

   Small per-stay memory (guest_ref, consent, stay token, tracker tokens)
   lives in localStorage behind try/catch: it is a convenience, the server
   stays the source of truth, and a private window simply starts fresh.
   ═══════════════════════════════════════════════════════════════════════ */

import { ApiError, apiFetch } from "../../lib/api";

// ─── Types ───────────────────────────────────────────────────────────────

export interface EmergencyNumber { number: string; kind: string }

export interface ConsentText {
  version: string;
  language: string;
  text: string;
  required: boolean;
  required_for_voice: boolean;
}

export interface StayContext {
  hotel: {
    slug: string; name: string; assistant_name: string; city: string | null;
    tz: string; currency: string; locale: string; languages: string[];
    /** Optional: a WhatsApp number for tracker opt-in links, when the server exposes one. */
    whatsapp_number?: string | null;
  };
  surface: "web_room" | "web_location";
  room: { number: string; floor: string | number | null } | null;
  location: { slug: string; name: string; type: string } | null;
  verified: boolean;
  key_status: "valid" | "stay_token" | "invalid" | "absent";
  guest: { first_name: string; language?: string | null } | null;
  language: string;
  greeting_text: string;
  chips: { key: string; label: string }[];
  /** assistant: concierge.live. False means every session mint answers 409
   *  concierge_not_live, so the page says "call the front desk" up front
   *  (optional: an older server omits it and the 409 path still applies). */
  features: { menu: boolean; tracker: boolean; whatsapp: boolean; voice: boolean; assistant?: boolean };
  emergency_numbers: EmergencyNumber[];
  consent_texts: Record<string, ConsentText>;
  consent_version: string;
  stay_session_key: string | null;
}

export type ConsentPurpose = "ai_processing" | "transfer_abroad" | "recording" | "ai_analytics";
export type ConsentGrants = Record<ConsentPurpose, boolean>;
export type SessionMode = "voice" | "text" | "chip" | "prewarm";
export type Transport = "live" | "http";

export interface SessionInfo {
  session_id: string;
  session_token: string;
  expires_at: string;
  client_secret: { value: string; expires_at?: number | string | null; model?: string } | null;
  greeting_text: string;
  prompt_version: string;
  surface: string;
  verified: boolean;
  key_status: string;
  language: string;
  model: string;
  /** "relay": voice goes through the server's Live relay socket (relay_url),
      not straight to Gemini with an ephemeral key (05 spec 3, voice relay). */
  transport: Transport | "relay";
  relay_url?: string | null;
  relay_token?: string | null;
  consents: Partial<ConsentGrants>;
  guest_ref: string;
  stay_token?: string;
}

/**
 * The "Job sent to staff" card a job tool result carries (05 spec 3): who
 * has the job and how fast it was picked up. First name only, never a
 * surname. Times are epoch ms (the server normalises them).
 */
export interface JobCard {
  item_label: string | null;
  quantity: number | null;
  room: string | null;
  staff_first_name: string | null;
  department_label: string | null;
  eta_minutes: number | null;
  created_at: number | string | null;
  accepted_at: number | string | null;
  state: string | null;
  /** The concierge request type ("towels"), for a label in the guest's language. */
  request_type?: string | null;
  /** Service's department key ("housekeeping"), for the same reason. */
  department_key?: string | null;
}

export interface ToolUi {
  kind: "request" | "handoff" | "tracker" | "order" | "emergency" | "recovery";
  token?: string | null;
  order_id?: string | number | null;
  numbers?: EmergencyNumber[];
  card?: JobCard | null;
}

export interface ToolResult {
  status: string;
  followup_instructions?: string;
  ui?: ToolUi | null;
  code?: string;
  deduped?: boolean;
  [k: string]: unknown;
}

export interface TurnResult {
  ok: boolean;
  stored: boolean;
  safety?: { level: string; kind: string | null };
  escalated?: boolean;
  ui?: ToolUi | null;
  followup_instructions?: string;
  request_ref?: string;
}

export interface ChatReply {
  text: string;
  handoff: boolean;
  tool_calls: { name: string; status: string; ui?: ToolUi | null }[];
  ui?: ToolUi | null;
  safety?: { level: string; kind: string | null };
  prompt_version?: string;
}

/** A staff reply, an approved gesture or a status note, already in the guest's language. */
export interface TrackerMessage {
  at?: number | string | null;
  kind: "staff_reply" | "gesture" | "status" | string;
  text: string;
  staff_first_name?: string | null;
}

export interface TrackerStatus {
  id: number | string;
  room: string | null;
  type: string | null;
  status: string;
  state: "received" | "assigned" | "on_the_way" | "done";
  request_type_label: string | null;
  request_type_label_ar: string | null;
  department_label?: string | null;
  department_label_ar?: string | null;
  created_at_ms?: number | null;
  updated_at_ms?: number | null;
  can_confirm: boolean;
  guest_confirmation: string | boolean | null;
  cancelled: boolean;
  // Added for the job card (05 spec 3); optional so an older server still reads.
  item_label?: string | null;
  quantity?: number | null;
  staff_first_name?: string | null;
  eta_minutes?: number | null;
  accepted_at?: number | string | null;
  proof_status?: string | null;
  needs_guest_confirmation?: boolean;
  closed_at?: number | string | null;
  messages?: TrackerMessage[];
}

// ─── Stay context ────────────────────────────────────────────────────────

export interface ContextQuery {
  k?: string | null;
  lang?: string | null;
  stayToken?: string | null;
  kind?: "room" | "location";
}

/** GET /public/stay/{slug}/{target}/context: the page's one bootstrap call. */
export function fetchStayContext(slug: string, target: string, q: ContextQuery = {}): Promise<StayContext> {
  const headers: Record<string, string> = {};
  if (q.stayToken) headers["X-Stay-Token"] = q.stayToken;
  return apiFetch<StayContext>(
    `/public/stay/${encodeURIComponent(slug)}/${encodeURIComponent(target)}/context`,
    { method: "GET", query: { k: q.k || undefined, lang: q.lang || undefined, kind: q.kind }, headers, silent401: true },
  );
}

// ─── Sessions ────────────────────────────────────────────────────────────

export interface StartSessionBody {
  hotel: string;
  target: { room: string } | { location: string };
  k?: string | null;
  stay_token?: string | null;
  lang?: string | null;
  mode: SessionMode;
  transport: Transport;
  guest_ref?: string | null;
  consents?: ConsentGrants;
}

/** Mint through the /api/live-session proxy. Throws ApiError with the server code. */
export async function startSession(body: StartSessionBody): Promise<SessionInfo> {
  let res: Response;
  try {
    res = await fetch("/api/live-session", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "network", "network_error");
  }
  const data = (await res.json().catch(() => null)) as (SessionInfo & { detail?: string; code?: string }) | null;
  if (!res.ok || !data) throw new ApiError(res.status, data?.detail || `HTTP ${res.status}`, data?.code);
  return data;
}

function bearer(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

export function endSession(sid: string, token: string, withdraw = false): Promise<{ ok: boolean; withdrawn: string[] }> {
  return apiFetch(`/public/concierge/sessions/${encodeURIComponent(sid)}/end`, {
    method: "POST", json: { withdraw_consent: withdraw, reason: withdraw ? "guest_withdrew" : "page_closed" },
    headers: bearer(token), silent401: true,
  });
}

/** POST /public/concierge/consent/withdraw: the Privacy button with no live
 *  session (a reload, an expired token). The device's guest_ref is the proof. */
export function withdrawByGuestRef(hotel: string, guestRef: string): Promise<{ ok: boolean; withdrawn: string[] }> {
  return apiFetch("/public/concierge/consent/withdraw", {
    method: "POST", json: { hotel, guest_ref: guestRef }, silent401: true,
  });
}

// ─── Tools, turns, chat ──────────────────────────────────────────────────

/** POST .../tools/{name}: the server runs every tool; the client only relays. */
export function runToolCall(sid: string, token: string, name: string, args: unknown, callId?: string | null): Promise<ToolResult> {
  return apiFetch<ToolResult>(
    `/public/concierge/sessions/${encodeURIComponent(sid)}/tools/${encodeURIComponent(name)}`,
    { method: "POST", json: { call_id: callId || undefined, arguments: args ?? {} }, headers: bearer(token), silent401: true },
  );
}

export interface TurnBody { role: "user" | "assistant"; text: string; item_id?: string | null; language?: string | null }

/** POST .../turns: the log with the server's safety double check. */
export function postTurnCall(sid: string, token: string, body: TurnBody): Promise<TurnResult> {
  return apiFetch<TurnResult>(`/public/concierge/sessions/${encodeURIComponent(sid)}/turns`, {
    method: "POST", json: { ...body, item_id: body.item_id || undefined, language: body.language || undefined },
    headers: bearer(token), silent401: true,
  });
}

/**
 * POST .../chat, the HTTP text fallback. Asks for SSE and reads it as it
 * arrives; a proxy that buffers (the /api/clefs hop gzips) just delivers
 * every piece at once, and a plain JSON answer is accepted as well.
 */
export async function chatCall(
  sid: string, token: string, message: string, language: string | null, onDelta?: (text: string) => void,
): Promise<ChatReply> {
  let res: Response;
  try {
    res = await fetch(`/api/clefs/public/concierge/sessions/${encodeURIComponent(sid)}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...bearer(token) },
      body: JSON.stringify({ message, language: language || undefined, stream: true }),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "network", "network_error");
  }
  if (!res.ok) {
    const err = (await res.json().catch(() => null)) as { detail?: string; code?: string } | null;
    throw new ApiError(res.status, err?.detail || `HTTP ${res.status}`, err?.code);
  }
  if (!(res.headers.get("content-type") || "").includes("event-stream") || !res.body) {
    const reply = (await res.json()) as ChatReply;
    onDelta?.(reply.text || "");
    return reply;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let done: ChatReply | null = null;
  for (;;) {
    const { value, done: eof } = await reader.read();
    if (value) buf += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue;
        try {
          const ev = JSON.parse(line.slice(5).trim()) as { type: string; text?: string } & Partial<ChatReply>;
          if (ev.type === "delta" && ev.text) onDelta?.(ev.text);
          else if (ev.type === "done") done = ev as unknown as ChatReply;
        } catch { /* a partial or keep-alive line */ }
      }
    }
    if (eof) break;
  }
  if (!done) throw new ApiError(502, "stream ended early", "chat_incomplete");
  return done;
}

// ─── Public tracker ──────────────────────────────────────────────────────

export function fetchTracker(token: string): Promise<TrackerStatus> {
  return apiFetch<TrackerStatus>(`/public/r/${encodeURIComponent(token)}`, { method: "GET", silent401: true });
}

export function confirmTracker(token: string, resolved: boolean): Promise<TrackerStatus> {
  return apiFetch<TrackerStatus>(`/public/r/${encodeURIComponent(token)}/confirm`, {
    method: "POST", json: { resolved }, silent401: true,
  });
}

/**
 * "Is everything OK?": POST /public/r/{token}/feedback. fixed=false reopens
 * the job and opens a case for the duty manager. A server without the route
 * yet (404/405) gets the older "Resolved?" answer, which does the same.
 */
export async function sendFeedback(token: string, fixed: boolean, comment?: string | null): Promise<Partial<TrackerStatus>> {
  try {
    return await apiFetch<Partial<TrackerStatus>>(`/public/r/${encodeURIComponent(token)}/feedback`, {
      method: "POST", json: { fixed, comment: comment || undefined }, silent401: true,
    });
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 405) && err.code !== "not_found") {
      return confirmTracker(token, fixed);
    }
    throw err;
  }
}

// ─── Menu orders ─────────────────────────────────────────────────────────

export interface OrderLine { menu_item_id: number; qty: number; notes?: string }
export interface PlacedOrder { tracking_token: string | null; order_id: string | number | null; followup_instructions?: string }

/**
 * With a live AI session: the place_order tool (quote, then confirm the same
 * basket), so the session knows the order and the server writes the spoken
 * follow-up. Throws unless the order is placed (FoodMenu shows a retry).
 */
export async function placeOrderViaSession(sid: string, token: string, lines: OrderLine[]): Promise<PlacedOrder> {
  const basket = lines.map((l) => ({ menu_item_id: l.menu_item_id, qty: l.qty }));
  const quote = await runToolCall(sid, token, "place_order", { lines: basket, confirm: false });
  if (quote.status !== "quoted") throw new ApiError(409, String(quote.code || quote.status), String(quote.code || quote.status));
  const placed = await runToolCall(sid, token, "place_order", { lines: basket, confirm: true });
  if (placed.status !== "placed") throw new ApiError(409, String(placed.code || placed.status), String(placed.code || placed.status));
  return { tracking_token: placed.ui?.token ?? null, order_id: placed.ui?.order_id ?? null, followup_instructions: placed.followup_instructions };
}

/** Without a session (AI off, no consent yet): the menu's own public quote and order. */
export async function placeOrderViaMenu(slug: string, room: string, k: string | null, lines: OrderLine[], lang: string): Promise<PlacedOrder> {
  const base = `/public/menu/${encodeURIComponent(slug)}`;
  const q = await apiFetch<{ quote_id: string }>(`${base}/quote`, {
    method: "POST", json: { room, k: k || undefined, lang, lines: lines.map((l) => ({ item_id: l.menu_item_id, qty: l.qty, notes: l.notes })) },
  });
  const o = await apiFetch<{ order_id: string | number; tracking_token: string | null }>(`${base}/orders`, {
    method: "POST", json: { room, k: k || undefined, quote_id: q.quote_id, lang },
  });
  return { tracking_token: o.tracking_token ?? null, order_id: o.order_id };
}
