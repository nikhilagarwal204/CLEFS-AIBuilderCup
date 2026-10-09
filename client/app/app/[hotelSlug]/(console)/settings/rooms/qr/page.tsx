"use client";

/* Settings > Rooms > QR cards (05 spec 1; deck slide 9: "What a hotel needs:
   QR cards in each room"). One printable A6 card per room: the hotel, the
   room number, a QR code of this web origin plus the room's keyed stay path
   (GET /hotels/{slug}/rooms/qr), and "Scan to talk or type to Clefs, in
   your language. No app needed." Print four to an A4 sheet (cut along the
   dashed lines) or one per A6 card.

   The QR is drawn as an inline SVG from the qrcode package's modules
   (qrCards.qrPath): black on white, crisp at any print size, no image
   request. Rotating a room's key (POST rooms/{room}/rotate-qr) retires its
   printed card, so it asks first, and a demo hotel's keys stay fixed so the
   cards printed for the demo keep working. Owners and GMs only
   (core.settings.manage, as the routes require). */

import { memo, useMemo, useState, useSyncExternalStore } from "react";
import { create } from "qrcode";
import { LuArrowLeft, LuCopy, LuExternalLink, LuPrinter, LuRotateCw } from "react-icons/lu";
import { api, hotelPath, useApi } from "../../../../../../lib/api";
import { useHotel } from "../../../../../../lib/hotel";
import { useSession } from "../../../../../../lib/session";
import { Button, Card, EmptyState, Input, Modal, Segmented, Select, Skeleton, useToast } from "../../../../../../ui";
import { errorText } from "../../../../../../ingest/helpers";
import {
  CARDS_PER_SHEET, cardMessage, floorsOf, qrPath, selectCards, sheets, stayUrl, type Paper, type RoomQr,
} from "../qrCards";

const PAPER_OPTIONS: { value: Paper; label: string }[] = [
  { value: "a4", label: "A4, 4 cards a sheet" },
  { value: "a6", label: "A6 cards" },
];

const screenCss = `
.qrc-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.qrc-toolbar .qrc-grow { flex: 1 1 150px; max-width: 220px; }
.qrc-note { font-size: 0.8rem; color: var(--clefs-ink-muted); line-height: 1.5; margin: 12px 0 0; }
.qrc-sheet { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(230px, 100%), 1fr)); gap: 20px; }
.qrc-page { display: contents; }
.qrc-cell { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.qrc-card {
  aspect-ratio: 105 / 148; background: #fff; color: #15171b; border-radius: 12px; padding: 22px 18px 12px;
  display: flex; flex-direction: column; align-items: center; text-align: center; gap: 10px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); font-family: var(--clefs-sans); overflow: hidden;
}
.qrc-card__hotel { font-family: var(--clefs-serif); font-size: 0.92rem; letter-spacing: 0.04em; color: #7a5c22; line-height: 1.25; }
.qrc-card__room { font-weight: 700; font-size: 1.7rem; line-height: 1.05; color: #15171b; overflow-wrap: anywhere; }
.qrc-card__room span { display: block; font-size: 0.6rem; font-weight: 600; letter-spacing: 0.2em; text-transform: uppercase; color: #6a6e77; margin-bottom: 2px; }
.qrc-card__qr { width: 72%; height: auto; aspect-ratio: 1 / 1; display: block; flex-shrink: 0; }
.qrc-card__qr--wait { background: #eef0f3; border-radius: 6px; }
.qrc-card__msg { margin: 0; font-size: 0.8rem; line-height: 1.4; color: #23262c; font-weight: 500; }
.qrc-card__foot { margin-top: auto; font-size: 0.56rem; letter-spacing: 0.06em; color: #8b9099; }
.qrc-actions { display: flex; flex-wrap: wrap; gap: 6px; }
`;

/** Print: only the sheets, at true size. @page follows the paper choice. */
function printCss(paper: Paper): string {
  const a4 = paper === "a4";
  const cardH = a4 ? "148.5mm" : "148mm";
  return `
@media print {
  @page { size: ${a4 ? "210mm 297mm" : "105mm 148mm"}; margin: 0; }
  html, body, .cx-shell, .cx-root, .cx-main { background: #fff !important; color: #000 !important; }
  .cx-shell { min-height: 0 !important; }
  .cx-topbar, .cx-bottombar, .cx-page-head, .cx-tabs, .cx-toasts, .qrc-noprint { display: none !important; }
  .cx-main { padding: 0 !important; margin: 0 !important; max-width: none !important; }
  .qrc-root { margin: 0 !important; gap: 0 !important; }
  .qrc-sheet { display: block; }
  .qrc-page {
    display: grid; grid-template-columns: repeat(${a4 ? 2 : 1}, 105mm); grid-template-rows: repeat(${a4 ? 2 : 1}, ${cardH});
    width: ${a4 ? "210mm" : "105mm"}; height: ${a4 ? "297mm" : "148mm"}; break-after: page; page-break-after: always; overflow: hidden;
  }
  .qrc-page:last-child { break-after: auto; page-break-after: auto; }
  .qrc-cell { display: block; }
  .qrc-card {
    width: 105mm; height: ${cardH}; aspect-ratio: auto; border-radius: 0; box-shadow: none; box-sizing: border-box;
    border: 0.25mm dashed #c4c4c4; padding: 11mm 9mm 7mm; gap: 4mm; break-inside: avoid;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }
  .qrc-card__hotel { font-size: 11pt; }
  .qrc-card__room { font-size: 27pt; }
  .qrc-card__room span { font-size: 8pt; }
  .qrc-card__qr { width: 62mm; }
  .qrc-card__msg { font-size: 11.5pt; }
  .qrc-card__foot { font-size: 7pt; }
}`;
}

