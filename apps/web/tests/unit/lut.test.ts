import { describe, expect, it } from "vitest";

import { HIGHLIGHT_ALPHA, LUT_CAPACITY, LUT_COLORS, LUT_SIZE, STATUS_ALPHA, buildLut, lutTexel } from "@/lib/lut";

describe("128x128 face LUT (§6.1)", () => {
  it("holds 16,384 faces (>= MAX_FACES_HARD 15,000) in one texture", () => {
    expect(LUT_SIZE).toBe(128);
    expect(LUT_CAPACITY).toBe(16384);
  });

  it("maps face ids exactly like the fragment shader (fid & 127, fid >> 7)", () => {
    for (const fid of [0, 1, 127, 128, 129, 4095, 14999, 16383]) {
      const { x, y, index } = lutTexel(fid);
      expect(x).toBe(fid % 128);
      expect(y).toBe(Math.floor(fid / 128));
      expect(index).toBe(fid);
      // shader UV centre of the texel
      const u = (x + 0.5) / 128;
      const v = (y + 0.5) / 128;
      expect(Math.floor(u * 128) + Math.floor(v * 128) * 128).toBe(fid);
    }
  });

  it("encodes status colours and highlights in RGBA (A=0 original, A=255 highlight)", () => {
    const data = buildLut({ modified: [7, 9], removed: [3] }, { selected: [9], measure: [130] });
    const texel = (fid: number) => Array.from(data.slice(fid * 4, fid * 4 + 4));
    expect(texel(7)).toEqual([...LUT_COLORS.modified, STATUS_ALPHA]);
    expect(texel(3)).toEqual([...LUT_COLORS.removed, STATUS_ALPHA]);
    expect(texel(9)).toEqual([...LUT_COLORS.selected, HIGHLIGHT_ALPHA]);
    expect(texel(130)).toEqual([...LUT_COLORS.measure, HIGHLIGHT_ALPHA]);
    expect(texel(0)).toEqual([0, 0, 0, 0]);
  });
});
