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

export type PaperProvider = "semantic_scholar" | "arxiv" | "crossref";

export interface PaperCandidate {
  source: PaperProvider;
  source_id: string;
  title: string;
  authors: string[];
  abstract: string | null;
  year: number | null;
  published_at: string | null;
  venue: string | null;
  doi: string | null;
  arxiv_id: string | null;
  arxiv_version: number | null;
  citation_count: number | null;
  influential_citation_count: number | null;
  landing_url: string | null;
  pdf_url: string | null;
  license: string | null;
  code_url: string | null;
  code_stars: number | null;
  importable: boolean;
  import_reason: string | null;
}

export interface PaperSearchResponse {
  query: string;
  query_kind: "title" | "doi" | "arxiv";
  items: PaperCandidate[];
  warnings: string[];
}

export interface ReferenceCandidateMatch {
  paper: PaperCandidate;
  match_score: number;
  match_reason: string;
}

export interface ReferenceResolution {
  reference_id: string;
  label: string;
  text: string;
  page_number: number;
  status: "matched" | "uncertain" | "not_found" | "error";
  query_kind: "title" | "doi" | "arxiv" | null;
  candidates: ReferenceCandidateMatch[];
  warnings: string[];
}

export interface ReferenceResolveResponse {
  document_id: string;
  total_references: number;
  attempted: number;
  matched: number;
  importable: number;
  items: ReferenceResolution[];
}

interface PaperImportResponse extends UploadResponse {
  source: PaperCandidate;
}

export interface ArxivSubscription {
  id: string;
  name: string;
  query: string;
  category: string | null;
  max_results: number;
  active: boolean;
  last_refreshed_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface PaperRecommendation {
  id: string;
  subscription_id: string;
  arxiv_id: string;
  arxiv_version: number | null;
  title: string;
  authors: string[];
  abstract: string | null;
  categories: string[];
  published_at: string | null;
  landing_url: string;
  pdf_url: string;
  code_url: string | null;
  code_stars: number | null;
  relevance_score: number;
  freshness_score: number;
  final_score: number;
  recommendation_reason: string;
  feedback: "neutral" | "like" | "dislike";
  discovered_at: string;
  updated_at: string;
}

export type CitationGraphRole =
  | "cornerstone"
  | "bridge"
  | "derivative"
  | "isolated"
  | "peripheral";

export interface CitationGraphNode {
  document_id: string;
  title: string;
  authors: string[];
  arxiv_id: string | null;
  arxiv_version: number | null;
  page_count: number | null;
  in_degree: number;
  out_degree: number;
  pagerank: number;
  foundation_score: number;
  role: CitationGraphRole;
}

export interface CitationGraphEdge {
  edge_id: string;
  source_document_id: string;
  target_document_id: string;
  reference_ids: string[];
  reference_labels: string[];
  reference_pages: number[];
  sample_reference: string;
  mention_count: number;
  match_score: number;
  match_reason: string;
}

export interface CitationGraphResponse {
  generated_at: string;
  nodes: CitationGraphNode[];
  edges: CitationGraphEdge[];
  stats: {
    document_count: number;
    relation_count: number;
    total_references: number;
    matched_references: number;
    unmatched_references: number;
    cornerstone_count: number;
    derivative_count: number;
    density: number;
  };
  warnings: string[];
}

export interface ReviewEvidence extends EvidenceAnchor {
  evidence_kind: "overview" | "future_work" | "later_progress";
}

export interface ReviewPaper {
  document_id: string;
  title: string;
  publication_year: number | null;
  graph_role: CitationGraphRole;
  foundation_score: number;
  overview_evidence_ids: string[];
  future_work_evidence_ids: string[];
}

export interface FutureDirection {
  direction_id: string;
  text: string;
  source_document_id: string;
  source_title: string;
  source_year: number | null;
  source_evidence_id: string;
  status: "open" | "possibly_addressed";
  possibly_addressed_by_document_ids: string[];
  progress_evidence_ids: string[];
  exploration_score: number;
  reason: string;
}

export interface ResearchReviewResponse {
  generated_at: string;
  review: string;
  claims: Array<{ text: string; evidence_ids: string[] }>;
  papers: ReviewPaper[];
  future_directions: FutureDirection[];
  evidence: ReviewEvidence[];
  graph_stats: CitationGraphResponse["stats"];
  provider: string;
  model: string | null;
  insufficient_evidence: boolean;
  warnings: string[];
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
  translation_configured: boolean;
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

export interface DocumentTranslationJob {
  document_id: string;
  source_language: "auto";
  target_language: "zh" | "en";
  provider: string;
  model: string | null;
  status: "partial" | "queued" | "processing" | "completed" | "failed";
  page_count: number;
  completed_pages: number;
  percentage: number;
  resumable: boolean;
  error: string | null;
  updated_at: string | null;
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

export async function searchPapers(query: string): Promise<PaperSearchResponse> {
  const params = new URLSearchParams({ q: query });
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/discovery/papers?${params}`, { cache: "no-store" }),
  );
  return response.json();
}

export async function importPaper(
  candidate: Pick<PaperCandidate, "source" | "source_id">,
): Promise<PaperImportResponse> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/discovery/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: candidate.source, source_id: candidate.source_id }),
    }),
  );
  return response.json();
}

export async function listArxivSubscriptions(): Promise<ArxivSubscription[]> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/recommendations/subscriptions`, { cache: "no-store" }),
  );
  return (await response.json()).items;
}

export async function createArxivSubscription(input: {
  name: string;
  query: string;
  category?: string;
  max_results?: number;
}): Promise<ArxivSubscription> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/recommendations/subscriptions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...input, refresh_now: true }),
    }),
  );
  return response.json();
}

