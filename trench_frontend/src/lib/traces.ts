import {
  Bot,
  Brain,
  Database,
  FileText,
  Link2,
  Search,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { apiClient } from "@/lib/api";
import { browserTimeZone } from "@/lib/usage";

// --- Types (app/schemas/trace.py) ------------------------------------------

export type TraceStatus = "success" | "error" | "pending";
export type TraceKind = "chat_turn" | "thread_title";
export type SpanType = "chain" | "llm" | "tool" | "retriever" | "embedding" | "prompt" | "parser" | string;

export interface TraceSummary {
  id: string;
  kind: TraceKind;
  knowledge_type: "personal" | "company" | null;
  name: string;
  status: TraceStatus;
  error: string | null;
  thread_id: string | null;
  start_time: string;
  end_time: string | null;
  latency_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost: number | null;
  span_count: number;
  llm_calls: number;
  tool_calls: number;
  retriever_calls: number;
  error_count: number;
  models: string[];
  input_preview: string | null;
  output_preview: string | null;
  complete: boolean;
}

export interface TraceListResponse {
  items: TraceSummary[];
  total: number;
  page: number;
  page_size: number;
  enabled: boolean;
  last_synced_at: string | null;
}

export interface TraceMessage {
  role: "system" | "user" | "assistant" | "tool" | string;
  content: string;
  name?: string;
  tool_calls?: { name?: string; args?: unknown }[];
}

export interface TraceSpan {
  id: string;
  parent_id: string | null;
  position: number;
  depth: number;
  name: string;
  run_type: SpanType;
  status: TraceStatus;
  error: string | null;
  start_time: string;
  end_time: string | null;
  latency_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost: number | null;
  model: string | null;
  inputs: unknown;
  outputs: unknown;
  inputs_truncated: boolean;
  outputs_truncated: boolean;
  meta: Record<string, unknown> | null;
}

export interface TraceDetail extends TraceSummary {
  spans: TraceSpan[];
}

export interface TraceDayStat {
  date: string;
  traces: number;
  errors: number;
  p50_latency_ms: number | null;
  total_tokens: number;
  total_cost: number;
}

export interface TraceStats {
  days: number;
  traces: number;
  errors: number;
  error_rate: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  total_tokens: number;
  total_cost: number;
  by_day: TraceDayStat[];
  by_model: { model: string; calls: number; total_tokens: number; total_cost: number; avg_latency_ms: number | null }[];
}

export interface TraceListParams {
  days: number;
  status?: TraceStatus;
  kind?: TraceKind;
  q?: string;
  page: number;
  page_size: number;
}

// --- API ---------------------------------------------------------------------

export async function listTraces(params: TraceListParams): Promise<TraceListResponse> {
  const { data } = await apiClient.get<TraceListResponse>("/api/v1/traces", { params });
  return data;
}

export async function getTraceStats(days: number): Promise<TraceStats> {
  const { data } = await apiClient.get<TraceStats>("/api/v1/traces/stats", {
    params: { days, tz: browserTimeZone() },
  });
  return data;
}

export async function getTrace(traceId: string): Promise<TraceDetail> {
  const { data } = await apiClient.get<TraceDetail>(`/api/v1/traces/${traceId}`);
  return data;
}

export async function syncTraces(): Promise<{ enabled: boolean; synced: number }> {
  const { data } = await apiClient.post<{ enabled: boolean; synced: number }>("/api/v1/traces/sync");
  return data;
}

// --- Formatting --------------------------------------------------------------

/** 850 ms, 4.21 s, 12.4 s, 1 m 05 s. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes} m ${String(seconds).padStart(2, "0")} s`;
}

/** 980, 12.4k, 1.2M. */
export function formatTraceTokens(value: number | null | undefined): string {
  if (!value) return "0";
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

/** $0.00017, $0.48, $12.30 -- small amounts keep two significant digits. */
export function formatCost(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toPrecision(2)}`;
  return `$${value.toFixed(2)}`;
}

export function formatPercent(rate: number): string {
  if (rate === 0) return "0%";
  if (rate < 0.001) return "<0.1%";
  return `${(rate * 100).toFixed(rate < 0.1 ? 1 : 0)}%`;
}

export function statusLabel(status: TraceStatus): string {
  return status === "error" ? "Failed" : status === "pending" ? "In progress" : "Succeeded";
}

export const KIND_LABELS: Record<TraceKind, string> = {
  chat_turn: "Chat answer",
  thread_title: "Chat title",
};

/** What the run was about: the question, else the run's name. */
export function traceTitle(trace: TraceSummary): string {
  return trace.input_preview?.split("\n")[0] || KIND_LABELS[trace.kind] || trace.name;
}

interface SpanTypeStyle {
  label: string;
  icon: LucideIcon;
  /** A theme token, for bars and icons. */
  color: string;
}

const SPAN_TYPES: Record<string, SpanTypeStyle> = {
  chain: { label: "Step", icon: Link2, color: "var(--chart-4)" },
  llm: { label: "LLM", icon: Brain, color: "var(--viz-1)" },
  tool: { label: "Tool", icon: Wrench, color: "var(--viz-4)" },
  retriever: { label: "Retriever", icon: Search, color: "var(--viz-3)" },
  embedding: { label: "Embedding", icon: Database, color: "var(--viz-7)" },
  prompt: { label: "Prompt", icon: FileText, color: "var(--muted-foreground)" },
  parser: { label: "Parser", icon: FileText, color: "var(--muted-foreground)" },
};

export function spanTypeStyle(type: SpanType): SpanTypeStyle {
  return SPAN_TYPES[type] ?? { label: type, icon: Bot, color: "var(--muted-foreground)" };
}

// The chat graph's nodes (app/graph/rag_graph.py) by what they do.
const SPAN_NAMES: Record<string, string> = {
  agentic_rag_chat: "Chat answer",
  "trench.thread_title": "Chat title",
  load_thread_state: "Load conversation",
  validate_knowledge_access: "Check knowledge access",
  condense_question: "Rewrite follow-up",
  retrieve: "Search documents",
  grade_documents: "Grade results",
  reformulate_query: "Retry search",
  generate: "Write answer",
  check_output_guardrail: "Safety check",
  build_citations: "Build citations",
  persist: "Save answer",
  summarize_history: "Summarize conversation",
  LangGraph: "Graph",
};

/** Human names for graph nodes and other snake_case steps. */
export function spanDisplayName(span: TraceSpan): string {
  if (SPAN_NAMES[span.name]) return SPAN_NAMES[span.name];
  if (/^[a-z0-9]+(_[a-z0-9]+)+$/.test(span.name)) {
    const words = span.name.replace(/_/g, " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  }
  return span.name;
}

/** LangChain/LangGraph plumbing: routing functions (`_route_…`) and
 * pass-through runnables. Hidden by default; the root never is. */
export function isFrameworkSpan(span: TraceSpan): boolean {
  if (span.run_type !== "chain" || !span.parent_id) return false;
  return (
    span.name.startsWith("_") ||
    /^(RunnableSequence|RunnableLambda|RunnableParallel|ChannelWrite|ChannelRead|Branch<)/.test(span.name)
  );
}
