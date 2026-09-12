"use client";

import { FormEvent, useMemo, useState } from "react";

import {
  AcademicTranslationResponse,
  translateAcademicText,
} from "../lib/api";

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

export default function AcademicTranslationWorkbench() {
  const [sourceText, setSourceText] = useState("");
  const [sourceLanguage, setSourceLanguage] = useState<"auto" | "zh" | "en">("zh");
  const [targetLanguage, setTargetLanguage] = useState<"zh" | "en">("en");
  const [documentType, setDocumentType] = useState<"abstract" | "paper">("abstract");
  const [glossaryText, setGlossaryText] = useState("");
  const [result, setResult] = useState<AcademicTranslationResponse | null>(null);
  const [translatedSource, setTranslatedSource] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<"translation" | "bilingual" | null>(null);
  const glossary = useMemo(() => parseGlossary(glossaryText), [glossaryText]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!sourceText.trim() || glossary.invalidLines.length) return;
    setLoading(true);
    setError(null);
    try {
      const submittedSource = sourceText.trim();
      setResult(await translateAcademicText({
        text: submittedSource,
        sourceLanguage,
        targetLanguage,
        documentType,
        glossary: glossary.terms,
      }));
      setTranslatedSource(submittedSource);
    } catch (requestError) {
      setResult(null);
      setTranslatedSource("");
      setError(requestError instanceof Error ? requestError.message : "学术翻译失败");
    } finally {
      setLoading(false);
    }
  }

  async function copyText(mode: "translation" | "bilingual") {
    if (!result) return;
    const content = mode === "translation"
      ? result.translation
      : `原文\n\n${translatedSource}\n\nTranslation\n\n${result.translation}`;
    await navigator.clipboard.writeText(content);
    setCopied(mode);
    window.setTimeout(() => setCopied(null), 1600);
  }

  return (
    <div className="academic-translation-workbench">
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
        <label className="academic-source-field">
          中文摘要或论文正文
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
              : ` 已识别 ${glossary.terms.length} 条。`}
          </small>
        </label>
        <button
          disabled={
            loading
            || !sourceText.trim()
            || Boolean(glossary.invalidLines.length)
            || (sourceLanguage !== "auto" && sourceLanguage === targetLanguage)
          }
          type="submit"
        >
          {loading ? "正在分段翻译与校验…" : "生成学术译文"}
        </button>
      </form>
      {error && <p className="academic-translation-error">{error}</p>}
      {result && (
        <>
          {result.warnings.map((warning) => <p className="citation-warning" key={warning}>{warning}</p>)}
          <div className="translation-quality-strip">
            <span>{result.model || result.provider}</span>
            <span>{result.paragraph_count} 个段落 · {result.request_count} 次模型请求</span>
            {result.preservation_checks.map((check) => (
              <span key={check.kind}>已核验 {PRESERVATION_LABELS[check.kind]} × {check.count}</span>
            ))}
            {result.glossary_applied.length > 0 && (
              <span>已应用术语 × {result.glossary_applied.reduce((sum, item) => sum + item.count, 0)}</span>
            )}
          </div>
          <div className="academic-translation-result">
            <article>
              <header><span>SOURCE</span><small>{result.source_characters} 字符</small></header>
              <p>{translatedSource}</p>
            </article>
            <article>
              <header>
                <span>ACADEMIC TRANSLATION</span>
                <div>
                  <button onClick={() => void copyText("translation")} type="button">{copied === "translation" ? "已复制" : "复制译文"}</button>
                  <button onClick={() => void copyText("bilingual")} type="button">{copied === "bilingual" ? "已复制" : "复制对照"}</button>
                </div>
              </header>
              <p>{result.translation}</p>
            </article>
          </div>
          {result.glossary_applied.length > 0 && (
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
