/**
 * 3-2-1 Datum Align (PRODUCT_SPEC §3.2): primary plane -> secondary plane/hole -> tertiary plane/hole,
 * exactly like a fixture setup. Both revisions get the same datum frame from their analytical_meta;
 * transform_4x4 = F_base · F_target⁻¹ (row-major, Target -> Base) is previewed in the browser and only
 * these 16 numbers are sent to POST /v1/diffs/{id}/realign.
 */

import type { Face } from "@revgate/contracts";

import { type Mat4, type V3, add, cross, dot, frame, invertRigid, mul, norm, normalize, scale, sub } from "./vec";

export type DatumTriple = [Face, Face, Face];

const isPlane = (f: Face) => f.params.type === "PLANE";
const isAxis = (f: Face) => f.params.type === "CYLINDER";

function planeOf(f: Face): { o: V3; n: V3 } {
  if (f.params.type !== "PLANE") throw new Error("not a plane");
  return { o: f.params.origin, n: normalize(f.params.normal) };
}

function axisOf(f: Face): { p: V3; d: V3 } {
  if (f.params.type !== "CYLINDER") throw new Error("not a cylinder");
  return { p: f.params.axis_origin, d: normalize(f.params.axis_dir) };
}

function axisPlane(axis: { p: V3; d: V3 }, plane: { o: V3; n: V3 }): V3 {
  const den = dot(axis.d, plane.n);
  if (Math.abs(den) < 1e-9) throw new Error("hole axis is parallel to the primary plane");
  return add(axis.p, scale(axis.d, dot(sub(plane.o, axis.p), plane.n) / den));
}

function threePlanes(a: { o: V3; n: V3 }, b: { o: V3; n: V3 }, c: { o: V3; n: V3 }): V3 {
  const det = dot(a.n, cross(b.n, c.n));
  if (Math.abs(det) < 1e-9) throw new Error("datum planes do not intersect in a point");
  const da = dot(a.n, a.o);
  const db = dot(b.n, b.o);
  const dc = dot(c.n, c.o);
  return scale(add(add(scale(cross(b.n, c.n), da), scale(cross(c.n, a.n), db)), scale(cross(a.n, b.n), dc)), 1 / det);
}

/** Why a triple cannot be used, or null when it is a valid 3-2-1 datum. */
export function validateDatum(t: Face[]): string | null {
  if (t.length !== 3) return "1차 · 2차 · 3차 기준을 모두 선택하세요";
  if (!isPlane(t[0])) return "1차 기준은 평면이어야 합니다";
  for (const f of t.slice(1)) if (!isPlane(f) && !isAxis(f)) return "2·3차 기준은 평면 또는 원통(홀)이어야 합니다";
  try {
    datumFrame(t as DatumTriple);
  } catch (e) {
    return (e as Error).message;
  }
  return null;
}

/** Row-major local -> world frame of a 3-2-1 datum. */
export function datumFrame([primary, secondary, tertiary]: DatumTriple): Mat4 {
  const A = planeOf(primary);
  const Z = A.n;
  let origin: V3;
  let xHint: V3;
  if (isPlane(secondary)) {
    const B = planeOf(secondary);
    xHint = sub(B.n, scale(Z, dot(B.n, Z)));
    if (norm(xHint) < 1e-6) throw new Error("2차 기준 평면이 1차 기준과 평행합니다");
    if (isPlane(tertiary)) {
      origin = threePlanes(A, B, planeOf(tertiary));
    } else {
      // tertiary hole: origin = point of the A∩B edge closest to the hole centre on A
      const c3 = axisPlane(axisOf(tertiary), A);
      const lineDir = normalize(cross(A.n, B.n));
      const onLine = threePlanes(A, B, { o: c3, n: lineDir });
      origin = onLine;
    }
  } else {
    origin = axisPlane(axisOf(secondary), A); // hole centre on the primary plane
    if (isPlane(tertiary)) {
      const C = planeOf(tertiary);
      xHint = sub(C.n, scale(Z, dot(C.n, Z)));
      if (norm(xHint) < 1e-6) throw new Error("3차 기준 평면이 1차 기준과 평행합니다");
    } else {
      const c3 = axisPlane(axisOf(tertiary), A);
      xHint = sub(c3, origin);
      xHint = sub(xHint, scale(Z, dot(xHint, Z)));
      if (norm(xHint) < 1e-6) throw new Error("2·3차 홀이 같은 위치에 있습니다");
    }
  }
  return frame(origin, Z, xHint);
}

/** Target -> Base transform aligning the target datum onto the base datum. */
export function solveDatumTransform(base: DatumTriple, target: DatumTriple): Mat4 {
  return mul(datumFrame(base), invertRigid(datumFrame(target)));
}
