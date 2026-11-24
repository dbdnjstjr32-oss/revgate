/** Tiny vector / row-major 4x4 kernel (mm, Z-up, right-handed) shared by measure.ts and datum.ts. */

export type V3 = [number, number, number];
export type Mat4 = number[]; // length 16, ROW-MAJOR (same convention as diff_manifest.alignment.transform_4x4)

export const IDENTITY: Mat4 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

export const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const scale = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
export const dot = (a: V3, b: V3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const norm = (a: V3): number => Math.hypot(a[0], a[1], a[2]);
export const dist = (a: V3, b: V3): number => norm(sub(a, b));

export function normalize(a: V3): V3 {
  const n = norm(a);
  return n > 1e-15 ? [a[0] / n, a[1] / n, a[2] / n] : [0, 0, 0];
}

export const toDeg = (rad: number): number => (rad * 180) / Math.PI;
export const clamp = (x: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, x));

export function applyPoint(T: Mat4, p: V3): V3 {
  return [
    T[0] * p[0] + T[1] * p[1] + T[2] * p[2] + T[3],
    T[4] * p[0] + T[5] * p[1] + T[6] * p[2] + T[7],
    T[8] * p[0] + T[9] * p[1] + T[10] * p[2] + T[11],
  ];
}

export function applyDir(T: Mat4, d: V3): V3 {
  return [T[0] * d[0] + T[1] * d[1] + T[2] * d[2], T[4] * d[0] + T[5] * d[1] + T[6] * d[2], T[8] * d[0] + T[9] * d[1] + T[10] * d[2]];
}

export function mul(A: Mat4, B: Mat4): Mat4 {
  const out = new Array<number>(16).fill(0);
  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 4; c++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += A[r * 4 + k] * B[k * 4 + c];
      out[r * 4 + c] = s;
    }
  }
  return out;
}

/** Inverse of a rigid (rotation + translation) row-major matrix. */
export function invertRigid(T: Mat4): Mat4 {
  const r = [T[0], T[1], T[2], T[4], T[5], T[6], T[8], T[9], T[10]];
  const t: V3 = [T[3], T[7], T[11]];
  const rt = [r[0], r[3], r[6], r[1], r[4], r[7], r[2], r[5], r[8]];
  const nt: V3 = [
    -(rt[0] * t[0] + rt[1] * t[1] + rt[2] * t[2]),
    -(rt[3] * t[0] + rt[4] * t[1] + rt[5] * t[2]),
    -(rt[6] * t[0] + rt[7] * t[1] + rt[8] * t[2]),
  ];
  return [rt[0], rt[1], rt[2], nt[0], rt[3], rt[4], rt[5], nt[1], rt[6], rt[7], rt[8], nt[2], 0, 0, 0, 1];
}

/** Row-major frame (local -> world) with columns X, Y = Z x X, Z and origin O. */
export function frame(origin: V3, z: V3, xHint: V3): Mat4 {
  const Z = normalize(z);
  const X = normalize(sub(xHint, scale(Z, dot(xHint, Z))));
  const Y = cross(Z, X);
  return [X[0], Y[0], Z[0], origin[0], X[1], Y[1], Z[1], origin[1], X[2], Y[2], Z[2], origin[2], 0, 0, 0, 1];
}

export function isRigid(T: Mat4, tol = 1e-6): boolean {
  if (T.length !== 16 || !T.every(Number.isFinite)) return false;
  const cols: V3[] = [0, 1, 2].map((c) => [T[c], T[4 + c], T[8 + c]] as V3);
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) if (Math.abs(dot(cols[i], cols[j]) - (i === j ? 1 : 0)) > tol) return false;
  const det = dot(cols[0], cross(cols[1], cols[2]));
  return Math.abs(det - 1) <= tol && Math.abs(T[12]) <= tol && Math.abs(T[13]) <= tol && Math.abs(T[14]) <= tol && Math.abs(T[15] - 1) <= tol;
}
