import ReactMarkdown, { type Components, type Options } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

import { MermaidDiagram } from "@/components/chat/mermaid-diagram";
import { CITATION_HREF_PREFIX, linkifyCitations } from "@/lib/citation-links";
import { normalizeMathDelimiters } from "@/lib/math-delimiters";
import { cn } from "cn";

// singleDollarTextMath off: only $$...$$ is math (see math-delimiters.ts),
// so prose like "costs $5 and $10" is never rendered as an equation.
const REMARK_PLUGINS: Options["remarkPlugins"] = [
  remarkGfm,
  [remarkMath, { singleDollarTextMath: false }],
];
// A malformed equation renders as its source in the error color instead
// of throwing and blanking the whole message.
const REHYPE_PLUGINS: Options["rehypePlugins"] = [
  [rehypeKatex, { throwOnError: false, strict: "ignore", errorColor: "var(--destructive)" }],
];

interface HastLike {
  type?: string;
  tagName?: string;
  value?: string;
  properties?: { className?: unknown };
  children?: HastLike[];
}

/** If a <pre>'s hast node wraps a ```mermaid code block, returns its raw
 * source; otherwise null. Handled at the <pre> level (not <code>) so the
 * diagram isn't nested inside a <pre> element. */
function mermaidSource(node: unknown): string | null {
  const code = (node as HastLike | undefined)?.children?.[0];
  const classes = code?.properties?.className;
  if (
    code?.tagName !== "code" ||
    !Array.isArray(classes) ||
    !classes.includes("language-mermaid")
  ) {
    return null;
  }
  const text = (n: HastLike): string =>
    n.type === "text" ? (n.value ?? "") : (n.children ?? []).map(text).join("");
  return text(code);
}

function buildComponents(
  onCitationClick?: (index: number) => void,
  isStreaming = false
): Components {
  return {
    a: ({ href, children, ...props }) => {
      if (href?.startsWith(CITATION_HREF_PREFIX)) {
        const index = Number(href.slice(CITATION_HREF_PREFIX.length));
        return (
          <button
            type="button"
            onClick={() => onCitationClick?.(index)}
            aria-label={`View source ${index}`}
            className="mx-0.5 inline-flex h-4 min-w-4 -translate-y-0.5 items-center justify-center rounded bg-emerald-100 px-1 align-middle text-[0.65rem] font-semibold text-emerald-900 hover:bg-emerald-200 dark:bg-emerald-400/20 dark:text-emerald-300 dark:hover:bg-emerald-400/30"
          >
            {index}
          </button>
        );
      }
      return (
        <a {...props} href={href} target="_blank" rel="noreferrer" className="underline underline-offset-2">
          {children}
        </a>
      );
    },
    p: ({ ...props }) => <p {...props} className="mb-3 last:mb-0" />,
    ul: ({ ...props }) => <ul {...props} className="mb-3 list-disc pl-5 last:mb-0" />,
    ol: ({ ...props }) => <ol {...props} className="mb-3 list-decimal pl-5 last:mb-0" />,
    li: ({ ...props }) => <li {...props} className="mb-1" />,
    h1: ({ ...props }) => <h1 {...props} className="mb-2 text-lg font-semibold" />,
    h2: ({ ...props }) => <h2 {...props} className="mb-2 text-base font-semibold" />,
    h3: ({ ...props }) => <h3 {...props} className="mb-2 text-sm font-semibold" />,
    strong: ({ ...props }) => <strong {...props} className="font-semibold" />,
    blockquote: ({ ...props }) => (
      <blockquote {...props} className="mb-3 border-l-2 border-border pl-3 text-muted-foreground" />
    ),
    code: ({ className, children, ...props }) => {
      const isBlock = /language-/.test(className ?? "");
      if (isBlock) {
        return (
          <code
            className={cn("block overflow-x-auto rounded-lg bg-muted p-3 text-xs", className)}
            {...props}
          >
            {children}
          </code>
        );
      }
      return (
        <code className="rounded bg-muted px-1 py-0.5 text-[0.85em]" {...props}>
          {children}
        </code>
      );
    },
    pre: ({ node, ...props }) => {
      const mermaid = mermaidSource(node);
      if (mermaid !== null) {
        return <MermaidDiagram code={mermaid} isStreaming={isStreaming} />;
      }
      return <pre {...props} className="mb-3 last:mb-0" />;
    },
    table: ({ ...props }) => (
      <div className="mb-3 overflow-x-auto">
        <table {...props} className="w-full border-collapse text-sm" />
      </div>
    ),
    th: ({ ...props }) => (
      <th {...props} className="border border-border px-2 py-1 text-left font-medium" />
    ),
    td: ({ ...props }) => <td {...props} className="border border-border px-2 py-1" />,
  };
}

interface MarkdownContentProps {
  content: string;
  /** The message's actual citation_numbers -- which "[n]" text sequences
   * get turned into clickable markers (see linkifyCitations). */
  citationNumbers?: ReadonlySet<number>;
  onCitationClick?: (index: number) => void;
  /** True while this message is still being generated -- diagrams wait to
   * render until their (otherwise incomplete) source has fully arrived. */
  isStreaming?: boolean;
}

export function MarkdownContent({
  content,
  citationNumbers,
  onCitationClick,
  isStreaming = false,
}: MarkdownContentProps) {
  // Math first (\( \) / \[ \] -> $$ form), then citations -- which skip
  // the resulting $$...$$ segments so "[n]" inside an equation is never
  // turned into a citation link.
  const withMath = normalizeMathDelimiters(content);
  const processed = citationNumbers?.size
    ? linkifyCitations(withMath, citationNumbers)
    : withMath;

  return (
    <div className="text-sm leading-relaxed">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={buildComponents(onCitationClick, isStreaming)}
      >
        {processed}
      </ReactMarkdown>
    </div>
  );
}
