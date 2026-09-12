"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  AcademicTranslationDraft,
  AcademicTranslationResponse,
  TranslationGlossary,
  createTranslationDraft,
  deleteTranslationDraft,
  deleteTranslationGlossary,
  listTranslationDrafts,
  listTranslationGlossaries,
  saveTranslationGlossary,
  translateAcademicText,
  updateTranslationDraft,
} from "../lib/api";
import {
  buildTranslationMarkdown,
  safeTranslationFilename,
} from "../lib/academicTranslation";

const PRESERVATION_LABELS: Record<
  AcademicTranslationResponse["preservation_checks"][number]["kind"],
  string
> = {
  formula: "公式",
  code: "代码",
  citation: "引用",
  url: "链接/DOI",
  number: "数字",
};

function parseGlossary(value: string): {
  terms: Array<{ source: string; target: string }>;
  invalidLines: number[];
} {
  const terms: Array<{ source: string; target: string }> = [];
  const invalidLines: number[] = [];
  value.split(/\r?\n/).forEach((rawLine, index) => {
    const line = rawLine.trim();
    if (!line) return;
    const match = line.match(/^(.+?)\s*(?:=>|=|：)\s*(.+)$/);
    if (!match) {
      invalidLines.push(index + 1);
      return;
    }
    const source = match[1].trim();
    const target = match[2].trim();
    if (!source || !target) {
      invalidLines.push(index + 1);
      return;
    }
    terms.push({ source, target });
  });
  return { terms, invalidLines };
}

function formatGlossary(terms: TranslationGlossary["terms"]): string {
  return terms.map((term) => `${term.source} = ${term.target}`).join("\n");
}

function defaultDraftTitle(documentType: "abstract" | "paper"): string {
  const stamp = new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date());
  return `${documentType === "abstract" ? "摘要" : "正文"}译稿 ${stamp}`;
}

