import type { Face } from "@revgate/contracts";

import type { V3 } from "@/lib/vec";

let nextId = 0;

const base = (surface_type: Face["surface_type"], params: Face["params"], centroid: V3 = [0, 0, 0]): Face => ({
  face_id: nextId++,
  surface_type,
  area_mm2: 100,
  centroid,
  bbox_min: centroid,
  bbox_max: centroid,
  lod0_tri_range: [0, 1],
  adjacent_face_ids: [],
  params,
});

export const plane = (origin: V3, normal: V3): Face => base("PLANE", { type: "PLANE", origin, normal }, origin);

export const cylinder = (axis_origin: V3, axis_dir: V3, radius_mm: number, is_internal = true): Face =>
  base("CYLINDER", { type: "CYLINDER", axis_origin, axis_dir, radius_mm, height_mm: 20, sweep_deg: 360, is_internal }, axis_origin);

export const bspline = (centroid: V3): Face => base("BSPLINE", { type: "OTHER", sample_normal: [0, 0, 1] }, centroid);

export const deg = (d: number) => (d * Math.PI) / 180;
