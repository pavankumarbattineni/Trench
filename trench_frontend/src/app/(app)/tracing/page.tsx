"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Activity, ChevronLeft, ChevronRight, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import { cn } from "cn";

import { RelativeTime } from "@/components/relative-time";
import { TraceActivityChart } from "@/components/tracing/trace-activity-chart";
import { TraceKpis } from "@/components/tracing/trace-kpis";
import { TraceTable } from "@/components/tracing/trace-table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useSyncTraces, useTraceStats, useTraces } from "@/hooks/use-traces";
import type { TraceKind, TraceStatus } from "@/lib/traces";

const PERIODS = [
  { days: 1, label: "24 hours" },
  { days: 7, label: "7 days" },
  { days: 14, label: "14 days" },
] as const;
const STATUS_ITEMS: Record<string, string> = { all: "All statuses", success: "Succeeded", error: "Failed" };
const KIND_ITEMS: Record<string, string> = { chat_turn: "Chat answers", thread_title: "Chat titles", all: "All runs" };
const PAGE_SIZE = 25;
const DEFAULTS = { days: "7", status: "all", kind: "chat_turn", page: "1", q: "" };

/** Filters live in the URL, so a filtered view survives reloads and the
 * back button from a trace. */
function useFilters() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const get = (key: keyof typeof DEFAULTS) => params.get(key) ?? DEFAULTS[key];
  const set = (changes: Partial<Record<keyof typeof DEFAULTS, string | number>>) => {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(changes)) {
      const text = String(value);
      if (text === DEFAULTS[key as keyof typeof DEFAULTS]) next.delete(key);
      else next.set(key, text);
    }
    const query = next.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  };
  const days = [1, 7, 14].includes(Number(get("days"))) ? Number(get("days")) : 7;
  const status = (["all", "success", "error"].includes(get("status")) ? get("status") : "all") as TraceStatus | "all";
  const kind = (["all", "chat_turn", "thread_title"].includes(get("kind")) ? get("kind") : "chat_turn") as TraceKind | "all";
  const page = Math.max(1, Number.parseInt(get("page"), 10) || 1);
  return { days, status, kind, page, q: get("q"), set };
}

