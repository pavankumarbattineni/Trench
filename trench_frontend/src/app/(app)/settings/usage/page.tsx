"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import {
  ArrowDownRight,
  ArrowUpRight,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Coins,
  Download,
  Info,
  MessageSquare,
  PiggyBank,
  Sigma,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "cn";
import { useUsageQueries, useUsageSummary } from "@/hooks/use-usage";
import { getErrorMessage } from "@/lib/errors";
import {
  KEY_OWNER_LABELS,
  STEP_LABELS,
  formatTokens,
  formatUsd,
  getAllUsageQueries,
  toNumber,
  usageToCsv,
  type QueryUsage,
  type UsageTotals,
} from "@/lib/usage";
import { DailySpendChart, ModelDonut, TokenBar, modelColor } from "./usage-charts";

const RANGES = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
] as const;

function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("rounded-2xl border border-border bg-card", className)}>{children}</div>;
}

function Trend({ current, previous }: { current: number; previous: number }) {
  if (previous <= 0 || current === previous) return null;
  const change = ((current - previous) / previous) * 100;
  const up = change > 0;
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className="inline-flex items-center gap-0.5 text-xs font-medium text-muted-foreground"
      title="Compared with the previous period of the same length"
    >
      <Icon className="size-3.5" />
      {Math.abs(change) >= 1000 ? ">999" : Math.abs(change).toFixed(0)}%
    </span>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  hint,
  trend,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  hint?: string;
  trend?: React.ReactNode;
}) {
  return (
    <Card className="flex min-w-0 flex-col gap-2 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="flex size-8 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <Icon className="size-4" />
        </span>
        {trend}
      </div>
      <div className="min-w-0">
        <p className="truncate text-xs text-muted-foreground">{label}</p>
        <p className="truncate text-xl font-semibold tracking-tight tabular-nums">{value}</p>
        {hint && <p className="truncate text-xs text-muted-foreground">{hint}</p>}
      </div>
    </Card>
  );
}

function totalTokens(t: UsageTotals) {
  return t.input_tokens + t.output_tokens;
}