function downloadMarkdown(
  title: string,
  source: string,
  translation: string,
  status: "draft" | "reviewed",
  sourceLanguage: string,
  targetLanguage: string,
) {
  const markdown = buildTranslationMarkdown({
    title,
    source,
    translation,
    status,
    sourceLanguage,
    targetLanguage,
  });
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = safeTranslationFilename(title);
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function AcademicTranslationWorkbench() {
  const [sourceText, setSourceText] = useState("");
  const [sourceLanguage, setSourceLanguage] = useState<"auto" | "zh" | "en">("zh");
  const [targetLanguage, setTargetLanguage] = useState<"zh" | "en">("en");
  const [documentType, setDocumentType] = useState<"abstract" | "paper">("abstract");
  const [glossaryText, setGlossaryText] = useState("");
  const [glossaryName, setGlossaryName] = useState("");
  const [glossaries, setGlossaries] = useState<TranslationGlossary[]>([]);
  const [selectedGlossaryId, setSelectedGlossaryId] = useState("");
  const [drafts, setDrafts] = useState<AcademicTranslationDraft[]>([]);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftStatus, setDraftStatus] = useState<"draft" | "reviewed">("draft");
  const [result, setResult] = useState<AcademicTranslationResponse | null>(null);
  const [translatedSource, setTranslatedSource] = useState("");
  const [editedTranslation, setEditedTranslation] = useState("");
  const [translationProvider, setTranslationProvider] = useState<string | null>(null);
  const [translationModel, setTranslationModel] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [libraryAction, setLibraryAction] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState<"translation" | "bilingual" | null>(null);
  const glossary = useMemo(() => parseGlossary(glossaryText), [glossaryText]);
  const hasTranslation = Boolean(translatedSource);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listTranslationGlossaries(), listTranslationDrafts()])
      .then(([nextGlossaries, nextDrafts]) => {
        if (!cancelled) {
          setGlossaries(nextGlossaries);
          setDrafts(nextDrafts);
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : "翻译工作区加载失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function chooseGlossary(glossaryId: string) {
    setSelectedGlossaryId(glossaryId);
    if (!glossaryId) return;
    const selected = glossaries.find((item) => item.id === glossaryId);
    if (!selected) return;
    setGlossaryName(selected.name);
    setGlossaryText(formatGlossary(selected.terms));
    setSourceLanguage(selected.source_language);
    setTargetLanguage(selected.target_language);
    setNotice(`已载入术语库“${selected.name}”。`);
  }

  function startNewGlossary() {
    setSelectedGlossaryId("");
    setGlossaryName("");
    setGlossaryText("");
    setNotice("已切换为新术语库。填写名称和术语后即可保存。");
  }

  async function persistGlossary() {
    if (
      !glossaryName.trim()
      || !glossary.terms.length
      || glossary.terms.length > 50
      || glossary.invalidLines.length
      || sourceLanguage === "auto"
      || sourceLanguage === targetLanguage
    ) return;
    setLibraryAction(true);
    setError(null);
    try {
      const saved = await saveTranslationGlossary({
        id: selectedGlossaryId || undefined,
        name: glossaryName.trim(),
        sourceLanguage,
        targetLanguage,
        terms: glossary.terms,
      });
      setGlossaries((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      setSelectedGlossaryId(saved.id);
      setNotice(`术语库“${saved.name}”已持久保存。`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "术语库保存失败");
    } finally {
      setLibraryAction(false);
    }
  }

  async function removeGlossary() {
    if (!selectedGlossaryId) return;
    const selected = glossaries.find((item) => item.id === selectedGlossaryId);
    if (!window.confirm(`删除术语库“${selected?.name ?? "未命名"}”吗？`)) return;
    setLibraryAction(true);
    setError(null);
    try {
      await deleteTranslationGlossary(selectedGlossaryId);
      setGlossaries((current) => current.filter((item) => item.id !== selectedGlossaryId));
      setDrafts((current) => current.map((item) => (
        item.glossary_id === selectedGlossaryId ? { ...item, glossary_id: null } : item
      )));
      startNewGlossary();
      setNotice("术语库已删除，已保存译稿仍然保留。");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "术语库删除失败");
    } finally {
      setLibraryAction(false);
    }
  }

  function loadDraft(nextDraftId: string) {
    if (!nextDraftId) return;
    const draft = drafts.find((item) => item.id === nextDraftId);
    if (!draft) return;
    setDraftId(draft.id);
    setDraftTitle(draft.title);
    setDraftStatus(draft.status);
    setSourceText(draft.source_text);
    setTranslatedSource(draft.source_text);
    setEditedTranslation(draft.translated_text);
    setSourceLanguage(draft.source_language);
    setTargetLanguage(draft.target_language);
    setDocumentType(draft.document_type);
    setTranslationProvider(draft.provider);
    setTranslationModel(draft.model);
    setResult(null);
    if (draft.glossary_id) chooseGlossary(draft.glossary_id);
    setNotice(`已载入${draft.status === "reviewed" ? "审校完成" : "待审校"}译稿。`);
  }

  async function removeDraft() {
    if (!draftId) return;
    const selected = drafts.find((item) => item.id === draftId);
    if (!window.confirm(`删除译稿“${selected?.title ?? "未命名"}”吗？`)) return;
    setLibraryAction(true);
    setError(null);
    try {
      await deleteTranslationDraft(draftId);
      setDrafts((current) => current.filter((item) => item.id !== draftId));
      setDraftId(null);
      setDraftTitle("");
      setDraftStatus("draft");
      setTranslatedSource("");
      setEditedTranslation("");
      setResult(null);
      setNotice("译稿已删除。");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "译稿删除失败");
    } finally {
      setLibraryAction(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !sourceText.trim()
      || glossary.invalidLines.length
      || glossary.terms.length > 50
    ) return;
    setLoading(true);
    setError(null);
    setNotice(null);
    try {
      const submittedSource = sourceText.trim();
      const nextResult = await translateAcademicText({
        text: submittedSource,
        sourceLanguage,
        targetLanguage,
        documentType,
        glossary: glossary.terms,
      });
      setResult(nextResult);
      setTranslatedSource(submittedSource);
      setEditedTranslation(nextResult.translation);
      setTranslationProvider(nextResult.provider);
      setTranslationModel(nextResult.model);
      setDraftId(null);
      setDraftTitle(defaultDraftTitle(documentType));
      setDraftStatus("draft");
      setNotice("译文已生成，可直接编辑、保存或完成审校。");
    } catch (requestError) {
      setResult(null);
      setTranslatedSource("");
      setEditedTranslation("");
      setError(requestError instanceof Error ? requestError.message : "学术翻译失败");
    } finally {
      setLoading(false);
    }
  }

  async function persistDraft(status: "draft" | "reviewed") {
    if (!hasTranslation || !editedTranslation.trim() || !draftTitle.trim()) return;
    setLibraryAction(true);
    setError(null);
    try {
      const saved = draftId
        ? await updateTranslationDraft(draftId, {
            title: draftTitle.trim(),
            translatedText: editedTranslation,
            status,
          })
        : await createTranslationDraft({
            title: draftTitle.trim(),
            sourceText: translatedSource,
            translatedText: editedTranslation,
            sourceLanguage,
            targetLanguage,
            documentType,
            status,
            glossaryId: selectedGlossaryId || null,
            provider: translationProvider,
            model: translationModel,
          });
      setDraftId(saved.id);
      setDraftStatus(saved.status);
      setDrafts((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      setNotice(status === "reviewed" ? "译稿已标记为人工审校完成。" : "译稿已保存。 ");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "译稿保存失败");
    } finally {
      setLibraryAction(false);
    }
  }

  async function copyText(mode: "translation" | "bilingual") {
    if (!hasTranslation || !editedTranslation.trim()) return;
    const content = mode === "translation"
      ? editedTranslation
      : `原文\n\n${translatedSource}\n\nTranslation\n\n${editedTranslation}`;
    await navigator.clipboard.writeText(content);
    setCopied(mode);
    window.setTimeout(() => setCopied(null), 1600);
  }

  return (
    <div className="academic-translation-workbench">
      <div className="translation-workspace-bar">
        <label>
          已保存译稿
          <select onChange={(event) => loadDraft(event.target.value)} value={draftId ?? ""}>
            <option value="">选择历史译稿…</option>
            {drafts.map((draft) => (
              <option key={draft.id} value={draft.id}>
                {draft.status === "reviewed" ? "✓" : "○"} {draft.title}
              </option>
            ))}
          </select>
        </label>
        <span>{drafts.length} 篇译稿 · {drafts.filter((item) => item.status === "reviewed").length} 篇已审校</span>
        <button disabled={!draftId || libraryAction} onClick={() => void removeDraft()} type="button">删除译稿</button>
      </div>
      <form className="academic-translation-form" onSubmit={submit}>
        <div className="translation-controls">
          <label>
            原文语言
            <select onChange={(event) => setSourceLanguage(event.target.value as "auto" | "zh" | "en")} value={sourceLanguage}>
              <option value="zh">中文</option>
              <option value="en">English</option>
              <option value="auto">自动识别</option>
            </select>
          </label>
          <label>
            目标语言
            <select onChange={(event) => setTargetLanguage(event.target.value as "zh" | "en")} value={targetLanguage}>
              <option value="en">English</option>
              <option value="zh">中文</option>
            </select>
          </label>
          <label>
            文本类型
            <select onChange={(event) => setDocumentType(event.target.value as "abstract" | "paper")} value={documentType}>
              <option value="abstract">论文摘要</option>
              <option value="paper">论文正文</option>
            </select>
          </label>
        </div>
        <div className="translation-glossary-library">
          <label>
            已保存术语库
            <select onChange={(event) => chooseGlossary(event.target.value)} value={selectedGlossaryId}>
              <option value="">选择或新建术语库…</option>
              {glossaries.map((item) => (
                <option key={item.id} value={item.id}>{item.name} · {item.term_count} 条</option>
              ))}
            </select>
          </label>
          <label>
            术语库名称
            <input maxLength={160} onChange={(event) => setGlossaryName(event.target.value)} placeholder="例如：计算机视觉核心术语" value={glossaryName} />
          </label>
          <div>
            <button onClick={startNewGlossary} type="button">新建</button>
            <button
              disabled={
                libraryAction
                || !glossaryName.trim()
                || !glossary.terms.length
                || glossary.terms.length > 50
                || Boolean(glossary.invalidLines.length)
                || sourceLanguage === "auto"
                || sourceLanguage === targetLanguage
              }
              onClick={() => void persistGlossary()}
              type="button"
            >
              {selectedGlossaryId ? "更新术语库" : "保存术语库"}
            </button>
            <button disabled={!selectedGlossaryId || libraryAction} onClick={() => void removeGlossary()} type="button">删除</button>
          </div>
        </div>
        <label className="academic-source-field">
          摘要或论文正文
          <textarea
            maxLength={24000}
            onChange={(event) => setSourceText(event.target.value)}
            placeholder="粘贴需要翻译的学术文本。LaTeX 公式、代码、[11] 等引用和数字会进行完整性保护。"
            rows={10}
            value={sourceText}
          />
          <small>{sourceText.length.toLocaleString("zh-CN")} / 24,000 字符</small>
        </label>
        <label className="academic-glossary-field">
          术语表（可选）
          <textarea
            onChange={(event) => setGlossaryText(event.target.value)}
            placeholder={"检索增强生成 = retrieval-augmented generation\n大语言模型 = large language model"}
            rows={4}
            value={glossaryText}
          />
          <small>
            每行“源术语 = 目标术语”，最多 50 条。
            {glossary.invalidLines.length
              ? ` 第 ${glossary.invalidLines.join("、")} 行格式无效。`
              : glossary.terms.length > 50
                ? ` 已识别 ${glossary.terms.length} 条，超过 50 条上限。`
                : ` 已识别 ${glossary.terms.length} 条。`}
          </small>
        </label>
        <button
          disabled={
            loading
            || !sourceText.trim()
            || Boolean(glossary.invalidLines.length)
            || glossary.terms.length > 50
            || (sourceLanguage !== "auto" && sourceLanguage === targetLanguage)
          }
          type="submit"
        >
          {loading ? "正在分段翻译与校验…" : "生成学术译文"}
        </button>
      </form>
      {error && <p className="academic-translation-error">{error}</p>}
      {notice && <p className="academic-translation-notice">{notice}</p>}
      {hasTranslation && (
        <>
          {result?.warnings.map((warning) => <p className="citation-warning" key={warning}>{warning}</p>)}
          <div className="translation-quality-strip">
            <span className={draftStatus === "reviewed" ? "reviewed" : "draft"}>
              {draftStatus === "reviewed" ? "人工审校完成" : "待人工审校"}
            </span>
            <span>{translationModel || translationProvider || "历史译稿"}</span>
            {result && <span>{result.paragraph_count} 个段落 · {result.request_count} 次模型请求</span>}
            {result?.preservation_checks.map((check) => (
              <span key={check.kind}>已核验 {PRESERVATION_LABELS[check.kind]} × {check.count}</span>
            ))}
            {result && result.glossary_applied.length > 0 && (
              <span>已应用术语 × {result.glossary_applied.reduce((sum, item) => sum + item.count, 0)}</span>
            )}
          </div>
          <div className="translation-draft-actions">
            <input maxLength={180} onChange={(event) => setDraftTitle(event.target.value)} placeholder="译稿标题" value={draftTitle} />
            <button disabled={!draftTitle.trim() || !editedTranslation.trim() || libraryAction} onClick={() => void persistDraft("draft")} type="button">保存译稿</button>
            <button disabled={!draftTitle.trim() || !editedTranslation.trim() || libraryAction} onClick={() => void persistDraft("reviewed")} type="button">完成审校</button>
            <button
              disabled={!editedTranslation.trim()}
              onClick={() => downloadMarkdown(draftTitle || "translation", translatedSource, editedTranslation, draftStatus, sourceLanguage, targetLanguage)}
              type="button"
            >
              导出 Markdown
            </button>
          </div>
          <div className="academic-translation-result">
            <article>
              <header><span>SOURCE</span><small>{translatedSource.length} 字符</small></header>
              <p>{translatedSource}</p>
            </article>
            <article>
              <header>
                <span>ACADEMIC TRANSLATION · EDITABLE</span>
                <div>
                  <button onClick={() => void copyText("translation")} type="button">{copied === "translation" ? "已复制" : "复制译文"}</button>
                  <button onClick={() => void copyText("bilingual")} type="button">{copied === "bilingual" ? "已复制" : "复制对照"}</button>
                </div>
              </header>
              <textarea
                maxLength={48000}
                onChange={(event) => {
                  setEditedTranslation(event.target.value);
                  if (draftStatus === "reviewed") {
                    setDraftStatus("draft");
                    setNotice("译文已修改，请重新保存并完成审校。");
                  }
                }}
                value={editedTranslation}
              />
            </article>
          </div>
          {result && result.glossary_applied.length > 0 && (
            <div className="translation-glossary-report">
              <strong>术语应用记录</strong>
              {result.glossary_applied.map((term) => (
                <span key={term.source}>{term.source} → {term.target} × {term.count}</span>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
