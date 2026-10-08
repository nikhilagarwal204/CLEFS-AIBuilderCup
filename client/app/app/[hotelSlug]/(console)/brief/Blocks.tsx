"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The tier blocks around the ranked items (Canon 18): inquiries (daily),
   autonomy suggestions and the compliance digest (weekly), the finance
   pulse (weekly NRevPAR, monthly P&L) and the weekly report pointer. Each
   block is optional: another lane's api may still return nothing, and an
   absent block renders nothing rather than an empty card.
   ═══════════════════════════════════════════════════════════════════════ */

import { useState } from "react";
import { api, hotelPath } from "../../../../lib/api";
import { useSession } from "../../../../lib/session";
import { Badge, Button, Card, CountdownChip, MoneyText, useToast } from "../../../../ui";
import { brfHref, PERM } from "./lib";
import type { AutonomyBlock, ComplianceRow, FinanceBlock, InquiriesBlock, ReportPointer, RunBlocks } from "./types";

// ─── Inquiries ───────────────────────────────────────────────────────────

function Inquiries({ slug, b }: { slug: string; b: InquiriesBlock }) {
  return (
    <Card title="Inquiries" subtitle="Unanswered leads and the money they could be worth"
          actions={<Button size="sm" variant="ghost" href={`/app/${encodeURIComponent(slug)}/inquiries`}>Open inquiries</Button>}>
      <div className="brf-block">
        {b.line && <p style={{ margin: 0 }}>{b.line}</p>}
        {!b.line && (
          <p style={{ margin: 0 }}>
            {b.unanswered ?? 0} unanswered{b.unanswered_over_2h ? `, ${b.unanswered_over_2h} over 2 h` : ""}
            {b.money_at_risk_minor ? <>, <MoneyText amount={b.money_at_risk_minor} minor currency={b.currency} /> at risk{b.estimated ? " (est.)" : ""}</> : null}
          </p>
        )}
      </div>
    </Card>
  );
}

// ─── Autonomy suggestions ────────────────────────────────────────────────

