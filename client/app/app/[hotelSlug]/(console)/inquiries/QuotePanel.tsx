"use client";

/* Quotes for one inquiry (4.6): the versions, newest first, and a builder
   for a new one. Rates come from Demand's guardrails on the server; when
   Demand has no view of a night the rate is left for a person to type
   (source "manual") and the quote can never auto-send. Building a new
   version supersedes the open ones, so there is only ever one live quote. */

import { useState } from "react";
import { api, hotelPath, useApi } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Badge, Button, EmptyState, Field, Input, Select, Skeleton, useToast } from "../../../../ui";
import QuoteEditor from "./QuoteEditor";
import { errorText, inqPath, OPEN_STAGES, QUOTE_STATUS_LABEL } from "./lib";
import type { InquiryDetail, Quote } from "./types";

interface RoomType { code: string; name?: string | null; is_active?: boolean }
interface RoomRow { room_type_code: string; rooms: string }

export default function QuotePanel({ row, onChanged }: { row: InquiryDetail; onChanged: () => void }) {
  const { slug } = useHotel();
  const [version, setVersion] = useState(0);
  const quotes = useApi<Quote[]>(inqPath(slug, `/${row.id}/quotes`), [version]);
  const [selected, setSelected] = useState<number | null>(null);
  const [building, setBuilding] = useState(false);

  const list = quotes.data ?? [];
  const current = list.find(q => q.version === selected) ?? list[0];

  const refresh = () => { setVersion(v => v + 1); onChanged(); };
  const isOpen = (OPEN_STAGES as string[]).includes(row.stage);

  if (quotes.loading && !quotes.data) return <Skeleton lines={5} height={16} />;
  if (quotes.error) {
    return <EmptyState title="Could not load quotes" body={quotes.error.message}
      action={<Button onClick={quotes.reload}>Try again</Button>} />;
  }

  return (
    <div>
      <div className="inq-row inq-row--between" style={{ marginBottom: 12 }}>
        <div className="inq-row">
          {list.map(q => (
            <button key={q.id} type="button" className="inq-chip" aria-pressed={q.id === current?.id}
              onClick={() => { setSelected(q.version); setBuilding(false); }}>
              v{q.version} {QUOTE_STATUS_LABEL[q.status]}
            </button>
          ))}
        </div>
        {isOpen && !building && (
          <Button size="sm" variant={list.length ? "secondary" : "primary"} onClick={() => setBuilding(true)}>
            {list.length ? "New version" : "Build a quote"}
          </Button>
        )}
      </div>
      {building || (!list.length && isOpen) ? (
        <QuoteBuilder row={row} onCancel={list.length ? () => setBuilding(false) : undefined}
          onBuilt={() => { setBuilding(false); setSelected(null); refresh(); }} />
      ) : current ? (
        <QuoteEditor key={`${current.id}:${current.status}:${current.total_minor}`} row={row} quote={current}
          onChanged={refresh} />
      ) : (
        <EmptyState title="No quote" body="This inquiry is closed, so no new quote can be built." />
      )}
    </div>
  );
}

// ─── Builder ─────────────────────────────────────────────────────────────

function QuoteBuilder({ row, onBuilt, onCancel }: {
  row: InquiryDetail;
  onBuilt: () => void;
  onCancel?: () => void;
}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  // The room-types list needs settings or service access; without it the
  // code is typed (the server validates it).
  const types = useApi<RoomType[]>(can(["core.settings.view", "service.view"]) ? hotelPath(slug, "/room-types") : null);
  const active = (types.data ?? []).filter(t => t.is_active !== false);
  const [rooms, setRooms] = useState<RoomRow[]>([{ room_type_code: row.room_type_code ?? "", rooms: String(row.rooms ?? 1) }]);
  const [language, setLanguage] = useState<"en" | "ar">(row.language === "ar" ? "ar" : "en");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const noDates = !row.check_in || !row.check_out;

  const setRoom = (i: number, patch: Partial<RoomRow>) =>
    setRooms(prev => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  async function build() {
    setBusy(true);
    setError(null);
    try {
      const body = {
        rooms: rooms.filter(r => Number(r.rooms) > 0)
          .map(r => ({ room_type_code: r.room_type_code.trim() || null, rooms: Number(r.rooms) })),
        language,
      };
      await api.post(inqPath(slug, `/${row.id}/quotes`), body);
      toast.success("Quote built", "Check every night's rate, then send it.");
      onBuilt();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {noDates && (
        <p className="inq-note inq-note--warn">Add check-in and check-out in Details first: a quote prices each night.</p>
      )}
      {rooms.map((r, i) => (
        <div key={i} className="inq-row" style={{ alignItems: "flex-end" }}>
          <Field label={i === 0 ? "Room type" : ""}>
            {active.length ? (
              <Select value={r.room_type_code} onChange={e => setRoom(i, { room_type_code: e.target.value })}>
                <option value="">Hotel default</option>
                {active.map(t => <option key={t.code} value={t.code}>{t.name ? `${t.name} (${t.code})` : t.code}</option>)}
              </Select>
            ) : (
              <Input value={r.room_type_code} maxLength={40} placeholder="e.g. DLX"
                onChange={e => setRoom(i, { room_type_code: e.target.value })} />
            )}
          </Field>
          <Field label={i === 0 ? "Rooms" : ""}>
            <Input type="number" min={1} max={500} value={r.rooms} style={{ width: 90 }}
              onChange={e => setRoom(i, { rooms: e.target.value })} />
          </Field>
          {rooms.length > 1 && (
            <Button size="sm" variant="ghost" onClick={() => setRooms(prev => prev.filter((_, j) => j !== i))}>Remove</Button>
          )}
        </div>
      ))}
      <div className="inq-row">
        {rooms.length < 10 && (
          <Button size="sm" variant="ghost" onClick={() => setRooms(prev => [...prev, { room_type_code: "", rooms: "1" }])}>
            Add a room type
          </Button>
        )}
        <Badge>Mixed room types are taxed in one calculation</Badge>
      </div>
      <Field label="Quote language">
        <Select value={language} onChange={e => setLanguage(e.target.value as "en" | "ar")}>
          <option value="en">English</option>
          <option value="ar">Arabic</option>
        </Select>
      </Field>
      {error && <p className="inq-note inq-note--bad" role="alert">{error}</p>}
      <div className="inq-row">
        <Button variant="primary" loading={busy} disabled={noDates} onClick={build}>Build quote</Button>
        {onCancel && <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>}
      </div>
    </div>
  );
}
