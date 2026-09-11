/** Trench's system prompt asks the model to cite retrieved chunks as
 * plain "[n]" text (see app/graph/rag_graph.py's context-building step),
 * so citation markers arrive as ordinary text inside the generated
 * markdown, not as real links. This rewrites `[n]` into a markdown link
 * pointing at a synthetic "#cite-n" href (only for n within the actual
 * citation count, so an unrelated "[3]" in prose isn't hijacked) --
 * MarkdownContent's `a` override then renders that specific href as a
 * clickable citation marker instead of following it as a real link. */
export function linkifyCitations(content: string, citationCount: number): string {
  if (citationCount === 0) return content;
  return content.replace(/\[(\d+)\]/g, (match, numStr: string) => {
    const index = Number(numStr);
    return index >= 1 && index <= citationCount ? `[${match}](#cite-${index})` : match;
  });
}

export const CITATION_HREF_PREFIX = "#cite-";
