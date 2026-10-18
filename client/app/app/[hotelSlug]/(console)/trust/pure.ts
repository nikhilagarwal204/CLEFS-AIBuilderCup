/* ═══════════════════════════════════════════════════════════════════════
   Trust pure helpers: no React, no runtime imports (only erased type
   imports), so node --test can load this file directly (see __tests__).
   ═══════════════════════════════════════════════════════════════════════ */

import type { Dsr } from "./types";

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
