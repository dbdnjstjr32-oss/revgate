"""Small numpy geometry kernel shared by align.py and diff_engine.py (mm, right-handed)."""

from __future__ import annotations

import itertools
import math

import numpy as np

EPS = 1e-12


def normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > EPS else v


def apply_points(T: np.ndarray, P: np.ndarray) -> np.ndarray:
    return P @ T[:3, :3].T + T[:3, 3]


def apply_dirs(T: np.ndarray, D: np.ndarray) -> np.ndarray:
    return D @ T[:3, :3].T


def invert_rigid(T: np.ndarray) -> np.ndarray:
    R, t = T[:3, :3], T[:3, 3]
    out = np.eye(4)
    out[:3, :3] = R.T
    out[:3, 3] = -R.T @ t
    return out


def is_rigid(T: np.ndarray, tol: float = 1e-6) -> bool:
    T = np.asarray(T, dtype=np.float64)
    if T.shape != (4, 4) or not np.all(np.isfinite(T)):
        return False
    R = T[:3, :3]
    return (
        np.allclose(R.T @ R, np.eye(3), atol=tol)
        and abs(np.linalg.det(R) - 1.0) < tol
        and np.allclose(T[3], [0.0, 0.0, 0.0, 1.0], atol=tol)
    )


def rotation_angle_deg(R: np.ndarray) -> float:
    """Rotation angle of R; atan2 form stays accurate near 0 (acos(trace) bottoms out at ~1e-6 deg)."""
    c = (np.trace(R) - 1.0) / 2.0
    s = 0.5 * np.linalg.norm([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    return math.degrees(math.atan2(s, c))


def kabsch(src: np.ndarray, dst: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    """Rigid T (4x4) minimizing sum w |R src + t - dst|^2 (SVD, reflection-safe)."""
    w = np.ones(len(src)) if weights is None else np.asarray(weights, dtype=np.float64)
    w = w / w.sum()
    cs = (w[:, None] * src).sum(axis=0)
    cd = (w[:, None] * dst).sum(axis=0)
    H = ((src - cs) * w[:, None]).T @ (dst - cd)
    U, _S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T)) or 1.0
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = cd - R @ cs
    return T


def spans_3d(P: np.ndarray, rel_tol: float = 1e-6) -> bool:
    """True if the points are not (nearly) collinear — enough to pin a rigid transform."""
    if len(P) < 3:
        return False
    s = np.linalg.svd(P - P.mean(axis=0), compute_uv=False)
    return bool(s[0] > EPS and s[1] > rel_tol * s[0])


def frame_matrix(origin: np.ndarray, z: np.ndarray, x_hint: np.ndarray) -> np.ndarray:
    """4x4 local->world frame with Z = z, X = x_hint orthogonalized, Y = Z x X."""
    Z = normalize(z)
    X = normalize(x_hint - np.dot(x_hint, Z) * Z)
    Y = np.cross(Z, X)
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3] = X, Y, Z, origin
    return M


def plane_basis(n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = normalize(n)
    e = np.eye(3)[int(np.argmin(np.abs(n)))]
    u = normalize(np.cross(n, e))
    return u, np.cross(n, u)


def line_plane(p: np.ndarray, d: np.ndarray, o: np.ndarray, n: np.ndarray) -> np.ndarray | None:
    den = float(np.dot(d, n))
    if abs(den) < 1e-9:
        return None
    return p + d * (np.dot(o - p, n) / den)


def three_planes(ns: np.ndarray, os_: np.ndarray) -> np.ndarray | None:
    N = np.asarray(ns, dtype=np.float64)
    if abs(np.linalg.det(N)) < 1e-9:
        return None
    d = np.einsum("ij,ij->i", N, np.asarray(os_, dtype=np.float64))
    return np.linalg.solve(N, d)


def point_line_distance(p: np.ndarray, o: np.ndarray, d: np.ndarray) -> np.ndarray:
    """|(p - o) x d| for unit d (vectorized over leading dims)."""
    v = p - o
    return np.linalg.norm(v - np.sum(v * d, axis=-1, keepdims=True) * d, axis=-1)


def cube_rotations() -> list[np.ndarray]:
    """The 24 proper rotations of the cube (signed permutation matrices with det = +1)."""
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1.0, -1.0), repeat=3):
            M = np.zeros((3, 3))
            for r, c in enumerate(perm):
                M[r, c] = signs[r]
            if np.linalg.det(M) > 0:
                out.append(M)
    return out


