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

type CitationText = { label: string; citation_text?: string | null };

// PDF.js may divide a citation into several font runs. Resolve across the page's
// text items, then mark only the original characters within each run.
export function linkifyPageCitations(
  items: readonly string[], mentions: readonly CitationText[], knownLabels: ReadonlySet<string>,
): string[] {
  const normalize = (text: string) => text.normalize("NFKC").replace(/\s/g, "");
  const keys = new Map<string, Set<string>>();
  for (const mention of mentions) {
    const text = mention.citation_text || "";
    const bracket = /^\[(\d{1,4}|[A-Za-z][A-Za-z0-9+._:-]{0,31})\]$/.exec(text);
    const nativeKey = bracket?.[1] === mention.label && !mention.label.startsWith("author-year-");
    if (!knownLabels.has(mention.label)
      || (!nativeKey && (!/[A-Za-zÀ-Þ]/.test(text) || text.startsWith("[")))) continue;
    const key = normalize(text);
    if (!key) continue;
    const labels = keys.get(key) || new Set<string>();
    labels.add(mention.label);
    keys.set(key, labels);
  }
  const raw = items.join(" ");
  let normalized = "";
  const positions: number[] = [];
  for (let offset = 0; offset < raw.length;) {
    const char = String.fromCodePoint(raw.codePointAt(offset)!);
    const value = normalize(char);
    for (let index = 0; index < value.length; index++) positions.push(offset);
    normalized += value;
    offset += char.length;
  }
  const ranges: { start: number; end: number; label: string }[] = [];
  for (const [key, labels] of keys) {
    if (labels.size !== 1) continue;
    let at = normalized.indexOf(key);
    while (at >= 0) {
      const start = positions[at];
      const last = positions[at + key.length - 1];
      const end = last + String.fromCodePoint(raw.codePointAt(last)!).length;
      if (key.startsWith("[") || (!/[\p{L}\p{N}_]/u.test(raw[start - 1] || "")
        && !/[\p{L}\p{N}_]/u.test(raw[end] || ""))) {
        ranges.push({ start, end, label: [...labels][0] });
      }
      at = normalized.indexOf(key, at + key.length);
    }
  }
  // Overlapping labels are uncertain; do not silently pick a destination.
  const safe = ranges.filter((r, i) => !ranges.some((other, j) =>
    i !== j && r.start < other.end && other.start < r.end && r.label !== other.label));
  let base = 0;
  return items.map((text) => {
    const fragments = safe.filter(r => r.start < base + text.length && r.end > base)
      .sort((a, b) => a.start - b.start);
    let html = "", cursor = 0;
    for (const r of fragments) {
      const start = Math.max(0, r.start - base), end = Math.min(text.length, r.end - base);
      if (start < cursor) continue;
      html += linkifyNumericCitations(text.slice(cursor, start), knownLabels);
      html += `<mark class="pdf-citation-link" data-reference-label="${escapeHtml(r.label)}">${escapeHtml(text.slice(start, end))}</mark>`;
      cursor = end;
    }
    html += linkifyNumericCitations(text.slice(cursor), knownLabels);
    base += text.length + 1;
    return html;
  });
}
