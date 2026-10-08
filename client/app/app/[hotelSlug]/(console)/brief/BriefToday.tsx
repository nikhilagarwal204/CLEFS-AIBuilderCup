"use client";

/* ═══════════════════════════════════════════════════════════════════════
   Today's brief (02 spec 6.4, Canon 18). Shows the stored run the owner
   was sent (or a live assembly marked Preview when none went out yet),
   a past run from ?run= (the link every brief email and WhatsApp button
   carries), or another tier via the cadence switch.

   Decisions patch the card's state in place from decide()'s answer; the
   run itself is a snapshot, so its numbers do not move until the next
   brief. Overdue pins render above the ranked items, as in the message.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { LuRefreshCw, LuSend, LuSunrise } from "react-icons/lu";
import { api, useApi } from "../../../../lib/api";
import { useFormat } from "../../../../lib/format";
import { useHotel } from "../../../../lib/hotel";
import { useSession } from "../../../../lib/session";
import type { Action, ActionStatus, Decision } from "../../../../lib/types";
import {
  Badge, Button, Card, EmptyState, MoneyText, Segmented, Select, Skeleton, StatTile, useToast,
} from "../../../../ui";
import AdjustSheet from "./AdjustSheet";
import BriefCard, { type CardDecide } from "./BriefCard";
import Blocks from "./Blocks";
import SendNowDialog from "./SendNowDialog";
import {
  actionApi, brfApi, brfHref, CADENCE_LABEL, CADENCES, decideError, OPEN_SIGNAL, PERM, plural, signalDecideBody,
} from "./lib";
import { periodLabel } from "./pure";
import type { BriefItem, Cadence, Run } from "./types";
import { useDecide } from "./useDecide";

export default function BriefToday({ runId, initialCadence, focusSignal, focusAction }: {
  runId: number | null;
  initialCadence: Cadence;
  focusSignal: number | null;
  focusAction: number | null;
}) {
  const { slug } = useHotel();
  const { can } = useSession();
  const fmt = useFormat();
  const router = useRouter();
  const toast = useToast();
  const [cadence, setCadence] = useState<Cadence>(initialCadence);
  const [preview, setPreview] = useState<Run | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [sendOpen, setSendOpen] = useState(false);
  const [adjusting, setAdjusting] = useState<BriefItem | null>(null);
  // Live action states after a decision, keyed by action id.
  const [live, setLive] = useState<Record<number, ActionStatus>>({});
  // Signal-only items after a decision, keyed by signal id: the action the
  // signal route proposed (the card then decides it like any other), or
  // the signal's new status after a snooze or dismiss.
  const [sigLive, setSigLive] = useState<Record<number, SignalLive>>({});
  const [sigBusy, setSigBusy] = useState<{ id: number; decision: Decision } | null>(null);

  const path = runId !== null ? brfApi(slug, `/runs/${runId}`) : brfApi(slug, `/today?cadence=${cadence}`);
  const run = useApi<Run>(path, [slug, runId, cadence]);
  // A ?run= link can be any tier: its history list is that run's tier.
  const tier = runId !== null && run.data ? run.data.cadence : cadence;
  const history = useApi<Run[]>(brfApi(slug, `/runs?cadence=${tier}&limit=30`), [slug, tier]);
  const shown = preview ?? run.data;

  const onChanged = useCallback((a: Action) => setLive(l => ({ ...l, [a.id]: a.status })), []);
  // A refused decision means the card is stale: take the action's live state.
  const onStale = useCallback((id: number) => {
    api.get<Action>(actionApi(slug, id)).then(onChanged).catch(() => undefined);
  }, [slug, onChanged]);
  const { busy, decide } = useDecide(slug, onChanged, onStale);

  const focusKey = useMemo(() => {
    const all = [...(shown?.overdue ?? []), ...(shown?.items ?? [])];
    const hit = all.find(i => (focusAction !== null && i.action_id === focusAction) || (focusSignal !== null && i.signal_id === focusSignal));
    return hit ? `${hit.kind}-${hit.item_no}` : null;
  }, [shown, focusAction, focusSignal]);

  useEffect(() => {
    if (!focusKey) return;
    document.getElementById(`brf-item-${focusKey}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focusKey]);

  // A signal-only item: the brief's signal route proposes (then core
  // decide() approves), assigns, snoozes or dismisses it.
  const decideSignal = useCallback(async (item: BriefItem, d: Decision, extra?: Parameters<CardDecide>[2]) => {
    const sid = item.signal_id;
    const body = signalDecideBody(d, extra);
    if (sid === null || body === null) return;
    setSigBusy({ id: sid, decision: d });
    try {
      const out = await api.post<SignalDecideResult>(brfApi(slug, `/signals/${sid}/decide`), body);
      setSigLive(m => ({ ...m, [sid]: { action: out.action, status: out.signal?.status ?? null } }));
      if (out.action) setLive(l => ({ ...l, [out.action!.id]: out.action!.status }));
      if (d === "approve" && out.action) {
        if (out.action.status === "proposed") await decide(out.action.id, "approve");
        else toast.info("Already moved on", out.action.title);
      } else {
        toast.success(SIGNAL_DONE[d] ?? "Done", out.action?.title ?? item.title);
      }
    } catch (err) {
      toast.error("Not done", decideError(err));
    } finally {
      setSigBusy(null);
    }
  }, [slug, decide, toast]);

  const onDecide = useCallback<CardDecide>(async (item, d, extra) => {
    const proposed = item.signal_id !== null ? sigLive[item.signal_id]?.action : null;
    const actionId = item.action_id ?? proposed?.id ?? null;
    if (actionId === null) return decideSignal(item, d, extra);
    await decide(actionId, d, extra);
  }, [decide, decideSignal, sigLive]);

  const refreshPreview = async () => {
    setPreviewBusy(true);
    try {
      setPreview(await api.post<Run>(brfApi(slug, "/preview"), { cadence }));
    } catch (err) {
      toast.error("Could not build a preview", (err as Error).message);
    } finally {
      setPreviewBusy(false);
    }
  };

  // Another tier starts from its own snapshot's states (a new ?run= remounts
  // this component through the page's key, which resets them the same way).
  const pickCadence = (c: Cadence) => {
    setCadence(c);
    setLive({});
    setSigLive({});
    setPreview(null);
    if (runId !== null) router.replace(brfHref(slug, `?cadence=${c}`));
  };

  const pickRun = (v: string) => {
    router.push(v ? brfHref(slug, `?run=${v}`) : brfHref(slug, `?cadence=${tier}`));
  };

  const busyFor = (item: BriefItem): Decision | null => {
    if (busy && item.action_id !== null && busy.id === item.action_id) return busy.decision;
    return sigBusy && item.signal_id !== null && sigBusy.id === item.signal_id ? sigBusy.decision : null;
  };

  return (
    <>
      <div className="brf-bar">
        <div className="brf-bar__left">
          <Segmented ariaLabel="Brief tier" value={shown?.cadence ?? cadence} onChange={pickCadence}
                     options={CADENCES.map(c => ({ value: c, label: CADENCE_LABEL[c] }))} />
          <Select aria-label="Earlier briefs" value={runId !== null ? String(runId) : ""} onChange={e => pickRun(e.target.value)}
                  options={[
                    { value: "", label: "Latest" },
                    ...(history.data ?? []).filter(r => r.id !== null).map(r => ({
                      value: String(r.id), label: `${fmt.date(r.brief_date + "T12:00:00Z", "medium")} · ${r.status ?? ""}`,
                    })),
                  ]} />
        </div>
        <div className="brf-bar__right">
          <Button size="sm" variant="ghost" icon={<LuRefreshCw />} loading={previewBusy} onClick={() => void refreshPreview()}>
            Preview now
          </Button>
          {can(PERM.manage) && (
            <Button size="sm" variant="primary" icon={<LuSend />} onClick={() => setSendOpen(true)}>Send now</Button>
          )}
        </div>
      </div>

      {run.loading && !shown && <Card><Skeleton lines={3} /></Card>}
      {run.error && !shown && (
        <EmptyState title={run.error.status === 404 ? "No brief found" : "Could not load the brief"} body={run.error.message}
                    action={<Button onClick={run.reload}>Try again</Button>} />
      )}
      {shown && <RunView slug={slug} run={shown} live={live} sigLive={sigLive} focusKey={focusKey} busyFor={busyFor}
                         onDecide={onDecide} onAdjust={setAdjusting} onBlocksChanged={run.reload} />}

      <AdjustSheet slug={slug} actionId={adjusting?.action_id ?? null} open={adjusting !== null}
                   onClose={() => setAdjusting(null)} busy={busy?.decision === "adjust"}
                   onSubmit={(patch, note) => adjusting?.action_id != null
                     ? decide(adjusting.action_id, "adjust", { params_patch: patch, note })
                     : undefined} />
      <SendNowDialog slug={slug} cadence={shown?.cadence ?? cadence} open={sendOpen} onClose={() => setSendOpen(false)}
                     onSent={id => { setSendOpen(false); if (id) router.push(brfHref(slug, `?run=${id}`)); else run.reload(); }} />
    </>
  );
}

// ─── One run ─────────────────────────────────────────────────────────────

interface SignalLive { action: Action | null; status: string | null }
interface SignalDecideResult { signal: { id: number; status: string } | null; action: Action | null }

const SIGNAL_DONE: Partial<Record<Decision, string>> = {
  assign: "Assigned", snooze: "Snoozed", ignore: "Dismissed",
};

/** The item as it stands now: a signal-only item whose signal route
    proposed an action carries that action, so the card decides it. */
