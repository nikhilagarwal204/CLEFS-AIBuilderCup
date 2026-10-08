/* ═══════════════════════════════════════════════════════════════════════
   Clefs API client.

   Two paths to the FastAPI server, on purpose:

   1. Ordinary calls go to API_PREFIX (/api/clefs/*) on the WEB origin and
      Next rewrites them to CLEFS_API_ORIGIN (next.config.mjs). The session
      lives in an HttpOnly cookie that FastAPI sets through that proxy, so it
      is first-party on www, previews and localhost, invisible to XSS, and no
      request needs a custom auth header. That is also why the legacy
      StaffDashboard's raw fetch() calls keep working unchanged once its
      API_BASE points here.

   2. SSE streams and large uploads go DIRECT to NEXT_PUBLIC_API_URL with a
      60-second HMAC ticket minted through path 1. EventSource cannot send
      headers, cookies do not cross origins (CORS runs with credentials off),
      and a 30-minute stream or a 20 MB statement PDF should not hang off a
      serverless proxy hop.

   Errors always surface as ApiError. A 401 also dispatches the window event
   "clefs:unauthorized", which SessionProvider turns into a /login redirect
   (unless the hotel is legacy open access).
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useState } from "react";
import type { ApiErrorBody, FileRef, StreamStatus, Ticket } from "./types";

// ─── Constants ───────────────────────────────────────────────────────────

export const API_PREFIX = "/api/clefs";

/** Direct API origin, for ticketed SSE and uploads only. */
export const API_ORIGIN = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");

export const UNAUTHORIZED_EVENT = "clefs:unauthorized";

/** Polling cadence when SSE is unavailable (Vercel EVENTS_BACKEND=local, proxies that buffer). */
const POLL_MS = 20_000;
/** After this many consecutive stream failures, poll while we keep retrying in the background. */
const POLL_AFTER_FAILURES = 3;
/**
 * Whether a connected stream should still be backed by polling when the
 * ticket does not say. WHY: the API defaults EVENTS_BACKEND to "local" on
 * Vercel, where publish() reaches only subscribers in the same function
 * instance; the stream opens, keepalives flow, and nothing else ever arrives
 * (review deploy-sse-local-stale). A server that knows sends `poll` on
 * POST /auth/stream-ticket and that wins; an older one leaves it out, and a
 * web build on Vercel (NEXT_PUBLIC_VERCEL_ENV is exposed automatically there)
 * assumes the Vercel API default.
 */
const STREAM_LOCAL_BY_DEFAULT = Boolean(process.env.NEXT_PUBLIC_VERCEL_ENV);
const BACKOFF_BASE_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;

// ─── Errors ──────────────────────────────────────────────────────────────

export class ApiError extends Error {
  status: number;
  code?: string;
  detail?: unknown;
  /** The whole parsed error body, so extra fields a route adds (Service's
      409 `conflict` block, for example) survive next to detail and code. */
  body?: unknown;

  constructor(status: number, message: string, code?: string, detail?: unknown, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.body = body;
  }

  /** Field-level messages from a FastAPI 422, keyed by the last loc segment. */
  get fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    if (Array.isArray(this.detail)) {
      for (const item of this.detail as { loc?: (string | number)[]; msg?: string }[]) {
        const key = item?.loc?.length ? String(item.loc[item.loc.length - 1]) : "_";
        if (item?.msg && !out[key]) out[key] = item.msg;
      }
    }
    return out;
  }
}

/** Turn any error body into one readable sentence. FastAPI 422 detail is an array. */
function messageFrom(body: unknown, status: number, fallback: string): string {
  const detail = (body as ApiErrorBody | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((d: { loc?: (string | number)[]; msg?: string }) => {
        const field = d?.loc?.filter(p => p !== "body").join(".");
        return field ? `${field}: ${d?.msg ?? "invalid"}` : d?.msg ?? "invalid";
      })
      .join("; ");
  }
  if (typeof body === "string" && body.trim() && body.length < 300) return body.trim();
  if (status === 0) return "Can't reach the Clefs server. Check your connection and try again.";
  return fallback;
}

