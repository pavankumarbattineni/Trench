import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CITATION_HREF_PREFIX, linkifyCitations } from "@/lib/citation-links";
import { cn } from "cn";

function buildComponents(onCitationClick?: (index: number) => void): Components {
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
    pre: ({ ...props }) => <pre {...props} className="mb-3 last:mb-0" />,
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
  /** Number of citations available on this message -- bounds which
   * "[n]" text sequences get turned into clickable markers. */
  citationCount?: number;
  onCitationClick?: (index: number) => void;
}

export function MarkdownContent({
  content,
  citationCount = 0,
  onCitationClick,
}: MarkdownContentProps) {
  const processed =
    citationCount > 0 ? linkifyCitations(content, citationCount) : content;

  return (
    <div className="text-sm leading-relaxed">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={buildComponents(onCitationClick)}>
        {processed}
      </ReactMarkdown>
    </div>
  );
}
