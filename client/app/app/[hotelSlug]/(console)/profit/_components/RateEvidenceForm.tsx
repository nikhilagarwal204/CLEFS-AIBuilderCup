"use client";

/* RateEvidenceForm (02 spec 3.10 parity/): one price seen on one channel,
   typed by a person with the conditions that make it comparable (stay
   date, nights, adults, room, device, login state) and, ideally, a
   screenshot: a breach backed by a screenshot is trusted more (L7 0.75
   instead of 0.6) and goes into the dispute or rate-parity complaint as
   proof. The screenshot is stored first through the Files contract. */

import { useState } from "react";
import { api } from "../../../../../lib/api";
import { currencyDigits, useFormat } from "../../../../../lib/format";
import { Button, Field, FileDrop, Input, ProgressBar, Select, useToast, useUpload } from "../../../../../ui";
import { majorToMinor, minorToDecimal, pgApi } from "../lib";
import type { RateEvidence } from "../types";
import { errorText } from "./bits";

const CHANNELS = [
  { value: "DIRECT_WEB", label: "Your website" },
  { value: "BOOKING", label: "Booking.com" },
  { value: "AGODA", label: "Agoda" },
  { value: "EXPEDIA", label: "Expedia" },
  { value: "GOOGLE_HOTELS", label: "Google Hotels" },
] as const;

export function RateEvidenceForm({ slug, onSaved }: { slug: string; onSaved: (r: RateEvidence) => void }) {
  const fmt = useFormat();
  const toast = useToast();
  const upload = useUpload(slug, "profit.rate_evidence");
  const [channel, setChannel] = useState<string>("BOOKING");
  const [stayDate, setStayDate] = useState("");
  const [price, setPrice] = useState("");
  const [los, setLos] = useState("1");
  const [adults, setAdults] = useState("2");
  const [room, setRoom] = useState("");
  const [plan, setPlan] = useState("");
  const [device, setDevice] = useState<"DESKTOP" | "MOBILE">("DESKTOP");
  const [login, setLogin] = useState<"LOGGED_OUT" | "LOGGED_IN" | "MEMBER">("LOGGED_OUT");
  const [taxes, setTaxes] = useState<"" | "yes" | "no">("");
  const [country, setCountry] = useState("");
  const [url, setUrl] = useState("");
  const [shot, setShot] = useState<{ id: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const digits = currencyDigits(fmt.currency);
  const priceMinor = majorToMinor(price, digits);
  const losN = Number(los);
  const adultsN = Number(adults);
  const urlOk = !url.trim() || /^https?:\/\//i.test(url.trim());
  const ok = Boolean(stayDate) && priceMinor !== null && priceMinor > 0 && Number.isInteger(losN) && losN >= 1 && losN <= 60
    && Number.isInteger(adultsN) && adultsN >= 1 && adultsN <= 10 && urlOk && !upload.uploading;

  async function onShot(files: File[]) {
    const f = files[0];
    if (!f) return;
    const ref = await upload.upload(f);
    if (ref) setShot({ id: ref.id, name: ref.filename });
  }

  async function save() {
    if (!ok || priceMinor === null) return;
    setBusy(true);
    setError(null);
    try {
      const row = await api.post<RateEvidence>(pgApi(slug, "/rate-evidence"), {
        channel, stay_date: stayDate, price: minorToDecimal(priceMinor, digits), currency: fmt.currency,
        los: losN, adults: adultsN, room_label: room.trim() || undefined, plan_label: plan.trim() || undefined,
        device, login_state: login, taxes_included: taxes === "" ? undefined : taxes === "yes",
        booker_country: country.trim().toUpperCase() || undefined, search_url: url.trim() || undefined,
        screenshot_file_id: shot?.id,
      });
      toast({ tone: "ok", title: "Price saved", body: "The parity check compares it on its next run." });
      onSaved(row);
      setPrice("");
      setShot(null);
      upload.reset();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="cx-stack">
      <div className="pg-toolbar">
        <Field label="Channel">
          <Select value={channel} onChange={e => setChannel(e.target.value)}>
            {CHANNELS.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
          </Select>
        </Field>
        <Field label="Stay date" required><Input type="date" value={stayDate} onChange={e => setStayDate(e.target.value)} /></Field>
        <Field label={`Price (${fmt.currency})`} required
               error={price && (priceMinor === null || priceMinor <= 0) ? "An amount like 450.00" : undefined}>
          <Input inputMode="decimal" value={price} onChange={e => setPrice(e.target.value)} />
        </Field>
        <Field label="Taxes in the price">
          <Select value={taxes} onChange={e => setTaxes(e.target.value as "" | "yes" | "no")}>
            <option value="">Not sure</option>
            <option value="yes">Included</option>
            <option value="no">Excluded</option>
          </Select>
        </Field>
      </div>
      <div className="pg-toolbar">
        <Field label="Nights"><Input type="number" min={1} max={60} value={los} onChange={e => setLos(e.target.value)} /></Field>
        <Field label="Adults"><Input type="number" min={1} max={10} value={adults} onChange={e => setAdults(e.target.value)} /></Field>
        <Field label="Room"><Input value={room} maxLength={200} placeholder="e.g. Deluxe King" onChange={e => setRoom(e.target.value)} /></Field>
        <Field label="Rate plan"><Input value={plan} maxLength={200} placeholder="e.g. Room only, flexible" onChange={e => setPlan(e.target.value)} /></Field>
      </div>
      <div className="pg-toolbar">
        <Field label="Device">
          <Select value={device} onChange={e => setDevice(e.target.value as "DESKTOP" | "MOBILE")}>
            <option value="DESKTOP">Desktop</option>
            <option value="MOBILE">Mobile</option>
          </Select>
        </Field>
        <Field label="Signed in?" hint="Member prices are compared only with member prices.">
          <Select value={login} onChange={e => setLogin(e.target.value as "LOGGED_OUT" | "LOGGED_IN" | "MEMBER")}>
            <option value="LOGGED_OUT">Signed out</option>
            <option value="LOGGED_IN">Signed in</option>
            <option value="MEMBER">Member or Genius price</option>
          </Select>
        </Field>
        <Field label="Searched from" hint="Two-letter country, optional.">
          <Input value={country} maxLength={2} onChange={e => setCountry(e.target.value)} />
        </Field>
      </div>
      <Field label="Search link" hint="Optional: the results page URL." error={urlOk ? undefined : "Starts with https://"}>
        <Input type="url" value={url} maxLength={2000} onChange={e => setUrl(e.target.value)} />
      </Field>
      <FileDrop accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp,.pdf" disabled={upload.uploading || busy} onFiles={onShot}
                onReject={r => setError(r)}
                title={shot ? `Screenshot: ${shot.name}` : upload.uploading ? "Uploading..." : "Add a screenshot (recommended)"}
                hint="A screenshot of the price with the date visible makes the evidence stronger.">
        {upload.uploading && <div style={{ width: "100%", marginTop: 10 }}><ProgressBar value={upload.progress} /></div>}
      </FileDrop>
      {(error || upload.error) && <div className="pg-error" role="alert">{error ?? upload.error?.message}</div>}
      <div className="pg-row" style={{ justifyContent: "flex-end" }}>
        {shot && <Button variant="ghost" size="sm" onClick={() => { setShot(null); upload.reset(); }}>Remove screenshot</Button>}
        <Button variant="primary" disabled={!ok} loading={busy} onClick={save}>Save price</Button>
      </div>
    </div>
  );
}