function emitUnauthorized(path: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT, { detail: { path } }));
}

// ─── apiFetch ────────────────────────────────────────────────────────────

export type Query = Record<string, string | number | boolean | undefined | null>;

export interface ApiInit extends RequestInit {
  /** Serialised as JSON with the right Content-Type. Mutually exclusive with body. */
  json?: unknown;
  /** Appended as a query string; undefined/null values are dropped. */
  query?: Query;
  /** Do not fire clefs:unauthorized on 401 (used where 401 is an expected answer). */
  silent401?: boolean;
}

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    qs.set(k, String(v));
  }
  const s = qs.toString();
  if (!s) return path;
  return path + (path.includes("?") ? "&" : "?") + s;
}

/** Resolve a contract path ("/hotels/x") to the proxied URL ("/api/clefs/hotels/x"). */
export function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  if (path.startsWith(API_PREFIX + "/")) return path;
  return API_PREFIX + (path.startsWith("/") ? path : `/${path}`);
}

async function readBody(res: Response): Promise<unknown> {
  if (res.status === 204 || res.status === 205) return undefined;
  const text = await res.text();
  if (!text) return undefined;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("json")) {
    try { return JSON.parse(text); } catch { return text; }
  }
  return text;
}

export async function apiFetch<T>(path: string, init: ApiInit = {}): Promise<T> {
  const { json, query, silent401, headers: initHeaders, ...rest } = init;
  const headers = new Headers(initHeaders);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  let body = rest.body;
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }

  const url = withQuery(apiUrl(path), query);
  let res: Response;
  try {
    res = await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      ...rest,
      headers,
      body,
    });
  } catch (err) {
    if ((err as Error)?.name === "AbortError") throw err;
    throw new ApiError(0, messageFrom(null, 0, ""), "network_error");
  }

  const data = await readBody(res);
  if (!res.ok) {
    if (res.status === 401 && !silent401) emitUnauthorized(path);
    const code = (data as ApiErrorBody | null)?.code;
    const detail = (data as ApiErrorBody | null)?.detail ?? data;
    throw new ApiError(res.status, messageFrom(data, res.status, `Request failed (${res.status})`), code, detail, data);
  }
  return data as T;
}

export const api = {
  get<T>(p: string, q?: Query, init?: ApiInit): Promise<T> {
    return apiFetch<T>(p, { ...init, method: "GET", query: q });
  },
  post<T>(p: string, body?: unknown, init?: ApiInit): Promise<T> {
    return apiFetch<T>(p, { ...init, method: "POST", json: body });
  },
  patch<T>(p: string, body?: unknown, init?: ApiInit): Promise<T> {
    return apiFetch<T>(p, { ...init, method: "PATCH", json: body });
  },
  put<T>(p: string, body?: unknown, init?: ApiInit): Promise<T> {
    return apiFetch<T>(p, { ...init, method: "PUT", json: body });
  },
  del<T>(p: string, init?: ApiInit): Promise<T> {
    return apiFetch<T>(p, { ...init, method: "DELETE" });
  },
};

export const hotelPath = (slug: string, p = "") => `/hotels/${encodeURIComponent(slug)}${p}`;

// ─── Realtime: openHotelStream ───────────────────────────────────────────

export interface HotelStreamOptions {
  onStatus?: (s: StreamStatus) => void;
  /** Called every POLL_MS while the stream is down or local-only, and once
      after a reconnect (events may have been missed while we were away). */
  refetch?: () => void;
  pollMs?: number;
}

/** Receives the event's JSON-parsed data (or the raw string if it is not JSON). */
type StreamHandler = (data: any) => void;

function parseEventData(raw: unknown): unknown {
  if (typeof raw !== "string") return raw;
  try { return JSON.parse(raw); } catch { return raw; }
}

