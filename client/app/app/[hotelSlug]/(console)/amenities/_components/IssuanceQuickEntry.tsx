"use client";

/* ═══════════════════════════════════════════════════════════════════════
   IssuanceQuickEntry (02 spec 5.3 issue/): the floor attendant's screen.
   Phone first: pick shift, floor and who is issuing once, then tap + on
   each item going onto the trolley and log the lot in one POST.

   Choices:
   - Shift defaults from the hotel-local clock with the server's own rule
     (07-15 morning, 15-23 evening, else night) and stays editable, since a
     night attendant often logs the closet fill after 07:00.
   - Shift, floor, issuer and reason are remembered per hotel on this
     device (localStorage, best effort) because the same person logs the
     same floor all shift. Counts are never remembered: a half-logged
     trolley from yesterday must not be sent twice.
   - The issuer is optional. On a paired device the server fills in the
     on-shift person (X-Clefs-Staff) for lines that name nobody.
   - Steppers are 44px targets with a typed fallback; the submit bar is
     sticky above the console's phone tab bar.
   ═══════════════════════════════════════════════════════════════════════ */

import { useEffect, useMemo, useState } from "react";
import { ApiError, api, hotelPath, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Button, Card, Field, Input, Segmented, Select, Skeleton, useToast } from "../../../../../ui";
import { AmnEmpty } from "./AmenitiesShell";
import {
  buildIssueLines, categoryLabel, clampQty, localHour, parseQtyInput, REASON_LABELS, shiftForHour, shiftLabel,
  SHIFTS, totalPieces, type ShiftKey,
} from "./lib";
import type { Item, Line, Location } from "./types";

interface StaffRow { id: number; name: string; department: string | null; is_active?: boolean }

interface Ctx { shift: ShiftKey; floor: string; staffName: string; reason: string }

const REASONS = ["REPLENISH", "GUEST_REQUEST", "VIP", "DAMAGE", "RETURN"] as const;
const ctxKey = (slug: string) => `clefs:amn:qe:${slug}`;

function loadCtx(slug: string, fallbackShift: ShiftKey): Ctx {
  const base: Ctx = { shift: fallbackShift, floor: "", staffName: "", reason: "REPLENISH" };
  try {
    const raw = window.localStorage.getItem(ctxKey(slug));
    if (!raw) return base;
    const saved = JSON.parse(raw) as Partial<Ctx> & { day?: string; hour?: number };
    // The remembered shift only holds within the same shift window; a new
    // shift starts from the clock again.
    const shift = saved.shift && saved.shift === fallbackShift ? saved.shift : fallbackShift;
    return {
      shift,
      floor: typeof saved.floor === "string" ? saved.floor : "",
      staffName: typeof saved.staffName === "string" ? saved.staffName : "",
      reason: REASONS.includes(saved.reason as (typeof REASONS)[number]) ? (saved.reason as string) : "REPLENISH",
    };
  } catch {
    return base;
  }
}

function saveCtx(slug: string, ctx: Ctx) {
  try { window.localStorage.setItem(ctxKey(slug), JSON.stringify(ctx)); } catch { /* private mode: fine */ }
}

