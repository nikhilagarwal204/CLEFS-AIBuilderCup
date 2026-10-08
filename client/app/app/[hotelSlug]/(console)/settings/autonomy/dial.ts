/* ═══════════════════════════════════════════════════════════════════════
   Autonomy dial helpers (not a route: no page.tsx). Pure functions, so the
   page stays about layout and the rules live in one readable place.

   The server is the authority on every rule here (clefs.core.actions:
   set_autonomy refuses a level above the ceiling with 422 above_ceiling, and
   raising into an auto level or loosening a limit without
   core.autonomy.raise_auto with 403; accept_suggestion needs raise_auto only
   above approve). The client only mirrors them so a person is never offered
   a choice that will bounce.
   ═══════════════════════════════════════════════════════════════════════ */

import { currencyDigits, minorToMajor } from "../../../../../lib/format";
import {
  AUTO_AUTONOMY_LEVELS, AUTONOMY_DEMOTED_KIND, AUTONOMY_LEVEL_LABELS, AUTONOMY_LEVELS, autonomyRank,
  type AutonomyEvent, type AutonomyLevel, type AutonomySetting, type DemotionTrigger, type JsonSchemaProperty,
  type LimitsSchema, type Signal,
} from "../../../../../lib/types";
import { joinList, splitList } from "../shared";

// ─── Levels ───

export interface LevelOption {
  value: AutonomyLevel;
  label: string;
  disabled: boolean;
}

/** The level select for one dial row. Levels above the type's ceiling are
 *  never offered (a refund is never automatic, Canon 17). Without raise_auto
 *  only a RAISE into an auto level is closed, exactly the set_autonomy rule
 *  (raising and level in AUTO_LEVELS): the current level stays selectable so
 *  a manager still sees what is set, and every level below it stays open,
 *  Auto -> Auto within limits included, because a GM can always make a dial
 *  safer. Blocking by "is an auto level" alone would hide that lowering and
 *  mislabel it owner-only. */
export function levelOptions(row: Pick<AutonomySetting, "level" | "max_level">, canRaiseAuto: boolean): LevelOption[] {
  const ceiling = autonomyRank(row.max_level);
  return AUTONOMY_LEVELS.map(level => {
    const aboveCeiling = autonomyRank(level) > ceiling;
    const needsOwner = !canRaiseAuto && raisesToAuto(row.level, level);
    const suffix = aboveCeiling ? " (above this action's ceiling)" : needsOwner ? " (owner only)" : "";
    return { value: level, label: AUTONOMY_LEVEL_LABELS[level] + suffix, disabled: aboveCeiling || needsOwner };
  });
}

/** True when saving `to` lets Clefs act with no person where it could not
 *  before: worth an explicit confirmation. */
export function raisesToAuto(from: AutonomyLevel, to: AutonomyLevel): boolean {
  return AUTO_AUTONOMY_LEVELS.has(to) && autonomyRank(to) > autonomyRank(from);
}

export type AutonomyPerm = "core.autonomy.manage" | "core.autonomy.raise_auto";

/** The permission accepting a promotion to `toLevel` needs, exactly as
 *  clefs.core.actions.accept_perm_for (04 spec 1.5): manage up to approve,
 *  raise_auto above it. */
export function acceptPermFor(toLevel: AutonomyLevel): AutonomyPerm {
  return autonomyRank(toLevel) > autonomyRank("approve") ? "core.autonomy.raise_auto" : "core.autonomy.manage";
}

/** Whether this person may press Accept. The route itself needs manage;
 *  a step above approve needs raise_auto as well. */
export function canAcceptSuggestion(toLevel: AutonomyLevel, canManage: boolean, canRaiseAuto: boolean): boolean {
  return canManage && (acceptPermFor(toLevel) === "core.autonomy.manage" || canRaiseAuto);
}

// ─── Limits form, generated from the type's limits_model JSON Schema ───

export type LimitKind = "integer" | "number" | "money" | "boolean" | "enum" | "string" | "list" | "json";

export interface LimitField {
  key: string;
  label: string;
  hint?: string;
  kind: LimitKind;
  /** Optional in the model (anyOf [..., {type: "null"}]): an empty input sends null. */
  nullable: boolean;
  /** Integer items for a list field. */
  integerItems?: boolean;
  options?: string[];
  minimum?: number;
  maximum?: number;
}

type SchemaWithDefs = LimitsSchema & { $defs?: Record<string, JsonSchemaProperty> };
type RefProperty = JsonSchemaProperty & { $ref?: string; allOf?: (JsonSchemaProperty & { $ref?: string })[] };

