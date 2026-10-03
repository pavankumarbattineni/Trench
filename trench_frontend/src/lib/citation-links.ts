/** Trench's system prompt asks the model to cite retrieved chunks as
 * plain ASCII "[n]" text (see app/graph/rag_graph.py's context-building
 * step), so citation markers arrive as ordinary text inside the
 * generated markdown, not as real links. This rewrites a citation marker
 * into a markdown link pointing at a synthetic "#cite-n" href (only for
 * n that's actually one of this message's citation_numbers, so an
 * unrelated "[3]" in prose isn't hijacked) -- MarkdownContent's `a`
 * override then renders that specific href as a clickable citation
 * marker instead of following it as a real link or leaving it as
 * unclickable text.
 *
 * Matches both the instructed ASCII "[n]" and the full-width/CJK "【n】"
 * variant as a defensive fallback: the model has been observed to
 * sometimes drift into full-width brackets despite the system prompt
 * explicitly requiring ASCII ones, and an uninteractive, unexplained
 * citation marker is a worse failure mode than being lenient about which
 * bracket glyphs count as one.
 *
 * `validNumbers` must be the set of `citation_number`s on the message's
 * chunks, NOT a 1..count range -- chunks the model didn't end up citing
 * are dropped server-side before they ever reach here (see
 * build_citations), so the surviving numbers are rarely a contiguous
 * range starting at 1. */
export function linkifyCitations(content: string, validNumbers: ReadonlySet<number>): string {
  if (validNumbers.size === 0) return content;
  return content.replace(/\[(\d+)\]|【(\d+)】/g, (match, ascii?: string, fullWidth?: string) => {
    const index = Number(ascii ?? fullWidth);
    return validNumbers.has(index) ? `[${match}](#cite-${index})` : match;
  });
}

export const CITATION_HREF_PREFIX = "#cite-";
