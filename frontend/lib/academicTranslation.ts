export interface TranslationMarkdownInput {
  title: string;
  source: string;
  translation: string;
  status: "draft" | "reviewed";
  sourceLanguage: string;
  targetLanguage: string;
  exportedAt?: Date;
}

export function buildTranslationMarkdown(input: TranslationMarkdownInput): string {
  return [
    `# ${input.title.replace(/\r?\n/g, " ")}`,
    "",
    `- Status: ${input.status}`,
    `- Language: ${input.sourceLanguage} → ${input.targetLanguage}`,
    `- Exported: ${(input.exportedAt ?? new Date()).toISOString()}`,
    "",
    "## Source",
    "",
    input.source,
    "",
    "## Academic Translation",
    "",
    input.translation,
    "",
  ].join("\n");
}

export function safeTranslationFilename(title: string): string {
  const base = title
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .trim()
    .slice(0, 80);
  return `${base || "translation"}.md`;
}
