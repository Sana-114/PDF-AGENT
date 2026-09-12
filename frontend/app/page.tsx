"use client";

import dynamic from "next/dynamic";
import { ChangeEvent, DragEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AgentAskResponse,
  ArxivSubscription,
  askAgent,
  buildCitationGraph,
  CitationGraphResponse,
  createArxivSubscription,
  deleteArxivSubscription,
  deleteDocument,
  documentFileUrl,
  DocumentProgress,
  DocumentRecord,
  EvidenceAnchor,
  generateResearchReview,
  generateWritingOutline,
  getAgentStatus,
  getDocumentProgress,
  importPaper,
  listArxivSubscriptions,
  listDocuments,
  listPaperRecommendations,
  PaperCandidate,
  PaperRecommendation,
  ReferenceResolveResponse,
  ResearchReviewResponse,
  resolveDocumentReferences,
  refreshArxivSubscription,
  searchPapers,
  setRecommendationFeedback,
  uploadDocument,
  WritingOutlineResponse,
} from "../lib/api";
import CitationGraph from "../components/CitationGraph";
import ResearchReview from "../components/ResearchReview";
import WritingWorkbench from "../components/WritingWorkbench";
import DataVisualizationWorkbench from "../components/DataVisualizationWorkbench";
import ArchitectureDiagramWorkbench from "../components/ArchitectureDiagramWorkbench";
import AcademicTranslationWorkbench from "../components/AcademicTranslationWorkbench";
import {
  defaultReferenceSelections,
  paperCandidateKey,
  ReferenceSelections,
  uniqueSelectedPapers,
} from "../lib/referenceDiscovery";

const PdfReader = dynamic(() => import("../components/PdfReader"), {
  ssr: false,
  loading: () => <div className="pdf-reader-loading">正在准备 PDF 阅读器…</div>,
});

interface ReaderState {
  documentId: string;
  title: string;
  pageNumber: number;
  evidence: EvidenceAnchor | null;
}

interface ReferenceExplorerDocument {
  documentId: string;
  title: string;
}

