/* ═══════════════════════════════════════════════════════════════════════
   Clefs UI kit barrel. Frozen after the foundation wave: modules import from
   here and never edit these files; anything missing goes in a
   FOUNDATION-REQUEST comment in the module's own code.
   ═══════════════════════════════════════════════════════════════════════ */

export { uiStyles } from "./styles";

export {
  cx, UiStyles, Card, StatTile, Badge, Button, EmptyState, Skeleton, PageHeader, Tabs, Segmented,
  type StatDelta, type Tone, type ButtonVariant, type ButtonProps, type TabItem,
} from "./core";

export {
  Field, Input, Select, Textarea, DateRange, lastNDays, lastNSettledDays, nextNDays,
  type InputProps, type SelectProps, type TextareaProps, type DateRangeValue,
} from "./forms";

export { Modal, Drawer } from "./overlay";

export { Table, type Column } from "./table";

export { ToastProvider, useToast, type ToastApi, type ToastInput, type ToastTone } from "./toast";

export { FileDrop, useUpload, ProgressBar, MAX_UPLOAD_BYTES, type UploadState } from "./upload";

export {
  MoneyText, ConfidencePill, confidenceBand, EvidencePanel, ActionBar, SignalCard, DECIDE_PERMISSION,
  type DecideHandler, type AssignPickerApi,
} from "./domain";

export {
  LineChart, Sparkline, CalendarHeatmap, DivergingBar, Donut, WaterfallChart, CountdownChip, foldSlices,
  SERIES_COLORS, OTHER_COLOR, DIVERGING_POS, DIVERGING_NEG, DIVERGING_MID, WATERFALL_TOTAL, HEAT_RAMP,
  type LineSeries, type GuardrailBand, type ChartEvent, type HeatDay, type DonutSlice, type WaterfallStep, type XValue,
} from "./charts-extra";
