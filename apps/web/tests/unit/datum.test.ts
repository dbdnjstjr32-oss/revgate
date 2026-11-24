import type { Face } from "@revgate/contracts";
import { describe, expect, it } from "vitest";

import { type DatumTriple, solveDatumTransform, validateDatum } from "@/lib/datum";
import { applyDir, applyPoint, invertRigid, isRigid, type Mat4, mul } from "@/lib/vec";

import { cylinder, plane } from "./fixtures";

/** row-major R = Rz(c) Ry(b) Rx(a) + t */
function rigid(a: number, b: number, c: number, t: [number, number, number]): Mat4 {
  const [ca, sa, cb, sb, cc, sc] = [Math.cos(a), Math.sin(a), Math.cos(b), Math.sin(b), Math.cos(c), Math.sin(c)];
  const R = [
    cc * cb, cc * sb * sa - sc * ca, cc * sb * ca + sc * sa,
    sc * cb, sc * sb * sa + cc * ca, sc * sb * ca - cc * sa,
    -sb, cb * sa, cb * ca,
  ];
  return [R[0], R[1], R[2], t[0], R[3], R[4], R[5], t[1], R[6], R[7], R[8], t[2], 0, 0, 0, 1];
}

function moved(f: Face, G: Mat4): Face {
  const p = f.params;
  if (p.type === "PLANE") return plane(applyPoint(G, p.origin), applyDir(G, p.normal));
  if (p.type === "CYLINDER") return cylinder(applyPoint(G, p.axis_origin), applyDir(G, p.axis_dir), p.radius_mm);
  throw new Error("unsupported");
}

const close = (A: Mat4, B: Mat4, tol = 1e-9) => A.forEach((v, i) => expect(v).toBeCloseTo(B[i], -Math.log10(tol)));

// block 100 x 60 x 20 (golden 01): bottom, front, left faces + two through-holes
const bottom = plane([50, 30, 0], [0, 0, -1]);
const front = plane([50, 0, 10], [0, -1, 0]);
const left = plane([0, 30, 10], [-1, 0, 0]);
const hole1 = cylinder([12, 10, 10], [0, 0, 1], 1.65);
const hole3 = cylinder([15, 48, 10], [0, 0, 1], 1.65);

describe("3-2-1 datum transform", () => {
  const G = rigid(1.5707963267948966, 0, 0.6457718232379019, [120, -45, 80]); // golden 03 motion
  const cases: [string, DatumTriple][] = [
    ["plane / plane / plane", [bottom, front, left]],
    ["plane / plane / hole", [bottom, front, hole1]],
    ["plane / hole / hole", [bottom, hole1, hole3]],
    ["plane / hole / plane", [bottom, hole1, left]],
  ];
  it.each(cases)("%s recovers the inverse of the part motion", (_name, baseTriple) => {
    const targetTriple = baseTriple.map((f) => moved(f, G)) as DatumTriple;
    const T = solveDatumTransform(baseTriple, targetTriple);
    expect(isRigid(T)).toBe(true);
    close(mul(T, G), [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
    close(T, invertRigid(G));
  });

  it("identity datum yields the identity transform", () => {
    close(solveDatumTransform([bottom, front, left], [bottom, front, left]), [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  });

  it("rejects invalid datum choices", () => {
    expect(validateDatum([hole1, bottom, front])).toBe("1차 기준은 평면이어야 합니다");
    expect(validateDatum([bottom, plane([0, 0, 20], [0, 0, 1]), front])).toBe("2차 기준 평면이 1차 기준과 평행합니다");
    expect(validateDatum([bottom, front])).toMatch(/모두 선택/);
    expect(validateDatum([bottom, front, left])).toBeNull();
  });
});