export default function IssuanceQuickEntry({ onLogged }: { onLogged?: (lines: Line[]) => void }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();

  const items = useApi<Item[]>(hotelPath(slug, "/amenities/items"), [slug]);
  const locations = useApi<Location[]>(hotelPath(slug, "/amenities/locations"), [slug]);
  // The legacy staff list needs service.view; without it the issuer is typed.
  const staff = useApi<StaffRow[]>(can(["service.view", "service.manage"]) ? hotelPath(slug, "/staff") : null, [slug]);

  const clockShift = shiftForHour(localHour(fmt.tz));
  const [ctx, setCtx] = useState<Ctx>(() => ({ shift: clockShift, floor: "", staffName: "", reason: "REPLENISH" }));
  const [counts, setCounts] = useState<Record<number, number>>({});
  const [query, setQuery] = useState("");
  const [cat, setCat] = useState<string>("ALL");
  const [sending, setSending] = useState(false);

  // Read the remembered context after mount (localStorage is client only,
  // so reading it during render would mismatch the server HTML).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCtx(loadCtx(slug, clockShift));
    // Once per hotel; the clock shift at mount is the right fallback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  const patchCtx = (p: Partial<Ctx>) => setCtx(prev => {
    const next = { ...prev, ...p };
    saveCtx(slug, next);
    return next;
  });

  // ─── Derived ───
  const active = useMemo(() => (items.data ?? []).filter(i => i.active), [items.data]);
  const cats = useMemo(() => Array.from(new Set(active.map(i => i.category))), [active]);
  const floors = useMemo(() => {
    const set = new Set<string>();
    for (const l of locations.data ?? []) if (l.floor) set.add(l.floor);
    return Array.from(set).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  }, [locations.data]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return active.filter(i => (cat === "ALL" || i.category === cat) && (!q
      || i.name_en.toLowerCase().includes(q) || (i.name_ar ?? "").includes(q) || i.key.toLowerCase().includes(q)
      || i.aliases.some(a => a.toLowerCase().includes(q))));
  }, [active, cat, query]);
  const pieces = totalPieces(counts);
  const cost = active.reduce((s, i) => s + (counts[i.id] ? clampQty(counts[i.id]) * (i.unit_cost_minor ?? 0) : 0), 0);
  const staffMatch = (staff.data ?? []).find(s => s.name.trim().toLowerCase() === ctx.staffName.trim().toLowerCase());

  const setCount = (id: number, v: number) => setCounts(prev => {
    const next = { ...prev };
    const q = clampQty(v);
    if (q > 0) next[id] = q; else delete next[id];
    return next;
  });

  // ─── Submit ───
  const submit = async () => {
    const lines = buildIssueLines(counts, { ...ctx, staffId: staffMatch?.id ?? null });
    if (!lines.length || sending) return;
    setSending(true);
    try {
      const res = await api.post<{ lines: Line[] }>(hotelPath(slug, "/amenities/issuance"), { lines });
      const n = totalPieces(counts);
      toast.success(`Logged ${n} piece${n === 1 ? "" : "s"}`,
        `${lines.length} item${lines.length === 1 ? "" : "s"}${ctx.floor ? ` to floor ${ctx.floor}` : ""}, ${shiftLabel(ctx.shift).toLowerCase()} shift.`);
      setCounts({});
      onLogged?.(res.lines);
    } catch (err) {
      // Counts stay on screen so nothing has to be re-tapped after a retry.
      toast.error("Not logged", err instanceof ApiError ? err.message : "Check the connection and try again.");
    } finally {
      setSending(false);
    }
  };

  if (items.error) {
    return <Card><AmnEmpty title="Could not load the item list" body={items.error.message}
                           action={<Button onClick={items.reload}>Try again</Button>} /></Card>;
  }

  return (
    <div className="amn-qe">
      <Card padded>
        <div className="amn-qe__ctx">
          <Field label="Shift" className="amn-span2">
            <Segmented ariaLabel="Shift" value={ctx.shift} onChange={v => patchCtx({ shift: v })}
                       options={SHIFTS.map(s => ({ value: s, label: shiftLabel(s) }))} />
          </Field>
          <Field label="Floor" hint="Where the items are going">
            <Input inputMode="numeric" list="amn-qe-floors" value={ctx.floor} maxLength={20} placeholder="e.g. 6"
                   onChange={e => patchCtx({ floor: e.target.value })} />
          </Field>
          <Field label="Reason">
            <Select value={ctx.reason} onChange={e => patchCtx({ reason: e.target.value })}>
              {REASONS.map(r => <option key={r} value={r}>{REASON_LABELS[r]}</option>)}
            </Select>
          </Field>
          <Field label="Issued by" hint={staffMatch ? "Matched to the staff list" : "Optional"} className="amn-span2">
            <Input list="amn-qe-staff" value={ctx.staffName} maxLength={80} placeholder="Name" autoComplete="off"
                   onChange={e => patchCtx({ staffName: e.target.value })} />
          </Field>
        </div>
        <datalist id="amn-qe-floors">{floors.map(f => <option key={f} value={f} />)}</datalist>
        <datalist id="amn-qe-staff">
          {(staff.data ?? []).filter(s => s.is_active !== false).map(s => <option key={s.id} value={s.name} />)}
        </datalist>
        {ctx.reason === "RETURN" && (
          <p className="amn-note">Returns are stored as stock coming back, so they reduce issued.</p>
        )}
      </Card>

      <Input className="amn-qe__search" type="search" value={query} placeholder="Search items"
             aria-label="Search items" onChange={e => setQuery(e.target.value)} />
      {cats.length > 1 && (
        <div className="amn-qe__cats" role="group" aria-label="Category">
          {["ALL", ...cats].map(c => (
            <Button key={c} size="sm" variant={cat === c ? "primary" : "secondary"} aria-pressed={cat === c}
                    onClick={() => setCat(c)}>
              {c === "ALL" ? "All" : categoryLabel(c)}
            </Button>
          ))}
        </div>
      )}

      {items.loading && !items.data ? (
        <Skeleton lines={6} height={56} gap={10} />
      ) : shown.length === 0 ? (
        <Card><AmnEmpty title={active.length ? "No item matches" : "No items in the catalog yet"}
                        body={active.length ? "Try another word or category." : "A manager can add items on the Catalog tab."} /></Card>
      ) : (
        <ul className="amn-qe__grid" aria-label="Items">
          {shown.map(i => (
            <ItemStepper key={i.id} item={i} value={counts[i.id] ?? 0} onChange={v => setCount(i.id, v)} />
          ))}
        </ul>
      )}

      <div className="amn-qe__bar" role="region" aria-label="Log issuance">
        <div className="amn-qe__sum" aria-live="polite">
          <b>{pieces}</b> piece{pieces === 1 ? "" : "s"}
          {pieces > 0 && cost > 0 && `, about ${fmt.money(cost, { minor: true })}`}
        </div>
        {pieces > 0 && <Button variant="ghost" onClick={() => setCounts({})} disabled={sending}>Clear</Button>}
        <Button variant="primary" onClick={submit} loading={sending} disabled={pieces === 0}>
          Log {pieces > 0 ? pieces : ""}
        </Button>
      </div>
    </div>
  );
}

// ─── One item row with a stepper ─────────────────────────────────────────

function ItemStepper({ item, value, onChange }: { item: Item; value: number; onChange: (v: number) => void }) {
  // A trolley or closet fill runs to hundreds; item.max_qty is the guest
  // request cap and does not apply to staff issuance.
  const max = 999;
  const inputId = `amn-qe-${item.id}`;
  return (
    <li className="amn-qe__item" data-on={value > 0 || undefined}>
      <label className="amn-qe__name" htmlFor={inputId}>
        <b>{item.name_en}</b>
        <span>{item.name_ar || categoryLabel(item.category)}</span>
      </label>
      <div className="amn-step">
        <button type="button" aria-label={`One less ${item.name_en}`} disabled={value <= 0}
                onClick={() => onChange(value - 1)}>-</button>
        <input id={inputId} inputMode="numeric" pattern="[0-9]*" value={value === 0 ? "" : String(value)} placeholder="0"
               onChange={e => onChange(parseQtyInput(e.target.value, max))} onFocus={e => e.target.select()} />
        <button type="button" aria-label={`One more ${item.name_en}`} disabled={value >= max}
                onClick={() => onChange(value + 1)}>+</button>
      </div>
    </li>
  );
}
