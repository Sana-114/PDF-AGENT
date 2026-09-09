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
  parser_name: string | null;
  parser_version: string | null;
  duplicate_of_id: string | null;
  duplicate_score: number | null;
  duplicate_recommendation: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentProgress {
  document_id: string;
  status: DocumentStatus;
  completed_pages: number;
  page_count: number | null;
  percentage: number;
  resumable: boolean;
  updated_at: string | null;
}

export interface DocumentOutlineNode {
  text: string;
  level: 1 | 2 | 3;
  page_number: number;
  block_id: string;
  bbox: number[] | null;
  children: DocumentOutlineNode[];
}

interface DocumentOutlineResponse {
  document_id: string;
  items: DocumentOutlineNode[];
}

export interface DocumentReference {
  reference_id: string;
  label: string;
  text: string;
  page_number: number;
  bbox: number[] | null;
  block_ids: string[];
}

export interface CitationMention {
  citation_id: string;
  label: string;
  page_number: number;
  block_id: string;
  bbox: number[] | null;
  context: string;
}

interface DocumentReferencesResponse {
  document_id: string;
  items: DocumentReference[];
  mentions: CitationMention[];
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

export interface EvidenceAnchor {
  evidence_id: string;
  chunk_id: string | null;
  document_id: string;
  document_title: string | null;
  page_number: number;
  block_ids: string[];
  bbox: number[] | null;
  section: string | null;
  source_type: "text" | "abstract" | "table" | "figure" | "formula" | "reference";
  retrieval_mode: "lexical" | "vector" | "hybrid" | "reranked";
  quote: string;
  score: number;
}

export interface AgentAskResponse {
  answer: string;
  claims: Array<{ text: string; evidence_ids: string[] }>;
  evidence: EvidenceAnchor[];
  insufficient_evidence: boolean;
  provider: string;
  model: string | null;
  trace: Array<{ skill: string; status: string; summary: string; duration_ms: number }>;
}

export interface AgentStatus {
  provider: string;
  model: string | null;
  llm_configured: boolean;
  retrieval_mode: string;
  embedding_provider: string;
  embedding_model: string;
  reranker_provider: string;
  reranker_model: string | null;
  skills: string[];
}

export interface TranslationResponse {
  translation: string;
  source_language: "auto" | "zh" | "en";
  target_language: "zh" | "en";
  provider: string;
  model: string | null;
}

export interface PageTranslationSegment {
  block_id: string;
  bbox: number[] | null;
  source_text: string;
  translation: string;
}

export interface DocumentPageTranslation {
  document_id: string;
  page_number: number;
  source_language: "auto";
  target_language: "zh" | "en";
  provider: string;
  model: string | null;
  truncated: boolean;
  segments: PageTranslationSegment[];
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

export async function getDocumentProgress(documentId: string): Promise<DocumentProgress> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/documents/${documentId}/progress`, { cache: "no-store" }),
  );
  return response.json();
}

export async function getDocumentOutline(documentId: string): Promise<DocumentOutlineResponse> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/documents/${documentId}/outline`, { cache: "no-store" }),
  );
  return response.json();
}

export async function getDocumentReferences(documentId: string): Promise<DocumentReferencesResponse> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/documents/${documentId}/references`, { cache: "no-store" }),
  );
  return response.json();
}

export async function askAgent(
  question: string,
  documentIds?: string[],
): Promise<AgentAskResponse> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/agent/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, document_ids: documentIds, top_k: 6 }),
    }),
  );
  return response.json();
}

export async function getAgentStatus(): Promise<AgentStatus> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/agent/status`, { cache: "no-store" }),
  );
  return response.json();
}

export async function translateSelection(
  sourceText: string,
  targetLanguage: "zh" | "en",
): Promise<TranslationResponse> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/agent/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: sourceText,
        source_language: "auto",
        target_language: targetLanguage,
      }),
    }),
  );
  return response.json();
}

export async function translateDocumentPage(
  documentId: string,
  pageNumber: number,
  targetLanguage: "zh" | "en",
): Promise<DocumentPageTranslation> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/documents/${documentId}/translations/pages/${pageNumber}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_language: targetLanguage }),
    }),
  );
  return response.json();
}

export function documentFileUrl(documentId: string, pageNumber?: number): string {
  const pageAnchor = pageNumber ? `#page=${pageNumber}` : "";
  return `${API_BASE_URL}/documents/${documentId}/file${pageAnchor}`;
}
