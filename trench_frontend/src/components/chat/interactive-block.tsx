"use client";

import { useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CircleCheck,
  CodeXml,
  Info,
  LayoutDashboard,
  RotateCcw,
  TriangleAlert,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { CodeBlock, tableToTsv } from "@/components/chat/code-block";
import { CopyButton } from "@/components/chat/copy-button";
import { Button } from "@/components/ui/button";
import {
  calculatorDefaults,
  computeCalculator,
  formatCompact,
  formatValue,
  interactiveBlockToText,
  parseInteractiveBlock,
  type CalculatorComponent,
  type CalloutComponent,
  type CartesianChart,
  type ComparisonComponent,
  type InteractiveComponent,
  type MetricCards,
  type PieChartComponent,
  type ProgressComponent,
  type TableComponent,
  type TimelineComponent,
} from "@/lib/interactive";
import { cn } from "cn";

const SERIES = Array.from({ length: 8 }, (_, i) => `var(--viz-${i + 1})`);
const MAX_PIE_SLICES = 7; // the rest fold into "Other"

interface CitationProps {
  citationNumbers?: ReadonlySet<number>;
  onCitationClick?: (index: number) => void;
}

// --- Shared pieces -----------------------------------------------------------

function CitationChips({ cite, citationNumbers, onCitationClick }: { cite?: number[] } & CitationProps) {
  const numbers = [...new Set(cite ?? [])].filter((n) => citationNumbers?.has(n)).sort((a, b) => a - b);
  if (numbers.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {numbers.map((n) => (
        <button
          key={n}
          type="button"
          onClick={() => onCitationClick?.(n)}
          aria-label={`View source ${n}`}
          className="inline-flex h-4 min-w-4 items-center justify-center rounded bg-emerald-100 px-1 text-[0.65rem] font-semibold text-emerald-900 hover:bg-emerald-200 dark:bg-emerald-400/20 dark:text-emerald-300 dark:hover:bg-emerald-400/30"
        >
          {n}
        </button>
      ))}
    </span>
  );
}

function TooltipBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      {children}
    </div>
  );
}

function Swatch({ color }: { color: string }) {
  return <span className="size-2.5 shrink-0 rounded-sm" style={{ background: color }} />;
}

const AXIS_TICK = { fill: "var(--muted-foreground)", fontSize: 11 };

// --- Components --------------------------------------------------------------

function MetricCardsView({ c }: { c: MetricCards }) {
  const cols = Math.min(c.items.length, 4);
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-2",
        cols >= 3 && "sm:grid-cols-3",
        cols >= 4 && "lg:grid-cols-4",
        cols === 1 && "grid-cols-1"
      )}
    >
      {c.items.map((item, i) => (
        <div key={i} className="min-w-0 rounded-lg border border-border bg-card px-3 py-2.5">
          <p className="truncate text-xs text-muted-foreground" title={item.label}>
            {item.label}
          </p>
          <p className="mt-0.5 text-lg font-semibold tabular-nums break-words sm:text-xl">
            {formatValue(item.value, item.format, item.currency)}
          </p>
          {item.hint && <p className="mt-0.5 text-xs text-muted-foreground">{item.hint}</p>}
        </div>
      ))}
    </div>
  );
}

type Cell = string | number | boolean | null;

function sortKey(value: Cell): number | string {
  if (typeof value === "number") return value;
  if (value === null) return "";
  const s = String(value);
  // "₹12,500", "8.4%", "-3" sort as numbers.
  const stripped = s.replace(/[,\s%$€£₹¥]/g, "");
  return /^-?\d+(\.\d+)?$/.test(stripped) ? Number(stripped) : s.toLowerCase();
}

