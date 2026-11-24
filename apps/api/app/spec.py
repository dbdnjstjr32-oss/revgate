"""Phase 4 manufacturing spec (docs/PHASE4_MANUFACTURING_SPEC.md §3, §5.2).

* upgrade_spec_card(): Phase 1~3 `spec_card_json` ({} / {"material": "AL6061"} / {"filename": ...}) and
  partially valid cards -> a strictly valid SpecCardV2 dict (defaults filled, part defaults inherited).
* compute_spec_diff(): Base<->Target differences, CRITICAL / MAJOR / INFO.
* missing_spec_warnings(): MISSING_MATERIAL / MISSING_TOLERANCE (ack required), UNDECLARED_THREAD /
  MISSING_BUILD_ORIENTATION (caution).
* snapshot_sha256(): the canonical hash of an approval snapshot (§2.1).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel, ValidationError
from revgate_contracts import SpecCardV2
from revgate_contracts.manufacturing import (
    AdditiveSpec, CriticalTolerance, GeneralTolerance, HeatTreatment, MaterialSpec, OrderSpec, SurfaceFinish, ThreadInsert,
)

# Phase 1~3 revision metadata that lives in spec_card_json next to the card (never part of SpecCardV2)
SPEC_META_KEYS = ("filename", "submitted_by")

PROCESSES = ("CNC_MILLING", "CNC_TURNING", "PRINT_FDM", "PRINT_SLA_SLS_MJF", "SHEET_METAL", "OTHER")
PROCESS_LABEL = {
    "CNC_MILLING": "CNC 밀링", "CNC_TURNING": "CNC 선반", "PRINT_FDM": "3D 프린팅 (FDM)",
    "PRINT_SLA_SLS_MJF": "3D 프린팅 (SLA/SLS/MJF)", "SHEET_METAL": "판금", "OTHER": "기타/미지정",
}
TOLERANCE_LABEL = {"ISO_2768_fH": "ISO 2768-fH", "ISO_2768_mK": "ISO 2768-mK", "ISO_2768_cL": "ISO 2768-cL"}
INSERT_LABEL = {"TAPPED_DIRECT": "직접 탭", "HELICOIL": "헬리코일", "HEAT_SET_BRASS": "열압입 황동 인서트", "PEM": "PEM 인서트"}
INSPECTION_LABEL = {"NONE": "검사 없음", "DIMENSIONAL_SHEET": "치수 성적서", "CMM_REPORT": "CMM 리포트"}

# §5.2 process-specific caution checklist ids (labels live in the web app)
DFM_CHECKS: dict[str, tuple[str, ...]] = {
    "CNC": ("corner_r", "thin_wall", "precision_fit", "coating_shift", "datum_setup"),
    "PRINT": ("orientation", "overhang", "heat_insert", "shrinkage"),
}

ACK_REQUIRED = ("MISSING_MATERIAL", "MISSING_TOLERANCE")
WARNING_MESSAGES = {
    "MISSING_MATERIAL": "재질(material.grade)이 지정되지 않았습니다.",
    "MISSING_TOLERANCE": "일반 공차·중요 공차가 지정되지 않았고 Target 리비전에 2D PDF 도면도 없습니다.",
    "UNDECLARED_THREAD": "3D Diff에 ISO 탭 추정 힌트가 있지만 나사·인서트 사양이 비어 있습니다.",
    "MISSING_BUILD_ORIENTATION": "3D 프린팅 공정인데 적층 방향(build orientation)이 지정되지 않았습니다.",
}
TAP_HINT = re.compile(r"ISO\s*M\d")


def canonical(card: SpecCardV2 | dict[str, Any]) -> dict[str, Any]:
    model = card if isinstance(card, SpecCardV2) else SpecCardV2.model_validate(card)
    return model.model_dump(mode="json", exclude_none=True)


def default_card(material: str | None = None, finish: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "process": "OTHER",
        "material": {"grade": (material or "").strip()},
        "heat_treatment": {"required": False},
        "surface_finish": {"type": (finish or "").strip()},
        "general_tolerance": {"standard": "UNSPECIFIED"},
        "critical_tolerances": [],
        "threads_and_inserts": [],
        "order": {"inspection_level": "NONE"},
    }


def _valid(model: type[BaseModel], value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        return model.model_validate(value).model_dump(mode="json", exclude_none=True)
    except ValidationError:
        return None


def upgrade_spec_card(raw: dict[str, Any] | None, *, default_material: str | None = None,
                      default_finish: str | None = None) -> dict[str, Any]:
    """Any stored spec_card_json -> a valid SpecCardV2 dict.

    A valid 2.0 card is returned as is. Everything else (Phase 1~3 `{}` / legacy keys / partially broken
    cards) is upgraded field by field: valid sub-objects are kept, legacy `material` / `finish` strings and
    the part's default_material / default_finish fill the gaps, the rest gets §3 defaults.
    """
    raw = {k: v for k, v in (raw or {}).items() if k not in SPEC_META_KEYS} if isinstance(raw, dict) else {}
    if raw.get("schema_version") == "2.0":
        try:
            return canonical(SpecCardV2.model_validate(raw))
        except ValidationError:
            pass  # salvage below

    legacy_material = raw.get("material") if isinstance(raw.get("material"), str) else None
    legacy_finish = next((raw[k] for k in ("finish", "surface_finish") if isinstance(raw.get(k), str)), None)
    card = default_card(legacy_material or default_material, legacy_finish or default_finish)
    if raw.get("process") in PROCESSES:
        card["process"] = raw["process"]
    for key, model in (("material", MaterialSpec), ("heat_treatment", HeatTreatment), ("surface_finish", SurfaceFinish),
                       ("general_tolerance", GeneralTolerance), ("additive_spec", AdditiveSpec), ("order", OrderSpec)):
        value = _valid(model, raw.get(key))
        if value is not None:
            card[key] = value
    for key, model in (("critical_tolerances", CriticalTolerance), ("threads_and_inserts", ThreadInsert)):
        if isinstance(raw.get(key), list):
            card[key] = [v for v in (_valid(model, item) for item in raw[key][:100]) if v is not None]
    return canonical(card)


def stored_card(meta_source: dict[str, Any] | None, card: dict[str, Any]) -> dict[str, Any]:
    """What PATCH /spec writes: the canonical card + the Phase 1~3 metadata keys of the old JSON."""
    meta = {k: meta_source[k] for k in SPEC_META_KEYS if isinstance(meta_source, dict) and k in meta_source}
    return {**meta, **card}


# --------------------------------------------------------------------------------------------
# §3.1 compute_spec_diff
# --------------------------------------------------------------------------------------------
def _txt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        value = f"{value:g}"
    text = str(value).strip()
    return text or None


def _num(value: Any, unit: str) -> str | None:
    return None if value is None else f"{value:g} {unit}".strip()


def _heat(c: dict[str, Any]) -> str | None:
    ht = c["heat_treatment"]
    if not ht.get("required"):
        return "없음"
    return f"필요 · {ht['spec']}" if _txt(ht.get("spec")) else "필요"


def _general_tolerance(c: dict[str, Any]) -> str | None:
    gt = c["general_tolerance"]
    std = gt["standard"]
    if std == "UNSPECIFIED":
        return None
    if std == "CUSTOM":
        return f"사용자 정의: {gt['custom_note']}" if _txt(gt.get("custom_note")) else "사용자 정의"
    return TOLERANCE_LABEL[std] + (f" ({gt['custom_note']})" if _txt(gt.get("custom_note")) else "")


def _critical(c: dict[str, Any]) -> str | None:
    parts = []
    for t in sorted(c["critical_tolerances"], key=lambda t: t["id"]):
        s = f"{t['id']} {t['location']}: {t['spec']}"
        if _txt(t.get("datum_ref")):
            s += f" [{t['datum_ref']}]"
        if t.get("linked_face_id") is not None:
            s += f" (face {t['linked_face_id']})"
        parts.append(s)
    return "; ".join(parts) or None


def _threads(c: dict[str, Any]) -> str | None:
    parts = []
    for t in c["threads_and_inserts"]:
        s = f"{t['spec']} ×{t['count']} {INSERT_LABEL[t['insert_type']]}"
        if t.get("depth_mm") is not None:
            s += f" 깊이 {t['depth_mm']:g}mm"
        if _txt(t.get("note")):
            s += f" ({t['note']})"
        parts.append(s)
    return "; ".join(sorted(parts)) or None


def _get(*path: str) -> Callable[[dict[str, Any]], Any]:
    def read(c: dict[str, Any]) -> Any:
        cur: Any = c
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        return cur
    return read


SPEC_FIELDS: list[tuple[str, str, str, Callable[[dict[str, Any]], str | None]]] = [
    ("process", "공정", "CRITICAL", lambda c: PROCESS_LABEL[c["process"]]),
    ("material.grade", "재질", "CRITICAL", lambda c: _txt(_get("material", "grade")(c))),
    ("material.standard", "재질 규격", "INFO", lambda c: _txt(_get("material", "standard")(c))),
    ("heat_treatment", "열처리", "CRITICAL", _heat),
    ("general_tolerance", "일반 공차", "CRITICAL", _general_tolerance),
    ("critical_tolerances", "중요 공차", "CRITICAL", _critical),
    ("threads_and_inserts", "나사·인서트", "CRITICAL", _threads),
    ("additive_spec.build_orientation", "적층 방향", "CRITICAL", lambda c: _txt(_get("additive_spec", "build_orientation")(c))),
    ("additive_spec.layer_height_mm", "적층 높이", "INFO", lambda c: _num(_get("additive_spec", "layer_height_mm")(c), "mm")),
    ("additive_spec.infill_pct", "충전율", "INFO", lambda c: _num(_get("additive_spec", "infill_pct")(c), "%")),
    ("additive_spec.wall_loops", "벽 루프", "INFO", lambda c: _txt(_get("additive_spec", "wall_loops")(c))),
    ("additive_spec.support_note", "서포트", "INFO", lambda c: _txt(_get("additive_spec", "support_note")(c))),
    ("surface_finish.type", "표면처리", "MAJOR", lambda c: _txt(_get("surface_finish", "type")(c))),
    ("surface_finish.anodizing_thickness_um", "아노다이징 두께", "MAJOR",
     lambda c: (f"{t} µm" if (t := _txt(_get("surface_finish", "anodizing_thickness_um")(c))) else None)),
    ("surface_finish.color", "색상", "MAJOR", lambda c: _txt(_get("surface_finish", "color")(c))),
    ("surface_finish.ra_um", "표면 조도 Ra", "MAJOR", lambda c: _num(_get("surface_finish", "ra_um")(c), "µm")),
    ("surface_finish.masking_note", "마스킹", "MAJOR", lambda c: _txt(_get("surface_finish", "masking_note")(c))),
    ("order.quantity", "수량", "MAJOR", lambda c: (f"{q}개" if (q := _get("order", "quantity")(c)) is not None else None)),
    ("order.due_date", "납기", "MAJOR", lambda c: _txt(_get("order", "due_date")(c))),
    ("order.inspection_level", "검사 수준", "INFO", lambda c: INSPECTION_LABEL[c["order"]["inspection_level"]]),
]


def compute_spec_diff(base: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    """§3.1 — [{field, label, before, after, severity}] for every display-level difference (stable order)."""
    out = []
    for field, label, severity, fmt in SPEC_FIELDS:
        before, after = fmt(base), fmt(target)
        if before != after:
            out.append({"field": field, "label": label, "before": before, "after": after, "severity": severity})
    return out


# --------------------------------------------------------------------------------------------
# §3.2 missing-spec warnings
# --------------------------------------------------------------------------------------------
def missing_spec_warnings(target: dict[str, Any], *, target_has_pdf: bool, hints: Iterable[str | None]) -> list[dict[str, Any]]:
    codes = []
    if not target["material"]["grade"].strip():
        codes.append("MISSING_MATERIAL")
    if (target["general_tolerance"]["standard"] == "UNSPECIFIED" and not target["critical_tolerances"]
            and not target_has_pdf):
        codes.append("MISSING_TOLERANCE")
    if not target["threads_and_inserts"] and any(TAP_HINT.search(h or "") for h in hints):
        codes.append("UNDECLARED_THREAD")
    if target["process"].startswith("PRINT_") and not ((target.get("additive_spec") or {}).get("build_orientation") or "").strip():
        codes.append("MISSING_BUILD_ORIENTATION")
    return [{"code": c, "level": "ACK_REQUIRED" if c in ACK_REQUIRED else "CAUTION", "message": WARNING_MESSAGES[c]}
            for c in codes]


def dfm_check_ids(process: str) -> tuple[str, ...]:
    if process.startswith("CNC_"):
        return DFM_CHECKS["CNC"]
    if process.startswith("PRINT_"):
        return DFM_CHECKS["PRINT"]
    return ()


# --------------------------------------------------------------------------------------------
# §2.1 canonical snapshot hash
# --------------------------------------------------------------------------------------------
def snapshot_sha256(snapshot: dict[str, Any]) -> str:
    """sha256(json.dumps(snap, sort_keys=True, separators=(",", ":"))) — exactly as specified (ASCII-escaped)."""
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
