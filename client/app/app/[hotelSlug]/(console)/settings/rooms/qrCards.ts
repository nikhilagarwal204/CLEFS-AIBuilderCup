/* Room QR cards (05 spec 1, deck slide 9: "QR cards in each room"): the
   pure parts of Settings > Rooms > QR cards, kept free of React and of the
   qrcode package so Node's test runner loads this file directly
   (__tests__/qrCards.test.mjs).

   Each card encodes the web origin plus the room's keyed stay path from
   GET /hotels/{slug}/rooms/qr (core/rooms.py stay_path:
   /stay/{slug}/{room}?k=<HMAC of slug:room:version>). The key ties the guest
   page to that room, so a scan files requests for the right room without
   "please confirm your room at the door". Rotating a room's key
   (POST rooms/{room}/rotate-qr) bumps its version and retires the printed
   card. */

export interface RoomQr {
  room_number: string;
  floor: string | null;
  qr_key_version: number;
  stay_path: string;
}

/** A6 is 105 x 148 mm: four to an A4 sheet, or one per A6 card. */
export type Paper = "a4" | "a6";
export const CARDS_PER_SHEET: Record<Paper, number> = { a4: 4, a6: 1 };

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

/** origin + stay path, each path segment URL-encoded (a room may be "PH 1").
    "" while the origin is unknown (the server render). */
export function stayUrl(origin: string, stayPath: string): string {
  const o = (origin ?? "").trim().replace(/\/+$/, "");
  const p = (stayPath ?? "").trim();
  if (!o || !p) return "";
  const q = p.indexOf("?");
  const pathPart = q >= 0 ? p.slice(0, q) : p;
  const query = q >= 0 ? p.slice(q + 1) : "";
  const path = pathPart.split("/").map(seg => (seg ? encodeURIComponent(safeDecode(seg)) : seg)).join("/");
  return `${o}${path.startsWith("/") ? "" : "/"}${path}${query ? `?${query}` : ""}`;
}

/** The line every card carries (05 spec: "in your language", never "any language"). */
export function cardMessage(assistantName: string | null | undefined): string {
  const name = (assistantName ?? "").trim() || "Clefs";
  return `Scan to talk or type to ${name}, in your language. No app needed.`;
}

/** The modules of a QR symbol (the qrcode package's BitMatrix has this shape). */
export interface QrMatrix {
  size: number;
  get(row: number, col: number): number | boolean;
}

/** One SVG path for the dark modules, a rectangle per horizontal run
    ("M3 0h2v1h-2z"), in module units with the origin at the symbol's corner. */
export function qrPath(m: QrMatrix): string {
  const out: string[] = [];
  for (let y = 0; y < m.size; y++) {
    let x = 0;
    while (x < m.size) {
      if (!m.get(y, x)) {
        x++;
        continue;
      }
      const start = x;
      while (x < m.size && m.get(y, x)) x++;
      const run = x - start;
      out.push(`M${start} ${y}h${run}v1h-${run}z`);
    }
  }
  return out.join("");
}

/** Floors present, in building order (numbers first, then names). */
export function floorsOf(rooms: readonly RoomQr[]): string[] {
  const set = new Set<string>();
  for (const r of rooms) if (r.floor) set.add(r.floor);
  return [...set].sort((a, b) => {
    const na = Number(a);
    const nb = Number(b);
    const ia = a.trim() !== "" && Number.isFinite(na);
    const ib = b.trim() !== "" && Number.isFinite(nb);
    if (ia && ib) return na - nb;
    if (ia) return -1;
    if (ib) return 1;
    return a.localeCompare(b, undefined, { numeric: true });
  });
}

/** The cards to print: one floor (or all), rooms matching the search, in room order. */
export function selectCards(rooms: readonly RoomQr[], opts: { floor?: string | null; query?: string }): RoomQr[] {
  const q = (opts.query ?? "").trim().toLowerCase();
  return rooms
    .filter(r => !opts.floor || r.floor === opts.floor)
    .filter(r => !q || r.room_number.toLowerCase().includes(q))
    .slice()
    .sort((a, b) => a.room_number.localeCompare(b.room_number, undefined, { numeric: true }));
}

/** Cards grouped into printed sheets of `per` cards. */
export function sheets<T>(cards: readonly T[], per: number): T[][] {
  const n = Math.max(1, Math.floor(per));
  const out: T[][] = [];
  for (let i = 0; i < cards.length; i += n) out.push(cards.slice(i, i + n));
  return out;
}
