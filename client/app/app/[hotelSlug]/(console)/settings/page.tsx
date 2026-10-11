"use client";

/* Hotel profile: the core columns on `hotels` that every module reads
   (timezone, currency, locale, weekend, brief time ...). Saved with
   PATCH /hotels/{slug}/settings, sending only what changed so two people
   editing different fields do not overwrite each other.

   Country-aware: the emirate (the UAE fee stack) and the Dubai hints show
   only while the country is AE, and leaving the UAE clears the emirate. The
   lists below are suggestions (datalists); any valid ISO or IANA code saves. */

import { useMemo, useState, type FormEvent } from "react";
import { ApiError, api, hotelPath } from "../../../../lib/api";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import type { Hotel, HotelSettingsPatch } from "../../../../lib/types";
import { Badge, Button, Card, Field, Input, Select, useToast } from "../../../../ui";

const EMIRATES = [
  { value: "", label: "Not set" },
  { value: "dubai", label: "Dubai" },
  { value: "abu_dhabi", label: "Abu Dhabi" },
  { value: "sharjah", label: "Sharjah" },
  { value: "ajman", label: "Ajman" },
  { value: "rak", label: "Ras Al Khaimah" },
  { value: "fujairah", label: "Fujairah" },
  { value: "uaq", label: "Umm Al Quwain" },
];

const STATUSES = [
  { value: "active", label: "Active" },
  { value: "pilot", label: "Pilot" },
  { value: "demo", label: "Demo" },
  { value: "archived", label: "Archived" },
];

const WEEKDAYS = [
  { iso: 1, label: "Mon" }, { iso: 2, label: "Tue" }, { iso: 3, label: "Wed" }, { iso: 4, label: "Thu" },
  { iso: 5, label: "Fri" }, { iso: 6, label: "Sat" }, { iso: 7, label: "Sun" },
];

// JAPAC first, then the Gulf, India and Europe. Money is stored in each
// currency's own minor unit (JPY, KRW and VND have none; lib/format.ts and
// server core/money.py agree on every code listed here).
const TIMEZONES = [
  "Asia/Tokyo", "Asia/Seoul", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Taipei", "Asia/Bangkok", "Asia/Singapore",
  "Asia/Jakarta", "Asia/Manila", "Asia/Ho_Chi_Minh", "Asia/Kolkata", "Australia/Sydney",
  "Asia/Dubai", "Asia/Riyadh", "Asia/Qatar", "Asia/Muscat", "Europe/London", "UTC",
];
const CURRENCIES = [
  "JPY", "KRW", "CNY", "HKD", "TWD", "THB", "SGD", "IDR", "PHP", "VND", "AUD",
  "INR", "AED", "SAR", "QAR", "OMR", "USD", "EUR", "GBP",
];
const LOCALES = [
  "en-JP", "ja-JP", "ko-KR", "zh-CN", "zh-HK", "zh-TW", "th-TH", "en-SG", "id-ID", "en-PH", "vi-VN", "en-AU",
  "en-IN", "en-AE", "ar-AE", "en-GB", "en-US",
];

/** The UAE fee stack (emirate, Tourism Dirham) applies to UAE hotels only. */
const isUae = (country: string) => country.trim().toUpperCase() === "AE";

interface FormState {
  name: string;
  assistant_name: string;
  city: string;
  country: string;
  emirate: string;
  timezone: string;
  currency: string;
  locale: string;
  guest_languages: string;
  weekend_days: number[];
  brief_time_local: string;
  star_rating: string;
  rooms_count: string;
  status: string;
}

function toForm(h: Hotel | null): FormState {
  return {
    name: h?.name ?? "",
    assistant_name: h?.assistant_name ?? "",
    city: h?.city ?? "",
    country: h?.country ?? "",
    emirate: h?.emirate ?? "",
    timezone: h?.timezone ?? "",
    currency: h?.currency ?? "",
    locale: h?.locale ?? "",
    guest_languages: (h?.guest_languages ?? []).join(", "),
    weekend_days: [...(h?.weekend_days ?? [])].sort((a, b) => a - b),
    brief_time_local: (h?.brief_time_local ?? "").slice(0, 5),
    star_rating: h?.star_rating ? String(h.star_rating) : "",
    rooms_count: h?.rooms_count ? String(h.rooms_count) : "",
    status: h?.status ?? "active",
  };
}

function validTimezone(tz: string): boolean {
  try { new Intl.DateTimeFormat("en", { timeZone: tz }); return true; } catch { return false; }
}

function validLocale(l: string): boolean {
  try { return Intl.getCanonicalLocales(l).length === 1; } catch { return false; }
}

