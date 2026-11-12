/** The ```trench-ui block format: a fixed catalog of components the model
 * may put in an answer (see rule 8 of the system prompt in
 * app/graph/rag_graph.py). The model only ever supplies DATA -- every
 * component is rendered by our own React code, so there is no model-written
 * HTML or JS. Parsing is lenient per component: one malformed component is
 * skipped, and only a block with nothing usable falls back to plain code. */
import { z } from "zod";

import { evaluateFormula } from "@/lib/formula";

export const INTERACTIVE_LANGUAGE = "trench-ui";

const MAX_COMPONENTS = 12;
const MAX_ITEMS = 500;

const text = z.union([z.string(), z.number()]).transform(String);
// Numbers sometimes arrive as strings ("12,500") -- accept those too.
const num = z.union([
  z.number(),
  z
    .string()
    .transform((s) => Number(s.replace(/[,\s]/g, "")))
    .refine(Number.isFinite),
]);
const optionalNum = z.union([num, z.null()]).optional();
const format = z.enum(["number", "currency", "percent", "plain"]).optional().catch(undefined);
const currency = z.string().optional().catch(undefined);
const cite = z
  .array(z.unknown())
  .transform((a) => a.filter((n): n is number => Number.isInteger(n)))
  .optional()
  .catch(undefined);
const list = <T extends z.ZodType>(item: T) => z.array(item).max(MAX_ITEMS);

const base = { title: text.optional(), cite };

const metricCards = z.object({
  ...base,
  type: z.literal("metric_cards"),
  items: list(
    z.object({
      label: text,
      value: z.union([z.number(), z.string()]),
      format,
      currency,
      hint: text.optional(),
    })
  ).min(1),
});

const table = z.object({
  ...base,
  type: z.literal("table"),
  columns: list(text).min(1),
  rows: list(list(z.union([z.string(), z.number(), z.boolean(), z.null()]))),
});

const series = z.object({ name: text, data: list(z.union([num, z.null()])) });
const cartesian = {
  ...base,
  x: list(text).min(1),
  series: z.array(series).min(1).max(8),
  format,
  currency,
};
const barChart = z.object({ ...cartesian, type: z.literal("bar_chart") });
const lineChart = z.object({ ...cartesian, type: z.literal("line_chart") });

const pieChart = z.object({
  ...base,
  type: z.literal("pie_chart"),
  items: list(z.object({ label: text, value: num })).min(1),
  format,
  currency,
});

const progress = z.object({
  ...base,
  type: z.literal("progress"),
  items: list(z.object({ label: text, value: num, max: num, format, currency })).min(1),
});

const timeline = z.object({
  ...base,
  type: z.literal("timeline"),
  items: list(z.object({ date: text, title: text, detail: text.optional() })).min(1),
});

const comparison = z.object({
  ...base,
  type: z.literal("comparison"),
  items: z.array(z.object({ title: text, points: list(text) })).min(1).max(6),
});

const callout = z.object({
  ...base,
  type: z.literal("callout"),
  tone: z.enum(["info", "warning", "success"]).catch("info"),
  text,
});

const identifier = z.string().regex(/^[A-Za-z_][A-Za-z0-9_]*$/);
const calculator = z.object({
  ...base,
  type: z.literal("calculator"),
  inputs: z
    .array(
      z.object({
        id: identifier,
        label: text,
        default: num,
        min: optionalNum,
        max: optionalNum,
        step: optionalNum,
        format,
        currency,
      })
    )
    .min(1)
    .max(20),
  outputs: z
    .array(
      z.object({
        id: identifier.optional(),
        label: text,
        formula: z.string().max(1000),
        format,
        currency,
      })
    )
    .min(1)
    .max(20),
  assumptions: z.union([text, z.array(text)]).optional(),
});

const COMPONENT_SCHEMAS = {
  metric_cards: metricCards,
  table,
  bar_chart: barChart,
  line_chart: lineChart,
  pie_chart: pieChart,
  progress,
  timeline,
  comparison,
  callout,
  calculator,
} as const;

