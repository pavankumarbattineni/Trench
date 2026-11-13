/**
 * Cleans up model-written Mermaid source before rendering.
 *
 * LLMs routinely produce Mermaid that the parser rejects even though the
 * intent is obvious, e.g. `B[Embeddings (n × d_model)]` -- inside a
 * square-bracket label, "(" starts a different node shape, so the label
 * has to be quoted. This applies the minimal, always-valid fixes:
 *
 * - Quotes node labels (`[...]`, `{...}`) and edge labels (`|...|`) that
 *   contain characters Mermaid treats as syntax. Quoting is valid for
 *   every label, so it can't break a diagram that already parsed.
 * - Drops `%%` comments that trail a statement (only whole-line comments
 *   are allowed).
 * - Drops styling (`classDef`, `class`, `style`, `linkStyle`, `:::name`)
 *   so diagrams follow the app's light/dark theme instead of hard-coded
 *   colors chosen by the model.
 */

// Characters that end or change a label's meaning when left unquoted.
const NEEDS_QUOTES = /[()[\]{}<>|#;&"]/;

function quote(label: string): string {
  return `"${label.trim().replace(/"/g, "#quot;")}"`;
}

/** Applies `fn` only to the parts of `line` outside "double-quoted" text,
 * so already-quoted labels are never touched twice. */
function outsideQuotes(line: string, fn: (segment: string) => string): string {
  return line
    .split(/("[^"]*")/)
    .map((segment, i) => (i % 2 === 1 ? segment : fn(segment)))
    .join("");
}

export function cleanMermaidSource(source: string): string {
  return source
    .trim()
    // Models sometimes escape quotes inside a label (`C[Return \"Blocked\"]`);
    // a raw `"` there would split the label below, so it becomes Mermaid's
    // quote entity first.
    .replace(/\\"/g, "#quot;")
    .split("\n")
    .map((rawLine) => {
      const trimmed = rawLine.trim();
      // Whole-line comments and %%{init}%% directives are valid as-is.
      if (trimmed.startsWith("%%")) return rawLine;
      if (/^(classDef|class|style|linkStyle)\s/.test(trimmed)) return "";

      let line = rawLine.replace(/\s+%%(?!\{).*$/, "").replace(/:::[\w-]+/g, "");

      // [label] -- skips other bracket shapes like [[sub]], [(db)], [/x/].
      line = outsideQuotes(line, (s) =>
        s.replace(/([\w-]+)\[(?![[(/\\"])([^[\]"]*?)\](?!\])/g, (match, id, label) =>
          NEEDS_QUOTES.test(label) ? `${id}[${quote(label)}]` : match
        )
      );
      // {decision label}
      line = outsideQuotes(line, (s) =>
        s.replace(/([\w-]+)\{(?![{"])([^{}"]*)\}/g, (match, id, label) =>
          NEEDS_QUOTES.test(label) ? `${id}{${quote(label)}}` : match
        )
      );
      // -->|edge label|
      line = outsideQuotes(line, (s) =>
        s.replace(/\|([^|"]+)\|/g, (match, label) =>
          NEEDS_QUOTES.test(label) ? `|${quote(label)}|` : match
        )
      );
      return line;
    })
    .join("\n");
}
