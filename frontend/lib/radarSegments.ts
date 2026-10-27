/** Break the circular outline at missing metrics. Never infer a zero radius. */
export function radarSegments(values: Array<number | null>): number[][] {
  if (!values.length) return [];
  const missing = values.findIndex((value) => value === null || !Number.isFinite(value));
  if (missing < 0) return [values.map((_, index) => index).concat(0)];
  const segments: number[][] = [];
  let current: number[] = [];
  for (let offset = 1; offset <= values.length; offset += 1) {
    const index = (missing + offset) % values.length;
    const value = values[index];
    if (value === null || !Number.isFinite(value)) {
      if (current.length) segments.push(current);
      current = [];
    } else {
      current.push(index);
    }
  }
  if (current.length) segments.push(current);
  return segments;
}