export type MetricCards = z.infer<typeof metricCards>;
export type TableComponent = z.infer<typeof table>;
export type CartesianChart = z.infer<typeof barChart> | z.infer<typeof lineChart>;
export type PieChartComponent = z.infer<typeof pieChart>;
export type ProgressComponent = z.infer<typeof progress>;
export type TimelineComponent = z.infer<typeof timeline>;
export type ComparisonComponent = z.infer<typeof comparison>;
export type CalloutComponent = z.infer<typeof callout>;
export type CalculatorComponent = z.infer<typeof calculator>;
export type InteractiveComponent =
  | MetricCards
  | TableComponent
  | CartesianChart
  | PieChartComponent
  | ProgressComponent
  | TimelineComponent
  | ComparisonComponent
  | CalloutComponent
  | CalculatorComponent;
export type NumberFormat = "number" | "currency" | "percent" | "plain" | undefined;

export interface ParsedBlock {
  components: InteractiveComponent[];
  /** Components that were present but couldn't be shown. */
  skipped: number;
}

/** Parses a block's JSON. Returns null when nothing in it can be rendered
 * (invalid JSON, wrong shape, or every component malformed). */
export function parseInteractiveBlock(source: string): ParsedBlock | null {
  let raw: unknown;
  try {
    raw = JSON.parse(source);
  } catch {
    return null;
  }
  // Accept {"components":[...]}, a bare array, or one bare component.
  const list = Array.isArray(raw)
    ? raw
    : raw && typeof raw === "object" && Array.isArray((raw as { components?: unknown }).components)
      ? (raw as { components: unknown[] }).components
      : raw && typeof raw === "object" && "type" in raw
        ? [raw]
        : null;
  if (!list) return null;

  const components: InteractiveComponent[] = [];
  let skipped = 0;
  for (const item of list.slice(0, MAX_COMPONENTS)) {
    const type = (item as { type?: unknown } | null)?.type;
    const schema =
      typeof type === "string" && Object.hasOwn(COMPONENT_SCHEMAS, type)
        ? COMPONENT_SCHEMAS[type as keyof typeof COMPONENT_SCHEMAS]
        : null;
    const parsed = schema?.safeParse(item);
    if (parsed?.success) components.push(parsed.data as InteractiveComponent);
    else skipped++;
  }
  skipped += Math.max(0, list.length - MAX_COMPONENTS);
  return components.length > 0 ? { components, skipped } : null;
}

// --- Number formatting -----------------------------------------------------

export function formatValue(
  value: number | string | null | undefined,
  fmt?: NumberFormat,
  currencyCode?: string
): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  if (!Number.isFinite(value)) return "—";
  const maxDigits = Math.abs(value) >= 100 ? 0 : 2;
  switch (fmt) {
    case "plain":
      return String(value);
    case "percent":
      // Values are already percentages: 8.4 -> "8.4%".
      return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
    case "currency":
      try {
        return value.toLocaleString(undefined, {
          style: "currency",
          currency: (currencyCode || "USD").toUpperCase(),
          maximumFractionDigits: maxDigits,
        });
      } catch {
        return `${currencyCode ?? ""} ${value.toLocaleString(undefined, { maximumFractionDigits: maxDigits })}`.trim();
      }
    default:
      return value.toLocaleString(undefined, { maximumFractionDigits: maxDigits });
  }
}

/** Short axis-tick form: 1.2M, 45k, ₹12k, 8%. */
export function formatCompact(value: number, fmt?: NumberFormat, currencyCode?: string): string {
  if (!Number.isFinite(value)) return "";
  if (fmt === "percent") return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  const options: Intl.NumberFormatOptions = { notation: "compact", maximumFractionDigits: 1 };
  if (fmt === "currency") {
    try {
      return value.toLocaleString(undefined, {
        ...options,
        style: "currency",
        currency: (currencyCode || "USD").toUpperCase(),
      });
    } catch {
      // fall through
    }
  }
  return value.toLocaleString(undefined, options);
}

// --- Calculator ------------------------------------------------------------

export interface CalculatorResult {
  label: string;
  value: number | null;
  error?: string;
  format?: NumberFormat;
  currency?: string;
}

