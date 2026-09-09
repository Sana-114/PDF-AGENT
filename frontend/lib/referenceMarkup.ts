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
  const pattern = /\[(\d{1,4})\]/g;
  let cursor = 0;
  let result = "";
  for (const match of text.matchAll(pattern)) {
    const index = match.index;
    const label = match[1];
    result += escapeHtml(text.slice(cursor, index));
    result += knownLabels.has(label)
      ? `<mark class="pdf-citation-link" data-reference-label="${label}">[${label}]</mark>`
      : escapeHtml(match[0]);
    cursor = index + match[0].length;
  }
  return result + escapeHtml(text.slice(cursor));
}
