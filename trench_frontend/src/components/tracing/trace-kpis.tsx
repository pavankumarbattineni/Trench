import { cn } from "cn";

import { Skeleton } from "@/components/ui/skeleton";
import { formatCost, formatDuration, formatPercent, formatTraceTokens, type TraceStats } from "@/lib/traces";

interface Kpi {
  label: string;
  value: string;
  hint?: string;
  tone?: "error";
}

function kpisFor(stats: TraceStats): Kpi[] {
  return [
    { label: "Runs", value: stats.traces.toLocaleString() },
    {
      label: "Error rate",
      value: formatPercent(stats.error_rate),
      hint: stats.errors ? `${stats.errors} failed` : "none failed",
      tone: stats.errors ? "error" : undefined,
    },
    { label: "Latency p50", value: formatDuration(stats.p50_latency_ms) },
    { label: "Latency p95", value: formatDuration(stats.p95_latency_ms) },
    { label: "Tokens", value: formatTraceTokens(stats.total_tokens) },
    { label: "Cost", value: formatCost(stats.total_cost) },
  ];
}

/** One divided strip -- a single instrument panel rather than six cards. */
export function TraceKpis({ stats }: { stats?: TraceStats }) {
  const items = stats ? kpisFor(stats) : null;
  return (
    <dl className="grid grid-cols-2 overflow-hidden rounded-2xl border border-border bg-card sm:grid-cols-3 lg:grid-cols-6">
      {(items ?? Array.from({ length: 6 }, () => null)).map((kpi, index) => (
        <div
          key={kpi?.label ?? index}
          className={cn(
            "flex min-w-0 flex-col gap-1 border-border px-4 py-3",
            // Dividers between cells, whatever the column count.
            "border-b [&:nth-last-child(-n+2)]:border-b-0 sm:[&:nth-last-child(-n+3)]:border-b-0 lg:border-b-0",
            "border-r even:border-r-0 sm:even:border-r sm:[&:nth-child(3n)]:border-r-0 lg:[&:nth-child(3n)]:border-r lg:last:border-r-0"
          )}
        >
          <dt className="text-xs text-muted-foreground">{kpi?.label ?? <Skeleton className="h-3 w-16" />}</dt>
          <dd className="flex min-w-0 items-baseline gap-2">
            {kpi ? (
              <>
                <span
                  className={cn(
                    "text-xl font-semibold tracking-tight tabular-nums",
                    kpi.tone === "error" ? "text-destructive" : "text-foreground"
                  )}
                >
                  {kpi.value}
                </span>
                {kpi.hint && <span className="truncate text-xs text-muted-foreground">{kpi.hint}</span>}
              </>
            ) : (
              <Skeleton className="h-6 w-16" />
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
