"use client";

/* ═══════════════════════════════════════════════════════════════════════
   ChartSpecRenderer (02 spec 7): maps the server's chart_spec onto the
   existing chart kit. kpi -> a big figure with its comparison chip;
   column -> ColumnChart; hbar -> HBarChart; line -> LineChart; a breakdown
   with a negative figure -> DivergingBar. The mapping itself is pure
   (pure.chartModel, tested); this file only renders it.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo } from "react";
import { useFormat } from "../../../../lib/format";
import { DivergingBar, LineChart, SERIES_COLORS } from "../../../../ui";
import { ColumnChart, HBarChart } from "../../../charts";
import { bucketLabel, changeTone, chartModel, formatChange, formatValue, type ChartModel } from "./pure";
import type { ChartSpec, Grain } from "./types";

// Two series at most on a breakdown, never stacked (see pure.chartModel).
const PRIMARY = SERIES_COLORS[0];

export default function ChartSpecRenderer({ spec, grain, higherIsBetter, title, compareLabel }: {
  spec: ChartSpec | null | undefined;
  grain?: Grain | null;
  /** "same period last year", shown after the KPI's comparison figure. */
  compareLabel?: string | null;
  /** From the catalog entry; colours the KPI change chip. Unknown stays neutral. */
  higherIsBetter?: boolean | null;
  /** Accessible name for the chart. */
  title: string;
}) {
  const fmt = useFormat();
  const model = useMemo(() => chartModel(spec, grain, fmt.locale), [spec, grain, fmt.locale]);
  if (!spec || model.kind === "none") return null;

  const unit = spec.unit;
  const currency = spec.currency || fmt.currency;
  const full = (v: number) => formatValue(v, { unit, currency, locale: fmt.locale });
  const compact = (v: number) => formatValue(v, { unit, currency, locale: fmt.locale, compact: true });

  return <ChartBody model={model} title={title} full={full} compact={compact} grain={grain}
                    higherIsBetter={higherIsBetter} locale={fmt.locale} compareLabel={compareLabel} />;
}

function ChartBody({ model, title, full, compact, grain, higherIsBetter, locale, compareLabel }: {
  model: Exclude<ChartModel, { kind: "none" }>;
  compareLabel?: string | null;
  title: string;
  full: (v: number) => string;
  compact: (v: number) => string;
  grain?: Grain | null;
  higherIsBetter?: boolean | null;
  locale: string;
}) {
  switch (model.kind) {
    case "kpi":
      return <Kpi model={model} full={full} higherIsBetter={higherIsBetter} locale={locale} compareLabel={compareLabel} />;

    case "column":
    case "hbar": {
      const series = model.series.map((s, i) => ({ label: s.label, color: i === 0 ? PRIMARY : SERIES_COLORS[i] }));
      const note = [
        model.scale.hint && `Figures in ${model.scale.hint}.`,
        model.comparedHidden && "The comparison is in the table below.",
      ].filter(Boolean).join(" ");
      return (
        <figure style={{ margin: 0 }}>
          {model.kind === "column"
            ? <ColumnChart data={model.data} series={series} unit={model.scale.suffix} ariaLabel={title} height={200} />
            : <HBarChart rows={model.rows} series={series} unit={model.scale.suffix} ariaLabel={title} />}
          {note && <figcaption className="ask-chart-note">{note}</figcaption>}
        </figure>
      );
    }

    case "diverging":
      return (
        <DivergingBar rows={model.rows} format={compact} ariaLabel={title}
                      positiveLabel="Positive" negativeLabel="Negative" />
      );

    case "line":
      return (
        <LineChart
          ariaLabel={title}
          height={220}
          yFormat={compact}
          xFormat={x => bucketLabel(x, grain ?? "day", locale)}
          series={model.series.map((s, i) => ({
            key: s.key,
            label: s.label,
            dashed: s.dashed,
            color: s.dashed ? SERIES_COLORS[1] : SERIES_COLORS[i % SERIES_COLORS.length],
            points: s.points,
          }))}
        />
      );
  }
}

function Kpi({ model, full, higherIsBetter, locale, compareLabel }: {
  model: Extract<ChartModel, { kind: "kpi" }>;
  compareLabel?: string | null;
  full: (v: number) => string;
  higherIsBetter?: boolean | null;
  locale: string;
}) {
  const tone = changeTone(model.change, higherIsBetter);
  const arrow = model.change === null || model.change === 0 ? "" : model.change > 0 ? "▲" : "▼";
  return (
    <div className="ask-kpi" role="group" aria-label={model.label}>
      <span className="ask-kpi__value">{model.value === null ? "–" : full(model.value)}</span>
      {model.compare !== null && (
        <span className="ask-kpi__cmp">
          {model.change !== null && (
            <span className={`ask-delta${tone === "good" || tone === "bad" ? ` ask-delta--${tone}` : ""}`}>
              {arrow && <span aria-hidden="true">{arrow}</span>}
              {formatChange(model.change, locale)}
            </span>
          )}
          <span>vs {full(model.compare)}{compareLabel ? `, ${compareLabel.toLowerCase()}` : ""}</span>
        </span>
      )}
    </div>
  );
}
