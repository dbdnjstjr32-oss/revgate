"""STEP ingest (IMPLEMENTATION_BLUEPRINT §5.1 / §5.3 / §2.2).

load_revision()  — shared by the ingest job and the diff job so both see identical face ids:
  1. Interface_Static.SetCVal_s("xstep.cascade.unit", "MM") before reading (mm everywhere)
  2. STEPCAFControl_Reader (XDE) -> shape + original face colours; reader.FileUnits() -> source_unit
  3. limits (TOO_MANY_SOLIDS / TOO_MANY_FACES), BRepCheck_Analyzer + watertight check,
     ShapeFix_Shape healing once when needed (HEALING_APPLIED / OPEN_SHELL_ONLY)
  4. face ids: TopTools_IndexedMapOfShape + TopExp.MapShapes_s(shape, TopAbs_FACE) -> face_id = idx - 1
  5. lod0 tessellation + per-face analytical descriptors (FaceRecord)

run_ingest_job() — lod0.glb -> BRepTools.Clean_s -> lod1.glb -> analytical_meta.json
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.GeomAbs import (
    GeomAbs_BezierSurface,
    GeomAbs_BSplineSurface,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Pnt, gp_Vec
from OCP.IFSelect import IFSelect_RetDone, IFSelect_RetVoid
from OCP.Interface import Interface_Static
from OCP.Quantity import Quantity_Color
from OCP.ShapeFix import ShapeFix_Shape
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.STEPControl import STEPControl_Controller
from OCP.TCollection import TCollection_ExtendedString
from OCP.TColStd import TColStd_SequenceOfAsciiString
from OCP.TDF import TDF_Label, TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Face, TopoDS_Shape
from OCP.BRep import BRep_Builder
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_IndexedMapOfShape
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import (
    XCAFDoc_ColorGen,
    XCAFDoc_ColorSurf,
    XCAFDoc_ColorTool,
    XCAFDoc_DocumentTool,
    XCAFDoc_ShapeTool,
)

from . import constants as C
from .errors import CadDomainError
from .glb_builder import build_glb
from .tessellate import MeshData, clean_triangulation, extract_mesh, mesh_shape

SURFACE_TYPES = ("PLANE", "CYLINDER", "CONE", "SPHERE", "TORUS", "BSPLINE", "OTHER")


# ============================================================================================
# Data model
# ============================================================================================
@dataclass
class FaceRecord:
    """Analytical description of one B-Rep face (revision-local coordinates, mm)."""

    face_id: int
    stype: str
    area: float
    centroid: np.ndarray
    bbox_min: np.ndarray
    bbox_max: np.ndarray
    sample_point: np.ndarray
    sample_normal: np.ndarray  # outward
    adjacent: list[int] = field(default_factory=list)
    tri_range: tuple[int, int] = (0, 0)
    color: tuple[float, float, float] = C.DEFAULT_FACE_RGB
    # PLANE
    normal: np.ndarray | None = None
    origin: np.ndarray | None = None  # plane point / axis point at mid-height / sphere & torus centre
    # CYLINDER / CONE / TORUS
    axis_dir: np.ndarray | None = None
    radius: float | None = None  # cylinder R, sphere R, torus major R, cone r_max
    radius2: float | None = None  # cone r_min, torus minor r
    height: float | None = None
    sweep_deg: float | None = None
    is_internal: bool | None = None
    half_angle_deg: float | None = None
    apex: np.ndarray | None = None
    axial_range: tuple[float, float] | None = None  # (s_min, s_max) along axis_dir from origin

    @property
    def diag(self) -> float:
        return float(np.linalg.norm(self.bbox_max - self.bbox_min))


@dataclass
class Revision:
    revision_id: str
    step_path: str
    sha256: str
    shape: TopoDS_Shape
    faces: list[TopoDS_Face]
    face_map: TopTools_IndexedMapOfShape
    records: list[FaceRecord]
    source_unit: str
    warnings: list[str]
    is_watertight: bool
    volume_mm3: float
    bbox_min: np.ndarray
    bbox_max: np.ndarray
    lod0: MeshData
    n_solids: int
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def bbox_diag(self) -> float:
        return float(np.linalg.norm(self.bbox_max - self.bbox_min))

    @property
    def total_area(self) -> float:
        return float(sum(r.area for r in self.records))

    @property
    def thread_bomb(self) -> bool:
        return "HELICAL_THREADS_DETECTED" in self.warnings


# ============================================================================================
# Helpers
# ============================================================================================
def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pnt(p: gp_Pnt) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()], dtype=np.float64)


def _dir(d) -> np.ndarray:
    v = np.array([d.X(), d.Y(), d.Z()], dtype=np.float64)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def canonical_dir(v: np.ndarray) -> np.ndarray:
    """Axis directions are sign-free: make the dominant component positive (deterministic)."""
    k = int(np.argmax(np.abs(v)))
    return -v if v[k] < 0 else v


def map_shapes(shape: TopoDS_Shape, kind) -> TopTools_IndexedMapOfShape:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, m)
    return m


def bbox_of(shape: TopoDS_Shape) -> tuple[np.ndarray, np.ndarray]:
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    if box.IsVoid():
        return np.zeros(3), np.zeros(3)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return np.array([xmin, ymin, zmin]), np.array([xmax, ymax, zmax])


def is_watertight(shape: TopoDS_Shape) -> bool:
    """Every face lives in a shell and every shell is closed (each edge shared by two faces)."""
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    has_shell = False
    while exp.More():
        has_shell = True
        if not BRep_Tool.IsClosed_s(exp.Current()):
            return False
        exp.Next()
    if not has_shell:
        return False
    free_faces = TopExp_Explorer(shape, TopAbs_FACE, TopAbs_SHELL)
    return not free_faces.More()


def shape_volume(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return abs(float(props.Mass()))


# ============================================================================================
# 1-2. STEP reading (XDE) + units + colours
# ============================================================================================
def _unit_from_names(names: list[str]) -> str:
    norm = [n.strip().lower() for n in names if n and n.strip()]
    if any("inch" in n for n in norm):
        return "INCH"
    if norm and all(n in ("millimetre", "millimeter", "mm") for n in norm):
        return "MM"
    return "UNKNOWN"


def _label_color(color_tool, label: TDF_Label) -> tuple[float, float, float] | None:
    # Label-based GetColor is static since OCCT 7.6 (OCP: GetColor_s).
    q = Quantity_Color()
    for ctype in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
        if XCAFDoc_ColorTool.GetColor_s(label, ctype, q):
            return (float(q.Red()), float(q.Green()), float(q.Blue()))
    return None


class _ColorIndex:
    """face (location-free) -> RGB with priority: face colour > solid/shell colour > part colour."""

    def __init__(self) -> None:
        self.keys = TopTools_IndexedMapOfShape()
        self.values: list[tuple[float, float, float]] = []
        self.prio: list[int] = []

    def put(self, shape: TopoDS_Shape, color: tuple[float, float, float], prio: int) -> None:
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            key = exp.Current().Located(TopLoc_Location())
            idx = self.keys.FindIndex(key)
            if idx == 0:
                self.keys.Add(key)
                self.values.append(color)
                self.prio.append(prio)
            elif prio >= self.prio[idx - 1]:
                self.values[idx - 1] = color
                self.prio[idx - 1] = prio
            exp.Next()

    def get(self, face: TopoDS_Shape) -> tuple[float, float, float]:
        idx = self.keys.FindIndex(face.Located(TopLoc_Location()))
        return self.values[idx - 1] if idx > 0 else C.DEFAULT_FACE_RGB


def _collect_colors(shape_tool, color_tool, free_labels: list[TDF_Label]) -> _ColorIndex:
    index = _ColorIndex()

    def visit(label: TDF_Label, inherited) -> None:
        color = _label_color(color_tool, label) or inherited
        if XCAFDoc_ShapeTool.IsAssembly_s(label):
            comps = TDF_LabelSequence()
            XCAFDoc_ShapeTool.GetComponents_s(label, comps, False)
            for i in range(1, comps.Length() + 1):
                comp = comps.Value(i)
                ref = TDF_Label()
                if XCAFDoc_ShapeTool.GetReferredShape_s(comp, ref):
                    visit(ref, _label_color(color_tool, comp) or color)
            return
        part_shape = XCAFDoc_ShapeTool.GetShape_s(label)
        if color is not None:
            index.put(part_shape, color, 1)
        subs = TDF_LabelSequence()
        XCAFDoc_ShapeTool.GetSubShapes_s(label, subs)
        for i in range(1, subs.Length() + 1):
            sub = subs.Value(i)
            sub_color = _label_color(color_tool, sub)
            if sub_color is None:
                continue
            sub_shape = XCAFDoc_ShapeTool.GetShape_s(sub)
            index.put(sub_shape, sub_color, 3 if sub_shape.ShapeType() == TopAbs_FACE else 2)

    for lab in free_labels:
        visit(lab, None)
    return index


def read_step(path: str) -> tuple[TopoDS_Shape, str, _ColorIndex]:
    """Return (shape in mm, source_unit, colour index)."""
    # Register the STEP statics before touching them (values set earlier are reset by Init).
    STEPControl_Controller.Init_s()
    Interface_Static.SetCVal_s("xstep.cascade.unit", C.CASCADE_UNIT)

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(False)
    reader.SetLayerMode(False)
    status = reader.ReadFile(str(path))
    if status == IFSelect_RetVoid:
        raise CadDomainError("EMPTY_GEOMETRY", "STEP file contains no data entities")
    if status != IFSelect_RetDone:
        raise CadDomainError("INVALID_STEP_SYNTAX", f"STEP reader status {status}")

    lengths, angles, solid_angles = (TColStd_SequenceOfAsciiString() for _ in range(3))
    reader.ChangeReader().FileUnits(lengths, angles, solid_angles)
    unit_names = [lengths.Value(i).ToCString() for i in range(1, lengths.Length() + 1)]
    source_unit = _unit_from_names(unit_names)

    if reader.ChangeReader().NbRootsForTransfer() == 0:
        raise CadDomainError("EMPTY_GEOMETRY", "STEP file has no transferable roots")
    if not reader.Transfer(doc):
        raise CadDomainError("INVALID_STEP_SYNTAX", "STEP transfer failed")

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(labels)
    free = [labels.Value(i) for i in range(1, labels.Length() + 1)]
    shapes = [XCAFDoc_ShapeTool.GetShape_s(lab) for lab in free]
    shapes = [s for s in shapes if not s.IsNull()]
    if not shapes:
        raise CadDomainError("EMPTY_GEOMETRY", "no shapes after transfer")
    if len(shapes) == 1:
        shape = shapes[0]
    else:
        comp = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(comp)
        for s in shapes:
            builder.Add(comp, s)
        shape = comp
    colors = _collect_colors(shape_tool, color_tool, free)
    return shape, source_unit, colors


# ============================================================================================
# 3. validity / healing
# ============================================================================================
def heal_if_needed(shape: TopoDS_Shape, warnings: list[str]) -> tuple[TopoDS_Shape, bool]:
    valid = BRepCheck_Analyzer(shape, True).IsValid()
    watertight = is_watertight(shape)
    if not valid or not watertight:
        fixer = ShapeFix_Shape(shape)
        fixer.SetPrecision(C.HEALING_PRECISION_MM)
        fixer.SetMaxTolerance(C.HEALING_MAX_TOLERANCE_MM)
        fixer.Perform()
        shape = fixer.Shape()
        warnings.append("HEALING_APPLIED")
        watertight = is_watertight(shape)
    if not watertight:
        warnings.append("OPEN_SHELL_ONLY")
    return shape, watertight


def check_limits(shape: TopoDS_Shape) -> tuple[int, int]:
    n_solids = map_shapes(shape, TopAbs_SOLID).Extent()
    if n_solids > C.MAX_SOLIDS_MVP:
        raise CadDomainError("TOO_MANY_SOLIDS", f"{n_solids} solids > {C.MAX_SOLIDS_MVP}", solids=n_solids)
    n_faces = map_shapes(shape, TopAbs_FACE).Extent()
    if n_faces == 0:
        raise CadDomainError("EMPTY_GEOMETRY", "shape has no faces")
    if n_faces > C.MAX_FACES_HARD:
        raise CadDomainError("TOO_MANY_FACES", f"{n_faces} faces > {C.MAX_FACES_HARD}", faces=n_faces)
    return n_solids, n_faces


# ============================================================================================
# 4. face ids + adjacency
# ============================================================================================
def face_index(shape: TopoDS_Shape) -> tuple[TopTools_IndexedMapOfShape, list[TopoDS_Face]]:
    """Deterministic 0-based face ids: face_id = (1-based IndexedMap index) - 1."""
    fmap = map_shapes(shape, TopAbs_FACE)
    faces = [TopoDS.Face_s(fmap.FindKey(i)) for i in range(1, fmap.Extent() + 1)]
    return fmap, faces


def face_adjacency(shape: TopoDS_Shape, fmap: TopTools_IndexedMapOfShape) -> list[list[int]]:
    """Attributed Adjacency Graph: faces sharing a (non-degenerate) edge."""
    n = fmap.Extent()
    adj: list[set[int]] = [set() for _ in range(n)]
    emap = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, emap)
    for i in range(1, emap.Extent() + 1):
        edge = TopoDS.Edge_s(emap.FindKey(i))
        if BRep_Tool.Degenerated_s(edge):
            continue
        ids = sorted({fmap.FindIndex(f) - 1 for f in emap.FindFromIndex(i)} - {-1})
        for a in ids:
            for b in ids:
                if a != b:
                    adj[a].add(b)
    return [sorted(s) for s in adj]


# ============================================================================================
# 5. analytical descriptors
# ============================================================================================
def _sample_on_face(face: TopoDS_Face, adaptor: BRepAdaptor_Surface, mesh: MeshData, fi: int,
                    centroid: np.ndarray) -> tuple[np.ndarray, np.ndarray, tuple[float, float] | None]:
    """A point/normal guaranteed to lie on the (trimmed) face: the mesh triangle closest to the
    area centroid. Returns (point, outward normal, uv or None)."""
    start, count = mesh.face_tri_range[fi]
    if count > 0:
        tris = mesh.indices[start : start + count]
        p = mesh.positions
        cent = (p[tris[:, 0]] + p[tris[:, 1]] + p[tris[:, 2]]) / 3.0
        k = int(np.argmin(np.linalg.norm(cent - centroid, axis=1)))
        n = mesh.normals[tris[k]].sum(axis=0)
        n /= max(np.linalg.norm(n), 1e-12)
        uv = None
        face_uv = mesh.face_uv[fi]
        if face_uv is not None:
            v0 = mesh.face_vertex_range[fi][0]
            uvk = face_uv[tris[k] - v0].mean(axis=0)
            uv = (float(uvk[0]), float(uvk[1]))
        return cent[k], n, uv
    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
    u, v = 0.5 * (umin + umax), 0.5 * (vmin + vmax)
    P, D1U, D1V = gp_Pnt(), gp_Vec(), gp_Vec()
    adaptor.D1(u, v, P, D1U, D1V)
    n = np.cross(_dir(D1U), _dir(D1V))
    n /= max(np.linalg.norm(n), 1e-12)
    if face.Orientation() == TopAbs_REVERSED:
        n = -n
    return _pnt(P), n, (u, v)


def build_records(faces: list[TopoDS_Face], adjacency: list[list[int]], mesh: MeshData,
                  colors: _ColorIndex) -> list[FaceRecord]:
    records: list[FaceRecord] = []
    for fi, face in enumerate(faces):
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        area = float(props.Mass())
        centroid = _pnt(props.CentreOfMass())
        bmin, bmax = bbox_of(face)
        adaptor = BRepAdaptor_Surface(face, True)
        stype_occ = adaptor.GetType()
        sp, sn, _uv = _sample_on_face(face, adaptor, mesh, fi, centroid)
        reversed_ = face.Orientation() == TopAbs_REVERSED
        rec = FaceRecord(
            face_id=fi, stype="OTHER", area=area, centroid=centroid, bbox_min=bmin, bbox_max=bmax,
            sample_point=sp, sample_normal=sn, adjacent=adjacency[fi], tri_range=mesh.face_tri_range[fi],
            color=colors.get(face),
        )
        umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)

        if stype_occ == GeomAbs_Plane:
            pos = adaptor.Plane().Position()
            n = np.cross(_dir(pos.XDirection()), _dir(pos.YDirection()))  # = D1U x D1V
            n /= np.linalg.norm(n)
            if reversed_:
                n = -n
            loc = _pnt(pos.Location())
            rec.stype = "PLANE"
            rec.normal = n
            rec.origin = centroid - np.dot(centroid - loc, n) * n
            rec.sample_normal = n
        elif stype_occ == GeomAbs_Cylinder:
            cyl = adaptor.Cylinder()
            ax = cyl.Axis()
            d = _dir(ax.Direction())
            loc = _pnt(ax.Location())
            mid = loc + d * 0.5 * (vmin + vmax)
            radial = (sp - mid) - np.dot(sp - mid, d) * d
            rec.stype = "CYLINDER"
            rec.radius = float(cyl.Radius())
            rec.height = float(abs(vmax - vmin))
            rec.sweep_deg = float(min(360.0, math.degrees(abs(umax - umin))))
            rec.is_internal = bool(np.dot(sn, radial) < 0.0)
            dc = canonical_dir(d)
            rec.axis_dir = dc
            rec.origin = mid
            half = 0.5 * rec.height
            rec.axial_range = (-half, half)
        elif stype_occ == GeomAbs_Cone:
            cone = adaptor.Cone()
            ax = cone.Axis()
            d = _dir(ax.Direction())
            loc = _pnt(ax.Location())
            alpha = float(cone.SemiAngle())
            r_ref = float(cone.RefRadius())
            radii = [abs(r_ref + v * math.sin(alpha)) for v in (vmin, vmax)]
            zs = [v * math.cos(alpha) for v in (vmin, vmax)]
            mid = loc + d * 0.5 * (zs[0] + zs[1])
            radial = (sp - mid) - np.dot(sp - mid, d) * d
            rec.stype = "CONE"
            rec.half_angle_deg = abs(math.degrees(alpha))
            rec.radius, rec.radius2 = max(radii), min(radii)
            rec.is_internal = bool(np.dot(sn, radial) < 0.0)
            rec.axis_dir = canonical_dir(d)
            rec.origin = mid
            rec.apex = _pnt(cone.Apex())
            rec.height = abs(zs[1] - zs[0])
            rec.axial_range = (-0.5 * rec.height, 0.5 * rec.height)
        elif stype_occ == GeomAbs_Sphere:
            sph = adaptor.Sphere()
            rec.stype = "SPHERE"
            rec.origin = _pnt(sph.Location())
            rec.radius = float(sph.Radius())
        elif stype_occ == GeomAbs_Torus:
            tor = adaptor.Torus()
            rec.stype = "TORUS"
            rec.origin = _pnt(tor.Location())
            rec.axis_dir = canonical_dir(_dir(tor.Axis().Direction()))
            rec.radius = float(tor.MajorRadius())
            rec.radius2 = float(tor.MinorRadius())
        elif stype_occ in (GeomAbs_BSplineSurface, GeomAbs_BezierSurface):
            rec.stype = "BSPLINE"
        records.append(rec)
    return records


def thread_bomb_detected(records: list[FaceRecord]) -> bool:
    n = len(records)
    if n < C.THREAD_BOMB_FACES:
        return False
    n_bspline = sum(1 for r in records if r.stype == "BSPLINE")
    n_small = sum(1 for r in records if r.area < C.THREAD_BOMB_SMALL_FACE_MM2)
    return n_bspline / n > C.THREAD_BOMB_BSPLINE_RATIO and n_small / n > C.THREAD_BOMB_SMALL_FACE_RATIO


# ============================================================================================
# Public entry points
# ============================================================================================
def load_revision(step_path: str, revision_id: str, expected_sha256: str | None = None) -> Revision:
    t0 = time.perf_counter()
    path = Path(step_path)
    if not path.is_file():
        raise CadDomainError("RAW_FILE_NOT_FOUND", f"{step_path} not found")
    digest = sha256_file(path)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        raise CadDomainError("SHA256_MISMATCH", "file hash does not match", expected=expected_sha256, actual=digest)

    timings: dict[str, float] = {}
    shape, source_unit, colors = read_step(str(path))
    timings["read_ms"] = (time.perf_counter() - t0) * 1e3

    warnings: list[str] = []
    if source_unit == "INCH":
        warnings.append("INCH_SOURCE_CONVERTED")
    n_solids, _ = check_limits(shape)
    t1 = time.perf_counter()
    shape, watertight = heal_if_needed(shape, warnings)
    n_solids, _ = check_limits(shape)
    timings["heal_ms"] = (time.perf_counter() - t1) * 1e3

    fmap, faces = face_index(shape)
    adjacency = face_adjacency(shape, fmap)
    bmin, bmax = bbox_of(shape)
    diag = float(np.linalg.norm(bmax - bmin))

    t2 = time.perf_counter()
    lin, ang = mesh_shape(shape, diag, "lod0")
    lod0 = extract_mesh(faces, linear_deflection=lin, angular_deflection=ang)
    timings["lod0_mesh_ms"] = (time.perf_counter() - t2) * 1e3

    t3 = time.perf_counter()
    records = build_records(faces, adjacency, lod0, colors)
    timings["analytics_ms"] = (time.perf_counter() - t3) * 1e3
    if thread_bomb_detected(records):
        warnings.append("HELICAL_THREADS_DETECTED")

    volume = shape_volume(shape) if watertight else 0.0
    return Revision(
        revision_id=revision_id, step_path=str(path), sha256=digest, shape=shape, faces=faces,
        face_map=fmap, records=records, source_unit=source_unit, warnings=warnings,
        is_watertight=watertight, volume_mm3=volume, bbox_min=bmin, bbox_max=bmax, lod0=lod0,
        n_solids=n_solids, timings_ms=timings,
    )


def _r(x: float, nd: int = 6) -> float:
    return round(float(x), nd) + 0.0  # + 0.0 folds -0.0 into 0.0


def _v(a, nd: int = 6) -> list[float]:
    return [_r(a[0], nd), _r(a[1], nd), _r(a[2], nd)]


def face_params(rec: FaceRecord) -> dict[str, Any]:
    if rec.stype == "PLANE":
        return {"type": "PLANE", "origin": _v(rec.origin), "normal": _v(rec.normal, 9)}
    if rec.stype == "CYLINDER":
        return {
            "type": "CYLINDER", "axis_origin": _v(rec.origin), "axis_dir": _v(rec.axis_dir, 9),
            "radius_mm": _r(rec.radius), "height_mm": _r(rec.height), "sweep_deg": _r(rec.sweep_deg, 4),
            "is_internal": bool(rec.is_internal),
        }
    if rec.stype == "CONE":
        return {
            "type": "CONE", "axis_origin": _v(rec.origin), "axis_dir": _v(rec.axis_dir, 9),
            "half_angle_deg": _r(rec.half_angle_deg), "r_min": _r(rec.radius2), "r_max": _r(rec.radius),
            "is_internal": bool(rec.is_internal),
        }
    return {"type": "OTHER", "sample_normal": _v(rec.sample_normal, 9)}


def analytical_meta(rev: Revision) -> dict[str, Any]:
    faces = []
    for rec in rev.records:
        faces.append({
            "face_id": rec.face_id,
            "surface_type": rec.stype,
            "area_mm2": _r(rec.area),
            "centroid": _v(rec.centroid),
            "bbox_min": _v(rec.bbox_min),
            "bbox_max": _v(rec.bbox_max),
            "lod0_tri_range": [int(rec.tri_range[0]), int(rec.tri_range[1])],
            "adjacent_face_ids": list(rec.adjacent),
            "params": face_params(rec),
        })
    return {
        "schema_version": C.SCHEMA_VERSION,
        "revision_id": rev.revision_id,
        "source_unit": rev.source_unit,
        "summary": {
            "bbox_min": _v(rev.bbox_min),
            "bbox_max": _v(rev.bbox_max),
            "volume_mm3": _r(rev.volume_mm3),
            "face_count": len(rev.records),
            "is_watertight": bool(rev.is_watertight),
            "warnings": list(rev.warnings),
        },
        "faces": faces,
    }


def vertex_colors(mesh: MeshData, records: list[FaceRecord]) -> np.ndarray:
    palette = np.array([r.color for r in records], dtype=np.float64).reshape(-1, 3)
    return palette[mesh.vertex_face] if len(mesh.vertex_face) else np.zeros((0, 3))


def mesh_to_glb(mesh: MeshData, records: list[FaceRecord], *, name: str, lod: str) -> bytes:
    return build_glb(
        mesh.positions, mesh.normals, vertex_colors(mesh, records), mesh.vertex_face, mesh.indices,
        name=name,
        extras={
            "lod": lod, "face_count": len(records), "triangle_count": mesh.n_triangles,
            "linear_deflection_mm": _r(mesh.linear_deflection), "angular_deflection_rad": _r(mesh.angular_deflection),
        },
    )


def validate_meta(meta: dict[str, Any]) -> None:
    """SSOT gate: the generated Pydantic model must accept what we are about to publish."""
    from revgate_contracts import AnalyticalMeta

    AnalyticalMeta.model_validate(meta)


def run_ingest_job(spec: dict[str, Any]) -> dict[str, Any]:
    """Job type "ingest": STEP -> lod0.glb, lod1.glb, analytical_meta.json in spec["out_dir"]."""
    t0 = time.perf_counter()
    out_dir = Path(spec["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    rev = load_revision(spec["step_path"], spec["revision_id"], spec.get("expected_sha256"))

    t1 = time.perf_counter()
    lod0_bytes = mesh_to_glb(rev.lod0, rev.records, name=f"{rev.revision_id}-lod0", lod="lod0")
    meta = analytical_meta(rev)  # lod0_tri_range comes from the lod0 mesh above

    # Mandatory cache reset: without it OCCT re-uses the lod0 triangulation for lod1.
    clean_triangulation(rev.shape)
    lin1, ang1 = mesh_shape(rev.shape, rev.bbox_diag, "lod1")
    lod1 = extract_mesh(rev.faces, linear_deflection=lin1, angular_deflection=ang1)
    lod1_bytes = mesh_to_glb(lod1, rev.records, name=f"{rev.revision_id}-lod1", lod="lod1")
    t2 = time.perf_counter()

    validate_meta(meta)
    (out_dir / "lod0.glb").write_bytes(lod0_bytes)
    (out_dir / "lod1.glb").write_bytes(lod1_bytes)
    meta_text = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    (out_dir / "analytical_meta.json").write_text(meta_text, encoding="utf-8")

    timings = dict(rev.timings_ms)
    timings["glb_ms"] = (t2 - t1) * 1e3
    timings["total_ms"] = (time.perf_counter() - t0) * 1e3
    return {
        "status": "READY",
        "job_type": "ingest",
        "revision_id": rev.revision_id,
        "file_sha256": rev.sha256,
        "source_unit": rev.source_unit,
        "warnings": rev.warnings,
        "summary": meta["summary"],
        "artifacts": {
            "lod0_glb": "lod0.glb",
            "lod1_glb": "lod1.glb",
            "analytical_meta": "analytical_meta.json",
        },
        "stats": {
            "face_count": len(rev.records),
            "solid_count": rev.n_solids,
            "lod0_triangles": rev.lod0.n_triangles,
            "lod1_triangles": lod1.n_triangles,
            "lod0_bytes": len(lod0_bytes),
            "lod1_bytes": len(lod1_bytes),
        },
        "timings_ms": {k: round(v, 1) for k, v in timings.items()},
    }
