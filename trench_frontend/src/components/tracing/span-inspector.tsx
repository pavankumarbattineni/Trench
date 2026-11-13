"use client";

import { useState } from "react";
import { AlertTriangle, FileText } from "lucide-react";
import { cn } from "cn";

import { CopyButton } from "@/components/chat/copy-button";
import { formatFullDateTime } from "@/lib/relative-time";
import {
  formatCost,
  formatDuration,
  formatTraceTokens,
  spanDisplayName,
  spanTypeStyle,
  statusLabel,
  type TraceMessage,
  type TraceSpan,
} from "@/lib/traces";
import { StatusDot } from "./status-dot";

type Tab = "input" | "output" | "metadata";

interface SpanInspectorProps {
  span: TraceSpan;
  /** ms from the start of the trace. */
  offsetMs: number;
}

export function SpanInspector({ span, offsetMs }: SpanInspectorProps) {
  const [tab, setTab] = useState<Tab>("input");
  const style = spanTypeStyle(span.run_type);
  const Icon = style.icon;
  const payload = tab === "input" ? span.inputs : tab === "output" ? span.outputs : span.meta;
  const truncated = (tab === "input" && span.inputs_truncated) || (tab === "output" && span.outputs_truncated);

  return (
    <section
      aria-label="Step details"
      className="flex min-w-0 flex-col overflow-hidden rounded-2xl border border-border bg-card"
    >
      <header className="border-b border-border px-4 py-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-medium text-foreground"
            style={{ background: `color-mix(in srgb, ${style.color} 16%, transparent)` }}
          >
            <Icon className="size-3" style={{ color: style.color }} aria-hidden="true" />
            {style.label}
          </span>
          <StatusDot status={span.status} />
          <span>{statusLabel(span.status)}</span>
        </div>
        <h2 className="mt-1.5 truncate text-base font-semibold" title={span.name}>
          {spanDisplayName(span)}
        </h2>
      </header>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-border px-4 py-3 text-sm">
        <Metric label="Duration" value={formatDuration(span.latency_ms)} />
        <Metric label="Started" value={`+${formatDuration(offsetMs)}`} title={formatFullDateTime(span.start_time)} />
        {span.total_tokens > 0 && (
          <Metric
            label="Tokens"
            value={formatTraceTokens(span.total_tokens)}
            detail={`${formatTraceTokens(span.prompt_tokens)} in, ${formatTraceTokens(span.completion_tokens)} out`}
          />
        )}
        {span.total_cost != null && <Metric label="Cost" value={formatCost(span.total_cost)} />}
        {span.model && (
          <div className="col-span-2 min-w-0">
            <dt className="text-xs text-muted-foreground">Model</dt>
            <dd className="truncate font-mono text-sm">{span.model}</dd>
          </div>
        )}
      </dl>

      {span.error && (
        <div role="alert" className="mx-4 mt-3 rounded-xl border border-destructive/30 bg-destructive/5 p-3">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-destructive">
            <AlertTriangle className="size-3.5" aria-hidden="true" /> Error
          </p>
          <pre className="mt-1 max-h-40 overflow-auto font-mono text-xs break-words whitespace-pre-wrap text-destructive">
            {span.error}
          </pre>
        </div>
      )}

      <div className="flex items-center justify-between gap-2 px-4 pt-3">
        <div role="tablist" aria-label="Step data" className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5">
          {(["input", "output", "metadata"] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              onClick={() => setTab(value)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                tab === value ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
              )}
            >
              {value}
            </button>
          ))}
        </div>
        {payload != null && <CopyButton label={`Copy ${tab}`} text={JSON.stringify(payload, null, 2)} />}
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-4 pt-2 pb-4" role="tabpanel">
        {/* Keyed by tab: each tab starts with fresh fold states. */}
        <Payload key={tab} value={payload} />
        {truncated && (
          <p className="mt-2 text-xs text-muted-foreground">
            Trimmed to 16 KB; the copy above is the stored part.
          </p>
        )}
      </div>
    </section>
  );
}

function Metric({ label, value, detail, title }: { label: string; value: string; detail?: string; title?: string }) {
  return (
    <div className="min-w-0" title={title}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium tabular-nums">
        {value}
        {detail && <span className="ml-1.5 text-xs font-normal text-muted-foreground">{detail}</span>}
      </dd>
    </div>
  );
}

function isMessageList(value: unknown): value is { messages: TraceMessage[] } {
  if (!value || typeof value !== "object" || !("messages" in value)) return false;
  const messages = (value as { messages: unknown }).messages;
  return (
    Array.isArray(messages) &&
    messages.every((m) => m && typeof m === "object" && "role" in m && "content" in m)
  );
}

interface TraceDocument {
  page_content: string;
  metadata?: Record<string, unknown>;
}

