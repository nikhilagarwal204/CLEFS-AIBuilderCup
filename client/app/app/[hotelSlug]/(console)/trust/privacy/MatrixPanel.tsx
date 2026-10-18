"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Access and consent: which role sees which kind of personal data (the PII
   matrix the server enforces through clefs.core.pii, shown read-only so an
   owner can answer an auditor), and the consent wording guests agree to.
   Consent wording is immutable per version (409 consent_text_immutable):
   a change means a new version, so every past consent still points at the
   exact words the guest saw.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { api, useApi } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { Badge, Button, Card, EmptyState, Field, Input, Modal, Select, Skeleton, Textarea, useToast } from "../../../../../ui";
import { errText, trustApi } from "../lib";
import type { ConsentText, PiiMatrix } from "../types";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner", gm: "GM", finance: "Finance", revenue: "Revenue", dept_head: "Dept head",
  front_desk: "Front desk", staff: "Staff", device: "Device",
};

const CLASS_LABEL: Record<string, string> = {
  guest_name: "Guest name", guest_contact: "Guest phone and email", health: "Health and allergies",
  transcript: "Conversation transcripts", staff_phone: "Staff phone", guest_id_document: "ID document number",
  guest_birth_date: "Date of birth", payment_card: "Payment card", request_text: "Request free text", audit_ip: "IP address in audit log",
};

function cellClass(policy: string): string {
  if (policy === "full") return "trs-cell trs-cell--full";
  if (policy === "none") return "trs-cell trs-cell--none";
  return "trs-cell trs-cell--part";
}

function cellLabel(policy: string): string {
  if (policy === "full") return "Full";
  if (policy === "none") return "None";
  if (policy === "masked") return "Masked";
  return policy.replace(/_/g, " ");
}

