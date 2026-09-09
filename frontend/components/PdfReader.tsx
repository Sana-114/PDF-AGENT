"use client";

import {
  FormEvent,
  MouseEvent as ReactMouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Document, Page, pdfjs } from "react-pdf";
import {
  CitationMention,
  DocumentPageTranslation,
  DocumentOutlineNode,
  DocumentReference,
  EvidenceAnchor,
  getDocumentOutline,
  getDocumentReferences,
  translateDocumentPage,
  translateSelection,
  TranslationResponse,
} from "../lib/api";
import { bboxToPercentRect } from "../lib/pdfGeometry";
import { linkifyNumericCitations } from "../lib/referenceMarkup";
import {
  inferTranslationTarget,
  mapSynchronizedScroll,
  MAX_TRANSLATION_CHARS,
} from "../lib/translation";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface PdfReaderProps {
  documentId: string;
  evidence?: EvidenceAnchor | null;
  fileUrl: string;
  title: string;
  initialPage?: number;
  onClose: () => void;
}

const MIN_SCALE = 0.65;
const MAX_SCALE = 2.2;
const SCALE_STEP = 0.15;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

function flattenOutline(items: DocumentOutlineNode[]): DocumentOutlineNode[] {
  return items.flatMap((item) => [item, ...flattenOutline(item.children)]);
}

interface OutlineTreeProps {
  activeBlockId?: string;
  items: DocumentOutlineNode[];
  onSelect: (item: DocumentOutlineNode) => void;
}

type SidebarTab = "outline" | "references";
type ReferenceTargetKind = "reference" | "citation";
type HighlightTarget = ReferenceTarget | {
  kind: "evidence";
  label: string;
  pageNumber: number;
  bbox: number[] | null;
  context: string;
};

interface ReferenceTarget {
  kind: ReferenceTargetKind;
  label: string;
  pageNumber: number;
  bbox: number[] | null;
  context: string;
}

interface SelectionDraft {
  text: string;
  x: number;
  y: number;
}

interface ReferencePanelProps {
  activeTarget: ReferenceTarget | null;
  items: DocumentReference[];
  mentions: CitationMention[];
  onSelectMention: (mention: CitationMention) => void;
  onSelectReference: (reference: DocumentReference) => void;
}

