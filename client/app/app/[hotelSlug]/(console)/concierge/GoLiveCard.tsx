"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Go-live switch for the guest concierge (server live.py, 03 spec 5.4).

   Guests reach the concierge (web, phone, WhatsApp) only while it is live.
   Switching it ON needs Trust's readiness gate: every check green, or an
   owner override that Trust records in the audit log. The server is the
   authority (PUT /live answers 409 readiness_not_met with the failing
   checks); this card shows each check so the manager sees WHY the button
   is disabled instead of guessing. Switching OFF is always allowed but
   takes effect on the next guest mint, so it asks for a confirmation.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { LuCheckCircle2, LuShieldCheck, LuXCircle } from "react-icons/lu";
import { apiFetch, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Card, Modal, Skeleton, useToast } from "../../../../ui";
import { PERM, ccApi, errText } from "./lib";
import type { LiveState } from "./types";

// Trust's check keys, for a check that arrives without its own label.
const CHECK_LABEL: Record<string, string> = {
  kb_sections: "Required knowledge sections approved",
  recent_run: "A recent test run on the current prompt and knowledge",
  safety: "Safety scenarios at 100%",
  routing: "Routing at 95% or above",
  overall: "Overall at 90% or above",
};

export function GoLiveCard({ slug }: { slug: string }) {
  const { can } = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const live = useApi<LiveState>(ccApi(slug, "/live"), [slug]);
  const [busy, setBusy] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);

  const switchLive = async (on: boolean) => {
    setBusy(true);
    try {
      await apiFetch(ccApi(slug, "/live"), { method: "PUT", json: { live: on } });
      toast({ title: on ? "The concierge is live for guests" : "The concierge is off for guests", tone: "ok" });
      setConfirmOff(false);
      live.reload();
    } catch (err) {
      toast({ title: "Could not switch the concierge", body: errText(err), tone: "bad" });
      live.reload();
    } finally {
      setBusy(false);
    }
  };

  const trustHref = `/app/${encodeURIComponent(slug)}/trust/evals`;
  const d = live.data;
  const checks = d?.readiness.checks ?? [];
  const override = d?.readiness.overridden ? d.readiness.override : null;

  return (
    <Card title="Go-live check"
      subtitle="Guests reach the concierge only once it is live. Switching it on needs Trust's test conversations to pass, or an owner override recorded in the audit log."
      actions={<Button size="sm" variant="secondary" href={trustHref} icon={<LuShieldCheck />}>Test runs</Button>}>
      {live.error ? (
        <div className="cc-callout" role="alert">{errText(live.error)}</div>
      ) : !d ? <Skeleton lines={3} /> : (
        <div className="cx-stack">
          <div className="cx-row">
            <Badge tone={d.live ? "ok" : "warn"} dot>
              {d.live ? (d.source === "grandfathered" ? "Live (existing hotel)" : d.source === "demo" ? "Live (demo hotel)" : "Live") : "Off for guests"}
            </Badge>
            <Badge tone={d.readiness.ready ? "ok" : "neutral"}>
              {d.readiness.ready ? (d.readiness.overridden ? "Ready (owner override)" : "Checks passed") : "Checks not passed yet"}
            </Badge>
            {can(PERM.manage) && (
              <Button size="sm" variant={d.live ? "secondary" : "primary"} loading={busy && !confirmOff}
                disabled={!d.live && !d.readiness.ready}
                onClick={() => (d.live ? setConfirmOff(true) : void switchLive(true))}>
                {d.live ? "Switch off" : "Go live"}
              </Button>
            )}
          </div>
          {checks.length > 0 ? (
            <ul className="cc-checks" aria-label="Readiness checks">
              {checks.map(c => (
                <li key={c.key} className={`cc-checks__item${c.ok ? "" : " cc-checks__item--bad"}`}>
                  {c.ok ? <LuCheckCircle2 aria-label="Passed" /> : <LuXCircle aria-label="Not passed" />}
                  <span className="cc-grow">{c.label || CHECK_LABEL[c.key] || c.key}</span>
                  {c.detail && <span className="cc-small cx-muted">{c.detail}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="cc-sub">Trust has no readiness result for this hotel yet. Run the test conversations first.</p>
          )}
          {override && (
            <div className="cc-callout cc-callout--info">
              Owner override in force until {fmt.dateTime(override.expires_at)}
              {override.reason ? `: ${override.reason}` : ""}. The red checks stay red; the next test run replaces it.
            </div>
          )}
          {!d.live && !d.readiness.ready && (
            <p className="cc-sub">Fix the red checks and run the tests again, or ask the owner to record an override in Trust.</p>
          )}
          {d.readiness.evaluated_at && (
            <p className="cc-small cx-muted">Checked {fmt.dateTime(d.readiness.evaluated_at)}</p>
          )}
        </div>
      )}
      <Modal open={confirmOff} onClose={() => setConfirmOff(false)} dismissible={!busy} size="sm"
        title="Switch the concierge off for guests?"
        footer={<>
          <Button variant="ghost" onClick={() => setConfirmOff(false)} disabled={busy}>Keep it live</Button>
          <Button variant="danger" loading={busy} onClick={() => void switchLive(false)}>Switch off</Button>
        </>}>
        <p className="cc-sub">
          New guest conversations stop at once: the room page asks guests to call the front desk and phone calls go to the
          operator. Switching it back on needs the readiness checks to pass again.
        </p>
      </Modal>
    </Card>
  );
}
