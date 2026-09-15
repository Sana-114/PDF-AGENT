export interface ReaderCitationLocation {
  citation_id: string;
  label: string;
  page_number: number;
  bbox: number[] | null;
}

export interface PagePoint {
  x: number;
  y: number;
}

export function nearestCitationLocation<T extends ReaderCitationLocation>(
  items: readonly T[],
  label: string,
  pageNumber: number,
  point?: PagePoint,
): T | null {
  const candidates = items.filter(
    (item) => item.label === label && item.page_number === pageNumber,
  );
  if (candidates.length === 0) return null;
  if (!point) return candidates[0];

  return candidates.reduce((nearest, candidate) => {
    return distanceToCenter(candidate.bbox, point) < distanceToCenter(nearest.bbox, point)
      ? candidate
      : nearest;
  });
}

export function adjacentCitationLocation<T extends ReaderCitationLocation>(
  items: readonly T[],
  label: string,
  currentId: string | null,
  direction: -1 | 1,
): T | null {
  const candidates = items.filter((item) => item.label === label);
  if (candidates.length === 0) return null;
  const currentIndex = candidates.findIndex((item) => item.citation_id === currentId);
  if (currentIndex < 0) return direction > 0 ? candidates[0] : candidates.at(-1) ?? null;
  return candidates[(currentIndex + direction + candidates.length) % candidates.length];
}

function distanceToCenter(bbox: number[] | null, point: PagePoint): number {
  if (!bbox || bbox.length !== 4 || bbox.some((value) => !Number.isFinite(value))) {
    return Number.POSITIVE_INFINITY;
  }
  const centerX = (bbox[0] + bbox[2]) / 2;
  const centerY = (bbox[1] + bbox[3]) / 2;
  return (centerX - point.x) ** 2 + (centerY - point.y) ** 2;
}
