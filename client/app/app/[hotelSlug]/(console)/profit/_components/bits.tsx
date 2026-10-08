"use client";

/* Small shared pieces for the Profit Guard pages: money in minor units,
   status and class badges, the error note, and the write-permission check
   every page uses to hide buttons a person cannot press. */

import type { ReactNode } from "react";
import { ApiError } from "../../../../../lib/api";
import { useSession } from "../../../../../lib/session";
import { Badge, Card, EmptyState, MoneyText, Skeleton } from "../../../../../ui";
import { CLASS_LABEL, CLASS_TONE, PACKET_STATUS_LABEL, PACKET_STATUS_TONE, STATUS_LABEL, STATUS_TONE } from "../lib";
import type { FindingClass, FindingStatus, PacketStatus } from "../types";

export const MANAGE_PERM = "profit.manage";

/** True when the viewer may change findings, packets, cards and evidence. */
export function useCanManage(): boolean {
  const { can } = useSession();
  return can(MANAGE_PERM);
}

/** Integer minor units in a currency (defaults to the hotel's). */
export function Money({
  minor, currency, compact = false, signed = false, tone = "none",
}: {
  minor: number | null | undefined;
  currency?: string | null;
  compact?: boolean;
  signed?: boolean;
  tone?: "none" | "auto";
}) {
  if (minor === null || minor === undefined) return <span className="cx-faint">n/a</span>;
  return <MoneyText amount={minor} minor currency={currency} compact={compact} signed={signed} tone={tone} className="pg-num" />;
}

export function StatusBadge({ status }: { status: FindingStatus }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"} dot>{STATUS_LABEL[status] ?? status}</Badge>;
}

export function ClassBadge({ klass }: { klass: FindingClass }) {
  return <Badge tone={CLASS_TONE[klass] ?? "neutral"}>{CLASS_LABEL[klass] ?? klass}</Badge>;
}

export function PacketBadge({ status }: { status: PacketStatus }) {
  return <Badge tone={PACKET_STATUS_TONE[status] ?? "neutral"} dot>{PACKET_STATUS_LABEL[status] ?? status}</Badge>;
}

/** One readable sentence for an API failure. A 404 on a Profit Guard route
    usually means the module's routes are not mounted yet on this server. */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return "You do not have access to this. Ask the owner for Profit Guard access.";
    if (err.status === 404 && err.code === undefined) return "This part of Profit Guard is not available on this server yet.";
    return err.message || `Request failed (${err.status}).`;
  }
  return err instanceof Error ? err.message : "Something went wrong.";
}

export function ErrorNote({ error, children }: { error: unknown; children?: ReactNode }) {
  if (!error) return null;
  return (
    <div className="pg-error" role="alert">
      {errorText(error)}
      {children}
    </div>
  );
}

/** Loading, error or empty, in one place, so every card behaves the same. */
export function LoadState({
  loading, error, empty, emptyTitle, emptyBody, lines = 3, children,
}: {
  loading: boolean;
  error?: unknown;
  empty?: boolean;
  emptyTitle?: ReactNode;
  emptyBody?: ReactNode;
  lines?: number;
  children?: ReactNode;
}) {
  if (loading) return <Skeleton lines={lines} />;
  if (error) return <ErrorNote error={error} />;
  if (empty) return <EmptyState title={emptyTitle ?? "Nothing here yet."} body={emptyBody} />;
  return <>{children}</>;
}

/** A Card whose body is a LoadState. */
export function DataCard({
  title, subtitle, actions, gold, ...state
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  gold?: boolean;
} & Parameters<typeof LoadState>[0]) {
  return (
    <Card title={title} subtitle={subtitle} actions={actions} gold={gold}>
      <LoadState {...state} />
    </Card>
  );
}