/**
 * Open the hotel's SSE stream and keep it open.
 *
 * - Mints a 60 s ticket via POST /auth/stream-ticket and connects DIRECT to
 *   the API origin. Tickets are checked only at connect time, so each
 *   reconnect re-mints (the server also closes streams after 30 min).
 * - A 401/403 on the ticket means "no session": legacy_open_access hotels
 *   serve /hotels/{slug}/stream without one, so we fall back to that URL.
 *   The ticket call is silent401 so an anonymous legacy dashboard is not
 *   bounced to /login.
 * - Exponential backoff with jitter. After POLL_AFTER_FAILURES misses the
 *   caller's refetch runs every 20 s (status "polling") while reconnects
 *   continue in the background.
 * - A local-only stream (the ticket's `poll: true`, see
 *   STREAM_LOCAL_BY_DEFAULT) keeps that refetch running while it is open:
 *   in-process events never reach it from other instances.
 *
 * Returns a cleanup function; call it on unmount.
 */
export function openHotelStream(
  slug: string,
  handlers: Record<string, StreamHandler>,
  opts: HotelStreamOptions = {},
): () => void {
  const pollMs = opts.pollMs ?? POLL_MS;
  let closed = false;
  let es: EventSource | null = null;
  let failures = 0;
  let everOpened = false;
  let status: StreamStatus | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  const setStatus = (s: StreamStatus) => {
    if (s === status) return;
    status = s;
    opts.onStatus?.(s);
  };

  // True when the server said (or, for an older server, we infer) that its
  // events are in-process only: see STREAM_LOCAL_BY_DEFAULT.
  let localOnly = STREAM_LOCAL_BY_DEFAULT;

  const ensurePollTimer = () => {
    if (pollTimer || !opts.refetch) return;
    pollTimer = setInterval(() => { if (!closed) opts.refetch?.(); }, pollMs);
  };

  const startPolling = () => {
    setStatus("polling");
    ensurePollTimer();
  };

  const stopPolling = () => {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  };

  const scheduleRetry = () => {
    if (closed) return;
    failures += 1;
    if (failures >= POLL_AFTER_FAILURES) startPolling();
    else setStatus("reconnecting");
    const exp = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** (failures - 1));
    const delay = exp / 2 + Math.random() * (exp / 2);
    retryTimer = setTimeout(connect, delay);
  };

  async function connect() {
    retryTimer = null;
    if (closed) return;
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      startPolling();
      return;
    }

    let path: string;
    try {
      const t = await apiFetch<Ticket & { poll?: boolean }>("/auth/stream-ticket", {
        method: "POST",
        json: { hotel_slug: slug },
        silent401: true,
      });
      path = t.url;
      if (typeof t.poll === "boolean") localOnly = t.poll;
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        path = hotelPath(slug, "/stream");
      } else {
        scheduleRetry();
        return;
      }
    }
    if (closed) return;

    const source = new EventSource(/^https?:\/\//.test(path) ? path : API_ORIGIN + path);
    es = source;
    for (const [name, fn] of Object.entries(handlers)) {
      source.addEventListener(name, (e: Event) => {
        try {
          fn(parseEventData((e as MessageEvent).data));
        } catch (handlerErr) {
          console.error(`[clefs stream] handler for "${name}" failed`, handlerErr);
        }
      });
    }
    source.onopen = () => {
      const wasDown = everOpened || failures > 0;
      everOpened = true;
      failures = 0;
      // A local-only stream is connected but deaf to other instances (guest
      // requests, webhooks and cron sweeps usually run elsewhere), so keep
      // refetching on the poll cadence while it is open; the status still
      // reads "live" because this instance's own events do arrive.
      if (localOnly) ensurePollTimer();
      else stopPolling();
      setStatus("live");
      if (wasDown) opts.refetch?.();
    };
    source.onerror = () => {
      // EventSource would auto-reconnect to the same URL, but that URL holds
      // an expired ticket. Close and re-mint instead.
      source.close();
      if (es === source) es = null;
      scheduleRetry();
    };
  }

  void connect();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    stopPolling();
    es?.close();
    es = null;
  };
}

