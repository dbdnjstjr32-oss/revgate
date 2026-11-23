"""Stage-3 Boolean subprocess (IMPLEMENTATION_BLUEPRINT §3.3 / §5.3).

Runs in its own interpreter so the 15 s hard timeout (BOOLEAN_TIMEOUT_SEC) can kill a runaway
OCCT boolean without touching the diff job:

    python -m src.boolean_job --base base.brep --target target_in_base_frame.brep --out DIR

writes DIR/removed.brep (Base - Target = material removed), DIR/added.brep (Target - Base =
material added) and DIR/boolean_result.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape
from OCP.TopTools import TopTools_ListOfShape

from .constants import BOOLEAN_FUZZY_MM
from .runner import die_with_parent


def read_brep(path: Path) -> TopoDS_Shape:
    shape = TopoDS_Shape()
    if not BRepTools.Read_s(shape, str(path), BRep_Builder()):
        raise RuntimeError(f"cannot read {path}")
    return shape


def as_solids(shape: TopoDS_Shape) -> TopoDS_Shape:
    """Closed shells without a solid wrapper are promoted to solids (needed by the Boolean)."""
    if TopExp_Explorer(shape, TopAbs_SOLID).More():
        return shape
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        mk = BRepBuilderAPI_MakeSolid(TopoDS.Shell_s(exp.Current()))
        if mk.IsDone():
            builder.Add(comp, mk.Solid())
        exp.Next()
    return comp


def cut(a: TopoDS_Shape, b: TopoDS_Shape, fuzzy: float) -> TopoDS_Shape:
    op = BRepAlgoAPI_Cut()
    args, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    args.Append(a)
    tools.Append(b)
    op.SetArguments(args)
    op.SetTools(tools)
    op.SetFuzzyValue(fuzzy)
    op.SetRunParallel(True)
    op.SetNonDestructive(True)
    op.Build()
    # (BOPAlgo_Options::HasErrors is not exposed by the OCP binding; IsDone() reflects it.)
    if not op.IsDone():
        raise RuntimeError("BRepAlgoAPI_Cut failed")
    res = op.Shape()
    if res.IsNull():
        raise RuntimeError("BRepAlgoAPI_Cut returned a null shape")
    return res


def main(argv: list[str] | None = None) -> int:
    die_with_parent()
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fuzzy", type=float, default=BOOLEAN_FUZZY_MM)
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    base = as_solids(read_brep(args.base))
    target = as_solids(read_brep(args.target))
    removed = cut(base, target, args.fuzzy)
    added = cut(target, base, args.fuzzy)
    args.out.mkdir(parents=True, exist_ok=True)
    BRepTools.Write_s(removed, str(args.out / "removed.brep"))
    BRepTools.Write_s(added, str(args.out / "added.brep"))
    result = {"status": "OK", "elapsed_ms": round((time.perf_counter() - t0) * 1e3, 1)}
    (args.out / "boolean_result.json").write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
