"use client";

/* Reply and quote rules. Two stores on the server:
   - GET|PUT /settings (InquirySettings): reply deadlines per segment,
     business hours, the automatic acknowledgement, the AI switch for
     inquiries that did not give express consent, web form limits;
   - GET|PUT /quote-settings (QuoteSettings): group and long-stay policy,
     deposit milestones, cancellation and inclusions.
   Until the settings model carries `quotes`, a PUT /settings drops the
   stored quote settings (lane README), so saving the reply rules re-puts
   the loaded quote settings straight after. */

import { useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, Field, Input, Skeleton, useToast } from "../../../../../ui";
import { errorText, inqPath } from "../lib";
import QuoteRules, { type QuoteSettings } from "./QuoteRules";

export interface InquirySettings {
  sla: Record<string, number>;
  business_open: string;
  business_close: string;
  ai_on_unconsented_inbound: boolean;
  ack_enabled: boolean;
  privacy_url: string | null;
  web_form_per_ip_hour: number;
  web_form_per_hotel_day: number;
  web_form_min_fill_s: number;
}

const SLA_ROWS: [string, string][] = [
  ["fit", "Leisure (FIT)"], ["unknown", "Not sure yet"], ["corporate", "Corporate"], ["long_stay", "Long stay"],
  ["group", "Group"], ["event", "Event"], ["rfp", "Formal RFP"],
];

export default function RulesTab({ canManage }: { canManage: boolean }) {
  const { slug } = useHotel();
  const toast = useToast();
  const rules = useApi<InquirySettings>(inqPath(slug, "/settings"));
  const quotes = useApi<QuoteSettings>(inqPath(slug, "/quote-settings"));
  const [draft, setDraft] = useState<InquirySettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if ((rules.loading && !rules.data) || (quotes.loading && !quotes.data)) return <Skeleton lines={8} height={16} />;
  if (rules.error || !rules.data) return <Card><p className="inq-muted">{rules.error?.message}</p><Button onClick={rules.reload}>Try again</Button></Card>;
  const d = draft ?? rules.data;
  const set = <K extends keyof InquirySettings>(k: K, v: InquirySettings[K]) => setDraft({ ...d, [k]: v });
  const hhmm = (v: string) => /^([01]\d|2[0-4]):[0-5]\d$/.test(v);
  const ready = draft !== null && hhmm(d.business_open) && hhmm(d.business_close)
    && Object.values(d.sla).every(m => Number.isInteger(m) && m >= 1 && m <= 10080);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const out = await api.put<InquirySettings>(inqPath(slug, "/settings"), d);
      if (quotes.data) await api.put(inqPath(slug, "/quote-settings"), quotes.data);
      rules.setData(out);
      setDraft(null);
      toast.success("Reply rules saved", "Open inquiries keep their deadline; new ones use these.");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Reply deadlines" subtitle="First-reply time per kind of request, counted in business hours only.">
        <div className="inq-form">
          {SLA_ROWS.map(([k, label]) => (
            <Field key={k} label={`${label} (minutes)`}>
              <Input type="number" min={1} max={10080} disabled={!canManage} value={String(d.sla[k] ?? "")}
                onChange={e => set("sla", { ...d.sla, [k]: Number(e.target.value) })} />
            </Field>
          ))}
          <Field label="Business hours open"><Input type="time" disabled={!canManage} value={d.business_open} onChange={e => set("business_open", e.target.value)} /></Field>
          <Field label="Business hours close"><Input type="time" disabled={!canManage} value={d.business_close} onChange={e => set("business_close", e.target.value)} /></Field>
        </div>
      </Card>
      <Card title="Automatic handling">
        <div style={{ display: "grid", gap: 12 }}>
          <label className="inq-row">
            <input type="checkbox" disabled={!canManage} checked={d.ack_enabled} onChange={e => set("ack_enabled", e.target.checked)} />
            Send an instant acknowledgement with the privacy notice (does not count as the first reply)
          </label>
          <label className="inq-row" style={{ alignItems: "flex-start" }}>
            <input type="checkbox" disabled={!canManage} checked={d.ai_on_unconsented_inbound}
              onChange={e => set("ai_on_unconsented_inbound", e.target.checked)} style={{ marginTop: 3 }} />
            <span>
              Let AI read WhatsApp, email, lead-ad and phone inquiries
              <span className="inq-sub">Off by default until your legal adviser signs off: these guests did not tick an express consent box. Website form inquiries are always read by AI; the others use the date and guest-count parser.</span>
            </span>
          </label>
          <Field label="Privacy notice link" hint="Sent in the acknowledgement. Leave empty to use the hotel's default page.">
            <Input type="url" disabled={!canManage} value={d.privacy_url ?? ""} onChange={e => set("privacy_url", e.target.value.trim() || null)} />
          </Field>
        </div>
      </Card>
      <Card title="Website form limits" subtitle="Spam protection. The defaults suit almost every hotel.">
        <div className="inq-form">
          <Field label="Per visitor per hour"><Input type="number" min={1} disabled={!canManage} value={String(d.web_form_per_ip_hour)} onChange={e => set("web_form_per_ip_hour", Number(e.target.value))} /></Field>
          <Field label="Per day for the hotel"><Input type="number" min={1} disabled={!canManage} value={String(d.web_form_per_hotel_day)} onChange={e => set("web_form_per_hotel_day", Number(e.target.value))} /></Field>
        </div>
      </Card>
      {canManage && (
        <div className="inq-row">
          <Button variant="primary" loading={busy} disabled={!ready} onClick={save}>Save reply rules</Button>
          {draft && <Button variant="ghost" disabled={busy} onClick={() => setDraft(null)}>Discard</Button>}
          {error && <span className="inq-note inq-note--bad" role="alert">{error}</span>}
        </div>
      )}
      {quotes.data && <QuoteRules initial={quotes.data} canManage={canManage} onSaved={v => quotes.setData(v)} />}
      {quotes.error && <p className="inq-note inq-note--bad">{quotes.error.message}</p>}
    </div>
  );
}
