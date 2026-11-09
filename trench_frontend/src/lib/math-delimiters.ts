/**
 * Trench's system prompt asks the model for LaTeX delimited the standard way
 * -- `\( ... \)` inline and `\[ ... \]` for display equations -- but
 * Markdown treats `\(`/`\[` as escaped punctuation (so the raw LaTeX leaks
 * into the page as text) and remark-math only recognizes `$` delimiters.
 * This rewrites the LaTeX delimiters into remark-math's `$$` form before the
 * Markdown is parsed:
 *
 * - `\[ ... \]` at the start of a line -> a display block (`$$` fences on
 *   their own lines), keeping the line's indentation so equations inside
 *   list items stay inside them.
 * - `\[ ... \]` mid-line, and `\( ... \)` -> inline math (`$$...$$`; with
 *   remark-math's singleDollarTextMath disabled, double dollars mark inline
 *   math and a lone "$5" in prose is never mistaken for math).
 *
 * Code (fenced blocks and inline code) is never touched. Unclosed
 * delimiters -- e.g. mid-stream, before `\]` has arrived -- are left as-is
 * until they close.
 */

// Fenced (``` / ~~~, including one still open mid-stream) and inline code.
const CODE_SEGMENT = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/;

// A "math" span holding nothing but citation markers -- e.g. `\([1]\)` or
// `\([2][3]\)`, which models produce once asked for LaTeX. That's never real
// math; unwrap it so it renders as a clickable citation, not math text.
const CITATION_ONLY_MATH = /\\[([]\s*((?:\[\d+\]\s*,?\s*)+?)\s*\\[)\]]/g;

/** Escapes `#` and `%` the model left bare inside math -- e.g.
 * `\text{#states}` or `50%`. In LaTeX `#` is reserved (KaTeX refuses to
 * render the whole equation) and `%` starts a comment that silently drops
 * the rest of it. Neither has a legitimate unescaped use in an answer. */
function escapeSpecials(body: string): string {
  return body.replace(/(?<!\\)([#%])/g, "\\$1");
}

function convertMath(text: string): string {
  return (
    text
      .replace(CITATION_ONLY_MATH, (_match, citations: string) =>
        citations.replace(/\s*,?\s*/g, "")
      )
      // Display equations that start a line: \[ ... \] -> $$ block. The
      // closing $$ must end its line (remark-math only closes a block on a
      // bare $$ line), so anything after \] -- typically citations like
      // "\] [1][8]" -- moves to its own following line; otherwise the block
      // never closes and swallows the rest of the answer.
      .replace(
        /^([ \t]*)\\\[([\s\S]+?)\\\][ \t]*(.*)$/gm,
        (_match, indent: string, body: string, rest: string) => {
          const lines = escapeSpecials(body.trim())
            .split("\n")
            .map((line) => `${indent}${line.trim()}`);
          const block = `${indent}$$\n${lines.join("\n")}\n${indent}$$`;
          return rest ? `${block}\n${indent}${rest}` : block;
        }
      )
      // Remaining \[ ... \] (mid-line) and all \( ... \) -> inline math.
      .replace(/\\\[([\s\S]+?)\\\]/g, (_match, body: string) => `$$${escapeSpecials(body.trim())}$$`)
      .replace(/\\\(([\s\S]+?)\\\)/g, (_match, body: string) => `$$${escapeSpecials(body.trim())}$$`)
  );
}

export function normalizeMathDelimiters(content: string): string {
  if (!content.includes("\\(") && !content.includes("\\[")) return content;
  // split() with a capture group puts code segments at the odd indices.
  return content
    .split(CODE_SEGMENT)
    .map((segment, i) => (i % 2 === 1 ? segment : convertMath(segment)))
    .join("");
}
