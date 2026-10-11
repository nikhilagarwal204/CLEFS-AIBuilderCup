"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Guest in-room dining menu (03 spec 2.6), DB-driven from the upsell module.

   Props follow the spec: {items, outlets, currency, locale, onPlaceOrder}.
   When the caller passes no items, the menu loads itself from
   GET /public/menu/{slug} (slug and room from the /stay/[hotelSlug]/[roomId]
   route, the room QR key from ?k=), so the guest page is live even before
   the concierge hands data down. The look is unchanged from the static
   version: same dark card list, gold accents and sticky checkout.

   Money: prices arrive as integer minor units (fils) and the basket total
   is summed in integers, then printed with Intl through format.ts. For a
   "plus plus" hotel the total is an estimate and says so; the confirmed
   figure comes from the server quote.

   Order lines carry the spec shape {menu_item_id, qty} plus the legacy
   {name, quantity, price} fields, so the pre-rebuild concierge call site
   keeps compiling and working until it switches to POST .../orders.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
  LuPlus, LuMinus, LuShoppingBag, LuChevronLeft, LuCheckCircle, LuChefHat, LuUtensils, LuRefreshCw, LuSparkles,
} from "react-icons/lu";
import { DEFAULT_ASSISTANT_NAME } from "../lib/assistant";
import { apiFetch, apiUrl } from "../lib/api";
import { DEFAULT_CURRENCY, DEFAULT_LOCALE, formatMoney } from "../lib/format";
import { isGuestLanguage, type GuestLanguage } from "./i18n";
import { langDir, makeT } from "./concierge/i18n";
import { orderableHere } from "./menuFilter";

// ─── Types ───────────────────────────────────────────────────────────────

/** One orderable item, as GET /public/menu/{slug} returns it. */
export interface GuestMenuItem {
  item_id: number;
  outlet: string;
  section: string | null;
  name: string;
  description: string | null;
  price: string;
  price_minor: number;
  currency: string;
  tags?: string[];
  dietary?: string[];
  alcohol?: boolean;
  prep_min?: number | null;
  /** Signed API path ("/hotels/{slug}/files/{id}?t=..."), resolved through /api/clefs. */
  image_url?: string | null;
  available_now?: boolean;
}

export interface GuestOutlet {
  key: string;
  name: string;
  kind: string;
  delivers_to_room: boolean;
  open_now: boolean;
}

export interface GuestPair {
  suggest_item_id: number;
  kind: "pairing" | "upgrade" | string;
  reason: string | null;
}

/** What onPlaceOrder receives: spec fields first, legacy aliases after. */
export interface MenuOrderLine {
  menu_item_id: number;
  qty: number;
  notes?: string;
  /** Legacy aliases for the pre-rebuild call site. `price` is in major units. */
  name: string;
  quantity: number;
  price: number;
}

interface GuestMenuPayload {
  outlets: GuestOutlet[];
  items: GuestMenuItem[];
  pairs: Record<string, GuestPair[]>;
  currency: string;
  price_display: "inclusive" | "plus_plus";
  ordering_enabled: boolean;
}

interface FoodMenuProps {
  /** Guest-facing persona name (per-hotel, DB-configurable). Not the Clefs brand. */
  assistantName?: string;
  onBack: () => void;
  /** Resolve on success; throw (e.g. on !res.ok) to show the error with a retry. */
  onPlaceOrder: (lines: MenuOrderLine[]) => Promise<unknown>;
  hotelName: string;
  items?: GuestMenuItem[];
  outlets?: GuestOutlet[];
  pairs?: Record<string, GuestPair[]>;
  currency?: string;
  /** The hotel's locale: money formatting only. */
  locale?: string;
  /** The guest's chosen language: strings, direction and item names. */
  lang?: GuestLanguage;
  priceDisplay?: "inclusive" | "plus_plus";
  /** Override the route's slug / room (the menu self-loads and beacons with them). */
  hotelSlug?: string | null;
  room?: string | number | null;
}

