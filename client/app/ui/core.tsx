"use client";

/* ═══════════════════════════════════════════════════════════════════════
   UI kit: layout and content primitives.
   All styling lives in ./styles.ts (cx- prefixed); components only compose
   class names so modules can restyle through their own prefixed CSS.
   ═══════════════════════════════════════════════════════════════════════ */

import Link from "next/link";
import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from "react";
import { uiStyles } from "./styles";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** Injects the kit stylesheet. Render once near the root of a surface. */
export function UiStyles() {
  return <style dangerouslySetInnerHTML={{ __html: uiStyles }} />;
}

// ─── Card ────────────────────────────────────────────────────────────────

export function Card({
  title, subtitle, actions, children, className, padded = true, gold = false, as: Tag = "section", style,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  padded?: boolean;
  /** Gold hairline on top, for the one card on a page that matters most. */
  gold?: boolean;
  as?: "section" | "div" | "article";
  style?: CSSProperties;
}) {
  return (
    <Tag className={cx("cx-card", padded && "cx-card--pad", gold && "cx-card--gold", className)} style={style}>
      {(title || actions) && (
        <div className="cx-card__head">
          <div style={{ minWidth: 0 }}>
            {title && <h3 className="cx-card__title">{title}</h3>}
            {subtitle && <div className="cx-card__sub">{subtitle}</div>}
          </div>
          {actions && <div className="cx-row" style={{ gap: 6 }}>{actions}</div>}
        </div>
      )}
      {children}
    </Tag>
  );
}

// ─── StatTile ────────────────────────────────────────────────────────────

export interface StatDelta {
  /** Pre-formatted, e.g. "+12%" or "AED 4,200". */
  value: string;
  direction: "up" | "down" | "flat";
  /** Whether this direction is good news (drives colour; arrows carry direction). */
  good?: boolean;
  label?: string;
}

export function StatTile({
  label, value, sub, delta, sparkline, className, loading = false,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  delta?: StatDelta;
  /** Slot for <Sparkline/> (charts-extra) or any small inline figure. */
  sparkline?: ReactNode;
  className?: string;
  loading?: boolean;
}) {
  const arrow = delta?.direction === "up" ? "▲" : delta?.direction === "down" ? "▼" : "→";
  const tone = !delta || delta.direction === "flat" || delta.good === undefined ? "flat" : delta.good ? "good" : "bad";
  return (
    <Card className={cx("cx-stat", className)}>
      <div className="cx-stat__label">{label}</div>
      <div className="cx-stat__body">
        <div className="cx-stat__value">{loading ? <Skeleton width={96} height={28} /> : value}</div>
        {sparkline && <div className="cx-stat__spark">{sparkline}</div>}
      </div>
      {(delta || sub) && (
        <div className="cx-stat__foot">
          {delta && (
            <span className={`cx-delta cx-delta--${tone}`}>
              <span aria-hidden="true">{arrow}</span> {delta.value}
              {delta.label && <span className="cx-faint" style={{ fontWeight: 400 }}> {delta.label}</span>}
            </span>
          )}
          {sub && <span>{sub}</span>}
        </div>
      )}
    </Card>
  );
}

// ─── Badge ───────────────────────────────────────────────────────────────

export type Tone = "neutral" | "gold" | "ok" | "warn" | "bad" | "info";