const noSubscribe = () => () => {};

/** This web app's origin; "" during the server render (no QR until hydrated). */
function useOrigin(): string {
  return useSyncExternalStore(noSubscribe, () => window.location.origin, () => "");
}

/** The QR symbol, computed once per URL (rooms in the hundreds stay cheap to re-render). */
const QrSvg = memo(function QrSvg({ url, label }: { url: string; label: string }) {
  const sym = useMemo(() => {
    try {
      const q = create(url, { errorCorrectionLevel: "M" });
      return { size: q.modules.size, d: qrPath(q.modules) };
    } catch {
      return null;
    }
  }, [url]);
  if (!sym) return <div className="qrc-card__qr qrc-card__qr--wait" role="img" aria-label="QR code unavailable" />;
  const m = 2; // quiet zone in modules; the white card adds more
  const box = sym.size + m * 2;
  return (
    <svg className="qrc-card__qr" viewBox={`${-m} ${-m} ${box} ${box}`} role="img" aria-label={label}
         shapeRendering="crispEdges" xmlns="http://www.w3.org/2000/svg">
      <rect x={-m} y={-m} width={box} height={box} fill="#ffffff" />
      <path d={sym.d} fill="#000000" />
    </svg>
  );
});

function QrCard({ room, url, hotelName, message }: { room: RoomQr; url: string; hotelName: string; message: string }) {
  return (
    <article className="qrc-card" aria-label={`QR card for room ${room.room_number}`}>
      {hotelName && <div className="qrc-card__hotel">{hotelName}</div>}
      <div className="qrc-card__room"><span>Room</span>{room.room_number}</div>
      {url
        ? <QrSvg url={url} label={`QR code that opens the guest page for room ${room.room_number}`} />
        : <div className="qrc-card__qr qrc-card__qr--wait" aria-hidden="true" />}
      <p className="qrc-card__msg">{message}</p>
      <div className="qrc-card__foot">Powered by Clefs · card v{room.qr_key_version}</div>
    </article>
  );
}

