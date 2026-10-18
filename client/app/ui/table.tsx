"use client";

/* ═══════════════════════════════════════════════════════════════════════
   UI kit: Table<T>.
   Below 640px every row becomes a card and each cell prints its column name
   from data-label (the same technique as the dashboard's history table), so
   no module has to build a second mobile layout. Wide tables scroll
   sideways on desktop rather than squeezing figures onto two lines.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ReactNode, SyntheticEvent } from "react";
import { cx, Skeleton } from "./core";

/* A click or Enter that starts on a control inside the row (a button, link,
   input, select, or anything marked data-row-click="ignore") belongs to that
   control, not to the row: without this, a row action also opened the row. */
const INTERACTIVE = "a, button, input, select, textarea, label, summary, [role='button'], [data-row-click='ignore']";

function fromControl(e: SyntheticEvent<HTMLElement>): boolean {
  const t = e.target as Element | null;
  const hit = t && typeof t.closest === "function" ? t.closest(INTERACTIVE) : null;
  return !!hit && hit !== e.currentTarget && e.currentTarget.contains(hit);
}

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Plain-text label for the mobile card view; defaults to header when it is a string. */
  mobileLabel?: string;
  render?: (row: T, index: number) => ReactNode;
  align?: "left" | "right" | "center";
  width?: number | string;
  hideOnMobile?: boolean;
  hideOnDesktop?: boolean;
}

export function Table<T>({
  columns, rows, rowKey, onRowClick, empty = "Nothing here yet.", loading = false, loadingRows = 4, caption, className,
}: {
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T, index: number) => string | number;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  loading?: boolean;
  loadingRows?: number;
  /** Screen-reader caption. */
  caption?: string;
  className?: string;
}) {
  const cellClass = (c: Column<T>) =>
    cx(
      c.align === "right" && "cx-al-right",
      c.align === "center" && "cx-al-center",
      c.hideOnMobile && "cx-hide-mobile",
      c.hideOnDesktop && "cx-hide-desktop",
    ) || undefined;
  const labelOf = (c: Column<T>) => c.mobileLabel ?? (typeof c.header === "string" ? c.header : "");

  return (
    <div className={cx("cx-table-wrap", className)}>
      <table className={cx("cx-table", onRowClick && "cx-table--clickable")}>
        {caption && <caption className="cx-sr">{caption}</caption>}
        <thead>
          <tr>
            {columns.map(c => (
              <th key={c.key} scope="col" className={cellClass(c)} style={c.width !== undefined ? { width: c.width } : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading && rows.length === 0 &&
            Array.from({ length: loadingRows }, (_, i) => (
              <tr key={`sk-${i}`}>
                {columns.map(c => (
                  <td key={c.key} data-label={labelOf(c)} className={cellClass(c)}>
                    <Skeleton width="70%" />
                  </td>
                ))}
              </tr>
            ))}
          {!loading && rows.length === 0 && (
            <tr className="cx-table__empty">
              <td colSpan={columns.length}>{empty}</td>
            </tr>
          )}
          {rows.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              onClick={onRowClick ? e => { if (!fromControl(e)) onRowClick(row); } : undefined}
              onKeyDown={onRowClick ? e => { if (e.key === "Enter" && !fromControl(e)) onRowClick(row); } : undefined}
              tabIndex={onRowClick ? 0 : undefined}
            >
              {columns.map(c => (
                <td key={c.key} data-label={labelOf(c)} className={cellClass(c)}>
                  {c.render ? c.render(row, i) : String((row as Record<string, unknown>)[c.key] ?? "-")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