function validate(f: FormState): Record<string, string> {
  const e: Record<string, string> = {};
  if (!f.name.trim()) e.name = "The hotel needs a name.";
  if (!/^[A-Z]{2}$/.test(f.country)) e.country = "Two-letter ISO code, such as JP, IN or GB.";
  if (!validTimezone(f.timezone)) e.timezone = "Use an IANA timezone such as Asia/Tokyo or Europe/London.";
  if (!/^[A-Z]{3}$/.test(f.currency)) e.currency = "Three-letter ISO code, such as JPY, INR or USD.";
  if (!validLocale(f.locale)) e.locale = "Use a locale tag such as en-GB or ja-JP.";
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(f.brief_time_local)) e.brief_time_local = "24-hour time, e.g. 07:00.";
  if (f.rooms_count && !/^\d+$/.test(f.rooms_count)) e.rooms_count = "A whole number.";
  const langs = f.guest_languages.split(",").map(s => s.trim()).filter(Boolean);
  if (!langs.length) e.guest_languages = "At least one language, such as en.";
  else if (langs.some(l => !validLocale(l))) e.guest_languages = "Use language codes such as en, ja, ko, zh, ar.";
  return e;
}

/** Only the fields that differ from the saved hotel, typed for the API. */
function diff(f: FormState, h: Hotel | null): HotelSettingsPatch {
  const base = toForm(h);
  const out: HotelSettingsPatch = {};
  const str = (k: "name" | "city" | "country" | "timezone" | "currency" | "locale" | "status") => {
    if (f[k].trim() !== base[k]) (out as Record<string, unknown>)[k] = f[k].trim();
  };
  (["name", "city", "country", "timezone", "currency", "locale", "status"] as const).forEach(str);
  if (f.assistant_name.trim() !== base.assistant_name) out.assistant_name = f.assistant_name.trim() || null;
  // A hotel outside the UAE has no emirate: changing the country away from
  // AE clears it, so no UAE fee rule can follow the hotel abroad.
  const emirate = isUae(f.country) ? f.emirate : "";
  if (emirate !== base.emirate) out.emirate = emirate || null;
  if (f.guest_languages !== base.guest_languages) {
    out.guest_languages = f.guest_languages.split(",").map(s => s.trim()).filter(Boolean);
  }
  if (f.weekend_days.join(",") !== base.weekend_days.join(",")) out.weekend_days = f.weekend_days;
  if (f.brief_time_local !== base.brief_time_local) out.brief_time_local = f.brief_time_local;
  if (f.star_rating !== base.star_rating) out.star_rating = f.star_rating ? Number(f.star_rating) : null;
  if (f.rooms_count !== base.rooms_count) out.rooms_count = f.rooms_count ? Number(f.rooms_count) : null;
  return out;
}

