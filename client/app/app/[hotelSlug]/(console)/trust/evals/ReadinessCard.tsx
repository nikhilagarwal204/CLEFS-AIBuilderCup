"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The readiness gate (03 spec 5.4): the concierge goes live only when the
   required KB sections are approved, a run on the current prompt and KB
   is at most 7 days old, all 22 standard safety and accuracy tests passed
   in it (Gemini judging), safety is 100%, routing 95%+, overall 90%+, and
   the hotel has run 7 days in shadow mode (05 spec, the deck's promise).
   An owner may override a red gate with a written reason; the override is
   audited, lapses after 7 days or at the next completed run, and can be
   withdrawn here.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, type UseApiResult } from "../../../../../lib/api";
import { useFormat } from "../../../../../lib/format";
import { useSession } from "../../../../../lib/session";
import { Badge, Button, Card, EmptyState, Field, Modal, Skeleton, Textarea, useToast } from "../../../../../ui";
import { errText, trustApi } from "../lib";
import { gateTestsText, shadowText } from "../pure";
import type { Readiness } from "../types";

export default function ReadinessCard({ slug, res }: { slug: string; res: UseApiResult<Readiness> }) {
  const fmt = useFormat();
  const toast = useToast();
  const { can } = useSession();
  const canOverride = can("trust.readiness.override");
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const { data, loading, error, reload } = res;

  async function override() {
    setBusy(true);
    try {
      await api.post(trustApi(slug, "/readiness/override"), { reason: reason.trim() });
      toast.success("Override recorded", "The concierge may go live for 7 days or until the next run.");
      setOpen(false);
      setReason("");
      reload();
    } catch (e) {
      toast.error("Override refused", errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function withdraw(id: number) {
    setBusy(true);
    try {
      await api.del(trustApi(slug, `/readiness/override/${id}`));
      toast.success("Override withdrawn");
      reload();
    } catch (e) {
      toast.error("Couldn't withdraw the override", errText(e));
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) return <Card><Skeleton lines={5} /></Card>;
  if (error && !data) return <Card><EmptyState title="Couldn't load the readiness gate" body={error.message} /></Card>;
  if (!data) return null;

  const tone = data.ready ? (data.overridden ? "warn" : "ok") : "bad";
  return (
    <Card
      title="Concierge readiness gate"
      subtitle={`Checked ${fmt.relative(data.evaluated_at)}${data.prompt_version ? ` · prompt ${data.prompt_version}` : ""}`}
      actions={
        <Badge tone={tone} dot>
          {data.ready ? (data.overridden ? "Ready by owner override" : "Ready to go live") : "Not ready"}
        </Badge>
      }
    >
      <GateHeadline data={data} />
      <ul className="trs-checks">
        {data.checks.map(c => (
          <li key={c.key} className="trs-check">
            <span className={`trs-check__mark trs-check__mark--${c.ok ? "ok" : "bad"}`} aria-hidden="true">{c.ok ? "✓" : "✕"}</span>
            <span>
              <strong>{c.label}</strong>
              <span className="cx-sr">{c.ok ? " passing" : " failing"}</span>
              {c.detail && <span className="cx-muted trs-small" style={{ display: "block" }}>{c.detail}</span>}
            </span>
          </li>
        ))}
      </ul>

      {data.override && (
        <div className="trs-banner trs-banner--warn" style={{ marginTop: 14 }}>
          <div className="trs-banner__body">
            <div className="trs-banner__title">Owner override in force until {fmt.dateTime(data.override.expires_at)}</div>
            <div className="trs-small">&ldquo;{data.override.reason}&rdquo;</div>
            {data.override.failing.length > 0 && (
              <div className="cx-muted trs-small">Failing when overridden: {data.override.failing.join(", ")}</div>
            )}
          </div>
          {canOverride && (
            <Button size="sm" variant="ghost" loading={busy} onClick={() => withdraw(data.override!.id)}>Withdraw</Button>
          )}
        </div>
      )}

      {!data.ready && canOverride && !data.override && (
        <div className="cx-row" style={{ marginTop: 14 }}>
          <Button variant="danger" size="sm" onClick={() => setOpen(true)}>Override the gate</Button>
          <span className="cx-muted trs-small">Only for a supervised soft launch. Logged in the audit trail.</span>
        </div>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        dismissible={!busy}
        title="Override the readiness gate"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
            <Button variant="danger" loading={busy} disabled={reason.trim().length < 10} onClick={override}>Record override</Button>
          </>
        }
      >
        <p className="cx-muted trs-small">
          The concierge will be allowed to talk to guests although these checks fail:{" "}
          {data.checks.filter(c => !c.ok).map(c => c.label).join(", ") || "none"}. Your name and reason go into the audit log.
        </p>
        <Field label="Reason" hint="At least 10 characters, for example: soft launch on floor 5 with the duty manager watching.">
          <Textarea value={reason} onChange={e => setReason(e.target.value)} rows={3} maxLength={1000} />
        </Field>
      </Modal>
    </Card>
  );
}


/* The two promises the deck makes, above the full list of checks. */
function GateHeadline({ data }: { data: Readiness }) {
  const tests = gateTestsText(data.tests);
  const shadow = shadowText(data.shadow);
  if (!data.tests && !data.shadow) return null;
  return (
    <div className="trs-gate">
      <div className={`trs-gate__item trs-gate__item--${tests.ok ? "ok" : "bad"}`}>
        <span className="trs-gate__label">Safety and accuracy tests</span>
        <span className="trs-gate__value">{tests.value}</span>
        <span className="cx-muted trs-small">{tests.sub}</span>
      </div>
      <div className={`trs-gate__item trs-gate__item--${shadow.ok ? "ok" : "bad"}`}>
        <span className="trs-gate__label">Shadow mode</span>
        <span className="trs-gate__value">{shadow.value}</span>
        <span className="cx-muted trs-small">{shadow.sub}</span>
      </div>
    </div>
  );
}
