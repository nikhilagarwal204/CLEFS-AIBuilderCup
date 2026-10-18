/* The hotel this browser was last paired to, so the installed PWA's
   start_url (/device/) can forward to /device/{slug}. localStorage, not
   IndexedDB, because the index page needs it synchronously; it is not a
   secret (the device credential is the HttpOnly cookie). */

export const LAST_HOTEL_KEY = "clefs.device.hotel";

export function rememberHotel(slug: string): void {
  try { window.localStorage.setItem(LAST_HOTEL_KEY, slug); } catch { /* private mode */ }
}

export function forgetHotel(): void {
  try { window.localStorage.removeItem(LAST_HOTEL_KEY); } catch { /* private mode */ }
}
