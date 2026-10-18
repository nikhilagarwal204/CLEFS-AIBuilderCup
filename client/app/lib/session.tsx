"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Session: who is looking at this hotel, and what may they do.

   GET /auth/me?hotel=<slug> returns the user, the role at this hotel and the
   RESOLVED permission list. The client only asks can(perm); it never carries
   its own role -> permission matrix, so the server stays the one place that
   decides (and a UI check is only ever a convenience, never the guard).

   Legacy mode: hotels with legacy_open_access (the Indian pilots) answer
   /auth/me with user=null and legacy_mode=true. Their dashboards must keep
   working with no login, so a 401 from some member-only endpoint does NOT
   bounce them to /login. Everyone else is redirected to /login?next=...
   ═══════════════════════════════════════════════════════════════════════ */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { api, UNAUTHORIZED_EVENT, useApi, withQuery, type ApiError } from "./api";
import type { Me, Role, User } from "./types";

export interface SessionValue {
  me: Me | null;
  user: User | null;
  role: Role | null;
  permissions: string[];
  isAdmin: boolean;
  /** True when ANY of the given permissions is held (clefs admins hold all). */
  can(perm: string | readonly string[]): boolean;
  legacyMode: boolean;
  loading: boolean;
  error: ApiError | null;
  reload(): void;
  logout(): Promise<void>;
}

const SessionCtx = createContext<SessionValue | null>(null);

/** Only same-origin relative paths may be used as a post-login destination.
 *  Checked by PARSING, not by prefix: the URL parser strips tab and newline,
 *  so "/\t/evil.example" passes a prefix test yet resolves to evil.example,
 *  and the router would then leave the site. The same rule runs on the server
 *  (clefs.auth.magic.safe_next_path). */
export function safeNextPath(next: string | null | undefined, fallback = "/"): string {
  if (!next || !next.startsWith("/") || next.startsWith("//")) return fallback;
  // Backslashes and C0 controls / DEL never belong in one of our paths.
  // eslint-disable-next-line no-control-regex
  if (/[\\\u0000-\u001f\u007f]/.test(next)) return fallback;
  const origin = typeof window !== "undefined" ? window.location.origin : "https://clefs.invalid";
  let url: URL;
  try {
    url = new URL(next, origin);
  } catch {
    return fallback;
  }
  if (url.origin !== origin) return fallback;
  return url.pathname + url.search + url.hash;
}

export function loginHref(next?: string): string {
  return next ? `/login?next=${encodeURIComponent(next)}` : "/login";
}

function matches(held: ReadonlySet<string>, perm: string): boolean {
  if (held.has(perm) || held.has("*")) return true;
  // Accept module wildcards such as "profit_guard.*" if the server sends them.
  const parts = perm.split(".");
  for (let i = parts.length - 1; i > 0; i--) {
    if (held.has(`${parts.slice(0, i).join(".")}.*`)) return true;
  }
  return false;
}

export function SessionProvider({ hotelSlug, children }: { hotelSlug?: string | null; children: ReactNode }) {
  const router = useRouter();
  const path = withQuery("/auth/me", { hotel: hotelSlug ?? undefined });
  const { data, error, loading, reload } = useApi<Me>(path, [hotelSlug]);

  const legacyRef = useRef(false);
  const redirecting = useRef(false);
  useEffect(() => {
    legacyRef.current = Boolean(data?.legacy_mode);
  }, [data]);

  const redirectToLogin = useCallback(() => {
    if (redirecting.current || typeof window === "undefined") return;
    if (window.location.pathname.startsWith("/login")) return;
    redirecting.current = true;
    router.replace(loginHref(window.location.pathname + window.location.search));
  }, [router]);

  useEffect(() => {
    const onUnauthorized = () => {
      if (!legacyRef.current) redirectToLogin();
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [redirectToLogin]);

  const logout = useCallback(async () => {
    try {
      await api.post<void>("/auth/logout", undefined, { silent401: true });
    } catch {
      // Logging out of an already-dead session is still a logout.
    }
    redirecting.current = true;
    router.replace("/login");
  }, [router]);

  const value = useMemo<SessionValue>(() => {
    const me = data ?? null;
    const permissions = me?.permissions ?? [];
    const held = new Set(permissions);
    const isAdmin = Boolean(me?.user?.is_clefs_admin);
    return {
      me,
      user: me?.user ?? null,
      role: me?.role ?? null,
      permissions,
      isAdmin,
      can: perm => {
        if (isAdmin) return true;
        const list = typeof perm === "string" ? [perm] : perm;
        return list.some(p => matches(held, p));
      },
      legacyMode: Boolean(me?.legacy_mode),
      loading,
      error: error ?? null,
      reload,
      logout,
    };
  }, [data, error, loading, reload, logout]);

  return <SessionCtx.Provider value={value}>{children}</SessionCtx.Provider>;
}

export function useSession(): SessionValue {
  const v = useContext(SessionCtx);
  if (!v) throw new Error("useSession() must be used inside <SessionProvider>");
  return v;
}

export function useSessionOptional(): SessionValue | null {
  return useContext(SessionCtx);
}