function deref(schema: SchemaWithDefs, p: RefProperty): JsonSchemaProperty {
  // Pydantic puts enums and nested models in $defs and points at them, and
  // wraps a $ref that carries a default in allOf.
  const ref = p.$ref ?? p.allOf?.find(x => x.$ref)?.$ref;
  if (!ref) return p;
  const name = ref.split("/").pop() ?? "";
  const target = schema.$defs?.[name];
  return target ? { ...target, title: p.title ?? target.title, description: p.description ?? target.description, default: p.default ?? target.default } : p;
}

/** "max_offer_price_minor" -> "Max offer price" (the Pydantic title would say "Max Offer Price Minor"). */
export function labelForKey(key: string): string {
  const words = key.replace(/_minor$/, "").split("_").filter(Boolean).join(" ");
  return words ? words[0].toUpperCase() + words.slice(1) : key;
}

export function limitFields(schema: LimitsSchema | null | undefined): LimitField[] {
  if (!schema?.properties) return [];
  const s = schema as SchemaWithDefs;
  return Object.entries(schema.properties).map(([key, raw]) => {
    let p = deref(s, raw as RefProperty);
    let nullable = false;
    if (p.anyOf?.length) {
      const nonNull = p.anyOf.filter(x => x.type !== "null");
      nullable = nonNull.length < p.anyOf.length;
      if (nonNull.length === 1) p = { ...deref(s, nonNull[0] as RefProperty), title: p.title, description: p.description, default: p.default };
    }
    const base = { key, label: labelForKey(key), hint: p.description, nullable, minimum: p.minimum, maximum: p.maximum };
    if (p.enum?.length) return { ...base, kind: "enum", options: p.enum.map(String) };
    switch (p.type) {
      case "integer":
        // *_minor limits are money in minor units (Canon 2): edit them in major units.
        return { ...base, kind: key.endsWith("_minor") ? "money" : "integer" };
      case "number":
        return { ...base, kind: "number" };
      case "boolean":
        return { ...base, kind: "boolean" };
      case "string":
        return { ...base, kind: "string" };
      case "array":
        if (!p.items || p.items.type === "string" || p.items.type === "integer") {
          return { ...base, kind: "list", integerItems: p.items?.type === "integer" };
        }
        return { ...base, kind: "json" };
      default:
        return { ...base, kind: "json" };
    }
  });
}

/** Form state: text for inputs, booleans for checkboxes. */
export type LimitDraft = Record<string, string | boolean>;

export function draftFromLimits(fields: readonly LimitField[], limits: Record<string, unknown>, currency: string): LimitDraft {
  const out: LimitDraft = {};
  for (const f of fields) {
    const v = limits[f.key];
    switch (f.kind) {
      case "boolean":
        out[f.key] = v === true;
        break;
      case "money":
        out[f.key] = typeof v === "number" || typeof v === "string" ? minorToMajor(v, currency) : "";
        break;
      case "list":
        out[f.key] = Array.isArray(v) ? joinList(v.map(String)) : "";
        break;
      case "json":
        out[f.key] = v === undefined || v === null ? "" : JSON.stringify(v, null, 2);
        break;
      default:
        out[f.key] = v === undefined || v === null ? "" : String(v);
    }
  }
  return out;
}

/** "250" / "250.5" / "250.50" -> 25050 fils, by string shifting (never float
 *  maths, Canon 2). null when the text is not a plain non-negative amount or
 *  has more decimals than the currency allows. */
export function majorToMinor(text: string, digits: number): number | null {
  const m = /^(\d+)(?:\.(\d*))?$/.exec(text.trim().replace(/,/g, ""));
  if (!m) return null;
  const frac = m[2] ?? "";
  if (frac.length > digits) return null;
  const minor = Number(m[1] + frac.padEnd(digits, "0"));
  return Number.isSafeInteger(minor) ? minor : null;
}

export interface LimitsParse {
  value: Record<string, unknown>;
  errors: Record<string, string>;
}

/** Build the limits body. Starts from the stored limits so keys the form
 *  does not model survive; a cleared optional field is sent as null, and a
 *  cleared required one is dropped so the model's default applies. */
