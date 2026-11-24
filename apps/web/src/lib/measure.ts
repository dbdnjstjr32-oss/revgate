/**
 * Analytical 0.001 mm measurement on analytical_meta.json params (IMPLEMENTATION_BLUEPRINT §6.2).
 *
 *  CYLINDER <-> CYLINDER  parallel (|d1·d2| >= PAR_COS): axis distance |(p2-p1) - ((p2-p1)·d1) d1|,
 *                          < 0.01 mm => "동축(Coaxial)"; otherwise crossing angle + shortest skew distance
 *  PLANE    <-> PLANE     parallel: |(o2-o1)·n1|; otherwise dihedral angle θ + supplement (180 - θ)
 *  CYLINDER <-> PLANE     axis ∥ plane (|d·n| <= PERP_SIN): axis-plane distance + min wall gap (distance - r);
 *                          axis ⟂ plane: "직교(90.0°)" + centre distance
 *  BSPLINE / OTHER        Euclidean distance of the picked points + "메쉬 근사치 (±0.05mm)"
 */

import type { Face } from "@revgate/contracts";

import { type Mat4, type V3, applyDir, applyPoint, clamp, cross, dist, dot, norm, normalize, scale, sub, toDeg } from "./vec";

export const PAR_COS = Math.cos((0.5 * Math.PI) / 180);
export const PERP_SIN = Math.sin((0.5 * Math.PI) / 180);
export const COAXIAL_MM = 0.01;
export const BADGE_COAXIAL = "동축(Coaxial)";
export const BADGE_ORTHOGONAL = "직교(90.0°)";
export const BADGE_MESH_APPROX = "메쉬 근사치 (±0.05mm)";

export type MeasureKind = "CYL_CYL" | "PLANE_PLANE" | "CYL_PLANE" | "POINT_POINT";

export interface MeasureValue {
  key: string;
  label: string;
  value: number;
  unit: "mm" | "deg";
}

export interface Measurement {
  kind: MeasureKind;
  values: MeasureValue[];
  badges: string[];
  approximate: boolean;
  summary: string;
}

export interface MeasureInput {
  face: Face;
  /** world-space pick point (needed for BSPLINE / OTHER faces) */
  point?: V3 | null;
  /** maps this face's revision frame into the measuring frame (Target -> Base for target faces) */
  transform?: Mat4 | null;
}

interface Axis {
  p: V3;
  d: V3;
  r: number;
  diameter: number;
}

interface Plane {
  o: V3;
  n: V3;
}

const fmt = (v: number, unit: "mm" | "deg") => `${v.toFixed(3)}${unit === "mm" ? " mm" : "°"}`;

function tp(T: Mat4 | null | undefined, p: V3): V3 {
  return T ? applyPoint(T, p) : p;
}

function td(T: Mat4 | null | undefined, d: V3): V3 {
  return normalize(T ? applyDir(T, d) : d);
}

export function axisOf(input: MeasureInput): Axis | null {
  const prm = input.face.params;
  if (prm.type === "CYLINDER") {
    return { p: tp(input.transform, prm.axis_origin), d: td(input.transform, prm.axis_dir), r: prm.radius_mm, diameter: 2 * prm.radius_mm };
  }
  if (prm.type === "CONE") {
    return { p: tp(input.transform, prm.axis_origin), d: td(input.transform, prm.axis_dir), r: prm.r_max, diameter: 2 * prm.r_max };
  }
  return null;
}

export function planeOf(input: MeasureInput): Plane | null {
  const prm = input.face.params;
  return prm.type === "PLANE" ? { o: tp(input.transform, prm.origin), n: td(input.transform, prm.normal) } : null;
}

function pickPoint(input: MeasureInput): V3 {
  return input.point ?? tp(input.transform, input.face.centroid);
}

export function measureCylinders(a: Axis, b: Axis): Measurement {
  const cosang = Math.abs(dot(a.d, b.d));
  const w = sub(b.p, a.p);
  const diam = [
    { key: "diameter_a", label: "Ø A", value: a.diameter, unit: "mm" as const },
    { key: "diameter_b", label: "Ø B", value: b.diameter, unit: "mm" as const },
  ];
  if (cosang >= PAR_COS) {
    const axisDist = norm(sub(w, scale(a.d, dot(w, a.d))));
    const coaxial = axisDist < COAXIAL_MM;
    return {
      kind: "CYL_CYL", approximate: false, badges: coaxial ? [BADGE_COAXIAL] : [],
      values: [{ key: "center_distance", label: "중심거리", value: axisDist, unit: "mm" }, ...diam],
      summary: coaxial ? `${BADGE_COAXIAL} · 중심거리 ${fmt(axisDist, "mm")}` : `중심거리 ${fmt(axisDist, "mm")}`,
    };
  }
  const angle = toDeg(Math.acos(clamp(cosang, -1, 1)));
  const c = cross(a.d, b.d);
  const skew = Math.abs(dot(w, c)) / norm(c);
  return {
    kind: "CYL_CYL", approximate: false, badges: [],
    values: [
      { key: "crossing_angle", label: "교차각", value: angle, unit: "deg" },
      { key: "skew_distance", label: "최단 꼬임거리", value: skew, unit: "mm" },
      ...diam,
    ],
    summary: `교차각 ${fmt(angle, "deg")} · 최단거리 ${fmt(skew, "mm")}`,
  };
}

