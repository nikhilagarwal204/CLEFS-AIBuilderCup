"use client";

/* The month's top anomalies (04 spec 6.8, at most 3): what moved against
   the trailing months, how sure the scan is, and "Create action", which
   opens a finance.investigate_anomaly task in the action ledger. That task
   is a human follow-up and never counts toward verified profit. The server
   call is idempotent per anomaly, so a double tap creates one task. */

import { useState } from "react";
import { apiFetch } from "../../../../../lib/api";
import { Badge, Button, Card, ConfidencePill, useToast } from "../../../../../ui";
import { anomalyKindLabel, anomalyLine, finApi, monthLabel, num } from "../lib";
import type { Anomaly } from "../types";
import { errText, Money } from "./bits";

interface ActionOut { action: { id: number | null; status: string; title: string }; anomaly_id: number }

export default function AnomalyList({ slug, anomalies, canAct, labels }: {
  slug: string;
  anomalies: readonly Anomaly[];
  canAct: boolean;
  /** USALI key to label, for line-level findings. */
  labels?: Record<string, string>;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<number | null>(null);
  const [created, setCreated] = useState<Record<number, number | null>>({});

  const act = async (a: Anomaly) => {
    setBusy(a.id);
    try {
      const out = await apiFetch<ActionOut>(finApi(slug, `/anomalies/${a.id}/action`), { method: "POST" });
      setCreated(c => ({ ...c, [a.id]: out.action.id }));
      toast.success("Action created", out.action.title);
    } catch (err) {
      toast.error("Could not create the action", errText(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="Worth a look" subtitle="The biggest moves against your trailing months, ranked by size and confidence.">
      {anomalies.length === 0 ? (
        <div className="fin-muted">Nothing unusual this month. Labour, energy and every expense line sit within their usual range.</div>
      ) : (
        <ul className="fin-list">
          {anomalies.map(a => {
            const line = a.usali_line ? labels?.[a.usali_line] ?? a.usali_line : null;
            const done = a.action_id !== null || a.id in created;
            return (
              <li key={a.id} className="fin-item">
                <div className="fin-item__head">
                  <span className="fin-item__title">
                    {anomalyKindLabel(a.kind)}{line && a.kind === "line_spike" ? `: ${line}` : ""}
                  </span>
                  <ConfidencePill value={num(a.confidence)} />
                </div>
                <div className="fin-muted">
                  {anomalyLine(a) || "Outside the usual range"} in {monthLabel(a.month)}
                  {num(a.delta) !== null && <>, about <Money value={a.delta} currency={a.currency} signed /> against usual</>}.
                </div>
                <div className="fin-row">
                  {done ? (
                    <Badge tone="ok" dot>Action created</Badge>
                  ) : canAct ? (
                    <Button size="sm" variant="secondary" loading={busy === a.id} onClick={() => act(a)}>
                      Create action
                    </Button>
                  ) : (
                    <span className="fin-small">Your role can view findings but not create actions.</span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
