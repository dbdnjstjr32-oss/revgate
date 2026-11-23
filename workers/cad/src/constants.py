"""Geometry / pipeline constants.

The first block is copied verbatim from IMPLEMENTATION_BLUEPRINT §5.2 and is part of the product
contract (golden tests assert these exact values). The second block holds implementation
constants the blueprint references only in prose; each one cites where it comes from.
"""

from __future__ import annotations

# --------------------------------------------------------------------------------------------
# IMPLEMENTATION_BLUEPRINT §5.2 — workers/cad/src/constants.py (verbatim)
# --------------------------------------------------------------------------------------------
FACE_MATCH_DIST_MM     = 0.010   # 동일 면(Unchanged) 판정 거리 오차 상한
CHANGE_REPORT_MIN_MM   = 0.020   # 실무 변경 리포트 최소 치수 변화 (노이즈 차단)
ANGLE_TOL_DEG          = 0.50    # 법선/원통축 평행·동축 판정 허용각
AREA_RATIO_TOL         = 0.02    # 미변경 면 판정 시 면적 오차 허용비 (2%)
AXIS_COAXIAL_TOL_MM    = 0.050   # 동축 홀 쌍 매칭 중심축 거리 오차
HOLE_SWEEP_MIN_DEG     = 355.0   # 완전체 원통 홀 판정 최소 회전각

FAST_PATH_INLIER_MIN   = 0.60    # Stage 0 항등행렬 통과 기준 (면적비 60%+)
RANSAC_MAX_HYPOTHESES  = 500
ALIGN_CONFIDENCE_MIN   = 0.50    # 미만 시 NEEDS_ALIGN 전환
ALIGN_RMS_MAX_MM       = 0.020   # SVD 정밀 스냅 후 허용 최대 RMS 오차

WALL_CRITICAL_MM       = 1.00    # CNC 알루미늄 채터링/변형 CRITICAL 경고
WALL_MAJOR_MM          = 2.00    # < 2.0mm 이면서 30%+ 감소 시 MAJOR 경고
WALL_REDUCTION_RATIO   = 0.30

ISO_METRIC_TAP_DRILL_MM = {
    "M2": (1.60, 0.40), "M2.5": (2.05, 0.45), "M3": (2.50, 0.50),
    "M3.5": (2.90, 0.60), "M4": (3.30, 0.70), "M5": (4.20, 0.80),
    "M6": (5.00, 1.00), "M8": (6.80, 1.25), "M10": (8.50, 1.50),
    "M12": (10.20, 1.75), "M14": (12.00, 2.00), "M16": (14.00, 2.00),
    "M18": (15.50, 2.50), "M20": (17.50, 2.50),
}
TAP_DRILL_MATCH_TOL_MM = 0.08

MAX_SOLIDS_MVP         = 10
MAX_FACES_HARD         = 15000
THREAD_BOMB_FACES      = 3000    # BSPLINE > 50% + 소면적(<5mm²) 다발
BOOLEAN_TIMEOUT_SEC    = 15.0
SUBPROCESS_TIMEOUT_SEC = 60.0

# --------------------------------------------------------------------------------------------
# Implementation constants referenced in blueprint prose
# --------------------------------------------------------------------------------------------
# §2.1 / §5.1: every length in mm, Z-up.
CASCADE_UNIT = "MM"

# §5.3: ShapeFix_Shape healing parameters.
HEALING_PRECISION_MM = 1e-3
HEALING_MAX_TOLERANCE_MM = 0.1

# §5.2 "THREAD_BOMB_FACES = 3000 # BSPLINE > 50% + 소면적(<5mm²) 다발".
THREAD_BOMB_BSPLINE_RATIO = 0.50
THREAD_BOMB_SMALL_FACE_MM2 = 5.0
THREAD_BOMB_SMALL_FACE_RATIO = 0.50

# §5.2 RANSAC candidate generation.
RANSAC_TOP_PLANES = 15              # (1순위) 면적 상위 15개 평면
RANSAC_TRIPLET_PLANES = 8           # (2순위) 비평행 상위 평면 3개 교점 프레임 (top-8 → C(8,3) triplets)
RANSAC_HOLES_PER_PLANE = 8          # perpendicular hole axes considered per plane
RANSAC_COARSE_FACES = 300           # largest target faces used for coarse hypothesis ranking
RANSAC_REFINE_TOP_K = 8             # hypotheses re-scored on all faces + Kabsch snapped
FREEFORM_ANALYTIC_AREA_MAX = 0.30   # (3순위) 평면·원통 30% 미만 자유곡면 → OBB 24방향 + ICP
ICP_SAMPLE_POINTS = 2000            # 2,000점 Trimmed ICP
ICP_TRIM_RATIO = 0.90
ICP_COARSE_ITERS = 12
ICP_FINE_ITERS = 40

# §5.3 heatmap fallback: lod0 5,000점 면적 가중 샘플링 + scipy cKDTree.
HEATMAP_SAMPLE_POINTS = 5000
RNG_SEED = 1_0303_21                 # deterministic sampling (ISO 10303-21)

# Boolean (stage 3) numerics.
BOOLEAN_FUZZY_MM = 1e-5
DELTA_REGION_MIN_THICKNESS_MM = CHANGE_REPORT_MIN_MM  # slivers thinner than this are noise

# Rendering defaults for COLOR_0 when the STEP file carries no colour.
DEFAULT_FACE_RGB = (0.72, 0.74, 0.78)
DELTA_ADDED_RGB = (0.16, 0.74, 0.36)
DELTA_REMOVED_RGB = (0.90, 0.22, 0.20)

# Severity policy (blueprint fixes only WALL_THICKNESS thresholds; the rest is product policy):
#   CRITICAL = manufacturing risk, MAJOR = dimensional change to act on, INFO = informational.
SEVERITY_HOLE = "MAJOR"
SEVERITY_PLANAR_OFFSET = "MAJOR"
SEVERITY_UNEXPLAINED_VOLUME = "MAJOR"

SCHEMA_VERSION = "1.0.0"
