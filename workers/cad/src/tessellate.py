"""LOD tessellation (IMPLEMENTATION_BLUEPRINT §5.1).

* Deflection is absolute (isRelative=False) and derived from the bbox diagonal (get_lod_params).
* After the lod0 GLB is built the caller MUST run `clean_triangulation(shape)`
  (BRepTools.Clean_s) before meshing lod1 — otherwise OCCT happily re-uses the finer lod0 mesh.
* Triangles are emitted face by face in face_id order, so each face owns one contiguous
  triangle range [start_tri, tri_count] (analytical_meta.faces[i].lod0_tri_range).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepLib import BRepLib_ToolTriangulatedShape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Face, TopoDS_Shape


def get_lod_params(bbox_diag_mm: float, lod: str) -> tuple[float, float]:
    """returns (linear_deflection_mm, angular_deflection_rad), isRelative=False"""
    if lod == "lod0":
        return max(0.03, min(0.10, bbox_diag_mm * 0.0005)), math.radians(12.0)
    return max(0.15, min(0.50, bbox_diag_mm * 0.0025)), math.radians(25.0)


@dataclass
class MeshData:
    """A tessellated shape, concatenated face by face (face index == position in `faces`)."""

    positions: np.ndarray  # (N, 3) float64, mm, global coordinates
    normals: np.ndarray  # (N, 3) float64, unit, outward (face orientation applied)
    indices: np.ndarray  # (M, 3) int64, CCW seen from outside
    vertex_face: np.ndarray  # (N,) int64 face index per vertex
    tri_face: np.ndarray  # (M,) int64 face index per triangle
    face_tri_range: list[tuple[int, int]]  # (start_tri, tri_count) per face
    face_vertex_range: list[tuple[int, int]]  # (start_vertex, vertex_count) per face
    face_uv: list[np.ndarray | None] = field(default_factory=list)  # (n_i, 2) UV nodes per face
    linear_deflection: float = 0.0
    angular_deflection: float = 0.0

    @property
    def n_triangles(self) -> int:
        return int(self.indices.shape[0])

    def face_vertices(self, face_index: int) -> np.ndarray:
        start, count = self.face_vertex_range[face_index]
        return self.positions[start : start + count]


def mesh_shape(shape: TopoDS_Shape, bbox_diag_mm: float, lod: str) -> tuple[float, float]:
    """Triangulate `shape` in place with the blueprint LOD parameters."""
    lin, ang = get_lod_params(bbox_diag_mm, lod)
    # Constructor performs the meshing (isRelative=False, isInParallel=True).
    BRepMesh_IncrementalMesh(shape, lin, False, ang, True)
    return lin, ang


def clean_triangulation(shape: TopoDS_Shape) -> None:
    """Drop cached triangulations (mandatory between lod0 and lod1)."""
    BRepTools.Clean_s(shape)


def _trsf_matrix(loc: TopLoc_Location) -> np.ndarray | None:
    if loc.IsIdentity():
        return None
    trsf = loc.Transformation()
    return np.array([[trsf.Value(r, c) for c in range(1, 5)] for r in range(1, 4)], dtype=np.float64)


def extract_mesh(faces: list[TopoDS_Face], *, linear_deflection: float = 0.0, angular_deflection: float = 0.0) -> MeshData:
    """Collect the triangulation of every face (faces must already be meshed)."""
    pos_chunks: list[np.ndarray] = []
    nrm_chunks: list[np.ndarray] = []
    idx_chunks: list[np.ndarray] = []
    vface_chunks: list[np.ndarray] = []
    tface_chunks: list[np.ndarray] = []
    tri_ranges: list[tuple[int, int]] = []
    vtx_ranges: list[tuple[int, int]] = []
    uvs: list[np.ndarray | None] = []
    v_off = 0
    t_off = 0

    for fi, face in enumerate(faces):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None or tri.NbTriangles() == 0:
            tri_ranges.append((t_off, 0))
            vtx_ranges.append((v_off, 0))
            uvs.append(None)
            continue

        n_nodes = tri.NbNodes()
        n_tris = tri.NbTriangles()
        nodes = np.empty((n_nodes, 3), dtype=np.float64)
        for i in range(1, n_nodes + 1):
            p = tri.Node(i)
            nodes[i - 1] = (p.X(), p.Y(), p.Z())

        if not tri.HasNormals():
            BRepLib_ToolTriangulatedShape.ComputeNormals_s(face, tri)
        normals = np.empty((n_nodes, 3), dtype=np.float64)
        for i in range(1, n_nodes + 1):
            d = tri.Normal(i)
            normals[i - 1] = (d.X(), d.Y(), d.Z())

        uv = None
        if tri.HasUVNodes():
            uv = np.empty((n_nodes, 2), dtype=np.float64)
            for i in range(1, n_nodes + 1):
                q = tri.UVNode(i)
                uv[i - 1] = (q.X(), q.Y())

        tris = np.empty((n_tris, 3), dtype=np.int64)
        for i in range(1, n_tris + 1):
            tris[i - 1] = tri.Triangle(i).Get()
        tris -= 1

        m = _trsf_matrix(loc)
        if m is not None:
            nodes = nodes @ m[:, :3].T + m[:, 3]
            normals = normals @ m[:, :3].T
            if np.linalg.det(m[:, :3]) < 0:  # mirrored instance keeps CCW outward winding
                tris = tris[:, [0, 2, 1]]

        if face.Orientation() == TopAbs_REVERSED:
            normals = -normals
            tris = tris[:, [0, 2, 1]]

        lens = np.linalg.norm(normals, axis=1)
        bad = lens < 1e-12
        lens[bad] = 1.0
        normals = normals / lens[:, None]
        if bad.any():  # singular points (poles/apex): fall back to adjacent triangle normals
            normals[bad] = _fallback_vertex_normals(nodes, tris, np.flatnonzero(bad))

        pos_chunks.append(nodes)
        nrm_chunks.append(normals)
        idx_chunks.append(tris + v_off)
        vface_chunks.append(np.full(n_nodes, fi, dtype=np.int64))
        tface_chunks.append(np.full(n_tris, fi, dtype=np.int64))
        tri_ranges.append((t_off, n_tris))
        vtx_ranges.append((v_off, n_nodes))
        uvs.append(uv)
        v_off += n_nodes
        t_off += n_tris

    def _cat(chunks: list[np.ndarray], shape: tuple[int, ...], dtype) -> np.ndarray:
        return np.concatenate(chunks) if chunks else np.zeros(shape, dtype=dtype)

    return MeshData(
        positions=_cat(pos_chunks, (0, 3), np.float64),
        normals=_cat(nrm_chunks, (0, 3), np.float64),
        indices=_cat(idx_chunks, (0, 3), np.int64),
        vertex_face=_cat(vface_chunks, (0,), np.int64),
        tri_face=_cat(tface_chunks, (0,), np.int64),
        face_tri_range=tri_ranges,
        face_vertex_range=vtx_ranges,
        face_uv=uvs,
        linear_deflection=linear_deflection,
        angular_deflection=angular_deflection,
    )


def _fallback_vertex_normals(nodes: np.ndarray, tris: np.ndarray, vids: np.ndarray) -> np.ndarray:
    a, b, c = nodes[tris[:, 0]], nodes[tris[:, 1]], nodes[tris[:, 2]]
    fn = np.cross(b - a, c - a)
    out = np.zeros((len(vids), 3))
    for k, v in enumerate(vids):
        mask = (tris == v).any(axis=1)
        s = fn[mask].sum(axis=0)
        n = np.linalg.norm(s)
        out[k] = s / n if n > 0 else (0.0, 0.0, 1.0)
    return out


def tessellate_faces(shape: TopoDS_Shape, faces: list[TopoDS_Face], bbox_diag_mm: float, lod: str) -> MeshData:
    """mesh_shape + extract_mesh in one call."""
    lin, ang = mesh_shape(shape, bbox_diag_mm, lod)
    return extract_mesh(faces, linear_deflection=lin, angular_deflection=ang)


def triangle_areas(mesh: MeshData) -> np.ndarray:
    p = mesh.positions
    i = mesh.indices
    return 0.5 * np.linalg.norm(np.cross(p[i[:, 1]] - p[i[:, 0]], p[i[:, 2]] - p[i[:, 0]]), axis=1)


def sample_surface(mesh: MeshData, n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Area-weighted random points on the mesh -> (points (n,3), face index (n,), normals (n,3))."""
    if mesh.n_triangles == 0:
        return np.zeros((0, 3)), np.zeros(0, dtype=np.int64), np.zeros((0, 3))
    areas = triangle_areas(mesh)
    total = areas.sum()
    if total <= 0:
        return np.zeros((0, 3)), np.zeros(0, dtype=np.int64), np.zeros((0, 3))
    tri_ids = rng.choice(mesh.n_triangles, size=n, p=areas / total)
    r1 = np.sqrt(rng.random(n))
    r2 = rng.random(n)
    w0, w1, w2 = 1.0 - r1, r1 * (1.0 - r2), r1 * r2
    idx = mesh.indices[tri_ids]
    p = mesh.positions
    pts = w0[:, None] * p[idx[:, 0]] + w1[:, None] * p[idx[:, 1]] + w2[:, None] * p[idx[:, 2]]
    nm = mesh.normals
    nrm = w0[:, None] * nm[idx[:, 0]] + w1[:, None] * nm[idx[:, 1]] + w2[:, None] * nm[idx[:, 2]]
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1), 1e-12)[:, None]
    return pts, mesh.tri_face[tri_ids], nrm
