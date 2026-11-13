import Link from "next/link";
import { Brain, Search } from "lucide-react";
import { cn } from "cn";

import { RelativeTime } from "@/components/relative-time";
import {
  KIND_LABELS,
  formatCost,
  formatDuration,
  formatTraceTokens,
  traceTitle,
  type TraceSummary,
} from "@/lib/traces";
import { StatusDot } from "./status-dot";

const GRID = "md:grid md:grid-cols-[minmax(0,1fr)_7rem_9rem_5rem_5.5rem_5.5rem] md:items-center md:gap-4";

export function TraceTable({ traces }: { traces: TraceSummary[] }) {
  const maxLatency = Math.max(1, ...traces.map((t) => t.latency_ms ?? 0));
  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card">
      <div className={cn(GRID, "hidden border-b border-border bg-muted/50 px-4 py-2 text-xs font-medium text-muted-foreground")}>
        <span>Run</span>
        <span>Started</span>
        <span>Duration</span>
        <span className="text-right">Tokens</span>
        <span className="text-right">Cost</span>
        <span>Steps</span>
      </div>
      <ul>
        {traces.map((trace) => (
          <li key={trace.id} className="border-b border-border last:border-b-0">
            <Link
              href={`/tracing/${trace.id}`}
              className={cn(
                GRID,
                "group block px-4 py-3 transition-colors hover:bg-muted/50 focus-visible:bg-muted/60 focus-visible:outline-none"
              )}
            >
              <span className="flex min-w-0 items-start gap-3">
                <StatusDot status={trace.status} className="mt-1.5" />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium group-hover:underline group-hover:underline-offset-2">
                    {traceTitle(trace)}
                  </span>
                  <span className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
                    <span className="shrink-0">
                      {KIND_LABELS[trace.kind] ?? trace.name}
                      {trace.knowledge_type && `, ${trace.knowledge_type} knowledge`}
                    </span>
                    {trace.models[0] && <span className="hidden truncate font-mono text-[11px] sm:inline">{trace.models[0]}</span>}
                    {trace.error_count > 0 && (
                      <span className="shrink-0 text-destructive">
                        {trace.error_count} error{trace.error_count === 1 ? "" : "s"}
                      </span>
                    )}
                  </span>
                </span>
              </span>

              {/* Mobile: one compact metrics line */}
              <span className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 pl-5 text-xs text-muted-foreground tabular-nums md:hidden">
                <RelativeTime value={trace.start_time} />
                <span>{formatDuration(trace.latency_ms)}</span>
                <span>{formatTraceTokens(trace.total_tokens)} tokens</span>
                <span>{formatCost(trace.total_cost)}</span>
              </span>

              <RelativeTime value={trace.start_time} className="hidden text-sm md:inline" />
              <span className="hidden min-w-0 items-center gap-2 md:flex">
                <span className="w-14 shrink-0 text-sm tabular-nums">{formatDuration(trace.latency_ms)}</span>
                <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted" aria-hidden="true">
                  <span
                    className={cn("block h-full rounded-full", trace.status === "error" ? "bg-destructive" : "bg-[var(--chart-4)]")}
                    style={{ width: `${Math.max(4, ((trace.latency_ms ?? 0) / maxLatency) * 100)}%` }}
                  />
                </span>
              </span>
              <span className="hidden text-right text-sm tabular-nums md:block">{formatTraceTokens(trace.total_tokens)}</span>
              <span className="hidden text-right text-sm tabular-nums md:block">{formatCost(trace.total_cost)}</span>
              <span className="hidden items-center gap-3 text-xs text-muted-foreground tabular-nums md:flex">
                <span className="flex items-center gap-1" title={`${trace.llm_calls} LLM call${trace.llm_calls === 1 ? "" : "s"}`}>
                  <Brain className="size-3.5 text-[var(--viz-1)]" aria-hidden="true" />
                  {trace.llm_calls}
                </span>
                <span
                  className="flex items-center gap-1"
                  title={`${trace.retriever_calls} document search${trace.retriever_calls === 1 ? "" : "es"}`}
                >
                  <Search className="size-3.5 text-[var(--viz-3)]" aria-hidden="true" />
                  {trace.retriever_calls}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