function isDocumentList(value: unknown): value is { documents: TraceDocument[] } {
  if (!value || typeof value !== "object" || !("documents" in value)) return false;
  const docs = (value as { documents: unknown }).documents;
  return Array.isArray(docs) && docs.every((d) => d && typeof d === "object" && "page_content" in d);
}

/** An LLM run's chat completion output, as one assistant message. */
function choiceMessage(value: unknown): TraceMessage | null {
  if (!value || typeof value !== "object" || !("choices" in value)) return null;
  const choices = (value as { choices: unknown }).choices;
  const message = Array.isArray(choices) ? (choices[0] as { message?: TraceMessage })?.message : null;
  return message && typeof message.content === "string" ? message : null;
}

function Payload({ value }: { value: unknown }) {
  if (value == null || (typeof value === "object" && Object.keys(value as object).length === 0)) {
    return <p className="py-6 text-center text-sm text-muted-foreground">Nothing recorded.</p>;
  }
  if (isMessageList(value)) return <MessageList messages={value.messages} />;
  if (isDocumentList(value)) return <DocumentList documents={value.documents} />;
  const choice = choiceMessage(value);
  if (choice) return <MessageList messages={[choice]} />;
  if (typeof value === "object" && "_truncated" in (value as object)) {
    return <CodeView text={String((value as { _truncated: string })._truncated)} />;
  }
  return <CodeView text={JSON.stringify(value, null, 2)} />;
}

function CodeView({ text }: { text: string }) {
  return (
    <pre className="overflow-x-auto rounded-xl bg-muted/60 p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">
      {text}
    </pre>
  );
}

const ROLE_LABEL: Record<string, string> = {
  system: "System",
  user: "User",
  assistant: "Assistant",
  tool: "Tool result",
};

function MessageList({ messages }: { messages: TraceMessage[] }) {
  return (
    <ol className="flex flex-col gap-3">
      {messages.map((message, index) => (
        <li key={index} className="min-w-0">
          <p className="mb-1 flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
            <span
              className={cn(
                "rounded px-1.5 py-0.5",
                message.role === "user" && "bg-primary text-primary-foreground",
                message.role === "assistant" && "bg-[color-mix(in_srgb,var(--viz-1)_16%,transparent)] text-foreground",
                message.role === "system" && "bg-muted",
                message.role === "tool" && "bg-[color-mix(in_srgb,var(--viz-4)_18%,transparent)] text-foreground"
              )}
            >
              {ROLE_LABEL[message.role] ?? message.role}
            </span>
            {message.name && <span className="font-mono font-normal">{message.name}</span>}
          </p>
          {message.content && <MessageText text={message.content} collapsible={message.role === "system"} />}
        </li>
      ))}
    </ol>
  );
}

function MessageText({ text, collapsible }: { text: string; collapsible: boolean }) {
  const long = text.length > 600;
  const [open, setOpen] = useState(!collapsible || !long);
  return (
    <div>
      <p
        className={cn(
          "text-sm leading-relaxed break-words whitespace-pre-wrap",
          collapsible && !open && "line-clamp-6"
        )}
      >
        {text}
      </p>
      {collapsible && long && (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="mt-1 text-xs font-medium text-muted-foreground underline underline-offset-2 hover:text-foreground"
        >
          {open ? "Show less" : `Show full prompt (${text.length.toLocaleString()} characters)`}
        </button>
      )}
    </div>
  );
}

function DocumentList({ documents }: { documents: TraceDocument[] }) {
  if (documents.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No matching passages.</p>;
  }
  return (
    <ol className="flex flex-col gap-2">
      {documents.map((doc, index) => {
        const meta = doc.metadata ?? {};
        const name = typeof meta.document_name === "string" ? meta.document_name : `Passage ${index + 1}`;
        const score = typeof meta.score === "number" ? meta.score : null;
        return (
          <li key={index} className="min-w-0 rounded-xl border border-border">
            <p className="flex items-center gap-2 border-b border-border bg-muted/40 px-3 py-1.5 text-xs">
              <FileText className="size-3.5 shrink-0 text-[var(--viz-3)]" aria-hidden="true" />
              <span className="truncate font-medium">{name}</span>
              {typeof meta.chunk_index === "number" && (
                <span className="shrink-0 text-muted-foreground">part {meta.chunk_index + 1}</span>
              )}
              {score != null && (
                <span className="ml-auto shrink-0 text-muted-foreground tabular-nums" title="Relevance score">
                  {score.toFixed(3)}
                </span>
              )}
            </p>
            <p className="line-clamp-6 px-3 py-2 text-xs leading-relaxed break-words whitespace-pre-wrap">
              {doc.page_content}
            </p>
          </li>
        );
      })}
    </ol>
  );
}
