"use client";

/* Website form (4.3 web form): the embed code for the hotel's site, which
   sites may show it, a preview, and key rotation. The key is public by
   design (it sits in the site's HTML); rotating it is for a form copied
   onto a site that should not have it, and breaks the old embed at once. */

import { useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useHotel } from "../../../../../lib/hotel";
import { Button, Card, Field, Skeleton, Textarea, useToast } from "../../../../../ui";
import { errorText, inqPath } from "../lib";
import { useCopy, type WidgetView } from "../SetupChecklist";

/** One origin per line -> ["https://hotel.ae", ...]; returns the bad lines too. */
export function parseOrigins(text: string): { ok: string[]; bad: string[] } {
  const ok: string[] = [];
  const bad: string[] = [];
  for (const raw of text.split(/[\n,]+/).map(s => s.trim()).filter(Boolean)) {
    try {
      const u = new URL(raw);
      if ((u.protocol === "https:" || u.protocol === "http:") && u.host) {
        const o = `${u.protocol}//${u.host}`.toLowerCase();
        if (!ok.includes(o)) ok.push(o);
      } else bad.push(raw);
    } catch {
      bad.push(raw);
    }
  }
  return { ok, bad };
}

export default function WidgetTab() {
  const { slug } = useHotel();
  const toast = useToast();
  const copy = useCopy();
  const w = useApi<WidgetView>(inqPath(slug, "/widget"));
  const [origins, setOrigins] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "rotate" | null>(null);

  if (w.loading && !w.data) return <Skeleton lines={5} height={16} />;
  if (w.error || !w.data) return <Card><p className="inq-muted">{w.error?.message}</p><Button onClick={w.reload}>Try again</Button></Card>;
  const data = w.data;
  const text = origins ?? data.allowed_origins.join("\n");
  const parsed = parseOrigins(text);

  async function save() {
    setBusy("save");
    try {
      const res = await api.put<WidgetView>(inqPath(slug, "/widget"), { allowed_origins: parsed.ok });
      w.setData(res);
      setOrigins(null);
      toast.success("Allowed sites saved");
    } catch (err) {
      toast.error("Not saved", errorText(err));
    } finally {
      setBusy(null);
    }
  }

  async function rotate() {
    if (!window.confirm("Make a new key? The form stops working on every site until you paste the new code.")) return;
    setBusy("rotate");
    try {
      const res = await api.post<WidgetView>(inqPath(slug, "/widget/rotate"));
      w.setData(res);
      toast.success("New key made", "Paste the new embed code on your website.");
    } catch (err) {
      toast.error("Not rotated", errorText(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card title="Embed code" subtitle="Paste it where the form should appear. It sizes itself and works in English and Arabic.">
        <pre className="inq-code">{data.embed_code}</pre>
        <div className="inq-row" style={{ marginTop: 10 }}>
          <Button size="sm" variant="primary" onClick={() => copy(data.embed_code, "Embed code copied")}>Copy code</Button>
          <a className="cx-btn cx-btn--secondary cx-btn--sm" href={data.iframe_path} target="_blank" rel="noopener noreferrer">
            Preview the form
          </a>
        </div>
        <p className="inq-muted" style={{ margin: "10px 0 0" }}>
          Options: add data-lang=&quot;ar&quot; for Arabic first, data-theme=&quot;dark&quot; on a dark site, or
          data-accent=&quot;#RRGGBB&quot; for your brand colour.
        </p>
      </Card>
      <Card title="Sites allowed to show the form" subtitle="Leave empty to allow any site. One address per line.">
        <Field label="Website addresses" error={parsed.bad.length ? `Not a website address: ${parsed.bad.join(", ")}` : undefined}
          hint="For example https://www.yourhotel.ae (the exact address, with or without www as your site uses it).">
          <Textarea rows={4} value={text} onChange={e => setOrigins(e.target.value)} placeholder="https://www.yourhotel.ae" />
        </Field>
        <div className="inq-row" style={{ marginTop: 10 }}>
          <Button variant="primary" loading={busy === "save"} disabled={origins === null || parsed.bad.length > 0 || parsed.ok.length > 20}
            onClick={save}>Save</Button>
          {parsed.ok.length > 20 && <span className="inq-muted">At most 20 sites.</span>}
        </div>
      </Card>
      <Card title="Form key" subtitle="Only needed if the form was copied to a site that should not have it.">
        <Button variant="danger" loading={busy === "rotate"} onClick={rotate}>Make a new key</Button>
      </Card>
    </div>
  );
}
