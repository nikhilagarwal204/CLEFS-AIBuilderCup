"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Journey settings. The server (GuestsSettings) is the validator of record:
   - the marketing window can only NARROW 09:00-18:00, never widen it;
   - review links must be the official Google or Tripadvisor hosts, so the
     feedback page can never point at a rating-gated funnel;
   - two switches wait on legal review and are shown read-only here.
   The PUT is partial: only the keys this form owns are sent.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, useApi } from "../../../../../../lib/api";
import { useFormat } from "../../../../../../lib/format";
import { useHotel } from "../../../../../../lib/hotel";
import { useSession } from "../../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Field, Input, Skeleton, Textarea, useToast } from "../../../../../../ui";
import { errText, jrnApi, PERM, STEPS, stepLabel } from "../../lib";
import type { JourneySettings, StepKey } from "../../types";

type Draft = Omit<JourneySettings, "wa_billing_attested_at">;

export default function JourneySettingsPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const res = useApi<JourneySettings>(jrnApi(slug, "/settings"), [slug]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const manage = can(PERM.jManage);

  if (res.error) return <EmptyState title="Could not load settings" body={res.error.message} />;
  if (!res.data) return <Skeleton lines={8} />;
  // Unedited, the form shows the server's values; the first edit forks a
  // local draft (no effect copying props into state).
  const draftOrSaved: Draft = draft ?? res.data;
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft({ ...draftOrSaved, [k]: v });
  const toggleStep = (s: StepKey) =>
    set("enabled_steps", draftOrSaved.enabled_steps.includes(s) ? draftOrSaved.enabled_steps.filter(x => x !== s) : [...draftOrSaved.enabled_steps, s]);

  async function save(extra?: Record<string, unknown>) {
    setSaving(true);
    try {
      // The counsel-gated switches are never sent from here.
      const { j7_for_ota_provided_contacts: _a, ai_on_unconsented_inbound: _b, ...body } = draftOrSaved;
      void _a; void _b;
      delete (body as Partial<JourneySettings>).wa_billing_attested_at;
      const saved = await api.put<JourneySettings>(jrnApi(slug, "/settings"), extra ?? {
        ...body,
        review_links: {
          google_url: body.review_links.google_url?.trim() || null,
          tripadvisor_url: body.review_links.tripadvisor_url?.trim() || null,
        },
      });
      res.setData(saved);
      setDraft(null);
      toast.success("Settings saved");
    } catch (err) {
      toast.error("Could not save", errText(err));
    } finally {
      setSaving(false);
    }
  }

  const ro = !manage;
  return (
    <form className="cx-stack" onSubmit={e => { e.preventDefault(); save(); }}>
      <Card title="Review links" subtitle="Shown the same way to every guest on the feedback page, before and after the survey.">
        <div className="gst-form-grid">
          <Field label="Google review link" htmlFor="gst-js-g" hint="Your Business Profile 'Get more reviews' link (g.page) or a search.google.com write-review link">
            <Input id="gst-js-g" type="url" disabled={ro} value={draftOrSaved.review_links.google_url ?? ""}
              onChange={e => set("review_links", { ...draftOrSaved.review_links, google_url: e.target.value })} />
          </Field>
          <Field label="Tripadvisor review link" htmlFor="gst-js-t" hint="A link on tripadvisor.com (or a local Tripadvisor domain)">
            <Input id="gst-js-t" type="url" disabled={ro} value={draftOrSaved.review_links.tripadvisor_url ?? ""}
              onChange={e => set("review_links", { ...draftOrSaved.review_links, tripadvisor_url: e.target.value })} />
          </Field>
        </div>
      </Card>

      <Card title="Steps" subtitle="A step that is off plans nothing. The dial for each step decides whether it observes, proposes or sends.">
        <div className="gst-checks">
          {STEPS.map(s => (
            <label key={s} className="gst-check">
              <input type="checkbox" disabled={ro} checked={draftOrSaved.enabled_steps.includes(s)} onChange={() => toggleStep(s)} />
              <span>{stepLabel(s)}</span>
            </label>
          ))}
        </div>
      </Card>
      <Card title="Timing" subtitle={`Hotel time (${fmt.tz}). Windows are half-open: a window ending 21:00 sends up to 20:59.`}>
        <div className="gst-form-grid">
          <Field label="Service messages from / to" htmlFor="gst-js-u0" hint="Confirmations, check-in details, feedback">
            <div className="cx-row">
              <Input id="gst-js-u0" type="time" disabled={ro} value={draftOrSaved.utility_window.start}
                onChange={e => set("utility_window", { ...draftOrSaved.utility_window, start: e.target.value })} />
              <Input aria-label="Service messages until" type="time" disabled={ro} value={draftOrSaved.utility_window.end}
                onChange={e => set("utility_window", { ...draftOrSaved.utility_window, end: e.target.value })} />
            </div>
          </Field>
          <Field label="Offers from / to" htmlFor="gst-js-m0" hint="Within 09:00-18:00 only; you can narrow it, not widen it">
            <div className="cx-row">
              <Input id="gst-js-m0" type="time" min="09:00" max="18:00" disabled={ro} value={draftOrSaved.marketing_window.start}
                onChange={e => set("marketing_window", { ...draftOrSaved.marketing_window, start: e.target.value })} />
              <Input aria-label="Offers until" type="time" min="09:00" max="18:00" disabled={ro} value={draftOrSaved.marketing_window.end}
                onChange={e => set("marketing_window", { ...draftOrSaved.marketing_window, end: e.target.value })} />
            </div>
          </Field>
          <Field label="Standard check-in" htmlFor="gst-js-ci">
            <Input id="gst-js-ci" type="time" disabled={ro} value={draftOrSaved.standard_checkin_time} onChange={e => set("standard_checkin_time", e.target.value)} />
          </Field>
          <Field label="Standard check-out" htmlFor="gst-js-co">
            <Input id="gst-js-co" type="time" disabled={ro} value={draftOrSaved.standard_checkout_time} onChange={e => set("standard_checkout_time", e.target.value)} />
          </Field>
          <Field label="Planning info: minimum days before arrival" htmlFor="gst-js-j2">
            <Input id="gst-js-j2" type="number" min={0} max={60} disabled={ro} value={draftOrSaved.j2_min_lead_days} onChange={e => set("j2_min_lead_days", Number(e.target.value))} />
          </Field>
          <Field label="Day-1 check: hours after check-in" htmlFor="gst-js-j5">
            <Input id="gst-js-j5" type="number" min={1} max={72} disabled={ro} value={draftOrSaved.j5_offset_h} onChange={e => set("j5_offset_h", Number(e.target.value))} />
          </Field>
          <Field label="Feedback: days after check-out" htmlFor="gst-js-j7">
            <Input id="gst-js-j7" type="number" min={0} max={14} disabled={ro} value={draftOrSaved.j7_offset_days} onChange={e => set("j7_offset_days", Number(e.target.value))} />
          </Field>
          <Field label="Rebooking offers: days after check-out" htmlFor="gst-js-j8" hint="Comma separated, 7 to 365 days, up to 4 (default 30, 60). Offers still go out at most 2 per 30 days.">
            <Input id="gst-js-j8" disabled={ro} value={draftOrSaved.j8_offsets.join(", ")}
              onChange={e => set("j8_offsets", e.target.value.split(",").map(v => Number(v.trim())).filter(n => Number.isFinite(n) && n > 0))} />
          </Field>
        </div>
      </Card>

      <Card title="Message content" subtitle="Used when the concierge knowledge base has no approved answer.">
        <div className="gst-form-grid">
          <Field label="Booking engine link" htmlFor="gst-js-be" hint="Where the rebooking offer sends guests (your website if empty)" className="gst-form-grid__wide">
            <Input id="gst-js-be" type="url" disabled={ro} value={draftOrSaved.booking_engine_url ?? ""} onChange={e => set("booking_engine_url", e.target.value || null)} />
          </Field>
          <Field label="Planning info" htmlFor="gst-js-pi" className="gst-form-grid__wide">
            <Textarea id="gst-js-pi" rows={2} maxLength={200} disabled={ro} value={draftOrSaved.planning_info ?? ""} onChange={e => set("planning_info", e.target.value || null)} />
          </Field>
          <Field label="What to bring at check-in" htmlFor="gst-js-cb">
            <Textarea id="gst-js-cb" rows={2} maxLength={200} disabled={ro} value={draftOrSaved.checkin_bring ?? ""} onChange={e => set("checkin_bring", e.target.value || null)} />
          </Field>
          <Field label="Directions" htmlFor="gst-js-dir">
            <Textarea id="gst-js-dir" rows={2} maxLength={200} disabled={ro} value={draftOrSaved.directions ?? ""} onChange={e => set("directions", e.target.value || null)} />
          </Field>
        </div>
      </Card>

      <Card title="WhatsApp billing and legal holds">
        <dl className="gst-kv">
          <dt>WhatsApp payment method</dt>
          <dd className="cx-row">
            {res.data.wa_billing_attested_at
              ? <Badge tone="ok">Confirmed {fmt.date(res.data.wa_billing_attested_at)}</Badge>
              : <Badge tone="warn">Not confirmed</Badge>}
            {manage && (
              <Button type="button" size="sm" variant="ghost" loading={saving}
                onClick={() => save({ wa_billing_attested: !res.data?.wa_billing_attested_at })}>
                {res.data.wa_billing_attested_at ? "Withdraw confirmation" : "I confirm a payment method is on file with Meta"}
              </Button>
            )}
          </dd>
          <dt>Feedback to OTA-provided contacts</dt>
          <dd><Badge>{draftOrSaved.j7_for_ota_provided_contacts ? "On" : "Off"}</Badge> <span className="gst-small gst-muted">Held until legal review</span></dd>
          <dt>AI replies without transfer consent</dt>
          <dd><Badge>{draftOrSaved.ai_on_unconsented_inbound ? "On" : "Off"}</Badge> <span className="gst-small gst-muted">Held until legal review</span></dd>
        </dl>
      </Card>

      {manage && (
        <div className="cx-row" style={{ justifyContent: "flex-end" }}>
          {draft && <Button type="button" variant="ghost" onClick={() => setDraft(null)}>Discard changes</Button>}
          <Button type="submit" loading={saving} disabled={!draft}>Save settings</Button>
        </div>
      )}
    </form>
  );
}
