export const MAX_TRANSLATION_CHARS = 12000;

export function inferTranslationTarget(text: string): "zh" | "en" {
  return /[\u3400-\u9fff]/u.test(text) ? "en" : "zh";
}
