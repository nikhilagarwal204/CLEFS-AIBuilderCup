/* ═══════════════════════════════════════════════════════════════════════
   Ask Clefs console helpers: API paths, permissions, error wording and the
   starter questions. No React state, so every Ask component imports it
   cheaply. The pure formatting and chart mapping live in ./pure (tested
   under node --test without a DOM).
   ═══════════════════════════════════════════════════════════════════════ */

import { ApiError, hotelPath } from "../../../../lib/api";

export * from "./pure";

// ─── Paths and permissions ───────────────────────────────────────────────

/** API path under /hotels/{slug}/ask. */
export const askApi = (slug: string, p = "") => hotelPath(slug, `/ask${p}`);

/** Console route under /app/{slug}/ask. */
export const askHref = (slug: string, q = "") => `/app/${encodeURIComponent(slug)}/ask${q}`;

export const PERM = { view: "ask.view", manage: "ask.manage" } as const;

/** The server refuses anything over 500 characters (guardrails); the box
    allows a little more so a paste is not cut silently, and says so. */
export const MAX_QUESTION = 500;

// ─── Errors ──────────────────────────────────────────────────────────────

/** One readable sentence for a failed Ask call. The server's own detail is
    already plain English; these cover the codes worth rewording. */
export function askErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.code) {
      case "rate_limited":
        return "That is the limit of 30 questions an hour. Saved views still run; try again shortly.";
      case "invalid_view_query":
        return "This saved view no longer fits your metrics or role. Ask the question again and save the new answer.";
      case "ask_view_not_found":
        return "That saved view was deleted.";
      case "ask_log_not_found":
        return "That answer is no longer available.";
      case "ask_whatsapp_role":
        return "WhatsApp questions are for owners, GMs and finance.";
      case "ask_whatsapp_no_phone":
        return "Add a mobile number to your profile first; the answers go there.";
      default:
        if (err.status === 403) return typeof err.detail === "string" ? err.detail : "You do not have access to this.";
        return err.message;
    }
  }
  return err instanceof Error ? err.message : "Something went wrong.";
}

// ─── Browser helpers ─────────────────────────────────────────────────────

/** Save text as a file (the table's CSV). A BOM keeps Excel on UTF-8. */
export function downloadText(filename: string, text: string, type = "text/csv;charset=utf-8") {
  if (typeof window === "undefined") return;
  const url = URL.createObjectURL(new Blob(["﻿", text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function csvFilename(title: string): string {
  const base = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  return `${base || "ask-clefs"}.csv`;
}
