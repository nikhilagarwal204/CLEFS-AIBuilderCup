"use client";

/* Log a phone or walk-in inquiry (POST /inquiries, source phone_desk). The
   note is required: it is the request itself and what extraction reads if
   the fields are left empty. A number or email is optional (a caller may
   not give one) but without either nobody can send a quote, so the form
   says so. On success it opens the new inquiry's drawer. If the same guest
   already has an open inquiry the server appends to it instead. */

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import { Button, Card, EmptyState, Field, Input, PageHeader, Select, Textarea, useToast } from "../../../../../ui";
import { errorText, inqPath } from "../lib";
import { inqStyles } from "../styles";
import type { Inquiry } from "../types";

interface Draft {
  text: string; name: string; phone: string; email: string; company: string; language: string;
  check_in: string; check_out: string; rooms: string; adults: string; children: string;
}

const EMPTY: Draft = {
  text: "", name: "", phone: "", email: "", company: "", language: "", check_in: "", check_out: "",
  rooms: "", adults: "", children: "",
};

const num = (v: string) => (v.trim() === "" ? undefined : Number(v));
const str = (v: string) => (v.trim() === "" ? undefined : v.trim());

/** room: the caller's room when the phone desk sent them here (in-house
 *  guest). The inquiry model has no room field, so it seeds the note, which
 *  is what extraction reads and what the sales team sees first. */
export default function NewInquiry({ room }: { room?: string } = {}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const router = useRouter();
  const toast = useToast();
  const [d, setD] = useState<Draft>(() => (room ? { ...EMPTY, text: `In-house guest, room ${room}: ` } : EMPTY));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setD(prev => ({ ...prev, [k]: e.target.value }));
  const datesBad = !!(d.check_in && d.check_out && d.check_out <= d.check_in);
  const back = `/app/${slug}/inquiries`;

  if (!can("inquiries.respond")) {
    return <Card><EmptyState title="No access" body="Your role cannot log inquiries." /></Card>;
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!d.text.trim() || datesBad) return;
    setBusy(true);
    setError(null);
    try {
      const row = await api.post<Inquiry & { outcome?: string }>(inqPath(slug), {
        source: "phone_desk", text: d.text.trim(), name: str(d.name), phone: str(d.phone), email: str(d.email),
        company: str(d.company), language: str(d.language), check_in: str(d.check_in), check_out: str(d.check_out),
        rooms: num(d.rooms), adults: num(d.adults), children: num(d.children),
      });
      toast.success(row.outcome === "appended" ? `Added to ${row.ref.toUpperCase()}` : `Logged as ${row.ref.toUpperCase()}`,
        row.outcome === "appended" ? "This guest already had an open inquiry." : undefined);
      router.push(`${back}?id=${row.id}`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <div>
      <style dangerouslySetInnerHTML={{ __html: inqStyles }} />
      <PageHeader eyebrow="Inquiries" title="Log a phone inquiry"
        subtitle="For a request taken on the phone or at the desk. It joins the same list, quotes and verification as every other channel."
        actions={<Button href={back}>Back to inquiries</Button>} />
      <Card>
        <form className="inq-form" onSubmit={submit}>
          <Field label="What did they ask for?" required className="inq-full"
            hint="Write it as the guest said it: dates, rooms, guests, budget. Clefs reads the details from this note.">
            <Textarea rows={4} maxLength={5000} dir="auto" value={d.text} onChange={set("text")} required />
          </Field>
          <Field label="Guest name"><Input value={d.name} maxLength={200} onChange={set("name")} autoComplete="off" /></Field>
          <Field label="Company"><Input value={d.company} maxLength={200} onChange={set("company")} /></Field>
          <Field label="Mobile" hint="With country code, for example +971 50 123 4567">
            <Input type="tel" value={d.phone} maxLength={40} onChange={set("phone")} autoComplete="off" />
          </Field>
          <Field label="Email"><Input type="email" value={d.email} maxLength={320} onChange={set("email")} autoComplete="off" /></Field>
          <Field label="Check-in"><Input type="date" value={d.check_in} onChange={set("check_in")} /></Field>
          <Field label="Check-out" error={datesBad ? "Check-out must be after check-in" : undefined}>
            <Input type="date" value={d.check_out} min={d.check_in || undefined} invalid={datesBad} onChange={set("check_out")} />
          </Field>
          <Field label="Rooms"><Input type="number" min={1} max={200} value={d.rooms} onChange={set("rooms")} /></Field>
          <Field label="Adults"><Input type="number" min={1} max={2000} value={d.adults} onChange={set("adults")} /></Field>
          <Field label="Children"><Input type="number" min={0} max={2000} value={d.children} onChange={set("children")} /></Field>
          <Field label="Language">
            <Select value={d.language} onChange={set("language")}>
              <option value="">Not sure</option>
              <option value="en">English</option>
              <option value="ar">Arabic</option>
            </Select>
          </Field>
          {!d.phone.trim() && !d.email.trim() && (
            <p className="inq-note inq-note--warn inq-full">
              Without a mobile number or email, Clefs cannot send this guest a quote or a payment link.
            </p>
          )}
          {error && <p className="inq-note inq-note--bad inq-full" role="alert">{error}</p>}
          <div className="inq-row inq-full">
            <Button type="submit" variant="primary" loading={busy} disabled={!d.text.trim() || datesBad}>Log inquiry</Button>
            <Button href={back} variant="ghost">Cancel</Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
