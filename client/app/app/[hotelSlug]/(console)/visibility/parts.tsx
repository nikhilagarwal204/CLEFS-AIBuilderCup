"use client";

/* Small pieces every visibility tab shares: the setup checklist (shown
   instead of an error when a tab lacks inputs, 04 spec 5.11), the vendor
   label line, the CI bar and the error text helper. */

import type { ReactNode } from "react";
import { ApiError } from "../../../../lib/api";
import { Badge, Button, Card, EmptyState, Skeleton } from "../../../../ui";
import { setupItems } from "./model";

// ─── Errors ──────────────────────────────────────────────────────────────

export function errText(err: unknown): string {
  if (err instanceof ApiError) return err.message || `Request failed (${err.status})`;
  return err instanceof Error ? err.message : "Something went wrong.";
}

/** Loading, error and empty handling for a tab's main read. */
export function TabState({ loading, error, onRetry, children }: {
  loading: boolean; error?: ApiError; onRetry: () => void; children: ReactNode;
}) {
  if (loading) return <Card><Skeleton lines={4} /></Card>;
  if (error) {
    return (
      <Card>
        <EmptyState title="Could not load this tab" body={errText(error)}
          action={<Button onClick={onRetry}>Try again</Button>} />
      </Card>
    );
  }
  return <>{children}</>;
}

// ─── Setup checklist ─────────────────────────────────────────────────────

export function SetupChecklist({ title, codes, onOpenSettings, intro }: {
  title: ReactNode; codes: readonly string[]; onOpenSettings?: () => void; intro?: ReactNode;
}) {
  const items = setupItems(codes);
  if (!items.length) return null;
  const fixable = items.some(i => i.where === "settings");
  return (
    <Card title={title} subtitle={intro ?? "Finish these and the numbers appear here."}
      actions={fixable && onOpenSettings ? <Button size="sm" onClick={onOpenSettings}>Open settings</Button> : undefined}>
      <ul className="vis-setup">
        {items.map(i => (
          <li key={i.code}>
            <span className="vis-setup__box" aria-hidden="true" />
            <div>
              <strong>{i.label}</strong>{" "}
              {i.where === "platform" && <Badge tone="neutral">Clefs side</Badge>}
              {i.hint && <span>{i.hint}</span>}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

// ─── Labels and intervals ────────────────────────────────────────────────

/** The source label every vendor-derived number carries ("API simulation...",
    "via SerpApi (third-party); indicative"). */
export function SourceLabel({ children }: { children: ReactNode }) {
  return <div className="vis-label"><Badge tone="info">Label</Badge><span>{children}</span></div>;
}

/** A 0..1 point estimate with its interval, drawn on a 0-100% track. */
export function CiBar({ rate, low, high, label }: { rate: number; low: number | null; high: number | null; label: string }) {
  const clamp = (v: number) => Math.max(0, Math.min(100, v * 100));
  return (
    <div className="vis-ci" role="img" aria-label={label}>
      {low !== null && high !== null && (
        <span className="vis-ci__band" style={{ left: `${clamp(low)}%`, width: `${Math.max(1, clamp(high) - clamp(low))}%` }} />
      )}
      <span className="vis-ci__dot" style={{ left: `${clamp(rate)}%` }} />
    </div>
  );
}
