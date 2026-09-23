function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character] || character,
  );
}

export function linkifyNumericCitations(text: string, knownLabels: ReadonlySet<string>): string {
  // Keep the export name for existing callers; also support native BibTeX keys.
  const pattern = /\[(\d{1,4}|[A-Za-z][A-Za-z0-9+._:-]{0,31})\]/g;
  let cursor = 0;
  let result = "";
  for (const match of text.matchAll(pattern)) {
    const index = match.index;
    const label = match[1];
    result += escapeHtml(text.slice(cursor, index));
    result += knownLabels.has(label) && !label.startsWith("author-year-")
      ? `<mark class="pdf-citation-link" data-reference-label="${label}">[${label}]</mark>`
      : escapeHtml(match[0]);
    cursor = index + match[0].length;
  }
  return result + escapeHtml(text.slice(cursor));
}
