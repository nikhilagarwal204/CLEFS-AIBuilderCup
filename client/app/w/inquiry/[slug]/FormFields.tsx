"use client";

/* The widget's fields and consent ticks. Consent lines render the server's
   wording verbatim (its language follows the form's), every box starts
   unticked, and required ones are marked in words, not colour alone. The
   honeypot is off-screen, unlabelled for assistive tech and skipped by Tab,
   so only a bot fills it. */

import { useId } from "react";
import type { WStrings } from "../i18n";
import type { ConsentLine } from "./InquiryForm";

export interface FormState {
  name: string;
  email: string;
  phone: string;
  check_in: string;
  check_out: string;
  rooms: string;
  adults: string;
  children: string;
  message: string;
  hp: string;
}

export const EMPTY_FORM: FormState = {
  name: "", email: "", phone: "", check_in: "", check_out: "", rooms: "1", adults: "2", children: "", message: "", hp: "",
};

/** Today as YYYY-MM-DD in the viewer's zone: the earliest bookable check-in
    (a date input's min is advisory; the server validates too). */
function todayKey(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function FormFields({ t, form, onChange, consents, ticks, onTick }: {
  t: WStrings;
  form: FormState;
  onChange: (f: FormState) => void;
  consents: ConsentLine[];
  ticks: Record<string, boolean>;
  onTick: (key: string, value: boolean) => void;
}) {
  const id = useId();
  const set = (k: keyof FormState) => (e: { target: { value: string } }) => onChange({ ...form, [k]: e.target.value });
  const datesBad = !!(form.check_in && form.check_out && form.check_out <= form.check_in);
  const today = todayKey();

  return (
    <>
      <div className="inq-w__grid">
        <label className="inq-w__field inq-w__full">
          <span className="inq-w__label">{t.name}</span>
          <input type="text" autoComplete="name" maxLength={200} value={form.name} onChange={set("name")} />
        </label>
        <label className="inq-w__field">
          <span className="inq-w__label">{t.email}</span>
          <input type="email" autoComplete="email" inputMode="email" maxLength={320} value={form.email} onChange={set("email")}
            aria-describedby={`${id}-contact`} />
        </label>
        <label className="inq-w__field">
          <span className="inq-w__label">{t.phone}</span>
          <input type="tel" autoComplete="tel" inputMode="tel" maxLength={40} value={form.phone} onChange={set("phone")}
            placeholder="+971" aria-describedby={`${id}-contact`} />
        </label>
        <p id={`${id}-contact`} className="inq-w__small inq-w__full">{t.contactHint}</p>
        <label className="inq-w__field">
          <span className="inq-w__label">{t.checkIn}</span>
          <input type="date" min={today} value={form.check_in} onChange={set("check_in")} />
        </label>
        <label className="inq-w__field">
          <span className="inq-w__label">{t.checkOut}</span>
          <input type="date" min={form.check_in || today} value={form.check_out} onChange={set("check_out")}
            aria-invalid={datesBad || undefined} />
        </label>
      </div>
      <div className="inq-w__grid inq-w__grid--3">
        <label className="inq-w__field">
          <span className="inq-w__label">{t.rooms}</span>
          <input type="number" min={1} max={200} inputMode="numeric" value={form.rooms} onChange={set("rooms")} />
        </label>
        <label className="inq-w__field">
          <span className="inq-w__label">{t.adults}</span>
          <input type="number" min={1} max={2000} inputMode="numeric" value={form.adults} onChange={set("adults")} />
        </label>
        <label className="inq-w__field">
          <span className="inq-w__label">{t.children}</span>
          <input type="number" min={0} max={2000} inputMode="numeric" value={form.children} onChange={set("children")} />
        </label>
      </div>
      <label className="inq-w__field">
        <span className="inq-w__label">{t.message}</span>
        <textarea maxLength={5000} dir="auto" placeholder={t.messagePh} value={form.message} onChange={set("message")} />
      </label>
      <div className="inq-w__hp" aria-hidden="true">
        <label>
          Website
          <input type="text" name="website" tabIndex={-1} autoComplete="off" value={form.hp} onChange={set("hp")} />
        </label>
      </div>
      <fieldset className="inq-w__consents">
        <legend>{t.consentsTitle}</legend>
        {consents.map(c => (
          <label key={c.key} className="inq-w__check" lang={c.language} dir={c.language === "ar" ? "rtl" : "ltr"}>
            <input type="checkbox" checked={!!ticks[c.key]} onChange={e => onTick(c.key, e.target.checked)}
              aria-required={c.required || undefined} />
            <span>
              {c.text}
              <span className={`inq-w__tag${c.required ? " inq-w__tag--req" : ""}`}>{c.required ? t.required : t.optional}</span>
            </span>
          </label>
        ))}
      </fieldset>
    </>
  );
}
