"""3-stage engineering B-Rep diff (PRODUCT_SPEC §3.3, IMPLEMENTATION_BLUEPRINT §2.3 / §5).

Stage 1  Face-to-Face matching on the Attributed Adjacency Graph (AAG)
         * identical faces under the alignment transform are eliminated first (geometric
           fingerprint = type + surface params + area + centroid, KD-tree candidates); ambiguous
           candidates are resolved by AAG neighbour consistency
         * remaining faces are paired: coaxial / moved cylinder-hole features, parallel planes
Stage 2  Rule-based AFR facts — facts (100% geometry) are kept apart from estimates:
         HOLE_DIAMETER (+ ISO metric tap-drill hint), HOLE_POSITION, PLANAR_OFFSET (pocket depth),
         WALL_THICKNESS (opposing plane pairs: CRITICAL < 1.0 mm, MAJOR < 2.0 mm with >= 30% loss)
Stage 3  OCCT Boolean (Base-Target = removed, Target-Base = added) in a subprocess with a 15 s
         hard timeout; open shells / helical-thread bombs / timeouts fall back to a lod0 5,000-point
         area-weighted sampling + scipy cKDTree deviation heatmap (BOOLEAN_FALLBACK_HEATMAP).
         Volume regions not explained by stage-1/2 facts become VOLUME_DELTA "육안 확인 필요" items.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Trsf
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape
from scipy.spatial import cKDTree

from . import constants as C
from .align import (
    COS_TOL,
    CYLINDER,
    PLANE,
    SIN_TOL,
    AlignmentResult,
    Features,
    HoleFeature,
    Matcher,
    auto_align,
    manual_align,
)
from .geom import (
    apply_points,
    closest_point_on_triangles,
    is_rigid,
    normalize,
    plane_basis,
    point_line_distance,
    subdivide_triangles,
)
from .errors import TIMEOUT_ERROR_CODE
from .glb_builder import build_glb
from .ingest import Revision, bbox_of, face_index, load_revision, shape_volume
from .tessellate import MeshData, extract_mesh, mesh_shape, sample_surface

SEVERITY_ORDER = {"CRITICAL": 0, "MAJOR": 1, "INFO": 2}
CATEGORY_ORDER = {"HOLE_DIAMETER": 0, "HOLE_POSITION": 1, "PLANAR_OFFSET": 2, "WALL_THICKNESS": 3, "VOLUME_DELTA": 4}
COS30 = math.cos(math.radians(30.0))
SIN5 = math.sin(math.radians(5.0))


def _r(x: float, nd: int = 4) -> float:
    return round(float(x), nd) + 0.0


def _v(a, nd: int = 6) -> list[float]:
    return [round(float(a[0]), nd) + 0.0, round(float(a[1]), nd) + 0.0, round(float(a[2]), nd) + 0.0]


# ============================================================================================
# Stage 1 — face matching
# ============================================================================================
@dataclass
class HolePair:
    a: HoleFeature
    b: HoleFeature  # in base frame
    kind: str  # COAXIAL | MOVED
    axis_dist: float


@dataclass
class PlanePair:
    i: int
    j: int
    offset: float  # signed along base outward normal (+ = material added)


@dataclass
class Matching:
    unchanged: list[tuple[int, int]]
    hole_pairs: list[HolePair]
    plane_pairs: list[PlanePair]
    other_pairs: list[tuple[int, int]]
    base_modified: set[int] = field(default_factory=set)
    target_modified: set[int] = field(default_factory=set)
    removed: list[int] = field(default_factory=list)
    added: list[int] = field(default_factory=list)

    @property
    def base_to_target(self) -> dict[int, int]:
        m = {i: j for i, j in self.unchanged}
        m.update({p.i: p.j for p in self.plane_pairs})
        m.update(dict(self.other_pairs))
        return m


def _face_points(F: Features, k: int) -> np.ndarray:
    pts = F.mesh.face_vertices(k) if F.mesh is not None else np.zeros((0, 3))
    if len(pts) == 0:
        pts = np.array([F.centroid[k]])
    return pts


def _extent2d(F: Features, k: int, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    P = _face_points(F, k)
    pu, pv = P @ u, P @ v
    return np.array([pu.min(), pu.max(), pv.min(), pv.max()])


def _overlap(ea: np.ndarray, eb: np.ndarray) -> tuple[float, float]:
    return (min(ea[1], eb[1]) - max(ea[0], eb[0]), min(ea[3], eb[3]) - max(ea[2], eb[2]))


def match_unchanged(A: Features, Bt: Features) -> list[tuple[int, int]]:
    """Identical faces; ambiguous candidates resolved by AAG neighbour consistency."""
    ii, jj = Matcher(A).candidates(Bt)
    cand: dict[int, list[int]] = {}
    rev: dict[int, list[int]] = {}
    for i, j in zip(ii.tolist(), jj.tolist()):
        cand.setdefault(i, []).append(j)
        rev.setdefault(j, []).append(i)
    pairs: dict[int, int] = {}
    for i, js in cand.items():
        if len(js) == 1 and len(rev[js[0]]) == 1:
            pairs[i] = js[0]
    used = set(pairs.values())
    for i in sorted((i for i in cand if i not in pairs), key=lambda i: (-A.area[i], i)):
        best = None
        for j in cand[i]:
            if j in used:
                continue
            nb = set(Bt.adjacency[j])
            consistent = sum(1 for k in A.adjacency[i] if k in pairs and pairs[k] in nb)
            key = (-consistent, float(np.linalg.norm(A.centroid[i] - Bt.centroid[j])), j)
            if best is None or key < best[0]:
                best = (key, j)
        if best is not None:
            pairs[i] = best[1]
            used.add(best[1])
    return sorted(pairs.items())


def pair_holes(A: Features, Bt: Features, base_free: set[int], target_free: set[int]) -> list[HolePair]:
    ha_list = [h for h in A.holes if any(f in base_free for f in h.face_ids)]
    hb_list = [h for h in Bt.holes if any(f in target_free for f in h.face_ids)]
    cands = []
    for ia, ha in enumerate(ha_list):
        for ib, hb in enumerate(hb_list):
            if ha.internal != hb.internal or abs(np.dot(ha.axis, hb.axis)) < COS_TOL:
                continue
            dist = float(point_line_distance(hb.origin, ha.origin, ha.axis))
            c = float(np.dot(hb.origin - ha.origin, ha.axis))
            overlap = min(ha.s_max, c + hb.s_max) - max(ha.s_min, c + hb.s_min)
            if overlap <= C.FACE_MATCH_DIST_MM:
                continue
            if dist <= C.AXIS_COAXIAL_TOL_MM:
                cands.append((abs(ha.radius - hb.radius), ia, ib, "COAXIAL", dist))
            elif abs(ha.radius - hb.radius) <= C.FACE_MATCH_DIST_MM and dist <= max(10.0 * ha.radius, 10.0):
                cands.append((1.0 + dist, ia, ib, "MOVED", dist))
    cands.sort(key=lambda x: (x[0], x[1], x[2]))
    used_a, used_b, out = set(), set(), []
    for _cost, ia, ib, kind, dist in cands:
        if ia in used_a or ib in used_b:
            continue
        used_a.add(ia)
        used_b.add(ib)
        out.append(HolePair(ha_list[ia], hb_list[ib], kind, dist))
    return out


def pair_planes(A: Features, Bt: Features, base_free: list[int], target_free: list[int]) -> list[PlanePair]:
    out, used = [], set()
    for i in sorted(base_free, key=lambda i: (-A.area[i], i)):
        n = A.normal[i]
        u, v = plane_basis(n)
        ea = _extent2d(A, i, u, v)
        best = None
        for j in target_free:
            if j in used or np.dot(n, Bt.normal[j]) < COS_TOL:
                continue
            ou, ov = _overlap(ea, _extent2d(Bt, j, u, v))
            if ou <= C.FACE_MATCH_DIST_MM or ov <= C.FACE_MATCH_DIST_MM:
                continue
            off = float(np.dot(n, Bt.origin[j] - A.origin[i]))
            key = (abs(off), -ou * ov, j)
            if best is None or key < best[0]:
                best = (key, j, off)
        if best is not None:
            used.add(best[1])
            out.append(PlanePair(i, best[1], best[2]))
    return out


def match_faces(A: Features, Bt: Features) -> Matching:
    unchanged = match_unchanged(A, Bt)
    ub = {i for i, _ in unchanged}
    ut = {j for _, j in unchanged}
    base_free = set(range(A.n)) - ub
    target_free = set(range(Bt.n)) - ut

    hole_pairs = pair_holes(A, Bt, base_free, target_free)
    bm = {f for hp in hole_pairs for f in hp.a.face_ids if f in base_free}
    tm = {f for hp in hole_pairs for f in hp.b.face_ids if f in target_free}

    planes_a = sorted(i for i in base_free - bm if A.types[i] == PLANE)
    planes_b = sorted(j for j in target_free - tm if Bt.types[j] == PLANE)
    plane_pairs = pair_planes(A, Bt, planes_a, planes_b)
    bm |= {p.i for p in plane_pairs}
    tm |= {p.j for p in plane_pairs}

    # remaining same-type faces that stayed in place (fillets, cones, free-form) -> modified
    other_pairs = []
    rest_b = sorted(target_free - tm)
    used = set()
    for i in sorted(base_free - bm, key=lambda i: (-A.area[i], i)):
        best = None
        for j in rest_b:
            if j in used or Bt.types[j] != A.types[i]:
                continue
            d = float(np.linalg.norm(A.centroid[i] - Bt.centroid[j]))
            if d <= 0.1 * max(A.diag[i], 1e-6) and (best is None or d < best[0]):
                best = (d, j)
        if best is not None:
            used.add(best[1])
            other_pairs.append((i, best[1]))
    bm |= {i for i, _ in other_pairs}
    tm |= {j for _, j in other_pairs}
    return Matching(
        unchanged=unchanged, hole_pairs=hole_pairs, plane_pairs=plane_pairs, other_pairs=other_pairs,
        base_modified=bm, target_modified=tm, removed=sorted(base_free - bm), added=sorted(target_free - tm),
    )


# ============================================================================================
# Stage 2 — AFR facts + engineering hints
# ============================================================================================
def change_id(category: str, base_ids: list[int], target_ids: list[int], anchor: list[float]) -> str:
    key = f"{category}|{base_ids}|{target_ids}|{[round(a, 3) for a in anchor]}"
    return "chg_" + hashlib.sha1(key.encode()).hexdigest()[:12]


def make_change(category: str, severity: str, title: str, fact: dict[str, Any], base_ids, target_ids,
                anchor: np.ndarray, view_dir: np.ndarray, radius: float, hint: dict[str, str] | None = None) -> dict[str, Any]:
    b = sorted({int(x) for x in base_ids})
    t = sorted({int(x) for x in target_ids})
    anchor_l = _v(anchor)
    ch = {
        "change_id": change_id(category, b, t, anchor_l),
        "category": category,
        "severity": severity,
        "title": title,
        "fact": fact,
        "base_face_ids": b,
        "target_face_ids": t,
        "anchor_xyz": anchor_l,
        "camera_focus": {"target": anchor_l, "suggested_dir": _v(normalize(view_dir), 9), "radius_mm": _r(max(radius, 1.0), 3)},
    }
    if hint is not None:
        ch["engineering_hint"] = hint
    return ch


def tap_drill_match(d_mm: float) -> str | None:
    best = None
    for name, (drill, _pitch) in C.ISO_METRIC_TAP_DRILL_MM.items():
        err = abs(d_mm - drill)
        if err <= C.TAP_DRILL_MATCH_TOL_MM and (best is None or err < best[0]):
            best = (err, name)
    return best[1] if best else None


def tap_hint(da: float, db: float) -> dict[str, str] | None:
    ma, mb = tap_drill_match(da), tap_drill_match(db)
    if ma and mb and ma != mb:
        return {"text": f"참고 추정: ISO {ma} → {mb} 탭 기초홀 규격과 일치", "confidence": "HIGH"}
    if mb and not ma:
        return {"text": f"참고 추정: 변경 후 Ø{db:.2f}는 ISO {mb} 탭 기초홀 규격과 일치", "confidence": "LOW"}
    if ma and not mb:
        return {"text": f"참고 추정: 변경 전 Ø{da:.2f}는 ISO {ma} 탭 기초홀 규격 (탭 사양 변경 여부 확인)", "confidence": "LOW"}
    return None


def hole_anchor(F: Features, h: HoleFeature) -> tuple[np.ndarray, np.ndarray]:
    """Hole entry point on the part surface + outward viewing direction along the axis."""
    best = None
    for s, out in ((h.s_max, h.axis), (h.s_min, -h.axis)):
        e = h.origin + h.axis * s
        entry = 0
        for f in h.face_ids:
            for k in F.adjacency[f]:
                if F.types[k] == PLANE and np.dot(F.normal[k], out) > COS30 and abs(np.dot(F.origin[k] - e, F.normal[k])) < 1e-3:
                    entry = 1
        key = (entry, float(out[2]), float(out[1]), float(out[0]))
        if best is None or key > best[0]:
            best = (key, e, out)
    return best[1], best[2]


def hole_changes(A: Features, Bt: Features, pairs: list[HolePair]) -> list[dict[str, Any]]:
    out = []
    for hp in pairs:
        a, b = hp.a, hp.b
        da, db = 2.0 * a.radius, 2.0 * b.radius
        word = "홀" if a.internal else "보스/축"
        feature = "HOLE" if a.internal else "BOSS"
        anchor, view = hole_anchor(A, a)
        radius = max(6.0 * max(a.radius, b.radius), 5.0)
        if hp.kind == "COAXIAL":
            delta = db - da
            if abs(delta) < C.CHANGE_REPORT_MIN_MM or not (a.is_full and b.is_full):
                continue
            fact = {"feature": feature, "from_diameter_mm": _r(da), "to_diameter_mm": _r(db), "delta_mm": _r(delta),
                    "depth_mm": _r(a.s_max - a.s_min)}
            title = f"{word} 지름 변경: Ø{da:.2f} → Ø{db:.2f} ({delta:+.2f}mm)"
            hint = tap_hint(da, db) if a.internal else None
            out.append(make_change("HOLE_DIAMETER", C.SEVERITY_HOLE, title, fact, a.face_ids, b.face_ids, anchor, view, radius, hint))
        else:
            if hp.axis_dist < C.CHANGE_REPORT_MIN_MM or not (a.is_full and b.is_full):
                continue
            c = float(np.dot(b.origin - a.origin, a.axis))
            dvec = (b.origin - a.origin) - c * a.axis
            fact = {"feature": feature, "diameter_mm": _r(da), "offset_mm": _r(hp.axis_dist),
                    "dx_mm": _r(dvec[0]), "dy_mm": _r(dvec[1]), "dz_mm": _r(dvec[2])}
            title = f"{word} 위치 변경: Ø{da:.2f} {hp.axis_dist:.3f}mm 이동"
            out.append(make_change("HOLE_POSITION", C.SEVERITY_HOLE, title, fact, a.face_ids, b.face_ids, anchor, view, radius + hp.axis_dist))
    return out


def pocket_depth(F: Features, i: int) -> float | None:
    """Depth of a recessed planar floor: >=3 perpendicular walls that face the floor centre and rise above it."""
    n, c = F.normal[i], F.centroid[i]
    walls = [k for k in F.adjacency[i] if F.types[k] == PLANE and abs(np.dot(F.normal[k], n)) <= SIN5]
    if len(walls) < 3:
        return None
    rise = []
    for k in walls:
        if np.dot(F.normal[k], c - F.centroid[k]) <= C.FACE_MATCH_DIST_MM:
            return None  # wall faces away -> boss top / outer face, not a pocket floor
        if np.dot(n, F.centroid[k] - c) <= C.FACE_MATCH_DIST_MM:
            return None
        rise.append(float((_face_points(F, k) @ n).max() - np.dot(n, F.origin[i])))
    return max(rise)


def planar_changes(A: Features, Bt: Features, pairs: list[PlanePair]) -> list[dict[str, Any]]:
    out = []
    for p in pairs:
        if abs(p.offset) < C.CHANGE_REPORT_MIN_MM:
            continue
        n = A.normal[p.i]
        depth_a, depth_b = pocket_depth(A, p.i), pocket_depth(Bt, p.j)
        fact: dict[str, Any] = {
            "offset_mm": _r(p.offset),
            "direction": "MATERIAL_ADDED" if p.offset > 0 else "MATERIAL_REMOVED",
            "from_area_mm2": _r(A.area[p.i], 3),
            "to_area_mm2": _r(Bt.area[p.j], 3),
        }
        if depth_a is not None and depth_b is not None:
            fact.update({"feature": "POCKET_FLOOR", "from_depth_mm": _r(depth_a), "to_depth_mm": _r(depth_b)})
            title = f"포켓 깊이 변경: {depth_a:.3f} → {depth_b:.3f}mm ({depth_b - depth_a:+.3f}mm)"
        else:
            fact["feature"] = "FACE"
            title = f"평면 위치 변경: {p.offset:+.3f}mm ({'살 추가' if p.offset > 0 else '살 제거'} 방향)"
        radius = 0.5 * max(A.diag[p.i], Bt.diag[p.j])
        out.append(make_change("PLANAR_OFFSET", C.SEVERITY_PLANAR_OFFSET, title, fact, [p.i], [p.j], A.origin[p.i], n, radius))
    return out


def wall_pairs(F: Features) -> dict[tuple[int, int], tuple[float, np.ndarray]]:
    """Thinnest opposing (anti-parallel, overlapping, material-between) planar partner per face."""
    planes = [k for k in range(F.n) if F.types[k] == PLANE]
    if len(planes) < 2:
        return {}
    N = F.normal[planes]
    O = F.origin[planes]
    out: dict[tuple[int, int], tuple[float, np.ndarray]] = {}
    for a, i in enumerate(planes):
        n = N[a]
        anti = np.flatnonzero(N @ n <= -COS_TOL)
        if len(anti) == 0:
            continue
        t = (O[a] - O[anti]) @ n  # distance from partner plane to face i, measured along n_i
        best = None
        u, v = plane_basis(n)
        ea = _extent2d(F, i, u, v)
        for b_idx in np.argsort(t):
            if t[b_idx] <= C.FACE_MATCH_DIST_MM:
                continue
            j = planes[anti[b_idx]]
            eb = _extent2d(F, j, u, v)
            ou, ov = _overlap(ea, eb)
            if ou > C.FACE_MATCH_DIST_MM and ov > C.FACE_MATCH_DIST_MM:
                cu = 0.5 * (max(ea[0], eb[0]) + min(ea[1], eb[1]))
                cv = 0.5 * (max(ea[2], eb[2]) + min(ea[3], eb[3]))
                cn = float(np.dot(n, O[a])) - 0.5 * float(t[b_idx])
                best = (float(t[b_idx]), j, cu * u + cv * v + cn * n)
                break
        if best is not None:
            key = (min(i, best[1]), max(i, best[1]))
            if key not in out or best[0] < out[key][0]:
                out[key] = (best[0], best[2])
    return out


def wall_changes(A: Features, Bt: Features, matching: Matching) -> list[dict[str, Any]]:
    t2b = {j: i for i, j in matching.base_to_target.items()}
    out = []
    for (p, q), (tb, center) in sorted(wall_pairs(Bt).items()):
        if tb >= C.WALL_MAJOR_MM:
            continue
        ap, aq = t2b.get(p), t2b.get(q)
        ta = None
        if ap is not None and aq is not None and A.types[ap] == PLANE and A.types[aq] == PLANE and np.dot(A.normal[ap], A.normal[aq]) <= -COS_TOL:
            ta = abs(float(np.dot(A.normal[ap], A.origin[ap] - A.origin[aq])))
        decreased = ta is None or tb < ta - C.CHANGE_REPORT_MIN_MM
        if not decreased:
            continue
        severity = None
        if tb < C.WALL_CRITICAL_MM:
            severity = "CRITICAL"
        elif ta is not None and (ta - tb) / ta >= C.WALL_REDUCTION_RATIO:
            severity = "MAJOR"
        if severity is None:
            continue
        threshold = C.WALL_CRITICAL_MM if severity == "CRITICAL" else C.WALL_MAJOR_MM
        fact: dict[str, Any] = {"to_mm": _r(tb), "min_wall_mm": _r(tb), "threshold_mm": _r(threshold)}
        if ta is not None:
            fact.update({"from_mm": _r(ta), "delta_mm": _r(tb - ta), "reduction_ratio": _r((ta - tb) / ta)})
            title = f"벽 두께 감소: {ta:.3f} → {tb:.3f}mm (박육 주의)"
        else:
            title = f"신규 박육 벽: {tb:.3f}mm (박육 주의)"
        hint = {"text": "참고: 알루미늄 CNC 기준 1.0mm 미만 벽은 채터링·변형 위험 (소재·공구 조건 확인)" if severity == "CRITICAL"
                else "참고: 2.0mm 미만 + 30% 이상 감소 — 가공 변형/공차 여유 확인", "confidence": "LOW"}
        n = Bt.normal[p]
        up = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        base_ids = [x for x in (ap, aq) if x is not None]
        out.append(make_change("WALL_THICKNESS", severity, title, fact, base_ids, [p, q], center, n + 0.8 * up,
                               max(12.0, 4.0 * tb), hint))
    return out


# ============================================================================================
# Stage 3 — Boolean / heatmap
# ============================================================================================
@dataclass
class Region:
    kind: str  # ADDED | REMOVED
    shape: TopoDS_Shape
    volume: float
    area: float
    centroid: np.ndarray
    bmin: np.ndarray
    bmax: np.ndarray


def _trsf(T: np.ndarray) -> gp_Trsf:
    trsf = gp_Trsf()
    trsf.SetValues(*(float(T[r, c]) for r in range(3) for c in range(4)))
    return trsf


def regions_of(shape: TopoDS_Shape, kind: str) -> list[Region]:
    out = []
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        solid = exp.Current()
        vp, sp = GProp_GProps(), GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, vp)
        BRepGProp.SurfaceProperties_s(solid, sp)
        vol, area = abs(float(vp.Mass())), float(sp.Mass())
        if vol > 1e-9 and area > 0 and 2.0 * vol / area >= C.DELTA_REGION_MIN_THICKNESS_MM:
            c = vp.CentreOfMass()
            bmin, bmax = bbox_of(solid)
            out.append(Region(kind, solid, vol, area, np.array([c.X(), c.Y(), c.Z()]), bmin, bmax))
        exp.Next()
    out.sort(key=lambda r: (-r.volume, tuple(np.round(r.centroid, 3))))
    return out


def run_boolean(base: Revision, target: Revision, T: np.ndarray, work_dir: Path, timeout: float) -> tuple[list[Region], str | None]:
    from .runner import run_subprocess

    bdir = work_dir / "boolean"
    bdir.mkdir(parents=True, exist_ok=True)
    BRepTools.Write_s(base.shape, str(bdir / "base.brep"))
    moved = BRepBuilderAPI_Transform(target.shape, _trsf(T), True).Shape()
    BRepTools.Write_s(moved, str(bdir / "target.brep"))
    res = run_subprocess(
        [sys.executable, "-m", "src.boolean_job", "--base", str(bdir / "base.brep"), "--target", str(bdir / "target.brep"),
         "--out", str(bdir), "--fuzzy", repr(C.BOOLEAN_FUZZY_MM)],
        result_path=bdir / "boolean_result.json", timeout=timeout,
    )
    if not res.ok:
        if res.error_code == TIMEOUT_ERROR_CODE:
            return [], f"BOOLEAN_TIMEOUT_{timeout:g}S"
        return [], f"BOOLEAN_{res.error_code or 'FAILED'}"
    regions = []
    totals = {"REMOVED": 0.0, "ADDED": 0.0}
    for kind, name in (("REMOVED", "removed.brep"), ("ADDED", "added.brep")):
        shape = TopoDS_Shape()
        BRepTools.Read_s(shape, str(bdir / name), BRep_Builder())
        totals[kind] = shape_volume(shape)
        regions += regions_of(shape, kind)
    # Sanity gate: Vol(B) - Vol(A) must equal added - removed, otherwise the Boolean is not trusted.
    expected = target.volume_mm3 - base.volume_mm3
    if abs((totals["ADDED"] - totals["REMOVED"]) - expected) > max(1.0, 1e-4 * max(base.volume_mm3, target.volume_mm3)):
        return [], "BOOLEAN_VOLUME_MISMATCH"
    return regions, None


def _face_box(F: Features, k: int) -> tuple[np.ndarray, np.ndarray]:
    P = _face_points(F, k)
    return P.min(axis=0), P.max(axis=0)


def _boxes_touch(amin, amax, bmin, bmax, pad: float) -> bool:
    return bool(np.all(amin - pad <= bmax) and np.all(bmin - pad <= amax))


def attach_regions(regions: list[Region], changes: list[dict[str, Any]], A: Features, Bt: Features,
                   matching: Matching) -> list[dict[str, Any]]:
    """Add region volumes to the stage-1/2 change that explains them; the rest -> VOLUME_DELTA."""
    pad = 0.05
    boxes = []
    for ch in changes:
        bx = [_face_box(A, k) for k in ch["base_face_ids"]] + [_face_box(Bt, k) for k in ch["target_face_ids"]]
        boxes.append(bx)
    out = []
    part_center = 0.5 * (A.bbox_min + A.bbox_max)
    for reg in regions:
        owner = None
        for ci, bx in enumerate(boxes):
            if any(_boxes_touch(reg.bmin, reg.bmax, lo, hi, pad) for lo, hi in bx):
                owner = ci
                break
        key = "removed_volume_mm3" if reg.kind == "REMOVED" else "added_volume_mm3"
        if owner is not None:
            fact = changes[owner]["fact"]
            fact[key] = _r(float(fact.get(key, 0.0)) + reg.volume, 3)
            continue
        base_ids = [k for k in range(A.n) if k not in dict(matching.unchanged)
                    and _boxes_touch(reg.bmin, reg.bmax, *_face_box(A, k), pad)]
        unchanged_t = {j for _, j in matching.unchanged}
        target_ids = [k for k in range(Bt.n) if k not in unchanged_t and _boxes_touch(reg.bmin, reg.bmax, *_face_box(Bt, k), pad)]
        size = reg.bmax - reg.bmin
        sign = "-" if reg.kind == "REMOVED" else "+"
        label = "제거된 살 영역" if reg.kind == "REMOVED" else "추가된 살 영역"
        fact = {"region": reg.kind, "volume_mm3": _r(reg.volume, 3), "size_x_mm": _r(size[0], 3), "size_y_mm": _r(size[1], 3),
                "size_z_mm": _r(size[2], 3), "method": "BOOLEAN"}
        view = reg.centroid - part_center
        view = view if np.linalg.norm(view) > 1e-6 else np.array([0.0, 0.0, 1.0])
        out.append(make_change("VOLUME_DELTA", C.SEVERITY_UNEXPLAINED_VOLUME if reg.volume >= 1.0 else "INFO",
                               f"{label}: {sign}{reg.volume:.3f} mm³ (육안 확인 필요)", fact, base_ids, target_ids,
                               reg.centroid, view, max(0.6 * float(np.linalg.norm(size)), 5.0)))
    return out


def mesh_distance(points: np.ndarray, mesh: MeshData, diag: float) -> np.ndarray:
    """Unsigned distance from points to a triangle mesh (subdivided + cKDTree + exact point-triangle)."""
    if len(points) == 0 or mesh.n_triangles == 0:
        return np.full(len(points), np.inf)
    tris = mesh.positions[mesh.indices]
    T, _ = subdivide_triangles(tris, mesh.tri_face, max(diag / 120.0, 0.25))
    tree = cKDTree(T.mean(axis=1))
    k = min(12, len(T))
    _, idx = tree.query(points, k=k)
    idx = np.asarray(idx).reshape(len(points), k)
    P = np.repeat(points, k, axis=0)
    I = idx.reshape(-1)
    Q = closest_point_on_triangles(P, T[I, 0], T[I, 1], T[I, 2])
    return np.linalg.norm(P - Q, axis=1).reshape(len(points), k).min(axis=1)


def _explained_mask(points: np.ndarray, A: Features, Bt: Features, matching: Matching, changes: list[dict[str, Any]], tau: float) -> np.ndarray:
    """Samples whose deviation is accounted for by a reported fact (swept slab / hole annulus)."""
    ok = np.zeros(len(points), dtype=bool)
    reported_planes = {(ch["base_face_ids"][0], ch["target_face_ids"][0]) for ch in changes
                       if ch["category"] == "PLANAR_OFFSET" and ch["base_face_ids"] and ch["target_face_ids"]}
    for p in matching.plane_pairs:
        if (p.i, p.j) not in reported_planes:
            continue
        n = A.normal[p.i]
        u, v = plane_basis(n)
        ea, eb = _extent2d(A, p.i, u, v), _extent2d(Bt, p.j, u, v)
        pad = abs(p.offset) + tau
        s = (points - A.origin[p.i]) @ n
        pu, pv = points @ u, points @ v
        ok |= ((s >= min(0.0, p.offset) - tau) & (s <= max(0.0, p.offset) + tau)
               & (pu >= min(ea[0], eb[0]) - pad) & (pu <= max(ea[1], eb[1]) + pad)
               & (pv >= min(ea[2], eb[2]) - pad) & (pv <= max(ea[3], eb[3]) + pad))
    for hp in matching.hole_pairs:
        for h in (hp.a, hp.b):
            r_lo = min(hp.a.radius, hp.b.radius) - tau
            r_hi = max(hp.a.radius, hp.b.radius) + tau
            d = point_line_distance(points, h.origin, h.axis)
            s = (points - h.origin) @ h.axis
            ok |= (d >= r_lo) & (d <= r_hi) & (s >= h.s_min - tau) & (s <= h.s_max + tau)
    return ok


def heatmap_changes(A: Features, Bt: Features, matching: Matching, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rng = np.random.default_rng(C.RNG_SEED)
    diag = float(np.linalg.norm(np.maximum(A.bbox_max, Bt.bbox_max) - np.minimum(A.bbox_min, Bt.bbox_min)))
    tau = max(C.CHANGE_REPORT_MIN_MM, 2.0 * max(A.mesh.linear_deflection, Bt.mesh.linear_deflection))
    unchanged_a = {i for i, _ in matching.unchanged}
    unchanged_b = {j for _, j in matching.unchanged}
    out = []
    for side, src, dst, skip in (("TARGET", Bt, A, unchanged_b), ("BASE", A, Bt, unchanged_a)):
        pts, fids, _ = sample_surface(src.mesh, C.HEATMAP_SAMPLE_POINTS, rng)
        if len(pts) == 0:
            continue
        dev = mesh_distance(pts, dst.mesh, diag)
        bad = (dev > tau) & ~_explained_mask(pts, A, Bt, matching, changes, tau)
        per_face: dict[int, list[float]] = {}
        for f, d in zip(fids[bad].tolist(), dev[bad].tolist()):
            if f not in skip:
                per_face.setdefault(f, []).append(d)
        # cluster deviating faces on the AAG
        remaining = set(per_face)
        while remaining:
            seed = min(remaining)
            comp, stack = [], [seed]
            remaining.discard(seed)
            while stack:
                f = stack.pop()
                comp.append(f)
                for g in src.adjacency[f]:
                    if g in remaining:
                        remaining.discard(g)
                        stack.append(g)
            comp.sort()
            devs = [d for f in comp for d in per_face[f]]
            P = np.vstack([_face_points(src, f) for f in comp])
            anchor = src.centroid[max(comp, key=lambda f: max(per_face[f]))]
            fact = {"method": "HEATMAP", "side": side, "max_deviation_mm": _r(max(devs)), "deviating_samples": len(devs),
                    "threshold_mm": _r(tau)}
            ids_b = comp if side == "BASE" else []
            ids_t = comp if side == "TARGET" else []
            center = 0.5 * (A.bbox_min + A.bbox_max)
            view = anchor - center if np.linalg.norm(anchor - center) > 1e-6 else np.array([0.0, 0.0, 1.0])
            out.append(make_change("VOLUME_DELTA", C.SEVERITY_UNEXPLAINED_VOLUME, f"형상 편차 영역: 최대 {max(devs):.3f}mm (육안 확인 필요)",
                                   fact, ids_b, ids_t, anchor, view, max(0.6 * float(np.linalg.norm(P.max(0) - P.min(0))), 5.0)))
    return out


def delta_glb(regions: list[Region], rgb: tuple[float, float, float], diag: float, name: str) -> bytes | None:
    if not regions:
        return None
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for r in regions:
        builder.Add(comp, r.shape)
    _fmap, faces = face_index(comp)
    lin, ang = mesh_shape(comp, diag, "lod0")
    mesh = extract_mesh(faces, linear_deflection=lin, angular_deflection=ang)
    colors = np.tile(np.array(rgb), (len(mesh.positions), 1))
    return build_glb(mesh.positions, mesh.normals, colors, mesh.vertex_face, mesh.indices, name=name,
                     extras={"lod": "delta", "region_count": len(regions), "volume_mm3": _r(sum(r.volume for r in regions), 3)})


# ============================================================================================
# job entry point
# ============================================================================================
def _sort_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(changes, key=lambda c: (SEVERITY_ORDER[c["severity"]], CATEGORY_ORDER[c["category"]],
                                          c["anchor_xyz"], c["change_id"]))


def validate_manifest(manifest: dict[str, Any]) -> None:
    from revgate_contracts import DiffManifest

    DiffManifest.model_validate(manifest)


def run_diff_job(spec: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    out_dir = Path(spec["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(spec.get("work_dir") or out_dir)
    options = spec.get("options") or {}

    base = load_revision(spec["base"]["step_path"], spec["base"]["revision_id"], spec["base"].get("expected_sha256"))
    target = load_revision(spec["target"]["step_path"], spec["target"]["revision_id"], spec["target"].get("expected_sha256"))
    timings["load_ms"] = (time.perf_counter() - t0) * 1e3
    A, B = Features.from_revision(base), Features.from_revision(target)

    t1 = time.perf_counter()
    al_spec = spec.get("alignment") or {"mode": "AUTO"}
    if al_spec.get("mode") == "MANUAL_321":
        T = np.asarray(al_spec.get("transform_4x4"), dtype=np.float64)
        if T.size != 16:
            raise ValueError("transform_4x4 must have 16 numbers")
        T = T.reshape(4, 4)
        if not is_rigid(T, 1e-6):
            raise ValueError("transform_4x4 is not a rigid (rotation + translation) transform")
        alignment: AlignmentResult = manual_align(A, B, T)
    else:
        alignment = auto_align(A, B)
    timings["align_ms"] = (time.perf_counter() - t1) * 1e3

    t2 = time.perf_counter()
    Bt = B.transformed(alignment.transform)
    matching = match_faces(A, Bt)
    changes = hole_changes(A, Bt, matching.hole_pairs) + planar_changes(A, Bt, matching.plane_pairs)
    changes += wall_changes(A, Bt, matching)
    timings["face_diff_ms"] = (time.perf_counter() - t2) * 1e3

    volume_delta = target.volume_mm3 - base.volume_mm3
    geometry_identical = (not matching.removed and not matching.added and not matching.base_modified
                          and not matching.target_modified and abs(volume_delta) <= 1e-6 * max(base.volume_mm3, 1.0))
    warnings: list[str] = sorted(set(base.warnings) | set(target.warnings))
    artifacts: dict[str, str | None] = {"delta_added_glb": None, "delta_removed_glb": None}

    t3 = time.perf_counter()
    boolean_status = "SKIPPED"
    fallback_reason = None
    if geometry_identical or alignment.requires_manual_review:
        boolean_status = "SKIPPED"
    else:
        if not (base.is_watertight and target.is_watertight):
            fallback_reason = "OPEN_SHELL_ONLY"
        elif base.thread_bomb or target.thread_bomb:
            fallback_reason = "HELICAL_THREADS_DETECTED"
        else:
            timeout = float(options.get("boolean_timeout_sec", C.BOOLEAN_TIMEOUT_SEC))
            regions, err = run_boolean(base, target, alignment.transform, work_dir, timeout)
            if err is None:
                boolean_status = "OK"
                changes += attach_regions(regions, changes, A, Bt, matching)
                diag = max(base.bbox_diag, target.bbox_diag)
                for kind, key, rgb, fname in (("ADDED", "delta_added_glb", C.DELTA_ADDED_RGB, "delta_added.glb"),
                                              ("REMOVED", "delta_removed_glb", C.DELTA_REMOVED_RGB, "delta_removed.glb")):
                    data = delta_glb([r for r in regions if r.kind == kind], rgb, diag, fname[:-4])
                    if data is not None:
                        (out_dir / fname).write_bytes(data)
                        artifacts[key] = fname
            else:
                fallback_reason = err
        if fallback_reason is not None:
            boolean_status = "FALLBACK_HEATMAP"
            warnings = sorted(set(warnings) | {"BOOLEAN_FALLBACK_HEATMAP"})
            changes += heatmap_changes(A, Bt, matching, changes)
    timings["volume_diff_ms"] = (time.perf_counter() - t3) * 1e3

    changes = _sort_changes(changes)
    manifest = {
        "schema_version": C.SCHEMA_VERSION,
        "diff_run_id": spec["diff_run_id"],
        "base_revision_id": base.revision_id,
        "target_revision_id": target.revision_id,
        "alignment": alignment.manifest(),
        "summary": {
            "geometry_identical": bool(geometry_identical),
            "total_changes": len(changes),
            "volume_delta_mm3": _r(volume_delta, 3),
            "boolean_status": boolean_status,
        },
        "face_status_map": {
            "base": {"unchanged": sorted(i for i, _ in matching.unchanged), "modified": sorted(matching.base_modified),
                     "removed": sorted(matching.removed)},
            "target": {"unchanged": sorted(j for _, j in matching.unchanged), "modified": sorted(matching.target_modified),
                       "added": sorted(matching.added)},
        },
        "changes": changes,
        "artifacts": artifacts,
    }
    validate_manifest(manifest)
    (out_dir / "diff_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    timings["total_ms"] = (time.perf_counter() - t0) * 1e3

    status = "NEEDS_ALIGN" if alignment.requires_manual_review else "READY"
    return {
        "status": status,
        "job_type": "diff",
        "diff_run_id": spec["diff_run_id"],
        "manifest": "diff_manifest.json",
        "alignment": {**alignment.manifest(), "stage": alignment.stage, "hypotheses_evaluated": alignment.hypotheses_evaluated},
        "summary": manifest["summary"],
        "warnings": warnings,
        "fallback_reason": fallback_reason,
        "artifacts": artifacts,
        "timings_ms": {k: round(v, 1) for k, v in timings.items()},
    }
