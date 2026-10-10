export function selectedReadyDocumentIds(
  documents: Array<{ id: string; status: string }>, selectedIds: string[],
): string[] {
  const selected = new Set(selectedIds);
  return [...new Set(documents.filter((item) => item.status === "ready" && selected.has(item.id)).map((item) => item.id))];
}
