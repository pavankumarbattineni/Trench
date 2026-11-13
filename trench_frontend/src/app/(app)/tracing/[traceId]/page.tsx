"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, MessageSquare } from "lucide-react";
import { cn } from "cn";

import { RelativeTime } from "@/components/relative-time";
import { SpanInspector } from "@/components/tracing/span-inspector";
import { SpanWaterfall, useTimeline } from "@/components/tracing/span-waterfall";
import { StatusDot } from "@/components/tracing/status-dot";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useTrace } from "@/hooks/use-traces";
import {
  KIND_LABELS,
  formatCost,
  formatDuration,
  formatTraceTokens,
  statusLabel,
  traceTitle,
  type TraceDetail,
} from "@/lib/traces";

export default function TraceDetailPage({ params }: { params: Promise<{ traceId: string }> }) {
  const { traceId } = use(params);
  const { data: trace, isLoading, isError } = useTrace(traceId);

  if (isLoading) {
    return (
      <main className="mx-auto flex h-full w-full max-w-7xl flex-col gap-4 overflow-y-auto px-4 py-10 [&>*]:shrink-0">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-16 w-full rounded-2xl" />
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
          <Skeleton className="h-96 rounded-2xl" />
          <Skeleton className="h-96 rounded-2xl" />
        </div>
      </main>
    );
  }
  if (isError || !trace) {
    return (
      <main className="mx-auto flex h-full w-full max-w-7xl flex-col items-center gap-3 overflow-y-auto px-4 py-16 text-center">
        <h1 className="text-lg font-semibold">This run isn&apos;t available</h1>
        <p className="text-sm text-muted-foreground">Runs are kept for 14 days, and you can only see your own.</p>
        <Button variant="outline" size="sm" nativeButton={false} render={<Link href="/tracing">Back to Tracing</Link>} />
      </main>
    );
  }
  return <TraceView trace={trace} />;
}

function TraceView({ trace }: { trace: TraceDetail }) {
  const firstError = trace.spans.find((s) => s.status === "error");
  const firstLlm = trace.spans.find((s) => s.run_type === "llm");
  const [selectedId, setSelectedId] = useState((firstError ?? firstLlm ?? trace.spans[0])?.id ?? "");
  const { timings } = useTimeline(trace.spans);
  const selected = useMemo(
    () => trace.spans.find((s) => s.id === selectedId) ?? trace.spans[0],
    [trace.spans, selectedId]
  );
  const title = traceTitle(trace);

  const metrics = [
    { label: "Duration", value: formatDuration(trace.latency_ms) },
    {
      label: "Tokens",
      value: formatTraceTokens(trace.total_tokens),
      detail: `${formatTraceTokens(trace.prompt_tokens)} in, ${formatTraceTokens(trace.completion_tokens)} out`,
    },
    { label: "Cost", value: formatCost(trace.total_cost) },
    { label: "LLM calls", value: String(trace.llm_calls) },
    { label: "Searches", value: String(trace.retriever_calls) },
    {
      label: "Steps",
      value: String(trace.span_count),
      detail: trace.error_count ? `${trace.error_count} failed` : undefined,
      error: trace.error_count > 0,
    },
  ];

  return (
    <main className="mx-auto flex h-full w-full max-w-7xl flex-col gap-4 overflow-y-auto px-4 py-10 [&>*]:shrink-0">
      <Link
        href="/tracing"
        className="inline-flex w-fit items-center gap-1 rounded text-sm text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <ArrowLeft className="size-4" aria-hidden="true" /> Tracing
      </Link>

      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <h1
            title={title}
            className="line-clamp-3 text-xl leading-snug font-bold tracking-tight [overflow-wrap:anywhere] sm:text-2xl"
          >
            {title}
          </h1>
          <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <StatusDot status={trace.status} />
              {statusLabel(trace.status)}
            </span>
            <span>
              {KIND_LABELS[trace.kind] ?? trace.name}
              {trace.knowledge_type && `, ${trace.knowledge_type} knowledge`}
            </span>
            <RelativeTime value={trace.start_time} className="text-sm" />
            {trace.models.map((model) => (
              <span key={model} className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground">
                {model}
              </span>
            ))}
            {!trace.complete && <span className="text-foreground">Still arriving from LangSmith…</span>}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {trace.thread_id && (
            <Button
              variant="outline"
              size="sm"
              nativeButton={false}
              render={
                <Link href={`/chat/${trace.thread_id}`}>
                  <MessageSquare className="size-3.5" aria-hidden="true" /> Open conversation
                </Link>
              }
            />
          )}
        </div>
      </header>

      {trace.error && (
        <div role="alert" className="rounded-2xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <span className="font-semibold">Run failed: </span>
          <span className="[overflow-wrap:anywhere]">{trace.error.split("\n")[0]}</span>
        </div>
      )}

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-border bg-border sm:grid-cols-3 lg:grid-cols-6">
        {metrics.map((metric) => (
          <div key={metric.label} className="min-w-0 bg-card px-4 py-2.5">
            <dt className="text-xs text-muted-foreground">{metric.label}</dt>
            <dd className="flex min-w-0 items-baseline gap-1.5">
              <span className={cn("text-lg font-semibold tabular-nums", metric.error && "text-destructive")}>
                {metric.value}
              </span>
              {metric.detail && <span className="truncate text-xs text-muted-foreground">{metric.detail}</span>}
            </dd>
          </div>
        ))}
      </dl>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        <SpanWaterfall spans={trace.spans} selectedId={selected?.id ?? null} onSelect={setSelectedId} />
        {selected && (
          <div className="lg:sticky lg:top-0 lg:flex lg:max-h-[calc(100dvh-5rem)] lg:flex-col">
            <SpanInspector
              key={selected.id}
              span={selected}
              offsetMs={timings.get(selected.id)?.offset ?? 0}
            />
          </div>
        )}
      </div>
    </main>
  );
}
