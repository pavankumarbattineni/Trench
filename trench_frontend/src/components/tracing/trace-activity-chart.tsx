"use client";

import { useMemo } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Skeleton } from "@/components/ui/skeleton";
import { formatDuration, type TraceDayStat } from "@/lib/traces";

interface ChartRow {
  date: string;
  label: string;
  ok: number;
  errors: number;
  p50: number | null;
}

/** Every day of the period (local calendar days, matching the `tz` the stats
 * were bucketed in), including quiet ones, so the axis is continuous. */
function fillDays(days: number, data: TraceDayStat[]): ChartRow[] {
  const byDate = new Map(data.map((d) => [d.date, d]));
  const rows: ChartRow[] = [];
  const today = new Date();
  for (let offset = days - 1; offset >= 0; offset--) {
    const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - offset);
    const key = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
    const stat = byDate.get(key);
    rows.push({
      date: key,
      label: day.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      ok: stat ? stat.traces - stat.errors : 0,
      errors: stat?.errors ?? 0,
      p50: stat?.p50_latency_ms ?? null,
    });
  }
  return rows;
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: { payload: ChartRow }[] }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      <p className="mb-1 font-medium">{row.label}</p>
      <p className="text-muted-foreground">
        <span className="text-foreground tabular-nums">{row.ok + row.errors}</span> runs
        {row.errors > 0 && (
          <>
            {", "}
            <span className="text-destructive tabular-nums">{row.errors}</span> failed
          </>
        )}
      </p>
      <p className="text-muted-foreground">
        Median latency <span className="text-foreground tabular-nums">{formatDuration(row.p50)}</span>
      </p>
    </div>
  );
}

export function TraceActivityChart({ days, data, loading }: { days: number; data?: TraceDayStat[]; loading: boolean }) {
  const rows = useMemo(() => fillDays(days, data ?? []), [days, data]);
  const latencyPoints = rows.filter((r) => r.p50 != null).length;
  return (
    <section aria-labelledby="trace-activity" className="rounded-2xl border border-border bg-card p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="trace-activity" className="text-sm font-semibold">
          Activity
        </h2>
        <div className="flex items-center gap-3 text-xs text-muted-foreground" aria-hidden="true">
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm bg-[var(--chart-4)]" /> Runs
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm bg-destructive" /> Failed
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-3 rounded bg-[var(--viz-1)]" /> Median latency
          </span>
        </div>
      </div>
      {loading ? (
        <Skeleton className="h-44 w-full rounded-xl" />
      ) : (
        <div className="h-44 w-full" role="img" aria-label={`Runs per day over the last ${days} days`}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: -12 }}>
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                interval="preserveStartEnd"
                minTickGap={16}
              />
              <YAxis
                yAxisId="runs"
                allowDecimals={false}
                tickLine={false}
                axisLine={false}
                width={36}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              />
              <YAxis yAxisId="latency" orientation="right" hide />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--muted)", opacity: 0.6 }} />
              <Bar yAxisId="runs" dataKey="ok" stackId="runs" fill="var(--chart-4)" maxBarSize={28} />
              <Bar
                yAxisId="runs"
                dataKey="errors"
                stackId="runs"
                fill="var(--destructive)"
                radius={[3, 3, 0, 0]}
                maxBarSize={28}
              />
              <Line
                yAxisId="latency"
                dataKey="p50"
                type="monotone"
                stroke="var(--viz-1)"
                strokeWidth={2}
                // A line needs two points; show dots while there are few.
                dot={latencyPoints <= 2 ? { r: 3, fill: "var(--viz-1)" } : false}
                connectNulls
                isAnimationActive={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}