export function Badge({
  tone = "neutral", dot = false, children, title, className,
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span className={cx("cx-badge", `cx-badge--${tone}`, className)} title={title}>
      {dot && <span className="cx-badge__dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

// ─── Button ──────────────────────────────────────────────────────────────

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  loading?: boolean;
  icon?: ReactNode;
  block?: boolean;
  /** Render as a Next <Link> instead of a <button>. */
  href?: string;
}

export function Button({
  variant = "secondary", size = "md", loading = false, icon, block = false, href,
  className, children, disabled, type = "button", ...rest
}: ButtonProps) {
  const classes = cx(
    "cx-btn", `cx-btn--${variant}`, size === "sm" && "cx-btn--sm", block && "cx-btn--block",
    !children && Boolean(icon) && "cx-btn--icon", className,
  );
  const inner = (
    <>
      {loading ? <span className="cx-spinner" aria-hidden="true" /> : icon}
      {children}
    </>
  );
  if (href && !disabled) {
    return (
      <Link href={href} className={classes} title={rest.title} aria-label={rest["aria-label"]} style={rest.style}>
        {inner}
      </Link>
    );
  }
  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {inner}
    </button>
  );
}

// ─── EmptyState / Skeleton ───────────────────────────────────────────────

export function EmptyState({
  icon, title, body, action, className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  body?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("cx-empty", className)}>
      {icon && <div className="cx-empty__icon" aria-hidden="true">{icon}</div>}
      <h3 className="cx-empty__title">{title}</h3>
      {body && <div className="cx-empty__body">{body}</div>}
      {action && <div className="cx-empty__action">{action}</div>}
    </div>
  );
}

export function Skeleton({
  width = "100%", height = 14, radius = 8, lines = 1, gap = 8, style,
}: {
  width?: number | string;
  height?: number | string;
  radius?: number;
  lines?: number;
  gap?: number;
  style?: CSSProperties;
}) {
  if (lines > 1) {
    return (
      <span style={{ display: "flex", flexDirection: "column", gap, ...style }} aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => (
          <span key={i} className="cx-skel" style={{ width: i === lines - 1 ? "60%" : width, height, borderRadius: radius }} />
        ))}
      </span>
    );
  }
  return <span className="cx-skel" aria-hidden="true" style={{ width, height, borderRadius: radius, ...style }} />;
}

// ─── PageHeader ──────────────────────────────────────────────────────────

export function PageHeader({
  title, eyebrow, subtitle, actions,
}: {
  title: ReactNode;
  eyebrow?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="cx-page-head">
      <div style={{ minWidth: 0 }}>
        {eyebrow && <div className="cx-page-head__eyebrow">{eyebrow}</div>}
        <h1 className="cx-page-head__title">{title}</h1>
        {subtitle && <p className="cx-page-head__sub">{subtitle}</p>}
      </div>
      {actions && <div className="cx-page-head__actions">{actions}</div>}
    </header>
  );
}

// ─── Tabs ────────────────────────────────────────────────────────────────

export interface TabItem<K extends string = string> {
  key: K;
  label: ReactNode;
  /** When set, the tab is a link (route-driven tabs such as settings). */
  href?: string;
  count?: number;
  disabled?: boolean;
}

export function Tabs<K extends string>({
  tabs, value, onChange, ariaLabel,
}: {
  tabs: readonly TabItem<K>[];
  value: K;
  onChange?: (key: K) => void;
  ariaLabel?: string;
}) {
  return (
    <div className="cx-tabs" role="tablist" aria-label={ariaLabel}>
      {tabs.map(t => {
        const selected = t.key === value;
        const body = (
          <>
            {t.label}
            {t.count !== undefined && <span className="cx-tab__count">{t.count}</span>}
          </>
        );
        if (t.href) {
          return (
            <Link key={t.key} href={t.href} className="cx-tab" role="tab" aria-selected={selected}
                  aria-current={selected ? "page" : undefined}>
              {body}
            </Link>
          );
        }
        return (
          <button key={t.key} type="button" className="cx-tab" role="tab" aria-selected={selected}
                  disabled={t.disabled} onClick={() => onChange?.(t.key)}>
            {body}
          </button>
        );
      })}
    </div>
  );
}

// ─── Segmented ───────────────────────────────────────────────────────────

export function Segmented<V extends string | number>({
  options, value, onChange, size = "md", ariaLabel,
}: {
  options: readonly { value: V; label: ReactNode; disabled?: boolean }[];
  value: V;
  onChange: (v: V) => void;
  size?: "sm" | "md";
  ariaLabel?: string;
}) {
  return (
    <div className={cx("cx-seg", size === "sm" && "cx-seg--sm")} role="group" aria-label={ariaLabel}>
      {options.map(o => (
        <button key={String(o.value)} type="button" className="cx-seg__opt" aria-pressed={o.value === value}
                disabled={o.disabled} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
