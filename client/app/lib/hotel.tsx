"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Hotel context: GET /hotels/{slug}/context, loaded once per console mount.

   Every console page reads the hotel's timezone, currency, locale, weekend
   days and enabled modules from here instead of hardcoding them. That is the
   fix for the old dashboard's "en-IN" and "Asia/Kolkata" assumptions, which
   are wrong for a Dubai property.
   ═══════════════════════════════════════════════════════════════════════ */

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { hotelPath, useApi, type ApiError } from "./api";
import type { Hotel, HotelContext } from "./types";

/** Modules every hotel has, regardless of hotel_module_settings. */
const ALWAYS_ENABLED = new Set(["core", "settings"]);

export interface HotelValue {
  slug: string;
  context: HotelContext | null;
  hotel: Hotel | null;
  loading: boolean;
  error: ApiError | null;
  reload(): void;
  /** Patch the cached context after a successful settings save. */
  setHotel(patch: Partial<Hotel>): void;
  moduleEnabled(module: string): boolean;
}

const HotelCtx = createContext<HotelValue | null>(null);

export function HotelProvider({ slug, children }: { slug: string; children: ReactNode }) {
  const { data, error, loading, reload, setData } = useApi<HotelContext>(hotelPath(slug, "/context"), [slug]);

  const value = useMemo<HotelValue>(() => {
    const enabled = new Set(data?.modules_enabled ?? []);
    return {
      slug,
      context: data ?? null,
      hotel: data?.hotel ?? null,
      loading,
      error: error ?? null,
      reload,
      setHotel: patch =>
        setData(prev => (prev ? { ...prev, hotel: { ...prev.hotel, ...patch } } : (prev as unknown as HotelContext))),
      // An empty list means the server has no per-module rows yet (every
      // module defaults to enabled), not "everything is off".
      moduleEnabled: (m: string) => ALWAYS_ENABLED.has(m) || enabled.size === 0 || enabled.has(m),
    };
  }, [slug, data, error, loading, reload, setData]);

  return <HotelCtx.Provider value={value}>{children}</HotelCtx.Provider>;
}

/** Hotel context for console pages. Throws outside <HotelProvider> so a missing
    provider fails loudly in development instead of rendering AED defaults. */
export function useHotel(): HotelValue {
  const v = useContext(HotelCtx);
  if (!v) throw new Error("useHotel() must be used inside <HotelProvider>");
  return v;
}

/** Same, but null outside a provider (for shared components like MoneyText). */
export function useHotelOptional(): HotelValue | null {
  return useContext(HotelCtx);
}