function effective(i: BriefItem, sigLive: Record<number, SignalLive>): BriefItem {
  const a = i.action_id === null && i.signal_id !== null ? sigLive[i.signal_id]?.action : null;
  return a ? { ...i, action_id: a.id, action_status: a.status } : i;
}

const RUN_STATUS: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "neutral" | "info" }> = {
  sent: { label: "Sent", tone: "ok" },
  partial: { label: "Partly sent", tone: "warn" },
  failed: { label: "Not delivered", tone: "bad" },
  empty: { label: "Nothing to send", tone: "neutral" },
  generated: { label: "Not sent", tone: "info" },
};

function RunView({ slug, run, live, sigLive, focusKey, busyFor, onDecide, onAdjust, onBlocksChanged }: {
  slug: string;
  run: Run;
  live: Record<number, ActionStatus>;
  sigLive: Record<number, SignalLive>;
  focusKey: string | null;
  busyFor: (item: BriefItem) => Decision | null;
  onDecide: CardDecide;
  onAdjust: (item: BriefItem) => void;
  onBlocksChanged: () => void;
}) {
  const fmt = useFormat();
  const items = (run.items ?? []).map(i => effective(i, sigLive));
  const overdue = (run.overdue ?? []).map(i => effective(i, sigLive));
  const st = run.status ? RUN_STATUS[run.status] : null;
  const v = run.verified;
  // Waiting on you: proposals (and snoozed ones) plus signal-only items
  // still open, which the card can now decide too. Verified items are won.
  const open = [...overdue, ...items].filter(i => {
    if (i.action_id === null) {
      const ss = i.signal_id !== null ? sigLive[i.signal_id]?.status : null;
      return i.signal_id !== null && i.section !== "verified" && (!ss || OPEN_SIGNAL.has(ss));
    }
    const s = live[i.action_id] ?? i.action_status;
    return s === "proposed" || s === "snoozed";
  }).length;
  const period = periodLabel(run.cadence, run.period_start, run.period_end);

  const card = (i: BriefItem) => (
    <li key={`${i.kind}-${i.item_no}`} id={`brf-item-${i.kind}-${i.item_no}`}>
      <BriefCard slug={slug} item={i} status={i.action_id !== null ? live[i.action_id] : undefined}
                 signalStatus={i.signal_id !== null ? sigLive[i.signal_id]?.status : undefined}
                 onDecide={onDecide} onAdjust={onAdjust} busy={busyFor(i)} focus={focusKey === `${i.kind}-${i.item_no}`} />
    </li>
  );

  return (
    <>
      <Card gold>
        <div className="brf-head">
          <p className="brf-head__line">{run.headline}</p>
          <div className="brf-head__meta">
            <span>{CADENCE_LABEL[run.cadence]} brief for {period ?? fmt.date(run.brief_date + "T12:00:00Z", "long")}</span>
            {run.preview ? <Badge tone="info">Preview, not sent</Badge> : st && <Badge tone={st.tone}>{st.label}</Badge>}
            {run.sent_at && <span>Sent {fmt.dateTime(run.sent_at)}</span>}
            {!run.sent_at && <span>Built {fmt.dateTime(run.generated_at)}</span>}
          </div>
        </div>
      </Card>

      <div className="brf-tiles">
        <StatTile label="At stake" value={<MoneyText amount={run.at_stake_minor} minor currency={run.currency} />} />
        <StatTile label="Waiting on you" value={fmt.number(open)} sub={`of ${plural(items.length + overdue.length, "item")}`} />
        <StatTile label="Verified, last 24 h" value={<MoneyText amount={v?.last_24h_minor ?? 0} minor currency={v?.currency ?? run.currency} />}
                  sub={v?.last_24h_actions ? plural(v.last_24h_actions, "action") : undefined} />
        <StatTile label="Verified this month" value={<MoneyText amount={v?.mtd_minor ?? 0} minor currency={v?.currency ?? run.currency} />}
                  sub={v?.mtd_actions ? plural(v.mtd_actions, "action") : undefined} />
      </div>

      {overdue.length > 0 && (
        <section className="brf-group" aria-label="Overdue">
          <h2 className="brf-group__title"><Badge tone="bad" dot>Overdue</Badge> Past their deadline by a day or more</h2>
          <ul className="brf-list">{overdue.map(card)}</ul>
        </section>
      )}

      <section className="brf-group" aria-label="Ranked items">
        <h2 className="brf-group__title">Ranked by money, confidence and deadline</h2>
        {items.length ? <ul className="brf-list">{items.map(card)}</ul> : (
          <EmptyState icon={<LuSunrise />} title="Nothing needs a decision"
                      body="Clefs found nothing worth your time in this tier. Verified results are still counted below." />
        )}
      </section>

      {v?.text && <div className="brf-verified" role="note"><b>Verified results.</b> {v.text.replace(/^Verified results:\s*/, "")}</div>}

      <Blocks slug={slug} blocks={run.blocks ?? {}} onChanged={onBlocksChanged} />
    </>
  );
}