export default function RoomQrCardsPage() {
  const { slug, hotel } = useHotel();
  const { can, loading: sessionLoading } = useSession();
  const toast = useToast();
  const canManage = can("core.settings.manage");
  const res = useApi<RoomQr[]>(slug && canManage ? hotelPath(slug, "/rooms/qr") : null, [slug, canManage]);
  const origin = useOrigin();
  const [floor, setFloor] = useState("");
  const [query, setQuery] = useState("");
  const [paper, setPaper] = useState<Paper>("a4");
  const [rotating, setRotating] = useState<RoomQr | null>(null);
  const [busy, setBusy] = useState(false);

  const rooms = useMemo(() => res.data ?? [], [res.data]);
  const floors = useMemo(() => floorsOf(rooms), [rooms]);
  const cards = useMemo(() => selectCards(rooms, { floor: floor || null, query }), [rooms, floor, query]);
  const pages = useMemo(() => sheets(cards, CARDS_PER_SHEET[paper]), [cards, paper]);
  const isDemo = hotel?.status === "demo";
  const message = cardMessage(hotel?.assistant_name);
  const hotelName = hotel?.name ?? "";
  const roomsHref = `/app/${encodeURIComponent(slug)}/settings/rooms`;

  const rotate = async () => {
    if (!rotating) return;
    setBusy(true);
    try {
      const out = await api.post<{ room_number: string; qr_key_version: number; stay_path: string }>(
        hotelPath(slug, `/rooms/${encodeURIComponent(rotating.room_number)}/rotate-qr`));
      res.setData(prev => (prev ?? []).map(r => (r.room_number === out.room_number
        ? { ...r, qr_key_version: out.qr_key_version, stay_path: out.stay_path } : r)));
      toast.success(`Room ${out.room_number} has a new key`, "Print its new card and swap it in the room: the old card no longer opens the room.");
      setRotating(null);
    } catch (err) {
      toast.error("Couldn't rotate the key", errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const copy = async (url: string, room: string) => {
    try {
      await navigator.clipboard.writeText(url);
      toast.success(`Link for room ${room} copied`);
    } catch {
      toast.error("Couldn't copy the link", url);
    }
  };

  if (!sessionLoading && !canManage) {
    return (
      <div className="cx-stack" style={{ marginTop: 18 }}>
        <EmptyState title="Room cards are printed by the owner or GM"
                    body="Each card carries a key that ties the guest page to its room, so only they can print or rotate them." />
      </div>
    );
  }

  return (
    <div className="cx-stack qrc-root" style={{ marginTop: 18 }}>
      <style dangerouslySetInnerHTML={{ __html: screenCss + printCss(paper) }} />

      <Card className="qrc-noprint" title="Room QR cards"
            subtitle="Guests scan the card in their room to talk or type to the assistant on their own phone. No app, nothing to install."
            actions={<Button size="sm" variant="ghost" icon={<LuArrowLeft aria-hidden="true" />} href={roomsHref}>Rooms</Button>}>
        <div className="qrc-toolbar">
          <Select className="qrc-grow" aria-label="Floor" value={floor} onChange={e => setFloor(e.target.value)}
                  options={[{ value: "", label: "All floors" }, ...floors.map(f => ({ value: f, label: `Floor ${f}` }))]} />
          <Input className="qrc-grow" placeholder="Find a room" value={query} onChange={e => setQuery(e.target.value)}
                 aria-label="Find a room" />
          <Segmented size="sm" ariaLabel="Paper" value={paper} onChange={setPaper} options={PAPER_OPTIONS} />
          <Button variant="primary" icon={<LuPrinter aria-hidden="true" />} disabled={!cards.length || !origin}
                  onClick={() => window.print()}>
            Print {cards.length === 1 ? "1 card" : `${cards.length} cards`}
          </Button>
        </div>
        <p className="qrc-note">
          Each code opens this room&apos;s guest page with its own key, so requests arrive tagged with the right room.
          {paper === "a4" ? " Print at 100% scale (no fit to page) and cut along the dashed lines." : " Print at 100% scale on A6 card."}
          {isDemo
            ? " This is a demo hotel: its keys stay fixed so cards printed for the demo keep working."
            : " Rotate a key if a card goes missing: the old card stops opening the room at once."}
        </p>
      </Card>

      {res.error && (
        <Card className="qrc-noprint"><EmptyState title="Couldn't load the room cards" body={res.error.message}
                                               action={<Button onClick={res.reload}>Try again</Button>} /></Card>
      )}

      {(res.loading || sessionLoading) && !res.data ? (
        <div className="qrc-sheet qrc-noprint" aria-busy="true">
          {[0, 1, 2, 3].map(i => <Skeleton key={i} height={300} radius={12} />)}
        </div>
      ) : !res.error && rooms.length === 0 ? (
        <Card className="qrc-noprint">
          <EmptyState title="No rooms registered yet"
                      body="Cards are printed from the room register. Add your rooms first (a range, or the PMS room list as CSV)."
                      action={<Button variant="primary" href={roomsHref}>Add rooms</Button>} />
        </Card>
      ) : !res.error && cards.length === 0 ? (
        <Card className="qrc-noprint"><EmptyState title="No rooms match" body="Clear the search or pick another floor." /></Card>
      ) : (
        <div className="qrc-sheet">
          {pages.map((page, i) => (
            <div className="qrc-page" key={`${paper}-${i}`}>
              {page.map(room => {
                const url = stayUrl(origin, room.stay_path);
                return (
                  <div className="qrc-cell" key={room.room_number}>
                    <QrCard room={room} url={url} hotelName={hotelName} message={message} />
                    <div className="qrc-actions qrc-noprint">
                      <Button size="sm" variant="ghost" icon={<LuExternalLink aria-hidden="true" />} disabled={!url}
                              onClick={() => window.open(url, "_blank", "noopener")} title="Open the guest page as the card does">
                        Open
                      </Button>
                      <Button size="sm" variant="ghost" icon={<LuCopy aria-hidden="true" />} disabled={!url}
                              onClick={() => void copy(url, room.room_number)}>
                        Copy link
                      </Button>
                      {!isDemo && (
                        <Button size="sm" variant="ghost" icon={<LuRotateCw aria-hidden="true" />} onClick={() => setRotating(room)}>
                          Rotate key
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}

      <Modal open={rotating !== null} onClose={() => setRotating(null)} dismissible={!busy} size="sm"
             title={rotating ? `Rotate the key for room ${rotating.room_number}?` : "Rotate the key?"}
             footer={(
               <>
                 <Button variant="ghost" onClick={() => setRotating(null)} disabled={busy}>Keep the current key</Button>
                 <Button variant="danger" loading={busy} onClick={() => void rotate()}>Rotate key</Button>
               </>
             )}>
        <p style={{ margin: 0, lineHeight: 1.55 }}>
          The card in the room stops opening the guest page at once. Print the new card and swap it in.
          Use this when a card is lost, copied or photographed by someone who should not have it.
        </p>
      </Modal>
    </div>
  );
}
