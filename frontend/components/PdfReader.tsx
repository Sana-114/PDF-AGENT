"use client";

import { FormEvent, useEffect, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface PdfReaderProps {
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

export default function PdfReader({ fileUrl, title, initialPage = 1, onClose }: PdfReaderProps) {
  const [numPages, setNumPages] = useState(0);
  const [pageNumber, setPageNumber] = useState(Math.max(1, initialPage));
  const [pageInput, setPageInput] = useState(String(Math.max(1, initialPage)));
  const [scale, setScale] = useState(1);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
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
  }, [pageNumber]);

  function goToPage(target: number) {
    const finalPage = clamp(Math.round(target), 1, Math.max(1, numPages));
    setPageNumber(finalPage);
    setPageInput(String(finalPage));
  }

  function submitPage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = Number(pageInput);
    if (Number.isFinite(target)) goToPage(target);
    else setPageInput(String(pageNumber));
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

        <div className="pdf-reader-viewport">
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

        <footer className="pdf-reader-footer">
          <span>方向键翻页 · Esc 关闭</span>
          <a href={`${fileUrl}#page=${pageNumber}`} rel="noreferrer" target="_blank">
            在浏览器新标签打开 ↗
          </a>
        </footer>
      </section>
    </div>
  );
}