// ─── Strings ───────────────────────────────────────────────────────────────
// From the guest dictionaries (app/stay/i18n, `menu.*`), so check-i18n
// enforces every guest language. WHY a `lang` prop separate from `locale`:
// the guest picks their language in the LanguagePicker, while `locale` is
// the hotel's and only formats money. Without `lang` the language follows
// the locale, as before.

interface Strings {
  eyebrow: string; title: string; intro: string; add: string; unavailable: string; placing: string; place: string;
  items: (n: number) => string; plusFees: string; received: string; receivedBody: string;
  back: (a: string) => string; help: (a: string) => string; loadFail: string; retry: string; orderFail: string;
  empty: string; closed: string; pairTitle: string; minutes: (m: number) => string; other: string;
}

function menuStrings(lang: GuestLanguage): Strings {
  const tr = makeT(lang);
  const k = (key: string) => tr(`menu.${key}`);
  return {
    eyebrow: k("eyebrow"), title: k("title"), intro: k("intro"), add: k("add"), unavailable: k("unavailable"),
    placing: k("placing"), place: k("place"),
    items: (n) => tr(n === 1 ? "menu.itemsOne" : "menu.itemsMany", { n }),
    plusFees: k("plusFees"), received: k("received"), receivedBody: k("receivedBody"),
    back: (a) => tr("menu.back", { name: a }), help: (a) => tr("menu.help", { name: a }),
    loadFail: k("loadFail"), retry: k("retry"), orderFail: k("orderFail"), empty: k("empty"), closed: k("closed"),
    pairTitle: k("pairTitle"), minutes: (m) => tr("menu.minutes", { m }), other: k("other"),
  };
}

const langFromLocale = (locale: string): GuestLanguage => {
  const base = locale.toLowerCase().split("-")[0];
  return isGuestLanguage(base) ? base : "en";
};

// ─── Route context ───────────────────────────────────────────────────────

/** Slug and room from /stay/[hotelSlug]/[roomId] ("room101" and "101" both work). */
function useRouteRoom(slugProp?: string | null, roomProp?: string | number | null) {
  const params = useParams<{ hotelSlug?: string; roomId?: string }>();
  const slug = slugProp || params?.hotelSlug || null;
  let room = roomProp != null && roomProp !== "" ? String(roomProp) : null;
  if (!room && params?.roomId) {
    const raw = decodeURIComponent(params.roomId);
    const norm = raw.toLowerCase().startsWith("room") ? raw.slice(4) : raw;
    room = /^\d+$/.test(norm) ? norm : null; // location QR pages have no room
  }
  return { slug, room };
}

/** The room QR key (?k=). Read at call time, never stored in state: the menu
 *  overlay only mounts after a tap, so window always exists here. */
function roomKey(): string | undefined {
  if (typeof window === "undefined") return undefined;
  return new URLSearchParams(window.location.search).get("k") || undefined;
}

// ─── Self-loading ────────────────────────────────────────────────────────

