import { describe, expect, it } from "vitest";

import { BADGE_COAXIAL, BADGE_MESH_APPROX, BADGE_ORTHOGONAL, PAR_COS, PERP_SIN, measureFaces } from "@/lib/measure";
import { applyDir, applyPoint, type Mat4 } from "@/lib/vec";

import { bspline, cylinder, deg, plane } from "./fixtures";

const value = (m: ReturnType<typeof measureFaces>, key: string) => m.values.find((v) => v.key === key)!.value;

describe("tolerances (§6.2)", () => {
  it("uses cos/sin of 0.5 degree", () => {
    expect(PAR_COS).toBeCloseTo(Math.cos(deg(0.5)), 15);
    expect(PERP_SIN).toBeCloseTo(Math.sin(deg(0.5)), 15);
  });
});

describe("CYLINDER <-> CYLINDER", () => {
  it("parallel holes: axis-to-axis centre distance (golden 01 holes #7 / #9)", () => {
    const m = measureFaces({ face: cylinder([88, 12, 10], [0, 0, 1], 1.65) }, { face: cylinder([80, 50, 10], [0, 0, 1], 2.1) });
    expect(m.kind).toBe("CYL_CYL");
    expect(value(m, "center_distance")).toBeCloseTo(Math.hypot(8, 38), 9); // 38.833 mm
    expect(value(m, "diameter_a")).toBeCloseTo(3.3, 12);
    expect(value(m, "diameter_b")).toBeCloseTo(4.2, 12);
    expect(m.badges).toEqual([]);
    expect(m.summary).toBe("중심거리 38.833 mm");
  });

  it("axis distance ignores the offset along the axis and flags coaxial below 0.01 mm", () => {
    const m = measureFaces({ face: cylinder([5, 5, 0], [0, 0, 1], 3) }, { face: cylinder([5.004, 5.003, 42], [0, 0, -1], 6) });
    expect(value(m, "center_distance")).toBeCloseTo(0.005, 12);
    expect(m.badges).toEqual([BADGE_COAXIAL]);
    const notCoaxial = measureFaces({ face: cylinder([0, 0, 0], [0, 0, 1], 3) }, { face: cylinder([0.011, 0, 0], [0, 0, 1], 3) });
    expect(notCoaxial.badges).toEqual([]);
  });

  it("0.4 deg is still parallel, 0.6 deg is not", () => {
    const tilt = (d: number) => [Math.sin(deg(d)), 0, Math.cos(deg(d))] as [number, number, number];
    expect(value(measureFaces({ face: cylinder([0, 0, 0], [0, 0, 1], 1) }, { face: cylinder([10, 0, 0], tilt(0.4), 1) }), "center_distance")).toBeCloseTo(10, 9);
    const m = measureFaces({ face: cylinder([0, 0, 0], [0, 0, 1], 1) }, { face: cylinder([10, 0, 0], tilt(0.6), 1) });
    expect(value(m, "crossing_angle")).toBeCloseTo(0.6, 9);
  });

  it("skew axes: crossing angle + shortest distance", () => {
    const m = measureFaces({ face: cylinder([0, 0, 0], [1, 0, 0], 2) }, { face: cylinder([3, 7, 5], [0, 1, 0], 2) });
    expect(value(m, "crossing_angle")).toBeCloseTo(90, 9);
    expect(value(m, "skew_distance")).toBeCloseTo(5, 12);
  });
});

describe("PLANE <-> PLANE", () => {
  it("parallel faces (outward normals opposite): perpendicular distance", () => {
    const m = measureFaces({ face: plane([1, 2, 20], [0, 0, 1]) }, { face: plane([50, -3, 0], [0, 0, -1]) });
    expect(m.kind).toBe("PLANE_PLANE");
    expect(value(m, "parallel_distance")).toBeCloseTo(20, 12);
  });

  it("non-parallel: dihedral + supplement", () => {
    const m = measureFaces({ face: plane([0, 0, 0], [0, 0, 1]) }, { face: plane([0, 0, 0], [0, Math.sin(deg(30)), Math.cos(deg(30))]) });
    expect(value(m, "dihedral_angle")).toBeCloseTo(30, 9);
    expect(value(m, "supplementary_angle")).toBeCloseTo(150, 9);
  });
});

describe("CYLINDER <-> PLANE", () => {
  it("axis parallel to the plane: axis-plane distance and minimum wall gap", () => {
    const m = measureFaces({ face: cylinder([10, 3.65, 5], [1, 0, 0], 1.65) }, { face: plane([0, 0, 0], [0, -1, 0]) });
    expect(m.kind).toBe("CYL_PLANE");
    expect(value(m, "axis_plane_distance")).toBeCloseTo(3.65, 12);
    expect(value(m, "min_wall_gap")).toBeCloseTo(2.0, 12);
  });

  it("axis perpendicular to the plane: 직교(90.0°) + centre distance", () => {
    const m = measureFaces({ face: plane([0, 0, 20], [0, 0, 1]) }, { face: cylinder([12, 10, 10], [0, 0, 1], 1.65) });
    expect(m.badges).toEqual([BADGE_ORTHOGONAL]);
    expect(value(m, "center_distance")).toBeCloseTo(10, 12);
  });
});

describe("free-form faces", () => {
  it("fall back to point distance with the mesh-approximation badge", () => {
    const m = measureFaces({ face: bspline([0, 0, 0]), point: [1, 2, 2] }, { face: plane([0, 0, 0], [0, 0, 1]), point: [4, 6, 2] });
    expect(m.kind).toBe("POINT_POINT");
    expect(m.approximate).toBe(true);
    expect(m.badges).toEqual([BADGE_MESH_APPROX]);
    expect(value(m, "point_distance")).toBeCloseTo(5, 12);
  });
});

describe("cross-revision measurement", () => {
  it("applies Target -> Base transform_4x4 to target params", () => {
    // target is the base rotated 90° about Z and shifted; T maps it back
    const T: Mat4 = [0, 1, 0, -45, -1, 0, 0, 120, 0, 0, 1, 0, 0, 0, 0, 1];
    const baseHole = cylinder([12, 10, 10], [0, 0, 1], 1.65);
    const p = applyPoint([0, -1, 0, 120, 1, 0, 0, 45, 0, 0, 1, 0, 0, 0, 0, 1], [12, 10, 10]); // inverse of T
    const targetHole = cylinder(p, applyDir([0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], [0, 0, 1]), 2.1);
    const m = measureFaces({ face: baseHole }, { face: targetHole, transform: T });
    expect(value(m, "center_distance")).toBeLessThan(1e-9);
    expect(m.badges).toEqual([BADGE_COAXIAL]);
  });
});
