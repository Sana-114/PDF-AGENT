export const MAX_TRANSLATION_CHARS = 12000;

export function inferTranslationTarget(text: string): "zh" | "en" {
  return /[\u3400-\u9fff]/u.test(text) ? "en" : "zh";
}

export function mapSynchronizedScroll(
  sourceTop: number,
  sourceScrollHeight: number,
  sourceClientHeight: number,
  targetScrollHeight: number,
  targetClientHeight: number,
): number | null {
  const sourceRange = sourceScrollHeight - sourceClientHeight;
  const targetRange = targetScrollHeight - targetClientHeight;
  if (sourceRange <= 0 || targetRange <= 0) return null;
  const ratio = Math.min(Math.max(sourceTop / sourceRange, 0), 1);
  return ratio * targetRange;
}
