import ReactMarkdown, { type Components, type Options } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

import { CodeBlock, CopyableTable } from "@/components/chat/code-block";
import { InteractiveBlock } from "@/components/chat/interactive-block";
import { MermaidDiagram } from "@/components/chat/mermaid-diagram";
import { CITATION_HREF_PREFIX, linkifyCitations } from "@/lib/citation-links";
import { INTERACTIVE_LANGUAGE, looksLikeInteractiveJson } from "@/lib/interactive";
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

/** If a <pre>'s hast node wraps a fenced code block, returns its language
 * ("" when none) and raw source; otherwise null. Handled at the <pre>
 * level (not <code>) so diagrams and interactive views aren't nested
 * inside a <pre> element. */
function fencedCode(node: unknown): { language: string; code: string } | null {
  const code = (node as HastLike | undefined)?.children?.[0];
  if (code?.tagName !== "code") return null;
  const classes = code.properties?.className;
  const languageClass = Array.isArray(classes)
    ? classes.find((c): c is string => typeof c === "string" && c.startsWith("language-"))
    : undefined;
  const text = (n: HastLike): string =>
    n.type === "text" ? (n.value ?? "") : (n.children ?? []).map(text).join("");
  return { language: languageClass?.slice("language-".length) ?? "", code: text(code) };
}

function buildComponents(
  onCitationClick?: (index: number) => void,
  isStreaming = false,
  citationNumbers?: ReadonlySet<number>
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
            className="mx-0.5 inline-flex h-4 min-w-4 -translate-y-0.5 items-center justify-center rounded bg-emerald-100 px-1 align-middle font-sans text-[0.65rem] font-semibold text-emerald-900 hover:bg-emerald-200 dark:bg-emerald-400/20 dark:text-emerald-300 dark:hover:bg-emerald-400/30"
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
    // Sizes relative to the 17px response text so headings stay larger than it.
    h1: ({ ...props }) => <h1 {...props} className="mb-2 text-[1.25em] font-semibold" />,
    h2: ({ ...props }) => <h2 {...props} className="mb-2 text-[1.125em] font-semibold" />,
    h3: ({ ...props }) => <h3 {...props} className="mb-2 text-[1em] font-semibold" />,
    strong: ({ ...props }) => <strong {...props} className="font-semibold" />,
    blockquote: ({ ...props }) => (
      <blockquote {...props} className="mb-3 border-l-2 border-border pl-3 text-muted-foreground" />
    ),
    code: ({ className, children, ...props }) => {
      const isBlock = /language-/.test(className ?? "");
      if (isBlock) {
        return (
          <code
            className={cn(
              "block overflow-x-auto rounded-lg bg-muted p-3 font-mono text-[0.875em]",
              className
            )}
            {...props}
          >
            {children}
          </code>
        );
      }
      return (
        <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.875em]" {...props}>
          {children}
        </code>
      );
    },
    pre: ({ node, ...props }) => {
      const block = fencedCode(node);
      if (block?.language === "mermaid") {
        return <MermaidDiagram code={block.code} isStreaming={isStreaming} />;
      }
      if (
        block?.language === INTERACTIVE_LANGUAGE ||
        (block?.language === "json" && !isStreaming && looksLikeInteractiveJson(block.code))
      ) {
        return (
          <InteractiveBlock
            source={block.code}
            isStreaming={isStreaming}
            citationNumbers={citationNumbers}
            onCitationClick={onCitationClick}
          />
        );
      }
      if (block) return <CodeBlock language={block.language} code={block.code} />;
      return <pre {...props} className="mb-3 last:mb-0" />;
    },
    table: ({ children }) => <CopyableTable>{children}</CopyableTable>,
    th: ({ ...props }) => (
      <th {...props} className="border border-border px-2 py-1 text-left font-semibold" />
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
    // Assistant response typography: Source Serif 4, ~17px, line-height 1.7.
    // Same element while streaming and after, so fonts never change mid-answer.
    <div className="font-serif text-[17px] leading-[1.7]">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={buildComponents(onCitationClick, isStreaming, citationNumbers)}
      >
        {processed}
      </ReactMarkdown>
    </div>
  );
}
