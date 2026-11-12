import { apiClient } from "@/lib/api";

// Decimal amounts arrive as strings (exact) -- convert at the edge.
type Money = string | number | null;

export interface UsageTotals {
  cost_usd: Money;
  questions: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  unpriced_calls: number;
  estimated_calls: number;
}

export interface DailyUsage {
  date: string;
  platform_cost_usd: Money;
  own_key_cost_usd: Money;
  questions: number;
}

export interface ModelUsage {
  provider: string;
  model: string;
  questions: number;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  cost_usd: Money;
  priced: boolean;
}

export interface UsageSummary {
  days: number;
  current: UsageTotals;
  previous: UsageTotals;
  cache_savings_usd: Money;
  daily: DailyUsage[];
  by_model: ModelUsage[];
}

export type KeyOwner = "platform" | "personal" | "tenant";

export interface UsageStep {
  step: "generate" | "condense" | "reformulate" | "title" | "summary" | string;
  provider: string;
  model: string;
  key_owner: KeyOwner;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  cost_usd: Money;
  estimated: boolean;
}

export interface QueryUsage {
  assistant_message_id: string;
  thread_id: string | null;
  query_preview: string;
  created_at: string;
  provider: string;
  model: string;
  key_owner: KeyOwner;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  cost_usd: Money;
  fully_priced: boolean;
  estimated: boolean;
  steps: UsageStep[];
}

export interface QueryUsagePage {
  total: number;
  page: number;
  page_size: number;
  items: QueryUsage[];
}

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export async function getUsageSummary(days: number): Promise<UsageSummary> {
  const { data } = await apiClient.get<UsageSummary>("/api/v1/usage/summary", {
    params: { days, tz: browserTimeZone() },
  });
  return data;
}

export async function getUsageQueries(
  days: number,
  page: number,
  pageSize = 20
): Promise<QueryUsagePage> {
  const { data } = await apiClient.get<QueryUsagePage>("/api/v1/usage/queries", {
    params: { days, page, page_size: pageSize },
  });
  return data;
}

/** Every question in the range (for CSV export), fetched page by page. */
export async function getAllUsageQueries(days: number): Promise<QueryUsage[]> {
  const all: QueryUsage[] = [];
  for (let page = 1; ; page++) {
    const batch = await getUsageQueries(days, page, 200);
    all.push(...batch.items);
    if (all.length >= batch.total || batch.items.length === 0) return all;
  }
}

// --- Formatting -------------------------------------------------------------

export function toNumber(value: Money): number | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** "$12.40", "$0.0213", "$0.00041", "<$0.00001"; null -> "Unpriced". */
export function formatUsd(value: Money): string {
  const n = toNumber(value);
  if (n === null) return "Unpriced";
  if (n === 0) return "$0.00";
  const abs = Math.abs(n);
  if (abs < 0.00001) return "<$0.00001";
  const digits = abs >= 1 ? 2 : abs >= 0.01 ? 4 : 5;
  return `$${n.toFixed(digits)}`;
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
  return String(n);
}

export const STEP_LABELS: Record<string, string> = {
  generate: "Answer",
  condense: "Follow-up rewrite",
  reformulate: "Search retry",
  title: "Chat title",
  summary: "Chat summary",
};

export const KEY_OWNER_LABELS: Record<KeyOwner, string> = {
  platform: "Trench (free)",
  personal: "Your API key",
  tenant: "Team API key",
};

function csvCell(value: string | number | boolean | null): string {
  const text = value === null ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function usageToCsv(rows: QueryUsage[]): string {
  const header = [
    "date",
    "question",
    "provider",
    "model",
    "paid_by",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "cost_usd",
    "estimated",
  ];
  const lines = rows.map((r) =>
    [
      r.created_at,
      r.query_preview,
      r.provider,
      r.model,
      KEY_OWNER_LABELS[r.key_owner] ?? r.key_owner,
      r.input_tokens,
      r.cached_input_tokens,
      r.output_tokens,
      toNumber(r.cost_usd),
      r.estimated,
    ]
      .map(csvCell)
      .join(",")
  );
  return [header.join(","), ...lines].join("\n");
}