export default function HotelProfilePage() {
  const hotel = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const canManage = can("core.settings.manage");

  const [form, setForm] = useState<FormState>(() => toForm(hotel.hotel));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const patch = useMemo(() => diff(form, hotel.hotel), [form, hotel.hotel]);
  const dirty = Object.keys(patch).length > 0;
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm(f => ({ ...f, [k]: v }));

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const v = validate(form);
    setErrors(v);
    if (Object.keys(v).length || !dirty) return;
    setSaving(true);
    try {
      const res = await api.patch<Partial<Hotel> | { hotel?: Partial<Hotel> } | undefined>(hotelPath(hotel.slug, "/settings"), patch);
      const saved = res && typeof res === "object" && "hotel" in res && res.hotel ? res.hotel : (res as Partial<Hotel> | undefined);
      hotel.setHotel({ ...patch, ...(saved && typeof saved === "object" ? saved : {}) } as Partial<Hotel>);
      toast.success("Hotel profile saved");
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      if (apiErr?.status === 422) setErrors(apiErr.fieldErrors);
      toast.error("Couldn't save the profile", apiErr?.message ?? String(err));
    } finally {
      setSaving(false);
    }
  };

  const dis = !canManage || saving;
  const uae = isUae(form.country);

  return (
    <form onSubmit={onSubmit} noValidate>
      <div className="cx-stack">
        {!canManage && (
          <Card><div className="cx-row"><Badge tone="info">Read only</Badge><span className="cx-muted" style={{ fontSize: "0.86rem" }}>Only owners and GMs can change the hotel profile.</span></div></Card>
        )}

        <Card title="Identity" subtitle="How the hotel appears to staff and guests.">
          <div className="cx-form-grid cx-form-grid--2">
            <Field label="Hotel name" required error={errors.name}>
              <Input value={form.name} disabled={dis} invalid={!!errors.name} onChange={e => set("name", e.target.value)} />
            </Field>
            <Field label="Assistant name" hint="What the AI concierge calls itself with guests.">
              <Input value={form.assistant_name} disabled={dis} placeholder="Clefs" onChange={e => set("assistant_name", e.target.value)} />
            </Field>
            <Field label="Star rating" hint={uae ? "Sets the Tourism Dirham category in Dubai." : undefined}>
              <Select value={form.star_rating} disabled={dis} onChange={e => set("star_rating", e.target.value)}
                      placeholder="Not rated" options={[1, 2, 3, 4, 5].map(n => ({ value: String(n), label: `${n} star` }))} />
            </Field>
            <Field label="Rooms" error={errors.rooms_count}>
              <Input inputMode="numeric" value={form.rooms_count} disabled={dis} invalid={!!errors.rooms_count}
                     onChange={e => set("rooms_count", e.target.value.replace(/[^\d]/g, ""))} />
            </Field>
            <Field label="Status" hint="Pilot and demo hotels are excluded from benchmarks.">
              <Select value={form.status} disabled={dis} onChange={e => set("status", e.target.value)} options={STATUSES} />
            </Field>
          </div>
        </Card>

        <Card title="Location and money" subtitle="Drives tax rules, currency and every date the console shows.">
          <div className="cx-form-grid cx-form-grid--2">
            <Field label="City">
              <Input value={form.city} disabled={dis} onChange={e => set("city", e.target.value)} />
            </Field>
            <Field label="Country" required error={errors.country}>
              <Input value={form.country} maxLength={2} disabled={dis} invalid={!!errors.country}
                     onChange={e => set("country", e.target.value.toUpperCase())} />
            </Field>
            {uae && (
              <Field label="Emirate" hint="Selects the fee stack (Dubai TD, Abu Dhabi tourism fee ...).">
                <Select value={form.emirate} disabled={dis} onChange={e => set("emirate", e.target.value)} options={EMIRATES} />
              </Field>
            )}
            <Field label="Currency" required error={errors.currency}>
              <Input value={form.currency} maxLength={3} list="cx-currencies" disabled={dis} invalid={!!errors.currency}
                     onChange={e => set("currency", e.target.value.toUpperCase())} />
            </Field>
            <Field label="Timezone" required error={errors.timezone} hint="Business days and the brief follow this.">
              <Input value={form.timezone} list="cx-timezones" disabled={dis} invalid={!!errors.timezone}
                     onChange={e => set("timezone", e.target.value)} />
            </Field>
            <Field label="Console locale" required error={errors.locale} hint="Number and date formatting.">
              <Input value={form.locale} list="cx-locales" disabled={dis} invalid={!!errors.locale}
                     onChange={e => set("locale", e.target.value)} />
            </Field>
          </div>
          <datalist id="cx-currencies">{CURRENCIES.map(c => <option key={c} value={c} />)}</datalist>
          <datalist id="cx-timezones">{TIMEZONES.map(c => <option key={c} value={c} />)}</datalist>
          <datalist id="cx-locales">{LOCALES.map(c => <option key={c} value={c} />)}</datalist>
        </Card>

        <Card title="Week and brief" subtitle="Weekend days shade the demand calendar; the brief arrives at this local time.">
          <div className="cx-form-grid cx-form-grid--2">
            <Field label="Weekend days" hint={uae ? "UAE hotels: Saturday and Sunday." : undefined} htmlFor="cx-weekend-days">
              {/* htmlFor keeps this a <div>: a wrapping <label> would forward clicks to the first button. */}
              <div id="cx-weekend-days" className="cx-row" role="group" aria-label="Weekend days" style={{ gap: 6 }}>
                {WEEKDAYS.map(d => {
                  const on = form.weekend_days.includes(d.iso);
                  return (
                    <Button key={d.iso} size="sm" variant={on ? "primary" : "secondary"} disabled={dis} aria-pressed={on}
                            onClick={() => set("weekend_days", on
                              ? form.weekend_days.filter(x => x !== d.iso)
                              : [...form.weekend_days, d.iso].sort((a, b) => a - b))}>
                      {d.label}
                    </Button>
                  );
                })}
              </div>
            </Field>
            <Field label="Brief time" required error={errors.brief_time_local}>
              <Input type="time" value={form.brief_time_local} disabled={dis} invalid={!!errors.brief_time_local}
                     onChange={e => set("brief_time_local", e.target.value)} />
            </Field>
            <Field label="Guest languages" required error={errors.guest_languages}
                   hint="Comma separated, such as en, ja, ko, zh. The concierge offers these.">
              <Input value={form.guest_languages} disabled={dis} invalid={!!errors.guest_languages}
                     onChange={e => set("guest_languages", e.target.value)} />
            </Field>
          </div>
        </Card>

        {canManage && (
          <div className="cx-form-actions" style={{ marginTop: 0 }}>
            <Button variant="ghost" disabled={!dirty || saving} onClick={() => { setForm(toForm(hotel.hotel)); setErrors({}); }}>
              Discard changes
            </Button>
            <Button type="submit" variant="primary" loading={saving} disabled={!dirty}>Save profile</Button>
          </div>
        )}
      </div>
    </form>
  );
}
