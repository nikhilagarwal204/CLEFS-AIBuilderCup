"use client";

/* The floor queue (03 spec 1.6/1.8), drawn as the deck's worker wireframe
   (05 spec, slides 6 and 10): "Room 204 · Towel" with URGENT and Emergency
   pills, the job in the worker's own language, the guest's own words one
   tap away, the department chip, the time since the request, and the big
   "Mark as Done" on the card itself.

   "Mine" comes first, then the team's open work in this device's scope,
   emergencies and URGENT on top. A card with actions still waiting in the
   outbox shows "Sending..." and its optimistic state, so an offline tap is
   visible at once and never looks lost. */

import { useMemo, useState } from "react";
import { LuCamera, LuCheck, LuClock3, LuMessageSquare } from "react-icons/lu";
import { Badge } from "../../ui";
import { canDo, isUrgent, UNACKED_STATES, type Card } from "../lib/deviceApi";
import { jobTitle, stateTone } from "../lib/cardView";
import { agoText, durationText, languageName, stateText } from "../lib/i18n";
import {
  deptColor, deptKeyOf, guestLanguageOf, guestWords, isEmergency, isPriorityUrgent, jobText, needsProof, proofInReview, proofRejected,
  slaInfo, sortForWorker,
} from "../lib/worker";
import type { TaskActionOpts } from "./TaskSheet";
import { useStaffI18n } from "./StaffI18n";

export type QueueTab = "mine" | "team" | "open";

export function Queue({
  cards, mine, staffIds, meId, pendingIds, now, onOpen, onAction, onMarkDone, onReply, loading,
}: {
  cards: Card[];
  /** This person's open jobs (also the "Active tasks" counter). */
  mine: Card[];
  /** Everyone on shift on this device (the shared-device pool). */
  staffIds: number[];
  meId: number;
  /** Request ids with actions still in the outbox. */
  pendingIds: Set<number>;
  now: number;
  onOpen: (c: Card) => void;
  onAction: (c: Card, action: string, opts?: TaskActionOpts) => void;
  onMarkDone: (c: Card) => void;
  onReply: (c: Card) => void;
  loading: boolean;
}) {
  const { t } = useStaffI18n();
  const unassigned = useMemo(() => cards.filter(c => c.state === "new" || c.state === "reopened"), [cards]);
  const [tab, setTab] = useState<QueueTab | null>(null);
  const active: QueueTab = tab ?? (mine.length ? "mine" : "team");
  const list = useMemo(
    () => sortForWorker(active === "mine" ? mine : active === "open" ? unassigned : cards),
    [active, mine, unassigned, cards],
  );
  const mineIds = useMemo(() => new Set(mine.map(c => c.id)), [mine]);

  const tabs: { key: QueueTab; label: string; n: number }[] = [
    { key: "mine", label: t("tabs.mine"), n: mine.length },
    { key: "open", label: t("tabs.open"), n: unassigned.length },
    { key: "team", label: t("tabs.team"), n: cards.length },
  ];

  return (
    <>
      <div className="svc-dv-tabs" role="tablist" aria-label="Queue">
        {tabs.map(x => (
          <button key={x.key} type="button" role="tab" aria-selected={active === x.key} className="svc-dv-tab" onClick={() => setTab(x.key)}>
            {x.label} <span className="svc-dv-count">{x.n}</span>
          </button>
        ))}
      </div>
      <div className="svc-dv-list">
        {list.map(c => (
          <JobCard
            key={c.id}
            card={c}
            now={now}
            pending={pendingIds.has(c.id)}
            mine={mineIds.has(c.id)}
            forMe={UNACKED_STATES.includes(c.state) && (c.assigned_staff_id === meId || staffIds.includes(c.assigned_staff_id ?? -1))}
            onOpen={onOpen}
            onAction={onAction}
            onMarkDone={onMarkDone}
            onReply={onReply}
          />
        ))}
        {!loading && list.length === 0 && (
          <div className="svc-dv-empty">{active === "mine" ? t("queue.emptyMine") : t("queue.emptyTeam")}</div>
        )}
        {loading && list.length === 0 && <div className="svc-dv-empty">{t("queue.loading")}</div>}
      </div>
    </>
  );
}

