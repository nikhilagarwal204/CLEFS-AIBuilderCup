"use client";

/* ═══════════════════════════════════════════════════════════════════════
   One decide() door for every brief surface in the console. Decisions go
   to the foundation's POST /hotels/{slug}/actions/{id}/decide (Canon 7 +
   K1, the autonomy dial, role permissions): Brief has no state machine of
   its own. The hook tracks which action and verb is in flight (so only
   that card's button spins), words refusals, and hands the updated action
   back so the caller can patch its list without a refetch.
   ═══════════════════════════════════════════════════════════════════════ */

import { useCallback, useState } from "react";
import { ApiError, api } from "../../../../lib/api";
import type { Action, DecideBody, Decision } from "../../../../lib/types";
import { useToast } from "../../../../ui";
import { actionApi, decideError } from "./lib";

const DONE_TEXT: Record<Decision, string> = {
  approve: "Approved",
  adjust: "Adjusted and approved",
  assign: "Assigned",
  dispute: "Dispute submitted",
  remind: "Reminder sent",
  ignore: "Ignored",
  snooze: "Snoozed",
};

export interface DecideState {
  busy: { id: number; decision: Decision } | null;
  decide(id: number, decision: Decision, extra?: Omit<DecideBody, "decision">): Promise<Action | null>;
}

export function useDecide(slug: string, onChanged?: (a: Action) => void, onStale?: (id: number) => void): DecideState {
  const toast = useToast();
  const [busy, setBusy] = useState<DecideState["busy"]>(null);

  const decide = useCallback(async (id: number, decision: Decision, extra?: Omit<DecideBody, "decision">) => {
    setBusy({ id, decision });
    try {
      const a = await api.post<Action>(actionApi(slug, id, "/decide"), { decision, ...extra });
      toast.success(DONE_TEXT[decision], a.title);
      onChanged?.(a);
      return a;
    } catch (err) {
      toast.error("Not done", decideError(err));
      // Someone else moved it on (another owner, WhatsApp, the dial): the
      // card is stale, so re-read it rather than leave a button that will fail.
      if (err instanceof ApiError && (err.code === "illegal_transition" || err.code === "action_expired")) onStale?.(id);
      return null;
    } finally {
      setBusy(null);
    }
  }, [slug, toast, onChanged, onStale]);

  return { busy, decide };
}