function useGuestMenu(slug: string | null, room: string | null, lang: GuestLanguage, skip: boolean) {
  const [data, setData] = useState<GuestMenuPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const active = !skip && Boolean(slug);

  useEffect(() => {
    if (!active || !slug) return;
    const ctrl = new AbortController();
    apiFetch<GuestMenuPayload>(`/public/menu/${encodeURIComponent(slug)}`, {
      signal: ctrl.signal, silent401: true,
      // The guest's language: the server returns item names in it (falling
      // back to the hotel's) so the list matches the chrome around it.
      query: { room: room ?? undefined, k: roomKey(), lang },
    })
      .then(d => { setData(d); setError(null); })
      .catch(err => {
        if ((err as Error)?.name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => ctrl.abort();
  }, [active, slug, room, lang, nonce]);

  const retry = useCallback(() => { setError(null); setData(null); setNonce(n => n + 1); }, []);
  const loading = active && !data && !error;
  return { data, error, loading, retry };
}

// ─── Beacon ──────────────────────────────────────────────────────────────

/** Log each pairing suggestion once per menu visit as upsell "shown". Fire and
 *  forget: a failed beacon must never disturb ordering. The server collapses
 *  repeats within a stay day, so a reopen costs nothing. */
function useShownBeacon(slug: string | null, room: string | null) {
  const sent = useRef(new Set<string>());
  return useCallback((itemId: number, sourceItemId: number, kind: string) => {
    if (!slug || !room) return;
    const key = `${sourceItemId}:${itemId}`;
    if (sent.current.has(key)) return;
    sent.current.add(key);
    apiFetch(`/public/menu/${encodeURIComponent(slug)}/events`, {
      method: "POST", silent401: true, keepalive: true,
      json: { room, item_id: itemId, source_item_id: sourceItemId, kind: kind === "upgrade" ? "upgrade" : "pairing" },
    }).catch(() => { /* beacon only */ });
  }, [slug, room]);
}

// ─── Component ───────────────────────────────────────────────────────────

const GOLD_BTN_BG = "rgba(225, 175, 104, 0.1)";

export default function FoodMenu({
  onBack, onPlaceOrder, hotelName, assistantName = DEFAULT_ASSISTANT_NAME,
  items: itemsProp, outlets: outletsProp, pairs: pairsProp, currency: currencyProp, locale = DEFAULT_LOCALE,
  lang: langProp, priceDisplay: priceDisplayProp, hotelSlug, room: roomProp,
}: FoodMenuProps) {
  const lang: GuestLanguage = langProp ?? langFromLocale(locale);
  const { slug, room } = useRouteRoom(hotelSlug, roomProp);
  const menu = useGuestMenu(slug, room, lang, itemsProp !== undefined);
  const beacon = useShownBeacon(slug, room);
  const t: Strings = useMemo(() => menuStrings(lang), [lang]);
  const dir = langDir(lang);

  const outlets = useMemo(() => outletsProp ?? menu.data?.outlets ?? [], [outletsProp, menu.data]);
  // In a room, only outlets that deliver there (QA GS-15, see menuFilter).
  const items = useMemo(
    () => orderableHere(itemsProp ?? menu.data?.items ?? [], outlets, room),
    [itemsProp, menu.data, outlets, room],
  );
  const pairs = useMemo(() => pairsProp ?? menu.data?.pairs ?? {}, [pairsProp, menu.data]);
  const currency = currencyProp || menu.data?.currency || items[0]?.currency || DEFAULT_CURRENCY;
  const priceDisplay = priceDisplayProp ?? menu.data?.price_display ?? "inclusive";
  const orderingOff = menu.data?.ordering_enabled === false;

  const [cart, setCart] = useState<Record<number, number>>({});
  const [isOrdering, setIsOrdering] = useState(false);
  const [orderSuccess, setOrderSuccess] = useState(false);
  const [orderError, setOrderError] = useState(false);

  const money = useCallback(
    (minor: number, code?: string) => formatMoney(minor, (code || currency).trim(), locale, { minor: true }),
    [currency, locale],
  );

  const updateQuantity = (id: number, delta: number) => {
    setOrderError(false);
    setCart(prev => {
      const next = Math.max(0, Math.min(20, (prev[id] || 0) + delta));
      if (next === 0) {
        const { [id]: _removed, ...rest } = prev;
        return rest;
      }
      return { ...prev, [id]: next };
    });
  };

  const byId = useMemo(() => new Map(items.map(i => [i.item_id, i])), [items]);
  const cartItems = useMemo(() => items.filter(i => (cart[i.item_id] || 0) > 0), [items, cart]);
  // Integer fils end to end: no float drift on a 3-item basket.
  const totalMinor = cartItems.reduce((sum, i) => sum + i.price_minor * cart[i.item_id], 0);

  // Group by outlet name when there are several outlets, else by section.
  const sections = useMemo(() => {
    const outletName = new Map(outlets.map(o => [o.key, o.name]));
    const multi = new Set(items.map(i => i.outlet)).size > 1;
    const groups = new Map<string, GuestMenuItem[]>();
    for (const it of items) {
      const head = multi ? outletName.get(it.outlet) || it.outlet : it.section || t.other;
      groups.set(head, [...(groups.get(head) ?? []), it]);
    }
    return Array.from(groups.entries());
  }, [items, outlets, t.other]);

  // Up to two suggestions for what is in the basket, never one already in it.
  const suggestions = useMemo(() => {
    const out: { item: GuestMenuItem; forId: number; pair: GuestPair }[] = [];
    for (const src of cartItems) {
      for (const p of pairs[String(src.item_id)] ?? []) {
        const s = byId.get(p.suggest_item_id);
        if (!s || cart[s.item_id] || s.available_now === false || out.some(o => o.item.item_id === s.item_id)) continue;
        out.push({ item: s, forId: src.item_id, pair: p });
        if (out.length >= 2) return out;
      }
    }
    return out;
  }, [cartItems, pairs, byId, cart]);

  useEffect(() => {
    for (const s of suggestions) beacon(s.item.item_id, s.forId, s.pair.kind);
  }, [suggestions, beacon]);

  const handleOrder = async () => {
    if (cartItems.length === 0 || isOrdering) return;
    setIsOrdering(true);
    setOrderError(false);
    try {
      const lines: MenuOrderLine[] = cartItems.map(i => ({
        menu_item_id: i.item_id,
        qty: cart[i.item_id],
        name: i.name,
        quantity: cart[i.item_id],
        price: Number(i.price), // major units, from the exact Decimal string
      }));
      await onPlaceOrder(lines);
      setOrderSuccess(true);
      setCart({});
    } catch (err) {
      console.error("Order failed", err);
      setOrderError(true); // the basket is kept so Retry resends the same order
    } finally {
      setIsOrdering(false);
    }
  };

  if (orderSuccess) {
    return (
      <div dir={dir} style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: 40,
        textAlign: "center",
        background: "linear-gradient(180deg, #0C1B15 0%, #081210 100%)",
        animation: "fadeIn 0.6s ease"
      }}>
        <div style={{
          width: 80,
          height: 80,
          borderRadius: "50%",
          background: "rgba(64, 145, 108, 0.1)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: 24,
          border: "1px solid #40916C"
        }}>
          <LuCheckCircle size={40} color="#40916C" />
        </div>
        <h2 style={{ fontFamily: "'Playfair Display', serif", fontSize: 32, color: "var(--hc-gold)", marginBottom: 16 }}>{t.received}</h2>
        <p style={{ color: "rgba(232, 228, 220, 0.7)", fontSize: 16, lineHeight: 1.6, maxWidth: 300, marginBottom: 40 }}>
          {t.receivedBody}
        </p>
        <button
          onClick={onBack}
          style={{
            background: "var(--hc-gold)",
            color: "#081210",
            padding: "16px 40px",
            borderRadius: 30,
            fontSize: 14,
            fontWeight: 700,
            letterSpacing: "2px",
            textTransform: "uppercase",
            border: "none",
            cursor: "pointer",
            boxShadow: "0 10px 20px rgba(225, 175, 104, 0.2)"
          }}
        >
          {t.back(assistantName)}
        </button>
      </div>
    );
  }

  return (
    <div style={{
      height: "100%",
      display: "flex",
      flexDirection: "column",
      background: "#081210",
      color: "#E8E4DC",
      animation: "fadeIn 0.5s ease",
      position: "relative",
    }}>
      {/* Header */}
      <div style={{
        padding: "24px 20px 16px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        borderBottom: "1px solid rgba(225, 175, 104, 0.1)",
        background: "rgba(12, 27, 21, 0.8)",
        backdropFilter: "blur(10px)",
        position: "sticky",
        top: 0,
        zIndex: 10
      }}>
        <button
          onClick={onBack}
          aria-label="Back"
          style={{
            background: "rgba(255,255,255,0.05)",
            border: "1px solid rgba(255,255,255,0.1)",
            borderRadius: "50%",
            width: 40,
            height: 40,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer"
          }}
        >
          <LuChevronLeft size={20} color="var(--hc-gold)" />
        </button>
        <div dir={dir} style={{ textAlign: "center" }}>
          <div style={{ fontSize: 10, letterSpacing: 2, color: "var(--hc-gold)", fontWeight: 600, textTransform: "uppercase", marginBottom: 2 }}>{t.eyebrow}</div>
          <div style={{ fontSize: 16, fontWeight: 700, fontFamily: "'Playfair Display', serif" }}>{hotelName}</div>
        </div>
        <div style={{ position: "relative" }}>
          <LuShoppingBag size={24} color={cartItems.length > 0 ? "var(--hc-gold)" : "rgba(255,255,255,0.3)"} />
          {cartItems.length > 0 && (
            <div style={{
              position: "absolute",
              top: -8,
              right: -8,
              background: "#E74C3C",
              color: "#fff",
              fontSize: 10,
              width: 18,
              height: 18,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 700,
              border: "2px solid #081210"
            }}>
              {cartItems.length}
            </div>
          )}
        </div>
      </div>

      {/* Menu List */}
      <div dir={dir} style={{ flex: 1, overflowY: "auto", padding: `24px 20px ${cartItems.length ? 200 + suggestions.length * 64 : 120}px` }}>
        <div style={{ marginBottom: 32 }}>
          <h1 style={{ fontSize: 24, fontFamily: "'Playfair Display', serif", marginBottom: 8 }}>{t.title}</h1>
          <p style={{ fontSize: 14, color: "rgba(232, 228, 220, 0.5)", lineHeight: 1.5 }}>
            {orderingOff ? t.closed : t.intro}
          </p>
        </div>

        {menu.loading ? (
          <StatusPanel loading />
        ) : menu.error && !itemsProp ? (
          <StatusPanel message={t.loadFail} retryLabel={t.retry} onRetry={menu.retry} />
        ) : items.length === 0 ? (
          <StatusPanel message={t.empty} />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
            {sections.map(([head, list]) => (
              <section key={head} style={{ display: "flex", flexDirection: "column", gap: 24 }}>
                {sections.length > 1 && (
                  <h2 style={{ fontSize: 11, letterSpacing: 2, textTransform: "uppercase", color: "var(--hc-gold)", fontWeight: 600, margin: 0 }}>{head}</h2>
                )}
                {list.map(item => (
                  <MenuCard key={item.item_id} item={item} qty={cart[item.item_id] || 0} t={t} money={money}
                            disabled={orderingOff} onChange={d => updateQuantity(item.item_id, d)} />
                ))}
              </section>
            ))}
          </div>
        )}
      </div>

      {/* Cart Summary / Checkout */}
      {cartItems.length > 0 && (
        <div dir={dir} style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          padding: "20px 20px 40px",
          background: "linear-gradient(0deg, #081210 60%, transparent 100%)",
          zIndex: 20,
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}>
          {suggestions.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, letterSpacing: 2, textTransform: "uppercase", color: "var(--hc-gold)", fontWeight: 600 }}>
                <LuSparkles size={12} /> {t.pairTitle}
              </div>
              {suggestions.map(s => (
                <button
                  key={s.item.item_id}
                  onClick={() => updateQuantity(s.item.item_id, 1)}
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, width: "100%",
                    background: "rgba(12, 27, 21, 0.92)", border: "1px solid rgba(225, 175, 104, 0.25)", borderRadius: 16,
                    padding: "10px 14px", color: "#E8E4DC", cursor: "pointer", textAlign: "start",
                  }}
                >
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: "block", fontSize: 14, fontWeight: 600 }}>{s.item.name}</span>
                    {s.pair.reason && <span style={{ display: "block", fontSize: 11, color: "rgba(232, 228, 220, 0.5)" }}>{s.pair.reason}</span>}
                  </span>
                  <span style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0, color: "var(--hc-gold)", fontSize: 13, fontWeight: 700 }}>
                    {money(s.item.price_minor, s.item.currency)} <LuPlus size={14} />
                  </span>
                </button>
              ))}
            </div>
          )}
          {orderError && (
            <div role="alert" style={{
              display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "10px 14px",
              borderRadius: 14, background: "rgba(231, 76, 60, 0.12)", border: "1px solid rgba(231, 76, 60, 0.4)", fontSize: 13,
            }}>
              <span>{t.orderFail}</span>
              <button onClick={handleOrder} disabled={isOrdering} style={{
                display: "inline-flex", alignItems: "center", gap: 6, background: "none", border: "1px solid rgba(232, 228, 220, 0.3)",
                color: "#E8E4DC", borderRadius: 20, padding: "6px 14px", fontSize: 12, fontWeight: 600, cursor: "pointer",
              }}>
                <LuRefreshCw size={12} /> {t.retry}
              </button>
            </div>
          )}
          <button
            disabled={isOrdering || orderingOff}
            onClick={handleOrder}
            style={{
              width: "100%",
              background: "var(--hc-gold)",
              color: "#081210",
              borderRadius: 20,
              padding: "20px",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              border: "none",
              cursor: isOrdering ? "wait" : "pointer",
              boxShadow: "0 10px 40px rgba(225, 175, 104, 0.3)",
              transition: "transform 0.2s",
              opacity: isOrdering || orderingOff ? 0.8 : 1
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ background: "rgba(0,0,0,0.1)", padding: "8px", borderRadius: 10 }}>
                <LuShoppingBag size={20} />
              </div>
              <div style={{ textAlign: "start" }}>
                <div style={{ fontSize: 14, fontWeight: 700 }}>{isOrdering ? t.placing : t.place}</div>
                <div style={{ fontSize: 11, opacity: 0.7 }}>{t.items(cartItems.reduce((n, i) => n + cart[i.item_id], 0))}</div>
              </div>
            </div>
            <div style={{ textAlign: "end" }}>
              <div style={{ fontSize: 18, fontWeight: 800 }}>{money(totalMinor)}</div>
              {priceDisplay === "plus_plus" && <div style={{ fontSize: 10, opacity: 0.7 }}>{t.plusFees}</div>}
            </div>
          </button>
        </div>
      )}

      {/* Empty State Cart Hint */}
      {cartItems.length === 0 && (
        <div dir={dir} style={{
          position: "absolute",
          bottom: 30,
          left: "50%",
          transform: "translateX(-50%)",
          display: "flex",
          alignItems: "center",
          gap: 12,
          color: "rgba(232, 228, 220, 0.4)",
          fontSize: 12,
          whiteSpace: "nowrap",
        }}>
          <LuChefHat size={16} />
          <span>{t.help(assistantName)}</span>
        </div>
      )}
    </div>
  );
}

