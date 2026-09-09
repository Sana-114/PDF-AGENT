"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { DocumentOutlineNode, getDocumentOutline } from "../lib/api";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface PdfReaderProps {
  documentId: string;
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
  const [outlineOpen, setOutlineOpen] = useState(
    () => typeof window === "undefined" || window.innerWidth > 760,
  );
  const outlineRef = useRef<HTMLElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);

  const flatOutline = useMemo(() => flattenOutline(outline), [outline]);
  const activeOutline = useMemo(
    () => flatOutline.reduce<DocumentOutlineNode | undefined>(
      (active, item) => item.page_number <= pageNumber ? item : active,
      undefined,
    ),
    [flatOutline, pageNumber],
  );

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
    viewportRef.current?.scrollTo({ top: 0, left: 0 });
  }, [pageNumber]);

  useEffect(() => {
    outlineRef.current
      ?.querySelector<HTMLElement>('[aria-current="location"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [activeOutline?.block_id]);

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
              ☰ <span>目录</span>
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

        <div className={`pdf-reader-body ${outlineOpen ? "" : "outline-collapsed"}`}>
          <aside className="pdf-outline" id="pdf-document-outline" ref={outlineRef}>
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
              <Page
                loading={<div className="pdf-page-loading">正在渲染第 {pageNumber} 页…</div>}
                pageNumber={pageNumber}
                renderAnnotationLayer
                renderTextLayer
                scale={scale}
              />
            </Document>
          </div>
        </div>

        <footer className="pdf-reader-footer">
          <span title={activeOutline?.text}>
            {activeOutline ? `当前章节：${activeOutline.text}` : "方向键翻页 · Esc 关闭"}
          </span>
          <a href={`${fileUrl}#page=${pageNumber}`} rel="noreferrer" target="_blank">
            在浏览器新标签打开 ↗
          </a>
        </footer>
      </section>
    </div>
  );
}
