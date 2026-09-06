export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type DocumentStatus = "queued" | "processing" | "ready" | "failed";

export interface DocumentRecord {
  id: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  status: DocumentStatus;
  error_message: string | null;
  title: string | null;
  page_count: number | null;
  arxiv_id: string | null;
  arxiv_version: number | null;
  duplicate_of_id: string | null;
  duplicate_score: number | null;
  duplicate_recommendation: string | null;
  created_at: string;
  updated_at: string;
}

interface DocumentListResponse {
  items: DocumentRecord[];
  total: number;
  limit: number;
  offset: number;
}

interface UploadResponse {
  document: DocumentRecord;
  exact_duplicate: boolean;
  message: string;
}

async function assertResponse(response: Response): Promise<Response> {
  if (response.ok) return response;
  let message = `请求失败 (${response.status})`;
  try {
    const body = (await response.json()) as { detail?: string };
    if (body.detail) message = body.detail;
  } catch {
    // Preserve the status-based fallback message.
  }
  throw new Error(message);
}

export async function listDocuments(): Promise<DocumentListResponse> {
  const response = await assertResponse(await fetch(`${API_BASE_URL}/documents`, { cache: "no-store" }));
  return response.json();
}

export async function uploadDocument(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/documents`, { method: "POST", body: form }),
  );
  return response.json();
}

export async function deleteDocument(documentId: string): Promise<void> {
  await assertResponse(
    await fetch(`${API_BASE_URL}/documents/${documentId}`, { method: "DELETE" }),
  );
}

export function documentFileUrl(documentId: string): string {
  return `${API_BASE_URL}/documents/${documentId}/file`;
}