// ─── Item card ───────────────────────────────────────────────────────────

function MenuCard({ item, qty, t, money, disabled, onChange }: {
  item: GuestMenuItem;
  qty: number;
  t: Strings;
  money: (minor: number, code?: string) => string;
  disabled: boolean;
  onChange: (delta: number) => void;
}) {
  const unavailable = item.available_now === false;
  const [imgFailed, setImgFailed] = useState(false);
  const img = item.image_url && !imgFailed ? apiUrl(item.image_url) : null;
  const meta = [
    ...(item.dietary ?? []).map(d => d.replace(/_/g, " ")),
    item.prep_min ? t.minutes(item.prep_min) : null,
  ].filter(Boolean).join(" · ");

  return (
    <div style={{
      display: "flex",
      gap: 16,
      background: "rgba(255,255,255,0.03)",
      WebkitBackdropFilter: "blur(10px)",
      backdropFilter: "blur(10px)",
      borderRadius: 20,
      padding: 12,
      border: "1px solid rgba(255,255,255,0.05)",
      transition: "transform 0.2s ease",
      opacity: unavailable ? 0.5 : 1,
    }}>
      <div style={{
        position: "relative", width: 100, height: 100, borderRadius: 16, overflow: "hidden", flexShrink: 0,
        background: "rgba(225, 175, 104, 0.06)", display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        {img ? (
          // Signed, short-lived API path: next/image's optimiser would cache it past its expiry.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={img} alt={item.name} loading="lazy" onError={() => setImgFailed(true)}
               style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        ) : (
          <LuUtensils size={28} color="rgba(225, 175, 104, 0.5)" aria-hidden="true" />
        )}
      </div>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8, marginBottom: 4 }}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{item.name}</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: "var(--hc-gold)", whiteSpace: "nowrap" }}>{money(item.price_minor, item.currency)}</div>
          </div>
          {item.description && (
            <p style={{ fontSize: 12, color: "rgba(232, 228, 220, 0.5)", lineHeight: 1.4, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
              {item.description}
            </p>
          )}
          {meta && <div style={{ fontSize: 11, color: "rgba(225, 175, 104, 0.6)", marginTop: 4, textTransform: "capitalize" }}>{meta}</div>}
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
          {unavailable ? (
            <span style={{ fontSize: 11, color: "rgba(232, 228, 220, 0.5)", padding: "6px 0" }}>{t.unavailable}</span>
          ) : qty ? (
            <div style={{ display: "flex", alignItems: "center", background: GOLD_BTN_BG, borderRadius: 30, border: "1px solid rgba(225, 175, 104, 0.3)", padding: "4px 12px", gap: 16 }}>
              <button aria-label={`Remove one: ${item.name}`} onClick={() => onChange(-1)} style={{ background: "none", border: "none", cursor: "pointer", display: "flex", alignItems: "center" }}>
                <LuMinus size={14} color="var(--hc-gold)" />
              </button>
              <span style={{ fontSize: 14, fontWeight: 700, color: "var(--hc-gold)", minWidth: 12, textAlign: "center" }}>{qty}</span>
              <button aria-label={`Add one: ${item.name}`} onClick={() => onChange(1)} style={{ background: "none", border: "none", cursor: "pointer", display: "flex", alignItems: "center" }}>
                <LuPlus size={14} color="var(--hc-gold)" />
              </button>
            </div>
          ) : (
            <button
              onClick={() => onChange(1)}
              disabled={disabled}
              // "ADD" alone does not say which dish (QA GS-15).
              aria-label={`${t.add} ${item.name}`}
              style={{
                background: GOLD_BTN_BG,
                border: "1px solid rgba(225, 175, 104, 0.3)",
                color: "var(--hc-gold)",
                fontSize: 12,
                fontWeight: 600,
                padding: "6px 20px",
                borderRadius: 30,
                cursor: disabled ? "not-allowed" : "pointer",
                transition: "all 0.2s"
              }}
              onMouseOver={(e) => { e.currentTarget.style.background = "rgba(225, 175, 104, 0.2)" }}
              onMouseOut={(e) => { e.currentTarget.style.background = GOLD_BTN_BG }}
            >
              {t.add}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Status panel (loading, load error, empty) ───────────────────────────

function StatusPanel({ loading, message, retryLabel, onRetry }: {
  loading?: boolean;
  message?: string;
  retryLabel?: string;
  onRetry?: () => void;
}) {
  if (loading) {
    return (
      <div aria-busy="true" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        {[0, 1, 2].map(i => (
          <div key={i} style={{
            height: 124, borderRadius: 20, background: "rgba(255,255,255,0.03)",
            border: "1px solid rgba(255,255,255,0.05)", animation: "pulse 1.6s ease-in-out infinite",
          }} />
        ))}
      </div>
    );
  }
  return (
    <div role="status" style={{
      display: "flex", flexDirection: "column", alignItems: "center", gap: 16, padding: "48px 12px", textAlign: "center",
      color: "rgba(232, 228, 220, 0.6)", fontSize: 14, lineHeight: 1.5,
    }}>
      <LuUtensils size={28} color="var(--hc-gold)" />
      <div>{message}</div>
      {onRetry && (
        <button onClick={onRetry} style={{
          display: "inline-flex", alignItems: "center", gap: 8, background: GOLD_BTN_BG,
          border: "1px solid rgba(225, 175, 104, 0.3)", color: "var(--hc-gold)", fontSize: 13, fontWeight: 600,
          padding: "10px 22px", borderRadius: 30, cursor: "pointer",
        }}>
          <LuRefreshCw size={14} /> {retryLabel}
        </button>
      )}
    </div>
  );
}
