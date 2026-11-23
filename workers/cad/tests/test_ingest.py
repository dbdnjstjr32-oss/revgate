"""STEP ingest: mm normalization, deterministic face ids, analytical sidecar, _FACE_ID GLB (§2.1, §2.2, §5.1)."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pygltflib
import pytest

from src.glb_builder import FACE_ID_ATTRIBUTE, read_glb_arrays
from tests.gen_golden_steps import HOLE_RGB, HOLES_XY, PART_RGB

CASE = "01_hole_diam"


@pytest.fixture(scope="module")
def ingested(run_ingest):
    res, out = run_ingest(CASE, "A")
    assert res.ok, (res.error_code, res.message, res.stderr_tail)
    meta = json.loads((out / "analytical_meta.json").read_text("utf-8"))
    return res, out, meta


def test_outputs_and_contract(ingested, validators):
    from revgate_contracts import AnalyticalMeta

    res, out, meta = ingested
    assert res.status == "READY"
    assert {p.name for p in out.iterdir()} >= {"lod0.glb", "lod1.glb", "analytical_meta.json"}
    validators["analytical_meta"].validate(meta)
    AnalyticalMeta.model_validate(meta)
    assert meta["source_unit"] == "MM"
    s = meta["summary"]
    assert s["face_count"] == 10 and s["is_watertight"] is True and s["warnings"] == []
    assert s["bbox_min"] == [0.0, 0.0, 0.0] and s["bbox_max"] == [100.0, 60.0, 20.0]
    expected_volume = 100 * 60 * 20 - 4 * np.pi * 1.65**2 * 20
    assert s["volume_mm3"] == pytest.approx(expected_volume, abs=1e-3)


def test_face_ids_follow_indexed_map_order(ingested, golden_dir):
    """face_id = (TopTools_IndexedMapOfShape index) - 1, independent of the job process."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    from src.ingest import read_step

    _res, _out, meta = ingested
    shape, _unit, _colors = read_step(str(golden_dir / CASE / "rev_A.step"))
    fmap = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, fmap)
    assert fmap.Extent() == len(meta["faces"])
    for idx in range(1, fmap.Extent() + 1):
        face = meta["faces"][idx - 1]
        assert face["face_id"] == idx - 1
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(fmap.FindKey(idx), props)
        c = props.CentreOfMass()
        assert face["area_mm2"] == pytest.approx(props.Mass(), abs=1e-6)
        assert face["centroid"] == pytest.approx([c.X(), c.Y(), c.Z()], abs=1e-6)


def test_analytic_params(ingested):
    _res, _out, meta = ingested
    faces = meta["faces"]
    planes = [f for f in faces if f["surface_type"] == "PLANE"]
    cyls = [f for f in faces if f["surface_type"] == "CYLINDER"]
    assert len(planes) == 6 and len(cyls) == 4
    # outward unit normals (face orientation applied): each normal points away from the block centre
    centre = np.array([50.0, 30.0, 10.0])
    for f in planes:
        n = np.array(f["params"]["normal"])
        assert np.linalg.norm(n) == pytest.approx(1.0)
        assert np.dot(n, np.array(f["params"]["origin"]) - centre) > 0
    top = next(f for f in planes if f["params"]["normal"] == [0.0, 0.0, 1.0])
    assert top["params"]["origin"][2] == 20.0
    xy = sorted(tuple(f["params"]["axis_origin"][:2]) for f in cyls)
    assert xy == sorted(HOLES_XY)
    for f in cyls:
        p = f["params"]
        assert p["radius_mm"] == pytest.approx(1.65, abs=1e-9)
        assert p["height_mm"] == pytest.approx(20.0, abs=1e-9)
        assert p["sweep_deg"] == pytest.approx(360.0)
        assert p["is_internal"] is True
        assert p["axis_dir"] == [0.0, 0.0, 1.0]
        # AAG: a through-hole touches exactly the top and bottom faces
        adj_normals = sorted(tuple(faces[k]["params"]["normal"]) for k in f["adjacent_face_ids"])
        assert adj_normals == [(0.0, 0.0, -1.0), (0.0, 0.0, 1.0)]


