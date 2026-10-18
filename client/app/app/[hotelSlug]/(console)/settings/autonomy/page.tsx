"use client";

/* Autonomy dial (Canon 17, 04 spec 1.6): one dial per action type, from
   "off" to "auto". Each row shows the level (options above the type's
   ceiling are disabled, a raise into an auto level is owner-only while a
   lowering, Auto to Auto within limits included, is not), a limits form generated
   from the type's limits_model JSON Schema, the 30-day decision stats that
   drive promotion suggestions, and the policy version every executed action
   is stamped with. Open promotion suggestions (the latest autonomy event per
   type) get Accept / Dismiss; open demotions arrive as core.autonomy_demoted
   compliance signals and are shown next to their row.

   Permissions (CONTRACTS.md Part 2): view core.autonomy.view; change,
   tighten limits, lower a level and dismiss core.autonomy.manage; raising
   into an auto level, loosening a limit and accepting a step above approve
   also need core.autonomy.raise_auto. The server enforces all of them; the
   page only avoids offering what would bounce (a loosening it cannot judge
   for every type, so that one is explained when the server refuses it). */

import { useMemo, useState } from "react";
import { LuCheck, LuGauge, LuHistory, LuRotateCcw, LuShieldAlert, LuTrendingDown, LuTrendingUp, LuX } from "react-icons/lu";
import { ApiError, api, hotelPath, useApi, withQuery, type UseApiResult } from "../../../../../lib/api";
import { useFormat, type Formatter } from "../../../../../lib/format";
import { useHotel } from "../../../../../lib/hotel";
import { useSession } from "../../../../../lib/session";
import {
  AUTONOMY_LEVEL_HINTS, AUTONOMY_LEVEL_LABELS,
  type AutonomyEvent, type AutonomyLevel, type AutonomySetting, type AutonomyUpdate, type Signal,
} from "../../../../../lib/types";
import {
  Badge, Button, Card, EmptyState, Field, Input, Modal, Select, Skeleton, Textarea, useToast, type Tone,
} from "../../../../../ui";
import { NAV } from "../../nav";
import {
  canAcceptSuggestion, demotionsByType, describeAutonomyEvent, draftFromLimits, labelForKey, levelOptions, limitFields,
  limitsFromDraft, raisesToAuto, sameLimits, type LimitDraft, type LimitField,
} from "./dial";

const LEVEL_TONE: Record<AutonomyLevel, Tone> = {
  off: "neutral",
  observe: "neutral",
  suggest: "info",
  approve: "ok",
  auto_within_limits: "warn",
  auto: "gold",
};

function moduleLabel(module: string): string {
  if (module === "core") return "Core";
  if (module === "voice") return "Phone AI";
  return NAV.find(n => n.module === module)?.label ?? labelForKey(module);
}

/** Nav order first (the console's order), then alphabetical. */
function moduleOrder(module: string): number {
  const i = NAV.findIndex(n => n.module === module);
  return i < 0 ? NAV.length : i;
}

function toApiError(err: unknown): ApiError {
  return err instanceof ApiError ? err : new ApiError(0, String((err as Error)?.message ?? err));
}

/** What the refused request tried: a 403 means a different thing for each. */
interface Attempt {
  /** The request would let Clefs act automatically where it could not before. */
  raisingToAuto?: boolean;
  /** The PUT carried changed limits (a loosening needs raise_auto). */
  limitsChanged?: boolean;
}

/** One readable sentence per server refusal (routes_contracts / set_autonomy). */
function explain(e: ApiError, attempt: Attempt = {}): string {
  switch (e.code) {
    case "above_ceiling":
      return "That level is above this action's ceiling. Refunds, folio, rate and OTA changes always need a person.";
    case "invalid_limits":
      return e.message.replace(/^invalid limits: /, "Limits rejected: ");
    case "invalid_level":
      return "The server does not know that level.";
    case "suggestion_stale":
      return "The dial changed since this suggestion was made.";
  }
  if (e.status === 403) {
    if (attempt.raisingToAuto) return "Only the owner can let Clefs act automatically.";
    if (attempt.limitsChanged) return "Only the owner can loosen these limits. You can tighten them or lower the level.";
    return "You do not have permission to change the autonomy dial.";
  }
  return e.message;
}

