"use client";

/* ═══════════════════════════════════════════════════════════════════════
   UI kit: form controls.
   Inputs are 16px on phones on purpose: iOS Safari zooms the page into any
   field whose font is smaller, which throws the console layout sideways.
   ═══════════════════════════════════════════════════════════════════════ */

import {
  forwardRef, useMemo,
  type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from "react";
import { cx, Segmented } from "./core";
import { DEFAULT_TIMEZONE, localDateKey } from "../lib/format";
import { useHotelOptional } from "../lib/hotel";

// ─── Field ───────────────────────────────────────────────────────────────

export function Field({
  label, hint, error, required, htmlFor, children, className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  /** Id of the control. Without it the field wraps the control in a <label>. */
  htmlFor?: string;
  children: ReactNode;
  className?: string;
}) {
  const labelText = (
    <span className="cx-label">
      {label}
      {required && <span className="cx-label__req" aria-hidden="true">*</span>}
    </span>
  );
  const tail = (
    <>
      {error ? <span className="cx-error-text" role="alert">{error}</span> : hint ? <span className="cx-hint">{hint}</span> : null}
    </>
  );
  if (htmlFor) {
    return (
      <div className={cx("cx-field", className)}>
        <label htmlFor={htmlFor}>{labelText}</label>
        {children}
        {tail}
      </div>
    );
  }
  return (
    <label className={cx("cx-field", className)}>
      {labelText}
      {children}
      {tail}
    </label>
  );
}

// ─── Controls ────────────────────────────────────────────────────────────

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ invalid, className, ...rest }, ref) {
  return <input ref={ref} className={cx("cx-input", className)} aria-invalid={invalid || undefined} {...rest} />;
});

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean;
  options?: readonly { value: string; label: ReactNode; disabled?: boolean }[];
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { invalid, className, options, placeholder, children, ...rest }, ref,
) {
  return (
    <select ref={ref} className={cx("cx-select", className)} aria-invalid={invalid || undefined} {...rest}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options?.map(o => (
        <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>
      ))}
      {children}
    </select>
  );
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
  /** Monospace, taller: for JSON editors. */
  code?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid, code, className, ...rest }, ref,
) {
  return (
    <textarea
      ref={ref}
      className={cx("cx-textarea", code && "cx-textarea--code", className)}
      aria-invalid={invalid || undefined}
      spellCheck={code ? false : rest.spellCheck}
      {...rest}
    />
  );
});

// ─── DateRange ───────────────────────────────────────────────────────────

/** Inclusive calendar-day range in the hotel's timezone, as YYYY-MM-DD. */
export interface DateRangeValue {
  start: string;
  end: string;
}

function shiftDay(key: string, days: number): string {
  const d = new Date(`${key}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** The last `days` hotel-local days ending today, inclusive. */
export function lastNDays(days: number, tz: string = DEFAULT_TIMEZONE, now: number = Date.now()): DateRangeValue {
  const end = localDateKey(now, tz);
  return { start: shiftDay(end, -(days - 1)), end };
}

/** The last `days` hotel-local days ending YESTERDAY: settled nightly data
    (occupancy, consumption, P&L) where today is still being written. */
export function lastNSettledDays(days: number, tz: string = DEFAULT_TIMEZONE, now: number = Date.now()): DateRangeValue {
  const end = shiftDay(localDateKey(now, tz), -1);
  return { start: shiftDay(end, -(days - 1)), end };
}

/** The next `days` hotel-local days starting today (demand calendars look forward). */
export function nextNDays(days: number, tz: string = DEFAULT_TIMEZONE, now: number = Date.now()): DateRangeValue {
  const start = localDateKey(now, tz);
  return { start, end: shiftDay(start, days - 1) };
}

export function DateRange({
  value, onChange, presets = [7, 30, 90], direction = "past", min, max, ariaLabel = "Date range",
}: {
  value: DateRangeValue;
  onChange: (v: DateRangeValue) => void;
  /** Day counts offered as one-tap presets. */
  presets?: readonly number[];
  /** Whether presets end today ("past"), end yesterday ("settled") or start today ("future"). */
  direction?: "past" | "settled" | "future";
  min?: string;
  max?: string;
  ariaLabel?: string;
}) {
  const tz = useHotelOptional()?.hotel?.timezone || DEFAULT_TIMEZONE;
  const presetRanges = useMemo(
    () => presets.map(n => ({
      n,
      range: direction === "past" ? lastNDays(n, tz) : direction === "settled" ? lastNSettledDays(n, tz) : nextNDays(n, tz),
    })),
    [presets, direction, tz],
  );
  const active = presetRanges.find(p => p.range.start === value.start && p.range.end === value.end)?.n ?? 0;

  return (
    <div className="cx-daterange" role="group" aria-label={ariaLabel}>
      {presets.length > 0 && (
        <Segmented
          size="sm"
          ariaLabel="Preset ranges"
          value={active}
          onChange={n => {
            const p = presetRanges.find(x => x.n === n);
            if (p) onChange(p.range);
          }}
          options={[
            ...presetRanges.map(p => ({ value: p.n, label: `${p.n}d` })),
            { value: 0, label: "Custom", disabled: true },
          ]}
        />
      )}
      <div className="cx-daterange__inputs">
        <Input
          type="date" aria-label="Start date" value={value.start} min={min} max={value.end || max}
          onChange={e => e.target.value && onChange({ ...value, start: e.target.value })}
        />
        <span className="cx-faint" aria-hidden="true">to</span>
        <Input
          type="date" aria-label="End date" value={value.end} min={value.start || min} max={max}
          onChange={e => e.target.value && onChange({ ...value, end: e.target.value })}
        />
      </div>
    </div>
  );
}