function TracingView() {
  const { days, status, kind, page, q, set } = useFilters();
  const [search, setSearch] = useState(q);

  // Debounced search into the URL.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (search.trim() !== q) set({ q: search.trim(), page: 1 });
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on typing
  }, [search]);

  const list = useTraces({
    days,
    status: status === "all" ? undefined : status,
    kind: kind === "all" ? undefined : kind,
    q: q || undefined,
    page,
    page_size: PAGE_SIZE,
  });
  const stats = useTraceStats(days);
  const sync = useSyncTraces();

  const refresh = () =>
    sync.mutate(undefined, {
      onSuccess: (result) => {
        if (!result.enabled) toast.info("Tracing isn't set up on this server.");
        else if (result.synced) toast.success(`Synced ${result.synced} run${result.synced === 1 ? "" : "s"} from LangSmith`);
        else toast.success("Already up to date");
      },
      onError: () => toast.error("Couldn't reach LangSmith. Try again in a moment."),
    });

  const data = list.data;
  const filtersActive = status !== "all" || kind !== "chat_turn" || Boolean(q);
  const from = data && data.total ? (data.page - 1) * data.page_size + 1 : 0;
  const to = data ? Math.min(data.page * data.page_size, data.total) : 0;

  return (
    <main className="mx-auto flex h-full w-full max-w-6xl flex-col gap-5 overflow-y-auto px-4 py-10 [&>*]:shrink-0">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight">Tracing</h1>
          <p className="text-sm text-muted-foreground">
            How each answer was produced, step by step: searches, model calls, timings, tokens and cost.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-lg border border-border bg-muted/40 p-0.5" role="tablist" aria-label="Period">
            {PERIODS.map((period) => (
              <button
                key={period.days}
                type="button"
                role="tab"
                aria-selected={days === period.days}
                onClick={() => set({ days: period.days, page: 1 })}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                  days === period.days
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {period.label}
              </button>
            ))}
          </div>
          <Button variant="outline" size="sm" onClick={refresh} disabled={sync.isPending}>
            <RefreshCw className={cn("size-3.5", sync.isPending && "animate-spin")} aria-hidden="true" />
            {sync.isPending ? "Syncing…" : "Refresh"}
          </Button>
        </div>
      </div>
      {data?.last_synced_at && (
        <p className="-mt-3 text-xs text-muted-foreground">
          Last sync: <RelativeTime value={data.last_synced_at} />
        </p>
      )}

      {data && !data.enabled ? (
        <EmptyPanel
          title="Tracing isn't set up on this server"
          body="Turn on LangSmith tracing in the backend (LANGCHAIN_TRACING, LANGSMITH_API_KEY, LANGCHAIN_PROJECT) and every answer will appear here."
        />
      ) : (
        <>
          <TraceKpis stats={stats.data} />
          <TraceActivityChart days={days} data={stats.data?.by_day} loading={stats.isLoading} />

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-0 flex-1 basis-full sm:max-w-xs sm:basis-auto">
              <Search
                className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search questions and answers"
                aria-label="Search runs"
                className="pl-8"
              />
            </div>
            <Select items={STATUS_ITEMS} value={status} onValueChange={(value) => value && set({ status: value, page: 1 })}>
              <SelectTrigger className="w-36" aria-label="Status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                <SelectItem value="success">Succeeded</SelectItem>
                <SelectItem value="error">Failed</SelectItem>
              </SelectContent>
            </Select>
            <Select items={KIND_ITEMS} value={kind} onValueChange={(value) => value && set({ kind: value, page: 1 })}>
              <SelectTrigger className="w-36" aria-label="Run type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="chat_turn">Chat answers</SelectItem>
                <SelectItem value="thread_title">Chat titles</SelectItem>
                <SelectItem value="all">All runs</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {list.isLoading ? (
            <div className="flex flex-col gap-px overflow-hidden rounded-2xl border border-border">
              {Array.from({ length: 6 }).map((_, index) => (
                <Skeleton key={index} className="h-14 rounded-none" />
              ))}
            </div>
          ) : list.isError ? (
            <EmptyPanel title="Runs didn't load" body="Reload the page to try again." />
          ) : data && data.items.length > 0 ? (
            <>
              <TraceTable traces={data.items} />
              {data.total > data.page_size && (
                <nav aria-label="Pages" className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
                  <span className="tabular-nums">
                    {from}–{to} of {data.total}
                  </span>
                  <span className="flex gap-1">
                    <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => set({ page: page - 1 })}>
                      <ChevronLeft className="size-4" aria-hidden="true" /> Newer
                    </Button>
                    <Button variant="outline" size="sm" disabled={to >= data.total} onClick={() => set({ page: page + 1 })}>
                      Older <ChevronRight className="size-4" aria-hidden="true" />
                    </Button>
                  </span>
                </nav>
              )}
            </>
          ) : filtersActive ? (
            <EmptyPanel title="No runs match these filters" body="Try another period, status or search term." />
          ) : (
            <EmptyPanel
              title="No runs yet"
              body="Ask a question in chat and its run appears here a few seconds after the answer finishes."
              action={<Button nativeButton={false} render={<Link href="/chat">Go to chat</Link>} />}
            />
          )}
        </>
      )}
    </main>
  );
}

function EmptyPanel({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-2xl border border-dashed border-border px-6 py-14 text-center">
      <span className="mb-3 flex size-12 items-center justify-center rounded-xl bg-muted text-muted-foreground">
        <Activity className="size-6" aria-hidden="true" />
      </span>
      <h2 className="font-medium">{title}</h2>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">{body}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export default function TracingPage() {
  return (
    <Suspense fallback={null}>
      <TracingView />
    </Suspense>
  );
}
