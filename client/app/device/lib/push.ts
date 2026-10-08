/* Service worker and Web Push for the floor device (03 spec 1.6).

   /device-sw.js is registered with scope /device only, so it never touches
   the marketing site, the console or the guest concierge. Web Push needs a
   VAPID public key (NEXT_PUBLIC_VAPID_PUBLIC_KEY); the server keeps
   subscriptions at POST|DELETE /device/service/push-subscriptions (Service
   push.py; sending is log-only until the server has VAPID_PRIVATE_KEY and
   VAPID_SUBJECT). Without a key the device still alerts in the foreground;
   iOS only delivers web push to a home-screen install, hence "Android for
   floor units". */

import { apiUrl } from "../../lib/api";

const SW_URL = "/device-sw.js";
// "/device", not "/device/": Next serves the index (the manifest start_url)
// without a trailing slash, and it must be inside the scope to work offline.
const SW_SCOPE = "/device";
const VAPID = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY || "";

export async function registerDeviceWorker(): Promise<ServiceWorkerRegistration | null> {
  try {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
    return await navigator.serviceWorker.register(SW_URL, { scope: SW_SCOPE });
  } catch {
    return null;
  }
}

function urlBase64ToUint8Array(base64: string): ArrayBuffer {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const buf = new ArrayBuffer(raw.length);
  const out = new Uint8Array(buf);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return buf;
}

/** Call from a user gesture (the PIN submit): the permission prompt needs one. */
export async function enablePush(staffId: number | null): Promise<"subscribed" | "denied" | "unsupported" | "no_key"> {
  if (!VAPID) return "no_key";
  if (typeof window === "undefined" || !("Notification" in window) || !("PushManager" in window)) return "unsupported";
  try {
    const perm = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
    if (perm !== "granted") return "denied";
    const reg = (await navigator.serviceWorker.getRegistration(SW_SCOPE)) ?? (await registerDeviceWorker());
    if (!reg) return "unsupported";
    const sub = (await reg.pushManager.getSubscription())
      ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(VAPID) }));
    // A missing route is fine: the subscription is retried at the next login.
    await fetch(apiUrl("/device/service/push-subscriptions"), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(staffId ? { "X-Clefs-Staff": String(staffId) } : {}) },
      body: JSON.stringify({ subscription: sub.toJSON() }),
    }).catch(() => undefined);
    return "subscribed";
  } catch {
    return "unsupported";
  }
}

/** A system notification while the page is hidden (the SW shows it, so it
    works in a backgrounded tab on Android). */
export async function notifyHidden(title: string, body: string, tag: string, urgent: boolean): Promise<void> {
  try {
    if (typeof document === "undefined" || document.visibilityState === "visible") return;
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const reg = await navigator.serviceWorker.getRegistration(SW_SCOPE);
    await reg?.showNotification(title, {
      body, tag, requireInteraction: urgent, icon: "/icon.png", badge: "/brand/notification-icon.png",
      data: { url: window.location.pathname },
    } as NotificationOptions);
  } catch { /* best effort */ }
}
