import type { WritingOutlineResponse } from "./api";

/** Copy the compact draft only; supplementary claims remain a separate review area. */
export function writingMarkdown(outline: WritingOutlineResponse): string {
  const sections = outline.sections.map(
    (section) => `## ${section.title}\n\n${section.content_markdown}`,
  );
  const references = outline.references.map(
    (item) => `[${item.citation_key}] ${item.formatted_citation}`,
  );
  return [
    `# ${outline.proposed_title}`,
    ...sections,
    "## References",
    references.join("\n\n") || "<!-- 暂无经过核验的参考文献 -->",
  ].join("\n\n");
}
