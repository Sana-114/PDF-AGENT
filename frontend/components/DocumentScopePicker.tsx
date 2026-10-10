"use client";

import type { DocumentRecord } from "../lib/api";
import { selectedReadyDocumentIds } from "../lib/documentScope";

export default function DocumentScopePicker({ documents, selectedIds, onChange, label }: {
  documents: DocumentRecord[]; selectedIds: string[]; onChange: (ids: string[]) => void; label: string;
}) {
  const selected = selectedReadyDocumentIds(documents, selectedIds);
  return <details className="document-scope-picker">
    <summary>{label} · 已选 {selected.length} 篇</summary>
    <fieldset aria-label={label}>
      <legend>仅使用勾选文献，不默认发送全库内容</legend>
      <div>{documents.filter((item) => item.status === "ready").map((item) => <label key={item.id} data-document-id={item.id}>
        <input type="checkbox" checked={selected.includes(item.id)} onChange={(event) => onChange(
          event.target.checked ? [...selected, item.id] : selected.filter((id) => id !== item.id),
        )} />
        <span>{item.title || item.original_filename}<small>{item.original_filename}</small></span>
      </label>)}</div>
    </fieldset>
  </details>;
}
