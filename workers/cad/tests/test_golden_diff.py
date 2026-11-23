"""Golden STEP regression suite (IMPLEMENTATION_BLUEPRINT §7.1) — real OCP worker subprocesses, no mocks."""

from __future__ import annotations

import json

import numpy as np
import pygltflib
import pytest

from src import constants as C
from src.geom import rotation_angle_deg
from tests.gen_golden_steps import CHANGED_HOLES, HOLES_XY

from .conftest import CASES, case_meta

pytestmark = pytest.mark.golden


def _ok(run, *args, **kw):
    res, out, manifest = run(*args, **kw)
    assert res.ok, (res.error_code, res.message, res.stderr_tail)
    return res, out, manifest


def _by_cat(manifest: dict, category: str) -> list[dict]:
    return [c for c in manifest["changes"] if c["category"] == category]


@pytest.mark.parametrize("case", CASES)
def test_manifest_contract(case, run_diff, validators):
    from revgate_contracts import DiffManifest

    res, _out, manifest = _ok(run_diff, case)
    validators["diff_manifest"].validate(manifest)
    DiffManifest.model_validate(manifest)
    assert manifest["summary"]["total_changes"] == len(manifest["changes"])
    fs = manifest["face_status_map"]
    # every face of each revision is classified exactly once
    meta_a = case_meta(case)  # noqa: F841 - documents the case under test
    base_ids = fs["base"]["unchanged"] + fs["base"]["modified"] + fs["base"]["removed"]
    target_ids = fs["target"]["unchanged"] + fs["target"]["modified"] + fs["target"]["added"]
    assert len(base_ids) == len(set(base_ids)) and len(target_ids) == len(set(target_ids))
    assert res.duration_ms < C.SUBPROCESS_TIMEOUT_SEC * 1000


def _hole_xy(run_ingest, case: str, face_ids: list[int]) -> list[tuple[float, float]]:
    res, out = run_ingest(case, "A")
    assert res.ok
    meta = json.loads((out / "analytical_meta.json").read_text("utf-8"))
    return sorted(tuple(meta["faces"][k]["params"]["axis_origin"][:2]) for k in face_ids)


def test_01_hole_diam(run_diff, run_ingest):
    res, out, m = _ok(run_diff, "01_hole_diam")
    assert res.status == "READY"
    assert m["alignment"]["method"] == "IDENTITY" and m["alignment"]["requires_manual_review"] is False
    holes = _by_cat(m, "HOLE_DIAMETER")
    assert len(holes) == 2 and m["summary"]["total_changes"] == 2
    for ch in holes:
        f = ch["fact"]
        assert (f["from_diameter_mm"], f["to_diameter_mm"], f["delta_mm"]) == (3.3, 4.2, 0.9)
        assert ch["title"] == "홀 지름 변경: Ø3.30 → Ø4.20 (+0.90mm)"
        # fact / estimate separation: the ISO reading lives only in engineering_hint
        assert ch["engineering_hint"] == {"text": "참고 추정: ISO M4 → M5 탭 기초홀 규격과 일치", "confidence": "HIGH"}
        assert not any("M4" in str(v) or "M5" in str(v) for v in f.values())
        assert f["removed_volume_mm3"] == pytest.approx(np.pi * (2.1**2 - 1.65**2) * 20, abs=1e-2)
    changed = sorted(HOLES_XY[i] for i in CHANGED_HOLES)
    assert sorted(xy for ch in holes for xy in _hole_xy(run_ingest, "01_hole_diam", ch["base_face_ids"])) == changed
    assert m["summary"]["boolean_status"] == "OK"
    assert m["summary"]["volume_delta_mm3"] == pytest.approx(-2 * np.pi * (2.1**2 - 1.65**2) * 20, abs=1e-2)
    assert m["artifacts"]["delta_removed_glb"] == "delta_removed.glb" and m["artifacts"]["delta_added_glb"] is None
    glb = pygltflib.GLTF2.load_from_bytes((out / "delta_removed.glb").read_bytes())
    assert len(glb.meshes) == 1


def test_02_pocket_offset(run_diff):
    res, _out, m = _ok(run_diff, "02_pocket_offset")
    assert res.status == "READY" and m["alignment"]["method"] == "IDENTITY"
    offsets = _by_cat(m, "PLANAR_OFFSET")
    assert len(offsets) == 1
    f = offsets[0]["fact"]
    assert f["offset_mm"] == -2.5 and f["direction"] == "MATERIAL_REMOVED"
    assert (f["feature"], f["from_depth_mm"], f["to_depth_mm"]) == ("POCKET_FLOOR", 8.0, 10.5)
    assert offsets[0]["title"] == "포켓 깊이 변경: 8.000 → 10.500mm (+2.500mm)"
    assert m["summary"]["volume_delta_mm3"] == pytest.approx(-3750.0, abs=1.0)
    assert f["removed_volume_mm3"] == pytest.approx(3750.0, abs=1.0)
    assert m["summary"]["total_changes"] == 1