export default function MatrixPanel({ slug, canManage }: { slug: string; canManage: boolean }) {
  const matrix = useApi<PiiMatrix>(trustApi(slug, "/pii-matrix"), [slug]);
  const m = matrix.data;
  return (
    <>
      <Card title="Who sees which personal data" subtitle="Enforced on the server for every screen and export. Masked shows part of the value, for example the last digits.">
        {matrix.error ? (
          <EmptyState title="Could not load the matrix" body={matrix.error.message} />
        ) : !m ? (
          <Skeleton lines={6} />
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="cx-table trs-matrix">
              <thead>
                <tr>
                  <th scope="col">Data</th>
                  {m.roles.map(r => <th key={r} scope="col">{ROLE_LABEL[r] ?? r}</th>)}
                </tr>
              </thead>
              <tbody>
                {Object.entries(m.classes).map(([cls, cells]) => (
                  <tr key={cls}>
                    <th scope="row" style={{ fontWeight: 500 }}>{CLASS_LABEL[cls] ?? cls.replace(/_/g, " ")}</th>
                    {m.roles.map(r => (
                      <td key={r}><span className={cellClass(cells[r] ?? "none")}>{cellLabel(cells[r] ?? "none")}</span></td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <ConsentCard slug={slug} canManage={canManage} />
    </>
  );
}

// ─── Consent wording ─────────────────────────────────────────────────────

function ConsentCard({ slug, canManage }: { slug: string; canManage: boolean }) {
  const fmt = useFormat();
  const texts = useApi<ConsentText[]>(trustApi(slug, "/consent-texts"), [slug]);
  const [showRetired, setShowRetired] = useState(false);
  const [adding, setAdding] = useState(false);

  // Group by purpose, current wording first, so the owner reads "what a
  // guest sees today" before the history.
  const groups = useMemo(() => {
    const by = new Map<string, ConsentText[]>();
    for (const t of texts.data ?? []) {
      if (!showRetired && !t.current) continue;
      const list = by.get(t.purpose) ?? [];
      list.push(t);
      by.set(t.purpose, list);
    }
    for (const list of by.values()) list.sort((a, b) => Number(b.current) - Number(a.current) || b.created_at.localeCompare(a.created_at));
    return [...by.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [texts.data, showRetired]);

  return (
    <Card
      title="Consent wording"
      subtitle="The exact words guests agree to, per purpose and language. Your hotel's version replaces the Clefs default."
      actions={
        <div className="cx-row" style={{ gap: 8 }}>
          <Button size="sm" variant="ghost" onClick={() => setShowRetired(v => !v)}>{showRetired ? "Current only" : "Show history"}</Button>
          {canManage && <Button size="sm" variant="secondary" onClick={() => setAdding(true)}>New version</Button>}
        </div>
      }
    >
      {texts.error ? (
        <EmptyState title="Could not load consent wording" body={texts.error.message} />
      ) : texts.loading && !texts.data ? (
        <Skeleton lines={5} />
      ) : groups.length === 0 ? (
        <EmptyState title="No consent wording yet" />
      ) : (
        <div className="cx-stack" style={{ gap: 14 }}>
          {groups.map(([purpose, list]) => (
            <div key={purpose}>
              <div className="trs-section-title" style={{ marginTop: 0 }}>{purpose.replace(/_/g, " ")}</div>
              <div className="cx-stack" style={{ gap: 8 }}>
                {list.map(t => (
                  <div key={t.id} className="trs-diff__row">
                    <div className="cx-row" style={{ gap: 6, flexWrap: "wrap" }}>
                      <Badge tone={t.current ? "ok" : "neutral"}>{t.current ? "Current" : "Retired"}</Badge>
                      <Badge tone={t.scope === "hotel" ? "gold" : "neutral"}>{t.scope === "hotel" ? "Your hotel" : "Clefs default"}</Badge>
                      <span className="trs-mono">{t.version}</span>
                      <span className="trs-small">{t.language.toUpperCase()}</span>
                      <span className="trs-small" style={{ color: "var(--clefs-ink-muted)" }}>{fmt.date(t.created_at, "medium")}</span>
                    </div>
                    <p dir={t.language === "ar" ? "rtl" : undefined} style={{ margin: "8px 0 0", whiteSpace: "pre-wrap" }}>{t.body}</p>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      {adding && (
        <NewConsentModal slug={slug} purposes={[...new Set((texts.data ?? []).map(t => t.purpose))]}
                         onClose={() => setAdding(false)} onSaved={() => { setAdding(false); texts.reload(); }} />
      )}
    </Card>
  );
}

function NewConsentModal({ slug, purposes, onClose, onSaved }: {
  slug: string;
  purposes: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [purpose, setPurpose] = useState(purposes[0] ?? "");
  const [version, setVersion] = useState("");
  const [language, setLanguage] = useState("en");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ok = purpose && version.trim() && body.trim();

  async function save() {
    setBusy(true); setErr(null);
    try {
      await api.post(trustApi(slug, "/consent-texts"), { purpose, version: version.trim(), language, body: body.trim() });
      toast.success("Consent wording saved", "New consents record this version from now on.");
      onSaved();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={() => { if (!busy) onClose(); }} title="New consent wording" size="md"
           footer={<><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button>
                     <Button loading={busy} disabled={!ok} onClick={save}>Save version</Button></>}>
      <div className="cx-stack" style={{ gap: 12 }}>
        <div className="trs-filters">
          <Field label="Purpose" htmlFor="ct-purpose">
            <Select id="ct-purpose" value={purpose} onChange={e => setPurpose(e.target.value)}
                    options={purposes.map(p => ({ value: p, label: p.replace(/_/g, " ") }))} />
          </Field>
          <Field label="Version" htmlFor="ct-version" hint="e.g. hotel-2026-11. Cannot be reused.">
            <Input id="ct-version" value={version} maxLength={40} onChange={e => setVersion(e.target.value)} />
          </Field>
          <Field label="Language" htmlFor="ct-lang">
            <Select id="ct-lang" value={language} onChange={e => setLanguage(e.target.value)}
                    options={[{ value: "en", label: "English" }, { value: "ar", label: "Arabic" }]} />
          </Field>
        </div>
        <Field label="Wording" htmlFor="ct-body">
          <Textarea id="ct-body" rows={6} value={body} maxLength={4000} dir={language === "ar" ? "rtl" : undefined}
                    onChange={e => setBody(e.target.value)} />
        </Field>
        {err && <div role="alert" style={{ color: "var(--cx-bad)" }}>{err}</div>}
      </div>
    </Modal>
  );
}
