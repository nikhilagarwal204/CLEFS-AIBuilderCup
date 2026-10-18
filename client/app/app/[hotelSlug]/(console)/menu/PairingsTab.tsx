"use client";

/* Pairings: "with the burger, suggest the fries" (pairing) or "for AED 20
   more, the wagyu" (upgrade). The guest menu, the concierge and the quote
   all read these; each suggestion shown is logged, so the funnel tab can
   say which pairings actually sell. Saving an existing pair upserts. */

import { useMemo, useState } from "react";
import { api, hotelPath, useApi } from "../../../../lib/api";
import {
  Badge, Button, Card, EmptyState, Field, Input, Segmented, Select, Table, useToast, type Column,
} from "../../../../ui";
import { errText } from "./lib";
import type { MenuData, Pair } from "./types";

export default function PairingsTab({ data }: { data: MenuData }) {
  const toast = useToast();
  const pairs = useApi<Pair[]>(hotelPath(data.slug, "/menu/pairs"), [data.slug]);
  const [itemId, setItemId] = useState("");
  const [suggestId, setSuggestId] = useState("");
  const [kind, setKind] = useState<"pairing" | "upgrade">("pairing");
  const [reasonEn, setReasonEn] = useState("");
  const [reasonAr, setReasonAr] = useState("");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<number | null>(null);

  const nameOf = useMemo(() => {
    const m = new Map(data.items.map(i => [i.id, i.name_en]));
    return (id: number) => m.get(id) ?? `Item ${id}`;
  }, [data.items]);

  const options = useMemo(
    () => [...data.items].sort((a, b) => a.name_en.localeCompare(b.name_en))
      .map(i => ({ value: String(i.id), label: i.active ? i.name_en : `${i.name_en} (hidden)` })),
    [data.items],
  );

  const add = async () => {
    if (!itemId || !suggestId || itemId === suggestId) return;
    setBusy(true);
    try {
      await api.post(hotelPath(data.slug, "/menu/pairs"), {
        item_id: Number(itemId), suggest_item_id: Number(suggestId), kind,
        reason_en: reasonEn.trim() || null, reason_ar: reasonAr.trim() || null,
      });
      toast.success("Pairing saved");
      setSuggestId(""); setReasonEn(""); setReasonAr("");
      pairs.reload();
    } catch (err) {
      toast.error("Could not save the pairing", errText(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (p: Pair) => {
    setRemoving(p.id);
    try {
      await api.del(hotelPath(data.slug, `/menu/pairs/${p.id}`));
      pairs.setData(prev => (prev ?? []).filter(x => x.id !== p.id));
    } catch (err) {
      toast.error("Could not remove the pairing", errText(err));
    } finally {
      setRemoving(null);
    }
  };

  const columns: Column<Pair>[] = [
    { key: "item", header: "When a guest picks", render: p => <span className="ups-item-name">{nameOf(p.item_id)}</span> },
    { key: "suggest", header: "Suggest", render: p => nameOf(p.suggest_item_id) },
    { key: "kind", header: "Type", render: p => <Badge tone={p.kind === "upgrade" ? "gold" : "info"}>{p.kind === "upgrade" ? "Upgrade" : "Pairing"}</Badge> },
    {
      key: "reason", header: "Reason shown",
      render: p => (
        <div>
          <div>{p.reason_en || <span className="cx-faint">None</span>}</div>
          {p.reason_ar && <div className="ups-item-ar" lang="ar">{p.reason_ar}</div>}
        </div>
      ),
    },
    {
      key: "x", header: "", align: "right",
      render: p => data.canManage && (
        <Button size="sm" variant="ghost" loading={removing === p.id}
                onClick={e => { e.stopPropagation(); void remove(p); }}>Remove</Button>
      ),
    },
  ];

  const same = itemId !== "" && itemId === suggestId;

  return (
    <div className="cx-stack">
      {data.canManage && (
        <Card title="Add a pairing" subtitle="Suggestions appear on the guest menu and in the concierge when the first item is in the basket.">
          <div className="ups-form ups-form--2">
            <Field label="When a guest picks" required>
              <Select value={itemId} onChange={e => setItemId(e.target.value)} placeholder="Choose an item" options={options} />
            </Field>
            <Field label="Suggest" required error={same ? "Choose a different item" : undefined}>
              <Select value={suggestId} onChange={e => setSuggestId(e.target.value)} placeholder="Choose an item" options={options} />
            </Field>
            <Field label="Reason (English)" hint="Short, e.g. Goes well with the steak.">
              <Input value={reasonEn} maxLength={200} onChange={e => setReasonEn(e.target.value)} />
            </Field>
            <Field label="Reason (Arabic)">
              <Input className="ups-rtl" dir="rtl" lang="ar" value={reasonAr} maxLength={200} onChange={e => setReasonAr(e.target.value)} />
            </Field>
            <div className="ups-span cx-row">
              <Segmented size="sm" ariaLabel="Suggestion type" value={kind} onChange={setKind}
                         options={[{ value: "pairing", label: "Pairing" }, { value: "upgrade", label: "Upgrade" }]} />
              <span className="ups-spacer" />
              <Button variant="primary" onClick={add} loading={busy} disabled={!itemId || !suggestId || same}>Save pairing</Button>
            </div>
          </div>
        </Card>
      )}
      <Card title="Pairings" subtitle="Only active items that are orderable right now are suggested to guests.">
        {pairs.error ? (
          <EmptyState title="Could not load pairings" body={pairs.error.message}
                      action={<Button onClick={pairs.reload}>Try again</Button>} />
        ) : (
          <Table
            columns={columns}
            rows={pairs.data ?? []}
            rowKey={p => p.id}
            loading={pairs.loading}
            empty={<EmptyState title="No pairings yet" body="Pair a main with a side or a drink. Guests see one or two suggestions, never a wall of offers." />}
          />
        )}
      </Card>
    </div>
  );
}