export function limitsFromDraft(
  fields: readonly LimitField[], draft: LimitDraft, stored: Record<string, unknown>, currency: string,
): LimitsParse {
  const value: Record<string, unknown> = { ...stored };
  const errors: Record<string, string> = {};
  const inRange = (f: LimitField, n: number) => {
    if (f.minimum !== undefined && n < f.minimum) errors[f.key] = `At least ${f.minimum}.`;
    if (f.maximum !== undefined && n > f.maximum) errors[f.key] = `At most ${f.maximum}.`;
  };
  for (const f of fields) {
    const raw = draft[f.key];
    if (f.kind === "boolean") {
      value[f.key] = raw === true;
      continue;
    }
    const text = typeof raw === "string" ? raw.trim() : "";
    if (!text) {
      if (f.nullable) value[f.key] = null;
      else delete value[f.key];
      continue;
    }
    switch (f.kind) {
      case "money": {
        const minor = majorToMinor(text, currencyDigits(currency));
        if (minor === null) errors[f.key] = `Enter an amount in ${currency}, e.g. 250 or 250.50.`;
        else {
          value[f.key] = minor;
          inRange(f, minor);
        }
        break;
      }
      case "integer":
        if (!/^-?\d+$/.test(text)) errors[f.key] = "Enter a whole number.";
        else {
          value[f.key] = Number(text);
          inRange(f, Number(text));
        }
        break;
      case "number":
        if (!Number.isFinite(Number(text))) errors[f.key] = "Enter a number.";
        else {
          value[f.key] = Number(text);
          inRange(f, Number(text));
        }
        break;
      case "list": {
        const items = splitList(text);
        if (f.integerItems) {
          if (items.some(x => !/^-?\d+$/.test(x))) errors[f.key] = "Whole numbers, separated by commas.";
          else value[f.key] = items.map(Number);
        } else value[f.key] = items;
        break;
      }
      case "json":
        try {
          value[f.key] = JSON.parse(text);
        } catch (e) {
          errors[f.key] = (e as Error).message;
        }
        break;
      default:
        value[f.key] = text;
    }
  }
  return { value, errors };
}

/** Order-insensitive comparison of two limits documents. */
export function sameLimits(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const norm = (o: Record<string, unknown>) =>
    JSON.stringify(Object.keys(o).sort().map(k => [k, o[k] === undefined ? null : o[k]]));
  return norm(a) === norm(b);
}

// ─── Demotions (core.autonomy_demoted signals) ───

/** The action type a core.autonomy_* signal is about. Its dedupe_key is
 *  "<kind>:<action_type>:<event_id>" (actions._signal_key); action type keys
 *  never contain a colon, but the event id is split from the right anyway. */
export function actionTypeOfAutonomySignal(sig: Pick<Signal, "kind" | "dedupe_key">): string | null {
  const prefix = `${sig.kind}:`;
  if (!sig.dedupe_key.startsWith(prefix)) return null;
  const rest = sig.dedupe_key.slice(prefix.length);
  const cut = rest.lastIndexOf(":");
  return cut > 0 ? rest.slice(0, cut) : null;
}

// ─── History (GET /autonomy/{action_type}/events) ───

const TRIGGER_TEXT: Record<DemotionTrigger, string> = {
  guest_complaint: "after a guest complaint",
  optout_spike: "after an opt-out spike",
  meta_quality: "after a template quality drop",
  eval_regression: "after an evaluation regression",
  manual: "by hand",
};

const SUGGESTION_STATUS_TEXT: Record<string, string> = {
  open: "open",
  accepted: "accepted",
  dismissed: "dismissed",
  superseded: "overtaken by a later change",
};

/** One readable line per dial event, e.g. "Changed from Suggest to Approve". */
export function describeAutonomyEvent(
  e: Pick<AutonomyEvent, "kind" | "from_level" | "to_level" | "trigger" | "status">,
): string {
  const label = (l: AutonomyLevel | null) => (l ? AUTONOMY_LEVEL_LABELS[l] ?? l : "?");
  switch (e.kind) {
    case "seed":
      // Logged by the dial's first change (04 spec 1.4): the default it left.
      return `Started at the default, ${label(e.to_level)}`;
    case "set":
      return e.from_level === e.to_level ? `Limits changed at ${label(e.to_level)}` : `Changed from ${label(e.from_level)} to ${label(e.to_level)}`;
    case "promotion_suggested":
      return `Suggested a step up to ${label(e.to_level)}` + (e.status ? ` (${SUGGESTION_STATUS_TEXT[e.status] ?? e.status})` : "");
    case "promotion_accepted":
      return `Step up to ${label(e.to_level)} accepted`;
    case "promotion_dismissed":
      return "Suggested step up dismissed";
    case "demoted":
      return `Lowered from ${label(e.from_level)} to ${label(e.to_level)}` + (e.trigger ? ` ${TRIGGER_TEXT[e.trigger] ?? e.trigger}` : "");
    default:
      return String(e.kind);
  }
}

/** Open demotion notices, keyed by action type (newest first per type). */
export function demotionsByType(signals: readonly Signal[]): Map<string, Signal[]> {
  const out = new Map<string, Signal[]>();
  for (const s of signals) {
    if (s.kind !== AUTONOMY_DEMOTED_KIND || (s.status !== "open" && s.status !== "surfaced")) continue;
    const type = actionTypeOfAutonomySignal(s);
    if (!type) continue;
    out.set(type, [...(out.get(type) ?? []), s].sort((a, b) => b.id - a.id));
  }
  return out;
}
