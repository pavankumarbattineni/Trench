/** The ```trench-ui block format: a fixed catalog of components the model
 * may put in an answer (see rule 8 of the system prompt in
 * app/graph/rag_graph.py). The model only ever supplies DATA -- every
 * component is rendered by our own React code, so there is no model-written
 * HTML or JS. Parsing is lenient per component: one malformed component is
 * skipped, and only a block with nothing usable falls back to plain code. */
import { z } from "zod";

import { evaluateFormula, formulaIdentifiers } from "@/lib/formula";

export const INTERACTIVE_LANGUAGE = "trench-ui";

const MAX_COMPONENTS = 12;
const MAX_ITEMS = 500;
const MAX_CALCULATOR_FIELDS = 40;

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
// Long calculators are trimmed to the first fields rather than rejected.
const capped = <T extends z.ZodType>(item: T) =>
  z
    .array(item)
    .min(1)
    .transform((a) => a.slice(0, MAX_CALCULATOR_FIELDS));
const option = z.object({ label: text, value: num });

export interface Assumptions {
  /** Shown to the user, one per line. */
  lines: string[];
  /** Named numbers formulas may refer to. */
  constants: Record<string, number>;
}

function normalizeAssumptions(
  raw: string | string[] | Record<string, number | string> | undefined
): Assumptions {
  if (raw === undefined) return { lines: [], constants: {} };
  if (typeof raw === "string") return { lines: [raw], constants: {} };
  if (Array.isArray(raw)) return { lines: raw, constants: {} };
  const constants: Record<string, number> = {};
  const lines = Object.entries(raw).map(([key, value]) => {
    if (typeof value === "number" && /^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) constants[key] = value;
    const name = key.replace(/_/g, " ");
    return `${name.charAt(0).toUpperCase()}${name.slice(1)}: ${value}`;
  });
  return { lines, constants };
}
const calculator = z.object({
  ...base,
  type: z.literal("calculator"),
  inputs: capped(
    z
      .object({
        id: identifier,
        label: text,
        default: num.optional(),
        min: optionalNum,
        max: optionalNum,
        step: optionalNum,
        format,
        currency,
        // A choice (sex, activity level, plan) shown as a dropdown.
        options: z.array(option).min(1).max(50).optional().catch(undefined),
      })
      .transform((input) => ({
        ...input,
        default: input.default ?? input.options?.[0]?.value ?? input.min ?? 0,
      }))
  ),
  outputs: capped(
    z.object({
      id: identifier.optional(),
      label: text,
      formula: z.string().max(2000),
      format,
      currency,
    })
  ),
  // Text, a list of texts, or named constants ({"kcal_per_g_fat": 9}).
  // Numeric constants can be used in formulas by name.
  assumptions: z
    .union([text, z.array(text), z.record(z.string(), z.union([z.number(), z.string()]))])
    .optional()
    .catch(undefined)
    .transform(normalizeAssumptions),
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
  /** Why each skipped component couldn't be shown, in plain words. */
  problems: string[];
}

type ComponentType = keyof typeof COMPONENT_SCHEMAS;

/** Models sometimes leave out "type"; the shape of the data still says
 * what the component is. Returns null when it's genuinely ambiguous. */
function inferType(item: Record<string, unknown>): ComponentType | null {
  const has = (key: string) => item[key] !== undefined;
  if (has("inputs") && has("outputs")) return "calculator";
  if (has("columns") && has("rows")) return "table";
  if (has("x") && has("series")) return "bar_chart";
  if (has("tone") || (has("text") && !has("items"))) return "callout";
  const first = Array.isArray(item.items) ? (item.items[0] as Record<string, unknown> | undefined) : undefined;
  if (first && typeof first === "object") {
    if ("points" in first) return "comparison";
    if ("date" in first) return "timeline";
    if ("max" in first) return "progress";
    if ("label" in first && "value" in first) return "metric_cards";
  }
  return null;
}

function describeIssue(type: string, error: z.ZodError): string {
  const issue = error.issues[0];
  const where = issue?.path.length ? ` (${issue.path.join(".")})` : "";
  return `A ${type.replace("_", " ")} has invalid data${where}`;
}

/** Removes // and /* *\/ comments and trailing commas -- common slips in
 * smaller models' JSON -- while leaving string contents untouched. */
export function repairJson(source: string): string {
  let out = "";
  let inString = false;
  for (let i = 0; i < source.length; i++) {
    const ch = source[i];
    if (inString) {
      out += ch;
      if (ch === "\\") out += source[++i] ?? "";
      else if (ch === '"') inString = false;
    } else if (ch === '"') {
      inString = true;
      out += ch;
    } else if (ch === "/" && source[i + 1] === "/") {
      while (i < source.length && source[i] !== "\n") i++;
      out += "\n";
    } else if (ch === "/" && source[i + 1] === "*") {
      const end = source.indexOf("*/", i + 2);
      i = end === -1 ? source.length : end + 1;
    } else {
      out += ch;
    }
  }
  // A comma followed only by whitespace before } or ] (outside strings,
  // since quotes were preserved above and these can't occur inside keys).
  return out.replace(/,(\s*[}\]])/g, "$1");
}

