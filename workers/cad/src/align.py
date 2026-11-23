"""Automatic rigid alignment Target(Rev B) -> Base(Rev A) (IMPLEMENTATION_BLUEPRINT §3.2 / §5.2).

Stage 0  IDENTITY fast path           inlier area ratio >= FAST_PATH_INLIER_MIN (0.60)
Stage 1  hypotheses (<= RANSAC_MAX_HYPOTHESES = 500, deterministic enumeration):
           P1 plane + perpendicular cylinder-hole frames (top-15 planes by area)
           P2 three non-parallel top planes -> corner frames
           P3 OBB (24 orientations) + 2,000-point trimmed ICP — only when planes+cylinders cover
              < 30% of the area (free-form parts)
Stage 2  scoring: align_confidence = sum(Area(inlier base faces)) / min(TotalArea_base, TotalArea_target)
         inlier = an "identical face" match (same surface within FACE_MATCH_DIST_MM / ANGLE_TOL_DEG,
         area within AREA_RATIO_TOL)
Stage 3  confidence >= ALIGN_CONFIDENCE_MIN (0.50): Kabsch/SVD snap on exact inlier feature points
         (hole-axis∩plane centres, 3-plane corners, sphere/torus centres, cone apexes);
         otherwise (or rms > ALIGN_RMS_MAX_MM) -> requires_manual_review (DiffRun NEEDS_ALIGN).

transform_4x4 is row-major and maps Target coordinates into the Base frame.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from . import constants as C
from .geom import (
    apply_dirs,
    apply_points,
    cube_rotations,
    frame_matrix,
    invert_rigid,
    kabsch,
    line_plane,
    normalize,
    pca_frame,
    point_line_distance,
    rodrigues,
    spans_3d,
    three_planes,
)
from .tessellate import MeshData, sample_surface

PLANE, CYLINDER, CONE, SPHERE, TORUS, BSPLINE, OTHER = range(7)
TYPE_CODE = {"PLANE": PLANE, "CYLINDER": CYLINDER, "CONE": CONE, "SPHERE": SPHERE, "TORUS": TORUS, "BSPLINE": BSPLINE, "OTHER": OTHER}
COS_TOL = math.cos(math.radians(C.ANGLE_TOL_DEG))
SIN_TOL = math.sin(math.radians(C.ANGLE_TOL_DEG))


# ============================================================================================
# vectorized per-revision descriptors
# ============================================================================================
@dataclass
class HoleFeature:
    """AFR grouping: adjacent coaxial cylinder faces with equal radius (split holes = 1 feature)."""

    face_ids: list[int]
    origin: np.ndarray  # axis point at the middle of the axial extent
    axis: np.ndarray
    radius: float
    internal: bool
    sweep_deg: float
    s_min: float  # axial extent relative to origin along axis
    s_max: float
    area: float

    @property
    def is_full(self) -> bool:
        return self.sweep_deg >= C.HOLE_SWEEP_MIN_DEG

    def transformed(self, T: np.ndarray) -> "HoleFeature":
        return HoleFeature(self.face_ids, apply_points(T, self.origin[None])[0], apply_dirs(T, self.axis[None])[0],
                           self.radius, self.internal, self.sweep_deg, self.s_min, self.s_max, self.area)


@dataclass
class Features:
    n: int
    types: np.ndarray
    area: np.ndarray
    centroid: np.ndarray
    diag: np.ndarray
    normal: np.ndarray
    origin: np.ndarray
    axis: np.ndarray
    radius: np.ndarray
    radius2: np.ndarray
    angle: np.ndarray
    internal: np.ndarray  # -1 n/a, 0 external, 1 internal
    apex: np.ndarray
    adjacency: list[list[int]]
    holes: list[HoleFeature]
    mesh: MeshData | None
    total_area: float
    bbox_min: np.ndarray
    bbox_max: np.ndarray
    T: np.ndarray = field(default_factory=lambda: np.eye(4))  # transform already applied

    @classmethod
    def from_revision(cls, rev) -> "Features":
        recs = rev.records
        n = len(recs)
        z3 = np.zeros((n, 3))
        types = np.array([TYPE_CODE[r.stype] for r in recs], dtype=np.int8)
        normal, origin, axis, apex = z3.copy(), z3.copy(), z3.copy(), z3.copy()
        radius, radius2, angle = np.zeros(n), np.zeros(n), np.zeros(n)
        internal = np.full(n, -1, dtype=np.int8)
        for i, r in enumerate(recs):
            origin[i] = r.origin if r.origin is not None else r.centroid
            if r.normal is not None:
                normal[i] = r.normal
            if r.axis_dir is not None:
                axis[i] = r.axis_dir
            if r.apex is not None:
                apex[i] = r.apex
            radius[i] = r.radius or 0.0
            radius2[i] = r.radius2 or 0.0
            angle[i] = r.half_angle_deg or 0.0
            if r.is_internal is not None:
                internal[i] = 1 if r.is_internal else 0
        feats = cls(
            n=n, types=types, area=np.array([r.area for r in recs]), centroid=np.array([r.centroid for r in recs]).reshape(n, 3),
            diag=np.array([r.diag for r in recs]), normal=normal, origin=origin, axis=axis, radius=radius,
            radius2=radius2, angle=angle, internal=internal, apex=apex, adjacency=[list(r.adjacent) for r in recs],
            holes=[], mesh=rev.lod0, total_area=float(sum(r.area for r in recs)), bbox_min=rev.bbox_min, bbox_max=rev.bbox_max,
        )
        feats.holes = group_holes(feats, recs)
        return feats

    def transformed(self, T: np.ndarray) -> "Features":
        mesh = None
        if self.mesh is not None:
            m = self.mesh
            mesh = MeshData(apply_points(T, m.positions), apply_dirs(T, m.normals), m.indices, m.vertex_face, m.tri_face,
                            m.face_tri_range, m.face_vertex_range, m.face_uv, m.linear_deflection, m.angular_deflection)
        corners = np.array(list(itertools.product(*zip(self.bbox_min, self.bbox_max))))
        tc = apply_points(T, corners)
        return Features(
            n=self.n, types=self.types, area=self.area, centroid=apply_points(T, self.centroid), diag=self.diag,
            normal=apply_dirs(T, self.normal), origin=apply_points(T, self.origin), axis=apply_dirs(T, self.axis),
            radius=self.radius, radius2=self.radius2, angle=self.angle, internal=self.internal,
            apex=apply_points(T, self.apex), adjacency=self.adjacency, holes=[h.transformed(T) for h in self.holes],
            mesh=mesh, total_area=self.total_area, bbox_min=tc.min(axis=0), bbox_max=tc.max(axis=0), T=T @ self.T,
        )

    def analytic_share(self) -> float:
        if self.total_area <= 0:
            return 0.0
        m = (self.types == PLANE) | (self.types == CYLINDER)
        return float(self.area[m].sum() / self.total_area)


def group_holes(F: Features, recs) -> list[HoleFeature]:
    cyl = [i for i in range(F.n) if F.types[i] == CYLINDER]
    parent = {i: i for i in cyl}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cyl_set = set(cyl)
    for i in cyl:
        for k in F.adjacency[i]:
            if k <= i or k not in cyl_set:
                continue
            if (F.internal[i] == F.internal[k] and abs(F.radius[i] - F.radius[k]) <= C.FACE_MATCH_DIST_MM
                    and abs(np.dot(F.axis[i], F.axis[k])) >= COS_TOL
                    and point_line_distance(F.origin[k], F.origin[i], F.axis[i]) <= C.AXIS_COAXIAL_TOL_MM):
                parent[find(k)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in cyl:
        groups.setdefault(find(i), []).append(i)
    holes = []
    for root in sorted(groups):
        ids = sorted(groups[root])
        a = F.axis[ids[0]]
        o0 = F.origin[ids[0]]
        lo, hi = [], []
        for i in ids:
            s = float(np.dot(F.origin[i] - o0, a))
            h = (recs[i].height or 0.0) / 2.0
            lo.append(s - h)
            hi.append(s + h)
        s0, s1 = min(lo), max(hi)
        mid = o0 + a * 0.5 * (s0 + s1)
        holes.append(HoleFeature(
            face_ids=ids, origin=mid, axis=a, radius=float(np.mean(F.radius[ids])), internal=bool(F.internal[ids[0]] == 1),
            sweep_deg=float(min(360.0, sum(recs[i].sweep_deg or 0.0 for i in ids))), s_min=-0.5 * (s1 - s0),
            s_max=0.5 * (s1 - s0), area=float(F.area[ids].sum()),
        ))
    return holes


# ============================================================================================
# identical-face inlier matching
# ============================================================================================
def identical_mask(A: Features, B: Features, ii: np.ndarray, jj: np.ndarray, tol_scale: float = 1.0) -> np.ndarray:
    """Vectorized 'same face' predicate for base faces ii vs (already transformed) target faces jj."""
    if len(ii) == 0:
        return np.zeros(0, dtype=bool)
    pos_tol = C.FACE_MATCH_DIST_MM * tol_scale
    cos_tol = math.cos(math.radians(min(C.ANGLE_TOL_DEG * tol_scale, 5.0)))
    ang_tol = min(C.ANGLE_TOL_DEG * tol_scale, 5.0)
    shape_tol = C.FACE_MATCH_DIST_MM

    ta, tb = A.types[ii], B.types[jj]
    m = ta == tb
    amax = np.maximum(A.area[ii], B.area[jj])
    m &= np.abs(A.area[ii] - B.area[jj]) <= C.AREA_RATIO_TOL * np.maximum(amax, 1e-12)
    dc = np.linalg.norm(A.centroid[ii] - B.centroid[jj], axis=1)
    loose_ctol = np.maximum(pos_tol, C.AREA_RATIO_TOL * A.diag[ii])
    tight = (ta == BSPLINE) | (ta == OTHER)
    m &= dc <= np.where(tight, pos_tol, loose_ctol)

    na, nb = A.normal[ii], B.normal[jj]
    oa, ob = A.origin[ii], B.origin[jj]
    aa, ab = A.axis[ii], B.axis[jj]
    dot_n = np.einsum("ij,ij->i", na, nb)
    dot_a = np.abs(np.einsum("ij,ij->i", aa, ab))
    geo = np.ones(len(ii), dtype=bool)

    pl = ta == PLANE
    geo[pl] = (dot_n[pl] >= cos_tol) & (np.abs(np.einsum("ij,ij->i", na[pl], ob[pl] - oa[pl])) <= pos_tol)

    ax = (ta == CYLINDER) | (ta == CONE)
    if ax.any():
        dist = point_line_distance(ob[ax], oa[ax], aa[ax])
        g = (dot_a[ax] >= cos_tol) & (dist <= pos_tol) & (A.internal[ii][ax] == B.internal[jj][ax])
        g &= np.abs(A.radius[ii][ax] - B.radius[jj][ax]) <= shape_tol
        cone = ta[ax] == CONE
        g[cone] &= (np.abs(A.radius2[ii][ax][cone] - B.radius2[jj][ax][cone]) <= shape_tol) & (
            np.abs(A.angle[ii][ax][cone] - B.angle[jj][ax][cone]) <= ang_tol)
        geo[ax] = g

    sp = (ta == SPHERE) | (ta == TORUS)
    if sp.any():
        g = (np.linalg.norm(oa[sp] - ob[sp], axis=1) <= pos_tol) & (np.abs(A.radius[ii][sp] - B.radius[jj][sp]) <= shape_tol)
        tor = ta[sp] == TORUS
        g[tor] &= (dot_a[sp][tor] >= cos_tol) & (np.abs(A.radius2[ii][sp][tor] - B.radius2[jj][sp][tor]) <= shape_tol)
        geo[sp] = g
    return m & geo


class Matcher:
    """KD-tree over base centroids; scores transforms by inlier base-face area."""

    def __init__(self, A: Features) -> None:
        self.A = A
        self.tree = cKDTree(A.centroid) if A.n else None

    def candidates(self, Bt: Features, subset: np.ndarray | None = None, tol_scale: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
        if self.tree is None or Bt.n == 0:
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
        jj_src = np.arange(Bt.n) if subset is None else subset
        radii = np.maximum(C.FACE_MATCH_DIST_MM * tol_scale, C.AREA_RATIO_TOL * Bt.diag[jj_src] * 1.03) + 1e-9
        hits = self.tree.query_ball_point(Bt.centroid[jj_src], radii)
        lens = np.fromiter((len(h) for h in hits), dtype=np.int64, count=len(hits))
        if lens.sum() == 0:
            return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
        ii = np.fromiter(itertools.chain.from_iterable(hits), dtype=np.int64, count=int(lens.sum()))
        jj = np.repeat(jj_src, lens)
        keep = identical_mask(self.A, Bt, ii, jj, tol_scale)
        return ii[keep], jj[keep]

    def score(self, T: np.ndarray, B: Features, subset: np.ndarray | None = None, tol_scale: float = 1.0,
              denom: float | None = None) -> tuple[float, list[tuple[int, int]]]:
        Bt = B.transformed(T) if not np.allclose(T, np.eye(4)) else B
        ii, jj = self.candidates(Bt, subset, tol_scale)
        if denom is None:
            denom = min(self.A.total_area, B.total_area)
        if len(ii) == 0 or denom <= 0:
            return 0.0, []
        pairs = best_pairs(self.A, Bt, ii, jj)
        matched = np.unique(ii)
        return float(min(1.0, self.A.area[matched].sum() / denom)), pairs


def best_pairs(A: Features, Bt: Features, ii: np.ndarray, jj: np.ndarray) -> list[tuple[int, int]]:
    """One-to-one (greedy by centroid distance) from candidate inlier pairs."""
    d = np.linalg.norm(A.centroid[ii] - Bt.centroid[jj], axis=1)
    order = np.lexsort((jj, ii, d))
    used_a: set[int] = set()
    used_b: set[int] = set()
    out = []
    for k in order:
        a, b = int(ii[k]), int(jj[k])
        if a in used_a or b in used_b:
            continue
        used_a.add(a)
        used_b.add(b)
        out.append((a, b))
    return sorted(out)


# ============================================================================================
# hypothesis generation
# ============================================================================================
@dataclass
class Frame:
    kind: int  # 0: plane+hole+hole, 1: plane+hole+plane, 2: three planes
    M: np.ndarray
    sig: np.ndarray


def _top_planes(F: Features, k: int) -> list[int]:
    idx = [int(i) for i in np.lexsort((np.arange(F.n), -F.area)) if F.types[i] == PLANE]
    return idx[:k]


def plane_hole_frames(F: Features) -> list[Frame]:
    planes = _top_planes(F, C.RANSAC_TOP_PLANES)
    holes = [h for h in F.holes if h.is_full]
    frames: list[Frame] = []
    for p in planes:
        n, o = F.normal[p], F.origin[p]
        perp = [h for h in holes if abs(np.dot(h.axis, n)) >= COS_TOL]
        centers = []
        for h in perp:
            c = line_plane(h.origin, h.axis, o, n)
            centers.append(c)
        order = sorted(range(len(perp)), key=lambda k: (-perp[k].radius, float(np.linalg.norm(centers[k] - F.centroid[p]))))
        order = order[: C.RANSAC_HOLES_PER_PLANE]
        for k1 in order:
            h1, O = perp[k1], centers[k1]
            for k2 in order:
                if k2 == k1:
                    continue
                v = centers[k2] - O
                v = v - np.dot(v, n) * n
                L = float(np.linalg.norm(v))
                if L < 1.0:
                    continue
                h2 = perp[k2]
                frames.append(Frame(0, frame_matrix(O, n, v / L), np.array(
                    [F.area[p], h1.radius, float(h1.internal), h2.radius, float(h2.internal), L])))
            for q in planes:
                if q == p:
                    continue
                nq = F.normal[q]
                if abs(np.dot(nq, n)) > SIN_TOL:
                    continue
                dist = float(np.dot(O - F.origin[q], nq))
                frames.append(Frame(1, frame_matrix(O, n, nq), np.array(
                    [F.area[p], h1.radius, float(h1.internal), F.area[q], 0.0, dist])))
    return frames


def triplet_frames(F: Features, ordered: bool) -> list[Frame]:
    planes = _top_planes(F, C.RANSAC_TRIPLET_PLANES)
    diag = float(np.linalg.norm(F.bbox_max - F.bbox_min))
    lo, hi = F.bbox_min - 0.1 * diag, F.bbox_max + 0.1 * diag
    combos = itertools.permutations(planes, 3) if ordered else itertools.combinations(planes, 3)
    frames: list[Frame] = []
    for i, j, k in combos:
        N = np.array([F.normal[i], F.normal[j], F.normal[k]])
        det = float(np.linalg.det(N))
        if abs(det) < 0.2:
            continue
        P = three_planes(N, np.array([F.origin[i], F.origin[j], F.origin[k]]))
        if P is None or np.any(P < lo) or np.any(P > hi):
            continue
        sig = np.array([np.dot(N[0], N[1]), np.dot(N[0], N[2]), np.dot(N[1], N[2]), math.copysign(1.0, det),
                        F.area[i], F.area[j], F.area[k]])
        frames.append(Frame(2, frame_matrix(P, N[0], N[1]), sig))
    return frames


def _area_ok(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.minimum(a, b) >= 0.5 * np.maximum(a, b)


def pair_frames(FA: list[Frame], FB: list[Frame]) -> list[tuple[float, np.ndarray]]:
    out: list[tuple[float, np.ndarray]] = []
    for kind in (0, 1, 2):
        fa = [f for f in FA if f.kind == kind]
        fb = [f for f in FB if f.kind == kind]
        if not fa or not fb:
            continue
        SB = np.array([f.sig for f in fb])
        for f in fa:
            s = f.sig
            if kind in (0, 1):
                m = (np.abs(SB[:, 1] - s[1]) <= C.FACE_MATCH_DIST_MM) & (SB[:, 2] == s[2]) & _area_ok(SB[:, 0], s[0])
                m &= np.abs(SB[:, 5] - s[5]) <= C.AXIS_COAXIAL_TOL_MM
                if kind == 0:
                    m &= (np.abs(SB[:, 3] - s[3]) <= C.FACE_MATCH_DIST_MM) & (SB[:, 4] == s[4])
                else:
                    m &= _area_ok(SB[:, 3], s[3])
                cost = np.abs(SB[:, 5] - s[5]) + np.abs(SB[:, 1] - s[1]) + 1e-3 * np.abs(np.log(SB[:, 0] / s[0]))
            else:
                m = np.all(np.abs(SB[:, :3] - s[:3]) <= 0.01, axis=1) & (SB[:, 3] == s[3])
                m &= _area_ok(SB[:, 4], s[4]) & _area_ok(SB[:, 5], s[5]) & _area_ok(SB[:, 6], s[6])
                cost = np.abs(SB[:, :3] - s[:3]).sum(axis=1) + 1e-3 * np.abs(np.log(SB[:, 4:7] / s[4:7])).sum(axis=1)
            for jb in np.flatnonzero(m):
                out.append((float(cost[jb]) + 10.0 * kind, f.M @ np.linalg.inv(fb[jb].M)))
    out.sort(key=lambda x: x[0])
    return out


def _dedupe(hyps: list[tuple[float, np.ndarray, str]], limit: int) -> list[tuple[float, np.ndarray, str]]:
    seen: set[tuple] = set()
    out = []
    for cost, T, stage in hyps:
        key = tuple(np.round(T[:3, :3], 3).reshape(-1)) + tuple(np.round(T[:3, 3], 2))
        if key in seen:
            continue
        seen.add(key)
        out.append((cost, T, stage))
        if len(out) >= limit:
            break
    return out


# ---------------- P3: OBB (24) + trimmed ICP --------------------------------------------------
def _icp(src: np.ndarray, tree: cKDTree, dst: np.ndarray, dst_n: np.ndarray, T: np.ndarray, iters: int,
         p2p_iters: int = 4) -> tuple[np.ndarray, float]:
    rms = float("inf")
    for it in range(iters):
        P = apply_points(T, src)
        d, idx = tree.query(P)
        keep = np.argsort(d)[: max(3, int(len(d) * C.ICP_TRIM_RATIO))]
        Ps, Q, Nq = P[keep], dst[idx[keep]], dst_n[idx[keep]]
        if it < p2p_iters:
            dT = kabsch(Ps, Q)
        else:
            r = np.einsum("ij,ij->i", Ps - Q, Nq)
            J = np.hstack([np.cross(Ps, Nq), Nq])
            x, *_ = np.linalg.lstsq(J, -r, rcond=None)
            dT = np.eye(4)
            dT[:3, :3] = rodrigues(x[:3])
            dT[:3, 3] = x[3:]
        T = dT @ T
        new_rms = float(np.sqrt(np.mean(d[keep] ** 2)))
        if abs(rms - new_rms) < 1e-7:
            rms = new_rms
            break
        rms = new_rms
    P = apply_points(T, src)
    d, _ = tree.query(P)
    keep = np.sort(d)[: max(3, int(len(d) * C.ICP_TRIM_RATIO))]
    return T, float(np.sqrt(np.mean(keep ** 2)))


def obb_icp_hypotheses(A: Features, B: Features) -> list[tuple[float, np.ndarray, str]]:
    if A.mesh is None or B.mesh is None or A.mesh.n_triangles == 0 or B.mesh.n_triangles == 0:
        return []
    rng = np.random.default_rng(C.RNG_SEED)
    PA, _, _ = sample_surface(A.mesh, C.ICP_SAMPLE_POINTS, rng)
    PB, _, _ = sample_surface(B.mesh, C.ICP_SAMPLE_POINTS, rng)
    QA, _, QN = sample_surface(A.mesh, 4 * C.ICP_SAMPLE_POINTS, rng)
    tree = cKDTree(QA)
    Ea, mua = pca_frame(PA)
    Eb, mub = pca_frame(PB)
    coarse = []
    for S in cube_rotations():
        T0 = np.eye(4)
        T0[:3, :3] = Ea @ S @ Eb.T
        T0[:3, 3] = mua - T0[:3, :3] @ mub
        T, err = _icp(PB, tree, QA, QN, T0, C.ICP_COARSE_ITERS)
        coarse.append((err, T))
    coarse.sort(key=lambda x: x[0])
    out = []
    for err, T in coarse[:3]:
        T, err = _icp(PB, tree, QA, QN, T, C.ICP_FINE_ITERS, p2p_iters=0)
        out.append((err, T, "P3_OBB_ICP"))
    return out


# ============================================================================================
# snap + residuals
# ============================================================================================
def virtual_correspondences(A: Features, B: Features, pairs: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
    """Exact feature points from inlier pairs: (points in Base frame, same points in Target frame)."""
    to_b = dict(pairs)
    pa, pb = [], []
    cos30 = math.cos(math.radians(30.0))
    for i, j in pairs:
        if A.types[i] in (CYLINDER, CONE):
            for k in A.adjacency[i]:
                l = to_b.get(k)
                if l is None or A.types[k] != PLANE or l not in B.adjacency[j]:
                    continue
                if abs(np.dot(A.axis[i], A.normal[k])) < cos30:
                    continue
                xa = line_plane(A.origin[i], A.axis[i], A.origin[k], A.normal[k])
                xb = line_plane(B.origin[j], B.axis[j], B.origin[l], B.normal[l])
                if xa is not None and xb is not None:
                    pa.append(xa)
                    pb.append(xb)
        if A.types[i] in (SPHERE, TORUS):
            pa.append(A.origin[i])
            pb.append(B.origin[j])
        if A.types[i] == CONE:
            pa.append(A.apex[i])
            pb.append(B.apex[j])
    planes = sorted(i for i, _ in pairs if A.types[i] == PLANE)
    pset = set(planes)
    for k in planes:
        nbrs = [x for x in A.adjacency[k] if x in pset and x > k]
        for k2, k3 in itertools.combinations(nbrs, 2):
            if k3 not in A.adjacency[k2]:
                continue
            N = np.array([A.normal[k], A.normal[k2], A.normal[k3]])
            if abs(np.linalg.det(N)) < 0.5:
                continue
            xa = three_planes(N, np.array([A.origin[k], A.origin[k2], A.origin[k3]]))
            l, l2, l3 = to_b[k], to_b[k2], to_b[k3]
            xb = three_planes(np.array([B.normal[l], B.normal[l2], B.normal[l3]]), np.array([B.origin[l], B.origin[l2], B.origin[l3]]))
            if xa is not None and xb is not None:
                pa.append(xa)
                pb.append(xb)
    if not pa:
        return np.zeros((0, 3)), np.zeros((0, 3))
    PA, PB = np.array(pa), np.array(pb)
    _, uniq = np.unique(np.round(PA, 6), axis=0, return_index=True)
    uniq = np.sort(uniq)
    return PA[uniq], PB[uniq]


def residual_rms(A: Features, B: Features, T: np.ndarray, pairs: list[tuple[int, int]]) -> float:
    """RMS (mm) of inlier feature residuals under T (virtual points when available)."""
    PA, PB = virtual_correspondences(A, B, pairs)
    if len(PA) >= 3 and spans_3d(PA):
        return float(np.sqrt(np.mean(np.sum((apply_points(T, PB) - PA) ** 2, axis=1))))
    if not pairs:
        return 0.0
    Bt = B.transformed(T)
    res = []
    for i, j in pairs:
        t = A.types[i]
        if t == PLANE:
            res.append(float(np.dot(A.normal[i], Bt.origin[j] - A.origin[i])))
        elif t in (CYLINDER, CONE):
            res.append(float(point_line_distance(Bt.origin[j], A.origin[i], A.axis[i])))
        elif t in (SPHERE, TORUS):
            res.append(float(np.linalg.norm(Bt.origin[j] - A.origin[i])))
        else:
            res.append(float(np.linalg.norm(Bt.centroid[j] - A.centroid[i])))
    return float(np.sqrt(np.mean(np.square(res))))


def snap(A: Features, B: Features, T: np.ndarray, matcher: Matcher) -> tuple[np.ndarray, list[tuple[int, int]]] | None:
    """Kabsch on exact inlier feature points (relaxed correspondences first, then strict re-score)."""
    for tol_scale in (10.0, 1.0):
        _, pairs = matcher.score(T, B, tol_scale=tol_scale)
        PA, PB = virtual_correspondences(A, B, pairs)
        if len(PA) >= 3 and spans_3d(PA):
            return kabsch(PB, PA), pairs
    return None


# ============================================================================================
# public API
# ============================================================================================
@dataclass
class AlignmentResult:
    method: str
    transform: np.ndarray
    confidence: float
    rms_error_mm: float
    requires_manual_review: bool
    stage: str
    hypotheses_evaluated: int = 0
    inlier_pairs: list[tuple[int, int]] = field(default_factory=list)

    def manifest(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "transform_4x4": [round(float(v), 12) + 0.0 for v in self.transform.reshape(-1)],
            "confidence": round(float(min(1.0, max(0.0, self.confidence))), 6),
            "rms_error_mm": round(float(self.rms_error_mm), 9),
            "requires_manual_review": bool(self.requires_manual_review),
        }


def evaluate_transform(A: Features, B: Features, T: np.ndarray, method: str, stage: str) -> AlignmentResult:
    matcher = Matcher(A)
    conf, pairs = matcher.score(T, B)
    rms = residual_rms(A, B, T, pairs)
    return AlignmentResult(method, T, conf, rms, False, stage, 1, pairs)


def auto_align(A: Features, B: Features) -> AlignmentResult:
    matcher = Matcher(A)
    I = np.eye(4)
    conf_id, pairs_id = matcher.score(I, B)
    if conf_id >= C.FAST_PATH_INLIER_MIN:
        rms = residual_rms(A, B, I, pairs_id)
        return AlignmentResult("IDENTITY", I, conf_id, rms, rms > C.ALIGN_RMS_MAX_MM, "STAGE0_IDENTITY", 1, pairs_id)

    hyps: list[tuple[float, np.ndarray, str]] = []
    hyps += [(c, T, "P1_PLANE_CYLINDER") for c, T in pair_frames(plane_hole_frames(A), plane_hole_frames(B))]
    hyps += [(c, T, "P2_THREE_PLANES") for c, T in pair_frames(triplet_frames(A, False), triplet_frames(B, True))]
    hyps = _dedupe(hyps, C.RANSAC_MAX_HYPOTHESES)
    if min(A.analytic_share(), B.analytic_share()) < C.FREEFORM_ANALYTIC_AREA_MAX:
        hyps = hyps[: C.RANSAC_MAX_HYPOTHESES - 3] + obb_icp_hypotheses(A, B)

    coarse_idx = np.lexsort((np.arange(B.n), -B.area))[: C.RANSAC_COARSE_FACES]
    denom = min(A.total_area, B.total_area)
    ranked = []
    for k, (cost, T, stage) in enumerate(hyps):
        s, _ = matcher.score(T, B, subset=coarse_idx, denom=denom)
        ranked.append((-s, k, T, stage))
    ranked.sort(key=lambda x: (x[0], x[1]))

    best = AlignmentResult("IDENTITY", I, conf_id, residual_rms(A, B, I, pairs_id), True, "STAGE0_IDENTITY", len(hyps) + 1, pairs_id)
    for _neg, _k, T, stage in ranked[: C.RANSAC_REFINE_TOP_K]:
        s, pairs = matcher.score(T, B)
        if s >= C.ALIGN_CONFIDENCE_MIN:
            snapped = snap(A, B, T, matcher)
            if snapped is not None:
                T2 = snapped[0]
                s2, pairs2 = matcher.score(T2, B)
                if s2 + 1e-9 >= s:
                    T, s, pairs = T2, s2, pairs2
        rms = residual_rms(A, B, T, pairs)
        # strictly better confidence wins; on a tie the lower RMS wins (identity is kept on ties)
        better = s > best.confidence + 1e-9 or (
            abs(s - best.confidence) <= 1e-9 and best.method != "IDENTITY" and rms < best.rms_error_mm - 1e-12)
        if better:
            best = AlignmentResult("AUTO_BREP_RANSAC", T, s, rms, False, stage, len(hyps) + 1, pairs)
    best.hypotheses_evaluated = len(hyps) + 1
    best.requires_manual_review = best.confidence < C.ALIGN_CONFIDENCE_MIN or best.rms_error_mm > C.ALIGN_RMS_MAX_MM
    if best.method == "IDENTITY" and best.requires_manual_review and hyps:
        best.method = "AUTO_BREP_RANSAC"  # auto alignment was attempted and rejected
    return best


def manual_align(A: Features, B: Features, T: np.ndarray) -> AlignmentResult:
    """3-2-1 datum alignment computed by the client: trust T, report how well it fits."""
    res = evaluate_transform(A, B, T, "MANUAL_321", "MANUAL_321")
    res.requires_manual_review = False
    return res


__all__ = ["AlignmentResult", "Features", "HoleFeature", "Matcher", "auto_align", "manual_align", "identical_mask", "invert_rigid"]