export default function UsagePage() {
  const [days, setDays] = useState<number>(30);
  const [page, setPage] = useState(1);
  const [exporting, setExporting] = useState(false);
  const summary = useUsageSummary(days);
  const queries = useUsageQueries(days, page);

  const changeRange = (next: number) => {
    setDays(next);
    setPage(1);
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      const rows = await getAllUsageQueries(days);
      const blob = new Blob([usageToCsv(rows)], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `trench-usage-last-${days}-days.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setExporting(false);
    }
  };

  const s = summary.data;
  const cur = s?.current;
  const prev = s?.previous;
  const hasUsage = Boolean(cur && cur.questions + cur.input_tokens > 0);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold tracking-tight">Usage &amp; cost</h2>
          <p className="text-sm text-muted-foreground">
            Tokens and cost for every question you ask, priced with LiteLLM&apos;s model prices.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-lg border border-border bg-muted/40 p-0.5" role="tablist" aria-label="Date range">
            {RANGES.map((r) => (
              <button
                key={r.days}
                type="button"
                role="tab"
                aria-selected={days === r.days}
                onClick={() => changeRange(r.days)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                  days === r.days
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {r.label}
              </button>
            ))}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={exportCsv}
            disabled={exporting || !hasUsage}
            aria-label="Export CSV"
          >
            <Download className="size-4" />
            <span className="hidden sm:inline">{exporting ? "Exporting…" : "Export CSV"}</span>
          </Button>
        </div>
      </div>

      {summary.isLoading ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28 rounded-2xl" />
          ))}
          <Skeleton className="col-span-2 h-72 rounded-2xl lg:col-span-4" />
        </div>
      ) : summary.isError ? (
        <Card className="p-6 text-sm text-destructive">{getErrorMessage(summary.error)}</Card>
      ) : !hasUsage || !s || !cur || !prev ? (
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <span className="flex size-12 items-center justify-center rounded-xl bg-muted text-muted-foreground">
            <Coins className="size-6" />
          </span>
          <p className="font-medium">No usage in the last {days} days</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            Ask a question in chat and its token usage and cost will appear here.
          </p>
          <Button nativeButton={false} render={<Link href="/chat">Go to chat</Link>} />
        </Card>
      ) : (
        <>
          {(cur.unpriced_calls > 0 || cur.estimated_calls > 0) && (
            <div className="flex items-start gap-2 rounded-xl border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              <Info className="mt-0.5 size-3.5 shrink-0" />
              <p>
                {cur.unpriced_calls > 0 &&
                  `${cur.unpriced_calls} call${cur.unpriced_calls === 1 ? " uses a model" : "s use models"} without a published price, so ${cur.unpriced_calls === 1 ? "its" : "their"} cost isn't included. `}
                {cur.estimated_calls > 0 &&
                  `${cur.estimated_calls} call${cur.estimated_calls === 1 ? " was" : "s were"} stopped before the provider reported usage; ${cur.estimated_calls === 1 ? "its" : "their"} tokens are estimated.`}
              </p>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Metric
              icon={Coins}
              label="Total cost"
              value={formatUsd(cur.cost_usd)}
              trend={<Trend current={toNumber(cur.cost_usd) ?? 0} previous={toNumber(prev.cost_usd) ?? 0} />}
            />
            <Metric
              icon={MessageSquare}
              label="Questions"
              value={cur.questions.toLocaleString()}
              trend={<Trend current={cur.questions} previous={prev.questions} />}
            />
            <Metric
              icon={Sigma}
              label="Avg. cost / question"
              value={cur.questions ? formatUsd((toNumber(cur.cost_usd) ?? 0) / cur.questions) : "—"}
            />
            <Metric
              icon={PiggyBank}
              label="Tokens used"
              value={formatTokens(totalTokens(cur))}
              hint={`${formatUsd(s.cache_savings_usd)} saved by caching`}
              trend={<Trend current={totalTokens(cur)} previous={totalTokens(prev)} />}
            />
          </div>

          <Card className="p-5">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">Daily cost</h3>
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <span className="size-2.5 rounded-sm" style={{ background: "var(--chart-1)" }} /> Your/team key
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="size-2.5 rounded-sm" style={{ background: "var(--chart-4)" }} /> Trench (free)
                </span>
              </div>
            </div>
            <DailySpendChart daily={s.daily} />
          </Card>

          <div className="grid gap-3 lg:grid-cols-5">
            <Card className="p-5 lg:col-span-3">
              <h3 className="mb-4 text-sm font-semibold">By model</h3>
              <div className="flex flex-col items-center gap-5 2xl:flex-row 2xl:items-start">
                <ModelDonut models={s.by_model} />
                <ul className="flex w-full min-w-0 flex-col gap-2">
                  {s.by_model.map((m, i) => (
                    <li key={`${m.provider}/${m.model}`} className="flex min-w-0 items-start gap-2 text-sm">
                      <span className="mt-1.5 size-2.5 shrink-0 rounded-sm" style={{ background: modelColor(i) }} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate" title={`${m.provider} · ${m.model}`}>
                          {m.model}
                        </p>
                        <p className="text-xs text-muted-foreground tabular-nums">
                          {m.questions} question{m.questions === 1 ? "" : "s"} ·{" "}
                          {formatTokens(m.input_tokens + m.output_tokens)} tokens
                        </p>
                      </div>
                      <span className="shrink-0 text-right font-medium tabular-nums">
                        {m.priced ? formatUsd(m.cost_usd) : "Unpriced"}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </Card>
            <Card className="flex flex-col gap-4 p-5 lg:col-span-2">
              <h3 className="text-sm font-semibold">Token mix</h3>
              <TokenBar input={cur.input_tokens} cached={cur.cached_input_tokens} output={cur.output_tokens} />
              <div className="mt-auto rounded-xl bg-muted/50 p-3 text-xs text-muted-foreground">
                <p className="flex items-center gap-1.5 font-medium text-foreground">
                  <PiggyBank className="size-3.5" /> {formatUsd(s.cache_savings_usd)} saved
                </p>
                <p className="mt-0.5">
                  Cached input tokens are billed at a lower rate than fresh input by most providers.
                </p>
              </div>
            </Card>
          </div>

          <QueryHistory
            items={queries.data?.items ?? []}
            total={queries.data?.total ?? 0}
            page={page}
            pageSize={queries.data?.page_size ?? 20}
            loading={queries.isLoading}
            onPage={setPage}
          />
        </>
      )}
    </div>
  );
}

function QueryHistory({
  items,
  total,
  page,
  pageSize,
  loading,
  onPage,
}: {
  items: QueryUsage[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  onPage: (page: number) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-border px-5 py-3">
        <h3 className="text-sm font-semibold">Questions</h3>
        <span className="text-xs text-muted-foreground">{total.toLocaleString()} total</span>
      </div>
      {loading ? (
        <div className="flex flex-col gap-2 p-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 rounded-lg" />
          ))}
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {items.map((q) => {
            const expanded = open === q.assistant_message_id;
            return (
              <li key={q.assistant_message_id}>
                <button
                  type="button"
                  onClick={() => setOpen(expanded ? null : q.assistant_message_id)}
                  aria-expanded={expanded}
                  className="flex w-full min-w-0 items-center gap-3 px-5 py-3 text-left transition-colors hover:bg-muted/40"
                >
                  <ChevronDown
                    className={cn("size-4 shrink-0 text-muted-foreground transition-transform", expanded && "rotate-180")}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{q.query_preview || "(no text)"}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {new Date(q.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                      {" · "}
                      {q.model}
                      {" · "}
                      {KEY_OWNER_LABELS[q.key_owner] ?? q.key_owner}
                      {!q.thread_id && " · chat deleted"}
                    </p>
                  </div>
                  <div className="hidden shrink-0 text-right text-xs text-muted-foreground tabular-nums sm:block">
                    {formatTokens(q.input_tokens)} in
                    {q.cached_input_tokens > 0 && ` (${formatTokens(q.cached_input_tokens)} cached)`}
                    {" · "}
                    {formatTokens(q.output_tokens)} out
                  </div>
                  <div className="w-20 shrink-0 text-right text-sm font-semibold tabular-nums">
                    {formatUsd(q.cost_usd)}
                    {q.estimated && <span className="block text-[10px] font-normal text-muted-foreground">estimated</span>}
                  </div>
                </button>
                {expanded && (
                  <div className="bg-muted/30 px-5 pb-4 pt-1">
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[480px] text-xs">
                        <thead className="text-left text-muted-foreground">
                          <tr>
                            <th className="py-2 pr-3 font-medium">Step</th>
                            <th className="py-2 pr-3 font-medium">Model</th>
                            <th className="py-2 pr-3 text-right font-medium">Input</th>
                            <th className="py-2 pr-3 text-right font-medium">Cached</th>
                            <th className="py-2 pr-3 text-right font-medium">Output</th>
                            <th className="py-2 text-right font-medium">Cost</th>
                          </tr>
                        </thead>
                        <tbody className="tabular-nums">
                          {q.steps.map((step, i) => (
                            <Fragment key={i}>
                              <tr className="border-t border-border/60">
                                <td className="py-2 pr-3">
                                  {STEP_LABELS[step.step] ?? step.step}
                                  {step.estimated && <span className="ml-1 text-muted-foreground">(est.)</span>}
                                </td>
                                <td className="max-w-[160px] truncate py-2 pr-3" title={step.model}>
                                  {step.model}
                                </td>
                                <td className="py-2 pr-3 text-right">{step.input_tokens.toLocaleString()}</td>
                                <td className="py-2 pr-3 text-right">{step.cached_input_tokens.toLocaleString()}</td>
                                <td className="py-2 pr-3 text-right">{step.output_tokens.toLocaleString()}</td>
                                <td className="py-2 text-right font-medium">{formatUsd(step.cost_usd)}</td>
                              </tr>
                            </Fragment>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {q.thread_id && (
                      <Link
                        href={`/chat/${q.thread_id}`}
                        className="mt-2 inline-flex text-xs font-medium text-primary-foreground underline-offset-4 hover:underline"
                      >
                        Open conversation →
                      </Link>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {pages > 1 && (
        <div className="flex items-center justify-between gap-2 border-t border-border px-5 py-2.5 text-xs text-muted-foreground">
          <span>
            Page {page} of {pages}
          </span>
          <div className="flex gap-1">
            <Button variant="ghost" size="icon" onClick={() => onPage(page - 1)} disabled={page <= 1} aria-label="Previous page">
              <ChevronLeft className="size-4" />
            </Button>
            <Button variant="ghost" size="icon" onClick={() => onPage(page + 1)} disabled={page >= pages} aria-label="Next page">
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