/** JSON.parse, retried once on the repaired text; undefined if both fail. */
function parseLenientJson(source: string): unknown {
  try {
    return JSON.parse(source);
  } catch {
    try {
      return JSON.parse(repairJson(source));
    } catch {
      return undefined;
    }
  }
}

/** A ```json block that is really an interactive view (smaller models
 * sometimes use the wrong fence tag): our {"version", "components"} shape. */
export function looksLikeInteractiveJson(source: string): boolean {
  if (!/"components"\s*:/.test(source)) return false;
  const raw = parseLenientJson(source) as { version?: unknown; components?: unknown } | undefined;
  return (
    !!raw &&
    typeof raw === "object" &&
    raw.version !== undefined &&
    Array.isArray(raw.components) &&
    raw.components.length > 0
  );
}

export interface BlockAnalysis {
  block: ParsedBlock | null;
  /** Why the block (or parts of it) couldn't be shown. */
  problems: string[];
}

/** Parses a block's JSON, keeping every component that can be shown and a
 * plain-words reason for each one that can't. */
export function analyzeInteractiveBlock(source: string): BlockAnalysis {
  const raw = parseLenientJson(source);
  if (raw === undefined) return { block: null, problems: ["The data isn't valid JSON"] };
  // Accept {"components":[...]}, a bare array, or one bare component.
  const list = Array.isArray(raw)
    ? raw
    : raw && typeof raw === "object" && Array.isArray((raw as { components?: unknown }).components)
      ? (raw as { components: unknown[] }).components
      : raw && typeof raw === "object"
        ? [raw]
        : null;
  if (!list) return { block: null, problems: ["The data has no components"] };

  const components: InteractiveComponent[] = [];
  const problems: string[] = [];
  for (const item of list.slice(0, MAX_COMPONENTS)) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      problems.push("A component isn't an object");
      continue;
    }
    const record = item as Record<string, unknown>;
    const declared = typeof record.type === "string" ? record.type : null;
    const type =
      declared && Object.hasOwn(COMPONENT_SCHEMAS, declared)
        ? (declared as ComponentType)
        : inferType(record);
    if (!type) {
      problems.push(
        declared ? `Unsupported component type "${declared}"` : "A component has no type"
      );
      continue;
    }
    const parsed = COMPONENT_SCHEMAS[type].safeParse({ ...record, type });
    if (parsed.success) components.push(parsed.data as InteractiveComponent);
    else problems.push(describeIssue(type, parsed.error));
  }
  if (list.length > MAX_COMPONENTS) {
    problems.push(`Only the first ${MAX_COMPONENTS} components are shown`);
  }
  const skipped = problems.length;
  return {
    block: components.length > 0 ? { components, skipped, problems } : null,
    problems,
  };
}

/** Parses a block's JSON. Returns null when nothing in it can be rendered
 * (invalid JSON, wrong shape, or every component malformed). */
export function parseInteractiveBlock(source: string): ParsedBlock | null {
  return analyzeInteractiveBlock(source).block;
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
  const scope: Record<string, number> = { ...calc.assumptions.constants, ...values };
  // Outputs that failed, by id -> label, so a result built on one says so
  // instead of repeating the same generic error down the whole chain.
  const failed = new Map<string, string>();
  return calc.outputs.map((out) => {
    const fail = (error: string): CalculatorResult => {
      if (out.id) {
        scope[out.id] = Number.NaN;
        failed.set(out.id, out.label);
      }
      return { label: out.label, value: null, error, format: out.format, currency: out.currency };
    };
    const brokenDependency = formulaIdentifiers(out.formula).find((name) => failed.has(name));
    if (brokenDependency) {
      return fail(`Depends on ${failed.get(brokenDependency)}, which couldn't be calculated`);
    }
    try {
      const value = evaluateFormula(out.formula, scope);
      if (!Number.isFinite(value)) return fail("The result isn't a finite number (division by zero?)");
      if (out.id) scope[out.id] = value;
      return { label: out.label, value, format: out.format, currency: out.currency };
    } catch (err) {
      return fail(err instanceof Error ? err.message : "Invalid formula");
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
      if (c.assumptions.lines.length > 0) {
        parts.push(`Assumptions: ${c.assumptions.lines.join("; ")}`);
      }
      break;
    }
  }
  return parts.filter(Boolean).join("\n");
}

export function interactiveBlockToText(block: ParsedBlock): string {
  return block.components.map(componentToText).join("\n\n");
}

const BLOCK_RE = /```(?:trench-ui|json)[^\n]*\n([\s\S]*?)```/g;

/** The message as the user would want it pasted: each valid trench-ui
 * block becomes readable Markdown instead of raw JSON. */
export function contentForCopy(content: string): string {
  return content.replace(BLOCK_RE, (whole, json: string) => {
    if (!whole.startsWith("```trench-ui") && !looksLikeInteractiveJson(json)) return whole;
    const parsed = parseInteractiveBlock(json);
    return parsed ? interactiveBlockToText(parsed) : whole;
  });
}
