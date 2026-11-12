"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatTokens, formatUsd, toNumber, type DailyUsage, type ModelUsage } from "@/lib/usage";

// Theme tokens (globals.css) so charts follow light/dark mode.
const OWN_KEY = "var(--chart-1)";
const PLATFORM = "var(--chart-4)";
const MODEL_COLORS = ["var(--chart-1)", "var(--chart-4)", "var(--chart-2)", "var(--chart-5)", "var(--chart-3)"];

function shortDate(iso: string) {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function TooltipBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      {children}
    </div>
  );
}

export function DailySpendChart({ daily }: { daily: DailyUsage[] }) {
  const data = daily.map((d) => ({
    date: d.date,
    platform: toNumber(d.platform_cost_usd) ?? 0,
    own: toNumber(d.own_key_cost_usd) ?? 0,
    questions: d.questions,
  }));
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 4, left: -8, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis
            dataKey="date"
            tickFormatter={shortDate}
            tickLine={false}
            axisLine={false}
            minTickGap={24}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
          />
          <YAxis
            tickFormatter={(v: number) => (v === 0 ? "$0" : formatUsd(v))}
            tickLine={false}
            axisLine={false}
            width={76}
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
          />
          <Tooltip
            cursor={{ fill: "var(--muted)", opacity: 0.5 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload as (typeof data)[number];
              return (
                <TooltipBox>
                  <p className="mb-1 font-medium">{shortDate(row.date)}</p>
                  <p>Your/team key: {formatUsd(row.own)}</p>
                  <p>Trench (free): {formatUsd(row.platform)}</p>
                  <p className="text-muted-foreground">
                    {row.questions} question{row.questions === 1 ? "" : "s"}
                  </p>
                </TooltipBox>
              );
            }}
          />
          <Bar dataKey="platform" stackId="cost" fill={PLATFORM} radius={[0, 0, 0, 0]} maxBarSize={28} />
          <Bar dataKey="own" stackId="cost" fill={OWN_KEY} stroke="var(--chart-4)" strokeOpacity={0.25} radius={[4, 4, 0, 0]} maxBarSize={28} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ModelDonut({ models }: { models: ModelUsage[] }) {
  const priced = models
    .map((m) => ({ name: m.model, value: toNumber(m.cost_usd) ?? 0 }))
    .filter((m) => m.value > 0);
  const data = priced.length > 0 ? priced : models.map((m) => ({ name: m.model, value: m.questions }));
  const byCost = priced.length > 0;
  return (
    <div className="h-44 w-44 shrink-0">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius="62%"
            outerRadius="100%"
            paddingAngle={data.length > 1 ? 2 : 0}
            stroke="var(--card)"
            strokeWidth={2}
          >
            {data.map((_, i) => (
              <Cell key={i} fill={MODEL_COLORS[i % MODEL_COLORS.length]} />
            ))}
          </Pie>
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const item = payload[0];
              return (
                <TooltipBox>
                  <p className="font-medium">{item.name}</p>
                  <p>
                    {byCost
                      ? formatUsd(item.value as number)
                      : `${item.value} question${item.value === 1 ? "" : "s"}`}
                  </p>
                </TooltipBox>
              );
            }}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

export function modelColor(index: number) {
  return MODEL_COLORS[index % MODEL_COLORS.length];
}

export function TokenBar({
  input,
  cached,
  output,
}: {
  input: number;
  cached: number;
  output: number;
}) {
  const fresh = Math.max(0, input - cached);
  const total = fresh + cached + output || 1;
  const parts = [
    { label: "Input", value: fresh, color: "var(--chart-4)" },
    { label: "Cached input", value: cached, color: "var(--chart-2)" },
    { label: "Output", value: output, color: "var(--chart-1)" },
  ];
  return (
    <div className="flex flex-col gap-3">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
        {parts.map((p) =>
          p.value > 0 ? (
            <div
              key={p.label}
              style={{ width: `${(p.value / total) * 100}%`, background: p.color }}
              title={`${p.label}: ${p.value.toLocaleString()} tokens`}
            />
          ) : null
        )}
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        {parts.map((p) => (
          <div key={p.label} className="flex min-w-0 items-start gap-1.5">
            <span className="mt-1 size-2.5 shrink-0 rounded-sm" style={{ background: p.color }} />
            <div className="min-w-0">
              <p className="truncate text-muted-foreground">{p.label}</p>
              <p className="font-medium tabular-nums">{formatTokens(p.value)}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
