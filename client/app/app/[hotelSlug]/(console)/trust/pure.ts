/* ═══════════════════════════════════════════════════════════════════════
   Trust pure helpers: no React, no runtime imports (only erased type
   imports), so node --test can load this file directly (see __tests__).
   ═══════════════════════════════════════════════════════════════════════ */

import type { Dsr, ReadinessShadow, ReadinessTests } from "./types";

// ─── Privacy request: who is asking ──────────────────────────────────────

export interface DsrPerson {
  /** Always a plain string, safe as a React child ("-" when nothing is known). */
  primary: string;
  /** A second, muted line (contact or room) when it adds something. */
  secondary: string | null;
}

/** Only non-blank strings (and plain numbers) count. WHY: the API payload is JSON we do not
    control at runtime (a masked field can come back as null, a boolean or,
    for identifiers_hint, a whole object); anything that is not text must
    never reach JSX, where an object crashes the page (React #31). */
function text(v: unknown): string | null {
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : null; // a room typed as 412
  if (typeof v !== "string") return null;
  const t = v.trim();
  return t ? t : null;
}

/** The queue's "Person" cell. Identifiers arrive already masked for the
    viewer's role, so they win; the intake hint (first name, masked contact,
    room) fills the gaps, e.g. when the sealed identifiers could not be
    opened. A legacy string hint is still accepted as a last resort. */
export function dsrPerson(r: Pick<Dsr, "identifiers" | "identifiers_hint">): DsrPerson {
  const ids = (r.identifiers && typeof r.identifiers === "object" ? r.identifiers : {}) as Record<string, unknown>;
  const rawHint: unknown = r.identifiers_hint;
  const hint = (rawHint && typeof rawHint === "object" ? rawHint : {}) as Record<string, unknown>;
  const legacy = text(rawHint);

  const name = text(ids.name) ?? text(hint.name);
  const contact = text(ids.email) ?? text(ids.phone) ?? text(hint.email) ?? text(hint.phone);
  const roomNo = text(ids.room) ?? text(hint.room);
  const room = roomNo ? `Room ${roomNo}` : null;

  const primary = name ?? contact ?? room ?? legacy ?? "-";
  const secondary = name ? (contact ?? room) : contact ? room : null;
  return { primary, secondary };
}

// ─── The go-live gate: 22 tests and the shadow week ──────────────────────


/** The headline of the 22 standard tests: "22/22 passed, judged by Gemini". */
export function gateTestsText(t: ReadinessTests | null | undefined): { value: string; sub: string; ok: boolean } {
  if (!t) return { value: "-", sub: "Run the tests to see the result.", ok: false };
  const n = t.expected || 22;
  const judge = t.judge_model ? `Judged by Gemini (${t.judge_model}).` : "Judged by Gemini.";
  if (t.run_id == null) return { value: "Not run yet", sub: `All ${n} must pass before going live.`, ok: false };
  if (t.total === 0) return { value: "Not in the last run", sub: `The last run left out the ${n} standard tests.`, ok: false };
  const ok = t.total === n && t.passed === n;
  return {
    value: `${t.passed}/${n} passed`,
    sub: ok ? `${judge} Safety, routing, languages, prices and policy.` : `${judge} All ${n} must pass before going live.`,
    ok,
  };
}

/** The shadow week: "7 of 7 days done" or "Day 3 of 7". */
export function shadowText(s: ReadinessShadow | null | undefined): { value: string; sub: string; ok: boolean } {
  if (!s) return { value: "-", sub: "7 days in shadow mode before going live.", ok: false };
  const n = s.days || 7;
  switch (s.state) {
    case "done":
      return { value: `${n} of ${n} days done`, sub: "Escalations were worked out and shown for a week before anyone was messaged.", ok: true };
    case "running":
      return { value: `Day ${s.day} of ${n}`, sub: "Escalations are worked out and shown; nobody is messaged yet.", ok: false };
    case "off":
      return { value: "Not needed", sub: "This hotel has no service desk to shadow.", ok: true };
    default:
      return { value: "Not started", sub: "The shadow week starts with the service desk's first job.", ok: false };
  }
}
