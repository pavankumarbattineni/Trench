"use client";

import { useRef, type ReactNode } from "react";

import { CopyButton } from "@/components/chat/copy-button";

const LANGUAGE_LABELS: Record<string, string> = {
  js: "JavaScript",
  javascript: "JavaScript",
  ts: "TypeScript",
  typescript: "TypeScript",
  tsx: "TSX",
  jsx: "JSX",
  py: "Python",
  python: "Python",
  json: "JSON",
  sh: "Shell",
  bash: "Bash",
  shell: "Shell",
  sql: "SQL",
  yaml: "YAML",
  yml: "YAML",
  html: "HTML",
  css: "CSS",
  md: "Markdown",
  markdown: "Markdown",
  "trench-ui": "Interactive view (JSON)",
};

/** Pretty-prints JSON for copying when it parses; otherwise returns it as is. */
function prettyJson(code: string): string {
  try {
    return JSON.stringify(JSON.parse(code), null, 2);
  } catch {
    return code;
  }
}

interface CodeBlockProps {
  /** Language from the ```fence ("" when none was given). */
  language: string;
  code: string;
  /** Shown under the header, e.g. why an interactive block fell back to code. */
  note?: ReactNode;
}

/** A fenced code block: a header with the language and a copy button, then
 * the code. Copies exactly what's shown (minus the trailing newline). */
export function CodeBlock({ language, code, note }: CodeBlockProps) {
  const text = code.replace(/\n$/, "");
  const label = LANGUAGE_LABELS[language.toLowerCase()] ?? (language || "Code");
  const isJson = language === "json" || language === "trench-ui";
  return (
    <div className="mb-3 overflow-hidden rounded-lg border border-border font-sans last:mb-0">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-muted px-3 py-1">
        <span className="truncate text-xs text-muted-foreground">{label}</span>
        <CopyButton text={isJson ? prettyJson(text) : text} label={isJson ? "Copy JSON" : "Copy code"} />
      </div>
      {note && (
        <p className="border-b border-border px-3 py-1.5 text-xs text-muted-foreground">{note}</p>
      )}
      <pre className="overflow-x-auto bg-card p-3">
        <code className="block font-mono text-[0.875em] leading-relaxed whitespace-pre">{text}</code>
      </pre>
    </div>
  );
}

/** Tab-separated rows read from a rendered table -- pastes cleanly into
 * spreadsheets and reads fine as plain text. */
export function tableToTsv(table: HTMLTableElement): string {
  return Array.from(table.rows)
    .map((row) =>
      Array.from(row.cells)
        .map((c) => (c.innerText ?? c.textContent ?? "").replace(/\s+/g, " ").trim())
        .join("\t")
    )
    .join("\n");
}

/** A Markdown table with a small "Copy table" control above it. */
export function CopyableTable({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLTableElement>(null);
  return (
    <div className="mb-3 last:mb-0">
      <div className="flex justify-end font-sans">
        <CopyButton
          label="Copy table"
          getText={() => (ref.current ? tableToTsv(ref.current) : "")}
        />
      </div>
      <div className="overflow-x-auto">
        <table ref={ref} className="w-full border-collapse">
          {children}
        </table>
      </div>
    </div>
  );
}
