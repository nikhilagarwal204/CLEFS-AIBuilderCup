"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Concierge settings (03 spec 2.5 console): guest languages, quick-reply
   chips, how menu prices are shown, and the session guard rails.

   Where each one lives, so a save never clobbers another module's keys:
     - languages: the hotel's guest_languages (core hotel settings, PATCH);
     - chips, voice, daily cap, legacy QR: module settings "concierge";
     - price display: module settings "upsell" (the menu owns pricing).
   Module settings PUT replaces the whole object, so every save here sends
   the settings just read with only this card's keys changed. All saves need
   core.settings.manage (owner, gm); others see the values read-only.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, hotelPath, useApi } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Button, Card, Field, Input, Segmented, Select, Skeleton, useToast } from "../../../../../ui";
import { GUEST_LANGUAGES, LANGUAGE_NAMES, PERM, errText } from "../lib";
import type { Chip, ConciergeSettings, ModuleSettingsView } from "../types";
import { ChipsCard } from "./ChipsCard";

interface UpsellSettings { price_display?: "inclusive" | "plus_plus"; service_charge_pct?: string | number;
  municipality_fee_pct?: string | number; vat_pct?: string | number; [k: string]: unknown }

const VOICES = ["marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"];

export default function ConciergeSettingsPage() {
  const { slug, hotel, setHotel, moduleEnabled } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const editable = can(PERM.settings);
  const cc = useApi<ModuleSettingsView<ConciergeSettings>>(hotelPath(slug, "/module-settings/concierge"), [slug]);
  const ups = useApi<ModuleSettingsView<UpsellSettings>>(
    moduleEnabled("upsell") ? hotelPath(slug, "/module-settings/upsell") : null, [slug]);
  const [saving, setSaving] = useState<string | null>(null);
  const [langs, setLangs] = useState<string[] | null>(null);
  const shownLangs = langs ?? hotel?.guest_languages ?? ["en"];

  async function saveModule<S>(module: "concierge" | "upsell", view: ModuleSettingsView<S> | undefined, patch: Partial<S>, card: string) {
    if (!view) return;
    setSaving(card);
    try {
      await api.put(hotelPath(slug, `/module-settings/${module}`), { settings: { ...view.settings, ...patch } });
      toast.success("Saved");
      if (module === "concierge") cc.reload(); else ups.reload();
    } catch (e) {
      toast.error("Could not save", errText(e));
    } finally {
      setSaving(null);
    }
  }

  async function saveLanguages() {
    const next = GUEST_LANGUAGES.filter(l => shownLangs.includes(l));
    if (!next.length) { toast.error("Keep at least one language"); return; }
    setSaving("langs");
    try {
      await api.patch(hotelPath(slug, "/settings"), { guest_languages: next });
      setHotel({ guest_languages: next });
      setLangs(null);
      toast.success("Languages saved");
    } catch (e) {
      toast.error("Could not save", errText(e));
    } finally {
      setSaving(null);
    }
  }

  if (cc.error) return <div className="cc-callout" role="alert">{errText(cc.error)}</div>;
  if (!cc.data) return <Skeleton lines={10} />;
  const s = cc.data.settings;
  const toggle = (l: string) => setLangs(shownLangs.includes(l) ? shownLangs.filter(x => x !== l) : [...shownLangs, l]);

  return (
    <>
      {!editable && <div className="cc-callout cc-callout--info">Only the owner or GM can change these settings.</div>}
      <Card title="Guest languages" subtitle="The languages the guest page offers. The concierge answers in the guest's language; Arabic replies are Gulf-leaning Modern Standard Arabic.">
        <div className="cx-stack">
          <div className="cc-lang-grid">
            {GUEST_LANGUAGES.map(l => (
              <label key={l}>
                <input type="checkbox" checked={shownLangs.includes(l)} disabled={!editable || l === "en"} onChange={() => toggle(l)} />
                {LANGUAGE_NAMES[l]}{l === "en" ? " (always on)" : ""}
              </label>
            ))}
          </div>
          <div><Button size="sm" loading={saving === "langs"} disabled={!editable || langs === null} onClick={() => void saveLanguages()}>Save languages</Button></div>
        </div>
      </Card>

      <ChipsCard key={cc.data.updated_at ?? "none"} languages={hotel?.guest_languages ?? ["en"]} chips={(s.chips ?? {}) as Record<string, Chip[]>}
        disabled={!editable} saving={saving === "chips"} onSave={chips => void saveModule("concierge", cc.data, { chips }, "chips")} />

      {ups.data && (
        <Card title="Menu price display" subtitle="How the concierge quotes menu prices when a guest orders.">
          <div className="cx-stack">
            <Segmented ariaLabel="Price display" value={ups.data.settings.price_display ?? "inclusive"}
              onChange={v => editable && void saveModule("upsell", ups.data, { price_display: v }, "price")}
              options={[{ value: "inclusive", label: "Prices include fees" }, { value: "plus_plus", label: "Plus plus (fees added)" }]} />
            <p className="cc-sub">
              {(ups.data.settings.price_display ?? "inclusive") === "plus_plus"
                ? `Menu prices are net. The quote adds service charge ${ups.data.settings.service_charge_pct ?? 10}%, municipality fee ${ups.data.settings.municipality_fee_pct ?? 7}% and VAT ${ups.data.settings.vat_pct ?? 5}%, and the concierge says so before the guest confirms.`
                : "The menu price is what the guest pays. The receipt splits the fees out of it."}
              {" "}Fee rates are set under Menu.
            </p>
          </div>
        </Card>
      )}

      <SessionCard settings={s} editable={editable} saving={saving === "session"}
        onSave={patch => void saveModule("concierge", cc.data, patch, "session")} />
    </>
  );
}

function SessionCard({ settings: s, editable, saving, onSave }: {
  settings: ConciergeSettings; editable: boolean; saving: boolean; onSave: (p: Partial<ConciergeSettings>) => void;
}) {
  const [voice, setVoice] = useState(s.voice ?? "marin");
  const [cap, setCap] = useState(String(s.daily_session_cap ?? 500));
  const [legacyQr, setLegacyQr] = useState(!!s.legacy_qr_personalization);
  const capN = Number(cap);
  const capOk = Number.isInteger(capN) && capN >= 0 && capN <= 100000;
  return (
    <Card title="Voice and limits" subtitle="The concierge's speaking voice, and the ceilings that stop a leaked QR code from running up the AI bill.">
      <div className="cx-stack">
        <div className="cc-fields">
          <Field label="Voice"><Select value={voice} disabled={!editable} onChange={e => setVoice(e.target.value)} options={VOICES.map(v => ({ value: v, label: v }))} /></Field>
          <Field label="Conversations per day" hint="Per hotel, in hotel time. 0 switches the guest concierge off." error={capOk ? undefined : "0 to 100,000"}>
            <Input type="number" min={0} max={100000} value={cap} disabled={!editable} onChange={e => setCap(e.target.value)} />
          </Field>
        </div>
        <label className="cc-check">
          <input type="checkbox" checked={legacyQr} disabled={!editable} onChange={e => setLegacyQr(e.target.checked)} />
          <span>Personalise old QR codes (printed before room keys). Off by default: anyone with an old code could see the guest&apos;s first name.</span>
        </label>
        <div>
          <Button size="sm" loading={saving} disabled={!editable || !capOk}
            onClick={() => onSave({ voice, daily_session_cap: capN, legacy_qr_personalization: legacyQr })}>Save</Button>
        </div>
      </div>
    </Card>
  );
}
