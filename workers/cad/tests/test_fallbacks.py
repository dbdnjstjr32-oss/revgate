"""Dirty-STEP defenses and fallbacks (§5.3) + free-form alignment path (§5.2, 3순위)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from src.geom import rotation_angle_deg
from src.runner import run_job


def _diff(a, b, out, **extra):
    spec = {"job_type": "diff", "diff_run_id": "diff_x", "base": {"revision_id": "rev_a", "step_path": str(a)},
            "target": {"revision_id": "rev_b", "step_path": str(b)}, "out_dir": str(out), **extra}
    res = run_job(spec)
    assert res.ok, (res.error_code, res.message, res.stderr_tail)
    return res, json.loads((out / "diff_manifest.json").read_text("utf-8"))


def test_boolean_timeout_falls_back_to_heatmap(golden_dir, tmp_path, validators):
    case = golden_dir / "01_hole_diam"
    res, m = _diff(case / "rev_A.step", case / "rev_B.step", tmp_path, options={"boolean_timeout_sec": 0.001})
    validators["diff_manifest"].validate(m)
    assert m["summary"]["boolean_status"] == "FALLBACK_HEATMAP"
    assert "BOOLEAN_FALLBACK_HEATMAP" in res.result["warnings"]
    assert res.result["fallback_reason"].startswith("BOOLEAN_TIMEOUT")
    # face-to-face facts survive the fallback; the hole deviations are explained by them
    cats = sorted(c["category"] for c in m["changes"])
    assert cats == ["HOLE_DIAMETER", "HOLE_DIAMETER"]
    assert m["artifacts"] == {"delta_added_glb": None, "delta_removed_glb": None}


def test_open_shell_skips_boolean_and_uses_ckdtree_heatmap(tmp_path, validators):
    from tests.gen_golden_steps import OPEN_SHELL_WALL_SHIFT_MM, write_open_shell_pair

    a, b = write_open_shell_pair(tmp_path)
    for step in (a, b):
        r = run_job({"job_type": "ingest", "revision_id": step.stem, "step_path": str(step), "out_dir": str(tmp_path / step.stem)})
        assert r.ok, r.stderr_tail
        meta = json.loads((tmp_path / step.stem / "analytical_meta.json").read_text("utf-8"))
        assert meta["summary"]["is_watertight"] is False
        assert {"OPEN_SHELL_ONLY", "HEALING_APPLIED"} <= set(meta["summary"]["warnings"])
        assert meta["summary"]["volume_mm3"] == 0.0
    res, m = _diff(a, b, tmp_path / "diff")
    validators["diff_manifest"].validate(m)
    assert m["summary"]["boolean_status"] == "FALLBACK_HEATMAP"
    assert {"OPEN_SHELL_ONLY", "BOOLEAN_FALLBACK_HEATMAP"} <= set(res.result["warnings"])
    offsets = [c for c in m["changes"] if c["category"] == "PLANAR_OFFSET"]
    assert m["alignment"]["method"] == "IDENTITY"
    assert len(offsets) == 1 and offsets[0]["fact"]["offset_mm"] == pytest.approx(OPEN_SHELL_WALL_SHIFT_MM)
    assert offsets[0]["fact"]["direction"] == "MATERIAL_ADDED"
    assert not [c for c in m["changes"] if c["category"] == "VOLUME_DELTA"], "heatmap deviations are explained by the offset"


def test_heatmap_reports_unexplained_deviation(golden_dir, tmp_path):
    """05 with an identity 3-2-1 and the Boolean disabled: the pockets show up as heatmap regions."""
    case = golden_dir / "05_symmetric_needs_align"
    identity = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    _res, m = _diff(case / "rev_A.step", case / "rev_B.step", tmp_path,
                    alignment={"mode": "MANUAL_321", "transform_4x4": identity}, options={"boolean_timeout_sec": 0.001})
    vols = [c for c in m["changes"] if c["category"] == "VOLUME_DELTA"]
    assert vols and all(c["fact"]["method"] == "HEATMAP" for c in vols)
    assert max(c["fact"]["max_deviation_mm"] for c in vols) == pytest.approx(10.0, abs=0.2)


def test_thread_bomb_predicate():
    from src.ingest import FaceRecord, thread_bomb_detected

    def rec(i, stype, area):
        z = np.zeros(3)
        return FaceRecord(face_id=i, stype=stype, area=area, centroid=z, bbox_min=z, bbox_max=z, sample_point=z, sample_normal=z)

    bomb = [rec(i, "BSPLINE", 1.0) for i in range(3000)] + [rec(3000 + i, "PLANE", 100.0) for i in range(200)]
    assert thread_bomb_detected(bomb)
    assert not thread_bomb_detected(bomb[:2999])  # below THREAD_BOMB_FACES
    assert not thread_bomb_detected([rec(i, "PLANE", 1.0) for i in range(4000)])  # not B-spline dominated


def test_freeform_part_uses_obb_icp_alignment(tmp_path):
    from tests.gen_golden_steps import write_freeform_pair

    a, b, g = write_freeform_pair(tmp_path)
    res, m = _diff(a, b, tmp_path / "diff")
    al = m["alignment"]
    assert res.status == "READY"
    assert al["method"] == "AUTO_BREP_RANSAC" and res.result["alignment"]["stage"] == "P3_OBB_ICP"
    assert al["confidence"] >= 0.85 and al["rms_error_mm"] < 0.005
    E = np.array(al["transform_4x4"]).reshape(4, 4) @ g
    assert rotation_angle_deg(E[:3, :3]) < 1e-3 and np.linalg.norm(E[:3, 3]) < 1e-3
    assert m["summary"]["geometry_identical"] is True and m["changes"] == []


def test_manual_transform_must_be_rigid(golden_dir, tmp_path):
    case = golden_dir / "01_hole_diam"
    scaled = [2.0, 0, 0, 0, 0, 2.0, 0, 0, 0, 0, 2.0, 0, 0, 0, 0, 1.0]
    res = run_job({"job_type": "diff", "diff_run_id": "d", "base": {"revision_id": "a", "step_path": str(case / "rev_A.step")},
                   "target": {"revision_id": "b", "step_path": str(case / "rev_B.step")}, "out_dir": str(tmp_path),
                   "alignment": {"mode": "MANUAL_321", "transform_4x4": scaled}})
    assert (res.ok, res.error_code) == (False, "WORKER_INTERNAL_ERROR")
    assert "rigid" in res.stderr_tail
