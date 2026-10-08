"use client";

/* ═══════════════════════════════════════════════════════════════════════
   PhoneEntryConsole (03 spec 1.8): the desk types up a phoned-in request
   without touching the mouse.

     room (autocomplete: floor, masked guest, VIP/returning, open jobs)
       -> Enter -> department chips (keys 1-8)
       -> request type / amenity grid (keys 1-9) with quantity (digits or +/-)
       -> note and caller language
       -> Enter files it (source phone_desk).
   Focus follows the choices (department -> types -> quantity or note), and
   each chip group is one Tab stop walked with the arrow keys, so "502,
   Enter, 1, 3, note, Enter" files towels without a single Tab (QA GS-14).
   A caller asking about a booking rather than a service goes to "Log an
   inquiry" (inquiries/new?source=phone_desk&room=, 04 spec 4.3).

   Duplicate guard: the same room and type already open offers "add a note
   instead" (the server would merge it anyway; this makes it visible). A
   "caller wants status" drawer reads the room's open jobs back.

   Mounted at /service/new and as the "N" overlay in the classic dashboard.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Drawer, Field, Select, Textarea, useToast } from "../../../../../ui";
import {
  LANGUAGE_LABELS, addNote, createRequestCompat, effectiveState, errorText, fetchOpenRequests, newEventId,
  type ServiceCard,
} from "./serviceApi";
import { useServiceCatalog, type RequestType } from "./catalog";
import { maskName, matchRooms, useRoomDirectory, type RoomInfo } from "./roomDirectory";
import { OriginalToggle, SvcStyles, V2Chips, useNow } from "./RequestMeta";

export interface PhoneEntryProps {
  /** Tighter layout for the drawer overlay. */
  compact?: boolean;
  onFiled?: (card: ServiceCard) => void;
  onDone?: () => void;
}

const CALLER_LANGUAGES = ["en", "ar", "hi", "ur", "ru", "zh", "de", "fr"] as const;

/** Minutes since an instant, for "assigned 4 min ago". */
function minsAgo(ts: number | null | undefined, now: number): number | null {
  if (!ts || !now) return null;
  return Math.max(0, Math.round((now - ts) / 60_000));
}

/** The Tab stop of a roving chip group (the chosen chip, else the first). */
function rovingStop(group: HTMLElement | null): HTMLButtonElement | null {
  return group?.querySelector<HTMLButtonElement>('button[tabindex="0"]') ?? group?.querySelector<HTMLButtonElement>("button") ?? null;
}

const ARROWS = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"];

/** DOM id of one room suggestion. The room input points aria-activedescendant
 *  at it so a screen reader announces the room the arrow keys land on (focus
 *  never leaves the input). Room labels may hold spaces ("PH 1"), which an id
 *  cannot, so anything outside [A-Za-z0-9_-] becomes "_". */
function roomOptionId(room: string): string {
  return `svc-pe-room-${room.replace(/[^A-Za-z0-9_-]/g, "_")}`;
}