def test_glb_single_primitive_with_face_id_and_colors(ingested):
    _res, out, meta = ingested
    data = (out / "lod0.glb").read_bytes()
    gltf = pygltflib.GLTF2.load_from_bytes(data)
    assert len(gltf.meshes) == 1 and len(gltf.meshes[0].primitives) == 1
    prim = gltf.meshes[0].primitives[0]
    fid_acc = gltf.accessors[getattr(prim.attributes, FACE_ID_ATTRIBUTE)]
    assert fid_acc.componentType == pygltflib.FLOAT and fid_acc.type == pygltflib.SCALAR
    assert prim.attributes.COLOR_0 is not None and prim.attributes.NORMAL is not None
    assert gltf.asset.extras["revgate"]["unit"] == "mm" and gltf.asset.extras["revgate"]["up_axis"] == "Z"

    arr = read_glb_arrays(data)
    fid = arr[FACE_ID_ATTRIBUTE]
    assert np.array_equal(fid, np.round(fid)), "face ids must be exact integers in FLOAT32"
    tris = arr["indices"]
    # lod0_tri_range partitions the index buffer, face by face, in face_id order
    start = 0
    for f in meta["faces"]:
        s, n = f["lod0_tri_range"]
        assert s == start and n > 0
        owner = fid[tris[s : s + n]]
        assert np.all(owner == f["face_id"]), f"triangles of face {f['face_id']} carry a foreign _FACE_ID"
        start += n
    assert start == len(tris)
    # COLOR_0 keeps the STEP face colours (holes blue, part grey)
    col = arr["COLOR_0"]
    for f in meta["faces"]:
        rgb = HOLE_RGB if f["surface_type"] == "CYLINDER" else PART_RGB
        s, n = f["lod0_tri_range"]
        verts = np.unique(tris[s : s + n])
        assert np.allclose(col[verts], rgb, atol=1e-6)
    # unit normals, winding consistent with them (CCW seen from outside)
    nrm = arr["NORMAL"]
    assert np.allclose(np.linalg.norm(nrm, axis=1), 1.0, atol=1e-5)
    p = arr["POSITION"].astype(np.float64)
    gn = np.cross(p[tris[:, 1]] - p[tris[:, 0]], p[tris[:, 2]] - p[tris[:, 0]])
    agree = np.einsum("ij,ij->i", gn, nrm[tris].sum(axis=1)) > 0
    assert agree.mean() > 0.99


def test_lod1_is_remeshed_after_clean(ingested):
    res, out, meta = ingested
    lod0 = read_glb_arrays((out / "lod0.glb").read_bytes())
    lod1 = read_glb_arrays((out / "lod1.glb").read_bytes())
    assert 0 < len(lod1["indices"]) < len(lod0["indices"])
    assert set(np.unique(lod1[FACE_ID_ATTRIBUTE]).astype(int)) == set(range(len(meta["faces"])))
    assert res.result["stats"]["lod1_triangles"] == len(lod1["indices"])


def test_clean_is_what_makes_lod1_coarser(golden_dir):
    """Without BRepTools.Clean_s OCCT re-uses the lod0 triangulation for lod1 (blueprint §5.1)."""
    from src.ingest import face_index, read_step
    from src.tessellate import clean_triangulation, extract_mesh, mesh_shape

    shape, _u, _c = read_step(str(golden_dir / CASE / "rev_A.step"))
    _fmap, faces = face_index(shape)
    mesh_shape(shape, 116.62, "lod0")
    n0 = extract_mesh(faces).n_triangles
    mesh_shape(shape, 116.62, "lod1")
    n_reused = extract_mesh(faces).n_triangles
    clean_triangulation(shape)
    mesh_shape(shape, 116.62, "lod1")
    n1 = extract_mesh(faces).n_triangles
    assert n_reused == n0 and n1 < n0


def test_ingest_is_deterministic(run_ingest, golden_dir, out_root):
    from src.runner import run_job

    _res, out = run_ingest(CASE, "A")
    again = out_root / "ingest" / "determinism"
    res2 = run_job({"job_type": "ingest", "revision_id": f"rev_{CASE}_A", "step_path": str(golden_dir / CASE / "rev_A.step"),
                    "out_dir": str(again)})
    assert res2.ok
    for name in ("analytical_meta.json", "lod0.glb", "lod1.glb"):
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == hashlib.sha256((again / name).read_bytes()).hexdigest(), name


def test_golden_generator_is_byte_reproducible(golden_dir, tmp_path):
    from tests.gen_golden_steps import generate_case

    generate_case(CASE, tmp_path)
    for side in "AB":
        assert (tmp_path / CASE / f"rev_{side}.step").read_bytes() == (golden_dir / CASE / f"rev_{side}.step").read_bytes()


def test_inch_source_is_detected_and_converted(tmp_path):
    from src.runner import run_job
    from tests.gen_golden_steps import write_inch_block

    step = write_inch_block(tmp_path / "block.step")
    text = step.read_text("latin-1")
    # stored in inches (the INCH unit itself is defined as LENGTH_MEASURE_WITH_UNIT(25.4, <mm>))
    assert "CONVERSION_BASED_UNIT('INCH'" in text and "(0.,0.,0.5)" in text
    res = run_job({"job_type": "ingest", "revision_id": "rev_inch", "step_path": str(step), "out_dir": str(tmp_path / "out")})
    assert res.ok, res.stderr_tail
    meta = json.loads((tmp_path / "out" / "analytical_meta.json").read_text("utf-8"))
    assert meta["source_unit"] == "INCH"
    assert "INCH_SOURCE_CONVERTED" in meta["summary"]["warnings"]
    assert meta["summary"]["bbox_max"] == pytest.approx([25.4, 50.8, 12.7], abs=1e-6)  # still mm
    assert meta["summary"]["volume_mm3"] == pytest.approx(25.4 * 50.8 * 12.7, rel=1e-9)
