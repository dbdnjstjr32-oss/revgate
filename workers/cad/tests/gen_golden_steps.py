"""Golden STEP test pairs (IMPLEMENTATION_BLUEPRINT §7.1).

Deterministic parametric models built with OCP (BRepPrimAPI + BRepAlgoAPI_Cut + gp_Trsf) and
written through XDE (STEPCAFControl_Writer, AP214, mm) with face colours. The FILE_NAME timestamp
is pinned so the produced bytes — and therefore the SHA-256 used by hash-verified re-drop — are
reproducible.

    python tests/gen_golden_steps.py [--out tests/golden]

Layout:  <out>/<case>/rev_A.step, rev_B.step, case.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Callable

import numpy as np
from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_Transform
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeSphere
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Controller
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Face, TopoDS_Shape
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool

PINNED_TIMESTAMP = "2026-01-01T00:00:00"
PART_RGB = (0.62, 0.66, 0.72)
HOLE_RGB = (0.20, 0.45, 0.85)

BLOCK = (100.0, 60.0, 20.0)
# Ø3.30 through-holes; deliberately asymmetric so no block symmetry maps the pattern onto itself.
HOLES_XY = [(12.0, 10.0), (88.0, 12.0), (15.0, 48.0), (80.0, 50.0)]
CHANGED_HOLES = (1, 3)  # 01: these two become Ø4.20
D_M4_TAP = 3.30
D_M5_TAP = 4.20

# 03_transform_shift: B' = T_gen · B with R = Rz(37°)·Ry(0°)·Rx(90°) (extrinsic X→Y→Z), t = (120, -45, 80)
SHIFT_T = (120.0, -45.0, 80.0)
SHIFT_R_DEG = (90.0, 0.0, 37.0)


# ============================================================================================
# geometry helpers
# ============================================================================================
def make_box(x: float, y: float, z: float, dx: float, dy: float, dz: float) -> TopoDS_Shape:
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


def cut(a: TopoDS_Shape, b: TopoDS_Shape) -> TopoDS_Shape:
    op = BRepAlgoAPI_Cut(a, b)
    if not op.IsDone():
        raise RuntimeError("boolean cut failed")
    return op.Shape()


def fuse(a: TopoDS_Shape, b: TopoDS_Shape) -> TopoDS_Shape:
    op = BRepAlgoAPI_Fuse(a, b)
    if not op.IsDone():
        raise RuntimeError("boolean fuse failed")
    return op.Shape()


def through_hole(shape: TopoDS_Shape, x: float, y: float, diameter: float, z0: float = 0.0, height: float = BLOCK[2]) -> TopoDS_Shape:
    tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z0 - 1.0), gp_Dir(0, 0, 1)), diameter / 2.0, height + 2.0).Shape()
    return cut(shape, tool)


def top_pocket(shape: TopoDS_Shape, x0: float, y0: float, w: float, h: float, depth: float, top: float = BLOCK[2]) -> TopoDS_Shape:
    return cut(shape, make_box(x0, y0, top - depth, w, h, depth + 1.0))


def rotation_xyz_deg(rx: float, ry: float, rz: float) -> np.ndarray:
    ax, ay, az = (math.radians(v) for v in (rx, ry, rz))
    Rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    Ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    Rz = np.array([[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def rigid_matrix(r_deg: tuple[float, float, float], t: tuple[float, float, float]) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = rotation_xyz_deg(*r_deg)
    m[:3, 3] = t
    return m


def transform(shape: TopoDS_Shape, m: np.ndarray) -> TopoDS_Shape:
    trsf = gp_Trsf()
    trsf.SetValues(*(float(m[r, c]) for r in range(3) for c in range(4)))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def faces_of(shape: TopoDS_Shape) -> list[TopoDS_Face]:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, m)
    return [TopoDS.Face_s(m.FindKey(i)) for i in range(1, m.Extent() + 1)]


def is_cylinder(face: TopoDS_Face) -> bool:
    return BRepAdaptor_Surface(face, True).GetType() == GeomAbs_Cylinder


def volume(shape: TopoDS_Shape) -> float:
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return float(p.Mass())


# ============================================================================================
# STEP writing (XDE, colours, pinned header)
# ============================================================================================
_TS_RE = re.compile(r"(FILE_NAME\s*\(\s*'[^']*'\s*,\s*')(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(')")


def write_step(
    shape: TopoDS_Shape,
    path: Path,
    *,
    part_rgb: tuple[float, float, float] | None = PART_RGB,
    face_rgb: Callable[[TopoDS_Face], tuple[float, float, float] | None] | None = None,
    unit: str = "MM",
    name: str | None = None,
    doc_unit_m: float = 0.001,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Register the STEP statics first: setting them before the controller is initialised is silently undone.
    STEPControl_Controller.Init_s()
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    # Document length unit (metres per model unit). OCCT 7.8's XDE writer labels the file with
    # write.step.unit but does not rescale coordinates, so callers pass geometry already expressed
    # in `unit` with the matching doc_unit_m (see write_inch_block).
    XCAFDoc_DocumentTool.SetLengthUnit_s(doc, doc_unit_m)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    label = shape_tool.AddShape(shape, False)
    # Explicit product name: OCCT's default embeds a per-process counter, which breaks byte reproducibility.
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name or f"revgate-{path.parent.name}-{path.stem}"))
    if part_rgb is not None:
        color_tool.SetColor(label, Quantity_Color(*part_rgb, Quantity_TOC_RGB), XCAFDoc_ColorSurf)
    if face_rgb is not None:
        for face in faces_of(shape):
            rgb = face_rgb(face)
            if rgb is None:
                continue
            sub = shape_tool.AddSubShape(label, face)
            if not sub.IsNull():
                color_tool.SetColor(sub, Quantity_Color(*rgb, Quantity_TOC_RGB), XCAFDoc_ColorSurf)

    Interface_Static.SetCVal_s("write.step.unit", unit)
    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    writer.SetLayerMode(False)
    if not writer.Transfer(doc, STEPControl_AsIs):
        raise RuntimeError(f"STEP transfer failed for {path}")
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed for {path}")
    Interface_Static.SetCVal_s("write.step.unit", "MM")

    text = path.read_text(encoding="latin-1")
    text, n = _TS_RE.subn(lambda m: m.group(1) + PINNED_TIMESTAMP + m.group(3), text, count=1)
    if n != 1:
        raise RuntimeError("could not pin FILE_NAME timestamp")
    path.write_bytes(text.replace("\r\n", "\n").encode("latin-1"))
    return path


def hole_colors(face: TopoDS_Face) -> tuple[float, float, float] | None:
    return HOLE_RGB if is_cylinder(face) else None


# ============================================================================================
# the five golden cases
# ============================================================================================
def block_with_holes(diameters: list[float]) -> TopoDS_Shape:
    s = make_box(0, 0, 0, *BLOCK)
    for (x, y), d in zip(HOLES_XY, diameters):
        s = through_hole(s, x, y, d)
    return s


def case_01() -> tuple[TopoDS_Shape, TopoDS_Shape, dict]:
    a = block_with_holes([D_M4_TAP] * 4)
    b = block_with_holes([D_M5_TAP if i in CHANGED_HOLES else D_M4_TAP for i in range(4)])
    meta = {
        "description": "100x60x20 block, 4x Ø3.30 through-holes; holes #1, #3 -> Ø4.20",
        "expect": {"HOLE_DIAMETER": 2, "from_mm": D_M4_TAP, "to_mm": D_M5_TAP, "hint": "M4 → M5"},
        "changed_hole_xy": [HOLES_XY[i] for i in CHANGED_HOLES],
    }
    return a, b, meta


def case_02() -> tuple[TopoDS_Shape, TopoDS_Shape, dict]:
    base = make_box(0, 0, 0, *BLOCK)
    a = top_pocket(base, 25.0, 15.0, 50.0, 30.0, 8.0)
    b = top_pocket(base, 25.0, 15.0, 50.0, 30.0, 10.5)
    meta = {
        "description": "centre pocket 50x30, depth 8.0 -> 10.5 (+2.5)",
        "expect": {"PLANAR_OFFSET": 1, "offset_mm": -2.5, "volume_delta_mm3": -3750.0},
    }
    return a, b, meta


def case_03() -> tuple[TopoDS_Shape, TopoDS_Shape, dict]:
    a, b01, _ = case_01()
    g = rigid_matrix(SHIFT_R_DEG, SHIFT_T)
    b = transform(b01, g)
    meta = {
        "description": "01 Rev B moved by t=(120,-45,80) mm and R=Rz(37)·Ry(0)·Rx(90) deg",
        "generation_transform_B": [float(v) for v in g.reshape(-1)],
        "expect": {"method": "AUTO_BREP_RANSAC", "confidence_min": 0.85, "rms_max_mm": 0.005, "HOLE_DIAMETER": 2},
    }
    return a, b, meta


def case_04() -> tuple[TopoDS_Shape, TopoDS_Shape, dict]:
    base = make_box(0, 0, 0, *BLOCK)
    a = top_pocket(base, 60.0, 15.0, 37.5, 30.0, 8.0)  # outer wall x=97.5..100 -> 2.5 mm
    b = top_pocket(base, 60.0, 15.0, 39.2, 30.0, 8.0)  # outer wall x=99.2..100 -> 0.8 mm
    meta = {
        "description": "pocket outer wall thickness 2.5 -> 0.8 mm",
        "expect": {"WALL_THICKNESS": "CRITICAL", "from_mm": 2.5, "to_mm": 0.8},
    }
    return a, b, meta


def case_05() -> tuple[TopoDS_Shape, TopoDS_Shape, dict]:
    a = make_box(0, 0, 0, 50.0, 50.0, 50.0)
    b = a
    for tool in (
        make_box(-1.0, 13.0, 13.0, 11.0, 24.0, 24.0),
        make_box(40.0, 13.0, 13.0, 11.0, 24.0, 24.0),
        make_box(13.0, -1.0, 13.0, 24.0, 11.0, 24.0),
        make_box(13.0, 40.0, 13.0, 24.0, 11.0, 24.0),
    ):
        b = cut(b, tool)
    meta = {
        "description": "symmetric 50^3 cube; 4 side faces get 24x24x10 pockets (matching area 33% < 40%)",
        "expect": {"status": "NEEDS_ALIGN", "confidence_max": 0.40},
    }
    return a, b, meta


CASES: dict[str, Callable[[], tuple[TopoDS_Shape, TopoDS_Shape, dict]]] = {
    "01_hole_diam": case_01,
    "02_pocket_offset": case_02,
    "03_transform_shift": case_03,
    "04_thin_wall": case_04,
    "05_symmetric_needs_align": case_05,
}


def generate_case(name: str, out_root: Path) -> dict:
    a, b, meta = CASES[name]()
    case_dir = out_root / name
    write_step(a, case_dir / "rev_A.step", face_rgb=hole_colors)
    write_step(b, case_dir / "rev_B.step", face_rgb=hole_colors)
    meta = {"case": name, **meta, "volume_A_mm3": round(volume(a), 6), "volume_B_mm3": round(volume(b), 6)}
    (case_dir / "case.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return meta


def generate_all(out_root: Path) -> list[dict]:
    return [generate_case(name, out_root) for name in CASES]


# ============================================================================================
# extra fixtures used by the robustness tests (not part of the golden five)
# ============================================================================================
def write_inch_block(path: Path) -> Path:
    """1 x 2 x 0.5 in block (= 25.4 x 50.8 x 12.7 mm) stored in an INCH-unit STEP file.

    OCCT 7.8 STEP writers (XDE and plain) put write.step.unit=INCH into the unit context but do
    NOT rescale lengths, so the geometry is handed over already expressed in inches. The guard
    below fails loudly if a future OCCT starts converting (fixture would silently change).
    """
    path = write_step(make_box(0, 0, 0, 1.0, 2.0, 0.5), path, unit="INCH")
    text = path.read_text(encoding="latin-1")
    if "CONVERSION_BASED_UNIT('INCH'" not in text or "(0.,0.,0.5)" not in text:
        raise RuntimeError("INCH fixture is not expressed in inches (OCCT writer behaviour changed)")
    return path


def open_box_shell(w: float, d: float, h: float) -> TopoDS_Shape:
    """Box without its top face, sewn into a single open shell."""
    sewing = BRepBuilderAPI_Sewing(1e-6)
    for face in faces_of(make_box(0, 0, 0, w, d, h)):
        p = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, p)
        if abs(p.CentreOfMass().Z() - h) < 1e-9:
            continue
        sewing.Add(face)
    sewing.Perform()
    return sewing.SewedShape()


OPEN_SHELL_WALL_SHIFT_MM = 0.5


def write_open_shell_pair(out_dir: Path) -> tuple[Path, Path]:
    """Open (top-less) 100x60x20 box; Rev B moves the +X wall outward by 0.5 mm."""
    a = write_step(open_box_shell(100.0, 60.0, 20.0), out_dir / "open_A.step")
    b = write_step(open_box_shell(100.0 + OPEN_SHELL_WALL_SHIFT_MM, 60.0, 20.0), out_dir / "open_B.step")
    return a, b


def tri_sphere() -> TopoDS_Shape:
    s = BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 0), 20.0).Shape()
    s = fuse(s, BRepPrimAPI_MakeSphere(gp_Pnt(24.0, 0, 0), 14.0).Shape())
    s = fuse(s, BRepPrimAPI_MakeSphere(gp_Pnt(0, 21.0, 6.0), 10.0).Shape())
    return s


FREEFORM_T = ((20.0, -35.0, 110.0), (14.0, -6.0, 9.0))  # (R deg xyz, t mm)


def write_freeform_pair(out_dir: Path) -> tuple[Path, Path, np.ndarray]:
    a = tri_sphere()
    g = rigid_matrix(*FREEFORM_T)
    b = transform(a, g)
    return write_step(a, out_dir / "free_A.step"), write_step(b, out_dir / "free_B.step"), g


def write_many_solids(path: Path, n: int = 11) -> Path:
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for i in range(n):
        builder.Add(comp, make_box(i * 15.0, 0, 0, 10.0, 10.0, 10.0))
    return write_step(comp, path)


EMPTY_STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('empty'),'2;1');
FILE_NAME('empty','2026-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));
ENDSEC;
DATA;
ENDSEC;
END-ISO-10303-21;
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "golden"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    for meta in generate_all(out):
        print(f"[golden] {meta['case']:<26} A={meta['volume_A_mm3']:>12.3f} mm3  B={meta['volume_B_mm3']:>12.3f} mm3  - {meta['description']}")
    print(f"[golden] wrote {len(CASES)} pairs to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
