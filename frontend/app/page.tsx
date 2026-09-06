"use client";

import { ChangeEvent, DragEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  deleteDocument,
  documentFileUrl,
  DocumentRecord,
  listDocuments,
  uploadDocument,
} from "../lib/api";

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
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async (quiet = false) => {
    try {
      const response = await listDocuments();
      setDocuments(response.items);
      if (!quiet) setError(null);
    } catch (requestError) {
      if (!quiet) setError(requestError instanceof Error ? requestError.message : "文献列表加载失败");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(true), 3000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const processingCount = useMemo(
    () => documents.filter((document) => ["queued", "processing"].includes(document.status)).length,
    [documents],
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

  async function remove(document: DocumentRecord) {
    if (!window.confirm(`确定删除“${document.title || document.original_filename}”吗？`)) return;
    try {
      await deleteDocument(document.id);
      setDocuments((current) => current.filter((item) => item.id !== document.id));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "删除失败");
    }
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
          <span>问答</span>
          <span>引用图谱</span>
          <span>写作台</span>
        </nav>
        <div className="system-pill"><span /> 本地工作区</div>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">EVIDENCE-FIRST RESEARCH</p>
          <h1>让每一个结论，<br /><em>都能回到原文。</em></h1>
          <p className="hero-copy">上传论文，建立带页码和坐标的结构化知识库。当前初版已接通内容去重、异步解析和版本识别基础链路。</p>
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

        {notice && <div className="notice success">{notice}</div>}
        {error && <div className="notice error">{error}</div>}

        <div className="section-heading">
          <div><p className="eyebrow">LIBRARY</p><h2>最近文献</h2></div>
          <button className="ghost" onClick={() => void refresh()}>刷新</button>
        </div>

        <div className="document-list">
          {loading && <div className="empty">正在连接文献服务…</div>}
          {!loading && documents.length === 0 && (
            <div className="empty"><strong>文献库还是空的</strong><span>从上方上传比赛测试 PDF，建立第一条解析记录。</span></div>
          )}
          {documents.map((document) => (
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
                  <span>·</span>{new Date(document.created_at).toLocaleString("zh-CN")}
                </p>
                {document.duplicate_recommendation && (
                  <div className="version-warning"><strong>版本提醒</strong>{document.duplicate_recommendation}</div>
                )}
                {document.error_message && <div className="failure">{document.error_message}</div>}
              </div>
              <div className="actions">
                <a href={documentFileUrl(document.id)} target="_blank" rel="noreferrer">打开原文</a>
                <button onClick={() => void remove(document)}>删除</button>
              </div>
            </article>
          ))}
        </div>
      </section>

      <footer>PaperPilot MVP · 所有答案都将绑定可验证的原文证据</footer>
    </main>
  );
}

