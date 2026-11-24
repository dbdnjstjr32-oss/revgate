/**
 * 128 x 128 face look-up table (IMPLEMENTATION_BLUEPRINT §6.1): one RGBA texel per face id.
 *   texel (x, y) = (fid & 127, fid >> 7)  -> row-major index y * 128 + x === fid   (max 16,384 faces)
 *   RGB = status / heat colour, A = 0 original | 1..254 blend | 255 full highlight
 */

export const LUT_SIZE = 128;
export const LUT_CAPACITY = LUT_SIZE * LUT_SIZE; // 16,384 faces in one draw call

export type RGB = [number, number, number];

export const LUT_COLORS = {
  modified: [255, 153, 0] as RGB,
  removed: [230, 57, 70] as RGB,
  added: [46, 196, 99] as RGB,
  selected: [255, 214, 10] as RGB,
  measure: [214, 51, 255] as RGB,
  datum: [56, 189, 248] as RGB,
} as const;

export const STATUS_ALPHA = 170;
export const HIGHLIGHT_ALPHA = 255;

export function lutTexel(faceId: number): { x: number; y: number; index: number } {
  const x = faceId & 127;
  const y = faceId >> 7;
  return { x, y, index: y * LUT_SIZE + x };
}

export function makeLutData(): Uint8Array {
  return new Uint8Array(LUT_CAPACITY * 4);
}

export function paint(data: Uint8Array, faceIds: Iterable<number>, rgb: RGB, alpha: number): void {
  for (const fid of faceIds) {
    if (fid < 0 || fid >= LUT_CAPACITY) continue;
    const o = lutTexel(fid).index * 4;
    data[o] = rgb[0];
    data[o + 1] = rgb[1];
    data[o + 2] = rgb[2];
    data[o + 3] = alpha;
  }
}

export interface SideStatus {
  modified: number[];
  removed?: number[];
  added?: number[];
}

/** Status colours for one revision + selection / measurement / datum highlights on top. */
export function buildLut(status: SideStatus | null, highlights: { selected?: number[]; measure?: number[]; datum?: number[] } = {}): Uint8Array {
  const data = makeLutData();
  if (status) {
    paint(data, status.modified, LUT_COLORS.modified, STATUS_ALPHA);
    if (status.removed) paint(data, status.removed, LUT_COLORS.removed, STATUS_ALPHA);
    if (status.added) paint(data, status.added, LUT_COLORS.added, STATUS_ALPHA);
  }
  if (highlights.datum) paint(data, highlights.datum, LUT_COLORS.datum, HIGHLIGHT_ALPHA);
  if (highlights.selected) paint(data, highlights.selected, LUT_COLORS.selected, HIGHLIGHT_ALPHA);
  if (highlights.measure) paint(data, highlights.measure, LUT_COLORS.measure, HIGHLIGHT_ALPHA);
  return data;
}
