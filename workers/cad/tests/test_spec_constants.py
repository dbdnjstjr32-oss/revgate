"""The numbers and codes fixed by IMPLEMENTATION_BLUEPRINT §5 are part of the contract."""

from __future__ import annotations

import json
import math

from src import constants as C
from src.errors import DOMAIN_ERRORS, EXIT_CODE_MAP, TIMEOUT_ERROR_CODE, WARNINGS, CadDomainError
from src.tessellate import get_lod_params

from .conftest import SCHEMA_DIR


def test_geometry_constants_match_blueprint():
    assert (C.FACE_MATCH_DIST_MM, C.CHANGE_REPORT_MIN_MM, C.ANGLE_TOL_DEG) == (0.010, 0.020, 0.50)
    assert (C.AREA_RATIO_TOL, C.AXIS_COAXIAL_TOL_MM, C.HOLE_SWEEP_MIN_DEG) == (0.02, 0.050, 355.0)
    assert (C.FAST_PATH_INLIER_MIN, C.RANSAC_MAX_HYPOTHESES, C.ALIGN_CONFIDENCE_MIN, C.ALIGN_RMS_MAX_MM) == (0.60, 500, 0.50, 0.020)
    assert (C.WALL_CRITICAL_MM, C.WALL_MAJOR_MM, C.WALL_REDUCTION_RATIO) == (1.00, 2.00, 0.30)
    assert C.TAP_DRILL_MATCH_TOL_MM == 0.08
    assert (C.MAX_SOLIDS_MVP, C.MAX_FACES_HARD, C.THREAD_BOMB_FACES) == (10, 15000, 3000)
    assert (C.BOOLEAN_TIMEOUT_SEC, C.SUBPROCESS_TIMEOUT_SEC) == (15.0, 60.0)


def test_iso_tap_drill_table_matches_blueprint():
    assert C.ISO_METRIC_TAP_DRILL_MM == {
        "M2": (1.60, 0.40), "M2.5": (2.05, 0.45), "M3": (2.50, 0.50),
        "M3.5": (2.90, 0.60), "M4": (3.30, 0.70), "M5": (4.20, 0.80),
        "M6": (5.00, 1.00), "M8": (6.80, 1.25), "M10": (8.50, 1.50),
        "M12": (10.20, 1.75), "M14": (12.00, 2.00), "M16": (14.00, 2.00),
        "M18": (15.50, 2.50), "M20": (17.50, 2.50),
    }


def test_error_codes_match_blueprint():
    assert EXIT_CODE_MAP == {
        -9: "OOM_KILLED", 137: "OOM_KILLED",
        -11: "SEGFAULT_OCCT", 139: "SEGFAULT_OCCT",
        -6: "SEGFAULT_OCCT", 134: "SEGFAULT_OCCT",
    }
    assert TIMEOUT_ERROR_CODE == "TIMEOUT_WORKER_60S"
    assert DOMAIN_ERRORS == ["INVALID_STEP_SYNTAX", "EMPTY_GEOMETRY", "TOO_MANY_SOLIDS", "TOO_MANY_FACES",
                             "RAW_FILE_NOT_FOUND", "SHA256_MISMATCH"]
    assert WARNINGS == ["OPEN_SHELL_ONLY", "HEALING_APPLIED", "INCH_SOURCE_CONVERTED", "HELICAL_THREADS_DETECTED",
                        "BOOLEAN_FALLBACK_HEATMAP"]
    try:
        CadDomainError("NOT_A_CODE")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("unknown domain codes must be rejected")


def test_lod_params_formula():
    # lod0: clamp(diag * 0.0005, 0.03, 0.10) mm, 12 deg | lod1: clamp(diag * 0.0025, 0.15, 0.50) mm, 25 deg
    assert get_lod_params(10.0, "lod0") == (0.03, math.radians(12.0))
    assert get_lod_params(116.619, "lod0")[0] == 116.619 * 0.0005
    assert get_lod_params(1000.0, "lod0") == (0.10, math.radians(12.0))
    assert get_lod_params(10.0, "lod1") == (0.15, math.radians(25.0))
    assert get_lod_params(116.619, "lod1")[0] == 116.619 * 0.0025
    assert get_lod_params(1000.0, "lod1") == (0.50, math.radians(25.0))


def test_packaged_schemas_are_the_ssot_schemas():
    from revgate_contracts import load_schema

    for name in ("analytical_meta", "diff_manifest"):
        repo = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text("utf-8"))
        assert load_schema(name) == repo, f"{name}: run `pnpm codegen` (packaged schema is stale)"