export function measurePlanes(a: Plane, b: Plane): Measurement {
  const c = dot(a.n, b.n);
  if (Math.abs(c) >= PAR_COS) {
    const d = Math.abs(dot(sub(b.o, a.o), a.n));
    return {
      kind: "PLANE_PLANE", approximate: false, badges: [],
      values: [{ key: "parallel_distance", label: "평행거리", value: d, unit: "mm" }],
      summary: `평행거리 ${fmt(d, "mm")}`,
    };
  }
  const theta = toDeg(Math.acos(clamp(c, -1, 1)));
  return {
    kind: "PLANE_PLANE", approximate: false, badges: [],
    values: [
      { key: "dihedral_angle", label: "이면각", value: theta, unit: "deg" },
      { key: "supplementary_angle", label: "보각", value: 180 - theta, unit: "deg" },
    ],
    summary: `이면각 ${fmt(theta, "deg")} (보각 ${fmt(180 - theta, "deg")})`,
  };
}

export function measureCylinderPlane(axis: Axis, plane: Plane): Measurement {
  const s = Math.abs(dot(axis.d, plane.n));
  const centerDist = Math.abs(dot(sub(axis.p, plane.o), plane.n));
  if (s <= PERP_SIN) {
    const wall = centerDist - axis.r;
    return {
      kind: "CYL_PLANE", approximate: false, badges: [],
      values: [
        { key: "axis_plane_distance", label: "축-면 거리", value: centerDist, unit: "mm" },
        { key: "min_wall_gap", label: "최소 벽간격", value: wall, unit: "mm" },
      ],
      summary: `축-면 거리 ${fmt(centerDist, "mm")} · 최소 벽간격 ${fmt(wall, "mm")}`,
    };
  }
  if (s >= PAR_COS) {
    return {
      kind: "CYL_PLANE", approximate: false, badges: [BADGE_ORTHOGONAL],
      values: [
        { key: "axis_plane_angle", label: "축-면 각도", value: 90, unit: "deg" },
        { key: "center_distance", label: "중심점 거리", value: centerDist, unit: "mm" },
      ],
      summary: `${BADGE_ORTHOGONAL} · 중심점 거리 ${fmt(centerDist, "mm")}`,
    };
  }
  const angle = 90 - toDeg(Math.acos(clamp(s, -1, 1)));
  return {
    kind: "CYL_PLANE", approximate: false, badges: [],
    values: [
      { key: "axis_plane_angle", label: "축-면 각도", value: angle, unit: "deg" },
      { key: "center_distance", label: "중심점 거리", value: centerDist, unit: "mm" },
    ],
    summary: `축-면 각도 ${fmt(angle, "deg")} · 중심점 거리 ${fmt(centerDist, "mm")}`,
  };
}

export function measureFaces(a: MeasureInput, b: MeasureInput): Measurement {
  const ax = [axisOf(a), axisOf(b)];
  const pl = [planeOf(a), planeOf(b)];
  const analytic = (i: 0 | 1) => ax[i] !== null || pl[i] !== null;
  if (!analytic(0) || !analytic(1)) {
    const d = dist(pickPoint(a), pickPoint(b));
    return {
      kind: "POINT_POINT", approximate: true, badges: [BADGE_MESH_APPROX],
      values: [{ key: "point_distance", label: "거리", value: d, unit: "mm" }],
      summary: `거리 ${fmt(d, "mm")} · ${BADGE_MESH_APPROX}`,
    };
  }
  if (ax[0] && ax[1]) return measureCylinders(ax[0], ax[1]);
  if (pl[0] && pl[1]) return measurePlanes(pl[0], pl[1]);
  const axis = (ax[0] ?? ax[1]) as Axis;
  const plane = (pl[0] ?? pl[1]) as Plane;
  return measureCylinderPlane(axis, plane);
}

export function describeFace(face: Face): string {
  const p = face.params;
  const c = face.centroid.map((v) => v.toFixed(1)).join(", ");
  if (p.type === "CYLINDER") return `${p.is_internal ? "홀" : "보스"} #${face.face_id} Ø${(2 * p.radius_mm).toFixed(2)} @(${c})`;
  if (p.type === "CONE") return `원뿔 #${face.face_id} @(${c})`;
  if (p.type === "PLANE") return `평면 #${face.face_id} n=(${p.normal.map((v) => v.toFixed(2)).join(", ")}) ${face.area_mm2.toFixed(0)}mm²`;
  return `${face.surface_type} #${face.face_id} @(${c})`;
}