const STATUS_LABEL: Record<DocumentRecord["status"], string> = {
  queued: "等待解析",
  processing: "解析中",
  ready: "已就绪",
  failed: "解析失败",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function Home() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [documentProgress, setDocumentProgress] = useState<Record<string, DocumentProgress>>({});
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [paperQuery, setPaperQuery] = useState("");
  const [paperResults, setPaperResults] = useState<PaperCandidate[]>([]);
  const [paperWarnings, setPaperWarnings] = useState<string[]>([]);
  const [searchingPapers, setSearchingPapers] = useState(false);
  const [importingPaper, setImportingPaper] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [selectedDocument, setSelectedDocument] = useState("all");
  const [answer, setAnswer] = useState<AgentAskResponse | null>(null);
  const [asking, setAsking] = useState(false);
  const [agentLabel, setAgentLabel] = useState("正在检测 Agent");
  const [reader, setReader] = useState<ReaderState | null>(null);
  const [referenceExplorer, setReferenceExplorer] = useState<ReferenceExplorerDocument | null>(null);
  const [referenceResults, setReferenceResults] = useState<ReferenceResolveResponse | null>(null);
  const [referenceSelections, setReferenceSelections] = useState<ReferenceSelections>({});
  const [resolvingReferences, setResolvingReferences] = useState(false);
  const [importingReferences, setImportingReferences] = useState(false);
  const [referenceImportProgress, setReferenceImportProgress] = useState("");
  const [subscriptions, setSubscriptions] = useState<ArxivSubscription[]>([]);
  const [recommendations, setRecommendations] = useState<PaperRecommendation[]>([]);
  const [feedName, setFeedName] = useState("");
  const [feedQuery, setFeedQuery] = useState("");
  const [feedCategory, setFeedCategory] = useState("");
  const [savingFeed, setSavingFeed] = useState(false);
  const [refreshingFeed, setRefreshingFeed] = useState<string | null>(null);
  const [importingRecommendation, setImportingRecommendation] = useState<string | null>(null);
  const [citationGraph, setCitationGraph] = useState<CitationGraphResponse | null>(null);
  const [buildingCitationGraph, setBuildingCitationGraph] = useState(false);
  const [researchReview, setResearchReview] = useState<ResearchReviewResponse | null>(null);
  const [generatingResearchReview, setGeneratingResearchReview] = useState(false);
  const [writingIdea, setWritingIdea] = useState("");
  const [writingLanguage, setWritingLanguage] = useState<"zh" | "en">("zh");
  const [writingOutline, setWritingOutline] = useState<WritingOutlineResponse | null>(null);
  const [generatingWritingOutline, setGeneratingWritingOutline] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async (quiet = false) => {
    try {
      const response = await listDocuments();
      setDocuments(response.items);
      const progressEntries = await Promise.all(
        response.items
          .filter((document) => document.status !== "ready")
          .map(async (document) => {
            try {
              return [document.id, await getDocumentProgress(document.id)] as const;
            } catch {
              return null;
            }
          }),
      );
      setDocumentProgress(
        Object.fromEntries(progressEntries.filter((entry): entry is NonNullable<typeof entry> => Boolean(entry))),
      );
      if (!quiet) setError(null);
    } catch (requestError) {
      if (!quiet) setError(requestError instanceof Error ? requestError.message : "文献列表加载失败");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  const refreshRecommendationData = useCallback(async () => {
    try {
      const [subscriptionItems, recommendationItems] = await Promise.all([
        listArxivSubscriptions(),
        listPaperRecommendations(),
      ]);
      setSubscriptions(subscriptionItems);
      setRecommendations(recommendationItems);
    } catch {
      // The main document workflow remains available while recommendation services recover.
    }
  }, []);

  useEffect(() => {
    void refresh();
    void getAgentStatus()
      .then((status) => setAgentLabel(status.model || status.provider))
      .catch(() => setAgentLabel("Agent 离线"));
    void refreshRecommendationData();
    const timer = window.setInterval(() => void refresh(true), 3000);
    const recommendationTimer = window.setInterval(() => void refreshRecommendationData(), 15000);
    return () => {
      window.clearInterval(timer);
      window.clearInterval(recommendationTimer);
    };
  }, [refresh, refreshRecommendationData]);

  const processingCount = useMemo(
    () => documents.filter((document) => ["queued", "processing"].includes(document.status)).length,
    [documents],
  );
  const selectedReferencePapers = useMemo(
    () => uniqueSelectedPapers(referenceSelections),
    [referenceSelections],
  );

  async function handleFiles(files: FileList | File[]) {
    const pdfs = Array.from(files).filter((file) => file.type === "application/pdf" || file.name.endsWith(".pdf"));
    if (!pdfs.length) {
      setError("请选择 PDF 文件。 ");
      return;
    }
    setUploading(true);
    setError(null);
    setNotice(null);
    try {
      const results = [];
      for (const file of pdfs) results.push(await uploadDocument(file));
      const duplicates = results.filter((result) => result.exact_duplicate).length;
      setNotice(
        duplicates
          ? `已处理 ${results.length} 个文件，其中 ${duplicates} 个为完全重复内容。`
          : `已创建 ${results.length} 个解析任务。`,
      );
      await refresh(true);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "上传失败");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) void handleFiles(event.target.files);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void handleFiles(event.dataTransfer.files);
  }

  async function submitPaperSearch(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleanQuery = paperQuery.trim();
    if (!cleanQuery) return;
    setSearchingPapers(true);
    setError(null);
    setNotice(null);
    try {
      const response = await searchPapers(cleanQuery);
      setPaperResults(response.items);
      setPaperWarnings(response.warnings);
      if (!response.items.length) setNotice("没有找到匹配论文，可以换用完整题名、DOI 或 arXiv ID。");
    } catch (searchError) {
      setPaperResults([]);
      setPaperWarnings([]);
      setError(searchError instanceof Error ? searchError.message : "论文检索失败");
    } finally {
      setSearchingPapers(false);
    }
  }

  async function addDiscoveredPaper(candidate: PaperCandidate) {
    setImportingPaper(`${candidate.source}:${candidate.source_id}`);
    setError(null);
    setNotice(null);
    try {
      const result = await importPaper(candidate);
      setNotice(result.message);
      await refresh(true);
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "论文导入失败");
    } finally {
      setImportingPaper(null);
    }
  }

  async function openReferenceExplorer(document: DocumentRecord) {
    setReferenceExplorer({
      documentId: document.id,
      title: document.title || document.original_filename,
    });
    setReferenceResults(null);
    setReferenceSelections({});
    setReferenceImportProgress("");
    setResolvingReferences(true);
    setError(null);
    try {
      const response = await resolveDocumentReferences(document.id);
      setReferenceResults(response);
      setReferenceSelections(defaultReferenceSelections(response.items));
    } catch (resolveError) {
      setError(resolveError instanceof Error ? resolveError.message : "参考文献解析失败");
    } finally {
      setResolvingReferences(false);
    }
  }

  function selectReferenceCandidate(referenceId: string, candidate: PaperCandidate) {
    setReferenceSelections((current) => {
      const existing = current[referenceId];
      if (existing && paperCandidateKey(existing) === paperCandidateKey(candidate)) {
        const next = { ...current };
        delete next[referenceId];
        return next;
      }
      return { ...current, [referenceId]: candidate };
    });
  }

  async function importSelectedReferences() {
    if (!selectedReferencePapers.length) return;
    setImportingReferences(true);
    setError(null);
    let imported = 0;
    let duplicates = 0;
    let failed = 0;
    for (let index = 0; index < selectedReferencePapers.length; index += 1) {
      const candidate = selectedReferencePapers[index];
      setReferenceImportProgress(`正在导入 ${index + 1} / ${selectedReferencePapers.length}`);
      try {
        const result = await importPaper(candidate);
        if (result.exact_duplicate) duplicates += 1;
        else imported += 1;
      } catch {
        failed += 1;
      }
    }
    setNotice(`引用下钻完成：新增 ${imported} 篇，已存在 ${duplicates} 篇，失败 ${failed} 篇。`);
    if (failed) setError("部分论文可能没有稳定的开放 PDF，可稍后单独重试。");
    setReferenceSelections({});
    setReferenceImportProgress("");
    setImportingReferences(false);
    setReferenceExplorer(null);
    await refresh(true);
  }

  async function createFeed(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!feedName.trim() || (!feedQuery.trim() && !feedCategory.trim())) return;
    setSavingFeed(true);
    setError(null);
    try {
      await createArxivSubscription({
        name: feedName.trim(),
        query: feedQuery.trim(),
        category: feedCategory.trim() || undefined,
        max_results: 10,
      });
      setFeedName("");
      setFeedQuery("");
      setFeedCategory("");
      setNotice("arXiv 追踪已创建，首次刷新任务已提交。 ");
      await refreshRecommendationData();
    } catch (feedError) {
      setError(feedError instanceof Error ? feedError.message : "arXiv 追踪创建失败");
    } finally {
      setSavingFeed(false);
    }
  }

  async function refreshFeed(subscriptionId: string) {
    setRefreshingFeed(subscriptionId);
    setError(null);
    try {
      await refreshArxivSubscription(subscriptionId);
      setNotice("arXiv 刷新任务已提交，推荐列表将自动更新。 ");
      window.setTimeout(() => void refreshRecommendationData(), 1200);
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : "订阅刷新失败");
    } finally {
      setRefreshingFeed(null);
    }
  }

  async function removeFeed(subscription: ArxivSubscription) {
    if (!window.confirm(`删除追踪“${subscription.name}”及其推荐记录吗？`)) return;
    try {
      await deleteArxivSubscription(subscription.id);
      await refreshRecommendationData();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "订阅删除失败");
    }
  }

  async function updateRecommendationFeedback(
    recommendation: PaperRecommendation,
    feedback: PaperRecommendation["feedback"],
  ) {
    try {
      const updated = await setRecommendationFeedback(
        recommendation.id,
        recommendation.feedback === feedback ? "neutral" : feedback,
      );
      setRecommendations((current) => current.map((item) => (
        item.id === updated.id ? updated : item
      )));
    } catch (feedbackError) {
      setError(feedbackError instanceof Error ? feedbackError.message : "反馈保存失败");
    }
  }

  async function generateCitationGraph() {
    const readyDocumentIds = documents
      .filter((document) => document.status === "ready")
      .map((document) => document.id);
    if (readyDocumentIds.length < 2) {
      setError("至少需要两篇已解析文献才能构建局域引用图谱。");
      return;
    }
    setBuildingCitationGraph(true);
    setError(null);
    try {
      setCitationGraph(await buildCitationGraph(readyDocumentIds));
    } catch (graphError) {
      setError(graphError instanceof Error ? graphError.message : "引用图谱构建失败");
    } finally {
      setBuildingCitationGraph(false);
    }
  }

  async function generateReview() {
    const readyDocumentIds = documents
      .filter((document) => document.status === "ready")
      .map((document) => document.id);
    if (!readyDocumentIds.length) {
      setError("至少需要一篇已解析文献才能生成证据综述。");
      return;
    }
    setGeneratingResearchReview(true);
    setError(null);
    try {
      setResearchReview(await generateResearchReview(readyDocumentIds));
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : "自动综述生成失败");
    } finally {
      setGeneratingResearchReview(false);
    }
  }

  async function generateOutline(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const idea = writingIdea.trim();
    if (!idea) return;
    const readyDocumentIds = documents
      .filter((document) => document.status === "ready")
      .map((document) => document.id);
    if (!readyDocumentIds.length) {
      setError("请先上传并解析至少一篇与 idea 相关的论文。");
      return;
    }
    setGeneratingWritingOutline(true);
    setError(null);
    try {
      setWritingOutline(await generateWritingOutline({
        idea,
        documentIds: readyDocumentIds,
        language: writingLanguage,
      }));
    } catch (outlineError) {
      setError(outlineError instanceof Error ? outlineError.message : "论文框架生成失败");
    } finally {
      setGeneratingWritingOutline(false);
    }
  }

  async function addRecommendation(recommendation: PaperRecommendation) {
    setImportingRecommendation(recommendation.id);
    setError(null);
    try {
      const version = recommendation.arxiv_version ? `v${recommendation.arxiv_version}` : "";
      const result = await importPaper({
        source: "arxiv",
        source_id: `${recommendation.arxiv_id}${version}`,
      });
      setNotice(result.message);
      await refresh(true);
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "推荐论文导入失败");
    } finally {
      setImportingRecommendation(null);
    }
  }

  async function remove(document: DocumentRecord) {
    if (!window.confirm(`确定删除“${document.title || document.original_filename}”吗？`)) return;
    try {
      await deleteDocument(document.id);
      setDocuments((current) => current.filter((item) => item.id !== document.id));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "删除失败");
    }
  }

  async function submitQuestion(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleanQuestion = question.trim();
    if (!cleanQuestion) return;
    setAsking(true);
    setError(null);
    try {
      const result = await askAgent(
        cleanQuestion,
        selectedDocument === "all" ? undefined : [selectedDocument],
      );
      setAnswer(result);
    } catch (askError) {
      setError(askError instanceof Error ? askError.message : "问答请求失败");
    } finally {
      setAsking(false);
    }
  }

  function openReader(
    documentId: string,
    title: string,
    pageNumber = 1,
    evidence: EvidenceAnchor | null = null,
  ) {
    setReader({ documentId, title, pageNumber, evidence });
  }

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#">
          <span className="brand-mark">P</span>
          <span>PaperPilot</span>
        </a>
        <nav aria-label="主导航">
          <a className="active" href="#library">文献库</a>
          <a href="#qa">问答</a>
          <a href="#recommendations">追踪</a>
          <a href="#citation-graph">引用图谱</a>
          <a href="#research-review">综述</a>
          <a href="#writing-workbench">写作台</a>
          <a href="#academic-translation">学术翻译</a>
          <a href="#data-visualization">数据作图</a>
          <a href="#architecture-diagram">架构图</a>
        </nav>
        <div className="system-pill"><span /> {agentLabel}</div>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">EVIDENCE-FIRST RESEARCH</p>
          <h1>让每一个结论，<br /><em>都能回到原文。</em></h1>
          <p className="hero-copy">上传论文，建立带页码和坐标的结构化知识库。当前版本已接通内容去重、异步解析、证据检索与可追溯问答链路。</p>
        </div>
        <div className="metric-row">
          <div><strong>{documents.length}</strong><span>篇文献</span></div>
          <div><strong>{processingCount}</strong><span>处理中</span></div>
          <div><strong>{documents.filter((item) => item.duplicate_of_id).length}</strong><span>版本提醒</span></div>
        </div>
      </section>

      <section className="workspace" id="library">
        <div
          className={`dropzone ${dragging ? "dragging" : ""}`}
          onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <div className="upload-icon">↑</div>
          <div>
            <h2>{uploading ? "正在上传…" : "添加研究资料"}</h2>
            <p>拖入单篇或多篇 PDF，最大 200 MB / 文件</p>
          </div>
          <button disabled={uploading} onClick={() => fileInputRef.current?.click()}>
            选择 PDF
          </button>
          <input ref={fileInputRef} type="file" accept="application/pdf,.pdf" multiple hidden onChange={onFileChange} />
        </div>

        <section className="paper-discovery" aria-labelledby="paper-discovery-title">
          <div className="discovery-intro">
            <div>
              <p className="eyebrow">SCHOLARLY DISCOVERY</p>
              <h2 id="paper-discovery-title">从学术索引直接入库</h2>
            </div>
            <p>输入完整题名、DOI 或 arXiv ID。服务端只下载可信来源的开放 PDF。</p>
          </div>
          <form className="discovery-search" onSubmit={submitPaperSearch}>
            <input
              aria-label="论文题名、DOI 或 arXiv ID"
              onChange={(event) => setPaperQuery(event.target.value)}
              placeholder="例如：Attention Is All You Need / 1706.03762v7"
              value={paperQuery}
            />
            <button disabled={searchingPapers || !paperQuery.trim()} type="submit">
              {searchingPapers ? "检索中…" : "检索论文"}
            </button>
          </form>
          {paperWarnings.map((warning) => (
            <p className="discovery-warning" key={warning}>{warning}</p>
          ))}
          {paperResults.length > 0 && (
            <div className="discovery-results">
              {paperResults.map((candidate) => {
                const importKey = `${candidate.source}:${candidate.source_id}`;
                return (
                  <article className="discovery-card" key={importKey}>
                    <div className="discovery-source">
                      <span>{candidate.source.replace("_", " ")}</span>
                      {candidate.year && <small>{candidate.year}</small>}
                    </div>
                    <div>
                      <h3>{candidate.title}</h3>
                      <p className="discovery-authors">
                        {candidate.authors.slice(0, 5).join(" · ") || "作者信息暂缺"}
                        {candidate.authors.length > 5 ? " 等" : ""}
                      </p>
                      <p className="discovery-meta">
                        {candidate.venue || "来源未注明"}
                        {candidate.arxiv_id && ` · arXiv:${candidate.arxiv_id}${candidate.arxiv_version ? `v${candidate.arxiv_version}` : ""}`}
                        {candidate.citation_count !== null && ` · ${candidate.citation_count} 次引用`}
                      </p>
                      {candidate.abstract && <p className="discovery-abstract">{candidate.abstract}</p>}
                    </div>
                    <div className="discovery-action">
                      <button
                        disabled={!candidate.importable || importingPaper !== null}
                        onClick={() => void addDiscoveredPaper(candidate)}
                        type="button"
                      >
                        {importingPaper === importKey ? "下载入库中…" : "下载并解析"}
                      </button>
                      {!candidate.importable && <small>{candidate.import_reason}</small>}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>

        <section className="recommendation-section" id="recommendations">
          <div className="section-heading">
            <div><p className="eyebrow">ARXIV RADAR</p><h2>研究动态追踪</h2></div>
            <span className="evidence-promise">每 6 小时自动刷新 · 代码论文优先</span>
          </div>
          <form className="feed-form" onSubmit={createFeed}>
            <label>
              追踪名称
              <input
                onChange={(event) => setFeedName(event.target.value)}
                placeholder="例如：多模态 RAG"
                value={feedName}
              />
            </label>
            <label>
              研究关键词
              <input
                onChange={(event) => setFeedQuery(event.target.value)}
                placeholder="retrieval augmented generation"
                value={feedQuery}
              />
            </label>
            <label>
              arXiv 分类（可选）
              <input
                onChange={(event) => setFeedCategory(event.target.value)}
                placeholder="cs.AI"
                value={feedCategory}
              />
            </label>
            <button
              disabled={savingFeed || !feedName.trim() || (!feedQuery.trim() && !feedCategory.trim())}
              type="submit"
            >
              {savingFeed ? "创建中…" : "创建追踪"}
            </button>
          </form>
          {subscriptions.length > 0 && (
            <div className="feed-chips">
              {subscriptions.map((subscription) => (
                <div className="feed-chip" key={subscription.id}>
                  <span>
                    <strong>{subscription.name}</strong>
                    <small>
                      {subscription.query || subscription.category}
                      {subscription.last_error ? " · 最近刷新失败" : ""}
                    </small>
                  </span>
                  <button
                    disabled={refreshingFeed !== null}
                    onClick={() => void refreshFeed(subscription.id)}
                    type="button"
                  >
                    {refreshingFeed === subscription.id ? "刷新中" : "刷新"}
                  </button>
                  <button onClick={() => void removeFeed(subscription)} type="button">×</button>
                </div>
              ))}
            </div>
          )}
          <div className="recommendation-grid">
            {subscriptions.length === 0 && (
              <div className="recommendation-empty">
                创建一个关键词或分类追踪，系统会结合本地文献库计算相关性。
              </div>
            )}
            {subscriptions.length > 0 && recommendations.length === 0 && (
              <div className="recommendation-empty">等待首次 arXiv 刷新结果…</div>
            )}
            {recommendations.map((recommendation) => (
              <article className="recommendation-card" key={recommendation.id}>
                <div className="recommendation-score">
                  <strong>{Math.round(recommendation.final_score * 100)}</strong>
                  <span>推荐分</span>
                </div>
                <div className="recommendation-main">
                  <div className="recommendation-tags">
                    <span>arXiv:{recommendation.arxiv_id}</span>
                    {recommendation.categories.slice(0, 2).map((category) => (
                      <span key={category}>{category}</span>
                    ))}
                    {recommendation.code_url && <span className="code-tag">GitHub Code</span>}
                  </div>
                  <h3>{recommendation.title}</h3>
                  <p className="recommendation-authors">
                    {recommendation.authors.slice(0, 5).join(" · ") || "作者信息暂缺"}
                  </p>
                  {recommendation.abstract && (
                    <p className="recommendation-abstract">{recommendation.abstract}</p>
                  )}
                  <small className="recommendation-reason">
                    {recommendation.recommendation_reason}
                  </small>
                </div>
                <div className="recommendation-actions">
                  {recommendation.code_url && (
                    <a href={recommendation.code_url} rel="noreferrer" target="_blank">
                      代码{recommendation.code_stars ? ` · ${recommendation.code_stars}★` : ""}
                    </a>
                  )}
                  <button
                    disabled={importingRecommendation !== null}
                    onClick={() => void addRecommendation(recommendation)}
                    type="button"
                  >
                    {importingRecommendation === recommendation.id ? "导入中…" : "下载论文"}
                  </button>
                  <div className="feedback-actions">
                    <button
                      aria-pressed={recommendation.feedback === "like"}
                      className={recommendation.feedback === "like" ? "active" : ""}
                      onClick={() => void updateRecommendationFeedback(recommendation, "like")}
                      title="喜欢，影响后续推荐"
                      type="button"
                    >
                      ↑
                    </button>
                    <button
                      aria-pressed={recommendation.feedback === "dislike"}
                      className={recommendation.feedback === "dislike" ? "active dislike" : ""}
                      onClick={() => void updateRecommendationFeedback(recommendation, "dislike")}
                      title="不感兴趣，影响后续推荐"
                      type="button"
                    >
                      ↓
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="citation-graph-section" id="citation-graph">
          <div className="section-heading">
            <div><p className="eyebrow">LOCAL CITATION TOPOLOGY</p><h2>局域引用图谱</h2></div>
            <button
              className="ghost"
              disabled={buildingCitationGraph || documents.filter((item) => item.status === "ready").length < 2}
              onClick={() => void generateCitationGraph()}
              type="button"
            >
              {buildingCitationGraph ? "正在计算…" : citationGraph ? "重新构建" : "生成图谱"}
            </button>
          </div>
          {!citationGraph && (
            <div className="citation-graph-placeholder">
              <strong>从已解析文献中发现真实互引关系</strong>
              <span>题名与 arXiv ID 本地匹配，不调用 LLM 猜测；至少需要两篇已就绪论文。</span>
            </div>
          )}
          {citationGraph && (
            <>
              <div className="citation-summary">
                <div><strong>{citationGraph.stats.document_count}</strong><span>图谱节点</span></div>
                <div><strong>{citationGraph.stats.relation_count}</strong><span>互引关系</span></div>
                <div><strong>{citationGraph.stats.cornerstone_count}</strong><span>基石论文</span></div>
                <div><strong>{citationGraph.stats.derivative_count}</strong><span>边缘衍生</span></div>
                <p>
                  匹配 {citationGraph.stats.matched_references} / {citationGraph.stats.total_references} 条参考文献
                  · 图密度 {(citationGraph.stats.density * 100).toFixed(1)}%
                </p>
              </div>
              {citationGraph.warnings.map((warning) => (
                <p className="citation-warning" key={warning}>{warning}</p>
              ))}
              <CitationGraph
                graph={citationGraph}
                onOpenDocument={(node) => openReader(node.document_id, node.title)}
              />
            </>
          )}
        </section>

        <section className="research-review-section" id="research-review">
          <div className="section-heading">
            <div><p className="eyebrow">EVIDENCE REVIEW</p><h2>自动综述与前沿探索</h2></div>
            <button
              className="ghost"
              disabled={generatingResearchReview || !documents.some((item) => item.status === "ready")}
              onClick={() => void generateReview()}
              type="button"
            >
              {generatingResearchReview ? "正在提取证据…" : researchReview ? "重新生成" : "生成综述"}
            </button>
          </div>
          {!researchReview && (
            <div className="research-review-placeholder">
              <strong>从原文证据生成研究脉络</strong>
              <span>提取摘要、贡献、Future Work 和较新论文进展，并保留页码锚点。</span>
            </div>
          )}
          {researchReview && (
            <>
              {researchReview.warnings.map((warning) => (
                <p className="citation-warning" key={warning}>{warning}</p>
              ))}
              <ResearchReview
                review={researchReview}
                onOpenEvidence={(evidence) => openReader(
                  evidence.document_id,
                  evidence.document_title || "未命名文献",
                  evidence.page_number,
                  evidence,
                )}
              />
            </>
          )}
        </section>

        <section className="writing-section" id="writing-workbench">
          <div className="section-heading">
            <div><p className="eyebrow">ACADEMIC WRITING COPILOT</p><h2>无幻觉论文框架</h2></div>
            <span className="evidence-promise">References 仅来自本地文献与来源元数据</span>
          </div>
          <form className="writing-idea-form" onSubmit={generateOutline}>
            <label>
              研究 idea
              <textarea
                onChange={(event) => setWritingIdea(event.target.value)}
                placeholder="例如：结合版式感知检索与引用约束，降低科研 PDF 问答中的事实幻觉"
                rows={3}
                value={writingIdea}
              />
            </label>
            <label>
              输出语言
              <select
                onChange={(event) => setWritingLanguage(event.target.value as "zh" | "en")}
                value={writingLanguage}
              >
                <option value="zh">中文</option>
                <option value="en">English</option>
              </select>
            </label>
            <button
              disabled={generatingWritingOutline || writingIdea.trim().length < 8}
              type="submit"
            >
              {generatingWritingOutline ? "正在检索证据…" : "生成论文框架"}
            </button>
          </form>
          {writingOutline && (
            <>
              {writingOutline.warnings.map((warning) => (
                <p className="citation-warning" key={warning}>{warning}</p>
              ))}
              <WritingWorkbench
                outline={writingOutline}
                onOpenEvidence={(evidence) => openReader(
                  evidence.document_id,
                  evidence.document_title || "未命名文献",
                  evidence.page_number,
                  evidence,
                )}
              />
            </>
          )}
        </section>

        <section className="academic-translation-section" id="academic-translation">
          <div className="section-heading">
            <div><p className="eyebrow">ACADEMIC TRANSLATION</p><h2>中英学术翻译与完整性校验</h2></div>
            <span className="evidence-promise">公式、引用、代码、数字与指定术语受保护</span>
          </div>
          <AcademicTranslationWorkbench />
        </section>

        <section className="data-visualization-section" id="data-visualization">
          <div className="section-heading">
            <div><p className="eyebrow">SCIENTIFIC DATA COPILOT</p><h2>CSV 数据可视化与学术图注</h2></div>
            <span className="evidence-promise">结论由实际统计值生成，不推断因果</span>
          </div>
          <DataVisualizationWorkbench />
        </section>

        <section className="architecture-diagram-section" id="architecture-diagram">
          <div className="section-heading">
            <div><p className="eyebrow">ARCHITECTURE COPILOT</p><h2>科研架构拓扑与多格式脚本</h2></div>
            <span className="evidence-promise">一份拓扑 · 四种可复现输出</span>
          </div>
          <ArchitectureDiagramWorkbench />
        </section>

        {notice && <div className="notice success">{notice}</div>}
        {error && <div className="notice error">{error}</div>}

        <section className="qa-section" id="qa">
          <div className="section-heading">
            <div><p className="eyebrow">GROUNDED Q&amp;A</p><h2>向文献提问</h2></div>
            <span className="evidence-promise">回答仅使用已解析原文</span>
          </div>
          <form className="qa-panel" onSubmit={submitQuestion}>
            <div className="qa-controls">
              <label>
                检索范围
                <select
                  value={selectedDocument}
                  onChange={(event) => setSelectedDocument(event.target.value)}
                >
                  <option value="all">全部已就绪文献</option>
                  {documents.filter((item) => item.status === "ready").map((document) => (
                    <option key={document.id} value={document.id}>
                      {document.title || document.original_filename}
                    </option>
                  ))}
                </select>
              </label>
              <span>{documents.filter((item) => item.status === "ready").length} 篇可检索</span>
            </div>
            <div className="question-box">
              <textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="例如：这篇论文使用了哪些训练超参数？请给出原文位置。"
                rows={3}
              />
              <button disabled={asking || !question.trim()} type="submit">
                {asking ? "正在查找证据…" : "检索并回答"}
              </button>
            </div>
            <div className="question-suggestions">
              {["论文的核心贡献是什么？", "训练使用了哪些数据集？", "作者提出了哪些未来工作？"].map((item) => (
                <button key={item} type="button" onClick={() => setQuestion(item)}>{item}</button>
              ))}
            </div>
          </form>

          {answer && (
            <div className="answer-layout">
              <article className="answer-card">
                <div className="answer-heading">
                  <span>{answer.insufficient_evidence ? "证据不足" : "可追溯回答"}</span>
                  <small>{answer.model || answer.provider}</small>
                </div>
                <p>{answer.answer}</p>
                <details>
                  <summary>查看执行轨迹</summary>
                  {answer.trace.map((step) => (
                    <div className="trace-step" key={`${step.skill}-${step.duration_ms}`}>
                      <code>{step.skill}</code><span>{step.summary}</span><small>{step.duration_ms} ms</small>
                    </div>
                  ))}
                </details>
              </article>
              <aside className="evidence-list">
                <h3>原文证据 · {answer.evidence.length}</h3>
                {answer.evidence.map((evidence) => (
                  <button
                    className="evidence-card"
                    key={evidence.evidence_id}
                    onClick={() => openReader(
                      evidence.document_id,
                      evidence.document_title || "未命名文献",
                      evidence.page_number,
                      evidence,
                    )}
                    type="button"
                  >
                    <div>
                      <strong>{evidence.evidence_id}</strong>
                      <span>
                        {({
                          text: "正文",
                          abstract: "摘要",
                          table: "表格",
                          figure: "图表",
                          formula: "公式",
                          reference: "参考文献",
                        } as const)[evidence.source_type] || "正文"}
                        {" · "}{evidence.retrieval_mode === "reranked"
                          ? "模型重排"
                          : evidence.retrieval_mode === "hybrid"
                            ? "混合检索"
                            : evidence.retrieval_mode === "vector"
                              ? "向量检索"
                              : "词法检索"}
                        {" · "}第 {evidence.page_number} 页 · {Math.round(evidence.score * 100)}%
                      </span>
                    </div>
                    <h4>{evidence.section || evidence.document_title || "未命名章节"}</h4>
                    <p>{evidence.quote}</p>
                    <small>在阅读器中定位 →</small>
                  </button>
                ))}
              </aside>
            </div>
          )}
        </section>

        <div className="section-heading">
          <div><p className="eyebrow">LIBRARY</p><h2>最近文献</h2></div>
          <button className="ghost" onClick={() => void refresh()}>刷新</button>
        </div>

        <div className="document-list">
          {loading && <div className="empty">正在连接文献服务…</div>}
          {!loading && documents.length === 0 && (
            <div className="empty"><strong>文献库还是空的</strong><span>从上方上传比赛测试 PDF，建立第一条解析记录。</span></div>
          )}
          {documents.map((document) => {
            const progress = documentProgress[document.id];
            return (
              <article className="document-card" key={document.id}>
                <div className="pdf-badge">PDF</div>
                <div className="document-main">
                  <div className="document-title-row">
                    <h3>{document.title || document.original_filename}</h3>
                    <span className={`status ${document.status}`}>{STATUS_LABEL[document.status]}</span>
                  </div>
                  <p className="meta">
                    {document.arxiv_id ? `arXiv:${document.arxiv_id}${document.arxiv_version ? `v${document.arxiv_version}` : ""}` : "等待识别论文标识"}
                    <span>·</span>{document.page_count ? `${document.page_count} 页` : formatBytes(document.size_bytes)}
                    {document.parser_name && (
                      <><span>·</span>{document.parser_name.includes("tesseract") ? "OCR" : document.parser_name}</>
                    )}
                    <span>·</span>{new Date(document.created_at).toLocaleString("zh-CN")}
                  </p>
                  {document.duplicate_recommendation && (
                    <div className="version-warning"><strong>版本提醒</strong>{document.duplicate_recommendation}</div>
                  )}
                  {progress?.page_count && document.status !== "ready" && (
                    <div className="parse-progress" aria-label={`解析进度 ${progress.percentage}%`}>
                      <div><span>已处理 {progress.completed_pages} / {progress.page_count} 页</span><strong>{progress.percentage.toFixed(0)}%</strong></div>
                      <progress max="100" value={progress.percentage} />
                      {progress.resumable && <small>任务中断后可从当前检查点继续</small>}
                    </div>
                  )}
                  {document.error_message && <div className="failure">{document.error_message}</div>}
                </div>
                <div className="actions">
                  <button
                    onClick={() => openReader(
                      document.id,
                      document.title || document.original_filename,
                    )}
                    type="button"
                  >
                    在线阅读
                  </button>
                  <button
                    disabled={document.status !== "ready"}
                    onClick={() => void openReferenceExplorer(document)}
                    type="button"
                  >
                    下钻引用
                  </button>
                  <button onClick={() => void remove(document)}>删除</button>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <footer>PaperPilot MVP · 所有答案都将绑定可验证的原文证据</footer>
      {referenceExplorer && (
        <div
          className="reference-explorer-backdrop"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target && !importingReferences) {
              setReferenceExplorer(null);
            }
          }}
        >
          <section
            aria-labelledby="reference-explorer-title"
            aria-modal="true"
            className="reference-explorer"
            role="dialog"
          >
            <header>
              <div>
                <p className="eyebrow">REFERENCE DRILL-DOWN</p>
                <h2 id="reference-explorer-title">被引论文批量下钻</h2>
                <span>{referenceExplorer.title}</span>
              </div>
              <button
                aria-label="关闭引用下钻"
                disabled={importingReferences}
                onClick={() => setReferenceExplorer(null)}
                type="button"
              >
                ×
              </button>
            </header>
            {resolvingReferences && (
              <div className="reference-explorer-state">正在分析前 12 条参考文献并匹配论文…</div>
            )}
            {!resolvingReferences && referenceResults && (
              <>
                <div className="reference-summary">
                  <span>共提取 <strong>{referenceResults.total_references}</strong> 条</span>
                  <span>本次分析 <strong>{referenceResults.attempted}</strong> 条</span>
                  <span>可信匹配 <strong>{referenceResults.matched}</strong> 条</span>
                  <span>可下载 <strong>{referenceResults.importable}</strong> 条</span>
                </div>
                <div className="reference-resolution-list">
                  {referenceResults.items.map((resolution) => (
                    <article className="reference-resolution" key={resolution.reference_id}>
                      <div className="reference-original">
                        <span>[{resolution.label}] · 第 {resolution.page_number} 页</span>
                        <p>{resolution.text}</p>
                      </div>
                      <div className="reference-candidates">
                        {resolution.candidates.length === 0 && (
                          <p className="reference-no-match">
                            {resolution.status === "error" ? "检索源暂时不可用" : "未找到候选论文"}
                          </p>
                        )}
                        {resolution.candidates.map((match) => {
                          const selected = referenceSelections[resolution.reference_id];
                          const active = selected
                            && paperCandidateKey(selected) === paperCandidateKey(match.paper);
                          return (
                            <button
                              aria-pressed={Boolean(active)}
                              className={active ? "selected" : ""}
                              disabled={!match.paper.importable || importingReferences}
                              key={paperCandidateKey(match.paper)}
                              onClick={() => selectReferenceCandidate(
                                resolution.reference_id,
                                match.paper,
                              )}
                              type="button"
                            >
                              <span className="candidate-check">{active ? "✓" : ""}</span>
                              <span>
                                <strong>{match.paper.title}</strong>
                                <small>
                                  匹配 {Math.round(match.match_score * 100)}% · {match.match_reason}
                                </small>
                                <small>
                                  {match.paper.year || "年份未知"} · {match.paper.source.replace("_", " ")}
                                  {match.paper.importable ? " · 开放 PDF" : " · 仅元数据"}
                                </small>
                              </span>
                            </button>
                          );
                        })}
                        {resolution.warnings.map((warning) => (
                          <small className="reference-warning" key={warning}>{warning}</small>
                        ))}
                      </div>
                    </article>
                  ))}
                </div>
              </>
            )}
            <footer>
              <span>{referenceImportProgress || `已选择 ${selectedReferencePapers.length} 篇不重复论文`}</span>
              <div>
                <button
                  disabled={importingReferences}
                  onClick={() => setReferenceExplorer(null)}
                  type="button"
                >
                  取消
                </button>
                <button
                  className="primary"
                  disabled={!selectedReferencePapers.length || importingReferences}
                  onClick={() => void importSelectedReferences()}
                  type="button"
                >
                  {importingReferences ? "批量导入中…" : `导入选中 ${selectedReferencePapers.length} 篇`}
                </button>
              </div>
            </footer>
          </section>
        </div>
      )}
      {reader && (
        <PdfReader
          documentId={reader.documentId}
          evidence={reader.evidence}
          fileUrl={documentFileUrl(reader.documentId)}
          initialPage={reader.pageNumber}
          key={`${reader.documentId}-${reader.pageNumber}-${reader.evidence?.evidence_id || "document"}`}
          onClose={() => setReader(null)}
          title={reader.title}
        />
      )}
    </main>
  );
}
