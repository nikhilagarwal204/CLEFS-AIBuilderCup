"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Journey templates (04 spec 3.6): the owner approves every version before
   it can send. EN and AR sit side by side (AR rendered right-to-left) so
   the owner checks that both say the same thing. Each shows the category
   the step needs next to the one Meta assigned (a utility template Meta
   re-labelled MARKETING then follows the marketing rules), the lint result
   (review words in an offer, em dashes, variables), and Meta's status.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { api, useApi } from "../../../../../../lib/api";
import { useFormat } from "../../../../../../lib/format";
import { useHotel } from "../../../../../../lib/hotel";
import { useSession } from "../../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Segmented, Skeleton, useToast } from "../../../../../../ui";
import { errText, humanize, jrnApi, PERM, SEND_STEPS, statusTone, stepLabel } from "../../lib";
import type { Lang, Template } from "../../types";

type Channel = "whatsapp" | "email";

/** Newest version per step, channel and language. */
function latest(list: Template[]): Map<string, Template> {
  const m = new Map<string, Template>();
  for (const t of list) {
    const k = `${t.step}|${t.channel}|${t.language}`;
    const cur = m.get(k);
    if (!cur || t.version > cur.version) m.set(k, t);
  }
  return m;
}

export default function TemplatesPage() {
  const { slug } = useHotel();
  const { can } = useSession();
  const toast = useToast();
  const res = useApi<Template[]>(jrnApi(slug, "/templates"), [slug]);
  const [channel, setChannel] = useState<Channel>("whatsapp");
  const [busy, setBusy] = useState<number | null>(null);
  const byKey = useMemo(() => latest(res.data ?? []), [res.data]);
  const manage = can(PERM.jManage);

  async function act(t: Template, what: "approve" | "revoke") {
    if (what === "revoke" && !window.confirm("Withdraw approval? This step stops sending in this language until you approve again.")) return;
    setBusy(t.id);
    try {
      const out = await api.post<Template & { submission?: { status?: string; error?: string } | null }>(
        jrnApi(slug, `/templates/${t.id}/${what}`));
      if (what === "approve") {
        toast.success("Template approved", out.channel === "whatsapp"
          ? `Submitted to Meta${out.submission?.status ? ` (${humanize(out.submission.status)})` : ""}. It sends once Meta approves it too.`
          : "It can send now, subject to the dial for its step.");
      } else {
        toast.info("Approval withdrawn");
      }
      res.reload();
    } catch (err) {
      toast.error(what === "approve" ? "Could not approve" : "Could not withdraw", errText(err));
    } finally {
      setBusy(null);
    }
  }

  if (res.error) return <EmptyState title="Could not load templates" body={res.error.message} />;

  return (
    <Card
      title="Message templates"
      subtitle="Approve each language separately. WhatsApp templates also need Meta's approval before they send."
      actions={<Segmented<Channel> ariaLabel="Channel" size="sm" value={channel} onChange={setChannel}
        options={[{ value: "whatsapp", label: "WhatsApp" }, { value: "email", label: "Email" }]} />}
    >
      {res.loading && !res.data ? <Skeleton lines={6} height={60} /> : (
        <div className="cx-stack">
          {SEND_STEPS.map(step => (
            <section key={step} aria-label={stepLabel(step)}>
              <div className="gst-section-title">{stepLabel(step)}</div>
              <div className="gst-tpl-pair">
                {(["en", "ar"] as Lang[]).map(lang => {
                  const t = byKey.get(`${step}|${channel}|${lang}`);
                  return t
                    ? <TemplateCard key={lang} t={t} manage={manage} busy={busy === t.id} onAct={w => act(t, w)} />
                    : <div key={lang} className="gst-tpl gst-tpl__missing">No {lang === "ar" ? "Arabic" : "English"} {channel === "email" ? "email" : "WhatsApp"} template for this step yet.</div>;
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </Card>
  );
}

function TemplateCard({ t, manage, busy, onAct }: { t: Template; manage: boolean; busy: boolean; onAct: (w: "approve" | "revoke") => void }) {
  const fmt = useFormat();
  const lintOk = t.lint?.ok ?? false;
  return (
    <article className="gst-tpl">
      <div className="gst-tpl__meta">
        <Badge>{t.language === "ar" ? "Arabic" : "English"}</Badge>
        <span className="gst-small gst-muted">v{t.version}{t.meta_name ? ` · ${t.meta_name}` : ""}</span>
        {t.sendable ? <Badge tone="ok">Can send</Badge> : <Badge tone="neutral">Not sending</Badge>}
      </div>
      {t.subject && <div className="gst-small" dir={t.dir}><strong>{t.subject}</strong></div>}
      <div className="gst-tpl__body" dir={t.dir} lang={t.language}>{t.body}</div>
      {t.buttons?.length ? (
        <div className="gst-chips" dir={t.dir}>{t.buttons.map((b, i) => <Badge key={i} tone="info">{b.text || b.type || "Button"}</Badge>)}</div>
      ) : null}
      <dl className="gst-kv gst-small">
        <dt>Category</dt>
        <dd>
          Needs {humanize((t.category_expected || "").toLowerCase())}
          {t.channel === "whatsapp" && <> · Meta: {t.category_meta ? humanize(t.category_meta.toLowerCase()) : "not synced"}</>}
          {t.category_mismatch && <> <Badge tone="warn">Meta re-labelled it</Badge></>}
        </dd>
        {t.channel === "whatsapp" && (<><dt>Meta status</dt><dd><Badge tone={statusTone(t.meta_status)}>{humanize((t.meta_status || "not submitted").toLowerCase())}</Badge>{t.quality ? ` · quality ${t.quality}` : ""}</dd></>)}
        <dt>Owner approval</dt><dd>{t.approved_at ? `Approved ${fmt.date(t.approved_at)}` : "Not approved"}</dd>
        <dt>Checks</dt><dd>{lintOk ? <Badge tone="ok">Passed</Badge> : <Badge tone="bad">Failed</Badge>}</dd>
      </dl>
      {!lintOk && t.lint?.errors?.length ? <ul className="gst-tpl__errors">{t.lint.errors.map(e => <li key={e}>{humanize(e)}</li>)}</ul> : null}
      {manage && (
        <div className="cx-row" style={{ justifyContent: "flex-end" }}>
          {t.approved_at
            ? <Button size="sm" variant="ghost" loading={busy} onClick={() => onAct("revoke")}>Withdraw approval</Button>
            : <Button size="sm" loading={busy} disabled={!lintOk} title={lintOk ? undefined : "Fix the failed checks first"} onClick={() => onAct("approve")}>Approve</Button>}
        </div>
      )}
    </article>
  );
}