export async function refreshArxivSubscription(subscriptionId: string): Promise<void> {
  await assertResponse(
    await fetch(`${API_BASE_URL}/recommendations/subscriptions/${subscriptionId}/refresh`, {
      method: "POST",
    }),
  );
}

export async function deleteArxivSubscription(subscriptionId: string): Promise<void> {
  await assertResponse(
    await fetch(`${API_BASE_URL}/recommendations/subscriptions/${subscriptionId}`, {
      method: "DELETE",
    }),
  );
}

export async function listPaperRecommendations(): Promise<PaperRecommendation[]> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/recommendations?limit=30`, { cache: "no-store" }),
  );
  return (await response.json()).items;
}

export async function setRecommendationFeedback(
  recommendationId: string,
  feedback: PaperRecommendation["feedback"],
): Promise<PaperRecommendation> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/recommendations/${recommendationId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feedback }),
    }),
  );
  return response.json();
}

export async function buildCitationGraph(
  documentIds: string[] = [],
): Promise<CitationGraphResponse> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/citation-graph`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_ids: documentIds, match_threshold: 0.72 }),
    }),
  );
  return response.json();
}

export async function generateResearchReview(
  documentIds: string[] = [],
): Promise<ResearchReviewResponse> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/reviews/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_ids: documentIds,
        match_threshold: 0.72,
        max_evidence: 30,
      }),
    }),
  );
  return response.json();
}

export async function resolveDocumentReferences(
  documentId: string,
  limit = 12,
): Promise<ReferenceResolveResponse> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/discovery/references/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: documentId,
        limit,
        candidates_per_reference: 3,
      }),
    }),
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

export async function startDocumentTranslation(
  documentId: string,
  targetLanguage: "zh" | "en",
  force = false,
): Promise<DocumentTranslationJob> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/documents/${documentId}/translations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_language: targetLanguage, force }),
    }),
  );
  return response.json();
}

export async function getDocumentTranslationStatus(
  documentId: string,
  targetLanguage: "zh" | "en",
): Promise<DocumentTranslationJob> {
  const response = await assertResponse(
    await fetch(`${API_BASE_URL}/documents/${documentId}/translations/${targetLanguage}`, {
      cache: "no-store",
    }),
  );
  return response.json();
}

export function documentFileUrl(documentId: string, pageNumber?: number): string {
  const pageAnchor = pageNumber ? `#page=${pageNumber}` : "";
  return `${API_BASE_URL}/documents/${documentId}/file${pageAnchor}`;
}