function ReferencePanel({
  activeTarget,
  items,
  mentions,
  onSelectMention,
  onSelectReference,
}: ReferencePanelProps) {
  return (
    <div className="pdf-reference-list">
      {items.map((reference) => {
        const locations = mentions.filter((item) => item.label === reference.label);
        const referenceActive = activeTarget?.kind === "reference"
          && activeTarget.label === reference.label;
        return (
          <article className={referenceActive ? "active" : ""} key={reference.reference_id}>
            <button
              className="pdf-reference-entry"
              onClick={() => onSelectReference(reference)}
              title={reference.text}
              type="button"
            >
              <span><strong>[{reference.label}]</strong><small>第 {reference.page_number} 页</small></span>
              <p>{reference.text}</p>
            </button>
            <div className="pdf-citation-locations">
              {locations.slice(0, 8).map((mention, index) => (
                <button
                  className={activeTarget?.kind === "citation"
                    && activeTarget.label === mention.label
                    && activeTarget.pageNumber === mention.page_number ? "active" : ""}
                  key={mention.citation_id}
                  onClick={() => onSelectMention(mention)}
                  title={mention.context}
                  type="button"
                >
                  正文 {index + 1} · p.{mention.page_number}
                </button>
              ))}
              {locations.length === 0 && <span>正文未检测到编号标记</span>}
              {locations.length > 8 && <span>另有 {locations.length - 8} 处</span>}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function OutlineTree({ activeBlockId, items, onSelect }: OutlineTreeProps) {
  return (
    <ul className="pdf-outline-list">
      {items.map((item) => (
        <li key={item.block_id}>
          <button
            aria-current={item.block_id === activeBlockId ? "location" : undefined}
            className={item.block_id === activeBlockId ? "active" : ""}
            onClick={() => onSelect(item)}
            title={item.text}
            type="button"
          >
            <span>{item.text}</span>
            <small>{item.page_number}</small>
          </button>
          {item.children.length > 0 && (
            <OutlineTree activeBlockId={activeBlockId} items={item.children} onSelect={onSelect} />
          )}
        </li>
      ))}
    </ul>
  );
}

export default function PdfReader({
  documentId,
  evidence,
  fileUrl,
  title,
  initialPage = 1,
  onClose,
}: PdfReaderProps) {
  const [numPages, setNumPages] = useState(0);
  const [pageNumber, setPageNumber] = useState(Math.max(1, initialPage));
  const [pageInput, setPageInput] = useState(String(Math.max(1, initialPage)));
  const [scale, setScale] = useState(1);
  const [outline, setOutline] = useState<DocumentOutlineNode[]>([]);
  const [outlineLoading, setOutlineLoading] = useState(true);
  const [outlineError, setOutlineError] = useState<string | null>(null);
  const [references, setReferences] = useState<DocumentReference[]>([]);
  const [mentions, setMentions] = useState<CitationMention[]>([]);
  const [referencesLoading, setReferencesLoading] = useState(true);
  const [referencesError, setReferencesError] = useState<string | null>(null);
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("outline");
  const [referenceTarget, setReferenceTarget] = useState<ReferenceTarget | null>(null);
  const [selectionDraft, setSelectionDraft] = useState<SelectionDraft | null>(null);
  const [translation, setTranslation] = useState<TranslationResponse | null>(null);
  const [translationLoading, setTranslationLoading] = useState(false);
  const [translationError, setTranslationError] = useState<string | null>(null);
  const [bilingualOpen, setBilingualOpen] = useState(false);
  const [bilingualTarget, setBilingualTarget] = useState<"zh" | "en">("zh");
  const [pageTranslation, setPageTranslation] = useState<DocumentPageTranslation | null>(null);
  const [pageTranslationLoading, setPageTranslationLoading] = useState(false);
  const [pageTranslationError, setPageTranslationError] = useState<string | null>(null);
  const [outlineOpen, setOutlineOpen] = useState(
    () => typeof window === "undefined" || window.innerWidth > 760,
  );
  const [pageSize, setPageSize] = useState<{ width: number; height: number } | null>(null);
  const outlineRef = useRef<HTMLElement>(null);
  const bilingualRef = useRef<HTMLElement>(null);
  const translationCacheRef = useRef(new Map<string, DocumentPageTranslation>());
  const translationRequestRef = useRef<string | null>(null);
  const pageShellRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);

  const flatOutline = useMemo(() => flattenOutline(outline), [outline]);
  const activeOutline = useMemo(
    () => flatOutline.reduce<DocumentOutlineNode | undefined>(
      (active, item) => item.page_number <= pageNumber ? item : active,
      undefined,
    ),
    [flatOutline, pageNumber],
  );
  const referenceLabels = useMemo(
    () => new Set(references.map((reference) => reference.label)),
    [references],
  );
  const activePageTarget = useMemo<HighlightTarget | null>(() => {
    if (referenceTarget?.pageNumber === pageNumber) return referenceTarget;
    if (evidence?.page_number === pageNumber) {
      return {
        kind: "evidence",
        label: evidence.evidence_id,
        pageNumber: evidence.page_number,
        bbox: evidence.bbox,
        context: evidence.quote,
      };
    }
    return null;
  }, [evidence, pageNumber, referenceTarget]);
  const highlightRect = useMemo(
    () => activePageTarget && pageSize
      ? bboxToPercentRect(activePageTarget.bbox, pageSize.width, pageSize.height)
      : null,
    [activePageTarget, pageSize],
  );
  const renderCitationText = useCallback(
    ({ str }: { str: string }) => linkifyNumericCitations(str, referenceLabels),
    [referenceLabels],
  );
  const loadPageTranslation = useCallback(async (
    targetPage: number,
    targetLanguage: "zh" | "en",
  ) => {
    const cacheKey = `${targetPage}:${targetLanguage}`;
    translationRequestRef.current = cacheKey;
    const cached = translationCacheRef.current.get(cacheKey);
    if (cached) {
      setPageTranslation(cached);
      setPageTranslationError(null);
      setPageTranslationLoading(false);
      return;
    }
    setPageTranslation(null);
    setPageTranslationError(null);
    setPageTranslationLoading(true);
    try {
      const result = await translateDocumentPage(documentId, targetPage, targetLanguage);
      translationCacheRef.current.set(cacheKey, result);
      if (translationRequestRef.current === cacheKey) setPageTranslation(result);
    } catch (error) {
      if (translationRequestRef.current === cacheKey) {
        setPageTranslationError(error instanceof Error ? error.message : "当前页翻译失败");
      }
    } finally {
      if (translationRequestRef.current === cacheKey) setPageTranslationLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    let active = true;
    setOutlineLoading(true);
    setOutlineError(null);
    void getDocumentOutline(documentId)
      .then((response) => {
        if (active) setOutline(response.items);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setOutlineError(error instanceof Error ? error.message : "目录加载失败");
      })
      .finally(() => {
        if (active) setOutlineLoading(false);
      });
    return () => {
      active = false;
    };
  }, [documentId]);

  useEffect(() => {
    let active = true;
    setReferencesLoading(true);
    setReferencesError(null);
    void getDocumentReferences(documentId)
      .then((response) => {
        if (!active) return;
        setReferences(response.items);
        setMentions(response.mentions);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setReferencesError(error instanceof Error ? error.message : "参考文献加载失败");
      })
      .finally(() => {
        if (active) setReferencesLoading(false);
      });
    return () => {
      active = false;
    };
  }, [documentId]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.target instanceof HTMLInputElement) return;
      if (event.key === "ArrowLeft") {
        setPageNumber((current) => Math.max(1, current - 1));
      }
      if (event.key === "ArrowRight" && numPages) {
        setPageNumber((current) => Math.min(numPages, current + 1));
      }
    }

    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = originalOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [numPages, onClose]);

  useEffect(() => {
    setPageInput(String(pageNumber));
    setPageSize(null);
    setSelectionDraft(null);
    setTranslation(null);
    setTranslationError(null);
    viewportRef.current?.scrollTo({ top: 0, left: 0 });
  }, [pageNumber]);

  useEffect(() => {
    outlineRef.current
      ?.querySelector<HTMLElement>('[aria-current="location"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [activeOutline?.block_id]);

  useEffect(() => {
    if (bilingualOpen) void loadPageTranslation(pageNumber, bilingualTarget);
  }, [bilingualOpen, bilingualTarget, loadPageTranslation, pageNumber]);

  useEffect(() => {
    const source = viewportRef.current;
    const target = bilingualRef.current;
    if (!bilingualOpen || !source || !target) return;
    let locked = false;
    let animationFrame = 0;

    function synchronize(from: HTMLElement, to: HTMLElement) {
      if (locked) return;
      const targetTop = mapSynchronizedScroll(
        from.scrollTop,
        from.scrollHeight,
        from.clientHeight,
        to.scrollHeight,
        to.clientHeight,
      );
      if (targetTop === null) return;
      locked = true;
      to.scrollTop = targetTop;
      window.cancelAnimationFrame(animationFrame);
      animationFrame = window.requestAnimationFrame(() => {
        locked = false;
      });
    }

    const fromPdf = () => synchronize(source, target);
    const fromTranslation = () => synchronize(target, source);
    source.addEventListener("scroll", fromPdf, { passive: true });
    target.addEventListener("scroll", fromTranslation, { passive: true });
    return () => {
      window.cancelAnimationFrame(animationFrame);
      source.removeEventListener("scroll", fromPdf);
      target.removeEventListener("scroll", fromTranslation);
    };
  }, [bilingualOpen, pageTranslation]);

  function goToPage(target: number) {
    const roundedTarget = Math.max(1, Math.round(target));
    const finalPage = numPages ? Math.min(roundedTarget, numPages) : roundedTarget;
    setPageNumber(finalPage);
    setPageInput(String(finalPage));
  }

  function submitPage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = Number(pageInput);
    if (Number.isFinite(target)) goToPage(target);
    else setPageInput(String(pageNumber));
  }

  function selectOutline(item: DocumentOutlineNode) {
    goToPage(item.page_number);
    if (window.innerWidth <= 760) setOutlineOpen(false);
  }

  function showReferenceTarget(target: ReferenceTarget) {
    setReferenceTarget(target);
    setSidebarTab("references");
    goToPage(target.pageNumber);
    if (window.innerWidth <= 760) setOutlineOpen(false);
  }

  function selectReference(reference: DocumentReference) {
    showReferenceTarget({
      kind: "reference",
      label: reference.label,
      pageNumber: reference.page_number,
      bbox: reference.bbox,
      context: reference.text,
    });
  }

  function selectMention(mention: CitationMention) {
    showReferenceTarget({
      kind: "citation",
      label: mention.label,
      pageNumber: mention.page_number,
      bbox: mention.bbox,
      context: mention.context,
    });
  }

  function handlePageClick(event: ReactMouseEvent<HTMLDivElement>) {
    if (!(event.target instanceof Element)) return;
    const marker = event.target.closest<HTMLElement>("[data-reference-label]");
    const label = marker?.dataset.referenceLabel;
    if (!label) return;
    const reference = references.find((item) => item.label === label);
    if (!reference) return;
    event.preventDefault();
    selectReference(reference);
  }

  function captureTextSelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) return;
    const range = selection.getRangeAt(0);
    if (!pageShellRef.current?.contains(range.commonAncestorContainer)) return;
    const text = selection.toString().trim();
    if (text.length < 2) return;
    const rect = range.getBoundingClientRect();
    setSelectionDraft({
      text: text.slice(0, MAX_TRANSLATION_CHARS),
      x: clamp(rect.left, 16, window.innerWidth - 150),
      y: Math.max(16, rect.top - 44),
    });
    setTranslation(null);
    setTranslationError(null);
  }

  async function translateSelectedText() {
    if (!selectionDraft || translationLoading) return;
    const targetLanguage = inferTranslationTarget(selectionDraft.text);
    setTranslationLoading(true);
    setTranslation(null);
    setTranslationError(null);
    try {
      setTranslation(await translateSelection(selectionDraft.text, targetLanguage));
    } catch (error) {
      setTranslationError(error instanceof Error ? error.message : "翻译请求失败");
    } finally {
      setTranslationLoading(false);
    }
  }

  function closeTranslation() {
    setSelectionDraft(null);
    setTranslation(null);
    setTranslationError(null);
    window.getSelection()?.removeAllRanges();
  }

  function toggleBilingualReader() {
    setBilingualOpen((current) => !current);
  }

  function changeBilingualTarget(targetLanguage: "zh" | "en") {
    setBilingualTarget(targetLanguage);
    setBilingualOpen(true);
  }

  return (
    <div className="pdf-reader-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        aria-label={`阅读 ${title}`}
        aria-modal="true"
        className="pdf-reader"
        role="dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="pdf-reader-header">
          <div>
            <span className="pdf-reader-kicker">PDF READER</span>
            <h2 title={title}>{title}</h2>
          </div>
          <div className="pdf-reader-controls" aria-label="PDF 阅读控制">
            <button
              aria-controls="pdf-document-outline"
              aria-expanded={outlineOpen}
              className={`pdf-outline-toggle ${outlineOpen ? "active" : ""}`}
              onClick={() => setOutlineOpen((current) => !current)}
              type="button"
            >
              ☰ <span>导航</span>
            </button>
            <button
              aria-pressed={bilingualOpen}
              className={`pdf-bilingual-toggle ${bilingualOpen ? "active" : ""}`}
              onClick={toggleBilingualReader}
              title="按当前页生成段落对齐译文"
              type="button"
            >
              中英
            </button>
            <span className="pdf-control-divider" />
            <button
              aria-label="上一页"
              disabled={pageNumber <= 1}
              onClick={() => goToPage(pageNumber - 1)}
              type="button"
            >
              ←
            </button>
            <form className="pdf-page-form" onSubmit={submitPage}>
              <label htmlFor="pdf-page-number">页码</label>
              <input
                id="pdf-page-number"
                inputMode="numeric"
                min="1"
                max={numPages || undefined}
                onChange={(event) => setPageInput(event.target.value)}
                value={pageInput}
              />
              <span>/ {numPages || "…"}</span>
            </form>
            <button
              aria-label="下一页"
              disabled={!numPages || pageNumber >= numPages}
              onClick={() => goToPage(pageNumber + 1)}
              type="button"
            >
              →
            </button>
            <span className="pdf-control-divider" />
            <button
              aria-label="缩小"
              disabled={scale <= MIN_SCALE}
              onClick={() => setScale((current) => clamp(current - SCALE_STEP, MIN_SCALE, MAX_SCALE))}
              type="button"
            >
              −
            </button>
            <output aria-label="当前缩放比例">{Math.round(scale * 100)}%</output>
            <button
              aria-label="放大"
              disabled={scale >= MAX_SCALE}
              onClick={() => setScale((current) => clamp(current + SCALE_STEP, MIN_SCALE, MAX_SCALE))}
              type="button"
            >
              +
            </button>
            <button className="pdf-reader-close" aria-label="关闭阅读器" onClick={onClose} type="button">
              ×
            </button>
          </div>
        </header>

        <div className={`pdf-reader-body ${outlineOpen ? "" : "outline-collapsed"} ${bilingualOpen ? "bilingual-open" : ""}`}>
          <aside className="pdf-outline" id="pdf-document-outline" ref={outlineRef}>
            <div className="pdf-sidebar-tabs" role="tablist" aria-label="文档导航类型">
              <button
                aria-selected={sidebarTab === "outline"}
                className={sidebarTab === "outline" ? "active" : ""}
                onClick={() => setSidebarTab("outline")}
                role="tab"
                type="button"
              >
                目录 <span>{flatOutline.length}</span>
              </button>
              <button
                aria-selected={sidebarTab === "references"}
                className={sidebarTab === "references" ? "active" : ""}
                onClick={() => setSidebarTab("references")}
                role="tab"
                type="button"
              >
                引用 <span>{references.length}</span>
              </button>
            </div>
            {sidebarTab === "outline" ? (
              <>
                <div className="pdf-outline-heading">
                  <div><span>DOCUMENT MAP</span><strong>文档目录</strong></div>
                  <small>{flatOutline.length} 个标题</small>
                </div>
                {outlineLoading && <div className="pdf-outline-message">正在读取标题树…</div>}
                {outlineError && <div className="pdf-outline-message error">{outlineError}</div>}
                {!outlineLoading && !outlineError && outline.length === 0 && (
                  <div className="pdf-outline-message">这篇文献暂未解析出标题。</div>
                )}
                {outline.length > 0 && (
                  <nav aria-label="论文标题导航">
                    <OutlineTree
                      activeBlockId={activeOutline?.block_id}
                      items={outline}
                      onSelect={selectOutline}
                    />
                  </nav>
                )}
              </>
            ) : (
              <>
                <div className="pdf-outline-heading">
                  <div><span>CITATION MAP</span><strong>参考文献</strong></div>
                  <small>{mentions.length} 处正文引用</small>
                </div>
                {referencesLoading && <div className="pdf-outline-message">正在建立引用链接…</div>}
                {referencesError && <div className="pdf-outline-message error">{referencesError}</div>}
                {!referencesLoading && !referencesError && references.length === 0 && (
                  <div className="pdf-outline-message">这篇文献暂未解析出编号参考文献。</div>
                )}
                {references.length > 0 && (
                  <ReferencePanel
                    activeTarget={referenceTarget}
                    items={references}
                    mentions={mentions}
                    onSelectMention={selectMention}
                    onSelectReference={selectReference}
                  />
                )}
              </>
            )}
          </aside>
          <div className="pdf-reader-viewport" ref={viewportRef}>
            <Document
              error={<div className="pdf-reader-state error">PDF 加载失败，请确认后端服务可用。</div>}
              file={fileUrl}
              loading={<div className="pdf-reader-state">正在加载 PDF…</div>}
              noData={<div className="pdf-reader-state">没有可读取的 PDF 文件。</div>}
              onLoadSuccess={({ numPages: loadedPages }) => {
                const targetPage = clamp(initialPage, 1, loadedPages);
                setNumPages(loadedPages);
                setPageNumber(targetPage);
                setPageInput(String(targetPage));
              }}
            >
              <div
                className="pdf-page-shell"
                onClick={handlePageClick}
                onMouseUp={captureTextSelection}
                ref={pageShellRef}
              >
                <Page
                  customTextRenderer={renderCitationText}
                  loading={<div className="pdf-page-loading">正在渲染第 {pageNumber} 页…</div>}
                  onLoadSuccess={(page) => setPageSize({
                    width: page.originalWidth,
                    height: page.originalHeight,
                  })}
                  pageNumber={pageNumber}
                  renderAnnotationLayer
                  renderTextLayer
                  scale={scale}
                />
                {highlightRect && (
                  <div
                    aria-label={`${activePageTarget?.kind === "evidence" ? "证据" : "引用"} ${activePageTarget?.label} 的原文位置`}
                    className={`pdf-evidence-highlight ${activePageTarget?.kind ?? ""}`}
                    style={{
                      left: `${highlightRect.left}%`,
                      top: `${highlightRect.top}%`,
                      width: `${highlightRect.width}%`,
                      height: `${highlightRect.height}%`,
                    }}
                    title={activePageTarget?.context}
                  >
                    <span>{activePageTarget?.kind === "evidence"
                      ? activePageTarget.label
                      : `[${activePageTarget?.label}]`}</span>
                  </div>
                )}
              </div>
            </Document>
          </div>
          <aside className="pdf-bilingual-pane" ref={bilingualRef}>
            <header>
              <div>
                <span>ALIGNED READING</span>
                <strong>第 {pageNumber} 页译文</strong>
              </div>
              <div className="pdf-bilingual-actions">
                <button
                  className={bilingualTarget === "zh" ? "active" : ""}
                  onClick={() => changeBilingualTarget("zh")}
                  type="button"
                >中</button>
                <button
                  className={bilingualTarget === "en" ? "active" : ""}
                  onClick={() => changeBilingualTarget("en")}
                  type="button"
                >EN</button>
                <button aria-label="关闭双语对照" onClick={() => setBilingualOpen(false)} type="button">×</button>
              </div>
            </header>
            {pageTranslationLoading && (
              <div className="pdf-bilingual-state">正在按段落翻译当前页…</div>
            )}
            {pageTranslationError && (
              <div className="pdf-bilingual-state error">{pageTranslationError}</div>
            )}
            {pageTranslation && (
              <div className="pdf-bilingual-segments">
                {pageTranslation.segments.map((segment, index) => (
                  <article data-block-id={segment.block_id} key={segment.block_id}>
                    <small>{String(index + 1).padStart(2, "0")} · {segment.block_id}</small>
                    <p>{segment.translation}</p>
                    <details>
                      <summary>查看原文</summary>
                      <blockquote>{segment.source_text}</blockquote>
                    </details>
                  </article>
                ))}
                {pageTranslation.truncated && (
                  <div className="pdf-bilingual-warning">当前页文本超过单次翻译上限，仅显示已处理段落。</div>
                )}
                <footer>{pageTranslation.model || pageTranslation.provider} · 滚动位置与原文联动</footer>
              </div>
            )}
          </aside>
        </div>

        {selectionDraft && !translationLoading && !translation && !translationError && (
          <button
            className="pdf-translate-action"
            onClick={() => void translateSelectedText()}
            onMouseDown={(event) => event.preventDefault()}
            style={{ left: selectionDraft.x, top: selectionDraft.y }}
            type="button"
          >
            译为 {inferTranslationTarget(selectionDraft.text) === "en" ? "English" : "中文"}
          </button>
        )}
        {selectionDraft && (translationLoading || translation || translationError) && (
          <aside className="pdf-translation-panel" aria-live="polite">
            <header>
              <div><span>ACADEMIC TRANSLATION</span><strong>选区翻译</strong></div>
              <button aria-label="关闭翻译" onClick={closeTranslation} type="button">×</button>
            </header>
            <blockquote>{selectionDraft.text}</blockquote>
            {translationLoading && <p className="pdf-translation-state">正在保持术语和公式格式进行翻译…</p>}
            {translationError && <p className="pdf-translation-state error">{translationError}</p>}
            {translation && (
              <div className="pdf-translation-result">
                <p>{translation.translation}</p>
                <small>{translation.model || translation.provider} · {translation.target_language === "zh" ? "译为中文" : "Translated to English"}</small>
              </div>
            )}
          </aside>
        )}

        <footer className="pdf-reader-footer">
          <div className="pdf-reader-location">
            {activePageTarget ? (
              <>
                <strong className={activePageTarget.kind}>{activePageTarget.kind === "evidence"
                  ? activePageTarget.label
                  : `[${activePageTarget.label}]`}</strong>
                <span className="pdf-evidence-status">{highlightRect
                  ? activePageTarget.kind === "evidence"
                    ? "已高亮原文证据"
                    : activePageTarget.kind === "reference"
                      ? "已定位参考文献条目"
                      : "已定位正文引用"
                  : "已定位目标页，暂无可用坐标"}</span>
              </>
            ) : (
              <span className="pdf-reader-section" title={activeOutline?.text}>
                {activeOutline ? `当前章节：${activeOutline.text}` : "拖拽选中文字可翻译 · 方向键翻页 · Esc 关闭"}
              </span>
            )}
          </div>
          <div className="pdf-reader-footer-actions">
            {evidence && evidence.page_number !== pageNumber && (
              <button onClick={() => goToPage(evidence.page_number)} type="button">
                返回证据 {evidence.evidence_id}
              </button>
            )}
            <a href={`${fileUrl}#page=${pageNumber}`} rel="noreferrer" target="_blank">
              在浏览器新标签打开 ↗
            </a>
          </div>
        </footer>
      </section>
    </div>
  );
}