// ─── Uploads ─────────────────────────────────────────────────────────────

function xhrUpload(
  url: string,
  form: FormData,
  withCredentials: boolean,
  onProgress?: (pct: number) => void,
): Promise<FileRef> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.withCredentials = withCredentials;
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = e => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onerror = () => reject(new ApiError(0, messageFrom(null, 0, ""), "network_error"));
    xhr.onload = () => {
      let data: unknown = xhr.responseText;
      try { data = JSON.parse(xhr.responseText); } catch { /* keep text */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(100);
        resolve(data as FileRef);
        return;
      }
      if (xhr.status === 401) emitUnauthorized(url);
      const body = data as ApiErrorBody | null;
      reject(new ApiError(xhr.status, messageFrom(data, xhr.status, `Upload failed (${xhr.status})`), body?.code, body?.detail ?? data));
    };
    xhr.send(form);
  });
}

/**
 * Upload one file for a hotel. Mints an upload ticket and posts the bytes
 * DIRECT to the API origin (large OTA statement PDFs should not go through
 * the proxy). If the ticket endpoint is missing (404/405, an older API), it
 * falls back to the cookie-authenticated proxied route.
 */
export async function uploadFile(
  slug: string,
  file: File,
  purpose: string,
  onProgress?: (pct: number) => void,
): Promise<FileRef> {
  const form = new FormData();
  form.append("file", file);
  form.append("purpose", purpose);

  let ticket: Ticket | null = null;
  try {
    ticket = await api.post<Ticket>("/files/upload-ticket", { hotel_slug: slug, purpose });
  } catch (err) {
    if (!(err instanceof ApiError) || (err.status !== 404 && err.status !== 405)) throw err;
  }

  if (ticket) {
    const url = /^https?:\/\//.test(ticket.url) ? ticket.url : API_ORIGIN + ticket.url;
    return xhrUpload(url, form, false, onProgress);
  }
  return xhrUpload(apiUrl(hotelPath(slug, "/files")), form, true, onProgress);
}

// ─── useApi ──────────────────────────────────────────────────────────────

export interface UseApiResult<T> {
  data?: T;
  error?: ApiError;
  loading: boolean;
  reload(): void;
  /** Optimistic local update without a refetch. */
  setData: (updater: T | ((prev: T | undefined) => T)) => void;
}

/**
 * GET a path and keep the result in state. `path === null` skips the call
 * (e.g. while a slug is unknown). Stale responses from a previous path or a
 * superseded reload are dropped, so fast tab switches never show old data.
 */
export function useApi<T>(path: string | null, deps: unknown[] = []): UseApiResult<T> {
  const [data, setDataState] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const [settledKey, setSettledKey] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  // One key per request. `loading` is derived from it rather than set inside
  // the effect, so a new path or reload() reads as loading on the very same
  // render (no flash of the previous result's "loaded" state).
  const depsKey = JSON.stringify(deps);
  const requestKey = path === null ? null : `${path}\u0000${depsKey}\u0000${nonce}`;

  useEffect(() => {
    if (path === null || requestKey === null) return;
    const ctrl = new AbortController();
    let live = true;
    apiFetch<T>(path, { signal: ctrl.signal })
      .then(d => {
        if (!live) return;
        setDataState(d);
        setError(undefined);
        setSettledKey(requestKey);
      })
      .catch(err => {
        if (!live || (err as Error)?.name === "AbortError") return;
        setError(err instanceof ApiError ? err : new ApiError(0, String((err as Error)?.message || err)));
        setSettledKey(requestKey);
      });
    return () => {
      live = false;
      ctrl.abort();
    };
  }, [path, requestKey]);

  const loading = requestKey !== null && settledKey !== requestKey;
  const reload = useCallback(() => setNonce(n => n + 1), []);
  const setData = useCallback((updater: T | ((prev: T | undefined) => T)) => {
    setDataState(prev => (typeof updater === "function" ? (updater as (p: T | undefined) => T)(prev) : updater));
  }, []);

  return { data, error, loading, reload, setData };
}
