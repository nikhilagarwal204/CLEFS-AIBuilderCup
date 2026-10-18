"use client";

/* ═══════════════════════════════════════════════════════════════════════
   The answer's table: every figure the text was checked against, formatted
   per column kind (pure.cellText). Long results show the first rows with a
   "Show all" toggle; the server already capped them at the query's limit
   (up to 50) or the registry's 500-row cap, which the footer says. A total
   row appears only when the server sent one (additive units only: a sum
   of ADRs or percentages would be meaningless, so the server omits it).
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from "react";
import { LuDownload } from "react-icons/lu";
import { useFormat } from "../../../../lib/format";
import { Button } from "../../../../ui";
import { cellText, columnDigits, csvFilename, downloadText, formatValue, tableToCsv } from "./lib";
import type { AnswerTable as Table, Grain } from "./types";

const FIRST_ROWS = 8;

export default function AnswerTable({ table, grain, title }: { table: Table; grain?: Grain | null; title: string }) {
  const fmt = useFormat();
  const [all, setAll] = useState(false);
  const rows = all ? table.rows : table.rows.slice(0, FIRST_ROWS);
  const hidden = table.rows.length - rows.length;
  const valueIdx = table.columns.findIndex(c => c.kind === "value");
  const compareIdx = table.columns.findIndex(c => c.kind === "compare");
  const numeric = (k: string) => k === "value" || k === "compare" || k === "change";
  const showTotal = table.rows.length > 1 && table.total !== null && valueIdx >= 0;
  const firstValueIdx = Math.min(...[valueIdx, compareIdx].filter(i => i >= 0));
  // One precision per money column, computed over every row (and the total).
  const digits = useMemo(() => table.columns.map((c, j) => {
    if (c.kind !== "value" && c.kind !== "compare") return undefined;
    const extra = c.kind === "value" ? table.total : table.compare_total;
    return columnDigits([...table.rows.map(r => r[j]), extra], c.unit);
  }), [table]);

  if (!table.rows.length) {
    return <p className="ask-card__note">No rows matched. Try a longer period or fewer filters.</p>;
  }

  return (
    <div>
      <div className="ask-table" role="region" aria-label={`${title}: table`} tabIndex={0}>
        <table>
          <thead>
            <tr>
              {table.columns.map(c => (
                <th key={c.key} scope="col" className={numeric(c.kind) ? "ask-num" : undefined}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {table.columns.map((c, j) => (
                  <td key={c.key} className={numeric(c.kind) ? "ask-num" : undefined}>
                    {cellText(c, r[j], grain, fmt.locale, digits[j])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          {showTotal && (
            <tfoot>
              <tr>
                {table.columns.map((c, j) => {
                  if (j === valueIdx || j === compareIdx) {
                    const v = j === valueIdx ? table.total : table.compare_total;
                    return (
                      <td key={c.key} className="ask-num">
                        {formatValue(v, { unit: c.unit, currency: c.currency, locale: fmt.locale, digits: digits[j] })}
                      </td>
                    );
                  }
                  if (j === 0 && j < firstValueIdx) return <td key={c.key}>Total</td>;
                  return <td key={c.key} />;
                })}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      <div className="ask-table__more">
        <span>
          {/* row_count is before the question's limit; rows is what came back. */}
          {table.limited && table.row_count > table.rows.length
            ? `${table.rows.length} of ${table.row_count} rows (the question's limit)`
            : `${table.row_count} ${table.row_count === 1 ? "row" : "rows"}`}
          {table.truncated ? ", cut at the 500-row cap" : ""}
        </span>
        <span style={{ display: "inline-flex", gap: 6 }}>
          {hidden > 0 && <Button size="sm" variant="ghost" onClick={() => setAll(true)}>Show all {table.rows.length}</Button>}
          {all && table.rows.length > FIRST_ROWS && (
            <Button size="sm" variant="ghost" onClick={() => setAll(false)}>Show fewer</Button>
          )}
          <Button size="sm" variant="ghost" icon={<LuDownload />} onClick={() => downloadText(csvFilename(title), tableToCsv(table))}>
            CSV
          </Button>
        </span>
      </div>
    </div>
  );
}