function Autonomy({ slug, b, onChanged }: { slug: string; b: AutonomyBlock; onChanged?: () => void }) {
  const { can } = useSession();
  const toast = useToast();
  const [busy, setBusy] = useState<number | null>(null);
  const [done, setDone] = useState<Record<number, "accepted" | "dismissed">>({});
  if (!b.promotions.length && !b.demotions.length) return null;
  const canManage = can(PERM.autonomy);

  const resolve = async (id: number, verb: "accept" | "dismiss") => {
    setBusy(id);
    try {
      await api.post(hotelPath(slug, `/autonomy/suggestions/${id}/${verb}`));
      setDone(d => ({ ...d, [id]: verb === "accept" ? "accepted" : "dismissed" }));
      toast.success(verb === "accept" ? "Autonomy raised" : "Suggestion dismissed");
      onChanged?.();
    } catch (err) {
      toast.error("Not done", (err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="Autonomy" subtitle="What Clefs could do on its own, and where it stepped back">
      <div className="brf-block">
        {b.promotions.length > 0 && (
          <ul>
            {b.promotions.map(p => (
              <li key={p.event_id}>
                <div className="brf-row">
                  <span className="brf-spacer">{p.text}</span>
                  {done[p.event_id] ? <Badge tone="ok">{done[p.event_id] === "accepted" ? "Accepted" : "Dismissed"}</Badge>
                    : canManage && (
                      <>
                        <Button size="sm" variant="primary" loading={busy === p.event_id} disabled={busy !== null}
                                onClick={() => void resolve(p.event_id, "accept")}>Accept</Button>
                        <Button size="sm" variant="ghost" disabled={busy !== null}
                                onClick={() => void resolve(p.event_id, "dismiss")}>Dismiss</Button>
                      </>
                    )}
                </div>
              </li>
            ))}
          </ul>
        )}
        {b.demotions.length > 0 && (
          <ul>{b.demotions.map(d => <li key={d.event_id}><Badge tone="warn">Lowered</Badge> {d.text}</li>)}</ul>
        )}
        {!canManage && b.promotions.length > 0 && <p className="brf-muted" style={{ margin: 0 }}>An owner or GM can accept these.</p>}
      </div>
    </Card>
  );
}

// ─── Compliance digest ───────────────────────────────────────────────────

function Compliance({ rows }: { rows: ComplianceRow[] }) {
  if (!rows.length) return null;
  return (
    <Card title="Compliance" subtitle="Listed every week, never ranked against money">
      <div className="brf-block">
        <ul>
          {rows.map(r => (
            <li key={r.signal_id}>
              <div className="brf-row">
                <span className="brf-spacer">{r.title}</span>
                {r.due_at && <CountdownChip due={r.due_at} prefix="Due" />}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

// ─── Finance pulse ───────────────────────────────────────────────────────

const WEEKLY_FIGURES: readonly [string, string][] = [
  ["nrevpar_minor", "NRevPAR"], ["revpar_minor", "RevPAR"], ["rooms_revenue_minor", "Rooms revenue"],
  ["distribution_cost_minor", "Distribution cost"], ["cancellation_leakage_minor", "Cancellation leakage"],
];
const MONTHLY_FIGURES: readonly [string, string][] = [
  ["total_revenue", "Total revenue"], ["gop", "GOP"], ["goppar", "GOPPAR"], ["verified_profit", "Verified profit"],
];

function Finance({ slug, b, canOpen }: { slug: string; b: FinanceBlock; canOpen: boolean }) {
  const d = b.data ?? {};
  const ccy = typeof d.currency === "string" ? d.currency : undefined;
  const weekly = b.kind === "weekly_pulse";
  const figures = (weekly ? WEEKLY_FIGURES : MONTHLY_FIGURES).filter(([k]) => d[k] !== null && d[k] !== undefined);
  return (
    <Card title={weekly ? "Weekly pulse" : "Monthly P&L pulse"}
          subtitle={weekly ? `Week from ${b.week_start ?? ""}` : `Month of ${b.month ?? ""}`}
          actions={canOpen ? <Button size="sm" variant="ghost" href={`/app/${encodeURIComponent(slug)}/finance`}>Open finance</Button> : undefined}>
      {b.available === false || !figures.length ? (
        <p className="brf-muted" style={{ margin: 0 }}>{weekly ? "No figures for that week yet." : "No published P&L for last month yet."}</p>
      ) : (
        <dl className="brf-kv">
          {figures.map(([k, label]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{label}</dt>
              <dd>{k.endsWith("_minor")
                ? <MoneyText amount={d[k] as number} minor currency={ccy} />
                : <MoneyText amount={d[k] as string} currency={ccy} />}</dd>
            </div>
          ))}
          {!weekly && typeof d.gop_margin_pct === "string" && (<><dt>GOP margin</dt><dd>{d.gop_margin_pct}%</dd></>)}
        </dl>
      )}
    </Card>
  );
}

// ─── Weekly report pointer ───────────────────────────────────────────────

function Report({ slug, b }: { slug: string; b: ReportPointer }) {
  const week = String(b.period_key ?? b.iso_week ?? "");
  if (!week) return null;
  return (
    <Card title="Weekly verified-value report" gold
          actions={<Button size="sm" variant="secondary" href={brfHref(slug, `/reports/weekly/${encodeURIComponent(week)}`)}>Open report</Button>}>
      <p style={{ margin: 0 }}>{typeof b.text === "string" ? b.text : `Week ${week}`}</p>
    </Card>
  );
}

// ─── All blocks ──────────────────────────────────────────────────────────

/* A block that only links elsewhere is shown to people who can open that
   page (QA OWN-15: finance saw "Open inquiries" and landed on "You don't
   have access to Inquiries"). The inquiry line is inquiry data, so the whole
   block needs inquiries.view; the finance figures are the brief's own, so
   only its link needs finance.view. */
const INQUIRIES_VIEW = "inquiries.view";
const FINANCE_VIEW = "finance.view";

export default function Blocks({ slug, blocks, onChanged }: { slug: string; blocks: RunBlocks; onChanged?: () => void }) {
  const { can } = useSession();
  const inquiries = can(INQUIRIES_VIEW) ? blocks.inquiries : undefined;
  const any = inquiries || blocks.autonomy || blocks.compliance?.length || blocks.finance || blocks.report;
  if (!any) return null;
  return (
    <section className="brf-group" aria-label="More in this brief">
      <h2 className="brf-group__title">Also in this brief</h2>
      <div className="brf-grid2">
        {blocks.report && <Report slug={slug} b={blocks.report} />}
        {inquiries && <Inquiries slug={slug} b={inquiries} />}
        {blocks.finance && <Finance slug={slug} b={blocks.finance} canOpen={can(FINANCE_VIEW)} />}
        {blocks.autonomy && <Autonomy slug={slug} b={blocks.autonomy} onChanged={onChanged} />}
        {blocks.compliance && <Compliance rows={blocks.compliance} />}
      </div>
    </section>
  );
}