function JobCard({ card: c, now, pending, mine, forMe, onOpen, onAction, onMarkDone, onReply }: {
  card: Card;
  now: number;
  pending: boolean;
  mine: boolean;
  forMe: boolean;
  onOpen: (c: Card) => void;
  onAction: (c: Card, action: string, opts?: TaskActionOpts) => void;
  onMarkDone: (c: Card) => void;
  onReply: (c: Card) => void;
}) {
  const { t, lang } = useStaffI18n();
  const [showOriginal, setShowOriginal] = useState(false);

  const sensitive = c.sensitivity === "sensitive";
  const emergency = isEmergency(c);
  const urgent = isPriorityUrgent(c);
  const text = sensitive ? "" : jobText(c);
  // The guest's own words stay one tap away on the card; the English
  // rendering lives in Details, so the card keeps the wireframe's calm.
  const original = sensitive ? null : guestWords(c);
  const guestLang = guestLanguageOf(c);
  const guestLangName = languageName(guestLang, lang);
  const dk = deptKeyOf(c.department_key) ?? deptKeyOf(c.category);
  const color = deptColor(dk);
  const deptName = dk ? t(`dept.${dk}`) : c.department_label || c.category;
  const sla = slaInfo(c, now);
  const titleId = `svc-dv-job-${c.id}`;

  const unassigned = c.state === "new" || c.state === "reopened";
  const primary: "ack" | "complete" | "assign" | null =
    mine && c.state === "assigned" ? "ack"
    : mine && canDo(c.state, "complete") ? "complete"
    : unassigned && canDo(c.state, "assign") ? "assign"
    : null;

  const cls = ["svc-dv-job", isUrgent(c) && "svc-dv-job--urgent", !isUrgent(c) && forMe && c.state === "assigned" && "svc-dv-job--new",
    pending && "svc-dv-job--pending"].filter(Boolean).join(" ");

  return (
    <article className={cls} aria-labelledby={titleId}>
      <div className="svc-dv-job__head">
        <button type="button" id={titleId} className="svc-dv-job__title" onClick={() => onOpen(c)}>
          <span className="svc-dv-job__room">{c.room ? t("job.room", { room: c.room }) : t("job.lobby")}</span>
          <span className="svc-dv-job__sep" aria-hidden="true">·</span>
          <span className="svc-dv-job__what" dir="auto">{sensitive ? t("job.sensitive") : jobTitle(c)}</span>
        </button>
        {(emergency || urgent) && (
          <span className="svc-dv-job__pills">
            {emergency && <span className="svc-dv-pill svc-dv-pill--emergency">{t("job.emergency")}</span>}
            {urgent && <span className="svc-dv-pill svc-dv-pill--urgent">{t("job.urgent")}</span>}
          </span>
        )}
      </div>

      {text && <p className="svc-dv-job__text" dir="auto">{text}</p>}
      {original && showOriginal && <p className="svc-dv-job__alt" dir="auto" lang={guestLang ?? undefined}>{original}</p>}
      {original && (
        <div className="svc-dv-job__toggles">
          <button type="button" className="svc-dv-link" aria-expanded={showOriginal} onClick={() => setShowOriginal(v => !v)}>
            {showOriginal ? t("job.hideOriginal") : guestLangName ? t("job.showOriginalLang", { lang: guestLangName }) : t("job.showOriginal")}
          </button>
        </div>
      )}

      {proofRejected(c) && (
        <div className="svc-dv-proofnote" role="status">
          {c.proof_reason ? t("proof.rejected", { reason: c.proof_reason }) : t("proof.rejectedPlain")}
        </div>
      )}
      {proofInReview(c) && <div className="svc-dv-proofnote svc-dv-proofnote--review" role="status">{t("proof.review")}</div>}

      <div className="svc-dv-job__meta">
        <span className="svc-dv-job__age"><LuClock3 aria-hidden="true" /> {agoText(c.timestamp || now, now, lang, t)}</span>
        {deptName && <span className="svc-dv-dept" style={{ color, background: `${color}1f` }}>{deptName}</span>}
        <Badge tone={stateTone(c.state)}>{stateText(c.state, t)}</Badge>
        {sla && <Badge tone={sla.tone}>{t(`sla.${sla.kind}`, { t: durationText(sla.ms, lang) })}</Badge>}
        {needsProof(c) && mine && <Badge tone="info"><LuCamera aria-hidden="true" /> {t("job.photoNeeded")}</Badge>}
        {mine && c.eta_minutes ? <Badge tone="neutral">{t("job.eta", { n: c.eta_minutes })}</Badge> : null}
        {(c.reopen_count ?? 0) > 0 && <Badge tone="warn">{t("job.reopened", { n: c.reopen_count ?? 0 })}</Badge>}
        {pending && <Badge tone="neutral">{t("job.sending")}</Badge>}
        {!mine && c.assigned_to && <span>{c.assigned_to}</span>}
      </div>

      {primary === "complete" && (
        <button type="button" className="svc-dv-done" onClick={() => onMarkDone(c)}>
          <LuCheck aria-hidden="true" /> {t("action.markDone")}
        </button>
      )}
      {primary === "ack" && (
        <button type="button" className="svc-dv-accept" onClick={() => onAction(c, "ack")}>{t("action.accept")}</button>
      )}
      {primary === "assign" && (
        <button type="button" className="svc-dv-accept" onClick={() => onAction(c, "assign", { self: true })}>{t("action.take")}</button>
      )}

      <div className="svc-dv-job__more">
        {mine && primary === "ack" && (
          <button type="button" className="svc-dv-chipbtn" onClick={() => onMarkDone(c)}>
            <LuCheck aria-hidden="true" /> {t("action.markDone")}
          </button>
        )}
        {mine && (
          <button type="button" className="svc-dv-chipbtn" onClick={() => onReply(c)}>
            <LuMessageSquare aria-hidden="true" /> {t("action.reply")}
          </button>
        )}
        <button type="button" className="svc-dv-chipbtn" onClick={() => onOpen(c)}>{t("action.details")}</button>
      </div>
    </article>
  );
}
