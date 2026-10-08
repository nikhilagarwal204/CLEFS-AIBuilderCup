/* ═══════════════════════════════════════════════════════════════════════
   Clefs floor-device service worker (03 spec 1.6). Registered by
   app/device/lib/push.ts with scope /device (Next serves the index without
   a trailing slash), so it controls only the device PWA.

   - push: shows the job (title/body/tag from the server's payload), with
     requireInteraction and a long vibration for urgent ones. Payloads carry
     no guest words (the server's escalation digests never do).
   - notificationclick: focuses an open device tab, or opens one.
   - fetch: network-first for device pages (a cached shell when a floor
     corridor has no signal), cache-first for Next's immutable build assets.
     API calls (/api/) are never cached: the outbox and the feed own
     offline behaviour for data.
   Bump CACHE when the caching rules change; old caches are dropped.
   ═══════════════════════════════════════════════════════════════════════ */

const CACHE = "clefs-device-v1";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith("clefs-device-") && k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

// ─── Push ───

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data ? event.data.text() : "" }; }
  const urgent = Boolean(data.urgent);
  const title = data.title || (urgent ? "URGENT request" : "New request");
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || "Open the device to see it.",
    tag: data.tag || (data.request_id ? `svc-${data.request_id}` : "svc"),
    renotify: true,
    requireInteraction: urgent,
    vibrate: urgent ? [400, 150, 400, 150, 400] : [200, 100, 200],
    icon: "/icon.png",
    badge: "/brand/notification-icon.png",
    data: { url: data.url || "/device" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/device";
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of all) {
      if (new URL(c.url).pathname.startsWith("/device") && "focus" in c) return c.focus();
    }
    return self.clients.openWindow(target);
  })());
});

// ─── Fetch ───

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      const hit = await cache.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) cache.put(req, res.clone());
      return res;
    })());
    return;
  }

  if (req.mode === "navigate" && (url.pathname === "/device" || url.pathname.startsWith("/device/"))) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      try {
        const res = await fetch(req);
        // Cache the page without its query (the pairing ?code= is single use).
        if (res.ok) cache.put(url.origin + url.pathname, res.clone());
        return res;
      } catch (err) {
        const hit = await cache.match(url.origin + url.pathname);
        if (hit) return hit;
        throw err;
      }
    })());
  }
});