def test_03_transform_shift(run_diff):
    res, _out, m = _ok(run_diff, "03_transform_shift")
    al = m["alignment"]
    assert res.status == "READY"
    assert al["method"] == "AUTO_BREP_RANSAC" and al["requires_manual_review"] is False
    assert al["confidence"] >= 0.85
    assert al["rms_error_mm"] < 0.005
    # recovered Target->Base transform is the inverse of the generation transform
    T = np.array(al["transform_4x4"]).reshape(4, 4)
    G = np.array(case_meta("03_transform_shift")["generation_transform_B"]).reshape(4, 4)
    E = T @ G
    assert rotation_angle_deg(E[:3, :3]) < 1e-6 and np.linalg.norm(E[:3, 3]) < 1e-4  # STEP stores 15 significant digits
    holes = _by_cat(m, "HOLE_DIAMETER")
    assert len(holes) == 2
    _r1, _o1, m01 = _ok(run_diff, "01_hole_diam")
    assert sorted(h["base_face_ids"] for h in holes) == sorted(h["base_face_ids"] for h in _by_cat(m01, "HOLE_DIAMETER"))
    for h in holes:
        assert (h["fact"]["from_diameter_mm"], h["fact"]["to_diameter_mm"]) == (3.3, 4.2)
    assert m["summary"]["boolean_status"] == "OK"


def test_04_thin_wall(run_diff):
    res, _out, m = _ok(run_diff, "04_thin_wall")
    assert res.status == "READY"
    walls = _by_cat(m, "WALL_THICKNESS")
    assert len(walls) == 1
    w = walls[0]
    assert w["severity"] == "CRITICAL"
    assert (w["fact"]["from_mm"], w["fact"]["to_mm"]) == (2.5, 0.8)
    assert w["fact"]["to_mm"] < C.WALL_CRITICAL_MM
    assert w["engineering_hint"]["confidence"] == "LOW"
    assert m["changes"][0]["severity"] == "CRITICAL"  # CRITICAL first
    offsets = _by_cat(m, "PLANAR_OFFSET")
    assert len(offsets) == 1 and offsets[0]["fact"]["offset_mm"] == -1.7
    assert m["summary"]["volume_delta_mm3"] == pytest.approx(-1.7 * 30 * 8, abs=1e-2)


def test_05_symmetric_needs_align(run_diff):
    res, _out, m = _ok(run_diff, "05_symmetric_needs_align")
    al = m["alignment"]
    assert res.status == "NEEDS_ALIGN"
    assert al["requires_manual_review"] is True
    assert al["confidence"] < C.ALIGN_CONFIDENCE_MIN and al["confidence"] < 0.40
    assert al["method"] == "AUTO_BREP_RANSAC"  # auto alignment attempted and rejected
    assert m["summary"]["boolean_status"] == "SKIPPED"


def test_05_manual_321_realign(run_diff):
    """3-2-1 datum alignment from the client (here: the true identity) recomputes as READY."""
    identity = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    res, _out, m = _ok(run_diff, "05_symmetric_needs_align", alignment={"mode": "MANUAL_321", "transform_4x4": identity}, tag="manual")
    assert res.status == "READY"
    assert m["alignment"]["method"] == "MANUAL_321" and m["alignment"]["requires_manual_review"] is False
    assert m["summary"]["boolean_status"] == "OK"
    vols = _by_cat(m, "VOLUME_DELTA")
    assert len(vols) == 4
    for v in vols:
        assert v["fact"]["region"] == "REMOVED"
        assert v["fact"]["volume_mm3"] == pytest.approx(24 * 24 * 10, abs=1e-3)
        assert v["title"].endswith("(육안 확인 필요)")
    assert m["summary"]["volume_delta_mm3"] == pytest.approx(-4 * 24 * 24 * 10, abs=1e-2)


def test_week1_cli_compare_under_15s(golden_dir, tmp_path):
    """Week-1 DoD: CLI with two STEP files -> align + diff JSON + lod GLBs + analytical_meta within 15 s."""
    import subprocess
    import sys
    import time

    from src.runner import WORKER_ROOT, child_env

    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "src.cli", "compare", str(golden_dir / "01_hole_diam" / "rev_A.step"),
         str(golden_dir / "01_hole_diam" / "rev_B.step"), "--out", str(tmp_path)],
        cwd=WORKER_ROOT, env=child_env(), capture_output=True, text=True, timeout=60,
    )
    elapsed = time.monotonic() - t0
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert elapsed < 15.0, elapsed
    for rel in ("base/lod0.glb", "base/lod1.glb", "base/analytical_meta.json", "target/analytical_meta.json", "diff/diff_manifest.json"):
        assert (tmp_path / rel).is_file(), rel
