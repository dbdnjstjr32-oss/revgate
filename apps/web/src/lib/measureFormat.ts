/** Phase 4 §5.1 — measurement reliability: analytical (CAD nominal) vs mesh approximation. */

import type { Measurement, MeasureValue } from "./measure";

export const CAD_NOMINAL = "CAD 이론치";
export const MESH_APPROX = "메쉬 근사(±0.05mm)";

/** `Ø4.200 mm · CAD 이론치` (analytical) / `~4.20 mm · 메쉬 근사(±0.05mm)` (mesh). */
export function formatMeasureValue(v: MeasureValue, approximate: boolean): string {
  const diameter = v.key.startsWith("diameter");
  if (v.unit === "deg") return approximate ? `~${v.value.toFixed(1)}°` : `${v.value.toFixed(2)}°`;
  const prefix = diameter ? "Ø" : "";
  return approximate ? `~${prefix}${v.value.toFixed(2)} mm` : `${prefix}${v.value.toFixed(3)} mm`;
}

export function reliability(m: Measurement): { label: string; approximate: boolean } {
  return m.approximate ? { label: MESH_APPROX, approximate: true } : { label: CAD_NOMINAL, approximate: false };
}