// ─── Page ───

export default function AutonomyPage() {
  const { slug } = useHotel();
  const session = useSession();
  const fmt = useFormat();
  const toast = useToast();
  const canView = session.can("core.autonomy.view");
  const canManage = session.can("core.autonomy.manage");
  const canRaiseAuto = session.can("core.autonomy.raise_auto");
  const canSignals = session.can("core.signals.view");

  const dial = useApi<AutonomySetting[]>(canView ? hotelPath(slug, "/autonomy") : null, [slug]);
  // Demotions are compliance signals of the core module; the list defaults
  // to open + surfaced, which is exactly "not yet dealt with".
  const signals = useApi<Signal[]>(
    canView && canSignals ? withQuery(hotelPath(slug, "/signals"), { module: "core", section: "compliance", limit: 200 }) : null,
    [slug],
  );

  const rows = useMemo(() => (Array.isArray(dial.data) ? dial.data : []), [dial.data]);
  const demotions = useMemo(() => demotionsByType(Array.isArray(signals.data) ? signals.data : []), [signals.data]);
  const groups = useMemo(() => {
    const by = new Map<string, AutonomySetting[]>();
    for (const r of rows) by.set(r.module, [...(by.get(r.module) ?? []), r]);
    return [...by.entries()]
      .sort(([a], [b]) => moduleOrder(a) - moduleOrder(b) || a.localeCompare(b))
      .map(([module, list]) => ({ module, rows: list.sort((a, b) => a.label.localeCompare(b.label)) }));
  }, [rows]);

  const [busy, setBusy] = useState<string | null>(null);

  const replaceRow = (updated: AutonomySetting) =>
    dial.setData(prev => (prev ?? []).map(r => (r.action_type === updated.action_type ? updated : r)));

  const decideSuggestion = async (row: AutonomySetting, verb: "accept" | "dismiss") => {
    const s = row.open_suggestion;
    if (!s) return;
    setBusy(`${verb}:${s.event_id}`);
    try {
      const res = await api.post<AutonomySetting>(hotelPath(slug, `/autonomy/suggestions/${s.event_id}/${verb}`));
      replaceRow(res);
      if (verb === "accept") toast.success(`${row.label} is now ${AUTONOMY_LEVEL_LABELS[s.to_level]}`);
      else toast.success("Suggestion dismissed", "Clefs will not suggest this change again for 30 days.");
    } catch (err) {
      const e = toApiError(err);
      if (e.code === "suggestion_stale" || e.code === "suggestion_not_found") {
        toast.info("That suggestion is out of date", "The dial changed since it was made, so the list was refreshed.");
        dial.reload();
      } else {
        toast.error(verb === "accept" ? "Couldn't accept" : "Couldn't dismiss",
                    explain(e, { raisingToAuto: verb === "accept" && raisesToAuto(row.level, s.to_level) }));
      }
    } finally {
      setBusy(null);
    }
  };

  if (session.loading) return <Card><Skeleton lines={4} /></Card>;
  if (!canView) {
    return (
      <Card>
        <EmptyState icon={<LuGauge />} title="No access to the autonomy dial"
                    body="Owners, general managers, finance, revenue and department heads can see it. Ask an owner if you need it." />
      </Card>
    );
  }
  if (dial.loading && !dial.data) return <Card><Skeleton lines={8} /></Card>;
  if (dial.error && !dial.data) {
    return (
      <Card>
        <EmptyState title="Couldn't load the autonomy dial" body={dial.error.message}
                    action={<Button onClick={dial.reload}>Try again</Button>} />
      </Card>
    );
  }
  if (!rows.length) {
    return (
      <Card>
        <EmptyState icon={<LuGauge />} title="No action types yet"
                    body="Each module registers the actions it can take as it ships. They appear here with their defaults." />
      </Card>
    );
  }

  return (
    <div className="cx-stack">
      <Card title="How much Clefs does on its own"
            subtitle="Every action type has its own dial. Nothing goes above its ceiling, and an incident lowers a dial to Approve by itself.">
        <div className="cx-muted" style={{ fontSize: "0.86rem", lineHeight: 1.6 }}>
          <strong>Observe</strong> logs only. <strong>Suggest</strong> leaves a draft in the console. <strong>Approve</strong> puts it
          in the brief for one tap. <strong>Auto within limits</strong> runs it when the limits pass. <strong>Auto</strong> runs it.
          {!canManage && <> Only owners and general managers can change a dial.</>}
          {canManage && !canRaiseAuto && <> Only the owner can switch on the automatic levels.</>}
        </div>
      </Card>

      <SuggestionsCard rows={rows} demotions={demotions} fmt={fmt} busy={busy}
                       canManage={canManage} canRaiseAuto={canRaiseAuto} onDecide={decideSuggestion} />

      {groups.map(g => (
        <section key={g.module} className="cx-stack" aria-label={moduleLabel(g.module)}>
          <h2 className="cx-muted" style={{ fontSize: "0.78rem", letterSpacing: "0.08em", textTransform: "uppercase", margin: "8px 0 0" }}>
            {moduleLabel(g.module)}
          </h2>
          {g.rows.map(row => (
            // The key carries the server version, so a save (or a reload that
            // brings a newer row) remounts the row with a fresh draft instead
            // of syncing state in an effect. The currency is in it because
            // money limits are drafted in the hotel's currency.
            <div key={`${row.action_type}:${row.policy_version}:${String(row.updated_at)}:${fmt.currency}`}
                 id={`dial-${row.action_type}`}>
              <AutonomyRow slug={slug} row={row} demotions={demotions.get(row.action_type) ?? []}
                           canManage={canManage} canRaiseAuto={canRaiseAuto} onSaved={replaceRow} />
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

// ─── Suggestions: promotions (dial events) and demotions (signals) ───

function SuggestionsCard({
  rows, demotions, fmt, busy, canManage, canRaiseAuto, onDecide,
}: {
  rows: readonly AutonomySetting[];
  demotions: Map<string, Signal[]>;
  fmt: Formatter;
  busy: string | null;
  canManage: boolean;
  canRaiseAuto: boolean;
  onDecide: (row: AutonomySetting, verb: "accept" | "dismiss") => void;
}) {
  const promotions = rows.filter(r => r.open_suggestion);
  const lowered = rows.flatMap(r => (demotions.get(r.action_type) ?? []).map(s => ({ row: r, signal: s })));
  if (!promotions.length && !lowered.length) return null;

  return (
    <Card title="Suggested changes" subtitle="Clefs suggests a step up after 50 decisions in 30 days with 95% approved unedited. It never applies one by itself.">
      <div className="cx-stack" style={{ gap: 12 }}>
        {promotions.map(r => {
          const s = r.open_suggestion!;
          const canAccept = canAcceptSuggestion(s.to_level, canManage, canRaiseAuto);
          return (
            <div key={`p${s.event_id}`} className="cx-row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
              <div style={{ minWidth: 0, flex: "1 1 260px" }}>
                <div className="cx-row" style={{ gap: 8 }}>
                  <LuTrendingUp aria-hidden="true" />
                  <strong>{r.label}</strong>
                  <span className="cx-muted">from {AUTONOMY_LEVEL_LABELS[r.level]} to {AUTONOMY_LEVEL_LABELS[s.to_level]}</span>
                </div>
                <div className="cx-faint" style={{ fontSize: "0.82rem", marginTop: 4 }}>
                  {r.stats.approved_unedited} of {r.stats.decisions} decisions in the last {r.stats.window_days} days were approved
                  unedited ({fmt.pct(r.stats.approval_rate, { ratio: true })}). Suggested {fmt.relative(s.at)}.
                </div>
              </div>
              <div className="cx-row" style={{ gap: 6 }}>
                <Button size="sm" variant="ghost" icon={<LuX />} disabled={!canManage || busy !== null}
                        loading={busy === `dismiss:${s.event_id}`} onClick={() => onDecide(r, "dismiss")}
                        title={canManage ? undefined : "Only owners and general managers can dismiss a suggestion"}>
                  Dismiss
                </Button>
                <Button size="sm" variant="primary" icon={<LuCheck />} disabled={!canAccept || busy !== null}
                        loading={busy === `accept:${s.event_id}`} onClick={() => onDecide(r, "accept")}
                        title={canAccept ? undefined : canManage
                          ? "Only the owner can accept a step up to an automatic level"
                          : "Only owners and general managers can accept a suggestion"}>
                  Accept
                </Button>
              </div>
            </div>
          );
        })}
        {lowered.map(({ row, signal }) => (
          <div key={`d${signal.id}`} className="cx-row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
            <div style={{ minWidth: 0, flex: "1 1 260px" }}>
              <div className="cx-row" style={{ gap: 8 }}>
                <LuTrendingDown aria-hidden="true" />
                <strong>{signal.title}</strong>
              </div>
              <div className="cx-faint" style={{ fontSize: "0.82rem", marginTop: 4 }}>
                {signal.summary ? `${signal.summary} ` : ""}Lowered {fmt.relative(signal.first_seen_at)}. It stays at{" "}
                {AUTONOMY_LEVEL_LABELS[row.level]} until someone raises it again.
              </div>
            </div>
            <Button size="sm" variant="ghost" href={`#dial-${row.action_type}`}>Review</Button>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ─── One dial row ───

function AutonomyRow({
  slug, row, demotions, canManage, canRaiseAuto, onSaved,
}: {
  slug: string;
  row: AutonomySetting;
  demotions: readonly Signal[];
  canManage: boolean;
  canRaiseAuto: boolean;
  onSaved: (row: AutonomySetting) => void;
}) {
  const fmt = useFormat();
  const toast = useToast();
  const stored = useMemo(() => row.limits ?? {}, [row.limits]);
  const fields = useMemo(() => limitFields(row.limits_schema), [row.limits_schema]);

  // Initialised once per mount: the parent's key remounts the row whenever
  // the server copy changes.
  const [level, setLevel] = useState<AutonomyLevel>(row.level);
  const [draft, setDraft] = useState<LimitDraft>(() => draftFromLimits(fields, stored, fmt.currency));
  const [rawLimits, setRawLimits] = useState(() => (Object.keys(stored).length ? JSON.stringify(stored, null, 2) : ""));
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  // A type with no limits_model still has the generic check (every max_<field>
  // limit against params[<field>]), and without limits auto_within_limits
  // always falls back to approval. Offer the raw editor only when it matters.
  const showRawLimits = fields.length === 0 && (level === "auto_within_limits" || Object.keys(stored).length > 0);

  const parsed = useMemo(() => {
    if (fields.length) return limitsFromDraft(fields, draft, stored, fmt.currency);
    if (!showRawLimits) return { value: stored, errors: {} as Record<string, string> };
    const text = rawLimits.trim();
    if (!text) return { value: {}, errors: {} as Record<string, string> };
    try {
      const v: unknown = JSON.parse(text);
      if (!v || typeof v !== "object" || Array.isArray(v)) return { value: stored, errors: { _raw: "Limits must be a JSON object: { ... }" } };
      return { value: v as Record<string, unknown>, errors: {} as Record<string, string> };
    } catch (e) {
      return { value: stored, errors: { _raw: (e as Error).message } };
    }
  }, [fields, draft, stored, fmt.currency, showRawLimits, rawLimits]);

  const limitsChanged = !sameLimits(parsed.value, stored);
  const dirty = level !== row.level || limitsChanged;
  const hasErrors = Object.keys(parsed.errors).length > 0;
  // A manager may tighten limits at any level, auto ones included:
  // set_autonomy asks for raise_auto only when a PUT raises the level into an
  // auto level or loosens a limit, and a refused loosening is explained.
  const limitsLocked = !canManage || saving;
  const [showHistory, setShowHistory] = useState(false);
  const history = useApi<AutonomyEvent[]>(
    showHistory ? withQuery(hotelPath(slug, `/autonomy/${encodeURIComponent(row.action_type)}/events`), { limit: 20 }) : null,
    [slug, row.action_type],
  );
  const demoted = demotions[0];

  const revert = () => {
    setLevel(row.level);
    setDraft(draftFromLimits(fields, stored, fmt.currency));
    setRawLimits(Object.keys(stored).length ? JSON.stringify(stored, null, 2) : "");
    setReason("");
    setServerError(null);
  };

  const save = async (confirmed: boolean) => {
    if (!dirty || hasErrors) return;
    if (!confirmed && raisesToAuto(row.level, level)) {
      setConfirming(true);
      return;
    }
    setSaving(true);
    setServerError(null);
    try {
      const body: AutonomyUpdate = { level };
      // Limits are sent only when they changed: the server keeps the stored
      // ones otherwise, and re-validating them on a level-only change would
      // surface an unrelated error.
      if (limitsChanged) body.limits = parsed.value;
      if (reason.trim()) body.reason = reason.trim().slice(0, 500);
      const res = await api.put<AutonomySetting>(hotelPath(slug, `/autonomy/${encodeURIComponent(row.action_type)}`), body);
      setConfirming(false);
      toast.success(`${row.label} saved`, `Now ${AUTONOMY_LEVEL_LABELS[res.level]}, policy version ${res.policy_version}.`);
      onSaved(res);
    } catch (err) {
      const msg = explain(toApiError(err), { raisingToAuto: raisesToAuto(row.level, level), limitsChanged });
      setServerError(msg);
      setConfirming(false);
      toast.error("Couldn't save", msg);
    } finally {
      setSaving(false);
    }
  };

  const levelHint = [
    AUTONOMY_LEVEL_HINTS[level],
    row.always_approve ? "This action always needs a person." : `Highest allowed: ${AUTONOMY_LEVEL_LABELS[row.max_level]}.`,
  ].join(" ");

  return (
    <Card
      title={row.label}
      subtitle={<code className="cx-faint">{row.action_type}</code>}
      actions={
        <>
          {row.always_approve && <Badge tone="info">Always needs approval</Badge>}
          {demoted && <Badge tone="bad" dot title={demoted.title}>Lowered</Badge>}
          {row.open_suggestion && <Badge tone="gold" dot>Suggestion open</Badge>}
          <Badge tone={LEVEL_TONE[row.effective_level] ?? "neutral"}>{AUTONOMY_LEVEL_LABELS[row.effective_level] ?? row.effective_level}</Badge>
        </>
      }
    >
      <div className="cx-stack" style={{ gap: 14 }}>
        <div className="cx-form-grid cx-form-grid--2">
          <Field label="Level" hint={levelHint}>
            <Select value={level} disabled={!canManage || saving} options={levelOptions(row, canRaiseAuto)}
                    onChange={e => { setLevel(e.target.value as AutonomyLevel); setServerError(null); }}
                    aria-label={`${row.label} autonomy level`} />
          </Field>
          <div className="cx-stack" style={{ gap: 4, fontSize: "0.86rem" }}>
            <span className="cx-label">Last {row.stats.window_days} days</span>
            <span className="cx-num">
              {fmt.number(row.stats.decisions)} decisions by people, {fmt.pct(row.stats.approval_rate, { ratio: true })} approved unedited
            </span>
            <span className="cx-faint">
              {row.stats.meets_threshold ? "Meets the bar for a step up." : "A step up is suggested at 50 decisions and 95% unedited."}
            </span>
          </div>
        </div>

        {row.effective_level !== row.level && (
          <div className="cx-row cx-muted" style={{ gap: 8, fontSize: "0.84rem" }}>
            <LuShieldAlert aria-hidden="true" />
            Set to {AUTONOMY_LEVEL_LABELS[row.level]}, but {AUTONOMY_LEVEL_LABELS[row.effective_level]} applies: that is this action&apos;s ceiling.
          </div>
        )}

        {fields.length > 0 && (
          <div className="cx-stack" style={{ gap: 10 }}>
            <span className="cx-label">Limits{level !== "auto_within_limits" ? " (used at Auto within limits)" : ""}</span>
            <div className="cx-form-grid cx-form-grid--2">
              {fields.map(f => (
                <LimitInput key={f.key} field={f} value={draft[f.key]} error={parsed.errors[f.key]} disabled={limitsLocked}
                            currency={fmt.currency} onChange={v => { setDraft(d => ({ ...d, [f.key]: v })); setServerError(null); }} />
              ))}
            </div>
          </div>
        )}

        {showRawLimits && (
          <Field label="Limits (JSON)" error={parsed.errors._raw}
                 hint="Each max_<field> limit is compared with the action's own <field>. With no limits, Auto within limits always waits for approval.">
            <Textarea code rows={4} value={rawLimits} readOnly={limitsLocked} invalid={Boolean(parsed.errors._raw)}
                      onChange={e => { setRawLimits(e.target.value); setServerError(null); }} aria-label={`${row.label} limits JSON`} />
          </Field>
        )}

        {canManage && !canRaiseAuto && (fields.length > 0 || showRawLimits) && (
          <p className="cx-faint" style={{ margin: 0, fontSize: "0.82rem" }}>
            Only the owner can loosen a limit (a higher maximum, a lower minimum, more allowed). You can tighten limits
            or lower the level.
          </p>
        )}

        {canManage && dirty && (
          <Field label="Reason (optional)" hint="Kept with this policy version and in the audit log.">
            <Input value={reason} maxLength={500} disabled={saving} onChange={e => setReason(e.target.value)}
                   placeholder="e.g. Quotes have been right for a month" />
          </Field>
        )}

        {serverError && (
          <div role="alert" style={{ border: "1px solid rgba(var(--cx-bad-rgb), 0.34)", background: "rgba(var(--cx-bad-rgb), 0.08)", borderRadius: 12, padding: "10px 14px", fontSize: "0.84rem" }}>
            {serverError}
          </div>
        )}

        <div className="cx-row" style={{ justifyContent: "space-between", gap: 12 }}>
          <span className="cx-row cx-faint" style={{ fontSize: "0.8rem", gap: 8 }}>
            <span>
              {row.source === "hotel" ? `Policy v${row.policy_version}` : "Default setting"}
              {row.updated_at ? `, changed ${fmt.relative(row.updated_at)}` : ""}
              {row.reason ? `: "${row.reason}"` : ""}
            </span>
            <Button variant="ghost" size="sm" icon={<LuHistory />} aria-expanded={showHistory}
                    onClick={() => setShowHistory(v => !v)}>
              {showHistory ? "Hide history" : "History"}
            </Button>
          </span>
          {canManage && (
            <div className="cx-row" style={{ gap: 8 }}>
              <Button variant="ghost" size="sm" icon={<LuRotateCcw />} disabled={!dirty || saving} onClick={revert}>Revert</Button>
              <Button variant="primary" size="sm" loading={saving && !confirming} disabled={!dirty || hasErrors} onClick={() => save(false)}>
                Save
              </Button>
            </div>
          )}
        </div>

        {showHistory && <DialHistory state={history} />}
      </div>

      <Modal
        open={confirming}
        onClose={() => setConfirming(false)}
        dismissible={!saving}
        size="sm"
        title={`Let Clefs run "${row.label}" by itself?`}
        footer={
          <>
            <Button variant="ghost" disabled={saving} onClick={() => setConfirming(false)}>Cancel</Button>
            <Button variant="primary" loading={saving} onClick={() => save(true)}>Switch to {AUTONOMY_LEVEL_LABELS[level]}</Button>
          </>
        }
      >
        <div className="cx-stack" style={{ gap: 12, fontSize: "0.88rem", lineHeight: 1.55 }}>
          <p style={{ margin: 0 }}>
            At {AUTONOMY_LEVEL_LABELS[level]}, Clefs approves and runs these actions without asking anyone.
            {level === "auto_within_limits" && " Only actions inside the limits run by themselves; the rest wait for approval."}
          </p>
          <p className="cx-muted" style={{ margin: 0 }}>
            Every run is recorded with this policy version. A guest complaint, an opt-out spike, a template quality drop or an
            evaluation regression lowers the dial back to Approve on its own.
          </p>
          <Field label="Reason (optional)">
            <Input value={reason} maxLength={500} disabled={saving} onChange={e => setReason(e.target.value)} />
          </Field>
        </div>
      </Modal>
    </Card>
  );
}

// ─── A dial's history (04 spec 1.6) ───

function DialHistory({ state }: { state: UseApiResult<AutonomyEvent[]> }) {
  const fmt = useFormat();
  if (state.loading && !state.data) return <Skeleton lines={3} />;
  if (state.error && !state.data) {
    return <p className="cx-faint" style={{ margin: 0, fontSize: "0.82rem" }}>Couldn&apos;t load the history: {state.error.message}</p>;
  }
  const events = Array.isArray(state.data) ? state.data : [];
  if (!events.length) {
    return <p className="cx-faint" style={{ margin: 0, fontSize: "0.82rem" }}>No changes yet: this dial is on its default.</p>;
  }
  return (
    <ol className="cx-stack" style={{ gap: 6, margin: 0, paddingLeft: 18, fontSize: "0.84rem" }} aria-label="Dial history">
      {events.map(e => (
        <li key={e.id}>
          {describeAutonomyEvent(e)}
          <span className="cx-faint">
            {e.policy_version ? `, policy v${e.policy_version}` : ""}, {fmt.relative(e.at)}
          </span>
        </li>
      ))}
    </ol>
  );
}

// ─── One limits field ───

function LimitInput({
  field, value, error, disabled, currency, onChange,
}: {
  field: LimitField;
  value: string | boolean | undefined;
  error?: string;
  disabled: boolean;
  currency: string;
  onChange: (v: string | boolean) => void;
}) {
  if (field.kind === "boolean") {
    return (
      <label className="cx-row" style={{ gap: 8, fontSize: "0.88rem", alignSelf: "end" }}>
        <input type="checkbox" checked={value === true} disabled={disabled} onChange={e => onChange(e.target.checked)} />
        {field.label}
        {field.hint && <span className="cx-faint">({field.hint})</span>}
      </label>
    );
  }
  const text = typeof value === "string" ? value : "";
  const hint = [
    field.kind === "money" ? `In ${currency}.` : field.kind === "list" ? "Separate items with commas." : "",
    field.hint ?? "",
    field.nullable ? "Leave empty for no limit." : "",
  ].filter(Boolean).join(" ");
  if (field.kind === "enum") {
    return (
      <Field label={field.label} hint={hint || undefined} error={error}>
        <Select value={text} disabled={disabled} onChange={e => onChange(e.target.value)} invalid={Boolean(error)}
                placeholder={field.nullable ? "No limit" : undefined}
                options={(field.options ?? []).map(o => ({ value: o, label: labelForKey(o) }))} />
      </Field>
    );
  }
  if (field.kind === "json") {
    return (
      <Field label={field.label} hint={hint || undefined} error={error}>
        <Textarea code rows={3} value={text} readOnly={disabled} invalid={Boolean(error)} onChange={e => onChange(e.target.value)} />
      </Field>
    );
  }
  const numeric = field.kind === "money" || field.kind === "integer" || field.kind === "number";
  return (
    <Field label={field.kind === "money" ? `${field.label} (${currency})` : field.label} hint={hint || undefined} error={error}>
      <Input value={text} disabled={disabled} invalid={Boolean(error)} onChange={e => onChange(e.target.value)}
             inputMode={numeric ? (field.kind === "integer" ? "numeric" : "decimal") : undefined} />
    </Field>
  );
}
