"use client";

/* Small shared pieces for the finance pages: money that says "n/a" instead
   of a dash or zero, API error copy, and the OAuth-return toast every page
   the core callback can land on shares. */

import { useEffect, type ReactNode } from "react";
import { ApiError } from "../../../../../lib/api";
import { Card, EmptyState, MoneyText, Skeleton, useToast } from "../../../../../ui";
import { codeText, NA, num, PROVIDER_LABELS, type OAuthReturn } from "../lib";
import type { Dec } from "../types";

// ─── Money and n/a ───────────────────────────────────────────────────────

/** A major-unit Decimal string as money; "n/a" when the figure has no
 *  denominator (6.1), which is not the same as zero. */
export function Money({ value, currency, compact = false, signed = false }: {
  value: Dec | undefined;
  currency?: string | null;
  compact?: boolean;
  signed?: boolean;
}) {
  if (num(value) === null) return <span className="cx-faint" title="Not available: no data or a zero denominator">{NA}</span>;
  return <MoneyText amount={value} currency={currency} compact={compact} signed={signed} />;
}

// ─── Errors ──────────────────────────────────────────────────────────────

export function errText(err: unknown): string {
  if (err instanceof ApiError) return codeText(err.code, err.message);
  return err instanceof Error ? err.message : String(err);
}

/** A loading, error or content switch for a card body. */
export function Loadable({ loading, error, children, lines = 3 }: {
  loading: boolean;
  error?: unknown;
  children: ReactNode;
  lines?: number;
}) {
  if (loading) return <Skeleton lines={lines} />;
  if (error) {
    return (
      <Card>
        <EmptyState title="Could not load this" body={errText(error)} />
      </Card>
    );
  }
  return <>{children}</>;
}

// ─── OAuth return ────────────────────────────────────────────────────────

/** One toast for the OAuth round trip, then the params leave the URL so a
 *  reload does not repeat it. history.replaceState keeps the Next router
 *  state intact (no navigation, no refetch). */
export function useOAuthReturnToast(ret: OAuthReturn | undefined, onDone?: () => void) {
  const toast = useToast();
  useEffect(() => {
    if (!ret) return;
    const name = PROVIDER_LABELS[ret.provider] ?? ret.provider;
    if (ret.status === "connected") toast.success(`${name} connected`, "Sync a month below, or wait for the monthly sync on day 3.");
    else if (ret.status === "denied") toast.info(`${name} not connected`, "The permission request was declined.");
    else toast.error(`${name} connection failed`, ret.error ? `Reason: ${ret.error}` : undefined);
    try {
      const url = new URL(window.location.href);
      ["oauth", "oauth_status", "oauth_error"].forEach(k => url.searchParams.delete(k));
      window.history.replaceState(window.history.state, "", url.toString());
    } catch { /* cosmetic only */ }
    onDone?.();
    // Once per mount: the params are consumed on first render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
