"use client";

/* Settings tab (04 spec 5.11): place id, the Business Profile connection
   (with the read-only project quota and the location picker), the Google
   Hotels property token, area and landmarks, rank keywords, AI markets,
   the monthly budget, and the engines, including Perplexity's legal gate
   and the SerpApi toggle. Only visibility.manage can save; everyone with
   view access sees the values. The form mirrors the server's 422 rules. */

import { useState, type ReactNode } from "react";
import { ApiError, apiFetch, hotelPath, useApi } from "../../../../lib/api";
import { currencyDigits, minorToMajor, useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import { Button, Card, Field, Input, Textarea, useToast } from "../../../../ui";
import type { AiView, VisSettings } from "./types";
import { ENGINE_LABELS, bodyFrom, draftFrom, validateDraft, type SettingsDraft } from "./model";
import { TabState, errText } from "./parts";
import GbpConnectCard from "./GbpConnectCard";

// format.ts's money digits for the budget field (model.ts takes them injected).
const MONEY = { currencyDigits, minorToMajor };

// ─── Engines ─────────────────────────────────────────────────────────────

const ENGINE_NOTES: Record<string, string> = {
  gemini_web_search: "Google Gemini with Google Search grounding. Labelled API simulation.",
  perplexity_sonar: "Runs only after Clefs' legal review of Perplexity's terms and with its key; home market only.",
  serpapi_ai_mode: "Google AI Mode through SerpApi (third-party). Needs the SerpApi toggle on.",
};

// ─── Tab ─────────────────────────────────────────────────────────────────

export default function SettingsTab() {
  const { slug } = useHotel();
  const s = useApi<VisSettings>(hotelPath(slug, "/visibility/settings"), [slug]);
  return (
    <TabState loading={s.loading && !s.data} error={s.error} onRetry={s.reload}>
      {/* Keyed by the save time: a save (or a location pick) returns fresh
          settings and the form restarts from them, with no effect syncing. */}
      {s.data && <SettingsBody key={s.data.updated_at ?? "new"} settings={s.data} onSaved={next => s.setData(next)} />}
    </TabState>
  );
}

function SettingsBody({ settings, onSaved }: { settings: VisSettings; onSaved: (s: VisSettings) => void }) {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const fmt = useFormat();
  const canManage = can("visibility.manage");
  // Engine availability (keys, the Perplexity legal gate) comes from /ai.
  const ai = useApi<AiView>(hotelPath(slug, "/visibility/ai"), [slug]);
  const [draft, setDraft] = useState<SettingsDraft>(() => draftFrom(settings, MONEY));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const set = <K extends keyof SettingsDraft>(k: K, v: SettingsDraft[K]) => setDraft(d => ({ ...d, [k]: v }));
  const engineStatus = (k: string) => ai.data?.engines.find(e => e.engine === k)?.status;

  const save = async () => {
    const errs = validateDraft(draft, MONEY);
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setSaving(true);
    try {
      const next = await apiFetch<VisSettings>(hotelPath(slug, "/visibility/settings"), { method: "PUT", json: bodyFrom(draft, MONEY) });
      toast.success("Visibility settings saved");
      onSaved(next);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) setErrors(err.fieldErrors);
      toast.error("Could not save", errText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="vis-stack">
      <GbpConnectCard state={settings.gbp} onSaved={onSaved} />
      <FormBody draft={draft} errors={errors} set={set} disabled={!canManage} engineStatus={engineStatus} />
      <div className="vis-row">
        {canManage ? <Button variant="primary" onClick={() => void save()} loading={saving}>Save settings</Button>
          : <span className="vis-muted">Only the owner or GM can change these settings.</span>}
        <span className="vis-spacer" />
        {settings.updated_at && <span className="vis-muted">Last saved {fmt.relative(settings.updated_at)}</span>}
      </div>
    </div>
  );
}

// ─── Form ────────────────────────────────────────────────────────────────

type Setter = <K extends keyof SettingsDraft>(k: K, v: SettingsDraft[K]) => void;

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return <Card title={title} subtitle={subtitle}><div className="vis-form">{children}</div></Card>;
}

function FormBody({ draft, errors, set, disabled, engineStatus }: {
  draft: SettingsDraft; errors: Record<string, string>; set: Setter; disabled: boolean;
  engineStatus: (k: string) => string | undefined;
}) {
  const text = (k: keyof SettingsDraft, label: string, hint?: string, placeholder?: string) => (
    <Field label={label} hint={hint} error={errors[k]}>
      <Input value={String(draft[k])} disabled={disabled} placeholder={placeholder} invalid={Boolean(errors[k])}
        onChange={e => set(k, e.target.value as never)} />
    </Field>
  );
  const lines = (k: keyof SettingsDraft, label: string, hint: string) => (
    <Field label={label} hint={hint} error={errors[k]}>
      <Textarea rows={3} value={String(draft[k])} disabled={disabled} invalid={Boolean(errors[k])}
        onChange={e => set(k, e.target.value as never)} />
    </Field>
  );
  const toggleEngine = (k: string, on: boolean) =>
    set("engines_enabled", on ? [...draft.engines_enabled.filter(e => e !== k), k] : draft.engines_enabled.filter(e => e !== k));

  return (
    <>
      <Section title="Google" subtitle="Where Clefs finds your hotel on Google Hotels and Maps.">
        {text("place_id", "Google place id", "From Google's Place ID finder.", "ChIJ...")}
        {text("gh_property_token", "Google Hotels property token", "Identifies your hotel in Google Hotels results.")}
        {text("website_url", "Website", "Used for site health and to spot your own site in AI citations.", "https://")}
        {text("booking_engine_host", "Booking engine host", "The domain of your booking engine, if different.", "book.example.com")}
        {text("free_booking_link_pattern", "Free booking link tag", "A pattern in UTM-tagged free booking link bookings.")}
        {lines("brand_aliases", "Other names for the hotel", "One per line: short names guests and AI answers use.")}
      </Section>
      <Section title="Area and rank" subtitle="Keywords are searched from a grid around the hotel every Monday.">
        {text("area", "Area", "The neighbourhood guests search for.", "Dubai Marina")}
        {lines("landmarks", "Landmarks", "One per line, up to 10.")}
        {lines("rank_keywords", "Rank keywords", "Up to 5, one per line. Leave empty for sensible defaults.")}
        {lines("room_page_urls", "Room pages", "One URL per line; checked for structured data.")}
        {text("grid_size", "Grid size", "3 to 9 points per side.")}
        {text("grid_spacing_m", "Grid spacing (metres)", "100 to 5000.")}
      </Section>
      <Section title="AI answers" subtitle="Engines are sampled weekly. Results are API simulations and are labelled so.">
        {lines("ai_markets", "Source markets", "One per line: country code and optional city, e.g. GB London. Empty uses your top 2 guest countries.")}
        <div className="vis-checks" role="group" aria-label="Answer engines">
          {Object.keys(ENGINE_LABELS).map(k => {
            const status = engineStatus(k);
            const gated = k === "perplexity_sonar" && (status === "terms_not_approved" || status === "no_perplexity_key");
            return (
              <label key={k}>
                <input type="checkbox" disabled={disabled} checked={draft.engines_enabled.includes(k)}
                  onChange={e => toggleEngine(k, e.target.checked)} />
                <span>
                  <strong>{ENGINE_LABELS[k]}</strong>
                  {status && status !== "ok" && status !== "disabled" && <span className="vis-muted"> ({status.replace(/_/g, " ")})</span>}
                  <span className="vis-muted" style={{ display: "block" }}>{gated ? "Legal gate: stays off until Clefs approves Perplexity's terms. " : ""}{ENGINE_NOTES[k]}</span>
                </span>
              </label>
            );
          })}
          <label>
            <input type="checkbox" disabled={disabled} checked={draft.serpapi_enabled} onChange={e => set("serpapi_enabled", e.target.checked)} />
            <span><strong>Use SerpApi</strong><span className="vis-muted" style={{ display: "block" }}>
              Google Hotels prices, AI Mode and rank calibration come through SerpApi, a third-party service and a vendor risk. Off stops all three.</span></span>
          </label>
        </div>
      </Section>
      <Section title="Budget" subtitle="Month-to-date vendor spend is checked against this before each paid call.">
        {text("monthly_budget", "Monthly budget", "Typical cost is USD 14-21 a month.")}
        {text("budget_currency", "Currency", "3-letter code.", "USD")}
      </Section>
    </>
  );
}
