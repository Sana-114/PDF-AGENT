export interface PercentRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

export function bboxToPercentRect(
  bbox: number[] | null | undefined,
  pageWidth: number,
  pageHeight: number,
): PercentRect | null {
  if (!bbox || bbox.length !== 4 || pageWidth <= 0 || pageHeight <= 0) return null;
  if (![...bbox, pageWidth, pageHeight].every(Number.isFinite)) return null;

  const left = clamp(Math.min(bbox[0], bbox[2]), 0, pageWidth);
  const right = clamp(Math.max(bbox[0], bbox[2]), 0, pageWidth);
  const top = clamp(Math.min(bbox[1], bbox[3]), 0, pageHeight);
  const bottom = clamp(Math.max(bbox[1], bbox[3]), 0, pageHeight);
  if (right <= left || bottom <= top) return null;

  return {
    left: (left / pageWidth) * 100,
    top: (top / pageHeight) * 100,
    width: ((right - left) / pageWidth) * 100,
    height: ((bottom - top) / pageHeight) * 100,
  };
}