def rodrigues(w: np.ndarray) -> np.ndarray:
    th = float(np.linalg.norm(w))
    if th < EPS:
        K = np.array([[0, -w[2], w[1]], [w[2], 0, -w[0]], [-w[1], w[0], 0]])
        return np.eye(3) + K
    k = w / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1.0 - math.cos(th)) * (K @ K)


def pca_frame(P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(right-handed principal axes as columns, centroid)."""
    mu = P.mean(axis=0)
    w, V = np.linalg.eigh(np.cov((P - mu).T))
    V = V[:, np.argsort(w)[::-1]]
    if np.linalg.det(V) < 0:
        V[:, 2] = -V[:, 2]
    return V, mu


def closest_point_on_triangles(P: np.ndarray, A: np.ndarray, B: np.ndarray, C: np.ndarray) -> np.ndarray:
    """Closest points on triangles (A,B,C) to points P, all (k,3) (Ericson, vectorized)."""
    ab, ac, ap = B - A, C - A, P - A
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    bp = P - B
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    cp = P - C
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)

    res = np.empty_like(P)
    done = np.zeros(len(P), dtype=bool)

    def take(mask, val):
        nonlocal done
        m = mask & ~done
        res[m] = val[m] if val.ndim == 2 else val
        done |= m

    take((d1 <= 0) & (d2 <= 0), A)
    take((d3 >= 0) & (d4 <= d3), B)
    vc = d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        v = d1 / (d1 - d3)
        take((vc <= 0) & (d1 >= 0) & (d3 <= 0), A + v[:, None] * ab)
        take((d6 >= 0) & (d5 <= d6), C)
        vb = d5 * d2 - d1 * d6
        w = d2 / (d2 - d6)
        take((vb <= 0) & (d2 >= 0) & (d6 <= 0), A + w[:, None] * ac)
        va = d3 * d6 - d5 * d4
        w2 = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        take((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), B + w2[:, None] * (C - B))
        denom = 1.0 / (va + vb + vc)
        vv = vb * denom
        ww = vc * denom
        take(np.ones(len(P), dtype=bool), A + vv[:, None] * ab + ww[:, None] * ac)
    return res


def subdivide_triangles(tris: np.ndarray, tri_face: np.ndarray, max_edge: float, max_tris: int = 600_000) -> tuple[np.ndarray, np.ndarray]:
    """Longest-edge bisection until every edge <= max_edge (non-conforming; for distance queries)."""
    T, F = tris, tri_face
    for _ in range(40):
        e = np.stack([
            np.linalg.norm(T[:, 1] - T[:, 0], axis=1),
            np.linalg.norm(T[:, 2] - T[:, 1], axis=1),
            np.linalg.norm(T[:, 0] - T[:, 2], axis=1),
        ], axis=1)
        longest = e.max(axis=1)
        split = longest > max_edge
        if not split.any() or len(T) + split.sum() > max_tris:
            break
        keep_T, keep_F = T[~split], F[~split]
        S, SF = T[split], F[split]
        k = e[split].argmax(axis=1)
        order = np.array([[0, 1, 2], [1, 2, 0], [2, 0, 1]])[k]
        S = np.take_along_axis(S, order[:, :, None], axis=1)
        m = 0.5 * (S[:, 0] + S[:, 1])
        t1 = np.stack([S[:, 0], m, S[:, 2]], axis=1)
        t2 = np.stack([m, S[:, 1], S[:, 2]], axis=1)
        T = np.concatenate([keep_T, t1, t2])
        F = np.concatenate([keep_F, SF, SF])
    return T, F