/** Evaluates outputs in order; each may use the inputs and earlier output ids. */
export function computeCalculator(
  calc: CalculatorComponent,
  values: Readonly<Record<string, number>>
): CalculatorResult[] {
  const scope: Record<string, number> = { ...values };
  return calc.outputs.map((out) => {
    try {
      const value = evaluateFormula(out.formula, scope);
      if (out.id) scope[out.id] = value;
      return {
        label: out.label,
        value: Number.isFinite(value) ? value : null,
        error: Number.isFinite(value) ? undefined : "Not a finite number",
        format: out.format,
        currency: out.currency,
      };
    } catch (err) {
      if (out.id) scope[out.id] = Number.NaN;
      return {
        label: out.label,
        value: null,
        error: err instanceof Error ? err.message : "Invalid formula",
        format: out.format,
        currency: out.currency,
      };
    }
  });
}

export function calculatorDefaults(calc: CalculatorComponent): Record<string, number> {
  return Object.fromEntries(calc.inputs.map((i) => [i.id, i.default]));
}

// --- Plain-text form (for "Copy response" and per-block text copy) -----------

function cell(value: unknown): string {
  return value === null || value === undefined ? "" : String(value).replace(/\|/g, "\\|").replace(/\n/g, " ");
}

function markdownTable(columns: string[], rows: unknown[][]): string {
  return [
    `| ${columns.map(cell).join(" | ")} |`,
    `| ${columns.map(() => "---").join(" | ")} |`,
    ...rows.map((r) => `| ${columns.map((_, i) => cell(r[i])).join(" | ")} |`),
  ].join("\n");
}

function componentToText(c: InteractiveComponent): string {
  const parts: string[] = [];
  if (c.title) parts.push(`**${c.title}**`);
  switch (c.type) {
    case "metric_cards":
      parts.push(
        c.items
          .map((i) => `- ${i.label}: ${formatValue(i.value, i.format, i.currency)}${i.hint ? ` (${i.hint})` : ""}`)
          .join("\n")
      );
      break;
    case "table":
      parts.push(markdownTable(c.columns, c.rows));
      break;
    case "bar_chart":
    case "line_chart":
      parts.push(
        markdownTable(
          ["", ...c.series.map((s) => s.name)],
          c.x.map((x, i) => [x, ...c.series.map((s) => formatValue(s.data[i] ?? null, c.format, c.currency))])
        )
      );
      break;
    case "pie_chart":
      parts.push(c.items.map((i) => `- ${i.label}: ${formatValue(i.value, c.format, c.currency)}`).join("\n"));
      break;
    case "progress":
      parts.push(
        c.items
          .map(
            (i) =>
              `- ${i.label}: ${formatValue(i.value, i.format, i.currency)} of ${formatValue(i.max, i.format, i.currency)}`
          )
          .join("\n")
      );
      break;
    case "timeline":
      parts.push(c.items.map((i) => `- ${i.date}: ${i.title}${i.detail ? ` — ${i.detail}` : ""}`).join("\n"));
      break;
    case "comparison":
      parts.push(c.items.map((i) => `${i.title}\n${i.points.map((p) => `- ${p}`).join("\n")}`).join("\n\n"));
      break;
    case "callout":
      parts.push(c.text);
      break;
    case "calculator": {
      const defaults = calculatorDefaults(c);
      parts.push(
        c.inputs.map((i) => `- ${i.label}: ${formatValue(i.default, i.format, i.currency)}`).join("\n"),
        computeCalculator(c, defaults)
          .map((r) => `- ${r.label}: ${formatValue(r.value, r.format, r.currency)}`)
          .join("\n")
      );
      if (c.assumptions) {
        parts.push(`Assumptions: ${Array.isArray(c.assumptions) ? c.assumptions.join("; ") : c.assumptions}`);
      }
      break;
    }
  }
  return parts.filter(Boolean).join("\n");
}

export function interactiveBlockToText(block: ParsedBlock): string {
  return block.components.map(componentToText).join("\n\n");
}

const BLOCK_RE = /```trench-ui[^\n]*\n([\s\S]*?)```/g;

/** The message as the user would want it pasted: each valid trench-ui
 * block becomes readable Markdown instead of raw JSON. */
export function contentForCopy(content: string): string {
  return content.replace(BLOCK_RE, (whole, json: string) => {
    const parsed = parseInteractiveBlock(json);
    return parsed ? interactiveBlockToText(parsed) : whole;
  });
}