function TableView({ c }: { c: TableComponent }) {
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const ref = useRef<HTMLTableElement>(null);
  const rows = useMemo(() => {
    if (!sort) return c.rows;
    return [...c.rows].sort((a, b) => {
      const x = sortKey(a[sort.col] ?? null);
      const y = sortKey(b[sort.col] ?? null);
      if (typeof x === "number" && typeof y === "number") return (x - y) * sort.dir;
      return String(x).localeCompare(String(y)) * sort.dir;
    });
  }, [c.rows, sort]);

  const toggle = (col: number) =>
    setSort((s) => (s?.col !== col ? { col, dir: 1 } : s.dir === 1 ? { col, dir: -1 } : null));

  return (
    <div>
      <div className="-mt-1 mb-1 flex justify-end">
        <CopyButton label="Copy table" getText={() => (ref.current ? tableToTsv(ref.current) : "")} />
      </div>
      <div className="max-h-96 overflow-auto rounded-lg border border-border">
        <table ref={ref} className="w-full border-collapse text-sm">
          <thead className="sticky top-0 bg-muted">
            <tr>
              {c.columns.map((col, i) => {
                const active = sort?.col === i;
                const Icon = !active ? ArrowUpDown : sort.dir === 1 ? ArrowUp : ArrowDown;
                return (
                  <th
                    key={i}
                    scope="col"
                    aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                    className="border-b border-border px-3 py-2 text-left font-medium whitespace-nowrap"
                  >
                    <button
                      type="button"
                      onClick={() => toggle(i)}
                      className="inline-flex items-center gap-1 hover:text-foreground"
                    >
                      {col}
                      <Icon className={cn("size-3", !active && "text-muted-foreground/60")} />
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="border-b border-border last:border-0 hover:bg-muted/40">
                {c.columns.map((_, i) => {
                  const v = row[i] ?? null;
                  return (
                    <td
                      key={i}
                      className={cn("px-3 py-1.5 align-top", typeof v === "number" && "text-right tabular-nums")}
                    >
                      {typeof v === "number" ? v.toLocaleString() : v === null ? "—" : String(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SeriesLegend({ names }: { names: string[] }) {
  if (names.length < 2) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {names.map((name, i) => (
        <span key={i} className="inline-flex items-center gap-1.5">
          <Swatch color={SERIES[i % SERIES.length]} />
          {name}
        </span>
      ))}
    </div>
  );
}

function CartesianChartView({ c }: { c: CartesianChart }) {
  const data = c.x.map((label, i) => {
    const row: Record<string, string | number | null> = { label };
    c.series.forEach((s, j) => {
      row[`s${j}`] = s.data[i] ?? null;
    });
    return row;
  });
  const isBar = c.type === "bar_chart";
  const Chart = isBar ? BarChart : LineChart;
  return (
    <div>
      <SeriesLegend names={c.series.map((s) => s.name)} />
      <div className="h-60 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <Chart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis
              dataKey="label"
              tickLine={false}
              axisLine={{ stroke: "var(--border)" }}
              interval="preserveStartEnd"
              minTickGap={12}
              tick={AXIS_TICK}
            />
            <YAxis
              tickFormatter={(v: number) => formatCompact(v, c.format, c.currency)}
              tickLine={false}
              axisLine={false}
              width={56}
              tick={AXIS_TICK}
            />
            <Tooltip
              cursor={isBar ? { fill: "var(--muted)", opacity: 0.5 } : { stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }}
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                return (
                  <TooltipBox>
                    <p className="mb-1 font-medium">{label}</p>
                    {payload.map((p, i) => (
                      <p key={i} className="flex items-center gap-1.5">
                        <Swatch color={String(p.color)} />
                        <span className="text-muted-foreground">{p.name}:</span>
                        <span className="font-medium tabular-nums">
                          {formatValue(p.value as number | null, c.format, c.currency)}
                        </span>
                      </p>
                    ))}
                  </TooltipBox>
                );
              }}
            />
            {c.series.map((s, j) =>
              isBar ? (
                <Bar
                  key={j}
                  dataKey={`s${j}`}
                  name={s.name}
                  fill={SERIES[j % SERIES.length]}
                  radius={[4, 4, 0, 0]}
                  maxBarSize={36}
                  isAnimationActive={false}
                />
              ) : (
                <Line
                  key={j}
                  dataKey={`s${j}`}
                  name={s.name}
                  type="monotone"
                  stroke={SERIES[j % SERIES.length]}
                  strokeWidth={2}
                  dot={{ r: 3, strokeWidth: 0, fill: SERIES[j % SERIES.length] }}
                  activeDot={{ r: 5, stroke: "var(--card)", strokeWidth: 2 }}
                  isAnimationActive={false}
                />
              )
            )}
          </Chart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function PieChartView({ c }: { c: PieChartComponent }) {
  const sorted = [...c.items].filter((i) => i.value > 0).sort((a, b) => b.value - a.value);
  const head = sorted.slice(0, MAX_PIE_SLICES);
  const rest = sorted.slice(MAX_PIE_SLICES).reduce((sum, i) => sum + i.value, 0);
  const data = rest > 0 ? [...head, { label: "Other", value: rest }] : head;
  const total = data.reduce((sum, i) => sum + i.value, 0) || 1;
  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-center">
      <div className="size-44 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="label"
              innerRadius="60%"
              outerRadius="100%"
              paddingAngle={data.length > 1 ? 2 : 0}
              stroke="var(--card)"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {data.map((_, i) => (
                <Cell key={i} fill={SERIES[i % SERIES.length]} />
              ))}
            </Pie>
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const item = payload[0];
                const value = item.value as number;
                return (
                  <TooltipBox>
                    <p className="font-medium">{item.name}</p>
                    <p className="tabular-nums">
                      {formatValue(value, c.format, c.currency)} · {((value / total) * 100).toFixed(1)}%
                    </p>
                  </TooltipBox>
                );
              }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="w-full min-w-0 space-y-1.5 text-sm">
        {data.map((item, i) => (
          <li key={i} className="flex items-center gap-2">
            <Swatch color={SERIES[i % SERIES.length]} />
            <span className="min-w-0 flex-1 truncate" title={item.label}>
              {item.label}
            </span>
            <span className="tabular-nums">{formatValue(item.value, c.format, c.currency)}</span>
            <span className="w-12 text-right text-xs text-muted-foreground tabular-nums">
              {((item.value / total) * 100).toFixed(1)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ProgressView({ c }: { c: ProgressComponent }) {
  return (
    <ul className="space-y-3">
      {c.items.map((item, i) => {
        const pct = item.max > 0 ? Math.max(0, Math.min(100, (item.value / item.max) * 100)) : 0;
        return (
          <li key={i}>
            <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
              <span className="min-w-0 truncate" title={item.label}>
                {item.label}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                {formatValue(item.value, item.format, item.currency)} / {formatValue(item.max, item.format, item.currency)}
                <span className="ml-1.5 font-medium text-foreground">{pct.toFixed(0)}%</span>
              </span>
            </div>
            <div
              className="h-2 w-full overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-label={item.label}
              aria-valuenow={Math.round(pct)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className="h-full rounded-full" style={{ width: `${pct}%`, background: SERIES[0] }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function TimelineView({ c }: { c: TimelineComponent }) {
  return (
    <ol className="relative ml-1.5 border-l border-border">
      {c.items.map((item, i) => (
        <li key={i} className="relative pb-4 pl-5 last:pb-0">
          <span
            className="absolute top-1.5 -left-[5px] size-2.5 rounded-full ring-2 ring-card"
            style={{ background: SERIES[0] }}
          />
          <p className="text-xs text-muted-foreground tabular-nums">{item.date}</p>
          <p className="text-sm font-medium">{item.title}</p>
          {item.detail && <p className="text-sm text-muted-foreground">{item.detail}</p>}
        </li>
      ))}
    </ol>
  );
}

function ComparisonView({ c }: { c: ComparisonComponent }) {
  return (
    <div className={cn("grid gap-2", c.items.length >= 2 && "sm:grid-cols-2", c.items.length >= 3 && "lg:grid-cols-3")}>
      {c.items.map((item, i) => (
        <div key={i} className="min-w-0 rounded-lg border border-border bg-card p-3">
          <p className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <Swatch color={SERIES[i % SERIES.length]} />
            {item.title}
          </p>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {item.points.map((p, j) => (
              <li key={j}>{p}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

const CALLOUT_TONES = {
  info: {
    Icon: Info,
    className: "border-sky-300 bg-sky-50 text-sky-950 dark:border-sky-400/40 dark:bg-sky-400/10 dark:text-sky-100",
  },
  warning: {
    Icon: TriangleAlert,
    className:
      "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-400/40 dark:bg-amber-400/10 dark:text-amber-100",
  },
  success: {
    Icon: CircleCheck,
    className:
      "border-emerald-300 bg-emerald-50 text-emerald-950 dark:border-emerald-400/40 dark:bg-emerald-400/10 dark:text-emerald-100",
  },
} as const;

function CalloutView({ c }: { c: CalloutComponent }) {
  const tone = CALLOUT_TONES[c.tone];
  return (
    <div className={cn("flex gap-2 rounded-lg border px-3 py-2.5 text-sm", tone.className)}>
      <tone.Icon className="mt-0.5 size-4 shrink-0" aria-label={c.tone} />
      <p className="min-w-0">{c.text}</p>
    </div>
  );
}

function CalculatorView({ c }: { c: CalculatorComponent }) {
  const defaults = useMemo(() => calculatorDefaults(c), [c]);
  const [values, setValues] = useState<Record<string, number>>(defaults);
  // Raw text per input, so a half-typed "1." or "" isn't forced back to a number.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const results = useMemo(() => computeCalculator(c, values), [c, values]);
  const changed = c.inputs.some((i) => values[i.id] !== defaults[i.id]);

  const setValue = (id: string, value: number) => {
    setValues((v) => ({ ...v, [id]: value }));
    setDrafts((d) => {
      const next = { ...d };
      delete next[id];
      return next;
    });
  };

  const assumptions = c.assumptions === undefined ? [] : Array.isArray(c.assumptions) ? c.assumptions : [c.assumptions];

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2">
        {c.inputs.map((input) => {
          const id = `calc-${input.id}`;
          const hasRange = input.min != null && input.max != null && input.max > input.min;
          const step = input.step ?? (hasRange ? niceStep(input.min!, input.max!) : "any");
          return (
            <div key={input.id} className="min-w-0">
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <label htmlFor={id} className="truncate text-xs text-muted-foreground" title={input.label}>
                  {input.label}
                </label>
                <span className="shrink-0 text-xs font-medium tabular-nums">
                  {formatValue(values[input.id], input.format, input.currency)}
                </span>
              </div>
              <input
                id={id}
                type="number"
                inputMode="decimal"
                value={drafts[input.id] ?? String(values[input.id])}
                min={input.min ?? undefined}
                max={input.max ?? undefined}
                step={step}
                onChange={(e) => {
                  const raw = e.target.value;
                  const n = Number(raw);
                  if (raw.trim() !== "" && Number.isFinite(n)) setValue(input.id, n);
                  else setDrafts((d) => ({ ...d, [input.id]: raw }));
                }}
                onBlur={() =>
                  setDrafts((d) => {
                    const next = { ...d };
                    delete next[input.id];
                    return next;
                  })
                }
                className="h-8 w-full rounded-md border border-input bg-transparent px-2.5 text-sm tabular-nums outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50"
              />
              {hasRange && (
                <input
                  type="range"
                  aria-label={`${input.label} slider`}
                  min={input.min!}
                  max={input.max!}
                  step={step}
                  value={Math.max(input.min!, Math.min(input.max!, values[input.id]))}
                  onChange={(e) => setValue(input.id, Number(e.target.value))}
                  className="mt-2 w-full cursor-pointer"
                  style={{ accentColor: "var(--viz-1)" }}
                />
              )}
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" aria-live="polite">
        {results.map((r, i) => (
          <div
            key={i}
            className={cn(
              "min-w-0 rounded-lg border border-border px-3 py-2.5",
              i === 0 ? "bg-muted sm:col-span-2" : "bg-card"
            )}
          >
            <p className="truncate text-xs text-muted-foreground" title={r.label}>
              {r.label}
            </p>
            <p className={cn("font-semibold tabular-nums break-words", i === 0 ? "text-2xl" : "text-lg")}>
              {r.value === null ? "—" : formatValue(r.value, r.format, r.currency)}
            </p>
            {r.error && <p className="text-xs text-destructive">Couldn&apos;t calculate this value.</p>}
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-start justify-between gap-2">
        {assumptions.length > 0 ? (
          <div className="min-w-0 flex-1 text-xs text-muted-foreground">
            <p className="font-medium">Assumptions</p>
            <ul className="list-disc pl-4">
              {assumptions.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          </div>
        ) : (
          <span />
        )}
        {changed && (
          <Button
            variant="ghost"
            size="xs"
            onClick={() => {
              setValues(defaults);
              setDrafts({});
            }}
          >
            <RotateCcw className="size-3" />
            Reset
          </Button>
        )}
      </div>
    </div>
  );
}

/** A slider step giving ~100 positions, rounded to 1/2/5 × 10^n. */
function niceStep(min: number, max: number): number {
  const raw = (max - min) / 100;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  return (norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10) * mag;
}

function ComponentView({ c }: { c: InteractiveComponent }) {
  switch (c.type) {
    case "metric_cards":
      return <MetricCardsView c={c} />;
    case "table":
      return <TableView c={c} />;
    case "bar_chart":
    case "line_chart":
      return <CartesianChartView c={c} />;
    case "pie_chart":
      return <PieChartView c={c} />;
    case "progress":
      return <ProgressView c={c} />;
    case "timeline":
      return <TimelineView c={c} />;
    case "comparison":
      return <ComparisonView c={c} />;
    case "callout":
      return <CalloutView c={c} />;
    case "calculator":
      return <CalculatorView c={c} />;
  }
}

// --- The block -----------------------------------------------------------------

interface InteractiveBlockProps extends CitationProps {
  source: string;
  /** While the answer streams the JSON is incomplete -- show a placeholder. */
  isStreaming?: boolean;
}

export function InteractiveBlock({ source, isStreaming = false, citationNumbers, onCitationClick }: InteractiveBlockProps) {
  const parsed = useMemo(() => (isStreaming ? null : parseInteractiveBlock(source)), [source, isStreaming]);
  const [showData, setShowData] = useState(false);

  if (isStreaming) {
    return (
      <div className="mb-3 flex h-28 animate-pulse items-center justify-center gap-2 rounded-lg border border-border bg-muted/40 font-sans text-xs text-muted-foreground last:mb-0">
        <LayoutDashboard className="size-4" />
        Building interactive view…
      </div>
    );
  }

  if (!parsed) {
    return (
      <CodeBlock
        language="trench-ui"
        code={source}
        note="This interactive view couldn't be displayed, so its data is shown instead."
      />
    );
  }

  return (
    <div className="mb-3 overflow-hidden rounded-lg border border-border font-sans text-sm leading-normal last:mb-0">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-muted px-3 py-1">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <LayoutDashboard className="size-3.5" />
          Interactive view
        </span>
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={showData ? "Hide data" : "View data"}
            aria-pressed={showData}
            title={showData ? "Hide data" : "View data"}
            onClick={() => setShowData((s) => !s)}
            className={cn("text-muted-foreground hover:text-foreground", showData && "bg-background text-foreground")}
          >
            <CodeXml className="size-3.5" />
          </Button>
          <CopyButton label="Copy as text" text={interactiveBlockToText(parsed)} />
        </div>
      </div>
      {showData && (
        <div className="border-b border-border p-3">
          <CodeBlock language="json" code={source} />
        </div>
      )}
      <div className="flex flex-col gap-5 bg-card p-3 sm:p-4">
        {parsed.components.map((c, i) => (
          <section key={i} className="min-w-0">
            {(c.title || (c.cite && c.cite.length > 0)) && (
              <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1">
                {c.title && <h4 className="text-sm font-semibold">{c.title}</h4>}
                <CitationChips cite={c.cite} citationNumbers={citationNumbers} onCitationClick={onCitationClick} />
              </div>
            )}
            <ComponentView c={c} />
          </section>
        ))}
        {parsed.skipped > 0 && (
          <p className="text-xs text-muted-foreground">
            {parsed.skipped} part{parsed.skipped === 1 ? "" : "s"} of this view couldn&apos;t be displayed.
          </p>
        )}
      </div>
    </div>
  );
}