export default function PhoneEntryConsole({ compact = false, onFiled, onDone }: PhoneEntryProps) {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const now = useNow(15_000);
  const catalog = useServiceCatalog(slug);
  const { rooms } = useRoomDirectory(slug);
  const revealNames = can("guests.view");

  // ─── Form state ───
  const [roomInput, setRoomInput] = useState("");
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [suggestIdx, setSuggestIdx] = useState(0);
  const [dept, setDept] = useState<string | null>(null);
  const [typeKey, setTypeKey] = useState<string | null>(null);
  const [amenityId, setAmenityId] = useState<number | null>(null);
  const [qty, setQty] = useState(1);
  const [note, setNote] = useState("");
  const [language, setLanguage] = useState<string>("en");
  const [urgent, setUrgent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [dupe, setDupe] = useState<ServiceCard | null>(null);
  const [statusOpen, setStatusOpen] = useState(false);
  const [openJobs, setOpenJobs] = useState<ServiceCard[]>([]);
  const idemKey = useRef(newEventId());

  const roomRef = useRef<HTMLInputElement>(null);
  const chipsRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const qtyRef = useRef<HTMLButtonElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);

  // ─── Focus flow ───
  /* A choice moves the focus to the next step once the render that shows it
     has run. A counter, not a flag: picking the same chip twice must still
     move the focus (identical state would skip the render). */
  const [focusReq, setFocusReq] = useState<{ to: "depts" | "types" | "detail"; n: number } | null>(null);
  const focusNext = (to: "depts" | "types" | "detail") => setFocusReq(r => ({ to, n: (r?.n ?? 0) + 1 }));
  useEffect(() => {
    if (!focusReq) return;
    if (focusReq.to === "depts") rovingStop(chipsRef.current)?.focus();
    else if (focusReq.to === "types") rovingStop(gridRef.current)?.focus();
    else (qtyRef.current ?? noteRef.current)?.focus();  // amenities: digits set the quantity there
  }, [focusReq]);

  // ─── Data ───
  const loadOpen = useCallback(() => {
    fetchOpenRequests(slug).then(setOpenJobs).catch(() => setOpenJobs([]));
  }, [slug]);
  useEffect(() => { loadOpen(); }, [loadOpen]);
  useEffect(() => { roomRef.current?.focus(); }, []);

  const room = roomInput.trim();
  const roomInfo: RoomInfo | null = useMemo(
    () => rooms.find(r => r.room.toLowerCase() === room.toLowerCase()) ?? null, [rooms, room]);
  const suggestions = useMemo(() => matchRooms(rooms, roomInput), [rooms, roomInput]);
  const roomJobs = useMemo(
    () => (room ? openJobs.filter(j => j.room.toLowerCase() === room.toLowerCase()) : []), [openJobs, room]);

  const departments = catalog.departments.slice(0, 8);
  const types: RequestType[] = useMemo(
    () => catalog.requestTypes.filter(t => !dept || t.department_key === dept), [catalog.requestTypes, dept]);
  const selectedType = catalog.requestTypes.find(t => t.key === typeKey) ?? null;
  const amenitiesForType = catalog.amenities.filter(a => !typeKey || !a.request_type || a.request_type === typeKey);
  const maxQty = catalog.amenities.find(a => a.id === amenityId)?.max_qty ?? 20;

  const pickRoom = (r: RoomInfo) => {
    setRoomInput(r.room);
    setSuggestOpen(false);
    rovingStop(chipsRef.current)?.focus();
  };

  const pickDept = (key: string) => {
    setDept(key);
    setTypeKey(null);
    setAmenityId(null);
    focusNext("types");
  };

  const pickType = (t: RequestType) => {
    setTypeKey(t.key);
    setAmenityId(null);
    setQty(1);
    focusNext("detail");
  };

  const reset = () => {
    setRoomInput(""); setDept(null); setTypeKey(null); setAmenityId(null); setQty(1); setNote("");
    setLanguage("en"); setUrgent(false); setDupe(null);
    idemKey.current = newEventId();
    roomRef.current?.focus();
  };

  // ─── Filing ───
  const describe = (): string => {
    const label = selectedType?.label_en ?? "Request";
    const item = catalog.amenities.find(a => a.id === amenityId)?.name;
    const head = `${item ?? label}${selectedType?.is_amenity || amenityId ? ` x${qty}` : ""}`;
    return note.trim() ? `${head}: ${note.trim()}` : head;
  };

  const canSubmit = !!room && !!typeKey && !submitting;

  const submit = async (force = false) => {
    if (!canSubmit) {
      if (!room) roomRef.current?.focus();
      else if (!dept) rovingStop(chipsRef.current)?.focus();
      else rovingStop(gridRef.current)?.focus();
      return;
    }
    // Duplicate guard: same room and type already open.
    const same = roomJobs.find(j => j.request_type_key
      ? j.request_type_key === typeKey
      : !!selectedType && [selectedType.legacy_type, selectedType.label_en].includes(j.type));
    if (same && !force) {
      setDupe(same);
      return;
    }
    setSubmitting(true);
    try {
      const deptRow = catalog.departments.find(d => d.key === selectedType?.department_key);
      const out = await createRequestCompat(slug, {
        room, description: describe(), request_type_key: typeKey ?? undefined,
        priority: urgent ? "high" : undefined, language: language === "en" ? undefined : language,
        amenity_item_id: amenityId ?? undefined,
        quantity: selectedType?.is_amenity || amenityId ? qty : undefined,
        idempotency_key: `phone_desk:${idemKey.current}`, source: "phone_desk",
      }, {
        type: selectedType?.legacy_type || selectedType?.label_en || "Other",
        category: deptRow?.legacy_category ?? "General",
      });
      onFiled?.(out.request);
      const who = out.routed?.name ? ` Sent to ${out.routed.name}.` : "";
      toast.success(out.merged ? `Added to the open request for room ${room}.` : `Filed for room ${room}.${who}`);
      reset();
      loadOpen();
      if (compact) onDone?.();
    } catch (err) {
      toast.error("Could not file the request", errorText(err));
    } finally {
      setSubmitting(false);
    }
  };

  const noteInstead = async () => {
    if (!dupe) return;
    setSubmitting(true);
    try {
      await addNote(slug, dupe.id, describe());
      toast.success(`Note added to #${dupe.id}.`);
      reset();
      loadOpen();
      if (compact) onDone?.();
    } catch (err) {
      toast.error("Could not add the note", errorText(err));
    } finally {
      setSubmitting(false);
    }
  };

  // ─── Keyboard ───
  const onRoomKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && suggestions.length) {
      e.preventDefault(); setSuggestOpen(true); setSuggestIdx(i => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp" && suggestions.length) {
      e.preventDefault(); setSuggestIdx(i => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (suggestOpen && suggestions[suggestIdx]) pickRoom(suggestions[suggestIdx]);
      else if (typeKey) void submit();
      else rovingStop(dept ? gridRef.current : chipsRef.current)?.focus();
    } else if (e.key === "Escape") {
      setSuggestOpen(false);
    }
  };

  /* Digits act on the step in front of the desk: keys 1-8 pick a
     department, then 1-9 a request type, then set the quantity (+/- too);
     on a focused chip group they pick in that group. Arrows walk a group,
     Backspace steps back (type, then department), Enter files. Ignored
     while typing in a text field (except the global Enter). */
  const onFormKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement;
    const typing = t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT";
    if (e.key === "Enter" && t.tagName === "TEXTAREA" && !e.shiftKey) {
      e.preventDefault(); void submit(); return;
    }
    // Enter on a department or type chip: file when the form is complete
    // (Space picks a chip), else pick the chip. Other buttons keep Enter.
    if (e.key === "Enter" && t.tagName === "BUTTON" && t.closest(".svc-pe-chips, .svc-pe-grid, .svc-pe-qty")) {
      e.preventDefault();
      if (room && typeKey) void submit();
      else (t as HTMLButtonElement).click();
      return;
    }
    if (typing) return;
    const group = t.tagName === "BUTTON" ? t.closest<HTMLElement>(".svc-pe-chips, .svc-pe-grid") : null;
    if (group && ARROWS.includes(e.key)) {
      const btns = Array.from(group.querySelectorAll<HTMLButtonElement>("button"));
      const i = btns.indexOf(t as HTMLButtonElement);
      const back = e.key === "ArrowLeft" || e.key === "ArrowUp";
      const n = e.key === "Home" ? 0 : e.key === "End" ? btns.length - 1 : back ? Math.max(i - 1, 0) : Math.min(i + 1, btns.length - 1);
      e.preventDefault(); btns[n]?.focus();
      return;
    }
    const digit = /^[1-9]$/.test(e.key) ? Number(e.key) : 0;
    const inDepts = !!group?.classList.contains("svc-pe-chips");
    const inTypes = !!group?.classList.contains("svc-pe-grid");
    if (digit && (inDepts || (!dept && !inTypes))) {
      const d = departments[digit - 1];
      if (d) { e.preventDefault(); pickDept(d.key); }
    } else if (digit && (inTypes || !typeKey)) {
      const ty = types[digit - 1];
      if (ty) { e.preventDefault(); pickType(ty); }
    } else if (digit) {
      e.preventDefault(); setQty(Math.min(digit, maxQty));
    } else if (e.key === "+" || e.key === "=") {
      e.preventDefault(); setQty(q => Math.min(q + 1, maxQty));
    } else if (e.key === "-") {
      e.preventDefault(); setQty(q => Math.max(q - 1, 1));
    } else if (e.key === "Enter" && t.tagName !== "BUTTON") {
      e.preventDefault(); void submit();
    } else if (e.key === "Backspace" && typeKey) {
      e.preventDefault(); setTypeKey(null); setAmenityId(null); focusNext("types");
    } else if (e.key === "Backspace" && dept) {
      e.preventDefault(); setDept(null); focusNext("depts");
    }
  };

  // ─── Render ───
  return (
    <div className="svc-pe" onKeyDown={onFormKey}>
      <SvcStyles />

      {/* 1. Room */}
      <Field label="Room" htmlFor="svc-pe-room" hint={<>Type the room, <span className="svc-kbd">Enter</span> to pick</>}>
        <input id="svc-pe-room" ref={roomRef} className="cx-input svc-pe-room" autoComplete="off" inputMode="numeric"
               role="combobox" aria-expanded={suggestOpen && suggestions.length > 0} aria-controls="svc-pe-rooms"
               aria-activedescendant={suggestOpen && suggestions[suggestIdx] ? roomOptionId(suggestions[suggestIdx].room) : undefined}
               aria-autocomplete="list" value={roomInput} placeholder="e.g. 502"
               onChange={e => { setRoomInput(e.target.value); setSuggestOpen(true); setSuggestIdx(0); setDupe(null); }}
               onKeyDown={onRoomKey} onBlur={() => setTimeout(() => setSuggestOpen(false), 120)} />
      </Field>
      {suggestOpen && suggestions.length > 0 && (
        <ul id="svc-pe-rooms" className="svc-pe-suggest" role="listbox">
          {suggestions.map((r, i) => (
            <li key={r.room} id={roomOptionId(r.room)} role="option" aria-selected={i === suggestIdx} onMouseDown={e => { e.preventDefault(); pickRoom(r); }}>
              <strong>{r.room}</strong>
              {r.floor && <span className="svc-faint">Floor {r.floor}</span>}
              {r.guestName && <span className="svc-muted">{maskName(r.guestName, revealNames)}</span>}
              {r.vip && <Badge tone="gold">VIP</Badge>}
            </li>
          ))}
        </ul>
      )}

      {room && (
        <div className="svc-row">
          {roomInfo?.floor && <Badge>Floor {roomInfo.floor}</Badge>}
          {!roomInfo && rooms.length > 0 && <Badge tone="warn" title="Not in the room register">Unknown room</Badge>}
          {roomInfo?.guestName && <span className="svc-muted">{maskName(roomInfo.guestName, revealNames)}</span>}
          {roomInfo?.vip && <Badge tone="gold">VIP</Badge>}
          {roomInfo?.returning && <Badge tone="info">Returning</Badge>}
          {roomJobs.length > 0 && (
            <button type="button" className="svc-linkbtn" onClick={() => setStatusOpen(true)}>
              {roomJobs.length} open job{roomJobs.length === 1 ? "" : "s"}: caller wants status?
            </button>
          )}
        </div>
      )}

      {/* 2. Department chips, keys 1-8 */}
      <div>
        <div className="cx-label" style={{ marginBottom: 6 }}>Department</div>
        <div className="svc-pe-chips" ref={chipsRef} role="group" aria-label="Department">
          {departments.map((d, i) => (
            <button key={d.key} type="button" className="svc-pe-chip" aria-pressed={dept === d.key}
                    tabIndex={(dept ? dept === d.key : i === 0) ? 0 : -1} onClick={() => pickDept(d.key)}>
              <span className="svc-kbd">{i + 1}</span>{d.name}
            </button>
          ))}
        </div>
      </div>

      {/* 3. Request type / amenity grid with quantity */}
      {dept && (
        <div>
          <div className="cx-label" style={{ marginBottom: 6 }}>What do they need?</div>
          <div className="svc-pe-grid" ref={gridRef} role="group" aria-label="Request type">
            {types.map((t, i) => (
              <button key={t.key} type="button" className="svc-pe-item" aria-pressed={typeKey === t.key}
                      tabIndex={(types.some(x => x.key === typeKey) ? typeKey === t.key : i === 0) ? 0 : -1}
                      aria-keyshortcuts={i < 9 ? String(i + 1) : undefined} onClick={() => pickType(t)}>
                <span>{i < 9 && <span className="svc-kbd">{i + 1}</span>}{t.label_en}</span>
                {t.default_severity !== "L3" && <span className="svc-faint">{t.default_severity}</span>}
              </button>
            ))}
          </div>
        </div>
      )}
      {typeKey && (amenitiesForType.length > 0 || selectedType?.is_amenity) && (
        <div className="svc-row">
          {amenitiesForType.length > 0 && (
            <Select aria-label="Item" value={amenityId ?? ""} onChange={e => setAmenityId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Any item</option>
              {amenitiesForType.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          )}
          <span className="svc-pe-qty" aria-label="Quantity">
            <button type="button" aria-label="One less" onClick={() => setQty(q => Math.max(q - 1, 1))}>-</button>
            <strong aria-live="polite">{qty}</strong>
            <button type="button" ref={qtyRef} aria-label="One more" onClick={() => setQty(q => Math.min(q + 1, maxQty))}>+</button>
          </span>
          <span className="svc-faint">digits or +/-</span>
        </div>
      )}

      {/* 4. Note and caller language */}
      <div className={compact ? "svc-stack" : "svc-grid-2"}>
        <Field label="Note" htmlFor="svc-pe-note" hint={<><span className="svc-kbd">Enter</span> files, <span className="svc-kbd">Shift</span>+<span className="svc-kbd">Enter</span> new line</>}>
          <Textarea id="svc-pe-note" ref={noteRef} rows={2} value={note} maxLength={1500}
                    placeholder="Anything the team should know" onChange={e => setNote(e.target.value)} />
        </Field>
        <div className="svc-stack">
          <Field label="Caller spoke" htmlFor="svc-pe-lang">
            <Select id="svc-pe-lang" value={language} onChange={e => setLanguage(e.target.value)}>
              {CALLER_LANGUAGES.map(l => <option key={l} value={l}>{LANGUAGE_LABELS[l] ?? l}</option>)}
            </Select>
          </Field>
          <label className="svc-row svc-muted">
            <input type="checkbox" checked={urgent} onChange={e => setUrgent(e.target.checked)} /> Urgent
          </label>
        </div>
      </div>

      {/* Duplicate guard */}
      {dupe && (
        <div className="svc-dup" role="alert">
          <div>
            <strong>#{dupe.id} {(catalog.requestTypes.find(t => t.key === dupe.request_type_key)?.label_en ?? dupe.type).toLowerCase()} already open</strong>
            {dupe.assigned_to
              ? <>, assigned {minsAgo(dupe.assigned_at ?? dupe.accepted_at, now) ?? 0} min ago to {dupe.assigned_to}</>
              : <>, waiting for someone to take it</>}
            . Add a note instead?
          </div>
          <div className="svc-row">
            <Button variant="primary" size="sm" onClick={() => void noteInstead()} loading={submitting}>Add note to #{dupe.id}</Button>
            <Button size="sm" onClick={() => { setDupe(null); void submit(true); }} disabled={submitting}>File a new one</Button>
            <Button variant="ghost" size="sm" onClick={() => setDupe(null)}>Cancel</Button>
          </div>
        </div>
      )}

      {/* 5. Enter submits */}
      <div className="svc-row" style={{ justifyContent: "space-between" }}>
        <span className="svc-faint">
          {room && selectedType ? `Room ${room}: ${describe()}` : "Room, department, type, then Enter."}
        </span>
        <div className="svc-row">
          {/* a caller asking about a booking, not a service: the sales inbox */}
          {can("inquiries.respond") && (
            <Button variant="ghost" href={`/app/${slug}/inquiries/new?source=phone_desk${room ? `&room=${encodeURIComponent(room)}` : ""}`}>
              Log an inquiry
            </Button>
          )}
          {onDone && <Button variant="ghost" onClick={onDone}>Close</Button>}
          <Button variant="primary" onClick={() => void submit()} disabled={!canSubmit} loading={submitting}>
            File request <span className="svc-kbd">Enter</span>
          </Button>
        </div>
      </div>

      {/* Caller wants status */}
      <Drawer open={statusOpen} onClose={() => setStatusOpen(false)} title={`Room ${room}: open jobs`}>
        <div className="svc-stack">
          {roomJobs.length === 0 && <p className="svc-muted">Nothing open for this room.</p>}
          {roomJobs.map(j => (
            <div key={j.id} className="svc-openjob">
              <div style={{ minWidth: 0 }}>
                <div><strong>#{j.id}</strong> {j.type}{j.subtype ? ` · ${j.subtype}` : ""}</div>
                <div className="svc-faint">{j.description}</div>
                <OriginalToggle req={j} />
                <div className="svc-faint">
                  {effectiveState(j) === "new" ? "Not taken yet" : j.assigned_to ? `With ${j.assigned_to}` : "Assigned"}
                  {` · filed ${minsAgo(j.timestamp, now) ?? 0} min ago`}
                </div>
              </div>
              <V2Chips req={j} now={now} />
            </div>
          ))}
          <p className="svc-caption">Tell the guest the team is on it; do not promise a time the SLA does not show.</p>
        </div>
      </Drawer>
    </div>
  );
}
